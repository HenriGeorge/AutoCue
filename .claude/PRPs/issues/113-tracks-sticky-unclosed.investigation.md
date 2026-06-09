# Issue #113 — `#tracks-sticky` is never closed; `#track-list` nests inside it

## Problem

The filter bar (`#tracks-sticky`) is intended to use `position: sticky` so search/sort/legend stay pinned while the track list scrolls. In reality it scrolls away with the page because the HTML parser leaves `#tracks-sticky` open through `#track-list`. The sticky element ends up containing the entire ~600k-px-tall track list and effectively becomes the scrolling container itself.

## Root cause (file:line)

`docs/index.html:2394-2524`. Stack trace by hand:

- L2394 OPEN `#tracks-sticky`
- L2408 OPEN `#filter-bar`
- L2410 OPEN `.filter-row-primary` → CLOSE L2431
- L2433 OPEN `.filter-row-facets` → CLOSE L2489
- L2490 `#sort-bar` and L2500 `#bpm-legend` siblings (correctly inside an ancestor)
- L2522 `</div><!-- /#tracks-sticky -->` — the comment is **wrong**; this `</div>` closes `#filter-bar`, not `#tracks-sticky`.
- L2523 `<div id="track-list"></div>` is opened with `#tracks-sticky` still on the stack.
- L2524 `</section>` triggers HTML5 auto-close of `#tracks-sticky`, but by then `#track-list` is already inside it.

Probe (per QA report): `document.querySelector('#tracks-sticky').contains(document.querySelector('#track-list'))` returns `true`.

## Proposed solution

Add an explicit `</div>` to close `#tracks-sticky` between L2522 and L2523. Update the misleading comment so the existing close points to `#filter-bar` and the new one points to `#tracks-sticky`.

Result (new diff at the same site):

```html
    </div><!-- /#filter-bar -->
    </div><!-- /#tracks-sticky -->
    <div id="track-list"></div>
```

Net change: **+1 line of HTML**, one comment correction. Sort-bar and bpm-legend remain inside `#filter-bar`, which is the existing (working) behavior — out of scope for this fix.

## Affected files

- `docs/index.html` — single-line structural fix at L2522–2523.
- `tests/web/regressions/track-list-not-nested-in-tracks-sticky.test.js` — new vitest regression that fails on the unfixed HTML.

## Risks

- None to runtime JS — selectors `#track-list`, `#tracks-sticky`, `#filter-bar` continue to resolve to the same elements.
- CSS: `#tracks-sticky` becomes the size of the filter bar (correct), so `position: sticky` will now actually pin. This is the intended user-facing change.
- Virtualizer (TASK-033) still scrolls the document, unchanged.
- Action bar (`position: fixed`) is unaffected.
