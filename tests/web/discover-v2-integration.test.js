/**
 * Discover v2 integration sweep (T-038).
 *
 * Per-PR tests verify individual helpers in isolation. This file wires the
 * pieces together end-to-end so the contract between them is verified:
 *   1. pub/sub fanout: notify() reaches every subscriber, errors don't break it
 *   2. SSE consumer integration: parse chunks → mutate state → render chain
 *   3. runScan against a stubbed ReadableStream → real wire format
 *
 * Mirrors the inline production module in docs/index.html.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
}


/* ====================================================== build a DiscoverV2 IIFE */

function makeDiscoverV2(fetchImpl) {
  const state = {
    cards: [],
    cardsByKey: new Map(),
    savedKeys: new Set(),
    dismissedKeys: new Set(),
    snoozedKeys: new Set(),
    resurfacedKeys: new Set(),
    snoozedMeta: new Map(),
    followedLabels: [],
    blockedArtists: [],
    blockedLabels: [],
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
  }

  const subs = new Set()
  function subscribe(fn) { subs.add(fn); return () => subs.delete(fn) }
  function notify() {
    for (const fn of subs) {
      try { fn(state) } catch (e) { /* swallow per production semantics */ }
    }
  }

  function _handleSSEChunk(chunk) {
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
      notify()
    } else if (event === 'release') {
      state.cards.push(data)
      state.cardsByKey.set(data.release_key, data)
      state.scanReleasesSeen++
      const src = (data.source || '').split(':')[0]
      if (Object.prototype.hasOwnProperty.call(state.scanReleasesByFeeder, src)) {
        state.scanReleasesByFeeder[src]++
      }
      notify()
    } else if (event === 'sparse_adjacency') {
      state.scanSparseAdjacency = true
      notify()
    } else if (event === 'warning') {
      state.scanWarnings.push({
        feeder: data.feeder || 'unknown',
        message: data.exc || data.message || 'warning',
      })
      notify()
    } else if (event === 'done') {
      state.scanFeeder = null
      state.scanLastSummary = {
        releases_surfaced: data.releases_surfaced,
        releases_seen: data.releases_seen,
        duration_ms: data.duration_ms,
      }
      notify()
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
      notify()
    }
  }

  // Issue #67: track in-flight AbortController so a new runScan can abort
  // the prior fetch.
  let _scanAbort = null

  async function runScan() {
    // Issue #67: if a scan is in flight, abort it (closes the SSE reader and
    // releases the server lock) before starting the new one. Preserves the
    // existing cards across error branches.
    if (_scanAbort) {
      _scanAbort.autocueSuperseded = true
      try { _scanAbort.abort() } catch (_) {}
      _scanAbort = null
      await Promise.resolve()
    }
    state.scanRunning = true
    state.scanFeeder = null
    state.scanReleasesSeen = 0
    state.scanFeedersDone = []
    state.scanReleasesByFeeder = {artist: 0, label: 0, novelty: 0}
    state.scanSparseAdjacency = false
    state.scanLastSummary = null
    state.scanError = null
    state.scanWarnings = []
    notify()

    const abort = new AbortController()
    _scanAbort = abort
    let res
    try {
      res = await fetchImpl('/api/discover/feed', {signal: abort.signal})
    } catch (e) {
      if (abort.autocueSuperseded || (e && e.name === 'AbortError')) {
        if (_scanAbort === abort) _scanAbort = null
        state.scanRunning = false
        notify()
        return
      }
      if (_scanAbort === abort) _scanAbort = null
      state.scanRunning = false
      state.scanError = {kind: 'network', message: String(e.message || e)}
      notify()
      return
    }
    if (res.status === 409) {
      // Issue #67: do NOT clear cards on 409 — keep the prior feed visible.
      if (_scanAbort === abort) _scanAbort = null
      state.scanRunning = false
      state.scanError = {kind: 'conflict', status: 409, message: 'busy'}
      notify()
      return
    }
    if (!res.ok) {
      if (_scanAbort === abort) _scanAbort = null
      state.scanRunning = false
      state.scanError = {kind: 'http', status: res.status, message: 'fail'}
      notify()
      return
    }

    // Fetch confirmed OK → reset the card grid (issue #67).
    state.cards = []
    state.cardsByKey.clear()
    notify()

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    try {
      while (true) {
        const {done, value} = await reader.read()
        if (done) break
        buf += decoder.decode(value, {stream: true})
        const chunks = buf.split('\n\n')
        buf = chunks.pop()
        for (const chunk of chunks) _handleSSEChunk(chunk)
      }
    } catch (e) {
      if (!(abort.autocueSuperseded || (e && e.name === 'AbortError'))) {
        state.scanError = {kind: 'stream', message: String(e.message || e)}
      }
    }
    if (_scanAbort === abort) _scanAbort = null
    state.scanRunning = false
    notify()
  }

  return {state, subscribe, notify, _handleSSEChunk, runScan}
}


/* ============================================================ pub/sub fanout */

describe('subscribe / notify pub/sub', () => {
  let DV2
  beforeEach(() => { DV2 = makeDiscoverV2(() => null) })

  it('reaches every subscriber on notify', () => {
    const a = vi.fn()
    const b = vi.fn()
    const c = vi.fn()
    DV2.subscribe(a); DV2.subscribe(b); DV2.subscribe(c)
    DV2.notify()
    expect(a).toHaveBeenCalledTimes(1)
    expect(b).toHaveBeenCalledTimes(1)
    expect(c).toHaveBeenCalledTimes(1)
  })

  it('an error in one subscriber does NOT prevent the others from running', () => {
    const a = vi.fn()
    const bad = vi.fn(() => { throw new Error('subscriber blew up') })
    const c = vi.fn()
    DV2.subscribe(a); DV2.subscribe(bad); DV2.subscribe(c)
    expect(() => DV2.notify()).not.toThrow()
    expect(a).toHaveBeenCalledOnce()
    expect(c).toHaveBeenCalledOnce()
  })

  it('subscribe returns an unsubscribe function', () => {
    const fn = vi.fn()
    const off = DV2.subscribe(fn)
    DV2.notify()
    expect(fn).toHaveBeenCalledTimes(1)
    off()
    DV2.notify()
    expect(fn).toHaveBeenCalledTimes(1)  // still 1 — unsubscribed
  })
})


/* ============================================================ runScan against a streamed response */

function fakeStreamResponse(chunks, {status = 200, ok = true} = {}) {
  const encoder = new TextEncoder()
  let i = 0
  return {
    status, ok,
    body: {
      getReader: () => ({
        read: async () => {
          if (i >= chunks.length) return {done: true}
          const value = encoder.encode(chunks[i++])
          return {done: false, value}
        },
      }),
    },
  }
}

describe('runScan — end-to-end against a stubbed SSE stream', () => {
  it('consumes a full scan and populates state', async () => {
    const fetchImpl = vi.fn(async () => fakeStreamResponse([
      'event: progress\ndata: {"feeder":"artist"}\n\n',
      'event: release\ndata: {"release_key":"k1","source":"artist","release":{"title":"T1","artist":"A"}}\n\n',
      'event: release\ndata: {"release_key":"k2","source":"artist","release":{"title":"T2","artist":"A"}}\n\n',
      'event: progress\ndata: {"feeder":"label"}\n\n',
      'event: release\ndata: {"release_key":"k3","source":"label","release":{"title":"T3","artist":"B"}}\n\n',
      'event: progress\ndata: {"feeder":"novelty"}\n\n',
      'event: release\ndata: {"release_key":"k4","source":"novelty:style","release":{"title":"T4","artist":"C"}}\n\n',
      'event: done\ndata: {"releases_surfaced":4,"releases_seen":4,"duration_ms":2500}\n\n',
    ]))
    const DV2 = makeDiscoverV2(fetchImpl)
    await DV2.runScan()
    expect(DV2.state.cards).toHaveLength(4)
    expect(DV2.state.scanRunning).toBe(false)
    expect(DV2.state.scanFeeder).toBeNull()
    expect(DV2.state.scanFeedersDone).toEqual(['artist', 'label'])
    expect(DV2.state.scanReleasesByFeeder).toEqual({artist: 2, label: 1, novelty: 1})
    expect(DV2.state.scanLastSummary.releases_surfaced).toBe(4)
    expect(DV2.state.scanError).toBeNull()
  })

  it('handles chunked SSE frames spanning multiple reads', async () => {
    // Split a single event across two stream reads — exercises the partial-buffer code path.
    const fetchImpl = vi.fn(async () => fakeStreamResponse([
      'event: release\ndata: {"release_key":"k',
      '1","source":"artist","release":{"title":"T1","artist":"A"}}\n\nevent: done\ndata: {"releases_surfaced":1,"releases_seen":1,"duration_ms":100}\n\n',
    ]))
    const DV2 = makeDiscoverV2(fetchImpl)
    await DV2.runScan()
    expect(DV2.state.cards).toHaveLength(1)
    expect(DV2.state.cards[0].release_key).toBe('k1')
  })

  it('409 sets scanError to conflict and does NOT consume the body', async () => {
    const reader = vi.fn()
    const fetchImpl = vi.fn(async () => ({status: 409, ok: false, body: {getReader: () => ({read: reader})}}))
    const DV2 = makeDiscoverV2(fetchImpl)
    await DV2.runScan()
    expect(DV2.state.scanError.kind).toBe('conflict')
    expect(DV2.state.scanRunning).toBe(false)
    expect(reader).not.toHaveBeenCalled()
  })

  // Issue #67 regression: a 409 response while a prior feed is already
  // rendered must NOT clear the existing cards. Before the fix, the UI
  // dumped the grid to the empty-state on any 409.
  it('issue #67 — 409 preserves the previously-rendered cards', async () => {
    // First scan: a normal, successful stream that fills the grid.
    let phase = 'ok'
    const fetchImpl = vi.fn(async () => {
      if (phase === 'ok') return fakeStreamResponse([
        'event: release\ndata: {"release_key":"k1","source":"artist","release":{"title":"T1","artist":"A"}}\n\n',
        'event: release\ndata: {"release_key":"k2","source":"artist","release":{"title":"T2","artist":"A"}}\n\n',
        'event: done\ndata: {"releases_surfaced":2,"releases_seen":2,"duration_ms":50}\n\n',
      ])
      // Second scan: 409 conflict (e.g. another client / tab holds the lock).
      return {status: 409, ok: false, body: {getReader: () => ({read: vi.fn()})}}
    })
    const DV2 = makeDiscoverV2(fetchImpl)
    await DV2.runScan()
    expect(DV2.state.cards).toHaveLength(2)  // baseline fill

    phase = 'conflict'
    await DV2.runScan()
    // Regression guard: cards survive the 409 (pre-fix this would be 0).
    expect(DV2.state.cards).toHaveLength(2)
    expect(DV2.state.cardsByKey.size).toBe(2)
    expect(DV2.state.scanError.kind).toBe('conflict')
    expect(DV2.state.scanRunning).toBe(false)
  })

  it('a fetch throw maps to scanError.kind=network', async () => {
    const fetchImpl = vi.fn(async () => { throw new Error('DNS') })
    const DV2 = makeDiscoverV2(fetchImpl)
    await DV2.runScan()
    expect(DV2.state.scanError).toMatchObject({kind: 'network'})
    expect(DV2.state.scanRunning).toBe(false)
  })

  it('a mid-stream throw maps to scanError.kind=stream but keeps partial results', async () => {
    let calls = 0
    const fetchImpl = vi.fn(async () => ({
      status: 200, ok: true,
      body: {getReader: () => ({
        read: async () => {
          if (calls++ === 0) {
            return {done: false, value: new TextEncoder().encode(
              'event: release\ndata: {"release_key":"k1","source":"artist","release":{"title":"T","artist":"A"}}\n\n'
            )}
          }
          throw new Error('stream blew up')
        },
      })},
    }))
    const DV2 = makeDiscoverV2(fetchImpl)
    await DV2.runScan()
    expect(DV2.state.scanError.kind).toBe('stream')
    expect(DV2.state.cards).toHaveLength(1)  // partial result preserved
  })

  // Issue #67: previously, the second concurrent runScan() was a silent
  // no-op. That caused user-perceived "filter doesn't work" until the in-
  // flight stream completed. After the fix, the second call ABORTS the
  // first (via AbortController) and supersedes it.
  it('issue #67 — a second runScan aborts the first and supersedes it', async () => {
    let firstFetchSignal = null
    let firstFetchReject = null
    let secondFetchCalled = false
    const fetchImpl = vi.fn((url, opts) => {
      if (!firstFetchSignal) {
        firstFetchSignal = opts?.signal
        return new Promise((_, reject) => {
          firstFetchReject = reject
          // First call hangs until the abort handler fires.
          if (opts?.signal) {
            opts.signal.addEventListener('abort', () => {
              const err = new Error('aborted')
              err.name = 'AbortError'
              reject(err)
            })
          }
        })
      }
      secondFetchCalled = true
      return Promise.resolve(fakeStreamResponse([
        'event: release\ndata: {"release_key":"new","source":"artist","release":{"title":"NEW","artist":"X"}}\n\n',
        'event: done\ndata: {"releases_surfaced":1,"releases_seen":1,"duration_ms":1}\n\n',
      ]))
    })
    const DV2 = makeDiscoverV2(fetchImpl)
    const p1 = DV2.runScan()
    // Microtask boundary so p1's fetch is in flight before runScan #2 starts.
    await Promise.resolve()
    const p2 = DV2.runScan()
    await Promise.all([p1, p2])
    // Both calls fired (no silent drop).
    expect(fetchImpl).toHaveBeenCalledTimes(2)
    expect(secondFetchCalled).toBe(true)
    // Aborted call did NOT produce a misleading network error.
    expect(DV2.state.scanError).toBeNull()
    // Final state reflects the SECOND (winning) scan's results.
    expect(DV2.state.cards.map(c => c.release_key)).toEqual(['new'])
    expect(DV2.state.scanRunning).toBe(false)
  })

  it('emits a notify per state change (progress / release / done)', async () => {
    const fetchImpl = vi.fn(async () => fakeStreamResponse([
      'event: progress\ndata: {"feeder":"artist"}\n\n',
      'event: release\ndata: {"release_key":"k1","source":"artist","release":{}}\n\n',
      'event: done\ndata: {"releases_surfaced":1,"releases_seen":1,"duration_ms":100}\n\n',
    ]))
    const DV2 = makeDiscoverV2(fetchImpl)
    const sub = vi.fn()
    DV2.subscribe(sub)
    await DV2.runScan()
    // 1 start + 1 cards-reset (post-fetch OK, issue #67) + 1 progress + 1
    // release + 1 done + 1 finalize = 6
    expect(sub).toHaveBeenCalledTimes(6)
  })
})


/* ============================================================ SSE → render contract */

describe('SSE → state → render chain', () => {
  it('every release event triggers one renderer call', async () => {
    const DV2 = makeDiscoverV2(async () => fakeStreamResponse([
      'event: release\ndata: {"release_key":"k1","source":"artist","release":{}}\n\n',
      'event: release\ndata: {"release_key":"k2","source":"label","release":{}}\n\n',
      'event: release\ndata: {"release_key":"k3","source":"novelty:style","release":{}}\n\n',
      'event: done\ndata: {"releases_surfaced":3,"releases_seen":3,"duration_ms":100}\n\n',
    ]))
    const seenCardCounts = []
    DV2.subscribe(s => seenCardCounts.push(s.cards.length))
    await DV2.runScan()
    // The renderer sees: 0 (initial reset), 1, 2, 3 (each release), 3 (done), 3 (finalize)
    expect(seenCardCounts).toContain(0)
    expect(seenCardCounts).toContain(1)
    expect(seenCardCounts).toContain(3)
    expect(seenCardCounts[seenCardCounts.length - 1]).toBe(3)
  })

  it('a renderer can read scanError after a 409 without seeing stale data', async () => {
    const DV2 = makeDiscoverV2(async () => ({status: 409, ok: false, body: {getReader: () => ({})}}))
    let lastError = null
    let lastRunning = null
    DV2.subscribe(s => { lastError = s.scanError; lastRunning = s.scanRunning })
    await DV2.runScan()
    expect(lastRunning).toBe(false)
    expect(lastError?.kind).toBe('conflict')
  })
})
