/**
 * Existing hot-cue ticks overlaid on the phrase strip (Skipped cards).
 *
 * On a Skipped card the #163 existing-cue chips and the phrase strip can't
 * both fit the fixed 160px card (TASK-033), so the cue positions are merged
 * INTO the strip as tick marks: a vertical line at left = start/totalTime,
 * a slot-letter cap, and the name/time in the title (hover). The positioning
 * + slot-letter + in-bounds logic lives in `buildPhraseStrip` in
 * docs/index.html. Vendored here — keep in sync.
 */

import { describe, it, expect } from 'vitest'

// Mirror of the cue-tick loop in buildPhraseStrip(phrases, totalTime, cueTicks).
// Returns one descriptor per RENDERED tick (out-of-bounds cues are dropped).
function buildCueTicks(cueTicks, totalTime) {
  if (!Array.isArray(cueTicks)) return []
  const out = []
  for (const ec of cueTicks) {
    const startSec = ec.start || 0
    if (startSec < 0 || startSec > totalTime) continue
    const leftPct = (startSec / totalTime) * 100
    const slotLetter =
      ec.num === -1 ? 'M' : ec.num >= 0 && ec.num <= 7 ? String.fromCharCode(65 + ec.num) : '?'
    const title = `${slotLetter === 'M' ? 'Memory cue' : 'Slot ' + slotLetter}${
      ec.name ? ' — ' + ec.name : ''
    }`
    out.push({ leftPct, slotLetter, title })
  }
  return out
}

describe('buildPhraseStrip cue ticks', () => {
  it('positions a tick at start/totalTime as a percentage', () => {
    const ticks = buildCueTicks([{ num: 0, start: 30 }], 120)
    expect(ticks).toHaveLength(1)
    expect(ticks[0].leftPct).toBeCloseTo(25)
  })

  it('maps slot numbers 0-7 to letters A-H', () => {
    const ticks = buildCueTicks(
      Array.from({ length: 8 }, (_, n) => ({ num: n, start: 1 })),
      100,
    )
    expect(ticks.map((t) => t.slotLetter)).toEqual(['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'])
  })

  it('renders the memory cue (num -1) as M', () => {
    expect(buildCueTicks([{ num: -1, start: 5 }], 100)[0].slotLetter).toBe('M')
  })

  it('uses ? for an out-of-range slot number', () => {
    expect(buildCueTicks([{ num: 12, start: 5 }], 100)[0].slotLetter).toBe('?')
  })

  it('drops cues beyond the track duration (no overflow past 100%)', () => {
    const ticks = buildCueTicks([{ num: 0, start: 50 }, { num: 1, start: 150 }], 100)
    expect(ticks).toHaveLength(1)
    expect(ticks[0].leftPct).toBeCloseTo(50)
  })

  it('drops cues with a negative start', () => {
    expect(buildCueTicks([{ num: 0, start: -3 }], 100)).toHaveLength(0)
  })

  it('treats a missing start as 0 (mix-in at the head)', () => {
    expect(buildCueTicks([{ num: 0 }], 100)[0].leftPct).toBe(0)
  })

  it('puts the cue name + slot in the hover title', () => {
    const t = buildCueTicks([{ num: 0, start: 6, name: 'Drop 1 (Mix In)' }], 120)[0]
    expect(t.title).toBe('Slot A — Drop 1 (Mix In)')
  })

  it('omits the dash when a cue has no name', () => {
    expect(buildCueTicks([{ num: 2, start: 6 }], 120)[0].title).toBe('Slot C')
  })

  it('returns nothing when cueTicks is not an array (regular non-skip strip)', () => {
    expect(buildCueTicks(undefined, 100)).toEqual([])
    expect(buildCueTicks(null, 100)).toEqual([])
  })
})
