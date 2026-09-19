# ADR-0002: Docs are the binding contract

- **Date**: 2026-09-18
- **Status**: Accepted

## Context

This is a rules engine: correctness *is* the product, and the rules are
subtle (see `docs/CARDS.md` and the worked rulebook examples cited in
`docs/ARCHITECTURE.md`). A future agent — or a future human — changing
behaviour without knowing what the current behaviour is *supposed* to be
will silently regress it. Prose that drifts from code is worse than no
prose, because it is trusted.

## Decision

`docs/` is the binding contract, not background reading. Read the relevant
document before changing the area it covers, and update it in the same
commit as the change. `AGENTS.md` carries the map (which document to read
before touching which area); `CLAUDE.md` points at `AGENTS.md`.

The five mandates (`docs/ARCHITECTURE.md`, recorded in
[ADR-0001](0001-five-architectural-mandates.md)) are the one part of the
contract that cannot be changed by a normal PR.

## Alternatives considered

- **Code as the contract, docs as orientation** — rejected: the subtle
  rules (e.g. 7.4's aggregated Ops modifiers, 8.1.3's phasing-player loss)
  are not recoverable from the code's behaviour without the rule citation.
- **Wiki / external docs** — rejected: they drift and cannot be updated in
  the same commit.

## Consequences

- A behaviour change that does not touch its document is an incomplete change.
- Deliberate simplifications are recorded in `docs/LIMITATIONS.md` and must
  be read *before* "fixing" one.
- Golden replays (`docs/TESTING.md`) mechanically pin behaviour the prose
  describes; an intentional change regenerates them in the same commit.
