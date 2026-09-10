#!/usr/bin/env python3
"""Install third_party/gmt-vassal graphics into gitignored ui/assets/ for the web UI.

Maps VASSAL TNRnTS-NN.svg card numbers to engine card ids via cards.json /
load_cards(), writes board.png + cards/{id}.svg + cards.json.

Requires: Pillow (for board JPG→PNG). Card SVGs are copied as-is.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "third_party" / "gmt-vassal"
OUT = ROOT / "ui" / "assets"
VMOD_URL = "https://obj.vassalengine.org/images/3/31/Twilight-Struggle-3.2.vmod"
VMOD_SHA256 = "b5b0f4cfc0f37c6bbbb26d22aaa69acab251bb3fd3b07aeb18679b901e68a24e"


def assemble_board_from_b64_parts(board_jpg: Path) -> bool:
    """If board JPG missing but .b64.part* + manifest exist, reassemble and return True."""
    import base64
    board_dir = board_jpg.parent
    manifest = board_dir / (board_jpg.name + ".b64.manifest")
    if board_jpg.is_file() or not manifest.is_file():
        return False
    meta = json.loads(manifest.read_text())
    parts = sorted(board_dir.glob(board_jpg.name + ".b64.part*"))
    if not parts:
        return False
    data = b"".join(base64.b64decode(p.read_text().encode("ascii")) for p in parts)
    expected = meta.get("sha256") or meta.get("sha256_hex")
    if expected:
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected:
            raise SystemExit(f"board b64 reassembly sha256 mismatch: {digest}")
    board_jpg.write_bytes(data)
    print(f"reassembled board from {len(parts)} b64 parts -> {board_jpg}")
    return True



def load_number_to_id() -> dict[int, str]:
    try:
        from struggler.engine.cards import load_cards

        return {card.number: cid for cid, card in load_cards().items()}
    except Exception:
        raw = json.loads((ROOT / "src" / "struggler" / "data" / "cards.json").read_text())
        cards = raw.get("cards", raw) if isinstance(raw, dict) else {}
        out: dict[int, str] = {}
        for cid, card in cards.items():
            if cid == "_schema" or not isinstance(card, dict):
                continue
            if "number" in card:
                out[int(card["number"])] = cid
        return out


def ensure_board(board_jpg: Path, out_board: Path) -> None:
    try:
        from PIL import Image
    except ImportError as e:
        raise SystemExit("Pillow required for board convert: pip install Pillow") from e
    img = Image.open(board_jpg)
    img.save(out_board, format="PNG")
    print(f"board: {img.size[0]}x{img.size[1]} -> {out_board}")


def fetch_board_if_missing(board_jpg: Path) -> None:
    if board_jpg.is_file():
        return
    board_jpg.parent.mkdir(parents=True, exist_ok=True)
    cache = ROOT / ".cache" / "Twilight-Struggle-3.2.vmod"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.is_file():
        print(f"Downloading {VMOD_URL} ...")
        urllib.request.urlretrieve(VMOD_URL, cache)
    import hashlib

    digest = hashlib.sha256(cache.read_bytes()).hexdigest()
    if digest != VMOD_SHA256:
        raise SystemExit(f"vmod sha256 mismatch: {digest}")
    with zipfile.ZipFile(cache) as zf:
        data = zf.read("images/TS Map-11.jpg")
    board_jpg.write_bytes(data)
    print(f"extracted board -> {board_jpg}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-board", action="store_true", help="Download board JPG from official vmod if missing")
    args = parser.parse_args()
    cards_dir = SRC / "cards"
    if not cards_dir.is_dir():
        raise SystemExit(f"missing {cards_dir}")
    board_jpg = SRC / "board" / "TS Map-11.jpg"
    if not board_jpg.is_file():
        assemble_board_from_b64_parts(board_jpg)
    if args.fetch_board or not board_jpg.is_file():
        fetch_board_if_missing(board_jpg)

    number_to_id = load_number_to_id()
    if len(number_to_id) < 110:
        print(f"warning: only {len(number_to_id)} card ids loaded", file=sys.stderr)

    out_cards = OUT / "cards"
    out_cards.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {}
    for n in range(1, 111):
        src = cards_dir / f"TNRnTS-{n:02d}.svg"
        if not src.is_file():
            raise SystemExit(f"missing {src}")
        cid = number_to_id.get(n)
        if cid is None:
            raise SystemExit(f"no engine id for card number {n}")
        name = f"{cid}.svg"
        shutil.copyfile(src, out_cards / name)
        manifest[cid] = name
    (OUT / "cards.json").write_text(json.dumps(manifest, indent=1) + "\n")
    ensure_board(board_jpg, OUT / "board.png")
    print(f"cards: {len(manifest)} -> {out_cards}")


if __name__ == "__main__":
    main()
