"""The deep arena can compare two checkpoints, not only two search settings."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from struggler.arena import GameResult


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "deep_gate.py"
    spec = importlib.util.spec_from_file_location("deep_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_deep_opponent_can_use_a_separate_checkpoint(monkeypatch, capsys):
    gate = _module()
    seen = []

    def fake_matchup(candidate, opponent, seeds, *, workers, events=True):
        seen.append((candidate, opponent, seeds, workers, events))
        return [GameResult(seed, candidate.name, opponent.name, "US") for seed in seeds]

    monkeypatch.setattr(gate, "run_matchup", fake_matchup)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deep_gate.py",
            "--model", "candidate.onnx",
            "--opponent-model", "incumbent.onnx",
            "--opponent", "deep",
            "--seeds", "5000:5002",
            "--workers", "3",
        ],
    )

    gate.main()

    candidate, opponent, seeds, workers, events = seen[0]
    assert candidate.net_path == "candidate.onnx"
    assert opponent.net_path == "incumbent.onnx"
    assert seeds == [5000, 5001]
    assert workers == 3
    assert events is True
    assert "deep vs deep" in capsys.readouterr().out
