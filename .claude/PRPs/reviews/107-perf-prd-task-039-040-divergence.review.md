# Self-review — Issue #107

## Verdict

**Approve.** Implementation now matches PRD §4.6 (TASK-039 + TASK-040 + TASK-041).

## What changed

`autocue/serve/routes.py`:
- Replaced `_wait_any` (thin wrapper over `concurrent.futures.wait`) with two
  module-level helpers — `_compute_stage` and `_writer_stage` — that match the
  PRD shape exactly.
- Rewired the `/api/generate-apply-stream` parallel path to use:
  - `queue.Queue(maxsize=2 * pool_size)` between stages (TASK-040 buffer).
  - Background thread for compute stage; background thread for writer stage
    (TASK-039 single-writer rule preserved — only the writer thread calls
    `write_cues_to_db`).
  - `None` sentinel pushed by compute to signal end-of-stream (TASK-039/041 —
    pushed even on cancel so the writer never deadlocks).
  - Second small `events_q` for SSE event strings, drained by the FastAPI
    generator so yields stay correctly ordered with writer progress.
  - Existing `threading.Event` cancel signal (TASK-041) is now observed by
    BOTH stages — compute stops submitting and exits with sentinel; writer
    drains the queue without further writes.

`tests/test_generate_apply_bounded.py`:
- Replaced the vacuous `_wait_any` tests (which only asserted `hasattr`) with
  five new stage-level unit tests + one end-to-end SSE regression test:
  1. `test_compute_stage_pushes_sentinel_when_drained` — N inputs → N results +
     exactly 1 sentinel.
  2. `test_compute_stage_caps_in_flight_at_max_in_flight` — TASK-040 hard cap;
     observes live worker concurrency and fails if it exceeds the cap.
  3. `test_compute_stage_pushes_sentinel_on_cancel` — TASK-041 deadlock guard:
     cancel BEFORE start, sentinel MUST still be pushed.
  4. `test_writer_stage_stops_on_sentinel` — boundary at the exact sentinel.
  5. `test_writer_stage_counts_skips_for_empty_cues_and_errors` — skip flag,
     empty cues, write_fn raising, write_fn returning 0 all count as `skipped`.
  6. `test_writer_stage_drains_queue_on_cancel_without_writing` — reverse-side
     of the deadlock guard.
  7. `test_generate_apply_stream_processes_every_track` — end-to-end SSE
     regression at 50 tracks with the queue / sentinel / two-thread plumbing.

## Audit

| Concern | Assessment |
|---|---|
| Correctness | Stage contract reviewed; sentinel pushed exactly once via `try/finally`. Writer's `q.get()` is unblocked even on cancel (compute pushes sentinel in `finally`). SSE generator's `events_q` is also sentinel-terminated by the writer thread's `finally`. |
| Single-writer rule on `master.db` | Preserved — `_write_one` (closure over `write_cues_to_db`) is only called from `_writer_stage`. |
| Bounded memory | `work_q` is `maxsize=2*pool_size`. Compute's `q.put` blocks when full → natural backpressure. In-flight futures are also capped at `2*pool_size` inside `_compute_stage`. |
| Ordering of SSE events | Writer is the sole `_on_event` caller; serial draining of `work_q` keeps `processed` monotonic. Same as previous behavior. |
| Test quality (would tests fail if fix reverted?) | Yes — reverting to the dict-of-futures pattern would break the `_compute_stage` import line; the cap test exercises live concurrency observation; the sentinel-on-cancel test would deadlock without the `finally`. |
| Security / safety contract | No master.db touched, no CORS change, no auth bypass, no `--no-verify`. |
| Patterns / types | New helpers are module-level and importable; type annotations on signatures (PEP 604). |
| Diff scope | ~227 LOC in routes.py (algorithm refactor, not drive-by) + ~331 LOC in tests (replacing one vacuous test file). Larger than the 50-line preference, but the issue explicitly requested a refactor (Option 1) and the change is contained to the parallel path. |

## Validation

- Leg A (`pytest -x -q`): **PASS** — 1328 passed, 4 skipped (no regressions; 7 new tests in `test_generate_apply_bounded.py`).
- Leg B (`npm test --silent`): **PASS** — 564 passed across 28 files.
- Leg C (`cd tests/e2e && playwright test`): **BLOCKED — pre-existing**.
  Playwright's test collector rejects the whole project because
  `tests/e2e/per-control-sweep.selector.test.ts` imports
  `tests/e2e/per-control-sweep.spec.ts`, which is now treated as a test file
  by Playwright 1.48+. This file pair predates issue #107 (last touched in
  PR #24, 2026-06-07) and is independent of this fix's scope. The fix does
  not touch any e2e files; the e2e leg is reported as blocked in the PR body
  so a follow-up can address the playwright config separately.

## Issues found in self-review

None requiring fix in this PR. The e2e leg blockage is out of scope.
