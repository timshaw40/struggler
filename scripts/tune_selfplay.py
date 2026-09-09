"""Self-play weight tuner: champion vs challenger over paired seeds.

    python scripts/tune_selfplay.py --minutes 45
    python scripts/tune_selfplay.py --baseline-match 12   # champ vs starting weights

Every iteration perturbs all tunable weights at once (multiplicative
jitter), and the challenger plays the champion with seeds played twice,
sides swapped — each seed's pair cancels side asymmetry. Adopt on strict
majority + margin. Seeds roll every iteration so a challenger can't
overfit one bank. Greedy-vs-greedy games run ~1s, so this is thousands
of games per hour on a laptop; workers parallelize across seeds.

`defcon_self_kill_penalty` is never tuned: guardrail, not preference.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.bots.greedy import GreedyPlayer, GreedyWeights  # noqa: E402
from struggler.engine import Engine, Side  # noqa: E402
from struggler.runner import play_game  # noqa: E402

TUNABLE = [f for f in GreedyWeights.__dataclass_fields__
           if f != "defcon_self_kill_penalty"]


def _play_pair(args: tuple[int, GreedyWeights, GreedyWeights]) -> tuple[int, int]:
    """One seed, both seats: (challenger wins, games played)."""
    seed, champ, chall = args
    wins = 0
    engine = Engine.new_game(seed=seed, events=True)
    winner = play_game(engine, {
        Side.US: GreedyPlayer(weights=chall),
        Side.USSR: GreedyPlayer(weights=champ),
    })
    wins += winner is Side.US
    engine = Engine.new_game(seed=seed, events=True)
    winner = play_game(engine, {
        Side.US: GreedyPlayer(weights=champ),
        Side.USSR: GreedyPlayer(weights=chall),
    })
    wins += winner is Side.USSR
    return wins, 2


def perturb(weights: GreedyWeights, rng: random.Random, jitter: float, knobs: int) -> GreedyWeights:
    fields = rng.sample(TUNABLE, min(knobs, len(TUNABLE)))
    kw = {f: getattr(weights, f) for f in GreedyWeights.__dataclass_fields__}
    for f in fields:
        kw[f] = max(kw[f] * math.exp(rng.uniform(-jitter, jitter)), 1e-6)
    kw["defcon_self_kill_penalty"] = weights.defcon_self_kill_penalty
    return GreedyWeights(**kw)


def tournament(
    champ: GreedyWeights,
    chall: GreedyWeights,
    seed_base: int,
    n_seeds: int,
    workers: int,
) -> tuple[int, int]:
    args = [(seed_base + i, champ, chall) for i in range(n_seeds)]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(workers) as pool:
        results = pool.map(_play_pair, args)
    wins = sum(w for w, _ in results)
    total = sum(t for _, t in results)
    return wins, total


def fit(
    minutes: float,
    max_iters: int,
    n_seeds: int,
    workers: int,
    min_wins: int,
    jitter: float,
    knobs: int,
    log=print,
) -> tuple[GreedyWeights, int, int]:
    """Returns (champion, adopted, games_played)."""
    champ = GreedyWeights()
    start = GreedyWeights()
    rng = random.Random(7)
    adopted = 0
    games = 0
    deadline = time.monotonic() + minutes * 60
    it = 0
    while it < max_iters and time.monotonic() < deadline:
        chall = perturb(champ, rng, jitter, knobs)
        seed_base = 10_000 + it * (n_seeds + 7)
        wins, total = tournament(champ, chall, seed_base, n_seeds, workers)
        games += total
        took = wins >= min_wins
        log(f"iter {it}: challenger {wins}-{total - wins} "
            f"{'ADOPT' if took else ''}")
        if took:
            champ = chall
            adopted += 1
        it += 1
    # The honest number: final champion vs the starting weights, fresh seeds.
    wins, total = tournament(champ, start, 900_000, max(n_seeds, 8), workers)
    log(f"final champion vs starting weights: {wins}-{total - wins}")
    return champ, adopted, games


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=45.0)
    ap.add_argument("--max-iters", type=int, default=10_000)
    ap.add_argument("--seeds", type=int, default=24, help="paired seeds per iteration")
    ap.add_argument("--workers", type=int, default=min(8, multiprocessing.cpu_count()))
    ap.add_argument(
        "--adopt-fraction", type=float, default=0.625,
        help="adopt iff challenger wins >= this fraction (0.625 of 48 games = 30)",
    )
    ap.add_argument("--jitter", type=float, default=0.3)
    ap.add_argument("--knobs", type=int, default=8, help="weights perturbed per challenger")
    ap.add_argument("--out", default="data/tuned_selfplay.json")
    args = ap.parse_args()

    champ, adopted, games = fit(
        args.minutes, args.max_iters, args.seeds, args.workers,
        int(round(args.adopt_fraction * 2 * args.seeds)), args.jitter, args.knobs,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"weights": {f: getattr(champ, f) for f in GreedyWeights.__dataclass_fields__},
         "adopted": adopted, "games": games},
        indent=1,
    ))
    print(f"wrote {out} ({adopted} adopted over {games} games)")


if __name__ == "__main__":
    main()
