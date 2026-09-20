"""The bots must survive an option that is not a card, and never divide by zero.

Reported as "played Asia Scoring as USSR, game hung on thinking". Two faults,
one behind the other:

1. `sit_out` is the engine's own pseudo-card for conceding the remaining Action
   Rounds (4.5-D, offered only with an empty hand). `greedy._score_action_round_play`
   looked it up in the card table and raised `KeyError`.
2. That KeyError made every MCTS rollout invalid, so `visits` stayed all-zero
   while the untried list emptied — and the UCB1 key divided by zero, killing
   `choose_action` outright. The server's `/action` thread died inside its lock,
   which the browser can only read as "thinking" forever.

The second is why a bot error became a hang: an unusable simulation has to be
excluded, never allowed to take the choice down with it.
"""

from __future__ import annotations

from struggler.bots import greedy
from struggler.bots.greedy import GreedyWeights, _score_action_round_play, _score_headline
from struggler.bots.mcts import MCTSPlayer
from struggler.engine import Action, DecisionKind, Engine, Side
from struggler.engine.cards import load_cards


def _empty_hand_action_round(seed: int = 1) -> tuple[Engine, object]:
    """Drive a real game to the USSR's action-round decision with an empty hand,
    which is the only way the engine offers `sit_out` (4.5-D)."""
    engine = Engine.new_game(seed=seed)
    steps = 0
    while steps < 2000:
        decision = engine.pending_decision
        if decision is None:
            break
        if decision.kind is DecisionKind.ACTION_ROUND_PLAY and decision.actor is Side.USSR:
            engine.hands["USSR"] = []
            engine._push_action_round_play(Side.USSR)
            return engine, engine.pending_decision
        engine.step(decision.options[0])
        steps += 1
    raise AssertionError("never reached a USSR action-round decision")


def test_sit_out_scores_without_a_card_lookup():
    """The engine offers `sit_out`, so the heuristics have to price it."""
    engine, decision = _empty_hand_action_round()
    assert decision.kind is DecisionKind.ACTION_ROUND_PLAY
    payloads = [a.payload for a in decision.options]
    assert {"card": "sit_out"} in payloads, f"sit_out not offered: {payloads}"

    action = next(a for a in decision.options if a.payload["card"] == "sit_out")
    observation = engine.observe(Side.USSR)
    board = engine.board
    # Both heuristics must answer rather than raise.
    assert _score_action_round_play(GreedyWeights(), board, observation, action) == 0.0
    assert _score_headline(GreedyWeights(), board, observation, action) == 0.0


def test_every_offered_option_scores_for_every_decision():
    """A sweep over a played game: whatever the engine offers, the heuristics
    price it. This is the guard that would have caught the KeyError directly."""
    engine = Engine.new_game(seed=7)
    cards = load_cards()
    seen: set[str] = set()
    steps = 0
    while not engine.is_terminal and steps < 4000:
        decision = engine.pending_decision
        if decision is None:
            break
        # observe() is only defined for the two seats; a CHANCE decision has no
        # hand-derived options to price anyway.
        if decision.actor not in (Side.US, Side.USSR):
            engine.step(decision.options[0])
            steps += 1
            continue
        observation = engine.observe(decision.actor)
        for action in decision.options:
            cid = action.payload.get("card")
            if cid is not None:
                seen.add(cid)
                _score_action_round_play(GreedyWeights(), engine.board, observation, action)
        engine.step(decision.options[0])
        steps += 1
    assert "The_China_Card" in cards  # sanity: the table loaded
    assert len(seen) > 20, f"only {len(seen)} card options exercised"

    # …and the pseudo-card, which the sweep above cannot reach on its own.
    empty_engine, empty_decision = _empty_hand_action_round()
    observation = empty_engine.observe(Side.USSR)
    for action in empty_decision.options:
        _score_action_round_play(GreedyWeights(), empty_engine.board, observation, action)


def test_mcts_answers_when_every_rollout_is_invalid():
    """An all-invalid simulation set must still produce an action, not a
    ZeroDivisionError. This is the exact shape of the reported hang."""
    engine = Engine.new_game(seed=1)
    decision = engine.pending_decision
    assert len(decision.options) > 1, "need a real choice for this test"
    # Reach a seat decision with a real choice: the opening setup decision has
    # one option and returns before the search runs at all.
    steps = 0
    while steps < 400:
        decision = engine.pending_decision
        if (
            decision is not None
            and decision.actor in (Side.US, Side.USSR)
            and len(decision.options) > 1
            and not decision.context.get("setup")
        ):
            break
        engine.step(decision.options[0])
        steps += 1
    assert decision is not None and len(decision.options) > 1, "no multi-option seat decision"

    player = MCTSPlayer(seed=1, sims=6)
    player.bind_engine(engine)

    # Every rollout is invalid: the choice must still come back.
    player._rollout = lambda *a, **k: None
    action = player.choose_action(engine.observe(decision.actor), ())
    assert action in decision.options
    # The zero-division came from the UCB1 key when the untried list empties
    # while every visit count is still zero; that path is only reached once the
    # budget exceeds the option count, so exercise both.
    for sims in (len(decision.options) - 1, len(decision.options), len(decision.options) + 4):
        busy = MCTSPlayer(seed=1, sims=sims)
        busy.bind_engine(engine)
        busy._rollout = lambda *a, **k: None
        assert busy.choose_action(engine.observe(decision.actor), ()) in decision.options
