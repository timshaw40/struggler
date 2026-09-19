"""Turn the user-supplied PnP PDFs into ui/assets images (board + card faces).

Reads whatever PDFs you point it at (paths below are the defaults Tim uses)
and writes into ui/assets/ — a gitignored folder: the PDFs and every image
derived from them are user-supplied art and never enter the repository.

Requires the optional dependency: pip install -e ".[ui]" (PyMuPDF).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pymupdf

from struggler.engine.cards import load_cards

OUT = Path(__file__).resolve().parent.parent / "ui" / "assets"


def render_board(map_pdf: Path) -> None:
    """Render page 1 of the map PDF to board.png (~3000 px wide)."""
    doc = pymupdf.open(map_pdf)
    page = doc[0]
    zoom = 3000 / page.rect.width
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    pix.save(OUT / "board.png")
    print(f"board: {pix.width}x{pix.height} -> {OUT / 'board.png'}")


def extract_cards(cards_pdf: Path) -> None:
    """Extract the embedded card-face images in deck order.

    Each card is an embedded raster; placement order (not xref order) is the
    canonical card-number order — verified: page 1's top row holds numbers
    1-4, bottom row 5-8, matching cards.json numbering, and the mid-deck
    slices carry matching printed numbers. So sort every placement across
    the document by (page, row, column) and the Nth placement is card
    number N. 110 cards; trailing pages hold spares and are skipped.
    """
    doc = pymupdf.open(cards_pdf)
    number_to_id = {card.number: cid for cid, card in load_cards().items()}
    cards_dir = OUT / "cards"
    cards_dir.mkdir(parents=True, exist_ok=True)
    placements = []
    for pno, page in enumerate(doc):
        for info in page.get_image_info(xrefs=True):
            x0, y0, _, _ = info["bbox"]
            placements.append((pno, round(y0 / 10), round(x0 / 10), info["xref"]))
    placements.sort()
    manifest: dict[str, str] = {}
    for number, (_, _, _, xref) in enumerate(placements, start=1):
        if number > 110:
            break
        cid = number_to_id[number]
        info = doc.extract_image(xref)
        name = f"{cid}.{info['ext']}"
        (cards_dir / name).write_bytes(info["image"])
        manifest[cid] = name
    (OUT / "cards.json").write_text(json.dumps(manifest, indent=1))
    sizes = sorted((cards_dir / f).stat().st_size for f in manifest.values())
    print(f"cards: {len(manifest)} faces, {sizes[0]}-{sizes[-1]} bytes -> {cards_dir}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map-pdf", default="Twilight Struggle Map v1.1.pdf")
    parser.add_argument("--cards-pdf", default="Twilight Struggle PNP Cards v3.2.pdf")
    parser.add_argument("--skip-board", action="store_true")
    parser.add_argument("--skip-cards", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.skip_board:
        render_board(Path(args.map_pdf))
    if not args.skip_cards:
        extract_cards(Path(args.cards_pdf))


if __name__ == "__main__":
    main()
