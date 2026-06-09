# Issue #111 — TASK-048 incomplete: missing Makefile + `make perf` docs

## Problem

TASK-048 (Performance v1 PRD) ships three deliverables; only one landed:

1. `pytest -m perf` marker registered — **DONE** (`pyproject.toml:27`).
2. `Makefile` with `make perf` target — **MISSING** (no `Makefile` at repo root).
3. `CLAUDE.md` "Development commands" documents `make perf` — **MISSING**.

The task was marked `passes: true` despite 2 of 3 deliverables absent. Without a
one-command entry point, the perf suite is invisible to contributors and the
finger-memory cost of `RUN_PERF=1 pytest -m perf` ensures the tests rot.

## Root cause

Oversight at completion: only the marker registration shipped.

- `pyproject.toml:27` — marker line ok.
- `tests/conftest.py:7-17` — `RUN_PERF=1` gate ok.
- Repo root — `ls Makefile` → no such file.
- `CLAUDE.md:18-35` — Development commands block has `pytest`, `npm test`, etc.,
  but no `make perf` row.

Perf tests exist (`tests/perf/test_tracks_snapshot_perf.py`,
`tests/test_snapshot_persistence.py:166`), so the gate works once invoked —
the only thing missing is the invocation path.

## Proposed solution

1. Create `Makefile` at repo root with a single `perf` PHONY target that runs
   `RUN_PERF=1 pytest -m perf -v`.
2. Add `make perf` to the Development commands block in `CLAUDE.md` with a
   short comment.

Scope: 2 files, < 10 lines diff. Pure documentation/tooling glue — no code paths
touched. Three-leg validation: only pytest leg needs to run (no autocue/**,
docs/index.html, or serve/** edits).

## Affected files

- `Makefile` (new, ~5 lines).
- `CLAUDE.md` Development commands block (+1 line).

## Risks

- None to runtime. A future contributor adding more perf targets (e.g.,
  `make perf-load`) can extend the same Makefile without churn.
- `Makefile` is a new top-level file; verify it isn't gitignored. (`.gitignore`
  spot-check confirms nothing matches `Makefile`.)

## Validation plan

- Pytest leg only — Makefile + CLAUDE.md edits don't touch any path that
  triggers vitest or e2e legs per the touch-log rule.
- Manually verify: `make -n perf` prints `RUN_PERF=1 pytest -m perf -v`.
- Manually verify: `make perf` runs the gated tests and they execute (not
  skipped), since `RUN_PERF=1` is set inside the recipe.
