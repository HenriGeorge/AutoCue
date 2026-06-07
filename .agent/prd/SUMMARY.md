# Discover & Watchlist v2 — Project Summary

## What this is

A rebuild of AutoCue's Discover tab — the page that surfaces new vinyl/digital releases worth buying — into a **personalized, per-user feed driven by the local Rekordbox library, with explicit exploration mixed into retrieval, durable curation state, and (Tier 1.5) shop sources that match what shops actually expose (RSS / Bandcamp / homepage), not what we wished they exposed (Discogs marketplace).**

Renamed honestly to **Discover & Watchlist** to acknowledge the system is part recommendation engine, part wishlist manager — not pure exploratory discovery.

## Main features

1. **Per-user taste vector** from Rekordbox library (artists, labels, styles, BPM, key) with normalized styles, `source='file'` filter (no streaming pollution), plays-weighted scoring
2. **Four feeders**: Artist watch, Label watch, Shop watch (Tier 1.5, conditional), Novelty (style/label/artist-adjacent — one strategy per scan, rotated)
3. **Two-stage ranker** — base score + conditional 25% novelty reservation (skipped when adjacency is sparse to avoid garbage backfill), plus Explore-mode toggle for 50/50
4. **Detail panel + inline YouTube preview carousel** — lazy on click (pre-warm only track #1), 3 candidates per track, disk-cached, no auto-download
5. **Power-user shortcuts** — Shift-click for instant download confirm (modal defaults to Cancel), j/k/Enter/S/X/Z/D keyboard
6. **Persistent state** — saved / dismissed / snoozed / downloaded / followed-labels / followed-shops / blocked-artists / blocked-labels in `~/Library/Application Support/AutoCue/discover.db`
7. **Backup integration** — parallel sidecar files; two-phase atomic rename; SQLCipher-aware restore validation (pyrekordbox's internal `deobfuscate(BLOB)` resolves the key for any path)
8. **Shop watch via real protocols** (Tier 1.5) — RSS auto-discover (primary), Bandcamp (well-supported), Discogs seller (rare bonus), manual link (honest fallback)
9. **Block artist / block label** as first-class actions, not just per-release dismissal
10. **Snooze with 1w / 1m / 3m quick options** + resurface badge when items reappear
11. **Concurrent-scan lock** + per-feeder TTLs (artist=24h, label=24h, shop=6h) + manual Refresh with delta-count + boot-time crash recovery via staging columns
12. **Telemetry table** (`scans`) so success metrics in §4 are actually measurable via timestamp-window correlation between saves and scans
13. **release_key as schema PK** with `release_key_version` for future re-normalization migrations

## Key user flows

1. **First-run**: Discogs token validated → onboarding suggests top library labels → first scan with progress bar → feed with 75% retrieval + 25% novelty → save / dismiss persists in discover.db
2. **Shop watch** (Tier 1.5 only if validation gate passes): validate-pack script probes each candidate shop, classifies as discogs/rss/bandcamp/manual → user toggles validated ones
3. **Preview-before-download**: card click → detail panel → ▶ on track plays inline YouTube → "Try another" cycles candidates → explicit Download button triggers existing yt-dlp flow
4. **Shift-click power flow**: Shift+click card → one confirm modal (default Cancel) → immediate download (no panel)
5. **Block-artist**: card More menu → 🚫 Block → that artist disappears from all feeders forever; manageable in settings
6. **Snooze + resurface**: snooze for 1m → release reappears in feed 30 days later with `🔁 You snoozed this on …` badge for one cycle

## Key requirements

- Backend adds `autocue/analysis/discover/` package: `taste.py`, `style_graph.py`, `feeders/*`, `ranker.py`, `store.py`, `scan_orchestrator.py`, `migrations/*.sql`, `shops/candidate_pack.json`, `shops/validate_pack.py`
- Extends `autocue/analysis/discogs.py` with label/seller/release/label-search/artist-relations endpoints + token validator + rate-limit-header awareness
- New endpoints `/api/discover/*` for feed (SSE with progress), release detail, save/dismiss/snooze/block CRUD, follow-label/shop CRUD, validate-shops SSE, state export/import, stats
- Frontend changes confined to `docs/index.html` (no build step); reuses `_consumeSSE`, `_esc`, AppState; adds focus-trap, keyboard shortcuts, carousel
- New runtime dep: `feedparser` (RSS parsing)
- Platform-native data dir for `discover.db`; backup integration with `/api/backups`
- CORS unchanged (localhost only); HTML escaping for all external strings; iframe sandbox for YouTube embed
- Test coverage: 9 new pytest files, 2 new Vitest files, 1 Playwright spec
- Honest effort estimate: ~50h critical path + 1.5× buffer = 6-9 weekends full-time or 12-18 weeks evenings (3-4 months) for a single engineer
- Documentation updates: CLAUDE.md, docs/FEATURES.md, docs/reference/discover-v2.md
- Friend lane is **out of scope** for this iteration (Tier 3)

## Known limitations (accepted Tier 1 tradeoffs, Tier 2 mitigations)

1. **Compilation-reissue duplication**: Discogs listing endpoints don't include `master_id`, so empty-artist releases use `release_id` as discriminator — different release_ids of the same comp surface as two cards. Tier 2 background `/releases/{id}` enrichment fixes this.
2. **Page-1-only catalog depth**: artist with 200+ releases shows only 50 most recent in per-scan feed. Tier 2 background pagination.
3. **Tier 1.5 conditional**: shop-watch ships only if validation gate (≥8 working auto-feed sources) passes. Else defers to Tier 2.
4. **Hand-curated adjacency**: ~60 styles for Tier 1; sparse-adjacency users see pure-retrieval feeds with one-time UI hint. Tier 2 expands to ~150.
5. **No automatic multi-machine sync**: Export/Import + Time Machine/Dropbox are the manual workflow.
6. **Late saves (>30min after scan)** are tracked as unattributed in telemetry.
