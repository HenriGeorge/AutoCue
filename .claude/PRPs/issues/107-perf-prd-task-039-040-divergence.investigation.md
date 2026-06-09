# Issue #107 — perf-prd: TASK-039/040 producer/consumer implementation diverges from PRD spec

## Problem

PRD §4.6 (TASK-039/040) specifies the `/api/generate-apply-stream` parallel path as a true
producer/consumer with:

- A factored `_compute_stage(...)` (compute side) and `_writer_stage(...)` (writer side).
- A bounded `queue.Queue(maxsize=2*pool_size)` between them.
- A sentinel `None` to signal end-of-stream from compute → writer.
- A separate writer thread (preserves single-writer SQLite semantics on `master.db`).

Actual implementation at `autocue/serve/routes.py:882-927` inlines everything in the
SSE `event_stream()` generator:

- One unfactored block — no `_compute_stage` / `_writer_stage` helpers.
- An in-flight `dict[Future, content_id]` keyed by `Future`, drained by a `_wait_any`
  wrapper over `concurrent.futures.wait(..., FIRST_COMPLETED)`.
- No `queue.Queue`, no sentinel, no separate writer thread.

Memory IS bounded (in-flight count ≤ `2 * pool_size`) so behavior is roughly
equivalent — but the PRD acceptance criteria are unmet and the architecture is
harder to test in isolation. The existing test `test_disconnect_cancellation_event_present`
is vacuous (only asserts `hasattr(routes_mod, '_wait_any')`).

## Root cause (file:line)

- `autocue/serve/routes.py:882-889` — `in_flight: dict = {}` + future-keyed
  draining (no `queue.Queue`).
- `autocue/serve/routes.py:148-158` — `_wait_any` helper (only consumer of the
  dict-keyed pattern; can be removed once the queue model lands).
- `tests/test_generate_apply_bounded.py:68-100` — tests assert the dict/wait-any
  shape rather than the queue-based contract.

## Proposed solution

Refactor the parallel path (TASK-039/040 spec-aligned) without behavior changes:

1. **Module-level helpers** in `autocue/serve/routes.py`:
   - `_compute_stage(track_iter, pool, work_fn, q, cancel, max_in_flight)` —
     submits up to `max_in_flight` (= `2*pool_size`) futures at a time, drains
     completed ones into `q`, pushes a final sentinel `None` when exhausted or
     cancelled.
   - `_writer_stage(q, write_fn, on_progress, cancel)` — pulls tuples from `q`
     until it sees the sentinel, performs the per-track write, calls
     `on_progress(applied, skipped)` so the SSE generator can emit events.
2. **Wire** the SSE `event_stream()` to use `queue.Queue(maxsize=2*pool_size)`,
   spawn the compute stage as a background `threading.Thread` (writer stays on
   the SSE-producing thread so its emission stays correctly ordered with the
   yield site).
3. **Drop** `_wait_any` after the migration; replace its (currently vacuous)
   tests with tests that exercise the queue-based stages directly:
   - `test_compute_stage_pushes_sentinel_when_drained` — feed N inputs, assert
     compute pushes N tuples + 1 sentinel.
   - `test_compute_stage_caps_in_flight_at_2x_pool_size` — regression for
     TASK-040 bound (FAILS if the cap is removed).
   - `test_writer_stage_stops_on_sentinel` — boundary case at the exact
     sentinel; writer returns after consuming `None`.
   - `test_cancel_event_short_circuits_compute_stage` — boundary at
     `cancel.set()`; compute stops submitting and still pushes a sentinel so the
     writer drains and exits cleanly (no deadlock).
   - Existing TASK-040 cap test continues to pass through the SSE endpoint
     (regression guard via the real route).

### Why two helpers + a queue (and not just two helpers wrapping the dict)

The dict-of-futures pattern conflates the queue (bounded buffer) and the work
unit (future). Splitting into `Queue` + `Thread` makes each stage testable
without spinning up FastAPI / TestClient — the new unit tests do exactly that.

## Affected files

- `autocue/serve/routes.py` — extract `_compute_stage`, `_writer_stage`; remove
  `_wait_any`; rewire `event_stream()` to use them.
- `tests/test_generate_apply_bounded.py` — replace the vacuous `_wait_any`
  tests with the stage-level tests above; keep / adapt the 50-track end-to-end
  cap test (regression guard).
- `.agent/prd/PERFORMANCE_PRD.md` — no change required; PRD already specifies
  the target.

## Risks

- **Ordering**: SSE events MUST stay monotonic on `processed`. Writer stage
  drains the queue serially so the order is exactly compute-completion order —
  same as the current pattern. The `processed` counter is owned by the writer
  stage.
- **Deadlock on cancel**: covered by the cancel test — compute MUST push a
  sentinel even on cancel, otherwise the writer (blocked on `q.get()`) hangs.
- **Single-writer rule on `master.db`**: preserved. Only the writer stage calls
  `write_cues_to_db` and `db.session.expire_all()`.
- **Backward compat**: serial path (`AUTOCUE_PARALLEL_GENERATE_APPLY=0`) is
  untouched.

## Validation plan

- Leg A (`pytest -x -q`) — new + existing tests in `tests/test_generate_apply_bounded.py`.
- Leg B (`npm test --silent`) — should be SKIPPED (touch log clean; only Python
  + tests/ touched). Will still run on the first iteration per the agent rules.
- Leg C (e2e) — should be SKIPPED if no `autocue/serve/**` shape changes
  break the SSE contract. Will still run on the first iteration.
