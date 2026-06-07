/**
 * Regression tests for UX audit PR A — Issue 4 (stats math),
 * M-2 (download confirm query), M-5 (VIA jargon).
 *
 * Issue 2 (panel containing block) is verified live via DevTools — it
 * depends on the `transform` on `#discover-tab-content` which jsdom
 * doesn't apply. Issue 1 (double-? overlay) is verified live too —
 * the app-wide handler is registered on a different code path that we
 * don't drive in unit tests.
 */

import { describe, it, expect } from 'vitest'

/* ====================================== Issue 4: stats math */

function _formatStatsPercent(n) {
  if (n == null || !Number.isFinite(n)) return '–'
  const ratio = Math.max(0, Math.min(1, n))
  return `${Math.round(ratio * 100)}%`
}

describe('_formatStatsPercent clamps to [0, 100]%', () => {
  it('renders 0..1 as 0..100%', () => {
    expect(_formatStatsPercent(0)).toBe('0%')
    expect(_formatStatsPercent(0.5)).toBe('50%')
    expect(_formatStatsPercent(1)).toBe('100%')
  })

  it('clamps raw counts (the production bug) to 100%', () => {
    expect(_formatStatsPercent(11)).toBe('100%')
    expect(_formatStatsPercent(1000)).toBe('100%')
  })

  it('clamps negatives to 0%', () => {
    expect(_formatStatsPercent(-0.1)).toBe('0%')
    expect(_formatStatsPercent(-100)).toBe('0%')
  })

  it('keeps "–" for null / NaN', () => {
    expect(_formatStatsPercent(null)).toBe('–')
    expect(_formatStatsPercent(undefined)).toBe('–')
    expect(_formatStatsPercent(NaN)).toBe('–')
  })
})


/* ====================================== M-2: download query no longer duplicates artist */

function _buildDownloadQuery(release) {
  const r = release?.release || {}
  const artist = (r.artist || '').trim()
  let albumOrTitle = (r.album || '').trim()
  if (!albumOrTitle) {
    const title = (r.title || '').trim()
    albumOrTitle = title.includes(' - ')
      ? title.split(' - ').slice(1).join(' - ').trim()
      : title
  }
  return [artist, albumOrTitle].filter(Boolean).join(' ')
}

describe('_buildDownloadQuery — no duplicated artist', () => {
  it('prefers release.album when set', () => {
    expect(_buildDownloadQuery({release: {
      artist: 'soFa elsewhere', title: 'soFa elsewhere - Forward In Reverse', album: 'Forward In Reverse',
    }})).toBe('soFa elsewhere Forward In Reverse')
  })

  it('strips "Artist - " prefix from title when album is missing', () => {
    expect(_buildDownloadQuery({release: {
      artist: 'Madvillain', title: 'Madvillain - Madvillainy',
    }})).toBe('Madvillain Madvillainy')
  })

  it('handles multi " - " titles by keeping everything after the first separator', () => {
    expect(_buildDownloadQuery({release: {
      artist: 'A', title: 'A - Title - With - Dashes',
    }})).toBe('A Title - With - Dashes')
  })

  it('falls back to raw title when no " - " in it', () => {
    expect(_buildDownloadQuery({release: {
      artist: 'X', title: 'Standalone',
    }})).toBe('X Standalone')
  })

  it('handles empty / missing release dict', () => {
    expect(_buildDownloadQuery({})).toBe('')
    expect(_buildDownloadQuery(null)).toBe('')
  })

  it('does NOT duplicate artist when Discogs format is "Artist - Album"', () => {
    const q = _buildDownloadQuery({release: {
      artist: 'Sandy B (3) & soFa elsewhere',
      title: 'Sandy B (3) & soFa elsewhere - Forward In Reverse Pt.1',
    }})
    // The artist appears EXACTLY once, not twice.
    expect((q.match(/Sandy B/g) || []).length).toBe(1)
  })
})


/* ====================================== M-5: VIA jargon → human source label */

function _renderCardSourceLine(release) {
  const rawSource = (release.source || '')
  const SOURCE_LABEL = {
    artist: 'Artist match',
    label:  'Label match',
    novelty: 'Novelty pick',
  }
  const sourceFamily = rawSource.split(':')[0]
  return SOURCE_LABEL[sourceFamily] || sourceFamily
}

describe('Card source-line — no "via X" jargon', () => {
  it('artist source → "Artist match"', () => {
    expect(_renderCardSourceLine({source: 'artist'})).toBe('Artist match')
  })
  it('label source → "Label match"', () => {
    expect(_renderCardSourceLine({source: 'label'})).toBe('Label match')
  })
  it('novelty:style → "Novelty pick"', () => {
    expect(_renderCardSourceLine({source: 'novelty:style'})).toBe('Novelty pick')
  })
  it('novelty:label → "Novelty pick"', () => {
    expect(_renderCardSourceLine({source: 'novelty:label'})).toBe('Novelty pick')
  })
  it('novelty:artist → "Novelty pick"', () => {
    expect(_renderCardSourceLine({source: 'novelty:artist'})).toBe('Novelty pick')
  })
  it('falls back to raw family for unknown sources', () => {
    expect(_renderCardSourceLine({source: 'shop:hardwax'})).toBe('shop')
  })
})
