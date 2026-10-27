"""struggler's `EVENT_CHOICE` answers, as DeepStruggle flat slots.

struggler names an event's choice in words ("remove", "boycott", a country, a
card); DeepStruggle asks it as whatever decision fits (a branch slot, a
country node, a card, a region, a DEFCON value, "done"). `choice_slots` maps
one answer at the DeepStruggle decision currently open, or returns [] when the
pair is not mapped; the replay then gives up the round and the player falls
back, both counted.

Branch numbers are DeepStruggle's own, read from its card handlers
(`engine/src/card_dispatcher.cpp`, `events/*.cpp`) at the pinned commit.
"""
from __future__ import annotations

from typing import Sequence

from struggler.bots.deep import backend as B
from struggler.bots.deep.ids import DS_REGIONS, IdMap

#: Event name -> {struggler choice: DeepStruggle branch}.
BRANCHES = {
    "Warsaw_Pact_Formed": {"remove": 0, "add": 1},
    "Olympic_Games": {"participate": 0, "boycott": 1},
    "Wargames": {"end_game": 0, "decline": 1},
}

#: Answers that decline or finish: DeepStruggle's CONFIRM_DONE.
DECLINE = {"none", "refuse", "done", "stop", "skip", "decline"}

#: Answers that say "yes, act" where struggler asks the target next and
#: DeepStruggle asks the target directly (Ortega's free coup, Junta's).
AFFIRM_TARGET = {"coup", "realign", "and_adjacent"}
_OP_MODE = {"influence": 0, "coup": 1, "realign": 2, "realignment": 2}

#: `ts::CardLocation::PEEKED_TEMP`: a card someone is looking at (Our Man in Tehran).
PEEKED_TEMP = 9

GRAIN_SALES = "Grain_Sales_to_Soviets"
OUR_MAN = "Our_Man_In_Tehran"


class PeekMismatch(ValueError):
    """Our Man in Tehran: DeepStruggle is not peeking at enough cards to hold
    the ones struggler showed."""


def peeked(engine: B.DeepEngine) -> list[int]:
    """The DeepStruggle card ids at PEEKED_TEMP."""
    locs = engine.save()["card_locations"]
    return [c for c in range(1, len(locs)) if locs[c] == PEEKED_TEMP]


def peek(engine: B.DeepEngine, cards: Sequence[int]) -> None:
    """Make DeepStruggle's Our Man in Tehran peek hold `cards` (DeepStruggle
    ids): it drew its own five from its RNG, struggler showed the US others.
    Each missing card swaps locations with a peeked card that is not one of
    `cards`, so every count stays as it was."""
    save = engine.save()
    locs = save["card_locations"]
    spare = [c for c in range(1, len(locs)) if locs[c] == PEEKED_TEMP and c not in cards]
    changed = False
    for card in cards:
        if locs[card] == PEEKED_TEMP:
            continue
        if not spare:
            raise PeekMismatch(f"card {card} has no peeked slot to take")
        other = spare.pop()
        locs[other], locs[card] = locs[card], locs[other]
        changed = True
    if changed:
        engine.load_save(save)


def our_man_slots(engine: B.DeepEngine, choice: str, card: int, kept: Sequence[int]) -> list[int]:
    """struggler asks keep/remove card by card; DeepStruggle asks which peeked
    card to discard next, or "done". Removing `card` is its slot; keeping it
    is anything else that keeps it: "done", or discarding a card struggler has
    not shown yet (the ones already `kept` are settled)."""
    if choice == "remove":
        return [B.CARD_OFFSET + card - 1]
    if choice == "keep":
        mask = engine.mask()
        rest = [B.CARD_OFFSET + c - 1 for c in peeked(engine) if c != card and c not in kept]
        return [s for s in [B.CONFIRM_DONE_INDEX, *rest] if mask[s]]
    return []


def grain_sales_slots(choice: str, mask: Sequence[int]) -> list[int]:
    """Grain Sales: DeepStruggle asks take-and-how in one decision (any play
    mode for the drawn card) or "done" to hand it back; struggler asks
    take/return, then the play mode."""
    if choice == "return":
        return [B.CONFIRM_DONE_INDEX]
    if choice == "take":
        return [B.PLAY_MODE_OFFSET + r for r in range(5) if mask[B.PLAY_MODE_OFFSET + r]]
    return []


def choice_slots(event: str, choice: str, dtype: int, mask: Sequence[int], ids: IdMap) -> list[int]:
    """The flat slots `choice` stands for at the open DeepStruggle decision."""
    if dtype == B.CHOOSE_BRANCH:
        branch = BRANCHES.get(event, {}).get(choice)
        if branch is not None:
            return [B.BRANCH_OFFSET + branch]
        if event == "Chernobyl" and choice in DS_REGIONS:
            return [B.REGION_OFFSET + DS_REGIONS.index(choice)]
        if event == "How_I_Learned_to_Stop_Worrying" and choice.isdigit():
            return [B.DEFCON_VALUE_OFFSET + int(choice) - 1]
        return []
    if choice in DECLINE:
        return [B.CONFIRM_DONE_INDEX]
    if dtype == B.SELECT_OP_MODE and choice in _OP_MODE:
        return [B.OP_MODE_OFFSET + _OP_MODE[choice]]
    if dtype == B.POINT_NODE:
        if event == "South_African_Unrest" and choice == "south_africa_only":
            return [B.NODE_OFFSET + ids.country_to_ds["South_Africa"]]
        if event == "South_African_Unrest" and choice == "and_adjacent":
            south_africa = B.NODE_OFFSET + ids.country_to_ds["South_Africa"]
            return [i for i in range(B.NODE_OFFSET, B.BRANCH_OFFSET) if mask[i] and i != south_africa]
        if choice in ids.country_to_ds:
            return [B.NODE_OFFSET + ids.country_to_ds[choice]]
        if choice in AFFIRM_TARGET:  # any target DeepStruggle offers
            return [i for i in range(B.NODE_OFFSET, B.BRANCH_OFFSET) if mask[i]]
        return []
    if dtype == B.SELECT_CARD and choice in ids.card_to_ds:
        return [B.CARD_OFFSET + ids.card_to_ds[choice] - 1]
    # Chernobyl and How I Learned may surface as their own slot kinds outside CHOOSE_BRANCH.
    if event == "Chernobyl" and choice in DS_REGIONS:
        return [B.REGION_OFFSET + DS_REGIONS.index(choice)]
    if event == "How_I_Learned_to_Stop_Worrying" and choice.isdigit():
        return [B.DEFCON_VALUE_OFFSET + int(choice) - 1]
    return []
