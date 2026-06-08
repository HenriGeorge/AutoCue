# Issue #113 — `#track-list` nested inside `#tracks-sticky` due to unbalanced `</div>`

## Problem

In `docs/index.html` the section opening at line 2393 has an extra unclosed `<div>`. The `</div>` at line 2522 is commented as closing `#tracks-sticky`, but it actually closes `#filter-bar` (opened at line 2408). The HTML5 parser silently leaves `#tracks-sticky` open through `</section>` on line 2524, so `#track-list` at 2523 ends up as a descendant of `#tracks-sticky`.

Verified via DOM probe in the filed issue:

```
document.querySelector('#tracks-sticky').contains(document.querySelector('#track-list')) → true
```

This breaks the TASK-037 invariant in `CLAUDE.md`: `#tracks-sticky` is supposed to be a sibling of `#track-list`, anchored to document scroll. Because the sticky container now wraps the ~600k px virtualized list, the sticky bar becomes the scrolling container itself and `getBoundingClientRect().top` tracks `window.scrollY` 1:1 — sticky behavior disappears.

User effect: the filter bar (search, phrase/beats checkboxes, sort buttons, BPM legend) scrolls off-screen instead of pinning.

## Root cause (file:line)

`docs/index.html` lines 2393–2524, specifically:

- 2394 `<div id="tracks-sticky">` opens
- 2408 `<div id="filter-bar">` opens
- 2489 closes `.filter-row-facets`
- 2522 `</div><!-- /#tracks-sticky -->` — the comment lies; this `</div>` closes `#filter-bar`
- 2523 `<div id="track-list">` opens **inside** still-open `#tracks-sticky`
- 2524 `</section>` — HTML5 parser implicitly closes both remaining divs

Counting the open divs from 2394 to 2522 (recursively): `#tracks-sticky`, `#filter-bar` are both still open when 2522 fires; the single `</div>` only resolves `#filter-bar`.

## Proposed solution

Insert one explicit `</div>` between the real `#filter-bar` close and `<div id="track-list">`, and relocate the comment marker so it labels the correct close. Minimal, surgical edit (≤ 5 lines) inside `docs/index.html`.

```html
    </div><!-- /#filter-bar -->
    </div><!-- /#tracks-sticky -->
    <div id="track-list"></div>
```

(Existing line 2522 currently reads `</div><!-- /#tracks-sticky -->`. We split it into two closes with correct labels — net +1 line.)

## Affected files

- `docs/index.html` — markup fix (one inserted `</div>` + corrected comment).
- `tests/web/dom-structure.test.js` — new vitest regression guard verifying that `#tracks-sticky` does NOT contain `#track-list` and that `#track-list` is a sibling within `#tracks-section`.

## Risks

- CSS specificity: `#tracks-sticky` has its own background and shadow. Once it correctly closes before the list, the legend's bottom shadow/background should now end at the bottom of the legend instead of bleeding past every card. Verified by visual inspection of the rule for `#tracks-sticky` in the same file — uses `position: sticky` with `top: 0`. No descendant selectors of `#tracks-sticky` reach into `#track-list` other than via global styles.
- Virtualizer wiring: `Virtualizer` uses `window` as the scroll source against `#track-list` as the container (per `tests/web/virtualization-wiring.test.js`). It only cares about `#track-list.getBoundingClientRect()` and `window.scrollY`. Sibling vs nested has no effect on the math because the container's offset comes from layout — and once unnested, the offset is smaller/lower (after the sticky bar), which is the intended layout.
- The IntersectionObserver "shadow on scroll" pinned-state observer (called out in TASK-037) becomes meaningful again because the sticky container actually pins instead of scrolling.
- No JS selector that I can find queries `#tracks-sticky > #track-list` or relies on nesting (verified via `grep '#tracks-sticky' docs/index.html` — usages are CSS-only and the JS that touches it does `getElementById('tracks-sticky')` and reads its rect, never descendant traversal).

## Test plan

1. Vitest regression: parse `docs/index.html` with `jsdom`, assert:
   - `#tracks-sticky` exists.
   - `#track-list` exists.
   - `!#tracks-sticky.contains(#track-list)` (this would FAIL on `main` — the regression guard).
   - Both `#tracks-sticky` and `#track-list` are direct children of `#tracks-section`.
2. Existing `tests/web/virtualization-wiring.test.js` must continue to pass (Virtualizer math is unaffected by sibling-vs-nested).
3. e2e leg: existing Playwright suite continues to pass (no selector under `#tracks-sticky #track-list` is used).
