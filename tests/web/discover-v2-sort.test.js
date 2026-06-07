/**
 * Tests for the Discover v2 filter-bar sort + Explore mode (T-029) —
 * client-side resort over the existing fetch. Mirrors docs/index.html —
 * keep in sync.
 */

import { describe, it, expect } from 'vitest'

function _applyDiscoverV2Sort(cards, sortMode) {
  if (!cards || !cards.length) return cards || []
  if (!sortMode || sortMode === 'taste') return cards.slice()
  if (sortMode === 'newest') {
    return cards.slice().sort((a, b) => {
      const ay = parseInt((a.release && a.release.year) || 0, 10) || 0
      const by = parseInt((b.release && b.release.year) || 0, 10) || 0
      return by - ay
    })
  }
  const norm = (s) => String((s || '')).toLocaleLowerCase()
  if (sortMode === 'title') {
    return cards.slice().sort((a, b) =>
      norm(a.release && a.release.title).localeCompare(norm(b.release && b.release.title)))
  }
  if (sortMode === 'artist') {
    return cards.slice().sort((a, b) =>
      norm(a.release && a.release.artist).localeCompare(norm(b.release && b.release.artist)))
  }
  if (sortMode === 'explore') {
    const novelty = []
    const other = []
    for (const c of cards) {
      if ((c.source || '').startsWith('novelty')) novelty.push(c)
      else other.push(c)
    }
    const out = []
    const max = Math.max(novelty.length, other.length)
    for (let i = 0; i < max; i++) {
      if (i < other.length) out.push(other[i])
      if (i < novelty.length) out.push(novelty[i])
    }
    return out
  }
  return cards.slice()
}


const CARDS = [
  {release_key: 'k1', source: 'artist',         release: {title: 'Brilliant', artist: 'Madvillain', year: 2004}},
  {release_key: 'k2', source: 'label',          release: {title: 'Aquatic',   artist: 'Drexciya',   year: 1999}},
  {release_key: 'k3', source: 'novelty:style',  release: {title: 'Cosmic',    artist: 'Sun Ra',     year: 1972}},
  {release_key: 'k4', source: 'novelty:label',  release: {title: 'Distant',   artist: 'Burial',     year: 2007}},
  {release_key: 'k5', source: 'artist',         release: {title: 'Encore',    artist: 'Aphex Twin'}},
]


describe('_applyDiscoverV2Sort — taste / default', () => {
  it('preserves backend order on taste sort', () => {
    expect(_applyDiscoverV2Sort(CARDS, 'taste').map(c => c.release_key))
      .toEqual(['k1', 'k2', 'k3', 'k4', 'k5'])
  })
  it('returns a copy (does not mutate the input)', () => {
    const out = _applyDiscoverV2Sort(CARDS, 'taste')
    expect(out).not.toBe(CARDS)
  })
  it('preserves backend order when sortMode is unknown', () => {
    expect(_applyDiscoverV2Sort(CARDS, 'bogus').map(c => c.release_key))
      .toEqual(['k1', 'k2', 'k3', 'k4', 'k5'])
  })
  it('returns empty for empty input', () => {
    expect(_applyDiscoverV2Sort([], 'newest')).toEqual([])
    expect(_applyDiscoverV2Sort(null, 'title')).toEqual([])
  })
})


describe('_applyDiscoverV2Sort — newest', () => {
  it('sorts by year DESC', () => {
    expect(_applyDiscoverV2Sort(CARDS, 'newest').map(c => c.release_key))
      .toEqual(['k4', 'k1', 'k2', 'k3', 'k5'])
  })
  it('puts cards without a year last (year=0)', () => {
    const out = _applyDiscoverV2Sort(CARDS, 'newest')
    expect(out[out.length - 1].release_key).toBe('k5')
  })
})


describe('_applyDiscoverV2Sort — title / artist', () => {
  it('sorts by title alpha case-insensitive', () => {
    expect(_applyDiscoverV2Sort(CARDS, 'title').map(c => c.release_key))
      .toEqual(['k2', 'k1', 'k3', 'k4', 'k5'])
  })
  it('sorts by artist alpha case-insensitive', () => {
    expect(_applyDiscoverV2Sort(CARDS, 'artist').map(c => c.release_key))
      .toEqual(['k5', 'k4', 'k2', 'k1', 'k3'])
  })
})


describe('_applyDiscoverV2Sort — Explore mode (50/50)', () => {
  it('interleaves non-novelty and novelty 1:1', () => {
    const out = _applyDiscoverV2Sort(CARDS, 'explore').map(c => c.release_key)
    // Other = k1, k2, k5; novelty = k3, k4
    // Round-robin (other first): k1, k3, k2, k4, k5
    expect(out).toEqual(['k1', 'k3', 'k2', 'k4', 'k5'])
  })

  it('first card is always taste-ranked when both groups are non-empty', () => {
    const out = _applyDiscoverV2Sort(CARDS, 'explore')
    expect(out[0].source.startsWith('novelty')).toBe(false)
  })

  it('falls through when only novelty cards exist', () => {
    const onlyNovelty = [
      {release_key: 'n1', source: 'novelty:style', release: {title: 'A', artist: 'X'}},
      {release_key: 'n2', source: 'novelty:label', release: {title: 'B', artist: 'Y'}},
    ]
    expect(_applyDiscoverV2Sort(onlyNovelty, 'explore').map(c => c.release_key))
      .toEqual(['n1', 'n2'])
  })

  it('falls through when no novelty cards exist', () => {
    const onlyTaste = [
      {release_key: 't1', source: 'artist', release: {title: 'A', artist: 'X'}},
      {release_key: 't2', source: 'label',  release: {title: 'B', artist: 'Y'}},
    ]
    expect(_applyDiscoverV2Sort(onlyTaste, 'explore').map(c => c.release_key))
      .toEqual(['t1', 't2'])
  })
})


describe('integration: dismissed/snoozed filter feeds into sort', () => {
  it('a dismissed card is removed before sorting (regression: filter then sort)', () => {
    const dismissedKeys = new Set(['k3'])
    const snoozedKeys = new Set()
    const filtered = CARDS.filter(c => !dismissedKeys.has(c.release_key) && !snoozedKeys.has(c.release_key))
    const out = _applyDiscoverV2Sort(filtered, 'newest').map(c => c.release_key)
    expect(out).toContain('k1')
    expect(out).not.toContain('k3')
  })
})
