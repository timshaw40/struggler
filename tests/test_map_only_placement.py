"""Influence placement is driven from the map, and the status bar says how.

The placement box listed every legal country as a button, which meant the
player read the directive inside a panel covering the map they were supposed to
click. Placement is now map-only: the box is gone, the directive ("USA: click a
country to add 1 influence in Western Europe — 7 influence remaining") lives in
the status bar, and the bar grows the spend's own Done button. Every other
country pick keeps its box and its keyboard-reachable list.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "ui" / "app.js").read_text()
CSS = (ROOT / "ui" / "style.css").read_text()


def body_of(name: str) -> str:
    match = re.search(rf"function {name}\(.*?\) \{{(.*?\n)\}}", APP, re.S)
    assert match, f"{name}() not found"
    return match.group(1)


def test_only_placement_is_map_only():
    guard = body_of("isMapOnlyPick")
    assert 'd.kind === "place_influence"' in guard
    assert "o.payload && o.payload.country" in guard, "the guard must need a country option"
    # The box's country list is skipped for exactly that case, and hidden.
    decision = body_of("renderDecision")
    skip = re.search(r"if \(isMapOnlyPick\(d\)\) \{(.*?)\n  \}", decision, re.S)
    assert skip, "the map-only early return is gone"
    assert "box.hidden = true" in skip.group(1), "the box must not linger over the map"
    # The pick band (which shrinks the map) is not applied to placement.
    assert "const pick = !isMapOnlyPick(d) && !!d && COUNTRY_PICK_KINDS.has(d.kind);" in decision


def test_the_directive_counts_the_right_budget():
    directive = body_of("placementDirective")
    # Setup spends influence; an Ops spend spends Ops (one Op can buy two
    # influence in an opponent-controlled country), so they cannot share a noun.
    assert 'c.setup && typeof c.remaining === "number"' in directive
    assert "influence remaining" in directive
    assert "placementOperationsLine(d)" in directive
    # Region names arrive as enum values.
    assert "regionLabel(c.subregion)" in directive
    ops = body_of("placementOperationsLine")
    assert "placementOpsLeft(d)" in ops
    assert "Ops remaining" in ops
    assert "last one" in ops


def test_the_status_bar_carries_the_directive_and_the_done_button():
    bar = body_of("renderActionBar")
    assert "placementDirective(d)" in bar
    assert "o.payload.stop" in bar, "Done comes from the decision's own stop option"
    assert "act(doneOpt.index)" in bar, "Done must submit the engine's option index"
    # A stale button from the previous decision must never remain clickable.
    assert bar.index("stale.remove()") < bar.index("if (doneOpt)"), \
        "clean up before the early returns, or a stale Done outlives its decision"
    # Placement shares the bar with the resolved-action caption it replaces.
    show = body_of("showAction")
    assert "actionBarLastAction" in show
    assert "renderActionBar({ bump: true })" in show


def test_the_bar_is_clickable_but_only_its_button():
    # The pill spans the bottom of the map, so the text must stay click-through
    # and only the control it grows may take the pointer.
    assert "pointer-events: none" in re.search(r"#actionbar \{(.*?)\}", CSS, re.S).group(1)
    button = re.search(r"#actionbar \.donebtn \{(.*?)\}", CSS, re.S)
    assert button, "no styling for the Done button"
    assert "pointer-events: auto" in button.group(1)


def test_placement_still_updates_when_only_the_budget_changes():
    """The render dedupe keys off the option list, which is identical before and
    after a placement inside the same spend — so the bar has to be keyed too, or
    the count would freeze at its first value."""
    render = body_of("render")
    assert "rem:${d.context.remaining}" in render
    assert "left:${placementOpsLeft(d)}" in render


def test_enter_finishes_a_spend_but_never_places():
    """With no box there are no option buttons, so Enter posts the bar's Done —
    and does nothing at all when the engine offers no stop."""
    keyboard = body_of("installKeyboard")
    assert '#actionbar .donebtn:not(:disabled)' in keyboard
    assert 'e.key === "Enter"' in keyboard
    assert "done.click()" in keyboard
