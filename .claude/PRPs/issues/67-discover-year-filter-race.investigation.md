# Issue #67 — Discover year-filter race → 409 → destructive empty state

## Problem

When the user changes the Discover **Year** filter twice in quick succession (e.g. "All" → "This year" → "All"), the second request arrives at `/api/discover/feed` while the first scan still holds the per-DB lock. The backend correctly returns `409 Conflict` ("A scan is already running for this database"). The frontend's 409 handler then clears the grid to the "Couldn't finish the scan…" empty state, even though the *first* request is still successfully streaming results.

Net effect: a user who toggles the year filter quickly loses their feed until they hit Refresh.

## Root cause

`docs/index.html`:

- **L6135-L6138** — every change event on the source chips and `#disc-v2-year` calls `DiscoverV2.runScan()` synchronously, with no debounce.
- **L4556-L4558** — `runScan()` is guarded only against re-entry while *its own* fetch is in flight (`if (state.scanRunning) return`). But the change handlers fire from user input, and the guard returns early — so the second click is silently dropped. The *real* race comes from the network side: the lock on the server is held by the streaming first response, while the second `fetch` (initiated *after* the guard releases on a delayed-resolution edge case, OR by a different code path like the source-chips handler) hits the locked endpoint and returns 409.

Re-reading the network log in the bug report:

```
reqid=2012 GET /api/discover/feed?...year_from=2026  [200]   ← first request, streaming
reqid=2032 GET /api/discover/feed?...                [409]   ← second fires while first still streaming
```

The first request is a long-lived SSE stream; `state.scanRunning` is `true` for the entire duration. So the re-entry guard *should* drop the second `runScan()` call — but it does NOT, because reqid=2032 fires. The trigger is in fact the user changing the year while the *initial* page-load scan is still streaming. The initial scan (kicked off when the Discover tab first activates) keeps `scanRunning=true` for several seconds; if the user touches the year picker during that window, the second `runScan()` call happens *after* the first stream has called `state.scanRunning = false` (the stream ends mid-toggle), so the guard passes, but the *server* still has the lock release in flight, and the second fetch loses the race.

Even when the guard does correctly drop the second call, the user-perceived bug remains: dropping silently means the year filter "doesn't work" until the first stream finishes.

## Proposed solution

Two minimal, surgical changes — both in `docs/index.html`, ~25 lines combined:

1. **Cancel the prior scan before starting a new one.** When `runScan()` is invoked while one is already running, instead of silently returning, POST to `/api/discover/feed/cancel` (which already exists; see L4703-L4705 and the route at `autocue/serve/routes.py:2573`), wait for the in-flight stream loop to exit (the cancel will short-circuit the reader), then start the new scan. This both un-locks the server and gives the user immediate feedback that their filter change took effect.

2. **Non-destructive 409 handling.** If a 409 *does* still happen (e.g. another tab / external client holds the lock), keep the existing cards visible. Set `state.scanError = {kind: 'conflict', ...}` so a small banner can surface, but do NOT clear `state.cards` or `state.cardsByKey`. The card grid stays visible.

The combination kills the race at its source (1) and degrades gracefully if it ever recurs (2). No new endpoints, no schema changes, no backend touched.

## Affected files

- `docs/index.html` — `runScan()` and the 409 branch (≈ 25 lines net change).
- `tests/web/discover-v2-integration.test.js` — mirror the new `runScan()` logic; add regression test: starting a second scan while one is in-flight cancels the first, fetches the new feed, and the second scan's results are what end up in `state.cards`.
- `tests/web/discover-v2-empty-error.test.js` — update 409 mapping: cards from a prior successful scan must NOT be cleared by a 409 response.

## Risks

- The cancel endpoint (`/api/discover/feed/cancel`) is best-effort: it sets a cancel flag the orchestrator polls. There is a short window where the in-flight scan finishes naturally before noticing the flag. That's fine — we `await` the cancel POST, then check `state.scanRunning` and wait briefly (via the existing stream loop's `state.scanRunning = false` setter) before starting the new fetch.
- Test mirror divergence: the runtime `runScan` in `index.html` is duplicated in test fixtures. Both must be updated atomically or the regression test will fail against one and pass against the other.

## Out of scope

- Debouncing the change handlers (covered by cancel-and-restart; debouncing is an optimization for *future* — not needed for correctness).
- Switching the sort behavior (already correctly client-side per the report).
- Backend lock semantics (issue explicitly accepts current 409 behavior).
