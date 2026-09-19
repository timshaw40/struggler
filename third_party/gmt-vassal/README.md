# GMT / VASSAL art for struggler

Graphics from the official Twilight Struggle **VASSAL Deluxe 3.2** module.

**License:** see [`LICENSE`](LICENSE) — personal, non-commercial; at least one
player must own a copy. **Not** covered by the repo root MIT `LICENSE`.

## Layout

- `board/TS Map-11.jpg` — map (5100×3300); fetch with the install script if missing
- `cards/TNRnTS-01.svg` … `TNRnTS-110.svg` — card faces by number
- `markers/` — optional influence / chrome SVGs

## Install into the web UI

The local web UI (`feature/web-ui`) expects runtime files under gitignored
`ui/assets/`. From the repo root:

```sh
python scripts/install_vassal_ui_assets.py --fetch-board
```

That maps card numbers → engine card ids and writes `ui/assets/board.png`,
`ui/assets/cards/{id}.svg`, and `ui/assets/cards.json`.
