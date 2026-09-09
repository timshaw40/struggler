"""Tests for the BGG-sessions log pipeline: engine replay mode, the WGR
parser, and the replay driver."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.engine import Engine, Side  # noqa: E402
from struggler.engine.core import HIDDEN_CARD  # noqa: E402


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def parse_sessions():
    return _load("parse_sessions")


@pytest.fixture(scope="module")
def replay_game():
    return _load("replay_game")


@pytest.fixture(scope="module")
def fit_weights():
    return _load("fit_weights")


# -- engine replay mode --------------------------------------------------------


def test_replay_mode_hides_both_hands() -> None:
    engine = Engine.new_game(seed=1, replay_mode=True)
    for side in ("US", "USSR"):
        assert HIDDEN_CARD in engine.hands[side]
    assert engine.replay_mode and engine.physical_mode
    assert engine.physical_side is None


def test_replay_mode_declares_card_on_play() -> None:
    engine = Engine.new_game(seed=1, replay_mode=True)
    # Drive the opening setup with plausible placements (6 USSR EE / 7 US WE),
    # then play one headline card per side, declared from hidden_pool.
    def place(side: str, subregion: set[str], n: int) -> None:
        while n:
            dec = engine.pending_decision
            assert dec is not None and dec.context.get("setup")
            cid = next(c for c in dec.options[0].payload.values() if c in subregion)
            engine.step(next(a for a in dec.options if a.payload["country"] == cid))
            n -= 1

    from struggler.engine.types import Subregion
    ee = {c for c in engine.board.countries if Subregion.EASTERN_EUROPE in engine.board.countries[c].subregions}
    we = {c for c in engine.board.countries if Subregion.WESTERN_EUROPE in engine.board.countries[c].subregions}
    place("USSR", ee, 6)
    place("US", we, 7)
    dec = engine.pending_decision
    assert dec.kind.value == "headline_play"
    # Any declared card is legal: the engine can't know the hand's contents.
    action = dec.options[0]
    cid = action.payload["card"]
    assert cid in engine.hidden_pool
    engine.step(action)
    assert cid not in engine.hidden_pool


# -- parser --------------------------------------------------------------------

MINI_LOG = """
************************************************************
 **              The  deck is being shuffled.              **
 ************************************************************
       3 USSR influence added to Poland, now at 3
       2 USSR influence added to Austria, now at 2
       1 USSR influence added to Finland, now at 2
       3 USA influence added to West Germany, now at 3
       2 USA influence added to Italy, now at 2
       1 USA influence added to Greece, now at 1
       1 USA influence added to Spain/Portugal, now at 1
       1 USA extra influence added to Iran, now at 2
 ** Turn 1 Headline Phase **
 Soviet Headline Card: #7  Ops 3: Socialist Governments (USSR)
 American Headline Card: #23  Ops 4: Marshall Plan * (USA)

 USA Headline Event: #23  Ops 4: Marshall Plan * (USA)

 The Americans play the following card as an Event:
   #23  Ops 4: Marshall Plan * (USA)
       American influence in United Kingdom increased by 1, now at 6
       American influence in West Germany increased by 1, now at 4
       American influence in France increased by 1, now at 1
       American influence in Spain/Portugal increased by 1, now at 2
       American influence in Italy increased by 1, now at 3
       American influence in Turkey increased by 1, now at 1
       American influence in Greece increased by 1, now at 2

 USSR Headline Event: #7  Ops 3: Socialist Governments (USSR)

 The Soviets play the following card as an Event:
   #7  Ops 3: Socialist Governments (USSR)
       American influence in West Germany reduced by 2, now at 2
       American influence in Italy reduced by 1, now at 2
 ** Turn 1 Action Phase **

  Turn 1, USSR action round 1

 The Soviets play the following card for a coup attempt:
   #34  Ops 4: Nuclear Test Ban
     Coup attempt in Iran (stability 2):
       ** USSR die roll = 1 (+4) = 5
     The modified roll exceeds the doubled stability by 1.
       American influence in Iran reduced by 1, now at 1
 DEFCON Level lowered to 4
       Soviet Military Operations for this turn increased to 4

  Turn 1, USA action round 1

 The Americans play the following card to place influence:
   #4  Ops 3: Duck and Cover (USA)
       2 USA influence added to West Germany, now at 4
       1 USA influence added to Lebanon, now at 1
"""


def test_wgr_parser_mini_log(parse_sessions) -> None:
    p = parse_sessions.WGRParser(1, "mini")
    game = p.parse(MINI_LOG.splitlines())
    assert game["warnings"] == []
    kinds = [(a["kind"], a["side"], a["card"]) for a in game["actions"]]
    assert kinds[0][0] == "setup" and kinds[1][0] == "setup"
    # setup: standard placements + the tournament extra separated out
    assert game["actions"][0]["placements"] == [["Poland", 3], ["Austria", 2], ["Finland", 1]]
    assert game["actions"][1]["extras"] == [["Iran", 1]]
    assert kinds[2] == ("headline", "USSR", "Socialist_Governments")
    assert kinds[3] == ("headline", "US", "Marshall_Plan")
    marshall = game["actions"][3]
    assert [p[:2] for p in marshall["placements"]] == [
        ["UK", "US"], ["West_Germany", "US"], ["France", "US"],
        ["Spain_Portugal", "US"], ["Italy", "US"], ["Turkey", "US"], ["Greece", "US"],
    ]
    sg = game["actions"][2]
    assert sg["removals"] == [["West_Germany", "US", 2], ["Italy", "US", 1]]
    coup = game["actions"][4]
    assert coup["mode"] == "ops" and coup["ops_type"] == "coup"
    assert coup["coup"] == {"country": "Iran", "roll": 1}
    assert coup["post"] == [["Iran", "US", 1]]
    assert game["vp_final"] is None


# -- replay driver ---------------------------------------------------------------


def test_replay_driver_opening_moves(parse_sessions, replay_game) -> None:
    """The mini-log's opening replays with the exact recorded board."""
    p = parse_sessions.WGRParser(1, "mini")
    game = p.parse(MINI_LOG.splitlines())
    r = replay_game.Replay(game)
    r.run(max_steps=40)
    board = r.engine.board.influence
    assert board["Poland"]["USSR"] == 3
    assert board["West_Germany"]["US"] == 4
    assert board["Iran"]["US"] == 1  # printed 1 + extra 1, then coup -1
    assert r.engine.vp == 0
    assert r.errors == []

def test_weight_fitter_scores_and_improves(parse_sessions, fit_weights) -> None:
    """agreement() scores a candidate; sweeps never lose the baseline."""
    p = parse_sessions.WGRParser(1, "mini")
    games = [p.parse(MINI_LOG.splitlines())]
    base_score, n = fit_weights.agreement(games, fit_weights.GreedyWeights())
    assert 0.0 <= base_score <= 1.0 and n > 0
    # Evaluating twice must give the same number: the driver deep-copies,
    # so no evaluation can consume the next one's states.
    again, n2 = fit_weights.agreement(games, fit_weights.GreedyWeights())
    assert again == base_score and n2 == n

    params, score, history = fit_weights.fit(
        games, minutes=0.05, max_sweeps=2, log=lambda _msg: None
    )
    assert score >= base_score - 1e-9
    assert history[0][1] == base_score
    for value in params.values():
        assert value > 0

def test_selfplay_pair_cancels_side(parse_sessions) -> None:
    """Identical weights on a paired seed must split 1-1: the seat swap is
    the only difference, so any other result means the pair is biased."""
    spec = importlib.util.spec_from_file_location(
        "tsp", ROOT / "scripts" / "tune_selfplay.py"
    )
    import importlib.util as _ilu
    tsp = _ilu.module_from_spec(spec)
    _ilu.sys.modules["tsp"] = tsp
    spec.loader.exec_module(tsp)
    w = tsp.GreedyWeights()
    wins, total = tsp._play_pair((50_000, w, w))
    assert (wins, total) == (1, 2)
    jittered = tsp.perturb(w, tsp.random.Random(1), 0.3)
    assert jittered.defcon_self_kill_penalty == w.defcon_self_kill_penalty
    assert all(getattr(jittered, f) > 0 for f in tsp.TUNABLE)
