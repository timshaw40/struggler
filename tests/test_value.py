"""Tests for the learned board value: antisymmetric features, ridge fit, bounds."""

from __future__ import annotations

from struggler.bots.mcts import MCTSPlayer
from struggler.bots.value import DIM, LinearValue, fit_ridge, value_features
from struggler.engine import Engine, Side
from struggler.engine.board import Board


def _features(board: Board, side: Side) -> list[float]:
    return value_features(
        board,
        side,
        vp=4,
        military_ops={"US": 2, "USSR": 0},
        space_race={"US": 1, "USSR": 0},
        china_card_owner="USSR",
    )


def test_features_have_fixed_dimension():
    assert len(_features(Board(), Side.US)) == DIM


def test_features_are_antisymmetric():
    board = Board()
    board.influence["Poland"] = {"US": 3, "USSR": 1}
    board.influence["Cuba"] = {"US": 0, "USSR": 4}
    us = _features(board, Side.US)
    ussr = _features(board, Side.USSR)
    assert all(abs(a + b) < 1e-9 for a, b in zip(us, ussr))


def test_ridge_recovers_an_exact_linear_target():
    X = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [2.0, 1.0], [0.5, 0.5]]
    Y = [2.0 * x[0] - x[1] for x in X]
    w = fit_ridge(X, Y, l2=0.0)
    assert abs(w[0] - 2.0) < 1e-6 and abs(w[1] + 1.0) < 1e-6


def test_linear_value_is_bounded_and_antisymmetric():
    v = LinearValue(weights=[50.0] + [0.0] * (DIM - 1))
    hi = [1.0] + [0.0] * (DIM - 1)
    assert v.value(hi) == 1.0
    assert v.value([-x for x in hi]) == 0.0
    assert v.value([0.0] * DIM) == 0.5


def test_mcts_uses_a_learned_value_and_still_returns_a_legal_action():
    engine = Engine.new_game(seed=1)
    player = MCTSPlayer(seed=1, sims=2, rollout_depth=0, value=LinearValue())
    player.bind_engine(engine)
    observation = engine.observe(engine.pending_decision.actor)
    action = player.choose_action(observation, [])
    assert action in observation.pending_decision.options
