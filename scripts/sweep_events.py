"""Fire every implemented event on a stocked board and drain all follow-up
decisions with option-0 play. Catches crashes and hangs in event flows.

    .venv/bin/python scripts/sweep_events.py [--seed N]

Each card resolves on a fresh engine: mid-game-ish influence spread, full
hands, DEFCON 5. A decision loop answers everything with options[0] until
the stack drains, the game ends, or 300 steps pass. Report lines:

    ok     <card> (<side>): <n> decisions drained
    skip   <card> (<side>): precondition unmet, nothing fired
    FAIL   <card> (<side>): <error>
    HANG   <card> (<side>): stack never drained
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.engine import Engine, Side  # noqa: E402
from struggler.engine.events import EVENTS  # noqa: E402

MAX_STEPS = 300

# A mid-game-ish spread so events have legal targets. Stability-exact
# stacks mean control for the stacking side.
SPREAD = {
    "US": ["UK", "West_Germany", "France", "Italy", "Canada", "Japan",
           "Israel", "Iran", "South_Korea", "Australia", "Spain_Portugal",
           "Greece", "Turkey", "Egypt", "Malaysia", "Colombia", "Brazil"],
    "USSR": ["East_Germany", "Poland", "Czechoslovakia", "Hungary", "Romania",
             "Yugoslavia", "Finland", "Syria", "Iraq", "North_Korea", "Cuba",
             "Vietnam", "Indonesia", "Angola", "Ethiopia", "Argentina"],
}

HAND = ["Duck_and_Cover", "Five_Year_Plan", "Socialist_Governments", "Fidel",
        "Blockade", "Nuclear_Test_Ban", "Nasser", "Suez_Crisis"]


def stocked(seed: int) -> Engine:
    engine = Engine(seed=seed)
    engine.events_enabled = True
    for side, countries in SPREAD.items():
        for cid in countries:
            engine.board.influence[cid][side] = engine.board.countries[cid].stability
    engine.hands = {"US": list(HAND), "USSR": list(HAND)}
    return engine


def fingerprint(engine: Engine) -> dict:
    return {
        "vp": engine.vp, "defcon": engine.defcon, "turn": engine.turn,
        "hands": {s: list(h) for s, h in engine.hands.items()},
        "influence": {c: dict(v) for c, v in engine.board.influence.items()},
        "turn_effects": dict(engine.turn_effects),
        "game_effects": dict(engine.game_effects),
        "space_race": dict(engine.space_race),
        "military_ops": dict(engine.military_ops),
        "discard": list(engine.discard_pile),
        "china": (engine.china_card_owner, engine.china_card_available),
    }


def _opp(side: Side) -> Side:
    return Side.USSR if side is Side.US else Side.US


def _setup_default(engine: Engine, side: Side) -> None:
    # Contested stacks so control-flipping events register a change.
    engine.board.influence["Cuba"]["US"] = 2
    engine.board.influence["Romania"]["US"] = 2
    engine.board.influence["Nicaragua"]["US"] = 2
    engine.board.influence["Libya"]["USSR"] = 3
    engine.board.influence["Austria"]["USSR"] = 2
    engine.military_ops[side.value] = 3


def _setup_nato(engine: Engine, side: Side) -> None:
    engine.game_effects["marshall_or_warsaw"] = True


def _setup_solidarity(engine: Engine, side: Side) -> None:
    engine.game_effects["john_paul"] = True


def _setup_wargames(engine: Engine, side: Side) -> None:
    engine.defcon = 2
    engine.vp = 19 if side is Side.US else -19


def _setup_cambridge(engine: Engine, side: Side) -> None:
    engine.hands["US"].append("Europe_Scoring")


def _setup_one_small_step(engine: Engine, side: Side) -> None:
    engine.space_race[_opp(side).value] = 1


def _setup_our_man(engine: Engine, side: Side) -> None:
    engine.draw_pile.extend(["Fidel", "Nasser", "Blockade", "Duck_and_Cover", "Nasser"])


def _setup_star_wars(engine: Engine, side: Side) -> None:
    engine.discard_pile.append("Fidel")
    engine.board.influence["Cuba"]["US"] = 2  # so the taken Fidel registers
    engine.space_race["US"] = 1  # Star_Wars requires a US space lead


SETUPS = {
    "Arms_Race": _setup_default,
    "Fidel": _setup_default,
    "Nixon_Plays_The_China_Card": _setup_default,
    "One_Small_Step": _setup_one_small_step,
    "Ortega_Elected_in_Nicaragua": _setup_default,
    "Our_Man_In_Tehran": _setup_our_man,
    "Reagan_Bombs_Libya": _setup_default,
    "Romanian_Abdication": _setup_default,
    "Solidarity": _setup_solidarity,
    "Star_Wars": _setup_star_wars,
    "The_Cambridge_Five": _setup_cambridge,
    "Truman_Doctrine": _setup_default,
    "Ussuri_River_Skirmish": _setup_default,
    "Wargames": _setup_wargames,
    "NATO": _setup_nato,
}


def sweep_one(cid: str, side: Side, seed: int) -> str:
    engine = stocked(seed)
    hook = SETUPS.get(cid)
    if hook is not None:
        hook(engine, side)
    before = fingerprint(engine)
    engine._fire_event(side, cid)
    pend = engine.pending_decision
    if pend is None and fingerprint(engine) == before and not engine.is_terminal:
        return "skip"
    steps = 0
    while not engine.is_terminal and steps < MAX_STEPS:
        pend = engine.pending_decision
        if pend is None:
            break
        if not pend.options:
            return f"FAIL no options for {pend.kind.value}"
        engine.step(pend.options[0])
        steps += 1
    if steps >= MAX_STEPS and engine.pending_decision is not None:
        return "HANG"
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()
    from struggler.engine.cards import load_cards

    cards = load_cards()
    failures = 0
    for cid in sorted(EVENTS):
        side_name = cards[cid].side.value
        sides = [Side.US, Side.USSR] if side_name == "NEUTRAL" else [Side(side_name)]
        for side in sides:
            try:
                result = sweep_one(cid, side, args.seed)
            except Exception as exc:  # noqa: BLE001 — the sweep is the catcher
                result = f"FAIL {type(exc).__name__}: {exc}"
                traceback.print_exc()
            print(f"{result.split()[0]:6} {cid} ({side.value}): {result}")
            if result.startswith(("FAIL", "HANG")):
                failures += 1
    print(f"\n{len(EVENTS)} events swept, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
