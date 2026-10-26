"""The deep bot's boundary save: pure translation, no DeepStruggle build needed.

The engine round trip (load the save, replay a round, compare) is
`scripts/deep_sync_census.py`; the test at the end runs a small census when a
DeepStruggle build is available and skips otherwise.
"""
import os

import pytest

from struggler.bots.deep import boundary
from struggler.bots.deep.ids import DS_US, DS_USSR, IdMap
from struggler.engine import Engine

# DeepStruggle's country names: struggler's ids with spaces, plus its two spellings.
_RENAME = {"UK": "United Kingdom", "Dominican_Republic": "Dominican Rep"}


def _ids(engine: Engine) -> IdMap:
    names = [_RENAME.get(c, c.replace("_", " ")) for c in engine.board.countries if c != "Chinese_Civil_War"]
    return IdMap.build(names, list(engine.board.countries))


def _at_first_action_round(seed: int = 3) -> Engine:
    engine = Engine.new_game(seed=seed, include_ccw=False, setup_us_extra=2)
    while engine.phase != "action_rounds":
        engine.step(engine.pending_decision.options[0])
    return engine


def test_every_country_and_card_maps_both_ways():
    engine = Engine.new_game(seed=1, include_ccw=False)
    ids = _ids(engine)
    assert len(ids.country_to_ds) == 84
    assert ids.country_to_ds["UK"] == list(engine.board.countries).index("UK")
    assert len(ids.card_to_ds) == 110 and ids.card_to_ds["The_China_Card"] == boundary.CHINA_CARD
    assert ids.card_from_ds[boundary.MISSILE_ENVY] == "Missile_Envy"


def test_action_round_save_holds_one_select_card_frame_for_the_phasing_side():
    engine = _at_first_action_round()
    ids = _ids(engine)
    save = boundary.to_save(boundary.from_engine(engine), ids)
    assert save["ctx_stack"] == [{"decision_player": DS_USSR, "decision_type": 1}]
    assert save["current_phase"] == 2 and save["action_round"] == engine.action_round
    assert save["victory_points"] == engine.vp and save["defcon"] == engine.defcon
    n = ids.country_to_ds["Poland"]
    assert save["ussr_influence"][n] == engine.board.influence["Poland"]["USSR"]


def test_card_locations_follow_the_piles_and_the_china_card_is_by_holder():
    engine = _at_first_action_round()
    ids = _ids(engine)
    locs = boundary.card_locations(boundary.from_engine(engine), ids)
    for cid in engine.hands["US"]:
        if cid != "The_China_Card":
            assert locs[ids.card_to_ds[cid]] == boundary.LOC_HAND_US
    for cid in engine.draw_pile:
        assert locs[ids.card_to_ds[cid]] == boundary.LOC_DRAW
    assert locs[boundary.CHINA_CARD] == boundary.LOC_ONGOING
    save = boundary.to_save(boundary.from_engine(engine), ids)
    assert save["china_card_holder"] == (DS_US if engine.china_card_owner == "US" else DS_USSR)


def test_effects_map_to_deepstruggle_bits_with_the_sides_turned_the_right_way():
    engine = _at_first_action_round()
    facts = boundary.from_engine(engine)
    facts.turn_effects = {"cuban_missile_crisis": "USSR", "red_scare": "US", "chernobyl": "Asia"}
    facts.game_effects = {"willy_brandt": True, "marshall_or_warsaw": True}
    facts.space_race_attempts = {"US": 1, "USSR": 0}
    bits = boundary.effect_bits(facts)
    on = lambda name: bool(bits >> boundary._BIT[name] & 1)  # noqa: E731
    assert on("CMC_ACTIVE_US")          # the USSR's coups lose: the US played it
    assert on("PURGE_US_ACTIVE")        # Red Scare/Purge costs the US an Op
    assert on("CHERNOBYL_ACTIVE") and (bits >> 40 & 7) == 1  # Asia
    assert on("WILLY_BRANDT_PLAYED") and on("NATO_CANCELED_WEST_GERMANY")
    assert on("MARSHALL_PLAN_PLAYED") and on("SPACE_US_ATTEMPT_1") and not on("SPACE_US_ATTEMPT_2")


def test_an_unknown_effect_is_refused_rather_than_dropped():
    facts = boundary.from_engine(_at_first_action_round())
    facts.turn_effects = {"something_new": True}
    with pytest.raises(boundary.UnmappedEffect):
        boundary.effect_bits(facts)


@pytest.mark.skipif(not os.environ.get("STRUGGLER_DEEP_ENGINE"),
                    reason="needs a DeepStruggle build (STRUGGLER_DEEP_ENGINE)")
def test_sync_census_agrees_on_most_rounds():
    import collections
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import deep_sync_census as census
    from struggler.bots.deep.backend import NativeBackend

    backend = NativeBackend()
    ids = IdMap.build(backend.country_names(), list(Engine.new_game(seed=0, include_ccw=False).board.countries))
    reasons, examples, totals = collections.Counter(), {}, collections.Counter()
    for seed in (1, 2, 3):
        census.run_game(seed, True, ids, backend, reasons, examples, totals)
    assert totals["agree"] / totals["rounds"] > 0.85, reasons
