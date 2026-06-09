/**
 * Tests for the Discover v2 client-side feed filters (search / styles /
 * hide-saved / hide-dismissed). Mirrors `_applyDiscoverV2Filters` in
 * docs/index.html — keep in sync.
 *
 * The IIFE-style helper lives inside index.html and isn't directly
 * importable; the project convention is to duplicate the function body
 * into the test file (see discover-v2-sort.test.js for precedent). If
 * the production logic changes, update both places.
 */

import { describe, it, expect } from 'vitest'

function _applyDiscoverV2Filters(cards, filters, s) {
  const search = (filters.search || '').trim().toLowerCase()
  const styles = filters.selectedStyles instanceof Set
    ? filters.selectedStyles
    : new Set(filters.selectedStyles || [])
  const hideSaved = !!filters.hideSaved
  const hideDismissed = filters.hideDismissed !== false
  return cards.filter((c) => {
    if (s.snoozedKeys && s.snoozedKeys.has(c.release_key)) return false
    if (hideDismissed && s.dismissedKeys && s.dismissedKeys.has(c.release_key)) return false
    if (hideSaved && s.savedKeys && s.savedKeys.has(c.release_key)) return false
    const r = c.release || {}
    if (search) {
      const hay = (
        (r.artist || '') + ' ' +
        (r.title || '') + ' ' +
        (r.album || '') + ' ' +
        (r.label || '')
      ).toLowerCase()
      if (!hay.includes(search)) return false
    }
    if (styles.size > 0) {
      const cardStyles = Array.isArray(r.styles) ? r.styles : []
      if (!cardStyles.some((st) => styles.has(String(st).toLowerCase()))) return false
    }
    return true
  })
}

const CARDS = [
  { release_key: 'k1', release: { artist: 'Drexciya', title: 'Neptune\'s Lair', label: 'Tresor', styles: ['Electro', 'Techno'] } },
  { release_key: 'k2', release: { artist: 'Madvillain', title: 'Madvillainy', label: 'Stones Throw', styles: ['Hip Hop'] } },
  { release_key: 'k3', release: { artist: 'Sun Ra', title: 'Space Is The Place', label: 'Blue Thumb', styles: ['Free Jazz', 'Experimental'] } },
  { release_key: 'k4', release: { artist: 'Aphex Twin', title: 'Selected Ambient Works 85-92', label: 'R&S', styles: ['Ambient', 'IDM'] } },
  { release_key: 'k5', release: { artist: 'Burial', title: 'Untrue', label: 'Hyperdub', styles: ['Dubstep', 'Ambient'] } },
]

function emptyState(overrides = {}) {
  return {
    savedKeys: new Set(),
    dismissedKeys: new Set(),
    snoozedKeys: new Set(),
    ...overrides,
  }
}

describe('_applyDiscoverV2Filters — search', () => {
  it('returns all cards when search is empty', () => {
    const out = _applyDiscoverV2Filters(CARDS, { search: '' }, emptyState())
    expect(out).toHaveLength(5)
  })

  it('matches against artist (case-insensitive)', () => {
    const out = _applyDiscoverV2Filters(CARDS, { search: 'aphex' }, emptyState())
    expect(out.map((c) => c.release_key)).toEqual(['k4'])
  })

  it('matches against title', () => {
    const out = _applyDiscoverV2Filters(CARDS, { search: 'untrue' }, emptyState())
    expect(out.map((c) => c.release_key)).toEqual(['k5'])
  })

  it('matches against label', () => {
    const out = _applyDiscoverV2Filters(CARDS, { search: 'tresor' }, emptyState())
    expect(out.map((c) => c.release_key)).toEqual(['k1'])
  })

  it('trims whitespace before matching', () => {
    const out = _applyDiscoverV2Filters(CARDS, { search: '  burial  ' }, emptyState())
    expect(out.map((c) => c.release_key)).toEqual(['k5'])
  })

  it('returns empty array when nothing matches', () => {
    const out = _applyDiscoverV2Filters(CARDS, { search: 'xyz-no-match-xyz' }, emptyState())
    expect(out).toHaveLength(0)
  })
})

describe('_applyDiscoverV2Filters — style chips', () => {
  it('returns all cards when no styles selected', () => {
    const out = _applyDiscoverV2Filters(CARDS, { selectedStyles: new Set() }, emptyState())
    expect(out).toHaveLength(5)
  })

  it('keeps cards matching at least one selected style (case-insensitive)', () => {
    const out = _applyDiscoverV2Filters(
      CARDS,
      { selectedStyles: new Set(['ambient']) },
      emptyState(),
    )
    expect(out.map((c) => c.release_key).sort()).toEqual(['k4', 'k5'])
  })

  it('uses OR semantics when multiple styles selected', () => {
    const out = _applyDiscoverV2Filters(
      CARDS,
      { selectedStyles: new Set(['hip hop', 'techno']) },
      emptyState(),
    )
    expect(out.map((c) => c.release_key).sort()).toEqual(['k1', 'k2'])
  })

  it('accepts Array as well as Set for selectedStyles', () => {
    const out = _applyDiscoverV2Filters(
      CARDS,
      { selectedStyles: ['ambient'] },
      emptyState(),
    )
    expect(out.map((c) => c.release_key).sort()).toEqual(['k4', 'k5'])
  })
})

describe('_applyDiscoverV2Filters — hide saved / hide dismissed / snoozed', () => {
  it('snoozed cards are ALWAYS hidden, regardless of toggles', () => {
    const s = emptyState({ snoozedKeys: new Set(['k1']) })
    const out = _applyDiscoverV2Filters(CARDS, { hideDismissed: false }, s)
    expect(out.map((c) => c.release_key)).not.toContain('k1')
  })

  it('hideDismissed defaults to true when omitted', () => {
    const s = emptyState({ dismissedKeys: new Set(['k2']) })
    const out = _applyDiscoverV2Filters(CARDS, {}, s)
    expect(out.map((c) => c.release_key)).not.toContain('k2')
  })

  it('hideDismissed=false shows dismissed cards', () => {
    const s = emptyState({ dismissedKeys: new Set(['k2']) })
    const out = _applyDiscoverV2Filters(CARDS, { hideDismissed: false }, s)
    expect(out.map((c) => c.release_key)).toContain('k2')
  })

  it('hideSaved=true hides saved cards', () => {
    const s = emptyState({ savedKeys: new Set(['k3']) })
    const out = _applyDiscoverV2Filters(CARDS, { hideSaved: true }, s)
    expect(out.map((c) => c.release_key)).not.toContain('k3')
  })

  it('hideSaved=false (default) keeps saved cards visible', () => {
    const s = emptyState({ savedKeys: new Set(['k3']) })
    const out = _applyDiscoverV2Filters(CARDS, {}, s)
    expect(out.map((c) => c.release_key)).toContain('k3')
  })
})

describe('_applyDiscoverV2Filters — composition', () => {
  it('combines search + style + hide-saved with AND semantics', () => {
    const s = emptyState({ savedKeys: new Set(['k4']) })
    const out = _applyDiscoverV2Filters(
      CARDS,
      {
        search: 'ambient',
        selectedStyles: new Set(['ambient']),
        hideSaved: true,
      },
      s,
    )
    // k4 has "Ambient" style + "Selected Ambient Works" title, but it's
    // saved → must be hidden. k5 has "Ambient" style and "untrue" title,
    // not matching "ambient" search. Result: empty.
    expect(out).toHaveLength(0)
  })

  it('combines search + style (no hide) keeps overlapping cards', () => {
    const out = _applyDiscoverV2Filters(
      CARDS,
      { search: 'selected', selectedStyles: new Set(['idm']) },
      emptyState(),
    )
    expect(out.map((c) => c.release_key)).toEqual(['k4'])
  })

  it('does not mutate inputs', () => {
    const styles = new Set(['ambient'])
    const filters = { search: 'untrue', selectedStyles: styles, hideSaved: false }
    const s = emptyState()
    _applyDiscoverV2Filters(CARDS, filters, s)
    expect(styles.size).toBe(1)
    expect(filters.search).toBe('untrue')
  })
})
