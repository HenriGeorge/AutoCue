/**
 * Regression for #113 — `#track-list` must NOT be a descendant of
 * `#tracks-sticky`.
 *
 * Background: `#tracks-sticky` was opened at docs/index.html L2394 but never
 * explicitly closed before `<div id="track-list">` at L2523. The browser's
 * HTML5 parser tolerated the imbalance and ended up parenting `#track-list`
 * inside `#tracks-sticky`. That made the sticky element ~600,000 px tall, so
 * `position: sticky` had nothing left to pin against and the filter bar
 * scrolled away with the page (broke the TASK-037 invariant in CLAUDE.md).
 *
 * The fix is structural: add an explicit `</div>` to close `#tracks-sticky`
 * before `<div id="track-list">`. We assert the parsed-DOM relationship
 * directly via jsdom so any future regression of the same closing-tag bug
 * (or a renamed selector that doesn't update the structure) fails loudly.
 */

import { describe, it, expect, beforeAll } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { JSDOM } from 'jsdom'

const __dirname = dirname(fileURLToPath(import.meta.url))
const INDEX_HTML = resolve(__dirname, '..', '..', 'docs', 'index.html')

let doc

beforeAll(() => {
  const html = readFileSync(INDEX_HTML, 'utf8')
  // Use the HTML5 spec parser (jsdom default) so unbalanced tags are repaired
  // exactly as Chrome would repair them. This is the whole point of the
  // regression — we want to see the *parsed* tree, not the literal source.
  // A real URL is required so localStorage access inside inline scripts
  // (jsdom default executes script tags on construction) doesn't throw on
  // about:blank's opaque origin.
  doc = new JSDOM(html, { url: 'http://localhost/' }).window.document
})

describe('#tracks-sticky DOM structure (issue #113)', () => {
  it('both anchor elements parse and exist exactly once', () => {
    expect(doc.querySelectorAll('#tracks-sticky')).toHaveLength(1)
    expect(doc.querySelectorAll('#track-list')).toHaveLength(1)
  })

  it('#track-list is NOT a descendant of #tracks-sticky (regression guard)', () => {
    const sticky = doc.getElementById('tracks-sticky')
    const list = doc.getElementById('track-list')
    expect(sticky.contains(list)).toBe(false)
  })

  it('#tracks-sticky and #track-list share the same parent <section>', () => {
    // Boundary: they must be siblings under #tracks-section. Anything else
    // (e.g. moving #track-list under <main>) would also break the layout
    // contract spelled out in CLAUDE.md's TASK-037 invariant.
    const sticky = doc.getElementById('tracks-sticky')
    const list = doc.getElementById('track-list')
    expect(sticky.parentElement).toBe(list.parentElement)
    expect(sticky.parentElement.id).toBe('tracks-section')
  })

  it('#filter-bar stays nested inside #tracks-sticky', () => {
    // Sibling guard: the bug was specifically "sticky never closes". If a
    // future edit over-corrects and accidentally hoists #filter-bar out of
    // #tracks-sticky, the sticky bar would lose its filter controls.
    const sticky = doc.getElementById('tracks-sticky')
    const filterBar = doc.getElementById('filter-bar')
    expect(filterBar).not.toBeNull()
    expect(sticky.contains(filterBar)).toBe(true)
  })
})
