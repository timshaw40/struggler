"""Local web UI: play the engine in a browser against a bot.

    python scripts/serve_ui.py --us human --ussr mcts --seed 1
    -> http://localhost:8000

One seat is human and the other is any bot kind from `main.build_player`
("mcts", "greedy", "random", "first", "llm") — or both seats are bots for
spectator mode:

    python scripts/serve_ui.py --us mcts --ussr mcts --seed 2

In spectator mode every GET /state resolves exactly one CHANCE roll or
bot decision before returning: the browser's poll chain paces playback
(bot think time dominates), and no single request blocks for a whole
game. The server mirrors `runner.play_game`'s loop, interactively:

- GET /state resolves CHANCE rolls and bot decisions until it is the
  human's turn (or the game ends), then returns the human's observation.
- POST /action {"index": n} steps the human's choice — `options[n]` of
  the pending decision, verbatim: the server never invents actions.
- Everything the browser sees comes from `Engine.observe(human)` plus
  the public `Event` list, so secrecy holds exactly as it does for bots
  (opponent hand = a count; headline picks hidden until both are in).

Board/card images under ui/assets/ are optional art, installed from
`third_party/gmt-vassal/` by `install_vassal_ui_assets.py` (or produced from
your own PDFs by `render_assets.py`); the folder is gitignored and never
committed. Without art the UI falls back to plain text cards and no board.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from main import build_player  # noqa: E402  (repo CLI module, needs src/ on sys.path)
from struggler.engine import Engine, Side  # noqa: E402
from struggler.engine.player import Event  # noqa: E402
from struggler.engine.replay import HistoryBuilder  # noqa: E402
from struggler.engine.types import Action  # noqa: E402

UI_DIR = ROOT / "ui"

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
}


class Session:
    """One interactive game: the engine, the bot seats, the advance loop."""

    def __init__(self, seed: int, us: str, ussr: str, events: bool,
                 include_ccw: bool = True) -> None:
        humans = (us == "human") + (ussr == "human")
        if humans > 1:
            raise SystemExit("at most one human seat (no per-client identity)")
        self.watch = humans == 0
        # Play mode: the human's seat. Watch mode: the camera side — the
        # opponent hand stays hidden exactly as it would be for a bot.
        self.human_side = Side.US if (us == "human" or self.watch) else Side.USSR
        self.seed = seed
        self.us, self.ussr, self.events = us, ussr, events
        self.bot_kind = ussr if us == "human" else us
        self.include_ccw = include_ccw
        self.setup_us_extra = 0
        self.lock = threading.Lock()
        self._rebuild()

    def _rebuild(self) -> None:
        self.engine = Engine.new_game(
            seed=self.seed, events=self.events, include_ccw=self.include_ccw,
            setup_us_extra=self.setup_us_extra,
        )
        self.players: dict[Side, Any] = {}
        for side, kind in ((Side.US, self.us), (Side.USSR, self.ussr)):
            if kind != "human":
                self.players[side] = build_player(kind, seed=self.seed + (1 if side is Side.US else 2))
        self._rebind()
        self.history = HistoryBuilder()
        self._undo = None

    def restart(self, include_ccw: bool | None = None, side: str | None = None,
                setup_us_extra: int | None = None) -> None:
        if include_ccw is not None:
            self.include_ccw = include_ccw
        if setup_us_extra is not None:
            self.setup_us_extra = max(0, min(6, int(setup_us_extra)))
        if side in ("US", "USSR") and not self.watch:
            self.human_side = Side(side)
            if self.human_side is Side.US:
                self.us, self.ussr = "human", self.bot_kind
            else:
                self.us, self.ussr = self.bot_kind, "human"
        self.seed += 1
        self._rebuild()

    def _rebind(self) -> None:
        for player in self.players.values():
            bind = getattr(player, "bind_engine", None)
            if callable(bind):
                bind(self.engine)

    def undo(self) -> None:
        """Take back the human's last action (plus any bot/CHANCE replies
        after it). Single level: one undo per action."""
        if self._undo is None:
            raise RuntimeError("nothing to undo")
        snap = self._undo
        self._undo = None
        self.engine = Engine.deserialize(snap["engine"])
        del self.history.history[snap["hist_len"]:]
        del self.history._pending_headline[snap["pending_hl_len"]:]
        self._rebind()

    def forfeit(self, **restart_kw: Any) -> str:
        """Opponent wins, then a new game starts. Returns the winner's side."""
        winner = self.human_side.opponent
        self.engine._win(winner, "forfeit")
        side = winner.value
        self.restart(**restart_kw)
        return side

    def _step(self, action: Action) -> Event:
        decision = self.engine.pending_decision
        self.engine.step(action)
        event = self.history.record(decision, action, self.engine)
        detail = dict(action.payload)
        print(
            f"T{event.turn}/R{event.action_round} {event.actor.value:4s} "
            f"{decision.kind.value:20s} {detail}"
        )
        return event

    def step_once(self) -> None:
        """Resolve exactly one CHANCE roll or bot decision (watch mode)."""
        if self.engine.is_terminal:
            return
        decision = self.engine.pending_decision
        if decision is None:  # unreachable in practice; never spin on it
            return
        if decision.actor is Side.CHANCE:
            self._step(decision.options[0])
        else:
            observation = self.engine.observe(decision.actor)
            action = self.players[decision.actor].choose_action(
                observation, self.history.history
            )
            self._step(action)

    def advance(self) -> None:
        """Resolve CHANCE + bot decisions until the human must choose."""
        while not self.engine.is_terminal:
            decision = self.engine.pending_decision
            if decision is None or decision.actor is self.human_side:
                return
            self.step_once()

    def act(self, index: int) -> None:
        decision = self.engine.pending_decision
        if decision is None:
            raise RuntimeError("no pending decision")
        if not 0 <= index < len(decision.options):
            raise ValueError(f"option index {index} out of range")
        if decision.actor is self.human_side:
            self._undo = {
                "engine": self.engine.serialize(),
                "hist_len": len(self.history.history),
                "pending_hl_len": len(self.history._pending_headline),
            }
        self._step(decision.options[index])
        self.advance()

    # -- state projection ---------------------------------------------------

    @staticmethod
    def _json(value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Mapping):
            return {k: Session._json(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [Session._json(v) for v in value]
        return value

    @staticmethod
    def _event_view(event: Event) -> dict:
        return {
            "actor": event.actor.value,
            "kind": event.decision.kind.value,
            "payload": Session._json(event.action.payload),
            "context": Session._json(event.decision.context),
            "defcon": event.defcon,
            "vp": event.vp,
            "turn": event.turn,
            "action_round": event.action_round,
            "country": event.country,
            "country_influence": Session._json(event.country_influence),
            "country_control": event.country_control,
            "space_race": Session._json(event.space_race),
        }

    def state(self) -> dict:
        engine = self.engine
        obs = engine.observe(self.human_side)
        decision = obs.pending_decision
        data: dict[str, Any] = {
            "human_side": self.human_side.value,
            "seed": self.seed,
            "include_ccw": "Chinese_Civil_War" in engine.board.countries,
            "watch": self.watch,
            "phase": obs.phase,
            "phasing": None if decision is None else decision.actor.value,
            "defcon": obs.defcon,
            "vp": obs.vp,
            "turn": obs.turn,
            "action_round": obs.action_round,
            "influence": obs.influence,
            "hand": list(obs.hand),
            "opponent_hand_size": obs.opponent_hand_size,
            "draw_pile_size": obs.draw_pile_size,
            "discard_pile": list(obs.discard_pile),
            "removed_cards": list(obs.removed_cards),
            "china_card_owner": obs.china_card_owner.value,
            "china_card_available": obs.china_card_available,
            "space_race": obs.space_race,
            "space_race_attempts": obs.space_race_attempts,
            "military_ops": obs.military_ops,
            "score_preview": {
                cid: engine.preview_scoring(cid) for cid in obs.hand
                if engine.cards[cid].scoring
            },
            "turn_effects": self._json(obs.turn_effects),
            "game_effects": self._json(obs.game_effects),
            "can_undo": self._undo is not None and not self.watch,
            "is_terminal": engine.is_terminal,
            "winner": engine.winner.value if engine.winner is not None else None,
            "game_over_reason": engine._game_over_reason,
            "history": [self._event_view(e) for e in self.history.history[-60:]],
            # Only the human's own decisions reach the browser; bot/CHANCE
            # decisions are resolved server-side before state is sent. In
            # watch mode NO decision is surfaced — a spectator post must not
            # spend a bot's move.
            "decision": None
            if decision is None or self.watch or decision.actor is not self.human_side
            else {
                "kind": decision.kind.value,
                "context": self._json(decision.context),
                "options": [
                    {"index": i, "kind": a.kind.value, "payload": self._json(a.payload)}
                    for i, a in enumerate(decision.options)
                ],
            },
        }
        return data


def _restart_opts(body: dict) -> dict[str, Any]:
    opts: dict[str, Any] = {}
    if "include_ccw" in body:
        opts["include_ccw"] = bool(body["include_ccw"])
    if body.get("side") in ("US", "USSR"):
        opts["side"] = body["side"]
    if "setup_us_extra" in body:
        opts["setup_us_extra"] = int(body["setup_us_extra"])
    return opts


def make_handler(session: Session, cards_meta: dict) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:  # console already shows steps
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # Dev server with no validators: Safari's heuristic cache kept
            # serving stale app.js/style.css across pushes. Never store.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, code: int, data: Any) -> None:
            self._send(code, json.dumps(data).encode(), CONTENT_TYPES[".json"])

        def _static(self, rel: str) -> None:
            path = (UI_DIR / rel).resolve()
            if not path.is_file() or UI_DIR not in path.parents:
                self._send_json(404, {"error": "not found"})
                return
            self._send(200, path.read_bytes(), CONTENT_TYPES[path.suffix])

        def do_GET(self) -> None:
            route = self.path.split("?")[0]
            if route == "/state":
                with session.lock:
                    if session.watch:
                        session.step_once()
                    else:
                        d = session.engine.pending_decision
                        if d is not None and d.actor is not session.human_side:
                            session.step_once()
                    self._send_json(200, session.state())
            elif route == "/cards":
                self._send_json(200, cards_meta)
            elif route in ("/", "/index.html"):
                self._static("index.html")
            elif route in ("/app.js", "/style.css", "/countries.json"):
                self._static(route.lstrip("/"))
            elif route.startswith("/assets/"):
                self._static(str(Path("assets") / route[len("/assets/"):]))
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "bad json"})
                return
            try:
                with session.lock:
                    if self.path == "/action":
                        session.act(int(body.get("index", -1)))
                        self._send_json(200, session.state())
                    elif self.path == "/new":
                        session.restart(**_restart_opts(body))
                        self._send_json(200, session.state())
                    elif self.path == "/forfeit":
                        if session.watch:
                            self._send_json(409, {"error": "watch mode"})
                            return
                        winner = session.forfeit(**_restart_opts(body))
                        self._send_json(200, {"forfeit": True, "winner": winner, **session.state()})
                    elif self.path == "/back":
                        session.undo()
                        self._send_json(200, session.state())
                    else:
                        self._send_json(404, {"error": "not found"})
            except (ValueError, RuntimeError) as exc:
                with session.lock:
                    self._send_json(409, {"error": str(exc), "state": session.state()})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--us", default="human")
    parser.add_argument("--ussr", default="mcts")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--events", action=argparse.BooleanOptionalAction, default=True,
        help="Pass events= to Engine.new_game (default: on).",
    )
    parser.add_argument("--no-open", action="store_true", help="don't auto-open the browser")
    args = parser.parse_args()

    session = Session(seed=args.seed, us=args.us, ussr=args.ussr, events=args.events)
    cards_meta = {
        cid: {
            "number": card.number,
            "name": card.name,
            "ops": card.ops,
            "side": card.side.value,
            "period": card.period.value,
            "scoring": card.scoring,
            "remove_after_event": card.remove_after_event,
            "event_summary": card.event_summary,
        }
        for cid, card in session.engine.cards.items()
    }
    handler = make_handler(session, cards_meta)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://localhost:{args.port}"
    print(f"Struggler UI on {url}  (US={args.us}, USSR={args.ussr}, seed={args.seed})")
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:  # no browser on this machine — the URL is printed
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
