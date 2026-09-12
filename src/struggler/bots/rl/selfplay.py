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
from struggler.bots.rl.net import load_policy
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


def collect_episode(
    net,
    seed: int,
    *,
    events: bool = True,
    anchor_fraction: float = 0.0,
    rng: random.Random | None = None,
    device: str = "cpu",
) -> list[Episode]:
    rng = rng or random.Random(seed)
    engine = Engine.new_game(seed=seed, events=events)
    anchor = GreedyPlayer()
    # Occasionally a seat is played by the heuristic anchor instead of the
    # policy, which keeps the opponent pool non-stationary and prevents the
    # policy from collapsing onto its own quirks. Never anchor both seats.
    use_anchor = {Side.US: rng.random() < anchor_fraction, Side.USSR: rng.random() < anchor_fraction}
    if use_anchor[Side.US] and use_anchor[Side.USSR]:
        use_anchor[rng.choice([Side.US, Side.USSR])] = False

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
        elif len(options) == 1:
            action = options[0]
        else:
            state = state_vector(obs)
            opts = [option_vector(a) for a in options]
            with torch.no_grad():
                logits, value = net(
                    torch.tensor([state], dtype=torch.float32, device=device),
                    torch.tensor([opts], dtype=torch.float32, device=device),
                )
            dist = torch.distributions.Categorical(logits=logits[0])
            idx = int(dist.sample().item())
            logprob = float(dist.log_prob(torch.tensor(idx, device=device)).item())
            recorded.setdefault(decision.actor.value, []).append(
                Transition(state, opts, idx, logprob, float(value.item()))
            )
            action = options[idx]
        engine.step(action)

    winner = engine.winner.value if engine.winner is not None else None
    episodes = []
    for side, transitions in recorded.items():
        reward = 0.0 if winner is None else (1.0 if side == winner else -1.0)
        episodes.append(Episode(side, transitions, reward))
    return episodes


def _collect_job(args: tuple) -> list[Episode]:
    net_path, seed, anchor_fraction, events = args
    torch.set_num_threads(1)  # avoid oversubscription across worker processes
    net = load_policy(net_path, device="cpu")
    return collect_episode(
        net, seed, events=events, anchor_fraction=anchor_fraction,
        rng=random.Random(seed), device="cpu",
    )
