"""Fit GreedyWeights to expert decisions: maximize top-1 agreement.

    python scripts/fit_weights.py --minutes 45
    python scripts/fit_weights.py --minutes 0 --max-sweeps 0   # baseline only

The expert states are FIXED: every candidate weight set is scored on the
same replayed game states (the driver always answers from the log, so the
search cannot drift the game). Coordinate descent, multiplicative steps:
for each knob try x2 and x0.5, keep what improves agreement. Deterministic;
a --minutes budget checkpoints the best-so-far to data/tuned_weights.json.

`defcon_self_kill_penalty` is never tuned: it is a guardrail sentinel, not
a preference.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from replay_game import Replay  # noqa: E402  (sibling script)

from struggler.bots.greedy import GreedyPlayer, GreedyWeights  # noqa: E402
from struggler.engine import Side  # noqa: E402

# Sentinel guardrails stay fixed; everything else is multiplicative.
TUNABLE = [f for f in GreedyWeights.__dataclass_fields__
           if f != "defcon_self_kill_penalty"]


def load_games(paths: list[str]) -> list[dict]:
    files: list[str] = []
    for p in paths:
        files.extend(sorted(glob.glob(p)))
    return [json.loads(Path(f).read_text()) for f in files]


def agreement(games: list[dict], weights: GreedyWeights) -> tuple[float, int]:
    greedy = GreedyPlayer(weights=weights)
    total = matched = 0
    for game in games:
        r = Replay(game)
        while not r.engine.is_terminal and not r.exhausted and r.ri < len(r.actions):
            dec = r.engine.pending_decision
            if dec is None:
                break
            before = len(r.errors)
            action = r.answer(dec)
            clean = len(r.errors) == before
            if clean and dec.actor in (Side.US, Side.USSR):
                obs = r.engine.observe(dec.actor)
                try:
                    g = greedy.choose_action(obs, ())
                    ok = g in dec.options and g.payload == action.payload
                except Exception:
                    ok = False
                matched += ok
                total += 1
            r.engine.step(action)
            r._consume_assertions()
    return (matched / total if total else 0.0), total


def fit(
    games: list[dict],
    minutes: float,
    max_sweeps: int,
    log=lambda msg: print(msg, flush=True),
) -> tuple[dict, float, list]:
    params = {f: getattr(GreedyWeights(), f) for f in TUNABLE}
    best_score, n = agreement(games, GreedyWeights())
    log(f"baseline agreement {best_score:.3%} over {n} decisions")
    history = [(dict(params), best_score)]
    deadline = time.monotonic() + minutes * 60
    for sweep in range(1, max_sweeps + 1):
        improved = False
        for field in TUNABLE:
            for mult in (2.0, 0.5):
                cand = dict(params)
                cand[field] = max(cand[field] * mult, 1e-6)
                if time.monotonic() > deadline:
                    return params, best_score, history
                score, n = agreement(games, GreedyWeights(**cand))
                log(f"sweep {sweep} {field} x{mult:g}: {score:.3%} (best {best_score:.3%})")
                if score > best_score + 1e-9:
                    params, best_score = cand, score
                    history.append((dict(params), best_score))
                    improved = True
                    break  # keep the first improving step for this knob
        if not improved:
            log(f"converged after {sweep} sweeps")
            break
    return params, best_score, history


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", default=["parsed/*.json"])
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--max-sweeps", type=int, default=12)
    ap.add_argument("--out", default="data/tuned_weights.json")
    args = ap.parse_args()

    games = load_games(args.paths if isinstance(args.paths, list) else [args.paths])
    params, score, history = fit(games, args.minutes, args.max_sweeps)
    print(f"\nfinal agreement {score:.3%}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"weights": params, "agreement": score}, indent=1))
    print(f"wrote {out}")
    for field in TUNABLE:
        base = getattr(GreedyWeights(), field)
        if params[field] != base:
            print(f"  {field}: {base:g} -> {params[field]:g}")


if __name__ == "__main__":
    main()
