"""Parse BGG session-report threads into engine-shaped ParsedGame JSON.

    python scripts/parse_sessions.py --thread 286443
    python scripts/parse_sessions.py --thread 289098 --out parsed/

The archive text (twilight_struggle_sessions.txt, gitignored like all
user-supplied data) is sliced by "THREAD <id>:" markers. Parsers are
per-format; `--format wgr` handles the WarGameRoom native-log family
(Ziemowit trio, Gupta semis, ...): every influence line carries an
explicit "now at N" post-state, so the parser validates itself by
chaining those values — a regex misread breaks continuity immediately.

Output schema (v1):
{
  "source": "bgg-<thread>", "format": "wgr", "include_optional": true,
  "players": {"US": ..., "USSR": ...}, "winner": ..., "vp_final": ...,
  "actions": [
    {"kind": "setup", "side": "USSR", "placements": [["Poland", 3], ...]},
    {"kind": "headline"|"play", "turn": N, "ar": N|null, "side": "USSR",
     "card": "<engine card id>", "mode": "event"|"ops"|"space_race",
     "ops_type": "influence"|"coup"|"realignment"|null,
     "coup": {"country": ..., "roll": N},                      # ops_type=coup
     "realignments": [{"country": ..., "rolls": {"US": N, "USSR": N}}, ...],
     "placements": [["<country>", "US"|"USSR", N], ...],        # +N influence
     "removals": [["<country>", "US"|"USSR", N], ...],          # -N influence
     "discards": ["<card id>", ...],     # quagmire/bear-trap/blockade/etc.
     "space": {"roll": N, "needed": N},
     "event_first": bool,               # "event occur first" (ops plays)
     "post": [["<country>", "US"|"USSR", N], ...],              # assertions
     "vp_after": N}
  ]
}
Country values are engine ids; card ids are engine card ids resolved by
canonical GMT number first, then by name.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.engine.data_loader import load_json  # noqa: E402

CARDS = load_json("cards.json")["cards"]
# Log sources number cards by their own schemes (WGR deck order differs from
# GMT/period numbering), so names are the reliable key: match on a
# punctuation/case-insensitive normal form, with numbers as a fallback.
NAME_MAP = {}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


for _cid, _c in CARDS.items():
    NAME_MAP[_norm(_c["name"])] = _cid

# WGR / BGG display spellings -> engine country ids. Built from engine
# country names first (they already say "Spain/Portugal"), then a small
# manual layer for abbreviations seen in logs.
_COUNTRY_ALIAS = {
    "united kingdom": "UK",
    "w germany": "West_Germany",
    "w. germany": "West_Germany",
    "west germany": "West_Germany",
    "e germany": "East_Germany",
    "e. germany": "East_Germany",
    "east germany": "East_Germany",
    "s. korea": "South_Korea",
    "s. korean": "South_Korea",
    "south korea": "South_Korea",
    "n. korea": "North_Korea",
    "n. korean": "North_Korea",
    "north korea": "North_Korea",
    "spain/portugal": "Spain_Portugal",
    "spain / portugal": "Spain_Portugal",
    "spain": "Spain_Portugal",
    "portugal": "Spain_Portugal",
    "laos/cambodia": "Laos_Cambodia",
    "laos / cambodia": "Laos_Cambodia",
    "laos": "Laos_Cambodia",
    "cambodia": "Laos_Cambodia",
    "se african states": "SE_African_States",
    "s.e. african states": "SE_African_States",
    "west african states": "West_African_States",
    "w african states": "West_African_States",
    "saharan states": "Saharan_States",
    "gulf states": "Gulf_States",
    "dominican rep": "Dominican_Republic",
    "dominican republic": "Dominican_Republic",
    "ivory/gold coast": "Ivory_Coast",
    "ivory gold coast": "Ivory_Coast",
    "central america": None,  # region names only appear in scoring blocks
    "south america": None,
    "middle east": None,
    "southeast asia": None,
}
for _cid, _e in json.loads(json.dumps(load_json("countries.json")))["countries"].items():
    _COUNTRY_ALIAS.setdefault(_e["name"].lower(), _cid)
    _COUNTRY_ALIAS.setdefault(_cid.lower().replace("_", " "), _cid)

_REGION_ALIAS = {
    "middle east": "MIDDLE_EAST",
    "europe": "EUROPE",
    "asia": "ASIA",
    "africa": "AFRICA",
    "central america": "CENTRAL_AMERICA",
    "south america": "SOUTH_AMERICA",
    "southeast asia": "SOUTHEAST_ASIA",
}


def resolve_country(name: str) -> str | None:
    key = name.strip().strip(".,").lower()
    return _COUNTRY_ALIAS.get(key)


def resolve_card(raw: str) -> str | None:
    """Card id from a fragment like '#101  Ops 2: Formosan Resolution * (USA)'.
    Name wins (numbering schemes differ across sources); number is fallback."""
    name = re.sub(r"^#?\d+\s*(Ops\s*\d+)?\s*:?\s*", "", raw.strip())
    name = re.sub(r"[(].*$", "", name).replace("*", "").strip()
    hit = NAME_MAP.get(_norm(name))
    if hit:
        return hit
    m = re.search(r"#(\d+)", raw)
    if m:
        for cid, c in CARDS.items():
            if c["number"] == int(m.group(1)):
                return cid
    return None


# -- line patterns (WGR native log grammar) ----------------------------------

RE_THREAD = re.compile(r"^THREAD (\d+): (.*)$")
RE_TURN_HEADLINE = re.compile(r"\*\* Turn (\d+) Headline Phase \*\*")
RE_TURN_ACTION = re.compile(r"\*\* Turn (\d+) Action Phase \*\*")
RE_AR = re.compile(r"Turn (\d+), (USSR|USA|Soviet|American) action round (\d+)")
RE_HEADLINE_CARD = re.compile(
    r"(Soviet|American) Headline Card:\s*(#.*?)(?:\s*\((USSR|USA)\))?\s*$"
)
RE_HEADLINE_EVENT = re.compile(r"(Soviet|American|USA|USSR) Headline Event:\s*(#.*)$")
RE_PLAY = re.compile(
    r"The (Soviets|Americans|American|Soviet player|US player|USSR player) "
    r"play(?:s)? the following card (.+?):\s*$"
)
RE_CARD = re.compile(r"#\d+.*")
RE_INF = re.compile(
    r"^\s*(?:(\d+)\s+)?(USA|USSR|Soviet|American)\s+influence\s+"
    r"(?:added to (.+?),|in (.+?))(?:\s+(increased|reduced) by (\d+))?,? now at (\d+)"
)
# Event-driven lines name the card instead of the side:
#   "1 De-Stalinization influence removed from Panama, now at 2"
#   "1 Warsaw Pact influence added to East Germany, now at 5"
# The influence belongs to the acting side (the card's player), which the
# continuity check will flag if that guess is ever wrong.
RE_INF_CARD = re.compile(
    r"^\s*(\d+)\s+[A-Z][\w'.-]*[^:]influence (added to|removed from) (.+?), now at (\d+)"
)
RE_INF_SET = re.compile(r"^\s*(USA|USSR|Soviet|American) influence in (.+?) now at (\d+)")
RE_COUP_AT = re.compile(r"Coup attempt in (.+?) \(stability \d+\):")
RE_DIE = re.compile(r"\*\* (USA|USSR) die roll = (\d+) \(([-+]?\d+)\) = (\d+)")
RE_COUP_RES = re.compile(
    r"The modified roll (exceeds the doubled stability(?: by \d+)?|does not exceed)"
)
RE_REALIGN = re.compile(r"Realignment roll in (.+?): (USA|USSR) modifier = ([-+]?\d+), (USA|USSR) modifier = ([-+]?\d+)")
RE_REALIGN_RES = re.compile(r"(reduced by (\d+), now at (\d+)|No effect|increased by (\d+), now at (\d+))")
RE_SPACE = re.compile(r"Space Race Die Roll \(1-(\d+) needed\): = (\d+)")
RE_SCORING = re.compile(r"\*\*\* Scoring in (.+?) \*\*\*")
RE_VP = re.compile(r"VPs (up|down) (\d+), now at (-?\d+)")
RE_DEFCON = re.compile(r"DEFCON Level (lowered|raised) to (\d+)")
RE_MILOPS = re.compile(r"(Soviet|American) Military Operations for this turn increased to (\d+)")
RE_DISCARD = re.compile(
    r"(?:The (USSR|Soviet player|Soviet|American player|Americans|USA|US player) discards?"
    r" the following cards? (because of Bear Trap|to be replaced|because of the Blockade|"
    r"for the reshuffle)?:|discards? the following card)"
)
RE_EVENT_FIRST = re.compile(r"They elect to have the (Soviet|American) event occur first")
RE_USE_EVENT = re.compile(r"The (Soviets|Americans) use the (USSR|USA) event played by the (USSR|USA)")
RE_USE_OPS = re.compile(r"The (Soviets|Americans) use the (.+?) card to (place influence|conduct operations)")
RE_WAR_ROLL = re.compile(
    r"\*\* Die roll: (\d+)(?: \(([-+]?\d+)\))?(?: = \d+)? "
    r"-- ((?:USA|USSR) (?:victory|failure)|no effect)"
)
RE_BOYCOTT = re.compile(r"decides? not to boycott|does not boycott")
RE_REMOVED = re.compile(r"\*\* The (.+?) card is permanently removed")
RE_CONTEST_DIE = re.compile(r"\*\* (American|Soviet) Die Roll: (\d+)")
RE_SETUP_INF = re.compile(
    r"^\s*(\d+) (USSR|USA) (extra )?influence added to (.+?), now at (\d+)"
)
RE_NO_DISCARD = re.compile(r"does not choose to discard")


RE_BBCODE = re.compile(r"\[/?(?:b|i|u|q|color=[^\]]*|imageid=[^\]]*)\]")


def side_of(token: str) -> str:
    token = token.strip()
    if token in ("USA", "US", "American", "American player", "US player", "Americans"):
        return "US"
    return "USSR"


def _norm_player(token: str) -> str:
    token = token.strip()
    return "USSR" if token in ("Soviets", "Soviet", "Soviet player", "USSR player", "USSR") else "US"


class WGRParser:
    """One WGR log -> ParsedGame. Tracks the board from 'now at' values so
    the parse validates itself (delta continuity) without the engine."""

    RE_HAND_HEADER = re.compile(r"Strategy Hand|^My hand:|^\s*Hand:\s*$")

    def __init__(self, thread_id: int, title: str) -> None:
        self.source = f"bgg-{thread_id}"
        self.title = title
        self.actions: list[dict] = []
        # Board tracking starts from the printed at-start influence — the
        # logs' "now at" totals include it (e.g. "1 added to East Germany,
        # now at 4" = 3 printed + 1 placed), exactly like the engine.
        setup = load_json("countries.json").get("setup_influence", {})
        self.board: dict[str, dict[str, int]] = {
            cid: {"US": 0, "USSR": 0}
            for cid in load_json("countries.json")["countries"]
        }
        for side, placements in setup.items():
            for cid, n in placements.items():
                self.board[cid][side] = n
        self.warnings: list[str] = []
        self.vp: int | None = None
        self.turn = 0
        self.cur: dict | None = None      # action being built
        self.mode: str | None = None      # sub-mode of the play intro
        self.realign: dict | None = None
        self.headline_recs: dict[str, dict] = {}  # side -> its headline record
        self.action_phase = False
        self.resolving: str | None = None  # side whose headline event is resolving

    def warn(self, msg: str) -> None:
        self.warnings.append(f"turn {self.turn}: {msg}")

    # -- record helpers ------------------------------------------------------

    def _new(self, kind: str, side: str, turn: int, ar: int | None = None) -> dict:
        rec = {
            "kind": kind, "turn": turn, "side": side,
            "card": None, "mode": None, "ops_type": None,
            "coup": None, "realignments": [], "placements": [], "removals": [],
            "discards": [], "space": None, "event_first": None,
            "post": [], "vp_after": None, "raw": [],
        }
        if ar is not None:
            rec["ar"] = ar
        self.actions.append(rec)
        return rec

    def _touch(self, country: str, side: str, after: int, delta: int | None) -> None:
        prev = self.board.get(country, {}).get(side)
        self.board.setdefault(country, {"US": 0, "USSR": 0})[side] = after
        if prev is not None and delta is not None and prev + delta != after:
            self.warn(f"continuity: {country}/{side} {prev}{delta:+d} != {after}")
        if self.cur is not None:
            if self.cur["kind"] == "setup":
                # Setup records carry [country, count] pairs only; the board
                # tracking below is what validates the parse. Only the LAST
                # value per country+side is asserted: extra-influence lines
                # legitimately move the same country within the setup.
                self.cur["post"] = [
                    e for e in self.cur["post"] if not (e[0] == country and e[1] == side)
                ]
            elif delta is not None and delta < 0:
                self.cur["removals"].append([country, side, -delta])
            elif delta is not None and delta > 0:
                self.cur["placements"].append([country, side, delta])
            self.cur["post"].append([country, side, after])

    # -- main line loop --------------------------------------------------------

    def parse(self, lines: list[str]) -> dict:
        pending_discards = False
        in_hand_block = False
        for raw in lines:
            line = RE_BBCODE.sub("", raw.rstrip("\n"))
            stripped = line.strip()

            if in_hand_block:
                # A hand listing is a run of card lines; the first line that
                # is neither blank nor a card line ends it (setup placements
                # and play logs may share the same [q] block).
                if not stripped:
                    continue
                if RE_CARD.search(stripped):
                    continue
                in_hand_block = False
            if self.RE_HAND_HEADER.search(stripped):
                in_hand_block = True
                continue

            m = RE_SETUP_INF.match(line)
            if m and self.turn == 0:  # setup placements only exist pre-game
                n, side = int(m.group(1)), side_of(m.group(2))
                country, after = resolve_country(m.group(4)), int(m.group(5))
                extra = m.group(3) is not None
                if country is None:
                    self.warn(f"unknown setup country: {line!r}")
                    continue
                last = self.actions[-1] if self.actions else None
                if not last or last["kind"] != "setup" or last["side"] != side:
                    rec = {
                        "kind": "setup", "turn": 0, "side": side,
                        "card": None, "mode": None, "ops_type": None,
                        "coup": None, "realignments": [], "placements": [], "removals": [],
                        "discards": [], "space": None, "event_first": None,
                        "extras": [], "post": [], "vp_after": None, "raw": [],
                    }
                    self.actions.append(rec)
                self.cur = self.actions[-1]
                (self.cur["extras"] if extra else self.cur["placements"]).append([country, n])
                self._touch(country, side, after, n)
                continue

            m = RE_TURN_HEADLINE.search(line)
            if m:
                self.turn = int(m.group(1))
                self.cur = None
                self.headline_recs = {}
                self.action_phase = False
                self.resolving = None
                continue
            m = RE_TURN_ACTION.search(line)
            if m:
                self.turn = int(m.group(1))
                self.cur = None
                self.headline_recs = {}  # headline resolution is over
                self.action_phase = True
                self.resolving = None
                continue
            m = RE_AR.search(line)
            if m:
                self.resolving = None
                self.cur = self._new("play", _norm_player(m.group(2)), int(m.group(1)), int(m.group(3)))
                continue
            m = RE_HEADLINE_CARD.search(line)
            if m:
                self.cur = self._new("headline", side_of(m.group(1)), self.turn)
                self.headline_recs[self.cur["side"]] = self.cur
                self.cur["card"] = resolve_card(m.group(2))
                if self.cur["card"] is None:
                    self.warn(f"unresolved card: {m.group(2)!r}")
                self.mode = "event"
                continue
            m = RE_HEADLINE_EVENT.search(line)
            if m:
                # the headline EVENT resolution of the card picked above
                if self.cur is not None and self.cur["kind"] == "headline":
                    self.mode = "event"
                self.resolving = side_of(m.group(1))
                continue
            m = RE_PLAY.search(line)
            if m:
                side = _norm_player(m.group(1))
                intro = m.group(2)
                # Headline resolution re-states the card ("The Soviets play
                # the following card as an Event:") — only trust it while the
                # engine-announced "X Headline Event:" is still the last word:
                # some logs (Ziemowit's) start action rounds without any
                # phase marker, and an AR play must never merge into the
                # headline record.
                hl = self.headline_recs.get(side)
                if ("as an Event" in intro and self.resolving == side
                        and hl is not None and hl.get("card") is not None
                        and not hl.get("_resolved")):
                    hl["_resolved"] = True
                    self.resolving = None
                    self.cur = hl
                elif (self.cur is not None and self.cur.get("side") == side
                      and not self.cur.get("placements")):
                    pass  # continuation: this record's ops half is still pending
                elif self.cur is None or self.cur.get("side") != side or self.cur.get("card") is not None:
                    self.cur = self._new("play", side, self.turn)
                # else: continue the current record — an event-first play's
                # ops half, or a Defectors+UN-Intervention-style combo, both
                # restate "The X play the following card..." with no new AR
                # header. The placements belong to the card already playing.
                if "as an Event" in intro:
                    self.mode, self.cur["mode"] = "event", "event"
                elif "place influence" in intro:
                    self.mode, self.cur["mode"], self.cur["ops_type"] = "ops", "ops", "influence"
                elif "coup attempt" in intro:
                    self.mode, self.cur["mode"], self.cur["ops_type"] = "coup", "ops", "coup"
                elif "realignment" in intro:
                    self.mode, self.cur["mode"], self.cur["ops_type"] = "realign", "ops", "realignment"
                elif "Space Race" in intro:
                    self.mode, self.cur["mode"] = "space", "space_race"
                else:  # "for Ops": event-first vs ops-only decided by later lines
                    self.mode = "ops"
                    self.cur["mode"] = "ops"
                continue
            if RE_CARD.match(stripped) and self.cur is not None and self.cur["card"] is None:
                self.cur["card"] = resolve_card(stripped)
                if self.cur["card"] is None:
                    self.warn(f"unresolved card: {stripped!r}")
                continue

            if not self.cur:
                continue

            m = RE_EVENT_FIRST.search(line)
            if m:
                self.cur["event_first"] = True
                self.mode = "event"  # event resolves first, then ops
                continue
            m = RE_USE_EVENT.search(line)
            if m:
                self.mode = "event"
                continue
            m = RE_USE_OPS.search(line)
            if m:
                self.mode = "ops"
                if self.cur["ops_type"] is None:
                    self.cur["ops_type"] = "influence" if "place influence" in m.group(3) else "ops"
                continue

            m = RE_DISCARD.search(line)
            if m:
                pending_discards = True
                continue
            if pending_discards and RE_CARD.match(stripped):
                cid = resolve_card(stripped)
                if cid:
                    self.cur["discards"].append(cid)
                else:
                    self.warn(f"unresolved discard: {stripped!r}")
                continue
            if pending_discards and stripped and not RE_CARD.match(stripped):
                pending_discards = False

            m = RE_COUP_AT.search(line)
            if m and self.mode in ("coup", "ops"):
                self.cur["coup"] = {"country": resolve_country(m.group(1)), "roll": None}
                continue
            m = RE_DIE.search(line)
            if m:
                roll = int(m.group(2))
                if self.cur["coup"] is not None and self.cur["coup"]["roll"] is None:
                    self.cur["coup"]["roll"] = roll
                elif self.realign is not None:
                    self.realign["rolls"][m.group(1)] = roll
                continue
            m = RE_COUP_RES.search(line)
            if m:
                continue
            m = RE_REALIGN.search(line)
            if m and self.mode == "realign":
                self.realign = {"country": resolve_country(m.group(1)), "rolls": {}}
                self.cur["realignments"].append(self.realign)
                continue
            if self.realign is not None and RE_REALIGN_RES.search(line):
                # Don't consume: the result line is a full influence line
                # ("Soviet influence in Mexico reduced by 3, now at 0") and
                # must flow into the board tracker below.
                self.realign = None
            m = RE_SPACE.search(line)
            if m and self.mode == "space":
                self.cur["space"] = {"roll": int(m.group(2)), "needed": int(m.group(1))}
                continue
            m = RE_SCORING.search(line)
            if m:
                self.mode = "event"
                continue
            m = RE_VP.search(line)
            if m:
                self.vp = int(m.group(3))
                if self.cur is not None:
                    self.cur["vp_after"] = self.vp
                continue
            m = RE_DEFCON.search(line)
            if m:
                continue
            m = RE_MILOPS.search(line)
            if m:
                continue
            m = RE_WAR_ROLL.search(line)
            if m:
                if self.cur is not None:
                    self.cur.setdefault("war", []).append(
                        {"roll": int(m.group(1)), "mod": int(m.group(2) or 0)}
                    )
                continue
            m = RE_NO_DISCARD.search(line)
            if m:
                if self.cur is not None:
                    self.cur.setdefault("choices", []).append("no_discard")
                continue
            if RE_BOYCOTT.search(line):
                if self.cur is not None:
                    self.cur.setdefault("choices", []).append("participate")
                continue
            m = RE_DIE.search(line)
            if m and self.mode == "event" and self.cur is not None:
                # Contest dice (Olympic Games, Summit): two dies under an
                # event-mode record with no coup/realignment context. A tie
                # reroll adds a further pair, hence a list of dicts.
                if self.cur["coup"] is None and not self.cur["realignments"]:
                    contests = self.cur.setdefault("contests", [])
                    side = "US" if m.group(1) == "USA" else "USSR"
                    if not contests or side in contests[-1]:
                        contests.append({})
                    contests[-1][side] = int(m.group(2))
                continue
            m = RE_CONTEST_DIE.search(line)
            if m and self.mode == "event" and self.cur is not None:
                contests = self.cur.setdefault("contests", [])
                side = "US" if m.group(1) == "American" else "USSR"
                if not contests or side in contests[-1]:
                    contests.append({})
                contests[-1][side] = int(m.group(2))
                continue

            m = RE_INF.search(line) or RE_INF_SET.search(line) or RE_INF_CARD.search(line) or None
            if m:
                g = m.groups()
                if len(g) == 7:  # RE_INF
                    n, side_tok, c1, c2, verb, amt, after = g
                    country = resolve_country(c1 or c2)
                    side = side_of(side_tok)
                    if verb:
                        delta = int(amt) if verb == "increased" else -int(amt)
                    else:
                        delta = int(n) if n else None
                    if country:
                        self._touch(country, side, int(after), delta)
                    else:
                        self.warn(f"unknown country: {line!r}")
                    continue
                elif len(g) == 3:  # RE_INF_SET
                    side_tok, cname, after = g
                    country = resolve_country(cname)
                    if country:
                        self._touch(country, side_of(side_tok), int(after), None)
                    else:
                        self.warn(f"unknown country: {line!r}")
                    continue
                # RE_INF_CARD: 3 groups (count, added|removed, country, after) = 4
                n, verb, cname, after = g
                country = resolve_country(cname)
                if country:
                    self._touch(
                        country, self.cur["side"], int(after),
                        int(n) if verb == "added to" else -int(n),
                    )
                else:
                    self.warn(f"unknown country: {line!r}")
                continue

        return {
            "source": self.source,
            "title": self.title,
            "format": "wgr",
            "include_optional": True,
            "winner": None,
            "vp_final": self.vp,
            "actions": self.actions,
            "warnings": self.warnings,
        }


# -- thread extraction ---------------------------------------------------------

def extract_thread(text: str, thread_id: int) -> tuple[str, list[str]]:
    lines = text.splitlines()
    start = end = None
    title = ""
    for i, line in enumerate(lines):
        m = RE_THREAD.match(line)
        if m and int(m.group(1)) == thread_id:
            start, title = i, m.group(2)
        elif start is not None and m:
            end = i
            break
    if start is None:
        raise SystemExit(f"thread {thread_id} not found")
    return title, lines[start:end if end else len(lines)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default="twilight_struggle_sessions.txt")
    ap.add_argument("--thread", type=int, required=True)
    ap.add_argument("--out", default=None, help="output dir (default: stdout)")
    args = ap.parse_args()

    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    title, lines = extract_thread(text, args.thread)
    game = WGRParser(args.thread, title).parse(lines)

    unresolved = sum(1 for a in game["actions"] if a["card"] is None)
    print(
        f"{game['source']}: {len(game['actions'])} actions, "
        f"{len(game['warnings'])} warnings, {unresolved} unresolved cards, "
        f"vp_final={game['vp_final']}",
        file=sys.stderr,
    )
    for w in game["warnings"][:10]:
        print(f"  warn: {w}", file=sys.stderr)
    payload = json.dumps(game, indent=1)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{game['source']}.json").write_text(payload)
        print(f"wrote {out / (game['source'] + '.json')}", file=sys.stderr)
    else:
        print(payload)


if __name__ == "__main__":
    main()
