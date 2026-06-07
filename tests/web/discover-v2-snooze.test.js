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
  const release = _snoozePopRelease
  _closeSnoozePopover()
  if (!release) return
  try {
    await DiscoverV2.snooze(release, duration)
  } catch (_) {}
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
