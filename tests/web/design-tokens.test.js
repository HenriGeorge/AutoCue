/**
 * Design-system token coverage.
 *
 * The AutoCue design system is vendored at docs/design/tokens/*. The live web
 * app (docs/index.html) is the source of truth for the actual rendered tokens,
 * but it MUST define (as a canonical name or an alias) every token the design
 * system formalizes — otherwise new UI work can't `var(--token)` against the
 * system. This test fails if docs/index.html drifts behind the vendored set.
 *
 * Policy (see .claude/PRPs/prds/adopt-autocue-design-system.prd.md): app keeps
 * its own canonical names (--surface2 / --font / --mono) and adds the
 * design-system names (--surface-2 / --font-sans / --font-mono) as aliases.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadAppHtml } from './_source.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = resolve(__dirname, '..', '..')
// P0 split: app CSS lives in docs/css/* — loadAppHtml() inlines it back.
const html = loadAppHtml()

// Extract every custom-property DECLARATION (`--name:`) from CSS. Strips
// `/* ... */` comments first so a `--token:` inside a comment can't count as a
// definition; uses a global match (not line-anchored) because both the app and
// the vendored files pack several tokens per line (e.g. `--cue-a:…; --cue-b:…;`).
// `var(--x)` is never matched — there's no `:` after the name in a reference.
function extractTokens(css) {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const out = new Set()
  const re = /(--[a-z0-9-]+)\s*:/gi
  let m
  while ((m = re.exec(stripped))) out.add(m[1])
  return out
}

function definedTokens(css) {
  return extractTokens(css)
}

// Token NAMES declared in a vendored token file (left-hand side only).
function declaredTokens(file) {
  return extractTokens(readFileSync(resolve(root, file), 'utf8'))
}

const appTokens = definedTokens(html)

describe('design-system token coverage', () => {
  for (const file of [
    'docs/design/tokens/colors.css',
    'docs/design/tokens/spacing.css',
    'docs/design/tokens/typography.css',
  ]) {
    it(`docs/index.html defines every token from ${file}`, () => {
      const required = declaredTokens(file)
      expect(required.size).toBeGreaterThan(0)
      const missing = [...required].filter((t) => !appTokens.has(t))
      expect(missing, `missing tokens: ${missing.join(', ')}`).toEqual([])
    })
  }

  it('defines the design-system-name aliases over the app canonical names', () => {
    for (const alias of ['--surface-2', '--font-sans', '--font-mono']) {
      expect(appTokens.has(alias), `alias ${alias} not defined`).toBe(true)
    }
    // each alias points at the canonical app name (var() reference)
    expect(html).toMatch(/--surface-2:\s*var\(--surface2\)/)
    expect(html).toMatch(/--font-sans:\s*var\(--font\)/)
    expect(html).toMatch(/--font-mono:\s*var\(--mono\)/)
  })

  it('keeps the ink-pill CTA token (primary action is ink, never green)', () => {
    expect(appTokens.has('--ink')).toBe(true)
    expect(appTokens.has('--on-ink')).toBe(true)
    // ink must NOT equal the green accent in either theme
    expect(html).toMatch(/--ink:\s*#0a0a0a/) // light
    expect(html).toMatch(/--ink:\s*#fafafa/) // dark (html.dark override)
  })
})
