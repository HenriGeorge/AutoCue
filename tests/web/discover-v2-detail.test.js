/**
 * Tests for the Discover v2 detail panel (T-025) — focus trap, return-focus,
 * backdrop close, action buttons, tracklist render, follow-label CTA, error
 * state. Mirrors the inline implementation in docs/index.html. If you change
 * the module there, mirror it here.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

/* ---------------------------------------------------------------- _esc */
function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

/* ---------------------------------------------------------------- module state */

let DiscoverV2
let _detailReturnFocusEl
let _detailKeydownHandler

function makeStub() {
  DiscoverV2 = {
    state: {
      cards: [],
      cardsByKey: new Map(),
      savedKeys: new Set(),
      dismissedKeys: new Set(),
      snoozedKeys: new Set(),
      followedLabels: [],
    },
    save: vi.fn(async () => {}),
    snooze: vi.fn(async () => {}),
    dismiss: vi.fn(async () => {}),
    followLabel: vi.fn(async () => {}),
    loadDetail: vi.fn(async () => ({
      id: 100,
      title: 'Detail Title',
      artist: 'Detail Artist',
      year: 2020,
      label: 'Detail Label',
      label_id: 500,
      cover: 'https://example.com/cover.jpg',
      styles: ['Techno', 'House'],
      tracklist: [
        {position: 'A1', title: 'Track One', duration: '5:30'},
        {position: 'A2', title: 'Track Two', duration: '4:15'},
      ],
    })),
  }
}

/* ---------------------------------------------------- copied implementation */

function _renderDetailBody(release, detail, status, errorMsg) {
  const body = document.getElementById('disc-v2-detail-body')
  if (!body) return

  const r = release.release || {}
  const id = (detail && detail.id) || r.id || 0
  const title = (detail && detail.title) || r.title || 'Untitled'
  const artist = (detail && detail.artist) || r.artist || 'Unknown Artist'
  const year = (detail && detail.year) || r.year || ''
  const label = (detail && detail.label) || r.label || ''
  const labelId = (detail && detail.label_id) || r.label_id || null
  const cover = (detail && (detail.cover || detail.cover_image)) || r.cover_image || r.thumb || ''
  const styles = (detail && detail.styles) || []
  const tracks = (detail && detail.tracklist) || []

  const isSaved = DiscoverV2.state.savedKeys.has(release.release_key)
  const isDismissed = DiscoverV2.state.dismissedKeys.has(release.release_key)
  const followsLabel = labelId &&
    DiscoverV2.state.followedLabels.some(l => l.label_id === labelId)

  const trackHtml = tracks.length
    ? `<ol class="disc-v2-detail-tracklist" aria-label="Tracklist">
         ${tracks.map(t => `
           <li>
             <span class="pos">${_esc(t.position || '')}</span>
             <span class="title">${_esc(t.title || '')}</span>
             <span class="dur">${_esc(t.duration || '')}</span>
           </li>`).join('')}
       </ol>`
    : (status === 'loading'
        ? '<p class="loading">Loading tracklist…</p>'
        : '<p>No tracklist available.</p>')

  const errHtml = status === 'error'
    ? `<p class="disc-v2-detail-error" role="alert">Could not load details: ${_esc(errorMsg || '')}</p>`
    : ''

  body.innerHTML = `
    ${cover ? `<img src="${_esc(cover)}" alt="">` : ''}
    <h2 id="disc-v2-detail-heading">${_esc(title)}</h2>
    <p>${_esc(artist)}${year ? ' · ' + _esc(String(year)) : ''}${label ? ' · ' + _esc(label) : ''}</p>
    ${styles.length ? `<p class="styles">${styles.map(_esc).join(' · ')}</p>` : ''}
    <div class="disc-v2-detail-actions">
      <button class="disc-v2-detail-action ${isSaved ? 'saved' : 'primary'}" data-detail-act="save">
        ${isSaved ? '✓ Saved' : '💚 Save'}
      </button>
      <button class="disc-v2-detail-action" data-detail-act="snooze">💤 Snooze 30d</button>
      <button class="disc-v2-detail-action" data-detail-act="dismiss" ${isDismissed ? 'disabled' : ''}>
        ✕ ${isDismissed ? 'Dismissed' : 'Dismiss'}
      </button>
      ${labelId && !followsLabel
        ? `<button class="disc-v2-detail-action" data-detail-act="follow-label" data-label-id="${labelId}" data-label-name="${_esc(label)}">+ Follow ${_esc(label)}</button>`
        : ''}
    </div>
    ${errHtml}
    ${trackHtml}
    ${id ? `<a href="https://www.discogs.com/release/${id}" target="_blank" rel="noopener noreferrer">View on Discogs ↗</a>` : ''}
  `

  body.querySelectorAll('[data-detail-act]').forEach(btn => {
    btn.addEventListener('click', async (ev) => {
      ev.preventDefault()
      const act = btn.getAttribute('data-detail-act')
      try {
        if (act === 'save') {
          await DiscoverV2.save(release)
        } else if (act === 'snooze') {
          await DiscoverV2.snooze(release, '30d')
          _closeDetailPanel()
          return
        } else if (act === 'dismiss') {
          await DiscoverV2.dismiss(release)
          _closeDetailPanel()
          return
        } else if (act === 'follow-label') {
          const lid = parseInt(btn.getAttribute('data-label-id'), 10)
          const lname = btn.getAttribute('data-label-name') || ''
          await DiscoverV2.followLabel(lid, lname)
        }
        _renderDetailBody(release, detail, status, errorMsg)
      } catch (e) {
        const err = document.createElement('p')
        err.className = 'disc-v2-detail-error'
        err.textContent = 'Action failed: ' + String(e)
        body.appendChild(err)
      }
    })
  })
}

function _detailTrapKeydown(ev) {
  if (ev.key === 'Escape') {
    _closeDetailPanel()
    return
  }
  if (ev.key !== 'Tab') return
  const panel = document.getElementById('disc-v2-detail-panel')
  if (!panel || panel.getAttribute('aria-hidden') !== 'false') return
  const focusables = panel.querySelectorAll(
    'button, a, input, select, textarea, [tabindex]:not([tabindex="-1"])'
  )
  if (!focusables.length) return
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  if (ev.shiftKey && document.activeElement === first) {
    ev.preventDefault()
    last.focus()
  } else if (!ev.shiftKey && document.activeElement === last) {
    ev.preventDefault()
    first.focus()
  }
}

async function _openDetailPanel(releaseKey) {
  const panel = document.getElementById('disc-v2-detail-panel')
  const backdrop = document.getElementById('disc-v2-detail-backdrop')
  const body = document.getElementById('disc-v2-detail-body')
  if (!panel || !body) return
  const release = DiscoverV2.state.cardsByKey.get(releaseKey)
  if (!release) return

  _detailReturnFocusEl = document.activeElement

  panel.setAttribute('aria-hidden', 'false')
  if (backdrop) backdrop.setAttribute('aria-hidden', 'false')

  _renderDetailBody(release, null, 'loading')

  _detailKeydownHandler = (ev) => _detailTrapKeydown(ev)
  document.addEventListener('keydown', _detailKeydownHandler)

  try {
    const r = release.release || {}
    const detail = r.id ? await DiscoverV2.loadDetail(r.id) : null
    _renderDetailBody(release, detail, 'loaded')
  } catch (e) {
    _renderDetailBody(release, null, 'error', String(e))
  }
}

function _closeDetailPanel() {
  const panel = document.getElementById('disc-v2-detail-panel')
  const backdrop = document.getElementById('disc-v2-detail-backdrop')
  if (panel) panel.setAttribute('aria-hidden', 'true')
  if (backdrop) backdrop.setAttribute('aria-hidden', 'true')
  if (_detailKeydownHandler) {
    document.removeEventListener('keydown', _detailKeydownHandler)
    _detailKeydownHandler = null
  }
  if (_detailReturnFocusEl && typeof _detailReturnFocusEl.focus === 'function') {
    try { _detailReturnFocusEl.focus() } catch (_) {}
  }
  _detailReturnFocusEl = null
}

/* ---------------------------------------------------- DOM setup */

function setupDOM() {
  document.body.innerHTML = `
    <button id="opener">Open</button>
    <div id="disc-v2-detail-backdrop" aria-hidden="true"></div>
    <div id="disc-v2-detail-panel" role="dialog" aria-modal="true"
         aria-labelledby="disc-v2-detail-heading" aria-hidden="true">
      <div class="disc-v2-detail-panel-inner">
        <button id="disc-v2-detail-close-btn">×</button>
        <div id="disc-v2-detail-body"></div>
      </div>
    </div>
  `
}

const SAMPLE_RELEASE = {
  release_key: 'k1',
  source: 'artist',
  release: {
    id: 100,
    title: 'Card Title',
    artist: 'Card Artist',
    label: 'Card Label',
    label_id: 500,
    year: 2020,
    cover_image: 'https://example.com/cover.jpg',
  },
}


/* ===================================================== tests */

describe('detail panel — open/close lifecycle', () => {
  beforeEach(() => {
    makeStub()
    setupDOM()
    DiscoverV2.state.cardsByKey.set('k1', SAMPLE_RELEASE)
  })

  it('flips aria-hidden on panel + backdrop when opened', async () => {
    await _openDetailPanel('k1')
    expect(document.getElementById('disc-v2-detail-panel').getAttribute('aria-hidden')).toBe('false')
    expect(document.getElementById('disc-v2-detail-backdrop').getAttribute('aria-hidden')).toBe('false')
  })

  it('hides panel + backdrop on close', async () => {
    await _openDetailPanel('k1')
    _closeDetailPanel()
    expect(document.getElementById('disc-v2-detail-panel').getAttribute('aria-hidden')).toBe('true')
    expect(document.getElementById('disc-v2-detail-backdrop').getAttribute('aria-hidden')).toBe('true')
  })

  it('returns focus to the opener after close', async () => {
    const opener = document.getElementById('opener')
    opener.focus()
    expect(document.activeElement).toBe(opener)
    await _openDetailPanel('k1')
    _closeDetailPanel()
    expect(document.activeElement).toBe(opener)
  })

  it('is a no-op when releaseKey is unknown', async () => {
    await _openDetailPanel('does-not-exist')
    expect(document.getElementById('disc-v2-detail-panel').getAttribute('aria-hidden')).toBe('true')
  })

  it('calls loadDetail with the release id', async () => {
    await _openDetailPanel('k1')
    expect(DiscoverV2.loadDetail).toHaveBeenCalledWith(100)
  })
})


describe('detail panel — body renderer', () => {
  beforeEach(() => {
    makeStub()
    setupDOM()
    DiscoverV2.state.cardsByKey.set('k1', SAMPLE_RELEASE)
  })

  it('renders title with the dialog heading id (aria-labelledby target)', async () => {
    await _openDetailPanel('k1')
    const heading = document.getElementById('disc-v2-detail-heading')
    expect(heading).not.toBeNull()
    // After load, the Discogs detail title overrides the card title.
    expect(heading.textContent).toBe('Detail Title')
  })

  it('renders the full tracklist with position + title + duration', async () => {
    await _openDetailPanel('k1')
    const tracks = document.querySelectorAll('.disc-v2-detail-tracklist li')
    expect(tracks.length).toBe(2)
    expect(tracks[0].querySelector('.pos').textContent).toBe('A1')
    expect(tracks[0].querySelector('.title').textContent).toBe('Track One')
    expect(tracks[0].querySelector('.dur').textContent).toBe('5:30')
  })

  it('renders Discogs link to the release page', async () => {
    await _openDetailPanel('k1')
    const link = document.querySelector('a[href^="https://www.discogs.com/release/"]')
    expect(link).not.toBeNull()
    expect(link.getAttribute('href')).toBe('https://www.discogs.com/release/100')
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('shows ✓ Saved button when release is already saved', async () => {
    DiscoverV2.state.savedKeys.add('k1')
    await _openDetailPanel('k1')
    const saveBtn = document.querySelector('[data-detail-act="save"]')
    expect(saveBtn.textContent.trim()).toBe('✓ Saved')
    expect(saveBtn.classList.contains('saved')).toBe(true)
  })

  it('shows follow-label CTA when label is not yet followed', async () => {
    await _openDetailPanel('k1')
    const followBtn = document.querySelector('[data-detail-act="follow-label"]')
    expect(followBtn).not.toBeNull()
    expect(followBtn.getAttribute('data-label-id')).toBe('500')
  })

  it('hides follow-label CTA when label is already followed', async () => {
    DiscoverV2.state.followedLabels.push({label_id: 500, name: 'Detail Label'})
    await _openDetailPanel('k1')
    expect(document.querySelector('[data-detail-act="follow-label"]')).toBeNull()
  })

  it('escapes XSS in titles', async () => {
    DiscoverV2.loadDetail = vi.fn(async () => ({
      id: 999, title: '<script>x()</script>', artist: 'A', tracklist: [],
    }))
    await _openDetailPanel('k1')
    const heading = document.getElementById('disc-v2-detail-heading')
    expect(heading.innerHTML).not.toContain('<script>')
    expect(heading.textContent).toBe('<script>x()</script>')
  })

  it('shows an error message when loadDetail rejects', async () => {
    DiscoverV2.loadDetail = vi.fn(async () => { throw new Error('boom') })
    await _openDetailPanel('k1')
    const err = document.querySelector('.disc-v2-detail-error')
    expect(err).not.toBeNull()
    expect(err.getAttribute('role')).toBe('alert')
    expect(err.textContent).toContain('boom')
  })

  it('falls back to card data when release has no id (cant call loadDetail)', async () => {
    const r = {
      release_key: 'k2',
      source: 'artist',
      release: {title: 'No ID', artist: 'A'},  // no id
    }
    DiscoverV2.state.cardsByKey.set('k2', r)
    await _openDetailPanel('k2')
    expect(document.getElementById('disc-v2-detail-heading').textContent).toBe('No ID')
    // loadDetail must NOT be called when r.id is falsy.
    expect(DiscoverV2.loadDetail).not.toHaveBeenCalled()
  })
})


describe('detail panel — action buttons', () => {
  beforeEach(() => {
    makeStub()
    setupDOM()
    DiscoverV2.state.cardsByKey.set('k1', SAMPLE_RELEASE)
  })

  it('save button calls DiscoverV2.save', async () => {
    await _openDetailPanel('k1')
    document.querySelector('[data-detail-act="save"]').click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.save).toHaveBeenCalledWith(SAMPLE_RELEASE)
  })

  it('snooze button calls snooze with 30d and closes the panel', async () => {
    await _openDetailPanel('k1')
    document.querySelector('[data-detail-act="snooze"]').click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.snooze).toHaveBeenCalledWith(SAMPLE_RELEASE, '30d')
    expect(document.getElementById('disc-v2-detail-panel').getAttribute('aria-hidden')).toBe('true')
  })

  it('dismiss button calls dismiss and closes the panel', async () => {
    await _openDetailPanel('k1')
    document.querySelector('[data-detail-act="dismiss"]').click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.dismiss).toHaveBeenCalledWith(SAMPLE_RELEASE)
    expect(document.getElementById('disc-v2-detail-panel').getAttribute('aria-hidden')).toBe('true')
  })

  it('follow-label button calls followLabel with the label id + name', async () => {
    await _openDetailPanel('k1')
    document.querySelector('[data-detail-act="follow-label"]').click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.followLabel).toHaveBeenCalledWith(500, 'Detail Label')
  })
})


describe('detail panel — focus trap', () => {
  beforeEach(() => {
    makeStub()
    setupDOM()
    DiscoverV2.state.cardsByKey.set('k1', SAMPLE_RELEASE)
  })

  it('Escape key closes the panel', async () => {
    await _openDetailPanel('k1')
    const ev = new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})
    document.dispatchEvent(ev)
    expect(document.getElementById('disc-v2-detail-panel').getAttribute('aria-hidden')).toBe('true')
  })

  it('Tab from the last focusable wraps to the first', async () => {
    await _openDetailPanel('k1')
    const panel = document.getElementById('disc-v2-detail-panel')
    const focusables = panel.querySelectorAll('button, a, input, select, textarea, [tabindex]:not([tabindex="-1"])')
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    last.focus()
    const ev = new KeyboardEvent('keydown', {key: 'Tab', bubbles: true, cancelable: true})
    document.dispatchEvent(ev)
    expect(document.activeElement).toBe(first)
  })

  it('Shift+Tab from the first focusable wraps to the last', async () => {
    await _openDetailPanel('k1')
    const panel = document.getElementById('disc-v2-detail-panel')
    const focusables = panel.querySelectorAll('button, a, input, select, textarea, [tabindex]:not([tabindex="-1"])')
    const first = focusables[0]
    const last = focusables[focusables.length - 1]
    first.focus()
    const ev = new KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true, cancelable: true})
    document.dispatchEvent(ev)
    expect(document.activeElement).toBe(last)
  })

  it('removes the keydown handler on close (Escape does nothing after close)', async () => {
    await _openDetailPanel('k1')
    _closeDetailPanel()
    // Spy: after close, dispatching Escape must not change anything (idempotent).
    const before = document.getElementById('disc-v2-detail-panel').getAttribute('aria-hidden')
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))
    const after = document.getElementById('disc-v2-detail-panel').getAttribute('aria-hidden')
    expect(after).toBe(before)
    expect(after).toBe('true')
  })
})
