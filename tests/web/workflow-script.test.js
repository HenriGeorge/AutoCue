/**
 * Regression test for issue #22 — `.claude/workflows/autocue-fixer.js` must
 * parse cleanly as JavaScript (the Workflow tool's parser is JS-only).
 *
 * Catches:
 *   - reintroduction of TypeScript-only syntax (type annotations, generic
 *     params on `new Set<T>()`, `as` casts, typed array literals)
 *   - the `meta` literal block stops being a pure object expression at the top
 *
 * The Workflow tool wraps the script body in an async function before
 * evaluating it (which is why top-level `return` is legal). We mirror that by
 * wrapping the script body in `async function() { ... }` and handing it to
 * `new Function` — if the JS engine can construct the function, the body
 * parses as JavaScript.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SCRIPT_PATH = resolve(__dirname, '../../.claude/workflows/autocue-fixer.js')

function loadScript() {
  return readFileSync(SCRIPT_PATH, 'utf8')
}

describe('.claude/workflows/autocue-fixer.js (issue #22 regression)', () => {
  it('starts with `export const meta = {` (Workflow tool requirement)', () => {
    const src = loadScript()
    expect(src.startsWith('export const meta = {')).toBe(true)
  })

  it('parses as valid JavaScript when wrapped in an async function', () => {
    const src = loadScript()
    // The Workflow tool turns `export const meta = X` into a top-level binding
    // and wraps the rest of the body in an async function (where top-level
    // `return` is legal). Mirror that: rewrite `export const` to `const` and
    // wrap the whole thing.
    const rewritten = src.replace(/^export\s+const\s+/, 'const ')
    // new Function throws SyntaxError on TS-only tokens like `: string`,
    // `<number>`, or `as { ... }` casts.
    expect(() => new Function(`return (async () => { ${rewritten} })`)).not.toThrow()
  })

  it('contains no TypeScript-only syntax', () => {
    const src = loadScript()
    // Strip strings + comments so the regex checks below don't false-match on
    // string contents (e.g. log("foo: bar")) or commented-out examples.
    const stripped = src
      // line comments
      .replace(/\/\/.*$/gm, '')
      // block comments
      .replace(/\/\*[\s\S]*?\*\//g, '')
      // template literals (lossy — collapse to empty backticks; we only care
      // about non-string TS tokens leaking through)
      .replace(/`[\s\S]*?`/g, '``')
      // single-quoted strings
      .replace(/'(?:[^'\\]|\\.)*'/g, "''")
      // double-quoted strings
      .replace(/"(?:[^"\\]|\\.)*"/g, '""')

    // Generic params on identifiers like `new Set<number>()` or `Promise<T>`.
    expect(stripped).not.toMatch(/\bnew\s+(?:Set|Map|Array|Promise)\s*</)
    expect(stripped).not.toMatch(/:\s*Promise\s*</)

    // `as` cast: `expr as { ... }` or `expr as Type`.
    expect(stripped).not.toMatch(/\)\s+as\s+\{/)
    expect(stripped).not.toMatch(/\bas\s+const\b/)

    // Typed function parameters: `(cmd: string, label: string)` and similar.
    // Heuristic: a parenthesized param list with a `:` annotation followed by
    // a TS-style identifier. Limit to known TS primitives to avoid false hits
    // on object literals.
    expect(stripped).not.toMatch(/\(\s*[a-zA-Z_$][\w$]*\s*:\s*(string|number|boolean|unknown|any|void)\b/)

    // Typed variable declarations: `const out: number[] = []`.
    expect(stripped).not.toMatch(/\b(?:const|let|var)\s+[a-zA-Z_$][\w$]*\s*:\s*[A-Za-z_$]/)
  })

  it('exposes meta.name === "autocue-fixer"', async () => {
    // Use a data: URL so we can import the ESM module without polluting the
    // jsdom global with its top-level `agent`/`phase`/`log` calls. We extract
    // just the meta literal — anything below would crash without the
    // Workflow runtime providing those globals.
    const src = loadScript()
    const match = src.match(/export\s+const\s+meta\s*=\s*(\{[\s\S]*?\n\})\s*;?/)
    expect(match, 'meta literal not found').toBeTruthy()
    const meta = new Function(`return (${match[1]})`)()
    expect(meta.name).toBe('autocue-fixer')
    expect(Array.isArray(meta.phases)).toBe(true)
    expect(meta.phases.length).toBeGreaterThan(0)
  })
})
