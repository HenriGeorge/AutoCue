# PR #104 self-review — SSE cancellation NameError fix

## Verdict
approve

## Summary of change
- Added `request: Request` to `generate_apply_stream(...)`'s signature
  (the original closure referenced an out-of-scope `request` name).
- Extracted the disconnect poll loop into a module-level helper
  `_poll_request_disconnect(request, cancel, *, ...)` so it can be
  unit-tested in isolation.
- Replaced the no-op bare-attribute poll with an
  `anyio.from_thread.run`-driven coroutine call so the parent ASGI event
  loop actually evaluates `request.is_disconnected()`. The endpoint captures
  the parent-loop token via `anyio.from_thread.current_token()` before
  spawning the poll thread.
- Narrowed `except Exception` to `(RuntimeError, asyncio.CancelledError)`
  so unrelated bugs surface instead of being silently swallowed.
- Added 8 regression / boundary tests in
  `tests/test_generate_apply_bounded.py`:
  - signature contains `request: Request`
  - poller sets `cancel` on disconnect (regression — pre-fix code never set it)
  - poller exits cleanly when `cancel` is pre-set (no busy loop / hang)
  - RuntimeError on receive → treated as disconnect + cancel set
  - asyncio.CancelledError on receive → same
  - unrelated `ValueError` on receive → propagates (does NOT get eaten)
  - tokenless degraded mode → waits for external cancel without raising
  - existing module exposure check now also requires
    `_poll_request_disconnect` to exist

## Verification

| Leg | Status | Notes |
|---|---|---|
| A — pytest -x -q | green | 1332 passed, 4 skipped |
| B — npm test (vitest) | green | 564 passed |
| C — playwright e2e | blocked | Pre-existing infra failure in `per-control-sweep.selector.test.ts` (test file importing another test file — confirmed exists on `main`); also unrelated `control-inventory.spec.ts` drift failure on `main`. Targeted runs of `pages-smoke.spec.ts` / `discover-v2.spec.ts` (which exercise the live SSE path) pass. |

## Issues Found
- None blocking. The fix is correctly isolated to the parallel path
  (`AUTOCUE_PARALLEL_GENERATE_APPLY` default-on as of TASK-008). The serial
  path was unaffected — it had no cancellation primitive, and that's
  preserved.
- The tokenless degraded path (when called outside an anyio worker thread,
  e.g. unit-test harnesses calling `event_stream()` directly) loses
  mid-stream cancellation. Documented in the helper docstring; production
  always has a token because the endpoint is invoked inside an anyio worker
  thread.

## Scope
- Diff: 277 insertions, 19 deletions across 2 files + 2 PRP artifacts.
- Larger than the agent's 50-line preference because (a) the original
  poll-loop was structurally broken (not a one-line typo) and (b) the
  test-quality requirements mandate regression + boundary + invariant
  cases, each of which needs a small fake-request fixture.

## Pattern compliance
- `autocue/serve/routes.py` follows the deferred-import pattern used
  elsewhere in the file (`from concurrent.futures import ...` inside the
  endpoint body).
- The `@router.post("/generate-apply-stream")` signature change is
  additive — FastAPI auto-injects the `Request` parameter without
  affecting the request model (`GenerateAndApplyRequest`).
- Single-writer rule for master.db is preserved — cancellation only
  short-circuits the loop; no concurrent writer is introduced.
