# CONTEXT.md — domain glossary

The ubiquitous language of `struggler`, an engine for *Twilight Struggle*
(GMT Games, 2005). Use these terms (not synonyms) in issue titles, test
names, refactors, and commits. Where a term has a code counterpart, it is
named in backticks.

The engine's *design* vocabulary — the five mandates, the pending-decision
stack, the observation boundary — is in `docs/ARCHITECTURE.md` and is not
repeated here.

## Core types

- **Engine** (`Engine`) — the state machine. Agents only ever touch it
  through `pending_decision`, `legal_actions()`, `step(action)`, and
  `observe(side)`.
- **Side** (`Side`) — `US`, `USSR`, or `CHANCE` (the dice).
- **Decision** (`Decision`) — one pending choice: an `actor`, a
  `DecisionKind`, its legal `options`, and a `context` dict. Held on a
  stack; resolving one can interrupt with sub-decisions.
- **Action** (`Action`) — a `kind` plus a `payload`, drawn from
  `pending_decision.options`.
- **Observation** (`Observation`) — a side-scoped view of the game, the
  only sanctioned way an agent sees state.
- **Player** (`Player`) — the bot/human contract:
  `choose_action(observation, history) -> Action`.
- **Game state** — flat, JSON-primitive; `serialize()`/`deserialize()`
  round-trip exactly.

## The turn

- **Turn** — one round of the game, made of a headline phase then action
  rounds.
- **Headline** — each side plays one card face-down; both are revealed
  simultaneously and resolve in descending Ops (ties to the US).
- **Action round** — one card play by one side; sides alternate.
- **Phasing side** — whose card play is resolving right now
  (`Engine.phasing_side`). Decides who loses at DEFCON 1.
- **Sit out** — a side with no legal play (empty hand, no China Card) skips
  its remaining action rounds, which transfer to the opponent (4.5-D).

## Cards and Operations

- **Ops** — a card's Operations value. **Effective Ops** is the card's Ops
  after per-turn modifiers, never below 1.
- **Ops modifier** — `Containment`/`Brezhnev` (+1) and `Red Scare` (-1).
  Applies "for all purposes": card plays, event-granted Ops, discard
  thresholds (7.4.2).
- **Ops spend** — the sequence of atomic single-point decisions from one
  card played for Ops: influence placement, coup, or realignment.
- **Influence** — the tokens placed on countries. **Stability** is a
  country's printed number; a side **Controls** a country when its
  influence margin is at least the stability.
- **Battleground** — a country whose control matters for scoring tiers.
- **Region tier** (`ScoringTier`) — `PRESENCE`, `DOMINATION`, or `CONTROL`:
  the set a side *Controls* in a region. 10.1.1 Control of a region = more
  countries than the opponent *and* all its Battlegrounds.
- **Event** — a card's printed effect (`events.py`). A card can be played
  for its event or its Ops.
- **Permanent ("underlined") event** — an event whose effect persists; the
  card sits face-up beside the board in `in_play_cards` (2.2.5), not in the
  discard pile, until the effect is cancelled or consumed.
- **`remove_after_event`** — a card flag: the card leaves the game after
  its event fires, rather than going to the discard pile.
- **Scoring card** — a region-scoring card; may not be held across a turn
  (4.5-D).
- **China Card** — passes to the opponent when played, is never discarded,
  and can never be compelled (9.8). Earns +1 Ops when all Ops are spent in
  Asia.
- **DEFCON** — the 5→1 escalation track. The **phasing side loses** the
  moment it reaches 1 (8.1.3), even if the opponent's choice moved it.
- **VP** — victory points, US-positive (a positive total favors the US).
- **Military Ops** — the military-operations track.
- **Space Race** — the 1–8 track; reaching box 8 grants an absolute 8
  action rounds per turn (6.4.4). A card is **spaceable** when
  `Engine._can_space_race` allows it for that side: the China Card, UN
  Intervention, scoring cards, and the side's own events are not, so a
  "spaceable" card is the opponent's event one cannot otherwise mitigate.
- **Influence vs. realignment "up to" spends** — a player may stop an
  influence or realignment spend early, once one point/roll is down
  (6.1.3 / 6.2.2); the decision offers an explicit stop option.

## Piles and modes

- **Piles** — draw pile, discard pile, removed-from-game, and in-play
  (`in_play_cards`).
- **Physical mode** — playing a real tabletop board; the engine cannot see
  hidden hands, so cards are **declared on play**.
- **Replay mode** — driving a recorded expert game through the engine, both
  hands hidden and declared on play.
- **Golden replay** (`tests/replays/*.json`) — a seed, a pinned action
  sequence, and full-state **checkpoints** at fixed steps; a diffable
  regression that pins engine behaviour.

## Bots and evaluation

- **Bot kinds** (`build_player`) — `human`, `first`, `random`, `greedy`,
  `mcts`, `llm`, `rl`.
- **Anchor** — the fixed opponent (greedy) a learned bot is measured
  against, so progress is not measured against a moving target.
- **Arena** (`arena.py`) — the matchup/eval backbone. Matches are
  **side-swapped** (each seed played from both seats) so side asymmetry
  cancels. **Elo** and the **Wilson interval** read results.
- **Ladder** — a fixed set of opponents used as the fitness environment for
  tuning; gating is always head-to-head, never win rate vs one opponent.
- **Promotion margin** — a policy checkpoint is promoted only when
  paired, side-swapped wins minus losses reaches `--gate-margin`.
- **Final bank** — seeds at or above `arena.FINAL_EVAL_SEED_BASE`, reserved
  for `scripts/final_eval.py`; never used for training or gating.
- **League** — the pool of past promoted checkpoints a learner plays
  against.
- **Value net** (`bots/value.py`) — a learned, antisymmetric board value
  (`value(US) + value(USSR) = 1`) used to replace greedy rollouts in MCTS.
- **Extraction** (`scripts/extract_training.py`) — replaying expert logs to
  measure a bot's **agreement**. It stops at the **first divergence**
  (engine-vs-log mismatch), and flags **`hand_known`** rows, because in
  replay mode the candidate list is a superset of the hidden hand and
  agreement there is not comparable.
