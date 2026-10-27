"""A random draw that fires an event replays in DeepStruggle as struggler drew it.

Five Year Plan has the USSR discard a card at random, and a US event drawn
that way fires. DeepStruggle draws from its own RNG, so a card merely swapped
in afterwards would sit in the discard pile unfired; `replay` redraws under
other RNG states instead (`_redraw`). Needs an engine build.
"""
import os
from pathlib import Path

import pytest

from struggler.bots.deep import backend as B
from struggler.bots.deep import boundary
from struggler.bots.deep.ids import IdMap
from struggler.bots.deep.replay import replay
from struggler.engine import Engine, Side
from struggler.engine.types import DecisionKind as K


def _backend():
    shared, native = os.environ.get("STRUGGLER_DEEP_SHARED_LIB"), os.environ.get("STRUGGLER_DEEP_ENGINE")
    wasm = os.environ.get("STRUGGLER_DEEP_WASM")
    if shared and Path(shared).exists():
        return B.SharedLibBackend(shared)
    if native:
        return B.NativeBackend()
    if wasm and Path(wasm).exists():
        return B.WasmBackend(wasm)
    pytest.skip("no DeepStruggle engine build")


class _Logged:
    def __init__(self, decision, action):
        self.decision, self.action = decision, action


def _give_ussr(engine: Engine, cards: list[str]) -> None:
    """Put `cards` in the USSR hand, trading places with cards already there."""
    hand = engine.hands["USSR"]
    for i, card in enumerate(cards):
        if card in hand:
            continue
        out = next(c for c in hand if c not in cards)
        for pile in (engine.hands["US"], engine.draw_pile, engine.discard_pile):
            if card in pile:
                pile[pile.index(card)] = out
                break
        hand[hand.index(out)] = card


def _first(decision, **payload):
    return next(a for a in decision.options if all(a.payload.get(k) == v for k, v in payload.items()))


def test_five_year_plan_drawing_a_us_event_fires_it_in_deepstruggle_too():
    engine_ds = _backend()
    names = engine_ds.country_names()
    ids = IdMap.build(names, list(Engine.new_game(seed=0, include_ccw=False).board.countries))
    for seed in range(1, 60):
        game = Engine.new_game(seed=seed, include_ccw=False, setup_us_extra=2)
        while not (game.pending_decision.kind is K.ACTION_ROUND_PLAY and game.pending_decision.actor is Side.USSR
                   and len(game._decision_stack) == 1):
            d = game.pending_decision
            game.step(d.options[0])
        _give_ussr(game, ["Five_Year_Plan", "Duck_and_Cover"])
        game._decision_stack.pop()  # re-ask the card choice over the edited hand
        game._push_action_round_play(Side.USSR)
        hand = list(game.hands["USSR"])
        facts = boundary.from_engine(game)
        logged = []

        def act(action):
            logged.append(_Logged(game.pending_decision, action))
            game.step(action)

        act(_first(game.pending_decision, card="Five_Year_Plan"))
        act(_first(game.pending_decision, mode="ops"))
        if game.pending_decision.kind is K.EVENT_OPS_ORDER:
            act(_first(game.pending_decision, order="event_first"))
        draw = game.pending_decision
        if draw.kind is not K.RANDOM_DISCARD or draw.options[0].payload["card"] != "Duck_and_Cover":
            continue  # struggler drew another card this seed
        act(draw.options[0])
        while game.pending_decision is not None and not (
                game.pending_decision.kind is K.ACTION_ROUND_PLAY and len(game._decision_stack) == 1):
            act(game.pending_decision.options[0])
        engine_ds.load_save(boundary.to_save(facts, ids))
        replay(engine_ds, logged, ids)  # raises Desync on any disagreement
        save = engine_ds.save()
        assert save["defcon"] == game.defcon  # Duck and Cover fired: DEFCON down, US VP
        assert save["victory_points"] == game.vp
        assert len(hand) > 2
        return
    pytest.skip("no seed drew Duck and Cover")
