# struggler

A complete, playable implementation of *Twilight Struggle* (GMT Games, 2005),
with a browser UI, several bots, and the test suite to keep them honest.

You can play it against a bot in the browser, watch two bots play, or drive the
engine from Python to train and evaluate agents. All 110 cards are implemented,
including every non-scoring event.

The bots are the point of the project. There is a **greedy** heuristic, an
**MCTS** player that searches lookahead on top of it, and an **LLM** player that
reads the rules and a strategy guide, receives the board at each decision, and
drafts a turn plan it then plays — or deliberately departs from.

> **Origins.** This began as a fork of
> [alekpinel/struggler](https://github.com/alekpinel/struggler) by Alejandro
> Pinel Martínez, which built the engine and card data. It has since grown well
> past that foundation. See [Origins and differences](#origins-and-differences)
> for exactly what is inherited and what was added.

## Install

Python 3.12+.

The easiest way is to use a [conda environment](https://www.anaconda.com/docs/getting-started/miniconda/install/windows-gui-install).

```sh
conda env create -f environment.yml
conda activate struggler
pip install -e ".[test]"
pip install -e ".[llm]"  # optional if you plan to use the llm
```

### Configure an LLM bot

To use the llm bot, you need to set up your api keys. This implementation supports anthropic, openai, and local OpenAI-compatible servers (LM Studio, Ollama — no key needed).

```sh
export ANTHROPIC_API_KEY=...   # for provider=anthropic
export OPENAI_API_KEY=...      # for provider=openai (the default)
# for provider=openai_compatible (e.g. LM Studio):
export STRUGGLER_LLM_BASE_URL=http://192.168.10.91:1234/v1
```

Provider and model are picked via environment variables, each overridable per run:

| Variable | Default | Purpose |
| --- | --- | --- |
| `STRUGGLER_LLM_PROVIDER` | `openai` | `anthropic`, `openai`, or `openai_compatible` — used for both the per-decision client and the once-per-turn planning client |
| `STRUGGLER_LLM_MODEL` | provider's built-in default | model for in-decision calls |
| `STRUGGLER_LLM_PLAN_MODEL` | provider's built-in default | model for the turn-planning call (same provider as above) |
| `STRUGGLER_LLM_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint for `openai_compatible` |
| `STRUGGLER_LLM_API_KEY` | `local` | key sent to the local server (required by the SDK, ignored by the server) |

## Play a game

You can just use the provided main to run any game.

```sh
python src/main.py                                  # human vs human
python src/main.py --us greedy --ussr greedy --seed 1  # bot vs bot
python src/main.py --ussr llm                       # human (US) vs llm bot (USSR)
python src/main.py --us human --ussr mcts --seed 1  # human (US) vs MCTS bot (USSR)
python src/main.py --physical us --ussr llm         # bot vs a real physical board
```
The options for the players are:
- human
- first
- random
- greedy
- mcts
- llm
- rl (a self-play PPO checkpoint; see [docs/BOTS.md](docs/BOTS.md))

But you can create your own implementation using the engine like this:

```python
from struggler.engine import Engine

engine = Engine.new_game(seed=12345)

while not engine.is_terminal:
    decision = engine.pending_decision
    action = pick(decision.options) # your agent has to pick the decision
    engine.step(action)

print(engine.winner)
```

Every game defaults to a saved replay log under `./logs/`
(`--game-log-path` to pick a location, `--no-game-log` to disable). Resume
one later with `--resume-game-log`, which rebuilds the game from that file
and keeps appending to it — useful as-is, or after hand-trimming the file's
`actions` to undo a bad play before continuing:

```sh
python src/main.py --resume-game-log logs/2026-08-18_10-58_game.json \
  --ussr llm --ussr-log-path logs/2026-08-18_10-58_ussr.json --resume
```

`--resume` additionally reloads an LLM player's own conversation memory
from its log see [docs/BOTS.md](docs/BOTS.md) for the resumption
contract, including keeping that memory in sync if you trim the game log.

## Play in the browser

A local web UI: the map on screen, you click countries and cards, a bot
thinks and answers.

```sh
pip install -e ".[ui]"    # Pillow, used only for the board conversion
python scripts/install_vassal_ui_assets.py --fetch-board
python scripts/serve_ui.py --us human --ussr mcts --seed 1
```

Open http://localhost:8000 (it opens itself). Seat flags mirror
`src/main.py` — `--us`/`--ussr` take `human/first/random/greedy/mcts/llm`,
and at most one seat may be `human`. The server resolves dice and bot
moves itself; the browser only ever posts an index into the pending
decision's options, so it is exactly as powerful as the engine allows.

### Watch the bots play each other

```sh
python scripts/serve_ui.py --us mcts --ussr mcts --seed 2
```

Spectator mode: every page poll resolves one move server-side, so the
game plays out in the browser at the bots' own pace (MCTS think time
dominates). A Pause/Resume control sits where the decision panel would
be. Use `--no-open` if you'd rather open the URL yourself.

The board and card faces come from the official VASSAL Deluxe 3.2 art in
`third_party/gmt-vassal/`: `install_vassal_ui_assets.py` maps the 110 card
numbers to engine card ids and writes `ui/assets/board.png`,
`ui/assets/cards/{id}.svg`, plus marker positions calibrated to that board
(`ui/assets/countries.json`, preferred by the UI when present).
`--fetch-board` downloads the board JPG from the official VASSAL module
when it is missing from the repo. That folder is gitignored — art is never
committed — so a fresh clone without it falls back to plain text cards and
no board.

Prefer your own print-and-play PDFs instead? `scripts/render_assets.py
--map-pdf "<your board pdf>" --cards-pdf "<your cards pdf>"` renders the
same `ui/assets/` layout from them, and `scripts/calibrate_countries.py`
derives matching marker positions.

## Add a new bot

Every player, human or bot, uses the same `Player` interface:
implement `choose_action(observation, history) -> Action`, returning one
action drawn from `observation.pending_decision.options`, then add one
branch to `build_player` in [src/main.py](src/main.py) mapping a new kind
name (for `--us`/`--ussr`) to it.  See [docs/BOTS.md](docs/BOTS.md) for the full `Player` contract and how the existing bots (`first`, `random`, `greedy`,
`mcts`, `llm`) are built.

## Status

All 110 cards are implemented, including every non-scoring card's event.
See [docs/LIMITATIONS.md](docs/LIMITATIONS.md) for any known limitations.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The public API, core types |
| [docs/CARDS.md](docs/CARDS.md) | Card data policy, the event layer, per-card coverage |
| [docs/BOTS.md](docs/BOTS.md) | The `Player` interface, physical mode, bot roadmap, training tooling |
| [docs/TESTING.md](docs/TESTING.md) | Replay logs, property tests, test-writing policy |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | What the engine does not model |
| [docs/STRATEGY.md](docs/STRATEGY.md) | The heuristics `GreedyPlayer` and the LLM prompt play by |
| [CONTEXT.md](CONTEXT.md) | The domain glossary (ubiquitous language) |
| [AGENTS.md](AGENTS.md) | Working notes for AI coding agents |

## Tests

620 tests. They are the reason the rest of this is trustworthy: the engine's
invariants are checked over random legal play rather than by example, and the
strategy rules in `GreedyPlayer` each have a test that fails if the rule is
removed.

```sh
pytest
python scripts/eval_mcts_vs_greedy.py --games 10 --seed 1 --sims 8
```

See [docs/BOTS.md](docs/BOTS.md) for MCTS knobs and the imperfect-info approximation. The MCTS bot is lookahead on top of greedy — stronger-than-greedy territory, not an expert claim.

## Train and evaluate bots

The autonomous tooling writes artifacts under `data/` (gitignored). Only the
self-play stack needs the `[rl]` extra (numpy + torch); the arena, the tuner,
and the value fit are pure-stdlib.

```sh
pip install -e ".[rl]"

python scripts/run_arena.py --help          # head-to-head / round-robin, Elo
python scripts/tune_greedy.py --help        # CEM over GreedyWeights
python scripts/train_value.py --help        # learned MCTS board value
python scripts/train_ppo.py --iterations 200 --games 64 --workers 8
python scripts/final_eval.py --help         # the reserved final seed bank
python scripts/extract_training.py --help   # expert-log agreement
```

See [docs/BOTS.md](docs/BOTS.md) for the arena, the league/promotion-margin
gating, and the reserved final-evaluation seed bank that must never feed
training.

`parsed/` holds five complete tournament games as engine-shaped JSON.
`scripts/replay_game.py` replays one with both hands hidden and asserts every
recorded board/VP snapshot, naming the decision where it diverges, and
`scripts/extract_training.py` turns the corpus into greedy's agreement with
the expert over hand-known decisions only.

## License

Code is released under the [MIT License](LICENSE). GMT VASSAL art under
`third_party/gmt-vassal/` is separately licensed — see that folder's LICENSE.

## Disclaimer

This is an unofficial, fan-made project. It is **not affiliated with,
endorsed by, or sponsored by GMT Games** or the designers of *Twilight
Struggle*. *Twilight Struggle* is a trademark of GMT Games, LLC.

**Dual license.** Engine and project code remain under the [MIT License](LICENSE).
Optional UI art under `third_party/gmt-vassal/` is **GMT Games copyrighted
material** redistributed only under that folder's own
[`LICENSE`](third_party/gmt-vassal/LICENSE) (personal, non-commercial use;
you must own a copy of the physical game). See also
[`SOURCE.md`](third_party/gmt-vassal/SOURCE.md) and
[`README.md`](third_party/gmt-vassal/README.md). Install into the gitignored
web UI with `scripts/install_vassal_ui_assets.py` (supports `--fetch-board`
from the official VASSAL module when the board JPG is absent).

The data files under `src/struggler/data/` still record only factual attributes
of the physical game — card names and numbers, Operations values, allegiance,
deck, country adjacency and Battleground status — re-entered independently.
The free-text `event_summary` fields describe this engine's own code, not
printed card text. No VASSAL Java/`.class` code is committed here.

Playing this engine is not a substitute for owning the game. If you enjoy
*Twilight Struggle*, buy a copy from [GMT Games](https://www.gmtgames.com/).

## Origins and differences

This project descends from
[alekpinel/struggler](https://github.com/alekpinel/struggler), started by
[Alejandro Pinel Martínez](https://github.com/alekpinel) in August 2026. That
work is the foundation; this repository carries it and adds substantially to it.
If you are looking for the original, start there.

### Inherited from the foundation

The engine's backbone — difficult work, used more or less as designed:

- the decision-stack model in `src/struggler/engine/core.py` (`Decision`,
  `DecisionKind`, the `_push`/`_advance` loop) and the type/observation layer;
- the board model: country adjacency, stability, the 8.1.5 DEFCON geography,
  control and scoring tiers;
- `src/struggler/data/` as facts-only card and country data (all 110 cards);
- the `RULES` constants and the M2/M3 card-event registry pattern, including
  the physical/replay mode that declares hidden hands on play.

### Added since

- **The browser UI** (`ui/`, ~5,300 lines) and `scripts/serve_ui.py`: map
  rendering from board coordinates, click-to-place with the influence and
  DEFCON tracks, dice and card reveals, the history feed, a draggable split
  between map and log, the start screen, a floating action box that
  shrink-wraps to its content, and a World view that fills the map's height so
  a narrow window leaves no band above the hand.
- **The VASSAL pipeline**: `install_vassal_ui_assets.py` maps the official
  module's art onto engine card ids, and `calibrate_countries.py` measures
  country rectangles from the board so markers land on the printed ovals.
- **MCTS support infrastructure**: the value function, training extractors, and
  the arena tooling in `scripts/`.
- **The LLM bot's board reporting and playbook** (`bots/llm/`).
- **The strategy work**: [`docs/STRATEGY.md`](docs/STRATEGY.md) codes the
  published *Twilight Strategy* general-strategy articles into the greedy
  heuristic, quoting each article's sentence beside the rule it produced.
- **The test suite** — 620 tests, including property-based invariants over
  random legal play and the strategy-rule tests.
- **The documentation set** ([`docs/`](docs/), five ADRs, `CONTEXT.md`).

The two projects have diverged far enough that this is better understood as a
project of its own sharing a lineage, rather than a fork tracking upstream.
Improvements are offered back where they are cleanly separable.

### Authorship

Original engine and data: [Alejandro Pinel
Martínez](https://github.com/alekpinel). Current development:
[Tim Shaw](https://github.com/timshaw40). Both names appear in the copyright
notice in [`LICENSE`](LICENSE), which must be preserved.
