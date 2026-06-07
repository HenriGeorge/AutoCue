/**
 * Tests for the Discover v2 Settings → Labels polish (T-030) —
 * _relativeTime + _renderSuggestedLabels follow-flow + followed-list
 * empty state copy. Mirrors docs/index.html — keep in sync.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

function _relativeTime(iso, nowMs) {
  if (!iso) return 'never'
  const then = Date.parse(iso)
  if (!Number.isFinite(then)) return 'never'
  const now = nowMs || Date.now()
  const diffSec = Math.max(0, Math.floor((now - then) / 1000))
  if (diffSec < 60) return 'just now'
  const m = Math.floor(diffSec / 60)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d}d ago`
  const months = Math.floor(d / 30)
  if (months < 12) return `${months}mo ago`
  const years = Math.floor(d / 365)
  return `${years}y ago`
}


let DiscoverV2

function _renderSuggestedLabels(suggestions) {
  const results = document.getElementById('disc-v2-label-suggest-results')
  if (!results) return
  if (!suggestions || !suggestions.length) {
    results.innerHTML = '<em>No suggestions — your library has no Discogs label metadata yet.</em>'
    return
  }
  results.innerHTML = ''
  const followed = new Set((DiscoverV2.state.followedLabels || []).map(l => l.label_id))
  suggestions.forEach(s => {
    const row = document.createElement('div')
    const trackInfo = s.track_count != null ? ` <span>(${s.track_count} tracks)</span>` : ''
    row.innerHTML = `<span>${_esc(s.name)}${trackInfo}</span>`
    const btn = document.createElement('button')
    btn.setAttribute('data-suggest-label-id', String(s.id))
    if (followed.has(s.id)) {
      btn.disabled = true
      btn.textContent = '✓ Following'
    } else {
      btn.textContent = 'Follow'
      btn.addEventListener('click', async () => {
        btn.disabled = true
        btn.textContent = '…'
        try {
          await DiscoverV2.followLabel(s.id, s.name)
          btn.textContent = '✓ Following'
        } catch (e) {
          btn.disabled = false
          btn.textContent = 'Follow'
        }
      })
    }
    row.appendChild(btn)
    results.appendChild(row)
  })
}


/* ============================================================ _relativeTime */

describe('_relativeTime', () => {
  const NOW = Date.UTC(2026, 5, 7, 12, 0, 0)  // 2026-06-07T12:00:00Z

  it('returns "never" for null/undefined/empty', () => {
    expect(_relativeTime(null, NOW)).toBe('never')
    expect(_relativeTime(undefined, NOW)).toBe('never')
    expect(_relativeTime('', NOW)).toBe('never')
  })

  it('returns "never" for unparseable strings', () => {
    expect(_relativeTime('not-a-date', NOW)).toBe('never')
  })

  it('returns "just now" for sub-minute deltas', () => {
    const iso = new Date(NOW - 30_000).toISOString()
    expect(_relativeTime(iso, NOW)).toBe('just now')
  })

  it('formats minute / hour / day deltas', () => {
    expect(_relativeTime(new Date(NOW - 5 * 60_000).toISOString(),       NOW)).toBe('5m ago')
    expect(_relativeTime(new Date(NOW - 3 * 60 * 60_000).toISOString(),  NOW)).toBe('3h ago')
    expect(_relativeTime(new Date(NOW - 4 * 24 * 60 * 60_000).toISOString(), NOW)).toBe('4d ago')
  })

  it('formats month + year deltas', () => {
    expect(_relativeTime(new Date(NOW - 45 * 24 * 60 * 60_000).toISOString(),  NOW)).toBe('1mo ago')
    expect(_relativeTime(new Date(NOW - 400 * 24 * 60 * 60_000).toISOString(), NOW)).toBe('1y ago')
  })

  it('clamps negative deltas to "just now" (future timestamps from clock skew)', () => {
    const iso = new Date(NOW + 60_000).toISOString()
    expect(_relativeTime(iso, NOW)).toBe('just now')
  })
})


/* ============================================================ Suggested labels */

describe('_renderSuggestedLabels', () => {
  beforeEach(() => {
    document.body.innerHTML = `<div id="disc-v2-label-suggest-results"></div>`
    DiscoverV2 = {
      state: {followedLabels: []},
      followLabel: vi.fn(async () => {}),
    }
  })

  it('renders an empty-state message when there are no suggestions', () => {
    _renderSuggestedLabels([])
    expect(document.body.textContent).toContain('No suggestions')
  })

  it('renders a row + Follow button per suggestion', () => {
    _renderSuggestedLabels([
      {id: 1, name: 'Stones Throw', track_count: 14},
      {id: 2, name: 'Hyperdub',     track_count: 7},
    ])
    expect(document.body.textContent).toContain('Stones Throw')
    expect(document.body.textContent).toContain('14 tracks')
    expect(document.body.textContent).toContain('Hyperdub')
    expect(document.querySelectorAll('button').length).toBe(2)
  })

  it('disables the Follow button for labels already in followedLabels', () => {
    DiscoverV2.state.followedLabels = [{label_id: 1, name: 'Stones Throw'}]
    _renderSuggestedLabels([
      {id: 1, name: 'Stones Throw'},
      {id: 2, name: 'Hyperdub'},
    ])
    const buttons = document.querySelectorAll('button')
    expect(buttons[0].disabled).toBe(true)
    expect(buttons[0].textContent).toBe('✓ Following')
    expect(buttons[1].disabled).toBe(false)
  })

  it('Follow click calls DiscoverV2.followLabel with label id + name', async () => {
    _renderSuggestedLabels([{id: 42, name: 'Test Label'}])
    document.querySelector('button').click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.followLabel).toHaveBeenCalledWith(42, 'Test Label')
  })

  it('Follow button shows ✓ Following on success', async () => {
    _renderSuggestedLabels([{id: 42, name: 'Test Label'}])
    const btn = document.querySelector('button')
    btn.click()
    await new Promise(r => setTimeout(r, 0))
    expect(btn.textContent).toBe('✓ Following')
    expect(btn.disabled).toBe(true)
  })

  it('Follow button re-enables on failure', async () => {
    DiscoverV2.followLabel = vi.fn(async () => { throw new Error('network') })
    _renderSuggestedLabels([{id: 42, name: 'Test Label'}])
    const btn = document.querySelector('button')
    btn.click()
    await new Promise(r => setTimeout(r, 0))
    expect(btn.disabled).toBe(false)
    expect(btn.textContent).toBe('Follow')
  })

  it('escapes XSS in label names', () => {
    _renderSuggestedLabels([{id: 1, name: '<img src=x onerror=alert(1)>'}])
    expect(document.body.innerHTML).not.toContain('<img src=x')
    expect(document.body.textContent).toContain('<img src=x onerror=alert(1)>')
  })
})
