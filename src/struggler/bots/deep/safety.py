"""Take a win the network would decline.

DeepStruggle's expert review (research/log/expert_review_E7.md) found the
network declining decisive wins: Wargames at a winning lead 429 of 430 times,
and events that would reach 20 VP about half the time. Self-play cannot see
these leaks because both seats share them. So before trusting the network,
the player asks: does one of my options win the game outright?

"Outright" follows the review's definition: a line that ends the game for the
mover using only the mover's own choices, decisions with a single legal
option, and dice that win on every face. No opponent choice, no hidden draw
(a step that consumes DeepStruggle's RNG disqualifies the line, since a
determinized card could be behind it). The search is bounded and runs only
where such a win is plausible, so it costs nothing on most moves.
"""
from __future__ import annotations

from struggler.bots.deep import backend as B

#: Decisions where the review's leaks live: the card, how it resolves, the
#: event's branch (Wargames), and a deferred Ops choice. Influence placement
#: is not searched: its branching is large and no outright win hides there.
SEARCHED = {B.SELECT_CARD, B.SELECT_PLAY_MODE, B.CHOOSE_BRANCH, B.SELECT_OP_MODE}
_SINGLE_DIE_ROLLS = {1, 3, 4, 7, 8}  # coup, space race, war event, trap escape, turn cleanup (ts::RollType)
_MAX_NODES = 400
_MAX_DEPTH = 8


def worth_searching(vp: int, defcon: int, turn: int, mover: int) -> bool:
    """A cheap filter: an outright win needs the mover near 20 VP, DEFCON at 2
    (Wargames, a coup the opponent cannot survive), or the last turn."""
    lead = vp * mover  # DeepStruggle's VP is US-positive; mover is +1 (US) / -1 (USSR)
    return lead >= 10 or defcon <= 2 or turn >= 10


def winning_slots(engine: B.DeepEngine, slots: list[int], mover: int) -> list[int]:
    """The candidate `slots` whose line wins outright for `mover`. Leaves the
    engine where it found it."""
    base = engine.save()
    wins = []
    budget = [_MAX_NODES]
    try:
        for slot in slots:
            engine.load_save(base)
            if _step_flat_deterministic(engine, slot) and _wins(engine, mover, _MAX_DEPTH, budget):
                wins.append(slot)
    finally:
        engine.load_save(base)
    return wins


def _wins(engine: B.DeepEngine, mover: int, depth: int, budget: list[int]) -> bool:
    budget[0] -= 1
    if budget[0] <= 0:
        return False
    if engine.is_terminal():
        vp = engine.save()["victory_points"]
        return vp * mover > 0
    if depth == 0:
        return False
    player, dtype = engine.decision()
    here = engine.save()
    if dtype == B.ROLL_DIE:
        roll = here["ctx_stack"][here["ctx_stack_depth"]].get("pending_roll")
        if roll not in _SINGLE_DIE_ROLLS:
            return False
        for face in range(1, 7):  # every face must win
            engine.load_save(here)
            if not engine.roll(face) or not _wins(engine, mover, depth - 1, budget):
                return False
        return True
    legal = [i for i, v in enumerate(engine.mask()) if v]
    if player != mover:
        if len(legal) != 1:
            return False  # the opponent has a choice: not forced
        return _step_flat_deterministic(engine, legal[0]) and _wins(engine, mover, depth - 1, budget)
    if len(legal) > 1 and dtype not in SEARCHED:
        return False
    for slot in legal:
        engine.load_save(here)
        if _step_flat_deterministic(engine, slot) and _wins(engine, mover, depth - 1, budget):
            return True
    return False


def _step_flat_deterministic(engine: B.DeepEngine, slot: int) -> bool:
    """Step, and refuse the line if the step drew on the RNG (a hidden draw)."""
    before = engine.save()["rng_state"]
    if not engine.step_flat(slot):
        return False
    return engine.save()["rng_state"] == before
