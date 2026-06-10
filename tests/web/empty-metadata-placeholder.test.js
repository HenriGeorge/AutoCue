/**
 * Regression guard for the empty-metadata-streaming-track placeholder.
 * Streaming-source tracks (Spotify / Tidal / Apple Music) frequently
 * import into Rekordbox with empty Title / ArtistName / AlbumName.
 * The API echoes the empty strings through and the card would render
 * as a visually blank row. The renderer now shows
 *   "— Untitled streaming track —" + ".track-name.untitled"
 *   "No artist metadata"           + ".track-artist.untitled"
 * for that case so the user spots them rather than thinking the
 * card layout is broken.
 *
 * Mirror of the placeholder branch in docs/index.html. Keep in sync.
 */

import { describe, it, expect } from 'vitest'

// Vendored placeholder predicates — match docs/index.html buildTrackCard.
function trackNameDisplay(track) {
  if (!track.name && track.source === 'streaming') {
    return { text: '— Untitled streaming track —', className: 'track-name untitled' }
  }
  return { text: track.name, className: 'track-name' }
}

function trackArtistDisplay(track) {
  if (track.artist) {
    return { text: track.artist, className: 'track-artist' }
  }
  if (!track.name && !track.artist && track.source === 'streaming') {
    return { text: 'No artist metadata', className: 'track-artist untitled' }
  }
  return null // sub-row not rendered
}

describe('streaming-empty-metadata placeholder (title)', () => {
  it('shows placeholder for streaming track with empty name', () => {
    const out = trackNameDisplay({ id: 1, name: '', artist: '', source: 'streaming' })
    expect(out.text).toBe('— Untitled streaming track —')
    expect(out.className).toContain('untitled')
  })

  it('renders normal title when name is present (streaming or not)', () => {
    expect(
      trackNameDisplay({ id: 1, name: 'My Track', source: 'streaming' }).text,
    ).toBe('My Track')
    expect(
      trackNameDisplay({ id: 1, name: 'My Track', source: 'file' }).text,
    ).toBe('My Track')
  })

  it('does NOT placeholder a file-source track with empty name', () => {
    // Only streaming gets the special-case copy — a missing file with a
    // blank title is a different problem the user should diagnose
    // separately.
    const out = trackNameDisplay({ id: 1, name: '', artist: '', source: 'file' })
    expect(out.text).toBe('')
    expect(out.className).not.toContain('untitled')
  })
})

describe('streaming-empty-metadata placeholder (artist)', () => {
  it('shows artist placeholder when BOTH name and artist are missing on a streaming track', () => {
    const out = trackArtistDisplay({ id: 1, name: '', artist: '', source: 'streaming' })
    expect(out.text).toBe('No artist metadata')
    expect(out.className).toContain('untitled')
  })

  it('does NOT placeholder when only the artist is missing but name is present', () => {
    // 187 tracks in a typical library have valid title + empty artist
    // (classical / various-artists). They should render naturally —
    // the sub-row hides itself because artist is falsy.
    const out = trackArtistDisplay({
      id: 1, name: 'Symphony No. 9', artist: '', source: 'streaming',
    })
    expect(out).toBeNull()
  })

  it('renders normal artist when present', () => {
    expect(
      trackArtistDisplay({ id: 1, name: 'X', artist: 'Daft Punk', source: 'file' }).text,
    ).toBe('Daft Punk')
  })
})
