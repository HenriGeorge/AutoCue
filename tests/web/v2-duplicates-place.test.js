/**
 * P3 — Duplicates as a place (rail place → center-pane view).
 *
 * T1 source-contract section: the legacy seams the v2 place module rides on —
 * three ACBridge pass-throughs, the R9 tracks-bus invalidation inside
 * _onTracksDeleted, and the autocue:duplicates-deleted CustomEvent that the
 * T5 restore sheet consumes. All additive; legacy behavior unchanged.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
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

/**
 * T3 — the pane is the real Duplicates surface; the legacy section is gone.
 */
describe('P3 T3 — re-hosted machinery (source contract)', () => {
  it('scanDuplicates reads the toolbar rescan pill (#wb-dupes-rescan)', () => {
    const fn = src.slice(
      src.indexOf('async function scanDuplicates('),
      src.indexOf('function _onDuplicatesBulkDelete('),
    )
    expect(fn).toContain("getElementById('wb-dupes-rescan')")
    expect(fn).not.toContain('duplicates-scan-btn')
  })

  it('no stale legacy duplicates ids remain anywhere in docs/', () => {
    const docs = resolve(__dirname, '..', '..', 'docs')
    const files = [
      resolve(docs, 'index.html'),
      resolve(docs, 'css', 'app.css'),
      ...readdirSync(resolve(docs, 'js'), { recursive: true })
        .filter((f) => typeof f === 'string' && f.endsWith('.js'))
        .map((f) => resolve(docs, 'js', f)),
    ]
    for (const f of files) {
      const code = readFileSync(f, 'utf8')
      for (const stale of ['duplicates-section', 'duplicates-scan-btn', 'duplicates-bulk-delete-btn']) {
        expect(code.includes(stale), `${f} still references ${stale}`).toBe(false)
      }
    }
  })

  it('the pane hosts every id the SSE reader reads (host-id reuse)', () => {
    const paneStart = src.indexOf('id="wb-dupes-pane"')
    const paneEnd = src.indexOf('</section>', paneStart)
    const pane = src.slice(paneStart, paneEnd)
    for (const id of ['wb-dupes-rescan', 'duplicates-status-label', 'duplicates-summary',
      'wb-dupes-bulk-delete', 'duplicates-progress', 'duplicates-empty', 'duplicates-list']) {
      expect(pane, `#${id} must live inside #wb-dupes-pane`).toContain(`id="${id}"`)
    }
  })

  it('the confirm modal + backdrop stay body-level (NOT inside the pane)', () => {
    const paneStart = src.indexOf('id="wb-dupes-pane"')
    const paneEnd = src.indexOf('</section>', paneStart)
    const pane = src.slice(paneStart, paneEnd)
    expect(pane).not.toContain('duplicates-confirm')
    expect(src).toContain('id="duplicates-confirm"')
    expect(src).toContain('id="duplicates-confirm-backdrop"')
  })

  it('_onDuplicatesBulkDelete re-collects at click time and routes through _openDuplicatesConfirm (R6)', () => {
    const fn = src.slice(
      src.indexOf('function _onDuplicatesBulkDelete('),
      src.indexOf('// ── Duplicates: destructive delete'),
    )
    expect(fn).toContain('dataset.nonKeeperIds')
    expect(fn).toContain('_openDuplicatesConfirm(')
    expect(fn).toContain('_onTracksDeleted(ids)')
    expect(fn).toContain('scanDuplicates()')
  })

  it('the static bulk button is wired once in _wireDuplicatesConfirm', () => {
    const fn = src.slice(src.indexOf('(function _wireDuplicatesConfirm'))
    expect(fn.slice(0, fn.indexOf('})();'))).toContain("getElementById('wb-dupes-bulk-delete')")
  })

  it('the v2 place module never references the legacy lexical bindings bare (R6)', () => {
    const code = readFileSync(resolve(V2_DIR, 'workbench', 'duplicates.js'), 'utf8')
    for (const ident of ['parsedTracks', 'scanDuplicates', '_openDuplicatesConfirm', '_onTracksDeleted']) {
      const bare = new RegExp(`(^|[^.\\w'"\`])${ident}\\b`, 'm')
      expect(bare.test(code), `duplicates.js references bare ${ident}`).toBe(false)
    }
  })
})

describe('P3 T3 — lazy first scan once the hosts live in the pane (jsdom)', () => {
  it('activate() scans exactly once; rescans are the pill, not re-entry', async () => {
    _setupDom()
    // emulate the T3 markup: the list host lives inside the pane
    const list = document.createElement('div')
    list.id = 'duplicates-list'
    document.getElementById('wb-dupes-host')?.remove()
    document.getElementById('wb-dupes-pane').appendChild(list)
    const bridge = { isLocalMode: () => true, renderTracks: vi.fn(), scanDuplicates: vi.fn() }
    window.ACBridge = bridge
    const dupes = await import('../../docs/js/v2/workbench/duplicates.js')
    dupes.deactivate()
    bridge.scanDuplicates.mockClear()
    dupes.activate()
    const callsAfterFirst = bridge.scanDuplicates.mock.calls.length
    dupes.deactivate()
    dupes.activate()
    expect(callsAfterFirst).toBeLessThanOrEqual(1)
    expect(bridge.scanDuplicates.mock.calls.length).toBe(callsAfterFirst)
    dupes.deactivate()
  })
})

/**
 * T4 — restyle to the five rules (presentation-only). The duplicates render
 * logic is unchanged; only inline styles moved to .wb-dup-* classes and every
 * hardcoded hex became a token. These guard against a regression that reaches
 * back for inline cssText or a raw hex.
 */
describe('P3 T4 — duplicates restyle is token-clean + class-driven', () => {
  const opsSrc = readFileSync(
    resolve(__dirname, '..', '..', 'docs', 'js', '02-local-ops.js'), 'utf8')
  // Scope to the duplicates region (render + scan + undo banner) so unrelated
  // legacy hexes elsewhere in the file don't false-positive.
  const dupRegion = opsSrc.slice(
    opsSrc.indexOf('function _renderDuplicateGroup('),
    opsSrc.indexOf('function _refreshDuplicatesSummaryAfterDelete('))
  const undoRegion = opsSrc.slice(
    opsSrc.indexOf('function _showDuplicatesUndoToast('),
    opsSrc.indexOf('function _wireDuplicatesConfirm('))

  it('no hardcoded #e4384e / #c98a00 remain in the duplicates render or undo regions', () => {
    expect(dupRegion).not.toMatch(/#e4384e|#c98a00/)
    expect(undoRegion).not.toMatch(/#e4384e|#c98a00/)
  })
  it('no amber/danger hex-fallback (var(--token, #hex)) in the duplicates regions', () => {
    expect(dupRegion).not.toMatch(/var\(--\w+,\s*#[0-9a-f]{3,6}\)/i)
    expect(undoRegion).not.toMatch(/var\(--\w+,\s*#[0-9a-f]{3,6}\)/i)
  })
  it('the keeper highlight is a class toggle, not an inline background write', () => {
    expect(dupRegion).toContain("row.classList.toggle('keeper', isKeeper)")
    expect(dupRegion).not.toContain('row.style.background')
  })
  it('group, row, count-chip and undo banner use .wb-dup-* classes', () => {
    expect(dupRegion).toContain('wb-dup-group')
    expect(dupRegion).toContain('wb-dup-row')
    expect(dupRegion).toContain('wb-dup-count-chip')
    expect(undoRegion).toContain('wb-dup-undo-banner')
  })
  it('the .wb-dup-* classes + amber/danger tokens are defined in app.css (both themes inherit)', () => {
    const css = readFileSync(
      resolve(__dirname, '..', '..', 'docs', 'css', 'app.css'), 'utf8')
    for (const cls of ['.wb-dup-group', '.wb-dup-row.keeper', '.wb-dup-count-chip',
      '.wb-dup-undo-drain', '#wb-dupes-bulk-delete']) {
      expect(css, `missing ${cls}`).toContain(cls)
    }
    expect(css).toMatch(/--warn-amber:\s*#/)
    expect(css).toMatch(/--danger:\s*#/)
  })
})

/**
 * T5 — restore as a status-sentence sheet (R8). The sheet listens for the
 * autocue:duplicates-deleted event (T1 seam) and POSTs /api/restore for the
 * just-written backup. The legacy in-view banner is untouched (convenience).
 */
function _restoreDom() {
  document.body.className = ''
  document.body.innerHTML = `
    <div id="app-status">
      <span class="status-sep" id="status-sep-restore" hidden>·</span>
      <button id="status-restore" hidden><span class="status-text"></span></button>
    </div>
    <div id="wb-restore-sheet" hidden>
      <div id="wb-restore-heading"></div>
      <div><span id="wb-restore-file"></span></div>
      <button id="wb-restore-go" class="primary">Restore from backup</button>
      <button id="wb-restore-dismiss">Dismiss</button>
    </div>`
}

function _fireDeleted(detail) {
  window.dispatchEvent(new CustomEvent('autocue:duplicates-deleted', { detail }))
}

describe('P3 T5 — restore sheet (R8)', () => {
  let mod
  beforeEach(async () => {
    _restoreDom()
    mod = await import('../../docs/js/v2/restore-sheet.js')
    mod.initRestoreSheet()
    vi.restoreAllMocks()
    // Reset module state from any prior test via the dismiss handler.
    document.getElementById('wb-restore-dismiss').click()
    _restoreDom()
    mod.initRestoreSheet()
  })

  it('shows the status fact when a delete reports a backup_path', () => {
    _fireDeleted({ deleted: 3, requested: 3, cancelled: false, backup_path: '/x/master_20260612.db' })
    expect(document.getElementById('status-restore').hidden).toBe(false)
    expect(document.getElementById('status-restore').textContent).toContain('3 deleted')
    expect(document.getElementById('status-sep-restore').hidden).toBe(false)
    // Sheet stays closed until the fact is clicked.
    expect(document.getElementById('wb-restore-sheet').hidden).toBe(true)
  })

  it('shows NOTHING when the delete reports no backup_path (cancelled-before-write)', () => {
    _fireDeleted({ deleted: 0, requested: 2, cancelled: true, backup_path: null })
    expect(document.getElementById('status-restore').hidden).toBe(true)
    expect(document.getElementById('wb-restore-sheet').hidden).toBe(true)
  })

  it('clicking the fact opens the sheet with the mono backup basename', () => {
    _fireDeleted({ deleted: 5, backup_path: '/Users/x/.autocue/backups/master_20260612.db' })
    document.getElementById('status-restore').click()
    expect(document.getElementById('wb-restore-sheet').hidden).toBe(false)
    expect(document.getElementById('wb-restore-file').textContent).toBe('master_20260612.db')
  })

  it('Restore POSTs /api/restore with the backup basename and hides on success', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
    window.showToast = vi.fn()
    _fireDeleted({ deleted: 4, backup_path: '/a/b/master_99.db' })
    document.getElementById('status-restore').click()
    document.getElementById('wb-restore-go').click()
    await Promise.resolve(); await Promise.resolve()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/restore')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toEqual({ filename: 'master_99.db' })
    await Promise.resolve()
    expect(document.getElementById('wb-restore-sheet').hidden).toBe(true)
    expect(document.getElementById('status-restore').hidden).toBe(true)
  })

  it('a failed restore keeps the sheet open and re-enables the button', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, statusText: 'boom', json: async () => ({ detail: 'locked' }) })
    vi.stubGlobal('fetch', fetchMock)
    window.showToast = vi.fn()
    _fireDeleted({ deleted: 1, backup_path: '/a/master_1.db' })
    document.getElementById('status-restore').click()
    const go = document.getElementById('wb-restore-go')
    go.click()
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve()
    expect(document.getElementById('wb-restore-sheet').hidden).toBe(false)
    expect(go.disabled).toBe(false)
    expect(window.showToast).toHaveBeenCalled()
  })

  it('the fact + sheet expire after the 30s backup window', () => {
    vi.useFakeTimers()
    _fireDeleted({ deleted: 2, backup_path: '/a/master_2.db' })
    expect(document.getElementById('status-restore').hidden).toBe(false)
    vi.advanceTimersByTime(30000)
    expect(document.getElementById('status-restore').hidden).toBe(true)
    expect(document.getElementById('status-sep-restore').hidden).toBe(true)
    vi.useRealTimers()
  })
})
