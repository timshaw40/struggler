"""CEM/evolutionary tuner for GreedyWeights, evaluated against a fixed ladder.

    python scripts/tune_greedy.py --minutes 20
    python scripts/tune_greedy.py --population 12 --elites 3 --seeds 5 --restart <weights.json>

Candidate fitness = mean score across a *ladder* (random, first, the
incumbent), with paired, side-swapped seeds, so no candidate can overfit a
single opponent — the failure mode of the project's earlier champion-vs-
challenger self-play tuner. Sampling is a Gaussian in log-weight space (all
tunable weights are positive) with per-dimension variance; each generation
keeps the elites and re-centres on them. The best candidate is re-scored on a
held-out seed bank against the incumbent and only saved if it wins that gate.
"""

from __future__ import annotations

import argparse
import math
import multiprocessing
import random
import sys
import time
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.arena import PlayerSpec, head_to_head, play_one, run_matchup  # noqa: E402
from struggler.bots.checkpoint import GUARDRAILS, load_weights, save_weights, weights_to_dict  # noqa: E402
from struggler.bots.greedy import GreedyWeights  # noqa: E402

TUNABLE = [f.name for f in fields(GreedyWeights) if f.name not in GUARDRAILS]
DIM = len(TUNABLE)
LOG_FLOOR = 0.03  # minimum per-dimension std in log space
CLAMP = (1e-4, 1e4)


def _clamp(x: float) -> float:
    return max(CLAMP[0], min(CLAMP[1], x))


def vec_to_weights(vec: list[float], guard: GreedyWeights) -> GreedyWeights:
    kw = {f: _clamp(v) for f, v in zip(TUNABLE, vec)}
    for g in GUARDRAILS:
        kw[g] = getattr(guard, g)
    return GreedyWeights(**kw)


def sample_log(mu: list[float], sigma: list[float], rng: random.Random) -> list[float]:
    """A Gaussian sample in log-weight space."""
    return [mu[i] + sigma[i] * rng.gauss(0.0, 1.0) for i in range(DIM)]


def logvec_to_weights(logvec: list[float], guard: GreedyWeights) -> GreedyWeights:
    return vec_to_weights([math.exp(x) for x in logvec], guard)


def _eval_job(args: tuple) -> tuple[int, float, int]:
    ci, name, weights, opp_name, opp_kind, opp_weights, seed, events = args
    cand = PlayerSpec(name, "greedy", weights)
    opp = PlayerSpec(opp_name, opp_kind, opp_weights)
    pts, n = 0.0, 0
    for us, ussr in ((cand, opp), (opp, cand)):
        result = play_one(seed, us, ussr, events=events)
        n += 1
        if result.winner is None:
            pts += 0.5
        elif (result.winner == "US") == (us.name == name):
            pts += 1.0
    return ci, pts, n


def evaluate_generation(
    candidate_weights: list[dict], ladder: list[PlayerSpec], seeds: list[int],
    workers: int, events: bool, pool,
) -> list[float]:
    jobs = []
    for ci, weights in enumerate(candidate_weights):
        for opp in ladder:
            for seed in seeds:
                jobs.append((ci, f"cand{ci}", weights, opp.name, opp.kind, opp.weights, seed, events))
    pts = [0.0] * len(candidate_weights)
    games = [0] * len(candidate_weights)
    for ci, p, n in pool.map(_eval_job, jobs):
        pts[ci] += p
        games[ci] += n
    return [pts[i] / games[i] if games[i] else 0.0 for i in range(len(candidate_weights))]


def _mean_std(logs: list[list[float]]) -> tuple[list[float], list[float]]:
    n = len(logs)
    mu = [sum(row[j] for row in logs) / n for j in range(DIM)]
    var = [sum((row[j] - mu[j]) ** 2 for row in logs) / n for j in range(DIM)]
    return mu, [max(LOG_FLOOR, math.sqrt(v)) for v in var]


def run(args: argparse.Namespace) -> GreedyWeights:
    incumbent = load_weights(args.restart) if args.restart else GreedyWeights()
    rng = random.Random(args.seed)
    mu = [math.log(getattr(incumbent, f)) for f in TUNABLE]
    sigma = [args.sigma] * DIM

    ladder = [
        PlayerSpec("random", "random", player_seed=1),
        PlayerSpec("first", "first"),
        PlayerSpec("incumbent", "greedy", weights_to_dict(incumbent)),
    ]
    best_log, best_fit = list(mu), float("-inf")
    deadline = time.monotonic() + args.minutes * 60
    ctx = multiprocessing.get_context("spawn")

    with ctx.Pool(args.workers) as pool:
        for gen in range(args.generations):
            if time.monotonic() > deadline:
                print("time budget reached")
                break
            logvecs = [list(mu)] + [sample_log(mu, sigma, rng) for _ in range(args.population - 1)]
            weights = [weights_to_dict(logvec_to_weights(v, incumbent)) for v in logvecs]
            seeds = [args.seed_base + gen * (args.seeds + 7) + i for i in range(args.seeds)]
            fits = evaluate_generation(weights, ladder, seeds, args.workers, args.events, pool)
            order = sorted(range(len(logvecs)), key=lambda i: -fits[i])
            elites = order[: max(1, args.elites)]
            mu, sigma = _mean_std([logvecs[i] for i in elites])
            if fits[order[0]] > best_fit:
                best_fit, best_log = fits[order[0]], list(logvecs[order[0]])
            print(f"gen {gen:3d}  best-fit {fits[order[0]]:.3f}  "
                  f"mean-fit {sum(fits) / len(fits):.3f}  all-time {best_fit:.3f}")

        champion = logvec_to_weights(best_log, incumbent)
        gate_seeds = [args.gate_base + i for i in range(args.gate_seeds)]
        results = run_matchup(
            PlayerSpec("champion", "greedy", weights_to_dict(champion)),
            PlayerSpec("incumbent", "greedy", weights_to_dict(incumbent)),
            gate_seeds, workers=args.workers, events=args.events,
        )
        wins, losses, draws = head_to_head(results, "champion", "incumbent")
        print(f"\nheld-out gate: champion {wins}-{losses}-{draws} vs incumbent "
              f"({len(gate_seeds)} paired seeds)")

    return champion, (wins, losses, draws)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=20.0)
    ap.add_argument("--generations", type=int, default=40)
    ap.add_argument("--population", type=int, default=12)
    ap.add_argument("--elites", type=int, default=3)
    ap.add_argument("--seeds", type=int, default=5, help="paired seeds per candidate per opponent")
    ap.add_argument("--sigma", type=float, default=0.35, help="initial log-space std")
    ap.add_argument("--seed", type=int, default=7, help="sampler seed")
    ap.add_argument("--seed-base", type=int, default=10_000)
    ap.add_argument("--gate-seeds", type=int, default=12)
    ap.add_argument("--gate-base", type=int, default=900_000)
    ap.add_argument("--workers", type=int, default=min(8, multiprocessing.cpu_count()))
    ap.add_argument("--restart", default=None, help="start from this weights JSON (else defaults)")
    ap.add_argument("--out", default="data/tuned_greedy.json")
    ap.add_argument("--save-always", action="store_true")
    ap.add_argument(
        "--events", action=argparse.BooleanOptionalAction, default=True,
        help="Pass events= to Engine.new_game (default: on).",
    )
    args = ap.parse_args()

    champion, (wins, losses, draws) = run(args)
    if args.save_always or wins > losses:
        save_weights(
            champion, args.out,
            extra={"gate": {"wins": wins, "losses": losses, "draws": draws}},
        )
        print(f"saved {args.out}")
    else:
        print(f"champion did not beat the incumbent ({wins}-{losses}-{draws}); not saved")


if __name__ == "__main__":
    main()
