# Known limitations

What this engine does not model, or models in a deliberately simplified
way.

## Rules fidelity

- **Aldrich Ames Remix**'s "USA reveals their hand face-up until end of
  turn" is modeled as a momentary reveal — the decision options — rather
  than an ongoing visibility grant surfaced through `observe()`. Modeling
  it properly would add a new hidden/shared-visibility field to the public
  `Observation` API, which is a larger change than a card-logic fix.
- **The Space Race accepts a narrower card set than the printed rules do.**
  `Engine._can_space_race` refuses the China Card, UN Intervention, scoring
  cards, and (with events on) the acting side's own events; see
  [CARDS.md](CARDS.md) for the per-card reasons, three of which are the
  cards' own printed text. The fourth — one's own events — is a deliberate
  **engine-level** restriction rather than a bot heuristic, so it changes
  play for every `Player`, human included. The justification is that the
  alternative is not a fair choice for any non-search player either: a bot
  that scores a root action by rolling out with a map-influence evaluator
  cannot see a card's deferred value, and empirically spaced the China Card
  on 6 of 40 seeds at one live decision. Removing the option makes the
  mistake unrepresentable rather than merely discouraged. If a future
  evaluator can price a held card's deferred value, this is the first
  limitation to lift.

## Data

- **`event_summary` can drift.** The field is a hand-maintained paraphrase
  of what `events.py` does for a card (see [CARDS.md](CARDS.md)); nothing
  automatically checks it against the code. It feeds the LLM bot's prompt,
  so drift degrades that bot's understanding rather than the engine's
  behavior.

## Physical mode

Physical mode makes one seat a real human playing the physical board, with
the engine as referee. Some things the engine simply cannot know.

- **The must-play-a-scoring-card rule is not enforced** for a hand the
  engine cannot see. Every not-yet-accounted-for card is offered at
  `ACTION_ROUND_PLAY`, and the physical player — who can see their own hand
  — is trusted to honor the rule, the same trust model any human player
  already gets for rules `HumanPlayer` does not independently re-verify.

  The one place it *is* enforced: a trapped side's Ops-2+-less round
  (`_push_trap_step`'s fallback) offers any scoring-card candidates as a
  genuine `QUAGMIRE_DISCARD` decision (`context["forced_scoring"]`) instead
  of auto-resolving one the way the non-physical path does. Auto-filing
  would risk firing a `hidden_pool` card that is not actually in that hand,
  since the pool is a superset, not a location.
- **Our Man in Tehran is a no-op.** It peeks at the *draw pile's* real
  contents, which physical mode makes unknown to the engine itself, not
  merely hidden from a player, so there is nothing to queue instead of
  `HIDDEN_CARD` placeholders.

Every other hand-touching event *is* wired for a physical hidden hand,
including the three where the deciding side must inspect a hand it cannot
see (Missile Envy, Aldrich Ames Remix, The Cambridge Five). How each one is
routed to the operator is described in [BOTS.md](BOTS.md).

## Bots

- **`GreedyPlayer` scores the core decision kinds** (`PLACE_INFLUENCE`,
  `COUP_TARGET`, `REALIGNMENT_TARGET`, `OPS_TYPE`, `HEADLINE_PLAY`,
  `ACTION_ROUND_PLAY`, `PLAY_MODE`) plus one card-specific `EVENT_CHOICE`
  (Aldrich Ames Remix). Other event-specific decision kinds fall back to the
  first legal option. This is approved scope, not an oversight: extend
  `_SCORERS` as each one earns a heuristic worth writing. Note the fallback is
  a measurable strength debt — `MCTSPlayer`'s rollouts run on this policy, so
  a missing heuristic weakens search too.
- **`LLMPlayer` resends its conversation every call**, now with a
  character-budget safety valve: `STRUGGLER_LLM_CONTEXT_CHARS` (or the
  `max_context_chars` constructor arg) drops the oldest turns with a marker
  when exceeded; 0 disables it. What gets *persisted* per turn is still
  trimmed to the event delta only (`prompt.build_history_entry`); the board
  report, hand dossier, and cards-in-play a call was answered against are
  recomputed fresh each call. With compaction off, a long game can still
  approach the model's context or tokens-per-minute limits.
- **Resuming an `LLMPlayer` from its log does not restore `_rng`'s exact
  position.** Acceptable because `_rng` is only consulted on the fallback
  path (picking *a* legal action after total LLM failure); mandate #3's
  determinism guarantee is about the engine's own RNG, not a bot's internal
  fallback RNG.
- **The LLM plan-queue matcher checks legality, not optimality.** A step is
  re-validated against the live decision's *kind* and options before it is
  consumed (`_try_consume_plan`), so an interruption that changes the decision
  kind mechanically drops the stale plan. But a step of the same kind that
  happens to stay legal after a CHANCE roll or opponent reply can still be
  consumed. The system prompt asks the model to avoid this; the mechanism
  itself does not enforce intent.
- **The OpenAI adapter's exact SDK call shape is unverified** against a
  live API. The Anthropic adapter's is current.
- **`MCTSPlayer` cannot play physical mode.** `bind_engine` refuses it: a
  physical hand's real card ids live in `hidden_pool`, which the bot's
  determinize step does not redact, so searching a physical game would read
  identities nobody is supposed to know. Use `--us greedy --ussr mcts`
  ordering (`--physical <side>` with greedy) instead.
- **`MCTSPlayer` excludes invalid simulations (strict by default).** A
  determinized clone can be internally inconsistent when the unknown pool
  comes up short; `strict=True` raises `ShortPoolError` and the simulation is
  dropped rather than scored, with per-search diagnostics
  (`stats["short_pool"]/["action_miss"]/["exception"]`) instead of a silent
  neutral 0.5. If every simulation is invalid the player falls back to the
  heuristic. Pass `strict=False` for the old recycle-and-score-0.5 behaviour
  (research/debugging only — it fabricates illegal positions).
