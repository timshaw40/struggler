// The deep bot's WebAssembly entry points, compiled with DeepStruggle's engine
// sources (third_party/deepstruggle) by scripts/build_deep_wasm.sh.
//
// DeepStruggle's own browser API (bindings/wasm/ts_engine_wasm.cpp) suits a
// page: every step drains the chance nodes it lands on, and a save is refused
// unless it round-trips byte for byte. The bot needs the opposite: it steps
// each die itself with struggler's logged value, and loads boundary saves that
// name only the fields that matter. So this shim exposes the same calls as
// NativeBackend (src/struggler/bots/deep/backend.py), with the same semantics,
// over plain C types. Strings pass through ds_alloc/ds_free.
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>

#include "state_json.hpp"
#include "ts/action_mask.hpp"
#include "ts/engine.hpp"
#include "ts/game_state.hpp"
#include "ts/map_data.hpp"
#include "ts/observation.hpp"

#define DS_API extern "C" __attribute__((used, visibility("default")))

namespace sj = ts::state_json;

namespace {
ts::GameState g_state{};
uint8_t g_mask[ts::FLAT_ACTION_SPACE_SIZE];
float g_obs[ts::OBS_SIZE_V23 + ts::obs_features::extra_width(ts::obs_features::ALL)];
std::string g_text;
std::string g_error;

const char* keep(std::string s) {
    g_text = std::move(s);
    return g_text.c_str();
}

ts::Player player_from_int(int p) {
    return p > 0 ? ts::Player::US : (p < 0 ? ts::Player::USSR : ts::Player::NONE);
}
}  // namespace

DS_API void* ds_alloc(int n) { return std::malloc(static_cast<size_t>(n)); }
DS_API void ds_free(void* p) { std::free(p); }
DS_API const char* ds_last_error() { return g_error.c_str(); }

DS_API void ds_new_game(uint32_t seed_lo, uint32_t seed_hi) {
    ts::Engine::init_game(g_state, (static_cast<uint64_t>(seed_hi) << 32) | seed_lo);
}

DS_API const char* ds_country_name(int id) {
    return (id >= 0 && id < 84) ? keep(std::string(ts::MapData::get_country_name(static_cast<uint8_t>(id)))) : "";
}

// Same as ts_engine.state_from_save_dict: named fields, missing ones defaulted.
// No round-trip check and no chance draining. Returns 1, or 0 with ds_last_error.
DS_API int ds_load_save_json(const char* text) {
    sj::Value v;
    std::string error;
    if (!sj::Value::parse(text, v, &error)) { g_error = "not JSON: " + error; return 0; }
    ts::GameState s{};
    if (!sj::load_save(v, s, &error)) { g_error = "not a save: " + error; return 0; }
    g_state = s;
    return 1;
}

DS_API const char* ds_save_json() { return keep(sj::save(g_state).dump()); }
DS_API int ds_decision_player() { return static_cast<int>(g_state.ctx().decision_player); }
DS_API int ds_decision_type() { return static_cast<int>(g_state.ctx().decision_type); }
DS_API int ds_is_terminal() { return ts::Engine::is_terminal(g_state) ? 1 : 0; }

DS_API const uint8_t* ds_mask() {
    ts::Engine::get_flat_action_mask(g_state, g_mask, false);
    return g_mask;
}

// Engine.try_step_flat(state, idx, auto_advance=False): 1 if applied, 0 if refused (state unchanged).
DS_API int ds_step_flat(int idx) {
    const ts::GameState before = g_state;
    if (!ts::Engine::step_flat(g_state, static_cast<uint16_t>(idx), false)) { g_state = before; return 0; }
    return 1;
}

// One chance node with forced dice: the acting side's, and the opponent's (realignment only).
DS_API int ds_roll(int actor_die, int opponent_die) {
    const ts::MicroAction a{ts::DecisionType::ROLL_DIE, static_cast<uint8_t>(actor_die),
                            static_cast<uint8_t>(opponent_die), 0};
    const ts::GameState before = g_state;
    if (!ts::Engine::step(g_state, a, false)) { g_state = before; return 0; }
    return 1;
}

DS_API int ds_obs_size_for(uint32_t features) {
    if (features & ~ts::obs_features::ALL) { g_error = "unknown obs_features bit"; return 0; }
    return static_cast<int>(ts::OBS_SIZE_V23 + ts::obs_features::extra_width(features));
}

DS_API const float* ds_observation(int player, uint32_t features) {
    if (features & ~ts::obs_features::ALL) { g_error = "unknown obs_features bit"; return nullptr; }
    ts::extract_observation_features(g_state, player_from_int(player), features, g_obs);
    return g_obs;
}
