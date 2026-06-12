/**
 * P1 T5 — palette pure helpers + markup contract.
 */
import { describe, it, expect } from 'vitest'
import { clampActive, buildResults } from '../../docs/js/v2/palette.js'
import { loadAppHtml } from './_source.js'

describe('clampActive (wrap-around)', () => {
  it('wraps past the end to the start', () => {
    expect(clampActive(3, 3)).toBe(0)
    expect(clampActive(4, 3)).toBe(1)
  })
  it('wraps before the start to the end', () => {
    expect(clampActive(-1, 3)).toBe(2)
  })
  it('is 0 for an empty list', () => {
    expect(clampActive(5, 0)).toBe(0)
  })
})

describe('buildResults', () => {
  const commands = [
    { id: 'find-duplicates', group: 'Library', label: 'Find duplicates', run() {} },
    { id: 'apply', group: 'Cues', label: 'Apply to Rekordbox', run() {} },
  ]
  const tracks = [{ id: 1, name: 'Glue', artist: 'Bicep', bpm: 124.5, key: '8A', existingHotCues: 6 }]

  it('empty query returns all commands, no tracks', () => {
    const r = buildResults('', { commands, tracks })
    expect(r.length).toBe(2)
    expect(r.every((x) => x.group !== 'Tracks')).toBe(true)
  })
  it('ranks matching commands and appends track hits under Tracks', () => {
    const r = buildResults('bicep', { commands, tracks })
    const groups = r.map((x) => x.group)
    expect(groups).toContain('Tracks')
  })
  it('a command query matches without surfacing tracks', () => {
    const r = buildResults('dupl', { commands, tracks })
    expect(r[0].id).toBe('find-duplicates')
    expect(r.some((x) => x.group === 'Tracks')).toBe(false)
  })
})

describe('palette markup', () => {
  const html = loadAppHtml()
  it('has the veil, dialog, input, list, and hint button', () => {
    for (const id of ['cmd-veil', 'cmd-palette', 'pal-input', 'pal-list', 'cmdk-hint-btn']) {
      expect(html.includes(`id="${id}"`), `missing #${id}`).toBe(true)
    }
  })
  it('the veil and hint start hidden (gated on local mode)', () => {
    expect(/<div id="cmd-veil" hidden>/.test(html)).toBe(true)
    expect(/<button id="cmdk-hint-btn"[^>]*hidden/.test(html)).toBe(true)
  })
  it('the input is a combobox', () => {
    expect(/<input id="pal-input"[^>]*role="combobox"/.test(html)).toBe(true)
  })
})
