/**
 * Phase 3 frontend logic for the Duplicate Tracks panel:
 *   - _pickKeeper reorder (cues → plays → last → bitrate → -id) — mirror
 *     of the Python pick_keeper, must agree on the same inputs.
 *   - same-path-as-keeper chip predicate.
 *   - _onTracksDeleted surgical library-state invalidation.
 *   - focus-trap cycle logic.
 *
 * Vendored from docs/index.html — keep in sync.
 */

import { describe, it, expect } from 'vitest'

// ── _pickKeeper (phase 3 reorder) ────────────────────────────────────────────
function pickKeeper(copies) {
  const keyOf = (c) => [
    c.existing_hot_cues || 0,
    c.play_count || 0,
    c.last_played || '',
    c.bitrate || 0,
    -c.track_id,
  ]
  let best = copies[0]
  for (let i = 1; i < copies.length; i++) {
    const c = copies[i]
    const a = keyOf(best), b = keyOf(c)
    let take = false
    for (let j = 0; j < a.length; j++) {
      if (b[j] > a[j]) { take = true; break }
      if (b[j] < a[j]) { break }
    }
    if (take) best = c
  }
  return best.track_id
}

const T = (id, o = {}) => ({
  track_id: id, existing_hot_cues: 0, play_count: 0,
  last_played: null, bitrate: 0, ...o,
})

describe('_pickKeeper — phase 3 order (cues first)', () => {
  it('cue-prep beats play history (grill Scenario B)', () => {
    expect(pickKeeper([
      T(1, { play_count: 50, existing_hot_cues: 0 }),
      T(2, { play_count: 0, existing_hot_cues: 8 }),
    ])).toBe(2)
  })

  it('plays decide when cues tied', () => {
    expect(pickKeeper([
      T(1, { existing_hot_cues: 4, play_count: 2 }),
      T(2, { existing_hot_cues: 4, play_count: 5 }),
    ])).toBe(2)
  })

  it('bitrate breaks the tie before track_id', () => {
    expect(pickKeeper([
      T(1, { existing_hot_cues: 4, play_count: 3, last_played: '2025-01-01', bitrate: 192 }),
      T(2, { existing_hot_cues: 4, play_count: 3, last_played: '2025-01-01', bitrate: 320 }),
    ])).toBe(2)
  })

  it('lowest track_id is the final tiebreak', () => {
    expect(pickKeeper([T(7), T(3), T(5)])).toBe(3)
  })

  it('agrees with the live-probe Céline Gillain group (all equal → lowest id)', () => {
    const copies = [
      T(168889658, { existing_hot_cues: 8 }),
      T(25211691, { existing_hot_cues: 8 }),
      T(128548339, { existing_hot_cues: 8 }),
      T(236080888, { existing_hot_cues: 8 }),
    ]
    expect(pickKeeper(copies)).toBe(25211691)
  })
})

// ── same-path chip predicate ─────────────────────────────────────────────────
function samePathAsKeeper(copy, keeper) {
  return (
    `${copy.folder_path || ''}${copy.file_name || ''}` ===
    `${keeper.folder_path || ''}${keeper.file_name || ''}`
  )
}

describe('same-path-as-keeper chip', () => {
  const keeper = { folder_path: '/lib/', file_name: 'song.mp3' }

  it('true when path matches the keeper exactly', () => {
    expect(samePathAsKeeper({ folder_path: '/lib/', file_name: 'song.mp3' }, keeper)).toBe(true)
  })

  it('false when the file lives in a different folder', () => {
    expect(samePathAsKeeper({ folder_path: '/other/', file_name: 'song.mp3' }, keeper)).toBe(false)
  })

  it('false when the filename differs', () => {
    expect(samePathAsKeeper({ folder_path: '/lib/', file_name: 'song2.mp3' }, keeper)).toBe(false)
  })

  it('handles missing path fields as empty string', () => {
    expect(samePathAsKeeper({}, { folder_path: '', file_name: '' })).toBe(true)
  })
})

// ── _onTracksDeleted surgical invalidation ───────────────────────────────────
function onTracksDeleted(ids, state) {
  const gone = new Set(ids.map(Number))
  state.parsedTracks = state.parsedTracks.filter(t => !gone.has(Number(t.id)))
  gone.forEach(id => state.parsedTracksById.delete(String(id)))
  gone.forEach(id => { delete state.healthData[String(id)] })
}

describe('_onTracksDeleted', () => {
  it('removes deleted ids from parsedTracks, the id map, and healthData', () => {
    const state = {
      parsedTracks: [{ id: 1 }, { id: 2 }, { id: 3 }],
      parsedTracksById: new Map([['1', {}], ['2', {}], ['3', {}]]),
      healthData: { '1': {}, '2': {}, '3': {} },
    }
    onTracksDeleted([2, 3], state)
    expect(state.parsedTracks.map(t => t.id)).toEqual([1])
    expect(state.parsedTracksById.has('2')).toBe(false)
    expect(state.parsedTracksById.has('3')).toBe(false)
    expect(state.healthData['2']).toBeUndefined()
    expect(state.healthData['1']).toBeDefined()
  })

  it('is a no-op on an empty id list', () => {
    const state = {
      parsedTracks: [{ id: 1 }],
      parsedTracksById: new Map([['1', {}]]),
      healthData: { '1': {} },
    }
    onTracksDeleted([], state)
    expect(state.parsedTracks).toHaveLength(1)
  })
})

// ── focus-trap cycle ─────────────────────────────────────────────────────────
function nextFocus(current, shiftKey, focusables) {
  const first = focusables[0]
  const last = focusables[focusables.length - 1]
  if (shiftKey && current === first) return last
  if (!shiftKey && current === last) return first
  if (!focusables.includes(current)) return first
  // Otherwise Tab would move naturally (we only intercept the wraps).
  return null
}

describe('modal focus trap', () => {
  const focusables = ['cancel', 'go']

  it('Tab from the last button wraps to the first', () => {
    expect(nextFocus('go', false, focusables)).toBe('cancel')
  })

  it('Shift+Tab from the first button wraps to the last', () => {
    expect(nextFocus('cancel', true, focusables)).toBe('go')
  })

  it('focus that escaped the modal is pulled back to the first', () => {
    expect(nextFocus('some-other-element', false, focusables)).toBe('cancel')
  })

  it('mid-trap Tab is left to the browser (no wrap needed)', () => {
    expect(nextFocus('cancel', false, focusables)).toBeNull()
  })
})
