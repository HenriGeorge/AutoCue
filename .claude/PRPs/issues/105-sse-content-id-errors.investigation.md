# Issue #105 — `/api/generate-apply-stream` SSE missing `content_id` + errors counter

## Problem

`/api/generate-apply-stream` (`autocue/serve/routes.py:773-960`) emits progress events shaped as `{processed, total, applied, skipped}` only. Two PRD-acceptance gaps result:

- **TASK-042** — completion-order events have no `content_id`, so a client cannot correlate a tick with a specific track row. (UI currently uses progress bars only, not per-card updates, but the PRD requires per-track identity in the wire protocol.)
- **TASK-043** — every per-track failure (writer raised, compute raised, no cues, content not found) is bucketed into `skipped`. Errors are indistinguishable from intentional skips ("already cued", "no phrase track"), so per-track error isolation is unobservable from outside.

## Root cause (file:line)

- `autocue/serve/routes.py:899-919` (parallel path) — `tid` is available via `fut.result()` but never included in the event payload. The `except Exception` branch on writer (`917-918`) increments `skipped`; the `_compute_one` `err:future` / `err:<exc>` returns also bucket into `skipped` via the `if skip or content is None or not cues:` branch at `905-906`. No `errors` counter exists.
- `autocue/serve/routes.py:933-960` (serial path) — same shape: emits `{processed, total, applied, skipped}` with no track identity, and the `except`-style branches (only via `cues, _ = generate_cues_for_track`) cannot raise into a separate counter.

## Proposed solution

1. **Add an `errors` counter** alongside `applied` / `skipped`. Distinguish:
   - `skipped` — intentional: `content is None` (not found), `not cues` (no_cues), `no_phrase` (phrase_only filter), `write_cues_to_db` returned `0` (no-op write because already cued and `overwrite=False`).
   - `errors` — exceptional: `_compute_one` returned `err:future` or `err:<exc>`, OR the writer's `write_cues_to_db` raised an exception.
2. **Add `content_id` to every progress event** — the integer track ID that the tick represents. Available as `tid` (parallel) or `req.track_ids[i]` (serial).
3. **Add `error_kind` (`"compute"` / `"writer"`) and `error_message`** to events that are errors. Keep payload size sensible.
4. **Include `errors` in the final `done` event.**
5. **Serial path mirrors the parallel path** so wire-shape is consistent (the spec talks about behaviour not implementation; both branches must emit the same fields).
6. **Tests** — extend `tests/test_generate_apply_parallel.py` + `tests/test_serve_routes.py` to assert:
   - `content_id` present on every progress event and equals the track that was just processed (regression guard).
   - A track that raises in `_compute_one` surfaces `errors`, not `skipped`.
   - A track whose writer raises surfaces `errors`, not `skipped`.
   - `done` event carries `errors` counter.
   - "No cues" + "already cued" still count as `skipped` (boundary case — must NOT regress into errors).

## Affected files

- `autocue/serve/routes.py` — both parallel branch and serial branch of `generate_apply_stream`.
- `tests/test_generate_apply_parallel.py` — add `content_id` + `errors` assertions.
- `tests/test_serve_routes.py` — serial-path assertions (file:706+).

## Risks

- Frontend (`docs/index.html:8034`, `8061`, `8074-8077`) reads `ev.applied`, `ev.skipped`, `ev.total`. Adding fields is additive and breaks nothing. The toast at line 8074 reads `finalData.skipped` — this stays accurate because compute/writer raises previously inflated `skipped`. The user-facing message will now under-count "skipped (already cued)" relative to before only when failures occur; this is the desired correction. The toast is improved by surfacing the errors count too (small UI follow-up — keep minimal: just append `errors` to the toast if `> 0`).
- Existing tests assert `applied + skipped == N`. After the fix, the invariant becomes `applied + skipped + errors == N`. Update the two assertions in `test_generate_apply_parallel.py` (`154`, `220`) to use the new invariant, AND verify the `test_per_track_exception_increments_skipped` test now becomes `_increments_errors` (boundary — the regression guard).
- Diff stays ≤ 50 lines for the route, plus tests. Within scope budget.

## Branch

`fix/105-sse-content-id-errors`
