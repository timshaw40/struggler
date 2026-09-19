# ADR-0003: Hidden hands are declared on play (physical and replay mode)

- **Date**: 2026-09-18
- **Status**: Accepted

## Context

Two modes drive the engine with a hand whose real contents the engine
cannot see:

- **Physical mode** — a human plays a real tabletop copy; the engine tracks
  the board while the operator tells it which card was played.
- **Replay mode** — a recorded expert game is driven through the engine to
  measure agreement (`scripts/extract_training.py`); both hands are hidden.

The engine still has to enforce legality, offer card choices, and produce a
replayable log — without knowing the hand it is choosing from.

## Decision

A hidden hand is modeled as a **placeholder pool** (`HIDDEN_CARD` slots, see
`Engine._declares` / `_physical_hand_candidates`), and the player **declares**
each card as it is played. The engine trusts the declaration, then removes
the corresponding placeholder and continues with real card identity from
that point.

Consequences of the trust model are made explicit rather than hidden:

- A declared play is not independently verified against the (unknown) hand.
  The candidate list offered is a **superset** of what was actually held;
  `extract_training.py` flags such rows `hand_known = False` and excludes
  them from agreement, because agreement there is not comparable.
- MCTS **refuses** to run in physical mode (`bind_engine` raises): a
  physical hand's real ids sit in `hidden_pool`, which search does not
  redact, so searching would peek. Use greedy as the physical opponent.
- In replay mode there is no hand to check, so a declared combo
  (e.g. UN Intervention) is trusted outright.

## Alternatives considered

- **Require the full hand up front** — rejected: it is not how a tabletop
  game works, and it leaks information the engine should not need.
- **Allow search over the declared pool** — rejected: it would let the bot
  see cards it cannot legally know (mandate #4).
- **Track only counts, not placeholders** — rejected: the pool is what makes
  the *candidate list* and the log's card identity coherent.

## Consequences

- The engine can run a game it cannot fully verify; this is a documented
  simplification (`docs/LIMITATIONS.md`, `docs/BOTS.md`), not an oversight.
- Test helpers must treat the placeholder pool as a real card location
  (`tests/conftest.py`'s `cards_in_play`).
