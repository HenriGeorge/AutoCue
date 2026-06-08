# Issue #119 — Self-review

## Verdict

**approve**

## Diff scope

`git diff origin/main...HEAD --stat`:

```
 .claude/PRPs/issues/119-…-investigation.md       |  88 +++++++++++++++
 .claude/agents/autocue-qa.md                     |   6 +-
 .claude/project/api-design.md                    |   2 +-
 .claude/project/architecture.md                  |   5 +-
 docs/qa_tester.md                                |   6 +-
 tests/e2e/{safety.spec.ts => 0-safety.spec.ts}   |   0
 tests/e2e/README.md                              |   7 +-
 tests/e2e/playwright.config.ts                   |  16 ++--
 tests/e2e/qa-full.spec.ts                        |   2 +-
 9 files changed, 114 insertions(+), 18 deletions(-)
```

Pure-functional changes total: 1 file rename, 1 numeric constant bump
(`300_000 → 1_800_000`). Everything else is doc references and a config
comment. Well inside the ≤50-line-diff preference.

## Issues found

**None.**

Spot-checks:

- **Correctness.** The rename gives `0-safety.spec.ts` a name that sorts
  before every other `*.spec.ts` in the directory (verified). Playwright
  uses alphabetical file discovery by default, so safety preflight now
  runs first. The `globalTimeout` bump from 5 min to 30 min only
  enlarges a ceiling — no test can be made to fail by relaxing this
  bound.
- **Test quality / regression guard.** The regression guard is the
  `npx playwright test --list` ordering check captured in the
  investigation artifact and demonstrated during Phase 2:

  ```
  Listing tests:
    [chromium] › 0-safety.spec.ts:16:3 › safety preflight › server reports …
    [chromium] › 0-safety.spec.ts:37:3 › safety preflight › sandbox DB is not …
    [chromium] › 0-safety.spec.ts:52:3 › safety preflight › diagnostic field …
    [chromium] › control-inventory.spec.ts:53:3 › …
  ```

  If the fix were reverted (rename back to `safety.spec.ts`), this list
  would put safety LAST, which is the exact symptom the issue
  describes. The check is mechanical and reproducible.
- **Security.** No surface affected. No new endpoints. No CORS change.
  No `master.db` write paths exercised.
- **Patterns.** Filename convention `0-foo.spec.ts` for "run first" is
  the same idiom used in many Playwright suites; comment in the config
  documents it explicitly.
- **Types.** No TS / Python type changes.
- **Refusal triggers.** None hit. Did not bypass any safety contract,
  did not widen CORS, did not remove any documented feature row, did
  not skip pre-commit hooks, did not force-push.

## Verification

- Leg A (`pytest`): SKIPPED — touch log clean (no `autocue/**.py` or
  `tests/**.py` changes; the e2e `*.spec.ts` files are not in the
  Leg A tracker).
- Leg B (`vitest`): SKIPPED — touch log clean (no `docs/index.html`
  or `tests/web/**` changes).
- Leg C (`playwright`): partially run — the load-bearing assertion
  ("`0-safety.spec.ts` is discovered first") was verified via
  `npx playwright test --list` against the renamed file. Running the
  full sweep here is not viable (would take 25+ min and requires a
  real `master.db` fixture), but the sweep timeout fix is a numeric
  config bump and the safety-ordering fix is verified mechanically.
- Worktree sanity check: `autocue` package resolves to
  `/Users/henrigeorge/Projects/AutoCue/.claude/worktrees/wf_60ed1d0c-1a8-94/autocue`
  (worktree wins over any system editable install).
- Final stale-reference grep `safety\.spec` across `*.ts|*.md|*.json|*.yaml|*.yml|*.py`
  excluding the new filename, `node_modules`, and the PRP folder:
  **0 results** (PRP files keep the old name as historical context;
  that is correct).

## Notes

The investigation artifact intentionally preserves the old `safety.spec.ts`
name in places where it describes the bug (it would be confusing to
back-rewrite history in the document explaining the rename).

`tests/e2e/package-lock.json` was generated during verification by
`npm install` but was deliberately NOT staged — it is an artifact of
running the verification, not part of the fix. Upstream has no
package-lock and the repo's existing convention is to omit it.
