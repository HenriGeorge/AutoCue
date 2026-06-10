/**
 * Tests for the Duplicate Tracks panel (Library tab).
 *
 * The frontend's `_pickKeeper` helper MUST agree with the backend's
 * `autocue.analysis.duplicates.pick_keeper`. The Python version is the
 * source of truth (the API echoes `is_keeper` in the SSE payload), but
 * a duplicate frontend implementation keeps the UI responsive even when
 * the backend version drifts and lets us highlight the keeper without
 * waiting for the server to recompute.
 *
 * Keep in sync with docs/index.html.
 */

import { describe, it, expect } from 'vitest'

// Mirror of the helper in docs/index.html. The tuple comparison is the
// same as the Python `max(... key=(plays, hot_cues, last_played, -id))`.
function pickKeeper(copies) {
  let best = copies[0]
  for (let i = 1; i < copies.length; i++) {
    const c = copies[i]
    const aKey = [
      best.play_count || 0,
      best.existing_hot_cues || 0,
      best.last_played || '',
      -best.track_id,
    ]
    const bKey = [
      c.play_count || 0,
      c.existing_hot_cues || 0,
      c.last_played || '',
      -c.track_id,
    ]
    let take = false
    for (let j = 0; j < aKey.length; j++) {
      if (bKey[j] > aKey[j]) { take = true; break }
      if (bKey[j] < aKey[j]) { break }
    }
    if (take) best = c
  }
  return best.track_id
}

const T = (track_id, overrides = {}) => ({
  track_id,
  play_count: 0,
  existing_hot_cues: 0,
  last_played: null,
  ...overrides,
})

describe('_pickKeeper — frontend mirror of the Python keeper heuristic', () => {
  it('highest play_count wins', () => {
    expect(pickKeeper([T(1, { play_count: 2 }), T(2, { play_count: 5 })])).toBe(2)
  })

  it('falls back to existing_hot_cues when plays tied', () => {
    expect(
      pickKeeper([
        T(1, { play_count: 3, existing_hot_cues: 2 }),
        T(2, { play_count: 3, existing_hot_cues: 8 }),
      ]),
    ).toBe(2)
  })

  it('falls back to last_played when plays + hot_cues tied', () => {
    expect(
      pickKeeper([
        T(1, { play_count: 3, existing_hot_cues: 4, last_played: '2024-01-01 00:00:00' }),
        T(2, { play_count: 3, existing_hot_cues: 4, last_played: '2026-06-10 00:00:00' }),
      ]),
    ).toBe(2)
  })

  it('lowest track_id breaks a final tie deterministically', () => {
    expect(pickKeeper([T(7), T(3), T(5)])).toBe(3)
  })

  it('missing last_played loses to any real date when other fields equal', () => {
    expect(
      pickKeeper([
        T(1, { play_count: 0, existing_hot_cues: 0, last_played: null }),
        T(2, { play_count: 0, existing_hot_cues: 0, last_played: '2025-01-01 00:00:00' }),
      ]),
    ).toBe(2)
  })

  it('agrees with the Python implementation on the live-probe case', () => {
    // The actual /api/duplicates SSE payload for one group from the live
    // 3,775-track scan: track 25211691 had is_keeper=true.
    const copies = [
      T(168889658, { existing_hot_cues: 8, play_count: 0, last_played: null }),
      T(25211691, { existing_hot_cues: 8, play_count: 0, last_played: null }),
      T(128548339, { existing_hot_cues: 8, play_count: 0, last_played: null }),
      T(236080888, { existing_hot_cues: 8, play_count: 0, last_played: null }),
    ]
    // All fields equal → lowest track_id wins. 25211691 < every other id.
    expect(pickKeeper(copies)).toBe(25211691)
  })
})
