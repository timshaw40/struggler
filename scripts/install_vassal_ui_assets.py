#!/usr/bin/env python3
"""Install third_party/gmt-vassal graphics into gitignored ui/assets/ for the web UI.

Maps VASSAL TNRnTS-NN.svg card numbers to engine card ids via cards.json /
load_cards(), writes board.png + cards/{id}.svg + cards.json + countries.json
(VASSAL-calibrated marker positions; the browser prefers this over the
schematic ui/countries.json when present).

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

# Marker anchor points, measured by hand on TS Map-11.jpg (5100x3300) as the
# center of each country's placement box (name strip + marker area). The UI
# positions influence/control markers at these fractions of the board image.
BOARD_W, BOARD_H = 5100, 3300
VASSAL_COUNTRIES: dict[str, tuple[int, int]] = {
    # North America
    "Canada": (949, 739),
    "Mexico": (278, 1338),
    "Guatemala": (467, 1507),
    "Cuba": (852, 1456),
    "Haiti": (1075, 1598),
    "Dominican_Republic": (1290, 1600),
    # Europe
    "Norway": (2026, 311),
    "Denmark": (2074, 467),
    "Sweden": (2323, 450),
    "UK": (1790, 608),
    "West_Germany": (2180, 762),
    "East_Germany": (2258, 617),
    "Benelux": (1958, 765),
    "France": (1918, 942),
    "Spain_Portugal": (1757, 1156),
    "Italy": (2213, 1081),
    "Austria": (2267, 924),
    "Hungary": (2476, 924),
    "Czechoslovakia": (2435, 761),
    "Poland": (2529, 617),
    "Yugoslavia": (2435, 1079),
    "Romania": (2713, 930),
    "Bulgaria": (2670, 1081),
    "Greece": (2486, 1240),
    "Turkey": (2863, 1100),
    "Finland": (2622, 312),
    # Americas south
    "El_Salvador": (393, 1715),
    "Honduras": (603, 1715),
    "Nicaragua": (813, 1715),
    "Costa_Rica": (597, 1870),
    "Panama": (833, 1865),
    "Venezuela": (1105, 1885),
    "Colombia": (977, 2041),
    "Ecuador": (753, 2111),
    "Peru": (868, 2275),
    "Bolivia": (1105, 2360),
    "Chile": (983, 2601),
    "Paraguay": (1233, 2530),
    "Argentina": (1063, 2913),
    "Brazil": (1492, 2273),
    "Uruguay": (1335, 2728),
    # Africa
    "Morocco": (1819, 1440),
    "Algeria": (2030, 1370),
    "Tunisia": (2259, 1361),
    "Libya": (2389, 1527),
    "Sudan": (2708, 1698),
    "West_African_States": (1800, 1636),
    "Saharan_States": (2131, 1690),
    "Ethiopia": (2797, 1893),
    "Somalia": (3063, 1958),
    "Kenya": (2792, 2084),
    "Ivory_Coast": (1933, 1922),
    "Nigeria": (2213, 1898),
    "Cameroon": (2313, 2072),
    "Zaire": (2562, 2120),
    "Zimbabwe": (2605, 2390),
    "Botswana": (2570, 2540),
    "Angola": (2373, 2278),
    "South_Africa": (2480, 2714),
    "SE_African_States": (2848, 2297),
    # Middle East / South Asia
    "Syria": (2938, 1189),
    "Lebanon": (2732, 1248),
    "Israel": (2723, 1393),
    "Jordan": (2863, 1544),
    "Iraq": (2945, 1388),
    "Saudi_Arabia": (3063, 1687),
    "Gulf_States": (3083, 1535),
    "Egypt": (2663, 1541),
    "Iran": (3163, 1383),
    "Afghanistan": (3441, 1298),
    "Pakistan": (3393, 1498),
    "India": (3691, 1628),
    # Asia
    "North_Korea": (4572, 1088),
    "South_Korea": (4620, 1235),
    "Taiwan": (4542, 1560),
    "Japan": (4803, 1388),
    "Chinese_Civil_War": (4208, 1240),
    "Burma": (3955, 1625),
    "Laos_Cambodia": (4181, 1638),
    "Thailand": (4080, 1796),
    "Vietnam": (4302, 1802),
    "Malaysia": (4183, 2034),
    "Indonesia": (4547, 2238),
    "Philippines": (4627, 1786),
    "Australia": (4538, 2534),
}


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
    countries = {
        cid: {"x": round(x / BOARD_W, 4), "y": round(y / BOARD_H, 4)}
        for cid, (x, y) in VASSAL_COUNTRIES.items()
    }
    (OUT / "countries.json").write_text(json.dumps(countries, indent=1, sort_keys=True) + "\n")
    print(f"cards: {len(manifest)} -> {out_cards}")


if __name__ == "__main__":
    main()
