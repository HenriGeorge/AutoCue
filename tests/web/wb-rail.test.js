/**
 * P2 workbench rail extras — pure helpers from docs/js/v2/workbench/rail.js.
 *
 * Only the deterministic, side-effect-free exports are unit-tested here
 * (healthLede + describeFilter). The DOM-driving init/render paths reuse legacy
 * controls via native events and are covered by the e2e selector/inventory
 * guard + a manual Chrome pass — they need the full legacy script graph + a
 * live ACBridge, which JSDOM can't faithfully stand up.
 *
 * healthLede is the G-organ template lede: NO LLM, deterministic from the
 * health summary. It must match the design-Z mockup string verbatim.
 */

import { describe, it, expect } from 'vitest'
import { healthLede, describeFilter } from '../../docs/js/v2/workbench/rail.js'

describe('healthLede (deterministic template — no LLM)', () => {
  it('matches the design-Z mockup string at 84/100 with 1 track', () => {
    expect(healthLede({ library_score: 84, no_cues: 1 }))
      .toBe('Almost gig-ready · 1 track needs cues')
  })

  it('says Gig-ready and "all tracks cued" at 90+ with zero', () => {
    expect(healthLede({ library_score: 90, no_cues: 0 }))
      .toBe('Gig-ready · all tracks cued')
    expect(healthLede({ library_score: 100, no_cues: 0 }))
      .toBe('Gig-ready · all tracks cued')
  })

  it('thresholds: >=90 Gig-ready, >=70 Almost, else Needs work', () => {
    expect(healthLede({ library_score: 70, no_cues: 5 })).toMatch(/^Almost gig-ready/)
    expect(healthLede({ library_score: 69, no_cues: 5 })).toMatch(/^Needs work/)
    expect(healthLede({ library_score: 89, no_cues: 5 })).toMatch(/^Almost gig-ready/)
    expect(healthLede({ library_score: 50, no_cues: 3 }))
      .toBe('Needs work · 3 tracks need cues')
  })

  it('pluralises track/need by count', () => {
    expect(healthLede({ library_score: 50, no_cues: 1 })).toBe('Needs work · 1 track needs cues')
    expect(healthLede({ library_score: 50, no_cues: 2 })).toBe('Needs work · 2 tracks need cues')
  })

  it('rounds a fractional score before bucketing', () => {
    expect(healthLede({ library_score: 89.6, no_cues: 0 })).toMatch(/^Gig-ready/)
    expect(healthLede({ library_score: 89.4, no_cues: 0 })).toMatch(/^Almost gig-ready/)
  })

  it('returns empty string for a null summary (pre-scan)', () => {
    expect(healthLede(null)).toBe('')
    expect(healthLede(undefined)).toBe('')
  })

  it('coerces missing fields to zero rather than throwing', () => {
    expect(healthLede({})).toBe('Needs work · all tracks cued')
  })
})

describe('describeFilter (saved-filter default name hint)', () => {
  it('joins search, crate label, and toggles without duplication', () => {
    expect(describeFilter({ search: 'kick', crate: 'phrase', phrase: true, beats: false }))
      .toBe('"kick" · phrase-ready · phrase-only')
  })

  it('omits the all-tracks crate and unset toggles', () => {
    expect(describeFilter({ search: '', crate: 'all', phrase: false, beats: false }))
      .toBe('')
  })

  it('maps crate ids to friendly labels', () => {
    expect(describeFilter({ crate: 'none' })).toBe('no cues')
    expect(describeFilter({ crate: 'cued' })).toBe('already cued')
    expect(describeFilter({ crate: 'custom-x' })).toBe('custom-x')
  })

  it('includes beat-grid when set', () => {
    expect(describeFilter({ beats: true })).toBe('beat-grid')
  })

  it('returns empty for a null filter', () => {
    expect(describeFilter(null)).toBe('')
  })
})
