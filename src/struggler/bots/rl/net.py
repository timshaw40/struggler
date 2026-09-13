"""Actor-critic net over (state, variable-length legal options).

The policy scores each legal option by a dot product between a state embedding
and an option embedding (a pointer-style head), so it handles the engine's
variable branching without a fixed action space. The value head reads the
state embedding. Both seats share one net.
"""

from __future__ import annotations

import math
from pathlib import Path

import torch
import torch.nn as nn


def _mlp(indim: int, hidden: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(indim, hidden), nn.ReLU(),
        nn.Linear(hidden, hidden), nn.ReLU(),
    )


class ActorCritic(nn.Module):
    def __init__(self, state_dim: int, option_dim: int, hidden: int = 256) -> None:
        super().__init__()
        self.state_dim = state_dim
        self.option_dim = option_dim
        self.hidden = hidden
        self.state_enc = _mlp(state_dim, hidden)
        self.option_enc = _mlp(option_dim, hidden)
        self.value_head = nn.Linear(hidden, 1)

    def forward(self, state: torch.Tensor, options: torch.Tensor, mask: torch.Tensor | None = None):
        """state [B,S], options [B,A,O], mask [B,A] (True = real). Returns
        logits [B,A] and value [B] (acting side's win probability logit)."""
        s = self.state_enc(state)
        o = self.option_enc(options)
        logits = (o * s.unsqueeze(1)).sum(-1) / math.sqrt(self.hidden)
        if mask is not None:
            # A large finite negative, not -inf: -inf makes Categorical's
            # entropy (0 * -inf) produce NaN in the PPO loss.
            logits = logits.masked_fill(~mask, -1e9)
        value = self.value_head(s).squeeze(-1)
        return logits, value


def save_policy(path: str | Path, net: ActorCritic) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "state_dim": net.state_dim,
            "option_dim": net.option_dim,
            "hidden": net.hidden,
            "state_dict": net.state_dict(),
        },
        p,
    )


def load_policy(path: str | Path, device: str | torch.device = "cpu") -> ActorCritic:
    ckpt = torch.load(path, map_location=device)
    net = ActorCritic(ckpt["state_dim"], ckpt["option_dim"], ckpt["hidden"])
    net.load_state_dict(ckpt["state_dict"])
    return net.to(device).eval()
