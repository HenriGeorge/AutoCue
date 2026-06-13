/**
 * P5 — Discover as a place (rail place → centre-pane view).
 *
 * Mirrors the P3 duplicates-place test layout:
 *   T1  source-contract: the legacy seams the v2 place rides on (window.DiscoverV2,
 *       ACBridge.discover accessors, _renderDetailBody exposure) — all additive.
 *   T2  jsdom: the place skeleton's centre-pane swap mechanics + mutual exclusion.
 *   T3  inspector re-host + mode flag.
 *   T4  restyle is token-clean.
 *   T5  aliveness transitions are reduced-motion-gated.
 *   T6  ⌘K commands target the place.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadAppHtml } from './_source.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const V2_DIR = resolve(__dirname, '..', '..', 'docs', 'js', 'v2')

const src = loadAppHtml()

describe('P5 T1 — legacy seams for the discover place', () => {
  it('exposes the DiscoverV2 IIFE on window (read surface for the place)', () => {
    expect(src).toMatch(/window\.DiscoverV2\s*=\s*DiscoverV2\b/)
  })

  it('leaves the IIFE return object untouched (no behaviour change)', () => {
    // The public return object still lists the same surface — assignment to
    // window must not have edited it.
    expect(src).toMatch(/return\s*{\s*\n\s*state,\s*subscribe,/)
    expect(src).toContain('loadInitialState, runScan, cancelScan,')
  })

  it('exposes _renderDetailBody for the inspector re-host (T3)', () => {
    expect(src).toMatch(/window\._renderDiscoverRenderDetail\s*=\s*_renderDetailBody\b/)
  })

  describe('ACBridge.discover pass-throughs', () => {
    it('exposes discoverRunScan delegating to DiscoverV2.runScan', () => {
      expect(src).toMatch(/discoverRunScan:\s*\(\)\s*=>\s*window\.DiscoverV2\?\.runScan\(\)/)
    })
    it('exposes discoverLoadInitialState delegating to DiscoverV2.loadInitialState', () => {
      expect(src).toMatch(/discoverLoadInitialState:\s*\(\)\s*=>\s*window\.DiscoverV2\?\.loadInitialState\(\)/)
    })
    it('exposes discoverState reading DiscoverV2.state', () => {
      expect(src).toMatch(/discoverState:\s*\(\)\s*=>\s*\(window\.DiscoverV2\s*\?\s*window\.DiscoverV2\.state\s*:\s*null\)/)
    })
    it('exposes discoverLoadDetail delegating to DiscoverV2.loadDetail', () => {
      expect(src).toMatch(/discoverLoadDetail:\s*\(id\)\s*=>\s*window\.DiscoverV2\?\.loadDetail\(id\)/)
    })
  })
})

/**
 * T2 jsdom section — the place skeleton's centre-pane swap mechanics + mutual
 * exclusion. The module is imported once (vitest registry); state is reset by
 * driving deactivate() between tests.
 */
function _setupDom() {
  document.body.className = 'wb-active'
  document.body.innerHTML = `
    <div id="tab-group">
      <button id="tab-cues" class="tab-btn active"></button>
      <button id="tab-discover" class="tab-btn"></button>
    </div>
    <div id="cues-tab-content"></div>
    <div id="discover-tab-content" style="display:none">
      <div id="disc-v2-grid"></div>
      <div id="disc-v2-detail-panel"><div id="disc-v2-detail-body"></div></div>
    </div>
    <aside id="wb-rail">
      <div id="wb-crates"></div>
      <button id="wb-disc-place" type="button" class="wb-crate"></button>
      <button id="wb-dupes-place" type="button" class="wb-crate"></button>
    </aside>
    <aside id="wb-inspector">
      <div id="wb-inspector-empty"></div>
      <div id="wb-inspector-body" hidden></div>
    </aside>
    <div id="tracks-sticky"><div id="wb-grid-head"></div></div>
    <div id="track-list"></div>
    <section id="wb-dupes-pane" hidden>
      <div id="wb-dupes-host"></div>
    </section>
  `
}

describe('P5 T2 — discover place swap mechanics (jsdom)', () => {
  let disc
  let switchCalls

  beforeEach(async () => {
    _setupDom()
    switchCalls = []
    window.switchTab = vi.fn((name) => {
      switchCalls.push(name)
      document.getElementById('cues-tab-content').style.display = name === 'cues' ? '' : 'none'
      document.getElementById('discover-tab-content').style.display = name === 'discover' ? '' : 'none'
    })
    window.ACBridge = {
      isLocalMode: () => true,
      renderTracks: vi.fn(),
      discoverLoadInitialState: vi.fn(),
      discoverState: () => ({ cardsByKey: new Map() }),
      discoverLoadDetail: vi.fn(),
    }
    window.AC2 = window.AC2 || {}
    disc = await import('../../docs/js/v2/workbench/discover.js')
    disc.deactivate() // reset any leaked state
    window.ACBridge.renderTracks.mockClear()
    window.switchTab.mockClear()
    switchCalls.length = 0
  })

  it('activate() shows the discover tab body, hides the grid surfaces + inspector, marks the place', () => {
    disc.activate()
    expect(disc.isActive()).toBe(true)
    expect(switchCalls).toContain('discover')
    for (const id of ['tracks-sticky', 'track-list', 'wb-grid-head']) {
      expect(document.getElementById(id).hidden, `#${id} should be hidden`).toBe(true)
    }
    expect(document.getElementById('wb-inspector').hidden).toBe(true)
    expect(document.body.classList.contains('wb-place-disc')).toBe(true)
    expect(document.getElementById('wb-disc-place').classList.contains('active')).toBe(true)
  })

  it('activate() never detaches or re-parents #track-list (TASK-033/037)', () => {
    const list = document.getElementById('track-list')
    const parent = list.parentNode
    disc.activate()
    expect(document.getElementById('track-list')).toBe(list)
    expect(list.parentNode).toBe(parent)
  })

  it('deactivate() restores every toggle, switches back to cues, repaints the grid', () => {
    disc.activate()
    disc.deactivate()
    expect(disc.isActive()).toBe(false)
    expect(switchCalls).toContain('cues')
    for (const id of ['tracks-sticky', 'track-list', 'wb-grid-head']) {
      expect(document.getElementById(id).hidden, `#${id} should be restored`).toBe(false)
    }
    expect(document.getElementById('wb-inspector').hidden).toBe(false)
    expect(document.body.classList.contains('wb-place-disc')).toBe(false)
    expect(document.getElementById('wb-disc-place').classList.contains('active')).toBe(false)
    expect(window.ACBridge.renderTracks).toHaveBeenCalled()
  })

  it('stays inert outside local mode (R-guard)', () => {
    window.ACBridge.isLocalMode = () => false
    disc.activate()
    expect(disc.isActive()).toBe(false)
    expect(switchCalls).not.toContain('discover')
  })

  it('lazy first load delegates to ACBridge.discoverLoadInitialState exactly once', () => {
    disc.activate()
    disc.deactivate()
    disc.activate()
    // _loadedOnce guard: idempotent across re-entries.
    expect(window.ACBridge.discoverLoadInitialState.mock.calls.length).toBeLessThanOrEqual(1)
    disc.deactivate()
  })

  it('initDiscoverPlace(): re-clicking the active rail entry toggles back to the grid', () => {
    disc.initDiscoverPlace()
    const btn = document.getElementById('wb-disc-place')
    btn.click()
    expect(disc.isActive()).toBe(true)
    btn.click()
    expect(disc.isActive()).toBe(false)
  })

  it('announces place changes so the shell can repaint crate active-state', () => {
    const seen = vi.fn()
    window.addEventListener('autocue:wb-place-change', seen)
    disc.activate()
    disc.deactivate()
    window.removeEventListener('autocue:wb-place-change', seen)
    expect(seen).toHaveBeenCalledTimes(2)
  })

  it('activate() deactivates the Duplicates place (mutual exclusion)', () => {
    const dupesDeactivate = vi.fn()
    window.AC2.duplicates = { deactivate: dupesDeactivate, isActive: () => false }
    disc.activate()
    expect(dupesDeactivate).toHaveBeenCalled()
    disc.deactivate()
    delete window.AC2.duplicates
  })
})

describe('P5 T2 — shell/rail/dupes exit the discover place (module source contract)', () => {
  const shellSrc = readFileSync(resolve(V2_DIR, 'workbench', 'shell.js'), 'utf8')
  const railSrc = readFileSync(resolve(V2_DIR, 'workbench', 'rail.js'), 'utf8')
  const dupesSrc = readFileSync(resolve(V2_DIR, 'workbench', 'duplicates.js'), 'utf8')
  const discSrc = readFileSync(resolve(V2_DIR, 'workbench', 'discover.js'), 'utf8')

  it('crate click + workbench deactivate route through AC2.discover.deactivate', () => {
    expect(shellSrc.match(/AC2\.discover\.deactivate\(\)/g)?.length ?? 0)
      .toBeGreaterThanOrEqual(2)
  })
  it('crates paint no active row while the discover place owns the centre', () => {
    expect(shellSrc).toContain('AC2.discover')
    expect(shellSrc).toContain('isActive()')
  })
  it('rail playlist + saved-filter clicks exit the discover place first', () => {
    expect(railSrc.match(/AC2\.discover\.deactivate\(\)/g)?.length ?? 0)
      .toBeGreaterThanOrEqual(2)
  })
  it('duplicates.activate() deactivates the discover place (reverse mutual exclusion)', () => {
    expect(dupesSrc).toContain('AC2.discover')
    expect(dupesSrc).toMatch(/AC2\.discover\.deactivate\(\)/)
  })
  it('the v2 discover place never talks to /api/discover itself (R10 — no parallel impl)', () => {
    expect(discSrc).not.toContain('/api/discover')
    expect(discSrc).not.toContain('/api/youtube')
    expect(discSrc).not.toContain('fetch(')
  })
})

/**
 * T3 — release detail re-hosted into the inspector (R4).
 */
describe('P5 T3 — legacy slide-in suppressed when the place owns the centre (source contract)', () => {
  it('_openDetailPanel early-returns to focusRelease when the discover place is active', () => {
    const fn = src.slice(
      src.indexOf('async function _openDetailPanel('),
      src.indexOf("const panel = document.getElementById('disc-v2-detail-panel');"),
    )
    expect(fn).toContain('window.AC2.discover.isActive()')
    expect(fn).toContain('window.AC2.discover.focusRelease(releaseKey)')
    expect(fn).toMatch(/return;\s*\n\s*}/)
  })
  it('exposes _renderDetailBody so the inspector reuses the legacy markup (not duplicated)', () => {
    expect(src).toMatch(/window\._renderDiscoverRenderDetail\s*=\s*_renderDetailBody/)
  })
})

function _inspectorDom() {
  document.body.className = 'wb-active'
  document.body.innerHTML = `
    <aside id="wb-inspector">
      <div id="wb-inspector-empty"></div>
      <div id="wb-inspector-body" hidden></div>
    </aside>
    <div id="track-list"></div>
    <div id="disc-v2-detail-panel"><div id="disc-v2-detail-body"></div></div>
  `
}

describe('P5 T3 — inspector release re-host + mode flag (jsdom)', () => {
  let insp
  let renderCalls

  beforeEach(async () => {
    _inspectorDom()
    renderCalls = []
    window._renderDiscoverRenderDetail = vi.fn((release, detail, status) => {
      renderCalls.push({ status })
      const host = document.getElementById('disc-v2-detail-body')
      if (host) host.textContent = `${status}:${release.release?.title || ''}`
    })
    window.ACBridge = {
      tracks: () => [],
      discoverState: () => ({
        cardsByKey: new Map([[
          'rk1', { release_key: 'rk1', release: { id: 101, title: 'Madvillainy', artist: 'Madvillain', label: 'Stones Throw', year: 2004, styles: ['Hip Hop'] } },
        ]]),
      }),
      discoverLoadDetail: vi.fn().mockResolvedValue({ id: 101, tracklist: [] }),
    }
    insp = await import('../../docs/js/v2/workbench/inspector.js')
    insp.clearInspector()
  })

  it('renderReleaseInspector populates the inspector body in release mode with mono data chips', () => {
    insp.renderReleaseInspector('rk1')
    expect(insp.inspectorMode()).toBe('release')
    const body = document.getElementById('wb-inspector-body')
    expect(body.hidden).toBe(false)
    expect(body.querySelector('.wb-insp-title').textContent).toBe('Madvillainy')
    const chips = Array.from(body.querySelectorAll('.wb-insp-chip')).map((c) => c.textContent)
    expect(chips).toContain('2004')
    expect(chips).toContain('Stones Throw')
    expect(chips).toContain('#101')
    expect(chips).toContain('Hip Hop')
    // R6 — data chips are mono.
    expect(body.querySelector('.wb-insp-chip').classList.contains('mono')).toBe(true)
  })

  it('reuses the legacy _renderDetailBody (loading then loaded) — no duplicated markup', async () => {
    insp.renderReleaseInspector('rk1')
    await Promise.resolve(); await Promise.resolve()
    expect(window._renderDiscoverRenderDetail).toHaveBeenCalled()
    expect(renderCalls.some((c) => c.status === 'loading')).toBe(true)
  })

  it('clearInspector resets mode to track + restores the detail node to its panel', () => {
    insp.renderReleaseInspector('rk1')
    // node relocated into the inspector
    expect(document.getElementById('wb-inspector-body').contains(document.getElementById('disc-v2-detail-body'))).toBe(true)
    insp.clearInspector()
    expect(insp.inspectorMode()).toBe('track')
    // node back inside the panel
    expect(document.getElementById('disc-v2-detail-panel').contains(document.getElementById('disc-v2-detail-body'))).toBe(true)
    expect(document.getElementById('wb-inspector-body').hidden).toBe(true)
  })

  it('a grid click while in release mode does not clobber the inspector with track detail', () => {
    insp.renderReleaseInspector('rk1')
    insp.initInspector()
    const list = document.getElementById('track-list')
    const card = document.createElement('div')
    card.className = 'track-card'
    card.dataset.trackId = '5'
    list.appendChild(card)
    card.click()
    // still in release mode — the early-return held
    expect(insp.inspectorMode()).toBe('release')
    expect(document.getElementById('wb-inspector-body').querySelector('.wb-insp-title').textContent).toBe('Madvillainy')
  })
})

/**
 * T4 — restyle to the five rules (presentation-only). Logic unchanged; only
 * inline styles moved to .disc-v2-* classes, emoji removed from builders, and
 * every hardcoded hex/rgba became a token in the restyled block.
 */
describe('P5 T4 — Discover restyle is token-clean + emoji-free', () => {
  const css = readFileSync(resolve(__dirname, '..', '..', 'docs', 'css', 'app.css'), 'utf8')
  const discJs = readFileSync(resolve(__dirname, '..', '..', 'docs', 'js', '03-download-discover.js'), 'utf8')

  // Scope to the restyled card + action + detail-action region so unrelated
  // legacy hexes (the YT video letterbox #000 / modal scrim, intentionally
  // non-themeable) don't false-positive.
  const cardRegion = css.slice(
    css.indexOf('.disc-v2-card {'),
    css.indexOf('.disc-v2-spinner {\n      display: inline-block'),
  )
  const detailActionRegion = css.slice(
    css.indexOf('.disc-v2-detail-action {'),
    css.indexOf('.disc-v2-detail-tracklist {'),
  )
  const chromeRegion = css.slice(
    css.indexOf('.disc-v2-head {'),
    css.indexOf('.disc-v2-spinner {\n      display: inline-block'),
  )

  it('the restyled card region has no hardcoded hex or rgba (R5)', () => {
    expect(cardRegion).not.toMatch(/#[0-9a-f]{3,6}\b/i)
    expect(cardRegion).not.toMatch(/rgba?\(/)
  })
  it('the detail-action region has no hardcoded hex or rgba (R5)', () => {
    expect(detailActionRegion).not.toMatch(/#[0-9a-f]{3,6}\b/i)
    expect(detailActionRegion).not.toMatch(/rgba?\(/)
  })
  it('the place chrome region (banners/filters/scan/settings) has no hex or rgba (R5)', () => {
    expect(chromeRegion).not.toMatch(/#[0-9a-f]{3,6}\b/i)
    expect(chromeRegion).not.toMatch(/rgba?\(/)
  })
  it('no var(--amber, #hex) fallback survives in the Discover CSS (T4.4 — real token)', () => {
    const discCss = css.slice(css.indexOf('/* ── Discover v2'), css.indexOf('/* ── /v2: workbench'))
    expect(discCss).not.toMatch(/var\(--amber,\s*#[0-9a-f]{3,6}\)/i)
  })

  it('the scan CTA is the ink pill, never green (rule 2)', () => {
    const cta = css.slice(css.indexOf('.disc-v2-scan-cta {'), css.indexOf('.disc-v2-pill-sm {'))
    expect(cta).toContain('var(--ink)')
    expect(cta).toContain('var(--on-ink)')
    expect(cta).not.toContain('var(--green)')
  })

  it('save-applied uses green (rule 2 — green = success signal)', () => {
    expect(cardRegion).toMatch(/\.disc-v2-card-action\.saved\s*{[^}]*var\(--green\)/)
  })

  it('the card source line + inspector detail data are mono (rule 3 / R6)', () => {
    const sourceStart = css.indexOf('.disc-v2-card-source {')
    const source = css.slice(sourceStart, css.indexOf('}', sourceStart))
    expect(source).toContain('var(--font-mono)')
  })

  it('emoji are gone from the card + detail action builders', () => {
    const cardBuilder = discJs.slice(
      discJs.indexOf('function _renderDiscoverV2Card('),
      discJs.indexOf('function _applyDiscoverV2Sort('),
    )
    for (const e of ['💚', '💤', '🚫']) expect(cardBuilder).not.toContain(e)
    const detailBuilder = discJs.slice(
      discJs.indexOf('function _renderDetailBody('),
      discJs.indexOf('function _detailTrapKeydown('),
    )
    for (const e of ['💚', '💤', '🚫', '⬇']) expect(detailBuilder).not.toContain(e)
  })

  it('the action delegation contract is intact (data-act survives the restyle)', () => {
    expect(discJs).toContain('data-act="save"')
    expect(discJs).toContain('data-act="snooze"')
    expect(discJs).toContain('data-act="dismiss"')
    expect(discJs).toContain('data-detail-act="save"')
  })
})

/**
 * T5 — aliveness round 2. Every new transition/animation is reduced-motion-gated:
 * either authored inside a `no-preference` block, or suppressed in the `reduce`
 * block. JS-driven motion is guarded by _prefersReducedMotion.
 */
describe('P5 T5 — aliveness round 2 is reduced-motion-gated', () => {
  const css = readFileSync(resolve(__dirname, '..', '..', 'docs', 'css', 'app.css'), 'utf8')
  const discJs = readFileSync(resolve(__dirname, '..', '..', 'docs', 'js', '03-download-discover.js'), 'utf8')

  it('the save-pop micro-feedback only animates under prefers-reduced-motion: no-preference', () => {
    const noPref = css.slice(
      css.indexOf('@media (prefers-reduced-motion: no-preference) {', css.indexOf('.disc-v2-card-action.saved {')),
    )
    expect(noPref.slice(0, noPref.indexOf('}\n'))).toContain('disc-v2-save-pop')
  })

  it('the reduce block suppresses the place crossfade, save-pulse, scan bar + spinner', () => {
    const reduce = css.slice(
      css.indexOf('.skeleton-card .skel-line { animation: none; }'),
    )
    const block = reduce.slice(0, reduce.indexOf('  }\n'))
    expect(block).toContain('.tab-entering { animation: none; }')
    expect(block).toContain('.disc-v2-card-action.saved { animation: none; }')
    expect(block).toContain('#disc-v2-scan-progress-fill { transition: none; }')
    expect(block).toContain('.disc-v2-spinner { animation: none; }')
  })

  it('the rail-crate press + reduced-motion suppression are both present', () => {
    expect(css).toContain('.wb-crate:active { transform: scale(.99); }')
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) { \.wb-crate, \.wb-crate:active { transition: none; transform: none; } }/)
  })

  it('JS-driven card motion (dismiss collapse, enter stagger) stays guarded by _prefersReducedMotion', () => {
    const collapse = discJs.slice(
      discJs.indexOf('function _collapseDiscoverCard('),
      discJs.indexOf('function _handleDiscoverKeydown('),
    )
    expect(collapse).toContain('if (_prefersReducedMotion')
    expect(discJs).toContain('if (freshRender && !_prefersReducedMotion)')
  })
})
