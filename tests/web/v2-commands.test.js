/**
 * P1 T4 — command registry + track search.
 */
import { describe, it, expect } from 'vitest'
import { buildCommands, searchTracks } from '../../docs/js/v2/commands.js'

describe('buildCommands', () => {
  const cmds = buildCommands()

  it('every command has a unique id, a group, a label, and a run fn', () => {
    const ids = new Set()
    for (const c of cmds) {
      expect(typeof c.id).toBe('string')
      expect(c.group).toBeTruthy()
      expect(c.label).toBeTruthy()
      expect(typeof c.run).toBe('function')
      expect(ids.has(c.id), `duplicate id ${c.id}`).toBe(false)
      ids.add(c.id)
    }
  })

  it('covers the core surfaces', () => {
    const ids = cmds.map((c) => c.id)
    for (const want of ['preview-cues', 'apply', 'health-scan', 'find-duplicates',
      'build-set', 'toggle-theme', 'go-cues', 'go-library', 'go-discover']) {
      expect(ids).toContain(want)
    }
  })
})

describe('searchTracks', () => {
  const tracks = [
    { id: 1, name: 'Glue', artist: 'Bicep', bpm: 124.5, key: '8A', existingHotCues: 6 },
    { id: 2, name: 'So U Kno', artist: 'Overmono', bpm: 130.0, key: '11B', existingHotCues: 0 },
    { id: 3, name: 'Vermilion', artist: 'Bicep', bpm: 0, key: '', existingHotCues: 2 },
  ]

  it('empty query yields nothing', () => {
    expect(searchTracks('', tracks)).toEqual([])
  })
  it('matches on artist OR title', () => {
    expect(searchTracks('bicep', tracks).map((r) => r.id)).toEqual(['track-1', 'track-3'])
    expect(searchTracks('kno', tracks).map((r) => r.id)).toEqual(['track-2'])
  })
  it('formats mono BPM · key meta and cue-state sub', () => {
    const [r] = searchTracks('glue', tracks)
    expect(r.meta).toBe('124.5 · 8A')
    expect(r.metaMono).toBe(true)
    expect(r.sub).toBe('6 cues')
  })
  it('handles missing bpm/key gracefully', () => {
    const [r] = searchTracks('vermilion', tracks)
    expect(r.meta).toBe('— · —')
    expect(r.sub).toBe('2 cues')
  })
  it('reports no-cues tracks', () => {
    const [r] = searchTracks('overmono', tracks)
    expect(r.sub).toBe('no cues')
  })
  it('caps at 8 results', () => {
    const many = Array.from({ length: 20 }, (_, i) => ({ id: i, name: 'mix ' + i, artist: 'x', bpm: 120 }))
    expect(searchTracks('mix', many).length).toBe(8)
  })
})
