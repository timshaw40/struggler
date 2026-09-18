# AGENTS.md

Working notes for AI coding agents (opencode, Claude Code, and others) in
this repo. This is the single source of truth for agent instructions;
`CLAUDE.md` points here.

## Start here

`docs/` is the binding contract, not background reading: read the relevant
document before changing the area it covers, and update it in the same
commit when you change what it specifies.

| Document | Read it before touching |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The decision stack, the public API, `Engine`, core types |
| [docs/CARDS.md](docs/CARDS.md) | `events.py`, `cards.json`, anything card-related |
| [docs/BOTS.md](docs/BOTS.md) | `bots/`, the `Player` protocol, physical mode, training/eval tooling |
| [docs/TESTING.md](docs/TESTING.md) | Adding or changing any test, or regenerating a golden replay |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | Before "fixing" something that may be a documented simplification |
| [docs/STRATEGY.md](docs/STRATEGY.md) | The heuristics `GreedyPlayer` and the LLM prompt play by |

`CONTEXT.md` is the domain glossary (the project's ubiquitous language);
`docs/adr/` records decisions that had real alternatives. Read the ADRs that
touch your area, and flag a conflict rather than silently overriding one.

The five architectural mandates in `docs/ARCHITECTURE.md` are
non-negotiable. Code referring to "mandate #3" means that list. An
implementation that violates one is wrong regardless of whether it passes
the tests.

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`github.com/timshaw40/struggler`), driven via
the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context layout: `CONTEXT.md` and `docs/adr/` at the repo root. See
`docs/agents/domain.md`.

## Conventions

- **Python**: 3.12+.
- **Tests**: `pytest`, plus `hypothesis` for property-based tests. Run the
  full suite before committing (`.venv/bin/pytest -q`, ~507 tests, well under
  a minute).
- **Environment**: `.venv` managed with `uv` (`uv pip install --python
  .venv/bin/python -e '.[test]'`), or conda (`environment.yml`), or plain
  `pip install -e ".[test]"`. Extras: `[test]`, `[ui]` (Pillow/PyMuPDF for
  asset conversion), `[llm]`, `[rl]` (numpy + torch for self-play).
- **License**: MIT (the vendored VASSAL art under `third_party/gmt-vassal/`
  is separately licensed — see its `LICENSE`).
- **Language**: all code, comments, docstrings, and commit messages in
  English.
- **Commits**: small, each with a "why" paragraph. Branches
  `feature/<slug>`, PRs to `main`.
- **Never commit user-supplied data**: PDFs, `ui/assets/`, `data/`,
  `*.pt`, `.cache/`, `twilight_struggle_sessions.txt`. Code and derived
  factual data (`ui/countries.json`) are committed.
- **Layout** (src-layout, to avoid accidental implicit imports of the
  working directory during tests):
  - `src/struggler/engine/` — the rules engine: `core.py` (state + decision
    dispatch), `board.py`, `cards.py`, `events.py`, `replay.py`, `rules.py`,
    `types.py`, `physical.py`, and the `Player`/`HumanPlayer` contract.
  - `src/struggler/bots/` — the automated `Player` implementations:
    `naive.py` (`first`/`random`), `greedy.py`, `mcts.py`, `value.py`,
    `checkpoint.py`, plus `bots/rl/` (self-play PPO) and `bots/llm/`.
  - `src/struggler/arena.py`, `src/struggler/runner.py` — matchups/Elo and
    `play_game`.
  - `src/struggler/data/` — the game's JSON facts (`cards.json`,
    `countries.json`, `rules.json`).
  - `src/main.py` — the CLI and `build_player` (one branch per player kind).
  - `scripts/` — `serve_ui.py`, the asset installer/renderer/calibrator, and
    the training/eval CLIs (`run_arena`, `tune_greedy`, `train_value`,
    `train_ppo`, `final_eval`, `extract_training`, `replay_game`, ...).
  - `ui/` — the static web UI served by `scripts/serve_ui.py`.
  - `tests/`, with golden replay logs under `tests/replays/`.
  - `third_party/gmt-vassal/` — separately licensed vendored VASSAL art.

## Two things that have bitten this codebase before

- **Check `tests/conftest.py` before writing a test helper.** A
  near-duplicate invariant checker copy-pasted across test files once let a
  real defect hide for weeks. See `docs/TESTING.md`.
- **Don't re-derive placement legality from the live board mid-Ops-spend.**
  Rule 6.1.1 freezes reachability at the start of the action round; see the
  reachability section of `docs/ARCHITECTURE.md`.
