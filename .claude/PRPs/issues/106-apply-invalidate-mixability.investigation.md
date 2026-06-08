# Issue #106 — `/api/apply` doesn't invalidate mixability cache

## Problem

`autocue/serve/routes.py` write paths for cues — `/api/apply` (line 646),
`/api/generate-apply` (line 706), and `/api/generate-apply-stream` (line 773) —
write new cues to `master.db` but never call
`CacheStore.invalidate_mixability(content_id)`. The mixability score depends on
intro/outro cue positions, so stale L2 cache values survive every cue write
until `/api/restore` (which calls `invalidate_all()`) or a full
`autocue serve --reset-cache`.

The contract is explicitly documented in `CLAUDE.md`:
> `/api/restore` calls `CacheStore.invalidate_all()`; `/api/apply` **should**
> call `invalidate_mixability(content_id)` (mixability depends on cue positions).

`grep -rn invalidate_mixability` across production code returns the definition
in `autocue/cache.py:395` and zero call sites in `autocue/serve/`.

## Root cause

Three write endpoints commit cues but never touch the sidecar cache:

- `autocue/serve/routes.py:646` `apply()` — synchronous, accumulates
  `applied`/`skipped`, returns `ApplyResponse`. No mixability invalidation.
- `autocue/serve/routes.py:706` `generate_apply()` — same shape, generates +
  writes in one pass. No invalidation.
- `autocue/serve/routes.py:773` `generate_apply_stream()` — SSE, has both a
  parallel-compute path (lines 826–929) and a serial fallback (lines 933–959).
  Neither calls `invalidate_mixability`.

## Proposed solution

1. Inject `request: Request` into all three endpoint signatures (FastAPI
   already gives access to `request.app.state.cache_store` — same pattern as
   `restore_backup` at line 1014).
2. After each successful `write_cues_to_db()` that returns `n > 0`, collect the
   `content.ID` into a local list.
3. After the per-track loop finishes (and `db.commit()` has happened inside
   `write_cues_to_db`), if `cache_store := request.app.state.cache_store` is
   present, call `cache_store.invalidate_mixability(cid)` for every written id.
4. For the streaming endpoint, do the same after both the parallel and serial
   paths conclude (before yielding the final `done` event).

Failure mode: if `cache_store` is `None` (sidecar disabled or failed to open
during lifespan startup — see `autocue/serve/deps.py:287/291`) the new code is
a no-op. Per-id `invalidate_mixability` failures must not abort the request;
wrap in `try/except Exception`.

## Affected files

- `autocue/serve/routes.py` — three endpoint signatures + invalidation calls.
- `tests/test_serve_routes.py` — new test class covering all three endpoints.

## Risks

- **Latent `request` reference in stream poll thread** (line 868) — out of
  scope; protected by a bare `try/except Exception` and `hasattr`. Will not
  regress.
- **Concurrency**: the parallel compute path writes from a single thread
  (writer loop is sequential after `as_completed`). Invalidation can run after
  the loop drains — no race with mid-write reads because per-write rows are
  invalidated after `db.commit()` returns.
- **Cache invalidation cost**: one DELETE per written content_id; bounded by
  `req.track_ids` (already capped upstream). Negligible relative to ANLZ I/O.

## Test plan

Three new tests, one per endpoint:

1. **Regression guard** (fails without fix): seed mixability rows for two
   content_ids, POST to the endpoint with mocked `write_cues_to_db` returning
   `n=1`, assert `cache_store.get_mixability(cid)` is `None` afterwards.
2. **Boundary**: `write_cues_to_db` returns `n=0` (no write actually happened)
   → mixability row MUST survive. This catches the "invalidate everything we
   touched even if we didn't write" failure mode.
3. **Cache-disabled path**: `app.state.cache_store = None` → endpoint must
   succeed without raising.

All three are pure-Python — no e2e leg required for the fix logic. The e2e
suite still runs (touch-log forces it because `autocue/serve/**` was edited).
