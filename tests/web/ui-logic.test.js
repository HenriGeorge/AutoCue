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

function computeCues(track, { memoryCueMode = 'none', skipExisting = false, analysisMode = 'bar',
                              phraseCueState = {}, barsInterval = 16, startBar = 1, maxCues = 8 } = {}) {
  if (skipExisting && track.existingHotCues > 0) return []
  let cues
  if (analysisMode === 'phrase' && phraseCueState[track.id]?.length) {
    cues = phraseCueState[track.id].map(c => ({
      slot: c.slot, posSec: c.position_ms / 1000,
      label: c.label, isPhrase: true, name: c.name || '',
    }))
  } else {
    cues = generateCues(track, barsInterval, startBar, maxCues).map(c => ({
      ...c,
      hasPhrase: !!(track.has_phrase),
    }))
  }
  if (memoryCueMode !== 'none' && cues.length) {
    const hotCues = cues.filter(c => c.slot !== -1)
    const loadPos = analysisMode === 'phrase' && hotCues.length
      ? Math.min(...hotCues.map(c => c.posSec))
      : 0
    const memCues = [{ slot: -1, posSec: loadPos, label: '', name: 'Load Point', color_id: 0 }]
    if (memoryCueMode === 'all' && analysisMode === 'phrase') {
      const mixIn = hotCues.find(c => c.slot === 0)
      if (mixIn && Math.abs(mixIn.posSec - loadPos) > 0.5) {
        memCues.push({ slot: -1, posSec: mixIn.posSec, label: '', name: 'Mix In', color_id: 5 })
      }
      const outros = hotCues.filter(c => c.label === 'Outro')
      if (outros.length) {
        const outroPos = Math.max(...outros.map(c => c.posSec))
        memCues.push({ slot: -1, posSec: outroPos, label: '', name: 'Mix Out', color_id: 3 })
      }
    }
    memCues.sort((a, b) => a.posSec - b.posSec)
    cues = [...memCues, ...cues]
  }
  return cues
}

const baseTrack = {
  id: '1', name: 'Test', existingHotCues: 0, totalTime: 300,
  tempo: { bpm: 128, inizio: 0, beatsPerBar: 4 },
}

describe('computeCues — memory cue', () => {
  it('no memory cue by default (memoryCueMode=none)', () => {
    const cues = computeCues(baseTrack)
    expect(cues.every(c => c.slot !== -1)).toBe(true)
  })

  it('prepends Load Point when memoryCueMode=load_only', () => {
    const cues = computeCues(baseTrack, { memoryCueMode: 'load_only' })
    expect(cues[0].slot).toBe(-1)
    expect(cues[0].name).toBe('Load Point')
    expect(cues[0].color_id).toBe(0)
  })

  it('Load Point is at posSec=0 in bar mode', () => {
    const cues = computeCues(baseTrack, { memoryCueMode: 'load_only', analysisMode: 'bar' })
    expect(cues[0].posSec).toBe(0)
  })

  it('Load Point anchors to first phrase position in phrase mode', () => {
    const phraseCueState = {
      '1': [{ slot: 0, position_ms: 5000, label: 'Intro', name: 'Intro' }],
    }
    const cues = computeCues(baseTrack, {
      memoryCueMode: 'load_only', analysisMode: 'phrase', phraseCueState,
    })
    expect(cues[0].slot).toBe(-1)
    expect(cues[0].posSec).toBe(5)
  })

  it('does not prepend memory cue when cues list is empty', () => {
    const noBpmTrack = { ...baseTrack, tempo: null }
    const cues = computeCues(noBpmTrack, { memoryCueMode: 'load_only' })
    expect(cues).toHaveLength(0)
  })

  it('hot cue slots are unaffected and start at 0', () => {
    const cues = computeCues(baseTrack, { memoryCueMode: 'load_only' })
    expect(cues[1].slot).toBe(0)
    expect(cues[1].name).toBe('Bar 1')
  })

  it('returns empty when skipExisting and track has hot cues', () => {
    const track = { ...baseTrack, existingHotCues: 3 }
    const cues = computeCues(track, { memoryCueMode: 'load_only', skipExisting: true })
    expect(cues).toHaveLength(0)
  })

  it('all mode in phrase adds Mix-In and Mix-Out', () => {
    // Intro at 0 → Load Point; Drop at 8s → Mix-In (slot 0); Outro → Mix-Out
    const phraseCueState = {
      '1': [
        { slot: 1, position_ms: 0,     label: 'Intro',  name: 'Intro' },
        { slot: 0, position_ms: 8000,  label: 'Chorus', name: 'Drop (Mix In)' },
        { slot: 2, position_ms: 60000, label: 'Outro',  name: 'Outro' },
      ],
    }
    const cues = computeCues(baseTrack, {
      memoryCueMode: 'all', analysisMode: 'phrase', phraseCueState,
    })
    const memNames = cues.filter(c => c.slot === -1).map(c => c.name)
    expect(memNames).toContain('Load Point')
    expect(memNames).toContain('Mix In')
    expect(memNames).toContain('Mix Out')
  })

  it('all mode Mix-In is green (color_id=5)', () => {
    // Intro at 0ms (→ Load Point), slot-0 = Drop at 8000ms (→ Mix-In)
    const phraseCueState = {
      '1': [
        { slot: 1, position_ms: 0,     label: 'Intro',  name: 'Intro' },
        { slot: 0, position_ms: 8000,  label: 'Chorus', name: 'Drop (Mix In)' },
        { slot: 2, position_ms: 60000, label: 'Outro',  name: 'Outro' },
      ],
    }
    const cues = computeCues(baseTrack, {
      memoryCueMode: 'all', analysisMode: 'phrase', phraseCueState,
    })
    const mixIn = cues.find(c => c.slot === -1 && c.name === 'Mix In')
    expect(mixIn?.color_id).toBe(5)
  })

  it('all mode Mix-Out is orange (color_id=3)', () => {
    const phraseCueState = {
      '1': [
        { slot: 1, position_ms: 0,     label: 'Intro',  name: 'Intro' },
        { slot: 0, position_ms: 8000,  label: 'Chorus', name: 'Drop (Mix In)' },
        { slot: 2, position_ms: 60000, label: 'Outro',  name: 'Outro' },
      ],
    }
    const cues = computeCues(baseTrack, {
      memoryCueMode: 'all', analysisMode: 'phrase', phraseCueState,
    })
    const mixOut = cues.find(c => c.slot === -1 && c.name === 'Mix Out')
    expect(mixOut?.color_id).toBe(3)
  })

  it('all mode in bar mode only adds Load Point', () => {
    const cues = computeCues(baseTrack, { memoryCueMode: 'all', analysisMode: 'bar' })
    const memCues = cues.filter(c => c.slot === -1)
    expect(memCues).toHaveLength(1)
    expect(memCues[0].name).toBe('Load Point')
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

// ── 8. Backup checklist DOM logic ────────────────────────────────────────────
// Inline copies of _populateChecklist, _updateSelectionCount, _checkedBackups
// extracted from docs/index.html. If you change them there, update here.

function setupBackupDOM() {
  document.body.innerHTML = `
    <div id="backup-checklist"></div>
    <input type="checkbox" id="backup-select-all">
    <span id="backup-select-count"></span>
  `
}

const sampleBackups = [
  { filename: 'master_20260601T120000.db', created_at: '2026-06-01 12:00:00', size_mb: '2.3' },
  { filename: 'master_20260531T080000.db', created_at: '2026-05-31 08:00:00', size_mb: '2.1' },
]

function _updateSelectionCount_fn() {
  const checkboxes = document.querySelectorAll('#backup-checklist input[type=checkbox]')
  const checked = [...checkboxes].filter(c => c.checked)
  const count = checked.length, total = checkboxes.length
  const allCb = document.getElementById('backup-select-all')
  allCb.checked = count === total && total > 0
  allCb.indeterminate = count > 0 && count < total
  const countSpan = document.getElementById('backup-select-count')
  countSpan.textContent = count > 0 ? `${count} selected` : ''
}

function _checkedBackups_fn() {
  return [...document.querySelectorAll('#backup-checklist input[type=checkbox]:checked')].map(c => c.value)
}

function _populateChecklist_fn(backups) {
  const list = document.getElementById('backup-checklist')
  list.innerHTML = ''
  const allCb = document.getElementById('backup-select-all')
  allCb.checked = false; allCb.indeterminate = false
  _updateSelectionCount_fn()
  for (const b of backups) {
    const row = document.createElement('div')
    row.className = 'backup-row'
    const cb = document.createElement('input')
    cb.type = 'checkbox'; cb.value = b.filename
    cb.addEventListener('change', _updateSelectionCount_fn)
    const name = document.createElement('span')
    name.className = 'backup-name'; name.textContent = b.created_at
    const size = document.createElement('span')
    size.className = 'backup-size'; size.textContent = b.size_mb + ' MB'
    row.append(cb, name, size)
    row.addEventListener('click', e => { if (e.target === cb) return; cb.checked = !cb.checked; cb.dispatchEvent(new Event('change')) })
    list.appendChild(row)
  }
}

describe('_populateChecklist', () => {
  beforeEach(setupBackupDOM)

  it('renders one row per backup', () => {
    _populateChecklist_fn(sampleBackups)
    expect(document.querySelectorAll('.backup-row')).toHaveLength(2)
  })

  it('each row checkbox has correct filename value', () => {
    _populateChecklist_fn(sampleBackups)
    const cbs = document.querySelectorAll('#backup-checklist input[type=checkbox]')
    expect(cbs[0].value).toBe('master_20260601T120000.db')
    expect(cbs[1].value).toBe('master_20260531T080000.db')
  })

  it('no checkboxes are checked after population', () => {
    _populateChecklist_fn(sampleBackups)
    expect(document.querySelectorAll('#backup-checklist input[type=checkbox]:checked')).toHaveLength(0)
  })

  it('resets select-all checkbox to unchecked', () => {
    document.getElementById('backup-select-all').checked = true
    _populateChecklist_fn(sampleBackups)
    expect(document.getElementById('backup-select-all').checked).toBe(false)
  })

  it('displays created_at as backup name text', () => {
    _populateChecklist_fn(sampleBackups)
    expect(document.querySelectorAll('.backup-name')[0].textContent).toBe('2026-06-01 12:00:00')
  })
})

describe('_updateSelectionCount', () => {
  beforeEach(() => { setupBackupDOM(); _populateChecklist_fn(sampleBackups) })

  it('count span is empty when nothing selected', () => {
    _updateSelectionCount_fn()
    expect(document.getElementById('backup-select-count').textContent).toBe('')
  })

  it('shows "1 selected" when one checkbox is checked', () => {
    document.querySelectorAll('#backup-checklist input[type=checkbox]')[0].checked = true
    _updateSelectionCount_fn()
    expect(document.getElementById('backup-select-count').textContent).toBe('1 selected')
  })

  it('shows "2 selected" when all are checked', () => {
    document.querySelectorAll('#backup-checklist input[type=checkbox]').forEach(c => { c.checked = true })
    _updateSelectionCount_fn()
    expect(document.getElementById('backup-select-count').textContent).toBe('2 selected')
  })

  it('select-all is checked when all rows are checked', () => {
    document.querySelectorAll('#backup-checklist input[type=checkbox]').forEach(c => { c.checked = true })
    _updateSelectionCount_fn()
    expect(document.getElementById('backup-select-all').checked).toBe(true)
    expect(document.getElementById('backup-select-all').indeterminate).toBe(false)
  })

  it('select-all is indeterminate when some but not all are checked', () => {
    document.querySelectorAll('#backup-checklist input[type=checkbox]')[0].checked = true
    _updateSelectionCount_fn()
    expect(document.getElementById('backup-select-all').indeterminate).toBe(true)
    expect(document.getElementById('backup-select-all').checked).toBe(false)
  })
})

describe('_checkedBackups', () => {
  beforeEach(() => { setupBackupDOM(); _populateChecklist_fn(sampleBackups) })

  it('returns empty array when nothing checked', () => {
    expect(_checkedBackups_fn()).toEqual([])
  })

  it('returns filenames of checked items', () => {
    document.querySelectorAll('#backup-checklist input[type=checkbox]')[0].checked = true
    expect(_checkedBackups_fn()).toEqual(['master_20260601T120000.db'])
  })

  it('returns all filenames when all checked', () => {
    document.querySelectorAll('#backup-checklist input[type=checkbox]').forEach(c => { c.checked = true })
    expect(_checkedBackups_fn()).toEqual([
      'master_20260601T120000.db',
      'master_20260531T080000.db',
    ])
  })
})

// deleteCheckedBackups logic (extracted from delete-backup-btn handler)
async function deleteCheckedBackups_fn(filenames, fetchImpl, showToast, onRefresh) {
  let deletedCount = 0
  try {
    for (const filename of filenames) {
      const r = await fetchImpl(`/api/backups/${encodeURIComponent(filename)}`, { method: 'DELETE' })
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText) }
      deletedCount++
    }
    showToast(`Deleted ${deletedCount} backup${deletedCount > 1 ? 's' : ''}`)
    await onRefresh()
  } catch (e) { showToast(`Delete failed: ${e.message}`) }
}

describe('deleteCheckedBackups — multi-delete logic', () => {
  it('calls DELETE once per selected file', async () => {
    const { showToast } = makeToastCapture()
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    await deleteCheckedBackups_fn(['a.db', 'b.db'], fetchSpy, showToast, vi.fn())
    expect(fetchSpy).toHaveBeenCalledTimes(2)
    expect(fetchSpy.mock.calls[0][0]).toContain('a.db')
    expect(fetchSpy.mock.calls[1][0]).toContain('b.db')
  })

  it('shows "Deleted 1 backup" for single deletion', async () => {
    const { showToast, messages } = makeToastCapture()
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    await deleteCheckedBackups_fn(['a.db'], fetchSpy, showToast, vi.fn())
    expect(messages[0]).toBe('Deleted 1 backup')
  })

  it('shows "Deleted 3 backups" (plural) for multiple', async () => {
    const { showToast, messages } = makeToastCapture()
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    await deleteCheckedBackups_fn(['a.db', 'b.db', 'c.db'], fetchSpy, showToast, vi.fn())
    expect(messages[0]).toBe('Deleted 3 backups')
  })

  it('shows error toast on server 404', async () => {
    const { showToast, messages } = makeToastCapture()
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: false, statusText: 'Not Found',
      json: async () => ({ detail: 'Backup not found: a.db' }),
    })
    await deleteCheckedBackups_fn(['a.db'], fetchSpy, showToast, vi.fn())
    expect(messages[0]).toContain('Delete failed')
    expect(messages[0]).toContain('Backup not found: a.db')
  })

  it('calls onRefresh after successful deletion', async () => {
    const { showToast } = makeToastCapture()
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    const refreshFn = vi.fn()
    await deleteCheckedBackups_fn(['a.db'], fetchSpy, showToast, refreshFn)
    expect(refreshFn).toHaveBeenCalledOnce()
  })

  it('does not call onRefresh when deletion fails', async () => {
    const { showToast } = makeToastCapture()
    const fetchSpy = vi.fn().mockResolvedValue({
      ok: false, statusText: 'Not Found',
      json: async () => ({ detail: 'not found' }),
    })
    const refreshFn = vi.fn()
    await deleteCheckedBackups_fn(['a.db'], fetchSpy, showToast, refreshFn)
    expect(refreshFn).not.toHaveBeenCalled()
  })
})

// ── 9. Two-step restore confirmation (no confirm() dialog) ───────────────────
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

// ── _explainCue — cue explanation panel (docs/index.html ~line 3042) ─────────

function _explainCue(cue) {
  const slot = cue.slot;
  const label = cue.label || '';
  const conf = cue.confidence ?? 1.0;
  const mode = cue.phraseMode || (conf >= 0.9 ? 'phrase' : conf >= 0.5 ? 'bar' : 'heuristic');
  const bars = cue.phraseBars ?? 0;

  if (slot === -1) {
    return {
      confidence: 'Auto',
      reasons: [
        'CDJ load point (Auto Cue)',
        'Anchored to earliest phrase boundary',
      ],
    };
  }

  if (cue.confidence == null && cue.phraseMode == null) {
    return { confidence: '—', reasons: ['Manually placed cue'] };
  }

  const confLabel = conf >= 0.9 ? 'High' : conf >= 0.5 ? 'Medium' : 'Low';
  const reasons = [];

  if (mode === 'heuristic') {
    reasons.push('No BPM or phrase data — 30-second interval estimate');
    reasons.push(`Position: ${cue.name || ''}`);
    return { confidence: confLabel, reasons };
  }

  if (mode === 'bar') {
    if (cue.hasPhrase) {
      reasons.push('Using bar intervals — switch to ✨ Phrase mode to use Rekordbox phrase data');
    } else {
      reasons.push('Bar-interval fallback (no Rekordbox phrase analysis)');
      reasons.push('Run analysis in Rekordbox to enable phrase-based cues');
    }
    reasons.push(`Position: ${cue.name || ''}`);
    return { confidence: confLabel, reasons };
  }

  const LABEL_REASONS = {
    'Drop':   'Rekordbox phrase: Chorus (high-energy section)',
    'Build':  'Rekordbox phrase: Up (energy rise)',
    'Break':  'Rekordbox phrase: Down (low-energy break)',
    'Intro':  'Rekordbox phrase: Intro',
    'Verse':  'Rekordbox phrase: Verse',
    'Bridge': 'Rekordbox phrase: Bridge',
    'Outro':  'Rekordbox phrase: Outro',
    'Fill':   'Rekordbox fill beat marker',
  };

  const baseName = (cue.name || label).replace(/\s+\d+$/, '');
  const phraseReason = LABEL_REASONS[baseName] || `Rekordbox phrase: ${baseName || label}`;
  reasons.push(phraseReason);

  if (bars > 0) reasons.push(`${bars}-bar phrase`);

  if (slot === 0) reasons.push('Slot A: mix-in point (first non-Intro phrase)');
  else if (baseName === 'Drop' || label === 'Chorus') reasons.push('Priority slot: main drop');
  else if (baseName === 'Build' || label === 'Up')    reasons.push('Priority slot: energy build');
  else if (baseName === 'Outro')                       reasons.push('Priority slot: outro/mix-out');

  return { confidence: confLabel, reasons };
}

describe('_explainCue — memory cue', () => {
  it('returns Auto confidence for memory cue (slot=-1)', () => {
    const result = _explainCue({ slot: -1, confidence: null, phraseMode: null })
    expect(result.confidence).toBe('Auto')
    expect(result.reasons).toContain('CDJ load point (Auto Cue)')
    expect(result.reasons).toContain('Anchored to earliest phrase boundary')
  })
})

describe('_explainCue — manually placed cue', () => {
  it('returns dash confidence when no AutoCue metadata present', () => {
    const result = _explainCue({ slot: 0, confidence: null, phraseMode: null })
    expect(result.confidence).toBe('—')
    expect(result.reasons).toEqual(['Manually placed cue'])
  })
})

describe('_explainCue — heuristic mode', () => {
  it('returns Low confidence for heuristic cues (conf=0.3)', () => {
    const result = _explainCue({ slot: 0, confidence: 0.3, phraseMode: 'heuristic', name: '0:30' })
    expect(result.confidence).toBe('Low')
    expect(result.reasons[0]).toContain('30-second interval estimate')
    expect(result.reasons[1]).toContain('0:30')
  })

  it('infers heuristic mode from conf < 0.5 when phraseMode absent', () => {
    const result = _explainCue({ slot: 0, confidence: 0.3, name: 'Bar 1' })
    expect(result.reasons[0]).toContain('30-second interval estimate')
  })
})

describe('_explainCue — bar mode', () => {
  it('returns Medium confidence for bar cues (conf=0.6)', () => {
    const result = _explainCue({ slot: 0, confidence: 0.6, phraseMode: 'bar', name: 'Bar 1' })
    expect(result.confidence).toBe('Medium')
    expect(result.reasons[0]).toContain('Bar-interval fallback')
    expect(result.reasons.some(r => r.includes('Bar 1'))).toBe(true)
  })

  it('infers bar mode from conf in [0.5, 0.9) when phraseMode absent', () => {
    const result = _explainCue({ slot: 0, confidence: 0.6, name: 'Bar 17' })
    expect(result.reasons[0]).toContain('Bar-interval fallback')
  })

  it('reasons list is never empty for bar cues', () => {
    const result = _explainCue({ slot: 3, confidence: 0.6, phraseMode: 'bar', name: '' })
    expect(result.reasons.length).toBeGreaterThan(0)
  })

  it('bar mode with hasPhrase=true suggests switching to Phrase mode', () => {
    const cue = { slot: 0, confidence: 0.6, phraseMode: 'bar', phraseBars: 4, label: '', name: 'Cue 1', hasPhrase: true };
    const result = _explainCue(cue);
    expect(result.reasons.some(r => r.includes('switch') || r.includes('Phrase mode'))).toBe(true);
    expect(result.reasons.some(r => r.includes('no Rekordbox phrase analysis'))).toBe(false);
  })

  it('bar mode with hasPhrase=false shows no phrase analysis message', () => {
    const cue = { slot: 0, confidence: 0.6, phraseMode: 'bar', phraseBars: 4, label: '', name: 'Cue 1', hasPhrase: false };
    const result = _explainCue(cue);
    expect(result.reasons.some(r => r.includes('no Rekordbox phrase analysis'))).toBe(true);
  })
})

describe('_explainCue — phrase mode', () => {
  it('High confidence for phrase cues (conf=1.0)', () => {
    const result = _explainCue({ slot: 1, confidence: 1.0, phraseMode: 'phrase', label: 'Chorus', name: 'Drop', phraseBars: 8 })
    expect(result.confidence).toBe('High')
  })

  it('Drop cue includes Chorus phrase reason', () => {
    const result = _explainCue({ slot: 1, confidence: 1.0, phraseMode: 'phrase', label: 'Chorus', name: 'Drop', phraseBars: 0 })
    expect(result.reasons[0]).toContain('Chorus')
    expect(result.reasons[0]).toContain('high-energy')
  })

  it('Drop on non-slot-0 includes priority slot note', () => {
    const result = _explainCue({ slot: 1, confidence: 1.0, phraseMode: 'phrase', label: 'Chorus', name: 'Drop', phraseBars: 0 })
    expect(result.reasons).toContain('Priority slot: main drop')
  })

  it('slot 0 cue gets mix-in point annotation instead of priority slot', () => {
    const result = _explainCue({ slot: 0, confidence: 1.0, phraseMode: 'phrase', label: 'Up', name: 'Build', phraseBars: 0 })
    expect(result.reasons).toContain('Slot A: mix-in point (first non-Intro phrase)')
    expect(result.reasons).not.toContain('Priority slot: energy build')
  })

  it('phraseBars > 0 adds bar count reason', () => {
    const result = _explainCue({ slot: 0, confidence: 1.0, phraseMode: 'phrase', label: 'Intro', name: 'Intro', phraseBars: 16 })
    expect(result.reasons).toContain('16-bar phrase')
  })

  it('phraseBars = 0 does not add bar count reason', () => {
    const result = _explainCue({ slot: 0, confidence: 1.0, phraseMode: 'phrase', label: 'Intro', name: 'Intro', phraseBars: 0 })
    expect(result.reasons.some(r => r.includes('-bar phrase'))).toBe(false)
  })

  it('strips trailing number from cue name for label lookup (Drop 2 → Drop)', () => {
    const result = _explainCue({ slot: 2, confidence: 1.0, phraseMode: 'phrase', label: 'Chorus', name: 'Drop 2', phraseBars: 0 })
    expect(result.reasons[0]).toContain('Chorus')
  })

  it('unknown label falls back to generic phrase reason', () => {
    const result = _explainCue({ slot: 1, confidence: 1.0, phraseMode: 'phrase', label: '?', name: 'Unknown', phraseBars: 0 })
    expect(result.reasons[0]).toContain('Rekordbox phrase')
  })

  it('reasons list is never empty for phrase cues', () => {
    const result = _explainCue({ slot: 3, confidence: 1.0, phraseMode: 'phrase', label: '', name: '', phraseBars: 0 })
    expect(result.reasons.length).toBeGreaterThan(0)
  })

  it('Build cue on non-slot-0 includes energy build note', () => {
    const result = _explainCue({ slot: 2, confidence: 1.0, phraseMode: 'phrase', label: 'Up', name: 'Build', phraseBars: 0 })
    expect(result.reasons).toContain('Priority slot: energy build')
  })

  it('Outro cue includes mix-out annotation', () => {
    const result = _explainCue({ slot: 2, confidence: 1.0, phraseMode: 'phrase', label: 'Outro', name: 'Outro', phraseBars: 0 })
    expect(result.reasons).toContain('Priority slot: outro/mix-out')
  })
})
