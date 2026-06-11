/**
 * Lazy viewport-driven phrase-cue loading (replaces the eager
 * full-library "Computing phrase cues N/M" pass).
 *
 * The decision of which visible cards need a phrase fetch lives in
 * `_collectPhraseLazyIds` in docs/index.html. It must queue a track only
 * when: phrase mode + local + the track hasPhrase + no cached cues + not
 * already in flight. Vendored here — keep in sync.
 */

import { describe, it, expect, beforeEach } from 'vitest'

// Mirror of _collectPhraseLazyIds, parameterised on the module state it
// reads (instead of globals) so it's unit-testable.
function collectPhraseLazyIds(visibleCards, ctx) {
  const { analysisMode, localMode, tracksById, phraseCueState, inFlight } = ctx
  const out = []
  if (analysisMode !== 'phrase' || !localMode) return out
  for (const card of visibleCards) {
    const tid = card.trackId
    if (!tid) continue
    const track = tracksById.get(tid)
    if (!track || !track.hasPhrase) continue
    if (phraseCueState[tid] !== undefined) continue
    if (inFlight.has(tid)) continue
    out.push(tid)
  }
  return out
}

function ctx(overrides = {}) {
  return {
    analysisMode: 'phrase',
    localMode: true,
    tracksById: new Map([
      ['1', { id: 1, hasPhrase: true }],
      ['2', { id: 2, hasPhrase: true }],
      ['3', { id: 3, hasPhrase: false }],   // no phrase data on server
    ]),
    phraseCueState: {},
    inFlight: new Set(),
    ...overrides,
  }
}

const cards = (...ids) => ids.map((trackId) => ({ trackId: String(trackId) }))

describe('_collectPhraseLazyIds', () => {
  it('queues visible phrase-capable tracks that are uncached + not in flight', () => {
    expect(collectPhraseLazyIds(cards(1, 2), ctx())).toEqual(['1', '2'])
  })

  it('skips tracks the server has no phrase data for (hasPhrase=false)', () => {
    expect(collectPhraseLazyIds(cards(1, 3), ctx())).toEqual(['1'])
  })

  it('skips tracks already cached (incl. empty array)', () => {
    const c = ctx({ phraseCueState: { '1': [{ slot: 0 }], '2': [] } })
    expect(collectPhraseLazyIds(cards(1, 2), c)).toEqual([])
  })

  it('skips tracks with a fetch already in flight', () => {
    const c = ctx({ inFlight: new Set(['1']) })
    expect(collectPhraseLazyIds(cards(1, 2), c)).toEqual(['2'])
  })

  it('returns nothing in bar mode (lazy phrase load is phrase-mode-only)', () => {
    expect(collectPhraseLazyIds(cards(1, 2), ctx({ analysisMode: 'bar' }))).toEqual([])
  })

  it('returns nothing in Pages (non-local) mode', () => {
    expect(collectPhraseLazyIds(cards(1, 2), ctx({ localMode: false }))).toEqual([])
  })

  it('ignores cards with no track-id', () => {
    expect(collectPhraseLazyIds([{ trackId: '' }, { trackId: '1' }], ctx())).toEqual(['1'])
  })

  it('only queues what is visible — never the whole library', () => {
    // The whole point of the change: a 2789-track library, 16 visible →
    // at most 16 queued, not 2789.
    const big = new Map()
    for (let i = 1; i <= 2789; i++) big.set(String(i), { id: i, hasPhrase: true })
    const c = ctx({ tracksById: big })
    const visible = cards(...Array.from({ length: 16 }, (_, i) => i + 1))
    expect(collectPhraseLazyIds(visible, c)).toHaveLength(16)
  })
})
