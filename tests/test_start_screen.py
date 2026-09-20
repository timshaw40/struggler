"""The pick-a-side screen shown when a brand-new game loads.

The server half is exercised directly (no socket: binding one is not permitted
in every sandbox), and the client half is text-guarded in the style of the
other UI files. The screen itself was also driven end-to-end in a DOM stub
during development; these guards are what stays in the repo.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from struggler.engine import Side

ROOT = Path(__file__).resolve().parent.parent
APP = (ROOT / "ui" / "app.js").read_text()
CSS = (ROOT / "ui" / "style.css").read_text()
HTML = (ROOT / "ui" / "index.html").read_text()


def _serve_ui():
    spec = importlib.util.spec_from_file_location("serve_ui", ROOT / "scripts" / "serve_ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def body_of(name: str) -> str:
    match = re.search(rf"function {name}\(.*?\) \{{(.*?)\n\}}", APP, re.S)
    assert match, f"{name}() not found"
    return match.group(1)


# -- the server half --------------------------------------------------------


def test_restart_opts_reads_side_and_extra_influence():
    serve_ui = _serve_ui()
    assert serve_ui._restart_opts({"side": "USSR", "setup_us_extra": "4"}) == {
        "side": "USSR", "setup_us_extra": 4,
    }
    assert serve_ui._restart_opts({"setup_us_extra": 99}) == {"setup_us_extra": 6}
    assert serve_ui._restart_opts({"side": "RUSSIA"}) == {}        # unknown side
    assert serve_ui._restart_opts({"setup_us_extra": "nonsense"}) == {}
    assert serve_ui._restart_opts({}) == {}


def test_restart_swaps_the_human_seat_and_keeps_the_extra():
    serve_ui = _serve_ui()
    session = serve_ui.Session(seed=3, us="human", ussr="greedy", events=True)
    assert session.human_side is Side.US

    # The exact path the screen's POST takes: /new -> _restart_opts -> restart.
    session.restart(**serve_ui._restart_opts({"side": "USSR", "setup_us_extra": 2}))

    assert session.human_side is Side.USSR
    assert (session.us, session.ussr) == ("greedy", "human")
    assert session.setup_us_extra == 2
    assert session.engine.setup_us_extra == 2
    payload = session.state()
    assert payload["human_side"] == "USSR"
    # A brand-new game: first turn, still setting up, nothing to take back.
    assert payload["turn"] == 1 and payload["phase"] == "setup"
    assert payload["can_undo"] is False
    # And the seat really moved: the human is now the side that sets up first.
    assert payload["decision"]["kind"] == "place_influence"

# -- the client half --------------------------------------------------------


def test_start_screen_offers_both_sides_and_defaults_to_usa_plus_two():
    assert '<div id="start"' in HTML
    assert 'name="start-side" value="US"' in HTML
    assert 'name="start-side" value="USSR"' in HTML
    assert 'id="start-extra" min="0" max="6" step="1" value="2"' in HTML
    assert "#start[hidden]" in CSS
    # Above the game-over dialog: nothing behind it is the game that was asked for.
    assert re.search(r"#start \{[^}]*z-index: 60", CSS, re.S)


def test_start_screen_posts_the_choice_the_server_understands():
    start = body_of("startGame")
    assert "input[name=start-side]:checked" in start
    assert 'localStorage.setItem("struggler.side"' in start
    assert 'localStorage.setItem("struggler.usExtra"' in start
    assert 'postGame("/new")' in start
    # These are the two keys _restart_opts reads on the other end.
    assert 'side: localStorage.getItem("struggler.side")' in body_of("postGame")
    assert 'setup_us_extra: +(localStorage.getItem("struggler.usExtra")' in body_of("postGame")


def test_start_screen_only_asks_for_an_untouched_game():
    fresh = body_of("freshGame")
    for cond in ("state.can_undo", "state.turn === 1", 'state.phase === "setup"'):
        assert cond in fresh, cond
    # Boot is busy while the bot sets up, and that is the window the screen is
    # for, so `busy` must not suppress it.
    assert "!busy" not in fresh
    render = re.search(r"function render\(\) \{(.*?)\n\}", APP, re.S).group(1)
    assert "renderStart();" in render


def test_new_game_asks_instead_of_prompting():
    newgame = body_of("newGame")
    assert "openStart()" in newgame
    assert "confirm(" not in newgame, "the screen is the confirmation now"
    # Escape leaves the game on the table rather than dealing another one.
    assert "dismissStart()" in body_of("installKeyboard")


def test_start_screen_survives_a_stale_boot_poll():
    """Boot polls the server while the bot sets up; a poll still in flight must
    not land on top of the game the player just started."""
    catch_up = body_of("catchUp")
    assert "gen !== catchUpGen" in catch_up
    assert "catchUpGen += 1" in body_of("postGame")
