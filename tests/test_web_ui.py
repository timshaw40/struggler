"""Smoke tests for the local web UI (scripts/serve_ui.py)."""

from __future__ import annotations

import importlib.util
import json
import threading
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("serve_ui", ROOT / "scripts" / "serve_ui.py")
serve_ui = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve_ui)


def make_session() -> serve_ui.Session:
    return serve_ui.Session(seed=3, us="human", ussr="greedy", events=True)


def test_two_human_seats_refused() -> None:
    with pytest.raises(SystemExit):
        serve_ui.Session(seed=1, us="human", ussr="human", events=True)


def test_watch_mode_steps_one_move_per_poll() -> None:
    session = serve_ui.Session(seed=1, us="greedy", ussr="greedy", events=True)
    assert session.watch and session.human_side.value == "US"
    before = len(session.history.history)
    session.step_once()
    assert len(session.history.history) == before + 1
    state = session.state()
    assert state["watch"] is True
    # A spectator never sees a decision to act on, even while a bot's
    # decision is pending.
    assert state["decision"] is None

    server = serve_ui.ThreadingHTTPServer(
        ("127.0.0.1", 0), serve_ui.make_handler(session, {})
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        first = json.load(urllib.request.urlopen(base + "/state"))["history"]
        second = json.load(urllib.request.urlopen(base + "/state"))["history"]
        assert len(second) > len(first)
    finally:
        server.shutdown()


def test_advance_parks_on_human_decision() -> None:
    session = make_session()
    session.advance()
    state = session.state()
    assert not state["is_terminal"]
    assert state["decision"] is not None
    assert state["decision"]["options"] == [
        {"index": i, "kind": a.kind.value, "payload": dict(a.payload)}
        for i, a in enumerate(session.engine.legal_actions())
    ]


def test_act_steps_option_and_advances() -> None:
    session = make_session()
    session.advance()
    first = session.engine.pending_decision
    session.act(0)
    after = session.engine.pending_decision
    assert after is not None and after.id != first.id
    state = session.state()
    # Between human decisions the server may resolve bot/CHANCE work; what
    # it never does is surface a decision addressed to the other seat.
    assert state["decision"] is None or not state["is_terminal"]


def test_state_hides_opponent_hand() -> None:
    session = make_session()
    session.advance()
    state = session.state()
    opponent = "USSR" if session.human_side.value == "US" else "US"
    for cid in state["hand"]:
        assert cid in session.engine.hands[session.human_side.value]
    for cid in session.engine.hands[opponent]:
        assert cid not in json.dumps(state)


def test_http_state_and_action_roundtrip() -> None:
    session = make_session()
    session.advance()
    server = serve_ui.ThreadingHTTPServer(
        ("127.0.0.1", 0), serve_ui.make_handler(session, {})
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        static = urllib.request.urlopen(base + "/style.css")
        assert static.status == 200
        state = json.load(urllib.request.urlopen(base + "/state"))
        assert state["decision"] is not None and state["decision"]["options"]
        reply = urllib.request.urlopen(
            urllib.request.Request(
                base + "/action",
                data=json.dumps({"index": 0}).encode(),
                headers={"Content-Type": "application/json"},
            )
        )
        after = json.load(reply)
        assert after["decision"] is not None or after["is_terminal"]
    finally:
        server.shutdown()


def test_forfeit_starts_a_new_game() -> None:
    session = make_session()
    session.advance()
    old = session.seed
    winner = session.forfeit()
    assert winner == "USSR"
    assert session.seed == old + 1
    assert not session.engine.is_terminal
    assert session.engine.pending_decision is not None


def test_restart_can_drop_ccw() -> None:
    session = make_session()
    assert "Chinese_Civil_War" in session.engine.board.countries
    session.restart(include_ccw=False)
    assert "Chinese_Civil_War" not in session.engine.board.countries
    assert "Chinese_Civil_War" not in session.engine.board.neighbors("USSR")
