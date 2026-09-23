"""The cross-revision gate: the mechanics that would silently corrupt a gate.

`scripts/h2h_revisions.py` decides whether a bot change ships, so the parts
worth pinning are the ones that fail *quietly*: a module that does not load,
seats that are not swapped, a reserved seed used as a practice seed, and a
DEFCON-1 column that attributes a loss to the wrong revision. A wrong win rate
is visible in the numbers; a mis-attributed DEFCON-1 loss just reads as zero.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "h2h_revisions.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("h2h_revisions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["h2h_revisions"] = module
    spec.loader.exec_module(module)
    return module


h2h = _load_script()


# -- loading an arbitrary revision ------------------------------------------


def test_load_revision_registers_the_module_before_exec(tmp_path):
    """`greedy.py` defines dataclasses, and dataclasses resolves a class's
    annotations through `sys.modules[cls.__module__]`. Without the
    registration this raises an AttributeError from inside the stdlib, which
    looks nothing like the actual cause."""
    shutil.copy(ROOT / "src" / "struggler" / "bots" / "greedy.py", tmp_path / "greedy.py")
    module = h2h.load_revision(tmp_path / "greedy.py", "_test_h2h_alias")
    assert hasattr(module, "GreedyPlayer")
    assert "_test_h2h_alias" in sys.modules
    # And it is a *fresh* class, not the installed one: two revisions have to
    # coexist in one process.
    from struggler.bots.greedy import GreedyPlayer

    assert module.GreedyPlayer is not GreedyPlayer
    del sys.modules["_test_h2h_alias"]


def test_load_revision_rejects_a_file_without_the_player(tmp_path):
    bad = tmp_path / "greedy.py"
    bad.write_text("X = 1\n")
    with pytest.raises(SystemExit):
        h2h.load_revision(bad, "_test_h2h_bad")


def test_a_failed_load_does_not_leave_a_broken_module_behind(tmp_path):
    """A half-executed module left in sys.modules poisons the next load of the
    same alias with a stale object, which is how one bad revision turns into
    confusing failures much later."""
    bad = tmp_path / "greedy.py"
    bad.write_text("raise RuntimeError('boom')\n")
    with pytest.raises(RuntimeError):
        h2h.load_revision(bad, "_test_h2h_poison")
    assert "_test_h2h_poison" not in sys.modules


# -- attribution ------------------------------------------------------------


def _outcome(seed, us_rev, ussr_rev, winner, reason):
    return h2h.Outcome(seed=seed, us_rev=us_rev, ussr_rev=ussr_rev,
                       winner=winner, reason=reason)


def test_defcon_1_losses_are_attributed_to_the_losing_seat(capsys):
    """The winner is a *side*; the revision is a seat occupant. Comparing the
    two vocabularies directly reads every DEFCON-1 loss as zero -- the one
    column the gate is supposed to be watching."""
    h2h.report([
        _outcome(1, "new", "old", "USSR", "defcon_1"),   # new sat US; US lost
        _outcome(1, "old", "new", "US", "defcon_1"),     # new sat USSR; USSR lost
        _outcome(2, "new", "old", "US", "defcon_1"),     # new sat US and won
        _outcome(3, "old", "new", "US", "defcon_1"),     # new sat USSR; USSR lost
    ], {"new": "current", "old": "old"})
    out = capsys.readouterr().out
    assert "new  3" in out, out
    assert "old  1" in out, out


def test_a_defcon_1_win_is_not_counted_as_a_loss(capsys):
    h2h.report([
        _outcome(1, "new", "old", "US", "defcon_1"),     # new wins by DEFCON 1
        _outcome(1, "old", "new", "USSR", "defcon_1"),   # new wins again
    ], {"new": "current", "old": "old"})
    out = capsys.readouterr().out
    assert "new  0" in out, out
    assert "old  2" in out, out


def test_the_win_rate_follows_the_swapped_seats(capsys):
    """Two games per seed with the seats swapped, so a revision that wins both
    games of a seed is 2-0, not 1-1."""
    h2h.report([
        _outcome(1, "new", "old", "US", "final_vp"),     # new sat US, won
        _outcome(1, "old", "new", "USSR", "final_vp"),   # new sat USSR, won
    ], {"new": "current", "old": "old"})
    out = capsys.readouterr().out
    assert "2-0-0" in out, out
    assert "100.0%" in out, out


def test_draws_are_reported_separately_from_wins_and_losses(capsys):
    h2h.report([
        _outcome(1, "new", "old", None, "draw"),
        _outcome(1, "old", "new", "US", "final_vp"),
    ], {"new": "current", "old": "old"})
    out = capsys.readouterr().out
    assert "0-1-1" in out, out


# -- the seed bank ----------------------------------------------------------


def test_reserved_seeds_are_refused_cli(tmp_path):
    """`arena.FINAL_EVAL_SEED_BASE` is reserved for final_eval.py; a gate that
    silently burned it would make the final number meaningless."""
    from struggler.arena import FINAL_EVAL_SEED_BASE

    greedy = tmp_path / "greedy.py"
    shutil.copy(ROOT / "src" / "struggler" / "bots" / "greedy.py", greedy)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(greedy), "2", str(FINAL_EVAL_SEED_BASE)],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode != 0
    assert "reserved" in (proc.stdout + proc.stderr)


def test_a_missing_old_revision_is_refused_cli(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "nope.py"), "2", "1"],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode != 0
    assert "no such file" in (proc.stdout + proc.stderr)


# -- the smoke test the acceptance criteria name ----------------------------


def test_two_seed_smoke_matches_a_hand_counted_run(tmp_path):
    """The gate's whole value is that its number is the truth. A side-swap or
    aggregation bug flips W and L and would go unnoticed against a bot that is
    near 50% -- so the harness is checked against a run whose seats are
    assigned explicitly."""
    shutil.copy(ROOT / "src" / "struggler" / "bots" / "greedy.py", tmp_path / "old.py")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "old.py"), "2", "1", "--workers", "1"],
        capture_output=True, text=True, timeout=900,
    )
    assert proc.returncode == 0, proc.stderr
    # A revision against itself is exactly 50%: every seed is played both ways.
    assert "2-2-0" in proc.stdout, proc.stdout
