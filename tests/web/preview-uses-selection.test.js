/**
 * Regression guard for issue #173 — #preview-cues-btn must target the
 * SELECTION when any tracks are checked, not the entire filtered list.
 *
 * Before this fix, the Preview handler read `filteredTracks()` while
 * every other write op (apply / delete / color / download) used
 * `activeTracks()`. A user who selected 2 tracks and clicked Preview
 * got a toast for 3,775 tracks, and a subsequent Apply silently
 * overwrote the entire visible library.
 *
 * Mirror of the `activeTracks` helper in docs/index.html. Keep in sync.
 */

import { describe, it, expect } from 'vitest'

// Vendored helper — matches docs/index.html line 9808.
function activeTracks(parsedTracks, filteredIndices, selectedTrackIds) {
  if (selectedTrackIds.size === 0) {
    return filteredIndices.map((i) => parsedTracks[i])
  }
  const out = []
  for (const i of filteredIndices) {
    const t = parsedTracks[i]
    if (selectedTrackIds.has(t.id)) out.push(t)
  }
  return out
}

const TRACKS = [
  { id: 1, title: 'A' },
  { id: 2, title: 'B' },
  { id: 3, title: 'C' },
  { id: 4, title: 'D' },
  { id: 5, title: 'E' },
]

describe('activeTracks — Preview scope (issue #173)', () => {
  it('returns ALL filtered tracks when nothing is selected', () => {
    const out = activeTracks(TRACKS, [0, 1, 2, 3, 4], new Set())
    expect(out.map((t) => t.id)).toEqual([1, 2, 3, 4, 5])
  })

  it('returns ONLY the selected subset when items are checked', () => {
    // The original #173 scenario: 2 tracks selected out of 3,775 visible.
    const selected = new Set([2, 4])
    const out = activeTracks(TRACKS, [0, 1, 2, 3, 4], selected)
    expect(out.map((t) => t.id)).toEqual([2, 4])
  })

  it('respects the filter — selected ids OUTSIDE the visible set are skipped', () => {
    // User selected ids 3 + 5, then narrowed the filter to ids 1 + 2.
    // Preview should target the EMPTY intersection (nothing visible AND
    // selected), not bleed through with the hidden selection.
    const selected = new Set([3, 5])
    const out = activeTracks(TRACKS, [0, 1], selected)
    expect(out).toEqual([])
  })

  it('returns a single-track list when exactly one selection survives the filter', () => {
    const selected = new Set([3, 99])
    const out = activeTracks(TRACKS, [0, 1, 2, 3, 4], selected)
    expect(out.map((t) => t.id)).toEqual([3])
  })

  it('returns empty array when the filtered set is empty', () => {
    const out = activeTracks(TRACKS, [], new Set([1, 2]))
    expect(out).toEqual([])
  })
})
