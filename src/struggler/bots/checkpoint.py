"""JSON (de)serialization for `GreedyWeights`.

Tuned weights need to be saved, diffed, and reconstructed inside worker
processes, so they cross a process boundary as a plain dict. Two groups of
fields are still written but never perturbed by the tuner:

- `GUARDRAILS` — the "never do this" sentinels, whose magnitude is the whole
  point (`defcon_self_kill_penalty`, `defcon_suicide_penalty`);
- `PLAYBOOK` — the judgements quoted from the strategy source (see
  `docs/STRATEGY.md`), whose comments depend on their *relative* sizes: the
  turn-1 plan is deliberately "below the headline bonus (40) and above the
  ordinary board-value swing a single Influence point buys". A per-dimension
  log-Gaussian search has no notion of those orderings, so tuning the set
  freely can dissolve a sourced rule into an arbitrary number without anyone
  noticing. Change one deliberately, with its source quoted and the arena gate
  run — not as a search dimension.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping

from struggler.bots.greedy import GreedyWeights

GUARDRAILS = ("defcon_self_kill_penalty", "defcon_suicide_penalty")

PLAYBOOK = (
    # Turn 1, from "General Strategy: Turn 1"
    "t1_headline_bonus",
    "t1_iran_coup_bonus",
    "t1_plan_bonus",
    "t1_us_retaliatory_coup_penalty",
    # Space Race / reshuffles / realignments, from their articles
    "space_race_card_bonus",
    "reshuffle_timing_bonus",
    "realignment_access_bonus",
    "realignment_at_defcon_2_bonus",
    "realignment_above_defcon_2_penalty",
    # How I Learned to Stop Worrying's DEFCON-level preference
    "defcon_setting_weight",
    # The turn plan's region focus (sized against influence_base and control)
    "plan_region_focus_bonus",
)

# What the tuner holds fixed. `tune_greedy.py` reads this, not `GUARDRAILS`.
FROZEN = GUARDRAILS + PLAYBOOK


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
