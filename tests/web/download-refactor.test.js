/**
 * Vitest coverage for the v0.2.0 Download component refactor
 * (PRD .agent/prd/DOWNLOAD_PRD.md).
 *
 * Focus: pure functions extracted from docs/index.html that don't require
 * DOM bootstrap. Larger integration tests for the IIFE state machine live
 * in Playwright (tests/e2e/test_download_*.spec.ts).
 */

import { describe, it, expect } from 'vitest'

/* ──────────────────────── URL classifier (PRD §6.3) ──────────────────────── */

// Mirror of _Download._classifyDownloadTarget — kept in sync with the IIFE
// in docs/index.html. If you change one, change the other.
function _classifyDownloadTarget(q) {
  const s = (q || '').trim()
  if (!s) return 'invalid'
  if (s.includes('\n') || s.includes('\r')) return 'invalid'
  const isUrl = /^https?:\/\//i.test(s)
  if (!isUrl) return 'search'
  const listMatch = /[?&]list=([A-Za-z0-9_-]+)/i.exec(s)
  const vMatch    = /[?&]v=([A-Za-z0-9_-]{6,})/i.exec(s)
  if (listMatch && vMatch) return 'mixed_video_in_playlist'
  if (listMatch)            return 'playlist'
  if (vMatch || /youtu\.be\/[A-Za-z0-9_-]{6,}/.test(s)) return 'single_video'
  return 'single_video'
}

describe('_classifyDownloadTarget', () => {
  it('classifies empty/blank as invalid', () => {
    expect(_classifyDownloadTarget('')).toBe('invalid')
    expect(_classifyDownloadTarget('   ')).toBe('invalid')
    expect(_classifyDownloadTarget(null)).toBe('invalid')
  })
  it('flags multi-line input as invalid (defense against bulk paste)', () => {
    expect(_classifyDownloadTarget('a\nb')).toBe('invalid')
  })
  it('treats bare term as search', () => {
    expect(_classifyDownloadTarget('Artist - Title')).toBe('search')
    expect(_classifyDownloadTarget('daft punk')).toBe('search')
  })
  it('identifies a single-video URL', () => {
    expect(_classifyDownloadTarget('https://www.youtube.com/watch?v=dQw4w9WgXcQ')).toBe('single_video')
    expect(_classifyDownloadTarget('https://youtu.be/dQw4w9WgXcQ')).toBe('single_video')
  })
  it('identifies a playlist URL', () => {
    expect(_classifyDownloadTarget('https://www.youtube.com/playlist?list=PLxxxxxxxxxxxx'))
      .toBe('playlist')
  })
  it('identifies mixed video-in-playlist URL', () => {
    expect(_classifyDownloadTarget('https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLxxxxxxxxxxxx'))
      .toBe('mixed_video_in_playlist')
  })
})

/* ──────────────────── Legacy format coercion (PRD §6.2) ─────────────────── */

const LEGACY_COERCION = {
  mp3: 'mp3_320', m4a: 'original', aac: 'original', opus: 'original',
  flac: 'wav', alac: 'wav', vorbis: 'wav',
}

describe('Legacy format coercion table (frontend mirror of backend)', () => {
  it('every legacy key maps to an allowed Literal value', () => {
    const allowed = new Set(['wav', 'mp3_320', 'original'])
    for (const [legacy, mapped] of Object.entries(LEGACY_COERCION)) {
      expect(allowed.has(mapped)).toBe(true)
    }
  })
  it('mp3 (current default) coerces to mp3_320 (new default)', () => {
    expect(LEGACY_COERCION.mp3).toBe('mp3_320')
  })
  it('source containers (m4a/aac/opus) map to "original" (no re-encode)', () => {
    expect(LEGACY_COERCION.m4a).toBe('original')
    expect(LEGACY_COERCION.aac).toBe('original')
    expect(LEGACY_COERCION.opus).toBe('original')
  })
  it('lossless containers (flac/alac/vorbis) map to "wav" (closest we ship)', () => {
    expect(LEGACY_COERCION.flac).toBe('wav')
    expect(LEGACY_COERCION.alac).toBe('wav')
    expect(LEGACY_COERCION.vorbis).toBe('wav')
  })
})

/* ───────────── Format × Normalize matrix (PRD §6.4 round-2 M2) ───────────── */

function normalizeAvailable(format) {
  return format !== 'original'
}

describe('Format × Normalize matrix — frontend gating', () => {
  it('normalize disabled when format = original', () => {
    expect(normalizeAvailable('original')).toBe(false)
  })
  it('normalize enabled for wav and mp3_320', () => {
    expect(normalizeAvailable('wav')).toBe(true)
    expect(normalizeAvailable('mp3_320')).toBe(true)
  })
})

/* ───────────── 410 already_consumed renderer contract (PRD §7.3) ─────────── */

function _shouldRenderFromCache(httpStatus) {
  return httpStatus === 410
}

describe('410 already_consumed — frontend must render from cache, not throw', () => {
  it('triggers cache render on 410', () => {
    expect(_shouldRenderFromCache(410)).toBe(true)
  })
  it('does NOT trigger cache render on 200 or 404', () => {
    expect(_shouldRenderFromCache(200)).toBe(false)
    expect(_shouldRenderFromCache(404)).toBe(false)
  })
})

/* ───────────── Esc-twice timing contract (PRD §8.6 round-2 M4) ─────────── */

function _shouldCancelOnEsc(firstEscAt, secondEscAt, windowMs = 1500) {
  if (firstEscAt === null) return false
  return (secondEscAt - firstEscAt) <= windowMs
}

describe('Double-Esc cancel timing', () => {
  it('cancels when second Esc within 1.5s window', () => {
    expect(_shouldCancelOnEsc(1000, 2000)).toBe(true)
    expect(_shouldCancelOnEsc(1000, 2500)).toBe(true)
  })
  it('does NOT cancel when second Esc after window', () => {
    expect(_shouldCancelOnEsc(1000, 2600)).toBe(false)
    expect(_shouldCancelOnEsc(1000, 5000)).toBe(false)
  })
  it('does NOT cancel if no prior Esc was registered', () => {
    expect(_shouldCancelOnEsc(null, 2000)).toBe(false)
  })
})

/* ───────────── SSE keepalive ignored by _consumeSSE (PRD §6.12) ─────────── */

// Re-implement the data-line filter logic locally to verify it.
function _parseSSELines(blob) {
  const events = []
  const lines = blob.split('\n')
  for (const line of lines) {
    if (!line.startsWith('data:')) continue
    try { events.push(JSON.parse(line.slice(5).trim())) } catch (_) { /* partial */ }
  }
  return events
}

describe('SSE keepalive (comment-line) is invisible to handlers', () => {
  it('emits data: events; ignores : keepalive comment lines', () => {
    const blob = [
      'data: {"type":"progress","percent":10}\n',
      '\n',
      ': keepalive\n',
      '\n',
      'data: {"type":"progress","percent":50}\n',
      '\n',
      ': keepalive\n',
      '\n',
      'data: {"type":"done","status":"success"}\n',
      '\n',
    ].join('')
    const events = _parseSSELines(blob)
    expect(events.length).toBe(3)
    expect(events[0].percent).toBe(10)
    expect(events[2].type).toBe('done')
  })
})

/* ──────────────── HTML guarantees: no jargon in user copy ──────────────── */

// P0 split: user copy may live in markup OR JS — loadAppHtml() sees both.
import { loadAppHtml } from './_source.js'

describe('docs/index.html user-facing copy is jargon-free', () => {
  const html = loadAppHtml()

  it('Download section helper no longer mentions yt-dlp', () => {
    const start = html.indexOf('id="download-section"')
    const section = html.slice(start, start + 4500)
    expect(section).not.toMatch(/yt-dlp/)
  })

  it('Download section helper no longer references "suggestion above"', () => {
    const start = html.indexOf('id="download-section"')
    const section = html.slice(start, start + 4500)
    expect(section).not.toMatch(/suggestion above/)
  })

  it('Verb "Download" is reserved for audio — XML uses "Export"', () => {
    expect(html).toContain('>Export XML<')
    expect(html).not.toContain('>Download XML<')
    expect(html).toContain('💾 Save backup XML')
    expect(html).not.toContain('💾 Download backup XML')
  })

  it('Confirm modal "Download anyway" loaded language was removed', () => {
    expect(html).not.toContain('Download anyway')
    expect(html).toContain('Download album')
  })

  it('Format selector has all 3 PRD §6.2 options with MP3 320 first/default', () => {
    expect(html).toMatch(/<option value="mp3_320">MP3 320 kbps \(default\)<\/option>/)
    expect(html).toMatch(/<option value="wav">WAV/)
    expect(html).toMatch(/<option value="original">Original/)
  })

  it('Normalize loudness and Auto-tag metadata toggles present', () => {
    expect(html).toContain('id="dl-normalize"')
    expect(html).toContain('id="dl-embed-meta"')
    expect(html).toContain('Normalize loudness to -14 LUFS')
    expect(html).toContain('Auto-tag metadata')
  })

  it('Primary Download button uses the primary class (brand green)', () => {
    expect(html).toMatch(/id="dl-go-btn" class="primary dl-target"/)
  })

  it('Status region has aria-live and role=status (WCAG 4.1.3)', () => {
    expect(html).toMatch(/id="dl-status-region"[^>]*role="status"[^>]*aria-live="polite"/)
  })
})

/* ─────────────────── Old SSE drivers are deleted/replaced ───────────────── */

describe('Old function definitions are removed (PRD §12 acceptance #2)', () => {
  const html = loadAppHtml()

  it('window._Download IIFE exists', () => {
    expect(html).toMatch(/window\._Download = \(function/)
  })

  it('_Download exposes start, bindManualPanel, bindCardButton', () => {
    // grep for the return-statement keys
    expect(html).toMatch(/start:\s*start/)
    expect(html).toMatch(/bindManualPanel:\s*bindManualPanel/)
    expect(html).toMatch(/bindCardButton:\s*bindCardButton/)
  })
})

/* ────────────── search→modal routing (PRP search→modal) ────────────── */

describe('Manual panel routes bare-text search through YouTube candidate modal', () => {
  const html = loadAppHtml()

  it('openYoutubeModalForQuery helper exists', () => {
    expect(html).toMatch(/function openYoutubeModalForQuery\(query\)/)
  })

  it('bindManualPanel._start dispatches to the modal for "search" targets', () => {
    // The control-flow check: if classifier returns 'search', call
    // openYoutubeModalForQuery and bail out before enqueueing directly.
    expect(html).toMatch(/openYoutubeModalForQuery\(q\)/)
    expect(html).toMatch(/targetKind\s*===?\s*['"]search['"]/)
  })

  it('URL targets still take the direct enqueue path (no modal)', () => {
    // The classifier branch must allow URL kinds to fall through to the
    // existing _Download.start() call rather than opening the modal.
    expect(html).toMatch(/allowPlaylist:\s*\['playlist',\s*'mixed_video_in_playlist'\]\.includes\(targetKind\)/)
  })

  it('Modal Pick handler routes through _Download.start (not legacy POST /api/download)', () => {
    // After PRP search→modal, _ytDownload should call window._Download.start
    // with the user's format / normalize / metadata prefs — NOT a fetch with
    // hardcoded audio_format:'mp3' against the deprecated alias.
    const ytDl = html.slice(html.indexOf('async function _ytDownload'),
                            html.indexOf('async function _ytDownload') + 2200)
    expect(ytDl).toContain('window._Download.start')
    expect(ytDl).not.toMatch(/audio_format:\s*['"]mp3['"]/)
  })

  it('openYoutubeModalForQuery auto-fires the search', () => {
    // UX: when opened with a non-empty query, the modal should kick the
    // candidate search itself so the user lands on the list, not on an
    // empty modal that demands they re-type and click Search.
    const fn = html.slice(html.indexOf('function openYoutubeModalForQuery'),
                          html.indexOf('function openYoutubeModalForQuery') + 1200)
    expect(fn).toContain('_ytSearch')
  })

  // Race-condition guards on _ytModalJob ownership (review of PR #102):
  // - Token guard prevents a stale onState from a cancelled job A from
  //   nulling out a freshly-started job B.
  // - Cancel-on-open in both modal-entry functions prevents an in-flight
  //   query-modal job from writing into a reset modal opened for a
  //   different flow.

  it('_ytDownload guards _ytModalJob = null with a per-call token', () => {
    const fn = html.slice(html.indexOf('async function _ytDownload'),
                          html.indexOf('async function _ytDownload') + 3000)
    // Module-level token counter must exist.
    expect(html).toMatch(/let\s+_ytModalJobToken\s*=\s*0/)
    // Each call snapshots the token before assigning _ytModalJob.
    expect(fn).toMatch(/const\s+myToken\s*=\s*\+\+_ytModalJobToken/)
    // The done branch only nulls _ytModalJob if our token is still current.
    expect(fn).toMatch(/if\s*\(\s*_ytModalJobToken\s*===\s*myToken\s*\)\s*_ytModalJob\s*=\s*null/)
    // Naked `_ytModalJob = null` inside the done branch would defeat the guard.
    const doneBranch = fn.slice(fn.indexOf("ev.type === 'done'"))
    expect(doneBranch).not.toMatch(/^\s*_ytModalJob\s*=\s*null\s*;/m)
  })

  it('openYoutubeModal cancels any in-flight _ytModalJob before resetting the modal UI', () => {
    const fn = html.slice(html.indexOf('function openYoutubeModal(track)'),
                          html.indexOf('function openYoutubeModal(track)') + 1200)
    // Cancel must fire BEFORE the first DOM reset (e.g. yt-candidates clear).
    const cancelIdx = fn.indexOf('_ytModalJob.cancel()')
    const firstResetIdx = fn.indexOf("getElementById('yt-candidates')")
    expect(cancelIdx).toBeGreaterThan(-1)
    expect(firstResetIdx).toBeGreaterThan(-1)
    expect(cancelIdx).toBeLessThan(firstResetIdx)
  })

  it('openYoutubeModalForQuery cancels any in-flight _ytModalJob before resetting the modal UI', () => {
    const fn = html.slice(html.indexOf('function openYoutubeModalForQuery'),
                          html.indexOf('function openYoutubeModalForQuery') + 1200)
    const cancelIdx = fn.indexOf('_ytModalJob.cancel()')
    const firstResetIdx = fn.indexOf("getElementById('yt-candidates')")
    expect(cancelIdx).toBeGreaterThan(-1)
    expect(firstResetIdx).toBeGreaterThan(-1)
    expect(cancelIdx).toBeLessThan(firstResetIdx)
  })
})
