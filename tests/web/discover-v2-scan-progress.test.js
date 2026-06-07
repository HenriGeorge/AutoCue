/**
 * Tests for the Discover v2 scan-progress UI (T-034) — feeder breakdown,
 * per-feeder release bucketing, sparse-adjacency warning, post-scan delta
 * strip, budget-bar % approximation. Mirrors docs/index.html — keep in sync.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

/* ============================================================ copied helpers */

const _DISC_V2_FEEDER_BUDGETS = {artist: 20, label: 15, novelty: 10}

function _feederProgressPercent(scanFeeder, feedersDone) {
  let consumed = 0
  let total = 0
  for (const f of ['artist', 'label', 'novelty']) {
    const budget = _DISC_V2_FEEDER_BUDGETS[f] || 0
    total += budget
    if (feedersDone.includes(f)) consumed += budget
    else if (scanFeeder === f) consumed += Math.round(budget * 0.5)
  }
  return total ? Math.round((consumed / total) * 100) : 0
}

// SSE chunk handler — mirror of the module body. Bumps per-feeder buckets,
// tracks scanFeedersDone via transitions, surfaces sparse_adjacency, captures
// the done telemetry into scanLastSummary.
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
  if (event === 'progress') {
    if (state.scanFeeder && state.scanFeeder !== data.feeder &&
        !state.scanFeedersDone.includes(state.scanFeeder)) {
      state.scanFeedersDone.push(state.scanFeeder)
    }
    state.scanFeeder = data.feeder
  } else if (event === 'release') {
    state.scanReleasesSeen++
    const src = (data.source || '').split(':')[0]
    if (Object.prototype.hasOwnProperty.call(state.scanReleasesByFeeder, src)) {
      state.scanReleasesByFeeder[src]++
    }
  } else if (event === 'sparse_adjacency') {
    state.scanSparseAdjacency = true
  } else if (event === 'done') {
    state.scanFeeder = null
    state.scanLastSummary = {
      releases_surfaced: data.releases_surfaced,
      releases_seen: data.releases_seen,
      duration_ms: data.duration_ms,
    }
  } else if (event === 'error') {
    state.scanFeeder = null
  }
}

function makeState() {
  return {
    scanFeeder: null,
    scanReleasesSeen: 0,
    scanFeedersDone: [],
    scanReleasesByFeeder: {artist: 0, label: 0, novelty: 0},
    scanSparseAdjacency: false,
    scanLastSummary: null,
  }
}


/* ============================================================ feeder budget % */

describe('_feederProgressPercent', () => {
  it('returns 0 before any feeder starts', () => {
    expect(_feederProgressPercent(null, [])).toBe(0)
  })

  it('is roughly 50% when artist feeder is mid-flight', () => {
    // Artist 20 * 0.5 = 10 / 45 ≈ 22%
    expect(_feederProgressPercent('artist', [])).toBe(22)
  })

  it('jumps to artist=fully-done when artist is in feedersDone', () => {
    // artist fully consumed (20) + label half-consumed (Math.round(15 * 0.5) = 8) = 28 / 45 → 62%
    expect(_feederProgressPercent('label', ['artist'])).toBe(62)
  })

  it('hits 100 when all three are done', () => {
    expect(_feederProgressPercent(null, ['artist', 'label', 'novelty'])).toBe(100)
  })
})


/* ============================================================ SSE-driven state */

describe('_handleSSEChunk — scan-progress bookkeeping', () => {
  let state
  beforeEach(() => { state = makeState() })

  it('progress events set scanFeeder', () => {
    _handleSSEChunk(state, 'event: progress\ndata: {"feeder":"artist","scanned":0}')
    expect(state.scanFeeder).toBe('artist')
  })

  it('progress transition pushes the prior feeder onto scanFeedersDone', () => {
    _handleSSEChunk(state, 'event: progress\ndata: {"feeder":"artist","scanned":0}')
    _handleSSEChunk(state, 'event: progress\ndata: {"feeder":"label","scanned":0}')
    expect(state.scanFeedersDone).toEqual(['artist'])
    expect(state.scanFeeder).toBe('label')
  })

  it('release events bucket by source: artist / label / novelty', () => {
    _handleSSEChunk(state, 'event: release\ndata: {"source":"artist","release_key":"k1","release":{}}')
    _handleSSEChunk(state, 'event: release\ndata: {"source":"label","release_key":"k2","release":{}}')
    _handleSSEChunk(state, 'event: release\ndata: {"source":"novelty:style","release_key":"k3","release":{}}')
    expect(state.scanReleasesByFeeder.artist).toBe(1)
    expect(state.scanReleasesByFeeder.label).toBe(1)
    expect(state.scanReleasesByFeeder.novelty).toBe(1)
    expect(state.scanReleasesSeen).toBe(3)
  })

  it('release events with an unknown source do NOT crash', () => {
    expect(() => _handleSSEChunk(state, 'event: release\ndata: {"source":"shop","release_key":"k1","release":{}}'))
      .not.toThrow()
    expect(state.scanReleasesSeen).toBe(1)
  })

  it('sparse_adjacency event flips the warning flag', () => {
    _handleSSEChunk(state, 'event: sparse_adjacency\ndata: {"strategy":"style"}')
    expect(state.scanSparseAdjacency).toBe(true)
  })

  it('done event clears scanFeeder + captures the summary', () => {
    _handleSSEChunk(state, 'event: done\ndata: {"releases_surfaced":42,"releases_seen":120,"duration_ms":4500}')
    expect(state.scanFeeder).toBeNull()
    expect(state.scanLastSummary).toEqual({
      releases_surfaced: 42, releases_seen: 120, duration_ms: 4500,
    })
  })

  it('error event clears scanFeeder without capturing a summary', () => {
    state.scanFeeder = 'label'
    _handleSSEChunk(state, 'event: error\ndata: {"feeder":"label","exc":"boom"}')
    expect(state.scanFeeder).toBeNull()
    expect(state.scanLastSummary).toBeNull()
  })

  it('full lifecycle: progress→releases→progress→releases→done', () => {
    _handleSSEChunk(state, 'event: progress\ndata: {"feeder":"artist"}')
    _handleSSEChunk(state, 'event: release\ndata: {"source":"artist","release_key":"k1","release":{}}')
    _handleSSEChunk(state, 'event: release\ndata: {"source":"artist","release_key":"k2","release":{}}')
    _handleSSEChunk(state, 'event: progress\ndata: {"feeder":"label"}')
    _handleSSEChunk(state, 'event: release\ndata: {"source":"label","release_key":"k3","release":{}}')
    _handleSSEChunk(state, 'event: progress\ndata: {"feeder":"novelty"}')
    _handleSSEChunk(state, 'event: release\ndata: {"source":"novelty:style","release_key":"k4","release":{}}')
    _handleSSEChunk(state, 'event: done\ndata: {"releases_surfaced":4,"releases_seen":4,"duration_ms":3000}')
    expect(state.scanFeedersDone).toEqual(['artist', 'label'])
    // The current 'novelty' feeder never transitioned to a next progress, so
    // it doesn't appear in scanFeedersDone — that's OK; the done event is the
    // signal that everything finished.
    expect(state.scanFeeder).toBeNull()
    expect(state.scanReleasesByFeeder).toEqual({artist: 2, label: 1, novelty: 1})
    expect(state.scanLastSummary.releases_surfaced).toBe(4)
  })
})


/* ============================================================ ScanProgress render */

describe('_renderDiscoverV2ScanProgress — DOM output', () => {
  let DiscoverV2

  function _renderDiscoverV2ScanProgress() {
    const el = document.getElementById('disc-v2-scan-progress')
    const label = document.getElementById('disc-v2-scan-progress-label')
    const breakdown = document.getElementById('disc-v2-scan-progress-breakdown')
    const warning = document.getElementById('disc-v2-scan-progress-warning')
    const fill = document.getElementById('disc-v2-scan-progress-fill')
    const delta = document.getElementById('disc-v2-scan-delta')
    if (!el || !label) return
    const s = DiscoverV2.state

    if (s.scanRunning) {
      el.style.display = ''
      if (delta) delta.style.display = 'none'
      const feeder = s.scanFeeder || 'starting'
      const count = s.scanReleasesSeen
      label.textContent = `Scanning ${feeder}… ${count} releases found so far`
      if (breakdown) {
        const parts = ['artist', 'label', 'novelty'].map(f => {
          const n = s.scanReleasesByFeeder[f] || 0
          const status = f === s.scanFeeder ? '🔄' : s.scanFeedersDone.includes(f) ? '✓' : '·'
          return `<span data-feeder="${f}">${status} ${f} ${n}</span>`
        })
        breakdown.innerHTML = parts.join('')
      }
      if (warning) {
        if (s.scanSparseAdjacency) {
          warning.style.display = ''
          warning.textContent = '⚠ Sparse adjacency'
        } else {
          warning.style.display = 'none'
        }
      }
      if (fill) fill.style.width = _feederProgressPercent(s.scanFeeder, s.scanFeedersDone) + '%'
      return
    }

    el.style.display = 'none'
    if (delta && s.scanLastSummary) {
      const sum = s.scanLastSummary
      delta.style.display = ''
      delta.textContent = `✓ Found ${sum.releases_surfaced} new releases`
    } else if (delta) {
      delta.style.display = 'none'
    }
  }

  beforeEach(() => {
    document.body.innerHTML = `
      <div id="disc-v2-scan-progress" style="display:none">
        <span id="disc-v2-scan-progress-label"></span>
        <div id="disc-v2-scan-progress-breakdown"></div>
        <div id="disc-v2-scan-progress-warning" style="display:none"></div>
        <div id="disc-v2-scan-progress-fill"></div>
      </div>
      <div id="disc-v2-scan-delta" style="display:none"></div>
    `
    DiscoverV2 = {state: {...makeState(), scanRunning: false}}
  })

  it('shows the indicator while scan is running', () => {
    DiscoverV2.state = {...DiscoverV2.state, scanRunning: true, scanFeeder: 'artist'}
    _renderDiscoverV2ScanProgress()
    expect(document.getElementById('disc-v2-scan-progress').style.display).toBe('')
    expect(document.getElementById('disc-v2-scan-progress-label').textContent).toContain('Scanning artist')
  })

  it('renders per-feeder breakdown with status icons', () => {
    DiscoverV2.state = {
      ...DiscoverV2.state,
      scanRunning: true,
      scanFeeder: 'label',
      scanFeedersDone: ['artist'],
      scanReleasesByFeeder: {artist: 5, label: 2, novelty: 0},
    }
    _renderDiscoverV2ScanProgress()
    const bd = document.getElementById('disc-v2-scan-progress-breakdown')
    expect(bd.querySelector('[data-feeder="artist"]').textContent).toContain('✓')
    expect(bd.querySelector('[data-feeder="label"]').textContent).toContain('🔄')
    expect(bd.querySelector('[data-feeder="novelty"]').textContent).toContain('·')
  })

  it('surfaces the sparse-adjacency warning when set', () => {
    DiscoverV2.state = {...DiscoverV2.state, scanRunning: true, scanSparseAdjacency: true}
    _renderDiscoverV2ScanProgress()
    expect(document.getElementById('disc-v2-scan-progress-warning').style.display).toBe('')
  })

  it('shows the post-scan delta strip when a summary is captured', () => {
    DiscoverV2.state = {
      ...DiscoverV2.state,
      scanRunning: false,
      scanLastSummary: {releases_surfaced: 17, releases_seen: 50, duration_ms: 2500},
    }
    _renderDiscoverV2ScanProgress()
    const delta = document.getElementById('disc-v2-scan-delta')
    expect(delta.style.display).toBe('')
    expect(delta.textContent).toContain('17 new releases')
  })

  it('hides the delta strip when there is no summary', () => {
    DiscoverV2.state = {...DiscoverV2.state, scanRunning: false, scanLastSummary: null}
    _renderDiscoverV2ScanProgress()
    expect(document.getElementById('disc-v2-scan-delta').style.display).toBe('none')
  })
})
