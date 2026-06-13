// @vitest-environment jsdom
/**
 * P4 Nightboard — set-model (pure state + fetch) and mode (open/close) unit tests.
 * T2: build maps SetBuilderRequest, terminated_reason surfaces honestly (R3),
 * swap/insert mutate order, rescoreJoints touches only the ≤2 affected joints (R7),
 * and the mode toggles body.nb-active + #nb-canvas[hidden] (local-mode gated, R1).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import * as model from '../../docs/js/v2/nightboard/set-model.js'
import { openNightboard, closeNightboard, isNightboardOpen } from '../../docs/js/v2/nightboard/mode.js'

function track(id, score) {
  return { track_id: id, title: `T${id}`, artist: `A${id}`, bpm: 120 + id, key: '8A', category: 'peak', transition_score: score, relaxed: false }
}

function stubFetch(handler) {
  globalThis.fetch = vi.fn(handler)
  return globalThis.fetch
}

beforeEach(() => {
  model._reset()
  document.body.className = ''
  document.body.innerHTML = ''
})

describe('set-model.buildSet', () => {
  it('maps canvas inputs to a SetBuilderRequest and parses the response', async () => {
    const f = stubFetch(async () => ({
      ok: true,
      json: async () => ({ tracks: [track(1, 0), track(2, 88)], total_tracks: 2, estimated_duration_minutes: 12, terminated_reason: 'target_duration_reached' }),
    }))
    const res = await model.buildSet({ start_bpm: '124', end_bpm: '130', duration_minutes: '90', energy_mode: 'drop', anchor_track_ids: [7, 9] })

    expect(f).toHaveBeenCalledOnce()
    const [url, opts] = f.mock.calls[0]
    expect(url).toBe('/api/setbuilder')
    const body = JSON.parse(opts.body)
    expect(body).toEqual({ start_bpm: 124, end_bpm: 130, duration_minutes: 90, energy_mode: 'drop', anchor_track_ids: [7, 9] })
    expect(res.tracks).toHaveLength(2)
    expect(model.getSet()).toHaveLength(2)
    expect(res.totalTracks).toBe(2)
  })

  it('omits anchor_track_ids when empty and defaults numbers', async () => {
    const f = stubFetch(async () => ({ ok: true, json: async () => ({ tracks: [] }) }))
    await model.buildSet({ energy_mode: 'build', anchor_track_ids: [] })
    const body = JSON.parse(f.mock.calls[0][1].body)
    expect(body.anchor_track_ids).toBeUndefined()
    expect(body.start_bpm).toBe(110)
    expect(body.end_bpm).toBe(135)
    expect(body.duration_minutes).toBe(60)
  })

  it('surfaces terminated_reason honestly (R3) and throws on !ok', async () => {
    stubFetch(async () => ({ ok: true, json: async () => ({ tracks: [track(1, 0)], terminated_reason: 'safety_cap_hit' }) }))
    const res = await model.buildSet({})
    expect(res.terminatedReason).toBe('safety_cap_hit')
    expect(model.terminatedReason()).toBe('safety_cap_hit')

    stubFetch(async () => ({ ok: false, status: 422, statusText: 'Unprocessable', json: async () => ({ detail: 'bad bpm range' }) }))
    await expect(model.buildSet({})).rejects.toThrow('bad bpm range')
  })
})

describe('set-model mutators + rescoreJoints (R7)', () => {
  async function seed(n) {
    stubFetch(async () => ({ ok: true, json: async () => ({ tracks: Array.from({ length: n }, (_, i) => track(i + 1, i === 0 ? 0 : 80)) }) }))
    await model.buildSet({})
  }

  it('swapAt replaces the slot; insertAfter grows the order', async () => {
    await seed(3)
    expect(model.swapAt(1, track(99, 90))).toBe(true)
    expect(model.getSet()[1].track_id).toBe(99)
    expect(model.insertAfter(1, track(50, 70))).toBe(true)
    expect(model.getSet().map((t) => t.track_id)).toEqual([1, 99, 50, 3])
    expect(model.swapAt(99, track(1, 1))).toBe(false) // out of range
  })

  it('rescoreJoints(idx) re-scores ONLY the <=2 joints touching the slot', async () => {
    await seed(4) // tiles 0..3 → joints 0,1,2
    const calls = []
    stubFetch(async (url, opts) => {
      calls.push(JSON.parse(opts.body))
      return { ok: true, json: async () => ({ overall: 95 }) }
    })
    model.swapAt(1, track(99, 0))
    const updated = await model.rescoreJoints(1)

    // joints 0 (tiles 0-1) and 1 (tiles 1-2) only — NOT joint 2.
    expect(calls).toHaveLength(2)
    expect(Object.keys(updated).sort()).toEqual(['0', '1'])
    // fresh overall written onto the incoming track's transition_score
    expect(model.getSet()[1].transition_score).toBe(95)
    expect(model.getSet()[2].transition_score).toBe(95)
    expect(model.getSet()[3].transition_score).toBe(80) // untouched
  })

  it('rescoreJoints at the edges touches a single joint', async () => {
    await seed(4)
    const calls = []
    stubFetch(async (url, opts) => { calls.push(JSON.parse(opts.body)); return { ok: true, json: async () => ({ overall: 70 }) } })
    await model.rescoreJoints(0) // first slot → only joint 0
    expect(calls).toHaveLength(1)
  })
})

describe('mode open/close (R1 local-mode gate)', () => {
  function mountCanvas() {
    const sec = document.createElement('section')
    sec.id = 'nb-canvas'
    sec.setAttribute('hidden', '')
    document.body.appendChild(sec)
    const btn = document.createElement('button')
    btn.id = 'nb-open-btn'
    document.body.appendChild(btn)
  }

  it('opens only in local mode; toggles body.nb-active + canvas hidden', () => {
    mountCanvas()
    window.ACBridge = { isLocalMode: () => true, renderTracks: vi.fn() }
    window.AC2 = { workbench: { setWorkbench: vi.fn() } }

    openNightboard()
    expect(isNightboardOpen()).toBe(true)
    expect(document.body.classList.contains('nb-active')).toBe(true)
    expect(document.getElementById('nb-canvas').hasAttribute('hidden')).toBe(false)
    expect(window.AC2.workbench.setWorkbench).toHaveBeenCalledWith(true)

    closeNightboard()
    expect(isNightboardOpen()).toBe(false)
    expect(document.body.classList.contains('nb-active')).toBe(false)
    expect(document.getElementById('nb-canvas').hasAttribute('hidden')).toBe(true)
    expect(window.ACBridge.renderTracks).toHaveBeenCalled()
  })

  it('no-ops outside local mode (R1 — local-mode only)', () => {
    mountCanvas()
    window.ACBridge = { isLocalMode: () => false, renderTracks: vi.fn() }
    window.AC2 = { workbench: { setWorkbench: vi.fn() } }
    openNightboard()
    expect(isNightboardOpen()).toBe(false)
    expect(document.body.classList.contains('nb-active')).toBe(false)
  })
})
