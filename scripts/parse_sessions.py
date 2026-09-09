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

# Authors' card spellings that normalize differently from cards.json names.
for _alias, _cid in {
    "degaulle leads france": "De_Gaulle_Leads_France",
    "mideast scoring": "Middle_East_Scoring",
    "usa japan mutual defense pact": "US_Japan_Mutual_Defense_Pact",
    "u2 incident": "U2_Incident",
    "caputured nazi scientist": "Captured_Nazi_Scientist",
    "cambridge five": "The_Cambridge_Five",
}.items():
    NAME_MAP[_alias] = _cid
# "The X" card spellings: try both with and without the leading article.
_the_map = {k: v for k, v in NAME_MAP.items() if k.startswith("the ")}
for _k, _v in _the_map.items():
    NAME_MAP.setdefault(_k[4:], _v)
for _k, _v in list(NAME_MAP.items()):
    if not _k.startswith("the "):
        NAME_MAP.setdefault("the " + _k, _v)

# WGR / BGG display spellings -> engine country ids. Built from engine
# country names first (they already say "Spain/Portugal"), then a small
# manual layer for abbreviations seen in logs.
_COUNTRY_ALIAS = {
    "united kingdom": "UK",
    "w germany": "West_Germany",
    "w. germany": "West_Germany",
    "w.germany": "West_Germany",
    "west germany": "West_Germany",
    "e germany": "East_Germany",
    "e. germany": "East_Germany",
    "e.germany": "East_Germany",
    "east germany": "East_Germany",
    "s. korea": "South_Korea",
    "s. korean": "South_Korea",
    "s.korea": "South_Korea",
    "south korea": "South_Korea",
    "n. korea": "North_Korea",
    "n. korean": "North_Korea",
    "n.korea": "North_Korea",
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
    "venezula": "Venezuela",  # 211562's spelling
    "columbia": "Colombia",
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
    name = re.sub(r"\s+event\.?$", "", name, flags=re.I).strip()
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
RE_UN_INT_CANCEL = re.compile(
    r"They also play UN Intervention to cancel the (?:American|Soviet) event")
RE_ME_EXCHANGE = re.compile(
    r"The (?:American|Soviet|USA|USSR) exchanges? the following card for the (?:Missile Envy|.+?):\s*$")
RE_PLAY = re.compile(
    r"The (Soviets|Americans|American|Soviet player|US player|USSR player) "
    r"(?:play(?:s)? the following card (?P<i1>.+?)"
    r"|use(?:s)? (?P<uc>.+?) card (?P<i2>for a coup attempt|to place influence"
    r"|as an Event|for Ops|for an attempt on the Space Race track))"
    r":?\s*$"
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


RE_BBCODE = re.compile(r"\[/?(?:b|i|u|q|size=[^\]]*|color=[^\]]*|imageid=[^\]]*)\]")


def side_of(token: str) -> str:
    token = token.strip()
    if token in ("USA", "US", "American", "American player", "US player", "Americans"):
        return "US"
    return "USSR"


def _norm_player(token: str) -> str:
    token = token.strip()
    return "USSR" if token in ("Soviets", "Soviet", "Soviet player", "USSR player", "USSR") else "US"


class GameParserBase:
    """Shared parser state: board tracking from printed at-start influence
    so the parse validates itself (delta continuity) without the engine."""

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
        self.li = 0  # line index within the thread slice (diagnostics)
        self.exchanging: dict | None = None  # Missile-Envy hand-over target
        self.turn = 0
        self.ar: int | None = None
        self.pending_side: str | None = None
        self.discard_mode = False
        self.cur: dict | None = None      # action being built
        self.mode: str | None = None      # sub-mode of the play intro
        self.realign: dict | None = None
        self.headline_recs: dict[str, dict] = {}  # side -> its headline record
        self.action_phase = False
        self.resolving: str | None = None  # side whose headline event is resolving

    def warn(self, msg: str) -> None:
        self.warnings.append(f"turn {self.turn} line {self.li}: {msg}")

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
        raise NotImplementedError


class WGRParser(GameParserBase):
    """One WGR log -> ParsedGame. Parses the Wargameroom log grammar."""

    RE_HAND_HEADER = re.compile(r"Strategy Hand|^My hand:|^\s*Hand:\s*$")

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
            m = RE_UN_INT_CANCEL.search(line)
            if m and self.cur is not None:
                # "They also play UN Intervention to cancel the American
                # event": the play is our engine's un_intervention combo
                # (the card is used purely for its Ops, the event cancelled).
                self.cur["mode"] = "un_intervention"
                continue
            m = RE_ME_EXCHANGE.search(line)
            if m and self.cur is not None:
                # Missile Envy: "The American exchanges the following card
                # for the Missile Envy:" — the given card follows on the
                # next line; the engine asks the operator to name it.
                self.exchanging = self.cur
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
                intro = m.group("i1") or m.group("i2")
                # Headline resolution re-states the card ("The Soviets play
                # the following card as an Event:") — only trust it while the
                # engine-announced "X Headline Event:" is still the last word:
                # some logs (Ziemowit's) start action rounds without any
                # phase marker, and an AR play must never merge into the
                # headline record.
                hl = self.headline_recs.get(side)
                restated = (
                    resolve_card(m.group("uc"))
                    if m.group("uc") else None
                )
                if ("as an Event" in intro and self.resolving == side
                        and hl is not None and hl.get("card") is not None
                        and not hl.get("_resolved") and not hl.get("exchanged")):
                    hl["_resolved"] = True
                    self.resolving = None
                    self.cur = hl
                elif (restated is not None and self.cur is not None
                      and self.cur.get("side") == side
                      and self.cur.get("card") == restated):
                    pass  # the same card's play continues ("...card for a coup attempt:")
                elif (self.cur is not None and self.cur.get("side") == side
                      and not self.cur.get("placements")
                      and not self.cur.get("exchanged")):
                    pass  # continuation: this record's ops half is still pending
                elif self.cur is None or self.cur.get("side") != side or self.cur.get("card") is not None:
                    self.cur = self._new("play", side, self.turn)
                # else: continue the current record — an event-first play's
                # ops half, or a Defectors+UN-Intervention-style combo, both
                # restate "The X play the following card..." with no new AR
                # header. The placements belong to the card already playing.
                if m.group("uc") and self.cur is not None and self.cur["card"] is None:
                    # "The X use the <Name> card for a coup attempt:" names
                    # the card inline.
                    self.cur["card"] = resolve_card(m.group("uc"))
                    if self.cur["card"] is None:
                        self.warn(f"unresolved card: {m.group('uc')!r}")
                new_ops_type = None
                if "as an Event" in intro:
                    new_mode, self.mode = "event", "event"
                elif "place influence" in intro:
                    new_mode, self.mode, new_ops_type = "ops", "ops", "influence"
                elif "coup attempt" in intro:
                    new_mode, self.mode, new_ops_type = "ops", "coup", "coup"
                elif "realignment" in intro:
                    new_mode, self.mode, new_ops_type = "ops", "realign", "realignment"
                elif "Space Race" in intro:
                    new_mode, self.mode = "space_race", "space"
                else:  # "for Ops": event-first vs ops-only decided by later lines
                    new_mode, self.mode = "ops", "ops"
                if self.cur["mode"] != "un_intervention":
                    # The UN Intervention combo was flagged on an earlier
                    # "They also play UN Intervention..." line; later coup/ops
                    # intros must not reset it to plain ops.
                    self.cur["mode"] = new_mode
                if new_ops_type:
                    self.cur["ops_type"] = new_ops_type
                continue
            m = RE_CARD.match(stripped)
            if m and self.exchanging is not None:
                # The card the opponent handed over for Missile Envy.
                self.exchanging["exchanged"] = resolve_card(m.group(0))
                self.exchanging = None
                continue
            if m and self.cur is not None and self.cur["card"] is None:
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


class BareParser(GameParserBase):
    """The purpose-built 'bare bones' log format (bgg-211562).

    Grammar: TURN/HEADLINE PHASE/ACTION ROUND headers; card lines
    'USA: Play Card as Operations - #4: 3 / Duck and Cover (USA)'
    (headline cards omit the 'Play Card as' clause; '#49 - 2' dash
    numbering appears too); placements '+1 Austria (us,ussr)' with []
    marking control; removals '-2 USA Italy (us,ussr)'; absolute effect
    sets written as bare 'Country (us,ussr)' lines; coups 'Coup X (...'
    with the die math inline in the result tuple; realignments; space
    race as 'Discard to Space Race' + 'Die roll of N (fails to) attain';
    VP asserted per record by 'VP Track: N'.
    """

    RE_TURN = re.compile(r"^TURN (\d+)\s*$")
    RE_HEADLINE = re.compile(r"^TURN \d+: HEADLINE PHASE:")
    RE_AR = re.compile(r"^TURN \d+: ACTION ROUND (\d+):")
    RE_MILOPS = re.compile(r"CHECK REQUIRED MILITARY OPERATIONS:")
    RE_SETUP = re.compile(r"^(USA|USSR) Discretionary Influence:")
    RE_CARD = re.compile(
        r"^(USA?|USSR):\s*(?:(Play Card as (Operations|Event|Space Race)"
        r"|Discard (?:Card )?to Space Race)\s*[-–]?\s*)?"
        r"#(\d+)\s*[:\-]?\s*(\d+)\s*/\s*(.+?)\s*(?:\((USA|USSR|Both|US)\))?\s*$"
    )
    RE_DISCARD_SPACE = re.compile(
        r"^(USA?|USSR): Discard (?:Card )?to Space Race\s*[-–]?\s*"
        r"#(\d+)\s*[:\-]?\s*(\d+)\s*/\s*(.+?)\s*(?:\((USA|USSR|Both|US)\))?\s*$")
    RE_HAND_OPEN = re.compile(r"Hand Revealed")
    RE_HAND_CARD = re.compile(r"^\s*#\d+")
    RE_OPS_PREFIX = re.compile(r"^(USA?|USSR|CIA|Lone Gunman) OPs?:\s*(.*)$")
    _T = r"\((?:[\[\]0-9*]+)[/,](?:[\[\]0-9*]+)\)"
    RE_SIDE_PLACE = re.compile(rf"^([+-])(\d+) (USA|USSR) (.+?) ({_T})$")
    RE_PLACE = re.compile(rf"^([+-])(\d+) (.+?) ({_T})$")
    RE_SET = re.compile(rf"^(.+?) ({_T})$")
    RE_TUPLE = re.compile(r"\((\[?[0-9*]+\]?),(\[?[0-9*]+\]?)\)")
    RE_COUP_RES = re.compile(rf"^(.+?) ({_T}) \[Die roll (\d+)")
    RE_REALIGN = re.compile(rf"^Real(?:ignment|ingment) (.+?) ({_T})$")
    RE_REALIGN_ROLL = re.compile(r"^(USA|USSR) (?:Adjusted )?Die \[[^\]]*\]: (\d+)")
    RE_DISCARD = re.compile(
        r"^(USA|USSR):?\s+Discard(?:s| Card)?\s*(?:\([Pp]er [^)]*\)\s*)?[-–:]?\s*#\d+")
    RE_SPACE_ROLL = re.compile(r"^Die roll of (\d+) fa?i?l?e?[ds]? to attain")
    RE_EXCHANGE = re.compile(
        r"^(?:Exchanged [Cc]ard (?:- |is )|Passed Card: )"
        r"#\d+\s*[:\-]?\s*\d+\s*/\s*(.+?)\s*\(")
    RE_EXCHANGE_MODE = re.compile(r"(Played as Operations|plays as event|event is triggered)")
    RE_SKIP = re.compile(
        r"^(DEFCON:|China Card:|Space Race:|Net VP Change|No effect$|Cancels Event:|\["
        r"|USSR MILOPs|USA MILOPs|USSR: --|USA: --|Die roll of \d+ attains"
        r"|THREAD |Started: |\+1 Adjacent|No Military|NB:|Return Strategy card"
        r"|.* cancelled\.$|=|the China Card passed|.* no longer playable\.$"
        r"|.* may not place"
        r"|(USA|USSR|CIA) (Presence|Domination|Control|Event:?|[Nn]o |attains|Lunar Probe)"
        r"|(USA|USSR): (Presence|Domination|Control|No cards|Earth|Animal|Lunar Probe)"
        r"|(USA|USSR) controls)"
    )
    RE_VP = re.compile(r"^VP Track: ([+-]?\d+)")

    def _parse_tuple(self, m: re.Match) -> tuple[int, int]:
        def v(s: str) -> int:
            s = s.strip("[]*")
            if "/" in s:  # "(1/1)" writes both sides as a fraction
                return int(s.split("/")[0])
            return int(s)
        return v(m.group(1)), v(m.group(2))

    def _apply_effect(
        self, country: str, after: tuple[int, int] | None = None,
        delta: int | None = None, delta_side: str | None = None,
    ) -> None:
        if not self.cur or country is None:
            return
        if delta is not None:
            side = delta_side or self.cur["side"]
            if self.cur["kind"] == "setup":
                self.cur["placements"].append([country, delta])
            self._touch(country, side, self.board.get(country, {}).get(side, 0) + delta, delta)
            return
        # Absolute set line: each side whose board value changed is the
        # side being set (this also splits two-sided sets like Fidel).
        for i, side in enumerate(("US", "USSR")):
            before = self.board.get(country, {}).get(side)
            if before is not None and before != after[i]:
                self._touch(country, side, after[i], after[i] - before)

    def _eff_rec(self) -> dict | None:
        """The record effects attach to: the current one, or the last record
        created (AR/turn headers clear `cur` but a roll can still belong to
        the previous play, e.g. Bear Trap's escape roll in the next AR)."""
        if self.cur is not None:
            return self.cur
        for rec in reversed(self.actions):
            if rec["kind"] != "setup":
                return rec
        return None

    def parse(self, lines: list[str]) -> dict:
        in_hand = False
        section = None  # None|'setup'|'headline'|'ar'
        seen_log = False
        for raw in lines:
            self.li += 1
            line = RE_BBCODE.sub("", raw.rstrip("\n"))
            stripped = line.strip()
            if self.discard_mode:
                if not stripped:
                    continue
                bm = re.match(
                    r"^#(\d+)\s*[:\-]?\s*(\d+)\s*/\s*(.+?)\s*(?:\((USA|USSR|Both|US)\))?\s*$",
                    stripped)
                if bm:
                    cid = resolve_card(bm.group(3))
                    if cid and self.cur is not None:
                        self.cur["discards"].append(cid)
                    continue
                self.discard_mode = False
            if not stripped or stripped.startswith("URL:") or stripped.startswith("---"):
                # The bare log lives entirely in the first post; the reply
                # posts quote log lines out of context and only add noise.
                if seen_log and stripped.startswith("--- post"):
                    break
                if not stripped:
                    continue
                if stripped.startswith("--- post"):
                    seen_log = True
                continue
            if in_hand:
                if self.RE_HAND_CARD.match(stripped):
                    continue
                in_hand = False
            if self.RE_HAND_OPEN.search(stripped):
                in_hand = True
                continue

            m = self.RE_TURN.match(stripped)
            if m:
                self.turn = int(m.group(1))
                section, self.cur = None, None
                continue
            if self.RE_HEADLINE.match(stripped):
                section, self.cur = "headline", None
                continue
            m = self.RE_AR.match(stripped)
            if m:
                self.ar = int(m.group(1))
                section, self.cur = "ar", None
                continue
            if self.RE_MILOPS.search(stripped):
                section, self.cur = None, None
                continue
            if stripped.startswith("SETUP"):
                section = "setup"
                continue

            if self.RE_SETUP.match(stripped):
                self.cur = self._new("setup", side_of(stripped.split()[0]), 0)
                continue

            # Trailing bracketed annotations never carry parse meaning for
            # placement/set lines; strip once so tail patterns match.
            base = re.sub(r"\s*\[[^]]*\]\s*$", "", stripped).rstrip()
            pm = re.match(r"^[\w .']*placement: (.*)$", base)
            if pm:
                base = pm.group(1).strip()

            m = re.match(r"^(USA|USSR) Dis(?:c)?ards? ?\[.*\]:?\s*$", stripped)
            if m:
                self.discard_mode = True
                continue

            m = self.RE_CARD.match(stripped)
            if m:
                who = side_of(m.group(1))
                card = resolve_card(m.group(6))
                if card is None:
                    self.warn(f"unresolved card: {m.group(6)!r}")
                if section == "headline":
                    rec = self._new("headline", who, self.turn)
                    self.cur = rec
                    self.headline_recs[who] = rec
                else:
                    mode_word = m.group(3)
                    mode = {"Operations": "ops", "Event": "event"}.get(
                        mode_word, "space_race" if mode_word == "Space Race" else "event"
                    )
                    rec = self._new("play", who, self.turn, self.ar)
                    self.cur = rec
                    rec["mode"] = mode
                if card:
                    self.cur["card"] = card
                continue

            m = self.RE_OPS_PREFIX.match(stripped)
            if m and self.cur is not None:
                side = {"CIA": "US", "Lone Gunman": "US"}.get(
                    m.group(1), side_of(m.group(1)))
                rest = m.group(2).strip()
                cm = re.match(rf"Coup (.+?) ({self._T})$", rest)
                if cm:
                    country = resolve_country(cm.group(1))
                    self.cur["ops_type"] = "coup"
                    self.cur["coup"] = {"country": country, "roll": None}
                    self.pending_side = None
                elif rest:
                    pm = re.match(rf"[+-](\d+) (.+?) ({self._T})$", rest)
                    if pm and pm.group(2):
                        self.cur["ops_type"] = "influence"
                        country = resolve_country(pm.group(2))
                        self._apply_effect(country, delta=int(pm.group(1)), delta_side=side)
                    self.pending_side = None
                else:
                    self.pending_side = side  # payload on following lines
                continue

            m = self.RE_SIDE_PLACE.match(base)
            if m:
                country = resolve_country(m.group(4))
                self._apply_effect(
                    country, delta=int(m.group(2)) * (-1 if m.group(1) == "-" else 1),
                    delta_side=side_of(m.group(3)),
                )
                continue
            m = self.RE_PLACE.match(base)
            if m:
                country = resolve_country(m.group(3))
                self._apply_effect(
                    country, delta=int(m.group(2)) * (-1 if m.group(1) == "-" else 1),
                    delta_side=self.pending_side or (self.cur["side"] if self.cur else None),
                )
                self.pending_side = None
                continue
            m = self.RE_COUP_RES.match(stripped)
            if m and self.cur is not None and self.cur.get("coup"):
                self.cur["coup"]["roll"] = int(m.group(3))
                tm = self.RE_TUPLE.search(m.group(2))
                if tm:
                    self._apply_effect(resolve_country(m.group(1)), after=self._parse_tuple(tm))
                continue
            m = self.RE_REALIGN.match(stripped)
            if m and self.cur is not None:
                self.cur["ops_type"] = "realignment"
                self.cur["realignments"].append(
                    {"country": resolve_country(m.group(1)), "rolls": {}}
                )
                tm = self.RE_TUPLE.search(m.group(2))
                if tm:
                    self._apply_effect(resolve_country(m.group(1)), after=self._parse_tuple(tm))
                continue
            m = self.RE_REALIGN_ROLL.match(stripped)
            if m and self.cur is not None and self.cur["realignments"]:
                self.cur["realignments"][-1]["rolls"][side_of(m.group(1))] = int(m.group(2))
                continue
            m = self.RE_DISCARD.match(stripped)
            if m:
                rec = self._eff_rec()
                nm = re.search(r"#\d+\s*[:\-]?\s*\d+\s*/\s*(.+?)\s*\(", stripped)
                if nm and rec is not None:
                    cid = resolve_card(nm.group(1))
                    if cid:
                        rec["discards"].append(cid)
                continue
            m = self.RE_SPACE_ROLL.match(stripped)
            if m:
                rec = self._eff_rec()
                if rec is not None:
                    rec["space"] = {"roll": int(m.group(1))}
                continue
            m = re.match(r"^Die roll of (\d+) - \d+", stripped)
            if m:
                rec = self._eff_rec()
                if rec is not None:
                    rec.setdefault("war", []).append({"roll": int(m.group(1))})
                continue
            m = re.match(r"^War fails \[die roll of (\d+)", stripped)
            if m:
                rec = self._eff_rec()
                if rec is not None:
                    rec.setdefault("war", []).append({"roll": int(m.group(1))})
                continue
            m = re.match(r"^Die roll of (\d+) succeeds in ending", stripped)
            if m:
                rec = self._eff_rec()
                if rec is not None:
                    rec.setdefault("quagmire_rolls", []).append(int(m.group(1)))
                continue
            m = re.match(r"^Target (.+?) \[Die roll of (\d+)", stripped)
            if m:
                rec = self._eff_rec()
                if rec is not None:
                    rec["ops_type"] = "coup"
                    rec["coup"] = {"country": resolve_country(m.group(1)), "roll": int(m.group(2))}
                continue
            m = self.RE_EXCHANGE.match(stripped)
            if m:
                # Missile-Envy/Grain-Sales style card transfer: the taker
                # plays the received card as a normal later action.
                cid = resolve_card(m.group(1))
                sm = re.search(r"which (USA|USSR) plays", stripped)
                side = side_of(sm.group(1)) if sm else (
                    self.cur["side"] if self.cur else "US")
                mode = ("event" if re.search(
                    r"plays as event|event is triggered", stripped) else "ops")
                rec = self._new("play", side, self.turn)
                self.cur = rec
                rec["mode"] = mode
                if cid:
                    rec["card"] = cid
                continue
            m = re.match(r"^(Brush War|Indo-Pakistani War|Iran-Iraq War) (.+?)\s*$", stripped)
            if m and self.cur is not None:
                # A declared-target war event: the engine asks WAR_TARGET.
                self.cur["coup"] = {"country": resolve_country(m.group(2)), "roll": None}
                continue
            m = re.match(rf"^Coup (.+?)(?: ({self._T}))?(?: \[.*)?$", stripped)
            if m and self.cur is not None:
                rec = self.cur
                rec["ops_type"] = "coup"
                rec["coup"] = {"country": resolve_country(m.group(1)), "roll": None}
                if m.group(2):
                    tm = self.RE_TUPLE.search(m.group(2))
                    if tm:
                        self._apply_effect(
                            resolve_country(m.group(1)), after=self._parse_tuple(tm))
                continue
            m = self.RE_VP.match(stripped)
            if m:
                self.vp = int(m.group(1))
                if self.cur is not None:
                    self.cur["vp_after"] = self.vp
                continue
            m = re.match(r"^(USA?|USSR) Event:?(.*)$", stripped)
            if m:
                payload = m.group(2).strip()
                em = re.match(rf"(.+?) ({self._T})$", payload)
                if em and em.group(1):
                    tm = self.RE_TUPLE.search(em.group(2))
                    if tm:
                        self._apply_effect(
                            resolve_country(em.group(1)), after=self._parse_tuple(tm)
                        )
                continue
            if self.RE_SKIP.match(stripped):
                continue

            m = self.RE_SET.match(base)
            if m and self.cur is not None:
                country = resolve_country(m.group(1))
                tm = self.RE_TUPLE.search(m.group(2))
                if country and tm:
                    self._apply_effect(country, after=self._parse_tuple(tm))
                    continue
            self.warn(f"unparsed: {stripped!r}")

        return {
            "source": self.source,
            "title": self.title,
            "format": "bare",
            "include_optional": True,
            "winner": None,
            "vp_final": self.vp,
            "actions": self.actions,
            "warnings": self.warnings,
        }


class PlaydekParser(GameParserBase):
    """The Playdek-app log grammar (the 'Ruskie steamroller' tetralogy,
    bgg-1443421/1445959/1454510/1455504, and bgg-1477640).

    Machine text lives inside [q] blocks; everything else is author prose.
    Every influence line is ABSOLUTE ('US Influence in Italy increased
    from 0 to 3' => set to 3); turn anchors are
    '*** US player updated turn to Turn N Round M (Side)'; card plays are
    '*** USSR plays Warsaw Pact Formed* for 3 Ops.' (headline picks before
    the first anchor, and scoring/ops/event suffixes otherwise)."""

    RE_TURN_MARK = re.compile(
        r"\*{2,3} (USA?|USSR) player updated turn to Turn (\d+) (?:Round (\d+) \((USA?|USSR)\)"
        r"|Headline Phase)")
    RE_HEADLINE_MARK = re.compile(r"\*\*\* (USA?|USSR) plays headline card\.?$")
    RE_CARD = re.compile(
        r"\*\*\* (USA?|USSR) plays (.+?)(?:\*)? "
        r"(for (\d+) Ops?\.?|Event\.?|in the Space Race\.?)?\s*$")
    RE_SCORES = re.compile(r"\*\*\* (USA?|USSR) scores (.+?)\.?$")
    REGION_CARDS = {
        "middle east": "Middle_East_Scoring", "europe": "Europe_Scoring",
        "asia": "Asia_Scoring", "africa": "Africa_Scoring",
        "central america": "Central_America_Scoring", "south america": "South_America_Scoring",
        "southeast asia": "Southeast_Asia_Scoring",
    }
    RE_INF = re.compile(
        r"^(USA?|USSR) Influence in (.+?) (?:increased|decreased|reduced)"
        r" (?:from -?\d+ )?to (-?\d+)")
    RE_REMOVED_ALL = re.compile(r"^All (USA?|USSR) Influence \(\d+\) removed from (.+?)\.?$")
    RE_DEFCON = re.compile(r"^DEFCON (?:increased|decreased) to (\d+)")
    RE_MILOPS = re.compile(r"^(USA?|USSR) Military Ops (?:increased|decreased) to (\d+)")
    RE_MILOPS_NOCHG = re.compile(r"^No change in (USA?|USSR) Military Ops")
    RE_ROLL = re.compile(r"^(USA?|USSR) rolls an? (\d+)")
    RE_COUP = re.compile(r"^\* (USA?|USSR) attempts Coup in (.+?) with (\d+) Ops?")
    RE_REALIGN = re.compile(r"^\* (USA?|USSR) attempts Realignment(?: Roll)? in (.+?)\.?$")
    RE_INVADING = re.compile(r"^(.+?) invades (.+?)\.?$")
    RE_WAR_WIN = re.compile(r"^\S+ wins? (.+?)[.*]?$")
    RE_DISCARD_CARD = re.compile(r"^(USA?|USSR)(?: player)? discards? (.+?)[*.]*\.?$")
    RE_SPACE = re.compile(r"^(USA?|USSR) Space Race advanced to (.+?)\.?$")
    RE_SPACE_ATTEMPT = re.compile(
        r"^(USA?|USSR) (?:plays .+ for Ops to Space Race|attempts Space Race)")
    RE_VP = re.compile(r"^(USA?|USSR) VPs increased by (\d+)")
    RE_PENALTY = re.compile(r"^(USA?|USSR) penalized (\d+) VPs?")
    RE_SKIP = re.compile(
        r"^- Twilight Struggle|^(USA?|USSR) (?:has (?:Domination|Presence|Control)"
        r"|controls \d+ Battleground|player has no Scoring|does not control)"
        r"|^(USA?|USSR) VPs? [^.]*for \d+ VPs|Coup and Realignment attempts"
        r"|no longer permitted|^(USA?|USSR) recieves|^.*(Scoring|scored)"
        r"|^(USA?|USSR) die-roll modifier|\. 0 die-roll modifier"
        r"|^\d+ \+ \d+ Ops|Coup Attempt is|^Coup Attempt|^\d+ \+ \d+ Ops"
        r"|^All cards played|^\+\d+ if all Ops|^(USA?|USSR) controls .*die-roll"
        r"|^(USA?|USSR) now controls|^Already no|may not longer|did not perform enough"
        r"|^\* Discards shuffled|^Cards dealt|already has|is canceled|^!!!"
        r"|hand is revealed|has the following cards|Space Race Cards per turn"
        r"|adds \+\d+ [Tt]o|adds \+\d+ Op|has more influence|^\d+ \+ \d+ Op "
        r"|^Modifier to|^(USA?|USSR) controls [A-Z]|has (?:no )?(?:Domination|Presence|Control)"
        r"|to add \d+ Influence|^Draw: |^No change in VPs|receives The China Card"
        r"|event now playable|die-roll modifier|reclaims|do not affect DEFCON"
        r"|^\* UNDO|scores null|Event is cancelled|must discard a \d+\+? Op card"
        r"|^\* Late-War Cards|less than \d+ Influence|no longer playable"
        r"|Realignment rolls are|may make a second Coup|Scandal is in effect")

    def _set(self, country: str, side: str, value: int) -> None:
        if self.cur is None or country is None:
            return
        before = self.board.get(country, {}).get(side)
        if before is not None:
            self._touch(country, side, value, value - before)

    def parse(self, lines: list[str]) -> dict:
        in_q = False
        in_headline = False  # plays before the first turn anchor are headlines
        for raw in lines:
            self.li += 1
            if "[q]" in raw:
                in_q = True
            if "[/q]" in raw:
                in_q = False
            if "[size=9]" in raw:
                in_q = True
            if "[/size]" in raw:
                in_q = False
                continue
            if not in_q:
                continue
            line = RE_BBCODE.sub("", raw.rstrip("\n"))
            stripped = line.strip()
            if not stripped or stripped.startswith("URL:") or stripped.startswith("---"):
                continue

            m = self.RE_TURN_MARK.match(stripped)
            if m:
                self.turn = int(m.group(2))
                in_headline = m.group(3) is None
                self.cur = None
                if not in_headline:
                    self.ar = int(m.group(3))
                    self.cur = self._new("play", side_of(m.group(4)), self.turn, self.ar)
                continue
            if self.RE_HEADLINE_MARK.match(stripped):
                in_headline = True
                self.cur = None
                continue

            m = self.RE_CARD.match(stripped)
            if m:
                who = side_of(m.group(1))
                card = resolve_card(m.group(2))
                if card is None and m.group(2).strip().lower() != "card":
                    self.warn(f"unresolved card: {m.group(2)!r}")
                if in_headline or self.cur is None:
                    rec = self._new("headline", who, self.turn)
                    self.cur = rec
                    self.headline_recs[who] = rec
                    in_headline = True
                if card:
                    self.cur["card"] = card
                if m.group(4):
                    self.cur["mode"] = "ops"
                elif (m.group(3) or "").startswith("Event"):
                    self.cur["mode"] = "event"
                elif (m.group(3) or "").startswith("in the Space Race"):
                    self.cur["mode"] = "space_race"
                continue

            m = self.RE_SCORES.match(stripped)
            if m:
                # "scores null" is the app's rendering of Southeast Asia Scoring.
                region = m.group(2).strip().lower()
                if region == "null":
                    card = "Southeast_Asia_Scoring"
                else:
                    card = self.REGION_CARDS.get(region)
                rec = self._new("play", side_of(m.group(1)), self.turn, self.ar)
                self.cur = rec
                rec["mode"] = "event"
                if card:
                    rec["card"] = card
                else:
                    self.warn(f"unresolved scoring region: {m.group(2)!r}")
                continue

            m = self.RE_COUP.match(stripped)
            if m and self.cur is not None:
                self.cur["ops_type"] = "coup"
                self.cur["coup"] = {"country": resolve_country(m.group(2)), "roll": None}
                continue
            m = self.RE_REALIGN.match(stripped)
            if m and self.cur is not None:
                self.cur["ops_type"] = "realignment"
                self.cur["realignments"].append(
                    {"country": resolve_country(m.group(2)), "rolls": {}})
                continue
            m = self.RE_ROLL.match(stripped)
            if m and self.cur is not None:
                rec = self.cur
                if rec.get("coup") and rec["coup"]["roll"] is None:
                    rec["coup"]["roll"] = int(m.group(2))
                elif rec["realignments"]:
                    rec["realignments"][-1]["rolls"][side_of(m.group(1))] = int(m.group(2))
                elif rec.get("war"):
                    rec["war"][-1]["roll"] = int(m.group(2))  # placeholder, set below
                else:
                    rec.setdefault("war", []).append({"roll": int(m.group(2))})
                continue
            m = self.RE_INVADING.match(stripped)
            if m and self.cur is not None:
                # A war event's target: the engine asks WAR_TARGET for
                # declared-target wars; record it the same way coups are.
                self.cur["ops_type"] = None
                self.cur["coup"] = {"country": resolve_country(m.group(2)), "roll": None}
                self.cur.setdefault("war", []).append({"roll": None})
                continue
            m = self.RE_WAR_WIN.match(stripped)
            if m and self.cur is not None:
                continue  # victory text only; the roll and VP deltas suffice
            m = self.RE_SPACE_ATTEMPT.match(stripped)
            if m and self.cur is not None:
                self.cur["mode"] = "space_race"
                continue
            m = self.RE_SPACE.match(stripped)
            if m:
                continue  # the engine advances space itself
            m = self.RE_INF.match(stripped)
            if m:
                side, country, value = (
                    side_of(m.group(1)), resolve_country(m.group(2)), int(m.group(3)))
                if self.turn == 0 and (self.cur is None or self.cur["kind"] == "headline"):
                    # Pre-anchor setup lines: track the board, and record the
                    # discretionary delta for the engine's setup decision.
                    delta = value - self.board.get(country, {}).get(side, 0)
                    if delta > 0:
                        if not self.actions or self.actions[-1]["kind"] != "setup" \
                                or self.actions[-1]["side"] != side:
                            self.cur = self._new("setup", side, 0)
                        self.cur["placements"].append([country, delta])
                    self._touch(country, side, value, value - self.board.get(country, {}).get(side, 0))
                    continue
                self._set(country, side, value)
                continue
            m = self.RE_REMOVED_ALL.match(stripped)
            if m:
                self._set(resolve_country(m.group(2)), side_of(m.group(1)), 0)
                continue
            m = self.RE_VP.match(stripped)
            if m:
                delta = int(m.group(2)) * (1 if side_of(m.group(1)) == "US" else -1)
                self.vp = (self.vp or 0) + delta
                if self.cur is not None:
                    self.cur["vp_after"] = self.vp
                continue
            m = self.RE_PENALTY.match(stripped)
            if m:
                delta = int(m.group(2)) * (1 if side_of(m.group(1)) == "US" else -1)
                self.vp = (self.vp or 0) + delta
                if self.cur is not None:
                    self.cur["vp_after"] = self.vp
                continue
            m = self.RE_DISCARD_CARD.match(stripped)
            if m and self.cur is not None:
                for name in re.split(r", | and | \+ ", m.group(2)):
                    cid = resolve_card(name.strip(' "'))
                    if cid:
                        self.cur["discards"].append(cid)
                continue
            if self.RE_SKIP.search(stripped) or self.RE_DEFCON.match(stripped) \
                    or self.RE_MILOPS.match(stripped) or self.RE_MILOPS_NOCHG.match(stripped):
                continue
            self.warn(f"unparsed: {stripped!r}")

        return {
            "source": self.source,
            "title": self.title,
            "format": "playdek",
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
    ap.add_argument("--thread", type=int, nargs="+", required=True)
    ap.add_argument("--out", default=None, help="output dir (default: stdout)")
    ap.add_argument("--format", default="auto", choices=["auto", "wgr", "bare", "playdek"])
    args = ap.parse_args()

    text = Path(args.file).read_text(encoding="utf-8", errors="replace")
    parts = [extract_thread(text, t) for t in args.thread]
    title = parts[0][0]
    lines = [ln for _, ls in parts for ln in ls]
    if args.format == "auto":
        joined = "\n".join(lines)
        if "Discretionary Influence" in joined:
            fmt = "bare"
        elif "player updated turn to" in joined:
            fmt = "playdek"
        else:
            fmt = "wgr"
    else:
        fmt = args.format
    parser = {"bare": BareParser, "playdek": PlaydekParser}.get(fmt, WGRParser)
    game = parser(args.thread[0], title).parse(lines)

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
