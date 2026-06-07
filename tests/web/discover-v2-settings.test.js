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

async function _followByName(name) {
  if (!name) return false
  try {
    const hits = await DiscoverV2.searchLabels(name)
    if (!hits || !hits.length) return false
    const top = hits[0]
    await DiscoverV2.followLabel(top.id, top.name || name)
    return true
  } catch (_) {
    return false
  }
}

function _renderSuggestedLabels(suggestions) {
  const results = document.getElementById('disc-v2-label-suggest-results')
  if (!results) return
  if (!suggestions || !suggestions.length) {
    results.innerHTML = '<em>No suggestions — your library has no Discogs label metadata yet.</em>'
    return
  }
  results.innerHTML = ''
  const followedNames = new Set(
    (DiscoverV2.state.followedLabels || []).map(l => (l.name || '').toLowerCase())
  )
  suggestions.forEach(s => {
    const row = document.createElement('div')
    const weight = (s.weight != null) ? ` <span>(score ${_esc(String(s.weight))})</span>` : ''
    row.innerHTML = `<span>${_esc(s.name)}${weight}</span>`
    const btn = document.createElement('button')
    if (followedNames.has((s.name || '').toLowerCase())) {
      btn.disabled = true
      btn.textContent = '✓ Following'
    } else {
      btn.textContent = 'Follow'
      btn.addEventListener('click', async () => {
        btn.disabled = true
        btn.textContent = '…'
        const followed = await _followByName(s.name)
        if (followed) {
          btn.textContent = '✓ Following'
        } else {
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
      // The suggested endpoint returns name+weight only; the UI resolves the
      // Discogs label_id via /labels/search at follow time.
      searchLabels: vi.fn(async (q) => [{id: 999, name: q}]),
      followLabel: vi.fn(async () => {}),
    }
  })

  it('renders an empty-state message when there are no suggestions', () => {
    _renderSuggestedLabels([])
    expect(document.body.textContent).toContain('No suggestions')
  })

  it('renders a row + Follow button per suggestion using {name, weight} shape', () => {
    _renderSuggestedLabels([
      {name: 'Stones Throw', weight: 14.5},
      {name: 'Hyperdub',     weight: 7.2},
    ])
    expect(document.body.textContent).toContain('Stones Throw')
    expect(document.body.textContent).toContain('14.5')
    expect(document.body.textContent).toContain('Hyperdub')
    expect(document.querySelectorAll('button').length).toBe(2)
  })

  it('disables Follow for labels whose name is already followed (case-insensitive)', () => {
    DiscoverV2.state.followedLabels = [{label_id: 1, name: 'Stones Throw'}]
    _renderSuggestedLabels([
      {name: 'stones throw'},  // different case — still matched
      {name: 'Hyperdub'},
    ])
    const buttons = document.querySelectorAll('button')
    expect(buttons[0].disabled).toBe(true)
    expect(buttons[0].textContent).toBe('✓ Following')
    expect(buttons[1].disabled).toBe(false)
  })

  it('Follow click resolves label_id via searchLabels then follows', async () => {
    _renderSuggestedLabels([{name: 'Test Label'}])
    document.querySelector('button').click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.searchLabels).toHaveBeenCalledWith('Test Label')
    expect(DiscoverV2.followLabel).toHaveBeenCalledWith(999, 'Test Label')
  })

  it('Follow button shows ✓ Following on success', async () => {
    _renderSuggestedLabels([{name: 'Test Label'}])
    const btn = document.querySelector('button')
    btn.click()
    await new Promise(r => setTimeout(r, 0))
    expect(btn.textContent).toBe('✓ Following')
    expect(btn.disabled).toBe(true)
  })

  it('Follow button re-enables when searchLabels returns no hits', async () => {
    DiscoverV2.searchLabels = vi.fn(async () => [])
    _renderSuggestedLabels([{name: 'Obscure Label'}])
    const btn = document.querySelector('button')
    btn.click()
    await new Promise(r => setTimeout(r, 0))
    expect(btn.disabled).toBe(false)
    expect(btn.textContent).toBe('Follow')
    expect(DiscoverV2.followLabel).not.toHaveBeenCalled()
  })

  it('Follow button re-enables when followLabel throws', async () => {
    DiscoverV2.followLabel = vi.fn(async () => { throw new Error('network') })
    _renderSuggestedLabels([{name: 'Test Label'}])
    const btn = document.querySelector('button')
    btn.click()
    await new Promise(r => setTimeout(r, 0))
    expect(btn.disabled).toBe(false)
    expect(btn.textContent).toBe('Follow')
  })

  it('escapes XSS in label names', () => {
    _renderSuggestedLabels([{name: '<img src=x onerror=alert(1)>'}])
    expect(document.body.innerHTML).not.toContain('<img src=x')
    expect(document.body.textContent).toContain('<img src=x onerror=alert(1)>')
  })
})
