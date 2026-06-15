// @vitest-environment jsdom
/**
 * P4 Nightboard — joint popover + in-place swap (R6/R7).
 * Opening a joint shows the pair, the 3 explanation reasons, <=2 alternatives
 * and a tip; "Swap in" replaces the incoming track and re-scores its joints
 * without a full rebuild.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import * as model from '../../docs/js/v2/nightboard/set-model.js'
import { render } from '../../docs/js/v2/nightboard/canvas.js'
import { open, close, isOpen } from '../../docs/js/v2/nightboard/joint-popover.js'

const T = (id, cat, score) => ({ track_id: id, title: `T${id}`, artist: `A${id}`, bpm: 124, key: '8A', category: cat, transition_score: score, relaxed: false })
const json = (o) => ({ ok: true, json: async () => o })

function mockFetch() {
  return vi.fn(async (url, opts) => {
    if (url === '/api/setbuilder') return json({ tracks: [T(1, 'warmup', 0), T(2, 'build', 80), T(3, 'peak', 82)] })
    if (url === '/api/transitions/score') return json({ overall: 90, explanation: ['Tempo aligns within 2 BPM', 'Keys are compatible (8A→8A)', 'Energy rises into the next'] })
    if (url.startsWith('/api/setbuilder/alternatives')) return json({ alternatives: [{ track_id: 99, title: 'Alt Track', artist: 'AltArtist', bpm: 125, key: '9A', score: 88, from_prev: 88, to_next: 80 }] })
    if (/\/api\/tracks\/\d+\/energy/.test(url)) return json({ energy: [0.3, 0.6, 0.4] })
    return json({})
  })
}

beforeEach(() => {
  model._reset()
  close()
  document.body.innerHTML = '<div id="nb-stats"></div><div id="nb-zones"></div><svg id="nb-arc"></svg><div id="nb-timeline"></div>'
  window.ACBridge = { tracks: () => [] }
  globalThis.fetch = mockFetch()
})

describe('joint popover (R6)', () => {
  it('opens with the pair, 3 explanation reasons, an alternative, and a tip', async () => {
    await model.buildSet({})
    render()
    expect(document.querySelectorAll('.nb-joint')).toHaveLength(2)

    await open(0) // joint between tile 0 and 1
    const po = document.querySelector('.nb-popover')
    expect(po).toBeTruthy()
    expect(isOpen()).toBe(true)
    expect(po.querySelector('.nb-po-pair').textContent).toContain('T1')
    expect(po.querySelector('.nb-po-pair').textContent).toContain('T2')
    expect(po.querySelectorAll('.nb-po-reasons li')).toHaveLength(3)
    expect(po.querySelector('.nb-po-reasons li').textContent).toBe('Tempo aligns within 2 BPM')
    expect(po.querySelectorAll('.nb-swap')).toHaveLength(1)
    expect(po.querySelector('.nb-po-tip')).toBeTruthy()
    // joint marked open
    expect(document.querySelector('.nb-joint[data-joint="0"]').classList.contains('nb-joint-open')).toBe(true)
  })

  it('is an accessible dialog — aria-modal set and focus moves into the popover', async () => {
    await model.buildSet({})
    render()
    await open(0)
    const po = document.querySelector('.nb-popover')
    expect(po.getAttribute('role')).toBe('dialog')
    expect(po.getAttribute('aria-modal')).toBe('true')
    expect(po.getAttribute('aria-label')).toBeTruthy()
    expect(document.activeElement).toBe(po) // focus moved into the dialog, not left on the joint
  })

  it('closes instead of mis-positioning when the joint vanishes during the fetch (re-render safety)', async () => {
    await model.buildSet({})
    render()
    // simulate a canvas repaint dropping the joint node mid-fetch (the bug: the
    // captured node detaches → zero rect → popover flung to the viewport corner)
    globalThis.fetch = vi.fn(async (url) => {
      if (url === '/api/transitions/score') {
        document.querySelector('.nb-joint[data-joint="1"]')?.remove()
        return json({ overall: 90, explanation: [] })
      }
      if (url.startsWith('/api/setbuilder/alternatives')) return json({ alternatives: [] })
      return json({})
    })
    await open(1)
    expect(isOpen()).toBe(false)
    expect(document.querySelector('.nb-popover')).toBeNull()
  })

  it('re-clicking the same joint toggles it closed', async () => {
    await model.buildSet({})
    render()
    await open(1)
    expect(isOpen()).toBe(true)
    await open(1)
    expect(isOpen()).toBe(false)
    expect(document.querySelector('.nb-popover')).toBeNull()
  })

  it('Escape closes the popover', async () => {
    await model.buildSet({})
    render()
    await open(0)
    expect(isOpen()).toBe(true)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(isOpen()).toBe(false)
  })
})

describe('Swap in (R7)', () => {
  it('replaces the incoming track and closes the popover', async () => {
    await model.buildSet({})
    render()
    await open(0) // incoming track = slot 1 (tile index 1)
    expect(model.getSet()[1].track_id).toBe(2)

    document.querySelector('.nb-swap').click()
    // swapAt + close run synchronously before the rescore await
    expect(model.getSet()[1].track_id).toBe(99)
    expect(isOpen()).toBe(false)
    // let the async rescore + energy reload settle without throwing
    await new Promise((r) => setTimeout(r, 0))
    expect(model.getSet()[1].transition_score).toBe(90) // re-scored from /api/transitions/score
  })
})
