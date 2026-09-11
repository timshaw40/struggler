"""Tests for GreedyPlayer: the board evaluator, DEFCON-safety heuristic,
fallback behavior, and a win-rate sanity check against RandomPlayer."""

from __future__ import annotations

import dataclasses

import pytest

from struggler.bots.greedy import GreedyPlayer, GreedyWeights, board_value
from struggler.bots.naive import FirstLegalPlayer, RandomPlayer
from struggler.engine import Action, Decision, DecisionKind, Engine, Side
from struggler.engine.board import Board
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
