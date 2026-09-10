#!/usr/bin/env python3
"""Install third_party/gmt-vassal graphics into gitignored ui/assets/ for the web UI.

Maps VASSAL TNRnTS-NN.svg card numbers to engine card ids via cards.json /
load_cards(), writes board.png + cards/{id}.svg + cards.json + countries.json
(country-box centers detected off the board, plus stability for the
control computation; the browser prefers this over the schematic
ui/countries.json when present). Also copies the four VASSAL influence
faces (controlled/uncontrolled per side) into markers/ for the map pips.

The TNRnTS faces leave the top banner empty (the VASSAL module overlays the
ops value / side stripe at runtime), so the install injects that banner —
side color + name, plus the ops value except on scoring cards — into each
card SVG as it is written.

Requires: Pillow (for board JPG→PNG).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "third_party" / "gmt-vassal"
OUT = ROOT / "ui" / "assets"
VMOD_URL = "https://obj.vassalengine.org/images/3/31/Twilight-Struggle-3.2.vmod"
VMOD_SHA256 = "b5b0f4cfc0f37c6bbbb26d22aaa69acab251bb3fd3b07aeb18679b901e68a24e"

# Detection anchors: one point inside each country's placement box on TS
# Map-11.jpg (5100x3300). find_box() derives the actual box rect and center
# from the map around these; they are not the marker positions themselves.
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



def load_number_to_meta() -> dict[int, dict]:
    """card number -> {id, side, ops, scoring}, for banner injection."""
    try:
        from struggler.engine.cards import load_cards

        return {
            card.number: {
                "id": cid,
                "side": card.side.value,
                "ops": card.ops,
                "scoring": card.scoring,
            }
            for cid, card in load_cards().items()
        }
    except Exception:
        raw = json.loads((ROOT / "src" / "struggler" / "data" / "cards.json").read_text())
        cards = raw.get("cards", raw) if isinstance(raw, dict) else {}
        out: dict[int, dict] = {}
        for cid, card in cards.items():
            if cid == "_schema" or not isinstance(card, dict):
                continue
            if "number" in card:
                out[int(card["number"])] = {
                    "id": cid,
                    "side": card["side"],
                    "ops": card["ops"],
                    "scoring": card["scoring"],
                }
        return out


# Twilight Struggle banners: US blue, USSR red, neutral purple. Photos on the
# TNRnTS faces start at y>=88, so a 44px header covers nothing.
BANNERS = {
    "US": ("UNITED STATES", "#2e5fa3"),
    "USSR": ("SOVIET UNION", "#b3282d"),
    "NEUTRAL": ("NEUTRAL", "#825aa5"),
}


def with_banner(svg: str, meta: dict) -> str:
    label, color = ("SCORING", "#825aa5") if meta["scoring"] else BANNERS[meta["side"]]
    ops = "" if meta["scoring"] else (
        f'<text x="26" y="33" style="font-size:30px;font-weight:bold;'
        f'font-family:Georgia;fill:#ffffff">{meta["ops"]}</text>'
    )
    banner = (
        f'<g id="banner"><rect width="388" height="44" style="fill:{color}"/>'
        f'<text x="194" y="31" text-anchor="middle" style="font-size:20px;'
        f'font-weight:bold;font-family:Georgia;fill:#ffffff">{label}</text>{ops}</g>'
    )
    return svg.replace("</svg>", banner + "</svg>")


def ensure_board(board_jpg: Path, out_board: Path) -> None:
    try:
        from PIL import Image
    except ImportError as e:
        raise SystemExit("Pillow required for board convert: pip install Pillow") from e
    img = Image.open(board_jpg)
    img.save(out_board, format="PNG")
    print(f"board: {img.size[0]}x{img.size[1]} -> {out_board}")


def find_box(img, cx: int, cy: int) -> tuple[tuple[int, int, int, int], tuple[int, int], int, bool] | None:
    """Locate a country box around an anchor point.

    The map draws every country box with a solid black top border spanning
    the full box width; side borders can't be used (flags/badges painted
    over them break the vertical runs). Scan upward from the anchor for the
    closest row with a long dark run containing it (map connection lines and
    labels are diagonal/short, so they never qualify). Box widths vary per
    country (~180-220px). Returns (strip rect, body center, box height,
    bottom border found) — the header-strip rect for the tooltip crop,
    the influence-columns center as the marker anchor, and the full box
    height so markers scale to the box.
    """
    win_w, win_h = 600, 400
    x0, y0 = cx - win_w // 2, cy - win_h // 2
    px = img.convert("RGB").crop((x0, y0, x0 + win_w, y0 + win_h)).load()
    ax, ay = cx - x0, cy - y0

    def dark(x: int, y: int) -> bool:
        r, g, b = px[x, y]
        # Near-black and unsaturated: the 2px box border is JPEG-softened
        # (~(90,92,68) at worst), while dark-red map terrain is saturated.
        return r < 110 and g < 110 and b < 110 and max(r, g, b) - min(r, g, b) < 70

    def row_runs(y: int) -> list[tuple[int, int]]:
        """Dark runs >= 150 px on one row; gaps <= 12 px (JPEG noise) bridge;
        runs cap at 280 px."""
        runs, start, gap = [], None, 0
        for x in range(win_w):
            if dark(x, y):
                if start is None:
                    start = x
                gap = 0
            elif start is not None:
                gap += 1
                if gap > 12:
                    end = min(x - gap, start + 280)
                    if end - start >= 150:
                        runs.append((start, end))
                    start, gap = None, 0
        if start is not None:
            end = min(win_w - gap, start + 280)
            if end - start >= 150:
                runs.append((start, end))
        return runs

    def spanning_run(y: int) -> tuple[int, int] | None:
        """The long dark run containing the anchor on row y."""
        hits = [r for r in row_runs(y) if r[0] <= ax < r[1]]
        return hits[-1] if hits else None

    hit = next(((y, r) for y in range(ay - 1, -1, -1)
                if (r := spanning_run(y)) is not None), None)
    if hit is None:
        return None
    y, run = hit
    bottom = None
    sep = next((y + k for k in range(1, 37) if spanning_run(y + k)), None)
    if sep is not None and sep - y <= 36:
        # Anchor sat inside the strip (or the separator lies just below the
        # first hit): that line is the strip | body separator.
        bottom = sep
    else:
        # First hit is the strip's bottom border; the box top is the highest
        # paired spanning line up to ~44px above (the name label's own top
        # border pairs too, a few px lower, hence descending order).
        for offset in range(44, 23, -1):
            if y - offset >= 0 and (up := spanning_run(y - offset)) is not None:
                run, y, bottom = up, y - offset, y
                break
    def strip_like(x: int, y: int) -> bool:
        """Cream label bg, purple label bg, red or yellow stability badge —
        i.e. colors that only occur inside the header strip."""
        r, g, b = px[x, y]
        return ((r > 235 and g > 235 and b > 170)
                or (80 < r < 170 and 50 < g < 120 and 140 < b < 210)
                or (r > 170 and g < 70 and b < 70)
                or (r > 220 and g > 180 and b < 90))

    # Left border: scan leftward from the run start; the flag often hides
    # the border on the top row, but the border column is dark nearly all
    # the way down the body (terrain shading and map lines don't hold a
    # column for the full body height). Reach capped ~52px so tight
    # neighbors aren't mistaken for the border.
    left = None
    for x in range(run[0] - 1, max(run[0] - 52, -1), -1):
        if sum(dark(x, y + 36 + 4 * i) for i in range(23)) >= 20:
            left = x
            break
    # Right border: trim the run end to the badge's right border — dark on
    # the top row, strip-colored just inside, map-colored just outside.
    right = run[1] + 2
    for x in range(run[1], max(run[1] - 80, run[0]), -1):
        if (dark(x, y) and strip_like(x - 4, y + 15)
                and not strip_like(x + 4, y + 15)):
            right = x + 2
            break
    if left is None:
        left = run[0]
    height = bottom - y if bottom is not None else 34  # strip design height
    strip_bottom = y + min(max(height, 30), 46)
    # Box bottom border: the next spanning line below the strip separator
    # with roughly the strip's own left edge and length. Box bodies run
    # ~98px, Romania's 124 — the scan reaches +140. The shape match keeps
    # a neighbor row's top border (different x-extent) from qualifying,
    # and the true border comes first anyway. The border's left end must
    # land near the strip's left edge by either estimate — the raw top
    # run (which can bridge into a neighbor) or the column-detected edge
    # (which can stop wide on map clutter) — and match the raw run
    # length (not the badge-trimmed right). The midpoint of top and
    # bottom borders is the box center, the marker anchor. Without it
    # the strip bottom stands in — ~33px above center, the old
    # riding-high offset.
    box_bottom = None
    if bottom is not None:
        box_bottom = next((by for by in range(bottom + 8, bottom + 141)
                           if (br := spanning_run(by)) is not None
                           and br[0] <= ax < br[1]
                           and min(abs(br[0] - run[0]), abs(br[0] - left)) <= 40
                           and abs((br[1] - br[0]) - (run[1] - run[0])) <= 60), None)
    # The marker anchor is the BODY center (strip bottom to box bottom):
    # VASSAL counters sit in the influence columns below the name strip,
    # and centering pips on the whole box lets tall pips swallow the
    # strip and badge.
    if box_bottom is not None:
        box_h = box_bottom - y
        center_y = (strip_bottom + box_bottom) // 2
    else:
        box_h = 2 * (strip_bottom - y)
        center_y = strip_bottom + (strip_bottom - y) // 2
    return ((x0 + left, y0 + y, x0 + right, y0 + strip_bottom),
            (x0 + (left + right) // 2, y0 + center_y), box_h,
            box_bottom is not None)


# Boxes that defeat the detector. Each entry: header-strip rect, body
# center, box height. Benelux: W. Germany's row-aligned box 25px away
# bridges into its top-border run. Chinese_Civil_War: a full red event
# panel, cropped whole so the title stays readable in the tooltip.
# Panama: Costa Rica's right border bridges into its top-border run
# (248px wide vs ~200), pulling the center 15px left onto Costa Rica.
HAND_TWEAK = {
    "Benelux": ((1856, 693, 2059, 726), (1957, 775), 131),
    "Chinese_Civil_War": ((4084, 1134, 4339, 1352), (4211, 1243), 218),
    "Panama": ((733, 1795, 933, 1827), (833, 1876), 130),
}


def install_boxes(board_png: Path) -> dict[str, tuple[int, int, int, int]]:
    """Detect every country box once; write the header-strip crops for the
    hover tooltip and return each (left, top, width, height) of the box."""
    from PIL import Image

    img = Image.open(board_png)
    out_dir = OUT / "headers"
    out_dir.mkdir(parents=True, exist_ok=True)
    found_boxes: dict[str, tuple[int, int, int, int]] = {}
    missing, fallback = [], []
    for cid, (cx, cy) in VASSAL_COUNTRIES.items():
        if cid in HAND_TWEAK:
            rect, center, h = HAND_TWEAK[cid]
        else:
            found = find_box(img, cx, cy)
            if found is None:
                missing.append(cid)
                continue
            (rect, center, h, ok) = found
            if not ok:
                fallback.append(cid)
        img.crop(rect).save(out_dir / f"{cid}.png")
        found_boxes[cid] = (rect[0], rect[1], rect[2] - rect[0], h)
    if missing:
        print(f"warning: no box found for {missing}", file=sys.stderr)
    if fallback:
        print(f"note: tall bodies: {fallback}")
    print(f"headers: {len(found_boxes)} -> {out_dir}")
    return found_boxes


# VASSAL influence faces, two-sided per Tim's ask: the white face while a
# side only has influence, the colored face once it controls the country.
# The spare *Control/NoInfluence faces stay in third_party (our digits sit
# on the influence faces VASSAL-style, and 0 shows on the white face).
MARKER_FACES = {
    "us_uncontrolled": "AmericanInfluenceUncontrolled.svg",
    "us_controlled": "AmericanInfluenceControlled.svg",
    "ussr_uncontrolled": "SovietInfluenceUncontrolled.svg",
    "ussr_controlled": "SovietInfluenceControlled.svg",
}


def install_markers() -> None:
    """Copy the influence faces into gitignored ui/assets/markers/."""
    import shutil

    src = SRC / "markers"
    out = OUT / "markers"
    out.mkdir(parents=True, exist_ok=True)
    for name, face in MARKER_FACES.items():
        shutil.copyfile(src / face, out / f"{name}.svg")
    print(f"markers: {len(MARKER_FACES)} -> {out}")


def load_stability() -> dict[str, int]:
    """country id -> stability number, for the browser's control check."""
    try:
        from struggler.engine.board import Board

        return {cid: info.stability for cid, info in Board().countries.items()}
    except Exception:
        raw = json.loads((ROOT / "src" / "struggler" / "data" / "countries.json").read_text())
        return {
            cid: entry["stability"] for cid, entry in raw["countries"].items()
        }


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

    number_to_meta = load_number_to_meta()
    if len(number_to_meta) < 110:
        print(f"warning: only {len(number_to_meta)} card ids loaded", file=sys.stderr)

    out_cards = OUT / "cards"
    out_cards.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {}
    for n in range(1, 111):
        src = cards_dir / f"TNRnTS-{n:02d}.svg"
        if not src.is_file():
            raise SystemExit(f"missing {src}")
        meta = number_to_meta.get(n)
        if meta is None:
            raise SystemExit(f"no engine id for card number {n}")
        name = f"{meta['id']}.svg"
        (out_cards / name).write_text(with_banner(src.read_text(), meta))
        manifest[meta["id"]] = name
    (OUT / "cards.json").write_text(json.dumps(manifest, indent=1) + "\n")
    ensure_board(board_jpg, OUT / "board.png")
    boxes = install_boxes(OUT / "board.png")
    install_markers()
    stability = load_stability()
    countries = {
        cid: {"x": round(left / BOARD_W, 5), "y": round(top / BOARD_H, 5),
              "s": stability[cid], "h": h, "w": w}
        for cid, (left, top, w, h) in boxes.items()
    }
    (OUT / "countries.json").write_text(json.dumps(countries, indent=1, sort_keys=True) + "\n")
    print(f"countries: {len(countries)} box rects -> {OUT / 'countries.json'}")
    print(f"cards: {len(manifest)} -> {out_cards}")


if __name__ == "__main__":
    main()
