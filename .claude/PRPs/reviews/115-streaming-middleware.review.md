# Self-review — Issue #115 fix

## Verdict

**Approve.**

## Diff scope

- `autocue/serve/app.py` — replace `@app.middleware("http")` (BaseHTTPMiddleware)
  with a pure-ASGI `SnapshotInvalidationMiddleware` class registered via
  `app.add_middleware(...)`.
- `tests/test_snapshot_persistence.py` — add 6 regression tests covering
  the production middleware path, including a `StreamingResponse` whose
  generator raises mid-stream (the exact failure mode from issue #115).
- `.claude/PRPs/issues/115-streaming-middleware.investigation.md` — investigation
  artifact.

Total: 1 production-code file, 1 test file, ~120 lines added / 22 removed.

## Issues found

None.

## Verification

### Leg A — pytest -x -q

```
1331 passed, 4 skipped in 16.12s
```

All pre-existing snapshot-middleware tests (`test_middleware_*` in
`tests/test_snapshot_persistence.py`) still pass — the inline stubs they
use mirror the OLD behaviour exactly, and the public invariants
(POST 2xx → invalidate; GET → no-op; non-/api → no-op; 5xx → no-op) are
preserved by the new middleware.

### Leg B — npm test (vitest)

```
Test Files  28 passed (28)
     Tests  564 passed (564)
```

No web code changed; vitest is green for safety.

### Leg C — qa-smoke Playwright e2e (the canonical reproducer)

```
13 passed (34.5s)
```

Includes `qa-smoke.spec.ts:134:3 — Web UI smoke (local mode) > filter
toggles do not crash the page` — the exact failing test the QA agent
cited in issue #115. PASSES now (was timing out at 30 s before the fix
with `RuntimeError: No response returned` in the server log).

The pre-existing `per-control-sweep.selector.test.ts` import error in the
full e2e collection is unrelated to issue #115 and was already present
on `origin/main` (commit `adeee99`); not in scope for this PR.

## Why the tests would fail if the fix is reverted

- `test_pure_asgi_middleware_streaming_response_aborts_without_no_response_error`
  imports `SnapshotInvalidationMiddleware` directly; if you re-introduced
  the `@app.middleware("http")` wrapper the class wouldn't exist (or, if
  kept side-by-side, the same `StreamingResponse` test would surface
  `RuntimeError: No response returned` because BaseHTTPMiddleware's
  `call_next` would still raise — the test asserts that exact substring is
  absent).
- The four `test_pure_asgi_middleware_*` invariant tests
  (POST → invalidate, GET → no-op, non-/api → no-op, 5xx → no-op) exercise
  the actual production middleware class, not an inline stub, so any
  regression in `SnapshotInvalidationMiddleware`'s status-code / path /
  method logic would fail them directly.

## Patterns / types / security

- Middleware is async, scope-typed via runtime checks on `scope["type"]`,
  and never raises into the response stream (catches `Exception` around
  the invalidation call, like the original).
- No new dependencies, no widened CORS, no DB writes, no `master.db`
  contact.
- Middleware ordering preserved: CORS (outermost) → GZip →
  SnapshotInvalidationMiddleware → routes.
