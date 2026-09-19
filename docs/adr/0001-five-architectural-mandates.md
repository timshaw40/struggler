# ADR-0001: The five architectural mandates

- **Date**: 2026-09-18
- **Status**: Accepted (non-negotiable)

## Context

This engine exists so that agents — human, scripted, or learned — can be
trained and evaluated against *Twilight Struggle* without any of them
receiving information or affordances a player at the table would not have.
Several obvious ways to build a card-game engine quietly violate that, and
each has been proposed or prototyped at some point.

## Decision

Five properties are non-negotiable. `docs/ARCHITECTURE.md` is their
normative text; this ADR records the decision and the alternatives.

1. **Pending-decision stack, not "one turn = one action".** Resolving a
   decision may push interrupting sub-decisions that must drain first.
2. **Atomic action space.** "Tens of options per decision, never thousands."
   A 4-Ops spend is four one-point decisions, not one composite choice.
3. **Seeded, injectable RNG; chance is a decision.** Every die is a logged
   `(Decision, Action)` with `actor=Side.CHANCE` — never resolved silently.
4. **Per-player observation function.** `observe(side)` is the only view;
   hidden information is absent from the returned object, not masked.
5. **Flat, serializable state.** The wire format *is* the internal shape.

An implementation that violates any of these is wrong regardless of whether
it passes the tests.

## Alternatives considered

- **Single-action "pick one move" model** — simpler to implement and to
  learn against, but it cannot represent event interrupts and it collapses
  the action space. The reference implementation consulted for
  cross-checking (`glowsplint/twilight-struggle-py`) uses it; we
  deliberately do not.
- **Composite action space** (`C(countries, 4)` for a 4-Ops placement) —
  rejected as intractable for search and RL.
- **Omniscient or "redact flag" observation** — rejected: a single object
  with hidden fields zeroed leaks their existence through shape (mandate #4).
- **Silent RNG inside `step()`** — rejected: it would make replay logs
  non-reproducible and force a separate RNG trace to stay in sync.

## Consequences

- Replay logs are complete, deterministic, and diffable — the primary test
  strategy (`docs/TESTING.md`).
- Search/training can clone state via `serialize()`, with no bespoke copy path.
- Bots are structurally limited to player-legal information; a stronger bot
  cannot cheat its way there.
