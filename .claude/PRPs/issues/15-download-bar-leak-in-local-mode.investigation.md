# Issue #15 — `#download-bar` leaks into local mode

## Problem
On opening the web UI in local mode (e.g. `http://127.0.0.1:7432/`), the legacy XML-mode `#download-bar` appears at the bottom of the viewport immediately, with default-projection text like `Ready to import: 1 track · 8 cues`, before the user selects anything or uploads any XML. The bar persists across all three tabs (Cues / Library / Discover) because it is a top-level fixed-bottom element. It also exposes legacy `Apply to Rekordbox` / `Color tracks by BPM` / `Delete all cues` controls that fire on the whole filtered set without any selection signal — duplicating (and conflicting with) the new selection-driven `#action-bar`.

## Root Cause
`docs/index.html:3601` — inside `loadTracksFromServer` (the local-mode track loader), the unconditional staggered fade-in loop adds the `visible` class to `#download-bar`:

```js
['settings-section', 'tracks-section', 'download-bar'].forEach(function(id) {
  var el = document.getElementById(id);
  setTimeout(function() {
    el.classList.add('visible');
    ...
  }, _sectDelay);
  _sectDelay += 70;
});
```

`#download-bar` is part of the Pages-mode XML round-trip flow (`Ready to import: …`). In local mode the canonical bottom bar is `#action-bar`, which slides in only when `selectedTrackIds.size > 0`. The CSS at line 906 even has a `body:has(#download-bar.visible)` rule to stack them — but that stacking only makes sense in Pages mode where `#download-bar` is the legitimate XML-import bar.

The XML-mode upload path that legitimately shows the bar lives separately at line 9401 (inside the `reader.onload` for an uploaded XML).

## Proposed Solution
Filter `'download-bar'` out of the staggered fade-in loop when `localMode` is true. Single-line change in the `forEach` source list.

Also defensively *remove* the `visible` class from `#download-bar` in the same path, so that if a future code-path ever added it earlier it would still be cleared on local-mode init.

## Affected Files
- `docs/index.html` — `loadTracksFromServer` (~line 3601)
- `tests/web/ui-logic.test.js` — new test asserting the fade-in list filtering invariant in local mode (regression guard + boundary at the local/Pages mode switch)

## Risks
- Pages-mode regression: if `localMode === false`, the bar still needs to fade in. The fix keeps the existing behavior for Pages mode (the XML upload handler at line 9401 is the canonical Pages-mode trigger, and the staggered fade-in path is a separate, additive entry).
- The CSS rule at line 906 (`body:has(#download-bar.visible) #action-bar`) is unchanged — it still works correctly because `#download-bar.visible` is now only set when an XML is uploaded (Pages mode), which is the only mode where stacking with `#action-bar` was ever intended.
