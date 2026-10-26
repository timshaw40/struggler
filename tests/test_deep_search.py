"""The deep bot's rollout search (`bots/deep/search.py`): the spec parser, the
backends agreeing on a search, and DeepPlayer using the search's pick only
where it overrides the network. Strength is the arena's job
(`scripts/deep_gate.py --search on --opponent deep`)."""
import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from struggler.bots.deep import backend as B  # noqa: E402
from struggler.bots.deep.search import RolloutConfig, SearchResult  # noqa: E402

WASM = os.environ.get("STRUGGLER_DEEP_WASM")
SHARED = os.environ.get("STRUGGLER_DEEP_SHARED_LIB")
NATIVE = os.environ.get("STRUGGLER_DEEP_ENGINE")


def _builds():
    out = []
    if WASM and Path(WASM).exists():
        out.append(("wasm", lambda: B.WasmBackend(WASM)))
    if SHARED and Path(SHARED).exists():
        out.append(("shared", lambda: B.SharedLibBackend(SHARED)))
    if NATIVE:
        out.append(("native", lambda: B.NativeBackend()))
    return out


def test_rollout_spec_parses_deepstruggles_measured_player_and_rejects_nonsense():
    assert RolloutConfig.parse("off") is None and RolloutConfig.parse("") is None
    assert RolloutConfig.parse("on") == RolloutConfig(4, 16, 4, "z2")
    assert RolloutConfig.parse("4:16:4:z2") == RolloutConfig()
    assert RolloutConfig.parse("3:8") == RolloutConfig(3, 8, 4, "z2")
    assert RolloutConfig.parse("4:16:0:argmax").rule == "argmax"
    assert RolloutConfig.parse(RolloutConfig(2, 4, 1, "kl0.5").spec()) == RolloutConfig(2, 4, 1, "kl0.5")
    for bad in ("1:16", "4:0", "4:16:-1", "4:16:4:best", "4:16:4:z2:x"):
        with pytest.raises(ValueError):
            RolloutConfig.parse(bad)


# -- with an engine build ------------------------------------------------------


@pytest.fixture(scope="module")
def policy(tmp_path_factory):
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from deep_fixtures import write_random_policy
    from struggler.bots.deep.player import OnnxPolicy

    return OnnxPolicy(str(write_random_policy(tmp_path_factory.mktemp("m") / "random.onnx")))


def _action_round_position(engine: B.DeepEngine, seed: int) -> dict:
    """Play random legal moves from a new game to an action-round card choice."""
    rng = random.Random(seed)
    for _ in range(2000):
        player, dtype = engine.decision()
        if dtype == B.ROLL_DIE:
            assert engine.roll(rng.randint(1, 6), rng.randint(1, 6))
            continue
        if dtype == B.SELECT_CARD and player != 0 and engine.save()["current_phase"] == B.PHASE_ACTION_ROUND:
            if sum(engine.mask()) >= 3:
                return engine.save()
        legal = [i for i, v in enumerate(engine.mask()) if v]
        assert engine.step_flat(rng.choice(legal))
    raise AssertionError("no action-round card choice reached")


@pytest.mark.parametrize("name,make", _builds() or [pytest.param("none", None, marks=pytest.mark.skip("no build"))])
def test_search_runs_and_leaves_the_position_alone(policy, name, make):
    from struggler.bots.deep.search import search

    engine = make()
    if not engine.supports_rollout:
        pytest.skip(f"{name} build predates the rollout calls")
    position = _action_round_position(engine, seed=3)
    player, _ = engine.decision()
    logits = policy.logits(engine.observation(player, policy.obs_features), list(engine.mask()))
    result = search(engine, policy, logits, seed=7, config=RolloutConfig(3, 4, 1, "argmax"))
    assert result is not None and result.worlds > 0 and result.rows > 0
    assert len(result.candidates) == 3 and result.pick in result.candidates
    assert engine.mask()[result.pick]
    assert result.candidates[0] == max(result.candidates, key=lambda a: logits[a])  # the network's move first
    assert engine.save() == position


def test_every_build_makes_the_same_search():
    builds = _builds()
    if len(builds) < 2:
        pytest.skip("needs two engine builds to compare")
    pytest.importorskip("onnxruntime")
    from deep_fixtures import write_random_policy
    from struggler.bots.deep.player import OnnxPolicy
    from struggler.bots.deep.search import search
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        policy = OnnxPolicy(str(write_random_policy(Path(d) / "r.onnx")))
        engines = [make() for _, make in builds]
        engines = [e for e in engines if e.supports_rollout]
        if len(engines) < 2:
            pytest.skip("fewer than two builds with the rollout calls")
        position = _action_round_position(engines[0], seed=11)
        results = []
        for e in engines:
            e.load_save(position)
            player, _ = e.decision()
            logits = policy.logits(e.observation(player, policy.obs_features), list(e.mask()))
            r = search(e, policy, logits, seed=99, config=RolloutConfig(3, 4, 2, "argmax"))
            results.append((r.pick, r.candidates, r.worlds, [round(q, 4) for q in r.q]))
        assert all(r == results[0] for r in results), results


# -- DeepPlayer's use of the result (no engine needed) --------------------------


class _StubEngine:
    supports_rollout = True


def _player(monkeypatch, result):
    from struggler.bots.deep import player as P

    monkeypatch.setattr(P, "run_search", lambda *a, **k: result)
    return P.DeepPlayer(policy=object(), engine=_StubEngine(), search="on")


def test_player_takes_the_search_pick_only_when_it_overrides_the_network(monkeypatch):
    scores = {0: [10], 1: [11, 12], 2: [13]}
    overriding = SearchResult(pick=12, candidates=[10, 12, 13], worlds=16)
    assert _player(monkeypatch, overriding)._searched([0.0] * 220, scores) == 1
    agreeing = SearchResult(pick=10, candidates=[10, 12, 13], worlds=16)
    deep = _player(monkeypatch, agreeing)
    assert deep._searched([0.0] * 220, scores) is None  # keep the network's own (logsumexp) choice
    unmapped = SearchResult(pick=99, candidates=[10, 99], worlds=16)
    deep = _player(monkeypatch, unmapped)
    assert deep._searched([0.0] * 220, scores) is None and deep.stats["search:unmapped"] == 1
    deep = _player(monkeypatch, None)
    assert deep._searched([0.0] * 220, scores) is None and deep.stats["search:skipped"] == 1


def test_player_refuses_search_on_an_engine_without_it():
    from struggler.bots.deep.player import DeepPlayer

    class Old:
        supports_rollout = False

    with pytest.raises(ValueError, match="install_deep"):
        DeepPlayer(policy=object(), engine=Old(), search="on")
    assert DeepPlayer(policy=object(), engine=Old(), search="off").search is None


def test_deep_player_with_search_finishes_a_game(policy):
    from struggler.bots.deep.player import DeepPlayer
    from struggler.bots.greedy import GreedyPlayer
    from struggler.engine import Engine, Side
    from struggler.runner import play_game

    builds = [b for b in _builds()]
    if not builds:
        pytest.skip("no DeepStruggle engine build")
    engine = builds[0][1]()
    if not engine.supports_rollout:
        pytest.skip("build predates the rollout calls")
    game = Engine.new_game(seed=8, include_ccw=False, setup_us_extra=2)
    deep = DeepPlayer(policy=policy, engine=engine, seed=1, search="2:2:1:argmax")
    play_game(game, {Side.USSR: deep, Side.US: GreedyPlayer()})  # refuses an illegal action
    assert game.is_terminal
    assert deep.stats["search"] > 0, deep.stats
