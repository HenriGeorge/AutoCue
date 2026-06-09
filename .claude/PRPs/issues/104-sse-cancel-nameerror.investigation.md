# Issue #104 — `/api/generate-apply-stream` cancellation broken (TASK-041 NameError)

## Problem
`_poll_disconnect` (autocue/serve/routes.py:868) references the name `request`,
but the enclosing endpoint `generate_apply_stream(req, db=Depends(get_db))`
(autocue/serve/routes.py:774) never accepts a `Request` parameter. On the
first iteration the poll thread raises `NameError: name 'request' is not
defined`, which the bare `except Exception: return` silently swallows. The
thread exits immediately; the `cancel` event is never set on a real client
disconnect; the SSE stream keeps computing + writing after the tab closes.
TASK-041's acceptance is silently false.

## Root cause (file:line)
- `autocue/serve/routes.py:774` — `def generate_apply_stream(req, db=...)` —
  no `request: Request` parameter.
- `autocue/serve/routes.py:861-874` — `_poll_disconnect` references a free
  name `request` that resolves to a `NameError` at runtime.
- `autocue/serve/routes.py:872-873` — bare `except Exception: return` hides
  the bug.

Additionally, even if `request` were in scope, `request._is_disconnected` is
only set as a side-effect of awaiting `request.is_disconnected()`. The
attribute starts as `False` and stays `False` until that coroutine runs.
So polling the bare attribute from a sync thread would never become True.
The correct primitive is to drive `await request.is_disconnected()` from a
private asyncio event loop on the poll thread.

## Proposed solution
1. Add `request: Request` to the endpoint signature so the closure captures it.
2. Replace the bare-attribute poll with a small private asyncio loop on the
   poll thread that calls `await request.is_disconnected()` every ~0.2 s.
3. Narrow the `except Exception` to `(RuntimeError, asyncio.CancelledError)`
   so unrelated bugs (e.g. AttributeError) actually surface.
4. Add a regression test that:
   - Constructs the route closure with a stub Request whose
     `is_disconnected` coroutine flips True on first call.
   - Asserts the `cancel` event is set on the next poll tick.
   - Boundary case: a stub whose `is_disconnected` raises `RuntimeError` (the
     narrowed exception path) — the thread must exit cleanly without setting
     `cancel`.
5. Add a regression test that fails on the OLD code: instantiate the route
   in a `TestClient`, post to `/api/generate-apply-stream` against the
   parallel path; if the poll thread crashes the cancel event never gets set
   on disconnect — assert the new path receives a disconnect signal.

## Affected files
- `autocue/serve/routes.py` — endpoint signature + `_poll_disconnect` body
  + narrowed exception.
- `tests/test_generate_apply_bounded.py` — replace the structural
  `test_disconnect_cancellation_event_present` with a real behavioural test;
  add the regression + boundary cases above.

## Risks
- `Request` injected by FastAPI must not change cache/DI behaviour; it
  doesn't — `Request` is a per-call dependency, not a singleton.
- Running an `asyncio.run()` per poll tick is wasteful — use a single loop
  on the thread (`asyncio.new_event_loop()` once, `loop.run_until_complete`
  per tick) so we keep the same low overhead as the bare-attribute version.
- The poll thread is daemon and stops when `cancel` is set OR when the
  generator returns and flips `cancel.set()`. Behaviour is unchanged.
- Single-writer rule for `master.db` is preserved — cancellation only
  short-circuits the loop; it never adds a concurrent writer.

## Test plan
- pytest `tests/test_generate_apply_bounded.py` (regression + boundary).
- pytest `tests/test_generate_apply_parallel.py` (existing parallel path must
  still pass — signature change is additive).
- vitest unchanged — no UI surface change.
- Manual: `autocue serve`, start a stream, close the tab, observe the
  process log no longer logs continued writes (out of scope for this PR;
  unit tests cover the structural fix).
