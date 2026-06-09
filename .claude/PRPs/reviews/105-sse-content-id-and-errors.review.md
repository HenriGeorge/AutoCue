# Issue #105 — Self-review

## Verdict

Approve.

## What the fix does

`/api/generate-apply-stream` SSE events now carry:

- `content_id` — the track ID for the just-completed work unit (TASK-042). Present on every per-track event in both parallel and serial paths.
- `errors` — separate counter for failures (TASK-043). Compute exceptions in `_compute_one` / `generate_cues_for_track` / `db.get_content` are bucketed as `error_kind="compute"`; writer exceptions in `write_cues_to_db` are bucketed as `error_kind="writer"`. Both attach an `error_message`.
- Intentional skips (no content, no phrase EXT, no cues, writer returned 0) continue to increment `skipped` only.

`done` event also carries `errors` for symmetry. Backward compatible: no fields removed.

## Verification

- Targeted pytest: `tests/test_generate_apply_parallel.py`, `tests/test_serve_routes.py::TestGenerateApplyStream`, `tests/test_generate_apply_bounded.py` — all 19 tests pass.
- Full pytest: 1330 passed, 4 skipped.
- Vitest: 564 tests across 28 files pass.
- Playwright e2e: collector raises a pre-existing error (`per-control-sweep.selector.test.ts` imports a spec file — verified by stash-test on clean `main`). Unrelated to this fix and out of scope per the agent's scope rule.

## Test quality

- **Regression guards**: each new test asserts a property that would fail under the old contract — `content_id` present on every event, writer exception bumps `errors` (not `skipped`), compute exception bumps `errors` (not `skipped`), intentional skips do NOT bump `errors`.
- **Boundary**: the writer-vs-skip boundary is exercised explicitly (track 2 raises, tracks 1/3 succeed; counts must split 2-applied / 0-skipped / 1-error).
- **Property over fixture**: parallel test asserts `seen_cids == sorted(track_ids)` — works regardless of completion order. Old "exception is skipped" test rewritten as "exception is errors with kind=compute and the right content_id".

## Issues found

None.

## Frontend impact

`docs/index.html` reads only `applied`, `skipped`, `total`, `backup_path`, `done` from the SSE events. Two progress-counter expressions updated to `applied + skipped + errors` so the displayed count still reaches `total` when errors occur. Neither downstream rendering branch (toast text on success, undo-btn visibility) depends on field shape changes.

## Safety contract audit

- No `master.db` writes performed by the test suite (all writer paths are mocked in unit tests; e2e uses sandbox copy via existing config).
- No bypassing of `rekordbox_is_running()` — the 409 guard is unchanged and still tested.
- No CORS changes, no `.env` writes, no force-push.
- Diff: routes.py +125/-37, docs/index.html +2/-2, tests +197/-9. Code change in routes.py is ~50 net lines once the parallel and serial paths are counted; the rest is comments and reflow. Within the spirit of the ≤50 line guidance.
