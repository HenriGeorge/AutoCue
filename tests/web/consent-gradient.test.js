/**
 * v2 consent gradient — "review unlocks apply" (design-H "Stagehand").
 *
 * Two guarantees:
 *  1. The pure verdict helper `_consentCanConfirm` (extracted live from
 *     docs/js/07-helpers-events.js, not vendored) gates the destructive
 *     primary correctly: legacy path is a pass-through; review-required path
 *     stays locked until the evidence is revealed AND the 250ms guard elapses.
 *  2. Source-contract: the shared `_confirmDialog` carries the reviewRequired
 *     path, and the cue-tools destructive flow opts into it. Backward-compat:
 *     existing non-destructive callers pass NO reviewRequired option.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadAppHtml } from './_source.js'

const __dirname = dirname(fileURLToPath(import.meta.url))
const HELPERS = readFileSync(
  resolve(__dirname, '..', '..', 'docs', 'js', '07-helpers-events.js'),
  'utf8',
)

// Pull the real `_consentCanConfirm` body out of source and instantiate it,
// so we test the shipped logic — not a copy that can drift.
function loadConsentHelper() {
  const m = HELPERS.match(/function _consentCanConfirm\(([^)]*)\)\s*\{([\s\S]*?)\n\}/)
  if (!m) throw new Error('_consentCanConfirm not found in 07-helpers-events.js')
  // eslint-disable-next-line no-new-func
  return new Function(m[1], m[2])
}

describe('_consentCanConfirm — review-unlocks-apply verdict', () => {
  const canConfirm = loadConsentHelper()

  it('is a no-op pass-through when review is not required (legacy callers)', () => {
    expect(canConfirm(false, false, 0)).toBe(true)
    expect(canConfirm(false, false, 9999)).toBe(true)
    expect(canConfirm(false, true, 0)).toBe(true)
  })

  it('stays locked until evidence is reviewed', () => {
    expect(canConfirm(true, false, 1000)).toBe(false)
  })

  it('stays locked during the 250ms accidental-Enter guard after reveal', () => {
    expect(canConfirm(true, true, 0)).toBe(false)
    expect(canConfirm(true, true, 249)).toBe(false)
  })

  it('unlocks only after review AND the 250ms guard elapses', () => {
    expect(canConfirm(true, true, 250)).toBe(true)
    expect(canConfirm(true, true, 5000)).toBe(true)
  })
})

describe('_confirmDialog — reviewRequired is additive / backward-compatible', () => {
  it('defines the reviewRequired + evidence opt-in path', () => {
    expect(HELPERS).toMatch(/opts\.reviewRequired === true/)
    expect(HELPERS).toMatch(/confirm-evidence/)
    // The destructive label stays gated behind a "Review to enable" announcement.
    expect(HELPERS).toMatch(/Review to enable/)
  })

  it('only opts into review-required when evidence is supplied', () => {
    // reviewRequired alone (no evidence) must NOT engage the gate.
    expect(HELPERS).toMatch(/opts\.reviewRequired === true && opts\.evidence != null/)
  })
})

describe('cue-tools destructive ops are gated by the consent gradient', () => {
  const html = loadAppHtml()

  it('passes reviewRequired for delete_orphan / shift writes', () => {
    // The single _confirmDialog call inside the destructive cue-tools branch
    // must carry reviewRequired: true.
    expect(html).toMatch(/reviewRequired:\s*true/)
  })

  it('keeps non-destructive callers free of reviewRequired (byte-identical legacy)', () => {
    // The health-fix and rename confirms must not have been touched.
    expect(html).toMatch(/confirmLabel:\s*['"]Fix tracks['"]/)
    // Exactly one reviewRequired opt-in across the whole app (the cue-tools path).
    const occurrences = (html.match(/reviewRequired:\s*true/g) || []).length
    expect(occurrences).toBe(1)
  })
})
