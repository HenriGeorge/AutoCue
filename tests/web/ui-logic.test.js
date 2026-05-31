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

const SORT_LABELS = { title: 'Title', artist: 'Artist', album: 'Album', bpm: 'BPM', key: 'Key' }

describe('SORT_LABELS', () => {
  it('has an entry for every sort key', () => {
    for (const k of ['title', 'artist', 'album', 'bpm', 'key']) {
      expect(SORT_LABELS[k]).toBeDefined()
    }
  })

  it('key maps to "Key" (not undefined)', () => {
    expect(SORT_LABELS['key']).toBe('Key')
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
