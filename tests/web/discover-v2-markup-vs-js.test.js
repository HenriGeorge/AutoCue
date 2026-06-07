/**
 * Regression test for the section-id mismatch bug.
 *
 * Background: the markup once had <section id="discover-v2-section"> while
 * every JS guard looked up getElementById('disc-v2-section'). initDiscoverV2()
 * returned early, loadInitialState() never fired, and the entire v2 surface
 * silently no-op'd. The state was discoverable only via DevTools (the cards
 * grid was empty and no /api/discover/* calls went out).
 *
 * This test reads docs/index.html and asserts: every #disc-v2-* id referenced
 * by `document.getElementById(...)` in the JS module actually exists in the
 * markup. The id list is exact-match, not heuristic.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const INDEX_HTML = resolve(__dirname, '..', '..', 'docs', 'index.html')
const html = readFileSync(INDEX_HTML, 'utf8')

// Every #disc-v2-* id that the JS *reads* via getElementById.
const getElementByIdRefs = new Set()
for (const m of html.matchAll(/getElementById\(['"](disc-v2-[^'"]+)['"]\)/g)) {
  getElementByIdRefs.add(m[1])
}

// Every #disc-v2-* id that's *defined* in the markup.
const definedIds = new Set()
for (const m of html.matchAll(/id=["'](disc-v2-[^"']+)["']/g)) {
  definedIds.add(m[1])
}


describe('Discover v2 markup ↔ JS id contract', () => {
  it('every disc-v2-* id read by JS exists in the markup', () => {
    const missing = [...getElementByIdRefs].filter(id => !definedIds.has(id))
    expect(missing, `Missing markup ids: ${missing.join(', ')}`).toEqual([])
  })

  it('the section wrapper is named disc-v2-section (initDiscoverV2 guard)', () => {
    expect(definedIds.has('disc-v2-section')).toBe(true)
    expect(html).not.toMatch(/id=["']discover-v2-section["']/)
  })

  it('finds the core surface ids', () => {
    // Belt-and-braces — these are the load-bearing elements; if any of them
    // disappear, initDiscoverV2 either dies or paints nothing.
    for (const id of [
      'disc-v2-section',
      'disc-v2-grid',
      'disc-v2-refresh-btn',
      'disc-v2-settings-btn',
      'disc-v2-token-banner',
      'disc-v2-onboarding-banner',
      'disc-v2-scan-progress',
      'disc-v2-empty-state',
      'disc-v2-detail-panel',
      'disc-v2-dl-confirm',
      'disc-v2-snooze-pop',
      'disc-v2-kbd-help',
    ]) {
      expect(definedIds.has(id), `Expected #${id} to be defined in docs/index.html`).toBe(true)
    }
  })
})
