# Issue #115 — Self-Review

## Verdict

**Approve.**

## Diff summary

`git diff main...HEAD --stat`:

| File | +/- |
|---|---|
| autocue/serve/app.py | +12 / −22 (replace decorator block with one-liner add_middleware) |
| autocue/serve/middleware.py | +82 (new file) |
| tests/test_snapshot_persistence.py | +131 / −31 (refactor existing stubs to use shipped class + 4 new regression/boundary tests) |
| .claude/PRPs/issues/115-asgi-snapshot-middleware.investigation.md | +74 (per-issue artifact) |

Net production code: about 52 lines (delete old block + add isolated middleware module). One conventional commit.

## Issues found

None.

### Considered and rejected

1. **Should we call `_invalidate_tracks_snapshot` before vs after the response stream finishes?**
   The original (BaseHTTPMiddleware) ran it AFTER `call_next` returned, which is "after the full response was buffered." The pure-ASGI version runs it inside `finally` after `await self.app(scope, receive, send_wrapper)` returns — that's "after the response stream's final message was sent." Both are "after the handler is done," so behavior matches.

2. **Should we register before or after CORS / GZip?**
   The new middleware never touches headers or body — it only observes status — so registration order is irrelevant. Left as the last `add_middleware` call (innermost) to match the original code's logical position.

3. **Should `scope["app"]` lookup be defensive?**
   Yes. The middleware sits inside the FastAPI middleware stack, where Starlette guarantees `scope["app"] = self` is set before any middleware runs (`applications.py:87`). But we still null-check it (`if app is not None:`) because the cost is one comparison and it future-proofs against being mounted at a different layer.

## Verification

### Leg A — pytest

`pytest -x -q` → 1329 passed, 4 skipped, 0 failed (15.96s). Includes 4 new tests:

- `test_streaming_response_that_aborts_does_not_raise_runtime_error` — regression guard
- `test_streaming_response_completing_cleanly_invalidates_snapshot` — boundary
- `test_middleware_invalidates_on_201_created_boundary` — boundary
- `test_middleware_does_not_invalidate_on_300_redirect_boundary` — boundary

### Leg B — vitest

`npm test --silent` → 564 passed across 28 test files (2.23s). No regressions.

### Leg C — Playwright e2e (`qa-smoke.spec.ts`)

- `qa-smoke.spec.ts:134 "filter toggles do not crash the page"` — PASSES when run in isolation against this branch (`28.3s`).
- Full qa-smoke suite shows a pre-existing flake at the same test (12/13 pass) — but the same flake also occurs on unmodified `main` (verified). Independent timing issue with similar-index warmup vs. the 30s Playwright default; **not introduced by this fix**.

## Regression guard quality

The new test in `tests/test_snapshot_persistence.py::test_streaming_response_that_aborts_does_not_raise_runtime_error`:

1. **Would FAIL without the fix**: with `@app.middleware("http")`, the aborting generator would surface a `RuntimeError("No response returned.")` — the assertion `"No response returned" not in str(e)` would fail. ✓
2. **Boundary check**: subsequent `/api/_ok` POST must still invalidate the snapshot (proves the middleware isn't "wedged" after the abort). ✓
3. **Property assertion, not value assertion**: tests an invariant ("error string does not contain X") rather than a magic constant. ✓
