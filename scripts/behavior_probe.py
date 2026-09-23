"""Behavior probe: the degenerate-play smells a win rate hides.

    python scripts/behavior_probe.py --games 40
    python scripts/behavior_probe.py --us greedy --ussr mcts --games 6
    python scripts/behavior_probe.py --no-events --games 10

Win rate alone cannot tell a bot that plays well from one that is lucky -- and
at this level it cannot even tell a bot that plays *at all*. Measured on the
shipped `GreedyPlayer`, 33 of 40 self-played games ended in a DEFCON-1
self-kill by turn 5, and every one of its 394 own-event opportunities was
spent on Operations instead. Neither fact shows up in a head-to-head score,
and both change what the score means.

So this prints what the games actually looked like:

  * how each game ended, and how long it lasted;
  * the decision that immediately preceded each DEFCON-1 loss (the loss path,
    named rather than counted);
  * own events fired vs offered -- a bot that never fires its own events is
    playing Operations-only, whatever else it does;
  * opponent cards spent on Operations, and the subset whose event is a DEFCON
    suicide at the time (see `greedy._defcon_suicide_risk`);
  * Coups, Realignments and Space Race attempts per game.

It is the companion to the arena: gate a change on `run_arena.py`'s
head-to-head, then read this to see whether the games that produced it are
games worth playing. `--events/--no-events` mirrors `Engine.new_game`.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.arena import PlayerSpec, make_player  # noqa: E402
from struggler.bots.greedy import _CARDS, _defcon_suicide_risk  # noqa: E402
from struggler.engine import DecisionKind, Engine, Side  # noqa: E402
from struggler.runner import play_game  # noqa: E402


class Tap:
    """Wraps a `Player` and records every (decision, chosen action) pair."""

    def __init__(self, inner, side: Side, log: list) -> None:
        self.inner = inner
        self.side = side
        self.log = log

    def bind_engine(self, engine) -> None:
        bind = getattr(self.inner, "bind_engine", None)
        if callable(bind):
            bind(engine)

    def choose_action(self, observation, history):
        action = self.inner.choose_action(observation, history)
        self.log.append((observation, observation.pending_decision, action))
        return action


def play_probed_game(seed: int, us: PlayerSpec, ussr: PlayerSpec, events: bool) -> dict:
    """One game, with per-decision behavior recorded. Mirrors `arena.play_one`."""
    engine = Engine.new_game(seed=seed, events=events)
    log: list = []
    players = {
        Side.US: Tap(make_player(us), Side.US, log),
        Side.USSR: Tap(make_player(ussr), Side.USSR, log),
    }
    play_game(engine, players)
    return {
        "seed": seed,
        "winner": engine.winner.value if engine.winner else None,
        "reason": engine._game_over_reason,
        "turn": engine.turn,
        "action_round": engine.action_round,
        "defcon": engine.defcon,
        "vp": engine.vp,
        "log": log,
    }


def summarize(game: dict) -> dict:
    """Behavior counters for one probed game."""
    counts = collections.Counter()
    events_offered = events_fired = 0
    opponent_ops = opponent_suicide_ops = opponent_suicide_forced = 0
    for observation, decision, action in game["log"]:
        counts[f"kind:{decision.kind.value}"] += 1
        if decision.kind is not DecisionKind.PLAY_MODE:
            continue
        cid = decision.context.get("card")
        card = _CARDS.get(cid)
        if card is None:
            continue
        mode = action.payload.get("mode")
        counts[f"mode:{mode}"] += 1
        own = card.side.value == observation.side.value
        offered = {option.payload.get("mode") for option in decision.options}
        if own and "event" in offered:
            events_offered += 1
            if mode == "event":
                events_fired += 1
        if not own and mode == "ops":
            opponent_ops += 1
            if _defcon_suicide_risk(observation, observation.side, cid, "ops"):
                opponent_suicide_ops += 1
                if len(decision.options) == 1:
                    # Nothing else was on offer: the loss was created earlier,
                    # by holding this card into a DEFCON-2 round, not here.
                    opponent_suicide_forced += 1
    return {
        **game,
        "counts": counts,
        "own_events_offered": events_offered,
        "own_events_fired": events_fired,
        "opponent_cards_ops": opponent_ops,
        "opponent_suicide_ops": opponent_suicide_ops,
        "opponent_suicide_forced": opponent_suicide_forced,
    }


def report(rows: list[dict], us: PlayerSpec, ussr: PlayerSpec, events: bool) -> None:
    n = len(rows)
    if not n:
        print("no games")
        return
    print(f"{us.name} (US) vs {ussr.name} (USSR)   {n} games   events={events}")
    print(f"  winners        " + "  ".join(f"{k} {v}" for k, v in collections.Counter(
        r["winner"] or "draw" for r in rows).most_common()))
    print(f"  ended by       " + "  ".join(f"{k} {v}" for k, v in collections.Counter(
        r["reason"] for r in rows).most_common()))
    print(f"  average end    turn {sum(r['turn'] for r in rows) / n:.1f}, "
          f"DEFCON {sum(r['defcon'] for r in rows) / n:.1f}, "
          f"VP {sum(r['vp'] for r in rows) / n:+.1f}")

    totals = collections.Counter()
    for r in rows:
        totals.update(r["counts"])
    per_game = lambda key: totals[key] / n  # noqa: E731
    print(f"  coups          {per_game('kind:coup_target'):.1f}/game   "
          f"realignments {per_game('kind:realignment_target'):.1f}/game   "
          f"space race {per_game('mode:space_race'):.1f}/game")

    offered = sum(r["own_events_offered"] for r in rows)
    fired = sum(r["own_events_fired"] for r in rows)
    rate = f"{100 * fired / offered:.0f}%" if offered else "n/a"
    print(f"  own events     {fired} fired / {offered} offered ({rate})")
    suicide = sum(r["opponent_suicide_ops"] for r in rows)
    forced = sum(r["opponent_suicide_forced"] for r in rows)
    print(f"  opponent cards {sum(r['opponent_cards_ops'] for r in rows)} "
          f"spent on Ops, of which {suicide} at a DEFCON-suicide risk "
          f"({forced} of those with no other option on offer)")

    defcon_rows = [r for r in rows if r["reason"] == "defcon_1"]
    if defcon_rows:
        print(f"  DEFCON-1 losses {len(defcon_rows)}/{n} -- last decision before each:")
        causes = collections.Counter()
        for r in defcon_rows:
            observation, decision, action = r["log"][-1]
            card = decision.context.get("card") or action.payload.get("card") or ""
            causes[
                f"{decision.kind.value}"
                f"({action.payload.get('mode') or action.payload.get('order') or card})"
            ] += 1
        for cause, count in causes.most_common():
            print(f"      {count:4d}  {cause}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--us", default="greedy", help="greedy | mcts | random | first | rl")
    ap.add_argument("--ussr", default="greedy")
    ap.add_argument("--games", type=int, default=40, help="seeds 1..N, one game each")
    ap.add_argument("--seed-base", type=int, default=1)
    ap.add_argument("--sims", type=int, default=8, help="MCTS simulations per decision")
    ap.add_argument(
        "--events", action=argparse.BooleanOptionalAction, default=True,
        help="Pass events= to Engine.new_game (default: on).",
    )
    args = ap.parse_args()

    us = PlayerSpec("us", args.us, sims=args.sims)
    ussr = PlayerSpec("ussr", args.ussr, sims=args.sims)
    rows = [
        summarize(play_probed_game(args.seed_base + i, us, ussr, args.events))
        for i in range(args.games)
    ]
    report(rows, us, ussr, args.events)


if __name__ == "__main__":
    main()
