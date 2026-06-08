# Issue #107 — perf-prd: TASK-039/040 producer/consumer implementation diverges from PRD spec

## Problem
PRD TASK-039/040 prescribe an explicit producer/consumer split for
`/api/generate-apply-stream`:
- Factored `_compute_stage(content_ids, db, prefs, queue, cancel_event)` /
  `_writer_stage(queue, db, send_event, cancel_event)` functions.
- Backpressure via `queue.Queue(maxsize=2 * pool_size())`.
- Sentinel `None` on the queue to signal end-of-stream.
- Separate writer thread; compute stage submits to the pool and pushes
  results onto the queue as they complete.

The shipped implementation at `autocue/serve/routes.py:879-929` instead
keeps everything inside `event_stream()`:
- `in_flight: dict = {}` keyed by Future, capped at `2 * pool_size()` via
  a manual top-up loop.
- A `_wait_any(in_flight)` helper that wraps
  `concurrent.futures.wait(..., FIRST_COMPLETED)`.
- The SSE generator thread does BOTH compute submission AND the writes —
  there is no separate writer thread.
- No `queue.Queue`, no sentinel.

Memory IS bounded (in-flight ≤ `2 * pool_size`, ~16 outstanding by
default) and per-track commit semantics survive, but the structural
shape doesn't match the PRD and the helper `_wait_any` has a vacuous
test (`tests/test_generate_apply_bounded.py:90` only asserts
`hasattr(routes_mod, '_wait_any')`).

## Root Cause
The implementer chose a simpler in-loop pattern instead of the explicit
producer/consumer architecture the PRD calls for. The PRD acceptance
criteria for TASK-039/040 were marked `passes: true` despite the
structural divergence.

## Proposed Solution (Option 1 from the issue)
Refactor `event_stream()` to use the spec'd producer/consumer pattern:

1. Add module-level `_compute_stage(content_ids, submit_fn, generate_fn,
   phrase_only, q, cancel)` that walks `content_ids`, submits work to the
   thread pool, drains completions via `concurrent.futures.as_completed`
   (with a bounded in-flight cap to preserve TASK-040's memory bound),
   and `q.put(result, block=True, timeout=10)`s each result onto the
   shared queue. Pushes a final `None` sentinel and returns.
2. Add module-level `_writer_stage(q, db, write_fn, overwrite, dry_run,
   send_event, cancel, total)` that runs on a dedicated thread; loops
   `q.get()`, breaks on `None`, performs the per-track commit, emits an
   SSE event, accumulates `applied/skipped`. Returns the final totals.
3. Wire from `event_stream()`: spawn the writer thread, run compute
   inline from the request thread (it's already a background thread vs.
   the FastAPI event loop), then `writer_thread.join()` and yield the
   `done:true` event.
4. Keep `AUTOCUE_PARALLEL_GENERATE_APPLY=0` serial fallback unchanged.
5. Make SSE events thread-safe by using a small `queue.Queue` for events
   (writer puts events on it; the SSE generator drains it). This keeps
   the existing yield-from-generator pattern intact.

The bounded queue between the two stages is `queue.Queue(maxsize=max(2,
2 * pool_size()))`. Sentinel `None` signals "no more results" from
compute → writer. A second event queue carries the SSE payload strings
from the writer thread back to the request thread (it's the only
thread that can `yield`).

### Why this is safer than it sounds
- Per-track commits stay on a single thread (the writer thread) — SQLite
  single-writer rule preserved.
- The pool is unchanged; we still use the process-singleton.
- `_wait_any` becomes dead code, but the dedicated unit test for it
  stays valid (it tests the wait wrapper, not the call site); we can
  remove the vacuous `hasattr` assertion in `test_disconnect_cancellation`
  and replace it with a real structural check.

## Affected Files
- `autocue/serve/routes.py` — extract `_compute_stage`, `_writer_stage`;
  rewire `event_stream()`'s parallel branch. Remove `_wait_any` (now
  unused) OR keep it for the existing unit test; preference: keep
  helper, drop the vacuous test.
- `tests/test_generate_apply_bounded.py` — replace the vacuous
  `hasattr(_wait_any)` assertion with a real structural assertion
  (module exposes `_compute_stage` and `_writer_stage`); add backpressure
  test (TASK-040 step 3) that asserts queue size stays ≤ maxsize when
  the writer is artificially slow.
- `tests/test_concurrency.py` — add the producer/consumer backpressure
  test the PRD mentions explicitly under `_compute_stage` semantics.

## Risks
- SSE event ordering: writer thread emits one event per result in the
  order it drains the queue. Compute stage drains `as_completed` order
  too — already non-deterministic in the shipped code, so the change is
  no worse.
- Disconnect cancellation: the `cancel` event is set by the existing
  poll thread; both compute and writer must check it between iterations
  to drain promptly. We add explicit checks.
- Pool starvation: if the writer thread crashes before draining the
  queue, the compute stage would block forever on `put`. The 10s
  timeout + cancel check makes this self-limiting (log + check, then
  give up).

## Test plan
- Regression-guard: a test that constructs `_compute_stage` + a tiny
  in-memory writer, pushes >2*pool_size results in, and asserts queue
  size never exceeds maxsize.
- Sentinel: a test that asserts `_writer_stage` returns on the `None`
  sentinel.
- Integration: keep `test_bounded_in_flight_caps_at_2_x_pool_size` —
  it exercises the SSE endpoint end-to-end and must still see all 50
  tracks processed.
- Existing `_wait_any` unit tests stay as-is (helper is preserved).
