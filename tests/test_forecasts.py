"""The hover forecasts must agree with what the engine actually does.

A forecast that drifts from the resolver is worse than none: the UI shows it
as odds, so it has to come from the same context and the same modifiers. These
tests drive real coups and realignments and compare the board after the roll
with the row the forecast predicted for that die.
"""

from __future__ import annotations

import pytest

from struggler.engine import DecisionKind, Engine, Side


def coup_forecast_rows(engine: Engine, country: str) -> tuple[dict, dict]:
    """Begin a coup and return (forecast, the country's target decision)."""
    decision = engine.pending_decision
    assert decision is not None and decision.kind is DecisionKind.COUP_TARGET
    return engine.coup_forecast(decision, country), decision


@pytest.mark.parametrize("seed", range(1, 16))
def test_coup_forecast_predicts_the_real_coup(seed):
    engine = Engine(seed=seed)
    engine.board.influence["Guatemala"]["USSR"] = 3
    engine.begin_coup(Side.US, ops=3)
    forecast, decision = coup_forecast_rows(engine, "Guatemala")

    target = next(
        a for a in decision.options if a.payload["country"] == "Guatemala"
    )
    engine.step(target)
    roll_decision = engine.pending_decision
    assert roll_decision.kind is DecisionKind.COUP_ROLL
    roll = roll_decision.options[0].payload["value"]
    row = next(r for r in forecast["rows"] if r["roll"] == roll)

    engine.step(roll_decision.options[0])
    influence = engine.board.influence["Guatemala"]
    assert influence["USSR"] == 3 - row["removed"], f"roll {roll}: {forecast}"
    assert influence["US"] == row["added"], f"roll {roll}: {forecast}"


def test_coup_forecast_reports_every_die_and_the_defcon_cost():
    engine = Engine(seed=2)
    engine.board.influence["France"]["USSR"] = 2   # France is a battleground
    engine.begin_coup(Side.US, ops=4)
    forecast = engine.coup_forecast(engine.pending_decision, "France")

    assert [r["roll"] for r in forecast["rows"]] == [1, 2, 3, 4, 5, 6]
    assert forecast["battleground"] is True and forecast["defcon_drop"] is True
    assert all(r["defcon"] == -1 for r in forecast["rows"])
    # Ops 4, stability 3 -> 2*3 = 6, so only a 3+ (margin > 0) removes anything.
    margins = {r["roll"]: r["margin"] for r in forecast["rows"]}
    assert margins == {1: -1, 2: 0, 3: 1, 4: 2, 5: 3, 6: 4}
    assert [r["removed"] for r in forecast["rows"]] == [0, 0, 1, 2, 2, 2]
    # Once the opponent's influence is gone, the excess becomes the couper's.
    assert [r["added"] for r in forecast["rows"]] == [0, 0, 0, 0, 1, 2]


def test_realignment_forecast_predicts_the_real_roll():
    for seed in range(1, 16):
        engine = Engine(seed=seed)
        engine.board.influence["Israel"]["US"] = 3
        engine.board.influence["Israel"]["USSR"] = 2
        engine.begin_realignment_operations(Side.US, ops=1)
        decision = engine.pending_decision
        assert decision.kind is DecisionKind.REALIGNMENT_TARGET
        forecast = engine.realignment_forecast(decision, "Israel")

        target = next(a for a in decision.options if a.payload.get("country") == "Israel")
        engine.step(target)
        actor_roll_decision = engine.pending_decision
        actor_roll = actor_roll_decision.options[0].payload["value"]
        engine.step(actor_roll_decision.options[0])
        opp_roll_decision = engine.pending_decision
        opp_roll = opp_roll_decision.options[0].payload["value"]
        engine.step(opp_roll_decision.options[0])

        own_bonus, opp_bonus = forecast["own_bonus"], forecast["opponent_bonus"]
        margin = (actor_roll + own_bonus) - (opp_roll + opp_bonus)
        influence = engine.board.influence["Israel"]
        if margin > 0:
            assert influence["USSR"] == 2 - min(margin, 2)
            assert influence["US"] == 3
        elif margin < 0:
            assert influence["US"] == 3 - min(-margin, 3)


def test_realignment_forecast_covers_all_36_rolls():
    engine = Engine(seed=1)
    engine.board.influence["Israel"]["US"] = 2
    engine.board.influence["Israel"]["USSR"] = 2
    engine.begin_realignment_operations(Side.US, ops=1)
    forecast = engine.realignment_forecast(engine.pending_decision, "Israel")

    assert forecast["wins"] + forecast["ties"] + forecast["losses"] == 36
    assert sum(o["count"] for o in forecast["outcomes"]) == 36
    assert -2 <= forecast["expected_delta"] <= 2


def test_realignment_bonus_parts_add_up_to_the_bonus():
    """The UI explains the odds with the itemised modifiers, so they have to
    agree with the number the resolver uses."""
    engine = Engine(seed=4)
    engine.board.influence["Israel"]["US"] = 2
    engine.board.influence["Israel"]["USSR"] = 1   # a realignment needs a target
    engine.begin_realignment_operations(Side.US, ops=1)
    decision = engine.pending_decision
    forecast = engine.realignment_forecast(decision, "Israel")

    assert sum(forecast["own_parts"].values()) == forecast["own_bonus"]
    assert sum(forecast["opponent_parts"].values()) == forecast["opponent_bonus"]
    assert set(forecast["own_parts"]) == {"adjacency", "neighbours", "influence"}


def test_realignment_forecast_does_not_promise_influence_to_lose():
    """With no friendly influence in the target, a losing roll cannot cost
    anything: the outcome table must not claim otherwise."""
    engine = Engine(seed=4)
    engine.board.influence["Israel"]["USSR"] = 2   # US holds nothing here
    engine.begin_realignment_operations(Side.US, ops=1)
    forecast = engine.realignment_forecast(engine.pending_decision, "Israel")

    assert forecast["own_influence"] == 0
    assert all(o["delta"] >= 0 for o in forecast["outcomes"])


def test_forecasts_refuse_the_wrong_decision():
    engine = Engine(seed=1)
    engine.board.influence["Guatemala"]["USSR"] = 2   # a coup needs a target
    engine.begin_coup(Side.US, ops=3)
    coup_decision = engine.pending_decision
    with pytest.raises(ValueError):
        engine.realignment_forecast(coup_decision, "Guatemala")
