"""Win-rate of MCTSPlayer vs GreedyPlayer over seeded games, both seats."""

from __future__ import annotations

import argparse
import time

from struggler.bots.greedy import GreedyPlayer
from struggler.bots.mcts import MCTSPlayer
from struggler.engine import Engine, Side
from struggler.runner import play_game


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--sims", type=int, default=8)
    parser.add_argument(
        "--events",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Pass events= to Engine.new_game (default: on).",
    )
    args = parser.parse_args()

    us_w = us_l = us_d = 0
    ussr_w = ussr_l = ussr_d = 0
    started = time.perf_counter()

    for i in range(args.games):
        seed = args.seed + i
        mcts_is_us = i % 2 == 0
        engine = Engine.new_game(seed=seed, events=args.events)
        mcts = MCTSPlayer(seed=seed + 1, sims=args.sims)
        greedy = GreedyPlayer()
        if mcts_is_us:
            winner = play_game(engine, {Side.US: mcts, Side.USSR: greedy})
            if winner is Side.US:
                us_w += 1
            elif winner is Side.USSR:
                us_l += 1
            else:
                us_d += 1
        else:
            winner = play_game(engine, {Side.US: greedy, Side.USSR: mcts})
            if winner is Side.USSR:
                ussr_w += 1
            elif winner is Side.US:
                ussr_l += 1
            else:
                ussr_d += 1
        seat = "US" if mcts_is_us else "USSR"
        print(f"game {i + 1}/{args.games} seed={seed} mcts={seat} winner={winner}")

    elapsed = time.perf_counter() - started
    wins = us_w + ussr_w
    losses = us_l + ussr_l
    draws = us_d + ussr_d
    decided = wins + losses
    rate = (wins / decided) if decided else 0.0

    print()
    print(f"games: {args.games}  sims: {args.sims}  events: {args.events}  seed: {args.seed}")
    print(f"MCTS as US:   {us_w}W {us_l}L {us_d}D")
    print(f"MCTS as USSR: {ussr_w}W {ussr_l}L {ussr_d}D")
    print(f"overall: {wins}-{losses}-{draws}  win rate (excluding draws): {rate:.1%}")
    print(f"wall time: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
