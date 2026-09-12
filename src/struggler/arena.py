"""A general arena for running seeded, side-swapped games between bots.

This is the evaluation backbone an autonomous improvement loop needs, and it
is deliberately pure-stdlib + `multiprocessing`-safe (players cross a process
boundary as small `PlayerSpec` dataclasses).

Two lessons from the project's earlier single-opponent self-play tuner (which
lost on fresh seeds) are baked in here:
  * every candidate is scored against a *ladder* of diverse opponents, never
    just the incumbent, so it cannot overfit one adversary;
  * every seed is played twice with the seats swapped, so side asymmetry
    cancels exactly (common random numbers).
Promotion decisions should use head-to-head results (`head_to_head`), not a
win rate against one fixed opponent (which saturates and misleads).
"""

from __future__ import annotations

import multiprocessing
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from struggler.bots.greedy import GreedyPlayer, GreedyWeights
from struggler.engine import Engine, Side
from struggler.runner import play_game


@dataclass(frozen=True)
class PlayerSpec:
    """A named, reconstructible player, small enough to pickle to a worker."""

    name: str
    kind: str = "greedy"  # greedy | mcts | random | first | rl
    weights: Mapping[str, float] | None = None
    sims: int = 16
    player_seed: int = 0
    net_path: str | None = None  # for kind == "rl"

    def with_name(self, name: str) -> "PlayerSpec":
        return PlayerSpec(name, self.kind, self.weights, self.sims, self.player_seed, self.net_path)


def make_player(spec: PlayerSpec):
    if spec.kind == "greedy":
        w = GreedyWeights(**dict(spec.weights)) if spec.weights else GreedyWeights()
        return GreedyPlayer(weights=w)
    if spec.kind == "rl":
        from struggler.bots.rl.player import RLPlayer

        return RLPlayer.from_path(spec.net_path, device="cpu", seed=spec.player_seed)
    if spec.kind == "mcts":
        from struggler.bots.mcts import MCTSPlayer

        return MCTSPlayer(seed=spec.player_seed, sims=spec.sims)
    if spec.kind == "random":
        from struggler.bots.naive import RandomPlayer

        return RandomPlayer(seed=spec.player_seed)
    if spec.kind == "first":
        from struggler.bots.naive import FirstLegalPlayer

        return FirstLegalPlayer()
    raise ValueError(f"unknown player kind {spec.kind!r}")


@dataclass(frozen=True)
class GameResult:
    seed: int
    us: str
    ussr: str
    winner: str | None  # "US" | "USSR" | None (draw), from the engine
    events: bool = True


def play_one(seed: int, us: PlayerSpec, ussr: PlayerSpec, events: bool = True) -> GameResult:
    engine = Engine.new_game(seed=seed, events=events)
    players = {Side.US: make_player(us), Side.USSR: make_player(ussr)}
    winner = play_game(engine, players)
    return GameResult(seed, us.name, ussr.name, winner.value if winner else None, events)


def play_pair(seed: int, a: PlayerSpec, b: PlayerSpec, events: bool = True) -> list[GameResult]:
    """One seed, both seat assignments (a=US then a=USSR)."""
    return [
        play_one(seed, a, b, events),  # a = US
        play_one(seed, b, a, events),  # swapped
    ]


def _play_pair(args: tuple[int, PlayerSpec, PlayerSpec, bool]) -> list[GameResult]:
    seed, a, b, events = args
    return play_pair(seed, a, b, events)


def run_matchup(
    a: PlayerSpec,
    b: PlayerSpec,
    seeds: Sequence[int],
    *,
    workers: int = 1,
    events: bool = True,
) -> list[GameResult]:
    """Each seed played twice with sides swapped."""
    jobs = [(int(s), a, b, events) for s in seeds]
    if workers <= 1:
        results = [_play_pair(j) for j in jobs]
    else:
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(workers) as pool:
            results = pool.map(_play_pair, jobs)
    return [r for pair in results for r in pair]


def ladder_seeds(base: int, n: int, generation: int = 0) -> list[int]:
    """Non-overlapping seed banks per generation, so candidates can't overfit
    a fixed bank across iterations."""
    return [base + generation * (n + 7) + i for i in range(n)]


def head_to_head(results: Sequence[GameResult], a: str, b: str) -> tuple[int, int, int]:
    """(a wins, b wins, draws) over games where a and b opposed each other."""
    wins = losses = draws = 0
    for g in results:
        if {g.us, g.ussr} != {a, b}:
            continue
        if g.winner is None:
            draws += 1
        elif (g.winner == "US") == (a == g.us):
            wins += 1
        else:
            losses += 1
    return wins, losses, draws


def score(results: Sequence[GameResult], name: str) -> tuple[float, int]:
    """(points, games) for `name`: win 1, draw 0.5, loss 0."""
    pts = 0.0
    n = 0
    for g in results:
        if name not in (g.us, g.ussr):
            continue
        n += 1
        if g.winner is None:
            pts += 0.5
        elif (g.winner == "US") == (g.us == name):
            pts += 1.0
    return pts, n


def elo(
    results: Sequence[GameResult],
    names: Sequence[str],
    *,
    k: float = 16.0,
    base: float = 1000.0,
    rounds: int = 200,
) -> dict[str, float]:
    """Simple Elo over all played games (order-averaged by repeated passes)."""
    rating = {nm: base for nm in names}
    games = list(results)
    for _ in range(rounds):
        random.Random(0).shuffle(games)
        for g in games:
            ra, rb = rating[g.us], rating[g.ussr]
            sa = 0.5 if g.winner is None else (1.0 if g.winner == "US" else 0.0)
            ea = 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))
            rating[g.us] = ra + k * (sa - ea)
            rating[g.ussr] = rb + k * ((1.0 - sa) - (1.0 - ea))
    return rating


def matchup_table(results: Sequence[GameResult], names: Sequence[str]) -> dict[str, dict[str, tuple[int, int, int]]]:
    """table[a][b] = (aWins, bWins, draws)."""
    table: dict[str, dict[str, tuple[int, int, int]]] = {a: {} for a in names}
    for a in names:
        for b in names:
            if a >= b:
                continue
            table[a][b] = head_to_head(results, a, b)
            w, l, d = table[a][b]
            table[b][a] = (l, w, d)
    return table


def round_robin(
    specs: Sequence[PlayerSpec],
    seeds: Sequence[int],
    *,
    workers: int = 1,
    events: bool = True,
) -> list[GameResult]:
    results: list[GameResult] = []
    for i, a in enumerate(specs):
        for b in specs[i + 1 :]:
            results.extend(run_matchup(a, b, seeds, workers=workers, events=events))
    return results
