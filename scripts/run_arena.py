"""Run a seeded, side-swapped round-robin tournament between bots.

    python scripts/run_arena.py --seeds 6 --workers 8
    python scripts/run_arena.py --weights data/tuned.json --include-mcts --mcts-sims 4

Prints each matchup's W-L-D from the first-named bot's perspective, and a
simple Elo rating. Head-to-head records (not win rate vs one opponent) are the
honest signal once a bot passes the baseline.
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.arena import (  # noqa: E402
    PlayerSpec,
    elo,
    matchup_table,
    round_robin,
)
from struggler.bots.checkpoint import load_weights, weights_to_dict  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seeds", type=int, default=6)
    ap.add_argument("--seed-base", type=int, default=0)
    ap.add_argument("--workers", type=int, default=min(8, multiprocessing.cpu_count()))
    ap.add_argument("--weights", default=None, help="greedy weights JSON to add as 'tuned'")
    ap.add_argument("--include-mcts", action="store_true")
    ap.add_argument("--mcts-sims", type=int, default=4)
    ap.add_argument(
        "--events", action=argparse.BooleanOptionalAction, default=True,
        help="Pass events= to Engine.new_game (default: on).",
    )
    args = ap.parse_args()

    specs = [
        PlayerSpec("random", "random", player_seed=1),
        PlayerSpec("first", "first"),
        PlayerSpec("greedy", "greedy"),
    ]
    if args.weights:
        specs.append(PlayerSpec("tuned", "greedy", weights_to_dict(load_weights(args.weights))))
    if args.include_mcts:
        specs.append(PlayerSpec("mcts", "mcts", sims=args.mcts_sims, player_seed=1))

    seeds = [args.seed_base + i for i in range(args.seeds)]
    print(f"round-robin: {len(specs)} bots, {args.seeds} seeds (side-swapped), "
          f"{args.workers} workers, events={args.events}")
    started = time.perf_counter()
    results = round_robin(specs, seeds, workers=args.workers, events=args.events)
    elapsed = time.perf_counter() - started

    names = [s.name for s in specs]
    table = matchup_table(results, names)
    print()
    for a in names:
        for b in names:
            if a < b:
                w, l, d = table[a][b]
                print(f"  {a:8s} vs {b:8s}: {w}-{l}-{d}")
    print()
    for name, rating in sorted(elo(results, names).items(), key=lambda kv: -kv[1]):
        print(f"  {name:8s} elo {rating:7.1f}")
    print(f"\n{len(results)} games in {elapsed:.1f}s "
          f"({len(results) / elapsed:.1f} games/s)")


if __name__ == "__main__":
    main()
