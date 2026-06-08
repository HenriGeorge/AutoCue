# Issue #115 — `_invalidate_snapshot_on_mutation` raises `No response returned`

## Problem

`autocue/serve/app.py:64-80` registers `_invalidate_snapshot_on_mutation` via the
decorator `@app.middleware("http")`. That decorator wraps the function in
Starlette's `BaseHTTPMiddleware`, which is documented as **incompatible with
`StreamingResponse`** (see encode/starlette#1925).

When the downstream handler returns a `StreamingResponse` whose generator
aborts or never yields a body (e.g. client disconnect, internal exception in
the generator), `BaseHTTPMiddleware.call_next` raises:

```
RuntimeError: No response returned.
```

In the QA Playwright run, clicking `#phrase-only-cb` triggered a filter-toggle
fetch that hung on `await call_next(request)`, and the test timed out at 30 s
with the error surfaced in the server log.

## Root cause

`autocue/serve/app.py:65-80` — `BaseHTTPMiddleware` interaction with
`StreamingResponse`. The mutating endpoints that return streaming bodies
(`/api/generate-apply-stream`, `/api/color-tracks-stream`,
`/api/cue-tools-stream`, `/api/enrich-comments/stream`,
`/api/download/stream/*`) are exactly the ones that hit the bug.

## Proposed solution

Replace the `@app.middleware("http")` registration with a **pure ASGI
middleware** that wraps the `send` callable to observe the response status
code, then invalidates the snapshot after the response completes
successfully. Pure ASGI middleware is not subject to the
`BaseHTTPMiddleware` streaming-response trap because it never awaits a
materialised response — it forwards ASGI events as they happen.

Behaviour preserved (existing tests in `tests/test_snapshot_persistence.py`):
- POST/PUT/DELETE to `/api/...` with 2xx → snapshot invalidated.
- GET → no-op.
- Non-`/api/` path → no-op.
- 5xx → no-op.

New behaviour:
- StreamingResponse bodies never raise `No response returned`; the snapshot
  invalidation runs once the first response chunk (start event) is seen with
  a 2xx status, regardless of whether the body generator later aborts.

## Affected files

- `autocue/serve/app.py` — swap the decorator for a pure-ASGI middleware
  class registered via `app.add_middleware(...)`.
- `tests/test_snapshot_persistence.py` — add a regression test that posts to
  a `StreamingResponse` endpoint whose generator raises mid-stream and
  asserts (a) no `RuntimeError` propagates, and (b) the snapshot is still
  invalidated when the start-event status was 2xx. The existing tests stay
  green because the public behaviour is unchanged.

## Risks

- Pure ASGI middleware reads `scope["path"]` / `scope["method"]` directly —
  must match how the decorator version read `request.url.path` /
  `request.method`. Verified equivalent for `/api/...` HTTP requests.
- The middleware must only act on `scope["type"] == "http"` (skip
  `lifespan` and `websocket`). Handled.
- Invalidating `request.app.state` from middleware needs `scope["app"]`
  which Starlette populates. Handled.
