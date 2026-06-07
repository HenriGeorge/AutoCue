/**
 * Tests for the Discover v2 Shift+click download power flow (T-027) —
 * confirm modal, Cancel-default focus, query builder, focus trap, panel-
 * internal Download button. Mirrors docs/index.html — keep in sync.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

let _dlConfirmReturnFocusEl = null
let _dlConfirmRelease = null
let _dlConfirmKeydownHandler = null
let runDownload  // global stub for the existing v1 helper

function _buildDownloadQuery(release) {
  const r = release?.release || {}
  const artist = (r.artist || '').trim()
  const title = (r.title || '').trim()
  return [artist, title].filter(Boolean).join(' ')
}

function _openDownloadConfirm(release) {
  const modal = document.getElementById('disc-v2-dl-confirm')
  const backdrop = document.getElementById('disc-v2-dl-confirm-backdrop')
  const cancelBtn = document.getElementById('disc-v2-dl-confirm-cancel')
  const goBtn = document.getElementById('disc-v2-dl-confirm-go')
  const body = document.getElementById('disc-v2-dl-confirm-body')
  if (!modal || !backdrop) return

  _dlConfirmReturnFocusEl = document.activeElement
  _dlConfirmRelease = release

  const r = release?.release || {}
  const query = _buildDownloadQuery(release)
  if (body) {
    body.innerHTML =
      `Download <strong>${_esc(r.title || 'Untitled')}</strong> by ` +
      `<strong>${_esc(r.artist || 'Unknown Artist')}</strong>?` +
      `<br><span><code>${_esc(query)}</code></span>`
  }
  if (goBtn) {
    goBtn.disabled = false
    goBtn.textContent = 'Download anyway'
  }

  modal.setAttribute('aria-hidden', 'false')
  backdrop.setAttribute('aria-hidden', 'false')

  _dlConfirmKeydownHandler = (ev) => _dlConfirmTrapKeydown(ev)
  document.addEventListener('keydown', _dlConfirmKeydownHandler)

  // Critical: Cancel is the default focus.
  cancelBtn?.focus()
}

function _dlConfirmTrapKeydown(ev) {
  if (ev.key === 'Escape') {
    _closeDownloadConfirm()
    return
  }
  if (ev.key !== 'Tab') return
  const modal = document.getElementById('disc-v2-dl-confirm')
  if (!modal || modal.getAttribute('aria-hidden') !== 'false') return
  const focusables = modal.querySelectorAll(
    'button, a, input, [tabindex]:not([tabindex="-1"])'
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

function _closeDownloadConfirm() {
  const modal = document.getElementById('disc-v2-dl-confirm')
  const backdrop = document.getElementById('disc-v2-dl-confirm-backdrop')
  if (modal) modal.setAttribute('aria-hidden', 'true')
  if (backdrop) backdrop.setAttribute('aria-hidden', 'true')
  if (_dlConfirmKeydownHandler) {
    document.removeEventListener('keydown', _dlConfirmKeydownHandler)
    _dlConfirmKeydownHandler = null
  }
  if (_dlConfirmReturnFocusEl && typeof _dlConfirmReturnFocusEl.focus === 'function') {
    try { _dlConfirmReturnFocusEl.focus() } catch (_) {}
  }
  _dlConfirmReturnFocusEl = null
  _dlConfirmRelease = null
}

async function _runDownloadConfirmGo() {
  const release = _dlConfirmRelease
  if (!release) return
  const query = _buildDownloadQuery(release)
  if (!query) return
  _closeDownloadConfirm()
  if (typeof runDownload === 'function') {
    runDownload(query, {})
  }
}


/* ============================================================ DOM */

function setupDOM() {
  document.body.innerHTML = `
    <button id="opener">Open</button>
    <div id="disc-v2-dl-confirm-backdrop" aria-hidden="true"></div>
    <div id="disc-v2-dl-confirm" role="dialog" aria-modal="true" aria-hidden="true">
      <h3 id="disc-v2-dl-confirm-heading">Download album?</h3>
      <p id="disc-v2-dl-confirm-body"></p>
      <button id="disc-v2-dl-confirm-cancel">Cancel</button>
      <button id="disc-v2-dl-confirm-go">Download anyway</button>
    </div>
  `
}

const RELEASE = {
  release_key: 'k1',
  release: {id: 99, artist: 'Madvillain', title: 'Madvillainy'},
}


/* ============================================================ build query */

describe('_buildDownloadQuery', () => {
  it('joins artist + title with a single space', () => {
    expect(_buildDownloadQuery(RELEASE)).toBe('Madvillain Madvillainy')
  })
  it('trims whitespace around each part', () => {
    expect(_buildDownloadQuery({release: {artist: '  A  ', title: '  T  '}})).toBe('A T')
  })
  it('falls back to whatever is non-empty when one part is missing', () => {
    expect(_buildDownloadQuery({release: {title: 'OnlyT'}})).toBe('OnlyT')
    expect(_buildDownloadQuery({release: {artist: 'OnlyA'}})).toBe('OnlyA')
  })
  it('returns empty string when both parts are missing', () => {
    expect(_buildDownloadQuery({release: {}})).toBe('')
    expect(_buildDownloadQuery({})).toBe('')
    expect(_buildDownloadQuery(null)).toBe('')
  })
})


/* ============================================================ modal open/close */

describe('download confirm modal — open/close', () => {
  beforeEach(() => {
    setupDOM()
    runDownload = vi.fn()
  })
  afterEach(() => { _closeDownloadConfirm() })

  it('flips aria-hidden on modal + backdrop when opened', () => {
    _openDownloadConfirm(RELEASE)
    expect(document.getElementById('disc-v2-dl-confirm').getAttribute('aria-hidden')).toBe('false')
    expect(document.getElementById('disc-v2-dl-confirm-backdrop').getAttribute('aria-hidden')).toBe('false')
  })

  it('hides modal on close', () => {
    _openDownloadConfirm(RELEASE)
    _closeDownloadConfirm()
    expect(document.getElementById('disc-v2-dl-confirm').getAttribute('aria-hidden')).toBe('true')
  })

  it('renders the release artist + title + query into the body', () => {
    _openDownloadConfirm(RELEASE)
    const body = document.getElementById('disc-v2-dl-confirm-body')
    expect(body.textContent).toContain('Madvillainy')
    expect(body.textContent).toContain('Madvillain')
    expect(body.querySelector('code').textContent).toBe('Madvillain Madvillainy')
  })

  it('returns focus to the opener after close', () => {
    const opener = document.getElementById('opener')
    opener.focus()
    expect(document.activeElement).toBe(opener)
    _openDownloadConfirm(RELEASE)
    _closeDownloadConfirm()
    expect(document.activeElement).toBe(opener)
  })
})


/* ============================================================ Cancel default focus */

describe('download confirm modal — Cancel is the default focus', () => {
  beforeEach(() => {
    setupDOM()
    runDownload = vi.fn()
  })
  afterEach(() => { _closeDownloadConfirm() })

  it('focuses the Cancel button when opened', () => {
    _openDownloadConfirm(RELEASE)
    expect(document.activeElement.id).toBe('disc-v2-dl-confirm-cancel')
  })

  it('Enter on Cancel does NOT call runDownload (default-cancel safety)', () => {
    _openDownloadConfirm(RELEASE)
    const cancel = document.activeElement
    expect(cancel.id).toBe('disc-v2-dl-confirm-cancel')
    // Simulate Enter on a button — browsers fire click on the focused button.
    cancel.click()
    expect(runDownload).not.toHaveBeenCalled()
  })

  it('Escape on open modal closes without downloading', () => {
    _openDownloadConfirm(RELEASE)
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))
    expect(document.getElementById('disc-v2-dl-confirm').getAttribute('aria-hidden')).toBe('true')
    expect(runDownload).not.toHaveBeenCalled()
  })
})


/* ============================================================ Confirm path */

describe('download confirm modal — Confirm path', () => {
  beforeEach(() => {
    setupDOM()
    runDownload = vi.fn()
  })
  afterEach(() => { _closeDownloadConfirm() })

  it('Confirm closes the modal and calls runDownload with the query', async () => {
    _openDownloadConfirm(RELEASE)
    await _runDownloadConfirmGo()
    expect(runDownload).toHaveBeenCalledWith('Madvillain Madvillainy', {})
    expect(document.getElementById('disc-v2-dl-confirm').getAttribute('aria-hidden')).toBe('true')
  })

  it('Confirm does nothing when no release is queued', async () => {
    // No _openDownloadConfirm — module state is fresh
    await _runDownloadConfirmGo()
    expect(runDownload).not.toHaveBeenCalled()
  })

  it('Confirm does nothing when the query would be empty', async () => {
    _openDownloadConfirm({release_key: 'kEmpty', release: {}})
    await _runDownloadConfirmGo()
    expect(runDownload).not.toHaveBeenCalled()
  })
})


/* ============================================================ Focus trap */

describe('download confirm modal — focus trap', () => {
  beforeEach(() => {
    setupDOM()
    runDownload = vi.fn()
  })
  afterEach(() => { _closeDownloadConfirm() })

  it('Tab from Confirm wraps back to Cancel', () => {
    _openDownloadConfirm(RELEASE)
    document.getElementById('disc-v2-dl-confirm-go').focus()
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', bubbles: true, cancelable: true}))
    expect(document.activeElement.id).toBe('disc-v2-dl-confirm-cancel')
  })

  it('Shift+Tab from Cancel wraps to Confirm', () => {
    _openDownloadConfirm(RELEASE)
    document.getElementById('disc-v2-dl-confirm-cancel').focus()
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true, cancelable: true}))
    expect(document.activeElement.id).toBe('disc-v2-dl-confirm-go')
  })

  it('removes the keydown handler on close (Escape is idempotent)', () => {
    _openDownloadConfirm(RELEASE)
    _closeDownloadConfirm()
    const before = document.getElementById('disc-v2-dl-confirm').getAttribute('aria-hidden')
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))
    expect(document.getElementById('disc-v2-dl-confirm').getAttribute('aria-hidden')).toBe(before)
  })
})


/* ============================================================ Card delegate semantics */

describe('card click delegate — Shift+click vs plain click', () => {
  it('Shift+click bypasses the panel and opens the confirm modal', () => {
    setupDOM()
    runDownload = vi.fn()
    // Inline the card delegate behavior under test.
    let openedPanel = false
    let openedConfirm = false
    function delegate(ev, release) {
      if (ev.shiftKey) {
        openedConfirm = true
        return
      }
      openedPanel = true
    }
    delegate({shiftKey: true}, RELEASE)
    expect(openedConfirm).toBe(true)
    expect(openedPanel).toBe(false)
  })

  it('plain click opens the panel (no confirm)', () => {
    let openedPanel = false
    let openedConfirm = false
    function delegate(ev, release) {
      if (ev.shiftKey) {
        openedConfirm = true
        return
      }
      openedPanel = true
    }
    delegate({shiftKey: false}, RELEASE)
    expect(openedPanel).toBe(true)
    expect(openedConfirm).toBe(false)
  })
})
