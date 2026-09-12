"""Self-play rollouts for PPO.

One game contributes one `Episode` per side that used the policy (anchored
seats are not learned from). Splitting by side matters: the value head is
from the acting side's perspective, so US and USSR transitions must not be
interleaved when computing advantages.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import torch

from struggler.bots.greedy import GreedyPlayer
from struggler.bots.rl.encode import option_vector, state_vector
from struggler.engine import Engine, Side


@dataclass
class Transition:
    state: list[float]
    options: list[list[float]]
    action: int
    logprob: float
    value: float
    advantage: float = 0.0
    ret: float = 0.0


@dataclass
class Episode:
    side: str
    transitions: list[Transition]
    reward: float


def _forward(net, obs, options, device):
    state = state_vector(obs)
    opts = [option_vector(a) for a in options]
    with torch.no_grad():
        logits, value = net(
            torch.tensor([state], dtype=torch.float32, device=device),
            torch.tensor([opts], dtype=torch.float32, device=device),
        )
    return state, opts, logits[0], float(value.item())


def collect_episode(
    net,
    seed: int,
    *,
    events: bool = True,
    anchor_fraction: float = 0.0,
    opponent_net=None,
    opponent_prob: float = 0.0,
    rng: random.Random | None = None,
    device: str = "cpu",
) -> list[Episode]:
    """One game. The current `net` is the learner; the other seat is, per game,
    a past-checkpoint `opponent_net` (league), the greedy anchor, or the same
    net (pure self-play). Only the learner's transitions are recorded."""
    rng = rng or random.Random(seed)
    engine = Engine.new_game(seed=seed, events=events)
    anchor = GreedyPlayer()
    use_anchor = {Side.US: rng.random() < anchor_fraction, Side.USSR: rng.random() < anchor_fraction}
    if use_anchor[Side.US] and use_anchor[Side.USSR]:
        use_anchor[rng.choice([Side.US, Side.USSR])] = False
    opp_side: Side | None = None
    if opponent_net is not None and not any(use_anchor.values()) and rng.random() < opponent_prob:
        opp_side = rng.choice([Side.US, Side.USSR])

    recorded: dict[str, list[Transition]] = {}
    while not engine.is_terminal:
        decision = engine.pending_decision
        if decision is None:
            break
        if decision.actor is Side.CHANCE:
            engine.step(decision.options[0])
            continue
        obs = engine.observe(decision.actor)
        options = decision.options
        if use_anchor[decision.actor]:
            action = anchor.choose_action(obs, ())
        elif decision.actor is opp_side:
            if len(options) == 1:
                action = options[0]
            else:
                _, _, logits, _ = _forward(opponent_net, obs, options, device)
                action = options[int(logits.argmax().item())]
        elif len(options) == 1:
            action = options[0]
        else:
            state, opts, logits, value = _forward(net, obs, options, device)
            dist = torch.distributions.Categorical(logits=logits)
            idx = int(dist.sample().item())
            logprob = float(dist.log_prob(torch.tensor(idx, device=device)).item())
            recorded.setdefault(decision.actor.value, []).append(
                Transition(state, opts, idx, logprob, value)
            )
            action = options[idx]
        engine.step(action)

    winner = engine.winner.value if engine.winner is not None else None
    episodes = []
    for side, transitions in recorded.items():
        reward = 0.0 if winner is None else (1.0 if side == winner else -1.0)
        episodes.append(Episode(side, transitions, reward))
    return episodes
