# Issue #115 — Investigation

## Problem

`autocue/serve/app.py:64-80` installs `_invalidate_snapshot_on_mutation` via the
`@app.middleware("http")` decorator. That decorator wraps the callable in
Starlette's `BaseHTTPMiddleware`, which is documented to be incompatible with
streaming responses — when a `StreamingResponse` generator aborts (filter-toggle
under load triggers a snapshot-invalidate-then-fetch sequence that races with
in-flight SSE traffic), `BaseHTTPMiddleware.call_next` raises
`RuntimeError("No response returned.")`. Surfaces as a Playwright qa-smoke
failure: `qa-smoke.spec.ts:134 filter toggles do not crash the page`.

## Root cause (file:line)

- `autocue/serve/app.py:64` — `@app.middleware("http")` is the offending wrapping.
- `starlette/middleware/base.py:169` — where the `RuntimeError` is raised, per
  the traceback in the issue body.

Reference: <https://github.com/encode/starlette/issues/1925>. Starlette's own
docs steer users to pure-ASGI middleware for anything that touches streaming
responses; `BaseHTTPMiddleware` buffers responses and breaks SSE / chunked
generators that don't complete cleanly.

## Proposed solution

Replace the `BaseHTTPMiddleware`-based decorator with a **pure-ASGI middleware
class** that:

1. Intercepts the `http.response.start` ASGI message to observe the status code.
2. Passes every other ASGI message through unchanged (no buffering).
3. After the response stream finishes, fires `_invalidate_tracks_snapshot(app)`
   iff the original request was `POST/PUT/DELETE`, the path starts with `/api/`,
   AND the captured status was 2xx.
4. Swallows invalidation errors (same defensive try/except the current code has).

This is the standard fix for the BaseHTTPMiddleware streaming-response trap and
keeps the snapshot-invalidation behavior byte-for-byte identical for the
existing tests (`tests/test_snapshot_persistence.py`). It also fixes SSE
streaming responses, which are the actual production path the issue describes.

## Affected files

| File | Change |
|---|---|
| `autocue/serve/app.py` | Replace `@app.middleware("http")` block with a small `SnapshotInvalidationMiddleware` ASGI class + `app.add_middleware(...)` registration. |
| `tests/test_snapshot_persistence.py` | Add a regression test that POSTs to a route returning a `StreamingResponse` whose generator raises mid-stream; assert no `RuntimeError("No response returned.")` is raised and the snapshot still invalidates when status was 2xx. Existing decorator-based stubs in `_stub_middleware_app` are switched to use the new class so they exercise the same code path that ships. |

## Risks

- **Behavior change for non-2xx**: The pure-ASGI version captures the status
  from the first `http.response.start` message — same source of truth the
  current code uses (`response.status_code`). No semantic change.
- **`tracks_snapshot_lock` may be missing on the stub apps used by tests** —
  the new middleware reads `app.state` via the scope, so the stub fixtures
  need a `tracks_snapshot_lock` attribute. The existing stub apps already set
  one (`tests/test_snapshot_persistence.py:76, 105, 119`). No change needed.
- **CORS / GZip ordering** — `add_middleware` registers in LIFO order; the
  invalidation middleware does not touch headers or body so order is irrelevant.

## Test plan

| Leg | Why |
|---|---|
| A (`pytest -x -q`) | Touches `autocue/serve/app.py` + new test in `tests/test_snapshot_persistence.py`. Confirms existing snapshot-invalidation behavior + new streaming-response regression guard. |
| B (`npm test --silent`) | No `docs/` or `tests/web/` changes — but vitest run is cheap and a no-op confirms nothing was bumped accidentally. |
| C (e2e) | This IS the failing surface in the issue (`qa-smoke.spec.ts:134`). MUST run to close the loop. |

## Pass criteria

- New pytest: streaming response that aborts → no `RuntimeError`, snapshot
  invalidation fires when status was 2xx.
- All existing snapshot persistence tests stay green.
- qa-smoke "filter toggles do not crash the page" stays green.
