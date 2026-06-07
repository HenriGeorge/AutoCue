# Issue #22 — Workflow script TypeScript syntax not parseable

## Problem

`.claude/workflows/autocue-fixer.ts` was shipped in PR #18 with TypeScript syntax
(type annotations, generic parameters, `as` casts). The Workflow tool's script
parser is a pure JavaScript parser and rejects the file:

```
Script must begin with `export const meta = { name, description, phases }` (pure literal).
Script parse error: Unexpected token (33:21)
```

Column 21 of line 33 is the `:` in `cmd: string` — the first type annotation it hits.

## Root Cause

`.claude/workflows/autocue-fixer.ts:33` — `async function sh(cmd: string, label: string): Promise<{ stdout: string; exitCode: number }>` (and several similar lines below) use TypeScript-only syntax. The Workflow tool's parser is JavaScript-only.

## Proposed Solution

Option (a) from the issue body: rename the file to `.js` and strip ALL TypeScript
syntax. Specifically:

- Drop type annotations on parameters and return types.
- Drop generic params (`new Set<number>()` → `new Set()`).
- Drop `as` casts.
- Drop typed array literals (`const out: number[] = []` → `const out = []`).
- Drop `RegExpExecArray | null` annotation on `m`.
- Drop the `[number, Set<string>] as const` tuple annotation.
- Keep `meta` at top (already a pure literal — no change needed).
- Keep the workflow's behaviour identical.

Then update `.claude/commands/autocue-fixer.md` to reference the new path
(`autocue-fixer.js` instead of `autocue-fixer.ts`).

## Affected Files

- `.claude/workflows/autocue-fixer.ts` → renamed to `.claude/workflows/autocue-fixer.js`,
  with TS syntax stripped.
- `.claude/commands/autocue-fixer.md` — update the two `scriptPath:` references and the
  "Workflow script:" line at the bottom.

## Regression Test

Add a Vitest fixture that asserts `node --check .claude/workflows/autocue-fixer.js`
exits 0 — catches the TS-parse-error class on any future regression. (Also covers
the case where someone re-adds a stray `as` cast or generic.) Lives in
`tests/web/workflow-script.test.js`.

## Risks

- Behavior parity: the JS conversion must be syntax-only — no logic change. The
  agent re-reads the diff before commit to verify.
- The Workflow tool's `meta` literal-only requirement is satisfied (already is).
- Stripped types might subtly hide bugs that TS would catch, but this file was
  never type-checked in CI anyway (no `tsc` runs against it).
