/**
 * P1 T2 — window.ACBridge read-only contract (source-level).
 *
 * v2 ES modules (docs/js/v2/) read legacy `let` state ONLY through
 * window.ACBridge — top-level let (parsedTracks/healthLastSummary/localMode/
 * selectedTrackIds) lives in the shared global lexical environment of the
 * classic scripts but is NOT on window, so a separate-scope module can't see
 * it directly. This spec pins the bridge surface + the two CustomEvents v2
 * listens for, and guards against v2 code reaching past the bridge.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadAppHtml } from './_source.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const V2_DIR = resolve(__dirname, '..', '..', 'docs', 'js', 'v2')

describe('window.ACBridge legacy bridge', () => {
  const src = loadAppHtml()

  it('defines the read-only bridge with its four accessors', () => {
    expect(src).toMatch(/window\.ACBridge\s*=\s*\{/)
    for (const fn of ['tracks', 'healthSummary', 'isLocalMode', 'selectedCount']) {
      expect(src).toContain(`${fn}:`)
    }
  })

  it('dispatches the two events v2 listens for', () => {
    expect(src).toContain("'autocue:health-summary'")
    expect(src).toContain("'autocue:local-mode'")
  })
})

describe('v2 modules do not reach past the bridge', () => {
  // v2 code must not reference legacy top-level `let` bindings directly —
  // they are not in a module's scope and would be ReferenceErrors anyway.
  const files = readdirSync(V2_DIR, { recursive: true })
    .filter((f) => typeof f === 'string' && f.endsWith('.js'))

  it('has at least the seam module', () => {
    expect(files).toContain('main.js')
  })

  for (const rel of files) {
    it(`${rel} reads legacy state only via window.ACBridge`, () => {
      const code = readFileSync(resolve(V2_DIR, rel), 'utf8')
      // bare references to the four bridged `let` bindings (not as a property
      // access like ACBridge.tracks or a string) would be a leak.
      for (const ident of ['parsedTracks', 'healthLastSummary', 'selectedTrackIds']) {
        const bare = new RegExp(`(^|[^.\\w'"\`])${ident}\\b`, 'm')
        expect(bare.test(code), `${rel} references bare ${ident}`).toBe(false)
      }
    })
  }
})
