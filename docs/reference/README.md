# AutoCue Feature Reference

Deep-dive technical documentation for every AutoCue feature. Each doc is a self-contained reference covering algorithm, public API, REST surface, UI integration, edge cases, performance, and tests.

For a high-level user tour, see [`../FEATURES.md`](../FEATURES.md).
For project invariants and architecture, see [`../../CLAUDE.md`](../../CLAUDE.md).

## Cue generation

| Doc | What it covers |
|---|---|
| [`cli-usage.md`](./cli-usage.md) | The `autocue` CLI — every flag, subcommand, exit code, and 10+ worked examples. Includes `autocue serve` launch options. |
| [`cue-generation.md`](./cue-generation.md) | The phrase → bar → heuristic strategy fallback. Smart slot ordering (A = mix-in, B = first Outro). Memory cue modes. `DjmdCue` insert invariants (`Kind = slot + 1`, `InFrame = round(position_ms * 150 / 1000)`). |

## Analysis suite

| Doc | What it covers |
|---|---|
| [`energy-and-mixability.md`](./energy-and-mixability.md) | PWAV waveform → 0–1 energy curve. `classify_energy_profile()` (flat / build / wave / drop-then-flat). Mixability formula (25% intro + 25% outro + 20% variance + 15% vocal + 15% phrase). Cache keying `(content.ID, n_points)`. |
| [`track-classification.md`](./track-classification.md) | Trapezoidal membership over BPM × energy × vocal-proxy for warmup / build / peak / after_hours / closing. Bug 2 fix (no-ANLZ cap at ~70%). Hypothesis property tests. |
| [`similar-tracks.md`](./similar-tracks.md) | 6-dim feature vector + cosine similarity. ±8 BPM gate. Data-quality cap of 0.65 when neither track has ANLZ energy data. Module-level `_INDEX` / `_INDEX_BUILT` / `_INDEX_LOCK` thread-safety. Bug 1 fix. |
| [`transition-scoring.md`](./transition-scoring.md) | BPM (40%) + Camelot key (35%) + Energy (25%). `_energy_score(None, None) = 50.0` and one-side-missing → cap at 75 (Bug 3 fix). `transition_advice()` DJ tips decision tree. |
| [`set-builder.md`](./set-builder.md) | Beam search (width=5) with O(n×K) candidate retrieval. Setbuilder-specific reweighting (0.25/0.40/0.35) when BPM is changing. +15 BPM-progress bonus. Asymmetric BPM gate (≥12 BPM forward). Three-axis dedup (track / title+artist / artist-count). `mix_advice` per row. `anchor_track_ids` must-includes. Bug 4 fix in full. |
| [`library-health.md`](./library-health.md) | Cue Quality Checker scoring (−30 NO_CUES, −10 NO_PHRASE/NO_BEATGRID, −5 DUPLICATE_CUE/UNNAMED_CUES). Fix tiers (phrase / bar / heuristic / none). `/api/health` SSE with per-track exception isolation. |

## Tagging & enrichment

| Doc | What it covers |
|---|---|
| [`auto-tag.md`](./auto-tag.md) | DjmdMyTag + DjmdSongMyTag writes. Idempotent `ensure_tag_by_name()` pattern. Detectors: category / vocal / energy_level / energy_profile / intro_outro / decade / bpm_tier / play_history. `MIN_SCORE = 0.70` for classification tags. `undo_data` / `undo_tag_run` reverses. `skip_existing` Discogs flow. |
| [`comment-enrichment.md`](./comment-enrichment.md) | MIK-compatible format `"8A - Energy 7 \| Peak \| 4 bar intro"`. `/* AutoCue: ... */` sentinel block (idempotent re-runs). Per-track commit on the stream endpoint. `DjmdContent.Commnt` spelling (NOT `Comment`). |

## External integrations

| Doc | What it covers |
|---|---|
| [`discogs-and-discovery.md`](./discogs-and-discovery.md) | Discogs styles API client + new-release discovery. Token-bucket rate limiter (60 req/min). `library_artists()` ranking by play-frequency. `DiscoverItem.formats` Discogs format chips. Auth resolution: request body → `DISCOGS_TOKEN` env → `.env`. |
| [`youtube-download.md`](./youtube-download.md) | `[download]` extra (yt-dlp + ffmpeg). Lazy imports. `download_audio()` URL pass-through vs `ytsearch1:` wrap. `_detect_music_folder()` via `os.path.commonpath()`. Worker-thread + queue pattern for SSE progress. 503 when extras missing. |

## Server, web, and ops

| Doc | What it covers |
|---|---|
| [`rest-api.md`](./rest-api.md) | Full reference for every `/api/*` endpoint with request/response schemas, status codes, side effects, and example payloads. GZipMiddleware + CORS rules. SSE conventions. |

## Pending

These docs were planned but their agent runs hit the session rate limit before writing. Coming after the next agent dispatch:

- **`web-app.md`** — `docs/index.html` architecture: two modes (XML / local), three tabs, AppState pub/sub bus, `_cardMap` smart diff + FLIP reorder, RAF playhead, mini waveform canvas, `_consumeSSE`, `_explainCue`.
- **`backup-and-restore.md`** — `~/.autocue/backups/master_TIMESTAMP.db` + WAL/SHM sidecars. `/api/backups`, `/api/restore`, `DELETE /api/backups/{filename}`. Why restore must call `similar.clear_index()` (stale feature vectors).
- **`cue-library-tools.md`** — bulk rename / recolor / shift / delete-orphan operations via `/api/cue-tools-stream`. Dry-run default. InFrame shift math. Backup behaviour.
- **`playlist-suggest.md`** — category-filtered weighted-random suggestions. `seed_track_ids` bypass `exclude_ids`. Pool size = `max(count * 3, 60)`. Pairs with `POST /api/playlists` for creating a Rekordbox playlist from the result.

## Conventions

Every doc follows the same shape:

1. **Overview** — what the feature does and why
2. **How it works** — algorithm/implementation with `file:line` refs
3. **Public API** — function signatures, REST endpoints, CLI args
4. **Configuration** — env vars, request params, settings
5. **Behavior details** — edge cases, error handling, performance
6. **UI surface** — where it appears in the web app
7. **Examples** — concrete inputs and outputs
8. **Limitations / known issues**
9. **Testing** — which tests cover it
10. **Related** — links to sibling docs

`file:line` references point into the live source tree. Code snippets are quoted verbatim from the implementation; if a snippet stops matching the code, the source is the source of truth.

## Cross-cutting references

- [`../FEATURES.md`](../FEATURES.md) — end-user feature tour with screenshots and walkthroughs
- [`../../README.md`](../../README.md) — install, quick-start, three-mode comparison
- [`../../CLAUDE.md`](../../CLAUDE.md) — project invariants, key constraints, architecture tree
- [`../../SCORING_BUGS.md`](../../SCORING_BUGS.md) — adversarial-review log behind the current scoring design (Bugs 1–4 in `similar.py`, `classify.py`, `transitions.py`, `setbuilder.py`)
