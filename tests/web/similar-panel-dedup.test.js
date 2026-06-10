/**
 * Regression guard for the similar-panel dedup typo: it used `t.title`
 * while `parsedTracks` rows store the song title under `t.name`. The
 * `(t.title || '').trim()` lookup silently produced `''` for every
 * entry, so every dedup key became `"<artist>|||"` and every same-
 * artist similar match collapsed into one row. Probe-verified against
 * a 3,775-track library: track 212087170's similar API returned 5
 * results, the panel rendered 1 row.
 *
 * Mirror of the dedup helper in docs/index.html. Keep in sync.
 */

import { describe, it, expect } from 'vitest'

// Vendored helper — matches docs/index.html after the typo fix.
function dedupSimilarResults(results, parsedTracks) {
  const lookup = {}
  for (const t of parsedTracks) lookup[String(t.id)] = t
  const seen = new Set()
  return results.filter((item) => {
    const t = lookup[String(item.track_id)]
    if (!t) return true
    const artistStr = (t.artist || '').toLowerCase().trim()
    const titleStr = (t.name || '').toLowerCase().trim()
    const key = `${artistStr}|||${titleStr}`
    if (!artistStr && !titleStr) return true
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

const TRACKS = [
  { id: 1, name: 'New Life (Original Mix)', artist: 'Ed Longo' },
  { id: 2, name: 'New Life (Original Mix)', artist: 'Ed Longo' },
  { id: 3, name: 'New Life (Original Mix)', artist: 'Ed Longo' },
  { id: 4, name: 'Arcadian Dream (Original Mix)', artist: 'Ed Longo' },
  { id: 5, name: 'Arcadian Dream (Original Mix)', artist: 'Ed Longo' },
  { id: 6, name: 'Hyperborea', artist: 'Tangerine Dream' },
  { id: 7, name: '', artist: '' }, // empty-metadata streaming track
]

describe('similar-panel dedup (typo fix)', () => {
  it('keeps DISTINCT same-artist tracks (the #1 regression case)', () => {
    // Track 212087170's API result: 3× "New Life" + 2× "Arcadian Dream".
    // Before the fix: 1 row. After: 2.
    const results = [
      { track_id: 1, score: 0.996, bpm_diff: 0 },
      { track_id: 2, score: 0.996, bpm_diff: 0 },
      { track_id: 3, score: 0.996, bpm_diff: 0 },
      { track_id: 4, score: 0.972, bpm_diff: 0.15 },
      { track_id: 5, score: 0.972, bpm_diff: 0.15 },
    ]
    const out = dedupSimilarResults(results, TRACKS)
    expect(out.map((r) => r.track_id)).toEqual([1, 4])
  })

  it('keeps cross-artist results (typo case where everything collapsed)', () => {
    const results = [
      { track_id: 1, score: 0.99, bpm_diff: 0 },
      { track_id: 6, score: 0.88, bpm_diff: 1.0 },
    ]
    const out = dedupSimilarResults(results, TRACKS)
    expect(out).toHaveLength(2)
  })

  it('still dedups exact (artist + title) duplicates from different track ids', () => {
    // The legit use case for the dedup — different ids, same import.
    const results = [
      { track_id: 1, score: 0.99, bpm_diff: 0 },
      { track_id: 2, score: 0.99, bpm_diff: 0 }, // same artist + title
    ]
    const out = dedupSimilarResults(results, TRACKS)
    expect(out).toHaveLength(1)
    expect(out[0].track_id).toBe(1)
  })

  it('keeps results whose track is not in parsedTracks (no lookup → keep)', () => {
    const results = [{ track_id: 999, score: 0.5, bpm_diff: 0 }]
    const out = dedupSimilarResults(results, TRACKS)
    expect(out).toEqual(results)
  })

  it('keeps empty-name+empty-artist rows so the user can still see them', () => {
    // Two streaming tracks with no metadata at all — they share an empty
    // key, but the dedup explicitly returns true for that case so we don't
    // silently swallow tracks with broken Rekordbox-side metadata.
    const results = [
      { track_id: 7, score: 0.9, bpm_diff: 0 },
      { track_id: 7, score: 0.9, bpm_diff: 0 }, // pretend two rows
    ]
    const out = dedupSimilarResults(results, TRACKS)
    expect(out).toHaveLength(2)
  })
})
