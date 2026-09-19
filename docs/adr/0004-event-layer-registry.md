# ADR-0004: The event layer is a registered, data-driven decision source

- **Date**: 2026-09-18
- **Status**: Accepted

## Context

110 cards, each with a printed event. Implemented naively, card events
become a large `if/elif` cascade inside the engine's play handler, each
branch mutating state directly and enqueueing decisions ad hoc. That
entangles card data with engine dispatch, makes "which events are
implemented?" unanswerable without reading the cascade, and makes it easy
to resolve an event *silently* — violating mandate #3's "chance is a
decision" and mandate #1's interrupt handling.

## Decision

Each implemented event is a function registered by decorator
(`@event("Card_Id")` → the `EVENTS` registry in `events.py`). The engine
resolves an event by looking it up, checking `eligible`, and calling
`resolve`. Events push their choices and every die roll through the same
decision stack as everything else (`push_event_choice`, `push_event_operations`,
`EVENT_CHOICE` branches routed by `CHOICE_ROUTERS`, so the stack stores only
an event id and the chosen option — never a function).

Card *facts* live in `src/struggler/data/cards.json`; each card's
`event_summary` is engine-derived documentation of what `events.py` does.

## Alternatives considered

- **`if/elif` cascade in the engine** — rejected: it couples card data to
  dispatch and hides the implemented-event set.
- **Put event logic in `cards.json`** — rejected: it is code, not data, and
  cannot express decisions/interrupts.
- **Store callables on the decision stack** — rejected: the stack must stay
  flat and serializable (mandate #5).

## Consequences

- "Is this event implemented?" is a registry lookup; `None` means it
  fizzles (a no-op discard).
- Every event's chance outcomes are logged, so `events.json` is replayable.
- `event_summary` can drift from `events.py`; there is no automated sync
  check (see `docs/LIMITATIONS.md`).
