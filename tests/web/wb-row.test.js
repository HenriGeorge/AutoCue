/**
 * P2 workbench dense thin-row grid (TASK part 2b).
 *
 * buildWbRow() in docs/js/06-render.js builds the design-Z grid row used by the
 * workbench centre (body.wb-active). It is NOT pure (reads localMode /
 * selectedTrackIds / nowPlayingId / audioPlayer globals), so this spec vendors
 * a faithful mirror of its DOM structure + cue-state logic and asserts the
 * load-bearing contract the rest of the system depends on:
 *
 *   1. The row carries `.track-card.wb-row` + `data-track-id` (inspector click
 *      capture + selection machinery key off these).
 *   2. The MIX / CLASS / ENERGY cells host the SAME lazy-load containers the
 *      legacy cards use (`.energy-sparkline`, `.mix-score-chip[data-track-id]`,
 *      `.category-chip[data-track-id]`) so renderTracks' IntersectionObservers
 *      wire them with no extra plumbing.
 *   3. Cue-state: existingHotCues > 0 → "N ready" (green), else "— no cues".
 *   4. BPM is mono one-decimal; guard against the "0.0" truthy-string trap.
 *
 * Mirror of buildWbRow — keep in sync if the row structure changes.
 */

import { describe, it, expect, beforeEach } from 'vitest'

// ── Vendored mirror of buildWbRow's structure (localMode = true) ────────────
function buildWbRow(track, { rowIndex = 0, localMode = true, selectedIds = new Set() } = {}) {
  const row = document.createElement('div')
  row.className = 'track-card wb-row'
  row.dataset.testid = 'track-card'
  row.dataset.trackId = track.id
  if (track.colorName) row.dataset.color = track.colorName

  const ckCell = document.createElement('div')
  ckCell.className = 'wb-c-ck'
  if (localMode) {
    const cb = document.createElement('input')
    cb.type = 'checkbox'
    cb.className = 'track-select-cb'
    cb.checked = selectedIds.has(track.id)
    if (selectedIds.has(track.id)) row.classList.add('selected')
    ckCell.appendChild(cb)
  }
  row.appendChild(ckCell)

  const idx = document.createElement('div')
  idx.className = 'wb-c-idx'
  idx.textContent = String(rowIndex + 1).padStart(2, '0')
  row.appendChild(idx)

  const title = document.createElement('div')
  title.className = 'wb-c-title'
  const tt = document.createElement('span')
  tt.className = 'wb-tt'
  tt.textContent = track.name || '(untitled)'
  const ta = document.createElement('span')
  ta.className = 'wb-ta'
  ta.textContent = track.artist || ''
  title.appendChild(tt); title.appendChild(ta)
  row.appendChild(title)

  const bpm = document.createElement('div')
  bpm.className = 'wb-c-bpm'
  bpm.textContent = (Number(track.bpm) > 0) ? Number(track.bpm).toFixed(1) : '—'
  row.appendChild(bpm)

  const keyCell = document.createElement('div')
  keyCell.className = 'wb-c-key'
  if (track.key) {
    const chip = document.createElement('span')
    chip.className = 'wb-key-chip'
    chip.textContent = track.key
    keyCell.appendChild(chip)
  }
  row.appendChild(keyCell)

  const energyCell = document.createElement('div')
  energyCell.className = 'wb-c-energy'
  if (localMode) {
    const spark = document.createElement('div')
    spark.className = 'energy-sparkline'
    spark.dataset.trackId = track.id
    energyCell.appendChild(spark)
  }
  row.appendChild(energyCell)

  const mixCell = document.createElement('div')
  mixCell.className = 'wb-c-mix'
  if (localMode) {
    const mixChip = document.createElement('span')
    mixChip.className = 'mix-score-chip loading'
    mixChip.dataset.trackId = track.id
    mixCell.appendChild(mixChip)
  }
  row.appendChild(mixCell)

  const classCell = document.createElement('div')
  classCell.className = 'wb-c-class'
  if (localMode) {
    const catChip = document.createElement('span')
    catChip.className = 'category-chip loading'
    catChip.dataset.trackId = track.id
    catChip._isCategoryChip = true
    classCell.appendChild(catChip)
  }
  row.appendChild(classCell)

  const cueCell = document.createElement('div')
  cueCell.className = 'wb-c-cues'
  const n = Number(track.existingHotCues) || 0
  const cueState = document.createElement('span')
  if (n > 0) {
    cueState.className = 'wb-cues-ready'
    cueState.textContent = n + ' ready'
  } else {
    cueState.className = 'wb-cues-none'
    cueState.textContent = '— no cues'
  }
  cueCell.appendChild(cueState)
  row.appendChild(cueCell)

  const more = document.createElement('div')
  more.className = 'wb-c-more'
  const moreBtn = document.createElement('button')
  moreBtn.type = 'button'
  moreBtn.className = 'wb-more-btn'
  more.appendChild(moreBtn)
  row.appendChild(more)
  return row
}

const TRACK = {
  id: '42', name: 'Vermillion Drift', artist: 'Roman Lindau',
  bpm: 126.04, key: '8A', totalTime: 402, existingHotCues: 8,
}

describe('buildWbRow — grid contract', () => {
  let row
  beforeEach(() => { row = buildWbRow(TRACK) })

  it('is a .track-card.wb-row with data-track-id (inspector + selection key)', () => {
    expect(row.classList.contains('track-card')).toBe(true)
    expect(row.classList.contains('wb-row')).toBe(true)
    expect(row.dataset.trackId).toBe('42')
    expect(row.dataset.testid).toBe('track-card')
  })

  it('renders all ten design-Z columns in order', () => {
    const cells = [...row.children].map((c) => c.className)
    expect(cells).toEqual([
      'wb-c-ck', 'wb-c-idx', 'wb-c-title', 'wb-c-bpm', 'wb-c-key',
      'wb-c-energy', 'wb-c-mix', 'wb-c-class', 'wb-c-cues', 'wb-c-more',
    ])
  })

  it('hosts the lazy-load containers the IntersectionObservers wire', () => {
    expect(row.querySelector('.energy-sparkline[data-track-id="42"]')).not.toBeNull()
    expect(row.querySelector('.mix-score-chip[data-track-id="42"]')).not.toBeNull()
    const cat = row.querySelector('.category-chip[data-track-id="42"]')
    expect(cat).not.toBeNull()
    expect(cat._isCategoryChip).toBe(true)
  })

  it('includes the bulk-select checkbox', () => {
    expect(row.querySelector('input.track-select-cb')).not.toBeNull()
  })

  it('formats BPM as mono one-decimal', () => {
    expect(row.querySelector('.wb-c-bpm').textContent).toBe('126.0')
  })

  it('renders the Camelot key chip', () => {
    expect(row.querySelector('.wb-key-chip').textContent).toBe('8A')
  })

  it('# index is zero-padded two-digit', () => {
    expect(buildWbRow(TRACK, { rowIndex: 0 }).querySelector('.wb-c-idx').textContent).toBe('01')
    expect(buildWbRow(TRACK, { rowIndex: 11 }).querySelector('.wb-c-idx').textContent).toBe('12')
  })
})

describe('buildWbRow — cue state', () => {
  it('existing hot cues → "N ready" with the green ready class', () => {
    const row = buildWbRow({ ...TRACK, existingHotCues: 8 })
    const el = row.querySelector('.wb-c-cues > span')
    expect(el.className).toBe('wb-cues-ready')
    expect(el.textContent).toBe('8 ready')
  })

  it('no cues → "— no cues" muted', () => {
    const row = buildWbRow({ ...TRACK, existingHotCues: 0 })
    const el = row.querySelector('.wb-c-cues > span')
    expect(el.className).toBe('wb-cues-none')
    expect(el.textContent).toBe('— no cues')
  })

  it('treats undefined existingHotCues as zero (no crash)', () => {
    const row = buildWbRow({ ...TRACK, existingHotCues: undefined })
    expect(row.querySelector('.wb-cues-none')).not.toBeNull()
  })
})

describe('buildWbRow — BPM guard (CLAUDE.md "0.0" truthy-string trap)', () => {
  it('renders an em-dash for a zero / "0.0" BPM rather than "0.0"', () => {
    expect(buildWbRow({ ...TRACK, bpm: 0 }).querySelector('.wb-c-bpm').textContent).toBe('—')
    expect(buildWbRow({ ...TRACK, bpm: '0.0' }).querySelector('.wb-c-bpm').textContent).toBe('—')
  })
})

describe('buildWbRow — no-local-mode (XML/frozen) renders no lazy chips', () => {
  it('omits checkbox + intelligence chips when localMode is false', () => {
    const row = buildWbRow(TRACK, { localMode: false })
    expect(row.querySelector('input.track-select-cb')).toBeNull()
    expect(row.querySelector('.energy-sparkline')).toBeNull()
    expect(row.querySelector('.mix-score-chip')).toBeNull()
    expect(row.querySelector('.category-chip')).toBeNull()
    // structural cells still present
    expect(row.querySelector('.wb-c-bpm').textContent).toBe('126.0')
  })
})
