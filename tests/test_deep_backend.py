"""The deep bot's engine backends agree with each other and with struggler.

Each test runs only where its build is present: `STRUGGLER_DEEP_WASM` (the
WebAssembly module, any OS), `STRUGGLER_DEEP_SHARED_LIB` (the same shim as a
native library) or `STRUGGLER_DEEP_ENGINE` (DeepStruggle's nanobind module).
CI builds the first two (.github/workflows/deep-engine.yml).
"""
import collections
import os
import random
import sys
from pathlib import Path

import pytest

from struggler.bots.deep import backend as B
from struggler.bots.deep.ids import IdMap
from struggler.engine import Engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

WASM = os.environ.get("STRUGGLER_DEEP_WASM")
SHARED = os.environ.get("STRUGGLER_DEEP_SHARED_LIB")
NATIVE = os.environ.get("STRUGGLER_DEEP_ENGINE")

needs_wasm = pytest.mark.skipif(not (WASM and Path(WASM).exists()), reason="no ts_engine.wasm")


def _reference():
    if SHARED and Path(SHARED).exists():
        return B.SharedLibBackend(SHARED)
    if NATIVE:
        return B.NativeBackend()
    pytest.skip("no reference build to compare with")


@needs_wasm
def test_wasm_backend_loads_and_names_the_map():
    wasm = B.WasmBackend(WASM)
    names = wasm.country_names()
    assert len(names) == 84 and "United Kingdom" in names
    IdMap.build(names, list(Engine.new_game(seed=0, include_ccw=False).board.countries))


@needs_wasm
def test_wasm_backend_plays_random_games_identically_to_the_reference():
    wasm, ref = B.WasmBackend(WASM), _reference()
    start = ref.save()  # a fresh game, at the setup placements
    for seed in (1, 2):
        rng = random.Random(seed)
        ref.load_save(start)
        wasm.load_save(start)
        for _ in range(300):
            if ref.is_terminal():
                break
            assert list(wasm.mask()) == list(ref.mask())
            assert list(wasm.observation(1, 0)) == list(ref.observation(1, 0))
            _, dtype = ref.decision()
            if dtype == B.ROLL_DIE:
                a, b = rng.randint(1, 6), rng.randint(1, 6)
                assert wasm.roll(a, b) == ref.roll(a, b)
            else:
                idx = rng.choice([i for i, v in enumerate(ref.mask()) if v])
                assert wasm.step_flat(idx) and ref.step_flat(idx)
            assert wasm.save() == ref.save()


@needs_wasm
def test_wasm_backend_passes_the_sync_census_on_a_game():
    import deep_sync_census as census

    wasm = B.WasmBackend(WASM)
    ids = IdMap.build(wasm.country_names(), list(Engine.new_game(seed=0, include_ccw=False).board.countries))
    reasons, examples, totals = collections.Counter(), {}, collections.Counter()
    census.run_game(1, True, ids, wasm, reasons, examples, totals)
    assert totals["agree"] / totals["rounds"] > 0.85, reasons
