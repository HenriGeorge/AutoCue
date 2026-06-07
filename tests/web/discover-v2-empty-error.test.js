/**
 * Tests for the Discover v2 empty + error state polish (T-035) —
 * scanError state surfacing, per-feeder warnings, empty-state messaging
 * branching (token / scan error / no labels / all filtered / truly empty).
 * Mirrors docs/index.html — keep in sync.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}

let DiscoverV2

function makeState(overrides) {
  return {
    cards: [],
    cardsByKey: new Map(),
    savedKeys: new Set(),
    dismissedKeys: new Set(),
    snoozedKeys: new Set(),
    followedLabels: [],
    scanRunning: false,
    scanFeeder: null,
    scanReleasesSeen: 0,
    scanFeedersDone: [],
    scanReleasesByFeeder: {artist: 0, label: 0, novelty: 0},
    scanSparseAdjacency: false,
    scanLastSummary: null,
    scanError: null,
    scanWarnings: [],
    tokenValid: null,
    ...overrides,
  }
}

// SSE handler — same as production (only the parts the tests touch).
function _handleSSEChunk(state, chunk) {
  let event = 'message'
  let data = null
  for (const line of chunk.split('\n')) {
    if (line.startsWith('event: ')) event = line.slice(7).trim()
    else if (line.startsWith('data: ')) {
      try { data = JSON.parse(line.slice(6)) } catch (_) {}
    }
  }
  if (!data) return
  if (event === 'warning') {
    state.scanWarnings.push({
      feeder: data.feeder || 'unknown',
      message: data.exc || data.message || 'warning',
    })
  } else if (event === 'error') {
    state.scanFeeder = null
    const isFatal = (data.feeder || '') === 'orchestrator'
    if (isFatal) {
      state.scanError = {kind: 'orchestrator', message: data.exc || 'scan crashed'}
    } else {
      state.scanWarnings.push({
        feeder: data.feeder || 'unknown',
        message: data.exc || 'feeder failed',
      })
    }
  }
}

function _renderDiscoverV2ScanWarnings() {
  const el = document.getElementById('disc-v2-scan-warnings')
  if (!el) return
  const w = DiscoverV2.state.scanWarnings || []
  if (!w.length) {
    el.style.display = 'none'
    return
  }
  el.style.display = ''
  const byFeeder = new Map()
  for (const x of w) byFeeder.set(x.feeder, (byFeeder.get(x.feeder) || 0) + 1)
  const lines = Array.from(byFeeder.entries()).map(
    ([f, n]) => `⚠ ${_esc(f)} (${n})`
  )
  el.innerHTML = `<strong>Some feeders had trouble:</strong> ${lines.join(' · ')}`
}

// Empty-state branching extracted from _renderDiscoverV2Feed.
function pickEmptyState(state) {
  if (state.tokenValid === false) return 'token-missing'
  if (state.scanError) return 'scan-error:' + state.scanError.kind
  if (!state.followedLabels.length) return 'no-labels'
  if (state.cards.length > 0) return 'all-filtered'
  if (state.scanLastSummary && state.scanLastSummary.releases_surfaced === 0) return 'no-new-releases'
  return 'first-scan'
}


/* ============================================================ SSE error routing */

describe('_handleSSEChunk — error + warning routing', () => {
  it('per-feeder warning event accumulates into scanWarnings', () => {
    const s = makeState()
    _handleSSEChunk(s, 'event: warning\ndata: {"feeder":"label","exc":"page-1 empty"}')
    expect(s.scanWarnings).toEqual([{feeder: 'label', message: 'page-1 empty'}])
  })

  it('per-feeder error event is a warning, NOT a fatal', () => {
    const s = makeState()
    _handleSSEChunk(s, 'event: error\ndata: {"feeder":"artist","exc":"http 500"}')
    expect(s.scanError).toBeNull()
    expect(s.scanWarnings).toEqual([{feeder: 'artist', message: 'http 500'}])
  })

  it('orchestrator error event becomes a fatal scanError', () => {
    const s = makeState({scanFeeder: 'novelty'})
    _handleSSEChunk(s, 'event: error\ndata: {"feeder":"orchestrator","exc":"crashed"}')
    expect(s.scanError).toEqual({kind: 'orchestrator', message: 'crashed'})
    expect(s.scanFeeder).toBeNull()
  })

  it('multiple warnings from the same feeder are kept separately', () => {
    const s = makeState()
    _handleSSEChunk(s, 'event: warning\ndata: {"feeder":"label","exc":"a"}')
    _handleSSEChunk(s, 'event: warning\ndata: {"feeder":"label","exc":"b"}')
    expect(s.scanWarnings).toHaveLength(2)
  })
})


/* ============================================================ warnings render */

describe('_renderDiscoverV2ScanWarnings', () => {
  beforeEach(() => {
    document.body.innerHTML = `<div id="disc-v2-scan-warnings" style="display:none"></div>`
    DiscoverV2 = {state: makeState()}
  })

  it('stays hidden when there are no warnings', () => {
    _renderDiscoverV2ScanWarnings()
    expect(document.getElementById('disc-v2-scan-warnings').style.display).toBe('none')
  })

  it('shows when at least one warning is recorded', () => {
    DiscoverV2.state.scanWarnings = [{feeder: 'label', message: 'page-1 empty'}]
    _renderDiscoverV2ScanWarnings()
    const el = document.getElementById('disc-v2-scan-warnings')
    expect(el.style.display).toBe('')
    expect(el.textContent).toContain('label (1)')
  })

  it('collapses duplicates by feeder and shows the count', () => {
    DiscoverV2.state.scanWarnings = [
      {feeder: 'label', message: 'a'},
      {feeder: 'label', message: 'b'},
      {feeder: 'novelty', message: 'c'},
    ]
    _renderDiscoverV2ScanWarnings()
    const txt = document.getElementById('disc-v2-scan-warnings').textContent
    expect(txt).toContain('label (2)')
    expect(txt).toContain('novelty (1)')
  })

  it('escapes XSS in feeder names', () => {
    DiscoverV2.state.scanWarnings = [{feeder: '<img src=x>', message: 'evil'}]
    _renderDiscoverV2ScanWarnings()
    expect(document.body.innerHTML).not.toContain('<img src=x>')
    expect(document.body.textContent).toContain('<img src=x>')
  })
})


/* ============================================================ empty-state branching */

describe('pickEmptyState — message-selection precedence', () => {
  it('token-missing wins over everything else', () => {
    expect(pickEmptyState(makeState({tokenValid: false, scanError: {kind: 'network'}})))
      .toBe('token-missing')
  })

  it('scanError beats no-labels', () => {
    expect(pickEmptyState(makeState({scanError: {kind: 'network', message: 'down'}, followedLabels: []})))
      .toBe('scan-error:network')
  })

  it('no-labels comes next', () => {
    expect(pickEmptyState(makeState({followedLabels: []})))
      .toBe('no-labels')
  })

  it('all-filtered when cards arrived but every one is dismissed/snoozed', () => {
    expect(pickEmptyState(makeState({
      followedLabels: [{label_id: 1, name: 'X'}],
      cards: [{release_key: 'k1'}, {release_key: 'k2'}],
    }))).toBe('all-filtered')
  })

  it('no-new-releases when last scan returned 0 surfaced', () => {
    expect(pickEmptyState(makeState({
      followedLabels: [{label_id: 1, name: 'X'}],
      scanLastSummary: {releases_surfaced: 0, releases_seen: 0, duration_ms: 100},
    }))).toBe('no-new-releases')
  })

  it('first-scan state when no scan has ever run', () => {
    expect(pickEmptyState(makeState({followedLabels: [{label_id: 1, name: 'X'}]})))
      .toBe('first-scan')
  })

  it('scan-error precedence: conflict / bad-request / network / http / stream / orchestrator', () => {
    for (const kind of ['conflict', 'bad-request', 'network', 'http', 'stream', 'orchestrator']) {
      expect(pickEmptyState(makeState({scanError: {kind}})))
        .toBe('scan-error:' + kind)
    }
  })
})


/* ============================================================ runScan error mapping */

describe('runScan error mapping — fetch + http statuses', () => {
  // Mirror of the error-mapping block in runScan, extracted to a pure helper
  // so the contract is verifiable without driving the full SSE loop.
  async function mapResponse(res, fetchThrew) {
    if (fetchThrew) return {kind: 'network', message: 'TypeError: failed'}
    if (res.status === 409) return {kind: 'conflict', status: 409, message: 'A Discover scan is already running.'}
    if (res.status === 400) {
      let detail = ''
      try { const j = await res.json(); detail = j.detail || '' } catch (_) {}
      return {kind: 'bad-request', status: 400, message: detail || 'Bad request'}
    }
    if (!res.ok) return {kind: 'http', status: res.status, message: `Server returned HTTP ${res.status}.`}
    return null
  }

  it('network throw → kind=network', async () => {
    const err = await mapResponse(null, true)
    expect(err.kind).toBe('network')
  })

  it('409 → kind=conflict', async () => {
    const fake = {status: 409, ok: false}
    expect((await mapResponse(fake)).kind).toBe('conflict')
  })

  it('400 with detail → kind=bad-request with message', async () => {
    const fake = {status: 400, ok: false, json: async () => ({detail: 'DISCOGS_TOKEN missing'})}
    const err = await mapResponse(fake)
    expect(err.kind).toBe('bad-request')
    expect(err.message).toContain('DISCOGS_TOKEN')
  })

  it('500 → kind=http with status', async () => {
    const fake = {status: 500, ok: false}
    const err = await mapResponse(fake)
    expect(err.kind).toBe('http')
    expect(err.status).toBe(500)
  })

  it('200 → no error', async () => {
    const fake = {status: 200, ok: true}
    expect(await mapResponse(fake)).toBeNull()
  })
})
