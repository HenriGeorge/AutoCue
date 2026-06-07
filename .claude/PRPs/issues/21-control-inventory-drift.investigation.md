# Issue #21 — control-inventory drift vs live DOM (42 missing, 1 stale)

## Problem
`tests/e2e/control-inventory.spec.ts` drift guard fails on first real run after
PR #19 merge: 42 interactive DOM ids in `docs/index.html` are absent from
`tests/e2e/control-inventory.json`, and 1 inventory id (`#sb-guide-header`)
has no corresponding `<button|input|select|textarea>` element in the DOM.

Fingerprint: `[autocue-qa] control-inventory:drift-vs-live-dom:42-missing-1-stale`

## Root cause
The inventory shipped in PR #19 was hand-written from a Chrome MCP sweep that
missed:
- Filter popup internals (tag/genre/key popups): `tag-search`, `tf-clear-btn`,
  `genre-search`, `genre-clear-btn`, `ck-clear-btn`, `ck-related-btn`.
- Modal/dialog internals: `kbd-close-btn`, `ti-close`, `ti-download`, `yt-*`,
  `delete-confirm-btn`, `delete-cancel-btn`, `restore-confirm-btn`,
  `restore-cancel-btn`.
- Collapsed Cue Library Tools sub-operation inputs (dynamically built into
  `#cue-recolor-slots`): `cue-recolor-slot-0..7`, `cue-shift-ms`,
  `cue-keep-slots`.
- The mini player: `mini-play-btn`, `mini-scrubber`.
- Legacy XML upload zone file inputs with real ids: `file-input`,
  `audio-file-input`, `anlz-file-input` (the existing allowlist referenced
  non-existent ids `xml-input`/`audio-input`/`anlz-input`).
- First-class buttons missed entirely: `audio-folder-toggle`, `backup-btn`,
  `backup-inline-btn`, `delete-backup-btn`, `deselect-all-btn`,
  `path-warning-dismiss`, `scroll-top-btn`, `transition-score-btn`,
  `undo-btn`.

`#sb-guide-header` is a `<div>` (not enumerated by the drift guard's
`button|input|select|textarea` scan) — inventory stale entry.

`docs/index.html`:1888 (`anlz-file-input`), 1855 (`audio-file-input`), 1807
(`file-input`), 2034 (`tag-search`), 2055 (`genre-search`), 2047
(`ck-clear-btn`), 2048 (`ck-related-btn`), 2271 (`cue-recolor-slots` container,
`cue-recolor-slot-N` created in JS at line 3452), 2279 (`cue-shift-ms`), 2287
(`cue-keep-slots`), 2525 (`sb-guide-header` div), …

## Proposed solution
Two-pronged minimal edit, both in test assets only — no production code change:

1. Extend `ALLOWED_DOM_EXTRAS` in `tests/e2e/control-inventory.spec.ts` for the
   ids that match the existing "modal/dialog internals" or "collapsed sub-panel
   inputs" patterns. Drop the stale legacy entries (`xml-input`/`audio-input`/
   `anlz-input`) in favour of the real id names. The existing allowlist
   comment already establishes the precedent — these are not user-facing
   first-class controls, they are exercised when the modal/popup is opened by
   its trigger button.
2. Add first-class user-facing buttons to `tests/e2e/control-inventory.json`
   in the `cues` panel — `backup-btn`, `backup-inline-btn`, `delete-backup-btn`,
   `audio-folder-toggle`, `deselect-all-btn`, `transition-score-btn`,
   `undo-btn`, `mini-play-btn`, `mini-scrubber`, `path-warning-dismiss`,
   `scroll-top-btn`. These are reachable from the main Cues tab without
   opening a modal.
3. Remove the stale `sb-guide-header` row (the element is a `<div>` — the
   drift guard cannot see it; if a future contributor turns it into a
   `<button>` they can re-add it).

## Affected files
- `tests/e2e/control-inventory.spec.ts` — `ALLOWED_DOM_EXTRAS` set.
- `tests/e2e/control-inventory.json` — additions to `panelControls.cues`,
  removal of `sb-guide-header` from `panelControls.library`.

## Risks
- Misclassifying a first-class control as a "modal internal" → it would never
  be sweep-tested. Mitigation: I'm only allowlisting controls that live inside
  a popup/modal whose trigger button (e.g. `tag-filter-btn`, `kbd-hint-btn`)
  is already inventoried.
- Adding a row whose kind is wrong → sweep would try to type into a button,
  etc. Mitigation: kinds are derived from the actual element tags.

## Validation
- Leg A (pytest): N/A — no Python touched. Test should run but is unaffected.
- Leg B (vitest): N/A — no docs/index.html touched.
- Leg C (e2e Playwright): drift guard must pass; per-track scope and panel-names
  tests should remain green.
