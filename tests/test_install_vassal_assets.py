"""Smoke tests for scripts/install_vassal_ui_assets.py (VASSAL art install)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "install_vassal_ui_assets", ROOT / "scripts" / "install_vassal_ui_assets.py"
)
inst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inst)


def test_card_mapping_covers_all_110_cards() -> None:
    assert sorted(inst.load_number_to_meta()) == list(range(1, 111))


def test_anchor_countries_match_engine_board() -> None:
    from struggler.engine.board import Board

    assert set(inst.VASSAL_COUNTRIES) == set(Board().countries)


def test_full_install_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("PIL")
    monkeypatch.setattr(inst, "OUT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["install_vassal_ui_assets.py"])
    inst.main()

    board = tmp_path / "board.png"
    assert board.is_file() and board.stat().st_size > 0
    manifest = json.loads((tmp_path / "cards.json").read_text())
    assert len(manifest) == 110
    one = next(iter(manifest.values()))
    face = (tmp_path / "cards" / one).read_text()
    assert 'id="banner"' in face  # ops value / side stripe injected
    countries = json.loads((tmp_path / "countries.json").read_text())
    assert len(countries) == 85  # every map country gets an anchor
    assert all(0 <= v["x"] <= 1 and 0 <= v["y"] <= 1 for v in countries.values())
