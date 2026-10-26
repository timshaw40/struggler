"""DeepStruggle's rollout root: the network's top moves, played out in sampled worlds.

DeepStruggle's measured search player (`ai/search/rollout_root.py`,
`rollout:<ckpt>:4:16:4:z2`), driven from here with struggler's ONNX policy:

1. **Candidates.** The network's `k` most probable legal moves at the root.
2. **Worlds.** `worlds` draws of the mover's unseen cards and of the dice.
   Every candidate plays in every world, so candidates are compared on the
   same cards and dice (paired differences).
3. **Playouts.** After the candidate, the network plays both sides greedily
   through `horizon` action-round boundaries; the critic (`v_win`) reads where
   each playout stops, from the mover's side. A finished game counts its result.
4. **Choice** (`rule`). `z2`: the best candidate by mean value, but only if its
   paired lead over the network's own move is at least 2 standard errors;
   otherwise the network's move. The search overrides the network only on
   evidence.

All of the game logic (dealing worlds, stepping playouts, the DEFCON suicide
guard, the statistics) is DeepStruggle's own C++ (`bindings/rollout_batch.cpp`),
compiled into the same engine build the bot already uses. This file only
carries the network's outputs to it, the same loop DeepStruggle's browser page
runs (`web/ui/src/analysis/rollout.ts`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from struggler.bots.deep import backend as B

#: A playout still running after this many plies is an error, not a leaf.
MAX_PLIES = 400


@dataclass(frozen=True)
class RolloutConfig:
    """`k` candidates, `worlds` worlds, `horizon` action-round boundaries, the
    choice `rule`. The defaults are DeepStruggle's measured player."""

    k: int = 4
    worlds: int = 16
    horizon: int = 4
    rule: str = "z2"

    @classmethod
    def parse(cls, spec: str) -> "RolloutConfig | None":
        """`"k:worlds:horizon:rule"` (any prefix; `"on"` for the defaults), or
        None for `"off"`/`""`."""
        spec = spec.strip().lower()
        if spec in ("", "off", "none", "0"):
            return None
        if spec in ("on", "default"):
            return cls()
        parts = spec.split(":")
        if len(parts) > 4:
            raise ValueError(f"rollout spec is k:worlds:horizon:rule, got {spec!r}")
        d = cls()
        k = int(parts[0]) if len(parts) > 0 and parts[0] else d.k
        worlds = int(parts[1]) if len(parts) > 1 and parts[1] else d.worlds
        horizon = int(parts[2]) if len(parts) > 2 and parts[2] else d.horizon
        rule = parts[3] if len(parts) > 3 and parts[3] else d.rule
        if k < 2 or worlds < 1 or horizon < 0:
            raise ValueError(f"rollout spec needs k >= 2, worlds >= 1, horizon >= 0: {spec!r}")
        if not re.fullmatch(r"argmax|z\d+(\.\d+)?|kl\d+(\.\d+)?", rule):
            raise ValueError(f"rollout rule is argmax, z<x> or kl<t>: {rule!r}")
        return cls(k, worlds, horizon, rule)

    def spec(self) -> str:
        return f"{self.k}:{self.worlds}:{self.horizon}:{self.rule}"


class BatchPolicy(Protocol):
    obs_features: int

    def forward(self, obs: Any, masks: Any) -> tuple[Any, Any]: ...


@dataclass
class SearchResult:
    """The search's pick (a flat slot), the candidates (the network's own move
    first), and each candidate's mean value and paired lead over it."""

    pick: int
    candidates: list[int]
    worlds: int
    q: list[float | None] = field(default_factory=list)
    lead: list[float | None] = field(default_factory=list)
    lead_se: list[float | None] = field(default_factory=list)
    rows: int = 0

    @property
    def overrode(self) -> bool:
        return self.pick != self.candidates[0]


def search(engine: B.RolloutEngine, policy: BatchPolicy, root_logits: Sequence[float], seed: int,
           config: RolloutConfig) -> SearchResult | None:
    """Run the rollout root over the position `engine` holds (the mover's
    decision). None when there is nothing to search: fewer than two candidates,
    or no world where every candidate is legal. Raises ValueError on an engine
    refusal; the engine's position is left as it was."""
    import numpy as np

    root = np.asarray(root_logits, dtype=np.float32)
    candidates = engine.rollout_candidates(root, config.k)
    if len(candidates) < 2 or engine.is_terminal():
        return None
    if not engine.rollout_start(config.horizon, policy.obs_features, seed, candidates, config.worlds):
        return None  # no world kept every candidate legal
    network_rows = 0
    for _ in range(MAX_PLIES):
        if engine.rollout_all_done():
            break
        obs, masks = engine.rollout_fill_live()
        logits, _ = policy.forward(obs, masks)
        network_rows += len(obs)
        engine.rollout_step(logits)
    else:
        raise ValueError("rollout_unfinished")
    if engine.rollout_refused():
        raise ValueError("rollout_refused")
    obs, masks = engine.rollout_fill_leaves()
    if len(obs):
        _, values = policy.forward(obs, masks)
        network_rows += len(obs)
        engine.rollout_set_leaf_values(np.asarray(values, dtype=np.float32).reshape(-1))
    d = engine.rollout_decide(candidates, root, config.rule)
    if not d["worlds"]:
        return None
    return SearchResult(int(d["pick"]), candidates, int(d["worlds"]), list(d["q"]), list(d["lead"]),
                        list(d["lead_se"]), network_rows)
