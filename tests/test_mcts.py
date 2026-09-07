"""Tests for MCTSPlayer: legal actions, non-mutation, no hidden-info peek, smoke."""

from __future__ import annotations

import random

from struggler.bots.mcts import MCTSPlayer, determinize
from struggler.bots.naive import FirstLegalPlayer, RandomPlayer
from struggler.engine import Engine, Side
from struggler.runner import play_game


def _player(seed: int = 1, sims: int = 3, rollout_depth: int = 6) -> MCTSPlayer:
    return MCTSPlayer(seed=seed, sims=sims, rollout_depth=rollout_depth)


def test_mcts_returns_a_legal_action():
    engine = Engine.new_game(seed=1)
    player = _player()
    player.bind_engine(engine)
    observation = engine.observe(engine.pending_decision.actor)

    action = player.choose_action(observation, [])

    assert action in observation.pending_decision.options


def test_search_does_not_mutate_the_parent_engine():
    engine = Engine.new_game(seed=2)
    player = _player()
    player.bind_engine(engine)
    before = engine.serialize()
    observation = engine.observe(engine.pending_decision.actor)

    player.choose_action(observation, [])

    assert engine.serialize() == before


def test_determinize_does_not_copy_hidden_identities():
    """Same public state, different hidden cards, same RNG → same clone fill."""
    engine_a = Engine.new_game(seed=3)
    engine_b = Engine.new_game(seed=3)
    opp = "USSR"
    if engine_b.hands[opp] and engine_b.draw_pile:
        engine_b.hands[opp][0], engine_b.draw_pile[0] = (
            engine_b.draw_pile[0],
            engine_b.hands[opp][0],
        )
    assert engine_a.hands[opp] != engine_b.hands[opp]

    filled_a = determinize(engine_a.serialize(), "US", random.Random(0))
    filled_b = determinize(engine_b.serialize(), "US", random.Random(0))

    assert filled_a["hands"][opp] == filled_b["hands"][opp]
    assert filled_a["draw_pile"] == filled_b["draw_pile"]


def test_same_seed_picks_the_same_action():
    engine = Engine.new_game(seed=4)
    observation = engine.observe(engine.pending_decision.actor)
    first = _player(seed=9)
    first.bind_engine(engine)
    second = _player(seed=9)
    second.bind_engine(engine)

    assert first.choose_action(observation, []) == second.choose_action(observation, [])


def test_mcts_vs_first_legal_terminates():
    engine = Engine.new_game(seed=5)
    mcts = _player(seed=5, sims=2, rollout_depth=4)
    winner = play_game(engine, {Side.US: FirstLegalPlayer(), Side.USSR: mcts})

    assert engine.is_terminal
    assert winner in (Side.US, Side.USSR, None)


def test_mcts_plays_legal_actions_for_several_steps():
    engine = Engine.new_game(seed=6)
    mcts = _player(seed=6, sims=2, rollout_depth=4)
    mcts.bind_engine(engine)
    random_bot = RandomPlayer(seed=7)
    for _ in range(25):
        if engine.is_terminal:
            break
        decision = engine.pending_decision
        if decision.actor is Side.CHANCE:
            engine.step(decision.options[0])
            continue
        observation = engine.observe(decision.actor)
        if decision.actor is Side.USSR:
            action = mcts.choose_action(observation, [])
        else:
            action = random_bot.choose_action(observation, [])
        assert action in decision.options
        engine.step(action)
    assert engine.pending_decision is not None or engine.is_terminal
