"""Engine: Space Race track mechanics, including the box 6/8 perks (6.4.3-6.4.4)."""

from struggler.engine import Action, DecisionKind, Engine, Side
from struggler.engine.rules import RULES

from conftest import bare_engine as _bare
from conftest import headline_setup as _headline_setup


def test_advance_space_race_box_awards_first_then_second_vp():
    engine = Engine(seed=1)
    engine.advance_space_race_box(Side.US)
    assert engine.space_race["US"] == 1
    assert engine.vp == 2  # box 1: 2 VP to the first side to reach it

    engine.advance_space_race_box(Side.USSR)
    assert engine.space_race["USSR"] == 1
    assert engine.vp == 1  # box 1: 1 VP to the second side (net US +2 -1)


def _advance_to(engine: Engine, side: Side, box: int) -> None:
    while engine.space_race[side.value] < box:
        engine.advance_space_race_box(side)


def test_reaching_box_8_grants_extra_action_round_cancelled_when_opponent_catches_up():
    engine = Engine.new_game(seed=1, events=False)
    base = engine._total_action_rounds()
    # Turn 1: 6 rounds per side; the box-8 holder plays an absolute 8
    # (6.4.4), i.e. +2 over the base alternation.
    box8_extras = max(0, RULES["space_race_box8_rounds"] - base // 2)

    _advance_to(engine, Side.US, 8)
    assert engine.game_effects["space_race_extra_round_holder"] == "US"
    assert engine._total_action_rounds() == base + box8_extras
    assert engine._side_for_play_index(base) is Side.US  # the extra rounds are the US's

    # 6.4.4: the ability is cancelled outright once the USSR also reaches box 8,
    # not transferred to the USSR.
    _advance_to(engine, Side.USSR, 8)
    assert "space_race_extra_round_holder" not in engine.game_effects
    assert engine._total_action_rounds() == base


def test_reaching_box_6_offers_held_card_discard_at_end_of_turn():
    engine = Engine.new_game(seed=1, events=False)
    _advance_to(engine, Side.USSR, 6)
    assert engine.game_effects["space_race_discard_holder"] == "USSR"

    held = engine.hands["USSR"][0]
    engine._end_of_turn()

    d = engine.pending_decision
    assert d.kind is DecisionKind.HELD_CARD_DISCARD and d.actor is Side.USSR
    choices = {a.payload["card"] for a in d.options}
    assert held in choices and "none" in choices

    engine.step(next(a for a in d.options if a.payload["card"] == held))
    assert held not in engine.hands["USSR"]
    assert held in engine.discard_pile
    # The turn boundary resumed once the discard was resolved.
    assert engine.turn == 2


def test_held_card_discard_can_be_declined():
    engine = Engine.new_game(seed=1, events=False)
    _advance_to(engine, Side.USSR, 6)
    hand_before = list(engine.hands["USSR"])
    engine._end_of_turn()

    d = engine.pending_decision
    engine.step(next(a for a in d.options if a.payload["card"] == "none"))
    assert all(cid in engine.hands["USSR"] for cid in hand_before)  # nothing discarded
    assert engine.turn == 2


def test_held_card_discard_not_offered_without_the_ability_or_an_empty_hand():
    engine = Engine.new_game(seed=1, events=False)
    engine._end_of_turn()
    assert engine.pending_decision is None or engine.pending_decision.kind != (
        DecisionKind.HELD_CARD_DISCARD
    )
    assert engine.turn == 2


def test_reaching_box_2_grants_second_attempt_cancelled_when_opponent_catches_up():
    engine = Engine.new_game(seed=1, events=False)

    _advance_to(engine, Side.US, 2)
    assert engine.game_effects["space_race_double_attempt_holder"] == "US"
    assert engine._space_attempts_allowed(Side.US) == 2
    assert engine._space_attempts_allowed(Side.USSR) == 1

    # 6.4.4: the ability is cancelled outright once the USSR also reaches
    # box 2, not transferred to the USSR -- so neither side gets a second
    # attempt from that point on, even mid-turn, right after the USSR's own
    # roll takes it to box 2.
    _advance_to(engine, Side.USSR, 2)
    assert "space_race_double_attempt_holder" not in engine.game_effects
    assert engine._space_attempts_allowed(Side.US) == 1
    assert engine._space_attempts_allowed(Side.USSR) == 1


def test_reaching_box_4_flips_headline_pick_order_and_reveals_opponent_pick():
    # Box 4's sole holder (USSR) picks its Headline second, after seeing the
    # US's already-committed pick -- the default USSR-first order is reversed.
    engine = _bare(seed=1)
    engine.game_effects["space_race_headline_reveal_holder"] = "USSR"
    _headline_setup(engine, "Fidel", "Duck_and_Cover")

    d = engine.pending_decision
    assert d.kind is DecisionKind.HEADLINE_PLAY and d.actor is Side.US
    assert "opponent_headline" not in d.context

    engine.step(Action(DecisionKind.HEADLINE_PLAY, {"card": "Duck_and_Cover"}))

    d = engine.pending_decision
    assert d.kind is DecisionKind.HEADLINE_PLAY and d.actor is Side.USSR
    assert d.context["opponent_headline"] == "Duck_and_Cover"


def test_reaching_box_4_is_cancelled_when_opponent_catches_up():
    engine = Engine.new_game(seed=1, events=False)
    _advance_to(engine, Side.US, 4)
    assert engine.game_effects["space_race_headline_reveal_holder"] == "US"

    # 6.4.4: cancelled outright, not transferred, the instant the USSR also
    # reaches box 4 -- the normal USSR-first pick order returns.
    _advance_to(engine, Side.USSR, 4)
    assert "space_race_headline_reveal_holder" not in engine.game_effects
    assert engine._headline_pick_order() == (Side.USSR, Side.US)


def test_space_race_ability_state_round_trips_through_serialization():
    # US alone reaches box 8 (passing through box 6 too, so it holds both
    # abilities) -- USSR stays behind, so neither is cancelled by a catch-up.
    engine = Engine.new_game(seed=1, events=False)
    _advance_to(engine, Side.US, 8)
    data = engine.serialize()
    restored = Engine.deserialize(data)
    assert restored.serialize() == data
    assert restored.game_effects["space_race_extra_round_holder"] == "US"
    assert restored.game_effects["space_race_discard_holder"] == "US"


def test_box8_grants_eight_absolute_rounds_even_in_early_war():
    # 6.4.4: "Upon reaching space 8 (Space Station), the player may play
    # eight (8) Action Rounds per turn" -- an absolute 8, not base + 1. The
    # engine granted +1, which gave the holder 7 rounds in turns 1-3.
    engine = Engine.new_game(seed=1, events=False)
    assert engine.turn == 1  # 6 action rounds per side here
    _advance_to(engine, Side.USSR, 8)
    total = engine._total_action_rounds()
    assert total == 2 * 6 + 2  # USSR: 6 + 2 = 8; US: 6
    sides = [engine._side_for_play_index(i) for i in range(total)]
    assert sides.count(Side.USSR) == 8
    assert sides.count(Side.US) == 6


# -- what may NOT be sent to the Space Race ---------------------------------


def test_space_race_never_offers_the_china_card():
    """Both printed faces of the China Card forbid it, and the bot could not
    price what spacing it costs: the +1 end-game VP for holding it and its 4
    Ops are invisible to `board_value`, so a lucky rollout sample won.

    Observed live: USSR spaced the China Card on turn 1, round 5, DEFCON 4, at
    a decision where the greedy heuristic scored Ops 9.00 to Space Race 2.50.
    """
    engine = Engine.new_game(seed=1)
    engine.events_enabled = True
    china = engine.cards[RULES["china_card_id"]]
    assert not engine._can_space_race(Side.USSR, china)
    assert not engine._can_space_race(Side.US, china)
    assert "space_race" not in engine._play_modes(Side.USSR, RULES["china_card_id"])


def test_space_race_never_offers_un_intervention():
    """Its own text: "may not be discarded for the Space Race."."""
    engine = Engine.new_game(seed=1)
    engine.events_enabled = True
    un = engine.cards[RULES["un_intervention_id"]]
    assert not engine._can_space_race(Side.US, un)
    assert not engine._can_space_race(Side.USSR, un)


def test_space_race_never_offers_a_scoring_card():
    """Spacing a scoring card would dodge scoring a region you control: there
    is no Ops value to give up and no event to avoid."""
    engine = Engine.new_game(seed=1)
    engine.events_enabled = True
    for cid, card in engine.cards.items():
        if card.scoring:
            assert not engine._can_space_race(Side.US, card), cid
            assert not engine._can_space_race(Side.USSR, card), cid
            assert "space_race" not in engine._play_modes(Side.US, cid), cid


def test_space_race_excludes_your_own_events_but_not_your_opponents():
    """You Space Race the opponent's events you must play anyway, not your own.

    The restriction is per side and asymmetric: the same card is spaceable by
    one seat and not the other. Neutral cards have no side's event to protect,
    so they stay spaceable by both.
    """
    engine = Engine.new_game(seed=1)
    engine.events_enabled = True
    cards = engine.cards

    for cid, card in cards.items():
        if card.scoring or card.side.value == "NEUTRAL":
            continue
        owner = Side(card.side.value)
        if engine._effective_ops(owner, card) < RULES["space_race_boxes"]["1"]["ops"]:
            continue  # fails the pre-existing Ops threshold, not this rule
        assert not engine._can_space_race(owner, card), f"{cid} is its owner's own event"
        assert engine._can_space_race(owner.opponent, card), f"{cid} is spaceable by its opponent"

    for cid, card in cards.items():
        if card.side.value != "NEUTRAL" or card.scoring:
            continue
        if cid in (RULES["china_card_id"], RULES["un_intervention_id"]):
            continue
        if engine._effective_ops(Side.US, card) < RULES["space_race_boxes"]["1"]["ops"]:
            continue
        assert engine._can_space_race(Side.US, card), cid
        assert engine._can_space_race(Side.USSR, card), cid


def test_space_race_exclusion_is_gated_on_events_being_on():
    """With events off nothing can fire, so there is no "own event" to protect
    and the exclusion must not silently narrow the option set."""
    engine = Engine.new_game(seed=1, events=False)
    assert not engine.events_enabled
    own = [c for c in engine.cards.values()
           if c.side.value == "USSR" and not c.scoring
           and engine._effective_ops(Side.USSR, c) >= RULES["space_race_boxes"]["1"]["ops"]]
    assert own, "no USSR card clears the Ops threshold to test with"
    for card in own:
        assert engine._can_space_race(Side.USSR, card), card.id
