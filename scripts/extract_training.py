"""Extract the expert-decision set from the parsed games — evidence, carefully.

    python scripts/extract_training.py parsed/*.json --out data/expert_decisions.jsonl

Runs each parsed game through the replay driver and records, for every
decision the log answered cleanly and BEFORE the first divergence, what
GreedyPlayer would have done at that exact engine state:

  {"game": ..., "split": "train"|"eval", "turn": ..., "kind": ..., "actor": ...,
   "n_options": N, "chosen": <option index>, "greedy": <greedy's index>,
   "match": bool, "hand_known": bool}

Two hard rules, because the stored agreement is only meaningful if the
position and candidate set are:

1. Verified prefixes only. Extraction stops at the FIRST divergence (a
   fallback or a board/VP mismatch); a decision after divergence describes a
   position the expert never faced.
2. Hand-known decisions only for agreement. In replay mode a hidden hand is a
   set of placeholders, so a card-choice decision's candidate list is a
   superset of what the expert actually held. Those rows carry
   `hand_known: false` and are excluded from the agreement denominator.

`split` assigns whole games (deterministically, `--holdout-every`) so a fitter
can hold out complete games rather than individual positions.
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
from struggler.engine.core import HIDDEN_CARD, _SECRET_HAND_KINDS  # noqa: E402


def _hand_unknown(engine, dec) -> bool:
    """True when this decision is drawn from a hand the engine does not truly
    know (replay mode hides both hands behind placeholders), so the candidate
    list is a superset of what the expert actually held and agreement on it
    would measure the wrong problem."""
    if HIDDEN_CARD in engine.hands.get(dec.actor.value, ()):
        if dec.kind in _SECRET_HAND_KINDS or dec.context.get("_private"):
            return True
    return any(HIDDEN_CARD in action.payload.values() for action in dec.options)


def extract(game: dict, greedy: GreedyPlayer, *, split: str = "train") -> tuple[list[dict], dict]:
    r = Replay(game)
    rows: list[dict] = []
    stopped = "records_end"
    first_divergence: int | None = None
    # Verified prefix only: stop at the FIRST divergence (fallback or
    # board/VP mismatch). Decisions after it describe positions the expert
    # never faced.
    while not r.engine.is_terminal and not r.exhausted and r.ri < len(r.actions):
        dec = r.engine.pending_decision
        if dec is None:
            break
        before = len(r.errors)
        action = r.answer(dec)
        if len(r.errors) != before:
            stopped = "first_divergence"
            first_divergence = r.ri
            break
        if dec.actor in (Side.US, Side.USSR):
            obs = r.engine.observe(dec.actor)
            g_action = greedy.choose_action(obs, ())
            g_idx = dec.options.index(g_action) if g_action in dec.options else None
            chosen = next(
                (i for i, a in enumerate(dec.options) if a.payload == action.payload),
                None,
            )
            hand_known = not _hand_unknown(r.engine, dec)
            rows.append({
                "game": game["source"],
                "split": split,
                "turn": r.actions[r.ri]["turn"] if r.ri < len(r.actions) else None,
                "kind": dec.kind.value,
                "actor": dec.actor.value,
                "n_options": len(dec.options),
                "chosen": chosen,
                "greedy": g_idx,
                "match": chosen is not None and chosen == g_idx,
                "hand_known": hand_known,
            })
        r.engine.step(action)
        r._consume_assertions()
    eligible = [row for row in rows if row["hand_known"] and row["chosen"] is not None
                and row["greedy"] is not None]
    agree = sum(row["match"] for row in eligible)
    return rows, {
        "game": game["source"],
        "split": split,
        "records_total": len(game["actions"]),
        "records_consumed": min(r.ri + 1, len(game["actions"])),
        "stopped": stopped,
        "first_divergence_record": first_divergence,
        "decisions": len(rows),
        "eligible_decisions": len(eligible),
        "hand_unknown_decisions": sum(1 for row in rows if not row["hand_known"]),
        "greedy_agreement": round(agree / len(eligible), 3) if eligible else None,
        "fallbacks": r.fallbacks,
        "board_errors": len(r.errors),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", default="data/expert_decisions.jsonl")
    ap.add_argument("--holdout-every", type=int, default=5,
                    help="every Nth game (in file order) is the eval split; 0 disables")
    args = ap.parse_args()

    files: list[str] = []
    for p in args.paths:
        files.extend(sorted(glob.glob(p)))
    greedy = GreedyPlayer()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    per_split: dict[str, list[int]] = {"train": [0, 0], "eval": [0, 0]}  # [eligible, matches]
    with out.open("w") as fh:
        for i, f in enumerate(files):
            split = "eval" if (args.holdout_every and (i + 1) % args.holdout_every == 0) else "train"
            game = json.loads(Path(f).read_text())
            rows, summary = extract(game, greedy, split=split)
            for row in rows:
                fh.write(json.dumps(row) + "\n")
            for row in rows:
                if row["hand_known"] and row["chosen"] is not None and row["greedy"] is not None:
                    per_split[split][0] += 1
                    per_split[split][1] += int(row["match"])
            print(json.dumps(summary))
    print()
    for split, (n, matches) in per_split.items():
        rate = f"{matches / n:.1%}" if n else "n/a"
        print(f"{split:5s}: {matches}/{n} hand-known eligible decisions = {rate}")


if __name__ == "__main__":
    main()
