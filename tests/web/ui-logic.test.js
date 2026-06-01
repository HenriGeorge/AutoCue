/**
 * Tests for UI logic bugs found during browser testing.
 *
 * Three bugs were discovered and fixed:
 *   1. Sort button label map omitted 'key' → active Key button showed "undefined ▲"
 *   2. colorTracksByBpm() read resp.colored without checking r.ok → "Colored undefined"
 *   3. applyToRekordbox() read resp.applied without checking r.ok → "Applied undefined"
 *
 * Functions under test are extracted verbatim from docs/index.html.
 * If you change them in index.html, update the copies here.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// ── 1. Sort button label map (docs/index.html ~line 2010) ──────────────────

const SORT_LABELS = {
  title: 'Title', artist: 'Artist', album: 'Album',
  bpm: 'BPM', key: 'Key', rating: 'Rating', plays: 'Plays',
}

describe('SORT_LABELS', () => {
  it('has an entry for every sort key including rating and plays', () => {
    for (const k of ['title', 'artist', 'album', 'bpm', 'key', 'rating', 'plays']) {
      expect(SORT_LABELS[k]).toBeDefined()
    }
  })

  it('key maps to "Key" (not undefined)', () => {
    expect(SORT_LABELS['key']).toBe('Key')
  })

  it('rating maps to "Rating"', () => {
    expect(SORT_LABELS['rating']).toBe('Rating')
  })

  it('plays maps to "Plays"', () => {
    expect(SORT_LABELS['plays']).toBe('Plays')
  })

  it('no entry produces undefined (sanity check for the lookup pattern)', () => {
    expect(SORT_LABELS['nonexistent']).toBeUndefined()
  })

  it('active sort button text is label + arrow, not "undefined ▲"', () => {
    const by = 'key'
    const label = SORT_LABELS[by]
    const buttonText = label + ' ▲'
    expect(buttonText).toBe('Key ▲')
    expect(buttonText).not.toContain('undefined')
  })
})

// ── 2. computeCues memory cue prepend (docs/index.html ~line 1708) ─────────

function generateCues(track, barsInterval, startBar, maxCues) {
  if (!track.tempo || !track.tempo.bpm) return []
  const { bpm, inizio, beatsPerBar } = track.tempo
  const barDuration = (60.0 / bpm) * beatsPerBar
  const cues = []
  for (let i = 0; i < maxCues; i++) {
    const posSec = inizio + (startBar - 1 + i * barsInterval) * barDuration
    if (posSec < 0) continue
    if (track.totalTime > 0 && posSec >= track.totalTime) break
    const barNumber = startBar + i * barsInterval
    cues.push({ slot: i, posSec: Math.round(posSec * 1000) / 1000, name: `Bar ${barNumber}` })
  }
  return cues
}

function computeCues(track, { addMemoryCue = false, skipExisting = false, analysisMode = 'bar',
                              phraseCueState = {}, barsInterval = 16, startBar = 1, maxCues = 8 } = {}) {
  if (skipExisting && track.existingHotCues > 0) return []
  let cues
  if (analysisMode === 'phrase' && phraseCueState[track.id]?.length) {
    cues = phraseCueState[track.id].map(c => ({
      slot: c.slot, posSec: c.position_ms / 1000,
      label: c.label, isPhrase: true, name: c.name || '',
    }))
  } else {
    cues = generateCues(track, barsInterval, startBar, maxCues)
  }
  if (addMemoryCue && cues.length) {
    const memPos = analysisMode === 'phrase' ? cues[0].posSec : 0
    cues = [{ slot: -1, posSec: memPos, label: '', name: 'Auto Cue' }, ...cues]
  }
  return cues
}

const baseTrack = {
  id: '1', name: 'Test', existingHotCues: 0, totalTime: 300,
  tempo: { bpm: 128, inizio: 0, beatsPerBar: 4 },
}

describe('computeCues — memory cue', () => {
  it('no memory cue by default', () => {
    const cues = computeCues(baseTrack)
    expect(cues.every(c => c.slot !== -1)).toBe(true)
  })

  it('prepends slot=-1 cue when addMemoryCue=true', () => {
    const cues = computeCues(baseTrack, { addMemoryCue: true })
    expect(cues[0].slot).toBe(-1)
    expect(cues[0].name).toBe('Auto Cue')
  })

  it('memory cue is at posSec=0 in bar mode', () => {
    const cues = computeCues(baseTrack, { addMemoryCue: true, analysisMode: 'bar' })
    expect(cues[0].posSec).toBe(0)
  })

  it('memory cue anchors to first phrase position in phrase mode', () => {
    const phraseCueState = {
      '1': [{ slot: 0, position_ms: 5000, label: 'Intro', name: 'Intro' }],
    }
    const cues = computeCues(baseTrack, {
      addMemoryCue: true, analysisMode: 'phrase', phraseCueState,
    })
    expect(cues[0].slot).toBe(-1)
    expect(cues[0].posSec).toBe(5)
  })

  it('does not prepend memory cue when cues list is empty', () => {
    const noBpmTrack = { ...baseTrack, tempo: null }
    const cues = computeCues(noBpmTrack, { addMemoryCue: true })
    expect(cues).toHaveLength(0)
  })

  it('hot cue slots are unaffected and start at 0', () => {
    const cues = computeCues(baseTrack, { addMemoryCue: true })
    expect(cues[1].slot).toBe(0)
    expect(cues[1].name).toBe('Bar 1')
  })

  it('returns empty when skipExisting and track has hot cues', () => {
    const track = { ...baseTrack, existingHotCues: 3 }
    const cues = computeCues(track, { addMemoryCue: true, skipExisting: true })
    expect(cues).toHaveLength(0)
  })
})

// ── 3. HTTP error propagation (docs/index.html ~line 971 & ~line 998) ──────

// Minimal toast + DOM stubs so the functions can run outside a browser
function makeToastCapture() {
  const messages = []
  return {
    showToast: (msg) => messages.push(msg),
    messages,
  }
}

async function colorTracksByBpm_fn(trackIds, fetchImpl, showToast) {
  try {
    const r = await fetchImpl('/api/color-tracks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: trackIds, dry_run: false }),
    })
    const resp = await r.json()
    if (!r.ok) {
      showToast(`Color by BPM failed: ${resp.detail || r.statusText}`)
    } else {
      const backupNote = resp.backup_path ? ' — backup saved to ~/.autocue/backups/' : ''
      showToast(`Colored ${resp.colored} track(s) by BPM${backupNote}`)
    }
  } catch (err) {
    showToast(`Color by BPM failed: ${err.message}`)
  }
}

// SSE-based applyToRekordbox (mirrors docs/index.html implementation)
async function applyToRekordbox_fn(fetchImpl, showToast, onProgress) {
  try {
    const r = await fetchImpl('/api/generate-apply-stream', { method: 'POST' })
    if (!r.ok) {
      const err = await r.json().catch(() => ({}))
      throw new Error(err.detail || r.statusText)
    }
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    let finalData = null
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n'); buf = lines.pop()
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const ev = JSON.parse(line.slice(6))
        if (ev.done) { finalData = ev }
        else if (onProgress) { onProgress(ev) }
      }
    }
    if (finalData) {
      const backupNote = finalData.backup_path ? ' — backup saved to ~/.autocue/backups/' : ''
      showToast(`Applied ${finalData.applied} track(s)${backupNote}`)
    }
  } catch (err) {
    showToast(`Error applying cues: ${err.message}`)
  }
}

function mockSseResponse(events, status = 200) {
  const eventText = events.map(e => `data: ${JSON.stringify(e)}\n\n`).join('')
  const bytes = new TextEncoder().encode(eventText)
  let consumed = false
  const reader = {
    read: async () => {
      if (!consumed) { consumed = true; return { done: false, value: bytes } }
      return { done: true, value: undefined }
    },
  }
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 409 ? 'Conflict' : 'OK',
    json: async () => ({ detail: 'Rekordbox is running — close it before applying cues' }),
    body: { getReader: () => reader },
  })
}

function mockResponse(body, status = 200) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 409 ? 'Conflict' : 'OK',
    json: async () => body,
  })
}

describe('colorTracksByBpm — HTTP error handling', () => {
  it('shows error toast when server returns 409', async () => {
    const { showToast, messages } = makeToastCapture()
    const fetch = mockResponse({ detail: 'Rekordbox is running — close it before coloring tracks' }, 409)
    await colorTracksByBpm_fn([1, 2], fetch, showToast)
    expect(messages[0]).toContain('Color by BPM failed')
    expect(messages[0]).toContain('Rekordbox is running')
    expect(messages[0]).not.toContain('undefined')
  })

  it('shows count on success without backup when backup_path is null', async () => {
    const { showToast, messages } = makeToastCapture()
    const fetch = mockResponse({ colored: 42, skipped: 0, dry_run: false, backup_path: null })
    await colorTracksByBpm_fn([1], fetch, showToast)
    expect(messages[0]).toBe('Colored 42 track(s) by BPM')
    expect(messages[0]).not.toContain('undefined')
  })

  it('includes backup note when backup_path is present', async () => {
    const { showToast, messages } = makeToastCapture()
    const fetch = mockResponse({ colored: 10, skipped: 0, dry_run: false, backup_path: '/some/path' })
    await colorTracksByBpm_fn([1], fetch, showToast)
    expect(messages[0]).toContain('Colored 10 track(s) by BPM')
    expect(messages[0]).toContain('backup saved')
  })
})

describe('applyToRekordbox — SSE error handling and progress', () => {
  it('shows error toast when server returns 409', async () => {
    const { showToast, messages } = makeToastCapture()
    const fetch = mockSseResponse([], 409)
    await applyToRekordbox_fn(fetch, showToast)
    expect(messages[0]).toContain('Error applying cues')
    expect(messages[0]).toContain('Rekordbox is running')
    expect(messages[0]).not.toContain('undefined')
  })

  it('shows applied count on success without backup when backup_path is null', async () => {
    const { showToast, messages } = makeToastCapture()
    const events = [
      { processed: 1, total: 1, applied: 1, skipped: 0 },
      { done: true, applied: 1, skipped: 0, backup_path: null },
    ]
    const fetch = mockSseResponse(events)
    await applyToRekordbox_fn(fetch, showToast)
    expect(messages[0]).toBe('Applied 1 track(s)')
    expect(messages[0]).not.toContain('undefined')
  })

  it('includes backup note when backup_path is present', async () => {
    const { showToast, messages } = makeToastCapture()
    const events = [
      { done: true, applied: 7, skipped: 0, backup_path: '/some/backup.db' },
    ]
    const fetch = mockSseResponse(events)
    await applyToRekordbox_fn(fetch, showToast)
    expect(messages[0]).toContain('Applied 7 track(s)')
    expect(messages[0]).toContain('backup saved to ~/.autocue/backups/')
    expect(messages[0]).not.toContain('undefined')
  })

  it('calls onProgress for each non-done SSE event', async () => {
    const { showToast } = makeToastCapture()
    const progressCalls = []
    const events = [
      { processed: 1, total: 3, applied: 1, skipped: 0 },
      { processed: 2, total: 3, applied: 2, skipped: 0 },
      { processed: 3, total: 3, applied: 3, skipped: 0 },
      { done: true, applied: 3, skipped: 0, backup_path: null },
    ]
    const fetch = mockSseResponse(events)
    await applyToRekordbox_fn(fetch, showToast, ev => progressCalls.push(ev))
    expect(progressCalls).toHaveLength(3)
    expect(progressCalls[0].processed).toBe(1)
    expect(progressCalls[2].processed).toBe(3)
  })

  it('shows error toast on network failure', async () => {
    const { showToast, messages } = makeToastCapture()
    const failFetch = vi.fn().mockRejectedValue(new Error('Network error'))
    await applyToRekordbox_fn(failFetch, showToast)
    expect(messages[0]).toContain('Error applying cues')
    expect(messages[0]).toContain('Network error')
  })
})

// ── 4. filteredTracks — new filter logic ─────────────────────────────────────
// Extracted from docs/index.html; accepts (tracks, filterState) instead of
// reading global variables, so it can be unit-tested without a DOM.

function filteredTracks(parsedTracks, {
  phraseOnlyFilter = false,
  searchQuery = '',
  ratingFilter = 0,
  playsFilter = 'all',
  lastPlayedFilter = 'all',
  myTagFilter = '',
} = {}) {
  let tracks = parsedTracks
  if (phraseOnlyFilter) tracks = tracks.filter(t => t.hasPhrase)
  if (searchQuery) {
    const q = searchQuery.toLowerCase()
    tracks = tracks.filter(t =>
      (t.name || '').toLowerCase().includes(q) ||
      (t.artist || '').toLowerCase().includes(q)
    )
  }
  if (ratingFilter > 0) tracks = tracks.filter(t => t.rating >= ratingFilter)
  if (playsFilter === 'played') tracks = tracks.filter(t => t.playCount > 0)
  else if (playsFilter === 'unplayed') tracks = tracks.filter(t => t.playCount === 0)
  if (lastPlayedFilter !== 'all') {
    if (lastPlayedFilter === 'never') {
      tracks = tracks.filter(t => !t.lastPlayed)
    } else {
      const days = lastPlayedFilter === '7d' ? 7 : 30
      const cutoff = new Date(Date.now() - days * 86400000).toISOString()
      tracks = tracks.filter(t => t.lastPlayed && t.lastPlayed >= cutoff)
    }
  }
  if (myTagFilter) tracks = tracks.filter(t => (t.myTags || []).includes(myTagFilter))
  return tracks
}

const sampleTracks = [
  { id: '1', name: 'Acid Rain', artist: 'Burial', hasPhrase: true,  rating: 5, playCount: 10, lastPlayed: new Date(Date.now() - 2 * 86400000).toISOString(), myTags: ['Techno'] },
  { id: '2', name: 'Midnight',  artist: 'Aphex',  hasPhrase: false, rating: 3, playCount: 0,  lastPlayed: null, myTags: [] },
  { id: '3', name: 'Flux',      artist: 'Burial', hasPhrase: true,  rating: 1, playCount: 5,  lastPlayed: new Date(Date.now() - 40 * 86400000).toISOString(), myTags: ['House', 'Techno'] },
]

describe('filteredTracks — phrase-only', () => {
  it('returns all tracks when filter is off', () => {
    expect(filteredTracks(sampleTracks)).toHaveLength(3)
  })

  it('returns only phrase tracks when phraseOnlyFilter is true', () => {
    const result = filteredTracks(sampleTracks, { phraseOnlyFilter: true })
    expect(result).toHaveLength(2)
    expect(result.every(t => t.hasPhrase)).toBe(true)
  })
})

describe('filteredTracks — search', () => {
  it('filters by track name (case-insensitive)', () => {
    const result = filteredTracks(sampleTracks, { searchQuery: 'acid' })
    expect(result).toHaveLength(1)
    expect(result[0].name).toBe('Acid Rain')
  })

  it('filters by artist name', () => {
    const result = filteredTracks(sampleTracks, { searchQuery: 'burial' })
    expect(result).toHaveLength(2)
  })

  it('empty query returns all tracks', () => {
    expect(filteredTracks(sampleTracks, { searchQuery: '' })).toHaveLength(3)
  })
})

describe('filteredTracks — rating filter', () => {
  it('ratingFilter=0 returns all tracks', () => {
    expect(filteredTracks(sampleTracks, { ratingFilter: 0 })).toHaveLength(3)
  })

  it('ratingFilter=3 returns tracks with rating >= 3', () => {
    const result = filteredTracks(sampleTracks, { ratingFilter: 3 })
    expect(result).toHaveLength(2)
    expect(result.every(t => t.rating >= 3)).toBe(true)
  })

  it('ratingFilter=5 returns only 5-star tracks', () => {
    const result = filteredTracks(sampleTracks, { ratingFilter: 5 })
    expect(result).toHaveLength(1)
    expect(result[0].rating).toBe(5)
  })
})

describe('filteredTracks — plays filter', () => {
  it('"played" keeps only tracks with playCount > 0', () => {
    const result = filteredTracks(sampleTracks, { playsFilter: 'played' })
    expect(result).toHaveLength(2)
    expect(result.every(t => t.playCount > 0)).toBe(true)
  })

  it('"unplayed" keeps only tracks with playCount === 0', () => {
    const result = filteredTracks(sampleTracks, { playsFilter: 'unplayed' })
    expect(result).toHaveLength(1)
    expect(result[0].playCount).toBe(0)
  })

  it('"all" returns all tracks', () => {
    expect(filteredTracks(sampleTracks, { playsFilter: 'all' })).toHaveLength(3)
  })
})

describe('filteredTracks — last-played filter', () => {
  it('"never" keeps only tracks with no lastPlayed date', () => {
    const result = filteredTracks(sampleTracks, { lastPlayedFilter: 'never' })
    expect(result).toHaveLength(1)
    expect(result[0].lastPlayed).toBeNull()
  })

  it('"7d" keeps only tracks played in the last 7 days', () => {
    const result = filteredTracks(sampleTracks, { lastPlayedFilter: '7d' })
    expect(result).toHaveLength(1)
    expect(result[0].name).toBe('Acid Rain') // played 2 days ago
  })

  it('"30d" keeps tracks played in the last 30 days', () => {
    const result = filteredTracks(sampleTracks, { lastPlayedFilter: '30d' })
    expect(result).toHaveLength(1)
    expect(result[0].name).toBe('Acid Rain') // the 40-day-old one is excluded
  })

  it('"all" returns all tracks', () => {
    expect(filteredTracks(sampleTracks, { lastPlayedFilter: 'all' })).toHaveLength(3)
  })
})

describe('filteredTracks — My Tag filter', () => {
  it('returns only tracks that include the selected tag', () => {
    const result = filteredTracks(sampleTracks, { myTagFilter: 'Techno' })
    expect(result).toHaveLength(2)
    expect(result.every(t => t.myTags.includes('Techno'))).toBe(true)
  })

  it('empty tag filter returns all tracks', () => {
    expect(filteredTracks(sampleTracks, { myTagFilter: '' })).toHaveLength(3)
  })

  it('tag not present on any track returns empty', () => {
    expect(filteredTracks(sampleTracks, { myTagFilter: 'Ambient' })).toHaveLength(0)
  })
})

describe('filteredTracks — combined filters', () => {
  it('phrase-only + rating + tag applied together', () => {
    // phraseOnly: keeps ids 1,3 — rating>=3: keeps id 1 — tag Techno: keeps id 1
    const result = filteredTracks(sampleTracks, {
      phraseOnlyFilter: true, ratingFilter: 3, myTagFilter: 'Techno',
    })
    expect(result).toHaveLength(1)
    expect(result[0].id).toBe('1')
  })
})

// ── 5. colorTracksByBpm — skip_colored field ─────────────────────────────────

async function colorTracksByBpm_with_skip_fn(trackIds, skipColored, fetchImpl, showToast) {
  try {
    const r = await fetchImpl('/api/color-tracks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: trackIds, dry_run: false, skip_colored: skipColored }),
    })
    const resp = await r.json()
    if (!r.ok) {
      showToast(`Color by BPM failed: ${resp.detail || r.statusText}`)
    } else {
      const backupNote = resp.backup_path ? ' — backup saved to ~/.autocue/backups/' : ''
      showToast(`Colored ${resp.colored} track(s) by BPM${backupNote}`)
    }
  } catch (err) {
    showToast(`Color by BPM failed: ${err.message}`)
  }
}

describe('colorTracksByBpm — skip_colored field', () => {
  it('sends skip_colored: true when checkbox is checked', async () => {
    const { showToast } = makeToastCapture()
    const fetchSpy = mockResponse({ colored: 5, skipped: 3, dry_run: false, backup_path: null })
    await colorTracksByBpm_with_skip_fn([1, 2], true, fetchSpy, showToast)
    const body = JSON.parse(fetchSpy.mock.calls[0][1].body)
    expect(body.skip_colored).toBe(true)
  })

  it('sends skip_colored: false when checkbox is unchecked', async () => {
    const { showToast } = makeToastCapture()
    const fetchSpy = mockResponse({ colored: 8, skipped: 0, dry_run: false, backup_path: null })
    await colorTracksByBpm_with_skip_fn([1], false, fetchSpy, showToast)
    const body = JSON.parse(fetchSpy.mock.calls[0][1].body)
    expect(body.skip_colored).toBe(false)
  })

  it('shows skipped count is implied in success toast', async () => {
    const { showToast, messages } = makeToastCapture()
    const fetchSpy = mockResponse({ colored: 5, skipped: 3, dry_run: false, backup_path: null })
    await colorTracksByBpm_with_skip_fn([1, 2, 3, 4, 5, 6, 7, 8], true, fetchSpy, showToast)
    expect(messages[0]).toContain('Colored 5 track(s)')
  })
})

// ── 6. applyToRekordbox — add_fill_cues in request body ─────────────────────

async function applyWithFillCues_fn(addFillCues, fetchImpl, showToast) {
  try {
    const r = await fetchImpl('/api/generate-apply-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: [1], add_fill_cues: addFillCues }),
    })
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText) }
    const reader = r.body.getReader()
    const decoder = new TextDecoder()
    let buf = '', finalData = null
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n'); buf = lines.pop()
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const ev = JSON.parse(line.slice(6))
        if (ev.done) finalData = ev
      }
    }
    if (finalData) showToast(`Applied ${finalData.applied} track(s)`)
  } catch (err) { showToast(`Error: ${err.message}`) }
}

describe('applyToRekordbox — add_fill_cues in request body', () => {
  it('sends add_fill_cues: true when checkbox checked', async () => {
    const { showToast } = makeToastCapture()
    const fetchSpy = mockSseResponse([{ done: true, applied: 1, skipped: 0, backup_path: null }])
    await applyWithFillCues_fn(true, fetchSpy, showToast)
    const body = JSON.parse(fetchSpy.mock.calls[0][1].body)
    expect(body.add_fill_cues).toBe(true)
  })

  it('sends add_fill_cues: false when checkbox unchecked', async () => {
    const { showToast } = makeToastCapture()
    const fetchSpy = mockSseResponse([{ done: true, applied: 1, skipped: 0, backup_path: null }])
    await applyWithFillCues_fn(false, fetchSpy, showToast)
    const body = JSON.parse(fetchSpy.mock.calls[0][1].body)
    expect(body.add_fill_cues).toBe(false)
  })
})

// ── 7. ensureLocalAudio — fetch-based audio loading ──────────────────────────

async function ensureLocalAudio_fn(trackId, audioState, fetchImpl, showToast) {
  if (audioState[trackId]) return  // already loaded
  try {
    const resp = await fetchImpl(`/api/tracks/${trackId}/audio`)
    if (!resp.ok) { showToast('Audio file not found on disk'); return }
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    audioState[trackId] = { file: null, objectUrl: url, artworkUrl: null }
  } catch (e) {
    showToast(`Could not load audio: ${e.message}`)
  }
}

// Minimal URL.createObjectURL stub for jsdom
if (typeof URL.createObjectURL === 'undefined') {
  URL.createObjectURL = () => 'blob:mock-url'
}

describe('ensureLocalAudio — fetch-based audio loading', () => {
  it('does nothing if audio already loaded', async () => {
    const audioState = { '42': { objectUrl: 'blob:existing' } }
    const { showToast, messages } = makeToastCapture()
    const fetchSpy = vi.fn()
    await ensureLocalAudio_fn('42', audioState, fetchSpy, showToast)
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(messages).toHaveLength(0)
  })

  it('fetches audio and stores objectUrl on success', async () => {
    const audioState = {}
    const { showToast } = makeToastCapture()
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: true,
      blob: async () => new Blob([new Uint8Array(4)], { type: 'audio/mpeg' }),
    })
    await ensureLocalAudio_fn('1', audioState, fetchSpy, showToast)
    expect(audioState['1']).toBeDefined()
    expect(audioState['1'].objectUrl).toBeDefined()
  })

  it('shows toast on 404', async () => {
    const audioState = {}
    const { showToast, messages } = makeToastCapture()
    const fetchSpy = vi.fn().mockResolvedValue({ ok: false, status: 404 })
    await ensureLocalAudio_fn('1', audioState, fetchSpy, showToast)
    expect(messages[0]).toContain('not found on disk')
    expect(audioState['1']).toBeUndefined()
  })

  it('shows toast on network error', async () => {
    const audioState = {}
    const { showToast, messages } = makeToastCapture()
    const fetchSpy = vi.fn().mockRejectedValue(new Error('Network timeout'))
    await ensureLocalAudio_fn('1', audioState, fetchSpy, showToast)
    expect(messages[0]).toContain('Could not load audio')
    expect(messages[0]).toContain('Network timeout')
  })
})

// ── 8. Two-step restore confirmation (no confirm() dialog) ───────────────────
// Tests the arming logic: first click arms, second click fires.

function makeRestoreConfirmFlow(doRestore) {
  let armed = false
  return {
    click: async () => {
      if (!armed) { armed = true; return 'armed' }
      armed = false
      await doRestore()
      return 'fired'
    },
    cancel: () => { armed = false },
    isArmed: () => armed,
  }
}

describe('restore confirm — two-step, no native dialog', () => {
  it('first click arms without firing restore', async () => {
    const restored = []
    const flow = makeRestoreConfirmFlow(() => restored.push(1))
    const result = await flow.click()
    expect(result).toBe('armed')
    expect(restored).toHaveLength(0)
    expect(flow.isArmed()).toBe(true)
  })

  it('second click fires restore', async () => {
    const restored = []
    const flow = makeRestoreConfirmFlow(() => restored.push(1))
    await flow.click()        // arm
    await flow.click()        // fire
    expect(restored).toHaveLength(1)
    expect(flow.isArmed()).toBe(false)
  })

  it('cancel resets armed state without firing restore', async () => {
    const restored = []
    const flow = makeRestoreConfirmFlow(() => restored.push(1))
    await flow.click()        // arm
    flow.cancel()             // reset
    expect(flow.isArmed()).toBe(false)
    expect(restored).toHaveLength(0)
  })

  it('requires rearming after cancel', async () => {
    const restored = []
    const flow = makeRestoreConfirmFlow(() => restored.push(1))
    await flow.click()        // arm
    flow.cancel()
    await flow.click()        // arms again (does not fire)
    expect(restored).toHaveLength(0)
    expect(flow.isArmed()).toBe(true)
  })
})
