"""The narrow interface the deep bot needs from a DeepStruggle engine build.

Two builds implement it. `NativeBackend` wraps the nanobind module
`ts_engine` (Linux; fastest, used by the arena and the sync census).
`WasmBackend` wraps the WebAssembly build of `wasm/shim.cpp` through
`wasmtime`, so Windows and macOS need no compiler (`SharedLibBackend` runs the
same shim as a native library, for tests). DeepStruggle holds the two
builds to bit-identical games (its `tests/web/test_wasm_engine.py`), so the
bot behaves the same on either.

A backend holds exactly one position. Nothing here auto-advances: chance nodes
are stepped by the caller with the die it read from struggler's log.
"""
from __future__ import annotations

import importlib
import os
import sys
from typing import Any, Protocol, Sequence

#: `ts::DecisionType`.
NONE, SELECT_CARD, SELECT_PLAY_MODE, CHOOSE_TIMING_BRANCH, SELECT_OP_MODE, POINT_NODE, \
    CHOOSE_BRANCH, ROLL_DIE = range(8)

#: Flat action layout (DeepStruggle `bindings/action_encoder.py`, 220 wide).
CARD_OFFSET = 0
PLAY_MODE_OFFSET = 110      # + Resolution: EVENT 0, SPACE 1, OPS_INFLUENCE 2, OPS_COUP 3, OPS_REALIGN 4
OP_MODE_OFFSET = 112        # + OpMode: INFLUENCE 0, COUP 1, REALIGN 2 (deferred Ops after an event)
ROLL_DIE_INDEX = 115
NODE_OFFSET = 116           # + country id
BRANCH_OFFSET = 200         # + branch 0..7
CONFIRM_DONE_INDEX = 208
DEFCON_VALUE_OFFSET = 209   # + (value - 1)
REGION_OFFSET = 214         # + ts::Region
FLAT_ACTION_SIZE = 220

RES_EVENT, RES_SPACE, RES_OPS_INFLUENCE, RES_OPS_COUP, RES_OPS_REALIGN = range(5)

#: Phases (`ts::Phase`).
PHASE_SETUP, PHASE_HEADLINE, PHASE_ACTION_ROUND = 0, 1, 2


class DeepEngine(Protocol):
    def country_names(self) -> list[str]: ...
    def load_save(self, save: dict) -> None: ...
    def save(self) -> dict: ...
    def decision(self) -> tuple[int, int]: ...
    def mask(self) -> Sequence[int]: ...
    def step_flat(self, index: int) -> bool: ...
    def roll(self, actor_die: int, opponent_die: int = 0) -> bool: ...
    def observation(self, player: int, features: int) -> Sequence[float]: ...
    def is_terminal(self) -> bool: ...


class RolloutEngine(DeepEngine, Protocol):
    """A backend that also runs DeepStruggle's rollout root
    (`bindings/rollout_batch.hpp`) over the position it holds: one batch at a
    time, driven by `search.py` with the network's outputs. Arrays are numpy:
    observations [rows, width] float32, masks [rows, 220] uint8."""

    supports_rollout: bool

    def rollout_candidates(self, logits: Any, k: int) -> list[int]: ...
    def rollout_start(self, horizon: int, features: int, seed: int, candidates: list[int], worlds: int) -> int: ...
    def rollout_all_done(self) -> bool: ...
    def rollout_refused(self) -> int: ...
    def rollout_fill_live(self) -> tuple[Any, Any]: ...
    def rollout_step(self, logits: Any) -> None: ...
    def rollout_fill_leaves(self) -> tuple[Any, Any]: ...
    def rollout_set_leaf_values(self, values: Any) -> None: ...
    def rollout_decide(self, candidates: list[int], logits: Any, rule: str) -> dict: ...


class NativeBackend:
    """`ts_engine` imported from a DeepStruggle build directory.

    The directory comes from `build_dir` or `STRUGGLER_DEEP_ENGINE` (the
    folder holding `ts_engine.cpython-*.so`)."""

    def __init__(self, build_dir: str | None = None) -> None:
        path = build_dir or os.environ.get("STRUGGLER_DEEP_ENGINE")
        if path and path not in sys.path:
            sys.path.insert(0, path)
        self.ts = importlib.import_module("ts_engine")
        self.state = self.ts.GameState()
        self.ts.Engine.init_game(self.state, 1)

    def country_names(self) -> list[str]:
        return [self.ts.MapData.get_country_name(i) for i in range(84)]

    def load_save(self, save: dict) -> None:
        self.state = self.ts.state_from_save_dict(save)

    def save(self) -> dict:
        return dict(self.state.to_save_dict())

    def decision(self) -> tuple[int, int]:
        d = self.save()
        top = d["ctx_stack"][d["ctx_stack_depth"]]
        return int(top["decision_player"]), int(top["decision_type"])

    def phase(self) -> int:
        return int(self.save()["current_phase"])

    def mask(self) -> Sequence[int]:
        return self.ts.Engine.get_flat_action_mask(self.state, False)

    def step_flat(self, index: int) -> bool:
        return bool(self.ts.Engine.try_step_flat(self.state, int(index), False))

    def roll(self, actor_die: int, opponent_die: int = 0) -> bool:
        action = self.ts.MicroAction(self.ts.DecisionType.ROLL_DIE, int(actor_die), int(opponent_die), 0)
        return bool(self.ts.Engine.try_step(self.state, action, False))

    def observation(self, player: int, features: int) -> Sequence[float]:
        return self.ts.extract_observation_features(self.state, self.ts.Player(player), int(features))

    def is_terminal(self) -> bool:
        return bool(self.ts.Engine.is_terminal(self.state))

    # -- the rollout root -----------------------------------------------------

    supports_rollout = True
    _batch: Any = None

    def rollout_candidates(self, logits: Any, k: int) -> list[int]:
        import numpy as np

        mask = np.asarray(self.mask(), dtype=np.uint8)
        return [int(a) for a in self.ts.rollout_candidates(np.asarray(logits, dtype=np.float32), mask, int(k))]

    def rollout_start(self, horizon: int, features: int, seed: int, candidates: list[int], worlds: int) -> int:
        self._batch = self.ts.RolloutBatch(horizon=int(horizon), shared_dice=True, guard=True,
                                           features=int(features), seed=int(seed))
        return int(self._batch.add(self.state, list(candidates), int(worlds)))

    def rollout_all_done(self) -> bool:
        return self._batch is None or bool(self._batch.all_done())

    def rollout_refused(self) -> int:
        return 0 if self._batch is None else int(self._batch.refused())

    def rollout_fill_live(self) -> tuple[Any, Any]:
        return self._batch.fill_live()

    def rollout_step(self, logits: Any) -> None:
        import numpy as np

        self._batch.step(np.ascontiguousarray(logits, dtype=np.float32))

    def rollout_fill_leaves(self) -> tuple[Any, Any]:
        return self._batch.fill_leaves()

    def rollout_set_leaf_values(self, values: Any) -> None:
        import numpy as np

        self._batch.set_leaf_values(np.ascontiguousarray(values, dtype=np.float32))

    def rollout_decide(self, candidates: list[int], logits: Any, rule: str) -> dict:
        import numpy as np

        mask = np.asarray(self.mask(), dtype=np.uint8)
        d = self.ts.rollout_decide(self._batch, 0, list(candidates), np.asarray(logits, dtype=np.float32), mask, rule)
        return {"pick": int(d["pick"]), "worlds": int(d["worlds"]), "q": list(d["q"]),
                "lead": list(d["lead"]), "lead_se": list(d["lead_se"])}


class _ShimBackend:
    """The `ds_*` C API of `wasm/shim.cpp`, over some way of calling it.

    Subclasses provide `_call(name, *args)`, `_read(ptr, n)` and
    `_write(data) -> ptr` (freed with `ds_free`). The C API's semantics match
    `NativeBackend` call for call."""

    _OBS_FLOAT = 4

    def _call(self, name: str, *args: int) -> int: ...
    def _read(self, ptr: int, n: int) -> bytes: ...
    def _write(self, data: bytes) -> int: ...

    def _string(self, ptr: int) -> str:
        out = bytearray()
        while True:
            chunk = self._read(ptr + len(out), 256)
            end = chunk.find(b"\0")
            if end >= 0:
                out += chunk[:end]
                return out.decode("utf-8")
            out += chunk

    def country_names(self) -> list[str]:
        return [self._string(self._call("ds_country_name", i)) for i in range(84)]

    def load_save(self, save: dict) -> None:
        import json

        ptr = self._write(json.dumps(save).encode() + b"\0")
        try:
            ok = self._call("ds_load_save_json", ptr)
        finally:
            self._call("ds_free", ptr)
        if not ok:
            raise ValueError(self._string(self._call("ds_last_error")))

    def save(self) -> dict:
        import json

        return json.loads(self._string(self._call("ds_save_json")))

    def decision(self) -> tuple[int, int]:
        return self._call("ds_decision_player"), self._call("ds_decision_type")

    def mask(self) -> Sequence[int]:
        return self._read(self._call("ds_mask"), FLAT_ACTION_SIZE)

    def step_flat(self, index: int) -> bool:
        return bool(self._call("ds_step_flat", int(index)))

    def roll(self, actor_die: int, opponent_die: int = 0) -> bool:
        return bool(self._call("ds_roll", int(actor_die), int(opponent_die)))

    def observation(self, player: int, features: int) -> Sequence[float]:
        import array

        n = self._call("ds_obs_size_for", int(features))
        ptr = self._call("ds_observation", int(player), int(features))
        if not n or not ptr:
            raise ValueError(self._string(self._call("ds_last_error")))
        out = array.array("f")
        out.frombytes(self._read(ptr, n * self._OBS_FLOAT))
        return out

    def is_terminal(self) -> bool:
        return bool(self._call("ds_is_terminal"))

    # -- the rollout root -----------------------------------------------------

    def _has(self, name: str) -> bool: ...

    @property
    def supports_rollout(self) -> bool:
        """False for an engine built before the rollout entry points (an older
        data/deep/ts_engine.wasm): the bot then plays the plain network."""
        return self._has("ds_rollout_start")

    def _floats(self, ptr: int, n: int) -> Any:
        import numpy as np

        return np.frombuffer(self._read(ptr, 4 * n), dtype=np.float32).copy()

    def _with(self, data: bytes, fn: Any) -> Any:
        ptr = self._write(data if data else b"\0")
        try:
            return fn(ptr)
        finally:
            self._call("ds_free", ptr)

    def rollout_candidates(self, logits: Any, k: int) -> list[int]:
        import numpy as np

        lg = np.ascontiguousarray(logits, dtype=np.float32).tobytes()
        out = self._write(bytes(4 * k))
        try:
            n = self._with(lg, lambda p: self._call("ds_rollout_candidates", p, int(k), out))
            return [int(a) for a in np.frombuffer(self._read(out, 4 * n), dtype=np.int32)]
        finally:
            self._call("ds_free", out)

    def rollout_start(self, horizon: int, features: int, seed: int, candidates: list[int], worlds: int) -> int:
        import numpy as np

        cands = np.asarray(candidates, dtype=np.int32).tobytes()
        seed &= (1 << 64) - 1
        n = self._with(cands, lambda p: self._call("ds_rollout_start", int(horizon), int(features),
                                                     seed & 0xFFFFFFFF, seed >> 32, p, len(candidates), int(worlds)))
        return n  # 0: no world kept every candidate (ds_last_error says why)

    def rollout_all_done(self) -> bool:
        return bool(self._call("ds_rollout_all_done"))

    def rollout_refused(self) -> int:
        return self._call("ds_rollout_refused")

    def _buffers(self, n: int) -> tuple[Any, Any]:
        import numpy as np

        width = self._call("ds_rollout_obs_width")
        obs = self._floats(self._call("ds_rollout_buf_obs"), n * width).reshape(n, width)
        masks = np.frombuffer(self._read(self._call("ds_rollout_buf_masks"), n * FLAT_ACTION_SIZE),
                              dtype=np.uint8).reshape(n, FLAT_ACTION_SIZE).copy()
        return obs, masks

    def rollout_fill_live(self) -> tuple[Any, Any]:
        return self._buffers(self._call("ds_rollout_fill_live"))

    def rollout_step(self, logits: Any) -> None:
        import numpy as np

        self._with(np.ascontiguousarray(logits, dtype=np.float32).tobytes(),
                   lambda p: self._call("ds_rollout_step", p))

    def rollout_fill_leaves(self) -> tuple[Any, Any]:
        return self._buffers(self._call("ds_rollout_fill_leaves"))

    def rollout_set_leaf_values(self, values: Any) -> None:
        import numpy as np

        self._with(np.ascontiguousarray(values, dtype=np.float32).tobytes(),
                   lambda p: self._call("ds_rollout_set_leaf_values", p))

    def rollout_decide(self, candidates: list[int], logits: Any, rule: str) -> dict:
        import json

        import numpy as np

        cands = self._write(np.asarray(candidates, dtype=np.int32).tobytes() or b"\0")
        lg = self._write(np.ascontiguousarray(logits, dtype=np.float32).tobytes())
        text = self._write(rule.encode() + b"\0")
        try:
            out = self._string(self._call("ds_rollout_decide", cands, len(candidates), lg, text))
        finally:
            for p in (cands, lg, text):
                self._call("ds_free", p)
        if not out:
            raise ValueError(self._string(self._call("ds_last_error")))
        return json.loads(out)


class WasmBackend(_ShimBackend):
    """The shim compiled to WebAssembly (`scripts/build_deep_wasm.sh`), run by
    `wasmtime`: pip-installable on Windows, macOS and Linux, no compiler."""

    def __init__(self, path: str | None = None) -> None:
        import wasmtime

        path = path or os.environ.get("STRUGGLER_DEEP_WASM") or _default_wasm()
        engine = wasmtime.Engine()
        self._store = wasmtime.Store(engine)
        linker = wasmtime.Linker(engine)
        linker.define_wasi()
        self._store.set_wasi(wasmtime.WasiConfig())
        module = wasmtime.Module.from_file(engine, path)
        # Emscripten's standalone output still imports a few `env` hooks
        # (`emscripten_notify_memory_growth`); none matters outside a browser,
        # so each becomes a no-op returning zeros.
        for imp in module.imports:
            if imp.module != "env" or not isinstance(imp.type, wasmtime.FuncType):
                continue
            results = list(imp.type.results)
            stub = (lambda *args, _r=results: None if not _r else
                    (0 if len(_r) == 1 else tuple(0 for _ in _r)))
            linker.define(self._store, "env", imp.name, wasmtime.Func(self._store, imp.type, stub))
        instance = linker.instantiate(self._store, module)
        self._exports = instance.exports(self._store)
        self._memory = self._exports["memory"]
        if "_initialize" in self._exports:  # reactor modules run static constructors here
            self._exports["_initialize"](self._store)
        self._call("ds_new_game", 1, 0)

    def _call(self, name: str, *args: int) -> int:
        result = self._exports[name](self._store, *args)
        return 0 if result is None else result

    def _has(self, name: str) -> bool:
        return name in self._exports

    def _read(self, ptr: int, n: int) -> bytes:
        size = self._memory.data_len(self._store)
        return bytes(self._memory.read(self._store, ptr, min(ptr + n, size)))

    def _write(self, data: bytes) -> int:
        ptr = self._call("ds_alloc", len(data))
        self._memory.write(self._store, data, ptr)
        return ptr


class SharedLibBackend(_ShimBackend):
    """The same shim built as a native shared library (`DEEP_CXX=clang++
    scripts/build_deep_wasm.sh lib.so`) and called through ctypes: for tests of
    the shim's semantics without Emscripten, and for anyone with a compiler."""

    def __init__(self, path: str) -> None:
        import ctypes

        self._ct = ctypes
        self._lib = ctypes.CDLL(path)
        for name in ("ds_alloc", "ds_country_name", "ds_save_json", "ds_mask", "ds_observation",
                     "ds_last_error"):
            getattr(self._lib, name).restype = ctypes.c_void_p
        self._lib.ds_free.argtypes = [ctypes.c_void_p]
        self._lib.ds_load_save_json.argtypes = [ctypes.c_void_p]
        if self._has("ds_rollout_start"):
            vp, i32, u32 = ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32
            for name in ("ds_rollout_buf_obs", "ds_rollout_buf_masks", "ds_rollout_decide"):
                getattr(self._lib, name).restype = vp
            self._lib.ds_rollout_candidates.argtypes = [vp, i32, vp]
            self._lib.ds_rollout_start.argtypes = [i32, u32, u32, u32, vp, i32, i32]
            self._lib.ds_rollout_step.argtypes = [vp]
            self._lib.ds_rollout_set_leaf_values.argtypes = [vp]
            self._lib.ds_rollout_decide.argtypes = [vp, i32, vp, vp]
        self._call("ds_new_game", 1, 0)

    def _call(self, name: str, *args: int) -> int:
        result = getattr(self._lib, name)(*args)
        return 0 if result is None else result

    def _has(self, name: str) -> bool:
        return hasattr(self._lib, name)

    def _read(self, ptr: int, n: int) -> bytes:
        return self._ct.string_at(ptr, n)

    def _string(self, ptr: int) -> str:
        return self._ct.string_at(ptr).decode("utf-8")

    def _write(self, data: bytes) -> int:
        ptr = self._call("ds_alloc", len(data))
        self._ct.memmove(ptr, data, len(data))
        return ptr


def _default_wasm() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[4] / "data" / "deep" / "ts_engine.wasm")


def load_backend() -> DeepEngine:
    """The backend the environment names: `STRUGGLER_DEEP_ENGINE` (native
    module dir), `STRUGGLER_DEEP_SHARED_LIB` (the shim as a native library), or
    by default the WebAssembly build (`STRUGGLER_DEEP_WASM`, else
    data/deep/ts_engine.wasm)."""
    if os.environ.get("STRUGGLER_DEEP_ENGINE"):
        return NativeBackend()
    if os.environ.get("STRUGGLER_DEEP_SHARED_LIB"):
        return SharedLibBackend(os.environ["STRUGGLER_DEEP_SHARED_LIB"])
    return WasmBackend()
