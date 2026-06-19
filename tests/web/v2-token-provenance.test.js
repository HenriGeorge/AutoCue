/**
 * UNIT B — token-provenance + Dupes-toolbar cleanup guards (fix/design-workbench).
 *
 * B1: the Nightboard zone tokens (runtime source of truth: docs/css/app.css)
 *     must be mirrored byte-for-byte into the vendored design system
 *     (docs/design/tokens/colors.css), both themes.
 * B2: the Dupes-toolbar verbs must carry the .wb-toolbar-sm token-layer class
 *     and NO inline font/padding style= (folded into the token layer); ids and
 *     the secondary-btn/primary classes are preserved; bulk-delete stays
 *     right-aligned via the spacer utility.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const DOCS = resolve(__dirname, '..', '..', 'docs')
const appCss = readFileSync(resolve(DOCS, 'css', 'app.css'), 'utf8')
const colorsCss = readFileSync(resolve(DOCS, 'design', 'tokens', 'colors.css'), 'utf8')
const indexHtml = readFileSync(resolve(DOCS, 'index.html'), 'utf8')

const ZONE_TOKENS = ['--zone-warmup', '--zone-build', '--zone-peak', '--zone-closing']

// Pull every `--token: value;` declaration (handles rgba commas) per token name.
function declValues(css, token) {
  const re = new RegExp(`${token}\\s*:\\s*([^;]+);`, 'g')
  const out = []
  let m
  while ((m = re.exec(css)) !== null) out.push(m[1].replace(/\s+/g, ''))
  return out
}

describe('B1 — Nightboard zone tokens vendored into the canonical design system', () => {
  it('app.css defines all four zone tokens in both light and dark', () => {
    for (const t of ZONE_TOKENS) {
      // light + dark = 2 declarations each
      expect(declValues(appCss, t).length, `${t} in app.css`).toBe(2)
    }
  })

  it('colors.css mirrors each zone token byte-for-byte (both themes)', () => {
    for (const t of ZONE_TOKENS) {
      const live = declValues(appCss, t)
      const vendored = declValues(colorsCss, t)
      expect(vendored.length, `${t} in colors.css`).toBe(2)
      // Sorted compare: both files carry the light + dark literal pair.
      expect([...vendored].sort()).toEqual([...live].sort())
    }
  })

  it('colors.css does NOT vendor the layout sizings (--nb-tile-height/--nb-joint-size)', () => {
    expect(colorsCss).not.toContain('--nb-tile-height')
    expect(colorsCss).not.toContain('--nb-joint-size')
  })
})

describe('B2 — Dupes-toolbar inline styles folded into a token-layer class', () => {
  const rescan = indexHtml.match(/<button id="wb-dupes-rescan"[^>]*>/)?.[0] ?? ''
  const bulk = indexHtml.match(/<button id="wb-dupes-bulk-delete"[^>]*>/)?.[0] ?? ''

  it('app.css declares the .wb-toolbar-sm sizing class', () => {
    expect(appCss).toMatch(/\.wb-toolbar-sm\s*\{[^}]*font-size:\s*12px[^}]*padding:\s*4px 12px/)
  })

  it('Rescan keeps secondary-btn, gains wb-toolbar-sm, drops inline style', () => {
    expect(rescan).toContain('class="secondary-btn wb-toolbar-sm"')
    expect(rescan).not.toMatch(/style=/)
  })

  it('bulk-delete keeps primary + disabled, gains wb-toolbar-sm + spacer, drops inline style', () => {
    expect(bulk).toContain('wb-toolbar-sm')
    expect(bulk).toContain('wb-toolbar-spacer')
    expect(bulk).toMatch(/class="primary /)
    expect(bulk).toContain('disabled')
    expect(bulk).not.toMatch(/style=/)
  })

  it('bulk-delete right-alignment comes from a utility, not inline margin', () => {
    expect(appCss).toMatch(/\.wb-toolbar-spacer\s*\{[^}]*margin-left:\s*auto/)
  })
})
