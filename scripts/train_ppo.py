"""PPO self-play training loop for the neural bot.

    python scripts/train_ppo.py --iterations 500 --games 64 --workers 8 \
        --eval-every 25 --eval-seeds 8 --gate-seeds 12 --shaping

A single persistent actor pool (`bots/rl/actors.py`) collects self-play games
against a league of past promoted checkpoints plus the greedy anchor; PPO
updates on the training device. Periodically it:

  * rates the candidate on a small Elo round-robin vs the fixed bots (anchored,
    so readings are comparable across evals),
  * runs a **significance gate** head-to-head against the incumbent best
    (paired, side-swapped seeds; promote only on a wins-minus-losses margin),
  * profiles a few RL-vs-greedy games for degenerate behavior (DEFCON losses,
    over-couping, a Southeast-Asia blind spot).

It writes `data/metrics.jsonl` (one JSON object per iteration) and a resumable
`data/run.pt`; `--resume data/run.pt` continues a long run.

Metrics to watch per iteration: `pi`, `v`, `H` (entropy), `kl`, `clip`,
`ev` (explained variance of the value). `--shaping` adds potential-based reward
shaping from the greedy heuristic — safe (leaves the optimal policy unchanged)
and usually speeds up the sparse ±1 signal.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import torch  # noqa: E402

from struggler.arena import PlayerSpec, elo, head_to_head, round_robin, run_matchup  # noqa: E402
from struggler.bots.rl.actors import collect_task, make_pool  # noqa: E402
from struggler.bots.rl.encode import OPTION_DIM, STATE_DIM  # noqa: E402
from struggler.bots.rl.net import ActorCritic, save_policy  # noqa: E402
from struggler.bots.rl.ppo import ppo_update  # noqa: E402
from struggler.bots.rl.selfplay import profile_game  # noqa: E402


def save_run(path, net, optimizer, iteration, best_path, league, shaping) -> None:
    torch.save(
        {
            "state_dim": net.state_dim, "option_dim": net.option_dim, "hidden": net.hidden,
            "state_dict": net.state_dict(), "optimizer": optimizer.state_dict(),
            "iteration": iteration, "best_path": best_path, "league": list(league),
            "shaping": bool(shaping),
        },
        path,
    )


def behavior_report(net, seeds, device, events) -> dict:
    rows = [profile_game(net, s, events=events, device=str(device)) for s in seeds]
    n = len(rows)
    if not n:
        return {}
    avg = lambda key: sum(r[key] for r in rows) / n
    return {
        "draws": sum(1 for r in rows if r["winner"] is None),
        "defcon1": sum(1 for r in rows if r["reason"] == "defcon_1"),
        "rl_wins": sum(1 for r in rows if r["winner"] == r["rl_side"]),
        "avg_turn": round(avg("turn"), 1),
        "avg_coups": round(avg("coups"), 1),
        "se_us": round(avg("se_us"), 1),
        "se_ussr": round(avg("se_ussr"), 1),
    }


def evaluate(candidate: str, pool, args) -> dict:
    specs = [
        PlayerSpec("cand", "rl", net_path=candidate),
        PlayerSpec("greedy", "greedy"),
        PlayerSpec("random", "random", player_seed=1),
        PlayerSpec("first", "first"),
    ]
    seeds = [args.eval_base + i for i in range(args.eval_seeds)]
    results = round_robin(specs, seeds, events=args.events, pool=pool)
    gw, gl, gd = head_to_head(results, "cand", "greedy")
    return {
        "elo": {k: round(v, 1) for k, v in elo(results, [s.name for s in specs]).items()},
        "vs_greedy": [gw, gl, gd],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iterations", type=int, default=500)
    ap.add_argument("--games", type=int, default=64, help="self-play games per iteration")
    ap.add_argument("--workers", type=int, default=min(8, multiprocessing.cpu_count()))
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--minibatch", type=int, default=64)
    ap.add_argument("--anchor", type=float, default=0.25, help="fraction of seats played by greedy")
    ap.add_argument("--league-fraction", type=float, default=0.3, help="games vs a past checkpoint")
    ap.add_argument("--league-size", type=int, default=4)
    ap.add_argument("--shaping", action="store_true", help="potential-based shaping from the heuristic")
    ap.add_argument("--device", default="auto", help="auto | cpu | mps")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seed-base", type=int, default=0)
    ap.add_argument("--eval-every", type=int, default=25)
    ap.add_argument("--eval-seeds", type=int, default=8, help="seeds for the Elo round-robin")
    ap.add_argument("--gate-seeds", type=int, default=12, help="paired seeds for the promotion gate")
    ap.add_argument("--gate-margin", type=int, default=2, help="promote iff wins-losses >= margin")
    ap.add_argument("--probe-games", type=int, default=4, help="RL-vs-greedy games for behavior")
    ap.add_argument("--eval-base", type=int, default=900_000)
    ap.add_argument("--resume", default=None, help="run.pt to continue")
    ap.add_argument("--out-dir", default="data")
    ap.add_argument("--metrics", default=None, help="defaults to <out-dir>/metrics.jsonl")
    ap.add_argument(
        "--events", action=argparse.BooleanOptionalAction, default=True,
        help="Pass events= to Engine.new_game (default: on).",
    )
    args = ap.parse_args()

    device = ("mps" if torch.backends.mps.is_available() else "cpu") if args.device == "auto" else args.device
    print(f"device={device}  state_dim={STATE_DIM}  option_dim={OPTION_DIM}  shaping={args.shaping}")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    metrics_path = Path(args.metrics) if args.metrics else out / "metrics.jsonl"
    net = ActorCritic(STATE_DIM, OPTION_DIM, args.hidden).to(device)
    optimizer = torch.optim.Adam(net.parameters(), lr=args.lr)
    start_it = 0
    best_path: str | None = None
    league: list[str] = []
    if args.resume:
        ckpt = torch.load(args.resume, map_location=device)
        net.load_state_dict(ckpt["state_dict"])
        optimizer.load_state_dict(ckpt["optimizer"])
        start_it = int(ckpt.get("iteration", 0))
        best_path = ckpt.get("best_path")
        league = [p for p in ckpt.get("league", []) if os.path.exists(p)]
        args.shaping = args.shaping or bool(ckpt.get("shaping", False))
        print(f"resumed from {args.resume} at iteration {start_it} (shaping={args.shaping})")

    rng = random.Random(args.seed + start_it)
    pool = make_pool(args.workers)
    try:
        for it in range(start_it, args.iterations):
            t0 = time.perf_counter()
            snap = str(out / f"snap_{it:05d}.pt")
            save_policy(snap, net)
            jobs = []
            for i in range(args.games):
                seed = args.seed_base + it * (args.games + 7) + i
                opponent = rng.choice(league) if (league and rng.random() < args.league_fraction) else None
                jobs.append((snap, opponent, seed, args.anchor, args.events))
            episodes = [ep for sub in pool.map(collect_task, jobs) for ep in sub]
            stats = ppo_update(
                net, optimizer, episodes, device=device, epochs=args.epochs,
                minibatch=args.minibatch, seed=args.seed + it, shaping=args.shaping,
            )
            os.remove(snap)
            record = {"iteration": it, **stats}
            print(
                f"iter {it:4d}  {len(episodes):5d} eps  {stats['transitions']:6.0f} steps  "
                f"pi {stats['policy']:+.3f}  v {stats['value']:.3f}  H {stats['entropy']:.2f}  "
                f"kl {stats['kl']:+.3f}  clip {stats['clip']:.2f}  ev {stats['explained_var']:+.2f}  "
                f"{time.perf_counter() - t0:5.1f}s"
            )

            if (it + 1) % args.eval_every == 0 or it == args.iterations - 1:
                cand = str(out / "ppo_latest.pt")
                save_policy(cand, net)
                ev = evaluate(cand, pool, args)
                report = {"eval": ev}

                # Significance gate vs the incumbent best.
                if best_path and os.path.exists(best_path):
                    gate_seeds = [args.eval_base + 500_000 + i for i in range(args.gate_seeds)]
                    results = run_matchup(
                        PlayerSpec("cand", "rl", net_path=cand),
                        PlayerSpec("best", "rl", net_path=best_path),
                        gate_seeds, events=args.events, pool=pool,
                    )
                    bw, bl, bd = head_to_head(results, "cand", "best")
                    promote = (bw - bl) >= args.gate_margin
                    report["gate"] = [bw, bl, bd]
                else:
                    bw = bl = bd = 0
                    promote = True

                report["behavior"] = behavior_report(
                    net, [1234 + i for i in range(args.probe_games)], device, args.events
                )
                gw, gl, gd = ev["vs_greedy"]
                print(
                    f"           eval  vs greedy {gw}-{gl}-{gd} ({gw / max(1, gw + gl):.0%})  "
                    f"elo {ev['elo']}"
                    + (f"  gate vs best {bw}-{bl}-{bd}" if "gate" in report else "")
                )
                if report.get("behavior"):
                    b = report["behavior"]
                    print(f"           behavior  draws {b['draws']}  defcon1 {b['defcon1']}  "
                          f"rlW {b['rl_wins']}  turn {b['avg_turn']}  coups {b['avg_coups']}  "
                          f"SE US {b['se_us']}/USSR {b['se_ussr']}")

                if promote:
                    save_policy(str(out / "ppo_best.pt"), net)
                    best_path = str(out / "ppo_best.pt")
                    snap_path = str(out / f"league_{it:05d}.pt")
                    save_policy(snap_path, net)
                    league.append(snap_path)
                    if len(league) > args.league_size:
                        league.pop(0)
                    print("           promoted -> best + league")

                record.update(report)
                save_run(str(out / "run.pt"), net, optimizer, it + 1, best_path, league, args.shaping)

            with metrics_path.open("a") as fh:
                fh.write(json.dumps(record) + "\n")
    finally:
        pool.close()
        pool.join()
    print(f"done; metrics in {metrics_path}")


if __name__ == "__main__":
    main()
