"""struggler event-choice answers -> DeepStruggle slots (pure mapping)."""
from struggler.bots.deep import backend as B
from struggler.bots.deep.choices import choice_slots
from struggler.bots.deep.ids import IdMap
from struggler.engine import Engine

_RENAME = {"UK": "United Kingdom", "Dominican_Republic": "Dominican Rep"}
_COUNTRIES = [c for c in Engine.new_game(seed=1, include_ccw=False).board.countries]
IDS = IdMap.build([_RENAME.get(c, c.replace("_", " ")) for c in _COUNTRIES], _COUNTRIES)
NO_MASK = [0] * B.FLAT_ACTION_SIZE


def test_branches_follow_deepstruggles_card_handlers():
    assert choice_slots("Warsaw_Pact_Formed", "add", B.CHOOSE_BRANCH, NO_MASK, IDS) == [B.BRANCH_OFFSET + 1]
    assert choice_slots("Olympic_Games", "participate", B.CHOOSE_BRANCH, NO_MASK, IDS) == [B.BRANCH_OFFSET]
    assert choice_slots("Wargames", "end_game", B.CHOOSE_BRANCH, NO_MASK, IDS) == [B.BRANCH_OFFSET]
    assert choice_slots("Unknown_Card", "x", B.CHOOSE_BRANCH, NO_MASK, IDS) == []


def test_regions_defcon_values_and_declines():
    assert choice_slots("Chernobyl", "ASIA", B.CHOOSE_BRANCH, NO_MASK, IDS) == [B.REGION_OFFSET + 1]
    assert choice_slots("How_I_Learned_to_Stop_Worrying", "3", B.CHOOSE_BRANCH, NO_MASK, IDS) == \
        [B.DEFCON_VALUE_OFFSET + 2]
    assert choice_slots("Blockade", "refuse", B.SELECT_CARD, NO_MASK, IDS) == [B.CONFIRM_DONE_INDEX]


def test_countries_cards_and_op_modes():
    assert choice_slots("De_Stalinization_remove", "Finland", B.POINT_NODE, NO_MASK, IDS) == \
        [B.NODE_OFFSET + IDS.country_to_ds["Finland"]]
    assert choice_slots("Ask_Not_What_Your_Country_Can_Do_For_You", "Fidel", B.SELECT_CARD, NO_MASK, IDS) == \
        [B.CARD_OFFSET + IDS.card_to_ds["Fidel"] - 1]
    assert choice_slots("Junta", "realign", B.SELECT_OP_MODE, NO_MASK, IDS) == [B.OP_MODE_OFFSET + 2]


def test_a_yes_coup_answer_stands_for_every_target_offered():
    mask = list(NO_MASK)
    for c in ("Cuba", "Honduras"):
        mask[B.NODE_OFFSET + IDS.country_to_ds[c]] = 1
    assert sorted(choice_slots("Ortega_Elected_in_Nicaragua", "coup", B.POINT_NODE, mask, IDS)) == \
        sorted(B.NODE_OFFSET + IDS.country_to_ds[c] for c in ("Cuba", "Honduras"))
