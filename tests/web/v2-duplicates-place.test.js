/**
 * P3 — Duplicates as a place (rail place → center-pane view).
 *
 * T1 source-contract section: the legacy seams the v2 place module rides on —
 * three ACBridge pass-throughs, the R9 tracks-bus invalidation inside
 * _onTracksDeleted, and the autocue:duplicates-deleted CustomEvent that the
 * T5 restore sheet consumes. All additive; legacy behavior unchanged.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadAppHtml } from './_source.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const V2_DIR = resolve(__dirname, '..', '..', 'docs', 'js', 'v2')

const src = loadAppHtml()

describe('P3 T1 — legacy seams for the duplicates place', () => {
  describe('ACBridge pass-throughs', () => {
    it('exposes scanDuplicates on the bridge', () => {
      expect(src).toMatch(/scanDuplicates:\s*\(\)\s*=>\s*scanDuplicates\(\)/)
    })
    it('exposes openDuplicatesConfirm on the bridge', () => {
      expect(src).toMatch(/openDuplicatesConfirm:\s*\(opts\)\s*=>\s*_openDuplicatesConfirm\(opts\)/)
    })
    it('exposes onTracksDeleted on the bridge', () => {
      expect(src).toMatch(/onTracksDeleted:\s*\(ids\)\s*=>\s*_onTracksDeleted\(ids\)/)
    })
  })

  describe('R9 — _onTracksDeleted invalidates the tracks bus', () => {
    it('signals AppState tracks after the surgical prune', () => {
      const fn = src.slice(
        src.indexOf('function _onTracksDeleted('),
        src.indexOf('function _refreshDuplicatesSummaryAfterDelete('),
      )
      expect(fn).toContain("AppState.signal('tracks')")
      // Guarded — the XML/Pages path has no AppState writes here.
      expect(fn).toMatch(/if\s*\(window\.AppState\)\s*AppState\.signal\('tracks'\)/)
    })
  })

  describe('R8 seam — autocue:duplicates-deleted event', () => {
    it('dispatches from _showDuplicatesUndoToast with the full detail payload', () => {
      const fn = src.slice(
        src.indexOf('function _showDuplicatesUndoToast('),
        src.indexOf('(function _wireDuplicatesConfirm'),
      )
      expect(fn).toContain("new CustomEvent('autocue:duplicates-deleted'")
      for (const key of ['deleted', 'requested', 'cancelled', 'backup_path']) {
        expect(fn).toContain(key)
      }
      // Fired BEFORE the no-backup early return so the sheet sees every outcome.
      const dispatchAt = fn.indexOf("'autocue:duplicates-deleted'")
      const earlyReturnAt = fn.indexOf('if (!backupPath)')
      expect(dispatchAt).toBeGreaterThan(-1)
      expect(earlyReturnAt).toBeGreaterThan(-1)
      expect(dispatchAt).toBeLessThan(earlyReturnAt)
    })
  })
})

/**
 * T2 jsdom section — the place skeleton's swap mechanics (R1/R2/R3).
 * The module is imported once (vitest module registry); state is reset by
 * driving deactivate() between tests.
 */
function _setupDom() {
  document.body.className = ''
  document.body.innerHTML = `
    <aside id="wb-rail">
      <div id="wb-crates"></div>
      <button id="wb-dupes-place" type="button" class="wb-crate"></button>
    </aside>
    <aside id="wb-inspector">
      <div id="wb-inspector-empty"></div>
      <div id="wb-inspector-body" hidden></div>
    </aside>
    <div id="tracks-sticky"><div id="wb-grid-head"></div></div>
    <div id="track-list"></div>
    <section id="wb-dupes-pane" hidden>
      <div id="wb-dupes-toolbar"></div>
      <div id="wb-dupes-host"></div>
    </section>
  `
}

describe('P3 T2 — duplicates place swap mechanics (jsdom)', () => {
  let dupes
  let bridge

  beforeEach(async () => {
    _setupDom()
    bridge = {
      isLocalMode: () => true,
      renderTracks: vi.fn(),
      scanDuplicates: vi.fn(),
    }
    window.ACBridge = bridge
    dupes = await import('../../docs/js/v2/workbench/duplicates.js')
    dupes.deactivate() // reset any state leaked from a prior test
    bridge.renderTracks.mockClear()
  })

  it('activate() hides the grid surfaces, shows the pane, marks the place', () => {
    dupes.activate()
    expect(dupes.isActive()).toBe(true)
    for (const id of ['tracks-sticky', 'track-list', 'wb-grid-head', 'wb-inspector']) {
      expect(document.getElementById(id).hidden, `#${id} should be hidden`).toBe(true)
    }
    expect(document.getElementById('wb-dupes-pane').hidden).toBe(false)
    expect(document.body.classList.contains('wb-place-dupes')).toBe(true)
    expect(document.getElementById('wb-dupes-place').classList.contains('active')).toBe(true)
  })

  it('activate() does NOT detach or re-parent #track-list (TASK-033/037)', () => {
    const list = document.getElementById('track-list')
    const parent = list.parentNode
    dupes.activate()
    expect(document.getElementById('track-list')).toBe(list)
    expect(list.parentNode).toBe(parent)
  })

  it('activate() clears the inspector back to its empty state (R3)', () => {
    const body = document.getElementById('wb-inspector-body')
    const empty = document.getElementById('wb-inspector-empty')
    body.hidden = false
    body.innerHTML = '<div>focused track</div>'
    empty.hidden = true
    dupes.activate()
    expect(body.hidden).toBe(true)
    expect(body.innerHTML).toBe('')
    expect(empty.hidden).toBe(false)
  })

  it('deactivate() restores every toggle and repaints the grid', () => {
    dupes.activate()
    dupes.deactivate()
    expect(dupes.isActive()).toBe(false)
    for (const id of ['tracks-sticky', 'track-list', 'wb-grid-head', 'wb-inspector']) {
      expect(document.getElementById(id).hidden, `#${id} should be restored`).toBe(false)
    }
    expect(document.getElementById('wb-dupes-pane').hidden).toBe(true)
    expect(document.body.classList.contains('wb-place-dupes')).toBe(false)
    expect(document.getElementById('wb-dupes-place').classList.contains('active')).toBe(false)
    expect(bridge.renderTracks).toHaveBeenCalled()
  })

  it('stays inert outside local mode (R-guard)', () => {
    bridge.isLocalMode = () => false
    dupes.activate()
    expect(dupes.isActive()).toBe(false)
    expect(document.getElementById('wb-dupes-pane').hidden).toBe(true)
  })

  it('does not scan while the duplicates hosts still live outside the pane (T2 inert scaffold)', () => {
    dupes.activate()
    expect(bridge.scanDuplicates).not.toHaveBeenCalled()
  })

  it('initDuplicatesPlace(): re-clicking the active rail entry toggles back to the grid', () => {
    dupes.initDuplicatesPlace()
    const btn = document.getElementById('wb-dupes-place')
    btn.click()
    expect(dupes.isActive()).toBe(true)
    btn.click()
    expect(dupes.isActive()).toBe(false)
  })

  it('announces place changes so the shell can repaint crate active-state', () => {
    const seen = vi.fn()
    window.addEventListener('autocue:wb-place-change', seen)
    dupes.activate()
    dupes.deactivate()
    window.removeEventListener('autocue:wb-place-change', seen)
    expect(seen).toHaveBeenCalledTimes(2)
  })
})

describe('P3 T2 — shell/rail exit the place (module source contract)', () => {
  // loadAppHtml only inlines <script src> tags; the v2 module graph is pulled
  // in via import statements, so read the module files directly.
  const shellSrc = readFileSync(resolve(V2_DIR, 'workbench', 'shell.js'), 'utf8')
  const railSrc = readFileSync(resolve(V2_DIR, 'workbench', 'rail.js'), 'utf8')
  const dupesSrc = readFileSync(resolve(V2_DIR, 'workbench', 'duplicates.js'), 'utf8')

  it('crate click + workbench deactivate route through AC2.duplicates.deactivate', () => {
    expect(shellSrc.match(/AC2\.duplicates\.deactivate\(\)/g)?.length ?? 0)
      .toBeGreaterThanOrEqual(2)
  })
  it('crates paint no active row while the place owns the centre', () => {
    expect(shellSrc).toContain('AC2.duplicates.isActive()')
  })
  it('rail playlist + saved-filter clicks exit the place first', () => {
    expect(railSrc.match(/AC2\.duplicates\.deactivate\(\)/g)?.length ?? 0)
      .toBeGreaterThanOrEqual(2)
  })
  it('the v2 place never talks to /api/duplicates itself (R6 — no parallel implementation)', () => {
    expect(dupesSrc).not.toContain('/api/duplicates')
    expect(dupesSrc).not.toContain('fetch(')
  })
})
