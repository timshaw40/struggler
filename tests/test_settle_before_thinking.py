""""Thinking…" must wait for the player's own influence to land.

The bug: busy/thinking was keyed off the network round trip. Clicking the last
Op set `busy = true`, which immediately rendered the feed's "Thinking…" row and
the tab's "resolving…" — before the influence was on the board, let alone before
the chit had landed. The board's counters update from the server response, but
the chit is an overlay that flies for another 400ms, so the announcement has to
follow the animation, not the request.

The rule now: `settling()` (a round trip in flight, FX queued, or a chit in the
air) means the player's own move is still going down. Only when nothing is
settling does the interface claim the opponent has the floor.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "ui" / "app.js").read_text()


def body_of(name: str) -> str:
    match = re.search(rf"function {name}\(.*?\) \{{(.*?\n)\}}", APP, re.S)
    assert match, f"{name}() not found"
    return match.group(1)


def test_settling_covers_the_round_trip_the_queue_and_the_chit():
    settling = body_of("settling")
    for part in ("busy", "fxBacklog > 0", "chitsInFlight > 0"):
        assert part in settling, part


def test_a_chit_that_bypasses_the_fx_queue_is_still_tracked():
    """The backlog branch calls flyBatch directly (the player's own chits are
    never dropped), so it has to register the flight itself or the note would
    appear on top of it."""
    placements = body_of("enqueuePlacements")
    assert "trackLanded" in placements
    track = body_of("trackLanded")
    assert "chitsInFlight += 1" in track
    assert "chitsInFlight -= 1" in track
    assert "flushSettleWaiters()" in track
    # The queued path returns the chain so the caller can register it too.
    assert "return queued;" in body_of("enqueueFx")


def test_the_feed_row_and_the_tab_agree_with_settling():
    # The feed row is what the player actually reads while waiting.
    panel = body_of("renderPanel")
    row = re.search(r"const waiting = (.*?);", panel, re.S)
    assert row, "the feed's waiting predicate is gone"
    assert "settling()" in row.group(1), "the feed must not claim Thinking while settling"
    assert "!busy && !settling()" in row.group(1), "and must claim it once nothing settles"
    # The tab says the same thing.
    cue = body_of("renderCue")
    assert "settling()" in cue
    assert 'title = "Struggler — resolving…"' in cue


def test_the_feed_is_re_rendered_when_the_board_settles():
    """Otherwise the row stays up (or stays absent) until the next poll happens
    to arrive, which is what made the ordering feel wrong in the first place."""
    fx = body_of("enqueueFx")
    assert "flushSettleWaiters()" in fx
    assert "renderPanel()" in fx, "the feed's Thinking row must be re-evaluated on drain"
    # …and the decision box, which is the other half of the same claim.
    assert "renderDecision()" in fx


def test_settle_waiters_fire_once_the_board_is_quiet():
    wait = body_of("waitForSettled")
    assert "if (!settling()) { fn(); return; }" in wait, "already-settled callers must not wait"
    assert "settleWaiters.add(fn)" in wait
    flush = body_of("flushSettleWaiters")
    assert "if (settling()) return;" in flush, "a waiter must not fire early"
    assert "settleWaiters.clear()" in flush


def test_act_waits_for_the_landing_before_it_can_say_thinking():
    act = body_of("act")
    assert "busy = false" in act and "render()" in act
    assert "waitForSettled(" in act, "the box is re-rendered once the chit lands"
