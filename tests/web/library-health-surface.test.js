/**
 * Library Health — "Cue-readiness scan" surface (2.0 workbench redesign).
 *
 * `_renderHealthSummary` (docs/js/02-local-ops.js) is a DOM-driving legacy
 * classic-script function. Per the repo convention (see wb-rail.test.js) those
 * render paths are covered by e2e + a manual Chrome pass, not jsdom — JSDOM
 * can't faithfully stand up the full legacy script graph + a live ACBridge.
 *
 * This spec guards the *contract* that render path depends on:
 *   - the markup skeleton + every consumer id legacy/v2 code keys on,
 *   - the ring SVG geometry (stroke-dasharray === the r=54 circumference), and
 *   - the design-system CSS contract (.lh-* classes; the ink-pill "Fix all",
 *     never green; the scan/popIn keyframes; the reduced-motion guard).
 */
import { describe, it, expect, beforeAll } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { JSDOM } from 'jsdom'
import { loadAppHtml } from './_source.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const DOCS = resolve(__dirname, '..', '..', 'docs')

let doc
let css
beforeAll(() => {
  // CLAUDE.md: source-reading specs use loadAppHtml() (inlines local CSS/JS into
  // the single-file view), never readFileSync(index.html). JSDOM's default
  // runScripts is off, so the inlined <script> bodies are inert text.
  doc = new JSDOM(loadAppHtml(), { url: 'http://localhost/' }).window.document
  css = readFileSync(resolve(DOCS, 'css', 'app.css'), 'utf8')
})

describe('Library Health surface — markup contract', () => {
  it('renders the "Cue-readiness scan" header + Run-scan button', () => {
    const sec = doc.getElementById('health-section')
    expect(sec).toBeTruthy()
    expect(sec.classList.contains('lh-surface')).toBe(true)
    expect(sec.querySelector('.lh-h1').textContent).toBe('Cue-readiness scan')
    expect(doc.getElementById('health-scan-label-text').textContent).toBe('Run health scan')
  })

  it('preserves every id legacy/v2 consumers depend on', () => {
    // scanLibraryHealth + _renderHealthSummary + rail.js + status-sentence.js +
    // commands.js + 08-set-builder-boot.js all key on these.
    for (const id of [
      'health-scan-btn', 'health-summary', 'health-progress-bar',
      'health-progress-fill', 'health-scanning-label', 'health-score-ring',
      'health-summary-title', 'health-summary-sub', 'health-issue-list',
      'health-fix-row',
    ]) {
      expect(doc.getElementById(id), `#${id} must exist`).toBeTruthy()
    }
  })

  it('adds the redesign host ids (ring arc / done / stats / fixes)', () => {
    for (const id of ['health-ring-arc', 'health-done', 'health-stats', 'health-fixes']) {
      expect(doc.getElementById(id), `#${id} must exist`).toBeTruthy()
    }
  })

  it('ring SVG stroke-dasharray equals the r=54 circumference (2π·54)', () => {
    const arc = doc.getElementById('health-ring-arc')
    expect(arc.getAttribute('r')).toBe('54')
    const dash = parseFloat(arc.getAttribute('stroke-dasharray'))
    expect(dash).toBeCloseTo(2 * Math.PI * 54, 0) // 339.29…
    // Starts fully empty (offset === circumference) so the arc animates in.
    expect(parseFloat(arc.getAttribute('stroke-dashoffset'))).toBeCloseTo(dash, 0)
  })

  it('the score number is a mono ring-num element (design rule 3 — mono for data)', () => {
    const num = doc.getElementById('health-score-ring')
    expect(num.classList.contains('lh-ring-num')).toBe(true)
    expect(css).toMatch(/\.lh-ring-num\b[^}]*font-family:\s*var\(--font-mono\)/)
  })
})

describe('Library Health surface — CSS contract', () => {
  it('defines the lh-* surface classes', () => {
    for (const cls of [
      '.lh-surface', '.lh-card', '.lh-ring-arc', '.lh-scan-shimmer',
      '.lh-stats', '.lh-fix-card', '.lh-fix-all', '.lh-other',
    ]) {
      expect(css.includes(cls), `${cls} rule must exist`).toBe(true)
    }
  })

  it('"Fix all" is the ink pill (--ink), never green (design rule 2)', () => {
    const m = css.match(/\.lh-fix-all\s*\{([^}]*)\}/)
    expect(m).toBeTruthy()
    expect(m[1]).toMatch(/background:\s*var\(--ink\)/)
    expect(m[1]).not.toMatch(/var\(--green/)
  })

  it('ships the scan-sweep + popIn keyframes', () => {
    expect(css).toMatch(/@keyframes _scanSweep/)
    expect(css).toMatch(/@keyframes _popIn/)
  })

  it('honours prefers-reduced-motion for the surface animations', () => {
    expect(css).toMatch(/prefers-reduced-motion[\s\S]{0,400}\.lh-(ring-arc|spinner|scan-shimmer)/)
  })
})
