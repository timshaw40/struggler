"""Replay a parsed BGG session game through the engine.

    python scripts/replay_game.py parsed/bgg-286443.json
    python scripts/replay_game.py parsed/*.json

The engine runs in recorded-replay mode (`Engine.new_game(replay_mode=True)`):
both hands are hidden and cards are declared when the log plays them, every
die is the recorded roll (physical-mode option exposure), and board states
are not validated as they happen — instead, each parsed record's `post`
entries ("... , now at N" assertions from the log) are checked when the
replay moves past that record. The report prints:

- consumed records / total parsed records (and the first unexplained gap)
- board mismatches: recorded post-state vs engine board at record end
- VP trajectory: recorded vp_after vs engine VP at record end
- decisions answered by fallback (log gave no answer)

Exit code 0 iff every record consumed with zero board/VP mismatches.
"""

from __future__ import annotations

import argparse
import copy
import glob
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.engine import Engine, Side  # noqa: E402
from struggler.engine.types import DecisionKind  # noqa: E402

CARD_KINDS = {DecisionKind.HEADLINE_PLAY, DecisionKind.ACTION_ROUND_PLAY}
SIDE_ROLL_KINDS = {
    DecisionKind.COUP_ROLL,
    DecisionKind.WAR_ROLL,
    DecisionKind.SPACE_RACE_ROLL,
    DecisionKind.QUAGMIRE_ROLL,
}


class Replay:
    def __init__(self, game: dict) -> None:
        # The driver mutates records in place (placement queues, discard
        # lists, realignment cursors): deep-copy so callers can reuse their
        # parsed games across evaluations.
        self.game = copy.deepcopy(game)
        self.engine = Engine.new_game(
            seed=0,
            include_optional=game.get("include_optional", True),
            replay_mode=True,
        )
        self.actions = self.game["actions"]
        self.ri = 0            # index of the record being consumed
        self.validated_i = 0   # records fully validated so far
        self.exhausted = False  # the log ran out before the game ended
        self.post_is: dict[int, int] = {}  # per-record next post assertion
        self.errors: list[str] = []
        self.fallbacks = 0
        self._build_assertions()
        # Flat setup queues per side, from the parsed setup records (the
        # engine interleaves its own printed at-start influence first).
        self.setup_queues: dict[str, list[str]] = {"US": [], "USSR": []}
        for rec in self.actions:
            if rec["kind"] != "setup":
                break
            for country, n in rec["placements"]:
                self.setup_queues[rec["side"]].extend([country] * n)
        # Tournament-balance extras ("+2 USA extra influence to Iran") have
        # no engine decision — apply them straight to the board at start.
        for rec in self.actions:
            if rec["kind"] != "setup":
                break
            for country, n in rec.get("extras", ()):
                self.engine.board.influence[country][rec["side"]] += n

    # -- record helpers -------------------------------------------------------

    def cur(self) -> dict | None:
        return self.actions[self.ri] if self.ri < len(self.actions) else None

    def next_matching(self, kind: str, side: str | None = None) -> dict | None:
        """Next unconsumed record of `kind` (and `side` if given). The engine
        demands picks strictly in sequence, so any earlier same-kind record
        is finished by definition and marked done."""
        for j in range(self.ri, len(self.actions)):
            rec = self.actions[j]
            if rec.get("_done") or rec["kind"] != kind:
                continue
            if side is not None and rec["side"] != side:
                continue
            for k in range(j):
                if self.actions[k]["kind"] == kind:
                    self.actions[k]["_done"] = True
            self.ri = j
            return rec
        return None

    def _build_assertions(self) -> None:
        """(rec_idx, kind, data, done) in log order. Consumed eagerly: after
        every engine step, any started record's assertion whose value now
        matches is checked off — snapshots are transient (a later record may
        move the same country), so 'matches now' is the only sound check."""
        self.assertions: list[list] = []
        for i, rec in enumerate(self.actions):
            for c, s, v in rec["post"]:
                self.assertions.append([i, "board", (c, s, v), False])
            if rec.get("vp_after") is not None:
                self.assertions.append([i, "vp", rec["vp_after"], False])

    def _consume_assertions(self) -> None:
        for a in self.assertions:
            if a[3] or a[0] > self.ri:
                continue
            if a[1] == "board":
                c, s, v = a[2]
                if self.engine.board.influence.get(c, {}).get(s) == v:
                    a[3] = True
            elif self.engine.vp == a[2]:
                a[3] = True

    def _fail_unmatched(self, upto: int) -> None:
        """Assertions of records we've moved past that never matched: the
        engine's board genuinely diverged from the log there."""
        for a in self.assertions:
            if a[3] or a[0] >= upto:
                continue
            a[3] = True
            rec = self.actions[a[0]]
            where = f"{rec['kind']} t{rec['turn']} {rec['side']} {rec['card']}"
            if a[1] == "board":
                c, s, v = a[2]
                actual = self.engine.board.influence.get(c, {}).get(s)
                self.errors.append(f"{where}: board {c}/{s} never reached {v} (engine {actual})")
            else:
                self.errors.append(f"{where}: VP never reached {a[2]} (engine {self.engine.vp})")

    def _pop_placement(self, dec, side: str) -> str | None:
        if dec.context.get("setup"):
            q = self.setup_queues.get(side) or []
            return q.pop(0) if q else None
        rec = self.cur()
        if rec is None:
            return None
        if dec.kind is DecisionKind.EVENT_INFLUENCE:
            op = dec.context.get("op", "place")
            inf_side = dec.context.get("inf_side", side)
            amount = int(dec.context.get("amount", 1))
            queue = rec["removals"] if op == "remove" else rec["placements"]
        else:
            inf_side, amount, queue = side, 1, rec["placements"]
        for item in queue:
            country, pside, n = item
            if pside == inf_side and n >= amount:
                if n == amount:
                    queue.remove(item)
                else:
                    item[2] = n - amount
                return country
        return None

    def _record_for_event(self, event: str | None) -> dict | None:
        """The record a sub-decision belongs to, found by the engine's
        event context (the resolving card id) near the current position."""
        if not event:
            return None
        lo = max(self.validated_i, self.ri - 3)
        for j in list(range(lo, min(len(self.actions), self.ri + 4))) + list(
            range(max(self.validated_i, self.ri - 8), lo)
        ):
            rec = self.actions[j]
            if rec["card"] == event:
                return rec
        return None

    def _switch(self, j: int, validate: bool) -> None:
        if validate:  # action-round card: everything before has resolved
            self._fail_unmatched(j)
        self.ri = j

    def _pop_discard(self, side: str) -> str | None:
        rec = self.cur()
        if rec is None:
            return None
        return rec["discards"].pop(0) if rec["discards"] else None

    # -- answering ------------------------------------------------------------

    def _pick(self, dec, want_payload: dict, label: str) -> Any:
        for a in dec.options:
            if a.payload == want_payload:
                return a
        if len(dec.options) == 1:
            # The engine offers exactly one legal answer (scoring cards, forced
            # continuations): the log cannot disagree, so just take it.
            return dec.options[0]
        self.fallbacks += 1
        self.errors.append(
            f"{label}: wanted {want_payload}, options were "
            f"{[a.payload for a in dec.options][:8]}"
        )
        return dec.options[0]

    def answer(self, dec) -> Any:
        kind = dec.kind
        side = dec.actor.value if dec.actor in (Side.US, Side.USSR) else None

        if kind in CARD_KINDS:
            is_headline = kind is DecisionKind.HEADLINE_PLAY
            rec = self.next_matching(
                "headline" if is_headline else "play", side
            )
            if rec is None:
                self.exhausted = True  # the log ends here
                return dec.options[0]
            j = self.actions.index(rec)
            self._switch(j, validate=not is_headline)
            want = {"card": rec["card"]}
            return self._pick(dec, want, f"{rec['kind']} t{rec['turn']} {side} card")

        if kind is DecisionKind.PLAY_MODE:
            rec = self.cur()
            mode = rec["mode"] if rec and rec["mode"] else "ops"
            return self._pick(dec, {"mode": mode}, f"play_mode t{rec['turn'] if rec else '?'}")

        if kind is DecisionKind.OPS_TYPE:
            rec = self.cur()
            t = rec["ops_type"] if rec and rec["ops_type"] else "influence"
            return self._pick(dec, {"type": t}, f"ops_type t{rec['turn'] if rec else '?'}")

        if kind is DecisionKind.EVENT_OPS_ORDER:
            rec = self.cur()
            order = "event_first" if rec and rec.get("event_first") else "ops_first"
            return self._pick(dec, {"order": order}, "event_ops_order")

        if kind is DecisionKind.PLACE_INFLUENCE or kind is DecisionKind.EVENT_INFLUENCE:
            if kind is DecisionKind.EVENT_INFLUENCE:
                found = self._record_for_event(dec.context.get("event"))
                if found is not None:
                    self.ri = self.actions.index(found)
            country = self._pop_placement(dec, side)
            if country:
                return self._pick(dec, {"country": country}, f"place {country}")
            self.fallbacks += 1
            self.errors.append(f"{kind.value} with no parsed placement ({side})")
            return dec.options[0]

        if kind is DecisionKind.COUP_TARGET or kind is DecisionKind.WAR_TARGET:
            rec = self.cur()
            want = rec["coup"]["country"] if rec and rec.get("coup") else None
            if want:
                return self._pick(dec, {"country": want}, f"target {want}")
            self.fallbacks += 1
            self.errors.append("coup/war target with no parsed record")
            return dec.options[0]

        if kind is DecisionKind.REALIGNMENT_TARGET:
            rec = self.cur()
            for r in rec["realignments"] if rec else []:
                if not r.get("_used"):
                    r["_used"] = True
                    return self._pick(dec, {"country": r["country"]}, f"realign {r['country']}")
            self.fallbacks += 1
            self.errors.append("realignment target with no parsed record")
            return dec.options[0]

        if kind in SIDE_ROLL_KINDS:
            rec = self.cur()
            if kind is DecisionKind.WAR_ROLL and dec.context.get("card"):
                # A war roll can fire while the resolving headline is not
                # the current record (resolution order is ops-descending).
                found = self._record_for_event(dec.context["card"])
                if found is not None:
                    self.ri = self.actions.index(found)
                    rec = found
            roll = None
            if rec:
                if kind is DecisionKind.COUP_ROLL and rec.get("coup"):
                    roll = rec["coup"]["roll"]
                elif kind is DecisionKind.SPACE_RACE_ROLL and rec.get("space"):
                    roll = rec["space"]["roll"]
                elif rec.get("war"):
                    roll = rec["war"].pop(0)["roll"]
                elif kind is DecisionKind.QUAGMIRE_ROLL and rec.get("quagmire_rolls"):
                    roll = rec["quagmire_rolls"].pop(0)
                elif rec["realignments"] and kind in (
                    DecisionKind.REALIGNMENT_ACTOR_ROLL, DecisionKind.REALIGNMENT_OPPONENT_ROLL
                ):
                    pass  # handled below
            if roll is None:
                self.fallbacks += 1
                self.errors.append(f"{kind.value} with no parsed roll")
                return dec.options[0]
            return self._pick(dec, {"value": roll}, f"roll {roll}")

        if kind in (DecisionKind.REALIGNMENT_ACTOR_ROLL, DecisionKind.REALIGNMENT_OPPONENT_ROLL):
            rec = self.cur()
            key = "actor" if kind is DecisionKind.REALIGNMENT_ACTOR_ROLL else "opponent"
            for r in reversed(rec["realignments"] if rec else []):
                if "rolls" in r and key not in r["rolls"]:
                    r["rolls"][key] = r["rolls"].get(
                        "US" if dec.actor is Side.US else "USSR"
                    )
                    return self._pick(dec, {"value": r["rolls"][key]}, f"realign roll")
            self.fallbacks += 1
            self.errors.append("realignment roll with no parsed record")
            return dec.options[0]

        if kind is DecisionKind.CONTEST_ROLL:
            rec = self._record_for_event(dec.context.get("event")) or self.cur()
            contests = (rec or {}).get("contests") or []
            if contests:
                entry = contests[min((rec or {}).setdefault("_contest_i", 0), len(contests) - 1)]
                sponsor = dec.context.get("sponsor")
                if any("sponsor_roll" in a.payload for a in dec.options):
                    return self._pick(dec, {"sponsor_roll": entry.get(sponsor)}, "contest sponsor")
                defender = "US" if sponsor == "USSR" else "USSR"
                rec["_contest_i"] += 1
                return self._pick(dec, {"defender_roll": entry.get(defender)}, "contest defender")
            self.fallbacks += 1
            self.errors.append("CONTEST_ROLL with no parsed dice")
            return dec.options[0]

        if kind is DecisionKind.DEAL_CARD:
            self.fallbacks += 1
            self.errors.append("DEAL_CARD in replay mode (should not happen)")
            return dec.options[0]

        if kind in (DecisionKind.RANDOM_DISCARD, DecisionKind.QUAGMIRE_DISCARD,
                    DecisionKind.HELD_CARD_DISCARD):
            cid = self._pop_discard(side)
            want = {"card": cid if cid else "none"}
            return self._pick(dec, want, f"discard {want['card']}")

        if kind is DecisionKind.EVENT_RESUME:
            return dec.options[0]

        if kind is DecisionKind.EVENT_CHOICE:
            if dec.context.get("event") == "Independent_Reds":
                # "Add US influence to equal the USSR's": if the USSR has
                # none anywhere eligible, the log rightly records nothing.
                if all(
                    self.engine.board.influence[o.payload["choice"]]["USSR"] == 0
                    for o in dec.options
                ):
                    return dec.options[0]
            if dec.context.get("event") == "Missile_Envy_physical_pick":
                # The operator names the card handed over: the log records it.
                for j in range(max(0, self.ri - 3), self.ri + 1):
                    rec = self.actions[j]
                    if rec.get("exchanged"):
                        for a in dec.options:
                            if a.payload.get("choice") == rec["exchanged"]:
                                return a
            if dec.context.get("event") == "Cambridge_Five_query":
                # "Does the US hold <scoring card>?" — the log never records
                # the reveal, but a card the US later plays was certainly
                # held then.
                scoring = dec.context.get("scoring_id")
                held = any(
                    a.get("card") == scoring and a["side"] == "US"
                    for a in self.actions
                    if a["kind"] in ("play", "headline") and a.get("card")
                )
                want = "yes" if held else "no"
                for a in dec.options:
                    if a.payload.get("choice") == want:
                        return a
            rec = self._record_for_event(dec.context.get("event"))
            if rec is not None:
                self.ri = self.actions.index(rec)
            choices_avail = {a.payload.get("choice") for a in dec.options}
            if rec and choices_avail == {"participate", "boycott"} and rec.get("contests"):
                # Olympic Games: the log's contest dice imply participation.
                for a in dec.options:
                    if a.payload.get("choice") == "participate":
                        return a
            if rec is not None:
                # Choice by country: De-Stalinization-style events ask "which
                # country" where the log recorded it as a placement/removal.
                for queue in (rec["removals"], rec["placements"]):
                    for item in list(queue):
                        country = item[0]
                        for a in dec.options:
                            if a.payload.get("choice") == country:
                                if queue is rec["removals"] and item in rec["removals"]:
                                    rec["removals"].remove(item)
                                else:
                                    rec["placements"].remove(item)
                                return a
            want = None
            choices_avail = {a.payload.get("choice") for a in dec.options}
            if rec and choices_avail == {"remove", "add"}:
                # Warsaw Pact-style branch: the log's placements/removals
                # tell which half of the event fired.
                choose = dec.context.get("choose_side")
                want = ("add" if any(it[1] == choose for it in rec["placements"])
                        else "remove")
                for a in dec.options:
                    if a.payload.get("choice") == want:
                        return a
            if rec and rec.get("choices"):
                want = rec["choices"][0]
                aliases = {"no_discard": "refuse"}
                want = aliases.get(want, want)
                for a in dec.options:
                    if a.payload.get("choice") == want:
                        rec["choices"].pop(0)
                        return a
            self.fallbacks += 1
            self.errors.append(
                f"event_choice unmatched (choices queued: {rec.get('choices') if rec else None}, "
                f"options: {[a.payload.get('choice') for a in dec.options][:8]})"
            )
            return dec.options[0]

        self.fallbacks += 1
        self.errors.append(f"unhandled decision kind: {kind}")
        return dec.options[0]

    def _started(self) -> bool:
        return self.ri > 0 or (self.cur() or {}).get("kind") != "setup"

    # -- main loop --------------------------------------------------------------

    def run(self, max_steps: int | None = None) -> dict:
        steps = 0
        limit = max_steps or 60_000  # hard stop against a driver bug spinning
        while not self.engine.is_terminal and steps < limit:
            if self.exhausted or self.ri >= len(self.actions):
                break  # the log ends here; further engine play is unscored
            dec = self.engine.pending_decision
            if dec is None:
                break
            action = self.answer(dec)
            self.engine.step(action)
            steps += 1
            self._consume_assertions()
        self._fail_unmatched(len(self.actions))
        return {
            "source": self.game["source"],
            "records": len(self.actions),
            "consumed": min(self.ri + 1, len(self.actions)),
            "steps": steps,
            "terminal": self.engine.is_terminal,
            "engine_vp": self.engine.vp,
            "log_vp": self.game.get("vp_final"),
            "fallbacks": self.fallbacks,
            "errors": self.errors[:40],
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", help="parsed game JSON files (or globs)")
    args = ap.parse_args()

    files: list[str] = []
    for p in args.paths:
        files.extend(sorted(glob.glob(p)))
    if not files:
        raise SystemExit("no input files")

    ok = True
    for f in files:
        game = json.loads(Path(f).read_text())
        rep = Replay(game).run()
        bad = rep["errors"] or rep["consumed"] < rep["records"] or rep["engine_vp"] != rep["log_vp"]
        ok &= not bad
        print(
            f"{rep['source']}: records {rep['consumed']}/{rep['records']} "
            f"steps {rep['steps']} terminal={rep['terminal']} "
            f"VP {rep['engine_vp']} (log {rep['log_vp']}) "
            f"fallbacks {rep['fallbacks']} errors {len(rep['errors'])}"
        )
        for e in rep["errors"][:12]:
            print(f"  - {e}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
