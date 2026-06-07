/**
 * Tests for the Discover v2 Settings → Blocked sub-panel (T-032) —
 * _renderDiscoverV2Blocked, unblock buttons, detail-panel Block actions.
 * Mirrors docs/index.html — keep in sync.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

let DiscoverV2

function _renderDiscoverV2Blocked() {
  const list = document.getElementById('disc-v2-blocked-list')
  if (!list) return
  const sa = DiscoverV2.state.blockedArtists || []
  const sl = DiscoverV2.state.blockedLabels || []
  if (!sa.length && !sl.length) {
    list.innerHTML = 'Nothing blocked. You can 🚫 block an artist or label from the release detail panel.'
    return
  }
  list.innerHTML = ''

  const _row = (kind, icon, name, id, unblockFn) => {
    const row = document.createElement('div')
    row.setAttribute('data-blocked-kind', kind)
    row.setAttribute('data-blocked-id', String(id))
    row.innerHTML = `<span>${icon} ${_esc(name || 'unknown')}</span>`
    const btn = document.createElement('button')
    btn.textContent = 'Unblock'
    btn.addEventListener('click', async () => {
      btn.disabled = true
      btn.textContent = '…'
      try {
        await unblockFn(id)
      } catch (_) {
        btn.disabled = false
        btn.textContent = 'Unblock'
      }
    })
    row.appendChild(btn)
    return row
  }

  if (sa.length) {
    const h = document.createElement('div')
    h.innerHTML = `<strong>Artists (${sa.length})</strong>`
    list.appendChild(h)
    sa.forEach(a => list.appendChild(_row('artist', '🎤', a.name, a.discogs_artist_id, DiscoverV2.unblockArtist)))
  }
  if (sl.length) {
    const h = document.createElement('div')
    h.innerHTML = `<strong>Labels (${sl.length})</strong>`
    list.appendChild(h)
    sl.forEach(l => list.appendChild(_row('label', '🏷', l.name, l.discogs_label_id, DiscoverV2.unblockLabel)))
  }
}


function setupDOM() {
  document.body.innerHTML = `<div id="disc-v2-blocked-list"></div>`
}

function makeStub() {
  DiscoverV2 = {
    state: {blockedArtists: [], blockedLabels: []},
    blockArtist:   vi.fn(async () => {}),
    unblockArtist: vi.fn(async () => {}),
    blockLabel:    vi.fn(async () => {}),
    unblockLabel:  vi.fn(async () => {}),
  }
}


/* ============================================================ render */

describe('_renderDiscoverV2Blocked', () => {
  beforeEach(() => { setupDOM(); makeStub() })

  it('shows the empty-state message when nothing is blocked', () => {
    _renderDiscoverV2Blocked()
    expect(document.body.textContent).toContain('Nothing blocked')
  })

  it('shows the count of blocked artists in the section header', () => {
    DiscoverV2.state.blockedArtists = [
      {discogs_artist_id: 1, name: 'Skrillex'},
      {discogs_artist_id: 2, name: 'Steve Aoki'},
    ]
    _renderDiscoverV2Blocked()
    expect(document.body.textContent).toContain('Artists (2)')
    expect(document.body.textContent).toContain('Skrillex')
    expect(document.body.textContent).toContain('Steve Aoki')
  })

  it('shows the count of blocked labels in a separate section', () => {
    DiscoverV2.state.blockedLabels = [
      {discogs_label_id: 100, name: 'Big Beat'},
    ]
    _renderDiscoverV2Blocked()
    expect(document.body.textContent).toContain('Labels (1)')
    expect(document.body.textContent).toContain('Big Beat')
  })

  it('renders both sections when both are populated', () => {
    DiscoverV2.state.blockedArtists = [{discogs_artist_id: 1, name: 'X'}]
    DiscoverV2.state.blockedLabels = [{discogs_label_id: 2, name: 'Y'}]
    _renderDiscoverV2Blocked()
    expect(document.body.textContent).toContain('Artists (1)')
    expect(document.body.textContent).toContain('Labels (1)')
    expect(document.querySelectorAll('button').length).toBe(2)
  })

  it('tags each row with a kind + id data attribute', () => {
    DiscoverV2.state.blockedArtists = [{discogs_artist_id: 42, name: 'Test'}]
    _renderDiscoverV2Blocked()
    const row = document.querySelector('[data-blocked-kind="artist"]')
    expect(row).not.toBeNull()
    expect(row.getAttribute('data-blocked-id')).toBe('42')
  })

  it('Unblock-artist click calls unblockArtist with the discogs id', async () => {
    DiscoverV2.state.blockedArtists = [{discogs_artist_id: 42, name: 'Test'}]
    _renderDiscoverV2Blocked()
    document.querySelector('button').click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.unblockArtist).toHaveBeenCalledWith(42)
  })

  it('Unblock-label click calls unblockLabel with the discogs id', async () => {
    DiscoverV2.state.blockedLabels = [{discogs_label_id: 200, name: 'Test'}]
    _renderDiscoverV2Blocked()
    document.querySelector('button').click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.unblockLabel).toHaveBeenCalledWith(200)
  })

  it('Unblock button re-enables on failure', async () => {
    DiscoverV2.unblockArtist = vi.fn(async () => { throw new Error('net') })
    DiscoverV2.state.blockedArtists = [{discogs_artist_id: 1, name: 'X'}]
    _renderDiscoverV2Blocked()
    const btn = document.querySelector('button')
    btn.click()
    await new Promise(r => setTimeout(r, 0))
    expect(btn.disabled).toBe(false)
    expect(btn.textContent).toBe('Unblock')
  })

  it('escapes XSS in artist + label names', () => {
    DiscoverV2.state.blockedArtists = [{discogs_artist_id: 1, name: '<img src=x>'}]
    DiscoverV2.state.blockedLabels = [{discogs_label_id: 2, name: '<svg onload=1>'}]
    _renderDiscoverV2Blocked()
    expect(document.body.innerHTML).not.toContain('<img src=x>')
    expect(document.body.innerHTML).not.toContain('<svg onload=1>')
    expect(document.body.textContent).toContain('<img src=x>')
    expect(document.body.textContent).toContain('<svg onload=1>')
  })
})


/* ============================================================ detail-panel button visibility */

describe('block-buttons in the detail-panel actions row', () => {
  // Mirror the conditional rendering rules from _renderDetailBody so the
  // contract is verifiable without instantiating the full panel.
  function actionsRow({state, release, detail}) {
    const r = release.release || {}
    const labelId = (detail && detail.label_id) || r.label_id || null
    const artistId = (detail && detail.artist_id) || r.artist_id || 0
    const artistBlocked = artistId && (state.blockedArtists || []).some(b => b.discogs_artist_id === artistId)
    const labelBlocked = labelId && (state.blockedLabels || []).some(b => b.discogs_label_id === labelId)
    return {
      showBlockArtist: !!(artistId && !artistBlocked),
      showBlockLabel: !!(labelId && !labelBlocked),
    }
  }

  it('shows both block buttons when neither is blocked', () => {
    const out = actionsRow({
      state: {blockedArtists: [], blockedLabels: []},
      release: {release: {artist_id: 1, label_id: 2}},
      detail: null,
    })
    expect(out.showBlockArtist).toBe(true)
    expect(out.showBlockLabel).toBe(true)
  })

  it('hides the block-artist button when the artist is already blocked', () => {
    const out = actionsRow({
      state: {blockedArtists: [{discogs_artist_id: 1}], blockedLabels: []},
      release: {release: {artist_id: 1, label_id: 2}},
      detail: null,
    })
    expect(out.showBlockArtist).toBe(false)
    expect(out.showBlockLabel).toBe(true)
  })

  it('hides the block-label button when the label is already blocked', () => {
    const out = actionsRow({
      state: {blockedArtists: [], blockedLabels: [{discogs_label_id: 2}]},
      release: {release: {artist_id: 1, label_id: 2}},
      detail: null,
    })
    expect(out.showBlockArtist).toBe(true)
    expect(out.showBlockLabel).toBe(false)
  })

  it('hides both buttons when ids are missing', () => {
    const out = actionsRow({
      state: {blockedArtists: [], blockedLabels: []},
      release: {release: {}},
      detail: null,
    })
    expect(out.showBlockArtist).toBe(false)
    expect(out.showBlockLabel).toBe(false)
  })

  it('prefers the Discogs-detail ids over the card-level ids', () => {
    // The card may carry stale ids from a partial fetch; the Discogs detail
    // overrides them once it lands.
    const out = actionsRow({
      state: {blockedArtists: [{discogs_artist_id: 99}], blockedLabels: []},
      release: {release: {artist_id: 1, label_id: 2}},
      detail: {artist_id: 99, label_id: 2},
    })
    // Detail-level artist_id 99 is blocked → button hidden, even though
    // card-level artist_id 1 is not blocked.
    expect(out.showBlockArtist).toBe(false)
  })
})
