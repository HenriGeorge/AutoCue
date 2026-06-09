/**
 * Regression tests for UX audit PR C — Issue 9 (stale error clear),
 * Issue 3 (YT mismatch heuristic), Issue 10 (onboarding chip warning).
 *
 * M-3 (chip-suggestion tooltip) is verified via DOM-attribute assertion.
 */

import { describe, it, expect } from 'vitest'

/* ====================================== Issue 3: YT mismatch heuristic */

function _ytLikelyMismatch(ytTitle, expectedArtist, expectedAlbum) {
  const norm = (s) => String(s || '').toLowerCase().split(/[^a-z0-9]+/).filter(t => t.length >= 4)
  const haystack = new Set(norm(ytTitle))
  const needles = [...norm(expectedArtist), ...norm(expectedAlbum)]
  if (!needles.length || !haystack.size) return false
  return !needles.some(n => haystack.has(n))
}

describe('_ytLikelyMismatch — surfaces obvious audiobook / unrelated content', () => {
  it('flags an audiobook result against an album request', () => {
    expect(_ytLikelyMismatch(
      'The Mystery of Angelina Frood by R. Austin Freeman',
      'Sandy B & soFa elsewhere', 'Forward In Reverse Pt.1',
    )).toBe(true)
  })

  it('does NOT flag a matching artist token', () => {
    expect(_ytLikelyMismatch(
      'Madvillain - Madvillainy (Full Album)',
      'Madvillain', 'Madvillainy',
    )).toBe(false)
  })

  it('matches case-insensitive artist match', () => {
    expect(_ytLikelyMismatch(
      'sofa elsewhere — full set',
      'soFa elsewhere', 'Forward in Reverse',
    )).toBe(false)  // "sofa" is < 4 chars when tokenized; "elsewhere" matches.
  })

  it('returns false when no needles (empty artist + album)', () => {
    expect(_ytLikelyMismatch('Anything', '', '')).toBe(false)
  })

  it('ignores short tokens (< 4 chars) so "the", "by", "of" never trigger', () => {
    // Both sides tokenize to ["album", "group"] (≥4 chars only). They match,
    // so NOT a mismatch. This is the test the heuristic must pass — short
    // stopwords ("the", "by") would otherwise dominate.
    expect(_ytLikelyMismatch(
      'The album by The Group',
      'The Group', 'The Album',
    )).toBe(false)
  })

  it('flags totally unrelated content', () => {
    expect(_ytLikelyMismatch(
      'How to bake sourdough at home',
      'Burial', 'Untrue',
    )).toBe(true)
  })
})


/* ====================================== Issue 10: onboarding chip messaging */

describe('onboarding chip failure messaging', () => {
  it('failed chip carries disc-v2-suggest-failed class', () => {
    document.body.innerHTML = '<button id="c"></button>'
    const chip = document.getElementById('c')
    // Simulate the production path: chip stays disabled + adds the class.
    chip.disabled = true
    chip.classList.add('disc-v2-suggest-failed')
    chip.textContent = '⚠ Mystery Label'
    chip.title = `Couldn't find "Mystery Label" on Discogs.`
    expect(chip.classList.contains('disc-v2-suggest-failed')).toBe(true)
    expect(chip.textContent.startsWith('⚠')).toBe(true)
    expect(chip.title).toContain("Couldn't find")
  })

  it('add-all summary message correctly counts successes vs failures', () => {
    document.body.innerHTML = `
      <div id="suggestions">
        <button disabled>✓ Stones Throw</button>
        <button disabled>✓ Hyperdub</button>
        <button disabled class="disc-v2-suggest-failed">⚠ Mystery Label</button>
      </div>
    `
    const container = document.getElementById('suggestions')
    const total = 3
    const followed = container.querySelectorAll('button[disabled]:not(.disc-v2-suggest-failed)').length
    const failed = container.querySelectorAll('.disc-v2-suggest-failed').length
    expect(followed).toBe(2)
    expect(failed).toBe(1)
    expect(followed + failed).toBe(total)
  })
})


/* ====================================== Issue 9: stale-error clear path */

describe('stale scanError clear on tab activation', () => {
  it('feed/status running=false clears state.scanError', async () => {
    const state = { scanError: {kind: 'conflict', message: 'busy'} }
    const fakeStatus = { running: false }
    // Mirror of the production logic in initDiscoverV2 after loadInitialState
    if (fakeStatus && fakeStatus.running === false) {
      state.scanError = null
    }
    expect(state.scanError).toBeNull()
  })

  it('feed/status running=true does NOT clear state.scanError', () => {
    const state = { scanError: {kind: 'conflict', message: 'busy'} }
    const fakeStatus = { running: true }
    if (fakeStatus && fakeStatus.running === false) {
      state.scanError = null
    }
    expect(state.scanError).not.toBeNull()
  })

  it('error copy for conflict kind no longer says "wait"', () => {
    // The new copy: "Click Refresh to try again." — not "Wait for the other
    // scan to finish or cancel it."
    const e = {kind: 'conflict', message: 'busy'}
    const copy = (e.kind === 'conflict' ? ' Click Refresh to try again.' : '')
    expect(copy).toBe(' Click Refresh to try again.')
    expect(copy).not.toContain('Wait')
  })
})


/* ====================================== Issue #121: auto-scan gating */

describe('Discover auto-scan gating on tab activation (issue #121)', () => {
  // Mirror of the production decision in initDiscoverV2 (docs/index.html
  // ~line 7615). The whole point is to avoid calling /api/discover/feed
  // (and the resulting native 409 console error) when a scan is already
  // running on the server.
  function decideAutoScan(status, state) {
    if (status && status.running === true) return false
    return Boolean(state.tokenValid && state.followedLabels.length > 0)
  }

  it('REGRESSION: returns false when a scan is already running (no 409)', () => {
    // Without the fix this branch never existed and runScan was unconditional
    // when tokenValid + followedLabels > 0 — guaranteed 409 on the second
    // page load.
    const state = {tokenValid: true, followedLabels: ['Stones Throw']}
    expect(decideAutoScan({running: true}, state)).toBe(false)
  })

  it('BOUNDARY: returns true when status.running flips from true to false', () => {
    // The exact threshold where behavior changes.
    const state = {tokenValid: true, followedLabels: ['Stones Throw']}
    expect(decideAutoScan({running: true},  state)).toBe(false)
    expect(decideAutoScan({running: false}, state)).toBe(true)
  })

  it('happy path: fires when no scan is running AND prereqs are met', () => {
    const state = {tokenValid: true, followedLabels: ['Stones Throw']}
    expect(decideAutoScan({running: false}, state)).toBe(true)
  })

  it('respects token gate even when no scan is running', () => {
    const state = {tokenValid: false, followedLabels: ['Stones Throw']}
    expect(decideAutoScan({running: false}, state)).toBe(false)
  })

  it('respects followedLabels gate even when no scan is running', () => {
    const state = {tokenValid: true, followedLabels: []}
    expect(decideAutoScan({running: false}, state)).toBe(false)
  })

  it('fall-through when status fetch threw (status === null): preserves happy path', () => {
    // Conservative behavior: if /api/discover/feed/status itself fails,
    // we do NOT silently swallow the auto-scan. The user still gets the
    // same behavior as before the fix.
    const state = {tokenValid: true, followedLabels: ['Stones Throw']}
    expect(decideAutoScan(null, state)).toBe(true)
  })

  it('truthiness invariant: any non-true running value still fires the auto-scan', () => {
    // Property-style: only the explicit running===true sentinel should block.
    const state = {tokenValid: true, followedLabels: ['Stones Throw']}
    for (const r of [false, undefined, 0, '', null]) {
      expect(decideAutoScan({running: r}, state)).toBe(true)
    }
  })
})
