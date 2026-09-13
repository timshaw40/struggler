"""Recorded-replay mode: both hands hidden, cards declared on play."""

from __future__ import annotations

from struggler.engine import Engine, Side
from struggler.engine.core import HIDDEN_CARD
from struggler.engine.types import Subregion


def test_replay_mode_hides_both_hands() -> None:
    engine = Engine.new_game(seed=1, replay_mode=True)
    for side in ("US", "USSR"):
        assert HIDDEN_CARD in engine.hands[side]
    assert engine.replay_mode and engine.physical_mode
    assert engine.physical_side is None


def test_replay_mode_declares_card_on_play() -> None:
    engine = Engine.new_game(seed=1, replay_mode=True)

    def place(subregion: Subregion, n: int) -> None:
        while n:
            dec = engine.pending_decision
            assert dec is not None and dec.context.get("setup")
            cid = next(
                c for c, info in engine.board.countries.items()
                if subregion in info.subregions
                and any(a.payload.get("country") == c for a in dec.options)
            )
            engine.step(next(a for a in dec.options if a.payload["country"] == cid))
            n -= 1

    place(Subregion.EASTERN_EUROPE, 6)
    place(Subregion.WESTERN_EUROPE, 7)
    dec = engine.pending_decision
    assert dec.kind.value == "headline_play"
    action = dec.options[0]
    cid = action.payload["card"]
    assert cid in engine.hidden_pool
    engine.step(action)
    assert cid not in engine.hidden_pool
    assert engine.pending_decision.actor is Side.US


def test_extract_training_runs_on_parsed_corpus() -> None:
    """Smoke: the log-pipeline extractor doesn't crash on a real parsed game."""
    import importlib.util
    import json
    from pathlib import Path

    import pytest

    root = Path(__file__).resolve().parent.parent
    parsed = sorted((root / "parsed").glob("bgg-*.json"))
    if not parsed:
        pytest.skip("no parsed/ corpus")
    spec = importlib.util.spec_from_file_location(
        "extract_training", root / "scripts" / "extract_training.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from struggler.bots.greedy import GreedyPlayer

    game = json.loads(parsed[0].read_text())
    rows, summary = mod.extract(game, GreedyPlayer())
    assert "greedy_agreement" in summary
    assert summary["human_decisions"] == len(rows)
