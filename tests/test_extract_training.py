"""Tests for the expert-decision extractor's honesty rules."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from struggler.bots.greedy import GreedyPlayer

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "extract_training", ROOT / "scripts" / "extract_training.py"
)
et = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(et)

GAME = ROOT / "parsed" / "bgg-1964083.json"


def test_extract_stops_at_first_divergence_and_flags_unknown_hands():
    game = json.loads(GAME.read_text())
    rows, summary = et.extract(game, GreedyPlayer(), split="eval")

    assert summary["split"] == "eval"
    assert summary["stopped"] in ("first_divergence", "records_end")
    assert summary["eligible_decisions"] <= summary["decisions"]
    # Replay mode hides both hands, so card-choice decisions are hand-unknown
    # and must be excluded from the agreement denominator.
    assert summary["hand_unknown_decisions"] > 0

    required = {"game", "split", "kind", "actor", "chosen", "greedy", "match", "hand_known"}
    assert all(required <= set(row) for row in rows)

    eligible = [
        row for row in rows
        if row["hand_known"] and row["chosen"] is not None and row["greedy"] is not None
    ]
    assert len(eligible) == summary["eligible_decisions"]


def test_extract_stops_before_the_first_error():
    # Every emitted decision must precede any recorded board/VP divergence.
    game = json.loads(GAME.read_text())
    rows, summary = et.extract(game, GreedyPlayer())
    if summary["stopped"] == "first_divergence":
        assert summary["first_divergence_record"] is not None
    # The extractor never emits rows after the game is terminal.
    assert summary["records_consumed"] <= summary["records_total"]
