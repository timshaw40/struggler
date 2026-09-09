# AGENTS.md

Working notes for AI coding agents in this repo.

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`github.com/timshaw40/struggler`), driven via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context layout: `CONTEXT.md` and `docs/adr/` at the repo root, created lazily by `/domain-modeling`. See `docs/agents/domain.md`.

## Repo conventions

- User-supplied data is never committed: PDFs, board/card art (`ui/assets/`), and the BGG sessions archive (`twilight_struggle_sessions.txt`) are gitignored. Code and derived factual data (e.g. `ui/countries.json`) are committed.
- Small commits, each with a "why" paragraph. Branches: `feature/<slug>`, PRs to `main`.
- `uv run pytest -q` must stay green (currently ~393 tests).
