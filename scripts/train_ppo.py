"""PPO self-play training loop for the neural bot.

    python scripts/train_ppo.py --iterations 200 --games 64 --workers 8
    python scripts/train_ppo.py --device cpu --iterations 6 --games 8 --eval-every 2   # smoke

A single persistent actor pool (see `bots/rl/actors.py`) collects self-play
games against a **league** of past promoted checkpoints plus the greedy anchor,
and PPO updates on the training device. Periodically it plays a head-to-head
gate against the incumbent best (and a small Elo round-robin versus
greedy/random) and only promotes on a head-to-head win — win rate against one
fixed opponent saturates and misleads.

Metrics printed per iteration are the ones to watch: `pi` policy loss, `v`
value loss, `H` entropy (should fall slowly, not collapse), `kl`/`clip` update
stability, and `ev` explained variance (how well the value predicts returns).
"""

from __future__ import annotations

import argparse
import multiprocessing
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import torch  # noqa: E402

from struggler.arena import PlayerSpec, elo, head_to_head, round_robin  # noqa: E402
from struggler.bots.rl.actors import collect_task, make_pool  # noqa: E402
from struggler.bots.rl.encode import OPTION_DIM, STATE_DIM  # noqa: E402
from struggler.bots.rl.net import ActorCritic, save_policy  # noqa: E402
from struggler.bots.rl.ppo import ppo_update  # noqa: E402


def evaluate(candidate: str, incumbent: str | None, pool, args) -> tuple[bool, dict]:
    specs = [PlayerSpec("cand", "rl", net_path=candidate)]
    if incumbent:
        specs.append(PlayerSpec("best", "rl", net_path=incumbent))
    specs += [
        PlayerSpec("greedy", "greedy"),
        PlayerSpec("random", "random", player_seed=1),
    ]
    seeds = [args.eval_base + i for i in range(args.eval_seeds)]
    results = round_robin(specs, seeds, events=args.events, pool=pool)
    ratings = elo(results, [s.name for s in specs])
    gw, gl, gd = head_to_head(results, "cand", "greedy")
    report = {
        "elo": {k: round(v, 1) for k, v in ratings.items()},
        "vs_greedy": (gw, gl, gd),
    }
    promote = True
    if incumbent:
        bw, bl, bd = head_to_head(results, "cand", "best")
        report["vs_best"] = (bw, bl, bd)
        promote = bw > bl
    return promote, report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iterations", type=int, default=100)
    ap.add_argument("--games", type=int, default=32, help="self-play games per iteration")
    ap.add_argument("--workers", type=int, default=min(8, multiprocessing.cpu_count()))
    ap.add_argument("--hidden", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--minibatch", type=int, default=64)
    ap.add_argument("--anchor", type=float, default=0.25, help="fraction of seats played by greedy")
    ap.add_argument("--league-fraction", type=float, default=0.3, help="games vs a past checkpoint")
    ap.add_argument("--league-size", type=int, default=4)
    ap.add_argument("--device", default="auto", help="auto | cpu | mps")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seed-base", type=int, default=0)
    ap.add_argument("--eval-every", type=int, default=10)
    ap.add_argument("--eval-seeds", type=int, default=3)
    ap.add_argument("--eval-base", type=int, default=900_000)
    ap.add_argument("--out-dir", default="data")
    ap.add_argument(
        "--events", action=argparse.BooleanOptionalAction, default=True,
        help="Pass events= to Engine.new_game (default: on).",
    )
    args = ap.parse_args()

    device = ("mps" if torch.backends.mps.is_available() else "cpu") if args.device == "auto" else args.device
    print(f"device={device}  state_dim={STATE_DIM}  option_dim={OPTION_DIM}")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    net = ActorCritic(STATE_DIM, OPTION_DIM, args.hidden).to(device)
    optimizer = torch.optim.Adam(net.parameters(), lr=args.lr)
    rng = random.Random(args.seed)
    league: list[str] = []
    best_path: str | None = None
    best_elo = float("-inf")

    pool = make_pool(args.workers)
    try:
        for it in range(args.iterations):
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
                net, optimizer, episodes, device=device,
                epochs=args.epochs, minibatch=args.minibatch, seed=args.seed + it,
            )
            print(
                f"iter {it:4d}  {len(episodes):5d} eps  {stats['transitions']:6.0f} steps  "
                f"pi {stats['policy']:+.3f}  v {stats['value']:.3f}  H {stats['entropy']:.2f}  "
                f"kl {stats['kl']:+.3f}  clip {stats['clip']:.2f}  ev {stats['explained_var']:+.2f}  "
                f"{time.perf_counter() - t0:5.1f}s"
            )

            if (it + 1) % args.eval_every == 0 or it == args.iterations - 1:
                cand = str(out / "ppo_latest.pt")
                save_policy(cand, net)
                promote, report = evaluate(cand, best_path, pool, args)
                gw, gl, gd = report["vs_greedy"]
                line = (f"           eval  vs greedy {gw}-{gl}-{gd} "
                        f"({gw / max(1, gw + gl):.0%})  elo {report['elo']}")
                if "vs_best" in report:
                    bw, bl, bd = report["vs_best"]
                    line += f"  vs best {bw}-{bl}-{bd}"
                print(line)
                if promote:
                    save_policy(str(out / "ppo_best.pt"), net)
                    best_path = str(out / "ppo_best.pt")
                    snap_path = str(out / f"league_{it:05d}.pt")
                    save_policy(snap_path, net)
                    league.append(snap_path)
                    if len(league) > args.league_size:
                        league.pop(0)
                    print("           promoted -> best + league")
    finally:
        pool.close()
        pool.join()
    print("done")


if __name__ == "__main__":
    main()
