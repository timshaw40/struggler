"""JSON (de)serialization for `GreedyWeights`.

Tuned weights need to be saved, diffed, and reconstructed inside worker
processes, so they cross a process boundary as a plain dict. Guardrail
fields (currently just `defcon_self_kill_penalty`) are still written but the
tuner never perturbs them.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping

from struggler.bots.greedy import GreedyWeights

GUARDRAILS = ("defcon_self_kill_penalty",)


def weights_to_dict(weights: GreedyWeights) -> dict[str, float]:
    return {f.name: float(getattr(weights, f.name)) for f in fields(weights)}


def weights_from_dict(data: Mapping[str, Any]) -> GreedyWeights:
    """Rebuild from a (possibly partial) dict; unknown keys are ignored."""
    known = {f.name for f in fields(GreedyWeights)}
    return GreedyWeights(**{k: float(v) for k, v in data.items() if k in known})


def save_weights(weights: GreedyWeights, path: str | Path, *, extra: Mapping[str, Any] | None = None) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"weights": weights_to_dict(weights)}
    if extra:
        payload.update(extra)
    p.write_text(json.dumps(payload, indent=1))


def load_weights(path: str | Path) -> GreedyWeights:
    data = json.loads(Path(path).read_text())
    return weights_from_dict(data.get("weights", data))
