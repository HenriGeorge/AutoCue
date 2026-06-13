// @vitest-environment jsdom
/**
 * P4 Nightboard — gravity tray + tile-focus inspector (R8/R9).
 * renderTray builds candidate cards from /api/setbuilder/alternatives; Add
 * inserts after the anchor; focusTile marks the tile active and re-hosts the
 * existing P2 inspector (mode 'track').
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import * as model from '../../docs/js/v2/nightboard/set-model.js'
import { render } from '../../docs/js/v2/nightboard/canvas.js'
import { initTray, renderTray, focusTile, clearFocus } from '../../docs/js/v2/nightboard/tray.js'

const T = (id, cat, score) => ({ track_id: id, title: `T${id}`, artist: `A${id}`, bpm: 124, key: '8A', category: cat, transition_score: score, relaxed: false })
const json = (o) => ({ ok: true, json: async () => o })

function mockFetch() {
  return vi.fn(async (url) => {
    if (url === '/api/setbuilder') return json({ tracks: [T(1, 'warmup', 0), T(2, 'build', 80), T(3, 'peak', 82)] })
    if (url === '/api/transitions/score') return json({ overall: 91, explanation: [] })
    if (url.startsWith('/api/setbuilder/alternatives')) return json({ alternatives: [
      { track_id: 50, title: 'Cand A', artist: 'CA', bpm: 126, key: '9A', score: 90 },
      { track_id: 51, title: 'Cand B', artist: 'CB', bpm: 127, key: '9A', score: 84 },
    ] })
    if (/\/api\/tracks\/\d+\/energy/.test(url)) return json({ energy: [0.3, 0.5] })
    return json({})
  })
}

beforeEach(() => {
  model._reset()
  document.body.className = ''
  document.body.innerHTML = `
    <div id="nb-stats"></div><div id="nb-zones"></div><svg id="nb-arc"></svg>
    <div id="nb-timeline"></div>
    <div id="nb-tray" hidden>
      <div class="nb-tray-head"><span id="nb-tray-context"></span><button id="nb-tray-toggle">Hide</button></div>
      <div id="nb-tray-row"></div>
    </div>
    <aside id="wb-inspector" hidden><div id="wb-inspector-body"></div><div id="wb-inspector-empty"></div></aside>`
  // parsed tracks (camelCase) so renderInspector can resolve the focused tile
  window.ACBridge = { tracks: () => [
    { id: 1, name: 'T1', artist: 'A1', bpm: 124, key: '8A', totalTime: 300 },
    { id: 2, name: 'T2', artist: 'A2', bpm: 124, key: '8A', totalTime: 280 },
    { id: 3, name: 'T3', artist: 'A3', bpm: 124, key: '8A', totalTime: 260 },
  ] }
  globalThis.fetch = mockFetch()
  initTray()
})

describe('gravity tray (R8)', () => {
  it('renders candidate cards with Add buttons, anchored to the last tile by default', async () => {
    await model.buildSet({})
    render()
    await renderTray(null)
    expect(document.getElementById('nb-tray').hasAttribute('hidden')).toBe(false)
    expect(document.querySelectorAll('.nb-crate-card')).toHaveLength(2)
    expect(document.querySelectorAll('.nb-tray-add')).toHaveLength(2)
    expect(document.querySelector('.nb-crate-title').textContent).toBe('Cand A')
    expect(document.getElementById('nb-tray-context').textContent).toContain('T3') // last tile
  })

  it('Add inserts the candidate after the anchor', async () => {
    await model.buildSet({})
    render()
    await renderTray(2) // anchor = last tile (idx 2)
    expect(model.getSet()).toHaveLength(3)
    document.querySelector('.nb-tray-add').click()
    // insertAfter runs synchronously before the async rescore
    expect(model.getSet()).toHaveLength(4)
    expect(model.getSet()[3].track_id).toBe(50)
    await new Promise((r) => setTimeout(r, 0))
  })

  it('toggle collapses / expands the tray row', async () => {
    await model.buildSet({})
    render()
    await renderTray(null)
    const toggle = document.getElementById('nb-tray-toggle')
    toggle.click()
    expect(document.getElementById('nb-tray').classList.contains('nb-tray-collapsed')).toBe(true)
    expect(toggle.textContent).toBe('Show')
    toggle.click()
    expect(document.getElementById('nb-tray').classList.contains('nb-tray-collapsed')).toBe(false)
  })
})

describe('tile-focus inspector (R9)', () => {
  it('focusTile marks the tile active, re-hosts the inspector, re-anchors the tray', async () => {
    await model.buildSet({})
    render()
    focusTile(0)
    await new Promise((r) => setTimeout(r, 0))
    expect(document.querySelector('.nb-tile[data-track-id="1"]').classList.contains('nb-tile-active')).toBe(true)
    expect(document.body.classList.contains('nb-inspecting')).toBe(true)
    expect(document.getElementById('wb-inspector').hasAttribute('hidden')).toBe(false)
    expect(document.getElementById('wb-inspector-body').innerHTML).toContain('wb-insp-title')
    expect(document.getElementById('nb-tray-context').textContent).toContain('T1') // re-anchored
  })

  it('clearFocus removes the active ring + inspecting state', async () => {
    await model.buildSet({})
    render()
    focusTile(1)
    clearFocus()
    expect(document.querySelectorAll('.nb-tile-active')).toHaveLength(0)
    expect(document.body.classList.contains('nb-inspecting')).toBe(false)
  })
})
