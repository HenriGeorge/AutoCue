/**
 * Tests for the Discover v2 onboarding banner (T-031) — lazy load on first
 * visible, _followByName resolver, banner show/hide logic. Mirrors
 * docs/index.html — keep in sync.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// jsdom's localStorage in this Vitest config is missing the standard API; stub it.
if (typeof localStorage === 'undefined' || typeof localStorage.getItem !== 'function') {
  const store = {}
  // eslint-disable-next-line no-global-assign
  globalThis.localStorage = {
    getItem: (k) => Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null,
    setItem: (k, v) => { store[k] = String(v) },
    removeItem: (k) => { delete store[k] },
    clear: () => { for (const k of Object.keys(store)) delete store[k] },
  }
}

let DiscoverV2
let _onboardingLoaded

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

async function _loadOnboardingSuggestions() {
  const container = document.getElementById('disc-v2-onboarding-suggestions')
  if (!container) return
  container.innerHTML = '<em>Loading suggestions…</em>'
  try {
    const suggestions = await DiscoverV2.fetchSuggestedLabels(10)
    container.innerHTML = ''
    if (!suggestions.length) {
      container.innerHTML = '<em>No suggested labels</em>'
      return
    }
    suggestions.forEach(sug => {
      const chip = document.createElement('button')
      chip.setAttribute('data-suggest-name', sug.name)
      chip.textContent = sug.name
      chip.addEventListener('click', async () => {
        chip.disabled = true
        chip.textContent = '… ' + sug.name
        const followed = await _followByName(sug.name)
        if (followed) chip.textContent = '✓ ' + sug.name
        else { chip.disabled = false; chip.textContent = sug.name }
      })
      container.appendChild(chip)
    })
  } catch (_) {
    container.innerHTML = '<em>Could not load suggestions.</em>'
  }
}

function _renderDiscoverV2Onboarding() {
  const banner = document.getElementById('disc-v2-onboarding-banner')
  if (!banner) return
  const shouldShow =
    DiscoverV2.state.followedLabels.length === 0 &&
    !localStorage.getItem('disc-v2-onboarding-skipped')
  if (shouldShow) {
    banner.style.display = ''
    if (!_onboardingLoaded) {
      _onboardingLoaded = true
      _loadOnboardingSuggestions()
    }
  } else {
    banner.style.display = 'none'
  }
}

async function _openOnboarding() {
  const banner = document.getElementById('disc-v2-onboarding-banner')
  if (!banner) return
  banner.style.display = ''
  _onboardingLoaded = true
  await _loadOnboardingSuggestions()
}


/* ============================================================ setup */

function setupDOM() {
  document.body.innerHTML = `
    <div id="disc-v2-onboarding-banner" style="display:none">
      <div id="disc-v2-onboarding-suggestions"></div>
    </div>
  `
}

function makeStub() {
  _onboardingLoaded = false
  DiscoverV2 = {
    state: {followedLabels: []},
    fetchSuggestedLabels: vi.fn(async () => [
      {name: 'Stones Throw', weight: 14.5},
      {name: 'Hyperdub', weight: 7.2},
    ]),
    searchLabels: vi.fn(async (q) => [{id: 1234, name: q}]),
    followLabel: vi.fn(async () => {}),
  }
  try { localStorage.removeItem('disc-v2-onboarding-skipped') } catch (_) {}
}


/* ============================================================ visibility + auto-load */

describe('_renderDiscoverV2Onboarding visibility', () => {
  beforeEach(() => { setupDOM(); makeStub() })
  afterEach(() => { try { localStorage.removeItem('disc-v2-onboarding-skipped') } catch (_) {} })

  it('shows the banner when no labels are followed', () => {
    _renderDiscoverV2Onboarding()
    expect(document.getElementById('disc-v2-onboarding-banner').style.display).toBe('')
  })

  it('hides the banner once the user follows ≥1 label', () => {
    DiscoverV2.state.followedLabels = [{label_id: 1, name: 'Stones Throw'}]
    _renderDiscoverV2Onboarding()
    expect(document.getElementById('disc-v2-onboarding-banner').style.display).toBe('none')
  })

  it('hides the banner when the user previously chose Skip for now', () => {
    localStorage.setItem('disc-v2-onboarding-skipped', '1')
    _renderDiscoverV2Onboarding()
    expect(document.getElementById('disc-v2-onboarding-banner').style.display).toBe('none')
  })

  it('auto-loads suggestions the FIRST time the banner becomes visible', async () => {
    _renderDiscoverV2Onboarding()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.fetchSuggestedLabels).toHaveBeenCalledTimes(1)
  })

  it('does NOT re-fetch on subsequent renders if already loaded', async () => {
    _renderDiscoverV2Onboarding()
    await new Promise(r => setTimeout(r, 0))
    _renderDiscoverV2Onboarding()
    _renderDiscoverV2Onboarding()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.fetchSuggestedLabels).toHaveBeenCalledTimes(1)
  })

  it('renders one chip per suggestion', async () => {
    _renderDiscoverV2Onboarding()
    await new Promise(r => setTimeout(r, 0))
    expect(document.querySelectorAll('#disc-v2-onboarding-suggestions button').length).toBe(2)
  })
})


/* ============================================================ chip click follow flow */

describe('onboarding chip click — _followByName resolver', () => {
  beforeEach(() => { setupDOM(); makeStub() })

  it('Suggest click resolves label_id via searchLabels then follows', async () => {
    _renderDiscoverV2Onboarding()
    await new Promise(r => setTimeout(r, 0))
    const chip = document.querySelector('button')
    chip.click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.searchLabels).toHaveBeenCalledWith('Stones Throw')
    expect(DiscoverV2.followLabel).toHaveBeenCalledWith(1234, 'Stones Throw')
    expect(chip.textContent).toBe('✓ Stones Throw')
    expect(chip.disabled).toBe(true)
  })

  it('Suggest click re-enables when searchLabels finds no match', async () => {
    DiscoverV2.searchLabels = vi.fn(async () => [])
    _renderDiscoverV2Onboarding()
    await new Promise(r => setTimeout(r, 0))
    const chip = document.querySelector('button')
    chip.click()
    await new Promise(r => setTimeout(r, 0))
    expect(DiscoverV2.followLabel).not.toHaveBeenCalled()
    expect(chip.disabled).toBe(false)
    expect(chip.textContent).toBe('Stones Throw')
  })
})


/* ============================================================ _openOnboarding force-load */

describe('_openOnboarding — empty-state action', () => {
  beforeEach(() => { setupDOM(); makeStub() })

  it('shows the banner regardless of the skipped flag', async () => {
    localStorage.setItem('disc-v2-onboarding-skipped', '1')
    await _openOnboarding()
    expect(document.getElementById('disc-v2-onboarding-banner').style.display).toBe('')
  })

  it('re-fetches suggestions even when previously loaded', async () => {
    _onboardingLoaded = true
    await _openOnboarding()
    expect(DiscoverV2.fetchSuggestedLabels).toHaveBeenCalledTimes(1)
  })
})


/* ============================================================ _followByName edges */

describe('_followByName edges', () => {
  beforeEach(() => { setupDOM(); makeStub() })

  it('returns false for empty / null name', async () => {
    expect(await _followByName(null)).toBe(false)
    expect(await _followByName('')).toBe(false)
    expect(DiscoverV2.searchLabels).not.toHaveBeenCalled()
  })

  it('returns false when searchLabels resolves to an empty array', async () => {
    DiscoverV2.searchLabels = vi.fn(async () => [])
    expect(await _followByName('Nothing')).toBe(false)
    expect(DiscoverV2.followLabel).not.toHaveBeenCalled()
  })

  it('returns false when searchLabels throws', async () => {
    DiscoverV2.searchLabels = vi.fn(async () => { throw new Error('net') })
    expect(await _followByName('X')).toBe(false)
    expect(DiscoverV2.followLabel).not.toHaveBeenCalled()
  })
})
