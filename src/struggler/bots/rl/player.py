"""`RLPlayer`: a `Player`-protocol wrapper around a trained `ActorCritic`.

Sampling (training/eval with exploration) or argmax (greedy eval). Hidden-info
safe: it only ever looks at the `Observation` it is handed.
"""

from __future__ import annotations

import random

import torch

from struggler.bots.rl.encode import option_vector, state_vector
from struggler.bots.rl.net import ActorCritic, load_policy
from struggler.engine import Action, Observation, Side


class RLPlayer:
    def __init__(
        self,
        net: ActorCritic,
        *,
        device: str | torch.device = "cpu",
        sample: bool = False,
        seed: int = 0,
    ) -> None:
        self._device = torch.device(device)
        self.net = net.to(self._device).eval()
        self.sample = sample
        self._rng = random.Random(seed)

    @classmethod
    def from_path(
        cls, path, *, device: str | torch.device = "cpu", sample: bool = False, seed: int = 0
    ) -> "RLPlayer":
        return cls(load_policy(path, device=device), device=device, sample=sample, seed=seed)

    @torch.no_grad()
    def choose_action(self, observation: Observation, history) -> Action:
        options = observation.pending_decision.options
        if len(options) == 1:
            return options[0]
        state = torch.tensor([state_vector(observation)], dtype=torch.float32, device=self._device)
        opts = torch.tensor(
            [[option_vector(a) for a in options]], dtype=torch.float32, device=self._device
        )
        logits, _ = self.net(state, opts)
        if self.sample:
            idx = int(torch.distributions.Categorical(logits=logits[0]).sample().item())
        else:
            idx = int(logits[0].argmax().item())
        return options[idx]
