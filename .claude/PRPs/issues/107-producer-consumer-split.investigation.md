# Issue #107 — perf-prd: TASK-039/040 producer/consumer implementation diverges from PRD spec

## Problem

PRD TASK-039/040 require a factored producer/consumer architecture for
`/api/generate-apply-stream`:

- `_compute_stage(content_ids, db, prefs, queue, cancel_event)` — parallel
  ANLZ read + cue generation.
- `_writer_stage(queue, db, send_event, cancel_event)` — single writer thread
  draining the queue, committing per-track.
- Bounded `queue.Queue(maxsize=2 * pool_size())` between them.
- Sentinel `None` to signal end-of-stream.
- Cancellation via shared `threading.Event`.

The shipped implementation at `autocue/serve/routes.py:847-974` uses a single
inline generator function with an in-flight `dict[Future, tid]` keyed by
`Future`, a `_wait_any` helper, and no separate writer thread. The dict
*does* bound memory (≤ `2 * pool_size`) and the SSE output is correct, but:

1. The architecture is not factored — impossible to unit-test the compute or
   writer stages in isolation, as the PRD calls out (TASK-039 acceptance #5).
2. There's no `queue.Queue` and no sentinel — the bounded-queue invariants
   that TASK-040 calls for (`qsize() <= maxsize at all times` under
   compute-faster-than-writer) can't be directly asserted.
3. `_wait_any` has a vacuous test (`tests/test_generate_apply_bounded.py:90`
   only asserts `hasattr(routes_mod, '_wait_any')`) — dead-end ergonomics.

## Root cause

`autocue/serve/routes.py:847-974` — the SSE generator was implemented as one
function with the simplest in-flight tracker (a dict keyed by `Future`),
which works functionally but skips the producer/consumer factoring promised
by the PRD spec.

## Proposed solution

**Option 1 from the issue body** — refactor to match spec:

1. Extract `_compute_stage(content_ids, db, prefs, q, cancel_event,
   phrase_only)` — submits one `pool.submit(_compute_one, tid)` per id, pushes
   tuples into `q` as `as_completed`. On cancel, stops submitting new
   futures and lets in-flight resolve. Always pushes a single `None`
   sentinel before returning (incl. on cancel and on exception).
2. Extract `_writer_stage(q, db, prefs, dry_run, overwrite, send_event,
   cancel_event)` — `q.get()` loop, `None` sentinel terminates. Per-track
   write happens through existing `write_cues_to_db`. Emits SSE via the
   `send_event` callable.
3. Replace the inline loop in `event_stream()` with: spawn writer thread,
   call `_compute_stage` from a worker thread, drain a thread-safe SSE event
   queue back to the SSE generator.
4. Bounded queue `queue.Queue(maxsize=max(2, 2 * pool_size()))`.
5. Cancellation through the existing `cancel` `threading.Event` (already in
   place for TASK-041).

Behavior is identical to today; only the architecture changes.

## Affected files

- `autocue/serve/routes.py` — extract `_compute_stage`, `_writer_stage`;
  rewire the SSE generator; remove `_wait_any` (no longer used).
- `tests/test_generate_apply_bounded.py` — replace the vacuous `_wait_any`
  tests with focused unit tests for `_compute_stage` and `_writer_stage` in
  isolation, plus a backpressure test (qsize never exceeds maxsize).
- `tests/test_concurrency.py` — add the backpressure test called out in
  TASK-040 step 3.

## Risks

- Threading bug — the SSE generator must not block; an internal event queue
  must use `block=False` or short timeouts to keep yielding control. The
  cancel event must propagate to both stages.
- `db.session` thread-safety: with the new layout, the writer thread is the
  sole owner of `db.session` for writes. The compute thread only does
  `db.get_content` + ANLZ reads (which TASK-008 verified as thread-safe).
- Test fixtures using `MagicMock` for `db` — must still work; the existing
  `tests/test_generate_apply_bounded.py` fixtures should keep passing.
