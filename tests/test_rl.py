"""Tests for the neural self-play stack: encoding, net, rollouts, PPO, player."""

from __future__ import annotations

import torch

from struggler.bots.rl.encode import OPTION_DIM, STATE_DIM, option_vector, state_vector
from struggler.bots.rl.net import ActorCritic, load_policy, save_policy
from struggler.bots.rl.player import RLPlayer
from struggler.bots.rl.ppo import compute_gae, ppo_update
from struggler.bots.rl.selfplay import Episode, Transition, collect_episode
from struggler.engine import Engine


def _decision():
    engine = Engine.new_game(seed=1)
    return engine, engine.pending_decision


def test_state_and_option_vectors_have_fixed_dimensions():
    engine, decision = _decision()
    obs = engine.observe(decision.actor)
    assert len(state_vector(obs)) == STATE_DIM
    for action in decision.options:
        assert len(option_vector(action)) == OPTION_DIM


def test_net_forward_masks_padding_and_round_trips(tmp_path):
    net = ActorCritic(STATE_DIM, OPTION_DIM, hidden=8)
    state = torch.zeros(2, STATE_DIM)
    opts = torch.zeros(2, 4, OPTION_DIM)
    mask = torch.tensor([[True, True, False, False], [True, False, False, False]])
    logits, value = net(state, opts, mask)
    assert logits.shape == (2, 4) and value.shape == (2,)
    assert logits[0, 2].item() < -1e8 and logits[1, 1].item() < -1e8

    path = tmp_path / "net.pt"
    save_policy(path, net)
    restored = load_policy(path)
    assert torch.allclose(logits, restored(state, opts, mask)[0])


def test_collect_episode_returns_per_side_terminal_reward():
    net = ActorCritic(STATE_DIM, OPTION_DIM, hidden=8)
    episodes = collect_episode(net, seed=1, anchor_fraction=1.0, device="cpu")
    assert episodes and all(isinstance(e, Episode) for e in episodes)
    for ep in episodes:
        assert ep.reward in (-1.0, 0.0, 1.0)
        assert ep.transitions
        for t in ep.transitions:
            assert len(t.options) == len(t.options)
            assert t.value == t.value  # not NaN
            assert all(len(o) == OPTION_DIM for o in t.options)


def test_collect_episode_vs_a_league_opponent_records_only_the_learner():
    net = ActorCritic(STATE_DIM, OPTION_DIM, hidden=8)
    episodes = collect_episode(
        net, seed=3, anchor_fraction=0.0, opponent_net=net, opponent_prob=1.0, device="cpu"
    )
    assert len(episodes) == 1  # only the learner side is recorded, not the opponent
    assert episodes[0].side in ("US", "USSR") and episodes[0].transitions


def test_actor_net_cache_returns_the_same_object(tmp_path):
    from struggler.bots.rl.actors import get_net
    path = tmp_path / "n.pt"
    save_policy(path, ActorCritic(STATE_DIM, OPTION_DIM, hidden=8))
    assert get_net(str(path)) is get_net(str(path))


def test_compute_gae_puts_terminal_reward_on_the_last_step():
    t0 = Transition(state=[0.0] * STATE_DIM, options=[[0.0] * OPTION_DIM], action=0, logprob=0.0, value=0.0)
    t1 = Transition(state=[0.0] * STATE_DIM, options=[[0.0] * OPTION_DIM], action=0, logprob=0.0, value=0.0)
    flat = compute_gae([Episode("US", [t0, t1], reward=1.0)], gamma=1.0, lam=0.95)
    assert len(flat) == 2
    assert all(t.ret == t.advantage + t.value for t in flat)
    assert flat[-1].ret == 1.0


def test_ppo_update_changes_parameters_and_reports_stats(tmp_path):
    net = ActorCritic(STATE_DIM, OPTION_DIM, hidden=8)
    before = [p.detach().clone() for p in net.parameters()]
    t0 = Transition([0.0] * STATE_DIM, [[0.0] * OPTION_DIM, [1.0] * OPTION_DIM], 1, 0.0, 0.0)
    t1 = Transition([0.0] * STATE_DIM, [[0.0] * OPTION_DIM, [1.0] * OPTION_DIM], 0, 0.0, 0.0)
    optimizer = torch.optim.Adam(net.parameters(), lr=1e-2)
    stats = ppo_update(net, optimizer, [Episode("US", [t0, t1], 1.0)], device="cpu", epochs=2, minibatch=2)
    assert stats["transitions"] == 2.0
    assert any(not torch.equal(a, b) for a, b in zip(before, net.parameters()))


def test_rl_player_returns_a_legal_action(tmp_path):
    net = ActorCritic(STATE_DIM, OPTION_DIM, hidden=8)
    path = tmp_path / "net.pt"
    save_policy(path, net)
    engine = Engine.new_game(seed=2)
    decision = engine.pending_decision
    player = RLPlayer.from_path(path, seed=1)
    action = player.choose_action(engine.observe(decision.actor), ())
    assert action in decision.options
