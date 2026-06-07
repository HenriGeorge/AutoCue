/**
 * Tests for the Discover v2 Stats surface (T-037) — formatters +
 * _renderDiscoverV2Stats DOM output. Mirrors docs/index.html — keep in sync.
 */

import { describe, it, expect, beforeEach } from 'vitest'

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

function _formatStatsDuration(ms) {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return '–'
  if (ms < 1000) return `${Math.round(ms)} ms`
  const sec = ms / 1000
  if (sec < 60) return `${sec.toFixed(1)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec - m * 60)
  return `${m}m ${s}s`
}

function _formatStatsRatio(n) {
  if (n == null || !Number.isFinite(n)) return '–'
  if (n >= 1) return n.toFixed(1)
  return n.toFixed(2)
}

function _formatStatsPercent(n) {
  if (n == null || !Number.isFinite(n)) return '–'
  return `${Math.round(n * 100)}%`
}

function _renderDiscoverV2Stats(stats) {
  const block = document.getElementById('disc-v2-stats-block')
  if (!block) return
  if (!stats) {
    block.innerHTML = '<em>No stats yet — run a scan first.</em>'
    return
  }
  const noveltyShare = stats.novelty_share || {}
  const noveltyParts = Object.keys(noveltyShare).sort().map(k =>
    `${_esc(k)} ${_formatStatsPercent(noveltyShare[k])}`
  )
  const topLabels = (stats.top_labels || []).slice(0, 5)
  const topArtists = (stats.top_artists || []).slice(0, 5)

  const counts = [
    `<strong>${stats.total_scans}</strong> scans`,
    `<strong>${stats.saved_count}</strong> saved`,
    `<strong>${stats.dismissed_count}</strong> dismissed`,
    `<strong>${stats.snoozed_count}</strong> snoozed`,
    `<strong>${stats.downloaded_count}</strong> downloaded`,
    `<strong>${stats.followed_count}</strong> followed labels`,
  ]
  if (stats.blocked_artist_count || stats.blocked_label_count) {
    counts.push(
      `<strong>${stats.blocked_artist_count + stats.blocked_label_count}</strong> blocked`
    )
  }

  const rows = []
  rows.push(`<div>${counts.join(' · ')}</div>`)
  rows.push(
    `<div>avg scan: <strong>${_formatStatsDuration(stats.avg_duration_ms)}</strong> · ` +
    `saves per scan: <strong>${_formatStatsRatio(stats.saves_per_scan)}</strong></div>`
  )
  if (noveltyParts.length) {
    rows.push(`<div>novelty mix: ${noveltyParts.join(' · ')}</div>`)
  }
  if (topLabels.length) {
    rows.push(
      `<div>top label sources: ` +
      topLabels.map(l => `${_esc(l.name || 'unknown')} (${l.count})`).join(' · ') +
      `</div>`
    )
  }
  if (topArtists.length) {
    rows.push(
      `<div>top artist sources: ` +
      topArtists.map(a => `${_esc(a.name || 'unknown')} (${a.count})`).join(' · ') +
      `</div>`
    )
  }
  block.innerHTML = rows.join('')
}


/* ============================================================ formatters */

describe('_formatStatsDuration', () => {
  it('returns "–" for null / undefined / NaN / non-positive', () => {
    expect(_formatStatsDuration(null)).toBe('–')
    expect(_formatStatsDuration(undefined)).toBe('–')
    expect(_formatStatsDuration(NaN)).toBe('–')
    expect(_formatStatsDuration(0)).toBe('–')
    expect(_formatStatsDuration(-50)).toBe('–')
  })
  it('formats sub-second durations in ms', () => {
    expect(_formatStatsDuration(450)).toBe('450 ms')
    expect(_formatStatsDuration(999)).toBe('999 ms')
  })
  it('formats sub-minute durations in seconds (1 decimal)', () => {
    expect(_formatStatsDuration(1500)).toBe('1.5s')
    expect(_formatStatsDuration(45_000)).toBe('45.0s')
  })
  it('formats minute durations as "Nm Ms"', () => {
    expect(_formatStatsDuration(75_000)).toBe('1m 15s')
    expect(_formatStatsDuration(3_661_000)).toBe('61m 1s')
  })
})

describe('_formatStatsRatio', () => {
  it('returns "–" for null / undefined / NaN', () => {
    expect(_formatStatsRatio(null)).toBe('–')
    expect(_formatStatsRatio(undefined)).toBe('–')
    expect(_formatStatsRatio(NaN)).toBe('–')
  })
  it('keeps 2 decimals for sub-1 ratios', () => {
    expect(_formatStatsRatio(0.42)).toBe('0.42')
    expect(_formatStatsRatio(0)).toBe('0.00')
  })
  it('keeps 1 decimal for ≥ 1 ratios', () => {
    expect(_formatStatsRatio(1)).toBe('1.0')
    expect(_formatStatsRatio(3.567)).toBe('3.6')
  })
})

describe('_formatStatsPercent', () => {
  it('returns "–" for null / undefined / NaN', () => {
    expect(_formatStatsPercent(null)).toBe('–')
    expect(_formatStatsPercent(NaN)).toBe('–')
  })
  it('multiplies by 100 and rounds', () => {
    expect(_formatStatsPercent(0.5)).toBe('50%')
    expect(_formatStatsPercent(0.123)).toBe('12%')
    expect(_formatStatsPercent(1)).toBe('100%')
    expect(_formatStatsPercent(0)).toBe('0%')
  })
})


/* ============================================================ render */

describe('_renderDiscoverV2Stats', () => {
  beforeEach(() => {
    document.body.innerHTML = `<div id="disc-v2-stats-block"></div>`
  })

  it('shows a "run a scan first" message for null stats', () => {
    _renderDiscoverV2Stats(null)
    expect(document.body.textContent).toContain('No stats yet')
  })

  it('renders the headline count row', () => {
    _renderDiscoverV2Stats({
      total_scans: 17, saved_count: 8, dismissed_count: 2, snoozed_count: 1,
      downloaded_count: 4, followed_count: 6,
      blocked_artist_count: 0, blocked_label_count: 0,
      avg_duration_ms: 4500, saves_per_scan: 0.47, novelty_share: {},
      top_labels: [], top_artists: [],
    })
    const text = document.body.textContent
    expect(text).toContain('17 scans')
    expect(text).toContain('8 saved')
    expect(text).toContain('6 followed labels')
    expect(text).toContain('avg scan: 4.5s')
    expect(text).toContain('saves per scan: 0.47')
  })

  it('includes blocked count only when non-zero', () => {
    _renderDiscoverV2Stats({
      total_scans: 5, saved_count: 0, dismissed_count: 0, snoozed_count: 0,
      downloaded_count: 0, followed_count: 0,
      blocked_artist_count: 2, blocked_label_count: 1,
      avg_duration_ms: null, saves_per_scan: null, novelty_share: {},
      top_labels: [], top_artists: [],
    })
    expect(document.body.textContent).toContain('3 blocked')
  })

  it('omits novelty mix when novelty_share is empty', () => {
    _renderDiscoverV2Stats({
      total_scans: 1, saved_count: 0, dismissed_count: 0, snoozed_count: 0,
      downloaded_count: 0, followed_count: 0,
      blocked_artist_count: 0, blocked_label_count: 0,
      avg_duration_ms: null, saves_per_scan: null, novelty_share: {},
      top_labels: [], top_artists: [],
    })
    expect(document.body.textContent).not.toContain('novelty mix')
  })

  it('renders novelty share with percentages', () => {
    _renderDiscoverV2Stats({
      total_scans: 5, saved_count: 0, dismissed_count: 0, snoozed_count: 0,
      downloaded_count: 0, followed_count: 0,
      blocked_artist_count: 0, blocked_label_count: 0,
      avg_duration_ms: null, saves_per_scan: null,
      novelty_share: {style: 0.5, label: 0.3, artist: 0.2},
      top_labels: [], top_artists: [],
    })
    expect(document.body.textContent).toContain('artist 20%')
    expect(document.body.textContent).toContain('label 30%')
    expect(document.body.textContent).toContain('style 50%')
  })

  it('caps top_labels and top_artists at 5', () => {
    const labels = Array.from({length: 8}, (_, i) => ({name: `L${i}`, count: 10 - i}))
    _renderDiscoverV2Stats({
      total_scans: 1, saved_count: 0, dismissed_count: 0, snoozed_count: 0,
      downloaded_count: 0, followed_count: 0,
      blocked_artist_count: 0, blocked_label_count: 0,
      avg_duration_ms: null, saves_per_scan: null, novelty_share: {},
      top_labels: labels, top_artists: [],
    })
    const txt = document.body.textContent
    expect(txt).toContain('L0')
    expect(txt).toContain('L4')
    expect(txt).not.toContain('L5')  // 6th label not rendered
  })

  it('escapes XSS in label + artist names', () => {
    _renderDiscoverV2Stats({
      total_scans: 1, saved_count: 0, dismissed_count: 0, snoozed_count: 0,
      downloaded_count: 0, followed_count: 0,
      blocked_artist_count: 0, blocked_label_count: 0,
      avg_duration_ms: null, saves_per_scan: null, novelty_share: {},
      top_labels: [{name: '<img src=x>', count: 9}],
      top_artists: [{name: '<svg onload=1>', count: 7}],
    })
    expect(document.body.innerHTML).not.toContain('<img src=x>')
    expect(document.body.innerHTML).not.toContain('<svg onload=1>')
    expect(document.body.textContent).toContain('<img src=x>')
  })
})
