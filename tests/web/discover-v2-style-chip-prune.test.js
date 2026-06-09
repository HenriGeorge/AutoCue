/**
 * Tests for the ghost-filter prune in _renderDiscoverStyleChips
 * (docs/index.html). Selected styles whose key has fully disappeared
 * from the feed must be pruned from _discoverFilters.selectedStyles AND
 * localStorage on the next render — otherwise they silently filter every
 * subsequent feed to an empty grid with no visible chip to un-toggle.
 *
 * Logic mirrors docs/index.html. Keep in sync.
 */

import { describe, it, expect, beforeEach } from 'vitest'

// Mirror of the production pruning logic — same predicate, just hoisted
// out of the renderer so the test doesn't need to spin up a DOM render.
function pruneGhostStyles(cards, selectedStyles) {
  const allStyles = new Set()
  for (const c of cards) {
    const styles = Array.isArray(c.release?.styles) ? c.release.styles : []
    for (const st of styles) {
      const key = String(st).toLowerCase()
      if (key) allStyles.add(key)
    }
  }
  let pruned = []
  for (const key of Array.from(selectedStyles)) {
    if (!allStyles.has(key)) {
      selectedStyles.delete(key)
      pruned.push(key)
    }
  }
  return pruned
}

describe('style-chip ghost-filter prune', () => {
  let selectedStyles
  beforeEach(() => {
    selectedStyles = new Set()
  })

  it('keeps a selection that still appears in the current feed', () => {
    selectedStyles.add('ambient')
    const cards = [{ release: { styles: ['Ambient', 'IDM'] } }]
    const pruned = pruneGhostStyles(cards, selectedStyles)
    expect(pruned).toEqual([])
    expect(selectedStyles.has('ambient')).toBe(true)
  })

  it('prunes a selection that has disappeared from the feed', () => {
    selectedStyles.add('bossa nova')
    const cards = [{ release: { styles: ['Techno'] } }]
    const pruned = pruneGhostStyles(cards, selectedStyles)
    expect(pruned).toEqual(['bossa nova'])
    expect(selectedStyles.has('bossa nova')).toBe(false)
  })

  it('keeps selections that appear past the top-16 cutoff (no false prune)', () => {
    // The render layer caps the visible CHIPS at 16 by count, but pruning
    // is by presence in any card.styles — so a style buried in card #17
    // should NOT be pruned.
    selectedStyles.add('darkwave')
    const cards = []
    for (let i = 0; i < 16; i++) cards.push({ release: { styles: ['Techno'] } })
    cards.push({ release: { styles: ['Darkwave'] } })
    const pruned = pruneGhostStyles(cards, selectedStyles)
    expect(pruned).toEqual([])
    expect(selectedStyles.has('darkwave')).toBe(true)
  })

  it('prunes when the feed is empty', () => {
    selectedStyles.add('jazz')
    selectedStyles.add('soul')
    const pruned = pruneGhostStyles([], selectedStyles)
    expect(pruned.sort()).toEqual(['jazz', 'soul'])
    expect(selectedStyles.size).toBe(0)
  })

  it('is case-insensitive — capitalization differences don\'t cause ghosts', () => {
    // Store keys lowercased (matches selectedStyles convention). Feed
    // provides title-case styles ("Hip Hop"). Should match.
    selectedStyles.add('hip hop')
    const cards = [{ release: { styles: ['Hip Hop', 'Funk'] } }]
    const pruned = pruneGhostStyles(cards, selectedStyles)
    expect(pruned).toEqual([])
    expect(selectedStyles.has('hip hop')).toBe(true)
  })

  it('handles cards with no styles array gracefully', () => {
    selectedStyles.add('ambient')
    const cards = [{ release: {} }, { release: { styles: ['Ambient'] } }]
    const pruned = pruneGhostStyles(cards, selectedStyles)
    expect(pruned).toEqual([])
  })

  it('returns multiple pruned keys when several have vanished', () => {
    selectedStyles.add('a')
    selectedStyles.add('b')
    selectedStyles.add('c')
    const cards = [{ release: { styles: ['B'] } }]
    const pruned = pruneGhostStyles(cards, selectedStyles)
    expect(pruned.sort()).toEqual(['a', 'c'])
    expect(Array.from(selectedStyles)).toEqual(['b'])
  })
})
