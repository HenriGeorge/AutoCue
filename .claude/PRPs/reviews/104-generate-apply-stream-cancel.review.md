# Self-review — Issue #104 / fix branch `fix/104-generate-apply-stream-cancel`

## Verdict
**Approve.** Surgical fix that resolves the documented NameError + silent
swallow. Validation: Leg A (pytest) green; Leg B (vitest) green; Leg C
(Playwright) is broken at HEAD irrespective of this PR (pre-existing
`per-control-sweep.selector.test.ts` import error — see notes below).

## Issues Found
None blocking. Minor notes:

1. **Serial-path completion now sets `cancel`.** I added a `cancel.set()`
   at the end of the serial path so the async poll task exits cleanly
   (previously absent — the poll thread relied on the request scope
   teardown to die, which works for the parallel branch via the existing
   `cancel.set()` at line 937 but not for the serial branch). One-line
   defensive addition, no functional risk.

2. **Pre-existing e2e harness break.** `tests/e2e/per-control-sweep.selector.test.ts`
   imports `per-control-sweep.spec.ts` — Playwright forbids this and the
   suite never starts. Reproduces on `origin/main` (`9cea85b` at HEAD).
   Out of scope for this fix; flagged for a follow-up issue.

3. **`from concurrent.futures import as_completed`** is dead inside the
   parallel branch (the actual iteration uses `_wait_any`). Pre-existing;
   not removed to keep the diff scoped.

## Verification

### Correctness
- `request: Request` is now resolved at the FastAPI handler level — no
  more undefined-name NameError. Verified by `inspect.signature` in the
  new test.
- The route is `async def` — confirmed by `inspect.iscoroutinefunction`
  in the new test. The sync `event_stream()` runs inside `StreamingResponse`
  exactly as `library_health` and `classify_library` already do
  (`autocue/serve/routes.py:1389,1801`).
- The poll task uses `await request.is_disconnected()` (async, the only
  way to get starlette to actually observe the TCP close). Setting
  `cancel: threading.Event` from the event loop is safe — `Event.set()`
  is thread-safe.
- Exception narrowing: replaced bare `except Exception: return` with
  `except asyncio.CancelledError: return` in the new poll task. If the
  task is awaited after request scope teardown and starlette raises
  something other than CancelledError, it now surfaces.

### Security
- No CORS change.
- No `rekordbox_is_running` bypass — the existing `_rb_running(db)` guard
  at line 798 is untouched.
- No new file writes, no new privileges.

### Test quality
- **Regression guard**: `test_generate_apply_stream_has_request_injection`
  fails on the pre-fix signature (`def generate_apply_stream(req, db)`).
  Reverting either property (async OR Request param) breaks the test.
- **Boundary**: `test_generate_apply_stream_respects_cancel_before_emission`
  patches `starlette.requests.Request.is_disconnected` to always return
  True, then asserts max processed < N (here, N=20 tracks). This is a
  property-style invariant: "if cancel fires, work stops early" — not a
  hard-coded count comparison.
- Pre-existing tests (`test_bounded_in_flight_caps_at_2_x_pool_size`,
  `test_wait_any_helper_returns_completed_first`,
  `test_wait_any_empty_returns_empty_sets`,
  `test_disconnect_cancellation_event_present`) still pass — proves no
  collateral damage to TASK-040 + the structural TASK-041 guard.

### Patterns
Matches the project's existing async-SSE pattern (`library_health`,
`classify_library`). Threading.Event bridge to async loop is the same
pattern as `autocue/serve/deps.py:330` (warmup_cancel_event).

### Types
The signature `async def generate_apply_stream(req, request, db)` — the
parameter order matters for FastAPI: ``request: Request`` after the
pydantic body and before the ``Depends`` is the conventional ordering
(see ``tracks(response, request, ..., db=Depends(...))`` at line 184).
