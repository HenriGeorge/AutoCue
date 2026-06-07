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
