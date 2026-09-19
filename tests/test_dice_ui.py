"""Guards for the dice surface: every roll the engine can produce must have a
UI path that draws it, and the popup must sit above the action box.

The 2026-09 regression was the second kind: the dice box rendered at
z-index 20 while the floating action box sits at 30, both centred on the map,
so rolls happened invisibly behind it. The first is a standing risk whenever a
new roll decision is added to the engine.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from struggler.engine import DecisionKind

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "ui" / "app.js").read_text()
CSS = (ROOT / "ui" / "style.css").read_text()


def client_roll_kinds() -> set[str]:
    block = re.search(r"const ROLL_KIND = \{(.*?)\};", APP, re.S)
    assert block, "ROLL_KIND not found in ui/app.js"
    return set(re.findall(r"([a-z_]+):", block.group(1)))


def z_index(css: str, selector: str) -> int:
    rule = re.search(selector + r"\s*([^}]*)\}", css)
    assert rule, f"no rule for {selector}"
    match = re.search(r"z-index:\s*(\d+)", rule.group(1))
    assert match, f"no z-index in {selector}"
    return int(match.group(1))


def test_client_draws_every_engine_roll_kind():
    engine = {k.value for k in DecisionKind if "roll" in k.value}
    missing = sorted(engine - client_roll_kinds())
    assert not missing, f"dice rolls with no UI animation: {missing}"


def test_client_roll_list_has_no_stale_kinds():
    known = {k.value for k in DecisionKind}
    extra = sorted(client_roll_kinds() - known)
    assert not extra, f"ui/app.js animates kinds the engine does not have: {extra}"


def test_both_multi_dice_shapes_are_read():
    """A contest roll carries both dice in one event; a realignment carries one
    die per side in two events. rollValues has to know both."""
    body = re.search(r"function rollValues\(item\) \{(.*?)\n\}", APP, re.S)
    assert body, "rollValues not found"
    assert "sponsor_roll" in body.group(1) and "defender_roll" in body.group(1)
    assert "realignment" in body.group(1) and "item.actor.payload.value" in body.group(1)


def test_the_dice_popup_sits_above_the_action_box():
    """Only meaningful once the floating action box exists -- that box is the
    thing the dice popup can hide behind (it did, in September 2026)."""
    if "body.decision-center" not in CSS:
        pytest.skip("floating action box is not in this tree")
    dice = z_index(CSS, r"#dicebox\s*\{")
    decision = z_index(CSS, r"body\.decision-center #decision:not\(\[hidden\]\)\s*\{")
    assert dice > decision, (
        f"the dice box (z-index {dice}) would paint under the floating action "
        f"box (z-index {decision}) and rolls would be invisible"
    )
