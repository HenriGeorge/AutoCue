/**
 * Tests for the Discover v2 keyboard shortcuts (T-028) — j/k navigation,
 * Enter, s/x/z, D (Shift+d), ? help overlay, text-input guard, dialog
 * gating. Mirrors docs/index.html — keep in sync.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

/* ---------------------------------------------------------------- stub helpers */

let DiscoverV2
let _openDetailPanel
let _openDownloadConfirm

let _activeCardIndex = -1

function _visibleDiscoverCards() {
  return Array.from(document.querySelectorAll('#disc-v2-grid .disc-v2-card'))
}

function _setActiveCard(index, scroll = false) {
  const cards = _visibleDiscoverCards()
  if (!cards.length) { _activeCardIndex = -1; return }
  if (index < 0) index = 0
  if (index >= cards.length) index = cards.length - 1
  cards.forEach(c => c.classList.remove('active'))
  cards[index].classList.add('active')
  _activeCardIndex = index
}

function _activeRelease() {
  const cards = _visibleDiscoverCards()
  if (_activeCardIndex < 0 || _activeCardIndex >= cards.length) return null
  const key = cards[_activeCardIndex].getAttribute('data-release-key')
  return DiscoverV2.state.cardsByKey.get(key) || null
}

function _kbdIsTextInputActive() {
  const el = document.activeElement
  if (!el || el === document.body) return false
  const tag = (el.tagName || '').toUpperCase()
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (el.isContentEditable) return true
  return false
}

function _kbdDialogIsOpen() {
  return ['disc-v2-detail-panel', 'disc-v2-dl-confirm', 'disc-v2-kbd-help']
    .some(id => document.getElementById(id)?.getAttribute('aria-hidden') === 'false')
}

function _toggleKbdHelp() {
  const modal = document.getElementById('disc-v2-kbd-help')
  const backdrop = document.getElementById('disc-v2-kbd-backdrop')
  if (!modal) return
  const isOpen = modal.getAttribute('aria-hidden') === 'false'
  modal.setAttribute('aria-hidden', isOpen ? 'true' : 'false')
  if (backdrop) backdrop.setAttribute('aria-hidden', isOpen ? 'true' : 'false')
}

function _handleDiscoverKeydown(ev) {
  if (_kbdIsTextInputActive()) return
  const section = document.getElementById('disc-v2-section')
  if (!section) return
  const visible = section.offsetParent !== null
  if (!visible) return

  if (ev.key === '?') {
    ev.preventDefault()
    _toggleKbdHelp()
    return
  }

  if (_kbdDialogIsOpen()) return

  switch (ev.key) {
    case 'j': ev.preventDefault(); _setActiveCard(_activeCardIndex + 1); return
    case 'k': ev.preventDefault(); _setActiveCard(_activeCardIndex - 1); return
    case 'Enter': {
      const rel = _activeRelease(); if (!rel) return
      ev.preventDefault(); _openDetailPanel(rel.release_key); return
    }
    case 's': {
      const rel = _activeRelease(); if (!rel) return
      ev.preventDefault(); DiscoverV2.save(rel); return
    }
    case 'x': {
      const rel = _activeRelease(); if (!rel) return
      ev.preventDefault(); DiscoverV2.dismiss(rel); return
    }
    case 'z': {
      const rel = _activeRelease(); if (!rel) return
      ev.preventDefault(); DiscoverV2.snooze(rel, '30d'); return
    }
    case 'D': {
      const rel = _activeRelease(); if (!rel) return
      ev.preventDefault(); _openDownloadConfirm(rel); return
    }
  }
}


/* ---------------------------------------------------- DOM setup */

function setupDOM(numCards = 3) {
  const cardHtml = Array.from({length: numCards}, (_, i) =>
    `<div class="disc-v2-card" data-release-key="k${i+1}"></div>`
  ).join('')
  document.body.innerHTML = `
    <div id="disc-v2-section">
      <div id="disc-v2-grid">${cardHtml}</div>
      <input id="search-box" type="text">
    </div>
    <div id="disc-v2-detail-panel" aria-hidden="true"></div>
    <div id="disc-v2-dl-confirm" aria-hidden="true"></div>
    <div id="disc-v2-kbd-backdrop" aria-hidden="true"></div>
    <div id="disc-v2-kbd-help" aria-hidden="true"></div>
  `
  // Ensure offsetParent is non-null inside jsdom — give the section a non-empty
  // bounding rect. jsdom returns null offsetParent for everything by default, so
  // stub the property instead.
  Object.defineProperty(document.getElementById('disc-v2-section'), 'offsetParent', {
    get() { return document.body },
    configurable: true,
  })
}

function makeStub() {
  _activeCardIndex = -1
  _openDetailPanel = vi.fn()
  _openDownloadConfirm = vi.fn()
  DiscoverV2 = {
    state: {
      cardsByKey: new Map([
        ['k1', {release_key: 'k1', release: {artist: 'A1', title: 'T1'}}],
        ['k2', {release_key: 'k2', release: {artist: 'A2', title: 'T2'}}],
        ['k3', {release_key: 'k3', release: {artist: 'A3', title: 'T3'}}],
      ]),
    },
    save: vi.fn(),
    dismiss: vi.fn(),
    snooze: vi.fn(),
  }
}

function fire(key, opts = {}) {
  const ev = new KeyboardEvent('keydown', {key, bubbles: true, cancelable: true, ...opts})
  document.dispatchEvent(ev)
  return ev
}


/* ============================================================ navigation */

describe('j/k navigation', () => {
  beforeEach(() => {
    setupDOM()
    makeStub()
    document.addEventListener('keydown', _handleDiscoverKeydown)
  })
  afterEach(() => {
    document.removeEventListener('keydown', _handleDiscoverKeydown)
  })

  it('j on a fresh page selects the first card', () => {
    fire('j')
    expect(_activeCardIndex).toBe(0)
    expect(document.querySelector('.disc-v2-card.active').getAttribute('data-release-key')).toBe('k1')
  })

  it('j advances; k goes back; bounds are clamped', () => {
    fire('j'); fire('j'); fire('j')  // k1 → k2 → k3 → clamped at k3
    expect(_activeCardIndex).toBe(2)
    fire('j')
    expect(_activeCardIndex).toBe(2)
    fire('k')
    expect(_activeCardIndex).toBe(1)
    fire('k'); fire('k'); fire('k')  // clamped at 0
    expect(_activeCardIndex).toBe(0)
  })

  it('j prevents the default browser scroll', () => {
    const ev = fire('j')
    expect(ev.defaultPrevented).toBe(true)
  })

  it('does NOT navigate when no cards are present', () => {
    document.getElementById('disc-v2-grid').innerHTML = ''
    fire('j')
    expect(_activeCardIndex).toBe(-1)
  })
})


/* ============================================================ Enter / actions */

describe('Enter / s / x / z / D shortcuts', () => {
  beforeEach(() => {
    setupDOM()
    makeStub()
    document.addEventListener('keydown', _handleDiscoverKeydown)
    fire('j')  // activate k1
  })
  afterEach(() => {
    document.removeEventListener('keydown', _handleDiscoverKeydown)
  })

  it('Enter opens the detail panel for the active card', () => {
    fire('Enter')
    expect(_openDetailPanel).toHaveBeenCalledWith('k1')
  })

  it('s saves the active card', () => {
    fire('s')
    expect(DiscoverV2.save).toHaveBeenCalledWith(expect.objectContaining({release_key: 'k1'}))
  })

  it('x dismisses the active card', () => {
    fire('x')
    expect(DiscoverV2.dismiss).toHaveBeenCalledWith(expect.objectContaining({release_key: 'k1'}))
  })

  it('z snoozes for 30 days', () => {
    fire('z')
    expect(DiscoverV2.snooze).toHaveBeenCalledWith(expect.objectContaining({release_key: 'k1'}), '30d')
  })

  it('D (Shift+d) opens the download confirm modal', () => {
    fire('D', {shiftKey: true})
    expect(_openDownloadConfirm).toHaveBeenCalledWith(expect.objectContaining({release_key: 'k1'}))
  })

  it('lowercase d does NOT open the download confirm modal', () => {
    fire('d')
    expect(_openDownloadConfirm).not.toHaveBeenCalled()
  })

  it('action shortcuts are no-op when there is no active card', () => {
    _activeCardIndex = -1
    fire('s')
    fire('x')
    fire('z')
    expect(DiscoverV2.save).not.toHaveBeenCalled()
    expect(DiscoverV2.dismiss).not.toHaveBeenCalled()
    expect(DiscoverV2.snooze).not.toHaveBeenCalled()
  })
})


/* ============================================================ ? help overlay */

describe('? toggles the help overlay', () => {
  beforeEach(() => {
    setupDOM()
    makeStub()
    document.addEventListener('keydown', _handleDiscoverKeydown)
  })
  afterEach(() => {
    document.removeEventListener('keydown', _handleDiscoverKeydown)
  })

  it('? opens the help overlay', () => {
    fire('?')
    expect(document.getElementById('disc-v2-kbd-help').getAttribute('aria-hidden')).toBe('false')
  })

  it('? again closes the help overlay', () => {
    fire('?')
    fire('?')
    expect(document.getElementById('disc-v2-kbd-help').getAttribute('aria-hidden')).toBe('true')
  })

  it('? also flips the backdrop', () => {
    fire('?')
    expect(document.getElementById('disc-v2-kbd-backdrop').getAttribute('aria-hidden')).toBe('false')
  })
})


/* ============================================================ guards */

describe('text-input + dialog guards', () => {
  beforeEach(() => {
    setupDOM()
    makeStub()
    document.addEventListener('keydown', _handleDiscoverKeydown)
  })
  afterEach(() => {
    document.removeEventListener('keydown', _handleDiscoverKeydown)
  })

  it('does nothing while a text input has focus', () => {
    document.getElementById('search-box').focus()
    fire('j')
    expect(_activeCardIndex).toBe(-1)
    fire('s')
    expect(DiscoverV2.save).not.toHaveBeenCalled()
  })

  it('does NOT activate action shortcuts when the detail panel is open', () => {
    document.getElementById('disc-v2-detail-panel').setAttribute('aria-hidden', 'false')
    fire('j')
    expect(_activeCardIndex).toBe(-1)
    fire('s')
    expect(DiscoverV2.save).not.toHaveBeenCalled()
  })

  it('does NOT activate action shortcuts when the download confirm is open', () => {
    document.getElementById('disc-v2-dl-confirm').setAttribute('aria-hidden', 'false')
    fire('Enter')
    expect(_openDetailPanel).not.toHaveBeenCalled()
  })

  it('? still works even when a dialog is open (escape hatch)', () => {
    document.getElementById('disc-v2-detail-panel').setAttribute('aria-hidden', 'false')
    fire('?')
    expect(document.getElementById('disc-v2-kbd-help').getAttribute('aria-hidden')).toBe('false')
  })

  it('does nothing when the Discover section is not visible', () => {
    // Force offsetParent to null to simulate display:none.
    Object.defineProperty(document.getElementById('disc-v2-section'), 'offsetParent', {
      get() { return null },
      configurable: true,
    })
    fire('j')
    expect(_activeCardIndex).toBe(-1)
  })
})
