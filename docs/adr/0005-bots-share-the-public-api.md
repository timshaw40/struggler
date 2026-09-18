# ADR-0005: Every bot shares the public player API

- **Date**: 2026-09-18
- **Status**: Accepted

## Context

The point of the project is to train and evaluate agents against a rules
engine (`docs/BOTS.md`). A stronger bot is trivial to write if it may read
the opponent's hand, the draw pile, or the discard-to-come — and such a bot
looks great on an Elo board while measuring nothing about play. The engine
also has to *enforce* that no bot cheats, not merely ask it not to.

## Decision

Every agent — `human`, `first`, `random`, `greedy`, `mcts`, `llm`, `rl` — is
a `Player` that sees only `observe(side)` and returns an `Action` drawn from
`observation.pending_decision.options` (mandate #4). No bot has an engine
handle that exposes hidden state. Search builds clones by
`serialize()`/`deserialize()` and determinizes the unknown pool itself,
reading only hidden **lengths** — and it refuses physical mode outright
([ADR-0003](0003-hidden-hands-declared-on-play.md)).

Evaluation tooling keeps the same discipline: `arena.py` matches are
side-swapped so side asymmetry cancels, gating is head-to-head on a
held-out seed bank, and seeds at or above `arena.FINAL_EVAL_SEED_BASE` are
reserved for `final_eval.py` and never used for training or promotion.

## Alternatives considered

- **Give search privileged access to hidden state** — rejected: it would
  inflate measured strength and violate mandate #4. Determinize-then-search
  is an approximation, documented as such.
- **Let a physical-mode bot read `hidden_pool`** — rejected: `bind_engine`
  raises instead.
- **Win-rate-vs-one-opponent as the promotion gate** — rejected: it
  saturates once the bot passes that opponent (documented in
  `docs/BOTS.md`); gating uses a margin on paired, side-swapped games.

## Consequences

- Reported strength is comparable across bots and honest about information.
- Search throughput is lower than a cheating search would be; this is the
  intended tradeoff.
- MCTS-vs-greedy is "stronger than greedy" territory, explicitly not an
  expert claim.
