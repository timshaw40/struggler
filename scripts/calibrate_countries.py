"""Calibrate country marker positions for the web UI from the map PDF.

Extracts each country label's position from the user-supplied map PDF
(Korman alternate board, the one `render_assets.py` renders), finds the
country box that contains it, and writes ui/countries.json — a committed,
factual table of marker anchors as fractions of the board image. Also
renders ui/assets/calibration_overlay.png (gitignored) so the result can
be checked visually before trusting it.

Requires: pip install -e ".[ui]" (PyMuPDF), and ui/assets/board.png from
render_assets.py.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ui" / "assets"
COUNTRIES_JSON = ROOT / "ui" / "countries.json"

# Map label (as printed, typos included) -> engine country id. The map
# spells "Equador"/"Guatamala" its own way and splits long names across
# lines; matching below normalizes case, spacing, and hyphenation.
LABEL_TO_ID: dict[str, str] = {
    "Canada": "Canada",
    "Norway": "Norway",
    "Sweden": "Sweden",
    "Finland": "Finland",
    "United Kingdom": "UK",
    "Benelux": "Benelux",
    "Denmark": "Denmark",
    "France": "France",
    "W Germany": "West_Germany",
    "E Germany": "East_Germany",
    "Czechoslovakia": "Czechoslovakia",
    "Austria": "Austria",
    "Hungary": "Hungary",
    "Yugoslavia": "Yugoslavia",
    "Romania": "Romania",
    "Poland": "Poland",
    "Italy": "Italy",
    "Greece": "Greece",
    "Bulgaria": "Bulgaria",
    "Turkey": "Turkey",
    "Spain & Portugal": "Spain_Portugal",
    "Mexico": "Mexico",
    "Guatamala": "Guatemala",
    "El Salvador": "El_Salvador",
    "Cuba": "Cuba",
    "Honduras": "Honduras",
    "Nicaragua": "Nicaragua",
    "Haiti": "Haiti",
    "Costa Rica": "Costa_Rica",
    "Panama": "Panama",
    "Dominican Republic": "Dominican_Republic",
    "Colombia": "Colombia",
    "Venezuela": "Venezuela",
    "Equador": "Ecuador",
    "Peru": "Peru",
    "Bolivia": "Bolivia",
    "Brazil": "Brazil",
    "Paraguay": "Paraguay",
    "Uruguay": "Uruguay",
    "Chile": "Chile",
    "Argentina": "Argentina",
    "Syria": "Syria",
    "Lebanon": "Lebanon",
    "Israel": "Israel",
    "Jordan": "Jordan",
    "Iraq": "Iraq",
    "Saudi Arabia": "Saudi_Arabia",
    "Gulf States": "Gulf_States",
    "Libya": "Libya",
    "Egypt": "Egypt",
    "Iran": "Iran",
    "Afghanistan": "Afghanistan",
    "Pakistan": "Pakistan",
    "India": "India",
    "North Korea": "North_Korea",
    "South Korea": "South_Korea",
    "Taiwan": "Taiwan",
    "Japan": "Japan",
    "Burma": "Burma",
    "Laos & Cambodia": "Laos_Cambodia",
    "Thailand": "Thailand",
    "Vietnam": "Vietnam",
    "Malaysia": "Malaysia",
    "Indonesia": "Indonesia",
    "Philippines": "Philippines",
    "Australia": "Australia",
    "Morocco": "Morocco",
    "Algeria": "Algeria",
    "Tunisia": "Tunisia",
    "Sudan": "Sudan",
    "West African States": "West_African_States",
    "Saharan States": "Saharan_States",
    "Ethiopia": "Ethiopia",
    "Somalia": "Somalia",
    "Kenya": "Kenya",
    "Ivory Coast": "Ivory_Coast",
    "Nigeria": "Nigeria",
    "Cameroon": "Cameroon",
    "Zaire": "Zaire",
    "Zimbabwe": "Zimbabwe",
    "Botswana": "Botswana",
    "Angola": "Angola",
    "South Africa": "South_Africa",
    "Southeast African States": "SE_African_States",
    "Chinese Civil War": "Chinese_Civil_War",
}

_NORM = re.compile(r"[^a-z]")


def _norm(text: str) -> str:
    return _NORM.sub("", text.casefold())


def _label_spans(page: pymupdf.Page) -> list[tuple[str, pymupdf.Rect]]:
    """Every text span in reading order with its bbox."""
    spans: list[tuple[str, pymupdf.Rect]] = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                spans.append((span["text"], pymupdf.Rect(span["bbox"])))
    return spans


def _country_boxes(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """White-filled rects (country boxes), smallest-area first."""
    boxes = [
        d["rect"]
        for d in page.get_drawings()
        if d.get("fill") is not None
        and all(v >= 0.99 for v in d["fill"])
        and d["rect"].width < 130
        and d["rect"].height < 90
    ]
    boxes.sort(key=lambda r: r.width * r.height)
    return boxes


def calibrate(map_pdf: Path) -> dict[str, dict[str, float]]:
    page = pymupdf.open(map_pdf)[0]
    spans = _label_spans(page)
    boxes = _country_boxes(page)
    wanted = {key: _norm(key) for key in LABEL_TO_ID}

    def anchor_for(rect: pymupdf.Rect) -> tuple[float, float] | None:
        center = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
        for box in boxes:  # sorted smallest-first: tightest containing box
            if box.contains(rect):
                return (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
        print(f"  no box contains label at {center}", file=sys.stderr)
        return None

    found: dict[str, dict[str, float]] = {}
    width, height = page.rect.width, page.rect.height

    def record(label: str, rect: pymupdf.Rect) -> None:
        anchor = anchor_for(rect)
        if anchor is None:
            return
        cid = LABEL_TO_ID[label]
        found[cid] = {"x": round(anchor[0] / width, 4), "y": round(anchor[1] / height, 4)}

    # Pass 1: one span == one label.
    unmatched = dict(wanted)
    single = {_norm(k): k for k in unmatched}
    for text, rect in spans:
        label = single.get(_norm(text))
        if label is not None and label in unmatched:
            record(label, rect)
            del unmatched[label]

    # Pass 2: labels split across consecutive spans ("Laos & Cambo-" + "dia").
    for i in range(len(spans)):
        for j in range(i + 1, min(i + 4, len(spans) + 1)):
            concat = _norm("".join(t for t, _ in spans[i:j]))
            for label, pattern in list(unmatched.items()):
                if concat == pattern:
                    union = pymupdf.Rect(spans[i][1])
                    for _, r in spans[i:j]:
                        union |= r
                    record(label, union)
                    del unmatched[label]

    missing = sorted(set(LABEL_TO_ID.values()) - set(found))
    if missing:
        print(f"UNMATCHED ({len(missing)}): {missing}", file=sys.stderr)
        raise SystemExit(1)
    return found


def overlay(found: dict[str, dict[str, float]]) -> None:
    """Draw all anchors onto a copy of board.png for a visual check."""
    board = pymupdf.Pixmap(OUT / "board.png")
    doc = pymupdf.open()
    page = doc.new_page(width=board.width, height=board.height)
    page.insert_image(page.rect, pixmap=board)
    for cid, pos in sorted(found.items()):
        point = pymupdf.Point(pos["x"] * board.width, pos["y"] * board.height)
        page.draw_circle(point, 16, color=(1, 0, 0), width=3)
        page.insert_text(point + pymupdf.Point(-26, 40), cid[:10], fontsize=13, color=(1, 0, 0))
    page.get_pixmap().save(OUT / "calibration_overlay.png")
    print(f"overlay: {OUT / 'calibration_overlay.png'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map-pdf", default="Twilight Struggle Map v1.1.pdf")
    parser.add_argument("--no-overlay", action="store_true")
    args = parser.parse_args()
    found = calibrate(Path(args.map_pdf))
    COUNTRIES_JSON.write_text(json.dumps(found, indent=1, sort_keys=True) + "\n")
    print(f"countries: {len(found)} anchors -> {COUNTRIES_JSON}")
    if not args.no_overlay:
        overlay(found)


if __name__ == "__main__":
    import sys

    main()
