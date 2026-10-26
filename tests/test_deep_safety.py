"""The deep player takes an outright win the network would decline.

The stand-in model is random, so taking Wargames at a winning lead can only
come from the forced-win check (`bots/deep/safety.py`), not from the network.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

from struggler.bots.deep.player import DeepPlayer, OnnxPolicy  # noqa: E402
from struggler.bots.greedy import GreedyPlayer  # noqa: E402
from struggler.engine import Engine, Side  # noqa: E402
from struggler.engine.types import DecisionKind  # noqa: E402
from test_deep_player import _backend  # noqa: E402


def _us_to_play_wargames(vp: int) -> Engine:
    """A turn-8 US action round with Wargames in hand at DEFCON 2."""
    engine = Engine.new_game(seed=11, include_ccw=False, setup_us_extra=2)
    greedy = GreedyPlayer()
    while not (engine.turn >= 8 and engine.phase == "action_rounds"
               and engine.pending_decision.kind is DecisionKind.ACTION_ROUND_PLAY
               and engine.pending_decision.actor is Side.US and len(engine._decision_stack) == 1):
        d = engine.pending_decision
        engine.step(d.options[0] if d.actor is Side.CHANCE else greedy.choose_action(engine.observe(d.actor), []))
        assert not engine.is_terminal, "the setup game ended before turn 8"
    data = engine.serialize()
    for pile in ("draw_pile", "discard_pile", "removed_cards"):
        data[pile] = [c for c in data[pile] if c != "Wargames"]
    data["hands"]["USSR"] = [c for c in data["hands"]["USSR"] if c != "Wargames"]
    data["hands"]["US"] = ["Wargames"] + [c for c in data["hands"]["US"] if c != "Wargames"][:3]
    data["defcon"], data["vp"] = 2, vp
    top = data["decision_stack"][-1]
    top["options"] = [{"kind": "action_round_play", "payload": {"card": c}} for c in data["hands"]["US"]]
    return Engine.deserialize(data)


def _play_us_round(engine: Engine, deep: DeepPlayer) -> None:
    from struggler.engine.replay import HistoryBuilder

    deep.bind_engine(engine)
    history = HistoryBuilder()
    while not engine.is_terminal and engine.pending_decision.actor in (Side.US, Side.CHANCE):
        d = engine.pending_decision
        action = d.options[0] if d.actor is Side.CHANCE else \
            deep.choose_action(engine.observe(Side.US), history.history)
        engine.step(action)
        history.record(d, action, engine)


@pytest.fixture(scope="module")
def policy(tmp_path_factory):
    from deep_fixtures import write_random_policy

    return OnnxPolicy(str(write_random_policy(tmp_path_factory.mktemp("m") / "random.onnx", seed=3)))


def test_wargames_at_a_winning_lead_is_taken(policy):
    engine = _us_to_play_wargames(vp=10)
    deep = DeepPlayer(policy=policy, engine=_backend(), seed=1)
    _play_us_round(engine, deep)
    assert engine.is_terminal and engine.winner is Side.US, (engine.vp, deep.stats)
    assert deep.stats["forced_win"] >= 1


def test_wargames_is_not_forced_when_it_would_lose(policy):
    engine = _us_to_play_wargames(vp=4)  # +4 then -6 VP: Wargames would hand the USSR the game
    deep = DeepPlayer(policy=policy, engine=_backend(), seed=1)
    _play_us_round(engine, deep)
    assert deep.stats["forced_win"] == 0
