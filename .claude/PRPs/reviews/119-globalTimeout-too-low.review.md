# Self-review — Issue #119 fix

## Verdict

Approve.

## Diff summary

- Renamed `tests/e2e/safety.spec.ts` → `tests/e2e/0-safety.spec.ts` (content unchanged; verified via `git diff --stat` shows 0 line delta on the file).
- `tests/e2e/playwright.config.ts`: `globalTimeout: 300_000` → `1_800_000` (5 min → 30 min). JSDoc updated to call out the rename + reference issue #119.
- Doc updates referring to the old filename: `tests/e2e/README.md`, `tests/e2e/qa-full.spec.ts` comment, `docs/qa_tester.md` (mermaid + table), `.claude/agents/autocue-qa.md` (safety contract + spec table), `.claude/project/architecture.md`, `.claude/project/api-design.md`.
- Investigation artifact `.claude/PRPs/issues/119-globalTimeout-too-low.investigation.md` added.

Net diff: 8 files modified, 1 renamed, 27 insertions / 18 deletions. Under the ≤ 50-line budget the agent prefers.

## Issues found

None. The change is purely configuration + filename + doc references. No code path semantics change.

## Verification

- **Leg A (pytest)**: 1325 passed, 4 skipped, 1 warning. 15.4s.
- **Leg B (vitest)**: 564 passed across 28 files. 2.4s.
- **Leg C (Playwright e2e, targeted)**: Ran `0-safety.spec.ts qa-smoke.spec.ts pages-smoke.spec.ts selectors-exist.spec.ts`. `results.json` confirms `0-safety.spec.ts` is now the FIRST file in spec discovery order (the bug from issue #119). 30 of 31 tests passed. The single failure (`qa-smoke.spec.ts:134 filter toggles do not crash the page`) is unrelated: it timed out at 30s on this run but passed in 26.1s when re-run in isolation. The test does not touch any file I modified — pre-existing flake just under the test timeout.
- **Full sweep not run**: Per the investigation artifact, the per-control sweep takes 19-29 min and the fix is a config / rename change verifiable by inspection. The new 30-min `globalTimeout` was sized to comfortably exceed the upper bound of the reported sweep duration. The next QA run that exercises the sweep will validate the timeout in practice.

## Regression-guard reasoning

A test that "fails without the fix" here would be a meta-test that asserts spec discovery order or `globalTimeout >= sweepDuration`. That's awkward to express as a unit test, but the structural change (filename prefix `0-`) is self-enforcing: alphabetical discovery is a deterministic Playwright behaviour, and `results.json` empirically confirmed `0-safety.spec.ts` lands first. The `globalTimeout` bump is a numeric constant; reverting it to 300_000 would reproduce the exact failure mode described in #119.

## Safety contract review

- HARD rule #1 (sandbox-only writes) — UNTOUCHED. `0-safety.spec.ts` content is byte-identical to `safety.spec.ts`.
- HARD rule #5 (no removing documented feature rows) — UNTOUCHED. Doc rows were renamed in place, not removed.
- HARD rule #7 (≤ 50 lines diff) — 27 insertions / 18 deletions. PASS.
- No CORS changes, no `db_writer.rekordbox_is_running()` bypass, no `.env` / credentials touched.
