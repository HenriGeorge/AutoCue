# AutoCue — Claude Code Guide

## Browser testing

When doing browser testing or UI verification with Chrome DevTools tools:
- **Always send screenshots to the user** using `SendUserFile` after every meaningful state change (before action, after action, result visible). Do not just describe what you see.
- **Screenshots are pre-approved** — take them freely without asking permission. `mcp__plugin_chrome-devtools-mcp_chrome-devtools__take_screenshot` is always allowed.
- Save screenshots to `/var/folders/kg/k03ymsv51sjd109wm__rfyt40000gn/T/` (the allowed temp dir).

## What this project is

AutoCue places hot cues on Rekordbox 7 tracks automatically and analyses a DJ library, across three surfaces:

1. **Python CLI** (`autocue/`) — reads Rekordbox's database and ANLZ files directly. Fallback strategy: phrase → bar → heuristic. Outputs a Rekordbox XML for import.
2. **Local server** (`autocue serve`) — FastAPI at `localhost:7432`. Serves the web UI and exposes a REST API that reads/writes the Rekordbox database directly. **All intelligence features** (energy, mixability, classification, similar tracks, transitions, set builder, library health, auto-tagging, comment enrichment, Discogs, discovery, download) are only available in this mode.
3. **Web app** (`docs/index.html`) — browser-based, single HTML file, no build step. Hosted on GitHub Pages (XML in/out). Also served by `autocue serve` in local mode for the full feature set.

## Development commands

```bash
pip install -e ".[dev]"              # install with test deps (fastapi, uvicorn, psutil, httpx, hypothesis)
pip install -e ".[download]"         # OPTIONAL: YouTube download support (yt-dlp; also needs ffmpeg on PATH)
pytest                               # run all 1094 Python tests
npm install                          # one-time: install JS test deps
npm test                             # run 435 Vitest tests for the web app

autocue serve --no-browser           # start local server on localhost:7432
autocue --library --dry-run          # preview CLI output without writing
autocue --track "Song Title"
autocue --library --overwrite        # re-generate for all tracks (default skips already-cued)
autocue --library --playlist "NAME"  # restrict --library to a named playlist
```

## Must-know constraints (read every session)

- **Rekordbox must be closed** before any write (CLI or local-mode Apply). DB is SQLCipher-locked while open. Server enforces this at every write endpoint via `_rb_running(db)`.
- **Web app is a single self-contained HTML file** — no build step, no framework. All app changes go to `docs/index.html`. `package.json` exists only for dev testing.
- **pyrekordbox**: use `Rekordbox6Database` from `pyrekordbox.db6`. `add_track()` takes the file path **positionally**.
- **ANLZ parsing**: wrap `db.read_anlz_file()` / `get_tag()` in `try/except Exception` — `ConstError` / `IndexError` are common for unsupported ANLZ versions and missing tags.
- **BPM guard**: always check `float(bpm) > 0` before using BPM in calculations — Rekordbox can store `"0.0"` (truthy string, zero float).
- **JS fetch error handling**: always check `r.ok` before reading typed properties from `r.json()` — error bodies return `{detail: "..."}` and reading `resp.applied` etc. yields `undefined` and misleading toasts.
- **Discover v2** lives in `autocue/analysis/discover/` (taste, style_graph, feeders/, ranker, scan_orchestrator, store) + REST surface under `/api/discover/*`. Per-scan budget is locked at **artist=20, label=15, novelty=10** (HARD_SCAN_REQUEST_CAP=60); changes need PRD §4 sync. Snooze durations are **1w / 1m / 3m** only — `'30d'` 400s the backend.

## Depth — read on demand

- Module map + REST endpoint list → `.claude/project/architecture.md`
- DB / pyrekordbox / DjmdCue / DjmdContent / DjmdKey / DjmdColor specifics → `.claude/project/db-constraints.md`
- API design (SSE patterns, CORS, source classification, /api/tracks SQL, /api/status diagnostic, restore, youtube/search bounds, has_phrase/has_beats) → `.claude/project/api-design.md`
- Web UI internals (AppState pub/sub, `_cardMap` diffing, RAF playhead, mini waveform, sticky bar, action bar, `_consumeSSE`) → `.claude/project/web-ui.md`
- Analysis modules + caches + testing (energy/transitions/setbuilder/auto-tag/comment/discogs/discovery/download, similar._INDEX guard, JS test sync, Hypothesis) → `.claude/project/analysis-and-testing.md`
- Discover v2 architecture (taste vector, style graph, feeders, ranker, scan orchestrator, store, novelty rotation, snooze popover, keyboard shortcuts, budget table) → `docs/reference/discover-v2.md`
- End-user feature documentation → `docs/FEATURES.md`
