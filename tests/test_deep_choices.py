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


def test_grain_sales_take_is_any_play_mode_and_return_is_done():
    from struggler.bots.deep.choices import grain_sales_slots

    mask = list(NO_MASK)
    for r in (B.RES_EVENT, B.RES_OPS_INFLUENCE):
        mask[B.PLAY_MODE_OFFSET + r] = 1
    assert grain_sales_slots("take", mask) == [B.PLAY_MODE_OFFSET + B.RES_EVENT, B.PLAY_MODE_OFFSET + B.RES_OPS_INFLUENCE]
    assert grain_sales_slots("return", mask) == [B.CONFIRM_DONE_INDEX]
    assert grain_sales_slots("burn", mask) == []


class _Peek:
    """Just enough of a DeepStruggle engine for Our Man in Tehran's peek."""

    def __init__(self, locations):
        self.locations = list(locations)

    def save(self):
        return {"card_locations": list(self.locations)}

    def load_save(self, save):
        self.locations = list(save["card_locations"])

    def mask(self):
        m = list(NO_MASK)
        m[B.CONFIRM_DONE_INDEX] = 1
        for c, loc in enumerate(self.locations):
            if loc == 9:
                m[B.CARD_OFFSET + c - 1] = 1
        return m


def test_our_man_peek_swaps_struggler_cards_in_and_keeps_counts():
    from collections import Counter

    from struggler.bots.deep.choices import PEEKED_TEMP, PeekMismatch, our_man_slots, peek, peeked

    locs = [0] * 111
    for c in (10, 11, 12):
        locs[c] = PEEKED_TEMP          # DeepStruggle's own draw
    locs[20], locs[21] = 1, 4          # struggler's cards: in the deck, in the USSR hand
    engine = _Peek(locs)
    before = Counter(engine.locations)
    peek(engine, [20, 11, 21])
    assert {20, 11, 21} <= set(peeked(engine)) and len(peeked(engine)) == 3
    assert Counter(engine.locations) == before
    # Keeping 21 (with 20 already kept): "done", or discarding the card not yet shown.
    assert our_man_slots(engine, "keep", 21, kept=[20]) == [B.CONFIRM_DONE_INDEX, B.CARD_OFFSET + 11 - 1]
    assert our_man_slots(engine, "remove", 21, kept=[20]) == [B.CARD_OFFSET + 21 - 1]
    try:
        peek(engine, [20, 11, 21, 30])
    except PeekMismatch:
        pass
    else:
        raise AssertionError("four cards cannot fit a three-card peek")


def test_box_six_held_card_discard_is_a_card_slot_or_done():
    from struggler.bots.deep.replay import held_card_slot

    assert held_card_slot("none", IDS) == B.CONFIRM_DONE_INDEX
    assert held_card_slot("Fidel", IDS) == B.CARD_OFFSET + IDS.card_to_ds["Fidel"] - 1
