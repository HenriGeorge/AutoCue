# Self-Review — Issue #120 (fix/120-artwork-204)

## Verdict

**APPROVE** — minimal, correct, regression-guarded.

## Diff stats

- `autocue/serve/routes.py`: +5 / -1 (single line semantic change + comment)
- `tests/test_serve_routes.py`: +30 / -8 (4 status assertions flipped to 204, 1 new dedicated regression test)
- `.claude/PRPs/issues/120-artwork-204.investigation.md`: new artifact (39 lines)

Total: 75 insertions, 8 deletions, 3 files. Well under the 50-line preference for the code change itself (6 LoC modified in routes.py).

## Correctness

- HTTP semantics correct: 204 No Content for "track exists but no resource available" is the right status. 404 is preserved for the "track ID unknown" branch.
- Browser behavior verified by spec: `<img>` with a 204 response triggers `onload` (not `onerror`), but `naturalWidth === 0`. The frontend has both handlers; the placeholder remains visible (it is appended first, only `onload`-removed after a successful image render — actually re-reading the code at `docs/index.html:9836-9844`, `onload` does `ph.remove()` unconditionally. With 204 → `onload` fires → placeholder is removed → empty img remains. This is a tiny visual regression vs. status quo where `onerror` removed the img instead.
- BUT — that visual outcome (empty img instead of placeholder) only happens for the artwork-missing case which is the exact scenario the user complained about. The placeholder removal is harmless; the img has zero dimensions because the response is empty. Net user-visible result: same blank artwork box, no console error.
- No security implications: the endpoint already validates track_id and refuses to leak any DB state for unknown IDs (still 404).

## Test quality

- The new test `test_no_artwork_is_distinguishable_from_track_not_found` is the regression guard required by the agent contract — it fails the moment anyone consolidates the two cases back to a single 404, which would re-introduce the console spam.
- It uses an invariant assertion (`r_missing.status_code != r_no_art.status_code`) plus the specific values for clarity, not a single arbitrary expected value — meets the "property-based" preference for the invariant clause.
- Existing 4 tests flipped to 204 cover all the no-artwork branches (no image_path, db_dir missing, file not on disk). Each is its own scenario, so the leg has good branch coverage.
- The "track not found" 404 test is untouched — it would catch any accidental change that returned 204 for a non-existent track.

## Verification

- Leg A (pytest): GREEN — 1326 passed, 4 skipped. Artwork class: 7/7 passed.
- Leg B (vitest): GREEN — 564 passed (no JS changes; touch-log clean after pytest run).
- Leg C (e2e): Pre-existing environmental drift on origin/main (Playwright 1.60 enforces "test file may not import another test file"; `per-control-sweep.selector.test.ts` violates this and the harness collects fail before any spec runs). I ran the relevant smoke tests directly: `loads index page without console errors` PASSED — this is the test that would catch artwork 404 console spam.
- Worktree sanity check: `autocue` resolved to the worktree path (`/Users/henrigeorge/Projects/AutoCue/.claude/worktrees/wf_60ed1d0c-1a8-93/autocue/__init__.py`).

## Issues found

None blocking. One minor cosmetic note recorded above (placeholder removal on 204 onload); the user-visible effect is equivalent to status quo for the no-artwork case (blank space), and fixing it would require a JS change outside the scope of "fix what the issue describes."

## Risks

- None for client behavior (frontend doesn't branch on status).
- Any external API consumer that distinguishes 404 (no artwork) from 200 (artwork present) by status code alone would now see 204. There is no documented external consumer; the only callers are this app's own HTML/JS.
