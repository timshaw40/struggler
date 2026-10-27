"""Replay struggler's events for one round on a DeepStruggle engine.

The two engines cut a turn into decisions differently. DeepStruggle asks once
how a card resolves ("event", "space", or "Ops for influence / coup /
realignment", with the event-first/Ops-first order folded in), where struggler
asks up to three times (`PLAY_MODE`, `EVENT_OPS_ORDER`, `OPS_TYPE`). So the
replay is driven by DeepStruggle's pending decision: at each one it consumes
as many struggler events as that decision needs, then steps it.

Dice are never rolled here. Each DeepStruggle chance node is stepped with the
die struggler logged for it (`Backend.roll`), so both engines see the same
dice; a realignment's two struggler rolls are one DeepStruggle node.

When struggler's log runs out part way through a DeepStruggle decision (the
player is in the middle of composing one), `replay` returns the partial group
so the caller can score struggler's current options against the slots that
group still allows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Sequence

from struggler.bots.deep import backend as B
from struggler.bots.deep.choices import (AFFIRM_TARGET, GRAIN_SALES, OUR_MAN, PeekMismatch, choice_slots,
                                         peek, peeked)
from struggler.bots.deep.ids import IdMap, ds_side
from struggler.engine.types import DecisionKind as K


class Desync(RuntimeError):
    """The two engines disagree, or a decision has no mapping yet. `reason`
    is a short stable key the census aggregates on."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


_ROLL_KINDS = {K.COUP_ROLL, K.SPACE_RACE_ROLL, K.WAR_ROLL, K.QUAGMIRE_ROLL}
_COUNTRY_KINDS = {K.PLACE_INFLUENCE, K.COUP_TARGET, K.REALIGNMENT_TARGET, K.EVENT_INFLUENCE,
                  K.WAR_TARGET}
_CARD_KINDS = {K.HEADLINE_PLAY, K.ACTION_ROUND_PLAY}
#: struggler decisions with no DeepStruggle counterpart: skipped.
_SKIP_KINDS = {K.EVENT_RESUME}

_OPS_TYPE_RES = {"influence": B.RES_OPS_INFLUENCE, "coup": B.RES_OPS_COUP,
                 "realignment": B.RES_OPS_REALIGN}
_OPS_TYPE_OPMODE = {"influence": 0, "coup": 1, "realignment": 2}


@dataclass
class Partial:
    """struggler events already consumed toward the DeepStruggle decision now
    pending: e.g. `PLAY_MODE` "ops" whose Ops type is not chosen yet."""

    ds_decision: int
    events: list[Any] = field(default_factory=list)


@dataclass
class ReplayResult:
    steps: int
    partial: Partial | None
    #: Our Man in Tehran cards the US has kept so far this round (DeepStruggle
    #: ids), when it saw them: the player's next keep/remove needs them.
    our_man_kept: list[int] = field(default_factory=list)


def _kind(event: Any) -> K:
    return event.decision.kind


def _payload(event: Any) -> dict:
    return dict(event.action.payload)


def replay(engine: B.DeepEngine, events: Sequence[Any], ids: IdMap) -> ReplayResult:
    """Step `engine` through `events` (struggler `Event`s, oldest first)."""
    queue = [e for e in events if _kind(e) not in _SKIP_KINDS]
    if any(_kind(e) is K.RANDOM_DISCARD for e in queue):
        engine = _Rewindable(engine)  # so a random draw can be redrawn (_check_random_card)
    i = 0
    steps = 0
    kept: list[int] = []

    def take() -> Any:
        nonlocal i
        event = queue[i]
        i += 1
        return event

    while i < len(queue):
        if engine.is_terminal():
            raise Desync("ds_terminal_early", f"{len(queue) - i} struggler events left")
        player, dtype = engine.decision()

        if _kind(queue[i]) is K.RANDOM_DISCARD:
            # DeepStruggle drew the card from its own RNG inside the step that
            # led here; struggler logged its draw. Agree or give up the round.
            _check_random_card(engine, take(), ids)
            continue

        if _kind(queue[i]) is K.EVENT_CHOICE and dtype != B.ROLL_DIE:
            event = take()
            event_name = event.decision.context.get("event", "")
            choice = str(_payload(event).get("choice"))
            if event_name == GRAIN_SALES and dtype == B.SELECT_PLAY_MODE:
                if choice == "take":
                    # DeepStruggle's one decision is "take, and play it this way";
                    # struggler asks the play mode next, which steps it.
                    if i >= len(queue):
                        return ReplayResult(steps, Partial(dtype, [event]), kept)
                    continue
                if choice == "return":
                    _step(engine, B.CONFIRM_DONE_INDEX, "choice", event)
                    steps += 1
                    continue
            if event_name == OUR_MAN and dtype == B.SELECT_CARD:
                steps += _our_man_step(engine, event, choice, kept, ids)
                continue
            if dtype == B.POINT_NODE and choice in AFFIRM_TARGET:
                # "Yes, coup": struggler asks the target next; DeepStruggle's node
                # decision is that target. Step it when the target event comes.
                if i >= len(queue):
                    return ReplayResult(steps, Partial(dtype, [event]), kept)
                continue
            slots = choice_slots(event_name, choice, dtype, engine.mask(), ids)
            if len(slots) != 1:
                raise Desync(f"unmapped_choice:{event_name}", f"{choice} at ds decision {dtype}")
            _step(engine, slots[0], "choice", event)
            steps += 1
            continue

        if dtype == B.ROLL_DIE:
            if _kind(queue[i]) not in _ROLL_KINDS | {K.REALIGNMENT_ACTOR_ROLL, K.CONTEST_ROLL}:
                # A DeepStruggle chance node struggler does not log (turn cleanup).
                _cleanup_or_fail(engine, queue[i])
                steps += 1
                continue
            event = take()
            if _kind(event) is K.CONTEST_ROLL:
                ok = engine.roll(*_contest_dice(event))
            elif _kind(event) is K.REALIGNMENT_ACTOR_ROLL:
                if i >= len(queue):
                    return ReplayResult(steps, Partial(dtype, [event]), kept)
                opp = take()
                if _kind(opp) is not K.REALIGNMENT_OPPONENT_ROLL:
                    raise Desync("realign_dice", str(_kind(opp)))
                ok = engine.roll(_payload(event)["value"], _payload(opp)["value"])
            else:
                ok = engine.roll(_payload(event)["value"])
            if not ok:
                raise Desync("roll_refused", f"{_kind(event).value}")
            steps += 1
            continue

        if _kind(queue[i]) is K.HELD_CARD_DISCARD and dtype == B.SELECT_CARD:
            # Space Race box 6 at the turn's end: DeepStruggle asks it as a card
            # choice with "done" to keep the card.
            event = take()
            _step(engine, held_card_slot(_payload(event).get("card"), ids), "held_card", event)
            steps += 1
            continue

        if dtype in (B.SELECT_CARD, B.POINT_NODE) and _event_waits_for_done(engine, queue[i], dtype):
            # struggler finished the event on its own (nothing left to choose);
            # DeepStruggle still waits for "done".
            if not engine.step_flat(B.CONFIRM_DONE_INDEX):
                raise Desync("implicit_done_refused", _kind(queue[i]).value)
            steps += 1
            continue

        if dtype == B.SELECT_CARD:
            # Headline picks are simultaneous: struggler's player-facing history
            # appends both once the second is in, in its own order, so take the
            # pick of the side DeepStruggle is asking.
            if _kind(queue[i]) is K.HEADLINE_PLAY:
                for j in range(i, min(i + 2, len(queue))):
                    if _kind(queue[j]) is K.HEADLINE_PLAY and ds_side(queue[j].decision.actor) == player:
                        queue.insert(i, queue.pop(j))
                        break
            event = take()
            card = _payload(event).get("card")
            if card is None:
                raise Desync("select_card_without_card", _kind(event).value)
            _step(engine, B.CARD_OFFSET + ids.card_to_ds[card] - 1, "card", event)
            steps += 1
            # A scoring card resolves on selection in DeepStruggle; struggler
            # still asks its (single) play mode. Nothing to step for it.
            if (i < len(queue) and _kind(queue[i]) is K.PLAY_MODE
                    and _payload(queue[i]).get("mode") == "event"
                    and engine.decision()[1] != B.SELECT_PLAY_MODE):
                take()
            continue

        if dtype == B.SELECT_PLAY_MODE:
            group: list[Any] = []
            index = None
            while index is None:
                if i >= len(queue):
                    return ReplayResult(steps, Partial(dtype, group), kept)
                event = take()
                group.append(event)
                index = _play_mode_index(group)
            _step(engine, index, "play_mode", event)
            steps += 1
            continue

        if dtype == B.SELECT_OP_MODE:
            event = take()
            if _kind(event) is not K.OPS_TYPE:
                raise Desync("op_mode_expected_ops_type", _kind(event).value)
            _step(engine, B.OP_MODE_OFFSET + _OPS_TYPE_OPMODE[_payload(event)["type"]], "op_mode", event)
            steps += 1
            continue

        if dtype == B.POINT_NODE:
            event = take()
            payload = _payload(event)
            country = payload.get("country") if _kind(event) in _COUNTRY_KINDS else None
            if _kind(event) is K.EVENT_CHOICE and payload.get("choice") in ids.country_to_ds:
                country = payload["choice"]  # a per-event country pick (De-Stalinization's removals)
            if payload.get("stop") or payload.get("choice") in ("stop", "done"):
                _step(engine, B.CONFIRM_DONE_INDEX, "stop", event)
            elif country is not None:
                n = ids.country_to_ds.get(country)
                if n is None:
                    raise Desync("unmapped_country", country)
                _step(engine, B.NODE_OFFSET + n, "node", event)
            else:
                raise Desync("node_expected_country", _kind(event).value)
            steps += 1
            continue

        raise Desync(f"unmapped_ds_decision_{dtype}", _kind(queue[i]).value)
    return ReplayResult(steps, None, kept)


def held_card_slot(card: Any, ids: IdMap) -> int:
    """struggler's box-6 answer (a card, or "none") as DeepStruggle's slot."""
    if not card or card == "none":
        return B.CONFIRM_DONE_INDEX
    return B.CARD_OFFSET + ids.card_to_ds[card] - 1


def finish_open_event(engine: B.DeepEngine, decision: Any) -> bool:
    """At the end of the log, bring DeepStruggle to the player's `decision`:
    step the turn-cleanup chance node when struggler has moved past the
    turn's action rounds (box 6's held-card discard), and step "done" when
    DeepStruggle still waits for it inside an event struggler has closed, as
    the replay does mid-log. True if it stepped."""
    stepped = False
    save = engine.save()
    if save["ctx_stack"][save["ctx_stack_depth"]].get("pending_roll") == _TURN_CLEANUP \
            and engine.decision()[1] == B.ROLL_DIE and decision.kind is K.HELD_CARD_DISCARD:
        stepped = bool(engine.roll(0))
    player, dtype = engine.decision()
    if decision.kind is K.HELD_CARD_DISCARD:
        return stepped
    probe = SimpleNamespace(decision=decision)
    if dtype in (B.SELECT_CARD, B.POINT_NODE) and _event_waits_for_done(engine, probe, dtype):
        return bool(engine.step_flat(B.CONFIRM_DONE_INDEX)) or stepped
    return stepped


def _our_man_step(engine: B.DeepEngine, event: Any, choice: str, kept: list[int], ids: IdMap) -> int:
    """One of struggler's Our Man in Tehran keep/remove answers. The US's own
    history names the card; the USSR's does not, and then any peeked card not
    yet kept stands in (the discard pile at the next boundary says which)."""
    name = event.decision.context.get("card")
    card = ids.card_to_ds.get(name) if name else None
    if card is not None:
        try:
            peek(engine, [*kept, card])
        except PeekMismatch as exc:
            raise Desync("our_man_peek", str(exc)) from exc
    if choice == "keep":
        if card is not None:
            kept.append(card)
        return 0
    if choice != "remove":
        raise Desync(f"unmapped_choice:{OUR_MAN}", choice)
    if card is None:
        candidates = [c for c in peeked(engine) if c not in kept]
        if not candidates:
            raise Desync("our_man_peek", "nothing peeked to remove")
        card = candidates[0]
    _step(engine, B.CARD_OFFSET + card - 1, "choice", event)
    return 1


class _Rewindable:
    """A backend that remembers the position before its last step and how to
    take that step again, so a random draw DeepStruggle made inside the step
    can be made again with another RNG state."""

    def __init__(self, inner: B.DeepEngine) -> None:
        self._inner = inner
        self.before: dict | None = None
        self.redo: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def step_flat(self, index: int) -> bool:
        self.before, self.redo = self._inner.save(), lambda: self._inner.step_flat(index)
        return self._inner.step_flat(index)

    def roll(self, actor_die: int, opponent_die: int = 0) -> bool:
        self.before, self.redo = self._inner.save(), lambda: self._inner.roll(actor_die, opponent_die)
        return self._inner.roll(actor_die, opponent_die)


#: RNG states tried when redrawing (each draw picks one of at most ~9 cards).
_REDRAWS = 64


def _holder(loc: int) -> int:
    """Whose hand a `ts::CardLocation` is: 1 US, -1 USSR, 0 neither (known and
    unknown copies of a hand are the same hand)."""
    return 1 if loc in (2, 3) else (-1 if loc in (4, 5) else 0)


def _redraw(engine: Any, want: int) -> bool:
    """Make DeepStruggle's random draw in the last step be `want`, the card
    struggler drew. A swapped-in card would not fire its event (Five Year
    Plan drawing a US event fires it), so the step is taken again under other
    RNG states until the draw is `want`. If DeepStruggle's world put `want`
    outside the hand the draw came from, it first trades places with the card
    DeepStruggle drew. True when DeepStruggle has now drawn `want`."""
    before, redo = getattr(engine, "before", None), getattr(engine, "redo", None)
    if before is None or redo is None:
        return False
    after = engine.save()
    b_locs, a_locs = before["card_locations"], after["card_locations"]

    def left_hand(locs_before: list[int], locs_after: list[int], card: int) -> bool:
        held = _holder(locs_before[card])
        return held != 0 and _holder(locs_after[card]) != held

    if left_hand(b_locs, a_locs, want):
        return True  # already drawn (an event that fired and finished)
    drawn = [c for c in range(1, len(b_locs)) if left_hand(b_locs, a_locs, c)]
    locs = list(b_locs)
    if drawn and _holder(locs[want]) != _holder(locs[drawn[0]]):
        locs[want], locs[drawn[0]] = locs[drawn[0]], locs[want]
    if not _holder(locs[want]):
        return False
    seed = int(before["rng_state"])
    for k in range(1, _REDRAWS + 1):
        trial = {**before, "card_locations": locs,
                 "rng_state": (seed + k * 0x9E3779B97F4A7C15) & ((1 << 64) - 1)}
        engine.load_save(trial)
        if redo() and left_hand(locs, engine.save()["card_locations"], want):
            return True
    engine.load_save(after)
    return False


def _check_random_card(engine: B.DeepEngine, event: Any, ids: IdMap) -> None:
    """struggler logged a random draw; DeepStruggle drew its own, from its RNG,
    inside the step that led here, and staged it as the frame's
    `pending_op_card`. If the two differ, take the step again with other RNG
    states until DeepStruggle draws struggler's card (`_redraw`); failing
    that, swap struggler's card into that slot (and their locations), so the
    round goes on with the card struggler drew."""
    save = engine.save()
    frame = save["ctx_stack"][save["ctx_stack_depth"]]
    want = ids.card_to_ds[_payload(event)["card"]]
    got = frame.get("pending_op_card")
    if want == got or want == frame.get("resolving_card"):
        return
    redrawn = _redraw(engine, want)
    if isinstance(engine, _Rewindable):
        engine.before = None  # a second draw in the same step must not redo the first
    if redrawn:
        return
    save = engine.save()
    frame = save["ctx_stack"][save["ctx_stack_depth"]]
    got = frame.get("pending_op_card")
    if not got:
        raise Desync("random_card", f"struggler drew {_payload(event)['card']}, nothing staged")
    locs = save["card_locations"]
    locs[got], locs[want] = locs[want], locs[got]
    frame["pending_op_card"] = want
    engine.load_save(save)


def _contest_dice(event: Any) -> tuple[int, int]:
    """A struggler two-die contest as DeepStruggle's (primary, secondary) dice:
    Olympic Games takes the sponsor's die first, Summit the US's."""
    payload, ctx = _payload(event), event.decision.context
    sponsor, defender = payload["sponsor_roll"], payload["defender_roll"]
    if str(ctx.get("event", "")).startswith("Summit") and ctx.get("sponsor") == "USSR":
        return defender, sponsor
    return sponsor, defender


def _event_waits_for_done(engine: B.DeepEngine, next_event: Any, dtype: int) -> bool:
    """DeepStruggle sits inside an event's open-ended choice that struggler
    closed without logging a "done": the next struggler event is something
    else, and DONE is legal."""
    if not engine.mask()[B.CONFIRM_DONE_INDEX]:
        return False
    save = engine.save()
    if not save["ctx_stack"][save["ctx_stack_depth"]].get("resolving_card"):
        return False
    kind = _kind(next_event)
    if dtype == B.SELECT_CARD:
        return kind not in _CARD_KINDS and kind is not K.EVENT_CHOICE
    return kind not in _COUNTRY_KINDS and kind is not K.EVENT_CHOICE


def _play_mode_index(group: list[Any]) -> int | None:
    """The flat index for a struggler `PLAY_MODE` group, or None while the
    group is still incomplete."""
    first = group[0]
    if _kind(first) is K.OPS_TYPE:  # struggler skipped PLAY_MODE: Ops was the only mode
        return B.PLAY_MODE_OFFSET + _OPS_TYPE_RES[_payload(first)["type"]]
    if _kind(first) is not K.PLAY_MODE:
        raise Desync("play_mode_expected", _kind(first).value)
    mode = _payload(first)["mode"]
    if mode == "event":
        return B.PLAY_MODE_OFFSET + B.RES_EVENT
    if mode == "space_race":
        return B.PLAY_MODE_OFFSET + B.RES_SPACE
    if mode != "ops":
        raise Desync(f"play_mode_{mode}")
    if len(group) == 1:
        return None
    second = group[1]
    if _kind(second) is K.EVENT_OPS_ORDER:
        if _payload(second)["order"] == "event_first":
            return B.PLAY_MODE_OFFSET + B.RES_EVENT  # the Ops type is asked after the event
        if len(group) == 2:
            return None
        second = group[2]
    if _kind(second) is not K.OPS_TYPE:
        raise Desync("ops_type_expected", _kind(second).value)
    return B.PLAY_MODE_OFFSET + _OPS_TYPE_RES[_payload(second)["type"]]


def _step(engine: B.DeepEngine, index: int, what: str, event: Any) -> None:
    mask = engine.mask()
    if not mask[index]:
        raise Desync(f"illegal_{what}", f"{_kind(event).value} {_payload(event)} -> slot {index}")
    if not engine.step_flat(index):
        raise Desync(f"refused_{what}", f"{_kind(event).value} {_payload(event)}")


def _cleanup_or_fail(engine: B.DeepEngine, next_event: Any) -> None:
    save = engine.save()
    frame = save["ctx_stack"][save["ctx_stack_depth"]]
    if frame.get("pending_roll") == _TURN_CLEANUP:
        if not engine.roll(0):
            raise Desync("cleanup_refused")
        return
    raise Desync("unlogged_roll", f"ds pending_roll={frame.get('pending_roll')} next={_kind(next_event).value}")


#: `ts::RollType::TURN_CLEANUP` (DeepStruggle engine/include/ts/game_state.hpp),
#: pinned by the submodule commit.
_TURN_CLEANUP = 8
