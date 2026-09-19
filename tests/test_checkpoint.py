"""Tests for GreedyWeights JSON checkpoints."""

from __future__ import annotations

from struggler.bots.checkpoint import (
    load_weights,
    save_weights,
    weights_from_dict,
    weights_to_dict,
)
from struggler.bots.greedy import GreedyWeights


def test_weights_round_trip_through_disk(tmp_path):
    weights = GreedyWeights(region_tier=7.5, coup_base=2.25)
    path = tmp_path / "w.json"
    save_weights(weights, path, extra={"note": "test"})
    assert load_weights(path) == weights


def test_partial_dict_fills_defaults_and_ignores_unknown_keys():
    weights = weights_from_dict({"region_tier": 9.0, "not_a_real_field": 3})
    assert weights.region_tier == 9.0
    assert weights.coup_base == GreedyWeights().coup_base


def test_to_dict_covers_every_field():
    assert set(weights_to_dict(GreedyWeights())) == set(GreedyWeights.__dataclass_fields__)
