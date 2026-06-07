# PRD — Discover Tab v2: Personalized New-Release Discovery & Curation

**Status:** Draft v0.8 (verified-fact reverts after grill round 7 — drops phantom `_key` reference on Rekordbox6Database; reverts to release_id discriminator for compilation dedup after live-API probe confirmed master_id is not in listing endpoints; documents compilation-reissue duplication as known Tier 1 limitation)
**Owner:** Henri George
**Date:** 2026-06-07
**Scope:** Substantive enhancement of the existing Discover tab in AutoCue's local-mode web app

> **v0.7 changelog** — both S6-1 / S6-2 grill findings from round 6 are addressed; see §15 round-6 row. Two small, focused changes vs v0.6:
> - **S6-1**: master.db validator passes the EXPLICIT SQLCipher key from `app.state.db._key` rather than relying on pyrekordbox's path-based auto-detect. BACKUP_DIR is not under the Rekordbox install root, so any path-fingerprint heuristic would fail there. Either `Rekordbox6Database(presnap_path, key=...)` (where supported) or a direct `sqlcipher3.connect()` + `PRAGMA key` + `SELECT 1` works.
> - **S6-2**: compilation `release_key` uses `master_id` as the discriminator (with `release_id` fallback when `master_id` is null). `master_id` is Discogs' canonical "same album across formats" identifier, so the 1981 vinyl and 2024 vinyl-reissue of the same comp share a key (format-variant dedup preserved). Cross-compilation collisions still blocked because different comps have different master_ids. Sentinel prefix uses zero-width-space flanking (`​__compilation__​`) to eliminate the W6-6 literal-band-name collision risk.

> **v0.6 changelog** — every C5-1 / S5-1..5 grill finding from round 5 is addressed; see §15 round-5 row. Major changes vs v0.5:
> - **C5-1 fixed**: restore validator distinguishes by file type — discover.db uses SQLite magic header check, master.db uses `pyrekordbox.Rekordbox6Database(...).open()` + size-match (SQLCipher v4 has no plaintext header)
> - **S5-1**: `downloaded.file_paths` always JSON-encoded list, even for single-file rows — re-normalization merge can `json.loads()` without parse errors
> - **S5-2**: `release_key` computed from RAW artist/title BEFORE coercion — store layer coerces empty/null to "Unknown Artist"/"Unknown Title" only for display columns, never feeds coerced values back into the dedup key
> - **S5-3**: compilation collision fixed — releases with no artist field get `release_key = "_compilation_|||{title}|||rid_{release_id}"`; release_id discriminator prevents unrelated compilations dedup'ing on title alone
> - **S5-4**: first-run UX rewritten — loader silently copies bundled default to user data dir on first run with informational log; warning flag only triggers for malformed/wrong-version user files
> - **S5-5**: T-007 (artist+label feeders), T-014 (orchestrator), T-047 (shop feeder) descriptions updated to explicitly mention `store.mark_*_scanned(...)` writes-to-staging-columns; orchestrator's `commit_pending_scan(scan_id)` is the only path that promotes pending → committed last_scanned_at
> - W5-1..3 cosmetic items deferred (not blocking ship)

> **v0.5 changelog** — every C4-1 / C4-2 / S4-1..5 / W4-1..3 grill finding from round 4 is addressed; see §15 round-4 row. Major changes vs v0.4:
> - **C4-1 killed**: §6.10 ship-gate paragraph rewritten to consistently say "Tier 1.5 ships" (was contradictory "stays in Tier 1" leftover)
> - **C4-2**: `unknown_styles` column added to `scans` table (was referenced but missing from schema)
> - **S4-1**: per-table re-normalization conflict resolution — saved keeps most-recent, dismissed keeps oldest, snoozed keeps max until_date, downloaded MERGES file_paths
> - **S4-2**: partial-state recovery uses `last_scanned_at_pending` + `pending_scan_id` staging columns — no more multi-crash cascading rollback; per-scan_id rollback only
> - **S4-3**: `artist`/`title` are NULLABLE; store layer coerces empty/null to "Unknown Artist"/"Unknown Title" at insert time — supports Discogs releases with missing artist
> - **S4-4**: restore pre-snapshot creation explicitly validated before proceeding; abort with clear error if safety net can't be established
> - **S4-5**: `style_adjacency.json` carries `schema_version` field; AutoCue ships per-version schemas + upgrade functions; user customizations survive AutoCue version bumps
> - **W4-1**: explicit `ORDER BY ... NULLS FIRST` in label-priority logic (PostgreSQL-portability)
> - **W4-2**: §10 effort estimate notes 1.5× unknown-unknowns buffer for realistic wall-clock
> - **W4-3**: §6.7 backup section notes startup cleanup failure degrades gracefully (retry on next boot)

> **v0.4 changelog** — every S3-1..9 / W3-1..5 grill finding from round 3 is addressed; see §15 round-3 row. Major changes vs v0.3:
> - **Cross-PRD consistency scrub**: §5 (Bandcamp tier label), §6.10 (v0.2 framing residue), §7 Flow B (Tier-1.5 conditional marker), §10 Tier 2 (removed novelty self-contradiction)
> - **Follow-label priority** (S3-3): user-explicit follows always take 15 slots first; taste-vector implicit follows fill remaining
> - **Critical-path expanded** (S3-4): UI work serializes through `docs/index.html` — true sequential chain is ~50h, wall-clock 6–9 weekends full-time or 12–18 weeks evenings
> - **release_key versioned** (S3-5): `release_key_version` column + re-normalization migration when version bumps; no silent orphans
> - **Backup atomicity** (S3-6): two-phase delete/restore with temp-file + rename; rollback path on partial failure
> - **Partial-scan TTL rollback** (S3-7): crash recovery also resets `last_scanned_at` for entities touched by the crashed scan
> - **style_adjacency.json validation** (S3-8): strict schema validation on load; fail-fast with bundled-default fallback + UI warning
> - **Unknown-style handling** (S3-9): `STYLE_ADJACENCY.get(style, [])` semantics; absent style counts as sparse_adjacency
> - W3-1..5 addressed inline (token-cache architectural note, saves-correlation tiebreaker, restore rotation-pointer note, depth tradeoff UI affordance, modal-default decision documented)

> **v0.3 changelog** — every C-NEW-1 / C-NEW-2 / S-NEW-1..9 / W-NEW-1..8 grill finding from round 2 is addressed below; see §15 (Resolved Issues Log) for the cross-reference. Major changes vs v0.2:
> - Reconciled rate-limit budget math: hard cap **60 requests per first scan** (= ≤60s at authenticated 60/min); strict "1 page per entity, freshness over depth" policy; deep pagination is a Tier 2 background job.
> - §1 exec summary no longer narrates shop-watch as delivered — moved to "may include" wording matching its Tier 1.5 gate.
> - Novelty cold-start hardened: adjacency-coverage criterion + explicit empty-novelty-pool behavior + per-style minimum-edge target.
> - Effort estimate restated as **5–8 weekends part-time** based on critical-path analysis, not 2.5-3.
> - Backup format spelled out (parallel sidecar file, no tarball, no break in existing flat-file pattern).
> - YouTube preview is now lazy on click (no per-panel-open search storm).
> - `release_key` is the source-of-truth dedup key everywhere — saved/dismissed/snoozed lookups go through it.
> - Token validation: 1h positive cache, **instant negative invalidation on any 401**.
> - Scan lock has boot-time cleanup; orphan locks can't strand the user.
> - `scans.saves_during_session` replaced with timestamp-window correlation that AutoCue's no-session model actually supports.
> - Novelty budget right-sized (40 requests + strategy rotation), or scoped to one strategy per scan to fit a smaller budget.

> **v0.2 changelog** (preserved for history) — every C1–C5 / S1–S7 / W1–W8 grill finding from round 1 is addressed below; see §15. Major changes vs v0.1:
> - Renamed conceptually to **"Discover & Watchlist"** to be honest about retrieval-plus-curation vs. true exploratory discovery.
> - **Shop-watch architecture pivoted**: RSS / Bandcamp / homepage are the primary sources; Discogs-seller is a rare bonus, not the spine. Driven by real Discogs probes showing real shops mostly don't sell through Discogs marketplace.
> - **Ranker gains explicit `novelty` term**; ~25% of feed slots reserved for adjacent-cluster exploration.
> - **Friend lane moved to Tier 3** until Discogs-collection privacy reality is acknowledged in UX.
> - **Per-feeder TTLs**, scan-in-progress lock, delta counts on Refresh.
> - **macOS-native data dir** (`~/Library/Application Support/AutoCue/`), discover.db integrated with existing `/api/backups`.
> - **Tier-1 re-scoped honestly**: shop-watch demoted to Tier 1.5 if validation yields <15 working sources; effort estimate now ~97h (derived from per-task hours), not the optimistic "40-60h" of v0.1.

---

## 1. Executive Summary

The current Discover tab surfaces new releases from a Rekordbox library's most-played artists via the Discogs API. It works, but feels narrow, can't be previewed before downloading, downloads with one click (accident risk), and forgets state on reload.

**Discover & Watchlist v2 (Tier 1)** rebuilds the feed around a **per-user taste vector** derived from the local Rekordbox library, adds **label watch** (the biggest current gap), introduces an explicit **`novelty` term** in the ranker so the feed surfaces adjacent finds and not just echoes of the library, adds **inline YouTube preview**, replaces the one-click download with a **detail panel + explicit downloads** (with a power-user Shift-click bypass), and persists state in a local SQLite store integrated with AutoCue's existing backup infrastructure.

**Tier 1.5 may add shop watch** built on **RSS / Bandcamp / homepage feeds**, gated on a one-time validation script discovering ≥ 8 working sources across the candidate shop list. If the gate isn't met, shop-watch is deferred to Tier 2 and Tier 1 ships without it — see §10 for the gate definition and §6.10 for the candidate list. A **friends lane** is deferred to Tier 3 until Discogs-collection privacy defaults are addressed with explicit UX.

Every install computes its own feed from its own data — no shared curation. Friends with different libraries see different feeds from the same codebase.

---

## 2. Problem Statement

### Current pain points (verbatim user feedback + grill findings)

1. **Too narrow** — only top-played artists; misses labels, related artists, genre/style angles
2. **Poor signal** — feels random or stale; no taste-aware ranking, no exploration
3. **Weak filtering & sorting** — can't slice by genre, format, year
4. **Limited sources** — Discogs only; user wants record-shop sources (Hard Wax-style shops)
5. **No preview** — can't hear a release before deciding
6. **Accidental downloads** — clicking "Album" downloads immediately
7. **No memory** — saves/dismisses don't survive reload
8. **No novelty** *(grill C4)* — current model is similarity-to-library; will never surface adjacent finds
9. **Concurrent-scan races, empty states, expired-token silent failures** *(grill S1)* — current UI hides errors
10. **No durability story for user curation** *(grill S2)* — saved chips live in JS memory

### Why now

Closing these gaps turns Discover from a novelty into a daily-driver tool — preview, wishlist, watch specific labels and shops, share finds with friends.

---

## 3. Target Audience

- **Primary**: Henri — DJ with Rekordbox 7 library; uses Discogs for metadata
- **Secondary**: His DJ friends — also Rekordbox + Discogs users; different musical taste; their own Discogs accounts

### Assumptions made explicit

- Rekordbox 7 library exists locally
- Discogs personal access token in `.env` (already required by current Discover)
- yt-dlp + ffmpeg are **optional** — preview works without them via YouTube iframe embed
- Local-mode server (`autocue serve`) is running
- **Discogs collections are PRIVATE BY DEFAULT** (corrected in v0.2 — see grill C2 fix in §6.4)
- Most independent record shops do NOT have active Discogs marketplace storefronts (verified via API probe — see §15 #C3)

---

## 4. Success Metrics (measurable + reconciled with rate-limit reality — grill C-NEW-1)

This is a personal/small-circle tool, not a SaaS — but every metric has a measurement plan in §13. Targets are now derived from the hard 60-request-per-scan budget (§8 Constraints), not aspirational numbers.

| Metric | Target | How measured |
|---|---|---|
| Saves per scan (vs current baseline) | ≥ 3× | `discover.db.saved` row count delta correlated to `scan_id` via timestamp window |
| Saved → downloaded within 7 days | ≥ 40% | join `saved` × `downloaded` on `release_key`; window by saved_at |
| Dismissed re-appearance rate | 0% | feeder integration test asserts dismissed never resurface |
| Users add ≥1 followed label first session | qualitative | manual check after Henri + 2 friends try it |
| Users add ≥1 followed shop first session (if Tier 1.5 ships) | qualitative | manual check; n/a if shop-watch gate fails |
| **First** scan completion (cold cache, ≤20 followed labels + ≤8 followed shops) | ≤ **75s** typical | scan duration logged in `scans` table; surfaced in UI |
| Cold scan at high config (≤20 labels + ≤20 shops) | ≤ **180s** | same; see "depth tradeoff" note below |
| **Subsequent** scan (warm cache; most TTLs fresh) | ≤ 30s | same |
| Accidental download events | 0 | only Download buttons inside detail panel can fire `/api/download`; Shift-click confirm modal defaults to Cancel (grill W-NEW-1) |
| Novelty-source picks per feed (when adjacency coverage ≥ 3 edges) | ≥ 20% | feeds with `source.startswith('novelty:')` counted; conditional metric — n/a when coverage insufficient (see §6.2 Feeder 4) |
| Discogs rate-limit hits per scan | 0 | wrapped client backs off when `x-discogs-ratelimit-remaining < 5`; scan declares clean completion or surfaces a hard error |

**Budget reconciliation (was the C-NEW-1 grill miss):**

The first-scan hard cap is **60 requests** (= ≤60s at authenticated 60/min, ≤145s at unauthenticated 25/min). Budget allocation:

| Feeder | Hard budget per scan | Coverage at full budget |
|---|---|---|
| Artist watch | 20 requests | Top-20 artists, **page 1 only** (50 releases per artist) |
| Label watch | 15 requests | Top-15 labels, page 1 only |
| Shop watch | 15 requests | Up to 15 followed shops, page 1 only (skipped if Tier 1.5 not shipping) |
| Novelty | 10 requests | One strategy per scan, rotated across scans (style→label→artist) — see §6.2 Feeder 4 |
| **Total** | **60** | First scan ≤60s authenticated |

**Depth tradeoff (explicit in v0.3):** With page-1-only, an artist with 200 releases shows only their 50 most recent. Going deeper requires Tier 2 background pagination (a daily job that pages through the top-5 followed entities and stores into `release_details`). Users who need depth get it eventually; first-scan UX optimizes for freshness, not exhaustiveness.

Original v0.1 target of "<30s for top-50 artists + 10 labels + 5 shops" was impossible at Discogs' 25–60 req/min cap. v0.2's "≤180s for top-50 artists + 10 labels + 5 shops" was also wrong because of pagination + novelty cost. v0.3's numbers are derived from a fixed budget, not from feeder design.

---

## 5. Competitive Landscape

| Product | What it does | What v2 takes / leaves |
|---|---|---|
| **Bandcamp follows / new arrivals** | Per-artist/label follow with RSS | **Take: Bandcamp RSS is a Tier-1.5 candidate source** (gated on shop-watch validation; promoted from Tier 3 in v0.1 to candidate source in v0.3) |
| **Beatport "My Beatport"** | DJ-focused new-release feed scoped to followed artists/labels/genres | Take: label-watch model. Leave: requires Beatport account |
| **Discogs `newreleases.discogs.com/for-me`** | Personalized weekly feed based on logged-in user's collection + follows | **Cannot consume — confirmed 403 to unauthenticated requests; no API equivalent. We must build our own personalization.** |
| **Spotify Release Radar** | Algorithmic weekly playlist | Take: weekly cadence. Leave: streaming-only |
| **Hard Wax / Boomkat newsletters** | Curated weekly emails | Take: shop-as-source. Leave: email parsing infra |

**Differentiator**: AutoCue Discover v2 is the only feed personalized by **your own local Rekordbox play data**, runs **fully local** (no third-party server holds your taste), and explicitly mixes **exploration with retrieval** (novelty term, not echo chamber).

---

## 6. Core Features

### 6.1 Taste Vector (per user, library-derived)

Computed from the local Rekordbox database. No Discogs collection sync required.

**Inputs:**
- **Artists** — weighted by **`log(1 + play_count)`** (so a single mega-played track doesn't dominate); fallback to track count when play history is empty
- **Labels** — weighted by **`log(1 + total_label_plays) × √track_count`** (resolved §14 #1: plays-weighted with depth tiebreaker; fallback to track count for cold-start libraries)
- **Styles** — **normalized**: lowercase, strip non-alphanumerics, apply `STYLE_ALIAS_MAP` (e.g. `deep-house` / `Deep House` / `deephouse` → `deep_house`). Inputs: `DjmdContent.GenreName`, My Tags (only AutoCue-namespaced tags from the My-Tag allowlist — user-created tags excluded by default to avoid taxonomy noise), enriched comments (parsed back from MIK format)
- **BPM histogram** — buckets of 4 BPM, range 60–200; tracks with BPM=0 (per CLAUDE.md guard) excluded
- **Key histogram** — Camelot distribution; tracks with no Key skipped
- **Source filter** *(grill S5)*: only `source == "file"` Rekordbox tracks contribute. Streaming-source tracks (`spotify:`, `tidal:`, etc.) and tracks with `_audioProbedAt[id] === "missing"` are excluded from the taste vector — they bias the model toward stuff the user never actually plays from local audio.

**Feedback signals:**
- `saved` items contribute positively (weight 1.0)
- `dismissed` items contribute negatively (weight -1.5)
- `dismissed_artist` / `dismissed_label` are *first-class blocks* (grill W1 fix) — zero out the relevant taste-vector entry, not just penalize
- `downloaded` items contribute positively (weight 0.5) AND exclude that release from future surfacing

**Persistence:** taste vector recomputed lazily on Discover open; cached in-memory for the session; invalidated on any feedback action so the next scan reflects it.

### 6.2 Candidate Pool (four feeders, deduped + ranked)

All feeders respect the hard per-scan budget from §4 ("Budget reconciliation"). Page 1 only on first scan; deeper pagination is a Tier 2 background job. Each feeder writes `last_scanned_at` on the relevant entity (`followed_labels`, `followed_shops`, or per-artist cache).

**Feeder 1 — Artist watch**
- Top-**20** artists from taste vector (was N=30 in v0.2 — reduced to fit budget)
- `GET /artists/{id}/releases?sort=year&sort_order=desc&page=1&per_page=50` — **page 1 only**
- Filter to releases in the last 90 days
- Per-artist budget: **1 request**; total feeder budget: 20 requests
- TTL: 24h (artists release rarely)
- "Load more" in UI → triggers Tier 2 background pagination for a chosen artist; not counted against per-scan budget

**Feeder 2 — Label watch (NEW — biggest current gap; v0.4 follow-priority — grill S3-3 + v0.5 W4-1 NULLS FIRST)**
- Up to **15** labels per scan; selection respects an **explicit priority order**:
  1. **User-explicit follows take precedence**: every label in `followed_labels` that is over its TTL (24h) gets a slot first, in `added_at` order
  2. **Taste-vector implicit follows fill remaining slots**: if the user has < 15 explicit follows, the top-N labels from `taste_vector.labels` (by `log(1 + plays) × √track_count` score) fill the gap — excluding any already in the explicit list to avoid double-scanning
  3. **Fairness when explicit follows > 15**: round-robin per-scan slot allocation — every explicit follow is guaranteed to be scanned at least once per `ceil(N_explicit / 15)` scans. Achieved by `ORDER BY last_scanned_at ASC NULLS FIRST` (W4-1 — explicit NULL ordering since SQLite default is NULLs first but PostgreSQL default is NULLs last; the explicit clause keeps behavior consistent if AutoCue ever migrates DB backend). Newly-followed labels (last_scanned_at IS NULL) get scanned first, then by ascending last_scanned_at.
- `GET /labels/{id}/releases?page=1&per_page=50` — page 1 only
- Filter to last 90 days
- Per-label budget: **1 request**; total feeder budget: 15 requests
- TTL: 24h
- Tier 2 background pagination on a label resolves to the same `release_details` cache

**Feeder 3 — Shop watch (NEW — REARCHITECTED in v0.2)**

  Discogs-seller-as-primary-source was wrong (grill C3 + §15 validation): probing 18 candidate shop handles showed all 6 that exist on Discogs return `num_for_sale=0` — real indie shops don't sell through Discogs marketplace. New architecture uses **a typed-source model**:

  ```python
  ShopSource = OneOf[
    DiscogsSeller(handle, last_scanned, num_for_sale_threshold=5),  # only used when num_for_sale>=5
    RssFeed(feed_url, last_scanned),                                  # primary path for most shops
    BandcampLabel(label_handle, last_scanned),                        # well-supported public RSS
    ManualLink(homepage_url),                                         # bookmark-only — no auto feed
  ]
  ```

  Per-shop probe sequence at follow-time:
  1. If a Discogs seller handle is supplied AND `GET /users/{handle}` shows `num_for_sale >= 5` → `DiscogsSeller`
  2. Else fetch `homepage_url`, look for `<link rel="alternate" type="application/rss+xml">` → `RssFeed`
  3. Else if homepage hosts a Bandcamp embed or handle (e.g. `<shop>.bandcamp.com`) → `BandcampLabel`
  4. Else common-path probes: `/feed`, `/rss`, `/atom.xml`, `/blogs/news.atom` (Shopify default) → `RssFeed`
  5. Else fall back to `ManualLink` — surface in UI as "🔗 Visit shop" only, no auto-feed

  TTL: 6h for `DiscogsSeller`/`RssFeed`/`BandcampLabel`; never auto-fetch `ManualLink` shops.

  This is no longer a uniform "ship the 26-shop starter pack" promise — see §6.7 for the validated subset and the candidate list.

**Feeder 4 — Novelty (NEW — grill C4 fix; hardened for cold-start in v0.3 per S-NEW-1 + S-NEW-9)**

  Reserves up to 25% of final feed slots for releases that are *adjacent but not literal matches* to the taste vector — the difference between "discovery" and "retrieval". To fit the §4 budget (10 requests/scan), only **one of three strategies runs per scan**, rotated round-robin so all three eventually surface (state stored in `discover.db.scans.novelty_strategy`):

  - **Style-adjacent** (rotated): for each top-3 style in taste vector, fetch one Discogs search query per adjacent style (capped at 3 styles × 3 adjacents = 9 candidate-style queries; total ≤ 10 requests). Adjacency from `STYLE_ADJACENCY` (loaded from `autocue/analysis/discover/style_adjacency.json` — JSON resource per W-NEW-4, live-tunable without rebuild).
  - **Label-adjacent** (rotated): for top-5 followed labels, resolve `parent_label_id` and `sub_labels` via Discogs label endpoint, then fetch recent releases from up to 5 adjacent labels (≤10 requests total).
  - **Artist-adjacent** (rotated): for top-5 artists by taste-vector weight, fetch their `members`/`groups` Discogs relations and pull recent releases from up to 5 unique adjacent artists (≤10 requests).

  **Cold-start hardening (grill S-NEW-1 + S3-8 + S3-9):**

  STYLE_ADJACENCY ships with **two-tier coverage**:
  - **Anchor styles** (~30): each has ≥ 3 adjacency edges. Includes core DJ genres: deep_house, tech_house, techno, drum_and_bass, footwork, jungle, dubstep, garage_uk, ambient, idm, hip_hop, dancehall, afrobeat, jazz_modern, leftfield, electro, breakbeat, etc.
  - **Terminal styles** (~30): styles with 0–2 edges (e.g. very niche subgenres). Explicitly marked `"terminal": true` in the JSON.

  **JSON loading + validation + versioning (grill S3-8 + v0.5 S4-5):** `style_adjacency.json` carries a top-level `"schema_version": N` field. AutoCue ships matching schema files at `autocue/analysis/discover/schemas/style_adjacency.v{N}.schema.json` plus upgrade functions `style_graph.upgrade_v{N}_to_v{N+1}(user_json)` that add reasonable defaults for newly-required fields.

  Loading sequence at server startup (v0.6 first-run UX fix — S5-4):
  0. **File location**: `style_adjacency.json` lives in the user data dir alongside `discover.db` (e.g. `~/Library/Application Support/AutoCue/style_adjacency.json` on macOS). NOT inside the Python package — package files are read-only to most users.
  1. **First-run path** (file does not exist + no prior `style_adjacency.json.bak` next to it): silently copy the bundled-default to the user data-dir path. Log: *"Created style_adjacency.json from bundled default at {path} — edit to customize the novelty graph."* — **NO warning flag, NO UI banner.** This is the normal first-run state.
  2. **Subsequent-run path** (file exists): read it. If malformed JSON → fall back to bundled default + set warning flag + preserve user file as `.bak`.
  3. Read top-level `schema_version` (default `1` if absent); if > current AutoCue version → log "JSON is from a newer AutoCue (vN) — using bundled defaults until you downgrade or upgrade AutoCue" + warning flag
  4. If `schema_version` < current → run sequential `upgrade_v1_to_v2()`, `upgrade_v2_to_v3()`, etc., chaining until at current; validate the result against current schema. On any upgrade-fn failure → fall back to bundled default + warning flag with message *"Could not migrate your style_adjacency.json from v{old} to v{new}. Using bundled defaults — your custom file is preserved at `style_adjacency.json.bak` so you can re-apply edits manually."*
  5. Validate the (possibly-upgraded) JSON against current `schema.v{current}.json`; on violation → fall back to default + warning flag, preserving user file as `.bak`
  6. Set a flag readable via `GET /api/discover/stats` so the UI can surface the warning (warning flag is NOT set on the first-run silent-copy path per step 1): *"Your style_adjacency.json has issues — using bundled defaults. [Details: ...]"*

  This makes future AutoCue version bumps non-destructive to user customizations (S4-5 fix): user edits survive across upgrades unless the user-edited file is internally invalid. Server never crashes on bad JSON; Discover always works at minimum default-quality.

  **Unknown-style handling (grill S3-9):** style lookups use `STYLE_ADJACENCY.get(style, [])` semantics — a style not present in the JSON returns an empty adjacency list, not a KeyError. Effects:
  - User's library contains an obscure or new style not in the JSON → that style contributes ZERO adjacency edges to the novelty pool for the current strategy
  - When a user's top-3 styles are ALL absent from the JSON (or all marked terminal), feeder behavior is identical to the all-terminal case below: `novelty_status = 'sparse_adjacency'`
  - Logged once per scan: `discover.db.scans.novelty_status` includes a sub-flag `unknown_styles=[...]` when any of the user's top-3 styles missed the JSON. UI's sparse-adjacency hint adds: *"(Styles unknown to graph: {list}. [Suggest these on GitHub])"*

  Feeder 4 behavior when a user's top-3 styles are all terminal OR all unknown (sparse novelty pool):
  1. Logs `novelty_status = 'sparse_adjacency'` on the scan row
  2. Skips the 25% reservation in stage 2 — feed assembles as top-50 by base score, no empty slots
  3. UI surfaces a one-time hint per above
  4. The §4 "Novelty-source picks ≥ 20%" metric becomes **conditional** (only measured when `novelty_status = 'ok'`)

  Novelty candidates are tagged `source=novelty:{strategy}` and **bypass the artist/label-match penalty** in the ranker. Stage 2 reservation runs only when the pool has at least `(0.25 × top_n)` candidates available; otherwise reservation is reduced or skipped — see §6.3.

  TTL: 24h. Per-scan budget: **10 requests** (one strategy only).

**Friend feeder (was Feeder 4 in v0.1)** — **moved to Tier 3** in v0.2. See §6.4 / §10.

**Dedup**: by **normalized `release_key`** (grill S6 + S-NEW-5 fix) — NOT by Discogs release ID. The dedup key is `release_key = f"{artist_normalized}|||{title_normalized}"`, where both sides are lowercased, stripped, and folded via `unicodedata.NFKD`. A release matching `library_album_set()` is excluded; multiple Discogs releases of the same album merge to one feed card with a "available in: vinyl, CD, digital" chip.

**release_key is the source-of-truth for all curation state (grill S-NEW-5 fix):** all CRUD operations on `saved` / `dismissed` / `snoozed` / `downloaded` look up state by `release_key`, NOT by `release_id`. The 2024 vinyl reissue of an album you saved in 2022 reads as already-saved because they share `release_key`. Schema enforces `UNIQUE(release_key)` on each table — see §6.7.

**Already-owned filter**: existing `library_album_set()` (the lowercased `(artist|||title)` set from CLAUDE.md) shares the same normalization. Per-line normalization is implemented in `autocue.analysis.discover.taste.normalize_release_key()` and reused on both sides for consistency.

### 6.3 Ranking

Two-stage scoring:

**Stage 1 — base score (in [0, 100]):**

```
base(release) =
   0.28 * artist_match     (cosine sim of release artists vs taste_vector.artists)
 + 0.22 * label_match      (1.0 if label ∈ taste_vector.top_labels, else fuzzy)
 + 0.18 * style_match      (Jaccard overlap of normalized release.styles vs taste_vector.styles)
 + 0.08 * bpm_fit          (bucket overlap; or 0.5 when neither side has BPM data — grill: explicit "no data" handling)
 + 0.10 * recency          (linear decay over 90 days; 1.0 today → 0.0 at 90d)
 + 0.05 * source_diversity (small bonus if release surfaces from a feeder type not yet over-represented in top-50)
 + 0.09 * cohort_freshness (bonus for releases by artists/labels not already heavily represented in the current feed top-50)
 - hard_block if artist ∈ blocked_artists OR label ∈ blocked_labels  (W1 fix)
```

(Note: weights renormalized to sum to 1.00 after adding `cohort_freshness` for diversity.)

**Stage 2 — novelty reservation (grill C4 + S-NEW-1 conditional fix):**

After stage-1 scoring, the feed is assembled as:

```
novelty_quota = floor(top_n * 0.25)
novelty_pool  = releases where source.startswith('novelty:')
                   sorted by base_score desc
retrieval_pool = remaining releases sorted by base_score desc

if len(novelty_pool) >= novelty_quota:
    feed = retrieval_pool[:top_n - novelty_quota] + novelty_pool[:novelty_quota]
elif len(novelty_pool) > 0:
    # Partial pool — surface what we have, backfill rest from retrieval
    feed = retrieval_pool[:top_n - len(novelty_pool)] + novelty_pool
    scan_status['novelty_partial'] = len(novelty_pool)
else:
    # Empty pool (sparse adjacency or zero results) — pure retrieval
    feed = retrieval_pool[:top_n]
    scan_status['novelty_status'] = 'sparse_adjacency'
    # UI surfaces sparse-adjacency hint per §6.2 Feeder 4
```

This guarantees the 25% share **when novelty data exists**, surfaces partial when it's thin, and degrades to pure retrieval (no empty slots, no garbage backfill) when the user's taste profile sits in terminal styles.

**Sort options exposed to user**: `Taste match` (default — runs the two-stage), `Newest`, `Title`, `Artist`, `Explore mode` (flips ratio to 50/50 retrieval/novelty for the "show me something new" mood).

Top-50 surfaced by default; "Load more" pages 25 at a time.

### 6.4 Friend Lane — moved to Tier 3 (grill C2 + C5)

Was Tier 2 in v0.1. Demoted because:

1. **Discogs collections are private by default** — most friends would need to actively change their settings before the lane shows anything. The PRD's v0.1 silent failure was a serious UX hole.
2. **Ranking-by-local-taste-vector defeats the social premise** — if friends had identical taste, their adds would be redundant; if differing, they'd score ~0 and the lane would be empty.

**Tier 3 design (when it ships):**
- Pre-flight check on friend add: `GET /users/{friend}/collection/folders/0` — if 401/403/empty, show explicit UI: *"{friend}'s Discogs collection is private. Ask them to: Discogs → Settings → Privacy → 'Show my collection' toggle, then save folder 0 as public."* with a "Copy message to send them" button.
- Ranking: **chronological** by friend's add date (not local taste vector). Optional "Match mine" sort toggle.
- Each card shows two scores: the friend's add recency and a small "matches your taste 87%" chip — visible signal, no filtering.

For Tier 1 / 1.5, no friend feature ships. The §1 executive summary is updated accordingly (grill W6 fix).

### 6.5 UI: Detail Panel (replaces one-click download)

**Card click** → right-side slide-out panel:

- Larger artwork, full title, artist, label, year, format chips, country
- Tracklist (fetched lazily from `GET /releases/{id}` — cached in `release_details` SQLite table; TTL 30 days)
- Per-track ▶ — inline YouTube preview (see §6.6)
- **Download all** button (disabled with tooltip if yt-dlp+ffmpeg missing)
- **Download selected** button (per-track checkboxes)
- 💚 Save / 💤 Snooze (with quick options: 1w / 1m / 3m — default 1m per §14 #3) / ✕ Dismiss
- 🚫 Block artist / 🚫 Block label (grill W1) — under a "More" menu
- External links: Discogs (always), Bandcamp (only if a URL literally containing `bandcamp.com` is found in the Discogs release `videos` URLs or `notes` field — heuristic match, not guessed; per W5 + W-NEW-6, this appears on a minority of releases and the UI simply omits the Bandcamp link when none is found). YouTube search link (always) as a useful generic fallback.
- Source breakdown ("Found via: label Stones Throw + shop Hard Wax + novelty: adjacent style")
- "🔁 Snoozed — resurfaced from snooze on {date}" badge if applicable (grill W4 fix)

**Power-user shortcuts (grill S3 + W-NEW-1):**
- `Shift+click` on card → bypass panel, jump straight to a confirmation modal. **Modal default focus = Cancel button**, not Download (grill W-NEW-1 fix — prevents sticky-Shift + accidental-Enter producing an unintended download). User must explicitly Tab or mouse-click to Download.
- Keyboard: `j` / `k` navigate feed; `Enter` open panel; `s` save; `x` dismiss; `z` snooze; `D` download all (uppercase intentional — case-sensitive to prevent accidental d-press fires download). `?` shows shortcut help modal.
- "Quick download" button on every saved-list row (intent already confirmed — single-click no modal)
- "Download all saved" batch action in the Saved tab

**Accessibility:**
- Detail panel uses dialog semantics: `role="dialog"`, `aria-modal="true"`, focus trap, `Esc` to close, focus return to triggering card on close
- All buttons have visible focus rings and keyboard activation
- Tracklist preview iframes use `title` attribute with release/track context

**Mobile / narrow screen:**
- < 900px viewport: detail panel renders **full-screen** instead of side-by-side; closeable via back gesture or `Esc`
- Filter chips collapse into an overflow `≡ Filter` button

**No download fires until the user clicks an explicit Download button** (panel or Shift-click confirm modal).

### 6.6 Inline YouTube Preview (lazy — grill S-NEW-4 fix)

- Backend: `/api/youtube/search` (existing; bounded by `_yt_search_semaphore`)
- Frontend: iframe embed at `https://www.youtube-nocookie.com/embed/{videoId}?autoplay=0&modestbranding=1` (nocookie domain — better privacy + works in restrictive iframe contexts)
- **Lazy search policy (grill S-NEW-4):**
  - On panel open: pre-warm YouTube search for **track #1 only**. Other tracks render their ▶ button with a "Find" hint state — search fires only when the user clicks that track's ▶.
  - Eliminates the "12-tracks × 2-concurrent = 30-60s background storm" failure mode where opening a panel just to read the tracklist triggered a yt-dlp burst that could trip YouTube bot detection.
  - User sees instant track-1 preview, on-demand previews elsewhere — matches the actual use case (browse tracklist → maybe preview a couple of standout tracks).
- **Show 3 candidate results in a small carousel** per played track (grill S4). User clicks any to play; default the first. "Try another" button cycles to next candidate.
- **Disk-cached** search results in `discover.db.youtube_results` table keyed by `(release_key, track_index)` — note `release_key` not `release_id` (grill S-NEW-5 consistency).
- **Inline error recovery**: if the iframe fires `onerror` (video removed / region-locked), auto-advance to next candidate; if none left, show "Preview unavailable — [Search YouTube](url) / [Search Bandcamp](url)".
- iframe `allow="autoplay; encrypted-media"`; `sandbox="allow-scripts allow-same-origin allow-presentation"`.
- **No autoplay on load** (cross-origin autoplay-with-sound is browser-blocked) — user clicks the iframe's own play button.
- Per-track YouTube search bounded by existing `_yt_search_semaphore` (max 2 concurrent) — but lazy policy means rarely saturated.

### 6.7 Persistent State (durable — v0.3 hardened)

**Location** *(grill S2)*: platform-native data directory:
- **macOS**: `~/Library/Application Support/AutoCue/discover.db`
- **Linux**: `${XDG_DATA_HOME:-~/.local/share}/autocue/discover.db`
- **Windows**: `%APPDATA%\AutoCue\discover.db`

Reachable via `autocue.serve.deps.discover_data_dir()` helper.

**Legacy-path migration removed (grill W-NEW-7 fix):** v0.2 documented a migration from `~/.autocue/discover.db`. Since v2 is the first version creating this file, no such legacy exists. The migration code is removed. If a future iteration introduces a path change, that iteration adds its own one-shot migrator.

**Backup integration** *(grill S2 + S-NEW-3 fix — parallel sidecar, not tarball)*:

The existing AutoCue backup pattern (per CLAUDE.md) is a bare copy of `master.db` at `BACKUP_DIR/master_<timestamp>.db`. v0.3 preserves this contract: `discover.db` rides as a **parallel sidecar file** at `BACKUP_DIR/discover_<timestamp>.db` using the **same timestamp** as the corresponding master backup. Backup list endpoints return a unified record per timestamp with two file paths.

- Old backups (master.db only, pre-v2) read fine — sidecar `discover_*.db` is just absent → restore restores master only and logs "no discover.db sidecar — leaving current curation state intact"
- New backups capture both
- **Atomicity (grill S3-6):** create + delete + restore are two-phase with rollback. Filesystem operations are NOT inherently atomic, so v0.4 wraps them in a temp-file + rename pattern:
  - **Create**: write `master_<ts>.db.tmp` + `discover_<ts>.db.tmp` to BACKUP_DIR, fsync both, then `os.rename` both. If first rename succeeds but second fails, immediately delete the first to leave no half-backup behind.
  - **Delete**: rename both files to `*.deleting` then unlink each. If a step fails between, the `.deleting` suffix is recognized by a startup cleanup pass that finishes the unlink.
  - **Restore (v0.6 C5-1 fix — per-db validation, SQLCipher-aware):** Rekordbox 6/7 `master.db` is SQLCipher-v4 encrypted from byte 0 — it has NO plaintext SQLite magic header. v0.5's universal header check would have failed on every real user's master.db. v0.6 distinguishes validators by file type:
    1. **Pre-snapshot phase**: copy current master.db + discover.db to `BACKUP_DIR/__presnap_<ts>.db` (two files). Validate each:
       - **discover.db**: SQLite magic header check (`bytes[:16] == b'SQLite format 3\\x00'`) — AutoCue owns this file, never encrypted; header must match.
       - **master.db (v0.8 — verified fix using pyrekordbox's internal key resolution)**: (a) file size matches source within one 4096-byte page (covers SQLCipher's page-aligned writes); (b) call `pyrekordbox.Rekordbox6Database(presnap_path)` with NO `key=` kwarg — pyrekordbox internally resolves the SQLCipher key via `deobfuscate(BLOB)` from a bundled obfuscated blob (verified by reading pyrekordbox source: `Rekordbox6Database.__init__` does `if not key: key = deobfuscate(BLOB)`). This works for ANY path including `BACKUP_DIR/__presnap_<ts>.db` because the key resolution is install-level, not path-dependent. The v0.7 attempt to pass `app.state.db._key` was incorrect: `Rekordbox6Database` consumes its `key=` parameter into the SQLAlchemy URL and never stores it as an instance attribute — `_key` does not exist. Validation succeeds when the constructor returns without raising; failure (corrupt snapshot, wrong key, schema mismatch) raises a clear exception.
       If either validation fails, abort restore with explicit error: *"Cannot create safety snapshot before restore (disk full, permission denied, or master.db could not be re-opened by pyrekordbox). Free space and retry."* — leaves user state untouched.
    2. **Copy phase**: copy backup `master_<ts>.db` + `discover_<ts>.db` to current data-dir paths under `.tmp` names; verify each by its appropriate validator (discover.db via header; master.db via pyrekordbox-open + size match).
    3. **Atomic rename phase**: rename both `.tmp` → final paths in sequence. If second rename fails (or master succeeded but discover didn't), use the pre-snapshot to roll back master to its pre-restore state.
    4. **Cleanup phase**: delete pre-snapshot files on success.
    Net effect: either both swap in, or neither does — with explicit failure messaging when the safety net itself can't be established. **The v0.5 spec's universal SQLite-magic-header check was incorrect for SQLCipher-encrypted master.db (C5-1); v0.6 uses pyrekordbox-can-open as the master.db integrity check.**
- Startup cleanup pass: on `DiscoverStore` and master-DB open, scan BACKUP_DIR for `*.tmp` or `*.deleting` files and remove (assumed orphaned from a crash). **Cleanup failures are non-fatal (W4-3)** — if the unlink itself fails (permission, IO), the orphan is logged and left in place; next startup retries. Backup creation also checks for and skips `*.tmp`/`*.deleting` files when computing "latest backup" so orphans never get misinterpreted as valid backups.
- No tarball, no format break, no v0.2 compat path needed.

**Schema (v0.3 — versioned, with migrations file):**

```sql
-- table: schema_version (always created first; runner detects "table missing" → fresh install)
CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

-- All state tables use release_key as the source-of-truth dedup key
-- (grill S-NEW-5): saved/dismissed/snoozed/downloaded lookups go through release_key,
-- which is UNIQUE on each table. release_id is captured for reference but not the lookup key.

-- release_key_version column on every state table (grill S3-5):
--   captures the version of normalize_release_key() that produced the stored key.
--   Future normalization-rule change → schema migration writes new keys for stored rows
--   instead of orphaning them. Re-normalization handled by the migration runner.
--   v1 ships normalize_release_key v1.
-- v0.5 (S4-3): artist/title are NULLABLE (not NOT NULL) because Discogs releases can have
-- empty/null artist (compilations); insertion coerces empty/null to "Unknown Artist" /
-- "Unknown Title" at the DiscoverStore CRUD layer so re-normalization always has SOMETHING
-- to work with. The NOT NULL constraint at the schema level would have failed inserts
-- for legit Discogs releases with no artist field.

CREATE TABLE saved (
  release_key TEXT PRIMARY KEY,            -- normalized (artist|||title) for cross-format dedup
  release_key_version INTEGER NOT NULL DEFAULT 1,
  release_id INTEGER NOT NULL,             -- the Discogs release_id of the variant the user saved
  artist TEXT,                             -- snapshot; coerced to "Unknown Artist" by store layer if empty (S4-3)
  title TEXT,                              -- snapshot; coerced to "Unknown Title" by store layer if empty (S4-3)
  label TEXT,
  saved_at TEXT NOT NULL
);
-- note: note + rating columns dropped from v1 (grill W-NEW-3) — added in migrations/002_*.sql
-- when Tier 2 surfaces them. No half-baked columns.

CREATE TABLE dismissed (release_key TEXT PRIMARY KEY, release_key_version INTEGER NOT NULL DEFAULT 1,
                        release_id INTEGER, artist TEXT, title TEXT,
                        dismissed_at TEXT NOT NULL, reason TEXT);
CREATE TABLE snoozed   (release_key TEXT PRIMARY KEY, release_key_version INTEGER NOT NULL DEFAULT 1,
                        release_id INTEGER, artist TEXT, title TEXT,
                        snoozed_at TEXT NOT NULL, until_date TEXT NOT NULL);
CREATE TABLE downloaded(release_key TEXT PRIMARY KEY, release_key_version INTEGER NOT NULL DEFAULT 1,
                        release_id INTEGER, artist TEXT, title TEXT,
                        downloaded_at TEXT NOT NULL, file_paths TEXT);

-- ==============================================================================
-- v0.6 store-layer invariants (grill S5-1, S5-2, S5-3)
-- ==============================================================================
--
-- (A) release_key is computed from RAW (un-coerced) artist/title (S5-2 fix).
--     normalize_release_key(raw_artist, raw_title, release_id):
--       - if raw_artist is non-empty: return f"{lowercase_strip_nfkd(raw_artist)}|||{lowercase_strip_nfkd(raw_title)}"
--       - if raw_artist is empty/None (compilation, Various, etc.):
--           return f"[compilation]|||{lowercase_strip_nfkd(raw_title)}|||rid_{release_id}"
--     The coerced "Unknown Artist"/"Unknown Title" values stored in the artist/title columns
--     are for display only; they are NEVER fed back into release_key computation.
--
--     KNOWN LIMITATION (documented Tier 1 tradeoff — v0.8):
--     The v0.7 attempt to use Discogs `master_id` as the discriminator was reverted because
--     `master_id` is NOT present in /labels/{id}/releases or /artists/{id}/releases responses
--     (verified by live API probe; response fields are only artist, catno, format, id,
--     resource_url, status, thumb, title, year). Fetching master_id would require a per-release
--     /releases/{id} call which would blow the 60-request scan budget.
--     Result: format-variant releases of the SAME compilation (1981 vinyl + 2024 reissue,
--     different release_ids on Discogs) surface as TWO separate feed cards rather than
--     deduping to one. This only affects empty-artist releases (compilations); named-artist
--     releases dedup correctly because release_key collapses on (artist, title).
--     The cross-compilation collision (S5-3) is still prevented via the release_id discriminator.
--     Mitigation deferred to Tier 2: a background pagination job could enrich /releases/{id}
--     for surfaced empty-artist releases and run a post-scan merge pass.
--
-- (B) artist/title coercion at insert (S4-3 + S5-3):
--     Store CRUD method (save / dismiss / snooze / record_download) does:
--       row.release_key = normalize_release_key(raw_artist, raw_title, release_id)  // RAW
--       row.artist      = raw_artist or "Unknown Artist"                            // coerced for display
--       row.title       = raw_title or "Unknown Title"                              // coerced for display
--
-- (C) file_paths always JSON-encoded list (S5-1 fix):
--     downloaded.file_paths is stored as JSON: '["/path/to/track.flac"]' even for a
--     single file. Never bare strings. Migration runner can MERGE via json.loads without
--     parse errors. record_download() wraps single paths into a list before insert.

-- normalize_release_key version migration plan (grill S3-5 + v0.5 S4-1 per-table conflict resolution):
-- When a future migration bumps normalize_release_key from v1 → v2:
--   1. Migration `00N_renormalize_keys.sql` reads each row (artist, title, release_key_version)
--   2. For rows where release_key_version < current, computes new key via v2 fn
--   3. UPDATE row with new release_key + release_key_version=2
--   4. Conflicts (two old keys collapse to same new key) are resolved PER TABLE (S4-1 fix):
--       - saved      → keep row with most-recent saved_at
--       - dismissed  → keep row with OLDEST dismissed_at (earliest "no thanks" wins; user dismissed earlier, respect that)
--       - snoozed    → keep row with the MAXIMUM until_date (longest snooze wins; the longer period covers both)
--       - downloaded → MERGE file_paths as JSON list union; keep most-recent downloaded_at
--   This prevents the v0.4 bug where "most recent saved_at" applied to `downloaded` discarded
--   file_paths references for an actually-present audio file.

-- W1 / S-NEW-5: blocks key by Discogs ID (artist/label IDs ARE stable in Discogs and don't have format variants)
CREATE TABLE blocked_artists (discogs_artist_id INTEGER PRIMARY KEY, name TEXT, blocked_at TEXT);
CREATE TABLE blocked_labels  (discogs_label_id  INTEGER PRIMARY KEY, name TEXT, blocked_at TEXT);

CREATE TABLE followed_labels (
  label_id INTEGER PRIMARY KEY, name TEXT NOT NULL,
  added_at TEXT NOT NULL,
  last_scanned_at TEXT,              -- committed value (TTL gate reads this)
  last_scanned_at_pending TEXT,      -- v0.5 (S4-2): in-flight scan writes here; committed at scan finish
  pending_scan_id INTEGER,           -- v0.5 (S4-2): scan_id that wrote the pending value
  health TEXT,                       -- 'ok' | 'rate-limit' | 'not-found' | 'error'
  consecutive_errors INTEGER DEFAULT 0,
  current_name_check_at TEXT         -- W3: label-rename watchdog (Tier 2)
);

CREATE TABLE followed_shops (
  shop_id INTEGER PRIMARY KEY AUTOINCREMENT,
  display_name TEXT NOT NULL,
  source_type TEXT NOT NULL,         -- 'discogs' | 'rss' | 'bandcamp' | 'manual'
  source_url TEXT NOT NULL,
  added_at TEXT NOT NULL,
  last_scanned_at TEXT,              -- committed value
  last_scanned_at_pending TEXT,      -- v0.5 (S4-2): staging column
  pending_scan_id INTEGER,
  health TEXT,
  consecutive_errors INTEGER DEFAULT 0
);

-- scans: telemetry + concurrent-scan lock (grill W7 + S-NEW-7 + S-NEW-8 + v0.5 C4-2)
CREATE TABLE scans (
  scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,                  -- NULL while running; SET on clean finish or crash recovery
  status TEXT NOT NULL DEFAULT 'running',  -- 'running' | 'ok' | 'cancelled' | 'rate_limited' | 'crashed'
  feeders TEXT,                      -- comma-list: 'artist,label,shop,novelty'
  novelty_strategy TEXT,             -- 'style' | 'label' | 'artist' — for round-robin rotation
  novelty_status TEXT,               -- 'ok' | 'sparse_adjacency' | 'partial' (grill S-NEW-1)
  unknown_styles TEXT,               -- JSON list of taste-vector styles missing from style_adjacency.json (grill S3-9 + C4-2 fix)
  duration_ms INTEGER,
  requests_used INTEGER,             -- Discogs requests consumed (validates the 60-cap)
  releases_seen INTEGER, releases_after_dedup INTEGER, releases_surfaced INTEGER
);
-- saves_during_session column dropped (grill S-NEW-8) — saves are correlated to scans
-- by timestamp window in queries: saves where saved_at BETWEEN scan.started_at AND scan.finished_at + 30min.
-- Materialized in /api/discover/stats roll-up; no per-scan counter needed.

CREATE TABLE release_details (release_id INTEGER PRIMARY KEY, payload_json TEXT,
                              fetched_at TEXT, expires_at TEXT);

-- W-NEW-5 fix: youtube_results keyed by release_key + track_index
CREATE TABLE youtube_results (release_key TEXT, track_index INTEGER, results_json TEXT,
                              fetched_at TEXT, PRIMARY KEY (release_key, track_index));

-- friends table reserved (Tier 3, not yet exposed via API)
CREATE TABLE friends (discogs_username TEXT PRIMARY KEY, alias TEXT, added_at TEXT);
```

**Migrations runner (grill W-NEW-2 fix):**

```python
def run_migrations(conn):
    # Bootstrap: detect first-run (no schema_version table)
    has_version = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    current = 0
    if has_version:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = row[0] or 0

    # Apply migrations in order from migrations/{N}_*.sql
    migration_files = sorted((Path(__file__).parent / "migrations").glob("[0-9]*.sql"))
    for path in migration_files:
        version = int(path.name.split("_")[0])
        if version <= current:
            continue
        sql = path.read_text()
        # 001_initial.sql includes CREATE TABLE schema_version (...)
        # subsequent migrations only contain their own ALTER/CREATE statements
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, datetime.utcnow().isoformat()),
        )
    conn.commit()
```

**Scan-lock recovery on boot (grill S-NEW-7 + S3-7 + v0.5 S4-2 — staging-column approach):**

v0.4's recovery used `MIN(started_at)` of all crashed scans, which over-rolled-back legitimate fresh data when multiple crashed scans bracket a successful one. v0.5 uses a **staging-column approach** that is scan-scoped (S4-2 fix):

**During a scan**: feeders write to `followed_labels.last_scanned_at_pending` (and `pending_scan_id`), NOT to `last_scanned_at`. The TTL gate (`24h` check) reads `last_scanned_at` — pending values are NOT visible to TTL decisions until the scan commits.

**On scan finish (status='ok')**:
```sql
UPDATE followed_labels
   SET last_scanned_at = last_scanned_at_pending,
       last_scanned_at_pending = NULL,
       pending_scan_id = NULL
 WHERE pending_scan_id = ?  -- the finishing scan_id
   AND last_scanned_at_pending IS NOT NULL;

-- same for followed_shops
```

**On boot-time crash recovery**:
```sql
-- Step 1: discard pending values from crashed scans (no need to track timestamps)
UPDATE followed_labels
   SET last_scanned_at_pending = NULL,
       pending_scan_id = NULL
 WHERE pending_scan_id IN (SELECT scan_id FROM scans WHERE finished_at IS NULL);

UPDATE followed_shops
   SET last_scanned_at_pending = NULL,
       pending_scan_id = NULL
 WHERE pending_scan_id IN (SELECT scan_id FROM scans WHERE finished_at IS NULL);

-- Step 2: close the scan rows
UPDATE scans SET finished_at = ?, status = 'crashed'
 WHERE finished_at IS NULL;
```

This guarantees:
1. A crashed-mid-scan never strands the user with a permanent 409 (S-NEW-7 invariant preserved)
2. Pending values from crashed scans never become visible to the TTL gate (S3-7 invariant preserved — never serve stale partial results)
3. **A crashed scan's recovery never wipes a successful scan's data** (S4-2 fix — multiple crashed scans interleaved with success do not produce cascading rollback because we operate per-scan_id, not by timestamp window)

The orphaned scan rows are preserved for telemetry.

**Multi-machine sync (grill S2):** not auto-synced — explicit Export / Import buttons in Discover settings produce a `.gz` of `discover.db` for manual transfer. The data dir lives in OS-standard locations that Time Machine includes by default; Dropbox-style sync requires manual config (documented in `docs/FEATURES.md`).

### 6.8 Followed Labels / Shops UI

**Settings panel** (Discover sub-tab):

- **Labels you watch** — list with remove buttons; "Add label by name" autocomplete (`search_labels()`); "Suggested from your library" list with one-click add (top-20 unwatched labels). Each row shows `last_scanned_at` and a health badge.
- **Shops you watch** — list with remove buttons; "Add shop" wizard: paste a URL → probe sequence (§6.2 Feeder 3 step list) → confirm detected source type → save. Each row shows source-type icon (Discogs / RSS / Bandcamp / Link), `last_scanned_at`, consecutive_errors badge.
- **Validated shop starter list** (see §6.10 for the candidate set + validation criteria) — pre-filled but **disabled by default**; user toggles each one on individually.
- **Blocked artists / labels** (grill W1) — list with remove buttons; populated by 🚫 Block actions from feed cards.

### 6.9 Filters & Sort

Above the feed:
- **Source**: Artist / Label / Shop / Novelty chips (multi-select)
- **Format**: Vinyl / Digital / CD / All
- **Year**: this year / last 2 / all
- **Style**: multi-select from styles present in current results
- **Sort**: Taste match (default) / Newest / Title / Artist / **Explore mode** (50/50 retrieval/novelty)

State persists per session only (re-loading is a clean slate — intentional).

### 6.10 Shop Starter Pack — validated candidate model (grill C3 + C-NEW-2 + S3-1 fix)

**v0.4 framing (Tier 1.5 conditional, not Tier 1):** the 26 shops in v0.1 were aspirational. Shop-watch is **Tier 1.5 conditional on validation** (§10 gate: ≥ 8 working auto-feed sources). If the gate passes, Tier 1.5 ships with whatever subset validates. If the gate fails, shop-watch defers to Tier 2 and Tier 1 ships with **artist + label feeders only**. The full candidate list (below) is grouped by likely-source-type — the user runs `validate_shop_pack.py` once before Tier 1.5 ships to learn whether the gate passes.

**Validation script** (`autocue/analysis/discover/shops/validate_pack.py`, runnable as `autocue discover validate-shops`):
- For each candidate in `candidate_pack.json`: try Discogs seller → RSS auto-discover → Bandcamp → fallback `manual`
- Writes `starter_pack.json` with only the validated ones
- Output report: "N shops have Discogs sellers, M have RSS, K Bandcamp-backed, P are manual-only"

**Tier 1.5 ship gate (v0.5 — C4-1 corrected; was contradictory v0.2/v0.3 wording):** if the validated set has **≥ 8 working auto-feed sources** (`discogs` + `rss` + `bandcamp`), **Tier 1.5 ships with shop-watch enabled**. **Else Tier 1.5 does not ship in this iteration**, shop-watch defers to Tier 2, and Tier 1 ships with **artist-watch + label-watch only**. Tier 1 is unaffected by gate outcome — shop-watch was never in Tier 1 per §1 / §10 / §6.10 first paragraph.

**Candidate list (from Henri's "Wil ik heen"):** 26 entries, grouped by likely strategy.

| Shop | Country | Strategy to attempt first |
|---|---|---|
| Wally's Groove World | BE | Discogs (handle exists, validate `num_for_sale ≥ 5`) → else RSS |
| Hard Wax (if claimed) | DE | RSS — Hard Wax's own site has weekly chart RSS |
| Phonica | UK | RSS (Shopify-based — try `/blogs/news.atom`) |
| Boomkat | UK | RSS (their chart pages have feeds) |
| Bleep | UK | RSS (their site historically has /rss) |
| Rush Hour | NL | RSS / Bandcamp (Rush Hour Recordings has Bandcamp) |
| Static Shock Music | UK | RSS (small punk/HC shop) |
| Balades Sonores | FR | RSS |
| Vinyl Vanguard London | UK | RSS / homepage probe |
| Vinylnerds GmbH | DE | RSS / homepage probe |
| Disk Union Jazz Tokyo | JP | Manual link only (no public RSS / Discogs) |
| Disk Union Osaka | JP | Manual link only |
| Tower Records Kyoto | JP | Manual link only |
| Discos del Espacio | CO | Bandcamp likely; else manual |
| RPM Records (Bogotá) | CO | Manual link only |
| Rotor Discos | — | Probe needed |
| The Disk Storage | — | Probe needed |
| Seawolf Records | — | Probe needed |
| Sono Ventura Records | — | Probe needed |
| Nostalgipalatset Stockholm | SE | RSS / homepage |
| Andra Jazz | SE | RSS / homepage |
| Fade Records | — | Probe needed |
| Optimal Records | — | Probe needed |
| Drop-Out Records | — | Probe needed |
| Next Door Records | — | Probe needed |
| 72 Record | — | Probe needed |
| Running Circle Records | — | Probe needed |
| Gimbab Records | KR | Bandcamp likely; else manual |
| NUT Records | — | Probe needed |
| Latitude Record Store | — | Probe needed |
| Tropicall Records | — | Probe needed |

**Friend divergence**: shipping the candidate list (not a curated mandatory feed) preserves the v0.1 promise — Henri enables Wally's Groove World + Phonica + Boomkat; a Stockholm friend enables Nostalgipalatset + Andra Jazz + Hard Wax. Same code, different shops on.

### 6.11 Snooze, Dismiss, Block, Resurface UX

- Card hover reveals: 💚 save / 💤 snooze / ✕ dismiss. Click 💤 → small popover with **1w / 1m / 3m** buttons (default 1m — §14 #3 resolved).
- Saved items get a green chip; snoozed items hidden until `until_date`; dismissed never re-appear.
- 🚫 Block artist / 🚫 Block label live under a "More" menu on each card (less destructive than dismiss — explicit gesture for "never ever").
- **Resurface badge** (grill W4): when a snoozed release reappears past its `until_date`, the card carries a "🔁 You snoozed this on {date}" badge for one feed-cycle, then disappears (just a normal card after the user has had a chance to see it).
- "Saved" tab shows wishlist; **notes + rating columns** are Tier 2, but the schema columns already exist so adding them is column-already-there + UI-only.

### 6.12 Album-action redesign (replaces current `↓ Album` button)

The ↓ Album button is **removed from the card**. Card click opens detail panel. Power-user `Shift+click` is the only path to a 1-step download.

---

## 7. User Flows

### Flow A: First-run discovery

1. User installs AutoCue, adds Discogs token, opens Discover tab
2. **Discogs token validation** runs (grill S1): `GET /oauth/identity` — if 401, banner: *"Your Discogs token isn't accepting requests. [Re-enter token]"* — DOES NOT silently scan with no results
3. Discover detects empty followed-labels list → onboarding banner: *"Pick labels from your library to watch"* + suggests top-10 labels by **plays-weighted score**
4. User clicks "Add all"
5. Discover triggers first scan (cold cache) → shows progress bar with feeder-by-feeder counts ("Artist watch: 12/30 artists scanned, 36 releases found, 18 new after dedup…")
6. Feed populates with **75% retrieval / 25% novelty** mix; user previews via YouTube
7. User saves 3, dismisses 2, downloads 1
8. State persists in `discover.db`. Next open shows cached feed instantly; refresh button shows "Refreshed 3h ago — 4 new since last refresh"

### Flow B: Shop watch onboarding (Tier 1.5 — only if validation gate passes; otherwise Tier 2)

1. User opens Discover settings → Shops sub-panel
2. Runs `validate_shop_pack.py` (one-time, exposed as "Validate starter shops" button)
3. **Validation outcome determines whether Tier 1.5 ships**: if ≥ 8 candidates resolve to `discogs` / `rss` / `bandcamp`, shop-watch is enabled in this build; else Discover shows only Artist + Label feeders this iteration
4. Validated shops appear toggle-able with source-type icons (assuming Tier 1.5 shipped)
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

### Flow D: Shift-click power flow (grill S3)

1. User scrolls Discover, recognizes a release they know they want
2. **`Shift+click` the card** → confirm modal: *"Download Sam Gendel — Digi-Squires (12 tracks)? [Yes, download all] [Cancel]"*
3. Single keyboard `Enter` or `Y` confirms → download starts immediately, no panel detour

### Flow E: Snooze and resurface (W4)

1. User clicks 💤 on a release → popover → clicks `1m`
2. Release disappears from feed
3. 30 days later, release reappears in next scan with `🔁 You snoozed this on 2026-06-07` badge for that one cycle

### Flow F: Block-artist (W1)

1. User opens a card → "More" menu → "🚫 Block artist"
2. All current and future releases by that artist are excluded from all feeders for this install
3. Visible in Settings → "Blocked artists" — can unblock

### Flow G: Friends lane onboarding (Tier 3 — not for v0.2 ship)

(Documented for future reference; do not implement in this iteration.)

---

## 8. Technical Stack

### Backend (Python — extend existing `autocue/`)

**New module** `autocue/analysis/discover/`:
- `taste.py` — `build_taste_vector(db, *, include_streaming=False) -> TasteVector` (S5)
- `style_graph.py` — hardcoded `STYLE_ADJACENCY` map + `STYLE_ALIAS_MAP` (~50 styles)
- `feeders/__init__.py` exporting `artist_feeder`, `label_feeder`, `shop_feeder`, `novelty_feeder`
- `feeders/shops/` — sub-package with `discogs_seller.py`, `rss.py`, `bandcamp.py`, `manual.py`; each implements `Source` protocol
- `feeders/rss_autodiscover.py` — `<link rel=alternate>` probing + common-path fallback
- `ranker.py` — `score_release(release, taste_vector) -> float`, `assemble_feed(scored, novelty_fraction=0.25)`
- `store.py` — `DiscoverStore` SQLite wrapper + migrations runner
- `migrations/001_initial.sql`
- `shops/candidate_pack.json` — the 30-row candidate list
- `shops/validate_pack.py` — CLI-runnable validator

**Extend `autocue/analysis/discogs.py`:**
- `search_label_releases(label_id, token, year_from)` *(NEW)*
- `search_seller_inventory(seller, token, since_date)` *(NEW — but now narrow use)*
- `get_release_details(release_id, token)` *(NEW — for tracklist)*
- `search_labels(query, token)` *(NEW — for autocomplete)*
- `get_artist_relations(artist_id, token)` *(NEW — for artist-adjacent novelty)*
- `validate_token(token)` *(NEW — calls `/oauth/identity`; used at startup)*
- Reuse: existing token bucket. **NEW** — surface `x-discogs-ratelimit-remaining` in the response wrapper so feeders can back off proactively before hitting 429.

**New REST endpoints in `serve/routes.py`:**
- `GET /api/discover/feed` (SSE) — accepts `?sources=`, `?format=`, `?year=`, `?style=`, `?sort=`, `?explore=true`. Concurrent-scan-guarded: returns 409 if a scan is already running for this DB.
- `GET /api/discover/feed/status` — currently-running scan info (progress per feeder)
- `POST /api/discover/feed/cancel` — abort an in-flight scan
- `GET /api/discover/releases/{id}` — release detail (cached)
- `POST /api/discover/save` / `dismiss` / `snooze` / `unsave` / `undismiss` / `block-artist` / `block-label` / `unblock-artist` / `unblock-label`
- `GET /api/discover/{saved,dismissed,snoozed,downloaded,blocked-artists,blocked-labels}`
- `GET /api/discover/labels/suggested` / `POST /api/discover/labels/follow` / `unfollow`
- `GET /api/discover/shops/candidates` (returns candidate_pack.json with current health status)
- `POST /api/discover/shops/validate` (kicks off `validate_pack.py` as SSE)
- `POST /api/discover/shops/follow` / `unfollow` / `probe` (probes a URL and returns detected source type without persisting)
- `POST /api/discover/state/export` (returns gzip of discover.db)
- `POST /api/discover/state/import` (accepts a discover.db file)
- `GET /api/discover/stats` (grill W7 — surfaces scan counts, save rates, novelty %)

Reuses: `/api/youtube/search`, `/api/download`, `/api/download/album`, `_consumeSSE` JS helper.

### Frontend (single-file `docs/index.html`)

- `TAB_CONTENTS.discover` rewritten — `_renderDiscoverCard`, `_renderTracklist`, `_renderYouTubeCarousel`, `_renderShopRow`, `_renderLabelRow`, `_renderBlockedList`, `_renderResurfaceBadge`
- New module-level state: `_discoverState`, `_savedReleases`, `_followedLabels`, `_followedShops`, `_blockedArtists`, `_blockedLabels`, `_currentScan`
- New helpers: `_openReleaseDetailPanel(release_id)`, `_handleShiftClick`, `_setupKeyboardShortcuts`, `_renderProgressBar`
- Reuses: `_consumeSSE`, `_esc`, `AppState`
- Adds: focus-trap helper for the panel; `prefers-reduced-motion` media query gate on slide animations

### Storage

- `discover.db` at platform-native data dir (§6.7)
- Backed up alongside `master.db` via existing `/api/backups`
- Schema migrations versioned

### Constraints respected & made explicit (v0.3 budget reconciliation — grill C-NEW-1)

- **Discogs rate-limit**: client wrapper reads `x-discogs-ratelimit-remaining` after every request; if < 5, sleep 5s before next; if 429, abort scan with `status='rate_limited'` and clear UI error. **Hard 60-request cap per scan** — feeders' aggregated budget cannot exceed it. Budget allocation:
  - `artists_budget = 20` (top-20 artists, page 1 only)
  - `labels_budget = 15` (top-15 labels, page 1 only)
  - `shops_budget = 15` (up to 15 followed shops, page 1 only; 0 if shop-watch not shipped)
  - `novelty_budget = 10` (single strategy per scan, rotated round-robin: style → label → artist)
  - **Total = 60 requests** → first-scan ≤ 60s at authenticated 60/min, ≤ 145s at unauthenticated 25/min.
  - All numbers re-derive from this cap. Changing it requires changing §4 metrics in lockstep.
- **Pagination policy**: page 1 only on every feeder call; Tier 2 "Load more" surfaces a per-entity background paginator that runs out-of-band of the per-scan budget.
- **Per-feeder TTL** (§14 #4 resolved): `artist=24h`, `label=24h`, `shop=6h`, `novelty=24h` (single strategy at a time; full rotation = 72h). Each feeder checks `last_scanned_at` on the relevant entity before fetching.
- **Per-scan request accounting**: each Discogs call increments `current_scan.requests_used` so we can verify the 60-cap holds even with future feeder additions.
- **Concurrent-scan lock**: a `scans` table row with `finished_at IS NULL` blocks new scans; `/feed` returns 409 with the running scan_id. **Boot-time recovery** (grill S-NEW-7) marks any unfinished scan as `status='crashed'` on `DiscoverStore` construction so an OS crash / server kill can't strand the user.
- **Token validation**: 1h positive cache; **instant negative invalidation** on any 401 from any Discogs API call (grill S-NEW-6) — the cache is one-shot-invalidating, so the silent-failure window is bounded by one request, not 1 hour.
- CORS, GZip, file-existence guards unchanged.

---

## 9. Security Considerations

(unchanged from v0.1 + additions)

- **Local SQLite only** — no PII leaves machine
- **Discogs token** — `.env` only; never sent to client
- **HTML escaping** via existing `_esc()`
- **Path-traversal guards** on `/api/discover/state/import` (validate the uploaded file is a valid SQLite header before swapping)
- **CORS lock unchanged** (localhost only)
- **RSS feed parsing** uses `feedparser` (already common; if not in deps, add — small library) with `sanitize_html=True` so feed-supplied HTML is not rendered as live markup
- **Bandcamp / homepage probes** use `requests` with a 10s timeout, 5MB max body size, `verify=True` for TLS, `allow_redirects=True` capped at 5 redirects
- **iframe sandbox**: YouTube iframe uses `nocookie.com` domain; embed includes `sandbox="allow-scripts allow-same-origin allow-presentation"` to prevent navigation hijack
- **Rate-limit-respect on probes**: shop validation runs sequentially with 1.5s delay between requests; never bursts more than 5 HTTPS calls in any 10s window

---

## 10. Tiered Scope (re-tiered honestly — grill S1)

### Tier 1 — MVP (gates this iteration's ship)

**Realistic estimate (v0.4 — UI-serial critical path, grill S-NEW-2 + S3-4 fix):**

- **Sum of per-task hours**: ~100 hours across 42 Tier-1 tasks (`tasks.json`)
- **Backend critical-path chain** (must serialize): T-002 → T-003 → T-007 → T-014 → T-015 ≈ **15h**
- **UI critical-path chain** (cannot parallelize — all touch `docs/index.html` and depend on prior DOM/state foundations): T-024 → T-025 → T-026 → T-027 → T-028 → T-029 → T-030 → T-031 → T-032 → T-033 → T-034 → T-035 → T-036 → T-037 → T-038 → T-039 ≈ **~36h**
- **Combined critical path**: ~50 sequential hours (backend chain feeds UI chain at T-024)
- **Parallel tracks**: store work T-011/T-012/T-013 (~9h), Discogs client T-005/T-006 (~4.5h), routes T-016–T-023 (~13h), docs T-041/T-042 (~3.5h), perf T-040 (~2.5h) — these run alongside the critical path without extending it
- **Realistic wall-clock at "full-time weekend" (16-24 productive hours)** without buffer: **6–9 weekends**
- **Realistic wall-clock at "part-time evenings" (8-12 hours/week)** without buffer: **12–18 weeks of evenings**
- **With unknown-unknowns buffer (grill W4-2 — industry rule-of-thumb 1.5×)**: **9–13 weekends full-time** or **18–27 weeks evenings**. Real software always eats more time than pure-implementation estimates on debugging, design pivots discovered during build, code-review back-and-forth, and integration surprises. The 1.5× multiplier on the critical path captures this honestly.

The v0.2 "~97h ≈ 2.5–3 weekends" estimate was off by ~3× because it ignored sequential dependencies. The v0.3 "31h critical path / 4-6 weekends" estimate was off by ~50% because it didn't model UI tasks as a serial chain (they all share `docs/index.html` and the panel/state DOM infrastructure). v0.4 caught the UI serialization but missed the unknown-unknowns buffer. v0.5 is the realistic shipping window: **4-6 months part-time evenings** for a single engineer working alone on this.

Specifically:

- Per-user taste vector with normalized styles + source filter + plays-weighted ranking
- Artist watch + Label watch feeders
- **Detail panel** with tracklist + inline YouTube preview carousel + explicit downloads + Shift-click power flow + keyboard shortcuts
- Persistent state: saved / dismissed / snoozed / downloaded / followed-labels / blocked-artists / blocked-labels
- Filters & sort (incl. Explore mode)
- Library-suggests-labels onboarding
- Removal of one-click `↓ Album` button
- macOS/Linux/Windows data dir + backup integration + Export/Import buttons
- Token validation surfaced in UI
- Concurrent-scan lock
- Style normalization
- Empty states + error states + scan-progress UI
- **Tests** (see §12)
- **Docs** (see §13)

### Tier 1.5 — Shop watch (ship-when-validation-passes)

- Shop-watch feeder with RSS / Bandcamp / Discogs-seller source-type model
- `validate_pack.py` script + UI
- Shop autocomplete / "add by URL" wizard
- Per-shop health badges
- **Gate**: ≥ 8 candidate shops validate to a working auto-feed source. Else stays Tier 2.

### Tier 2 — Should have

- **Expanded style adjacency graph (~150 styles)** — Tier 1 ships ~60; Tier 2 grows coverage so more users sit in non-sparse-adjacency zones (the novelty feeder itself is Tier 1, this is a quality iteration on its data)
- Star rating + notes UI on saved items (schema columns will be added by `002_*.sql` migration when Tier 2 ships — they are NOT in v1 schema per W-NEW-3)
- "Library suggests these shops" (analyze library labels → which sellers stock them)
- Weekly digest view
- Label-rename watchdog (W3)
- Background pagination of top-followed entities (the "Load more" / depth fix per §4 depth-tradeoff and W3-4 UI affordance)
- More granular per-feeder rate-limit tuning

### Tier 3 — Stretch / future

- **Friends lane** with explicit private-collection UX + chronological ranking + match-score chip
- Bandcamp daily-pull beyond per-label-RSS
- Custom shop URL with full RSS-discovery + generic HTML scrape
- Beatport / Juno integrations (if API access opens)
- Export saved → text/playlist for shop visits
- Auto-pre-download top-N saved overnight
- AI-curated weekly summary ("you saved 14 things this week, here are the 3 that look most like a peak-time pick")

---

## 11. Assumptions (corrected from v0.1)

- Discogs API remains accessible at current rate limits (validated 25/min unauthenticated, 60/min documented for authenticated; client now reads `x-discogs-ratelimit-remaining` rather than assuming)
- Discogs personal access tokens continue to authorize `/users/{me}/*` and seller inventory reads
- **Discogs collections are PRIVATE by default** (corrected from v0.1's incorrect claim) — Tier 3 friends lane has UX for this
- yt-dlp + ffmpeg remain optional
- Local SQLite (sqlite3 stdlib) suffices for the curation store
- Rekordbox library is stable mid-scan (label/artist IDs don't churn)
- **Most independent record shops do NOT have active Discogs marketplace storefronts** (validated via API probe — see §15 #C3)
- YouTube embeds are permitted (TOS-compliant) under standard `nocookie.com` iframe model; per-track preview searches respect rate-limit semaphores

---

## 12. Dependencies & Testing

### Runtime deps

**New**: `feedparser` (small, BSD-licensed, for RSS parsing). All other deps unchanged from CLAUDE.md.

### Test deps unchanged: `pytest`, `hypothesis`, `vitest`

**Test files added/extended:**

- `tests/test_discover_taste.py` — taste vector math, including source-filter behavior and normalization
- `tests/test_discover_style_graph.py` — STYLE_ADJACENCY / STYLE_ALIAS_MAP integrity
- `tests/test_discover_feeders.py` — mocked Discogs + RSS responses; concurrent-scan guard; rate-limit-respect; one-feeder-fails-others-continue
- `tests/test_discover_ranker.py` — Hypothesis properties (score ∈ [0,100]; novelty share ≥ 20%; blocked artist never appears; resurfaced snooze appears once)
- `tests/test_discover_store.py` — full CRUD, migration replay from blank, schema_version handling
- `tests/test_discover_shop_sources.py` — discogs/rss/bandcamp/manual probe paths
- `tests/test_discover_rss_autodiscover.py` — `<link rel=alternate>` + common-path probes (mocked HTTP)
- `tests/test_discover_validate_pack.py` — validation script integration
- `tests/test_serve_routes.py` — extended; covers every new endpoint including concurrent-scan 409
- `tests/web/discover.test.js` — `_renderDiscoverCard`, `_renderYouTubeCarousel`, focus-trap on detail panel, keyboard shortcuts, Shift-click flow
- `tests/web/discover_filters.test.js` — filter matrix + sort + Explore-mode toggle
- `tests/e2e/discover.spec.ts` — Playwright smoke: open Discover, scan progress visible, save → reload → saved persists, dismiss → no resurface, snooze → resurface badge on +30d simulated date

---

## 13. Documentation & Telemetry (W7)

### Documentation

- **CLAUDE.md** — new section: Discover v2 architecture, `~/Library/Application Support/AutoCue/discover.db` location, new `/api/discover/*` endpoints, new test files (so future sessions know)
- **docs/FEATURES.md** — user-facing copy for follow/preview/persistent-state/Shift-click/keyboard shortcuts
- **docs/reference/discover-v2.md** — long-form: shop source-type model, novelty graph, validation script usage

### Telemetry (W7 + S-NEW-8 fix — local only, never exfiltrated)

- `scans` table accrues per-scan rows with `status`, `requests_used`, `releases_seen/dedup/surfaced`, `novelty_strategy`, `novelty_status` — no session column (AutoCue has no session model)
- **Saves-per-scan computed by timestamp-window correlation** (grill S-NEW-8 + W3-2 tiebreaker): for each scan, count `saved` rows where `saved.saved_at` falls between `scan.started_at` and `scan.finished_at + 30 minutes`. The 30-minute tail captures the realistic case "user scans → browses → saves a few minutes later."
  - **Multi-scan overlap tiebreaker (W3-2)**: when a save's timestamp falls within multiple scans' windows (typical when scans run in rapid succession), the save is attributed to the **most recent scan whose `started_at` precedes `saved_at`** — i.e. the scan whose feed the user was looking at when they clicked save. Implemented in `/api/discover/stats` as `MAX(scan_id) WHERE scan.started_at < saved.saved_at AND scan.finished_at + 30min > saved.saved_at`.
  - **Late saves (> 30min after scan finish) are unattributed** — surfaced as a separate "unattributed saves" count in stats so the user knows not every save is included in saves-per-scan.
  - Queries materialize this in `/api/discover/stats`; no per-scan counter column.
- `/api/discover/stats` returns roll-ups: scan count, avg duration, saves-per-scan (timestamp-window), novelty share (`status='ok' / 'partial' / 'sparse_adjacency'` breakdown), top labels, top shops
- A small "Discover stats" surface in Settings shows whether targets in §4 are being hit, so the user can sanity-check the algorithm

---

## 14. Decisions made (was Open Questions in v0.1)

Locked from grill-me round 1 discussion:

1. **Label-suggestion weighting** — Hybrid: `log(1 + plays) × √track_count`, fallback to track count for cold-start
2. **Snooze default duration** — 30 days default, with **1w / 1m / 3m** quick options on the button
3. **Friend-lane placement** — N/A in this iteration (Tier 3); when shipped: top horizontal strip, collapsible, only shown when ≥1 friend exists
4. **Re-scan cadence** — Per-feeder TTL (artist=24h, label=24h, shop=6h), scan-on-open if any TTL expired, manual Refresh always available with delta-count, concurrent-scan lock

### Decisions made in v0.3 (from grill round 2)

5. **Style adjacency graph** — shipped as `autocue/analysis/discover/style_adjacency.json` (W-NEW-4 fix), loaded at runtime with strict JSON Schema validation + bundled-default fallback (S3-8). Live-tunable without rebuild; PR-shareable for community contributions. Anchor / terminal style markers in the JSON (per S-NEW-1 cold-start handling). ~60 styles for Tier 1 ship.
6. **Shop probe etiquette** — validator identifies as `AutoCue/0.4 +https://github.com/HenriGeorge/AutoCue` and respects `robots.txt` (parses `/robots.txt` for the relevant path; skips if `Disallow:` matches).
7. **release_key as schema PK** (S-NEW-5 resolution) — `saved` / `dismissed` / `snoozed` / `downloaded` use `release_key` as primary key. `release_id` is captured as reference but not the lookup key. Locked. **+ Versioned via `release_key_version` column (S3-5)** so future normalization changes migrate, not orphan.
8. **Backup format** (S-NEW-3 + S3-6 resolution) — parallel sidecar files in `BACKUP_DIR/`, no tarball; create/delete/restore are two-phase with `.tmp`/`.deleting` temp-file pattern for atomicity; startup cleanup pass removes orphans. Locked.
9. **Token-validation cache shape** (S-NEW-6 + W3-1 scope note) — 1h positive, instant-on-401 invalidate. **Scope (W3-1)**: applies to authenticated Discogs API endpoints. Future endpoints that legitimately 401 for non-token reasons (e.g., Tier 3 friend's private collection) will need an explicit allowlist to avoid false invalidations. Tier 1 endpoints (artist/label/release/search/identity) all 401 only on genuine token issues, so the wrapper is safe today. Locked for Tier 1.
10. **YouTube preview policy** (S-NEW-4) — lazy on click (pre-warm only track #1). Locked.

### Decisions made in v0.4 (from grill round 3)

11. **Follow-label priority** (S3-3) — user-explicit follows take precedence in the 15-label budget; taste-vector implicit follows fill remaining slots; round-robin by `last_scanned_at ASC` ensures fairness when explicit follows exceed budget. Locked.
12. **Modal default focus** (W-NEW-1 + W3-5 explicit decision) — Shift-click download confirm modal defaults to Cancel button focus. This is intentional and defensible: Download is destructive (bandwidth, disk, time, non-reversible). Convention varies (macOS no-default, Material Cancel-default, Windows OK-default); we pick Cancel-default to match the "zero accidental downloads" success metric in §4. Document this in code with a comment so a future contributor doesn't "fix" it back to OK-default. Locked.
13. **Depth-tradeoff UI affordance** (W3-4) — Tier 1 surfaces "Showing recent 50 — older catalog deferred to background scan" chip on artist/label cards where the page-1 fetch returned exactly 50 results (the only reliable signal of "more available"). Full background pagination is Tier 2. Locked as a one-line UI affordance for Tier 1.
14. **Restore overwriting novelty rotation** (W3-3) — accepted as cosmetic; after restore, the next scan picks up rotation from the restored state. Documented in `docs/FEATURES.md` under "Restoring backups". No code change.
15. **Saves-correlation tiebreaker** (W3-2) — most-recent-scan-whose-start-precedes-saved_at wins; late saves (> 30min) tracked separately as "unattributed." Locked.

---

## 15. Resolved Issues Log

### Round 1 grill (v0.1 → v0.2)

| ID | Finding | Where fixed in v0.2 |
|---|---|---|
| **C1** | Rate-limit math contradicts <30s scan target | §4 + §8 (further reconciled in v0.3 — see C-NEW-1 below) |
| **C2** | Discogs collections assumed public — wrong | §3 corrected; §6.4 demotes friend lane to Tier 3 |
| **C3** | Shop pack vapor | §6.2 Feeder 3 rearchitected; §6.10 candidate vs validated split; §10 Tier 1.5 conditional |
| **C4** | Echo-chamber ranking | §6.2 Feeder 4 + §6.3 Stage 2; product renamed |
| **C5** | Friend-lane self-defeating ranking | §6.4 + deferred to Tier 3 |
| **S1** | Tier 1 understates effort + missing tasks | §10 (further refined in v0.3 — see S-NEW-2 below) |
| **S2** | SQLite durability missing | §6.7 platform-native data dir + Export/Import (backup design refined in v0.3 — S-NEW-3) |
| **S3** | Detail panel friction for power users | §6.5 Shift-click + keyboard shortcuts (modal default fixed in v0.3 — W-NEW-1) |
| **S4** | YouTube preview holes | §6.6 carousel + disk cache (lazy policy added in v0.3 — S-NEW-4) |
| **S5** | Streaming tracks corrupt taste vector | §6.1 `source == 'file'` filter |
| **S6** | Already-owned dedup by release ID under-dedupes | §6.2 dedup by `release_key` (now PK in v0.3 — S-NEW-5) |
| **S7** | Rescan cadence weakest decision | §14 #4 + scan-lock (boot-recovery added in v0.3 — S-NEW-7) |
| **W1** | No block-artist / block-label | §6.5 More menu + §6.7 schema tables + §6.3 hard_block |
| **W2** | Style normalization naive | §6.1 STYLE_ALIAS_MAP |
| **W3** | Label rename breaks follows | §6.7 watchdog column + Tier 2 |
| **W4** | Snooze re-surface silent | §6.11 resurface badge |
| **W5** | Bandcamp link in panel fictional | §6.5 (refined in v0.3 — W-NEW-6) |
| **W6** | §1 exec summary contradicts Tier-2 marker | §1 rewritten (re-fixed for shop-watch in v0.3 — C-NEW-2) |
| **W7** | No success-metric measurement plan | §4 + §13 telemetry (saves correlation fixed in v0.3 — S-NEW-8) |
| **W8** | Friend Discogs friction | §6.4 Tier 3 UX |

### Round 2 grill (v0.2 → v0.3)

| ID | Finding | Where fixed in v0.3 |
|---|---|---|
| **C-NEW-1** | Per-feeder budget arithmetic violates §4 metric | §4 "Budget reconciliation" + §8 Constraints — hard 60-request cap, explicit allocation table, page-1-only policy, depth-tradeoff documented |
| **C-NEW-2** | §1 over-promises shop-watch (re-introduces W6) | §1 rewritten to mark shop-watch as Tier 1.5 conditional ("may add"); matches §10 gate wording |
| **S-NEW-1** | Novelty cold-start with sparse adjacency | §6.2 Feeder 4 anchor/terminal style markers; §6.3 Stage 2 conditional reservation (no garbage backfill); UI hint when `novelty_status = 'sparse_adjacency'`; §4 metric made conditional |
| **S-NEW-2** | Tier 1 effort sequential, not parallel | §10 critical-path analysis: ~31h sequential + parallel tracks → 4-6 weekends full-time or 8-12 weeks evenings |
| **S-NEW-3** | Backup format hand-waved (tarball would break flat-file) | §6.7 parallel sidecar files (`BACKUP_DIR/discover_<timestamp>.db`) — no format break, old backups read fine |
| **S-NEW-4** | YouTube search storm on panel open | §6.6 lazy policy — pre-warm track #1 only; other tracks search on click |
| **S-NEW-5** | `release_id` CRUD breaks dedup | §6.2 + §6.7 — `release_key` is PRIMARY KEY on saved/dismissed/snoozed/downloaded; `release_id` is a reference only |
| **S-NEW-6** | Token-validation 1h cache extends silent failure | §8 Constraints — 1h positive cache, instant invalidation on any 401 from any Discogs call |
| **S-NEW-7** | Scan lock leaks on crash | §6.7 + §8 — boot-time `UPDATE scans SET status='crashed' WHERE finished_at IS NULL` |
| **S-NEW-8** | `saves_during_session` column has no trigger | §6.7 column removed; §13 telemetry uses timestamp-window correlation between saves and scans |
| **S-NEW-9** | Novelty budget too small for design | §6.2 Feeder 4 single-strategy-per-scan round-robin; budget 10 requests fits |
| **W-NEW-1** | Shift+click modal could auto-confirm | §6.5 modal default focus = Cancel button |
| **W-NEW-2** | Migration runner bootstrap edge case | §6.7 migrations runner code includes "no schema_version table → fresh install" detection |
| **W-NEW-3** | Pre-emptive Tier-2 schema columns | §6.7 v1 schema drops `note` / `rating`; added in `002_*.sql` when Tier 2 needs them |
| **W-NEW-4** | Adjacency graph as code constant | §14 #5 — adjacency loaded from `style_adjacency.json` resource, live-tunable |
| **W-NEW-5** | Tier 1.5 quality unknown until first user validates | Documented as expected; §6.10 onboarding flow shows the user the validation results before they commit |
| **W-NEW-6** | Bandcamp link discovery fragile | §6.5 — literal `bandcamp.com` substring match on `videos` or `notes`; link simply absent when not found |
| **W-NEW-7** | Dead-code migration from `~/.autocue/` | §6.7 — legacy-path migration removed |
| **W-NEW-8** | Per-feeder TTL vs global "Refreshed Xh ago" | §6.7 — global string driven by `MAX(last_scanned_at)` across the entities scanned in the most recent `status='ok'` scan |

### Round 3 grill (v0.3 → v0.4)

| ID | Finding | Where fixed in v0.4 |
|---|---|---|
| **S3-1** | §5 + §6.10 + §7 contradict §1 on shop-watch Tier 1.5 | §5 Bandcamp row rewritten to "Tier-1.5 candidate source"; §6.10 first paragraph rewritten with v0.4 framing; §7 Flow B prefixed "(Tier 1.5 — only if validation gate passes)" |
| **S3-2** | §10 Tier 2 self-contradicts on novelty | §10 Tier 2 bullet replaced with "Expanded style adjacency graph (~150 styles)"; novelty feeder itself is Tier 1 (no longer ambiguous) |
| **S3-3** | Silent top-15 cap on followed labels | §6.2 Feeder 2 — explicit priority: user-explicit > taste-vector; round-robin by `last_scanned_at ASC` when explicit exceeds budget |
| **S3-4** | Critical-path under-counts UI dependencies | §10 — backend chain ~15h + UI chain ~36h = ~50h sequential; wall-clock 6-9 weekends full-time / 12-18 weeks evenings |
| **S3-5** | release_key orphan rows on normalize-fn changes | §6.7 schema — every state table has `release_key_version` column + NOT NULL `artist`/`title` for re-normalization migrations |
| **S3-6** | Backup atomicity gaps | §6.7 backup section — two-phase create/delete/restore with `.tmp`/`.deleting` temp-file pattern + startup cleanup pass |
| **S3-7** | Partial-scan TTL state after crash recovery | §6.7 scan-lock recovery — two-step recovery rolls back `last_scanned_at` for entities updated during the crashed scan's window |
| **S3-8** | style_adjacency.json runtime loading unspecified | §6.2 Feeder 4 — strict JSON Schema validator at startup; bundled-default fallback; UI warning surfaced via `/api/discover/stats` |
| **S3-9** | Style not in adjacency JSON → KeyError | §6.2 Feeder 4 — `STYLE_ADJACENCY.get(style, [])` semantics; unknown-style counts as sparse_adjacency; logged on scan row |
| **W3-1** | Token cache 401-invalidate scope | §14 #9 scope note — applies to Tier 1 endpoints only; Tier 3 future endpoints will allowlist legitimate-401 cases |
| **W3-2** | Multi-scan overlap in saves correlation | §13 — tiebreaker "most recent scan whose started_at precedes saved_at"; late saves (>30min) tracked as "unattributed" |
| **W3-3** | Restore overwrites novelty_strategy rotation | §14 #14 — accepted as cosmetic; documented in `docs/FEATURES.md` |
| **W3-4** | Page-1-only depth tradeoff not surfaced in UI | §14 #13 — Tier 1 chip "Showing recent 50 — older catalog deferred"; full pagination Tier 2 |
| **W3-5** | Modal Cancel-default convention | §14 #12 explicit decision documented; comment in code prevents future "fix" |

### Round 4 grill (v0.4 → v0.5)

| ID | Finding | Where fixed in v0.5 |
|---|---|---|
| **C4-1** | §6.10 ship-gate paragraph contradicts §6.10 first paragraph + §1 (third recurrence of shop-watch over-promise) | §6.10 ship-gate paragraph rewritten — now says "Tier 1.5 ships" (was "stays in Tier 1" leftover); explicit note that Tier 1 is unaffected by gate outcome |
| **C4-2** | Scans schema lacks `unknown_styles` column that §6.2 design references | §6.7 — `unknown_styles TEXT` column added (JSON list); §6.2 references match the schema |
| **S4-1** | Re-normalization conflict resolution only covers `saved`; `downloaded` would lose file_paths | §6.7 — per-table strategy: saved (most-recent saved_at), dismissed (oldest dismissed_at), snoozed (max until_date), downloaded (MERGE file_paths as JSON list union) |
| **S4-2** | Multi-crash partial-state recovery uses MIN(started_at) which over-rolls-back successful intervening scans | §6.7 — `last_scanned_at_pending` + `pending_scan_id` staging columns; per-scan_id rollback; never overlaps with successful scans |
| **S4-3** | NOT NULL artist/title clashes with Discogs Various-Artists releases | §6.7 — artist/title NULLABLE; DiscoverStore CRUD coerces empty/null to "Unknown Artist"/"Unknown Title" at insert |
| **S4-4** | Restore pre-snapshot creation failure unguarded | §6.7 — explicit "assert both pre-snapshots exist and pass SQLite header check" before proceeding to copy phase; abort with clear error if not |
| **S4-5** | JSON schema migration plan absent — v0.5+ silently reverts user JSON | §6.2 Feeder 4 — `schema_version` top-level field + per-version schema files + chained upgrade functions; .bak preservation on failure |
| **W4-1** | Implicit SQLite NULL-first ordering | §6.2 Feeder 2 — explicit `ORDER BY ... ASC NULLS FIRST` |
| **W4-2** | Effort estimate lacks unknown-unknowns buffer | §10 — 1.5× buffer added; realistic wall-clock now 4-6 months part-time evenings |
| **W4-3** | Startup cleanup pass failing recursively | §6.7 backup section — cleanup failures non-fatal; next-startup retry; orphans don't poison "latest backup" computation |

---

### Round 5 grill (v0.5 → v0.6)

| ID | Finding | Where fixed in v0.6 |
|---|---|---|
| **C5-1** | Pre-snapshot SQLite header check fails on SQLCipher-encrypted master.db | §6.7 restore section — per-db validator: discover.db via SQLite magic header, master.db via `pyrekordbox.Rekordbox6Database(...).open()` + size-match (SQLCipher-aware) |
| **S5-1** | `downloaded.file_paths` MERGE assumes JSON but schema is TEXT with no format spec | §6.7 store-layer invariants section — file_paths always JSON-encoded list `["..."]`, even for single file; `record_download()` wraps; migration `json.loads()` is safe |
| **S5-2** | release_key computation vs artist/title coercion order unspecified — silent dedup risk | §6.7 store-layer invariants — release_key is computed from RAW artist/title BEFORE coercion; coerced "Unknown Artist"/"Unknown Title" only fill display columns, never feed back into release_key |
| **S5-3** | "Unknown Artist" coercion creates shared release_key bucket for compilations | §6.7 store-layer invariants — empty-artist releases use sentinel key `"_compilation_|||{title}|||rid_{release_id}"`; release_id discriminator blocks cross-compilation false dedup |
| **S5-4** | First-run loader flags default as "warning" — incorrect; no customization path | §6.2 Feeder 4 loading sequence — step 0 (file location: user data dir alongside discover.db), step 1 (first-run silent copy + informational log, no warning), step 6 (warning flag NOT set on silent-copy path) |
| **S5-5** | T-007/T-014/T-047 task descriptions don't reflect v0.5 staging-column writes | tasks.json — T-007/T-014/T-047 descriptions updated; T-014 adds `commit_pending_scan(scan_id)` as the atomic promote-pending step on successful finish |
| W5-1 | §6.10 first paragraph labeled "v0.4 framing" (cosmetic) | Deferred — cosmetic only, not blocking |
| W5-2 | Commit-time transaction not explicit | T-014 description now says "atomically promotes ... in one transaction" via store.commit_pending_scan |
| W5-3 | Dismissed-oldest-wins reasoning post-hoc | Deferred — strategy is functionally correct (dismissed = excluded from feed regardless of timestamp); reasoning is annotation only |

### Round 6 grill (v0.6 → v0.7)

| ID | Finding | Where fixed in v0.7 |
|---|---|---|
| **S6-1** | pyrekordbox path-based auto-detect fails for BACKUP_DIR paths (no install fingerprint there) | §6.7 restore section + T-022 — pass explicit `key=app.state.db._key` to the master.db validator; path-agnostic via direct sqlcipher3 or pyrekordbox `key=` kwarg |
| **S6-2** | S5-3 compilation-collision fix broke format-variant dedup for compilation reissues (different release_ids of same comp) | §6.7 store-layer invariants + T-005 + T-014 — compilation release_key uses Discogs `master_id` as discriminator (`...|||mid_{master_id}`); falls back to release_id only when master_id is null on older entries; sentinel prefix uses zero-width-space flanking to eliminate band-name collision (W6-6) |
| W6-1 | file_paths CHECK constraint | Deferred — store-layer invariant is sufficient for v1; CHECK constraint can be added in a Tier 2 migration if it becomes load-bearing |
| W6-2 | pyrekordbox-open is heavy | Mitigated by S6-1 fix — direct sqlcipher3.connect() + `SELECT 1 FROM djmdContent LIMIT 1` is lightweight; doesn't pre-load tables |
| W6-3 | Parent dir creation ordering | T-011 (DiscoverStore __init__) creates the user data dir with `Path.mkdir(parents=True, exist_ok=True)` before any file write — style_adjacency.json loader runs after DiscoverStore init per server lifespan order |
| W6-4 | §6.10 "v0.4 framing" label cosmetic | Deferred — pure label, doesn't affect content correctness |
| W6-5 | `.bak` move vs copy ambiguous | Resolved as MOVE (rename original to .bak) — next startup hits first-run path, user sees the new default file and the .bak side-by-side; can manually inspect or re-apply edits |
| W6-6 | `_compilation_` sentinel could collide with band name | Sentinel rewritten as `[compilation]` (bracketed literal); release_id discriminator further reduces collision impact |

### Round 7 grill (v0.7 → v0.8) — verified-fact reverts

| ID | Finding | Where fixed in v0.8 |
|---|---|---|
| **C7-1** | `app.state.db._key` does not exist on Rekordbox6Database; v0.7 fix would crash | §6.7 restore validator + T-022 — reverted to `pyrekordbox.Rekordbox6Database(presnap_path)` with NO `key=` kwarg. Verified by reading pyrekordbox source: the constructor does `if not key: key = deobfuscate(BLOB)` and consumes the resolved key into the SQLAlchemy URL. Path-agnostic by design — works for any path including BACKUP_DIR. The round-6 grill's premise about path-based fingerprinting was incorrect. |
| **C7-2** | Discogs `master_id` is not in `/labels/{id}/releases` or `/artists/{id}/releases` responses; v0.7 mechanism is functionally a no-op at dedup time | §6.7 store-layer invariant + T-005 + T-014 — reverted to `release_id` discriminator. Compilation-reissue duplication (different release_ids of the same comp surfacing as separate cards) documented as a known Tier 1 limitation. Only affects empty-artist releases. Tier 2 background pagination could enrich master_id post-scan and run a merge pass. |

---

## 16. Out of Scope (unchanged from v0.1, refined)

- Email newsletter parsing
- Generic HTML scrape of arbitrary shop sites (Tier 3 stretch)
- Spotify / Apple Music integration
- Last.fm scrobble import
- Mobile app
- Cross-user real-time sharing of saved lists
- Auto-purchasing / cart integration with shops
- Editorial / human-curated global lanes

---
