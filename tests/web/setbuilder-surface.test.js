/**
 * Set Builder 2.0 — "Build a set by BPM arc" surface contract (source-level).
 *
 * Pins the redesigned workbench Set Builder markup + wiring without a build
 * step. Two jobs:
 *   1. Must-preserve guard — every `sb-*` id the control-inventory / build
 *      logic depends on still exists, `#sb-energy-mode` is STILL a real
 *      <select> (kept as the data source; the segmented toggle is id-less and
 *      drives it), and `_sbRenderSet` still writes into #sb-tracklist.
 *   2. New-surface guard — eyebrow/H1/lede, the range sliders, the id-less
 *      segmented energy toggle that mirrors the select both ways, the ink CTA
 *      (never green), the Open-in-Nightboard delegation, the per-card energy
 *      sparkline + transition-score pill, and the surface-scoped _sbPopIn
 *      keyframe (reduced-motion-guarded).
 */
import { describe, it, expect } from 'vitest'
import { loadAppHtml } from './_source.js'

const src = loadAppHtml()
// Markup-only slice of the section, so structural assertions don't accidentally
// match CSS/JS text elsewhere in the inlined source.
const section = src.slice(
  src.indexOf('id="setbuilder-section"'),
  src.indexOf('<!-- DJ Mixing Guide -->'),
)

describe('Set Builder 2.0 — must-preserve ids/types', () => {
  it('keeps every build-logic / inventory sb-* id', () => {
    for (const id of [
      'sb-start-bpm', 'sb-end-bpm', 'sb-duration', 'sb-energy-mode',
      'sb-build-btn', 'sb-use-selected-btn', 'sb-seed-clear',
      'sb-copy-btn', 'sb-save-playlist-btn',
      'sb-result', 'sb-summary', 'sb-tracklist', 'sb-status', 'sb-progress',
    ]) {
      expect(section, `#${id} must be preserved`).toContain(`id="${id}"`)
    }
  })

  it('#sb-energy-mode is STILL a real <select> (data source, not buttons)', () => {
    expect(section).toMatch(/<select id="sb-energy-mode"[^>]*>/)
    // All three energy values stay as options the build logic reads via .value.
    for (const v of ['build', 'flat', 'drop']) {
      expect(section).toContain(`value="${v}"`)
    }
  })

  it('the select is visually hidden + aria-hidden (segmented toggle is its view)', () => {
    expect(section).toMatch(/<select id="sb-energy-mode"[^>]*class="sb2-vh-select"[^>]*aria-hidden="true"/)
  })

  it('BPM + duration inputs are now range sliders, same ids', () => {
    expect(section).toMatch(/<input type="range" id="sb-start-bpm"/)
    expect(section).toMatch(/<input type="range" id="sb-end-bpm"/)
    expect(section).toMatch(/<input type="range" id="sb-duration"/)
  })

  it('_sbRenderSet still writes into #sb-tracklist; buildSet reveals #sb-result', () => {
    expect(src).toMatch(/function _sbRenderSet\(\)\s*{[\s\S]*getElementById\('sb-tracklist'\)/)
    // buildSet reads #sb-result via a local and reveals it on success.
    expect(src).toMatch(/result\s*=\s*document\.getElementById\('sb-result'\)/)
    expect(src).toMatch(/_sbRenderSet\(\);\s*\n\s*result\.style\.display\s*=\s*''/)
  })
})

describe('Set Builder 2.0 — new surface', () => {
  it('has the eyebrow / H1 / lede hero copy', () => {
    expect(section).toContain('class="sb2-eyebrow"')
    expect(section).toMatch(/class="sb2-h1"[^>]*>Build a set by BPM arc</)
    expect(section).toMatch(/class="sb2-lede"[^>]*>Beam-search assembles a deduplicated path/)
  })

  it('config card uses radius-xl + shadow-sm + surface bg', () => {
    expect(src).toMatch(/\.sb2-config\s*{[^}]*border-radius:var\(--radius-xl\)/)
    expect(src).toMatch(/\.sb2-config\s*{[^}]*box-shadow:var\(--shadow-sm\)/)
    expect(src).toMatch(/\.sb2-config\s*{[^}]*background:var\(--surface\)/)
  })

  it('id-less segmented energy toggle has Build/Flat/Drop, no ids', () => {
    const seg = section.slice(section.indexOf('class="sb2-segmented"'), section.indexOf('</div>', section.indexOf('class="sb2-segmented"')) + 6)
    expect(seg).toContain('data-energy="build"')
    expect(seg).toContain('data-energy="flat"')
    expect(seg).toContain('data-energy="drop"')
    // Drift-guard safety: the new interactive controls carry NO id.
    expect(seg).not.toMatch(/<button[^>]*\bid=/)
  })

  it('CTA is the ink pill (.primary), never green; the set-size readout is mono', () => {
    expect(section).toMatch(/id="sb-build-btn"[^>]*class="sb2-cta primary"/)
    // sb-summary readout moved into the CTA row.
    expect(section).toMatch(/class="sb2-set-size" id="sb-summary"/)
    expect(src).toMatch(/\.sb2-set-size\s*{[^}]*font-family:var\(--font-mono\)/)
  })

  it('Open-in-Nightboard pill delegates to the existing Nightboard open', () => {
    expect(section).toContain('class="sb2-nightboard-btn"')
    expect(src).toMatch(/window\.AC2\.nightboard\.open\(\)/)
    expect(src).toMatch(/getElementById\('nb-open-btn'\)\?\.click\(\)/)
  })

  it('cards render a sparkline (reusing _renderEnergySparkline) + score pill', () => {
    expect(src).toMatch(/class="sb2-spark" data-track-id=/)
    expect(src).toMatch(/window\._renderEnergySparkline\(sparkEl\)/)
    expect(src).toContain('sb2-score-pill')
    // green-wash + green text only when score >= 80
    expect(src).toMatch(/score\s*!=\s*null\s*&&\s*score\s*>=\s*80/)
    expect(src).toMatch(/\.sb2-score-pill-on\s*{[^}]*background:var\(--green-wash\)/)
    expect(src).toMatch(/\.sb2-bpm\s*{[^}]*color:var\(--green\)/)
  })

  it('sliders sync to live readouts; segmented toggle mirrors the select both ways', () => {
    expect(src).toMatch(/function _sbInitControls\(\)/)
    // segmented click -> set select.value + dispatch change
    expect(src).toMatch(/sel\.value\s*=\s*b\.dataset\.energy/)
    expect(src).toMatch(/sel\.dispatchEvent\(new Event\('change'/)
    // select change -> repaint buttons
    expect(src).toMatch(/sel\.addEventListener\('change',\s*\(\)\s*=>\s*paintSegs\(sel\.value\)\)/)
  })

  it('staggered entrance keyframe is surface-scoped + reduced-motion guarded', () => {
    expect(src).toContain('@keyframes _sbPopIn')
    expect(src).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*{\s*\.sb2-card\s*{\s*animation:none/)
  })
})
