"""PPO self-play training loop for the neural bot.

    python scripts/train_ppo.py --iterations 100 --games 32 --workers 8
    python scripts/train_ppo.py --device cpu --iterations 5 --games 8   # smoke

Each iteration: snapshot the policy to disk, run a batch of self-play games in
parallel (workers load the snapshot on CPU), collect per-side episodes, and run
PPO updates on the training device. Periodically evaluates head-to-head against
the greedy heuristic with the arena and saves `latest`/`best` checkpoints.

Reward is sparse ±1 terminal (draw 0). Sparse, unshaped reward is what made the
proven offline attempts work; add potential-based shaping only if needed.
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import torch  # noqa: E402

from struggler.arena import PlayerSpec, head_to_head, run_matchup  # noqa: E402
from struggler.bots.rl.encode import OPTION_DIM, STATE_DIM  # noqa: E402
from struggler.bots.rl.net import ActorCritic, save_policy  # noqa: E402
from struggler.bots.rl.ppo import ppo_update  # noqa: E402
from struggler.bots.rl.selfplay import _collect_job  # noqa: E402


def evaluate(net_path: str, seeds: list[int], workers: int, events: bool) -> tuple[int, int, int]:
    rl = PlayerSpec("rl", "rl", net_path=net_path)
    greedy = PlayerSpec("greedy", "greedy")
    results = run_matchup(rl, greedy, seeds, workers=workers, events=events)
    return head_to_head(results, "rl", "greedy")


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
    ap.add_argument("--device", default="auto", help="auto | cpu | mps")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--seed-base", type=int, default=0)
    ap.add_argument("--eval-every", type=int, default=5)
    ap.add_argument("--eval-seeds", type=int, default=4)
    ap.add_argument("--out-dir", default="data")
    ap.add_argument(
        "--events", action=argparse.BooleanOptionalAction, default=True,
        help="Pass events= to Engine.new_game (default: on).",
    )
    args = ap.parse_args()

    if args.device == "auto":
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    else:
        device = args.device
    print(f"device={device}  state_dim={STATE_DIM}  option_dim={OPTION_DIM}")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    snap = str(out / "ppo_snap.pt")
    net = ActorCritic(STATE_DIM, OPTION_DIM, args.hidden).to(device)
    optimizer = torch.optim.Adam(net.parameters(), lr=args.lr)

    ctx = multiprocessing.get_context("spawn")
    best_rate = -1.0
    for it in range(args.iterations):
        t0 = time.perf_counter()
        save_policy(snap, net)
        seeds = [args.seed_base + it * (args.games + 7) + i for i in range(args.games)]
        jobs = [(snap, s, args.anchor, args.events) for s in seeds]
        with ctx.Pool(args.workers) as pool:
            episodes = [ep for sub in pool.map(_collect_job, jobs) for ep in sub]
        stats = ppo_update(
            net, optimizer, episodes, device=device,
            epochs=args.epochs, minibatch=args.minibatch, seed=args.seed + it,
        )
        print(
            f"iter {it:4d}  {len(episodes):5d} eps  {stats['transitions']:6.0f} steps  "
            f"pi {stats['policy']:+.3f}  v {stats['value']:.3f}  H {stats['entropy']:.3f}  "
            f"{time.perf_counter() - t0:5.1f}s"
        )

        if (it + 1) % args.eval_every == 0 or it == args.iterations - 1:
            save_policy(str(out / "ppo_latest.pt"), net)
            ev = [900_000 + it * (args.eval_seeds + 3) + i for i in range(args.eval_seeds)]
            wins, losses, draws = evaluate(str(out / "ppo_latest.pt"), ev, args.workers, args.events)
            rate = wins / max(1, wins + losses)
            tag = ""
            if rate > best_rate:
                best_rate = rate
                save_policy(str(out / "ppo_best.pt"), net)
                tag = "  -> best"
            print(f"           eval vs greedy: {wins}-{losses}-{draws} ({rate:.0%}){tag}")

    print("done")


if __name__ == "__main__":
    main()
