"""Tests for GreedyPlayer: the board evaluator, DEFCON-safety heuristic,
fallback behavior, and a win-rate sanity check against RandomPlayer."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from struggler.bots.greedy import (
    GreedyPlayer,
    GreedyWeights,
    _in_bonus_region,
    _score_coup_target,
    board_value,
)
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


def test_in_bonus_region_reads_the_aggregated_bonus_list():
    """7.4 aggregates region bonuses, so the engine now hands the decision
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
            defcon=5,
            turn_effects={},
            pending_decision=SimpleNamespace(context={"ops": 1, "bonus": bonus}),
        )
        return _score_coup_target(weights, board, observation, action)

    assert score([]) < score(["se_asia"]) < score(["asia", "se_asia"])
