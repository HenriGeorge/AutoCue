/**
 * Issue #113 regression guard — the markup at the bottom of #tracks-section
 * must not nest #track-list inside #tracks-sticky.
 *
 * Background: docs/index.html line 2522 once carried a single `</div>` with a
 * `<!-- /#tracks-sticky -->` comment that lied — it actually closed `#filter-bar`,
 * leaving `#tracks-sticky` open through `</section>`. The HTML5 parser silently
 * recovered, but the runtime result was `#tracks-sticky.contains(#track-list)`
 * === true, which makes the ~600k px virtualized list the scroll container of
 * the supposed-to-be-sticky filter bar — breaking the TASK-037 invariant in
 * CLAUDE.md.
 *
 * This test parses docs/index.html and asserts the structural contract:
 *   #tracks-sticky and #track-list are SIBLINGS inside #tracks-section,
 *   not parent/child.
 *
 * It will FAIL on main prior to the fix (regression guard) and PASS once
 * the extra `</div>` is in place.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const INDEX_HTML = resolve(__dirname, '..', '..', 'docs', 'index.html')
const html = readFileSync(INDEX_HTML, 'utf8')

describe('Issue #113 — #tracks-sticky structural contract', () => {
  // Parse the full document with jsdom's DOMParser. Spec-compliant HTML5
  // parser handles the imbalance the same way Chrome does, so this catches
  // the production bug.
  const doc = new DOMParser().parseFromString(html, 'text/html')

  const section = doc.getElementById('tracks-section')
  const sticky = doc.getElementById('tracks-sticky')
  const list = doc.getElementById('track-list')

  it('all three structural anchors exist', () => {
    expect(section, '#tracks-section missing').not.toBeNull()
    expect(sticky, '#tracks-sticky missing').not.toBeNull()
    expect(list, '#track-list missing').not.toBeNull()
  })

  it('#track-list is NOT nested inside #tracks-sticky (regression guard)', () => {
    // The bug: parser left #tracks-sticky open, so it ended up wrapping the
    // entire virtualized list. This assertion would FAIL on main.
    expect(sticky.contains(list)).toBe(false)
  })

  it('#tracks-sticky and #track-list are direct children of #tracks-section', () => {
    expect(sticky.parentElement).toBe(section)
    expect(list.parentElement).toBe(section)
  })

  it('#tracks-sticky comes before #track-list in document order', () => {
    // DOCUMENT_POSITION_FOLLOWING (4) — list follows sticky.
    const order = sticky.compareDocumentPosition(list)
    // eslint-disable-next-line no-bitwise
    expect(order & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
