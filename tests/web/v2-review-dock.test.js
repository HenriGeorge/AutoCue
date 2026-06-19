// @vitest-environment jsdom
/**
 * Review Dock (dev-only) — docs/js/v2/review-dock.js.
 *
 * Two independent guards keep it off for real users; here we cover the CLIENT
 * gate (localMode AND localStorage.ac_review_dock==='1'), the current-page
 * derivation, and the submit flow (POST /api/review-note → r.ok-checked clear +
 * "✓ sent"; !ok → window.showToast). No DOM is injected unless both gates pass.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { initReviewDock, _derivePage } from '../../docs/js/v2/review-dock.js'

// jsdom's localStorage in this Vitest config is missing the standard API; stub it.
if (typeof localStorage === 'undefined' || typeof localStorage.getItem !== 'function') {
  const store = {}
  // eslint-disable-next-line no-global-assign
  globalThis.localStorage = {
    getItem: (k) => (Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v) },
    removeItem: (k) => { delete store[k] },
    clear: () => { for (const k of Object.keys(store)) delete store[k] },
  }
}

let _localMode = true
let _crate = 'all'

beforeEach(() => {
  document.body.className = ''
  document.body.innerHTML = ''
  _localMode = true
  _crate = 'all'
  window.ACBridge = {
    isLocalMode: () => _localMode,
    crate: () => _crate,
  }
  localStorage.clear()
  localStorage.setItem('ac_review_dock', '1')
  vi.restoreAllMocks()
  delete window.showToast
})

afterEach(() => {
  localStorage.clear()
})

const dock = () => document.querySelector('.review-dock')

describe('render gate', () => {
  it('does NOT render by default (localStorage flag unset)', () => {
    localStorage.removeItem('ac_review_dock')
    initReviewDock()
    expect(dock()).toBeNull()
  })

  it('does NOT render when the flag is not exactly "1"', () => {
    localStorage.setItem('ac_review_dock', '0')
    initReviewDock()
    expect(dock()).toBeNull()
  })

  it('does NOT render outside local mode (Pages/XML)', () => {
    _localMode = false
    initReviewDock()
    expect(dock()).toBeNull()
  })

  it('renders the dock when both gates pass', () => {
    initReviewDock()
    const d = dock()
    expect(d).toBeTruthy()
    expect(d.tagName).toBe('FORM')
    // a11y: a real sr-only label tied to the input
    const label = d.querySelector('label.sr-only[for="review-dock-input"]')
    expect(label).toBeTruthy()
    const input = d.querySelector('#review-dock-input')
    expect(input).toBeTruthy()
    expect(input.getAttribute('placeholder')).toBe('describe a change for this page…')
    // page badge is mono
    expect(d.querySelector('.review-dock-page')?.classList.contains('mono')).toBe(true)
    // confirmation region is an aria-live polite region
    expect(d.querySelector('[aria-live="polite"]')).toBeTruthy()
  })

  it('is idempotent — a second init does not append a second dock', () => {
    initReviewDock()
    initReviewDock()
    expect(document.querySelectorAll('.review-dock')).toHaveLength(1)
  })
})

describe('_derivePage', () => {
  it('nb-active → nightboard', () => {
    document.body.classList.add('nb-active')
    expect(_derivePage()).toBe('nightboard')
  })
  it('wb-place-dupes → duplicates', () => {
    document.body.classList.add('wb-place-dupes')
    expect(_derivePage()).toBe('duplicates')
  })
  it('wb-place-discover → discover', () => {
    document.body.classList.add('wb-place-discover')
    expect(_derivePage()).toBe('discover')
  })
  it('wb-place-library → library', () => {
    document.body.classList.add('wb-place-library')
    expect(_derivePage()).toBe('library')
  })
  it('falls back to ACBridge.crate() when no place is active', () => {
    _crate = 'needcues'
    expect(_derivePage()).toBe('needcues')
  })
  it('falls back to "cues" when crate is empty/falsy', () => {
    _crate = ''
    expect(_derivePage()).toBe('cues')
  })
})

describe('submit', () => {
  it('POSTs {page, note}, clears the input, and shows "✓ sent"', async () => {
    document.body.classList.add('wb-place-dupes')
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ ok: true }) }))
    globalThis.fetch = fetchMock
    initReviewDock()

    const input = document.querySelector('#review-dock-input')
    input.value = '  make the rescan pill bigger  '
    dock().dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }))

    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce())
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/review-note')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toEqual({ page: 'duplicates', note: 'make the rescan pill bigger' })

    await vi.waitFor(() => {
      expect(document.querySelector('#review-dock-input').value).toBe('')
      expect(document.querySelector('.review-dock-status').textContent).toContain('sent')
    })
  })

  it('does nothing on an empty/whitespace note (no fetch)', async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ ok: true }) }))
    globalThis.fetch = fetchMock
    initReviewDock()
    const input = document.querySelector('#review-dock-input')
    input.value = '   '
    dock().dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }))
    await Promise.resolve()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('on !r.ok shows window.showToast and does NOT clear the input', async () => {
    const fetchMock = vi.fn(async () => ({ ok: false, status: 403, json: async () => ({ detail: 'disabled' }) }))
    globalThis.fetch = fetchMock
    const toast = vi.fn()
    window.showToast = toast
    initReviewDock()

    const input = document.querySelector('#review-dock-input')
    input.value = 'something'
    dock().dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }))

    await vi.waitFor(() => expect(toast).toHaveBeenCalled())
    expect(toast.mock.calls[0][0]).toMatch(/disabled|403|fail/i)
    // input retained so the note isn't lost
    expect(document.querySelector('#review-dock-input').value).toBe('something')
  })
})
