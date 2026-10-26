"""DeepPlayer's plumbing with a random stand-in model: every action legal, the
network actually consulted, fallbacks counted. Strength is the arena's job."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

from struggler.bots.deep import backend as B  # noqa: E402
from struggler.bots.deep.player import DeepPlayer, OnnxPolicy  # noqa: E402
from struggler.bots.greedy import GreedyPlayer  # noqa: E402
from struggler.engine import Engine, Side  # noqa: E402
from struggler.runner import play_game  # noqa: E402


def _backend():
    for env, make in (("STRUGGLER_DEEP_WASM", B.WasmBackend), ("STRUGGLER_DEEP_SHARED_LIB", B.SharedLibBackend),
                      ("STRUGGLER_DEEP_ENGINE", lambda p: B.NativeBackend())):
        path = os.environ.get(env)
        if path and (env == "STRUGGLER_DEEP_ENGINE" or Path(path).exists()):
            return make(path)
    pytest.skip("no DeepStruggle engine build")


@pytest.fixture(scope="module")
def policy(tmp_path_factory):
    from deep_fixtures import write_random_policy

    return OnnxPolicy(str(write_random_policy(tmp_path_factory.mktemp("m") / "random.onnx")))


@pytest.mark.parametrize("deep_side", [Side.US, Side.USSR])
def test_deep_player_finishes_a_game_with_legal_moves_and_uses_the_network(policy, deep_side):
    engine = Engine.new_game(seed=5, include_ccw=False, setup_us_extra=2)
    deep = DeepPlayer(policy=policy, engine=_backend(), seed=1)
    players = {deep_side: deep, deep_side.opponent: GreedyPlayer()}
    play_game(engine, players)  # play_game refuses an action not in the options
    assert engine.is_terminal
    total = sum(deep.stats.values())
    assert deep.stats["network"] / total > 0.5, deep.stats


def test_deep_player_refuses_unsupported_games(policy):
    deep = DeepPlayer(policy=policy, engine=_backend())
    with pytest.raises(RuntimeError):
        deep.bind_engine(Engine.new_game(seed=1))                      # Chinese Civil War on
    with pytest.raises(RuntimeError):
        deep.bind_engine(Engine.new_game(seed=1, include_ccw=False, events=False))
