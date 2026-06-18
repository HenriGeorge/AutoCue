/**
 * P5 redesign — "New releases for your taste" Discover surface (visual-only).
 *
 * This is a presentation contract test. It pins the NEW workbench-style
 * structure the redesign introduces — the hero (eyebrow + H1), the read-only
 * taste-fingerprint row, the restructured release card, the fixed 2-column
 * grid, and the reduced-motion-gated _discPopIn entrance — WITHOUT touching the
 * frozen DiscoverV2 engine. The existing discover-v2-* tests still own the
 * data/fetch/filter/snooze/save contracts; this only guards the look.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadAppHtml } from './_source.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const html = loadAppHtml()
const css = readFileSync(resolve(__dirname, '..', '..', 'docs', 'css', 'app.css'), 'utf8')
const discJs = readFileSync(
  resolve(__dirname, '..', '..', 'docs', 'js', '03-download-discover.js'),
  'utf8',
)

describe('Discover surface — hero + taste fingerprint (markup)', () => {
  it('renders the "Discover" eyebrow + the "New releases for your taste" H1', () => {
    expect(html).toContain('class="disc-v2-eyebrow"')
    expect(html).toMatch(/disc-v2-head-title">New releases for your taste</)
  })

  it('keeps the load-bearing Settings + Refresh ids unchanged (engine seams)', () => {
    expect(html).toContain('id="disc-v2-settings-btn"')
    expect(html).toContain('id="disc-v2-refresh-btn"')
  })

  it('adds an id-less, class-selectable read-only taste row', () => {
    expect(html).toContain('class="disc-v2-taste-row"')
    expect(html).toContain('class="disc-v2-taste-label"')
    expect(html).toContain('class="disc-v2-taste-chips"')
    // The taste row is read-only — it must NOT introduce a new disc-v2-* id.
    const tasteRow = html.slice(
      html.indexOf('class="disc-v2-taste-row"'),
      html.indexOf('class="disc-v2-taste-chips"') + 40,
    )
    expect(tasteRow).not.toMatch(/id=["']disc-v2-taste/)
  })
})

describe('Discover surface — taste row is sourced from existing state (no fetch)', () => {
  const builder = discJs.slice(
    discJs.indexOf('function _renderDiscoverTasteRow('),
    discJs.indexOf('function _renderDiscoverV2Feed('),
  )

  it('exists and is invoked from the feed renderer', () => {
    expect(builder.length).toBeGreaterThan(0)
    expect(discJs).toContain('_renderDiscoverTasteRow();')
  })

  it('derives chips only from DiscoverV2 state already on hand — no network call', () => {
    expect(builder).toContain('DiscoverV2.state')
    expect(builder).toContain('followedLabels')
    expect(builder).toContain('release?.styles')
    // VISUAL-ONLY: the taste row never fetches or invents a value.
    expect(builder).not.toContain('fetch(')
    expect(builder).not.toContain('/api/discover')
  })
})

describe('Discover surface — restructured release card', () => {
  const cardBuilder = discJs.slice(
    discJs.indexOf('function _renderDiscoverV2Card('),
    discJs.indexOf('function _renderDiscoverTasteRow('),
  )

  it('keeps every must-preserve disc-v2-* card class', () => {
    for (const cls of [
      'disc-v2-card',
      'disc-v2-card-art',
      'disc-v2-card-body',
      'disc-v2-card-title',
      'disc-v2-card-artist',
      'disc-v2-card-source',
      'disc-v2-card-actions',
      'disc-v2-card-action',
    ]) {
      expect(cardBuilder).toContain(cls)
    }
  })

  it('keeps the data-act delegation contract intact (save/snooze/dismiss)', () => {
    expect(cardBuilder).toContain('data-act="save"')
    expect(cardBuilder).toContain('data-act="snooze"')
    expect(cardBuilder).toContain('data-act="dismiss"')
    expect(cardBuilder).toContain('data-actions')
  })

  it('renders a distinct surfacing-reason pill row', () => {
    expect(cardBuilder).toContain('disc-v2-card-reason')
    expect(cardBuilder).toContain('disc-v2-card-reason-text')
    expect(discJs).toContain('function _discoverReason(')
  })

  it('shows always-visible action labels + the saved/dismissed state badge', () => {
    expect(cardBuilder).toContain('Saved ✓')
    expect(cardBuilder).toContain('disc-v2-card-state')
    expect(cardBuilder).toContain('disc-v2-card-dimmed')
  })
})

describe('Discover surface — CSS', () => {
  it('the feed is a fixed 2-column grid that collapses to one column', () => {
    const grid = css.slice(css.indexOf('.disc-v2-grid {'))
    const block = grid.slice(0, grid.indexOf('}'))
    expect(block).toContain('grid-template-columns: 1fr 1fr')
    expect(css).toMatch(/@media \(max-width: 720px\) \{\s*\.disc-v2-grid \{ grid-template-columns: 1fr; \}/)
  })

  it('the card uses a 14px radius + soft shadow + hover lift', () => {
    const start = css.indexOf('.disc-v2-card {')
    const card = css.slice(start, css.indexOf('}', start))
    expect(card).toContain('border-radius: 14px')
    expect(card).toContain('box-shadow: var(--shadow-sm)')
    expect(css).toMatch(/\.disc-v2-card:hover \{ transform: translateY\(-2px\)/)
  })

  it('the 54×54 radius-10 art thumb is token-driven', () => {
    const art = css.slice(css.indexOf('.disc-v2-card-art {'))
    const block = art.slice(0, art.indexOf('}'))
    expect(block).toContain('width: 54px')
    expect(block).toContain('height: 54px')
    expect(block).toContain('border-radius: 10px')
  })

  it('the surface is a centred reading column', () => {
    const surf = css.slice(css.indexOf('.disc-v2-surface {'))
    const block = surf.slice(0, surf.indexOf('}'))
    expect(block).toContain('max-width: 880px')
  })

  it('taste chips are green-wash token pills with mono values', () => {
    const chip = css.slice(css.indexOf('.disc-v2-taste-chip {'))
    const block = chip.slice(0, chip.indexOf('}'))
    expect(block).toContain('var(--green-wash)')
    expect(block).toContain('var(--font-mono)')
    expect(block).toContain('var(--radius-pill)')
  })

  it('the _discPopIn entrance is reduced-motion-gated (no-preference only)', () => {
    const popIdx = css.indexOf('_discPopIn')
    expect(popIdx).toBeGreaterThan(-1)
    // The application is authored inside a no-preference media block.
    const before = css.slice(0, css.indexOf('.disc-v2-card.fade-in-up { animation: _discPopIn'))
    expect(before.lastIndexOf('@media (prefers-reduced-motion: no-preference) {'))
      .toBeGreaterThan(before.lastIndexOf('@media (prefers-reduced-motion: reduce)'))
  })

  it('the restyled card region stays token-clean (no hex / rgba) — both themes', () => {
    const region = css.slice(
      css.indexOf('.disc-v2-card {'),
      css.indexOf('.disc-v2-spinner {\n      display: inline-block'),
    )
    expect(region).not.toMatch(/#[0-9a-f]{3,6}\b/i)
    expect(region).not.toMatch(/rgba?\(/)
  })
})
