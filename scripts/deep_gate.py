"""Deep bot vs one opponent, side-swapped, as JSON (one shard of the gate).

    python scripts/deep_gate.py --opponent mcts --seeds 5000:5010 --workers 4 --out shard.json
    python scripts/deep_gate.py --search on --opponent deep --seeds 5000:5010   # search vs plain
    python scripts/deep_gate.py --summarize shard*.json

The Phase 1 gate (docs/BOTS.md, "Deep bot"): above 70% against `mcts` with
the Wilson interval clear of 50%, over 200 side-swapped games. `mcts` games
are slow, so .github/workflows/deep-arena.yml splits the seeds into shards
on separate runners and summarizes them.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from struggler.arena import PlayerSpec, run_matchup, wilson_interval  # noqa: E402


def summarize(rows: list[dict]) -> str:
    wins = sum(r["deep_won"] for r in rows)
    draws = sum(r["draw"] for r in rows)
    losses = len(rows) - wins - draws
    lo, hi = wilson_interval(wins, losses)
    me = rows[0].get("deep", "deep")
    lines = [f"{me} vs {rows[0]['opponent']}: {wins}-{losses}-{draws} over {len(rows)} games, "
             f"win rate {wins / max(1, wins + losses):.1%} [Wilson 95%: {lo:.1%}-{hi:.1%}]"]
    for side in ("US", "USSR"):
        mine = [r for r in rows if r["deep_side"] == side]
        lines.append(f"  as {side}: won {sum(r['deep_won'] for r in mine)} of {len(mine)}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--opponent", default="mcts",
                    help="a player kind; 'deep' is the plain network, 'deep:<spec>' the network with that search")
    ap.add_argument("--search", default="off", help="the deep seat's rollout search: off, on, or k:worlds:horizon:rule")
    ap.add_argument("--sims", type=int, default=16, help="mcts simulations (the browser default)")
    ap.add_argument("--seeds", default="5000:5010", help="start:stop seed range")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--model", default=str(ROOT / "data" / "deep" / "model.onnx"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--summarize", nargs="*", default=None, help="shard JSON files to combine")
    args = ap.parse_args()
    if args.summarize is not None:
        rows = [row for path in args.summarize for row in json.loads(Path(path).read_text())]
        print(summarize(rows))
        return
    start, stop = (int(x) for x in args.seeds.split(":"))
    me = "deep" if args.search == "off" else f"deep+search({args.search})"
    deep = PlayerSpec(me, "deep", net_path=args.model, player_seed=1, search=args.search)
    if args.opponent.split(":")[0] == "deep":
        opp_search = args.opponent.partition(":")[2] or "off"
        opp_name = "deep" if opp_search == "off" else f"deep+search({opp_search})"
        if opp_name == me:
            opp_name += "-2"
        opp = PlayerSpec(opp_name, "deep", net_path=args.model, player_seed=2, search=opp_search)
    else:
        opp = PlayerSpec(args.opponent, args.opponent, sims=args.sims, player_seed=1)
    began = time.perf_counter()
    results = run_matchup(deep, opp, list(range(start, stop)), workers=args.workers)
    rows = []
    for r in results:
        side = "US" if r.us == me else "USSR"
        rows.append({"seed": r.seed, "deep": me, "opponent": opp.name, "deep_side": side,
                     "winner": r.winner, "deep_won": r.winner == side, "draw": r.winner is None})
    if args.out:
        Path(args.out).write_text(json.dumps(rows))
    print(summarize(rows))
    print(f"{len(rows)} games in {time.perf_counter() - began:.0f}s")


if __name__ == "__main__":
    main()
