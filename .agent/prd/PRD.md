# PRD — Discover & Watchlist v2

**Version:** 1.0 (design-locked)
**Owner:** Henri George
**Date:** 2026-06-07

> Iteration history (7 grill rounds, v0.1 through v0.8) is archived in [`iteration-log.md`](./iteration-log.md). This document is the current authoritative design.

---

## 1. Executive Summary

The current Discover tab surfaces new releases from a Rekordbox library's most-played artists via the Discogs API. It works, but feels narrow, can't be previewed before downloading, downloads with one click (accident risk), and forgets state on reload.

**Discover & Watchlist v2 (Tier 1)** rebuilds the feed around a **per-user taste vector** derived from the local Rekordbox library, adds **label watch** (the biggest current gap), introduces a **novelty term** in the ranker so the feed surfaces adjacent finds and not just echoes of the library, adds **inline YouTube preview**, replaces the one-click download with a **detail panel + explicit downloads** (with a power-user Shift-click bypass), and persists state in a local SQLite store integrated with AutoCue's existing backup infrastructure.

**Tier 1.5 may add shop watch** built on RSS / Bandcamp / homepage feeds, gated on a one-time validation script discovering ≥ 8 working sources across the candidate shop list. If the gate isn't met, shop-watch defers to Tier 2 and Tier 1 ships without it. A **friends lane** is deferred to Tier 3 until Discogs-collection privacy defaults are addressed with explicit UX.

Every install computes its own feed from its own data — no shared curation. Friends with different libraries see different feeds from the same codebase.

---

## 2. Problem Statement

Current pain points (from user feedback + design pressure-testing):

1. **Too narrow** — only top-played artists; misses labels, related artists, genre/style angles
2. **Poor signal** — feels random or stale; no taste-aware ranking, no exploration
3. **Weak filtering & sorting** — can't slice by genre, format, year
4. **Limited sources** — Discogs only; user wants record-shop sources
5. **No preview** — can't hear a release before deciding
6. **Accidental downloads** — clicking "Album" downloads immediately
7. **No memory** — saves/dismisses don't survive reload
8. **No novelty** — current model is similarity-to-library; will never surface adjacent finds
9. **Concurrent-scan races, empty states, expired-token silent failures**
10. **No durability story for user curation** — saved chips live in JS memory

Closing these gaps turns Discover from a novelty into a daily-driver tool.

---

## 3. Target Audience

- **Primary**: Henri — DJ with Rekordbox 7 library; uses Discogs for metadata
- **Secondary**: His DJ friends — also Rekordbox + Discogs users; different musical taste; their own Discogs accounts

### Assumptions

- Rekordbox 7 library exists locally
- Discogs personal access token in `.env` (already required by current Discover)
- yt-dlp + ffmpeg are optional — preview works without them via YouTube iframe embed
- Local-mode server (`autocue serve`) is running
- **Discogs collections are PRIVATE by default** — friends lane (Tier 3) requires explicit opt-in
- **Most independent record shops do NOT have active Discogs marketplace storefronts** (validated via API probe: hardwax, phonica, boomkat, bleep, rubadub, wallys-groove-world all exist as Discogs users but return `num_for_sale: 0`)

---

## 4. Success Metrics

Personal/small-circle tool, not a SaaS — metrics are qualitative + behavioral. Targets derive from the hard 60-request-per-scan budget (§8 Constraints), not aspirational numbers.

| Metric | Target | How measured |
|---|---|---|
| Saves per scan (vs current baseline) | ≥ 3× | `discover.db.saved` row count delta correlated to `scan_id` via timestamp window |
| Saved → downloaded within 7 days | ≥ 40% | join `saved` × `downloaded` on `release_key`; window by saved_at |
| Dismissed re-appearance rate | 0% | feeder integration test asserts dismissed never resurface |
| Users add ≥1 followed label first session | qualitative | manual check after Henri + 2 friends try it |
| Users add ≥1 followed shop first session (if Tier 1.5 ships) | qualitative | manual check; n/a if shop-watch gate fails |
| First scan completion (cold cache, ≤20 followed labels + ≤8 followed shops) | ≤ 75s typical | scan duration logged in `scans` table; surfaced in UI |
| Cold scan at high config (≤20 labels + ≤20 shops) | ≤ 180s | same |
| Subsequent scan (warm cache; most TTLs fresh) | ≤ 30s | same |
| Accidental download events | 0 | only Download buttons inside detail panel can fire `/api/download`; Shift-click confirm modal defaults to Cancel |
| Novelty-source picks per feed (when adjacency coverage ≥ 3 edges) | ≥ 20% | feeds with `source.startswith('novelty:')` counted; conditional metric — n/a when coverage insufficient |
| Discogs rate-limit hits per scan | 0 | wrapped client backs off when `x-discogs-ratelimit-remaining < 5`; scan declares clean completion or surfaces a hard error |

**Budget reconciliation:** first-scan hard cap is **60 requests** (= ≤60s at authenticated 60/min, ≤145s at unauthenticated 25/min). Budget allocation:

| Feeder | Hard budget per scan | Coverage at full budget |
|---|---|---|
| Artist watch | 20 requests | Top-20 artists, page 1 only (50 releases per artist) |
| Label watch | 15 requests | Top-15 labels, page 1 only |
| Shop watch | 15 requests | Up to 15 followed shops, page 1 only (0 if shop-watch not shipping) |
| Novelty | 10 requests | One strategy per scan, rotated across scans (style→label→artist) |
| **Total** | **60** | First scan ≤60s authenticated |

**Depth tradeoff:** with page-1-only, an artist with 200 releases shows only their 50 most recent. Going deeper requires Tier 2 background pagination. First-scan UX optimizes for freshness, not exhaustiveness.

---

## 5. Competitive Landscape

| Product | What it does | What v2 takes / leaves |
|---|---|---|
| **Bandcamp follows / new arrivals** | Per-artist/label follow with RSS | Take: Bandcamp RSS as Tier-1.5 candidate source |
| **Beatport "My Beatport"** | DJ-focused new-release feed | Take: label-watch model. Leave: paid Beatport account |
| **Discogs newreleases.discogs.com/for-me** | Personalized feed based on user's Discogs follows | Cannot consume — confirmed 403 to unauthenticated requests; no API equivalent. We build our own personalization |
| **Spotify Release Radar** | Algorithmic weekly playlist | Take: weekly cadence. Leave: streaming-only |
| **Hard Wax / Boomkat newsletters** | Curated weekly emails | Take: shop-as-source. Leave: email parsing infra |

**Differentiator**: AutoCue Discover v2 is the only feed personalized by **your own local Rekordbox play data**, runs **fully local** (no third-party server holds your taste), and explicitly mixes **exploration with retrieval** (novelty term, not echo chamber).

---

## 6. Core Features

### 6.1 Taste Vector

Per-user, computed from the local Rekordbox database. No Discogs collection sync required.

**Inputs:**
- **Artists** — weighted by `log(1 + play_count)` (single mega-played track doesn't dominate); fallback to track count when play history is empty
- **Labels** — weighted by `log(1 + total_label_plays) × √track_count`; fallback to track count for cold-start libraries
- **Styles** — normalized: lowercase, strip non-alphanumerics, apply `STYLE_ALIAS_MAP` (e.g. `deep-house` / `Deep House` / `deephouse` → `deep_house`). Inputs: `DjmdContent.GenreName`, AutoCue-namespaced My Tags only, enriched comments parsed back from MIK format
- **BPM histogram** — buckets of 4 BPM, range 60–200; tracks with BPM=0 excluded
- **Key histogram** — Camelot distribution; tracks with no Key skipped
- **Source filter** — only `source == "file"` Rekordbox tracks contribute. Streaming-source tracks (`spotify:`, `tidal:`, etc.) and tracks with `_audioProbedAt[id] === "missing"` are excluded

**Feedback signals:**
- `saved` items contribute positively (weight 1.0)
- `dismissed` items contribute negatively (weight -1.5)
- `blocked_artist` / `blocked_label` zero out the relevant taste-vector entry (first-class blocks, not just penalties)
- `downloaded` items contribute positively (weight 0.5) AND exclude that release from future surfacing

**Persistence:** recomputed lazily on Discover open; cached in-memory for the session; invalidated on any feedback action.

### 6.2 Candidate Pool — Four Feeders

All feeders respect the per-scan budget from §4. Page 1 only on first scan; deeper pagination is a Tier 2 background job. Each feeder writes `last_scanned_at_pending` + `pending_scan_id` on the relevant entity; the orchestrator commits these to `last_scanned_at` atomically on successful scan finish.

**Feeder 1 — Artist watch**
- Top-20 artists from taste vector
- `GET /artists/{id}/releases?sort=year&sort_order=desc&page=1&per_page=50`
- Filter to releases in the last 90 days
- Per-artist budget: 1 request; TTL: 24h

**Feeder 2 — Label watch**
- Up to 15 labels per scan with explicit priority order:
  1. **User-explicit follows take precedence**: every label in `followed_labels` over its 24h TTL gets a slot first
  2. **Taste-vector implicit follows fill remaining slots**
  3. **Fairness when explicit follows > 15**: `ORDER BY last_scanned_at ASC NULLS FIRST` — longest-unscanned first; every explicit follow is scanned at least once per `ceil(N_explicit / 15)` scans
- `GET /labels/{id}/releases?page=1&per_page=50`
- Per-label budget: 1 request; TTL: 24h

**Feeder 3 — Shop watch (Tier 1.5, conditional)**

Typed source model — Discogs sellers are a rare bonus, not the spine:

```python
ShopSource = OneOf[
  DiscogsSeller(handle, num_for_sale_threshold=5),  # only when num_for_sale>=5
  RssFeed(feed_url),                                 # primary path for most shops
  BandcampLabel(label_handle),                       # well-supported public RSS
  ManualLink(homepage_url),                          # bookmark-only — no auto feed
]
```

Per-shop probe sequence at follow-time:
1. Discogs seller handle supplied AND `num_for_sale >= 5` → `DiscogsSeller`
2. Else fetch homepage, look for `<link rel="alternate" type="application/rss+xml">` → `RssFeed`
3. Else if homepage hosts a Bandcamp embed or handle → `BandcampLabel`
4. Else common-path probes: `/feed`, `/rss`, `/atom.xml`, `/blogs/news.atom` (Shopify default) → `RssFeed`
5. Else `ManualLink` — surface in UI as "🔗 Visit shop" only, no auto-feed

TTL: 6h for `DiscogsSeller`/`RssFeed`/`BandcampLabel`; never auto-fetch `ManualLink`.

**Feeder 4 — Novelty**

Reserves up to 25% of final feed slots for releases that are *adjacent but not literal matches* to the taste vector. To fit the 10-request budget, only **one of three strategies runs per scan**, rotated round-robin (state stored in `scans.novelty_strategy`):

- **Style-adjacent**: for each top-3 style, fetch one search query per adjacent style (capped at 3 styles × 3 adjacents = 9 queries). Adjacency from `style_adjacency.json` (~60 styles for Tier 1)
- **Label-adjacent**: for top-5 followed labels, resolve `parent_label_id` and `sub_labels` via Discogs; pull recent releases from adjacent labels
- **Artist-adjacent**: for top-5 taste-vector artists, fetch `members`/`groups` Discogs relations; pull recent releases from adjacent artists

**Cold-start handling:** `style_adjacency.json` ships with two-tier coverage:
- **Anchor styles** (~30): each has ≥ 3 adjacency edges (deep_house, tech_house, techno, drum_and_bass, footwork, jungle, dubstep, garage_uk, ambient, idm, hip_hop, dancehall, afrobeat, jazz_modern, leftfield, electro, breakbeat, etc.)
- **Terminal styles** (~30): 0-2 edges; marked `"terminal": true`

When a user's top-3 styles are all terminal OR all absent from the JSON (sparse-adjacency case): novelty pool returns empty, `scan.novelty_status = 'sparse_adjacency'`, the 25% reservation is skipped (feed assembles as pure retrieval, no garbage backfill), and the UI shows a one-time hint.

Style lookups use `STYLE_ADJACENCY.get(style, [])` — unknown styles return empty list, never `KeyError`.

**Friend feeder** — moved to Tier 3.

### 6.3 Dedup & Ranking

**Dedup** by normalized `release_key`:

```python
def normalize_release_key(raw_artist, raw_title, release_id):
    if raw_artist:  # named-artist case
        return f"{nfkd(raw_artist.lower().strip())}|||{nfkd(raw_title.lower().strip())}"
    else:           # compilation / Various / empty-artist
        return f"[compilation]|||{nfkd(raw_title.lower().strip())}|||rid_{release_id}"
```

- Named-artist case: format variants of the same album (2002 CD + 2024 vinyl) dedup to one card with "available in: vinyl, CD, digital" chip
- Empty-artist case: `release_id` discriminator prevents cross-compilation collisions (two unrelated "Vol 1" comps stay separate). **Known limitation**: different release_ids of the SAME compilation (1981 original + 2024 reissue) appear as TWO separate cards. Mitigation deferred to Tier 2 (background `/releases/{id}` enrichment + post-scan merge using Discogs `master_id`)

The coerced "Unknown Artist"/"Unknown Title" values stored in display columns are NEVER fed back into `release_key` computation.

`release_key` is the **source-of-truth for all curation state**: all CRUD operations on `saved` / `dismissed` / `snoozed` / `downloaded` look up state by `release_key`. The 2024 reissue of an album you saved in 2022 reads as already-saved (for named-artist releases).

**Already-owned filter:** `library_album_set()` (the existing lowercased `(artist|||title)` set from CLAUDE.md) shares the same normalization; per-line normalization is implemented in `autocue.analysis.discover.taste.normalize_release_key()`.

**Ranking — two stages:**

Stage 1 base score in [0, 100]:
```
base(release) =
   0.28 * artist_match     (cosine sim of release artists vs taste_vector.artists)
 + 0.22 * label_match      (1.0 if label ∈ taste_vector.top_labels, else fuzzy)
 + 0.18 * style_match      (Jaccard overlap of normalized release.styles vs taste_vector.styles)
 + 0.08 * bpm_fit          (bucket overlap; 0.5 when neither side has BPM data)
 + 0.10 * recency          (linear decay over 90 days)
 + 0.05 * source_diversity (bonus if feeder type not over-represented in top-50)
 + 0.09 * cohort_freshness (bonus for under-represented artists/labels in current feed)
 - hard_block if artist ∈ blocked_artists OR label ∈ blocked_labels
```

Stage 2 novelty reservation:
```
novelty_quota = floor(top_n * 0.25)
novelty_pool  = releases where source.startswith('novelty:') sorted by base_score desc
retrieval_pool = remaining releases sorted by base_score desc

if len(novelty_pool) >= novelty_quota:
    feed = retrieval_pool[:top_n - novelty_quota] + novelty_pool[:novelty_quota]
elif len(novelty_pool) > 0:
    # Partial pool: surface what we have, backfill rest from retrieval
    feed = retrieval_pool[:top_n - len(novelty_pool)] + novelty_pool
    scan_status['novelty_partial'] = len(novelty_pool)
else:
    # Empty pool: pure retrieval, no garbage backfill
    feed = retrieval_pool[:top_n]
    scan_status['novelty_status'] = 'sparse_adjacency'
```

**Sort options:** `Taste match` (default), `Newest`, `Title`, `Artist`, `Explore mode` (flips ratio to 50/50 retrieval/novelty).

Top-50 surfaced by default; "Load more" pages 25 at a time.

### 6.4 Friend Lane (Tier 3)

Deferred for two reasons:
1. **Discogs collections are private by default** — most friends would need to actively change settings before the lane shows anything. Silent failure is a UX hole.
2. **Ranking-by-local-taste-vector defeats the social premise** — if friends had identical taste, their adds would be redundant; if differing, they'd score ~0 and the lane would be empty.

**When it ships:**
- Pre-flight check: `GET /users/{friend}/collection/folders/0` — if 401/403/empty, show explicit UI: *"{friend}'s Discogs collection is private. Ask them to: Discogs → Settings → Privacy → 'Show my collection' toggle, then save folder 0 as public."* with a "Copy message to send them" button
- Ranking: **chronological** by friend's add date (not local taste vector). Optional "Match mine" sort toggle
- Each card shows recency + "matches your taste 87%" chip — visible signal, no filtering

### 6.5 Detail Panel

Card click → right-side slide-out panel:

- Larger artwork, full title, artist, label, year, format chips, country
- Tracklist (fetched lazily from `GET /releases/{id}`; cached in `release_details` SQLite table with 30-day TTL)
- Per-track ▶ — inline YouTube preview (see §6.6)
- **Download all** button (disabled with tooltip if yt-dlp+ffmpeg missing)
- **Download selected** button (per-track checkboxes)
- 💚 Save / 💤 Snooze (1w / 1m / 3m popover, default 1m) / ✕ Dismiss
- 🚫 Block artist / 🚫 Block label under a "More" menu
- External links: Discogs (always), Bandcamp (only when literal `bandcamp.com` substring found in Discogs `videos` or `notes` field), YouTube search (always)
- Source breakdown ("Found via: label Stones Throw + shop Hard Wax + novelty: adjacent style")
- "🔁 Snoozed — resurfaced on {date}" badge if applicable

**Power-user shortcuts:**
- `Shift+click` on card → bypass panel, jump to confirmation modal. **Modal default focus = Cancel** (prevents sticky-Shift + accidental-Enter producing an unintended download)
- Keyboard: `j` / `k` navigate; `Enter` open panel; `s` save; `x` dismiss; `z` snooze; `D` download all (uppercase intentional). `?` shows shortcut help
- "Quick download" button on every saved-list row (intent already confirmed — single-click no modal)
- "Download all saved" batch action in the Saved tab

**Accessibility:**
- Detail panel uses dialog semantics: `role="dialog"`, `aria-modal="true"`, focus trap, `Esc` to close, focus return to triggering card on close
- All buttons have visible focus rings and keyboard activation
- Tracklist preview iframes use `title` attribute with release/track context

**Mobile / narrow screen:**
- < 900px viewport: detail panel renders full-screen instead of side-by-side
- Filter chips collapse into overflow `≡ Filter` button

**No download fires until the user clicks an explicit Download button.**

### 6.6 Inline YouTube Preview

Backend: existing `/api/youtube/search` (bounded by `_yt_search_semaphore`).

Frontend: iframe embed at `https://www.youtube-nocookie.com/embed/{videoId}?autoplay=0&modestbranding=1`.

**Lazy search policy:** On panel open, pre-warm YouTube search for **track #1 only**. Other tracks render their ▶ button with a "Find" hint state — search fires only when the user clicks. Eliminates the per-panel-open yt-dlp storm that could trip YouTube bot detection.

- 3 candidate results per played track in a small carousel; "Try another" cycles
- Disk-cached search results in `youtube_results` table keyed by `(release_key, track_index)`
- Inline error recovery: iframe `onerror` → auto-advance to next candidate; on exhaustion show "Preview unavailable — [Search YouTube] / [Search Bandcamp]"
- iframe `allow="autoplay; encrypted-media"`; `sandbox="allow-scripts allow-same-origin allow-presentation"`
- No autoplay on load (cross-origin autoplay-with-sound is browser-blocked)

### 6.7 Persistent State

**Location:** platform-native data directory:
- **macOS**: `~/Library/Application Support/AutoCue/discover.db`
- **Linux**: `${XDG_DATA_HOME:-~/.local/share}/autocue/discover.db`
- **Windows**: `%APPDATA%\AutoCue\discover.db`

Reachable via `autocue.serve.deps.discover_data_dir()`. Parent dirs created with `Path.mkdir(parents=True, exist_ok=True)` on DiscoverStore init.

**Backup integration — parallel sidecar:**

`discover.db` rides AutoCue's existing `BACKUP_DIR` / `/api/backups` system as a parallel sidecar file at `BACKUP_DIR/discover_<timestamp>.db` using the same timestamp as the master.db backup. No tarball, no format break.

- Old backups (master.db only, pre-v2): restore restores master only and logs "no discover.db sidecar — leaving current curation state intact"
- New backups capture both
- `DELETE /api/backups/{timestamp}` deletes both files
- Two-phase atomicity: CREATE writes `.tmp` files then atomic rename; DELETE renames to `.deleting` then unlinks; RESTORE makes a pre-snapshot before swap with rollback on failure
- Startup cleanup pass removes orphan `*.tmp` / `*.deleting` files; failures are non-fatal (retry next startup)

**Restore validation:**
- `discover.db`: SQLite magic header check (`bytes[:16] == b'SQLite format 3\x00'`)
- `master.db` (SQLCipher-v4 encrypted from byte 0 in Rekordbox 6/7 — no plaintext header): `pyrekordbox.Rekordbox6Database(presnap_path)` opens without raising. pyrekordbox internally resolves the SQLCipher key via `deobfuscate(BLOB)` from a bundled obfuscated blob — works for arbitrary paths including BACKUP_DIR (verified by reading the `Rekordbox6Database.__init__` source)
- If pre-snapshot creation or validation fails: abort restore with explicit error, leave user state untouched

**Schema (v1):**

```sql
CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

-- State tables: release_key is PRIMARY KEY (source of truth for curation lookups)
-- release_key_version captures the normalize_release_key() version that produced the key.
-- Future normalization changes trigger a migration that re-derives keys (artist/title columns
-- are kept on every row to support re-normalization).
-- artist/title are NULLABLE because Discogs releases can have empty artist (compilations);
-- the store CRUD layer coerces empty/null to "Unknown Artist"/"Unknown Title" at insert time
-- for display purposes only — never fed back into release_key.

CREATE TABLE saved (
  release_key TEXT PRIMARY KEY,
  release_key_version INTEGER NOT NULL DEFAULT 1,
  release_id INTEGER NOT NULL,
  artist TEXT, title TEXT, label TEXT,
  saved_at TEXT NOT NULL
);
CREATE TABLE dismissed (release_key TEXT PRIMARY KEY, release_key_version INTEGER NOT NULL DEFAULT 1,
                        release_id INTEGER, artist TEXT, title TEXT,
                        dismissed_at TEXT NOT NULL, reason TEXT);
CREATE TABLE snoozed   (release_key TEXT PRIMARY KEY, release_key_version INTEGER NOT NULL DEFAULT 1,
                        release_id INTEGER, artist TEXT, title TEXT,
                        snoozed_at TEXT NOT NULL, until_date TEXT NOT NULL);
CREATE TABLE downloaded(release_key TEXT PRIMARY KEY, release_key_version INTEGER NOT NULL DEFAULT 1,
                        release_id INTEGER, artist TEXT, title TEXT,
                        downloaded_at TEXT NOT NULL, file_paths TEXT);
-- file_paths is ALWAYS JSON-encoded list ('["/path/to/file.flac"]') even for single files;
-- record_download() wraps; future re-normalization merge runs json.loads() safely.

CREATE TABLE blocked_artists (discogs_artist_id INTEGER PRIMARY KEY, name TEXT, blocked_at TEXT);
CREATE TABLE blocked_labels  (discogs_label_id  INTEGER PRIMARY KEY, name TEXT, blocked_at TEXT);

CREATE TABLE followed_labels (
  label_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  added_at TEXT NOT NULL,
  last_scanned_at TEXT,              -- committed value; TTL gate reads this
  last_scanned_at_pending TEXT,      -- in-flight scan writes here
  pending_scan_id INTEGER,           -- scan_id that wrote the pending value
  health TEXT,                       -- 'ok' | 'rate-limit' | 'not-found' | 'error'
  consecutive_errors INTEGER DEFAULT 0,
  current_name_check_at TEXT         -- label-rename watchdog (Tier 2)
);

CREATE TABLE followed_shops (
  shop_id INTEGER PRIMARY KEY AUTOINCREMENT,
  display_name TEXT NOT NULL,
  source_type TEXT NOT NULL,         -- 'discogs' | 'rss' | 'bandcamp' | 'manual'
  source_url TEXT NOT NULL,
  added_at TEXT NOT NULL,
  last_scanned_at TEXT,
  last_scanned_at_pending TEXT,
  pending_scan_id INTEGER,
  health TEXT,
  consecutive_errors INTEGER DEFAULT 0
);

CREATE TABLE scans (
  scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,                  -- NULL while running
  status TEXT NOT NULL DEFAULT 'running',  -- 'running' | 'ok' | 'cancelled' | 'rate_limited' | 'crashed'
  feeders TEXT,                      -- comma-list
  novelty_strategy TEXT,             -- 'style' | 'label' | 'artist'
  novelty_status TEXT,               -- 'ok' | 'sparse_adjacency' | 'partial'
  unknown_styles TEXT,               -- JSON list of taste-vector styles missing from style_adjacency.json
  duration_ms INTEGER,
  requests_used INTEGER,             -- validates the 60-cap
  releases_seen INTEGER, releases_after_dedup INTEGER, releases_surfaced INTEGER
);

CREATE TABLE release_details (release_id INTEGER PRIMARY KEY, payload_json TEXT,
                              fetched_at TEXT, expires_at TEXT);

CREATE TABLE youtube_results (release_key TEXT, track_index INTEGER, results_json TEXT,
                              fetched_at TEXT, PRIMARY KEY (release_key, track_index));

CREATE TABLE friends (discogs_username TEXT PRIMARY KEY, alias TEXT, added_at TEXT);  -- Tier 3 reserved
```

**Migrations runner:**

```python
def run_migrations(conn):
    has_version = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    current = 0
    if has_version:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] or 0
    for path in sorted((Path(__file__).parent / "migrations").glob("[0-9]*.sql")):
        version = int(path.name.split("_")[0])
        if version <= current:
            continue
        conn.executescript(path.read_text())
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                     (version, datetime.utcnow().isoformat()))
    conn.commit()
```

001_initial.sql includes `CREATE TABLE schema_version (...)`; later migrations only contain their own ALTER/CREATE statements.

**Boot-time scan-lock recovery:** On `DiscoverStore` construction:

```sql
-- Step 1: discard pending values from crashed scans
UPDATE followed_labels SET last_scanned_at_pending = NULL, pending_scan_id = NULL
 WHERE pending_scan_id IN (SELECT scan_id FROM scans WHERE finished_at IS NULL);
UPDATE followed_shops  SET last_scanned_at_pending = NULL, pending_scan_id = NULL
 WHERE pending_scan_id IN (SELECT scan_id FROM scans WHERE finished_at IS NULL);

-- Step 2: close the scan rows
UPDATE scans SET finished_at = ?, status = 'crashed' WHERE finished_at IS NULL;
```

Per-scan_id rollback (not timestamp window) — multiple crashed scans interleaved with successful ones never produce cascading rollback.

**Re-normalization migrations** (when `normalize_release_key()` changes in a future version):

A `00N_renormalize_keys.sql` migration:
1. Reads each row's (artist, title, release_key_version)
2. Computes new key via current `normalize_release_key()`
3. UPDATEs row with new release_key + release_key_version=N
4. Conflicts (two old keys collapse to one new key) resolved per-table:
   - `saved`: keep most-recent `saved_at`
   - `dismissed`: keep OLDEST `dismissed_at`
   - `snoozed`: keep MAX `until_date` (longest snooze wins)
   - `downloaded`: MERGE `file_paths` as JSON list union; keep most-recent `downloaded_at`

**Multi-machine sync:** not auto-synced — explicit Export/Import buttons in Discover settings produce a `.gz` of `discover.db`. Data dir lives in OS-standard locations that Time Machine includes by default; Dropbox-style sync requires manual config.

### 6.8 Settings Panel

Discover sub-tab:

- **Labels you watch** — list with remove buttons; "Add label by name" autocomplete (`search_labels()`); "Suggested from your library" (top-20 unwatched labels). Each row shows `last_scanned_at` + health badge
- **Shops you watch** — list with remove; "Add shop" wizard: paste URL → probe sequence → confirm detected source type → save. Each row shows source-type icon, `last_scanned_at`, consecutive_errors badge
- **Validated shop starter list** (see §6.10) — pre-filled but disabled by default; user toggles each on individually
- **Blocked artists / labels** — list with remove buttons; populated by 🚫 Block actions from feed cards
- **Stats** — scan count, avg duration, saves-per-scan, novelty share, top labels/shops
- **Sync between machines** — [Export discover.db] + [Import] buttons

### 6.9 Filters & Sort

Sticky bar above feed:
- **Source**: Artist / Label / Shop / Novelty chips (multi-select)
- **Format**: Vinyl / Digital / CD / All
- **Year**: this year / last 2 / all
- **Style**: multi-select from styles present in current results
- **Sort**: Taste match (default) / Newest / Title / Artist / Explore mode

State persists per session only (re-loading is a clean slate).

### 6.10 Shop Starter Pack

Tier 1.5 ships shop-watch with whatever subset of candidates validates. The full candidate list (from Henri's curated record-shop set) is grouped by likely strategy:

| Shop | Country | Strategy to attempt first |
|---|---|---|
| Wally's Groove World | BE | Discogs (handle exists, validate num_for_sale ≥ 5) → else RSS |
| Hard Wax | DE | RSS — own site has weekly chart RSS |
| Phonica | UK | RSS (Shopify-based — try `/blogs/news.atom`) |
| Boomkat | UK | RSS |
| Bleep | UK | RSS |
| Rush Hour | NL | RSS / Bandcamp |
| Static Shock Music | UK | RSS |
| Balades Sonores | FR | RSS |
| Vinyl Vanguard London | UK | RSS / homepage probe |
| Vinylnerds GmbH | DE | RSS / homepage probe |
| Disk Union Jazz Tokyo / Osaka | JP | Manual link only |
| Tower Records Kyoto | JP | Manual link only |
| Discos del Espacio | CO | Bandcamp likely; else manual |
| RPM Records (Bogotá) | CO | Manual link only |
| Nostalgipalatset Stockholm | SE | RSS / homepage |
| Andra Jazz | SE | RSS / homepage |
| Gimbab Records | KR | Bandcamp likely; else manual |
| ... + 12 more requiring probe | — | Probe needed |

**Validation script** (`autocue discover validate-shops`):
- For each candidate: probe Discogs seller → RSS auto-discover → Bandcamp → fallback `manual`
- Writes `starter_pack.json` with validated entries
- Output report: "N shops have Discogs sellers, M have RSS, K Bandcamp-backed, P are manual-only"

**Tier 1.5 ship gate:** if the validated set has **≥ 8 working auto-feed sources** (`discogs` + `rss` + `bandcamp`), Tier 1.5 ships with shop-watch enabled. Else Tier 1.5 doesn't ship in this iteration; shop-watch defers to Tier 2; Tier 1 ships with artist + label feeders only.

**Friend divergence:** shipping the candidate list (not a curated mandatory feed) lets Henri enable Wally's + Phonica + Boomkat while a Stockholm friend enables Nostalgipalatset + Andra Jazz + Hard Wax. Same code, different shops on.

### 6.11 Snooze, Dismiss, Block, Resurface UX

- Card hover reveals: 💚 save / 💤 snooze / ✕ dismiss. Click 💤 → small popover with 1w / 1m / 3m buttons (default 1m)
- Saved items get a green chip; snoozed items hidden until `until_date`; dismissed never re-appear
- 🚫 Block artist / 🚫 Block label live under a "More" menu (less destructive than dismiss — explicit gesture for "never ever")
- **Resurface badge**: when a snoozed release reappears past its `until_date`, the card carries a "🔁 You snoozed this on {date}" badge for one feed-cycle
- "Saved" tab shows wishlist; notes + rating columns are Tier 2

### 6.12 Album-action redesign

The ↓ Album button is removed from the card. Card click opens detail panel. Power-user `Shift+click` is the only path to a 1-step download (with confirm modal defaulting to Cancel).

---

## 7. User Flows

### Flow A: First-run discovery

1. User installs AutoCue, adds Discogs token, opens Discover tab
2. **Discogs token validation** runs: `GET /oauth/identity` — if 401, banner: *"Your Discogs token isn't accepting requests. [Re-enter token]"*
3. Discover detects empty followed-labels list → onboarding banner: *"Pick labels from your library to watch"* + top-10 suggestions
4. User clicks "Add all"
5. First scan (cold cache) → progress bar with feeder-by-feeder counts
6. Feed populates with 75% retrieval / 25% novelty mix; user previews via YouTube
7. User saves 3, dismisses 2, downloads 1
8. State persists. Next open shows cached feed instantly; refresh button shows "Refreshed 3h ago — 4 new since last refresh"

### Flow B: Shop watch onboarding (Tier 1.5 — only if validation gate passes)

1. User opens Discover settings → Shops sub-panel
2. Runs `validate_shop_pack.py` (one-time, exposed as "Validate starter shops" button)
3. **Validation outcome determines whether Tier 1.5 ships** in this iteration
4. Validated shops appear toggle-able with source-type icons
5. User enables Wally's Groove World (RSS) + Phonica (RSS)
6. Next scan includes shop_feeder results, badged with shop name
7. Power-user: pastes `https://my-favourite-shop.com` → probe runs → "Found RSS feed at /blogs/news.atom" → user confirms → saved as `RssFeed`

### Flow C: Preview-before-download (headline fix)

1. User sees `Sam Gendel — Digi-Squires` in feed
2. Clicks card → detail panel slides out with tracklist (cached if seen before)
3. Clicks ▶ on track 3 → first of 3 YouTube candidates loads in iframe
4. Wrong track → clicks "Try another" → next candidate plays
5. Right track → clicks `↓ Download album` → confirm modal → SSE progress in panel
6. On completion, release moves to `downloaded` state and is filtered from future scans

### Flow D: Shift-click power flow

1. User scrolls Discover, recognizes a release they know they want
2. `Shift+click` the card → confirm modal: *"Download Sam Gendel — Digi-Squires (12 tracks)? [Cancel] [Download all]"*
3. Modal default focus is Cancel — user must explicitly Tab or mouse-click to Download
4. Confirms → download starts, no panel detour

### Flow E: Snooze and resurface

1. User clicks 💤 → popover → clicks `1m`
2. Release disappears from feed
3. 30 days later, release reappears in next scan with `🔁 You snoozed this on 2026-06-07` badge for one feed-cycle

### Flow F: Block-artist

1. User opens a card → "More" menu → "🚫 Block artist"
2. All current and future releases by that artist are excluded from all feeders for this install
3. Visible in Settings → "Blocked artists" — can unblock

### Flow G: Friends lane (Tier 3, not shipping in this iteration)

Documented for future reference; see §6.4.

---

## 8. Technical Stack

### Backend (Python — extend existing `autocue/`)

**New module `autocue/analysis/discover/`:**
- `taste.py` — `build_taste_vector(db, *, include_streaming=False)`, `normalize_release_key(...)`
- `style_graph.py` — `STYLE_ALIAS_MAP` + JSON loader for `STYLE_ADJACENCY`
- `style_adjacency.json` — user-editable adjacency graph (~60 styles for Tier 1)
- `style_adjacency.default.json` — bundled default; loader copies to user data dir on first run
- `style_adjacency.schema.json` — JSON Schema for validation
- `feeders/__init__.py` exporting `artist_feeder`, `label_feeder`, `shop_feeder`, `novelty_feeder`
- `feeders/shops/` — `discogs_seller.py`, `rss.py`, `bandcamp.py`, `manual.py`
- `feeders/rss_autodiscover.py` — `<link rel=alternate>` + common-path probing
- `ranker.py` — `score_release(release, taste_vector)`, `assemble_feed(scored, novelty_fraction=0.25)`
- `store.py` — `DiscoverStore` SQLite wrapper + migrations runner + boot-time recovery
- `scan_orchestrator.py` — concurrent-scan lock + dedup pipeline + staging-column commit
- `migrations/001_initial.sql`
- `shops/candidate_pack.json` — the ~30-row candidate list
- `shops/validate_pack.py` — CLI-runnable validator

**Extend `autocue/analysis/discogs.py`:**
- `search_label_releases(label_id, token, year_from)` *(NEW)*
- `search_seller_inventory(seller, token, since_date)` *(NEW)*
- `get_release_details(release_id, token)` *(NEW — for tracklist + master_id)*
- `search_labels(query, token)` *(NEW — for autocomplete)*
- `get_artist_relations(artist_id, token)` *(NEW — for artist-adjacent novelty)*
- `validate_token(token)` *(NEW — calls `/oauth/identity`; positive cache 1h, instant-invalidate on any 401)*
- Wrapper surfaces `x-discogs-ratelimit-remaining`; raises `RateLimitNearExhausted` when <5; raises `Discogs429` with retry-after on 429

**New REST endpoints in `serve/routes.py`:**
- `GET /api/discover/feed` (SSE) — `?sources=`, `?format=`, `?year=`, `?style=`, `?sort=`, `?explore=true`. Concurrent-scan-guarded: returns 409 if a scan is already running
- `GET /api/discover/feed/status` — currently-running scan info (progress per feeder)
- `POST /api/discover/feed/cancel` — abort an in-flight scan
- `GET /api/discover/releases/{id}` — release detail (cached 30 days)
- `POST /api/discover/save` / `dismiss` / `snooze` / `unsave` / `undismiss` / `block-artist` / `block-label` / `unblock-artist` / `unblock-label`
- `GET /api/discover/{saved,dismissed,snoozed,downloaded,blocked-artists,blocked-labels}`
- `GET /api/discover/labels/suggested` / `POST /api/discover/labels/follow` / `unfollow` / `GET /api/discover/labels/search`
- `GET /api/discover/shops/candidates` / `POST /api/discover/shops/validate` (SSE) / `POST /api/discover/shops/follow` / `unfollow` / `probe`
- `POST /api/discover/state/export` (returns gzip of discover.db)
- `POST /api/discover/state/import`
- `GET /api/discover/stats`

Reuses: `/api/youtube/search`, `/api/download`, `/api/download/album`, `_consumeSSE` JS helper.

### Frontend (single-file `docs/index.html`)

- `TAB_CONTENTS.discover` rewritten — `_renderDiscoverCard`, `_renderTracklist`, `_renderYouTubeCarousel`, `_renderShopRow`, `_renderLabelRow`, `_renderBlockedList`, `_renderResurfaceBadge`, `_renderProgressBar`
- New module-level state: `_discoverState`, `_savedReleases`, `_followedLabels`, `_followedShops`, `_blockedArtists`, `_blockedLabels`, `_currentScan`
- New helpers: `_openReleaseDetailPanel(release_key)`, `_handleShiftClick`, `_setupKeyboardShortcuts`, focus-trap helper
- Reuses: `_consumeSSE`, `_esc`, `AppState`
- `prefers-reduced-motion` gate on slide animations

### Storage

- `discover.db` at platform-native data dir (§6.7)
- Backed up alongside `master.db` via existing `/api/backups` as parallel sidecar
- Schema migrations versioned

### Constraints

- **Hard 60-request cap per scan** (artists=20, labels=15, shops=15, novelty=10)
- **Page-1-only policy**: page 1 only on every feeder call; Tier 2 "Load more" surfaces a per-entity background paginator
- **Per-feeder TTL**: artist=24h, label=24h, shop=6h, novelty=24h (single strategy per scan; full rotation = 72h)
- **Per-scan request accounting**: each Discogs call increments `current_scan.requests_used`; scan refuses to dispatch beyond budget
- **Concurrent-scan lock**: `scans` row with `finished_at IS NULL` blocks new scans; `/feed` returns 409 with running scan_id
- **Token validation**: 1h positive cache; instant invalidation on any 401 from any Discogs API call
- CORS, GZip, file-existence guards unchanged

---

## 9. Security Considerations

- **Local SQLite only** — no PII leaves machine
- **Discogs token** — `.env` only; never sent to client
- **HTML escaping** via existing `_esc()` for all Discogs-supplied strings
- **Path-traversal guards** on `/api/discover/state/import` (validate uploaded file is a valid SQLite header before swapping)
- **CORS lock unchanged** (localhost only)
- **RSS feed parsing** uses `feedparser` with `sanitize_html=True`
- **Bandcamp / homepage probes** use `requests` with 10s timeout, 5MB max body size, `verify=True`, redirects capped at 5
- **iframe sandbox**: YouTube iframe uses `nocookie.com` domain; `sandbox="allow-scripts allow-same-origin allow-presentation"`
- **Rate-limit-respect on probes**: shop validation runs sequentially with 1.5s delay; never bursts more than 5 HTTPS calls in any 10s window
- **Shop probe etiquette**: validator identifies as `AutoCue/1.0 +https://github.com/HenriGeorge/AutoCue` and respects `robots.txt`

---

## 10. Tiered Scope

### Tier 1 — MVP

Critical-path effort: ~50 sequential hours (backend chain ~15h + UI chain ~36h); with 1.5× unknown-unknowns buffer, realistic wall-clock is **6–9 weekends full-time** or **12–18 weeks evenings** (= 3-4 months of evenings for a single engineer).

Includes:
- Per-user taste vector with normalized styles + source filter + plays-weighted ranking
- Artist watch + Label watch feeders + Novelty feeder
- Detail panel with tracklist + inline YouTube preview carousel + explicit downloads + Shift-click power flow + keyboard shortcuts
- Persistent state: saved / dismissed / snoozed / downloaded / followed-labels / blocked-artists / blocked-labels
- Filters & sort (incl. Explore mode)
- Library-suggests-labels onboarding
- Removal of one-click ↓ Album button
- macOS/Linux/Windows data dir + backup integration + Export/Import buttons
- Token validation surfaced in UI
- Concurrent-scan lock + boot-time crash recovery
- Style normalization + JSON schema versioning
- Empty states + error states + scan-progress UI
- Tests (see §12)
- Docs (see §13)

### Tier 1.5 — Shop watch (conditional)

Ships if validation gate passes (≥8 working auto-feed sources). Adds:
- Shop-watch feeder with RSS / Bandcamp / Discogs-seller source-type model
- `validate_pack.py` script + UI
- Shop autocomplete / "add by URL" wizard
- Per-shop health badges

### Tier 2 — Should have

- Expanded style adjacency graph (~150 styles)
- Star rating + notes UI on saved items (schema added via migration when needed)
- "Library suggests these shops" (analyze library labels → which sellers stock them)
- Weekly digest view
- Label-rename watchdog
- Background pagination of top-followed entities ("Load more" / depth)
- Background `/releases/{id}` enrichment of empty-artist releases + post-scan merge using Discogs `master_id` to dedup compilation reissues (resolves the known limitation in §6.3)

### Tier 3 — Stretch

- Friends lane with explicit private-collection UX + chronological ranking
- Bandcamp daily-pull beyond per-label-RSS
- Custom shop URL with full RSS-discovery + generic HTML scrape
- Beatport / Juno integrations (if API access opens)
- Export saved → text/playlist for shop visits
- Auto-pre-download top-N saved overnight
- AI-curated weekly summary

---

## 11. Assumptions

- Discogs API remains accessible at current rate limits (validated 25/min unauthenticated, 60/min documented for authenticated; client reads `x-discogs-ratelimit-remaining`)
- Discogs personal access tokens continue to authorize `/users/{me}/*` and seller inventory reads
- Discogs collections are PRIVATE by default — friends lane (Tier 3) has UX for this
- yt-dlp + ffmpeg remain optional
- Local SQLite (sqlite3 stdlib) suffices for the curation store
- Rekordbox library is stable mid-scan
- Most independent record shops do NOT have active Discogs marketplace storefronts
- YouTube embeds are permitted under standard `nocookie.com` iframe model; per-track preview searches respect rate-limit semaphores
- `pyrekordbox.Rekordbox6Database(path)` resolves SQLCipher key via `deobfuscate(BLOB)` internally — works for arbitrary paths

---

## 12. Dependencies & Testing

### Runtime deps

**New**: `feedparser` (small, BSD-licensed, for RSS parsing). All other deps unchanged from CLAUDE.md.

### Test deps unchanged: `pytest`, `hypothesis`, `vitest`

**Test files added/extended:**

- `tests/test_discover_taste.py` — taste vector math + source-filter behavior + normalization
- `tests/test_discover_style_graph.py` — STYLE_ADJACENCY / STYLE_ALIAS_MAP integrity
- `tests/test_discover_feeders.py` — mocked Discogs + RSS responses; concurrent-scan guard; rate-limit-respect; one-feeder-fails-others-continue
- `tests/test_discover_ranker.py` — Hypothesis properties (score ∈ [0,100]; novelty share when adjacency dense; blocked artist never appears; resurfaced snooze appears once)
- `tests/test_discover_store.py` — CRUD, migration replay from blank, schema_version handling, staging-column commit, boot-recovery
- `tests/test_discover_shop_sources.py` — discogs/rss/bandcamp/manual probe paths
- `tests/test_discover_rss_autodiscover.py` — `<link rel=alternate>` + common-path probes (mocked HTTP)
- `tests/test_discover_validate_pack.py` — validation script integration
- `tests/test_serve_routes.py` — extended; every new endpoint including concurrent-scan 409
- `tests/web/discover.test.js` — `_renderDiscoverCard`, `_renderYouTubeCarousel`, focus-trap on detail panel, keyboard shortcuts, Shift-click flow
- `tests/web/discover_filters.test.js` — filter matrix + sort + Explore-mode toggle
- `tests/e2e/discover.spec.ts` — Playwright smoke: open Discover, scan progress visible, save → reload → saved persists, dismiss → no resurface, snooze → resurface badge on +30d simulated date

---

## 13. Documentation & Telemetry

### Documentation

- **CLAUDE.md** — new section: Discover v2 architecture, platform-native data dir, new `/api/discover/*` endpoints, new test files
- **docs/FEATURES.md** — user-facing copy for follow/preview/persistent-state/Shift-click/keyboard shortcuts
- **docs/reference/discover-v2.md** — long-form: shop source-type model, novelty graph, validation script usage, known limitations

### Telemetry (local only, never exfiltrated)

- `scans` table accrues per-scan rows with `status`, `requests_used`, `releases_seen/dedup/surfaced`, `novelty_strategy`, `novelty_status`, `unknown_styles`
- **Saves-per-scan via timestamp-window correlation**: for each scan, count `saved` rows where `saved.saved_at` falls between `scan.started_at` and `scan.finished_at + 30 minutes`. Tiebreaker for overlapping windows: most-recent scan whose `started_at` precedes `saved_at`. Late saves (>30min) tracked separately as "unattributed"
- `/api/discover/stats` returns roll-ups: scan count, avg duration, saves-per-scan, novelty share breakdown, top labels, top shops
- "Discover stats" surface in Settings shows whether targets in §4 are being hit

---

## 14. Design Decisions

| # | Decision | Rationale |
|---|---|---|
| 1 | Label weighting: `log(1 + plays) × √track_count` | Plays-weighted with depth tiebreaker; fallback to track count for cold-start libraries (no play history yet) |
| 2 | Snooze default: 30 days with 1w / 1m / 3m quick options | 30d is the sweet spot — long enough you've been paid again, short enough you haven't forgotten what you snoozed |
| 3 | Friend lane placement: top horizontal collapsible strip (Tier 3) | Most prominent for the social signal; collapsible so users can hide it |
| 4 | Rescan cadence: per-feeder TTL + manual Refresh + concurrent-scan lock | Predictable budget per session; "Refresh now" button surfaces delta count |
| 5 | Style adjacency graph: JSON resource at user data dir, schema-versioned, bundled-default fallback | Live-tunable; PR-shareable; survives AutoCue version bumps via upgrade functions |
| 6 | Shop probe etiquette: identify in User-Agent, respect robots.txt | Polite citizen of the web |
| 7 | Schema PK: `release_key` (versioned via `release_key_version`) | Format-variant dedup; future normalize changes migrate, not orphan |
| 8 | Backup format: parallel sidecar files | Preserves existing flat-file `/api/backups` contract |
| 9 | Token cache: 1h positive, instant invalidate on 401 | Tight silent-failure window |
| 10 | YouTube preview: lazy on click (pre-warm only track #1) | Avoids per-panel-open yt-dlp storm |
| 11 | Follow-label priority: user-explicit > taste-vector; round-robin when explicit > budget | User's curation respected; fairness guaranteed |
| 12 | Modal default focus: Cancel button on Shift-click download confirm | "Zero accidental downloads" success metric defended |
| 13 | Depth tradeoff: Tier 1 ships page-1-only; "Showing recent 50 — older catalog deferred" chip on cards | Honest UX about what's surfaced |
| 14 | Restore overwrite of `novelty_strategy` rotation | Accepted as cosmetic — next scan picks up restored state |
| 15 | Saves-correlation tiebreaker: most-recent scan whose started_at precedes saved_at; late saves unattributed | Telemetry accuracy without per-scan counter column |

---

## 15. Known Limitations

Explicitly accepted tradeoffs for Tier 1; mitigations deferred to Tier 2:

1. **Compilation-reissue duplication**: empty-artist releases use `release_id` as the dedup discriminator (because Discogs `master_id` is not in `/labels/{id}/releases` or `/artists/{id}/releases` listing endpoint responses — verified by API probe). Different release_ids of the same compilation (1981 original + 2024 reissue) surface as two separate cards rather than deduping to one. Only affects empty-artist releases; named-artist releases dedup correctly. Tier 2 mitigation: background `/releases/{id}` enrichment + post-scan merge using `master_id`.

2. **Page-1-only catalog depth**: an artist with 200+ releases shows only their 50 most recent in the per-scan feed. "Load more" UI hint surfaced. Tier 2 mitigation: background pagination job runs out-of-band of the per-scan budget.

3. **Tier 1.5 quality is gated on first-user validation**: shop-watch's working subset is unknown until the validation script runs against Henri's network. If <8 candidates validate to auto-feed sources, Tier 1.5 doesn't ship and shop-watch defers to Tier 2.

4. **Style adjacency graph is hand-curated**: ~60 styles for Tier 1; sparse-adjacency users (in styles with 0-2 edges OR styles not in the JSON) get pure-retrieval feeds with a one-time UI hint suggesting they propose missing edges on GitHub. Tier 2 expands the graph to ~150 styles.

5. **No automatic multi-machine sync**: Export/Import buttons + Time Machine / Dropbox of the data dir are the manual workflow. Auto-sync not in scope.

6. **Late saves (>30min after scan finish) are not attributed to any scan in telemetry**: surfaced as a separate "unattributed saves" count rather than forced onto an arbitrary scan.

---

## 16. Out of Scope

- Email newsletter parsing (Hard Wax newsletter etc.)
- Generic HTML scrape of arbitrary shop sites (Tier 3 stretch)
- Spotify / Apple Music integration
- Last.fm scrobble import
- Mobile app
- Cross-user real-time sharing of saved lists
- Auto-purchasing / cart integration with shops
- Editorial / human-curated global lanes

---
