/**
 * P4 Nightboard — interop contract (source-level).
 *
 * T1: the two new ACBridge accessors the Nightboard mode needs exist and the
 * shared create-playlist write path is factored out (one write path, reused by
 * the legacy set-builder Save AND the canvas Export).
 *
 * T7 extends this file with the bare-state-read sweep over docs/js/v2/nightboard/*
 * once those modules exist (R11). Kept tolerant of the dir not existing yet so
 * this spec is green from T1 onward.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, existsSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadAppHtml } from './_source.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const NB_DIR = resolve(__dirname, '..', '..', 'docs', 'js', 'v2', 'nightboard')

describe('P4 ACBridge seams (T1)', () => {
  const src = loadAppHtml()

  it('exposes anchorsFromSelection + createSetPlaylist on ACBridge', () => {
    expect(src).toMatch(/anchorsFromSelection:\s*\(\)\s*=>/)
    expect(src).toMatch(/createSetPlaylist:\s*\(name,\s*ids\)\s*=>/)
  })

  it('anchorsFromSelection coerces ids to ints (matches _useSelectedForSetBuilder)', () => {
    expect(src).toMatch(/anchorsFromSelection:\s*\(\)\s*=>\s*\[\.\.\.selectedTrackIds\]\.map\(\(id\)\s*=>\s*parseInt\(id,\s*10\)\)/)
  })

  it('factors the create-playlist POST into one shared write path', () => {
    expect(src).toMatch(/async function _createPlaylist\(name,\s*ids\)/)
    // legacy Save delegates to it (no second POST /api/playlists body inline there)
    expect(src).toMatch(/await _createPlaylist\(name,\s*_sbTracks\.map/)
    // bridge Export delegates to the SAME path
    expect(src).toMatch(/createSetPlaylist:\s*\(name,\s*ids\)\s*=>\s*_createPlaylist\(name,\s*ids\)/)
  })
})

describe('P4 nightboard modules read legacy state only via ACBridge (R11)', () => {
  // Tolerant until the dir lands (T2+). Once it exists, ban bare legacy-state
  // identifiers — they are not in a module's scope and bypass the bridge.
  const BANNED = ['parsedTracks', 'pendingCues', 'selectedTrackIds']

  const files = existsSync(NB_DIR)
    ? readdirSync(NB_DIR, { recursive: true }).filter((f) => typeof f === 'string' && f.endsWith('.js'))
    : []

  it.skipIf(files.length === 0)('contains no bare legacy-state reads', () => {
    for (const f of files) {
      const code = readFileSync(resolve(NB_DIR, f), 'utf8')
      for (const id of BANNED) {
        expect(code, `${f} references bare ${id}`).not.toMatch(new RegExp(`\\b${id}\\b`))
      }
    }
  })
})
