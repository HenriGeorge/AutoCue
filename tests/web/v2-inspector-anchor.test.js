// @vitest-environment jsdom
/**
 * UNIT A — inspector "Transition in" anchor-transition card (fix/design-workbench).
 *
 * renderInspector() (mode 'track' only) scores the transition anchor → focused
 * track and shows the band + reasons. Anchor = window.ACBridge.nowPlayingId(),
 * fallback = the previously-focused id. Hidden when there is no anchor, when the
 * anchor IS the focused track, or in release mode. POST /api/transitions/score;
 * !r.ok → silent (no lingering empty header). overall → band good>=85/ok>=70/weak.
 * Stale fetches (focus changed mid-flight) are ignored.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  renderInspector,
  renderReleaseInspector,
  clearInspector,
} from '../../docs/js/v2/workbench/inspector.js'

const TRACKS = [
  { id: 1, name: 'Anchor One', artist: 'AA', bpm: 124, key: '8A', totalTime: 300 },
  { id: 2, name: 'Focused Two', artist: 'BB', bpm: 126, key: '9A', totalTime: 320 },
  { id: 3, name: 'Third', artist: 'CC', bpm: 128, key: '9A', totalTime: 310 },
]

let _nowPlaying = null
function setBridge() {
  window.ACBridge = {
    tracks: () => TRACKS,
    nowPlayingId: () => _nowPlaying,
  }
}

function mountDom() {
  document.body.className = ''
  document.body.innerHTML = `
    <div id="track-list"></div>
    <aside id="wb-inspector">
      <div id="wb-inspector-empty"></div>
      <div id="wb-inspector-body" hidden></div>
    </aside>`
}

function txSection() {
  // The anchor card is the section whose header reads "Transition in".
  return [...document.querySelectorAll('.wb-insp-section')].find(
    (s) => s.querySelector('.wb-insp-h')?.textContent === 'Transition in',
  ) || null
}

function okResp(body) {
  return { ok: true, json: async () => body }
}

beforeEach(() => {
  _nowPlaying = null
  setBridge()
  mountDom()
  clearInspector() // reset module mode/focus between tests
  mountDom()
  vi.restoreAllMocks()
})

describe('renderInspector — anchor card present', () => {
  it('scores anchor (now-playing) → focused and renders mono score + reasons', async () => {
    _nowPlaying = 1
    const fetchMock = vi.fn(async () =>
      okResp({ overall: 91, bpm_a: 124, bpm_b: 126, key_a: '8A', key_b: '9A',
               explanation: ['Tempo within 2%', 'Harmonic neighbours', 'Energy lifts'] }),
    )
    globalThis.fetch = fetchMock

    renderInspector(2)

    // POST to the shared transitions endpoint with the right body (ints).
    expect(fetchMock).toHaveBeenCalledOnce()
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/transitions/score')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body)).toEqual({ track_a_id: 1, track_b_id: 2 })

    await vi.waitFor(() => {
      const sec = txSection()
      expect(sec).toBeTruthy()
      const score = sec.querySelector('.wb-insp-tx-score')
      expect(score).toBeTruthy()
      expect(score.textContent).toBe('91')
      // mono class on the data value
      expect(score.classList.contains('mono')).toBe(true)
      // good band (>=85) → green signal class
      expect(score.classList.contains('tx-good')).toBe(true)
    })

    const sec = txSection()
    expect(sec.querySelector('.wb-insp-tx-from').textContent).toContain('Anchor One')
    // up to 3 explanation lines
    expect(sec.querySelectorAll('.wb-insp-tx-reason')).toHaveLength(3)
  })

  it('caps the explanation list at 3 lines', async () => {
    _nowPlaying = 1
    globalThis.fetch = vi.fn(async () =>
      okResp({ overall: 75, explanation: ['a', 'b', 'c', 'd', 'e'] }),
    )
    renderInspector(2)
    await vi.waitFor(() =>
      expect(txSection()?.querySelectorAll('.wb-insp-tx-reason').length).toBe(3),
    )
  })

  it('maps band cutoffs: ok (>=70) → amber, weak (<70) → muted', async () => {
    _nowPlaying = 1
    globalThis.fetch = vi.fn(async () => okResp({ overall: 72, explanation: [] }))
    renderInspector(2)
    await vi.waitFor(() => {
      const s = txSection()?.querySelector('.wb-insp-tx-score')
      expect(s?.classList.contains('tx-ok')).toBe(true)
      expect(s.classList.contains('tx-good')).toBe(false)
    })

    mountDom()
    globalThis.fetch = vi.fn(async () => okResp({ overall: 40, explanation: [] }))
    renderInspector(2)
    await vi.waitFor(() => {
      const s = txSection()?.querySelector('.wb-insp-tx-score')
      expect(s?.classList.contains('tx-weak')).toBe(true)
    })
  })

  it('falls back to the previously-focused track when nothing is playing', async () => {
    _nowPlaying = null
    const fetchMock = vi.fn(async () => okResp({ overall: 80, explanation: [] }))
    globalThis.fetch = fetchMock

    // First focus: no anchor yet → no card, no fetch.
    renderInspector(1)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(txSection()).toBeNull()

    // Second focus: anchor falls back to the previously-focused id (1).
    renderInspector(2)
    expect(fetchMock).toHaveBeenCalledOnce()
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ track_a_id: 1, track_b_id: 2 })
  })
})

describe('renderInspector — anchor card hidden states', () => {
  it('hides (no fetch, no empty header) when there is no anchor', () => {
    _nowPlaying = null
    const fetchMock = vi.fn(async () => okResp({ overall: 88 }))
    globalThis.fetch = fetchMock
    renderInspector(2)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(txSection()).toBeNull()
  })

  it('hides when the anchor IS the focused track (no self-scoring)', () => {
    _nowPlaying = 2
    const fetchMock = vi.fn(async () => okResp({ overall: 88 }))
    globalThis.fetch = fetchMock
    renderInspector(2)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(txSection()).toBeNull()
  })

  it('removes the section silently on !r.ok (no lingering empty header)', async () => {
    _nowPlaying = 1
    globalThis.fetch = vi.fn(async () => ({ ok: false, status: 422, json: async () => ({}) }))
    renderInspector(2)
    // brief placeholder may mount, then it must be removed once the !ok lands
    await vi.waitFor(() => expect(txSection()).toBeNull())
  })
})

describe('renderInspector — stale-fetch guard', () => {
  it('ignores a stale response when focus changes mid-flight', async () => {
    _nowPlaying = 1
    let resolveFirst
    const first = new Promise((res) => { resolveFirst = res })
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => first.then(() => okResp({ overall: 10, explanation: ['STALE'] })))
      .mockImplementationOnce(async () => okResp({ overall: 95, explanation: ['FRESH'] }))
    globalThis.fetch = fetchMock

    renderInspector(2)          // kicks off the slow first fetch
    renderInspector(3)          // re-focus before the first resolves
    await vi.waitFor(() => expect(txSection()?.querySelector('.wb-insp-tx-score')?.textContent).toBe('95'))

    resolveFirst()              // now let the stale one resolve
    await Promise.resolve(); await Promise.resolve()
    // The fresh (95 / FRESH) card must NOT be clobbered by the stale (10 / STALE).
    const sec = txSection()
    expect(sec.querySelector('.wb-insp-tx-score').textContent).toBe('95')
    expect(sec.textContent).toContain('FRESH')
    expect(sec.textContent).not.toContain('STALE')
  })
})

describe('release mode never renders the anchor card', () => {
  it('renderReleaseInspector shows no Transition-in section', () => {
    _nowPlaying = 1
    window.ACBridge.discoverState = () => ({
      cardsByKey: new Map([['k1', { release: { id: 99, title: 'Rel', artist: 'X' } }]]),
    })
    const fetchMock = vi.fn(async () => okResp({ overall: 88 }))
    globalThis.fetch = fetchMock
    renderReleaseInspector('k1')
    expect(txSection()).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
