"""Extract scoring-moment pairs from the parsed games (Phase-4 item 8).

At every scoring-card record the replay reaches, snapshot the region's
board (per-country influence, per-side tiers) plus the engine's net
region VP and the log's own net VP (the record's vp_after minus the
previous VP assertion). Purposes: validate the engine's scoring formula
against real games, and document where log and engine diverge.

Usage:
    uv run python scripts/extract_scoring.py 'parsed/*.json'
Writes data/scoring_moments.jsonl (one JSON object per moment).
"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from replay_game import Replay  # noqa: E402

from struggler.engine import Side  # noqa: E402
from struggler.engine.board import Board  # noqa: E402
from struggler.engine.types import Region  # noqa: E402

CARDS = {
    cid: c for cid, c in json.loads(
        (ROOT / "src/struggler/data/cards.json").read_text())["cards"].items()
    if c.get("scoring")
}
CARD_REGION = {
    "Europe_Scoring": "EUROPE", "Asia_Scoring": "ASIA",
    "Middle_East_Scoring": "MIDDLE_EAST", "Africa_Scoring": "AFRICA",
    "Central_America_Scoring": "CENTRAL_AMERICA",
    "South_America_Scoring": "SOUTH_AMERICA",
    "Southeast_Asia_Scoring": "SOUTHEAST_ASIA",
}


def region_snapshot(board: Board, region_name: str, engine=None) -> dict:
    if region_name == "SOUTHEAST_ASIA":  # a subregion, scored by a special case
        ids = tuple(
            cid for cid, c in board.countries.items()
            if any(s.name == "SOUTHEAST_ASIA" for s in c.subregions))
        return {
            "countries": {cid: [board.influence[cid]["US"], board.influence[cid]["USSR"]]
                          for cid in ids},
            "engine_net_vp": engine._score_southeast_asia() if engine else None,
        }
    region = Region[region_name]
    ids = board.countries_in(region)
    return {
        "countries": {cid: [board.influence[cid]["US"], board.influence[cid]["USSR"]]
                      for cid in ids},
        "us_tier": board.region_tier(Side.US, region).name,
        "ussr_tier": board.region_tier(Side.USSR, region).name,
        "engine_net_vp": board.score_region(region),
    }


def main(glob: str) -> None:
    out = []
    for path in sorted(Path().glob(glob)):
        game = json.loads(path.read_text())
        replay = Replay(copy.deepcopy(game))
        prev_vp = 0
        while not replay.engine.is_terminal:
            dec = replay.engine.pending_decision
            if dec is None:
                break
            ri_before = replay.ri
            action = replay.answer(dec)
            replay.engine.step(action)
            replay._consume_assertions()
            if replay.ri > ri_before and ri_before < len(replay.actions):
                rec = replay.actions[ri_before]
                card = rec.get("card")
                if card in CARDS:
                    region = CARD_REGION[card]
                    snap = region_snapshot(replay.engine.board, region, replay.engine)
                    log_net = rec["vp_after"] - prev_vp if rec.get("vp_after") is not None else None
                    if rec.get("vp_after") is not None:
                        prev_vp = rec["vp_after"]
                    out.append({
                        "source": game["source"],
                        "turn": rec["turn"],
                        "card": card,
                        "region": region,
                        "engine_vp_total": replay.engine.vp,
                        "log_vp_total": rec.get("vp_after"),
                        "log_region_net_vp": log_net,
                        "board": snap,
                        "consumed_all": replay.ri >= len(replay.actions) - 1,
                    })
        print(
            f"{game['source']}: {sum(1 for m in out if m['source'] == game['source'])} "
            f"scoring moments, replay records "
            f"{min(replay.ri + 1, len(replay.actions))}/{len(replay.actions)}",
            file=sys.stderr,
        )
    Path("data/scoring_moments.jsonl").write_text(
        "\n".join(json.dumps(m) for m in out) + "\n")
    agree = sum(
        1 for m in out
        if m["log_vp_total"] is not None and m["engine_vp_total"] == m["log_vp_total"])
    have_vp = sum(1 for m in out if m["log_vp_total"] is not None)
    print(f"total: {len(out)} moments, VP agreement {agree}/{have_vp}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "parsed/*.json")
