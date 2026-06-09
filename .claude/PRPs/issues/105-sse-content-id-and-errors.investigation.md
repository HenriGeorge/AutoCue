# Issue #105 — SSE events on `/api/generate-apply-stream` need `content_id` + separate `errors` counter

## Problem

`/api/generate-apply-stream` SSE events emit `{processed, total, applied, skipped}`. Two PRD acceptances fail:

- **TASK-042** — "Completion-order SSE events + monotonic processed counter": clients can see the counter advance but cannot correlate an event with the specific track that just completed.
- **TASK-043** — "Per-track error isolation across producer/consumer": compute failures (`_compute_one` raised) and writer failures (`write_cues_to_db` raised) both increment `skipped`, indistinguishable from intentional skips (no cues, no phrase data, etc.).

## Root Cause

`autocue/serve/routes.py`:

- **Parallel path** (`event_stream` → `while in_flight`), lines ~891–919:
  - `_compute_one` returns `(tid, content, cues, skip)` but the SSE payload at line 919 only carries cumulative counters.
  - The `try/except` around `write_cues_to_db` (917–918) buckets compute failures and writer failures into `skipped`.
- **Serial path** (the same function), lines ~933–959: same shape — `processed/total/applied/skipped` only, no `content_id`, no `errors`.

## Proposed Solution

Backward-compatible additive change (existing clients keep working — `docs/index.html` only reads `applied`, `skipped`, `total`, `backup_path`, `done`):

1. Add `content_id` to every per-track SSE event (the track ID just processed — known in both paths).
2. Split the failure bucket:
   - `skipped` continues to count intentional skips (no content, no phrase EXT when `phrase_only`, no cues generated, `write_cues_to_db` returned 0).
   - New `errors` counter increments when an exception fired in compute OR writer.
3. For error events, include `error_message` (str(exc)) and `error_kind` ("compute" or "writer").
4. Final `done` event also includes `errors` for symmetry.

This satisfies both PRD tasks while preserving the existing public contract (no removed fields, only additions).

### Parallel path specifics

`_compute_one` already returns a `skip` string like `"err:<exc>"` for compute failures vs domain skips like `"not_found"`, `"no_phrase"`, `"no_cues"`. We branch on the `err:` prefix to classify. Writer exceptions are caught separately and classified as `writer`.

### Serial path specifics

Wrap `db.get_content`, the EXT check, `generate_cues_for_track`, and `write_cues_to_db` calls in narrow `try/except` blocks so the per-track event can be classified the same way.

## Affected Files

- `autocue/serve/routes.py` — both parallel and serial paths inside `generate_apply_stream`.
- `tests/test_generate_apply_parallel.py` — extend assertions for `content_id` and `errors`.
- `tests/test_serve_routes.py` — assert serial-path events also include `content_id`.

## Risks

- **Field-shape change**: additive only — existing client code (`docs/index.html`) reads only the fields it knows about and ignores extras.
- **Skipped-vs-errors regression in existing tests**: previously a `_get_content` that raises was counted as `skipped`. The dedicated parallel-path test (`test_per_track_exception_increments_skipped`) explicitly checks `done["skipped"] == 1`; this assertion must be updated to `done["errors"] == 1` and `done["skipped"] == 0` — but only because the new behavior is the entire point of the fix. We update the test to match the new contract.
- **Frontend rendering**: `docs/index.html` shows `${ev.applied + ev.skipped} / ${ev.total}` in progress; this no longer accounts for `errors`. Update those two spots to `applied + skipped + errors` so the count still reaches `total`.

## Scope check

Three files, focused additive change. Diff well under 50 lines for the core routes change; tests grow modestly.
