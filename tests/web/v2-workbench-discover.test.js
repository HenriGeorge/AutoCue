/**
 * P5 — Discover as a place (rail place → centre-pane view).
 *
 * Mirrors the P3 duplicates-place test layout:
 *   T1  source-contract: the legacy seams the v2 place rides on (window.DiscoverV2,
 *       ACBridge.discover accessors, _renderDetailBody exposure) — all additive.
 *   T2  jsdom: the place skeleton's centre-pane swap mechanics + mutual exclusion.
 *   T3  inspector re-host + mode flag.
 *   T4  restyle is token-clean.
 *   T5  aliveness transitions are reduced-motion-gated.
 *   T6  ⌘K commands target the place.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadAppHtml } from './_source.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const V2_DIR = resolve(__dirname, '..', '..', 'docs', 'js', 'v2')

const src = loadAppHtml()

describe('P5 T1 — legacy seams for the discover place', () => {
  it('exposes the DiscoverV2 IIFE on window (read surface for the place)', () => {
    expect(src).toMatch(/window\.DiscoverV2\s*=\s*DiscoverV2\b/)
  })

  it('leaves the IIFE return object untouched (no behaviour change)', () => {
    // The public return object still lists the same surface — assignment to
    // window must not have edited it.
    expect(src).toMatch(/return\s*{\s*\n\s*state,\s*subscribe,/)
    expect(src).toContain('loadInitialState, runScan, cancelScan,')
  })

  it('exposes _renderDetailBody for the inspector re-host (T3)', () => {
    expect(src).toMatch(/window\._renderDiscoverRenderDetail\s*=\s*_renderDetailBody\b/)
  })

  describe('ACBridge.discover pass-throughs', () => {
    it('exposes discoverRunScan delegating to DiscoverV2.runScan', () => {
      expect(src).toMatch(/discoverRunScan:\s*\(\)\s*=>\s*window\.DiscoverV2\?\.runScan\(\)/)
    })
    it('exposes discoverLoadInitialState delegating to DiscoverV2.loadInitialState', () => {
      expect(src).toMatch(/discoverLoadInitialState:\s*\(\)\s*=>\s*window\.DiscoverV2\?\.loadInitialState\(\)/)
    })
    it('exposes discoverState reading DiscoverV2.state', () => {
      expect(src).toMatch(/discoverState:\s*\(\)\s*=>\s*\(window\.DiscoverV2\s*\?\s*window\.DiscoverV2\.state\s*:\s*null\)/)
    })
    it('exposes discoverLoadDetail delegating to DiscoverV2.loadDetail', () => {
      expect(src).toMatch(/discoverLoadDetail:\s*\(id\)\s*=>\s*window\.DiscoverV2\?\.loadDetail\(id\)/)
    })
  })
})
