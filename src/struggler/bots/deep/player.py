"""`DeepPlayer`: DeepStruggle's network choosing among struggler's options.

Per decision:

1. **Boundary.** At each of this seat's own headline picks and action-round
   starts, the player keeps the observation it was shown, plus how long the
   history was then. That is the last clean boundary it saw.
2. **Determinize.** The opponent's hand and the draw pile are unknown. They
   are filled from the unknown pool by public counts, with one constraint: any
   card the opponent has since played from hand (public in `history`) is put
   in its hand, so the replay can play it.
3. **Rebuild and replay.** `boundary.to_save` + `replay.replay` on the
   backend, through every event since the boundary.
4. **Check sync.** DeepStruggle must now be asking this seat, and its board
   must match the observation. Otherwise: fall back.
5. **Ask the network** with this seat's observation; map every struggler
   option to the flat slot(s) it stands for and take the best. An option made
   of several slots (struggler's "ops" before its Ops type is chosen) scores
   their combined probability. Options with no mapping are never chosen.
6. **Fall back** to `GreedyPlayer` whenever any step fails, and count why in
   `stats`.

It reads only `observe(side)` and `history` (mandate #4, ADR-0005). It keeps
per-game state (the boundary), like the LLM player; a fresh instance plays
greedy until its next boundary.
"""
from __future__ import annotations

import math
import os
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any, Sequence

from struggler.bots.deep import backend as B
from struggler.bots.deep import boundary, safety
from struggler.bots.deep.choices import choice_slots
from struggler.bots.deep.ids import IdMap, ds_side
from struggler.bots.deep.replay import Desync, Partial, replay
from struggler.bots.greedy import GreedyPlayer
from struggler.bots.mcts import _entered_ids
from struggler.engine import Action, Observation, Side
from struggler.engine.cards import load_cards
from struggler.engine.player import Event
from struggler.engine.types import DecisionKind as K

_CARDS = load_cards()
_COUNTRY_KINDS = {K.PLACE_INFLUENCE, K.COUP_TARGET, K.REALIGNMENT_TARGET, K.EVENT_INFLUENCE,
                  K.WAR_TARGET}
_OPS_SLOTS = [B.PLAY_MODE_OFFSET + r for r in (B.RES_OPS_INFLUENCE, B.RES_OPS_COUP, B.RES_OPS_REALIGN)]
_OPS_TYPE_RES = {"influence": B.RES_OPS_INFLUENCE, "coup": B.RES_OPS_COUP,
                 "realignment": B.RES_OPS_REALIGN}
_OPS_TYPE_OPMODE = {"influence": 0, "coup": 1, "realignment": 2}


class OnnxPolicy:
    """A DeepStruggle ONNX export (`tools/export_onnx.py`): inputs `obs`
    [batch, width] float32 and `mask` [batch, 220] uint8; outputs `logits`,
    `v_win`, `v_vp`. Its metadata says which observation it reads."""

    def __init__(self, path: str) -> None:
        import onnxruntime as ort

        self.session = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        meta = self.session.get_modelmeta().custom_metadata_map
        if meta.get("ts.merged_influence", "false") == "true":
            raise ValueError("merged-influence (E4.1) networks are not supported by the replay's action view")
        self.obs_features = int(meta.get("ts.obs_features", "0"))
        self.label = meta.get("ts.label", os.path.basename(path))

    def logits(self, obs: Sequence[float], mask: Sequence[int]) -> list[float]:
        import numpy as np

        out = self.session.run(["logits"], {
            "obs": np.asarray(obs, dtype=np.float32)[None, :],
            "mask": np.asarray(mask, dtype=np.uint8)[None, :],
        })
        return out[0][0].tolist()


@dataclass
class _Boundary:
    facts: boundary.PositionFacts
    history_len: int


class DeepPlayer:
    def __init__(self, model_path: str | None = None, *, seed: int = 0,
                 engine: B.DeepEngine | None = None, policy: OnnxPolicy | None = None) -> None:
        path = model_path or os.environ.get("STRUGGLER_DEEP_MODEL") or _default_model()
        if policy is None and not os.path.exists(path):
            raise ValueError(f"no deep model at {path}: run scripts/install_deep.py --model <file.onnx>, "
                             "or set STRUGGLER_DEEP_MODEL")
        self.policy = policy or OnnxPolicy(path)
        self.engine = engine or B.load_backend()
        self.ids: IdMap | None = None
        self.fallback = GreedyPlayer()
        self.rng = random.Random(seed)
        self._boundary: _Boundary | None = None
        self.stats: Counter = Counter()
        #: Take an outright win before asking the network (`safety.py`).
        self.take_forced_wins = True

    def bind_engine(self, engine: Any) -> None:
        if engine.physical_mode:
            raise RuntimeError("DeepPlayer does not support physical mode (a physical hand cannot be determinized)")
        if "Chinese_Civil_War" in engine.board.countries:
            raise RuntimeError("DeepPlayer needs the Chinese Civil War option off (DeepStruggle does not model it)")
        if not engine.events_enabled:
            raise RuntimeError("DeepPlayer needs the event layer on (DeepStruggle always plays events)")

    # -- the decision ---------------------------------------------------------

    def choose_action(self, observation: Observation, history: Sequence[Event]) -> Action:
        decision = observation.pending_decision
        if len(decision.options) == 1:
            return decision.options[0]
        self._note_boundary(observation, history)
        try:
            action = self._network_action(observation, history)
            self.stats["network"] += 1
            return action
        except (Desync, boundary.UnmappedEffect, _NoMapping, ValueError, KeyError) as exc:
            reason = getattr(exc, "reason", None) or type(exc).__name__
            self.stats[f"fallback:{reason}"] += 1
            return self.fallback.choose_action(observation, history)

    def _note_boundary(self, obs: Observation, history: Sequence[Event]) -> None:
        kind = obs.pending_decision.kind
        own_headline = kind is K.HEADLINE_PLAY and obs.phase == "headline"
        if kind is K.ACTION_ROUND_PLAY or own_headline:
            self._boundary = _Boundary(self._facts(obs), len(history))

    def _network_action(self, obs: Observation, history: Sequence[Event]) -> Action:
        if self._boundary is None or obs.phase not in ("headline", "action_rounds"):
            raise _NoMapping("no_boundary")
        if self.ids is None:
            from struggler.engine import Engine
            self.ids = IdMap.build(self.engine.country_names(),
                                   [c for c in Engine.new_game(seed=0, include_ccw=False).board.countries])
        events = list(history[self._boundary.history_len:])
        facts = self._determinized(self._boundary.facts, obs.side, events)
        self.engine.load_save(boundary.to_save(facts, self.ids, rng_state=self.rng.getrandbits(63)))
        result = replay(self.engine, events, self.ids)
        self._check_sync(obs)
        player = ds_side(obs.side)
        scores = self._option_slots(obs, result.partial)
        win = self._outright_win(obs, player, scores)
        if win is not None:
            self.stats["forced_win"] += 1
            return obs.pending_decision.options[win]
        mask = list(self.engine.mask())
        for slots in scores.values():
            for s in slots:
                mask[s] = 1
        logits = self.policy.logits(self.engine.observation(player, self.policy.obs_features), mask)
        best, best_score = None, -math.inf
        for index, slots in scores.items():
            score = _logsumexp([logits[s] for s in slots])
            if score > best_score:
                best, best_score = index, score
        if best is None or best_score == -math.inf:
            raise _NoMapping("no_mapped_option")
        return obs.pending_decision.options[best]

    # -- the pieces -----------------------------------------------------------

    def _outright_win(self, obs: Observation, player: int, scores: dict[int, list[int]]) -> int | None:
        """An option that wins the game outright (`safety.py`), or None. Checked
        before the network, which is known to decline some decisive wins."""
        if not self.take_forced_wins:
            return None
        _, dtype = self.engine.decision()
        if dtype not in safety.SEARCHED or not safety.worth_searching(obs.vp, obs.defcon, obs.turn, player):
            return None
        for index, slots in scores.items():
            if safety.winning_slots(self.engine, slots, player):
                return index
        return None

    def _facts(self, obs: Observation) -> boundary.PositionFacts:
        me = obs.side.value
        opp = obs.side.opponent.value
        return boundary.PositionFacts(
            phase=obs.phase, turn=obs.turn, action_round=obs.action_round,
            phasing=me if obs.phase == "action_rounds" else None,
            first_headline_picker=me,
            defcon=obs.defcon, vp=obs.vp,
            military_ops=dict(obs.military_ops), space_race=dict(obs.space_race),
            space_race_attempts=dict(obs.space_race_attempts),
            influence={c: dict(v) for c, v in obs.influence.items()},
            china_card_owner=obs.china_card_owner.value if isinstance(obs.china_card_owner, Side)
            else str(obs.china_card_owner),
            china_card_available=obs.china_card_available,
            hands={me: list(obs.hand), opp: ["?"] * obs.opponent_hand_size},
            draw_pile=["?"] * obs.draw_pile_size,
            discard_pile=list(obs.discard_pile), removed_cards=list(obs.removed_cards),
            in_play_cards=list(obs.in_play_cards),
            turn_effects=dict(obs.turn_effects), game_effects=dict(obs.game_effects),
        )

    def _determinized(self, facts: boundary.PositionFacts, me: Side, events: list[Event]) -> boundary.PositionFacts:
        """Fill the `"?"` slots. Cards the opponent later plays from hand go in
        its hand; the rest of the unknown pool is shuffled into the gaps."""
        opp = me.opponent.value
        known = set(facts.hands[me.value]) | set(facts.discard_pile) | set(facts.removed_cards) \
            | set(facts.in_play_cards) | {"The_China_Card"}
        played = [e.action.payload.get("card") for e in events
                  if e.decision.actor.value == opp and e.decision.kind in (K.HEADLINE_PLAY, K.ACTION_ROUND_PLAY)]
        played = [c for c in dict.fromkeys(played) if c and c not in known]
        pool = [c for c in _entered_ids(facts.turn, True) if c not in known and c not in played]
        self.rng.shuffle(pool)
        n_hand = len(facts.hands[opp])
        hand = played[:n_hand]
        hand += pool[: n_hand - len(hand)]
        rest = pool[n_hand - len(played[:n_hand]):]
        draw = rest[: len(facts.draw_pile)]
        out = boundary.PositionFacts(**{**facts.__dict__})
        out.hands = {me.value: list(facts.hands[me.value]), opp: hand}
        out.draw_pile = draw
        return out

    def _check_sync(self, obs: Observation) -> None:
        player, dtype = self.engine.decision()
        if player != ds_side(obs.side):
            raise Desync("ds_asks_other_side", f"ds player {player}, struggler {obs.side.value}")
        save = self.engine.save()
        assert self.ids is not None
        for cid, n in self.ids.country_to_ds.items():
            inf = obs.influence[cid]
            if (inf["US"], inf["USSR"]) != (save["us_influence"][n], save["ussr_influence"][n]):
                raise Desync("board_differs", cid)
        if save["defcon"] != obs.defcon:
            raise Desync("defcon_differs")

    def _option_slots(self, obs: Observation, partial: Partial | None) -> dict[int, list[int]]:
        """struggler option index -> the flat slots it stands for."""
        assert self.ids is not None
        decision = obs.pending_decision
        _, dtype = self.engine.decision()
        out: dict[int, list[int]] = {}
        for index, option in enumerate(decision.options):
            slots = self._slots(decision, option, dtype, partial, obs)
            if slots:
                out[index] = slots
        return out

    def _slots(self, decision: Any, option: Action, dtype: int, partial: Partial | None,
               obs: Observation) -> list[int]:
        assert self.ids is not None
        kind, payload = decision.kind, dict(option.payload)
        if kind is K.EVENT_CHOICE:
            return choice_slots(str(decision.context.get("event", "")), str(payload.get("choice")),
                                dtype, self.engine.mask(), self.ids)
        if kind in (K.HEADLINE_PLAY, K.ACTION_ROUND_PLAY) and dtype == B.SELECT_CARD:
            card = payload.get("card")
            return [B.CARD_OFFSET + self.ids.card_to_ds[card] - 1] if card else []
        if kind is K.PLAY_MODE and dtype == B.SELECT_PLAY_MODE:
            mode = payload.get("mode")
            if mode == "event":
                return [B.PLAY_MODE_OFFSET + B.RES_EVENT]
            if mode == "space_race":
                return [B.PLAY_MODE_OFFSET + B.RES_SPACE]
            if mode == "ops":
                card = decision.context.get("card")
                opponents = card is not None and _CARDS[card].side.value == obs.side.opponent.value
                # An opponent's card played for Ops: DeepStruggle's EVENT slot is the
                # event-first order, still "ops" in struggler's terms.
                return _OPS_SLOTS + ([B.PLAY_MODE_OFFSET + B.RES_EVENT] if opponents else [])
            return []
        if kind is K.EVENT_OPS_ORDER and dtype == B.SELECT_PLAY_MODE:
            return ([B.PLAY_MODE_OFFSET + B.RES_EVENT] if payload.get("order") == "event_first"
                    else list(_OPS_SLOTS))
        if kind is K.OPS_TYPE:
            t = payload.get("type")
            if dtype == B.SELECT_PLAY_MODE and t in _OPS_TYPE_RES:
                return [B.PLAY_MODE_OFFSET + _OPS_TYPE_RES[t]]
            if dtype == B.SELECT_OP_MODE and t in _OPS_TYPE_OPMODE:
                return [B.OP_MODE_OFFSET + _OPS_TYPE_OPMODE[t]]
            return []
        if dtype == B.POINT_NODE:
            if payload.get("stop"):
                return [B.CONFIRM_DONE_INDEX]
            country = payload.get("country") if kind in _COUNTRY_KINDS else None
            if country in self.ids.country_to_ds:
                return [B.NODE_OFFSET + self.ids.country_to_ds[country]]
        return []


def _default_model() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[4] / "data" / "deep" / "model.onnx")


class _NoMapping(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _logsumexp(values: list[float]) -> float:
    m = max(values)
    if m == -math.inf:
        return m
    return m + math.log(sum(math.exp(v - m) for v in values))
