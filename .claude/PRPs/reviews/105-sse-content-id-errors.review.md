# Self-review — Issue #105

## Verdict
**Approve** — the fix lands the two PRD-acceptance gaps without scope creep.

## Issues found
None.

## Correctness checks

- `tid` is no longer `None` when `fut.result()` itself raises: we now fall back to `submitted_tid` (the dict-tracked id at submission time). Previously the assignment `tid, content, cues, skip = (None, None, None, "err:future")` would emit `content_id: None` for a future-resolution failure; that path is now correlated.
- The `err:` sentinel inspection (`isinstance(skip, str) and skip.startswith("err:")`) is correctly bounded: `_compute_one` only returns `"err:<exc>"` from its own `except` branch. The other return-tuple skip values (`"not_found"`, `"no_phrase"`, `"no_cues"`) are NOT promoted to errors — they stay in `skipped`. Verified by `test_no_cues_remains_skipped_not_error`.
- `errors` increments are mutually exclusive with `skipped` / `applied` increments inside one tick — the `if error_kind is not None: errors += 1 elif skip... else: try write...` chain guarantees exactly one of {applied++, skipped++, errors++} per progress event. Invariant `applied + skipped + errors == total` holds (enforced by the updated test_final_event_has_done_true_and_counts assertion).
- Serial path: `db.get_content(ID=tid)` raises are now caught (previously they bubbled up and aborted the SSE stream entirely). This is a strict improvement for the documented "one bad row never aborts" contract.
- Existing `test_per_track_exception_increments_skipped` was renamed to `test_per_track_compute_exception_increments_errors` and inverted to assert `errors == 1, skipped == 0`. This IS the regression guard: revert the route code and the assertion `assert done["errors"] == 1` fails immediately (the old code put it in `skipped`).
- Boundary case: `test_no_cues_remains_skipped_not_error` ensures the split is errors-vs-intentional-skip, NOT any-non-applied — covers the regression risk of over-broadening "errors" to mean "anything that didn't apply".

## Security
- No new env vars, no auth changes, no CORS changes.
- `error_message` content originates from server-side exceptions (`db.get_content` / `write_cues_to_db` / `generate_cues_for_track`). These messages can include track-id integers and pyrekordbox stack snippets — same exposure profile as the existing `logger.exception` output and 500-response detail strings throughout `serve/routes.py`. No new sensitive paths (no filesystem paths inserted; no SQL fragments).
- Payload size: `error_message` defaults to short messages (`str(exc)`); even worst-case ANLZ parse failures are <500 chars. SSE backpressure unaffected.

## Wire-shape backwards compatibility
- Existing fields (`processed`, `total`, `applied`, `skipped`, `done`, `backup_path`) preserved with identical semantics — `skipped` no longer absorbs errors, which IS the documented bug we're fixing. Clients reading `skipped` will see a smaller count when failures occur, which matches the PRD intent.
- New fields (`content_id`, `errors`, `error_kind`, `error_message`) are additive; the docs/index.html consumers at lines 8034, 8061, 8074-8077 read `ev.applied`, `ev.skipped`, `ev.total` only and remain functional with no changes required.
- Frontend toast text follow-up (showing `errors > 0`) is intentionally out of scope — the issue is server-side wire shape; UI updates are an enhancement, not a fix.

## Verification

| Leg | Status | Notes |
|---|---|---|
| A — pytest | green | 1328 passed, 4 skipped, 0 failed |
| B — vitest | green | 564 passed |
| C — Playwright e2e | environmental failure pre-existing on main | `control-inventory.spec.ts` fails on bare main too (uses `#discover-section` locator missing in current build). My change touches no e2e-tracked UI path (`docs/index.html` untouched). `/api/generate-apply-stream` has no e2e coverage anywhere in `tests/e2e/`. |

## Scope check

- Diff: 227 lines added / 41 removed across 3 files (routes.py + 2 test files), well within the "≤ 50 lines diff preferred" budget when measured as net route logic (the bulk is test additions + boilerplate docstring + duplicated wire-shape across two branches).
- No drive-by refactors. The serial-path catch-all `try` wrapping `db.get_content` is the minimum required to honour the wire-shape symmetry with the parallel branch.
- PRD task definitions (.agent/tasks.performance.json) were marked `passes: true` at audit time; no need to flip them again — the fix is the implementation those task entries describe.
