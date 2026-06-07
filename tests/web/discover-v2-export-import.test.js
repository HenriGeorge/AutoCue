/**
 * Tests for the Discover v2 export / import polish (T-036) —
 * _discoverV2ExportFilename timestamping + _formatImportDiff structured
 * delta summary. Mirrors docs/index.html — keep in sync.
 */

import { describe, it, expect } from 'vitest'

function _discoverV2ExportFilename(now) {
  const d = now || new Date()
  const yyyy = d.getFullYear()
  const mm = String(d.getMonth() + 1).padStart(2, '0')
  const dd = String(d.getDate()).padStart(2, '0')
  return `discover-${yyyy}-${mm}-${dd}.db.gz`
}

function _formatImportDiff(before, after) {
  const labels = {
    saved: 'saves',
    dismissed: 'dismisses',
    snoozed: 'snoozes',
    downloaded: 'downloads',
    followed_labels: 'followed labels',
    blocked_artists: 'blocked artists',
    blocked_labels: 'blocked labels',
  }
  const parts = []
  for (const key of Object.keys(labels)) {
    const b = (before && before[key]) || 0
    const a = (after && after[key]) || 0
    const delta = a - b
    if (delta === 0) continue
    const sign = delta > 0 ? '+' : ''
    parts.push(`${sign}${delta} ${labels[key]}`)
  }
  if (!parts.length) return 'Imported — no changes'
  return 'Imported · ' + parts.join(', ')
}


/* ============================================================ filename */

describe('_discoverV2ExportFilename', () => {
  it('uses YYYY-MM-DD format with zero-padding', () => {
    const d = new Date(2026, 5, 7)  // June 7, 2026
    expect(_discoverV2ExportFilename(d)).toBe('discover-2026-06-07.db.gz')
  })

  it('zero-pads single-digit months and days', () => {
    const d = new Date(2027, 0, 1)  // Jan 1, 2027
    expect(_discoverV2ExportFilename(d)).toBe('discover-2027-01-01.db.gz')
  })

  it('handles end-of-year correctly', () => {
    const d = new Date(2030, 11, 31)  // Dec 31, 2030
    expect(_discoverV2ExportFilename(d)).toBe('discover-2030-12-31.db.gz')
  })

  it('uses the current date when no argument is passed', () => {
    const fn = _discoverV2ExportFilename()
    expect(fn).toMatch(/^discover-\d{4}-\d{2}-\d{2}\.db\.gz$/)
  })
})


/* ============================================================ diff */

describe('_formatImportDiff', () => {
  it('returns "no changes" when before === after', () => {
    const b = {saved: 5, dismissed: 3, snoozed: 0, downloaded: 0,
               followed_labels: 2, blocked_artists: 0, blocked_labels: 0}
    expect(_formatImportDiff(b, b)).toBe('Imported — no changes')
  })

  it('formats a positive delta with +N', () => {
    const b = {saved: 5, dismissed: 0, snoozed: 0, downloaded: 0,
               followed_labels: 2, blocked_artists: 0, blocked_labels: 0}
    const a = {...b, saved: 12}
    expect(_formatImportDiff(b, a)).toBe('Imported · +7 saves')
  })

  it('formats a negative delta with -N (no double sign)', () => {
    const b = {saved: 12, dismissed: 0, snoozed: 0, downloaded: 0,
               followed_labels: 2, blocked_artists: 0, blocked_labels: 0}
    const a = {...b, saved: 5}
    expect(_formatImportDiff(b, a)).toBe('Imported · -7 saves')
  })

  it('joins multiple deltas with commas', () => {
    const b = {saved: 5, dismissed: 3, snoozed: 0, downloaded: 0,
               followed_labels: 2, blocked_artists: 0, blocked_labels: 0}
    const a = {saved: 8, dismissed: 1, snoozed: 0, downloaded: 0,
               followed_labels: 4, blocked_artists: 0, blocked_labels: 0}
    expect(_formatImportDiff(b, a)).toBe('Imported · +3 saves, -2 dismisses, +2 followed labels')
  })

  it('skips fields with no change', () => {
    const b = {saved: 5, dismissed: 3, snoozed: 0, downloaded: 0,
               followed_labels: 2, blocked_artists: 0, blocked_labels: 0}
    const a = {...b, saved: 6}
    expect(_formatImportDiff(b, a)).not.toContain('dismisses')
    expect(_formatImportDiff(b, a)).not.toContain('snoozes')
  })

  it('tolerates missing keys in before/after', () => {
    expect(_formatImportDiff({}, {saved: 5})).toBe('Imported · +5 saves')
    expect(_formatImportDiff({saved: 5}, {})).toBe('Imported · -5 saves')
    expect(_formatImportDiff(null, null)).toBe('Imported — no changes')
  })

  it('handles all 7 categories', () => {
    const b = {saved: 0, dismissed: 0, snoozed: 0, downloaded: 0,
               followed_labels: 0, blocked_artists: 0, blocked_labels: 0}
    const a = {saved: 1, dismissed: 1, snoozed: 1, downloaded: 1,
               followed_labels: 1, blocked_artists: 1, blocked_labels: 1}
    const out = _formatImportDiff(b, a)
    expect(out).toContain('saves')
    expect(out).toContain('dismisses')
    expect(out).toContain('snoozes')
    expect(out).toContain('downloads')
    expect(out).toContain('followed labels')
    expect(out).toContain('blocked artists')
    expect(out).toContain('blocked labels')
  })
})
