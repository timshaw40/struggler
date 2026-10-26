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
