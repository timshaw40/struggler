"""Final, untouched evaluation on the reserved seed bank.

    python scripts/final_eval.py --policy data/ppo_best.pt --seeds 40 --workers 8
    python scripts/final_eval.py --policy a.pt --policy b.pt --policies-head-to-head

Seeds start at `arena.FINAL_EVAL_SEED_BASE`, which training collection and the
promotion gate are forbidden from touching, so these games are ones no
candidate ever saw. Reports win/loss/draw and a Wilson 95% interval per
matchup — a number to report, not a "significance" claim.
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.arena import (  # noqa: E402
    FINAL_EVAL_SEED_BASE,
    PlayerSpec,
    elo,
    head_to_head,
    round_robin,
    wilson_interval,
)


def _name(path: str, i: int) -> str:
    return f"policy{i}:{Path(path).stem}" if path else f"policy{i}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--policy", action="append", default=[],
                    help="RL checkpoint (.pt); repeat for several")
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--workers", type=int, default=min(8, multiprocessing.cpu_count()))
    ap.add_argument("--policies-head-to-head", action="store_true",
                    help="also play the policies against each other")
    ap.add_argument(
        "--events", action=argparse.BooleanOptionalAction, default=True,
        help="Pass events= to Engine.new_game (default: on).",
    )
    args = ap.parse_args()
    if not args.policy:
        raise SystemExit("give at least one --policy")

    policies = [PlayerSpec(_name(p, i), "rl", net_path=p) for i, p in enumerate(args.policy)]
    specs = list(policies) + [
        PlayerSpec("greedy", "greedy"),
        PlayerSpec("random", "random", player_seed=1),
        PlayerSpec("first", "first"),
    ]
    for p in policies:
        if not Path(p.net_path).is_file():
            raise SystemExit(f"no such checkpoint: {p.net_path}")

    seeds = [FINAL_EVAL_SEED_BASE + i for i in range(args.seeds)]
    print(f"final bank {FINAL_EVAL_SEED_BASE}+  {args.seeds} seeds (side-swapped), "
          f"{args.workers} workers")
    results = round_robin(specs, seeds, workers=args.workers, events=args.events)

    for p in policies:
        for opp in ("greedy", "random", "first"):
            w, l, d = head_to_head(results, p.name, opp)
            lo, hi = wilson_interval(w, l)
            print(f"  {p.name:24s} vs {opp:7s}: {w}-{l}-{d}  "
                  f"win rate {w / max(1, w + l):.0%} [{lo:.0%}-{hi:.0%}]")
    if args.policies_head_to_head:
        for i, a in enumerate(policies):
            for b in policies[i + 1:]:
                w, l, d = head_to_head(results, a.name, b.name)
                lo, hi = wilson_interval(w, l)
                print(f"  {a.name:24s} vs {b.name:24s}: {w}-{l}-{d}  "
                      f"{w / max(1, w + l):.0%} [{lo:.0%}-{hi:.0%}]")
    print("  elo:", {k: round(v, 1) for k, v in elo(results, [s.name for s in specs]).items()})


if __name__ == "__main__":
    main()
