# Issue #111 — perf-prd: TASK-048 incomplete — no Makefile, no `make perf` target

## Problem
TASK-048 was marked `passes: true` but only 1 of 3 acceptance criteria are actually satisfied:
1. `pytest -m perf` marker — DONE (`pyproject.toml:25-28` registers the marker, `tests/conftest.py:6-17` gates execution behind `RUN_PERF=1`).
2. `Makefile` with `make perf` target — MISSING. No `Makefile` exists at the repo root.
3. `CLAUDE.md` "Development commands" documents `make perf` — MISSING. The Development commands code block has no `make perf` line.

Net effect: contributors discovering perf benchmarks must already know about `RUN_PERF=1 pytest -m perf -v`. The one-command path the PRD specifies does not exist, so the perf suite is invisible.

## Root cause (file:line)
- Missing file: `Makefile` (repo root)
- `CLAUDE.md:24-35` — Development commands block omits the `make perf` line called out in the PRD

The perf gating itself works (`tests/perf/test_tracks_snapshot_perf.py` is collected; tests get skipped via `tests/conftest.py:14-17` unless `RUN_PERF=1`). So the test infrastructure is correct — only the developer-facing entry point is missing.

## Proposed solution
1. Add `Makefile` at repo root with a single `.PHONY` `perf` target:
   ```makefile
   .PHONY: perf
   perf:
   	RUN_PERF=1 pytest -m perf -v
   ```
   (Hard tab indentation — required by Make.)
2. Add `make perf                              # run perf budget enforcement (RUN_PERF=1 pytest -m perf)` to the CLAUDE.md Development commands block.

Both changes are direct deliverables specified by TASK-048 — no scope creep. ≤ 10 lines diff.

## Affected files
- `Makefile` (NEW)
- `CLAUDE.md` (1-line insertion in the Development commands block)

## Risks
- Make is preinstalled on every macOS / Linux dev machine — no new dependency.
- `make perf` is a thin wrapper around an already-working command; no behavior change to test collection or gating.
- CLAUDE.md change touches an AI-context asset → commit needs the `Context:` block per `~/.claude/rules/context-engineering.md`.
- Test validation: `pytest -x -q` will still pass (perf tests are skipped without `RUN_PERF=1`). Smoke-test `make perf` manually to confirm it invokes the right command (cannot rely on CI alone since perf is opt-in).

## Validation plan
- Leg A (pytest) — touched (`tests/` not modified, but Makefile is a shared root only when it's `package.json`/`pyproject.toml`/etc. Makefile is NEW and not in the shared-roots list, so this is a code-only change with no test impact). Run `pytest -x -q` to confirm no regression.
- Leg B (vitest) — not touched, SKIP.
- Leg C (e2e) — not touched, SKIP.
- Manual: `make -n perf` (dry-run print) to confirm the recipe expands to `RUN_PERF=1 pytest -m perf -v`.

First iteration runs all legs per the agent rules (no touch-log baseline). After that the touch log will mark B and C clean.
