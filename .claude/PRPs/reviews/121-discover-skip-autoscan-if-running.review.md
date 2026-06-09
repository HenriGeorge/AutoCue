# Self-review — Issue #121

**Verdict:** approve

## Diff summary

- `docs/index.html` (+14 / -2 lines): in the `initDiscoverV2` post-init
  block, hoist `status` from the `try` scope and gate the auto-scan on
  `status.running !== true`. Comment block explains the issue + the
  trade-off (existing SSE consumer still surfaces results).
- `tests/web/ux-pr-c.test.js` (+60 / 0): new `describe('Discover
  auto-scan gating on tab activation (issue #121)')` with 7 cases
  covering REGRESSION, BOUNDARY, happy path, the two prereq gates, the
  fetch-threw fall-through, and a property-style truthiness invariant.

## Audit

- **Correctness:** the new branch is `if (status && status.running ===
  true) return;` — only the explicit `running===true` value blocks,
  matching the server's response contract from
  `autocue/serve/routes.py:3170-3178`. `null` (fetch threw) falls
  through to the existing happy path, preserving today's behavior in
  the error case. `undefined`/`false`/`0`/`''` also fall through (see
  truthiness-invariant test).
- **Security:** no new fetches, no new query params, no surface
  expansion. Same origin, same endpoint already exposed.
- **Test quality (revert-the-fix check):** if the new `if (status &&
  status.running === true) return;` line is removed, the REGRESSION
  case (`{running:true}` returns `false`) fails because
  `decideAutoScan` then collapses to the unconditional happy-path
  branch and returns `true`. The mirror is faithful — the test would
  catch the regression.
- **Patterns:** follows the same `let foo = null; try { foo = ... }
  catch (_) {}` pattern already used elsewhere in this IIFE.
- **Types:** vanilla JS, no type signature change.
- **Hooks / safety contract:** no `.claude/`, `CLAUDE.md`, agent, or
  `docs/qa_*` file touched → no `Context:` block needed for the
  commit. No write-path code touched → no `_rb_running` concern. CORS
  whitelist untouched. Diff under 50 lines as preferred.

## Verification

- Leg A (pytest): 1325 passed, 4 skipped, 16.7s.
- Leg B (vitest): 571 passed (includes 7 new in ux-pr-c.test.js), 2.6s.
- Leg C (e2e Playwright): 6 passed (discover-v2.spec.ts + pages-smoke).
  A pre-existing collection error in
  `tests/e2e/per-control-sweep.selector.test.ts` (file imports another
  spec file — flagged by Playwright since the day it landed in commit
  `adeee99`) blocks the default test list; not introduced by this fix
  and not in scope. Confirmed identical to `main` via `git diff main
  -- tests/e2e/per-control-sweep.{selector.test,spec}.ts`.

## Issues found

None.
