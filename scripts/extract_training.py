"""Extract the expert-decision training set from the parsed games.

    python scripts/extract_training.py parsed/*.json --out data/expert_decisions.jsonl

Runs each parsed game through the replay driver and records, for every
decision the log answered cleanly (no fallback), what GreedyPlayer would
have done at that exact engine state:

  {"game": ..., "turn": ..., "kind": ..., "actor": ...,
   "chosen": <option index>, "greedy": <greedy's index>,
   "greedy_scores": [...], "n_options": N, "payload": {...}}

Two consumers:
- the agreement rate (greedy == log) is the strength baseline every
  weight change must not regress;
- the records are the supervised set: fit the ~20 GreedyWeights knobs to
  maximize expert agreement (a weight is good iff it makes the bot choose
  what WBC champions chose).
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from replay_game import Replay  # noqa: E402  (sibling script)

from struggler.bots.greedy import GreedyPlayer  # noqa: E402
from struggler.engine import Side  # noqa: E402


def extract(game: dict, greedy: GreedyPlayer, max_errors: int = 12) -> tuple[list[dict], dict]:
    r = Replay(game)
    rows: list[dict] = []
    decisions = 0
    # Quality first: once the engine's board has diverged from the log in
    # more than `max_errors` places, later decisions describe positions the
    # experts never faced — stop rather than pollute the training set.
    stopped = "records_end"
    while not r.engine.is_terminal and not r.exhausted and r.ri < len(r.actions):
        dec = r.engine.pending_decision
        if dec is None:
            break
        if len(r.errors) > max_errors:
            stopped = "board_divergence"
            break
        before = len(r.errors)
        action = r.answer(dec)
        clean = len(r.errors) == before
        if dec.actor in (Side.US, Side.USSR) and clean:
            decisions += 1
            obs = r.engine.observe(dec.actor)
            g_action = greedy.choose_action(obs, ())
            g_idx = dec.options.index(g_action) if g_action in dec.options else None
            chosen = next(
                (i for i, a in enumerate(dec.options) if a.payload == action.payload),
                None,
            )
            rows.append({
                "game": game["source"],
                "turn": r.actions[r.ri]["turn"] if r.ri < len(r.actions) else None,
                "kind": dec.kind.value,
                "actor": dec.actor.value,
                "n_options": len(dec.options),
                "chosen": chosen,
                "greedy": g_idx,
                "match": chosen is not None and chosen == g_idx,
            })
        r.engine.step(action)
        r._consume_assertions()
    agree = sum(row["match"] for row in rows)
    return rows, {
        "game": game["source"],
        "records_total": len(game["actions"]),
        "records_consumed": min(r.ri + 1, len(game["actions"])),
        "stopped": stopped,
        "human_decisions": len(rows),
        "greedy_agreement": round(agree / len(rows), 3) if rows else None,
        "fallbacks": r.fallbacks,
        "board_errors": len(r.errors),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", default="data/expert_decisions.jsonl")
    args = ap.parse_args()

    files: list[str] = []
    for p in args.paths:
        files.extend(sorted(glob.glob(p)))
    greedy = GreedyPlayer()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    total_decisions = total_matches = 0
    with out.open("w") as fh:
        for f in files:
            game = json.loads(Path(f).read_text())
            rows, summary = extract(game, greedy)
            total_decisions += len(rows)
            total_matches += sum(row["match"] for row in rows)
            for row in rows:
                fh.write(json.dumps(row) + "\n")
            print(json.dumps(summary))
    print(
        f"\nTOTAL: {total_decisions} expert decisions, "
        f"greedy agreement {total_matches}/{total_decisions} "
        f"= {total_matches / total_decisions:.1%}" if total_decisions else ""
    )


if __name__ == "__main__":
    main()
