# Issue #120 — Cues-tab tracks artwork 404 console spam

## Problem

The Cues-tab track list renders `<img src="/api/tracks/{id}/artwork">` for every visible row. Tracks without artwork (streaming sources, library-only references) cause the endpoint to return **404 Not Found**. Each 404 emits a browser console error ("Failed to load resource: the server responded with a status of 404 (Not Found)"). In a 3.8k-track library load: 2083 console 404 entries from a single page-load sequence.

The noise drowns out real errors and forces Playwright tests asserting `errors === []` to filter them out.

## Root Cause

`autocue/serve/routes.py:436` — `track_artwork` raises `HTTPException(404, "No artwork")` when the track row exists but no artwork file resolves on disk. The 404 status is what the browser logs as a console error for `<img>` elements.

There is no way for the frontend to know in advance which rows have artwork without an HTTP probe per row, so the `<img>` is always rendered.

## Proposed Solution

Adopt the issue's suggested **Option 3**: keep 404 ONLY for the case where the track doesn't exist in the DB (the genuine "not found"); when the track row exists but no artwork file is available, return **204 No Content**.

Browsers handle 204 silently in `<img>` loads — no console error is emitted, the load resolves quietly, and the existing `img.onerror = () => img.remove()` placeholder fallback still triggers (a 204 image is not `naturalWidth>0`, but the `onload` path runs and the placeholder is left intact; the `<img>` simply renders empty over the placeholder). The frontend already uses `artwork-placeholder` underneath, which remains visible.

This is the cheapest change — one-line semantic distinction at the API layer, no schema change, no extra round-trip, no JS changes required.

## Affected Files

- `autocue/serve/routes.py` — change `raise HTTPException(404, "No artwork")` to `return Response(status_code=204)`.
- `tests/test_serve_routes.py` — update the four "missing artwork" tests in `TestArtworkEndpoint` to assert 204 (regression guard). Keep the "track not found" test asserting 404.

## Risks

- Any client code that relied on 404 to distinguish "no artwork" from "track missing" would now see 204 for the no-artwork branch. The frontend uses only `img.onload`/`img.onerror` and does not branch on status — safe.
- The four existing pytest assertions for `status_code == 404` will need to flip to 204; they're explicitly part of the fix (regression guard).
- No web UI / vitest changes — the JS code path doesn't inspect the artwork response status.
- E2E impact: this is exactly what the issue is about — fewer console errors, no regressions in functional behavior.

## Touch log (initial)

- Leg A (pytest): dirty — touches `autocue/serve/routes.py` + `tests/test_serve_routes.py`.
- Leg B (vitest): no changes to `docs/index.html` or `tests/web/` — skip after first iteration.
- Leg C (e2e): touches `autocue/serve/**` — must run first iteration.
