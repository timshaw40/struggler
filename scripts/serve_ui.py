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
import time
import urllib.parse
import webbrowser
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from main import build_player  # noqa: E402  (repo CLI module, needs src/ on sys.path)
from struggler.engine import DecisionKind, Engine, Side  # noqa: E402
from struggler.engine.player import Event  # noqa: E402
from struggler.engine.replay import (  # noqa: E402
    GameLogWriter,
    HistoryBuilder,
    encode_action,
    replay_history,
)
from struggler.web_support import (GameLibrary, HistoryEncoder, encode_history, decode_history,
                                   strategic_overview, action_preview)
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


def forecast_for(engine: Engine, decision, country: str) -> dict:
    """Hover odds for `country`, or {"kind": "none"} when this decision rolls
    no dice. Only legal targets of the pending decision get a forecast, so the
    UI can ask about whatever the cursor is over.

    All three target picks that end in a die roll are covered: a Coup and a
    Realignment roll immediately, and a War rolls once its country is chosen —
    so a War target gets a table too, rather than the guess the other two were
    fixed to remove."""
    if decision is None or not country:
        return {"kind": "none"}
    legal = {
        a.payload.get("country")
        for a in decision.options
        if a.payload and a.payload.get("country")
    }
    if country not in legal:
        return {"kind": "none"}
    if decision.kind is DecisionKind.COUP_TARGET:
        return engine.coup_forecast(decision, country)
    if decision.kind is DecisionKind.REALIGNMENT_TARGET:
        return engine.realignment_forecast(decision, country)
    if decision.kind is DecisionKind.WAR_TARGET:
        return engine.war_forecast(decision, country)
    return {"kind": "none"}


class Session:
    """One interactive game: the engine, the bot seats, the advance loop."""

    def __init__(self, seed: int, us: str, ussr: str, events: bool,
                 include_ccw: bool = True, log_dir: str | None = None,
                 library_dir: str | Path | None = None, restore: bool = True) -> None:
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
        self.log_dir = log_dir
        self.library = GameLibrary(library_dir) if library_dir else None
        self.save_id = None
        self.saved_at = None
        self.save_error = None
        self.game_id = time.time_ns() // 1_000_000 if self.library else 0
        self._log_writer: GameLogWriter | None = None
        self.lock = threading.Lock()
        # Restore only the most recent save, and only while it is unfinished:
        # a finished game, or explicit seats/seed on the command line
        # (`restore=False`, decided by `parse_args`), start a fresh game. The
        # earlier saves stay in the library either way.
        entries = self.library.entries() if (self.library and restore) else []
        self.restored = bool(entries) and not entries[0]['finished']
        if self.restored:
            try:
                self.load_game(entries[0]['id'])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise RuntimeError(f"Could not restore saved game: {exc}") from exc
        else:
            self._rebuild()

    def save_game(self) -> None:
        if not self.library:
            return
        # Every part is JSON primitives already (the engine's state by mandate
        # #5, history via `encode_event`), so no recursive conversion pass:
        # that pass over the whole history was most of each save's cost.
        encoder = self.__dict__.setdefault('_history_encoder', HistoryEncoder())
        snapshot = dict(
            engine=self.engine.serialize(), history=encoder.encode(self.history.history),
            pending_headline=encode_history(self.history._pending_headline),
            human_side=self.human_side.value, bot_kind=self.bot_kind,
            us=self.us, ussr=self.ussr, events=self.events, watch=self.watch,
            undo=self._encode_undo(),
        )
        try:
            self.save_id, self.saved_at = self.library.write(self.save_id, snapshot)
            self.save_error = None
        except (OSError, ValueError, TypeError) as exc:
            self.save_error = f"Automatic save failed: {exc}"
            print(self.save_error)

    def _encode_undo(self) -> dict | None:
        """The undo point for a save. History only grows between an undo point
        and the next save, so its history is a prefix of the live one: store
        the prefix length, not a second copy of the whole game (which doubled
        every save). Falls back to the full list if that ever stops holding."""
        if self._undo is None:
            return None
        past, live = self._undo['history'], self.history.history
        prefix = len(past) <= len(live) and all(a is b for a, b in zip(past, live))
        return dict(engine=self._undo['engine'],
                    pending_headline=encode_history(self._undo['pending_headline']),
                    **({'history_len': len(past)} if prefix else {'history': encode_history(past)}))

    def load_game(self, game: str) -> None:
        if not self.library:
            raise RuntimeError('Game library is not enabled')
        snapshot = self.library.read(game)
        # Decode everything before replacing the live session. A damaged save
        # cannot leave the current game half-loaded.
        engine = Engine.deserialize(snapshot['engine'])
        history = decode_history(snapshot['history'])
        pending = decode_history(snapshot['pending_headline'])
        undo = snapshot.get('undo')
        if undo:
            # `history_len`: the undo point's history is a prefix of this
            # save's history. Saves written before that field carry the list.
            undo_history = (history[:undo['history_len']] if 'history_len' in undo
                            else decode_history(undo['history']))
            undo = dict(engine=undo['engine'], history=undo_history,
                        pending_headline=decode_history(undo['pending_headline']))
        players = {Side(side): build_player(kind, seed=snapshot['engine']['seed'] + offset)
                   for side, kind, offset in (('US', snapshot['us'], 1), ('USSR', snapshot['ussr'], 2))
                   if kind != 'human'}
        self._close_log()
        self.engine, self.players = engine, players
        self.seed = int(snapshot['engine']['seed'])
        self.human_side = Side(snapshot['human_side'])
        self.us, self.ussr = snapshot['us'], snapshot['ussr']
        self.bot_kind, self.events = snapshot['bot_kind'], snapshot['events']
        self.watch = snapshot['watch']
        self.include_ccw = 'Chinese_Civil_War' in engine.board.countries
        self.setup_us_extra = engine.setup_us_extra
        self.history = HistoryBuilder(history)
        self.history._pending_headline = pending
        self._undo = undo
        self.game_id += 1
        self.save_id, self.saved_at = game, snapshot['saved_at']
        self._rebind()
        self._open_log(initial_actions=[encode_action(e.action) for e in history + pending])
        self.save_game()

    def placement_plan(self, countries: list[str], game_id: int, decision_id: int) -> tuple[Engine, list[Action]]:
        if game_id != self.game_id or self.engine.pending_decision is None or decision_id != self.engine.pending_decision.id:
            raise RuntimeError('The position changed. Please plan again.')
        if self.watch or not isinstance(countries, list) or not 0 < len(countries) <= 20:
            raise ValueError('Choose between 1 and 20 placements')
        clone = Engine.deserialize(self.engine.serialize())
        actions = []
        for country in countries:
            d = clone.pending_decision
            if d is None or d.actor is not self.human_side or d.kind is not DecisionKind.PLACE_INFLUENCE or d.context.get('setup'):
                raise ValueError('A plan must stay within the current Operations placement')
            action = next((a for a in d.options if a.payload.get('country') == country), None)
            if action is None:
                raise ValueError(f'{country} is not a legal placement in this plan')
            actions.append(action)
            clone.step(action)
        return clone, actions

    def commit_plan(self, countries: list[str], game_id: int, decision_id: int) -> None:
        _, actions = self.placement_plan(countries, game_id, decision_id)
        self._undo = {'engine': self.engine.serialize(), 'history': list(self.history.history),
                      'pending_headline': list(self.history._pending_headline)}
        for action in actions:
            self._step(action)
        self.advance()

    @classmethod
    def resume(cls, log_path: str, us: str, ussr: str, log_dir: str | None = None) -> "Session":
        """Rebuild a saved game from its log and continue it. The seat kinds
        come from the caller (they aren't stored in the log)."""
        log = json.loads(Path(log_path).read_text())
        if log.get("winner") is not None:
            raise SystemExit(f"{log_path} is a finished game; nothing to resume")
        session = cls.__new__(cls)
        session.watch = False
        session.human_side = Side.US if us == "human" else Side.USSR
        session.seed = int(log["seed"])
        session.us, session.ussr = us, ussr
        session.events = bool(log.get("events", True))
        session.bot_kind = ussr if us == "human" else us
        session.log_dir = log_dir
        session.library = None
        session.save_id = session.saved_at = session.save_error = None
        session.restored = False
        session.game_id = 0
        session._log_writer = None
        session.lock = threading.Lock()
        engine, history = replay_history(log)
        session.engine = engine
        session.include_ccw = "Chinese_Civil_War" in engine.board.countries
        session.setup_us_extra = engine.setup_us_extra
        session.history = HistoryBuilder(history)
        session._undo = None
        session.players = {}
        for side, kind in ((Side.US, us), (Side.USSR, ussr)):
            if kind != "human":
                session.players[side] = build_player(kind, seed=session.seed + (1 if side is Side.US else 2))
        session._rebind()
        session.game_id = 1
        session._open_log(initial_actions=log.get("actions"))
        return session

    def _open_log(self, initial_actions: list[dict] | None = None) -> None:
        if not self.log_dir:
            return
        path = Path(self.log_dir) / f"game_{self.game_id}.json"
        self._log_writer = GameLogWriter(path, self.engine, initial_actions=initial_actions)

    def _close_log(self) -> None:
        if self._log_writer is not None:
            self._log_writer.finalize(self.engine.winner)
            self._log_writer = None

    def _rebuild(self) -> None:
        self._close_log()  # finalize the outgoing game's log
        self.save_id = None
        self.game_id += 1
        if "deep" in (self.us, self.ussr):
            # DeepStruggle models neither the Chinese Civil War option nor any US
            # setup bonus but its own 2 points: the deep bot plays only that game.
            self.include_ccw, self.setup_us_extra = False, 2
            if not self.events:
                raise SystemExit("the deep bot needs the event layer on (drop --no-events)")
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
        self._open_log()
        self.save_game()

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
        after it). Single level: one undo per action. Restores the FULL
        HistoryBuilder state, including an in-progress headline buffer that
        length-truncation could not put back."""
        if self._undo is None:
            raise RuntimeError("nothing to undo")
        snap = self._undo
        self._undo = None
        self.engine = Engine.deserialize(snap["engine"])
        self.history.history = list(snap["history"])
        self.history._pending_headline = list(snap["pending_headline"])
        self._rebind()
        # Rewrite the on-disk log to match the rewound game.
        self._close_log()
        self._open_log(initial_actions=[encode_action(e.action) for e in self.history.history])
        self.save_game()

    def forfeit(self, **restart_kw: Any) -> str:
        """Opponent wins, then a new game starts. Returns the winner's side."""
        winner = self.human_side.opponent
        self.engine._win(winner, "forfeit")
        self.save_game()
        side = winner.value
        self.restart(**restart_kw)
        return side

    def _step(self, action: Action) -> Event:
        decision = self.engine.pending_decision
        self.engine.step(action)
        event = self.history.record(decision, action, self.engine)
        if self._log_writer is not None:
            self._log_writer.record_step(event)
        detail = dict(action.payload)
        print(
            f"T{event.turn}/R{event.action_round} {event.actor.value:4s} "
            f"{decision.kind.value:20s} {detail}"
        )
        return event

    def step_once(self) -> None:
        """Resolve exactly one CHANCE roll or bot decision (watch mode), then
        save."""
        self._step_once()
        self.save_game()

    def _step_once(self) -> None:
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
        """Resolve CHANCE + bot decisions until the human must choose, then
        save once. Saves happen at request boundaries, not per step: a full
        snapshot per step made autosave most of a bot game's wall time."""
        while not self.engine.is_terminal:
            decision = self.engine.pending_decision
            if decision is None or decision.actor is self.human_side:
                break
            self._step_once()
        self.save_game()

    def act(self, index: int, game_id: int | None = None,
            decision_id: int | None = None) -> None:
        """Apply the human's choice. `game_id`/`decision_id`, when given (the
        browser always sends them), reject a stale submission — a duplicated or
        late request can never advance a decision it wasn't answering."""
        decision = self.engine.pending_decision
        if decision is None:
            raise RuntimeError("no pending decision")
        if self.watch:
            raise RuntimeError("watch mode: no human seat")
        if game_id is not None and int(game_id) != self.game_id:
            raise RuntimeError(f"stale game id {game_id} (server {self.game_id})")
        if decision_id is not None and int(decision_id) != decision.id:
            raise RuntimeError("stale decision id")
        if decision.actor is not self.human_side:
            raise RuntimeError("not the human's decision")
        if not 0 <= index < len(decision.options):
            raise ValueError(f"option index {index} out of range")
        self._undo = {
            "engine": self.engine.serialize(),
            "history": list(self.history.history),
            "pending_headline": list(self.history._pending_headline),
        }
        self._step(decision.options[index])
        self.save_game()  # the human's own move is durable before bots think
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
            "game_id": self.game_id,
            "save_id": self.save_id,
            "saved_at": self.saved_at,
            "save_error": self.save_error,
            "library_enabled": self.library is not None,
            "strategy": strategic_overview(engine),
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
            "in_play_cards": list(obs.in_play_cards),
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
            # Why the human's own hand is narrowed this round, if it is: the
            # client dims the cards that are not on offer and says why, rather
            # than leaving a dead click. None whenever the question does not
            # apply (no decision, someone else's decision, watch mode).
            "play_restriction": (
                engine.play_restriction(self.human_side)
                if decision is not None
                and not self.watch
                and decision.actor is self.human_side
                and decision.kind is DecisionKind.ACTION_ROUND_PLAY
                else None
            ),
            "is_terminal": engine.is_terminal,
            "winner": engine.winner.value if engine.winner is not None else None,
            "game_over_reason": engine._game_over_reason,
            # `history` is truncated to a window, so its length is not a
            # progress signal once the window fills. `history_len` is the
            # monotonic total, which the client uses to know how many new
            # events a poll carried (otherwise all animation stops at 60).
            "history_len": len(self.history.history),
            "history": [self._event_view(e) for e in self.history.history[-60:]],
            # Only the human's own decisions reach the browser; bot/CHANCE
            # decisions are resolved server-side before state is sent. In
            # watch mode NO decision is surfaced — a spectator post must not
            # spend a bot's move.
            "decision": None
            if decision is None or self.watch or decision.actor is not self.human_side
            else {
                "id": decision.id,
                "kind": decision.kind.value,
                "context": self._json(decision.context),
                "options": [
                    {"index": i, "kind": a.kind.value, "payload": self._json(a.payload)}
                    for i, a in enumerate(decision.options)
                ],
            },
        }
        if data['decision'] is not None:
            data['action_previews'] = {str(i): action_preview(engine, decision, action)
                                       for i, action in enumerate(decision.options)}
        return data


def _restart_opts(body: dict) -> dict[str, Any]:
    opts: dict[str, Any] = {}
    if "include_ccw" in body:
        opts["include_ccw"] = bool(body["include_ccw"])
    if body.get("side") in ("US", "USSR"):
        opts["side"] = body["side"]
    raw_extra = body.get("setup_us_extra")
    if raw_extra is not None:
        try:
            opts["setup_us_extra"] = max(0, min(6, int(raw_extra)))
        except (TypeError, ValueError):
            pass  # a tampered/legacy null must not 500 the restart
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
            self._send(
                200, path.read_bytes(),
                CONTENT_TYPES.get(path.suffix, "application/octet-stream"),
            )

        def do_GET(self) -> None:
            route = self.path.split("?")[0]
            if route == "/state":
                # Compute under the lock, then write the socket *outside* it:
                # a slow client (or a slow LLM step) must not block every other
                # request behind the response write.
                try:
                    with session.lock:
                        if session.watch:
                            session.step_once()
                        else:
                            d = session.engine.pending_decision
                            if d is not None and d.actor is not session.human_side:
                                session.step_once()
                        payload = session.state()
                except Exception as exc:  # a bot/provider failure must not drop the socket
                    print(f"/state error: {exc!r}")
                    self._send_json(500, {"error": str(exc)})
                    return
                self._send_json(200, payload)
            elif route == "/games":
                with session.lock:
                    entries = session.library.entries() if session.library else []
                self._send_json(200, {'games': entries})
            elif route == "/position":
                # A read-only refresh: retries must not spend another bot move.
                with session.lock:
                    payload = session.state()
                self._send_json(200, payload)
            elif route == "/cards":
                self._send_json(200, cards_meta)
            elif route == "/countryfacts":
                # Static geography (region, Battleground, the DEFCON floor for
                # Coups) fetched once per page: the hover tip needs it on every
                # marker, and it must not cost a round trip per hover. Computed
                # per request rather than cached because /new swaps the engine
                # (and the country set moves with it). Read under the lock,
                # written outside it, like every other handler here.
                with session.lock:
                    facts = session.engine.country_facts()
                self._send_json(200, facts)
            elif route.startswith("/odds"):
                # Hover odds: the engine computes them, so the table matches
                # what a roll will actually do (see Engine.coup_forecast).
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                country = (query.get("country") or [""])[0]
                try:
                    with session.lock:
                        decision = session.engine.pending_decision
                        payload = forecast_for(session.engine, decision, country)
                except Exception as exc:
                    self._send_json(400, {"error": str(exc)})
                    return
                self._send_json(200, payload)
            elif route in ("/", "/index.html"):
                self._static("index.html")
            elif route in ("/app.js", "/experience.js", "/style.css", "/countries.json"):
                self._static(route.lstrip("/"))
            elif route.startswith("/assets/"):
                self._static(str(Path("assets") / route[len("/assets/"):]))
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, TypeError, json.JSONDecodeError):
                self._send_json(400, {"error": "bad json"})
                return
            # Resolve the whole request under ONE lock acquisition and write the
            # socket afterwards: the previous code re-locked on the error path
            # (a mutation window) and wrote the response while holding the lock.
            with session.lock:
                try:
                    if self.path == "/action":
                        session.act(
                            int(body.get("index", -1)),
                            game_id=body.get("game_id"),
                            decision_id=body.get("decision_id"),
                        )
                        code, payload = 200, session.state()
                    elif self.path == "/games/load":
                        session.load_game(body.get('id'))
                        code, payload = 200, session.state()
                    elif self.path in ('/plan', '/plan/commit'):
                        countries = body.get('countries')
                        game_id, decision_id = body.get('game_id'), body.get('decision_id')
                        if self.path == '/plan/commit':
                            session.commit_plan(countries, game_id, decision_id)
                            code, payload = 200, session.state()
                        else:
                            clone, _ = session.placement_plan(countries, game_id, decision_id)
                            d = clone.pending_decision
                            code, payload = 200, {
                                'influence': {c: clone.board.influence[c] for c in set(countries)},
                                'next_options': [a.payload.get('country') for a in d.options if a.payload.get('country')]
                                    if d and d.actor is session.human_side and d.kind is DecisionKind.PLACE_INFLUENCE else [],
                                'context': session._json(d.context)
                                    if d and d.actor is session.human_side and d.kind is DecisionKind.PLACE_INFLUENCE else {},
                                'strategy': strategic_overview(clone),
                                'controls': {c: clone.board.control(c).value if clone.board.control(c) else None for c in set(countries)},
                                'previews': {a.payload['country']: action_preview(clone, d, a) for a in d.options if a.payload.get('country')}
                                    if d and d.actor is session.human_side and d.kind is DecisionKind.PLACE_INFLUENCE else {},
                            }
                    elif self.path == "/new":
                        session.restart(**_restart_opts(body))
                        code, payload = 200, session.state()
                    elif self.path == "/forfeit":
                        if session.watch:
                            code, payload = 409, {"error": "watch mode"}
                        else:
                            winner = session.forfeit(**_restart_opts(body))
                            payload = session.state()
                            payload["forfeit"] = True
                            payload["winner"] = winner  # state()'s winner is the fresh game's
                            code = 200
                    elif self.path == "/back":
                        session.undo()
                        code, payload = 200, session.state()
                    else:
                        code, payload = 404, {"error": "not found"}
                except (ValueError, TypeError, RuntimeError, OSError, KeyError) as exc:
                    code, payload = 409, {"error": str(exc), "state": session.state()}
            self._send_json(code, payload)

    return Handler


#: Flags that describe a new game. Passing any of them means "this game",
#: so the last save is not restored over them.
GAME_DEFAULTS = {"us": "human", "ussr": "mcts", "seed": 1, "events": True}


def default_opponent() -> str:
    """`deep` when it can run here (packages, model and engine installed),
    else `mcts`. The deep bot beat mcts 199-1 over 200 side-swapped games."""
    from struggler.bots.deep import missing

    reason = missing()
    if reason:
        print(f"Opponent: mcts (deep bot unavailable: {reason})")
        return "mcts"
    return "deep"


def parse_args(argv: list[str] | None = None) -> tuple[argparse.Namespace, bool]:
    """Parse the command line and decide whether to restore the last save.

    Returns `(args, restore)`. `restore` is False when `--fresh` is given or
    when any game-describing flag (`--us`, `--ussr`, `--seed`, `--events` /
    `--no-events`) is passed explicitly, so a command such as the README's
    watch-mode line starts the game it names instead of reopening the last
    one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--us", default=None, help="seat kind (default: human)")
    parser.add_argument("--ussr", default=None,
                        help="seat kind (default: deep when installed, else mcts)")
    parser.add_argument("--seed", type=int, default=None, help="deal seed (default: 1)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--events", action=argparse.BooleanOptionalAction, default=None,
        help="Pass events= to Engine.new_game (default: on).",
    )
    parser.add_argument("--fresh", action="store_true",
                        help="start a new game instead of restoring the last unfinished save")
    parser.add_argument("--no-open", action="store_true", help="don't auto-open the browser")
    parser.add_argument("--log-dir", default=None,
                        help="write each game's replay log here (default: none)")
    parser.add_argument("--resume-log", default=None,
                        help="continue the game saved in this replay log instead of dealing fresh")
    parser.add_argument('--library-dir', default=str(ROOT / 'data' / 'games'),
                        help='automatic saves and game library (default: data/games)')
    args = parser.parse_args(argv)
    explicit = any(getattr(args, k) is not None for k in GAME_DEFAULTS)
    defaults = dict(GAME_DEFAULTS)
    if args.ussr is None and args.us in (None, "human"):
        defaults["ussr"] = default_opponent()
    for key, value in defaults.items():
        if getattr(args, key) is None:
            setattr(args, key, value)
    return args, not (explicit or args.fresh)


def main() -> None:
    args, restore = parse_args()

    if args.resume_log:
        session = Session.resume(
            args.resume_log, us=args.us, ussr=args.ussr, log_dir=args.log_dir
        )
        session.library = GameLibrary(args.library_dir)
        session.save_game()
    else:
        session = Session(seed=args.seed, us=args.us, ussr=args.ussr,
                          events=args.events, log_dir=args.log_dir, library_dir=args.library_dir,
                          restore=restore)
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
    print(f"Struggler UI on {url}  (US={session.us}, USSR={session.ussr}, seed={session.seed})")
    if session.restored:
        print("Restored the last unfinished game; pass --fresh (or --us/--ussr/--seed) for a new one.")
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
