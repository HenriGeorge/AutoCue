# Self-review — #107 producer/consumer refactor

## Verdict
**Approve** — implementation matches PRD TASK-039/040 spec; tests
include the regression guards and boundary properties the agent
contract requires.

## Issues found
None blocking. Notes:

- **Diff size (~256 LOC added in routes.py)** exceeds the ≤50-line
  agent preference, but the issue explicitly asks for the structural
  refactor (extract two new module-level functions with full docstrings
  + retain backwards-compatible wiring). The bulk is comments +
  docstrings; net behavior change is the wiring substitution in
  `event_stream()`.
- **Pre-existing e2e collection failure**
  (`tests/e2e/per-control-sweep.selector.test.ts` imports
  `per-control-sweep.spec.ts`) blocks the full Playwright suite. This
  is on `origin/main` (see `git log adeee99`); my change does not
  introduce it and fixing it is out of scope per the safety contract
  (rule 7: "Fix ONLY what the issue describes"). The pages-smoke spec
  passes against the new server, confirming the refactor does not
  regress server startup or basic UI delivery.
- `_wait_any` retained (used inside `_compute_stage` for the future
  draining loop; covered by the existing unit tests).

## Verification
- **Leg A (pytest)**: `1329 passed, 4 skipped in 16.57s` — full suite
  green including the 8 tests in `test_generate_apply_bounded.py`
  (4 new producer/consumer tests + 4 existing).
- **Leg B (vitest)**: `28 files, 564 passed in 2.17s`.
- **Leg C (Playwright)**: blocked by pre-existing collection error
  (see above). Individual `pages-smoke.spec.ts` run against the new
  server passes (`1 passed (3.5s)`), confirming server boot + page
  delivery survives the refactor.

## Test quality audit (per agent contract)
For each new test:

1. **Regression guard (fails without fix)** —
   - `test_compute_stage_pushes_sentinel_on_cancellation` would fail
     if the `finally: q.put(_COMPUTE_DONE)` were removed; the writer
     would hang forever on `q.get()`.
   - `test_writer_stage_drains_until_sentinel` would fail without the
     `if item is _COMPUTE_DONE: break` branch — writer would loop.
   - `test_compute_writer_backpressure_queue_never_exceeds_maxsize`
     would fail (or never terminate, or queue.qsize > maxsize) if
     `q.put` used `block=False` or the queue lacked a `maxsize`.
2. **Boundary case** — backpressure test sets `maxsize=4`, with a
   pool of 4 and 30 items; `qsize` must hit but never exceed 4 under
   fast-producer/slow-consumer pressure.
3. **Property assertion (not specific values)** —
   `max(observed_sizes) <= maxsize` is the invariant; not a single
   golden value.

## Safety contract
- Sandbox-only DB writes: untouched (writer still calls
  `write_cues_to_db` against the `db` injected by `Depends(get_db)`).
- `db_writer.rekordbox_is_running()` check still fires at the top of
  the endpoint (`_rb_running(db)`); refactor sits below that gate.
- No `master.db` files, secrets, CORS changes, or pre-commit bypasses.
- No documented features removed from `docs/qa_*` or
  `docs/FEATURES.md`.
