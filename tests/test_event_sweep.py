"""Every implemented event fires and drains without crashing, hanging,
or silently no-opping. The sweep lives in scripts/sweep_events.py so it
can also run standalone for triage; this test pins the result."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "sweep_events", ROOT / "scripts" / "sweep_events.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_all_events_fire_and_drain():
    sweep = _load()
    from struggler.engine.cards import load_cards
    from struggler.engine import Side

    cards = load_cards()
    bad: list[str] = []
    for cid in sorted(sweep.EVENTS):
        side_name = cards[cid].side.value
        sides = [Side.US, Side.USSR] if side_name == "NEUTRAL" else [Side(side_name)]
        for side in sides:
            try:
                result = sweep.sweep_one(cid, side, 1234)
            except Exception as exc:  # noqa: BLE001 — report, don't abort
                result = f"FAIL {type(exc).__name__}: {exc}"
            if result != "ok":
                bad.append(f"{cid} ({side.value}): {result}")
    assert not bad, "event sweep failures:\n" + "\n".join(bad)
