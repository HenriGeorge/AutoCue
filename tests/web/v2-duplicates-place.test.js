/**
 * P3 — Duplicates as a place (rail place → center-pane view).
 *
 * T1 source-contract section: the legacy seams the v2 place module rides on —
 * three ACBridge pass-throughs, the R9 tracks-bus invalidation inside
 * _onTracksDeleted, and the autocue:duplicates-deleted CustomEvent that the
 * T5 restore sheet consumes. All additive; legacy behavior unchanged.
 */
import { describe, it, expect } from 'vitest'
import { loadAppHtml } from './_source.js'

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
