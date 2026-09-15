"""Tests for the bot arena: seeded side-swapped games, scoring, Elo."""

from __future__ import annotations

from struggler.arena import (
    PlayerSpec,
    elo,
    head_to_head,
    matchup_table,
    play_one,
    run_matchup,
    score,
)


def test_play_one_terminates_and_reports_a_winner():
    result = play_one(1, PlayerSpec("g", "greedy"), PlayerSpec("r", "random", player_seed=1))
    assert result.winner in (None, "US", "USSR")
    assert result.us == "g" and result.ussr == "r"


def test_run_matchup_plays_each_seed_twice_side_swapped():
    a = PlayerSpec("g", "greedy")
    b = PlayerSpec("f", "first")
    results = run_matchup(a, b, [1, 2, 3], workers=1)
    assert len(results) == 6  # 3 seeds x 2 seatings
    wins, losses, draws = head_to_head(results, "g", "f")
    assert wins + losses + draws == 6
    points, games = score(results, "g")
    assert games == 6 and 0.0 <= points <= 6.0


def test_matchup_table_is_reciprocal():
    a = PlayerSpec("g", "greedy")
    b = PlayerSpec("f", "first")
    results = run_matchup(a, b, [4], workers=1)
    table = matchup_table(results, ["g", "f"])
    gw, gl, gd = table["g"]["f"]
    fw, fl, fd = table["f"]["g"]
    assert (gw, gl, gd) == (fl, fw, fd)


def test_elo_returns_a_rating_for_every_name():
    results = run_matchup(PlayerSpec("g", "greedy"), PlayerSpec("f", "first"), [5], workers=1)
    ratings = elo(results, ["g", "f"])
    assert set(ratings) == {"g", "f"}
    assert all(isinstance(v, float) for v in ratings.values())


def test_wilson_interval_and_reserved_final_bank():
    from struggler.arena import FINAL_EVAL_SEED_BASE, wilson_interval

    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(13, 11)  # a "margin 2" result is not evidence
    assert 0.0 <= lo < 13 / 24 < hi <= 1.0
    assert FINAL_EVAL_SEED_BASE >= 900_000_000  # well clear of training banks
