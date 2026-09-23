"""Tests for GreedyPlayer: the board evaluator, DEFCON-safety heuristic,
fallback behavior, and a win-rate sanity check against RandomPlayer."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from struggler.bots.greedy import (
    _US_SPACE_RACE,
    _USSR_SPACE_RACE,
    GreedyPlayer,
    GreedyWeights,
    TurnPlan,
    _defcon_suicide_risk,
    plan_turn,
    _in_bonus_region,
    _is_reshuffle_turn,
    _realignment_severs_access,
    _score_coup_target,
    _score_ops_type,
    _strand_disposal_bonus,
    _strand_penalty,
    _unavoidable_strands,
    board_value,
    milops_gain,
    scoreboard_value,
)
from struggler.bots.naive import FirstLegalPlayer, RandomPlayer
from struggler.engine import Action, Decision, DecisionKind, Engine, Region, Side
from struggler.engine.board import Board
from struggler.engine.cards import action_rounds
from struggler.runner import play_game


def test_board_value_zero_with_no_influence_anywhere():
    board = Board()
    assert board_value(GreedyWeights(), board, Side.US) == 0.0


def test_board_value_rewards_controlling_a_battleground_over_a_non_battleground():
    weights = GreedyWeights()
    battleground = next(cid for cid, info in Board().countries.items() if info.battleground)
    non_battleground = next(cid for cid, info in Board().countries.items() if not info.battleground)

    bg_board = Board()
    bg_board.influence[battleground]["US"] = bg_board.countries[battleground].stability
    bg_value = board_value(weights, bg_board, Side.US)

    plain_board = Board()
    plain_board.influence[non_battleground]["US"] = plain_board.countries[non_battleground].stability
    plain_value = board_value(weights, plain_board, Side.US)

    assert bg_value > plain_value > 0.0


def test_greedy_avoids_coup_as_an_ops_type_at_defcon_2():
    """DEFCON 2 -> 1 loses the game for whoever caused the drop (mandate:
    priority #1: never die to DEFCON). Even
    with a juicy Coup target on offer, GreedyPlayer must pick something
    else at the OPS_TYPE decision."""
    engine = Engine(seed=1)
    # Mexico: a Battleground with no DEFCON region-lock, so a Coup here risks DEFCON 1
    # even at DEFCON 2 (unlike Europe/Asia/Middle East, which lock out first).
    engine.board.influence["Mexico"]["USSR"] = 3
    engine._change_defcon(-3, caused_by=Side.US)  # 5 -> 2
    assert engine.defcon == 2

    engine._push_ops_type(Side.US, ops=3)
    observation = engine.observe(Side.US)
    assert observation.pending_decision.kind is DecisionKind.OPS_TYPE
    offered = {a.payload["type"] for a in observation.pending_decision.options}
    assert "coup" in offered  # the engine itself doesn't forbid the suicidal option

    action = GreedyPlayer().choose_action(observation, [])
    assert action.payload["type"] != "coup"


def test_greedy_scores_southeast_asia_scoring():
    from struggler.bots.greedy import _scoring_card_favorability

    board = Board()
    board.influence["Thailand"] = {"US": board.countries["Thailand"].stability, "USSR": 0}
    board.influence["Vietnam"] = {"US": 0, "USSR": board.countries["Vietnam"].stability}

    us = _scoring_card_favorability(board, Side.US, "Southeast_Asia_Scoring")
    ussr = _scoring_card_favorability(board, Side.USSR, "Southeast_Asia_Scoring")
    assert us > 0 and ussr < 0 and us == -ussr  # Thailand 2 - Vietnam 1 = +1 net US


def test_greedy_avoids_playing_an_opponent_event_for_ops():
    # Warsaw Pact Formed (USSR, 3 Ops) vs Duck and Cover (US, 3 Ops): equal
    # Ops, but the USSR card would fire its Event for the opponent on an Ops
    # play, so Greedy picks the US card.
    engine = Engine(seed=1)
    engine.hands["US"] = ["Warsaw_Pact_Formed", "Duck_and_Cover"]
    engine._push_action_round_play(Side.US)
    action = GreedyPlayer().choose_action(engine.observe(Side.US), [])
    assert action.payload["card"] == "Duck_and_Cover"


def test_greedy_avoids_coup_under_cuban_missile_crisis():
    """Cuban Missile Crisis: any Coup by the flagged side this turn loses the
    game outright, so Greedy must refuse "coup" at OPS_TYPE even with a good
    target on offer (its DEFCON guard alone does not know about CMC)."""
    engine = Engine(seed=1)
    engine.board.influence["Mexico"]["USSR"] = 3  # a juicy, DEFCON-safe target
    engine.turn_effects["cuban_missile_crisis"] = "US"

    engine._push_ops_type(Side.US, ops=3)
    observation = engine.observe(Side.US)
    assert observation.pending_decision.kind is DecisionKind.OPS_TYPE
    assert "coup" in {a.payload["type"] for a in observation.pending_decision.options}

    action = GreedyPlayer().choose_action(observation, [])
    assert action.payload["type"] != "coup"


def test_greedy_falls_back_to_first_option_for_unmapped_decision_kinds():
    engine = Engine.new_game(seed=1)
    observation = engine.observe(engine.pending_decision.actor)
    fallback_decision = Decision(
        id=999,
        actor=observation.side,
        kind=DecisionKind.EVENT_CHOICE,
        options=(
            Action(DecisionKind.EVENT_CHOICE, {"choice": "a"}),
            Action(DecisionKind.EVENT_CHOICE, {"choice": "b"}),
        ),
    )
    observation = dataclasses.replace(observation, pending_decision=fallback_decision)

    action = GreedyPlayer().choose_action(observation, [])

    assert action == fallback_decision.options[0]


def test_greedy_aldrich_ames_remix_discards_the_opponents_highest_ops_card():
    """Unlike the generic EVENT_CHOICE fallback above, Aldrich Ames Remix has
    its own heuristic: force the discard of the US hand's highest-Ops card,
    rather than blindly taking whichever option came first."""
    engine = Engine.new_game(seed=1)
    engine.hands["US"] = ["Nasser", "Fidel", "Duck_and_Cover"]  # Ops 1, 2, 3
    engine._fire_event(Side.USSR, "Aldrich_Ames_Remix")
    observation = engine.observe(Side.USSR)
    assert observation.pending_decision.kind is DecisionKind.EVENT_CHOICE

    action = GreedyPlayer().choose_action(observation, [])

    assert action.payload["choice"] == "Duck_and_Cover"


def test_ussr_standard_opening_is_four_poland_one_eg_one_yugo():
    """Twilight Strategy standard USSR setup: 4 E.Ger, 4 Poland, 1 Yugoslavia."""
    engine = Engine.new_game(seed=1)
    greedy = GreedyPlayer()
    placed: list[str] = []
    while (
        engine.pending_decision
        and engine.pending_decision.actor is Side.USSR
        and engine.pending_decision.context.get("setup")
    ):
        action = greedy.choose_action(engine.observe(Side.USSR), [])
        placed.append(action.payload["country"])
        engine.step(action)
    assert placed.count("Poland") == 4
    assert placed.count("East_Germany") == 1
    assert placed.count("Yugoslavia") == 1
    assert "Austria" not in placed


def test_us_standard_opening_is_four_wg_three_italy():
    engine = Engine.new_game(seed=1)
    greedy = GreedyPlayer()
    while (
        engine.pending_decision
        and engine.pending_decision.actor is Side.USSR
        and engine.pending_decision.context.get("setup")
    ):
        engine.step(greedy.choose_action(engine.observe(Side.USSR), []))
    placed: list[str] = []
    while (
        engine.pending_decision
        and engine.pending_decision.actor is Side.US
        and engine.pending_decision.context.get("setup")
    ):
        action = greedy.choose_action(engine.observe(Side.US), [])
        placed.append(action.payload["country"])
        engine.step(action)
    assert placed.count("West_Germany") == 4
    assert placed.count("Italy") == 3


def test_ussr_does_not_headline_a_us_event():
    engine = Engine.new_game(seed=1)
    observation = engine.observe(engine.pending_decision.actor)
    decision = Decision(
        id=999,
        actor=Side.USSR,
        kind=DecisionKind.HEADLINE_PLAY,
        options=(
            Action(DecisionKind.HEADLINE_PLAY, {"card": "CIA_Created"}),
            Action(DecisionKind.HEADLINE_PLAY, {"card": "Socialist_Governments"}),
        ),
    )
    observation = dataclasses.replace(
        observation, pending_decision=decision, side=Side.USSR,
    )
    action = GreedyPlayer().choose_action(observation, [])
    assert action.payload["card"] == "Socialist_Governments"


def test_ussr_t1_headlines_red_scare_over_a_filler_ussr_event():
    engine = Engine.new_game(seed=1)
    observation = engine.observe(engine.pending_decision.actor)
    observation = dataclasses.replace(observation, turn=1, side=Side.USSR)
    decision = Decision(
        id=998,
        actor=Side.USSR,
        kind=DecisionKind.HEADLINE_PLAY,
        options=(
            Action(DecisionKind.HEADLINE_PLAY, {"card": "Romanian_Abdication"}),
            Action(DecisionKind.HEADLINE_PLAY, {"card": "Red_Scare_Purge"}),
        ),
    )
    observation = dataclasses.replace(observation, pending_decision=decision)
    action = GreedyPlayer().choose_action(observation, [])
    assert action.payload["card"] == "Red_Scare_Purge"


def test_ussr_t1_prefers_couping_iran_over_italy():
    engine = Engine.new_game(seed=1)
    observation = engine.observe(engine.pending_decision.actor)
    observation = dataclasses.replace(
        observation, turn=1, defcon=5, side=Side.USSR,
    )
    decision = Decision(
        id=997,
        actor=Side.USSR,
        kind=DecisionKind.COUP_TARGET,
        options=(
            Action(DecisionKind.COUP_TARGET, {"country": "Italy"}),
            Action(DecisionKind.COUP_TARGET, {"country": "Iran"}),
        ),
        context={"ops": 4, "bonus": None},
    )
    observation = dataclasses.replace(observation, pending_decision=decision)
    action = GreedyPlayer().choose_action(observation, [])
    assert action.payload["country"] == "Iran"


def test_greedy_option_scores_align_with_the_legal_options():
    engine = Engine.new_game(seed=1)
    obs = engine.observe(engine.pending_decision.actor)
    scores = GreedyPlayer().option_scores(obs)
    assert len(scores) == len(obs.pending_decision.options)
    assert all(isinstance(s, float) for s in scores)


def test_in_bonus_region_reads_the_aggregated_bonus_list():
    """7.4 aggregates region bonuses, so the engine hands the decision
    context a *list* of live bonuses (China Card's Asia plus Vietnam Revolts'
    SE Asia both apply to a SE Asia target). A bare region name still has to
    work: the China Card alone and callers written against the old shape pass
    a single string."""
    board = Board()
    thailand = board.countries["Thailand"]  # Asia and SE Asia
    india = board.countries["India"]  # Asia, not SE Asia

    assert _in_bonus_region(thailand, "se_asia")
    assert _in_bonus_region(thailand, ["se_asia"])
    assert _in_bonus_region(thailand, ["asia", "se_asia"])
    assert _in_bonus_region(india, ["asia"])
    assert not _in_bonus_region(india, ["se_asia"])
    assert not _in_bonus_region(india, [])
    assert not _in_bonus_region(india, None)


def test_coup_scoring_aggregates_every_live_region_bonus():
    """The coup scorer adds +1 Op per satisfied bonus, so the same target is
    worth strictly more as more live bonuses cover it: none, then SE Asia
    alone (Vietnam Revolts), then Asia and SE Asia together (China Card plus
    Vietnam Revolts, 7.4's worked example)."""
    weights = GreedyWeights()
    action = Action(DecisionKind.COUP_TARGET, {"country": "Laos_Cambodia"})

    def score(bonus):
        board = Board()
        board.influence["Laos_Cambodia"]["US"] = 3
        observation = SimpleNamespace(
            side=Side.USSR,
            turn=5,
            defcon=5,
            turn_effects={},
            pending_decision=SimpleNamespace(context={"ops": 1, "bonus": bonus}),
        )
        return _score_coup_target(weights, board, observation, action)

    assert score([]) < score(["se_asia"]) < score(["asia", "se_asia"])


# -- turn 1, from Twilight Strategy's "General Strategy: Turn 1" -------------
#
# The article is prose; these pin the prose as behaviour. Each test names the
# sentence it encodes, so a future change that breaks the plan has to argue with
# the article rather than with a number.


def _t1_obs(engine, side, kind, options, context=None, **over):
    """A turn-1 observation with a hand-built decision."""
    obs = engine.observe(side)
    decision = Decision(
        id=900, actor=side, kind=kind,
        options=tuple(Action(kind, p) for p in options),
        context=context or {},
    )
    obs = dataclasses.replace(obs, pending_decision=decision, turn=1, side=side, **over)
    return obs


def test_ussr_t1_places_into_the_middle_east_before_europe():
    """"modern Twilight Struggle thinking is that access to Pakistan and India
    is simply too important" — Iran and the Middle East ahead of the European
    grab."""
    engine = Engine.new_game(seed=1)
    obs = _t1_obs(engine, Side.USSR, DecisionKind.PLACE_INFLUENCE,
                  [{"country": "Greece"}, {"country": "Iran"}])
    action = GreedyPlayer().choose_action(obs, [])
    assert action.payload["country"] == "Iran"


def test_ussr_t1_prefers_jordan_when_israel_is_us_held():
    """"if the US is still in Israel, taking Jordan and/or Lebanon puts some
    real pressure on the US position\""""
    engine = Engine.new_game(seed=1)
    board = engine.board
    board.influence["Israel"]["US"] = 1     # the article's premise
    obs = _t1_obs(engine, Side.USSR, DecisionKind.PLACE_INFLUENCE,
                  [{"country": "Egypt"}, {"country": "Jordan"}])
    action = GreedyPlayer().choose_action(obs, [])
    assert action.payload["country"] == "Jordan"


def test_us_t1_protects_israel_before_chasing_thailand():
    """"Protect Israel via Lebanon and/or Jordan" is first in the article's
    list; "Gun for Thailand via Malaysia" is third."""
    engine = Engine.new_game(seed=1)
    obs = _t1_obs(engine, Side.US, DecisionKind.PLACE_INFLUENCE,
                  [{"country": "Malaysia"}, {"country": "Lebanon"}])
    action = GreedyPlayer().choose_action(obs, [])
    assert action.payload["country"] == "Lebanon"


def test_us_t1_works_through_egypt_into_libya():
    """"Make your way through Egypt into Libya before Nasser wipes you out.\""""
    engine = Engine.new_game(seed=1)
    obs = _t1_obs(engine, Side.US, DecisionKind.PLACE_INFLUENCE,
                  [{"country": "Greece"}, {"country": "Egypt"}])
    action = GreedyPlayer().choose_action(obs, [])
    assert action.payload["country"] == "Egypt"


def test_us_t1_does_not_retaliate_into_iran():
    """"if the USSR opening coup of Iran is too good, then I wouldn't bother
    dropping DEFCON to 3 by couping Iran back\""""
    engine = Engine.new_game(seed=1)
    board = engine.board
    board.influence["Iran"]["USSR"] = 3     # the USSR took it
    board.influence["Iran"]["US"] = 0
    obs = _t1_obs(engine, Side.US, DecisionKind.COUP_TARGET,
                  [{"country": "Iran"}, {"country": "Syria"}],
                  context={"ops": 3}, defcon=5)
    action = GreedyPlayer().choose_action(obs, [])
    assert action.payload["country"] != "Iran", "the article rejects this coup"


def test_the_t1_plan_does_not_leak_into_later_turns():
    """The whole plan is turn 1 only: turn 2 gets the ordinary heuristic."""
    engine = Engine.new_game(seed=1)
    obs = _t1_obs(engine, Side.US, DecisionKind.PLACE_INFLUENCE,
                  [{"country": "Malaysia"}, {"country": "Lebanon"}])
    obs = dataclasses.replace(obs, turn=2)
    scores = GreedyPlayer().option_scores(obs)
    # Neither country carries the plan's ordering once the turn has passed.
    assert abs(scores[0] - scores[1]) < 12.0


# -- DEFCON safety for events, from Twilight Strategy's "General Strategy: ----
# DEFCON" --------------------------------------------------------------------
#
# "you lose the game if DEFCON drops to 1 on your turn. It doesn't matter who
# 'caused' it: if it happened on your watch, you're responsible for humanity's
# destruction." The engine implements that (8.1.3: the *phasing* player loses),
# so the bot must not resolve an event that can put DEFCON at 1 while phasing.


def _clean_obs(phasing, defcon, influence=None):
    """An observation on an empty board, so a predicate has one thing to find."""
    engine = Engine.new_game(seed=1)
    inf = {c: {"US": 0, "USSR": 0} for c in engine.board.influence}
    for (owner, country, n) in (influence or []):
        inf[country][owner.value] = n
    return dataclasses.replace(engine.observe(phasing), defcon=defcon, influence=inf)


def test_unconditional_defcon_degraders_are_lethal_only_at_defcon_2():
    for cid in ("Duck_and_Cover", "We_Will_Bury_You", "Soviets_Shoot_Down_KAL_007"):
        at2 = _clean_obs(Side.US, 2)
        assert _defcon_suicide_risk(at2, Side.US, cid, "event"), cid
        # "Cards that unconditionally degrade DEFCON ... You can never trigger
        # these events on your turn when DEFCON is at 2" — at 3 the drop lands
        # on 2, which is bad play rather than a loss.
        at3 = _clean_obs(Side.US, 3)
        assert not _defcon_suicide_risk(at3, Side.US, cid, "event"), cid


def test_an_opponent_suicide_card_is_lethal_on_an_ops_play_too():
    """The engine fires the opponent's event on an Ops play, and never offers
    an opponent card as a voluntary "event" (`_play_modes`), so Ops is the only
    door through which a bot can trigger one."""
    # The USSR holding a US DEFCON degrader: its text resolves whichever way
    # the card is committed.
    assert _defcon_suicide_risk(_clean_obs(Side.USSR, 2), Side.USSR, "Duck_and_Cover", "ops")
    # The US holding its own copy: an Ops play resolves nothing.
    assert not _defcon_suicide_risk(_clean_obs(Side.US, 2), Side.US, "Duck_and_Cover", "ops")
    # Same asymmetry for the "hands the opponent Ops" category.
    assert _defcon_suicide_risk(
        _clean_obs(Side.USSR, 2, [(Side.USSR, "Angola", 3)]), Side.USSR, "CIA_Created", "ops")
    assert not _defcon_suicide_risk(
        _clean_obs(Side.US, 2, [(Side.US, "Angola", 3)]), Side.US, "CIA_Created", "ops")


def test_the_space_race_and_un_intervention_never_resolve_the_text():
    obs = _clean_obs(Side.USSR, 2)
    for mode in ("space_race", "un_intervention"):
        assert not _defcon_suicide_risk(obs, Side.USSR, "Duck_and_Cover", mode), mode
    # And a mode the bot does not know at all is not lethal either.
    assert not _defcon_suicide_risk(obs, Side.USSR, "Duck_and_Cover", "not_a_mode")


def test_the_bot_does_not_play_an_opponent_suicide_card_for_ops():
    """Regression for the measured loss path: 30 of 33 DEFCON-1 losses in 40
    self-played games resolved through an opponent card played for Ops."""
    engine = Engine.new_game(seed=1)
    obs = engine.observe(Side.USSR)
    decision = Decision(
        id=903, actor=Side.USSR, kind=DecisionKind.PLAY_MODE,
        options=(
            Action(DecisionKind.PLAY_MODE, {"mode": "ops"}),
            Action(DecisionKind.PLAY_MODE, {"mode": "space_race"}),
        ),
        context={"card": "Duck_and_Cover"},
    )
    player = GreedyPlayer()
    at2 = dataclasses.replace(obs, pending_decision=decision, defcon=2)
    assert player.choose_action(at2, []).payload["mode"] == "space_race"
    assert player.option_scores(at2)[0] <= -player.weights.defcon_suicide_penalty
    # At DEFCON 3 the drop only reaches 2, so the Ops play is live again.
    at3 = dataclasses.replace(obs, pending_decision=decision, defcon=3)
    assert player.option_scores(at3)[0] > -player.weights.defcon_suicide_penalty


def test_the_bot_never_sets_defcon_to_one():
    """How I Learned to Stop Worrying offers DEFCON levels as its options, and
    the first one is an immediate loss for the side choosing it."""
    engine = Engine.new_game(seed=1)
    obs = engine.observe(Side.US)
    decision = Decision(
        id=904, actor=Side.US, kind=DecisionKind.EVENT_CHOICE,
        options=tuple(
            Action(DecisionKind.EVENT_CHOICE, {"choice": c}) for c in ("1", "2", "3", "4", "5")
        ),
        context={"event": "How_I_Learned_to_Stop_Worrying", "choose_side": "US"},
    )
    player = GreedyPlayer()
    at2 = dataclasses.replace(obs, pending_decision=decision, defcon=2)
    assert player.choose_action(at2, []).payload["choice"] == "5"
    assert player.option_scores(at2)[0] <= -player.weights.defcon_self_kill_penalty


def test_opponent_ops_cards_need_a_coupeable_battleground():
    """"you can never play your opponent's events from this list on your turn
    when DEFCON is 2 and your opponent can drop DEFCON by couping a battleground
    of yours (keeping in mind DEFCON restrictions)\""""
    # Lone Gunman gives the USSR 1 Op, so it threatens a US phasing player.
    assert _defcon_suicide_risk(
        _clean_obs(Side.US, 2, [(Side.US, "Mexico", 3)]), Side.US, "Lone_Gunman", "event")
    assert _defcon_suicide_risk(
        _clean_obs(Side.US, 2, [(Side.US, "South_Africa", 1)]), Side.US, "Lone_Gunman", "event")
    # Nothing to coup: the article's "only possible under Containment / not
    # much of a problem if you have no influence in a Mid War battleground".
    assert not _defcon_suicide_risk(
        _clean_obs(Side.US, 2), Side.US, "Lone_Gunman", "event")
    # Third World only: a European or Asian battleground is not coupeable at
    # DEFCON 2 ("keeping in mind DEFCON restrictions").
    assert not _defcon_suicide_risk(
        _clean_obs(Side.US, 2, [(Side.US, "France", 3)]), Side.US, "Lone_Gunman", "event")
    assert not _defcon_suicide_risk(
        _clean_obs(Side.US, 2, [(Side.US, "North_Korea", 3)]), Side.US, "Lone_Gunman", "event")


def test_the_ops_go_to_the_opponent_not_the_phasing_side():
    """CIA Created gives the *US* the Ops, so it only threatens a USSR turn,
    and what the US can coup is USSR influence."""
    assert _defcon_suicide_risk(
        _clean_obs(Side.USSR, 2, [(Side.USSR, "Angola", 3)]), Side.USSR, "CIA_Created", "event")
    assert not _defcon_suicide_risk(
        _clean_obs(Side.USSR, 2, [(Side.USSR, "East_Germany", 3)]),
        Side.USSR, "CIA_Created", "event")
    # And the US holding influence is irrelevant to a USSR turn.
    assert not _defcon_suicide_risk(
        _clean_obs(Side.USSR, 2, [(Side.US, "Mexico", 3)]), Side.USSR, "CIA_Created", "event")


def test_neutral_events_are_not_special_cased():
    """"you would have to be daft to play either of these for the event at
    DEFCON 2. Simply play them for Operations" — which the ordinary
    event_mode_penalty already prefers, so no special rule is needed."""
    assert not _defcon_suicide_risk(_clean_obs(Side.US, 2), Side.US, "Olympic_Games", "event")
    assert not _defcon_suicide_risk(_clean_obs(Side.US, 2), Side.US, "Summit", "event")


def test_the_bot_refuses_a_suicide_headline_but_takes_it_at_defcon_3():
    engine = Engine.new_game(seed=1)
    obs = engine.observe(Side.US)
    # The alternative is an *unpriced* card on purpose: a card whose event the
    # playbook calls worth firing now outranks Duck and Cover here, and that is
    # correct -- a headline resolves the event at the start of the turn, which
    # is exactly the "first AR" these events want (see `_EARLY_TURN_EVENTS`).
    # This test is about the DEFCON-2 refusal, so it holds that variable still.
    decision = Decision(
        id=901, actor=Side.US, kind=DecisionKind.HEADLINE_PLAY,
        options=(
            Action(DecisionKind.HEADLINE_PLAY, {"card": "Duck_and_Cover"}),
            Action(DecisionKind.HEADLINE_PLAY, {"card": "Nuclear_Test_Ban"}),
        ),
    )
    at2 = dataclasses.replace(obs, pending_decision=decision, defcon=2)
    assert GreedyPlayer().choose_action(at2, []).payload["card"] == "Nuclear_Test_Ban"
    # At DEFCON 3 the drop only reaches 2, so the card is playable again.
    at3 = dataclasses.replace(obs, pending_decision=decision, defcon=3)
    assert GreedyPlayer().choose_action(at3, []).payload["card"] == "Duck_and_Cover"


def test_the_bot_refuses_a_suicide_event_play():
    engine = Engine.new_game(seed=1)
    obs = engine.observe(Side.US)
    decision = Decision(
        id=902, actor=Side.US, kind=DecisionKind.PLAY_MODE,
        options=(
            Action(DecisionKind.PLAY_MODE, {"mode": "event"}),
            Action(DecisionKind.PLAY_MODE, {"mode": "ops"}),
        ),
        context={"card": "Duck_and_Cover"},
    )
    at2 = dataclasses.replace(obs, pending_decision=decision, defcon=2)
    assert GreedyPlayer().choose_action(at2, []).payload["mode"] == "ops"


# -- own-event valuation, from the card playbook ------------------------------
#
# The playbook's per-side advice ("Always event", "Free event", "Usually
# better to use for ops") is the source for every number below. What is being
# tested is not the exact value -- that is a judgement, and the comments in
# greedy.py carry it -- but that the *extremes* are wired through to a
# decision, and that the cards the playbook rules out stay out.


def _play_mode_choice(phasing, defcon, cid, modes=("event", "ops")):
    obs = _clean_obs(phasing, defcon)
    decision = Decision(
        id=903, actor=phasing, kind=DecisionKind.PLAY_MODE,
        options=tuple(Action(DecisionKind.PLAY_MODE, {"mode": m}) for m in modes),
        context={"card": cid},
    )
    return GreedyPlayer().choose_action(
        dataclasses.replace(obs, pending_decision=decision), []
    ).payload["mode"]


def test_a_decisive_own_event_outranks_spending_its_ops():
    """Fidel is 2 Ops (6.0 on the ops scale) and the playbook's entry is
    "Always event." -- so the event wins. Glasnost is the control: also
    priced, but 4 Ops (12.0) is worth more than a "Free event" (7.0)."""
    assert _play_mode_choice(Side.USSR, 3, "Fidel") == "event"
    assert _play_mode_choice(Side.USSR, 3, "Glasnost") == "ops"


def test_an_unpriced_own_event_still_prefers_ops():
    """Five Year Plan is "Ops when your hand is short on US cards" -- a
    condition this bot cannot evaluate, so it keeps the Ops-first default
    rather than guessing."""
    assert _play_mode_choice(Side.USSR, 3, "Five_Year_Plan") == "ops"
    # Nuclear Test Ban is the playbook's explicit "usually 4 clean Ops".
    assert _play_mode_choice(Side.US, 3, "Nuclear_Test_Ban") == "ops"


def test_the_playbook_s_never_event_cards_are_not_priced():
    """The negative half of the table: these have entries saying the event is
    a mistake, and an unpriced card is how the bot expresses that."""
    from struggler.bots.greedy import _own_event_value

    obs = _clean_obs(Side.US, 3)
    for cid in ("NORAD", "NATO", "Olympic_Games", "Cuban_Missile_Crisis"):
        assert _own_event_value(Side.US, obs, cid) == 0.0, cid
    assert _own_event_value(Side.USSR, obs, "Flower_Power") == 0.0


def test_an_event_is_never_priced_for_the_side_that_cannot_fire_it():
    """A US card's event fires for the US. The USSR never gets the `event`
    mode for it, so there is nothing for it to be worth."""
    from struggler.bots.greedy import _own_event_value

    assert _own_event_value(Side.USSR, _clean_obs(Side.USSR, 3), "Marshall_Plan") == 0.0
    assert _own_event_value(Side.US, _clean_obs(Side.US, 3), "Fidel") == 0.0
    # Neutral cards are fireable by both seats.
    assert _own_event_value(Side.US, _clean_obs(Side.US, 3), "Junta") > 0.0
    assert _own_event_value(Side.USSR, _clean_obs(Side.USSR, 3), "Junta") > 0.0


def test_the_early_turn_events_are_worth_firing_only_on_the_first_action_round():
    """"Event, first AR of the turn. Worthless played late." -- a condition the
    bot can read, so it is encoded rather than dropped."""
    from struggler.bots.greedy import _EVENT_DECISIVE, _own_event_value

    engine = Engine.new_game(seed=1)
    obs = engine.observe(Side.US)
    assert _own_event_value(Side.US, dataclasses.replace(obs, action_round=1), "Containment") == _EVENT_DECISIVE
    assert _own_event_value(Side.US, dataclasses.replace(obs, action_round=3), "Containment") == 0.0
    # `_clean_obs` starts at action round 1, so pin the round to test the
    # "worthless played late" half at a decision, not just at the predicate.
    late = dataclasses.replace(_clean_obs(Side.US, 3), action_round=3)
    decision = Decision(
        id=906, actor=Side.US, kind=DecisionKind.PLAY_MODE,
        options=(
            Action(DecisionKind.PLAY_MODE, {"mode": "event"}),
            Action(DecisionKind.PLAY_MODE, {"mode": "ops"}),
        ),
        context={"card": "Containment"},
    )
    assert GreedyPlayer().choose_action(
        dataclasses.replace(late, pending_decision=decision), []
    ).payload["mode"] == "ops"
    early = dataclasses.replace(late, action_round=1, pending_decision=decision)
    assert GreedyPlayer().choose_action(early, []).payload["mode"] == "event"


def test_every_priced_event_id_exists_and_belongs_to_that_side():
    """The table is keyed by card id. An id that silently disappeared, or an
    entry filed under the seat that cannot fire it, would turn a priced event
    into a no-op with no error anywhere."""
    from struggler.bots.greedy import _OWN_EVENT_VALUE, _own_event_value
    from struggler.engine.cards import load_cards

    cards = load_cards()
    for cid, by_side in _OWN_EVENT_VALUE.items():
        assert cid in cards, cid
        for side_name in by_side:
            assert side_name in (Side.US.value, Side.USSR.value), (cid, side_name)
            side = Side(side_name)
            card = cards[cid]
            assert card.side.value in (side.value, "NEUTRAL"), (cid, side_name)
            assert _own_event_value(side, _clean_obs(side, 3), cid) > 0.0, (cid, side_name)


def test_events_off_makes_every_event_worthless():
    """The `event` mode is still offered with the layer off, but nothing
    resolves, so it is a no-op discard -- and the bot has to be able to tell,
    which is what `Observation.events_enabled` is for."""
    from struggler.bots.greedy import _own_event_value

    obs = dataclasses.replace(_clean_obs(Side.USSR, 3), events_enabled=False)
    assert _own_event_value(Side.USSR, obs, "Fidel") == 0.0
    assert _play_mode_choice(Side.USSR, 3, "Fidel") == "event"  # events on
    decision = Decision(
        id=904, actor=Side.USSR, kind=DecisionKind.PLAY_MODE,
        options=(
            Action(DecisionKind.PLAY_MODE, {"mode": "event"}),
            Action(DecisionKind.PLAY_MODE, {"mode": "ops"}),
        ),
        context={"card": "Fidel"},
    )
    off = dataclasses.replace(obs, pending_decision=decision)
    assert GreedyPlayer().choose_action(off, []).payload["mode"] == "ops"


def test_the_headline_prices_an_own_event_over_a_filler():
    """A headline resolves as the card's event (5.1) and costs no action
    round, so a card worth firing is worth headlining."""
    engine = Engine.new_game(seed=1)
    obs = engine.observe(Side.US)
    decision = Decision(
        id=905, actor=Side.US, kind=DecisionKind.HEADLINE_PLAY,
        options=(
            Action(DecisionKind.HEADLINE_PLAY, {"card": "Marshall_Plan"}),
            Action(DecisionKind.HEADLINE_PLAY, {"card": "Nuclear_Test_Ban"}),
        ),
    )
    assert GreedyPlayer().choose_action(
        dataclasses.replace(obs, pending_decision=decision), []
    ).payload["card"] == "Marshall_Plan"


# -- the scoreboard, and the DEFCON floor it is read against ------------------


def test_scoreboard_value_is_us_positive_and_flips_for_the_ussr():
    weights = GreedyWeights()
    common = {"defcon": 5, "military_ops": {"US": 5, "USSR": 5}, "turn": 3, "action_round": 1}
    assert scoreboard_value(weights, Side.US, vp=4, **common) == pytest.approx(8.0)
    assert scoreboard_value(weights, Side.USSR, vp=4, **common) == pytest.approx(-8.0)


def test_a_military_ops_shortfall_is_worth_more_the_later_it_is():
    """"If you do not meet the required number, your opponent gets the
    difference" -- assessed at the end of the turn, so the same shortfall on
    the last action round is real VP and on the first is cheap to fix."""
    weights = GreedyWeights()
    behind = {"US": 0, "USSR": 4}  # the US is short, the USSR is level
    first = scoreboard_value(weights, Side.US, vp=0, defcon=4, military_ops=behind, turn=3, action_round=1)
    last = scoreboard_value(
        weights, Side.US, vp=0, defcon=4, military_ops=behind, turn=3, action_round=action_rounds(3)
    )
    assert last < first < 0
    assert last == pytest.approx(-weights.vp_weight * weights.milops_weight * 4)
    # Level on both sides is worth nothing either way: it is a *difference*.
    level = {"US": 4, "USSR": 4}
    assert scoreboard_value(
        weights, Side.US, vp=0, defcon=4, military_ops=level, turn=3, action_round=1
    ) == pytest.approx(0.0)


def test_milops_gain_is_zero_once_the_requirement_is_already_met():
    weights = GreedyWeights()
    obs = dataclasses.replace(_clean_obs(Side.USSR, 4), action_round=action_rounds(1))
    met = dataclasses.replace(obs, military_ops={"US": 0, "USSR": 4})
    assert milops_gain(weights, met, Side.USSR, 3) == pytest.approx(0.0)
    short = dataclasses.replace(obs, military_ops={"US": 4, "USSR": 0})
    assert milops_gain(weights, short, Side.USSR, 3) == pytest.approx(6.0)


def test_closing_a_military_ops_shortfall_raises_the_coup_score():
    """The Coup is the only Ops type that pays the requirement, so the same
    Coup is worth more when the track is behind -- which is how "Coup when you
    are behind on Military Ops" falls out of the score."""
    weights = GreedyWeights()
    board = Board()
    board.influence["Iran"]["US"] = 2
    obs = _clean_obs(Side.USSR, 4)
    obs = dataclasses.replace(obs, action_round=action_rounds(obs.turn))
    coup = Action(DecisionKind.OPS_TYPE, {"type": "coup"})
    behind = dataclasses.replace(obs, military_ops={"US": 4, "USSR": 0})
    level = dataclasses.replace(obs, military_ops={"US": 4, "USSR": 4})
    ctx = {"ops": 3, "bonus": None}
    behind_score = _score_ops_type(
        weights, board, dataclasses.replace(behind, pending_decision=_ops_type_decision(3)), coup
    )
    level_score = _score_ops_type(
        weights, board, dataclasses.replace(level, pending_decision=_ops_type_decision(3)), coup
    )
    assert behind_score - level_score == pytest.approx(6.0)
    assert ctx  # the context shape above is what the decision carries


def _ops_type_decision(ops):
    return Decision(
        id=907, actor=Side.USSR, kind=DecisionKind.OPS_TYPE,
        options=(
            Action(DecisionKind.OPS_TYPE, {"type": "influence"}),
            Action(DecisionKind.OPS_TYPE, {"type": "coup"}),
            Action(DecisionKind.OPS_TYPE, {"type": "realignment"}),
        ),
        context={"ops": ops, "bonus": None},
    )


def test_a_held_suicide_card_is_a_strand_at_defcon_2():
    """Holding the opponent's DEFCON degrader at DEFCON 2 is a card that cannot
    be committed at all: Ops fires their event and the Space Race is the only
    door left. It is only a *strand* once that door is used up."""
    obs = dataclasses.replace(
        _clean_obs(Side.USSR, 3), hand=("Duck_and_Cover", "Fidel", "Five_Year_Plan")
    )
    # The Space Race attempt is still available this turn: disposable.
    assert _unavoidable_strands(obs, Side.USSR) == 0
    spent = dataclasses.replace(obs, space_race_attempts={"US": 0, "USSR": 1})
    assert _unavoidable_strands(spent, Side.USSR) == 1
    # Two of them with one attempt: one is unavoidable either way.
    two = dataclasses.replace(obs, hand=("Duck_and_Cover", "Soviets_Shoot_Down_KAL_007"))
    assert _unavoidable_strands(two, Side.USSR) == 1
    assert _unavoidable_strands(
        dataclasses.replace(two, space_race_attempts={"US": 0, "USSR": 1}), Side.USSR
    ) == 2
    # A card that is fine at DEFCON 2 does not count.
    assert _unavoidable_strands(
        dataclasses.replace(obs, hand=("Fidel", "Five_Year_Plan")), Side.USSR
    ) == 0


def test_the_strand_penalty_applies_only_to_a_coup_that_drops_to_defcon_2():
    weights = GreedyWeights()
    obs = dataclasses.replace(
        _clean_obs(Side.USSR, 3), hand=("Duck_and_Cover", "Soviets_Shoot_Down_KAL_007")
    )
    iran = Board().countries["Iran"]  # Battleground: a Coup there drops DEFCON
    plain = next(info for info in Board().countries.values() if not info.battleground)
    assert iran.battleground and not plain.battleground
    assert _strand_penalty(weights, obs, Side.USSR, iran) == pytest.approx(
        weights.defcon_strand_penalty
    )
    assert _strand_penalty(weights, obs, Side.USSR, plain) == pytest.approx(0.0)
    # At DEFCON 4 the drop lands on 3, so there is a turn of slack.
    assert _strand_penalty(weights, dataclasses.replace(obs, defcon=4), Side.USSR, iran) == 0.0
    # And with nothing stranded, taking DEFCON to 2 is just ordinary caution.
    assert _strand_penalty(weights, dataclasses.replace(obs, hand=("Fidel",)), Side.USSR, iran) == 0.0


def test_a_stranded_card_is_pushed_to_the_space_race_at_defcon_3():
    weights = GreedyWeights()
    obs = dataclasses.replace(_clean_obs(Side.USSR, 3), hand=("Duck_and_Cover",))
    assert _strand_disposal_bonus(weights, obs, Side.USSR, "Duck_and_Cover") == pytest.approx(
        weights.strand_disposal_bonus
    )
    # Not at DEFCON 4+: the marker recovers at the end of the turn, so there is
    # no countdown yet and the Ops are worth more.
    at4 = dataclasses.replace(obs, defcon=4)
    assert _strand_disposal_bonus(weights, at4, Side.USSR, "Duck_and_Cover") == 0.0
    # And not for a card that is playable at DEFCON 2.
    assert _strand_disposal_bonus(weights, obs, Side.USSR, "Fidel") == 0.0


def test_the_card_choice_refuses_a_card_with_no_safe_mode():
    """The card choice happens *before* the mode choice, so a card that cannot
    be committed safely has to lose here too -- `_score_play_mode` never sees a
    card that was not picked. This is the shape of the remaining DEFCON-1
    losses: at DEFCON 2, holding five cards, the bot picked We Will Bury You for
    its 4 Ops (the highest score in the hand) and every mode left was fatal."""
    obs = _clean_obs(Side.US, 2)
    decision = Decision(
        id=909, actor=Side.US, kind=DecisionKind.ACTION_ROUND_PLAY,
        options=(
            Action(DecisionKind.ACTION_ROUND_PLAY, {"card": "We_Will_Bury_You"}),
            Action(DecisionKind.ACTION_ROUND_PLAY, {"card": "Nuclear_Test_Ban"}),
        ),
    )
    hand = ("We_Will_Bury_You", "Nuclear_Test_Ban")
    disposable = dataclasses.replace(obs, pending_decision=decision, hand=hand)
    # The Space Race attempt is still available, so the play is to dispose of it
    # -- worth more than the 4 Ops it would otherwise look like.
    assert GreedyPlayer().choose_action(disposable, []).payload["card"] == "We_Will_Bury_You"
    # With the attempt spent, the card is a loss waiting for the hand to empty.
    spent = dataclasses.replace(disposable, space_race_attempts={"US": 1, "USSR": 0})
    assert GreedyPlayer().choose_action(spent, []).payload["card"] == "Nuclear_Test_Ban"
    # And when it is the only card in hand it is played: nothing else is legal,
    # so the refusal must not turn into a crash or a stall. (The option list is
    # what the engine builds from the hand, so it has to be rebuilt here too.)
    alone = dataclasses.replace(
        obs,
        hand=("We_Will_Bury_You",),
        space_race_attempts={"US": 1, "USSR": 0},
        pending_decision=Decision(
            id=910, actor=Side.US, kind=DecisionKind.ACTION_ROUND_PLAY,
            options=(Action(DecisionKind.ACTION_ROUND_PLAY, {"card": "We_Will_Bury_You"}),),
        ),
    )
    assert GreedyPlayer().choose_action(alone, []).payload["card"] == "We_Will_Bury_You"


def test_the_bot_prefers_spacing_a_stranded_card_over_spending_it():
    """The two halves meet here: the Space Race disposal bonus has to be enough
    to beat the Ops of a 2-Ops card the bot would otherwise spend."""
    obs = dataclasses.replace(_clean_obs(Side.USSR, 3), hand=("Duck_and_Cover",))
    decision = Decision(
        id=908, actor=Side.USSR, kind=DecisionKind.PLAY_MODE,
        options=(
            Action(DecisionKind.PLAY_MODE, {"mode": "event"}),
            Action(DecisionKind.PLAY_MODE, {"mode": "ops"}),
            Action(DecisionKind.PLAY_MODE, {"mode": "space_race"}),
        ),
        context={"card": "Duck_and_Cover"},
    )
    chosen = GreedyPlayer().choose_action(
        dataclasses.replace(obs, pending_decision=decision), []
    ).payload["mode"]
    assert chosen == "space_race"


# -- Space Race card lists, from "General Strategy: The Space Race" ----------


def test_every_named_space_race_card_exists():
    """The lists are keys into the card table. An id that silently disappeared
    would turn a "this belongs in space" rule into a no-op, so it fails here."""
    from struggler.engine.cards import load_cards

    cards = load_cards()
    missing = sorted(c for c in (_USSR_SPACE_RACE | _US_SPACE_RACE) if c not in cards)
    assert not missing, f"space-race lists name cards that do not exist: {missing}"


def test_space_race_lists_are_the_article_s_cards():
    """"These are the US events that I tend to Space Race" / "These are the USSR
    events that I tend to Space Race" — the article's cards, per side."""
    # One card from each of the article's own sub-groups, sampled not exhaustive.
    assert {"CIA_Created", "Grain_Sales_to_Soviets", "Tear_Down_This_Wall"} <= _USSR_SPACE_RACE
    assert {"The_Voice_Of_America", "Ussuri_River_Skirmish", "Puppet_Governments"} <= _USSR_SPACE_RACE
    assert {"Lone_Gunman", "We_Will_Bury_You", "Ortega_Elected_in_Nicaragua"} <= _US_SPACE_RACE
    assert {"Decolonization", "De_Stalinization", "OPEC"} <= _US_SPACE_RACE
    # The lists are per side: the US must not be told to space its own events.
    from struggler.engine.cards import load_cards

    cards = load_cards()
    # The USSR spaces *US* cards; the US spaces *USSR* cards. Neutral cards
    # belong to neither side and are not in these lists.
    wrong = [c for c in _USSR_SPACE_RACE if cards[c].side.value not in ("US",)]
    assert not wrong, f"the USSR's list names cards that are not US events: {wrong}"
    wrong = [c for c in _US_SPACE_RACE if cards[c].side.value not in ("USSR",)]
    assert not wrong, f"the US's list names cards that are not USSR events: {wrong}"


def test_listed_cards_are_preferred_for_the_space_race():
    engine = Engine.new_game(seed=1)
    obs = engine.observe(Side.USSR)

    def space_score(cid):
        decision = Decision(
            id=910, actor=Side.USSR, kind=DecisionKind.PLAY_MODE,
            options=(Action(DecisionKind.PLAY_MODE, {"mode": "space_race"}),),
            context={"card": cid},
        )
        return GreedyPlayer().option_scores(
            dataclasses.replace(obs, pending_decision=decision, side=Side.USSR)
        )[0]

    # "The Voice of America" is on the list; "Blockade" is not.
    assert space_score("The_Voice_Of_America") > space_score("Blockade")


def test_the_article_warns_against_over_spacing():
    """"The number one mistake beginning players make ... is to send too many
    cards off to space" and "Ops are paramount" — so a big Ops card is still
    worth more on the board than a listed card is in space."""
    engine = Engine.new_game(seed=1)
    obs = engine.observe(Side.US)
    decision = Decision(
        id=911, actor=Side.US, kind=DecisionKind.PLAY_MODE,
        options=(
            Action(DecisionKind.PLAY_MODE, {"mode": "space_race"}),
            Action(DecisionKind.PLAY_MODE, {"mode": "ops"}),
        ),
        context={"card": "Muslim_Revolution"},   # listed, 2 Ops
    )
    scores = GreedyPlayer().option_scores(dataclasses.replace(obs, pending_decision=decision))
    # It is listed, so space_race should outscore the 2 Ops play.
    assert scores[0] > scores[1]


# -- Reshuffle timing, from "General Strategy: Reshuffles" -------------------


def test_the_engine_reshuffles_on_the_article_s_turns():
    """The article describes the physical deck's Turns 3 and 7. This engine
    reshuffles when the draw pile empties, so the claim has to be checked
    against this deck — it is the evidence behind _RESHUFFLE_TURNS."""
    from collections import Counter

    from struggler.engine import Engine

    seen: Counter[int] = Counter()
    for seed in range(1, 9):
        engine = Engine.new_game(seed=seed)
        steps = 0
        while not engine.is_terminal and steps < 20000:
            decision = engine.pending_decision
            if decision is None:
                break
            before = len(engine.discard_pile)
            engine.step(decision.options[0])
            if before > 0 and not engine.discard_pile and engine.draw_pile:
                seen[engine.turn] += 1
            steps += 1
    assert seen, "no reshuffle observed in eight games"
    # Turn 3 is the early reshuffle in every game; Turn 7 shows up once the
    # deck lasts that long.
    assert seen[3] == 8, f"expected the early reshuffle on turn 3 every time: {dict(seen)}"
    assert set(seen) <= {1, 3, 7, 9}, f"unexpected reshuffle turns: {sorted(seen)}"


def test_the_timing_bonus_applies_only_on_reshuffle_turns():
    assert _is_reshuffle_turn(3) and _is_reshuffle_turn(7)
    assert not _is_reshuffle_turn(2)
    assert not _is_reshuffle_turn(6)


def test_spacing_a_vital_card_is_better_on_a_reshuffle_turn():
    """"you want to discard them on Turns 3 and 7, rather than on Turns 2 or 6\""""
    engine = Engine.new_game(seed=1)
    obs = engine.observe(Side.US)

    def score(turn):
        decision = Decision(
            id=912, actor=Side.US, kind=DecisionKind.PLAY_MODE,
            options=(Action(DecisionKind.PLAY_MODE, {"mode": "space_race"}),),
            context={"card": "De_Stalinization"},     # a vital USSR event
        )
        return GreedyPlayer().option_scores(
            dataclasses.replace(obs, pending_decision=decision, turn=turn)
        )[0]

    assert score(3) > score(2)
    assert score(7) > score(6)
    assert score(3) == score(7)


def test_the_timing_bonus_ignores_cards_that_are_not_vital():
    engine = Engine.new_game(seed=1)
    obs = engine.observe(Side.US)

    def score(turn):
        decision = Decision(
            id=913, actor=Side.US, kind=DecisionKind.PLAY_MODE,
            options=(Action(DecisionKind.PLAY_MODE, {"mode": "space_race"}),),
            context={"card": "Blockade"},    # a USSR event, but not on the list
        )
        return GreedyPlayer().option_scores(
            dataclasses.replace(obs, pending_decision=decision, turn=turn)
        )[0]

    assert score(3) == score(2), "an unlisted card must not gain from the timing"


# -- Realignments, from "General Strategy: Realignments" --------------------


def test_realignment_access_severing_needs_a_last_foothold():
    """"The first kind of realignment, and the best kind, is the realignment that
    eliminates your opponent's access to the region." Only true when it is their
    last influence there."""
    engine = Engine.new_game(seed=1)
    board = engine.board
    for cid in board.influence:
        board.influence[cid]["US"] = 0
        board.influence[cid]["USSR"] = 0

    board.influence["Cuba"]["USSR"] = 3
    assert _realignment_severs_access(board, Side.USSR, "Cuba")

    board.influence["Nicaragua"]["USSR"] = 1   # another way into the region
    assert not _realignment_severs_access(board, Side.USSR, "Cuba")

    board.influence["Cuba"]["USSR"] = 0
    assert not _realignment_severs_access(board, Side.USSR, "Cuba")


def test_realignments_are_preferred_at_defcon_2():
    """"In general, realignments only occur at DEFCON 2. In most cases,
    battleground coups are a more powerful method ... But once DEFCON drops to 2,
    you must search for other ways to attack your opponent's battlegrounds.\""""
    engine = Engine.new_game(seed=1)
    obs = engine.observe(Side.US)

    def score(defcon):
        decision = Decision(
            id=914, actor=Side.US, kind=DecisionKind.REALIGNMENT_TARGET,
            options=(Action(DecisionKind.REALIGNMENT_TARGET, {"country": "Cuba"}),),
            context={"card_ops": 3, "spent": 0},
        )
        merged = dataclasses.replace(
            obs, pending_decision=decision, defcon=defcon,
            influence={**obs.influence, "Cuba": {"US": 0, "USSR": 3}},
        )
        return GreedyPlayer().option_scores(merged)[0]

    assert score(2) > score(3), "DEFCON 2 should favour the realignment"
    assert score(3) == score(5)


# -- the probe's risk counter: two sets, two meanings ------------------------


def test_defcon_risk_kind_names_the_two_sets_separately():
    """The probe printed one combined number beside "0 games ended at DEFCON 1",
    which reads as a contradiction and misleads. `_defcon_suicide_risk` is an
    over-approximation by design -- right for a scorer, wrong for a counter."""
    from struggler.bots.greedy import (
        DEFCON_RISK_CONDITIONAL,
        DEFCON_RISK_NONE,
        DEFCON_RISK_UNCONDITIONAL,
        defcon_risk_kind,
    )

    # Category 1: the event drops DEFCON itself. Real at DEFCON 2.
    at2 = _clean_obs(Side.USSR, 2)
    assert defcon_risk_kind(at2, Side.USSR, "Duck_and_Cover", "ops") == DEFCON_RISK_UNCONDITIONAL

    # Category 2: the event hands the opponent Ops. It only threatens when they
    # have a battleground of ours to coup, which is why the flag is conditional.
    armed = _clean_obs(Side.USSR, 2, [(Side.USSR, "Angola", 3)])
    assert defcon_risk_kind(armed, Side.USSR, "CIA_Created", "ops") == DEFCON_RISK_CONDITIONAL
    # Same card, nothing to coup: not a risk at all.
    assert defcon_risk_kind(_clean_obs(Side.USSR, 2), Side.USSR, "CIA_Created", "ops") == DEFCON_RISK_NONE

    # A safe card, and a safe context, are both "none".
    assert defcon_risk_kind(at2, Side.USSR, "Containment", "ops") == DEFCON_RISK_NONE
    assert defcon_risk_kind(_clean_obs(Side.USSR, 4), Side.USSR, "Duck_and_Cover", "ops") == DEFCON_RISK_NONE


def test_defcon_risk_kind_agrees_with_the_predicate_it_wraps():
    """It must never disagree with `_defcon_suicide_risk`, or the counter and
    the scorer would be reading different games."""
    from struggler.bots.greedy import DEFCON_RISK_NONE, defcon_risk_kind

    for obs_side, defcon, country in ((Side.USSR, 2, None), (Side.US, 2, None), (Side.USSR, 3, None)):
        obs = _clean_obs(obs_side, defcon, [(obs_side, "Angola", 3)] if country else None)
        for cid in ("Duck_and_Cover", "CIA_Created", "Containment", "Lone_Gunman"):
            for mode in ("ops", "event", "space_race"):
                flagged = _defcon_suicide_risk(obs, obs_side, cid, mode)
                kind = defcon_risk_kind(obs, obs_side, cid, mode)
                assert (kind != DEFCON_RISK_NONE) == flagged, (cid, mode, kind)


def test_a_new_suicide_rule_must_be_taught_to_the_classifier(monkeypatch):
    """`defcon_risk_kind` raises rather than bucketing an unknown case as
    "cannot kill me" -- the one answer that must never be wrong."""
    import struggler.bots.greedy as greedy

    monkeypatch.setattr(greedy, "_defcon_suicide_risk", lambda *a, **k: True)
    with pytest.raises(AssertionError, match="neither category"):
        greedy.defcon_risk_kind(_clean_obs(Side.USSR, 2), Side.USSR, "Containment", "ops")


# -- the turn plan: one plan, five scorers ------------------------------------

_USSR_DISPOSE_HAND = ("CIA_Created", "Duck_and_Cover", "Decolonization")


def _plan_obs(side, defcon, hand, influence=None):
    engine = Engine.new_game(seed=1)
    obs = _clean_obs(side, defcon, influence)
    return dataclasses.replace(engine.observe(side), defcon=defcon, influence=obs.influence, hand=tuple(hand))


def test_plan_turn_is_pure_and_frozen():
    """The acceptance criterion as an assertion: the same observation plans
    the same plan twice, and the plan cannot be mutated in place. A plan
    stored on the player would silently diverge between pickled worker copies
    and MCTS clones, so there is deliberately nothing to store it in."""
    obs = _plan_obs(Side.USSR, 4, _USSR_DISPOSE_HAND, [(Side.USSR, "Angola", 3)])
    weights = GreedyWeights()
    first, second = plan_turn(obs, Side.USSR, weights), plan_turn(obs, Side.USSR, weights)
    assert first == second
    assert isinstance(first, TurnPlan)
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.defcon_floor = 2  # type: ignore[misc]


def test_plan_dispose_orders_unconditional_first_then_higher_ops():
    """While the marker is still high (DEFCON 5 here -- every existing
    DEFCON-2 rule is silent), the plan already names the cards that cannot be
    committed at 2: Duck and Cover kills on commit, CIA Created only hands
    over Ops, Decolonization is the USSR's own and always committable."""
    obs = _plan_obs(Side.USSR, 5, _USSR_DISPOSE_HAND, [(Side.USSR, "Angola", 3)])
    plan = plan_turn(obs, Side.USSR, GreedyWeights())
    assert plan.dispose == ("Duck_and_Cover", "CIA_Created")


def test_plan_floor_is_three_while_a_card_must_leave():
    obs = _plan_obs(Side.USSR, 4, ("Duck_and_Cover",))
    assert plan_turn(obs, Side.USSR, GreedyWeights()).defcon_floor == 3
    clean = _plan_obs(Side.USSR, 4, ("Decolonization",))
    assert plan_turn(clean, Side.USSR, GreedyWeights()).defcon_floor == 2
    # The floor never floats above the marker: at DEFCON 2 there is nothing
    # left to hold high.
    low = _plan_obs(Side.USSR, 2, ("Duck_and_Cover",))
    assert plan_turn(low, Side.USSR, GreedyWeights()).defcon_floor == 2


def test_plan_floor_ignores_conditional_holdings():
    """CIA Created needs the opponent's cooperation to kill -- they must
    choose to coup into DEFCON 1, which loses for them, so they usually
    decline. Refusing every battleground coup at 3 for that would concede
    certain tempo against a danger that mostly does not materialise, so the
    floor stays at 2 and only the unconditional kind engages it."""
    obs = _plan_obs(Side.USSR, 4, ("CIA_Created",), [(Side.USSR, "Angola", 3)])
    plan = plan_turn(obs, Side.USSR, GreedyWeights())
    assert plan.dispose == ("CIA_Created",)
    assert plan.defcon_floor == 2


def test_plan_region_focus_follows_the_held_scoring_card():
    europe = _plan_obs(Side.US, 5, ("Europe_Scoring", "Olympic_Games"))
    assert plan_turn(europe, Side.US, GreedyWeights()).region_focus is Region.EUROPE
    se_asia = _plan_obs(Side.US, 5, ("Southeast_Asia_Scoring",))
    assert plan_turn(se_asia, Side.US, GreedyWeights()).region_focus is Region.ASIA
    none = _plan_obs(Side.US, 5, ("Olympic_Games",))
    assert plan_turn(none, Side.US, GreedyWeights()).region_focus is None


def _action_round_decision(*cards):
    return Decision(
        id=910, actor=Side.USSR, kind=DecisionKind.ACTION_ROUND_PLAY,
        options=tuple(Action(DecisionKind.ACTION_ROUND_PLAY, {"card": c}) for c in cards),
        context={},
    )


def test_action_round_play_prefers_a_dispose_card_while_an_attempt_remains():
    """At DEFCON 4 the old refusal is silent (it only fires at 2), so without
    the plan Duck and Cover scores its Ops minus the opponent-event penalty
    and loses to an ordinary 2-Ops card. With the plan it is picked -- it has
    to be *chosen* now, because the mode choice comes after."""
    player = GreedyPlayer()
    obs = dataclasses.replace(
        _plan_obs(Side.USSR, 4, ("Duck_and_Cover", "Decolonization")),
        pending_decision=_action_round_decision("Duck_and_Cover", "Decolonization"),
    )
    scores = player.option_scores(obs)
    assert scores[0] > scores[1]
    assert player.choose_action(obs, []).payload["card"] == "Duck_and_Cover"
    # Attempt spent: the same card is refused -- preferring it now would spend
    # the action round on a card with nowhere to go.
    spent = dataclasses.replace(obs, space_race_attempts={"USSR": 1})
    refused = player.option_scores(spent)
    assert refused[0] <= -player.weights.defcon_suicide_penalty


def test_play_mode_spaces_a_dispose_card_while_the_marker_is_high():
    """`_strand_disposal_bonus` only prices disposal at DEFCON 3 and below, so
    at 5 the Space Race has to beat tempting Ops on the plan alone. Same Ops
    on both cards, so the only difference left is the plan's bonus."""
    player = GreedyPlayer()
    decision = Decision(
        id=911, actor=Side.USSR, kind=DecisionKind.PLAY_MODE,
        options=(
            Action(DecisionKind.PLAY_MODE, {"mode": "space_race"}),
            Action(DecisionKind.PLAY_MODE, {"mode": "ops"}),
        ),
        context={"card": "Duck_and_Cover"},
    )
    at5 = dataclasses.replace(_plan_obs(Side.USSR, 5, ("Duck_and_Cover",)), pending_decision=decision)
    space, ops = player.option_scores(at5)
    assert space > ops, (space, ops)
    control = dataclasses.replace(at5, pending_decision=dataclasses.replace(decision, context={"card": "Containment"}))
    space_control, _ = player.option_scores(control)
    assert space - space_control == player.weights.strand_disposal_bonus


def _plan_ops_type_decision():
    return Decision(
        id=912, actor=Side.USSR, kind=DecisionKind.OPS_TYPE,
        options=tuple(
            Action(DecisionKind.OPS_TYPE, {"type": t}) for t in ("influence", "coup", "realignment")
        ),
        context={"ops": 3},
    )


def test_ops_type_refuses_a_coup_below_the_plan_floor():
    """DEFCON 3, holding Duck and Cover: the only battleground coup (Iran)
    would take the marker to 2 with the card still in hand, so "coup" is
    refused as an Ops type. A non-degrading coup (Jordan) stays live."""
    player = GreedyPlayer()
    iran = dataclasses.replace(
        _plan_obs(Side.USSR, 3, ("Duck_and_Cover",), [(Side.US, "Iran", 2)]),
        pending_decision=_plan_ops_type_decision(),
    )
    assert player.option_scores(iran)[1] <= -player.weights.defcon_self_kill_penalty
    jordan = dataclasses.replace(
        _plan_obs(Side.USSR, 3, ("Duck_and_Cover",), [(Side.US, "Jordan", 1)]),
        pending_decision=_plan_ops_type_decision(),
    )
    assert player.option_scores(jordan)[1] > -player.weights.defcon_self_kill_penalty


def test_coup_target_refuses_a_floor_breaking_battleground():
    """The same floor at the target decision: Iran refused, Jordan live, at a
    DEFCON where every pre-plan rule is silent (all of them fire at 2)."""
    player = GreedyPlayer()

    def decide(country):
        return Decision(
            id=913, actor=Side.USSR, kind=DecisionKind.COUP_TARGET,
            options=(Action(DecisionKind.COUP_TARGET, {"country": country}),),
            context={"ops": 3},
        )

    iran = dataclasses.replace(
        _plan_obs(Side.USSR, 3, ("Duck_and_Cover",), [(Side.US, "Iran", 2)]),
        pending_decision=decide("Iran"),
    )
    assert player.option_scores(iran)[0] <= -player.weights.defcon_self_kill_penalty
    jordan = dataclasses.replace(
        _plan_obs(Side.USSR, 3, ("Duck_and_Cover",), [(Side.US, "Jordan", 1)]),
        pending_decision=decide("Jordan"),
    )
    assert player.option_scores(jordan)[0] > -player.weights.defcon_self_kill_penalty


def test_place_influence_prefers_the_plan_region():
    """Same country, same board; the only difference is the hand. With Europe
    Scoring held, France costs exactly the plan's bonus more than with a
    scoreless hand -- placement agrees with the card choice."""
    player = GreedyPlayer()
    decision = Decision(
        id=914, actor=Side.US, kind=DecisionKind.PLACE_INFLUENCE,
        options=(Action(DecisionKind.PLACE_INFLUENCE, {"country": "France"}),),
        context={},
    )
    focused = dataclasses.replace(
        _plan_obs(Side.US, 5, ("Europe_Scoring",)), pending_decision=decision
    )
    plain = dataclasses.replace(
        _plan_obs(Side.US, 5, ("Olympic_Games",)), pending_decision=decision
    )
    assert player.option_scores(focused)[0] - player.option_scores(plain)[0] == pytest.approx(
        player.weights.plan_region_focus_bonus
    )


# -- event influence is scored, not first-listed --------------------------------

_DECOLONIZATION_CTX = {
    "event": "Decolonization", "op": "place", "choose_side": "USSR",
    "inf_side": "USSR", "remaining": 4, "cap": 1, "whole": False,
    "requires_uncontrolled": False, "exclude_controlled_by": None,
    "amount": 1, "placed": {},
}


def _event_influence_decision(ctx, *countries):
    return Decision(
        id=920, actor=Side(Side(ctx["choose_side"])), kind=DecisionKind.EVENT_INFLUENCE,
        options=tuple(
            Action(DecisionKind.EVENT_INFLUENCE, {"country": c}) for c in countries
        ),
        context=dict(ctx),
    )


def test_event_placement_prefers_battlegrounds_over_list_order():
    """The reported game: Decolonization's candidates arrive in map order, so
    the old first-option fallback placed into whatever the data file lists
    first. A battleground is worth more than a non-battleground in the same
    empty region, whichever order the options arrive in."""
    player = GreedyPlayer()
    # Cameroon (non-BG) lists before Nigeria (BG); the bot must not care.
    decide = _event_influence_decision(_DECOLONIZATION_CTX, "Cameroon", "Nigeria")
    obs = dataclasses.replace(_clean_obs(Side.USSR, 5), pending_decision=decide)
    assert player.choose_action(obs, []).payload["country"] == "Nigeria"
    # And reversed: still Nigeria, proving this is valuation, not position.
    decide2 = _event_influence_decision(_DECOLONIZATION_CTX, "Nigeria", "Cameroon")
    obs2 = dataclasses.replace(_clean_obs(Side.USSR, 5), pending_decision=decide2)
    assert player.choose_action(obs2, []).payload["country"] == "Nigeria"


def test_event_removal_takes_the_highest_value_target():
    """Suez Crisis lists France first, but the UK stack is worth more: removal
    prices the board swing, not the candidate order."""
    player = GreedyPlayer()
    ctx = dict(
        _DECOLONIZATION_CTX, event="Suez_Crisis", op="remove", inf_side="US",
        remaining=4, cap=2,
    )
    decide = _event_influence_decision(ctx, "France", "UK")
    obs = _clean_obs(Side.USSR, 5, [(Side.US, "UK", 5), (Side.US, "France", 1)])
    obs = dataclasses.replace(obs, pending_decision=decide)
    assert player.choose_action(obs, []).payload["country"] == "UK"
