# Discover v2 — Architecture Reference

Technical reference for the personalised release-discovery pipeline. End-user
documentation lives in [FEATURES.md §14](../FEATURES.md#feature-14-new-release-discovery-discover-v2);
the legacy v1 Discover (still backing the **Discogs Genre Tags** panel) is in
[discogs-and-discovery.md](discogs-and-discovery.md).

## Table of contents

- [1. Overview](#1-overview)
- [2. Module map](#2-module-map)
- [3. Taste vector (`discover.taste`)](#3-taste-vector-discovertaste)
- [4. Style adjacency graph (`discover.style_graph`)](#4-style-adjacency-graph-discoverstyle_graph)
- [5. Feeders](#5-feeders)
  - [5.1 Artist watch](#51-artist-watch)
  - [5.2 Label watch + TTL](#52-label-watch--ttl)
  - [5.3 Novelty + rotation](#53-novelty--rotation)
- [6. Ranker](#6-ranker)
- [7. Scan orchestrator + concurrent-scan lock](#7-scan-orchestrator--concurrent-scan-lock)
- [8. Per-scan budget table](#8-per-scan-budget-table)
- [9. State store (`discover.store`)](#9-state-store-discoverstore)
- [10. REST + SSE surface](#10-rest--sse-surface)
- [11. Frontend module (`docs/index.html` DiscoverV2)](#11-frontend-module-docsindexhtml-discoverv2)
- [12. Backup integration](#12-backup-integration)
- [13. Testing](#13-testing)

---

## 1. Overview

Discover v2 replaces the v1 single-source "top-N artists" feed with a
personalised three-feeder pipeline driven by the user's own Rekordbox library.
Every install computes its own feed from its own data — no shared curation,
no third-party server holds the taste profile. The PRD (`.agent/prd/PRD.md`)
locks the design contract:

- **Hard budget**: 60 Discogs requests per scan (matches the authenticated
  rate-limit window).
- **Three feeders**: artist watch / label watch / novelty, with budgets
  20 / 15 / 10.
- **Page-1-only**: each feeder takes one Discogs page per artist/label/style
  per scan. Deeper pagination is deferred to a Tier-2 background job.
- **Streaming-first**: the orchestrator yields events as releases land,
  so the UI paints incrementally and the user can cancel mid-scan.
- **State persists locally** in `discover.db` (sidecar to `master.db`)
  and rides along with the existing backup infrastructure.

## 2. Module map

```
autocue/analysis/discover/
├── __init__.py              # package marker
├── taste.py                 # TasteVector dataclass + build_taste_vector + normalize_release_key
├── style_graph.py           # StyleAdjacency + load_style_adjacency (loads data/style_graph.json)
├── ranker.py                # score_release + assemble_feed
├── scan_orchestrator.py     # ScanConfig + ScanResult + run_scan generator + HARD_SCAN_REQUEST_CAP
├── store.py                 # DiscoverStore (sqlite3 wrapper, boot recovery, staging-column writes)
└── feeders/
    ├── __init__.py
    ├── artist.py            # artist_feeder + DEFAULT_ARTIST_BUDGET=20
    ├── label.py             # label_feeder + select_label_slots + DEFAULT_LABEL_BUDGET=15
    └── novelty.py           # novelty_feeder + DEFAULT_NOVELTY_BUDGET=10 + strategy rotation
```

The REST + SSE endpoints live in `autocue/serve/routes.py` under the
`/api/discover/*` prefix; their Pydantic schemas in `autocue/serve/schemas.py`.

## 3. Taste vector (`discover.taste`)

`TasteVector` is a frozen `dataclass` of `Counter`s over the user's library:

| field      | shape         | meaning                                    |
|------------|---------------|--------------------------------------------|
| `artists`  | `Counter[str]`| play-count proxy: track count per artist   |
| `labels`   | `Counter[str]`| same, per label                            |
| `styles`   | `Counter[str]`| same, per Discogs style                    |
| `bpm_hist` | `list[int]`   | 14-bucket BPM histogram                    |
| `key_hist` | `Counter[str]`| same, per Camelot key                      |

`build_taste_vector(db)` walks the `DjmdContent` table, deobfuscates label /
artist names via the existing `pyrekordbox` helpers, and accumulates the
counters. Streaming tracks (Tidal / Spotify shortcuts) are detected by
`file_path` prefix and filtered out — they don't reflect the library you
actually own.

`normalize_release_key(artist, title, release_id)` is the
**PRD §6.3-locked dedup key**:

- Named-artist release → `{artist_norm}|||{title_norm}` so format reissues of
  the same album collapse to one card.
- Empty-artist release (compilation / `Various`) →
  `[compilation]|||{title_norm}|||rid_{release_id}`. The `release_id`
  discriminator prevents two unrelated "Vol 1" comps from colliding;
  KNOWN LIMITATION: different release_ids of the same comp reissue surface as
  separate cards. Master-id enrichment is deferred to Tier 2.

## 4. Style adjacency graph (`discover.style_graph`)

`load_style_adjacency(data_dir)` reads `data/style_graph.json` and returns a
`StyleAdjacency` with two views:

- `adjacency.adjacent(style) -> list[str]` — directional neighbours, ranked
  by edge weight.
- `adjacency.is_terminal(style) -> bool` — true if the style has no outgoing
  edges. Terminal styles never contribute novelty.
- `adjacency.styles` — name → metadata map.

The graph is hand-curated; updating it is a manual JSON edit. The novelty
feeder's `style-adjacent` strategy uses `adjacent()` to pick the 3
neighbours of each of the user's top 3 styles.

## 5. Feeders

Each feeder is a generator that yields:

- `dict` → one release: `{"source": str, "release": {...}, ...}`
- `("warning", {...})` → non-fatal degraded state (e.g. rate-limit
  near-exhausted) — the orchestrator records and continues.
- `("error", {...})` → per-entity exception. Feeder continues to the next
  entity; orchestrator surfaces in the SSE stream.
- `("sparse_adjacency", {...})` → only the novelty feeder, when the taste
  vector or graph can't produce candidates.
- `Discogs429` (raised) → hard stop. Orchestrator aborts the scan.

### 5.1 Artist watch

`artist_feeder(taste_vector, token, *, budget, year_from)` iterates the top
`budget` artists by play count, calls
`discogs.search_artist_releases(name)` once per artist (page 1, per_page=50),
and yields one release per result. Errors yield an `("error", …)` sentinel
*and consume the budget slot* so a flaky artist can't grant the feeder
extra retries.

### 5.2 Label watch + TTL

The label feeder is more involved because the user explicitly follows labels
and we want each follow to be scanned regularly — but not within 24 hours of
the prior scan.

`select_label_slots(store, taste_vector, *, budget, ttl_hours=24, now)` is
the scheduler:

1. List explicit follows from `store.list_followed_labels()`, ordered
   `last_scanned_at ASC NULLS FIRST` (longest-unscanned first).
2. Filter out anything within the TTL window.
3. Take up to `budget` slots.
4. Implicit-fill from `taste_vector.top_labels()` is wired but currently a
   no-op because the name→Discogs-id resolver is deferred to Tier 2.

`label_feeder` then walks the slots, calls `search_label_releases(label_id)`,
and on success writes `store.mark_label_scanned(label_id, scan_id)` to the
staging columns. The orchestrator promotes the staging writes to the live
`last_scanned_at` column on successful scan completion (so a crashed scan
never falsely advances the TTL).

### 5.3 Novelty + rotation

`novelty_feeder(taste_vector, adjacency, token, strategy, *, budget, …)`
dispatches to one of three strategies per scan:

- `"style"` — `_style_adjacent`: take top 3 styles, find their adjacent
  styles in the graph (skipping terminal ones), Discogs full-text search
  by style.
- `"label"` — `_label_adjacent`: fetch `/labels/{id}` for each followed
  label, collect parent + sublabel ids, then search releases for each
  adjacent label.
- `"artist"` — `_artist_adjacent`: fetch `/artists/{id}` relations for the
  top resolved artist ids, collect groups + members, then search releases
  for each.

Strategy rotation is driven by the orchestrator: the last successful scan's
`novelty_strategy` is read from the `scans` table, and
`next_novelty_strategy(prev)` rotates `style → label → artist → style`.

Every strategy yields `("sparse_adjacency", {...})` if it can't produce
candidates (no top styles, all top styles terminal, no resolved artist ids,
no followed labels with ids). The orchestrator surfaces these as a UI
warning so the user knows that scan won't have much novelty.

## 6. Ranker

`score_release(release, taste_vector, feed_ctx) -> float` computes a single
fitness score. Components:

- **Taste match**: weight on artist/label/style overlap with the taste vector.
- **Novelty bonus**: small boost when `source.startswith("novelty:")` so
  novelty isn't always last.
- **Freshness**: linear bonus on release year for the past 5 years.
- **Hard-block guards**: `feed_ctx.blocked_artists` and
  `feed_ctx.blocked_labels` zero the score; the orchestrator filters those
  out before yielding.

`assemble_feed(scored, top_n)` sorts by score desc and returns the top N.

## 7. Scan orchestrator + concurrent-scan lock

`run_scan(store, taste_vector, adjacency, token, *, config, ...)` is the
master generator. It:

1. Calls `store.start_scan(...)` which inserts a row in `scans` with
   `finished_at = NULL`. **That NULL is the concurrent-scan lock** — the
   SSE endpoint refuses to start another scan while it exists. Boot
   recovery clears any row left open by a crash.
2. Iterates feeders in config order; for each feeder it dispatches via
   `_dispatch_feeder` (looks up by name) and re-yields the events.
3. Dedups every release against `library_album_set`, `store.is_saved(key)`,
   `store.is_downloaded(key)`, `store.is_dismissed(key)`,
   `store.is_snoozed(key)` before scoring.
4. Hard-blocked releases (score 0) drop out before yielding.
5. On the final `done` event, runs `store.commit_pending_scan(scan_id)`
   which promotes staging-column writes to live columns and stamps
   `finished_at`. Releases the lock.

`ScanConfig.validate()` is the **last-line guard**: any feeder-budget table
that sums above `HARD_SCAN_REQUEST_CAP=60` raises `ValueError("exceeds
hard cap")`. Tests live in `test_discover_budget_audit.py`.

## 8. Per-scan budget table

| feeder   | default | rationale                                            |
|----------|---------|------------------------------------------------------|
| artist   | 20      | top-20 artists at 1 request each, ≤ 20s @ 60/min     |
| label    | 15      | explicit follows, longest-unscanned-first            |
| novelty  | 10      | one strategy per scan; budget shared across substeps |
| **total**| **45**  | leaves 15 of the 60-req window as headroom           |

The `HARD_SCAN_REQUEST_CAP` constant in `scan_orchestrator.py` is the
**absolute ceiling** — any change to the budget table needs PRD §4 sync.

## 9. State store (`discover.store`)

`DiscoverStore(db_path)` opens a sqlite3 connection with `journal_mode=WAL`
and runs the schema migrations + boot recovery. Tables:

- `scans` — per-scan telemetry + concurrent-scan lock (`finished_at`).
- `saved`, `dismissed`, `snoozed`, `downloaded` — keyed by
  `release_key` (the `normalize_release_key` output).
- `followed_labels` — Discogs label_id + name + `last_scanned_at` +
  staging columns (`last_scanned_at_pending`, `pending_scan_id`).
- `blocked_artists`, `blocked_labels`.

Staging-column writes ensure a crashed scan never falsely advances the
label TTL. `commit_pending_scan(scan_id)` promotes by `scan_id` so two
interleaved scans (the lock prevents this in production but boot recovery
must still handle the edge) don't clobber each other.

## 10. REST + SSE surface

Every endpoint is under `/api/discover/*`. Selected highlights:

| Method | Path                                  | Purpose                              |
|--------|---------------------------------------|--------------------------------------|
| GET    | `/feed`                               | SSE stream of progress / release / warning / error / sparse_adjacency / done events |
| GET    | `/feed/status`                        | Is a scan currently running?         |
| POST   | `/feed/cancel`                        | Cancel the active scan               |
| GET    | `/saved`, `/dismissed`, `/snoozed`    | List rows (snooze takes `include_resurfaced`) |
| POST   | `/save`, `/dismiss`, `/snooze`        | Mutate state                         |
| GET    | `/labels`, `/labels/suggested`, `/labels/search` | Follow management         |
| POST   | `/labels/follow`, `/labels/unfollow`  |                                      |
| GET    | `/blocked-artists`, `/blocked-labels` |                                      |
| POST   | `/block-artist`, `/unblock-artist`, `/block-label`, `/unblock-label` |       |
| GET    | `/releases/{id}`                      | Discogs release detail (tracklist + master_id when available) |
| GET    | `/state/export`                       | Gzipped sqlite snapshot              |
| POST   | `/state/import`                       | Replace the live store with an uploaded snapshot |
| GET    | `/stats`                              | Aggregate telemetry                  |
| GET    | `/token-status`                       | `{valid: bool}` — drives the token banner |

The `snooze` POST accepts `duration ∈ {1w, 1m, 3m}` only. `30d` is
rejected with HTTP 400 — the UI's snooze popover enforces the set.

## 11. Frontend module (`docs/index.html` DiscoverV2)

The web app is a single `docs/index.html`. The Discover v2 module is an
IIFE that exposes a pub/sub state container plus the SSE consumer:

```js
const DiscoverV2 = (() => {
  const state = { cards, cardsByKey, savedKeys, dismissedKeys,
                  snoozedKeys, resurfacedKeys, followedLabels,
                  blockedArtists, blockedLabels, scanRunning,
                  scanError, scanWarnings, … };
  function subscribe(fn) { … }
  async function runScan() { /* fetch + ReadableStream + _handleSSEChunk */ }
  async function save / dismiss / snooze / followLabel / … (release) { … }
  return { state, subscribe, runScan, … };
})();
```

Subscribers are renderers — `_renderDiscoverV2Feed`,
`_renderDiscoverV2ScanProgress`, `_renderDiscoverV2ScanWarnings`,
`_renderDiscoverV2Onboarding`, `_renderDiscoverV2TokenBanner`,
`_renderDiscoverV2Followed`, `_renderDiscoverV2Blocked`. They all read
from `state`; pub/sub fan-out swallows per-subscriber exceptions so one
broken renderer doesn't break the chain.

The detail panel + snooze popover + download confirm modal + keyboard
help overlay each install a per-open keydown handler that's removed on
close, so Escape stays scoped to the active dialog.

Keyboard map (PRD §5.6):

| key   | action                                         |
|-------|------------------------------------------------|
| `j`   | next card                                      |
| `k`   | previous card                                  |
| `Enter` | open detail panel                            |
| `s`   | save                                           |
| `x`   | dismiss                                        |
| `z`   | open snooze popover                            |
| `D` (Shift+d) | open download confirm modal            |
| `?`   | toggle keyboard help overlay                   |
| `Esc` | close active dialog                            |

`D` is uppercase on purpose — requires Shift — so a stray lowercase `d`
never starts a download. The confirm modal's default focus is **Cancel**
as the second layer of safety.

## 12. Backup integration

`discover.db` rides along with the existing backup sidecar machinery
(see `backup-and-restore.md`). `autocue/db_writer.py:backup_database()`
takes an optional `discover_db_path=` arg and writes both files into the
backup with paired timestamps. Pre-v2 backups (master-only) restore
cleanly — the missing discover.db is treated as an empty store and a
fresh scan repopulates it.

Export / import via `/api/discover/state/export` and `/import` is a
separate gzip-snapshot path independent of the master.db backup — use
it for multi-machine sync. The export filename is timestamped
(`discover-YYYY-MM-DD.db.gz`).

## 13. Testing

- **Backend pytest** — `tests/test_discover_*.py` covers taste vector,
  style graph, each feeder, ranker, scan orchestrator, store CRUD + boot
  recovery, REST endpoints, backup sidecar, and `test_discover_budget_audit.py`
  is the adversarial budget-overrun audit (T-040).
- **Frontend Vitest** — `tests/web/discover-v2-*.test.js` covers
  per-helper logic (sort, snooze, detail panel, YouTube carousel,
  keyboard handler, settings, blocked list, empty/error states,
  export/import, stats) and `discover-v2-integration.test.js` is the
  end-to-end SSE-consumer integration sweep.
- **Playwright e2e** — `tests/e2e/discover-v2.spec.ts` drives the real
  UI in Chromium with the `/api/discover/*` surface routed/mocked. Not
  in CI; invoked by the `autocue-qa` agent locally.
