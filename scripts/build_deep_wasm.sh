#!/usr/bin/env bash
#
# Build the deep bot's engine as one standalone WebAssembly module:
# DeepStruggle's engine sources (the pinned third_party/deepstruggle submodule)
# plus src/struggler/bots/deep/wasm/shim.cpp. wasmtime loads the result on
# Windows, macOS and Linux with no compiler on the player's machine.
#
#     scripts/build_deep_wasm.sh [out.wasm]          # needs Emscripten (em++) on PATH
#     DEEP_CXX=clang++ scripts/build_deep_wasm.sh lib.so   # same sources as a native
#                                                         # shared library, for tests
#
# CI builds it (.github/workflows/deep-engine.yml); a user downloads it with
# scripts/install_deep_model.py rather than building it.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DS="$ROOT/third_party/deepstruggle"
OUT="${1:-$ROOT/data/deep/ts_engine.wasm}"
mkdir -p "$(dirname "$OUT")"

if [[ ! -f "$DS/engine/src/engine.cpp" ]]; then
    echo "third_party/deepstruggle is empty: git submodule update --init third_party/deepstruggle" >&2
    exit 1
fi

SOURCES=(
    "$DS"/engine/src/map_data.cpp "$DS"/engine/src/card_data.cpp "$DS"/engine/src/scoring.cpp
    "$DS"/engine/src/ops.cpp "$DS"/engine/src/space_race.cpp
    "$DS"/engine/src/events/early_war.cpp "$DS"/engine/src/events/mid_war.cpp
    "$DS"/engine/src/events/late_war.cpp "$DS"/engine/src/card_dispatcher.cpp
    "$DS"/engine/src/action_mask.cpp "$DS"/engine/src/state_machine.cpp
    "$DS"/engine/src/observation.cpp "$DS"/engine/src/serialization.cpp
    "$DS"/engine/src/invariant.cpp "$DS"/engine/src/engine.cpp "$DS"/engine/src/cached_state.cpp
    "$DS"/bindings/state_json.cpp
    "$ROOT"/src/struggler/bots/deep/wasm/shim.cpp
)
INCLUDES=(-I"$DS/engine/include" -I"$DS/bindings")
COMMON=(-std=c++20 -O3 "${INCLUDES[@]}")

if [[ -n "${DEEP_CXX:-}" ]]; then
    "$DEEP_CXX" "${COMMON[@]}" -fPIC -shared -o "$OUT" "${SOURCES[@]}"
else
    EXPORTS=_ds_alloc,_ds_free,_ds_last_error,_ds_new_game,_ds_country_name,_ds_load_save_json
    EXPORTS+=,_ds_save_json,_ds_decision_player,_ds_decision_type,_ds_is_terminal,_ds_mask
    EXPORTS+=,_ds_step_flat,_ds_roll,_ds_obs_size_for,_ds_observation
    em++ "${COMMON[@]}" -sSTANDALONE_WASM=1 --no-entry -sALLOW_MEMORY_GROWTH=1 \
        -sDISABLE_EXCEPTION_CATCHING=1 -sEXPORTED_FUNCTIONS="$EXPORTS" \
        -o "$OUT" "${SOURCES[@]}"
fi
echo "built $OUT ($(wc -c < "$OUT") bytes)"
