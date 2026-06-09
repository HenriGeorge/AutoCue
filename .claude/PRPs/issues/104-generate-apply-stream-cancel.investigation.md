# Issue #104 — `generate_apply_stream` cancellation is broken (TASK-041 NameError)

## Problem
`/api/generate-apply-stream` never honours client disconnect. The
`_poll_disconnect` helper at `autocue/serve/routes.py:868` references a
`request` name that doesn't exist in scope (`generate_apply_stream(req, db)`
has no `request: Request` parameter, line 774). Every poll iteration raises
`NameError`, which is swallowed by `except Exception: return`, so the thread
exits silently. The compute + writer loop keeps running until every track in
`req.track_ids` is processed, even after the browser tab closes.

PRD acceptance criterion **TASK-041** ("Cancellation on SSE client
disconnect") is silently false.

## Root Cause
- `autocue/serve/routes.py:774` — `def generate_apply_stream(req: GenerateAndApplyRequest, db=Depends(get_db))`. No `Request` injected.
- `autocue/serve/routes.py:868` — `_poll_disconnect()` references `request`, undefined → `NameError`.
- `autocue/serve/routes.py:872-873` — bare `except Exception: return` masks the bug.
- Even with `request` in scope, polling `request._is_disconnected` from a
  sync thread does NOT work: starlette only sets that flag inside
  `await request.is_disconnected()`. A sync thread cannot await — the flag
  is always `False`.

## Proposed Solution
1. Make `generate_apply_stream` an **async** handler and inject
   `request: Request`. This matches the pattern already used by
   `library_health` (async) and `classify_library` (async).
2. Inside the async handler, spawn `asyncio.create_task(_poll(request, cancel))`
   that awaits `request.is_disconnected()` every 200ms and sets the
   `threading.Event` shared with the sync compute/writer loop.
3. Run the existing sync `event_stream` generator via the StreamingResponse;
   no change to the compute/writer flow needed beyond keeping the existing
   `cancel.is_set()` checks (they already exist at lines 893 and 921).
4. Replace the bare `except Exception: return` with the explicit
   `except (RuntimeError, asyncio.CancelledError): return` per the issue's
   suggested fix, so a real bug surfaces in the future.
5. Remove the dead `poll_thread` (threading) and `_poll_disconnect` sync
   polling. Replace with the async task.
6. Cancel the async poll task when the stream finishes (via `cancel.set()`
   + task.cancel() in a `finally`).

## Test (regression guard)
`tests/test_generate_apply_bounded.py::test_disconnect_cancellation_event_present`
exists but is structural-only (asserts `_wait_any` is importable). Add a
real regression test that:
- Patches `_poll` to immediately set the cancel event after a few iterations,
  asserts the stream stops emitting events before all tracks are processed.
- A "boundary" assertion: with the cancel event set BEFORE the first
  iteration, **zero** track-level work runs.
- A "regression" assertion: reverts to the pre-fix `def` signature would
  fail with `NameError`, but since we can't easily monkey-patch the
  signature, the indirect guard is: the route accepts a `Request` injection
  (FastAPI will resolve `request: Request` automatically) — confirm by
  importing the route and inspecting its signature contains a parameter
  annotated `Request`.

## Affected Files
- `autocue/serve/routes.py` — change `generate_apply_stream` signature + poll.
- `tests/test_generate_apply_bounded.py` — strengthen the disconnect test.

## Risks
- **Async-handler regression for the sync generator.** StreamingResponse
  accepts sync generators in async handlers (the existing
  `library_health` route does exactly this) — safe.
- **Thread interplay.** The async poll task lives on the asyncio loop;
  the compute pool + writer loop live on the sync threadpool. They
  communicate only via `cancel: threading.Event`, which is thread-safe.
- **`request.is_disconnected()` cost.** Async, non-blocking, drains one
  message from receive. Called every 200ms — negligible.
- **Existing serial path** (`AUTOCUE_PARALLEL_GENERATE_APPLY=0`) is below
  the parallel branch and does NOT have any cancel hook. The issue is
  scoped to the parallel path; the serial path is unchanged here to keep
  the diff under the 50-line preference. (A follow-up could add cancel
  to the serial path if needed; not in this PR.)
