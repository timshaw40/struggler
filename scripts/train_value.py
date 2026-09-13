"""Train a LinearValue by self-play for MCTS leaf evaluation.

    python scripts/train_value.py --games 60 --workers 8 --out data/value.json

Runs seeded greedy-vs-greedy games, records the public board features at each
player decision, labels them with the final result from the acting side's
perspective (+1 win / -1 loss / 0 draw), and fits a ridge regression. Each
sample is mirrored with its negation so the fitted value is antisymmetric
(value(US) + value(USSR) = 1). Reports held-out mean-squared error and sign
accuracy, then saves the model for `MCTSPlayer(..., value=LinearValue.load(...))`.
"""

from __future__ import annotations

import argparse
import multiprocessing
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.bots.greedy import GreedyPlayer  # noqa: E402
from struggler.bots.value import LinearValue, features_from_engine, fit_ridge  # noqa: E402
from struggler.engine import Engine, Side  # noqa: E402


def _collect_job(args: tuple[int, bool, int]):
    seed, events, stride = args
    engine = Engine.new_game(seed=seed, events=events)
    players = {Side.US: GreedyPlayer(), Side.USSR: GreedyPlayer()}
    feats, actors = [], []
    seen = 0
    while not engine.is_terminal:
        dec = engine.pending_decision
        if dec is None:
            break
        if dec.actor is not Side.CHANCE:
            if seen % stride == 0:
                feats.append(features_from_engine(engine, dec.actor))
                actors.append(dec.actor.value)
            seen += 1
            action = players[dec.actor].choose_action(engine.observe(dec.actor), ())
        else:
            action = dec.options[0]
        engine.step(action)
    winner = engine.winner.value if engine.winner is not None else None
    return feats, actors, winner


def collect(games: int, seed_base: int, workers: int, events: bool, stride: int):
    jobs = [(seed_base + i, events, stride) for i in range(games)]
    if workers <= 1:
        return [_collect_job(j) for j in jobs]
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(workers) as pool:
        return pool.map(_collect_job, jobs)


def build_dataset(raw) -> tuple[list[list[float]], list[float]]:
    X: list[list[float]] = []
    Y: list[float] = []
    for feats, actors, winner in raw:
        for f, actor in zip(feats, actors):
            y = 0.0 if winner is None else (1.0 if actor == winner else -1.0)
            X.append(f)
            Y.append(y)
            X.append([-v for v in f])  # mirrored sample enforces antisymmetry
            Y.append(-y)
    return X, Y


def _mse(X, Y, w) -> float:
    return sum((sum(wi * xi for wi, xi in zip(w, x)) - y) ** 2 for x, y in zip(X, Y)) / max(1, len(X))


def _sign_accuracy(X, Y, w) -> float:
    hits = sum(1 for x, y in zip(X, Y) if y == 0 or (sum(wi * xi for wi, xi in zip(w, x)) >= 0) == (y > 0))
    return hits / max(1, len(X))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--seed-base", type=int, default=0)
    ap.add_argument("--workers", type=int, default=min(8, multiprocessing.cpu_count()))
    ap.add_argument("--stride", type=int, default=1, help="record every Nth player decision")
    ap.add_argument("--l2", type=float, default=1.0)
    ap.add_argument("--val-fraction", type=float, default=0.15)
    ap.add_argument("--out", default="data/value.json")
    ap.add_argument(
        "--events", action=argparse.BooleanOptionalAction, default=True,
        help="Pass events= to Engine.new_game (default: on).",
    )
    args = ap.parse_args()

    raw = collect(args.games, args.seed_base, args.workers, args.events, args.stride)
    X, Y = build_dataset(raw)
    rng = random.Random(0)
    order = list(range(len(X)))
    rng.shuffle(order)
    cut = int(len(order) * (1 - args.val_fraction))
    train, val = order[:cut], order[cut:]
    w = fit_ridge([X[i] for i in train], [Y[i] for i in train], l2=args.l2)

    print(f"{len(raw)} games, {len(X)} samples ({len(train)} train / {len(val)} val)")
    print(f"train MSE {_mse([X[i] for i in train], [Y[i] for i in train], w):.3f}  "
          f"val MSE {_mse([X[i] for i in val], [Y[i] for i in val], w):.3f}")
    print(f"val sign accuracy {_sign_accuracy([X[i] for i in val], [Y[i] for i in val], w):.1%}")

    LinearValue(weights=w).save(args.out)
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
