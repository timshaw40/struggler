"""How often DeepStruggle's engine agrees with struggler's, round by round.

Phase 0 of the deep bot (docs/BOTS.md, "Deep bot"). Plays struggler games
with greedy in both seats; at every headline and action-round start, writes
the position as a DeepStruggle save, replays that round's events on
DeepStruggle with struggler's dice, and compares the result with struggler's
position at the next boundary. Prints the agreement rate and the reasons for
every disagreement, grouped.

The census reads the full engine state: it measures whether the *rules*
agree, not what a player may know. The player itself never does this.

    STRUGGLER_DEEP_ENGINE=/path/to/DeepStruggle/build/release \\
        python scripts/deep_sync_census.py --games 40
"""
from __future__ import annotations

import argparse
import collections
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.bots.deep import boundary  # noqa: E402
from struggler.bots.deep.backend import NativeBackend  # noqa: E402
from struggler.bots.deep.ids import IdMap  # noqa: E402
from struggler.bots.deep.replay import Desync, replay  # noqa: E402
from struggler.bots.greedy import GreedyPlayer  # noqa: E402
from struggler.engine import Engine, Side  # noqa: E402
from struggler.engine.types import DecisionKind  # noqa: E402


@dataclass(frozen=True)
class LoggedEvent:
    """What `replay` reads from a struggler event, in true resolution order
    (the player-facing history buffers headline picks; the census does not)."""

    decision: Any
    action: Any


def is_boundary(engine: Engine) -> bool:
    d = engine.pending_decision
    if d is None or len(engine._decision_stack) != 1:
        return False
    if d.kind is DecisionKind.ACTION_ROUND_PLAY:
        return True
    return d.kind is DecisionKind.HEADLINE_PLAY and not any(engine._headline.values())


def compare(ds_save: dict, engine: Engine, ids: IdMap, same_turn: bool) -> list[str]:
    diffs = []
    for cid, n in ids.country_to_ds.items():
        s = engine.board.influence[cid]
        d = (ds_save["us_influence"][n], ds_save["ussr_influence"][n])
        if (s["US"], s["USSR"]) != d:
            diffs.append(f"influence {cid}: struggler {s['US']}/{s['USSR']} ds {d[0]}/{d[1]}")
    checks = [("space US", engine.space_race["US"], ds_save["us_space_track"]),
              ("space USSR", engine.space_race["USSR"], ds_save["ussr_space_track"])]
    if same_turn:
        checks += [("vp", engine.vp, ds_save["victory_points"]),
                   ("defcon", engine.defcon, ds_save["defcon"]),
                   ("milops US", engine.military_ops["US"], ds_save["us_mil_ops"]),
                   ("milops USSR", engine.military_ops["USSR"], ds_save["ussr_mil_ops"])]
    diffs += [f"{name}: struggler {a} ds {b}" for name, a, b in checks if a != b]
    return diffs


def run_game(seed: int, events: bool, ids: IdMap, backend: NativeBackend,
             reasons: collections.Counter, examples: dict, totals: collections.Counter) -> None:
    engine = Engine.new_game(seed=seed, events=events, include_ccw=False, setup_us_extra=2)
    players = {Side.US: GreedyPlayer(), Side.USSR: GreedyPlayer()}
    facts = None
    round_events: list[LoggedEvent] = []
    start_turn = 0

    def close_round() -> None:
        if facts is None:
            return
        totals["rounds"] += 1
        label = f"seed {seed} T{facts.turn} {facts.phase} AR{facts.action_round} {facts.phasing or ''}"
        try:
            backend.load_save(boundary.to_save(facts, ids))
            replay(backend, round_events, ids)
            diffs = compare(backend.save(), engine, ids, same_turn=engine.turn == start_turn)
            if diffs:
                field = diffs[0].split(":")[0].split(" ")[0]
                raise Desync(f"state_differs:{field}", "; ".join(diffs[:3]))
            totals["agree"] += 1
        except boundary.UnmappedEffect as exc:
            reasons[f"unmapped_effect:{exc}"] += 1
            examples.setdefault(f"unmapped_effect:{exc}", label)
        except Desync as exc:
            reasons[exc.reason] += 1
            examples.setdefault(exc.reason, f"{label}: {exc.detail}")

    steps = 0
    while not engine.is_terminal and steps < 5000:
        if is_boundary(engine):
            close_round()
            facts = boundary.from_engine(engine)
            start_turn = engine.turn
            round_events = []
        d = engine.pending_decision
        if d.actor is Side.CHANCE:
            action = d.options[0]
        else:
            action = players[d.actor].choose_action(engine.observe(d.actor), [])
        engine.step(action)
        round_events.append(LoggedEvent(d, action))
        steps += 1
    close_round()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--games", type=int, default=40)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--events", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--engine-dir", default=None, help="DeepStruggle build dir (else STRUGGLER_DEEP_ENGINE)")
    args = parser.parse_args()

    backend = NativeBackend(args.engine_dir)
    probe = Engine.new_game(seed=0, include_ccw=False)
    ids = IdMap.build(backend.country_names(), list(probe.board.countries))
    reasons: collections.Counter = collections.Counter()
    examples: dict = {}
    totals: collections.Counter = collections.Counter()
    for k in range(args.games):
        run_game(args.seed + k, args.events, ids, backend, reasons, examples, totals)
    rounds, agree = totals["rounds"], totals["agree"]
    print(f"{args.games} games, events {'on' if args.events else 'off'}: "
          f"{agree}/{rounds} rounds agree ({100 * agree / max(rounds, 1):.1f}%)")
    for reason, count in reasons.most_common():
        print(f"  {count:5d}  {reason}\n         e.g. {examples[reason]}")


if __name__ == "__main__":
    main()
