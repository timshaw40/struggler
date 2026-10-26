# Browser experience

The local server saves atomically under `data/games/` at the end of each
request: the human's move is saved before the bots reply, the bots' replies
once they finish, and each watch-mode step as it resolves. A save encodes only
the history added since the last one, and the undo point is stored as a
history prefix length rather than a second copy of the game.
Override this with `--library-dir`. Saves contain private hands and engine RNG;
keep that directory private. The browser library receives metadata only.
Starting a new game preserves the previous game. A server restart restores the
most recently saved game, including its pending decisions, history, and undo,
but only while that game is unfinished and only when the command line names no
game: `--us`, `--ussr`, `--seed`, `--events`/`--no-events` or `--fresh` start
the game they describe instead (`serve_ui.parse_args`). Older saves stay in the
library either way.
With no seat flags the opponent is `deep` when `struggler.bots.deep.missing()`
finds its packages, model and engine installed, and `mcts` otherwise; the
server says which, and why, at start-up.
Bot instances are reconstructed: their private search RNG or conversation
memory is not preserved. Save errors appear in the status bar.

Connection recovery reads `/position` before resuming bot steps. It never
replays a failed move request automatically. A retry button remains available.
`/position` is read-only; `/state` retains the existing advancing behavior.

The strategy overview projects each region scored separately now, using the
engine scoring rules and public effects. These values are not an AI evaluation
or a forecast of future cards. Scoring previews must not consume live effects.

Action previews explain immediate costs, control changes, and event handling.
Optional influence planning validates an entire sequence on a cloned engine,
including frozen reachability and changing influence costs. The map displays
staged additions and projected scoring. Commit revalidates the whole sequence
before modifying the real game; one undo restores the whole batch and reply.
Direct map placement remains the default. Setup placement is not batch planned.

Implementation: `src/struggler/web_support.py`, `scripts/serve_ui.py`, and
`ui/experience.js`. Regression coverage: `tests/test_web_experience.py`.

## Dice presentation order

The engine resolves and saves chance immediately; the client presents that
resolved snapshot only after its dice animation finishes. Influence counters,
tracks, strategic scores, history, next decisions, and game-over dialogs all
wait at the same presentation barrier. Resizing or opening sidebar detail must
not reveal the result early. A realignment received across two polls waits for
both dice, and no bot roll is dropped because of an animation backlog.
Clicks cannot dismiss dice while they are tumbling; after landing, the player
can dismiss the result or let it close automatically. Bot polling waits for
queued animations before requesting another step. Undo, game loading, and new
games wait for queued animations before replacing the position.

`tests/browser/dice_order.cjs` runs the production render and animation queue
with controllable dice promises. It covers all roll types, split realignments,
watch mode, batched rolls, backlog, resize callbacks, and early dismissal.
Run it with `node tests/browser/dice_order.cjs`; `test_dice_ui.py` also invokes
it when Node.js is available.
