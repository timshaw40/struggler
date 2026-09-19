"""Tests for the CEM tuner's pure helpers (no long runs)."""

from __future__ import annotations

import importlib.util
import random
from pathlib import Path

from struggler.bots.checkpoint import weights_to_dict
from struggler.bots.greedy import GreedyWeights

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("tune_greedy", ROOT / "scripts" / "tune_greedy.py")
tg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tg)


def test_vec_to_weights_clamps_and_keeps_guardrails():
    base = GreedyWeights()
    weights = tg.vec_to_weights([1_000_000.0] * tg.DIM, base)
    assert weights.defcon_self_kill_penalty == base.defcon_self_kill_penalty
    assert all(getattr(weights, f) <= tg.CLAMP[1] for f in tg.TUNABLE)


def test_log_sample_maps_to_positive_weights():
    logvec = tg.sample_log([0.0] * tg.DIM, [0.3] * tg.DIM, random.Random(0))
    weights = tg.logvec_to_weights(logvec, GreedyWeights())
    assert len(logvec) == tg.DIM
    assert all(v > 0 for v in weights_to_dict(weights).values())


def test_eval_job_scores_a_side_swapped_pair():
    weights = weights_to_dict(GreedyWeights())
    ci, points, games = tg._eval_job((0, "cand0", weights, "first", "first", None, 1, True))
    assert ci == 0 and games == 2 and 0.0 <= points <= 2.0
