"""Head-to-head between the current bot and an earlier revision of it.

    git show <sha>:src/struggler/bots/greedy.py > /tmp/greedy_old.py
    python scripts/h2h_revisions.py /tmp/greedy_old.py 250 1

`arena.py` compares players built from the *same* revision, so gating a bot
change against the previous bot otherwise needs an ad-hoc script. This is that
script, kept in the repo so every candidate is measured the same way.

Why a separate harness and not a `PlayerSpec` kind: the old bot is a *file*,
not an installed player. It is loaded from disk under a private module name so
both revisions can be alive in one process without their `struggler.bots.greedy`
imports colliding.

Everything else follows the arena's conventions deliberately:

  * every seed is played twice with the seats swapped, so side asymmetry
    cancels (ADR-0005);
  * seeds are checked against `arena.FINAL_EVAL_SEED_BASE` and refused above it,
    because that bank is reserved for `final_eval.py`;
  * a spawn pool with a top-level worker, so each process loads both revisions
    itself rather than inheriting one (see `_worker`).

Reported, per the gate: wins-losses-draws, the win rate with its Wilson
interval, the end-reason split, and **the DEFCON-1 loss count per revision**.
That last column is the one that has caught every real bug in this bot,
including two the win rate did not move on.
"""

from __future__ import annotations

import argparse
import collections
import importlib.util
import multiprocessing
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.arena import FINAL_EVAL_SEED_BASE, wilson_interval  # noqa: E402
from struggler.engine import Engine, Side  # noqa: E402
from struggler.runner import play_game  # noqa: E402


def load_revision(path: Path, alias: str):
    """Import a `greedy.py` from an arbitrary path as `alias`.

    The `sys.modules` registration before `exec_module` is load-bearing, not
    hygiene: `greedy.py` defines dataclasses, and dataclasses resolves a
    class's annotations through `sys.modules[cls.__module__]`. Without the
    entry that lookup is a `KeyError`, which surfaces as a confusing
    `AttributeError` from deep inside the stdlib.
    """
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load a module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # A half-initialised module left in sys.modules would poison a later
        # load of the same alias with a stale, broken object.
        sys.modules.pop(alias, None)
        raise
    if not hasattr(module, "GreedyPlayer"):
        raise SystemExit(f"{path} does not define GreedyPlayer")
    return module


@dataclass(frozen=True)
class Outcome:
    """One played game, reduced to what the gate reports."""

    seed: int
    us_rev: str          # "new" | "old"
    ussr_rev: str
    winner: str | None   # "US" | "USSR" | None (draw)
    reason: str | None   # engine's game_over_reason


def play_one(seed: int, us_rev: str, ussr_rev: str, paths: dict[str, Path]) -> Outcome:
    """Both revisions are loaded here, in whichever process is running."""
    mods = {name: load_revision(path, f"_h2h_greedy_{name}") for name, path in paths.items()}
    engine = Engine.new_game(seed=seed, events=True)
    players = {
        Side.US: mods[us_rev].GreedyPlayer(),
        Side.USSR: mods[ussr_rev].GreedyPlayer(),
    }
    winner = play_game(engine, players)
    return Outcome(
        seed=seed,
        us_rev=us_rev,
        ussr_rev=ussr_rev,
        winner=winner.value if winner else None,
        reason=engine._game_over_reason,
    )


def _worker(job: tuple[int, str, str, dict[str, str]]) -> list[Outcome]:
    """Top-level so a spawn pool can call it: `(seed, us_rev, ussr_rev, paths)`.

    Paths travel as strings because a `Path` pickles fine but the dict shape is
    clearer as plain data.
    """
    seed, us_rev, ussr_rev, raw_paths = job
    paths = {name: Path(p) for name, p in raw_paths.items()}
    return [
        play_one(seed, us_rev, ussr_rev, paths),   # new = US
        play_one(seed, ussr_rev, us_rev, paths),   # swapped: new = USSR
    ]


def run(seeds: list[int], paths: dict[str, Path], workers: int) -> list[Outcome]:
    jobs = [(seed, "new", "old", {k: str(v) for k, v in paths.items()}) for seed in seeds]
    if workers <= 1:
        results = [_worker(j) for j in jobs]
    else:
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(workers) as pool:
            results = pool.map(_worker, jobs)
    return [outcome for pair in results for outcome in pair]


def report(outcomes: list[Outcome], labels: dict[str, str]) -> None:
    """The gate's numbers: W-L-D, rate + interval, end reasons, DEFCON-1."""
    wins = losses = draws = 0
    for o in outcomes:
        # "new" is the reference player; its opponent is the other revision.
        new_is_us = o.us_rev == "new"
        if o.winner is None:
            draws += 1
        elif (o.winner == "US") == new_is_us:
            wins += 1
        else:
            losses += 1
    played = wins + losses + draws
    low, high = wilson_interval(wins, losses)
    rate = wins / (wins + losses) if wins + losses else 0.0
    print(f"{played} games ({played // 2} seeds, side-swapped)")
    print(f"  new {labels['new']} vs old {labels['old']}")
    print(f"  W-L-D          {wins}-{losses}-{draws}")
    print(f"  win rate       {100 * rate:.1f}%  (95% CI {100 * low:.1f}-{100 * high:.1f}%)")

    print("  ended by       " + "  ".join(
        f"{k} {v}" for k, v in collections.Counter(o.reason or "none" for o in outcomes).most_common()))

    # The column that matters: a revision that loses to its own DEFCON
    # handling is broken regardless of what the win rate says. Counted per
    # revision, so "new lost 3 games to DEFCON 1" is attributable.
    print("  DEFCON-1 losses")
    for name in ("new", "old"):
        # The winner is a *side*; the revision is a seat occupant. Map the
        # losing side to whoever was sitting in that seat before comparing.
        count = 0
        for o in outcomes:
            if o.reason != "defcon_1" or o.winner is None:
                continue
            loser = "USSR" if o.winner == "US" else "US"
            if (o.us_rev if loser == "US" else o.ussr_rev) == name:
                count += 1
        print(f"    {name:<4} {count}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old_greedy", type=Path,
                    help="path to the previous revision's greedy.py")
    ap.add_argument("seeds", type=int, help="number of seeds (2 games each)")
    ap.add_argument("start_seed", type=int, help="first seed")
    ap.add_argument("--workers", type=int, default=0,
                    help="pool size (default: cpu count)")
    args = ap.parse_args()

    if not args.old_greedy.is_file():
        raise SystemExit(f"no such file: {args.old_greedy}")
    seeds = [args.start_seed + i for i in range(args.seeds)]
    reserved = [s for s in seeds if s >= FINAL_EVAL_SEED_BASE]
    if reserved:
        raise SystemExit(
            f"seeds {reserved[:3]}... are at or above FINAL_EVAL_SEED_BASE "
            f"({FINAL_EVAL_SEED_BASE}); that bank is reserved for final_eval.py"
        )

    workers = args.workers or (multiprocessing.cpu_count() or 1)
    paths = {"new": ROOT / "src" / "struggler" / "bots" / "greedy.py",
             "old": args.old_greedy.resolve()}
    outcomes = run(seeds, paths, workers)
    report(outcomes, {"new": "current", "old": str(args.old_greedy)})


if __name__ == "__main__":
    main()
