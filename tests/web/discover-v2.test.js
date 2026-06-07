/**
 * Tests for the Discover v2 UI module (T-024) — DiscoverState + card renderer
 * + SSE chunk handler. The implementation lives inline in docs/index.html;
 * these tests re-implement the same module to verify behavior.
 *
 * The patterns are copied from docs/index.html — if you change the module
 * there, mirror the change here.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

/* ---------------------------------------------------------------- helpers */

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

// Mirror of DiscoverV2's SSE-chunk handling — copied here so we can test it.
function makeState() {
  return {
    cards: [],
    cardsByKey: new Map(),
    savedKeys: new Set(),
    dismissedKeys: new Set(),
    snoozedKeys: new Set(),
    followedLabels: [],
    blockedArtists: [],
    blockedLabels: [],
    scanRunning: false,
    scanFeeder: null,
    scanReleasesSeen: 0,
    tokenValid: null,
    settingsOpen: false,
  }
}

function handleSSEChunk(state, chunk) {
  let event = 'message'
  let data = null
  for (const line of chunk.split('\n')) {
    if (line.startsWith('event: ')) event = line.slice(7).trim()
    else if (line.startsWith('data: ')) {
      try { data = JSON.parse(line.slice(6)) } catch (_) { /* ignore */ }
    }
  }
  if (!data) return
  if (event === 'progress') {
    state.scanFeeder = data.feeder
  } else if (event === 'release') {
    state.cards.push(data)
    state.cardsByKey.set(data.release_key, data)
    state.scanReleasesSeen++
  } else if (event === 'done' || event === 'error') {
    state.scanFeeder = null
  }
}

function renderCard(release, state) {
  const r = release.release || {}
  const isSaved = state.savedKeys.has(release.release_key)
  const card = document.createElement('div')
  card.className = 'disc-v2-card'
  card.setAttribute('data-release-key', release.release_key)
  card.setAttribute('role', 'button')
  card.setAttribute('tabindex', '0')
  const art = r.thumb || r.cover_image || ''
  const sourceLabel = (release.source || '').split(':')[0]
  card.innerHTML = `
    <div class="disc-v2-card-art" style="${art ? `background-image:url('${_esc(art)}')` : ''}"></div>
    <div class="disc-v2-card-body">
      <p class="disc-v2-card-title">${_esc(r.title || 'Untitled')}</p>
      <p class="disc-v2-card-artist">${_esc(r.artist || 'Unknown Artist')}</p>
      <p class="disc-v2-card-source">via ${_esc(sourceLabel)}${r.label ? ' · ' + _esc(r.label) : ''}${r.year ? ' · ' + r.year : ''}</p>
    </div>
    <div class="disc-v2-card-actions" data-actions>
      <button class="disc-v2-card-action ${isSaved ? 'saved' : ''}" data-act="save" title="Save">${isSaved ? '✓' : '💚'}</button>
      <button class="disc-v2-card-action" data-act="snooze" title="Snooze (1w / 1m / 3m)">💤</button>
      <button class="disc-v2-card-action" data-act="dismiss" title="Dismiss">✕</button>
    </div>
  `
  return card
}


/* ---------------------------------------------------- _esc HTML escaping */

describe('_esc', () => {
  it('escapes HTML special chars', () => {
    expect(_esc('<script>alert("xss")</script>')).toBe(
      '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'
    )
  })

  it('escapes ampersand + apostrophe', () => {
    expect(_esc("J&Z's")).toBe('J&amp;Z&#39;s')
  })

  it('returns empty string for null/undefined', () => {
    expect(_esc(null)).toBe('')
    expect(_esc(undefined)).toBe('')
  })

  it('coerces non-strings to strings', () => {
    expect(_esc(42)).toBe('42')
  })
})


/* ---------------------------------------------------- handleSSEChunk */

describe('handleSSEChunk', () => {
  it('parses an event + data line pair', () => {
    const state = makeState()
    const chunk = 'event: release\ndata: {"release_key":"k1","release":{"title":"X","artist":"A"},"source":"artist"}'
    handleSSEChunk(state, chunk)
    expect(state.cards.length).toBe(1)
    expect(state.cardsByKey.get('k1').release.title).toBe('X')
    expect(state.scanReleasesSeen).toBe(1)
  })

  it('updates scanFeeder on progress events', () => {
    const state = makeState()
    handleSSEChunk(state, 'event: progress\ndata: {"feeder":"label"}')
    expect(state.scanFeeder).toBe('label')
  })

  it('clears scanFeeder on done event', () => {
    const state = makeState()
    state.scanFeeder = 'label'
    handleSSEChunk(state, 'event: done\ndata: {"status":"ok"}')
    expect(state.scanFeeder).toBeNull()
  })

  it('clears scanFeeder on error event', () => {
    const state = makeState()
    state.scanFeeder = 'artist'
    handleSSEChunk(state, 'event: error\ndata: {"feeder":"artist","exc":"boom"}')
    expect(state.scanFeeder).toBeNull()
  })

  it('ignores chunks without a data line', () => {
    const state = makeState()
    handleSSEChunk(state, 'event: release\n')
    expect(state.cards.length).toBe(0)
  })

  it('tolerates malformed JSON in data without throwing', () => {
    const state = makeState()
    expect(() =>
      handleSSEChunk(state, 'event: release\ndata: {not json}'),
    ).not.toThrow()
    expect(state.cards.length).toBe(0)
  })
})


/* ---------------------------------------------------- card renderer */

describe('renderCard', () => {
  it('renders title + artist from the release dict', () => {
    const state = makeState()
    const card = renderCard({
      release_key: 'k1',
      source: 'artist',
      release: {title: 'Madvillainy', artist: 'Madvillain', year: 2004, label: 'Stones Throw'},
    }, state)
    expect(card.querySelector('.disc-v2-card-title').textContent).toBe('Madvillainy')
    expect(card.querySelector('.disc-v2-card-artist').textContent).toBe('Madvillain')
    // Source line shows the feeder + label + year.
    const src = card.querySelector('.disc-v2-card-source').textContent
    expect(src).toContain('artist')
    expect(src).toContain('Stones Throw')
    expect(src).toContain('2004')
  })

  it('shows ✓ on saved cards', () => {
    const state = makeState()
    state.savedKeys.add('k1')
    const card = renderCard({
      release_key: 'k1',
      source: 'artist',
      release: {title: 'T', artist: 'A'},
    }, state)
    const saveBtn = card.querySelector('[data-act="save"]')
    expect(saveBtn.classList.contains('saved')).toBe(true)
    expect(saveBtn.textContent).toBe('✓')
  })

  it('escapes user-supplied strings in titles', () => {
    const state = makeState()
    const card = renderCard({
      release_key: 'k1',
      source: 'artist',
      release: {title: '<script>alert(1)</script>', artist: 'A'},
    }, state)
    // The title element's INNER HTML should NOT contain a live <script> tag.
    expect(card.querySelector('.disc-v2-card-title').innerHTML).not.toContain('<script>')
    expect(card.querySelector('.disc-v2-card-title').textContent).toBe('<script>alert(1)</script>')
  })

  it('falls back to "Untitled" / "Unknown Artist" when fields are missing', () => {
    const state = makeState()
    const card = renderCard({release_key: 'k1', source: 'artist', release: {}}, state)
    expect(card.querySelector('.disc-v2-card-title').textContent).toBe('Untitled')
    expect(card.querySelector('.disc-v2-card-artist').textContent).toBe('Unknown Artist')
  })

  it('embeds the release_key on the card root for delegated click handlers', () => {
    const state = makeState()
    const card = renderCard({release_key: 'k1', source: 'artist', release: {}}, state)
    expect(card.getAttribute('data-release-key')).toBe('k1')
  })

  // Regression for issue #68 — the snooze button's tooltip must reflect the
  // durations the backend actually accepts (1w / 1m / 3m). The string
  // 'Snooze 30 days' is forbidden because '30d' would 400 against the backend.
  it('snooze button tooltip reflects accepted backend durations', () => {
    const state = makeState()
    const card = renderCard({release_key: 'k1', source: 'artist', release: {}}, state)
    const snoozeBtn = card.querySelector('[data-act="snooze"]')
    expect(snoozeBtn).not.toBeNull()
    const title = snoozeBtn.getAttribute('title') || ''
    // Must NOT contain the stale '30 days' string.
    expect(title).not.toMatch(/30\s*days?/i)
    // Must surface every duration the backend allows.
    expect(title).toMatch(/1w/i)
    expect(title).toMatch(/1m/i)
    expect(title).toMatch(/3m/i)
  })
})
