# Self-review — Issue #111 fix

## Verdict
**approve**

## Diff under review
4 files, 22 net additions, 2 deletions:
- `Makefile` (new, 12 lines) — single `perf` PHONY target.
- `CLAUDE.md` (+1) — `make perf` row in Development commands block.
- `tests/test_snapshot_persistence.py` (±) — replace `assert False` tripwire
  with `assert os.environ.get("RUN_PERF") == "1"`.
- `.claude/PRPs/issues/111-perf-makefile-target.investigation.md` (new).

## Issues found
**None.**

## Audit notes

**Correctness.** `make perf` was demonstrated end-to-end:
- `make -n perf` → `RUN_PERF=1 pytest -m perf -v` (matches issue's suggested
  recipe).
- `make perf` → 3 perf tests collected, all PASS, 1326 deselected.
- Full pytest leg (without `RUN_PERF`) → 1325 passed, 4 skipped, 0 failed.

**Security.** No code paths touched. Makefile target is read-only invocation
of pytest. No CORS, db-writer, or sandbox surfaces involved.

**Test quality.** The tripwire rewrite satisfies the three invariants:
1. *Fails without fix.* If the conftest gate breaks (e.g. someone deletes the
   `pytest_collection_modifyitems` hook), the body would run with `RUN_PERF`
   unset → assertion fires. Confirmed by inspection — the only way the body
   runs is if pytest collected & invoked the perf-marked function.
2. *Boundary.* `== "1"` matches conftest's exact gate (`get("RUN_PERF") == "1"`).
3. *Invariant, not value.* Asserts "RUN_PERF=1 iff body runs", not a specific
   marker count or test ordering — robust to future perf tests being added.

The original `assert False` was a self-defeating canary: the docstring said
"should skip unless RUN_PERF=1", but the body broke `make perf` (the only
intended invocation path). Fix aligns body with docstring intent.

**Scope.** 4 files, well under the 50-line soft cap. No drive-by refactors.
The test edit is in-scope because it's part of the same TASK-048 commit
(`cfb1bc9`) and blocks the issue's stated fix from passing.

**Patterns.** Makefile follows GNU Make conventions (`.PHONY` declaration,
tab-indented recipe, comment header). CLAUDE.md edit maintains the
existing comment-column alignment.

**Hard-rules compliance.**
- No real `master.db` touched (no e2e leg needed).
- No `rekordbox_is_running()` bypass.
- No `.env` / `master.db` / `~/Library/Pioneer/` staged.
- No CORS widening.
- No documented-feature row removed.
- No `--no-verify`, `--force`, or `reset --hard` on shared refs.

## Verification

```
$ make perf
RUN_PERF=1 pytest -m perf -v
... 3 passed, 1326 deselected in 1.40s

$ pytest -x -q
... 1325 passed, 4 skipped, 1 warning in 13.90s
```

Vitest (Leg B) and Playwright e2e (Leg C) skipped per touch-log rule — no
`docs/index.html`, `tests/web/**`, `autocue/serve/**`, `db_writer.py`, or
`tests/e2e/**` paths were modified.

**Shared-roots check.** Modified paths against the force-all list:
`pyproject.toml`, `package.json`, `package-lock.json`, `vitest.config.js`,
`tests/conftest.py`, `playwright.config.ts`, `.claude/fixer.yaml` — none of
these were edited. Touch-log skip is honest.
