"""Every country's marker anchor must land on the box the map actually prints.

The influence markers are an overlay sized from the box
`scripts/install_vassal_ui_assets.py` detects on the board, so an anchor a few
pixels off shifts every pip in that country. The stability badge used to
truncate the right edge by 12-30 px on 25 countries (Cuba 30, UK 28, Colombia
20, ...), which pulled both influence columns left of their printed halves.

These checks read the art directly: the box's own side borders, a plausible
strip and body, and the box staying inside the board. They are skipped when
the VASSAL board art is absent, since it is the input under test.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "install_vassal_ui_assets", ROOT / "scripts" / "install_vassal_ui_assets.py"
)
inst = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inst)

BOARD = ROOT / "third_party" / "gmt-vassal" / "board" / "TS Map-11.jpg"

pytestmark = pytest.mark.skipif(
    not BOARD.exists(), reason="VASSAL board art not installed"
)


@pytest.fixture(scope="module")
def board():
    Image = pytest.importorskip("PIL.Image", reason="the [ui] extra provides Pillow")
    return Image.open(BOARD).convert("RGB")


def _dark(px, x, y):
    """The detector's own idea of a border pixel: near-black and unsaturated."""
    r, g, b = px[x, y]
    return r < 120 and g < 120 and b < 120 and max(r, g, b) - min(r, g, b) < 90


def _column_ratio(px, x, y_from, y_to):
    return sum(1 for y in range(y_from, y_to) if _dark(px, x, y)) / max(1, y_to - y_from)


def _detected(board):
    px = board.load()
    for cid, (cx, cy) in inst.VASSAL_COUNTRIES.items():
        if cid in inst.HAND_TWEAK:
            continue
        found = inst.find_box(board, cx, cy)
        assert found is not None, f"{cid}: the detector found no box at all"
        (left, top, right, strip_bottom), _center, height, _ok = found
        yield cid, left, top, right, strip_bottom, height, px


def test_every_country_on_the_map_has_a_detection_anchor():
    from struggler.engine.board import Board

    on_map = set(Board().countries)
    missing = sorted(on_map - set(inst.VASSAL_COUNTRIES))
    assert not missing, f"countries with no detection anchor: {missing}"


def test_detected_boxes_match_the_printed_borders(board):
    """Both side edges must be printed borders, not the badge or a neighbour."""
    px = board.load()
    width, height = board.size
    offenders = []
    for cid, left, top, right, strip_bottom, box_height, _ in _detected(board):
        body_from = strip_bottom + 6
        body_to = top + box_height - 6
        assert body_to - body_from >= 20, f"{cid}: body too short to check"
        left_ratio = max(_column_ratio(px, left + d, body_from, body_to) for d in (0, 1, 2, 3, 4))
        right_ratio = max(
            _column_ratio(px, right - 1 - d, body_from, body_to) for d in (0, 1, 2, 3, 4)
        )
        # 0.80, not 0.95: JPEG softens a border column where the body meets
        # terrain (Romania's tall body reads 0.85), while a truncated edge —
        # the badge's left border, or a neighbour — reads far lower.
        if left_ratio < 0.80 or right_ratio < 0.80:
            offenders.append((cid, round(left_ratio, 2), round(right_ratio, 2)))
        assert 0 <= left < right <= width, f"{cid}: box escapes the board horizontally"
        assert 0 <= top < top + box_height <= height, f"{cid}: box escapes the board"
    assert not offenders, f"edges not on a printed border (left, right): {offenders}"


def test_strip_and_body_are_plausible(board):
    """A wrong row pairing shows up as an absurd strip or a tiny body."""
    bad = []
    for cid, _left, top, _right, strip_bottom, box_height, _ in _detected(board):
        strip = strip_bottom - top
        body = box_height - strip
        if not 26 <= strip <= 40 or body < 60:
            bad.append((cid, strip, body))
    assert not bad, f"implausible strip/body (strip, body): {bad}"


def test_the_stability_badge_no_longer_truncates_the_box(board):
    """Regression: these read 168-190 px wide when the run stopped at the
    badge's left edge, against the ~199 px every standard box is printed."""
    widths = {cid: right - left for cid, left, _top, right, _sb, _h, _px in _detected(board)}
    truncated = {cid: widths[cid] for cid in (
        "Cuba", "UK", "Colombia", "Morocco", "Nigeria", "Guatemala", "Peru",
        "Tunisia", "Ecuador", "Burma", "Ivory_Coast", "Cameroon", "Zimbabwe",
    ) if widths.get(cid, 0) < 195}
    assert not truncated, f"boxes narrower than the printed standard: {truncated}"
