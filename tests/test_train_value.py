"""Tests for the value-training split: whole games per partition."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("train_value", ROOT / "scripts" / "train_value.py")
tg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tg)


def _game(marker: float, winner: str):
    # (feats, actors, winner) — one position, marker identifies the game.
    return ([[marker, marker]], ["US"], winner)


def test_split_keeps_games_whole_and_mirrors_within_partitions():
    games = [_game(1.0, "US"), _game(2.0, "USSR"), _game(3.0, "US"), _game(4.0, "USSR")]
    train, val = tg.split_games(games, val_fraction=0.5, seed=0)

    assert sorted(id(g) for g in train + val) == sorted(id(g) for g in games)  # no game lost
    assert len(train) == 2 and len(val) == 2

    Xt, Yt = tg.build_dataset(train)
    Xv, Yv = tg.build_dataset(val)
    train_markers = {x[0] for x in Xt}
    val_markers = {x[0] for x in Xv}
    assert train_markers.isdisjoint(val_markers)  # no game crosses the split
    assert len(Xt) == 2 * 2 and len(Xv) == 2 * 2  # mirrored, no cross-contamination
