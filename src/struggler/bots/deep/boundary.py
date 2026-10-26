"""A struggler position at a clean boundary, written as a DeepStruggle save.

A *clean boundary* is the start of a headline phase or of an action round:
the moment DeepStruggle's decision stack holds a single "pick a card" frame
and nothing is mid-resolution. Everything after it in the same round is
replayed micro-action by micro-action (`replay.py`), so the mid-card stack,
the hardest part of DeepStruggle's state to write by hand, is never written.

`PositionFacts` is what a boundary needs. `from_engine` reads it from a
struggler `Engine` (the sync census, which may see everything); the player
builds it from `observe(side)` with the unknown cards determinized, so the
bot never sees more than its seat (mandate #4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from struggler.bots.deep import backend as B
from struggler.bots.deep.ids import DS_REGIONS, DS_US, DS_USSR, IdMap, ds_side

# `ts::CardLocation`
LOC_UNAVAILABLE, LOC_DRAW, LOC_HAND_US, LOC_HAND_US_KNOWN, LOC_HAND_USSR, LOC_HAND_USSR_KNOWN, \
    LOC_DISCARD, LOC_REMOVED, LOC_ONGOING = range(9)

CHINA_CARD = 6
SHUTTLE_DIPLOMACY = 74
MISSILE_ENVY = 49

# `ts::effect_bits` (DeepStruggle engine/include/ts/game_state.hpp).
_BIT = {
    "NATO_ACTIVE": 0, "NATO_CANCELED_FRANCE": 1, "NATO_CANCELED_WEST_GERMANY": 2,
    "MARSHALL_PLAN_PLAYED": 3, "WARSAW_PACT_PLAYED": 4, "US_JAPAN_PACT_ACTIVE": 5,
    "CONTAINMENT_ACTIVE": 6, "PURGE_US_ACTIVE": 7, "PURGE_USSR_ACTIVE": 8,
    "VIETNAM_REVOLTS_ACTIVE": 9, "FORMOSAN_RESOLUTION_ACTIVE": 10, "CMC_ACTIVE_US": 11,
    "CMC_ACTIVE_USSR": 12, "NUCLEAR_SUBS_ACTIVE": 13, "QUAGMIRE_ACTIVE": 14,
    "BEAR_TRAP_ACTIVE": 15, "SALT_ACTIVE": 16, "WE_WILL_BURY_YOU_PENDING": 17,
    "BREZHNEV_DOCTRINE_ACTIVE": 18, "FLOWER_POWER_ACTIVE": 19, "U2_INCIDENT_ACTIVE": 20,
    "SHUTTLE_DIPLOMACY_ACTIVE": 21, "DEATH_SQUADS_US": 22, "DEATH_SQUADS_USSR": 23,
    "CAMP_DAVID_PLAYED": 24, "IRON_LADY_PLAYED": 25, "NORTH_SEA_OIL_PLAYED": 26,
    "NORTH_SEA_OIL_ACTIVE": 27, "THE_REFORMER_PLAYED": 28, "IRAN_CONTRA_ACTIVE": 29,
    "EVIL_EMPIRE_PLAYED": 30, "ALDRICH_AMES_ACTIVE": 31, "JOHN_PAUL_II_PLAYED": 32,
    "NORAD_ACTIVE": 33, "YURI_AND_SAMANTHA_ACTIVE": 34, "AWACS_PLAYED": 35,
    "IRANIAN_HOSTAGE_CRISIS_PLAY": 36, "WILLY_BRANDT_PLAYED": 37, "TEAR_DOWN_THIS_WALL_PLAYED": 38,
    "CHERNOBYL_ACTIVE": 39, "SPACE_US_ATTEMPT_1": 43, "SPACE_US_ATTEMPT_2": 44,
    "SPACE_USSR_ATTEMPT_1": 45, "SPACE_USSR_ATTEMPT_2": 46,
}
_CHERNOBYL_REGION_SHIFT = 40

#: struggler effect key -> DeepStruggle bit, for plain on/off effects.
_FLAG_EFFECTS = {
    "nato": "NATO_ACTIVE", "degaulle_france": "NATO_CANCELED_FRANCE",
    "us_japan_pact": "US_JAPAN_PACT_ACTIVE", "containment": "CONTAINMENT_ACTIVE",
    "vietnam_revolts": "VIETNAM_REVOLTS_ACTIVE", "formosan_resolution": "FORMOSAN_RESOLUTION_ACTIVE",
    "nuclear_subs": "NUCLEAR_SUBS_ACTIVE", "quagmire": "QUAGMIRE_ACTIVE",
    "bear_trap": "BEAR_TRAP_ACTIVE", "salt": "SALT_ACTIVE",
    "we_will_bury_you": "WE_WILL_BURY_YOU_PENDING", "brezhnev": "BREZHNEV_DOCTRINE_ACTIVE",
    "flower_power": "FLOWER_POWER_ACTIVE", "u2_incident": "U2_INCIDENT_ACTIVE",
    "shuttle_diplomacy": "SHUTTLE_DIPLOMACY_ACTIVE", "camp_david": "CAMP_DAVID_PLAYED",
    "iron_lady": "IRON_LADY_PLAYED", "north_sea_oil": "NORTH_SEA_OIL_PLAYED",
    "north_sea_oil_extra": "NORTH_SEA_OIL_ACTIVE", "reformer": "THE_REFORMER_PLAYED",
    "iran_contra": "IRAN_CONTRA_ACTIVE", "evil_empire": "EVIL_EMPIRE_PLAYED",
    "john_paul": "JOHN_PAUL_II_PLAYED", "norad": "NORAD_ACTIVE",
    "yuri_samantha": "YURI_AND_SAMANTHA_ACTIVE", "awacs": "AWACS_PLAYED",
    "iranian_hostage": "IRANIAN_HOSTAGE_CRISIS_PLAY", "tear_down_wall": "TEAR_DOWN_THIS_WALL_PLAYED",
}

#: Effect keys handled specially below, or with no DeepStruggle state of their
#: own (space-race box perks are read from the track; Missile Envy's forced play
#: becomes `forced_card_*`).
_SPECIAL_EFFECTS = {
    "marshall_or_warsaw", "willy_brandt", "cuban_missile_crisis", "red_scare",
    "la_death_squads", "chernobyl", "missile_envy_forced", "space_race_discard_holder",
    "space_race_double_attempt_holder", "space_race_extra_round_holder",
    "space_race_headline_reveal_holder",
}


class UnmappedEffect(ValueError):
    """A struggler effect key the boundary cannot express. The census counts
    these; the player falls back to greedy."""


@dataclass
class PositionFacts:
    """Everything a boundary save needs, in struggler's own terms."""

    phase: str                                  # "headline" | "action_rounds"
    turn: int
    action_round: int
    phasing: str | None                         # "US" | "USSR" at an action round
    first_headline_picker: str                  # who picks first at a headline
    defcon: int
    vp: int
    military_ops: Mapping[str, int]
    space_race: Mapping[str, int]
    space_race_attempts: Mapping[str, int]
    influence: Mapping[str, Mapping[str, int]]
    china_card_owner: str
    china_card_available: bool
    hands: Mapping[str, list[str]]              # determinized for the opponent
    draw_pile: list[str]                        # determinized for the player
    discard_pile: list[str]
    removed_cards: list[str]
    in_play_cards: list[str]
    turn_effects: Mapping[str, Any] = field(default_factory=dict)
    game_effects: Mapping[str, Any] = field(default_factory=dict)


def from_engine(engine: Any) -> PositionFacts:
    """Read the facts off a struggler `Engine` (full information)."""
    # The side struggler asks first at a headline picks first in the save too,
    # so the bot's own pick is asked in the same order in both engines.
    pending = engine.pending_decision
    first = pending.actor.value if (engine.phase == "headline" and pending is not None
                                    and pending.actor.value in ("US", "USSR")) else "USSR"
    return PositionFacts(
        phase=engine.phase,
        turn=engine.turn,
        action_round=engine.action_round,
        phasing=engine.phasing_side.value if engine.phasing_side is not None else None,
        first_headline_picker=first,
        defcon=engine.defcon,
        vp=engine.vp,
        military_ops=dict(engine.military_ops),
        space_race=dict(engine.space_race),
        space_race_attempts=dict(engine.space_race_attempts),
        influence={c: dict(v) for c, v in engine.board.influence.items()},
        china_card_owner=engine.china_card_owner,
        china_card_available=engine.china_card_available,
        hands={s: list(cards) for s, cards in engine.hands.items()},
        draw_pile=list(engine.draw_pile),
        discard_pile=list(engine.discard_pile),
        removed_cards=list(engine.removed_cards),
        in_play_cards=list(engine.in_play_cards),
        turn_effects=dict(engine.turn_effects),
        game_effects=dict(engine.game_effects),
    )


def effect_bits(facts: PositionFacts) -> int:
    """struggler's `turn_effects` + `game_effects` as DeepStruggle's 64-bit mask."""
    effects = {**facts.game_effects, **facts.turn_effects}
    bits = 0

    def on(name: str) -> None:
        nonlocal bits
        bits |= 1 << _BIT[name]

    for key, value in effects.items():
        if not value:
            continue
        if key in _FLAG_EFFECTS:
            on(_FLAG_EFFECTS[key])
        elif key == "marshall_or_warsaw":
            on("MARSHALL_PLAN_PLAYED")  # either one enables NATO, all DeepStruggle reads
        elif key == "willy_brandt":
            on("WILLY_BRANDT_PLAYED")
            if not effects.get("tear_down_wall"):
                on("NATO_CANCELED_WEST_GERMANY")
        elif key == "cuban_missile_crisis":
            # struggler stores the side whose coups now lose; DeepStruggle the side that played it.
            on("CMC_ACTIVE_US" if value == "USSR" else "CMC_ACTIVE_USSR")
        elif key == "red_scare":
            on("PURGE_US_ACTIVE" if value == "US" else "PURGE_USSR_ACTIVE")
        elif key == "la_death_squads":
            on("DEATH_SQUADS_US" if value == "US" else "DEATH_SQUADS_USSR")
        elif key == "chernobyl":
            on("CHERNOBYL_ACTIVE")
            bits |= DS_REGIONS.index(str(value).upper().replace(" ", "_")) << _CHERNOBYL_REGION_SHIFT
        elif key in _SPECIAL_EFFECTS:
            continue
        else:
            raise UnmappedEffect(key)
    for side, names in (("US", ("SPACE_US_ATTEMPT_1", "SPACE_US_ATTEMPT_2")),
                        ("USSR", ("SPACE_USSR_ATTEMPT_1", "SPACE_USSR_ATTEMPT_2"))):
        for name in names[: facts.space_race_attempts.get(side, 0)]:
            on(name)
    return bits


def card_locations(facts: PositionFacts, ids: IdMap) -> list[int]:
    locs = [LOC_UNAVAILABLE] * 111
    piles = (
        (facts.draw_pile, LOC_DRAW),
        (facts.discard_pile, LOC_DISCARD),
        (facts.removed_cards, LOC_REMOVED),
        (facts.in_play_cards, LOC_REMOVED),
        (facts.hands.get("US", []), LOC_HAND_US),
        (facts.hands.get("USSR", []), LOC_HAND_USSR),
    )
    for cards, loc in piles:
        for cid in cards:
            n = ids.card_to_ds[cid]
            if n != CHINA_CARD:
                locs[n] = loc
    if SHUTTLE_DIPLOMACY_ACTIVE(facts):
        locs[SHUTTLE_DIPLOMACY] = LOC_ONGOING
    locs[CHINA_CARD] = LOC_ONGOING  # DeepStruggle tracks it by holder, never by location
    return locs


def SHUTTLE_DIPLOMACY_ACTIVE(facts: PositionFacts) -> bool:  # noqa: N802 - mirrors the flag
    return bool(facts.game_effects.get("shuttle_diplomacy"))


def to_save(facts: PositionFacts, ids: IdMap, rng_state: int = 0x9E3779B97F4A7C15) -> dict:
    """The `ts_save_v2` dict for a boundary position."""
    if facts.phase == "headline":
        phase, picker, ar = B.PHASE_HEADLINE, ds_side(facts.first_headline_picker), 0
    elif facts.phase == "action_rounds":
        phase, picker, ar = B.PHASE_ACTION_ROUND, ds_side(facts.phasing), facts.action_round
    else:
        raise ValueError(f"no boundary in phase {facts.phase!r}")
    us_inf = [0] * 84
    ussr_inf = [0] * 84
    for cid, inf in facts.influence.items():
        n = ids.country_to_ds.get(cid)
        if n is None:
            if inf.get("US") or inf.get("USSR"):
                raise ValueError(f"{cid} has influence but no DeepStruggle country")
            continue
        us_inf[n], ussr_inf[n] = int(inf.get("US", 0)), int(inf.get("USSR", 0))
    forced = facts.game_effects.get("missile_envy_forced")
    return {
        "format": "ts_save_v2",
        "victory_points": facts.vp,
        "defcon": facts.defcon,
        "turn": facts.turn,
        "action_round": ar,
        "us_mil_ops": facts.military_ops.get("US", 0),
        "ussr_mil_ops": facts.military_ops.get("USSR", 0),
        "us_space_track": facts.space_race.get("US", 0),
        "ussr_space_track": facts.space_race.get("USSR", 0),
        "phasing_player": picker,
        "current_phase": phase,
        "headline_us_card": 0, "headline_ussr_card": 0,
        "headline_first_card": 0, "headline_second_card": 0, "headline_stage": 0,
        "headline_first_owner": 0, "headline_second_owner": 0,
        "forced_card_player": ds_side(forced) if forced else 0,
        "forced_card_id": MISSILE_ENVY if forced else 0,
        "defcon_dropped_to_2": 0,
        "china_card_holder": DS_US if facts.china_card_owner == "US" else DS_USSR,
        "china_card_playable": int(bool(facts.china_card_available)),
        "persistent_effects": effect_bits(facts),
        "rng_state": rng_state,
        "last_die_roll": 0, "last_opp_die_roll": 0,
        "ctx_stack": [{"decision_player": picker, "decision_type": B.SELECT_CARD}],
        "ctx_stack_depth": 0,
        "card_locations": card_locations(facts, ids),
        "us_influence": us_inf,
        "ussr_influence": ussr_inf,
    }
