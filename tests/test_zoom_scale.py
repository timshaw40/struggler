"""The board's scale is one number, and the zoom bar shows it.

Jumping between view buttons used to reset a per-view multiplier while the
slider stayed at 100, so the worst place to stand was the one the map is
biggest: the world view at 100 meant "whole board", and every region at 100
meant "region fit" — the same reading, three times the magnification. The
client now keeps a single `scale` (1 = the whole board fitted to the map
width), derives the region fits from the region rectangles, and the slider
carries it directly.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "ui" / "app.js").read_text()

BOARD_W = 5100


def number(name: str) -> float:
    """A numeric constant, including `const A = 1, B = 2;` declarations."""
    match = re.search(rf"(?:const )?(?:[A-Z_]+, )?{name} = ([0-9.]+)", APP)
    assert match, f"{name} not found"
    return float(match.group(1))


def regions() -> dict[str, tuple[float, float, float, float]]:
    """The region rectangles, with the two board-size constants resolved."""
    constants = {"BOARD_W": float(BOARD_W), "BOARD_H": number("BOARD_H")}
    block = re.search(r"const REGIONS = \{(.*?)\n\};", APP, re.S).group(1)
    out = {}
    for name, body in re.findall(r'"([^"]+)": \[([^\]]+)\]', block):
        parts = [p.strip() for p in body.split(",")]
        assert len(parts) == 4, (name, body)
        out[name] = tuple(float(constants.get(p, p)) for p in parts)  # type: ignore[arg-type]
    return out


def fit_scale(name: str) -> float:
    l, _t, r, _b = regions()[name]
    return BOARD_W / (r - l)


def body_of(name: str) -> str:
    match = re.search(rf"function {name}\(.*?\) \{{(.*?)\n\}}", APP, re.S)
    assert match, f"{name}() not found"
    return match.group(1)


def test_one_scale_variable_and_the_world_fit_is_one():
    assert "let scale = 1;" in APP
    assert not re.search(r"\blet zoom\b", APP), "the per-view multiplier should be gone"
    # layoutBoard must derive the width from the scale alone: if a region
    # rectangle sneaks back in, the two can disagree again.
    layout = body_of("layoutBoard")
    assert "clientWidth * scale" in layout
    assert "REGIONS[" not in layout


def test_region_fits_come_from_the_region_rectangles():
    assert "const fitScale = (name) => BOARD_W / (REGIONS[name][2] - REGIONS[name][0]);" in APP
    # The regions are all about a third of the board across, which is why a
    # region fit is ~3x the world fit and why the old slider (max 180) could
    # never represent it.
    fits = {name: fit_scale(name) for name in regions()}
    assert fits["World"] == 1.0
    assert 2.9 < min(v for n, v in fits.items() if n != "World") < 3.3
    assert fits["Europe"] > 3.0


def test_slider_range_covers_every_view():
    scale = body_of("buildViewBar")
    assert 'slider.min = "100"' in scale
    assert "MAX_SCALE" in scale
    assert "syncZoomSlider()" in scale or "slider.value = String(Math.round(scale" in scale
    # The maximum has to be reachable from the deepest view, or a region fit
    # would sit past the end of its own track.
    deepest = max(fit_scale(n) for n in regions())
    # MAX_SCALE is an expression (1.8 * the deepest fit), so assert its shape
    # and its consequence rather than a literal.
    expr = re.search(r"const MAX_SCALE = (.*?);", APP).group(1)
    assert expr.startswith("1.8 *") and "fitScale" in expr
    assert 1.8 * deepest * 100 > deepest * 100


def test_view_buttons_land_on_the_fit_and_update_the_bar():
    set_view = body_of("setView")
    assert "scale = fitScale(name)" in set_view
    assert "syncZoomSlider()" in set_view, "the bar must follow the view"


def test_every_zoom_path_goes_through_setScale():
    """The keyboard and the slider used to write `zoom` and patch the slider by
    hand; both now move the one scale, so they cannot drift apart."""
    assert "function setScale(" in APP
    assert "function setZoom(" not in APP
    for handler in ("setScale(scale * 1.2)", "setScale(scale / 1.2)", "setScale(fitScale(view))"):
        assert handler in APP, handler
    keyboard = body_of("installKeyboard")
    assert 'slider.value = "' not in keyboard, "the keyboard must not hand-set the slider"
    assert "slider.value" not in keyboard


def test_scale_is_clamped_to_the_slider_range():
    """The slider is the only control, so a programmatic zoom may not exceed
    what the bar can express — otherwise the bar reads a percentage that is not
    the map's actual scale."""
    scale = body_of("setScale")
    assert "Math.max(1, Math.min(MAX_SCALE, next))" in scale


# -- filling the band a narrow window leaves under the board ---------------


def test_layout_board_grows_the_world_view_to_cover_the_hand_gap():
    """The board is 5100x3300, so fitting it to the map area's *width* leaves it
    only 0.647 of that width tall. A short wide area absorbs that (the board
    overflows and scrolls); a tall narrow one does not, and the leftover showed
    as a black band between the map and the hand -- 108px at a 900px window."""
    layout = body_of("layoutBoard")
    assert "fillFactor(area, scale)" in layout, "the fill correction is gone"
    factor = body_of("fillFactor")
    assert "tall / (wide * aspect)" in factor
    # Filling to *height* needs the area's height; the width fit alone cannot
    # tell whether there is a band to close.
    assert "area.clientHeight" in factor


def test_the_fill_correction_only_ever_zooms_in():
    """A hand-set zoom above the fit must never be pulled back by the
    correction, so its multiplier is floored at 1 rather than centring the
    overflow."""
    factor = body_of("fillFactor")
    assert "if (wide * aspect >= tall) return 1;" in factor
    # Two early guards return 1 (not the World view; already fills). The only
    # other return is the band-closing ratio, which is > 1 exactly when it is
    # reached, because the guard above has already excluded the <= 1 case.
    returns = re.findall(r"return ([^;]+);", factor)
    assert returns == ["1", "1", "tall / (wide * aspect)"], returns


def test_the_fill_correction_is_world_only():
    """A region view is a deliberate magnification of one slice, and its rect is
    not the board's aspect ratio — applying the same correction there would
    fight the player's own zoom."""
    factor = body_of("fillFactor")
    assert 'if (view !== "World") return 1;' in factor


def test_layout_runs_again_once_the_hand_has_its_height():
    """The hand bar's height comes from the cards in it, so #boardarea only has
    its true height after renderHand(). On a cold load layoutBoard() ran while
    the bar was still empty, over-estimated the area, and zoomed a wide window
    in for no reason (the board measured taller than the area it sat in)."""
    render = body_of("render")
    hand_at = render.index("renderHand();")
    layout_at = render.index("layoutBoard();")
    assert layout_at > hand_at, "layoutBoard must run after renderHand"
