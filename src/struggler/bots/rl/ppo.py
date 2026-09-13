"""PPO for the variable-branching TS decision: GAE per side, padded option
batches, clipped surrogate, value + entropy losses. Sparse ±1 terminal reward
(no shaping), so the only bias is the value function itself.
"""

from __future__ import annotations

import random
from typing import Sequence

import numpy as np
import torch
import torch.nn as nn

from struggler.bots.rl.encode import OPTION_DIM
from struggler.bots.rl.selfplay import Episode, Transition


def compute_gae(
    episodes: Sequence[Episode],
    gamma: float = 1.0,
    lam: float = 0.95,
    shaping: bool = False,
) -> list[Transition]:
    """GAE over each side's episode. With `shaping=True`, adds potential-based
    shaping F = gamma*phi(s') - phi(s) (phi is the stored heuristic potential),
    the one form that provably leaves the optimal policy unchanged. The
    terminal outcome (±1) is added on the last step."""
    flat: list[Transition] = []
    for ep in episodes:
        n = len(ep.transitions)
        if n == 0:
            continue
        rewards = [0.0] * n
        if shaping:
            phis = [t.phi for t in ep.transitions]
            for t in range(n):
                next_phi = phis[t + 1] if t + 1 < n else 0.0
                rewards[t] = gamma * next_phi - phis[t]
        rewards[-1] += ep.reward
        values = [t.value for t in ep.transitions]
        adv = [0.0] * n
        running = 0.0
        for t in reversed(range(n)):
            next_v = values[t + 1] if t + 1 < n else 0.0
            delta = rewards[t] + gamma * next_v - values[t]
            running = delta + gamma * lam * running
            adv[t] = running
        for t in range(n):
            ep.transitions[t].advantage = adv[t]
            ep.transitions[t].ret = adv[t] + values[t]
        flat.extend(ep.transitions)
    return flat


def _tensors(batch: Sequence[Transition], device):
    states = torch.tensor([t.state for t in batch], dtype=torch.float32, device=device)
    amax = max(len(t.options) for t in batch)
    opts_np = np.zeros((len(batch), amax, OPTION_DIM), dtype=np.float32)
    mask = torch.zeros(len(batch), amax, dtype=torch.bool, device=device)
    for i, t in enumerate(batch):
        opts_np[i, : len(t.options)] = t.options
        mask[i, : len(t.options)] = True
    return (
        states,
        torch.from_numpy(opts_np).to(device),
        mask,
        torch.tensor([t.action for t in batch], dtype=torch.long, device=device),
        torch.tensor([t.logprob for t in batch], dtype=torch.float32, device=device),
        torch.tensor([t.ret for t in batch], dtype=torch.float32, device=device),
        torch.tensor([t.advantage for t in batch], dtype=torch.float32, device=device),
    )


def ppo_update(
    net: nn.Module,
    optimizer: torch.optim.Optimizer,
    episodes: Sequence[Episode],
    *,
    device,
    epochs: int = 4,
    minibatch: int = 64,
    clip: float = 0.2,
    vf_coef: float = 0.5,
    ent_coef: float = 0.01,
    seed: int = 0,
    shaping: bool = False,
) -> dict[str, float]:
    flat = compute_gae(episodes, shaping=shaping)
    if not flat:
        return {"transitions": 0.0, "policy": 0.0, "value": 0.0, "entropy": 0.0,
                "kl": 0.0, "clip": 0.0, "explained_var": 0.0}
    advs = torch.tensor([t.advantage for t in flat], dtype=torch.float32)
    adv_norm = (advs - advs.mean()) / (advs.std() + 1e-8)
    for t, a in zip(flat, adv_norm.tolist()):
        t.advantage = a

    # How much of the return variance the value head explains (pre-update).
    # Near 0 means the value is noise; near 1 means it predicts outcomes.
    rets_all = np.array([t.ret for t in flat])
    vals_all = np.array([t.value for t in flat])
    explained = 1.0 - np.var(rets_all - vals_all) / (np.var(rets_all) + 1e-8)

    rng = random.Random(seed)
    stats = {"policy": 0.0, "value": 0.0, "entropy": 0.0, "kl": 0.0, "clip": 0.0, "n": 0}
    for _ in range(epochs):
        order = list(range(len(flat)))
        rng.shuffle(order)
        for start in range(0, len(order), minibatch):
            batch = [flat[i] for i in order[start : start + minibatch]]
            if len(batch) < 2:
                continue
            states, opts, mask, actions, old_lp, rets, adv = _tensors(batch, device)
            logits, values = net(states, opts, mask)
            dist = torch.distributions.Categorical(logits=logits)
            new_lp = dist.log_prob(actions)
            ratio = (new_lp - old_lp).exp()
            surr = torch.min(ratio * adv, ratio.clamp(1 - clip, 1 + clip) * adv)
            policy_loss = -surr.mean()
            value_loss = (values - rets).pow(2).mean()
            entropy = dist.entropy().mean()
            loss = policy_loss + vf_coef * value_loss - ent_coef * entropy

            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(net.parameters(), 0.5)
            optimizer.step()

            with torch.no_grad():
                stats["kl"] += float((old_lp - new_lp).mean().item())
                stats["clip"] += float(((ratio - 1).abs() > clip).float().mean().item())
            stats["policy"] += float(policy_loss.item())
            stats["value"] += float(value_loss.item())
            stats["entropy"] += float(entropy.item())
            stats["n"] += 1
    n = max(1, stats.pop("n"))
    stats = {k: v / n for k, v in stats.items()}
    stats["transitions"] = float(len(flat))
    stats["explained_var"] = float(explained)
    return stats
