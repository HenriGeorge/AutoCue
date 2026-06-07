/**
 * Tests for the Discover v2 snooze popover + resurfaced badge (T-033) —
 * popover open/close lifecycle, duration buttons fire snooze with PRD-locked
 * durations (1w/1m/3m), and the resurfaced badge appears for releases whose
 * snooze has expired. Mirrors docs/index.html — keep in sync.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

let DiscoverV2
let _snoozePopRelease = null
let _snoozePopReturnFocusEl = null
let _snoozePopKeydownHandler = null

function _openSnoozePopover(release, anchorEl) {
  const pop = document.getElementById('disc-v2-snooze-pop')
  if (!pop) return
  _snoozePopRelease = release
  _snoozePopReturnFocusEl = document.activeElement

  pop.setAttribute('aria-hidden', 'false')

  _snoozePopKeydownHandler = (ev) => {
    if (ev.key === 'Escape') {
      ev.preventDefault()
      _closeSnoozePopover()
    }
  }
  document.addEventListener('keydown', _snoozePopKeydownHandler)

  const def = pop.querySelector('button.default') || pop.querySelector('button')
  def?.focus()
}

function _closeSnoozePopover() {
  const pop = document.getElementById('disc-v2-snooze-pop')
  if (pop) pop.setAttribute('aria-hidden', 'true')
  if (_snoozePopKeydownHandler) {
    document.removeEventListener('keydown', _snoozePopKeydownHandler)
    _snoozePopKeydownHandler = null
  }
  if (_snoozePopReturnFocusEl && typeof _snoozePopReturnFocusEl.focus === 'function') {
    try { _snoozePopReturnFocusEl.focus() } catch (_) {}
  }
  _snoozePopReturnFocusEl = null
  _snoozePopRelease = null
}

async function _runSnoozeWithDuration(duration) {
  // Issue #69: snooze BEFORE closing the popover.
  const release = _snoozePopRelease
  if (!release) {
    _closeSnoozePopover()
    return
  }
  try {
    await DiscoverV2.snooze(release, duration)
  } catch (_) {
  } finally {
    _closeSnoozePopover()
  }
}

// ── Active card stickiness (Issue #69) ────────────────────────────────
// Mirrors docs/index.html — the active card is tracked by release_key so
// re-renders don't silently shift `.active` onto an adjacent card.
let _activeCardIndex = -1
let _activeReleaseKey = null

function _visibleDiscoverCards() {
  return Array.from(document.querySelectorAll('#disc-v2-grid .disc-v2-card'))
}

function _setActiveCard(index) {
  const cards = _visibleDiscoverCards()
  if (!cards.length) { _activeCardIndex = -1; _activeReleaseKey = null; return }
  if (index < 0) index = 0
  if (index >= cards.length) index = cards.length - 1
  cards.forEach(c => c.classList.remove('active'))
  cards[index].classList.add('active')
  _activeCardIndex = index
  _activeReleaseKey = cards[index].getAttribute('data-release-key')
}

function _activeRelease() {
  const cards = _visibleDiscoverCards()
  if (_activeCardIndex < 0 || _activeCardIndex >= cards.length) return null
  const key = cards[_activeCardIndex].getAttribute('data-release-key')
  if (_activeReleaseKey && key !== _activeReleaseKey) return null
  return DiscoverV2.state.cardsByKey.get(key) || null
}

// Re-render subscriber (the production code uses DiscoverV2.subscribe).
function _onFeedReRender() {
  const cards = _visibleDiscoverCards()
  if (!cards.length) { _activeCardIndex = -1; _activeReleaseKey = null; return }
  if (_activeReleaseKey) {
    const idx = cards.findIndex(c => c.getAttribute('data-release-key') === _activeReleaseKey)
    if (idx >= 0) {
      _activeCardIndex = idx
      cards[idx].classList.add('active')
      return
    }
    _activeCardIndex = -1
    _activeReleaseKey = null
    return
  }
  if (_activeCardIndex >= 0 && _activeCardIndex < cards.length) {
    cards[_activeCardIndex].classList.add('active')
    _activeReleaseKey = cards[_activeCardIndex].getAttribute('data-release-key')
  }
}

function _resurfacedBadge(release) {
  if (!release || !DiscoverV2.state.resurfacedKeys.has(release.release_key)) return ''
  const meta = DiscoverV2.state.snoozedMeta && DiscoverV2.state.snoozedMeta.get(release.release_key)
  const dateStr = meta && meta.until_date ? meta.until_date.slice(0, 10) : ''
  const titleAttr = dateStr ? ` title="Snooze expired on ${_esc(dateStr)}"` : ''
  return ` <span class="disc-v2-resurfaced-badge"${titleAttr}>🔁 Resurfaced</span>`
}


function setupDOM() {
  document.body.innerHTML = `
    <button id="opener">open</button>
    <div id="disc-v2-snooze-pop" role="dialog" aria-hidden="true">
      <h4>Snooze for</h4>
      <div>
        <button data-snooze-dur="1w">1 week</button>
        <button data-snooze-dur="1m" class="default">1 month</button>
        <button data-snooze-dur="3m">3 months</button>
      </div>
    </div>
  `
  // Wire the buttons exactly as initDiscoverV2 does.
  document.querySelectorAll('#disc-v2-snooze-pop [data-snooze-dur]').forEach(btn =>
    btn.addEventListener('click', (ev) => {
      ev.preventDefault()
      _runSnoozeWithDuration(btn.getAttribute('data-snooze-dur'))
    })
  )
}

const RELEASE = {release_key: 'k1', release: {artist: 'A', title: 'T'}}

function makeStub() {
  DiscoverV2 = {
    state: {
      resurfacedKeys: new Set(),
      snoozedMeta: new Map(),
    },
    snooze: vi.fn(async () => {}),
  }
}


/* ============================================================ open/close */

describe('snooze popover — open/close lifecycle', () => {
  beforeEach(() => { setupDOM(); makeStub() })

  it('flips aria-hidden on open', () => {
    _openSnoozePopover(RELEASE, null)
    expect(document.getElementById('disc-v2-snooze-pop').getAttribute('aria-hidden')).toBe('false')
  })

  it('focuses the default button (1 month)', () => {
    _openSnoozePopover(RELEASE, null)
    expect(document.activeElement.getAttribute('data-snooze-dur')).toBe('1m')
  })

  it('Escape closes the popover without calling snooze', () => {
    _openSnoozePopover(RELEASE, null)
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))
    expect(document.getElementById('disc-v2-snooze-pop').getAttribute('aria-hidden')).toBe('true')
    expect(DiscoverV2.snooze).not.toHaveBeenCalled()
  })

  it('returns focus to the opener after close', () => {
    const opener = document.getElementById('opener')
    opener.focus()
    expect(document.activeElement).toBe(opener)
    _openSnoozePopover(RELEASE, null)
    _closeSnoozePopover()
    expect(document.activeElement).toBe(opener)
  })
})


/* ============================================================ duration buttons */

describe('snooze popover — duration buttons', () => {
  beforeEach(() => { setupDOM(); makeStub() })

  it('1 week button fires snooze with "1w"', async () => {
    _openSnoozePopover(RELEASE, null)
    document.querySelector('[data-snooze-dur="1w"]').click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.snooze).toHaveBeenCalledWith(RELEASE, '1w')
  })

  it('1 month button fires snooze with "1m"', async () => {
    _openSnoozePopover(RELEASE, null)
    document.querySelector('[data-snooze-dur="1m"]').click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.snooze).toHaveBeenCalledWith(RELEASE, '1m')
  })

  it('3 months button fires snooze with "3m"', async () => {
    _openSnoozePopover(RELEASE, null)
    document.querySelector('[data-snooze-dur="3m"]').click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.snooze).toHaveBeenCalledWith(RELEASE, '3m')
  })

  it('Enter on the default-focused button picks 1m (PRD default)', async () => {
    _openSnoozePopover(RELEASE, null)
    // Browsers fire click on focused button when Enter is pressed; simulate it.
    document.activeElement.click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.snooze).toHaveBeenCalledWith(RELEASE, '1m')
  })

  it('button click closes the popover', async () => {
    _openSnoozePopover(RELEASE, null)
    document.querySelector('[data-snooze-dur="1w"]').click()
    await new Promise(r => setTimeout(r, 0))
    expect(document.getElementById('disc-v2-snooze-pop').getAttribute('aria-hidden')).toBe('true')
  })

  it('rejects the deprecated "30d" — uses only PRD durations', () => {
    // Build a set of supported durations from the popover DOM.
    const supported = Array.from(document.querySelectorAll('[data-snooze-dur]'))
      .map(b => b.getAttribute('data-snooze-dur'))
    expect(supported.sort()).toEqual(['1m', '1w', '3m'].sort())
    expect(supported).not.toContain('30d')
  })
})


/* ============================================================ resurfaced badge */

describe('_resurfacedBadge', () => {
  beforeEach(() => { setupDOM(); makeStub() })

  it('returns empty string when the release is NOT resurfaced', () => {
    expect(_resurfacedBadge(RELEASE)).toBe('')
  })

  it('renders the badge when the release is in resurfacedKeys', () => {
    DiscoverV2.state.resurfacedKeys.add('k1')
    const html = _resurfacedBadge(RELEASE)
    expect(html).toContain('disc-v2-resurfaced-badge')
    expect(html).toContain('🔁 Resurfaced')
  })

  it('includes the original resurface date as a tooltip', () => {
    DiscoverV2.state.resurfacedKeys.add('k1')
    DiscoverV2.state.snoozedMeta.set('k1', {until_date: '2026-03-15T12:00:00Z'})
    const html = _resurfacedBadge(RELEASE)
    expect(html).toContain('title="Snooze expired on 2026-03-15"')
  })

  it('omits the title attribute when no until_date is known', () => {
    DiscoverV2.state.resurfacedKeys.add('k1')
    const html = _resurfacedBadge(RELEASE)
    expect(html).not.toContain('title=')
  })

  it('escapes XSS in until_date', () => {
    DiscoverV2.state.resurfacedKeys.add('k1')
    DiscoverV2.state.snoozedMeta.set('k1', {until_date: '"><img src=x>'})
    const html = _resurfacedBadge(RELEASE)
    expect(html).not.toContain('"><img src=x>')
  })
})


/* ============================================================ Issue #69 — stray-dismiss regression */

describe('Issue #69 — snooze popover close must not dismiss an adjacent card', () => {
  function setupGrid(keys) {
    document.body.innerHTML = `
      <button id="opener">open</button>
      <div id="disc-v2-grid">
        ${keys.map(k => `<div class="disc-v2-card" data-release-key="${k}" role="button" tabindex="0">
          <div class="disc-v2-card-actions">
            <button data-act="dismiss">x</button>
          </div>
        </div>`).join('')}
      </div>
      <div id="disc-v2-snooze-pop" role="dialog" aria-hidden="true">
        <h4>Snooze for</h4>
        <div>
          <button data-snooze-dur="1w">1 week</button>
          <button data-snooze-dur="1m" class="default">1 month</button>
          <button data-snooze-dur="3m">3 months</button>
        </div>
      </div>
    `
    document.querySelectorAll('#disc-v2-snooze-pop [data-snooze-dur]').forEach(btn =>
      btn.addEventListener('click', (ev) => {
        ev.preventDefault()
        ev.stopPropagation()
        _runSnoozeWithDuration(btn.getAttribute('data-snooze-dur'))
      })
    )
  }

  beforeEach(() => {
    _activeCardIndex = -1
    _activeReleaseKey = null
    _snoozePopRelease = null
    _snoozePopReturnFocusEl = null
    _snoozePopKeydownHandler = null
  })

  it('drops the active state when the active card is removed by snooze (regression: would shift onto neighbor)', async () => {
    // Three cards. User picks the MIDDLE one and snoozes it. After the
    // re-render only A and C remain — the active state must NOT silently
    // transfer to C (which slid into index 1).
    setupGrid(['kA', 'kB', 'kC'])
    const releaseB = {release_key: 'kB', release: {artist: 'B', title: 'tB'}}
    DiscoverV2 = {
      state: {
        cardsByKey: new Map([
          ['kA', {release_key: 'kA'}],
          ['kB', releaseB],
          ['kC', {release_key: 'kC'}],
        ]),
        resurfacedKeys: new Set(),
        snoozedMeta: new Map(),
        snoozedKeys: new Set(),
      },
      snooze: vi.fn(async (r) => {
        // Simulate the production re-render: remove the snoozed card
        // BEFORE this promise resolves. Production order is now
        // snooze → re-render → close popover.
        DiscoverV2.state.snoozedKeys.add(r.release_key)
        document.querySelector(`.disc-v2-card[data-release-key="${r.release_key}"]`)?.remove()
        _onFeedReRender()
      }),
    }
    _setActiveCard(1)  // kB
    expect(_activeReleaseKey).toBe('kB')

    _openSnoozePopover(releaseB, null)
    document.querySelector('[data-snooze-dur="1w"]').click()
    // Drain the microtask queue so the async snooze completes.
    await new Promise(r => setTimeout(r, 0))

    // Without the fix: _activeCardIndex would still be 1, kC would have
    // slid into index 1, and _activeRelease() would return kC. That is
    // the bug — a stray Space/Enter would then dismiss kC.
    expect(_activeRelease()).toBeNull()
    // Neither kA nor kC may carry the active class.
    expect(document.querySelector('.disc-v2-card.active')).toBeNull()
  })

  it('boundary: snoozing the LAST card does not wrap onto the first', async () => {
    setupGrid(['kA', 'kB'])
    const releaseB = {release_key: 'kB', release: {artist: 'B', title: 'tB'}}
    DiscoverV2 = {
      state: {
        cardsByKey: new Map([['kA', {release_key: 'kA'}], ['kB', releaseB]]),
        resurfacedKeys: new Set(),
        snoozedMeta: new Map(),
        snoozedKeys: new Set(),
      },
      snooze: vi.fn(async (r) => {
        document.querySelector(`.disc-v2-card[data-release-key="${r.release_key}"]`)?.remove()
        _onFeedReRender()
      }),
    }
    _setActiveCard(1)
    _openSnoozePopover(releaseB, null)
    document.querySelector('[data-snooze-dur="3m"]').click()
    await new Promise(r => setTimeout(r, 0))
    expect(_activeRelease()).toBeNull()
    expect(document.querySelector('.disc-v2-card.active')).toBeNull()
  })

  it('property: for any active card position, after snooze the surviving cards never carry the active class', async () => {
    // Property-based: for every position i in [0, n), snoozing the card
    // at position i must leave NO surviving card marked active.
    for (let i = 0; i < 4; i++) {
      setupGrid(['k0', 'k1', 'k2', 'k3'])
      _activeCardIndex = -1
      _activeReleaseKey = null
      const key = `k${i}`
      const target = {release_key: key, release: {artist: 'a', title: 't'}}
      DiscoverV2 = {
        state: {
          cardsByKey: new Map([0,1,2,3].map(j => [`k${j}`, {release_key: `k${j}`}])),
          resurfacedKeys: new Set(),
          snoozedMeta: new Map(),
          snoozedKeys: new Set(),
        },
        snooze: vi.fn(async (r) => {
          document.querySelector(`.disc-v2-card[data-release-key="${r.release_key}"]`)?.remove()
          _onFeedReRender()
        }),
      }
      _setActiveCard(i)
      _openSnoozePopover(target, null)
      document.querySelector('[data-snooze-dur="1m"]').click()
      await new Promise(r => setTimeout(r, 0))
      expect(document.querySelectorAll('.disc-v2-card.active').length).toBe(0)
      expect(_activeRelease()).toBeNull()
    }
  })

  it('snooze popover button click does NOT bubble to ancestor grid handlers', () => {
    // Defence-in-depth: the popover lives outside the grid, but we still
    // assert stopPropagation so any future restructuring stays safe.
    setupGrid(['kA'])
    let bubbled = false
    document.body.addEventListener('click', () => { bubbled = true })
    DiscoverV2 = {
      state: {
        cardsByKey: new Map([['kA', {release_key: 'kA'}]]),
        resurfacedKeys: new Set(), snoozedMeta: new Map(), snoozedKeys: new Set(),
      },
      snooze: vi.fn(async () => {}),
    }
    _openSnoozePopover({release_key: 'kA', release: {}}, null)
    document.querySelector('[data-snooze-dur="1m"]').click()
    expect(bubbled).toBe(false)
  })
})
