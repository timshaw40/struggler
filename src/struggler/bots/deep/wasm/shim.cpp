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
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>
#include <vector>

#include "rollout_batch.hpp"
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
std::unique_ptr<ts::rollout::RolloutBatch> g_rollout;
std::vector<float> g_ro_obs;
std::vector<uint8_t> g_ro_masks;

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

// ---- the rollout root (DeepStruggle bindings/rollout_batch.hpp) -------------------------------
//
// The same C++ DeepStruggle's Python and browser searchers share; the bot drives
// it from Python (src/struggler/bots/deep/search.py) with the network's logits
// and values. One batch at a time, over the position this shim holds:
// ds_rollout_candidates -> ds_rollout_start -> until ds_rollout_all_done:
// ds_rollout_fill_live, network, ds_rollout_step -> ds_rollout_fill_leaves,
// critic, ds_rollout_set_leaf_values -> ds_rollout_decide.

// The `k` most probable legal moves by `logits` (220 floats) under the held
// position's mask, best first, into `out` (k int32s). Returns how many.
DS_API int ds_rollout_candidates(const float* logits, int k, int32_t* out) {
    if (k <= 0) return 0;
    ts::Engine::get_flat_action_mask(g_state, g_mask, false);
    std::vector<uint16_t> c(static_cast<size_t>(k));
    const size_t n = ts::rollout::top_candidates(logits, g_mask, static_cast<size_t>(k), c.data());
    for (size_t i = 0; i < n; ++i) out[i] = c[i];
    return static_cast<int>(n);
}

// A fresh batch (DeepStruggle's measured defaults: shared dice, the DEFCON
// guard, stop after `horizon` action-round boundaries, leaves from the mover's
// side) holding the held position with its `k` candidates in `worlds` worlds.
// Returns the rows added; 0 with ds_last_error on a refusal.
DS_API int ds_rollout_start(int horizon, uint32_t features, uint32_t seed_lo, uint32_t seed_hi,
                            const int32_t* candidates, int k, int worlds) {
    if (horizon < 0 || k <= 0 || worlds <= 0) { g_error = "bad rollout shape"; return 0; }
    if (features & ~ts::obs_features::ALL) { g_error = "unknown obs_features bit"; return 0; }
    ts::rollout::Config cfg{};
    cfg.horizon = horizon;
    cfg.features = features;
    g_rollout = std::make_unique<ts::rollout::RolloutBatch>(cfg, (static_cast<uint64_t>(seed_hi) << 32) | seed_lo);
    std::vector<uint16_t> c;
    for (int i = 0; i < k; ++i) {
        if (candidates[i] < 0 || candidates[i] >= static_cast<int32_t>(ts::FLAT_ACTION_SPACE_SIZE)) {
            g_error = "a candidate is outside the flat action space";
            return 0;
        }
        c.push_back(static_cast<uint16_t>(candidates[i]));
    }
    const size_t n = g_rollout->add(g_state, c.data(), c.size(), static_cast<size_t>(worlds));
    if (n == 0) g_error = g_rollout->last_error()[0] ? g_rollout->last_error() : "no world kept every candidate";
    return static_cast<int>(n);
}

DS_API int ds_rollout_all_done() { return !g_rollout || g_rollout->all_done() ? 1 : 0; }
DS_API int ds_rollout_refused() { return g_rollout ? static_cast<int>(g_rollout->refused()) : 0; }
DS_API int ds_rollout_obs_width() { return g_rollout ? static_cast<int>(g_rollout->obs_width()) : 0; }
DS_API const float* ds_rollout_buf_obs() { return g_ro_obs.data(); }
DS_API const uint8_t* ds_rollout_buf_masks() { return g_ro_masks.data(); }

// The live rows' observations (each from its acting side) and masks into the
// buffers above. Returns the row count.
DS_API int ds_rollout_fill_live() {
    if (!g_rollout) return 0;
    const size_t n = g_rollout->live();
    g_ro_obs.assign(std::max<size_t>(1, n * g_rollout->obs_width()), 0.0f);
    g_ro_masks.assign(std::max<size_t>(1, n * ts::FLAT_ACTION_SPACE_SIZE), 0);
    return static_cast<int>(g_rollout->fill_live(g_ro_obs.data(), g_ro_masks.data()));
}

// `logits`: one 220-float row per row the last fill_live wrote.
DS_API int ds_rollout_step(const float* logits) {
    if (!g_rollout) { g_error = "ds_rollout_start first"; return 0; }
    g_rollout->step(logits);
    return 1;
}

// The rows at their stop and not yet valued, from the mover's side, into the
// same buffers. Returns the row count.
DS_API int ds_rollout_fill_leaves() {
    if (!g_rollout) return 0;
    size_t n = 0;
    for (size_t r = 0; r < g_rollout->rows(); ++r) {
        const auto& row = g_rollout->row(r);
        n += (row.done && !row.terminal && !row.refused && !row.leaf_valued) ? 1 : 0;
    }
    g_ro_obs.assign(std::max<size_t>(1, n * g_rollout->obs_width()), 0.0f);
    g_ro_masks.assign(std::max<size_t>(1, n * ts::FLAT_ACTION_SPACE_SIZE), 0);
    return static_cast<int>(g_rollout->fill_leaves(g_ro_obs.data(), g_ro_masks.data()));
}

// `values`: the critic's v_win, one per row the last fill_leaves wrote.
DS_API int ds_rollout_set_leaf_values(const float* values) {
    if (!g_rollout) { g_error = "ds_rollout_start first"; return 0; }
    g_rollout->set_leaf_values(values);
    return 1;
}

// The choice from the valued batch under `rule` ("argmax", "z<x>", "kl<t>"),
// with the root's `logits` and the held position's mask, as JSON: {"pick",
// "worlds", "q", "lead", "lead_se"}. "" with ds_last_error on a bad rule.
DS_API const char* ds_rollout_decide(const int32_t* candidates, int k, const float* logits, const char* rule_text) {
    if (!g_rollout || g_rollout->positions() == 0) { g_error = "no rollout batch"; return ""; }
    ts::rollout::Rule rule;
    if (!ts::rollout::parse_rule(rule_text, rule)) { g_error = "unknown rule: argmax, z<x> or kl<t>"; return ""; }
    std::vector<uint16_t> c;
    for (int i = 0; i < k; ++i) c.push_back(static_cast<uint16_t>(candidates[i]));
    ts::Engine::get_flat_action_mask(g_state, g_mask, false);
    const ts::rollout::Decision d = ts::rollout::decide(*g_rollout, 0, c.data(), c.size(), logits, g_mask, rule);
    auto list = [](const std::vector<float>& v) {
        std::string s = "[";
        for (size_t i = 0; i < v.size(); ++i) {
            const float x = v[i];
            s += (i ? "," : "");
            s += (x == x) ? std::to_string(x) : std::string("null");  // NaN is not JSON
        }
        return s + "]";
    };
    return keep("{\"pick\":" + std::to_string(d.pick) + ",\"worlds\":" + std::to_string(d.worlds) +
                ",\"q\":" + list(d.q) + ",\"lead\":" + list(d.lead) + ",\"lead_se\":" + list(d.lead_se) + "}");
}
