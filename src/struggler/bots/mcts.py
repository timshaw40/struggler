"""MCTSPlayer: root UCT search with greedy rollouts on determinized clones.

Search never steps the live engine. Each simulation:

1. `Engine.serialize()` the bound live game, redact hidden card identities,
   `Engine.deserialize()` into a clone, and reseed the *clone's* RNG from this
   player's own seeded RNG (never the live engine's).
2. Play one root action, then `GreedyPlayer` for both seats (DEFCON suicide
   avoidance included) until terminal or `rollout_depth`.
3. Score: win 1 / draw 0.5 / loss 0, or `board_value` at the depth cap.

Imperfect-info approximation
----------------------------
`observe()` hides the opponent's hand and the draw pile. The clone fills those
slots by shuffling the unknown pool: cards that have entered the deck by the
current turn, minus this side's hand, the discard, the removed pile, and
in-flight cards already named by public/frozen fields (own headline, resolving
headlines, Our Man in Tehran's queue). Secret opponent headline (picked, not
yet revealed) is resampled the same way.

Search therefore never copies opponent-hand or draw-pile *identities* from the
live serialize dict. It does not claim expert strength: this is lookahead on
top of greedy, not a trained policy.

Physical mode is refused at bind time: a physical hand's real ids sit in
`hidden_pool`, which this bot does not redact, so searching there would peek.
"""

from __future__ import annotations

import math
import random
from typing import Sequence

from struggler.bots.greedy import GreedyPlayer, board_value
from struggler.engine import Action, Engine, Observation, Period, Side
from struggler.engine.cards import cards_entering, load_cards
from struggler.engine.player import Event
from struggler.engine.rules import RULES

_CARDS = load_cards()
_CHINA = RULES["china_card_id"]
_VALUE_SCALE = 20.0


def _entered_ids(turn: int, include_optional: bool) -> list[str]:
    ids = list(cards_entering(_CARDS, Period.EARLY_WAR, include_optional))
    if turn >= 4:
        ids += cards_entering(_CARDS, Period.MID_WAR, include_optional)
    if turn >= 8:
        ids += cards_entering(_CARDS, Period.LATE_WAR, include_optional)
    return ids


def _frozen_ids(data: dict, me: str) -> list[str]:
    """Cards whose identities stay put so the decision stack stays consistent."""
    frozen: list[str] = []
    headline = data.get("headline") or {}
    if headline.get(me):
        frozen.append(headline[me])
    for pair in data.get("headline_pending") or []:
        frozen.append(pair[1])
    frozen.extend(data.get("our_man_queue") or [])
    frozen.extend(data.get("our_man_kept") or [])
    return frozen


def determinize(data: dict, me: str, rng: random.Random) -> dict:
    """Overwrite hidden opponent-hand / draw-pile (and secret headline) slots.

    Reads only *lengths* of those hidden fields, never their card ids.
    """
    opp = "USSR" if me == "US" else "US"
    n_hand = len(data["hands"][opp])
    n_draw = len(data["draw_pile"])
    secret_hl = bool(data.get("headline", {}).get(opp)) and not data.get("headline_resolving")

    used = set(data["hands"][me])
    used.update(data["discard_pile"])
    used.update(data["removed_cards"])
    used.update(_frozen_ids(data, me))
    used.add(_CHINA)

    pool = [cid for cid in _entered_ids(data["turn"], data.get("include_optional", True)) if cid not in used]
    rng.shuffle(pool)

    need = n_hand + n_draw + (1 if secret_hl else 0)
    # ponytail: the pool can come up short on mid-resolution accounting drift;
    # recycle rather than crash, illegal clones then score 0.5 in the rollout
    # try/except.
    if len(pool) < need:
        pool = (pool * (need // max(len(pool), 1) + 1))[:need]

    data["hands"][opp] = pool[:n_hand]
    data["draw_pile"] = pool[n_hand : n_hand + n_draw]
    if secret_hl:
        data["headline"][opp] = pool[n_hand + n_draw]
    return data


def _position_value(engine: Engine, side: Side, greedy: GreedyPlayer) -> float:
    if engine.is_terminal:
        winner = engine.winner
        if winner is side:
            return 1.0
        if winner is None:
            return 0.5
        return 0.0
    return 0.5 + 0.5 * math.tanh(board_value(greedy.weights, engine.board, side) / _VALUE_SCALE)


class MCTSPlayer:
    """Root-UCT player. Call `bind_engine` once before `choose_action`."""

    def __init__(
        self,
        seed: int,
        sims: int = 16,
        rollout_depth: int = 16,
        uct_c: float = 1.4,
    ) -> None:
        self._rng = random.Random(seed)
        self.sims = sims
        self.rollout_depth = rollout_depth
        self.uct_c = uct_c
        self._greedy = GreedyPlayer()
        self._engine: Engine | None = None

    def bind_engine(self, engine: Engine) -> None:
        if engine.physical_mode:
            raise RuntimeError(
                "MCTSPlayer does not support physical mode: the physical hand's "
                "real card ids live in hidden_pool, which determinize does not "
                "redact, so search there would peek. Use greedy for the physical "
                "opponent."
            )
        self._engine = engine

    def choose_action(self, observation: Observation, history: Sequence[Event]) -> Action:
        decision = observation.pending_decision
        options = decision.options
        if len(options) == 1:
            return options[0]
        # Opening setup is a known line, not a search problem: 16 noisy
        # rollouts will not rediscover 4 Poland / 4 E.Ger / 1 Yugoslavia.
        if decision.context.get("setup"):
            return self._greedy.choose_action(observation, history)
        if self._engine is None:
            raise RuntimeError("MCTSPlayer.bind_engine(engine) must be called before choose_action")

        visits = [0] * len(options)
        totals = [0.0] * len(options)
        untried = list(range(len(options)))
        self._rng.shuffle(untried)
        snapshot = self._engine.serialize()

        for sim in range(max(1, self.sims)):
            if untried:
                idx = untried.pop()
            else:
                # sim counts the searches already finished, i.e. this node's
                # visit total: exactly what UCB1's parent term wants.
                idx = max(
                    range(len(options)),
                    key=lambda i: totals[i] / visits[i]
                    + self.uct_c * math.sqrt(math.log(sim) / visits[i]),
                )
            totals[idx] += self._rollout(snapshot, observation.side, options[idx])
            visits[idx] += 1

        best = max(
            (i for i in range(len(options)) if visits[i] > 0),
            key=lambda i: (visits[i], totals[i] / visits[i]),
        )
        return options[best]

    def _rollout(self, snapshot: dict, side: Side, action: Action) -> float:
        try:
            clone = Engine.deserialize(determinize(snapshot, side.value, self._rng))
            clone._rng.seed(self._rng.getrandbits(64))
            if action not in clone.pending_decision.options:
                return 0.5
            clone.step(action)
            steps = 0
            while not clone.is_terminal and steps < self.rollout_depth:
                pending = clone.pending_decision
                if pending.actor is Side.CHANCE:
                    clone.step(pending.options[0])
                else:
                    obs = clone.observe(pending.actor)
                    clone.step(self._greedy.choose_action(obs, ()))
                steps += 1
            return _position_value(clone, side, self._greedy)
        except (ValueError, RuntimeError, KeyError):
            return 0.5
