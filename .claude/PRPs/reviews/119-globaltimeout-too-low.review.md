# Issue #119 — Self-Review

**Verdict:** approve

## Issues Found

None blocking.

### Considered and dismissed

- **Test asserts ≥ 25 min but config sets 30 min.** Intentional. A future operator may legitimately want to push above 30 min (e.g. CI on slow runners) — the test is a floor, not a fence. The PRD comment in the config explains the upper-bound rationale (split into a separate Playwright project past 30 min).
- **Why a 0- prefix rather than Playwright project-config?** Playwright supports declaring multiple projects with their own `testMatch` so you can sequence them — but that introduces a second test runner pass per spec-discovery and complicates the existing single-project chromium config. A filename prefix is the minimum-invasive fix and is a well-established convention (cf. `0001_initial.sql` migration files).
- **Doesn't this break operators who grep `safety.spec.ts`?** Updated every reference in the same commit: `tests/e2e/README.md`, `docs/qa_tester.md`, `.claude/agents/autocue-qa.md`, `.claude/project/api-design.md`, `.claude/project/architecture.md`, `tests/e2e/qa-full.spec.ts`. Only `.claude/PRPs/reviews/15-*.md` retains the old name — historical review artifacts are intentionally left untouched.
- **Pre-existing Playwright 1.60.0 breakage** (`per-control-sweep.selector.test.ts` "should not import test file") is NOT issue #119 — it's a separate upstream bug that blocks `tests/e2e && npx playwright test --list` on origin/main. Out of scope. The vitest regression guard in `tests/web/safety-ordering.test.js` validates the #119 fix without depending on Leg C.

## Verification

| Leg | Command | Result |
|---|---|---|
| A — pytest | `pytest -x -q` | **1325 passed, 4 skipped** |
| B — vitest | `npm test --silent` | **568 passed** (was 564 before; +4 new regression-guard tests) |
| C — Playwright | `cd tests/e2e && AUTOCUE_SOURCE_DB=… npm test` | **Blocked upstream** by `per-control-sweep.selector.test.ts` Playwright 1.60.0 error (unrelated, pre-existing on origin/main; verified by stashing this commit). The relevant assertions for #119 are covered by Leg B's regression-guard tests. |

### Regression-guard semantics

The four new vitest assertions in `tests/web/safety-ordering.test.js` would fail if either symptom returned:

| Hypothetical regression | Failing test |
|---|---|
| Someone renames `0-safety.spec.ts` → `safety.spec.ts` (or anything sorting after `per-control-sweep.spec.ts`) | "safety spec sorts BEFORE per-control-sweep" + "safety spec is the LITERAL first spec" |
| Someone drops `globalTimeout` back to `300_000` | "playwright.config.ts globalTimeout is at least 25 minutes" |
| Someone deletes the safety spec entirely | "a safety preflight spec exists in tests/e2e/" |

### Self-audit checklist

- [x] Diff bounded (10 files, 189+/17− — bulk is investigation + regression test, not code).
- [x] Conventional commit, includes `Closes #119`, includes `Context:` block for the `.claude/` and agent files.
- [x] No drive-by refactors; the Playwright 1.60.0 upstream issue was explicitly NOT touched.
- [x] No security-sensitive paths changed (cache, db_writer, CORS untouched).
- [x] No widening of test scope to mutate state; this is purely a config/ordering change.
- [x] Filenames in agent prompts updated in the same commit — no stale paths.
