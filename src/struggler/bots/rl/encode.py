"""Fixed-size vector encodings of an `Observation` and its legal `Action`s.

Everything is derived from the player-scoped `Observation` only (mandate #4),
so a policy trained on these vectors can never see the opponent's hand. The
state is the acting side's view; the same physical position yields different
vectors for US and USSR, which is what lets one shared net play both seats of
a zero-sum game.

Dimensions are module constants (`STATE_DIM`, `OPTION_DIM`) so the net can be
built without a sample call.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from struggler.engine import DecisionKind, Observation, Side
from struggler.engine.board import Board
from struggler.engine.cards import load_cards
from struggler.engine.types import Region


def _tables():
    board = Board()  # one-time read of countries.json
    countries = tuple(sorted(board.countries))
    stability = {c: board.countries[c].stability for c in countries}
    battleground = {c: board.countries[c].battleground for c in countries}
    region = {c: board.countries[c].region for c in countries}
    return countries, stability, battleground, region


COUNTRIES, STABILITY, BATTLEGROUND, REGION = _tables()
CARD_IDS = tuple(sorted(load_cards()))
REGIONS = tuple(Region)
KINDS = tuple(DecisionKind)
PHASES = ("setup", "headline", "action_rounds", "predeal", "complete", "idle")
MODES = ("ops", "event", "space_race", "un_intervention")
TYPES = ("influence", "coup", "realignment")
ORDERS = ("event_first", "ops_first")
SENTINELS = (
    "none", "stop", "done", "skip", "refuse", "decline", "participate",
    "boycott", "end_game", "raise", "lower", "take", "return", "keep",
    "remove", "south_africa_only", "and_adjacent",
)

N, C, K, R, P = len(COUNTRIES), len(CARD_IDS), len(KINDS), len(REGIONS), len(PHASES)
# 4 per country (own/opp influence, own/opp control), 4 per region
# (own/opp battlegrounds, own/opp countries), 20 scalars, own hand, kind, phase.
_STATE_SCALARS = 20
STATE_DIM = 4 * N + 4 * R + _STATE_SCALARS + C + K + P
OPTION_DIM = K + N + C + len(MODES) + len(TYPES) + len(ORDERS) + (len(SENTINELS) + 1) + 1

_COUNTRY_IX = {c: i for i, c in enumerate(COUNTRIES)}
_CARD_IX = {c: i for i, c in enumerate(CARD_IDS)}
_KIND_IX = {k.value: i for i, k in enumerate(KINDS)}
_PHASE_IX = {p: i for i, p in enumerate(PHASES)}
_BG_TOTAL = {r: sum(BATTLEGROUND[c] for c in COUNTRIES if REGION[c] is r) for r in REGIONS}
_REGION_COUNTRIES = {r: tuple(c for c in COUNTRIES if REGION[c] is r) for r in REGIONS}


def _control(inf: Mapping[str, Mapping[str, int]], cid: str) -> str | None:
    us, ussr, stab = inf[cid]["US"], inf[cid]["USSR"], STABILITY[cid]
    if us - ussr >= stab:
        return "US"
    if ussr - us >= stab:
        return "USSR"
    return None


def state_vector(obs: Observation) -> list[float]:
    v = [0.0] * STATE_DIM
    i = 0
    inf = obs.influence

    for cid in COUNTRIES:  # influence
        v[i] = min(inf[cid]["US"], 12) / 12.0
        v[i + 1] = min(inf[cid]["USSR"], 12) / 12.0
        i += 2
    for cid in COUNTRIES:  # control
        owner = _control(inf, cid)
        v[i] = 1.0 if owner == "US" else 0.0
        v[i + 1] = 1.0 if owner == "USSR" else 0.0
        i += 2
    for r in REGIONS:  # regional control counts
        cids = _REGION_COUNTRIES[r]
        own_bg = opp_bg = own = opp = 0
        for c in cids:
            owner = _control(inf, c)
            if owner == "US":
                own += 1
                own_bg += BATTLEGROUND[c]
            elif owner == "USSR":
                opp += 1
                opp_bg += BATTLEGROUND[c]
        v[i] = own_bg / max(1, _BG_TOTAL[r])
        v[i + 1] = opp_bg / max(1, _BG_TOTAL[r])
        v[i + 2] = own / max(1, len(cids))
        v[i + 3] = opp / max(1, len(cids))
        i += 4

    dec = obs.pending_decision
    ctx = dict(dec.context) if dec is not None else {}
    ops = ctx.get("ops", ctx.get("ops_remaining"))
    remain = ctx.get("remaining")
    scalars = (
        obs.vp / 20.0,
        obs.defcon / 5.0,
        obs.turn / 10.0,
        obs.action_round / 8.0,
        obs.military_ops.get("US", 0) / 5.0,
        obs.military_ops.get("USSR", 0) / 5.0,
        obs.space_race.get("US", 0) / 8.0,
        obs.space_race.get("USSR", 0) / 8.0,
        min(obs.space_race_attempts.get("US", 0), 3) / 3.0,
        min(obs.space_race_attempts.get("USSR", 0), 3) / 3.0,
        1.0 if obs.china_card_owner is Side.US else 0.0,
        1.0 if obs.china_card_owner is Side.USSR else 0.0,
        1.0 if obs.china_card_available else 0.0,
        min(obs.opponent_hand_size, 12) / 12.0,
        min(obs.draw_pile_size, 110) / 110.0,
        (ops or 0) / 12.0,
        (remain or 0) / 12.0,
        1.0 if ctx.get("setup") else 0.0,
        1.0 if ctx.get("bonus") else 0.0,
        len(obs.hand) / 12.0,
    )
    v[i : i + _STATE_SCALARS] = scalars
    i += _STATE_SCALARS

    for cid in obs.hand:  # own hand (multi-hot)
        j = _CARD_IX.get(cid)
        if j is not None:
            v[i + j] = 1.0
    i += C
    if dec is not None:
        j = _KIND_IX.get(dec.kind.value)
        if j is not None:
            v[i + j] = 1.0
    i += K
    j = _PHASE_IX.get(obs.phase)
    if j is not None:
        v[i + j] = 1.0
    i += P
    assert i == STATE_DIM, (i, STATE_DIM)
    return v


def option_vector(action: Any) -> list[float]:
    v = [0.0] * OPTION_DIM
    i = 0
    v[_KIND_IX.get(action.kind.value, 0)] = 1.0
    i += K
    p = dict(action.payload or {})
    country = p.get("country")
    if country not in _COUNTRY_IX and p.get("choice") in _COUNTRY_IX:
        country = p["choice"]
    if country in _COUNTRY_IX:
        v[i + _COUNTRY_IX[country]] = 1.0
    i += N
    card = p.get("card")
    if card in _CARD_IX:
        v[i + _CARD_IX[card]] = 1.0
    i += C
    for block, value in ((MODES, p.get("mode")), (TYPES, p.get("type")), (ORDERS, p.get("order"))):
        if value in block:
            v[i + block.index(value)] = 1.0
        i += len(block)
    choice = p.get("choice")
    if choice in SENTINELS:
        v[i + SENTINELS.index(choice)] = 1.0
    elif choice is not None and choice not in _COUNTRY_IX:
        v[i + len(SENTINELS)] = 1.0  # "other"
    i += len(SENTINELS) + 1
    v[i] = min(float(p.get("ops", 0)), 12) / 12.0
    return v
