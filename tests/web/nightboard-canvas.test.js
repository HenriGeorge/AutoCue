// @vitest-environment jsdom
/**
 * P4 Nightboard — canvas pure helpers + DOM render.
 * R5 (arc NaN-guard), R6 (joint band thresholds), R4 (tiles render with mono
 * BPM + relaxed tag; N tiles → N-1 joints).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import * as model from '../../docs/js/v2/nightboard/set-model.js'
import { jointBand, zoneFractions, buildArcPath, render, JOINT_BANDS } from '../../docs/js/v2/nightboard/canvas.js'

const T = (id, cat, score = 80) => ({ track_id: id, title: `T${id}`, artist: `A${id}`, bpm: 124, key: '8A', category: cat, transition_score: score, relaxed: false })

beforeEach(() => {
  model._reset()
  document.body.innerHTML = ''
  delete window.ACBridge
})

describe('jointBand thresholds (R6)', () => {
  it('maps score to band by the absolute cutoffs', () => {
    expect(jointBand(90)).toBe('good')
    expect(jointBand(JOINT_BANDS.good)).toBe('good')   // 85 inclusive
    expect(jointBand(84)).toBe('ok')
    expect(jointBand(JOINT_BANDS.ok)).toBe('ok')       // 70 inclusive
    expect(jointBand(69)).toBe('bad')
    expect(jointBand(null)).toBe('na')
    expect(jointBand(undefined)).toBe('na')
    expect(jointBand(NaN)).toBe('na')
  })
})

describe('zoneFractions', () => {
  it('sums to 1 and maps the four categories to their buckets', () => {
    const fr = zoneFractions([T(1, 'warmup'), T(2, 'build'), T(3, 'peak'), T(4, 'closing')], null)
    expect(fr.warmup + fr.build + fr.peak + fr.closing).toBeCloseTo(1, 6)
    expect(fr.warmup).toBeCloseTo(0.25, 6)
  })
  it('after_hours → closing, unknown → build', () => {
    const fr = zoneFractions([T(1, 'after_hours'), T(2, 'unknown')], null)
    expect(fr.closing).toBeCloseTo(0.5, 6)
    expect(fr.build).toBeCloseTo(0.5, 6)
  })
  it('weights by duration when provided', () => {
    const fr = zoneFractions([T(1, 'warmup'), T(2, 'peak')], new Map([[1, 300], [2, 100]]))
    expect(fr.warmup).toBeCloseTo(0.75, 6)
    expect(fr.peak).toBeCloseTo(0.25, 6)
  })
  it('empty set → all zero', () => {
    const fr = zoneFractions([], null)
    expect(fr.warmup).toBe(0)
    expect(fr.peak).toBe(0)
  })
})

describe('buildArcPath NaN-guard (R5)', () => {
  it('no energy → flat fallback, valid path, no NaN', () => {
    const { line, area } = buildArcPath([T(1, 'build'), T(2, 'peak')], null)
    expect(line).toMatch(/^M/)
    expect(area).toMatch(/Z$/)
    expect(line).not.toMatch(/NaN/)
    expect(area).not.toMatch(/NaN/)
  })
  it('empty set → empty path', () => {
    expect(buildArcPath([], null)).toEqual({ line: '', area: '' })
  })
  it('stitches duration-weighted points when curves are loaded', async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ energy: [0.2, 0.8, 0.5] }) }))
    await model.loadEnergyCurves([1, 2])
    const { line } = buildArcPath([T(1, 'build'), T(2, 'peak')], new Map([[1, 100], [2, 100]]), 1000, 84)
    expect((line.match(/[ML]/g) || []).length).toBe(6) // 3 samples × 2 tracks
    expect(line).not.toMatch(/NaN/)
  })
  it('clamps non-finite samples (no NaN / Infinity in d)', async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ energy: [NaN, 0.5, Infinity] }) }))
    await model.loadEnergyCurves([1])
    const { line, area } = buildArcPath([T(1, 'build')], null)
    expect(line).not.toMatch(/NaN|Infinity/)
    expect(area).not.toMatch(/NaN|Infinity/)
  })
})

describe('render() DOM (R4)', () => {
  it('paints N tiles + (N-1) joints with mono BPM, relaxed tag, banded joint', async () => {
    document.body.innerHTML = '<div id="nb-stats"></div><div id="nb-zones"></div><svg id="nb-arc"></svg><div id="nb-timeline"></div>'
    // parsed tracks use camelCase (totalTime / existingHotCues), as ACBridge.tracks() returns
    window.ACBridge = { tracks: () => [
      { id: 1, totalTime: 300, existingHotCues: 4 },
      { id: 2, totalTime: 240, existingHotCues: 0 },
    ] }
    globalThis.fetch = vi.fn(async () => ({ ok: true, json: async () => ({ tracks: [
      { track_id: 1, title: 'A', artist: 'X', bpm: 124.5, key: '8A', category: 'warmup', transition_score: 0, relaxed: false },
      { track_id: 2, title: 'B', artist: 'Y', bpm: 126.0, key: '9A', category: 'peak', transition_score: 88, relaxed: true },
    ] }) }))
    await model.buildSet({})
    render()

    expect(document.querySelectorAll('.nb-tile')).toHaveLength(2)
    expect(document.querySelectorAll('.nb-joint')).toHaveLength(1)            // N-1
    expect(document.querySelector('.nb-chip-bpm').textContent).toBe('124.5')  // mono BPM
    expect(document.querySelector('.nb-chip-relaxed')).toBeTruthy()           // tile 2 relaxed
    expect(document.querySelector('.nb-joint-good')).toBeTruthy()             // score 88 → good
    expect(document.querySelector('.nb-cue-ok')).toBeTruthy()                 // tile 1 has cues
    expect(document.querySelector('.nb-cue-warn')).toBeTruthy()               // tile 2 none
    expect(document.getElementById('nb-stats').textContent).toContain('tracks')
    expect(document.querySelectorAll('.nb-zone').length).toBeGreaterThan(0)
  })
})
