# Issue #106 — Self-review

## Verdict: APPROVE

## Diff size
- `autocue/serve/routes.py`: +53 / -3 (3 signature changes + 3 invalidation blocks).
- `tests/test_serve_routes.py`: +294 (one new test class, 9 tests).
- `.claude/PRPs/issues/106-…investigation.md`: +81 (Phase 1 artifact).

Inside the agent's preferred-budget ceiling (≤ 50 prod lines; tests are
intentionally larger to cover both branches of the streaming endpoint).

## Correctness audit

1. **Mixability invalidation runs at the right time** — after every
   per-track `write_cues_to_db(...)` returns `n > 0` and the loop drains.
   `write_cues_to_db` already calls `db.commit()`, so by the time we
   invalidate, the new cue rows are durable. No race with concurrent
   readers: future `/score` calls re-derive intro/outro from the freshly
   committed cues, then re-populate the L2 row.

2. **Boundaries are correct**:
   - `n=0` → no row appended → no invalidation. The boundary test
     `test_apply_does_not_invalidate_when_write_returned_zero` locks this.
   - `dry_run=True` → guarded by `if not req.dry_run` before the
     invalidation block. Tests cover both `/api/apply` and the stream
     endpoint.
   - `cache_store is None` → graceful skip. Test covers `/api/apply`.

3. **Failure containment** — `try/except Exception` around each
   `invalidate_mixability(cid)` ensures a corrupt sidecar row, a
   sqlite3.OperationalError under WAL contention, or any other failure
   never aborts the request. The user's cues were written; the cache
   row is at worst stale (the same state the bug shipped with — a strict
   non-regression).

4. **Streaming endpoint covers both compute paths** — the parallel
   branch (default) and the serial fallback both append to the same
   `written_ids` list inside the `event_stream` closure, and the same
   invalidation block runs in each path before yielding the final
   `done` SSE event. Two tests pin this:
   `test_generate_apply_stream_parallel_invalidates_mixability` and
   `test_generate_apply_stream_serial_invalidates_mixability`.

5. **Pre-existing latent bug surfaced for free** — the SSE poll thread
   referenced an undefined `request` symbol (line 868). Now that
   `request: Request` is on the signature, that reference resolves
   correctly. Not the issue's scope, but a positive side effect with
   no behavior change (the previous `hasattr/try/except` swallowed the
   NameError).

## Security
- No new attack surface. No new untrusted input parsed.
- `getattr(request.app.state, "cache_store", None)` defends against the
  attribute being absent on stripped app instances.
- No CORS, auth, path-traversal, or secret-handling code touched.

## Test quality
- **Regression guard**: 4 tests fail loudly without the fix
  (`test_apply_invalidates_…`, `test_generate_apply_invalidates_…`,
  `test_generate_apply_stream_parallel_…`, `test_generate_apply_stream_serial_…`).
  Verified by stashing `autocue/serve/routes.py` and re-running the
  class: exactly those 4 fail; the 5 boundary tests stay green
  (correct — they assert "row STAYS" or "no crash", behaviors that hold
  in both pre- and post-fix code).
- **Boundaries** explicitly exercised: `n=0`, `dry_run=True`, no-cue,
  `cache_store=None`.
- **Both branches of the streaming endpoint** covered via
  `monkeypatch.setenv("AUTOCUE_PARALLEL_GENERATE_APPLY", "0"/"1")`.
- No property-based assertions needed — this is invariant assertion
  ("if you wrote, your sidecar row is gone"), not a scoring function.

## Verification

- Leg A (pytest): `1334 passed, 4 skipped` against the worktree.
- Leg B (vitest): `564 passed`.
- Leg C (Playwright): `30 passed, 1 known-flake fail` on the spec sets
  that don't trip the pre-existing
  `per-control-sweep.selector.test.ts` import error (which exists on
  `main` too — confirmed by stashing the fix and re-listing). The
  single flake is `qa-smoke.spec.ts:134 "filter toggles do not crash
  the page"` — a 30s-bound test that hits 28-29s on main with no fix
  applied. Not a regression.

## Issues found
None.

## Risks left in place
- The pre-existing `per-control-sweep.selector.test.ts` import error
  remains unfixed (out of scope; existed on `main`).
- The pre-existing `qa-smoke "filter toggles"` flake remains unfixed
  (out of scope; root cause is similar-index warm-up timing).

## Lessons noted
- TestClient's lifespan closes `cache_store` on `__exit__`, so when
  asserting against a store mounted manually on `app.state`, the
  client must NOT be entered as a context manager. Helper updated
  accordingly with a comment so the next test author doesn't repeat
  the trap.
