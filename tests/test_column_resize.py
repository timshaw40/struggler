"""The log column's width is the player's to set.

The log, the status rows and the decision box share the right column, which is
exactly the space the map would otherwise have, so "give the map more room" is
"make the log narrower". It is dragged by #colsplit, clamped so neither side
can be squeezed out, remembered per browser, and re-clamped when the window
changes size.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "ui" / "app.js").read_text()
CSS = (ROOT / "ui" / "style.css").read_text()
HTML = (ROOT / "ui" / "index.html").read_text()


def number(name: str) -> int:
    match = re.search(rf"const {name} = ([0-9]+);", APP)
    assert match, f"{name} not found"
    return int(match.group(1))


def body_of(name: str) -> str:
    match = re.search(rf"function {name}\(.*?\) \{{(.*?)\n\}}", APP, re.S)
    assert match, f"{name}() not found"
    return match.group(1)


def limit(main_width: int) -> int:
    """The clamp the client applies, evaluated from its own constants."""
    lo, hi, leave = number("SIDEBAR_MIN"), number("SIDEBAR_MAX"), number("SIDEBAR_LEAVE")
    return max(lo, min(hi, main_width - leave))


def test_divider_exists_between_the_map_and_the_log():
    assert 'id="colsplit"' in HTML
    separator = re.search(r'<div id="colsplit".*?>', HTML, re.S).group(0)
    assert 'role="separator"' in separator
    assert 'aria-orientation="vertical"' in separator
    assert 'tabindex="0"' in separator, "the divider has to be reachable by keyboard"
    # It is a sibling of #panel, inside <main>: the two columns it divides.
    assert HTML.index('id="hand"') < HTML.index('id="colsplit"') < HTML.index('id="panel"')


def test_the_panel_width_prefers_a_dragged_value_over_the_stylesheet():
    assert "--sidebar-w-user" in CSS
    assert "width: var(--sidebar-w-user, var(--sidebar-w))" in CSS
    # The media queries stay as the default a hand-arranged window overrides.
    assert re.search(r"@media \(max-width: 1180px\) \{\s*:root \{ --sidebar-w: 290px; \}", CSS)
    # Dragging must not fight the map for the pointer.
    assert "#colsplit { " in CSS and "cursor: col-resize" in CSS
    assert "touch-action: none" in CSS
    assert "body.colresize #boardwrap { pointer-events: none; }" in CSS


def test_drag_clamps_and_saves():
    setter = body_of("setSidebarWidth")
    assert "Math.max(SIDEBAR_MIN, Math.min(sidebarLimit(), Math.round(px)))" in setter
    assert 'localStorage.setItem("struggler.sidebarW"' in setter
    assert "layoutBoard()" in setter, "the map has to be re-measured after a drag"
    resize = body_of("enableColumnResize")
    assert 'addEventListener("pointerdown"' in resize
    assert 'addEventListener("dblclick", resetSidebarWidth)' in resize
    assert '"pointermove"' in resize and '"pointerup"' in resize
    # Released outside the window: without this the drag never ends.
    assert '"blur"' in resize


def test_neither_column_can_be_squeezed_out():
    lo, hi, leave = number("SIDEBAR_MIN"), number("SIDEBAR_MAX"), number("SIDEBAR_LEAVE")
    assert lo >= 180, "a 180px log cannot show a turn header and a card thumb"
    assert hi > lo
    assert leave >= 150, "the map needs a real minimum too"
    # A 1400px window: the max applies. A 800px window: the map keeps its 200px.
    assert limit(1400) == hi
    assert limit(800) == 800 - leave
    assert limit(400) == lo, "below the floor, the minimum wins rather than a negative width"
    # The limit comes from the layout, not the viewport, so it survives a scrollbar.
    assert 'document.querySelector("main")' in body_of("sidebarLimit")


def test_a_remembered_width_is_re_clamped_on_a_new_window():
    """A width dragged on a wide screen must not squeeze the map on a narrow
    one: the resize listener re-clamps before the map is measured."""
    resize = re.search(r'window\.addEventListener\("resize", \(\) => \{(.*?)\n  \}\);', APP, re.S)
    assert resize, "resize listener not found"
    assert "setSidebarWidth(sidebarWidth, { save: false })" in resize.group(1)
    assert "layoutBoard()" in resize.group(1)
    boot = body_of("bootInner")
    assert "enableColumnResize()" in boot
    assert "setSidebarWidth(sidebarWidth, { save: false })" in boot, "clamp a restored width at boot"


def test_map_only_mode_hides_the_divider_too():
    """With the panel folded away there is nothing to drag, and a divider that
    eats the pointer at the map's right edge would be a trap."""
    assert "body.mapfocus #panel,\nbody.mapfocus #colsplit { display: none; }" in CSS


def test_reset_returns_to_the_stylesheet_default():
    reset = body_of("resetSidebarWidth")
    assert "sidebarWidth = null" in reset
    assert 'localStorage.removeItem("struggler.sidebarW")' in reset
    apply = body_of("applySidebarWidth")
    assert 'removeProperty("--sidebar-w")' in apply
