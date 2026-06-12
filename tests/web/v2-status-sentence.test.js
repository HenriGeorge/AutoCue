/**
 * P1 T3 — status sentence: pure deriveFacts() + markup contract.
 */
import { describe, it, expect } from 'vitest'
import { deriveFacts } from '../../docs/js/v2/status-sentence.js'
import { loadAppHtml } from './_source.js'

describe('deriveFacts()', () => {
  it('counts tracks with no hot cues', () => {
    const tracks = [
      { existingHotCues: 0 }, { existingHotCues: 3 },
      { existingHotCues: 0 }, { existingHotCues: 8 },
    ]
    const [needcues] = deriveFacts({ tracks })
    expect(needcues).toEqual({ id: 'needcues', visible: true, count: 2 })
  })

  it('keeps the needcues fact visible at 0 once tracks are loaded', () => {
    const [needcues] = deriveFacts({ tracks: [{ existingHotCues: 4 }] })
    expect(needcues.visible).toBe(true)
    expect(needcues.count).toBe(0)
  })

  it('hides needcues when no tracks are loaded (or arg omitted)', () => {
    expect(deriveFacts({ tracks: [] })[0].visible).toBe(false)
    expect(deriveFacts({})[0].visible).toBe(false)
    expect(deriveFacts()[0].visible).toBe(false)
  })

  it('hides health until a summary exists', () => {
    const [, health] = deriveFacts({ tracks: [{ existingHotCues: 0 }] })
    expect(health.visible).toBe(false)
    expect(health.score).toBe(null)
  })

  it('rounds library_score', () => {
    const [, health] = deriveFacts({
      tracks: [{ existingHotCues: 0 }],
      healthSummary: { library_score: 77.6 },
    })
    expect(health).toEqual({ id: 'health', visible: true, score: 78 })
  })

  it('treats non-numeric existingHotCues as cued (not need-cues)', () => {
    const [needcues] = deriveFacts({ tracks: [{ existingHotCues: undefined }, { existingHotCues: 0 }] })
    expect(needcues.count).toBe(1)
  })
})

describe('status sentence markup', () => {
  const html = loadAppHtml()

  it('the four core facts are buttons', () => {
    for (const id of ['status-db', 'status-count', 'status-scan', 'status-rb']) {
      const re = new RegExp(`<button[^>]*id="${id}"`)
      expect(re.test(html), `${id} should be a <button>`).toBe(true)
    }
  })

  it('the two new facts exist and start hidden', () => {
    for (const id of ['status-needcues', 'status-health']) {
      const re = new RegExp(`<button[^>]*id="${id}"[^>]*hidden`)
      expect(re.test(html), `${id} should be a hidden <button>`).toBe(true)
    }
  })

  it('warmup chip stays a span (progress, not an action)', () => {
    expect(/<span[^>]*id="status-warmup"/.test(html)).toBe(true)
  })
})
