/**
 * P1 T4 — fuzzy matcher.
 */
import { describe, it, expect } from 'vitest'
import { fuzzyScore, rank } from '../../docs/js/v2/fuzzy.js'

describe('fuzzyScore', () => {
  it('empty query matches everything with score 0', () => {
    expect(fuzzyScore('', 'anything')).toBe(0)
  })
  it('returns -1 when not all query chars are present', () => {
    expect(fuzzyScore('xyz', 'Find duplicates')).toBe(-1)
  })
  it('matches a subsequence', () => {
    expect(fuzzyScore('fd', 'Find duplicates')).toBeGreaterThan(0)
  })
  it('ranks word-boundary/initials above incidental subsequence', () => {
    // "fd" hits the F of Find and D of duplicates (two boundaries)
    const boundary = fuzzyScore('fd', 'Find duplicates')
    const incidental = fuzzyScore('fd', 'shuffled') // f...d inside one word
    expect(boundary).toBeGreaterThan(incidental)
  })
  it('prefix beats mid-word match', () => {
    expect(fuzzyScore('app', 'Apply')).toBeGreaterThan(fuzzyScore('app', 'Snapping'))
  })
})

describe('rank', () => {
  const items = ['Find duplicates', 'Apply to Rekordbox', 'Build a set', 'Preview cues']
  const textOf = (x) => x

  it('drops non-matches and orders by score', () => {
    const r = rank('set', items, textOf)
    expect(r).toContain('Build a set')
    expect(r).not.toContain('Apply to Rekordbox')
  })
  it('empty query returns all in stable input order', () => {
    expect(rank('', items, textOf)).toEqual(items)
  })
  it('is stable for equal scores', () => {
    const dup = ['ab', 'ab', 'ab']
    expect(rank('a', dup, textOf)).toEqual(dup)
  })
})
