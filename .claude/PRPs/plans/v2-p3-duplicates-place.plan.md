# Feature: AutoCue 2.0 — P3 Duplicates as a place (rail place → center-pane view)

## Summary

Re-surface the finished Library duplicate-detection capability — backend grouping +
keeper heuristic, SSE scan, safety-hardened cascade delete, undo — as a **rail place
that opens a center-pane view** in the P2 workbench shell. The algorithm, keeper
ranking, 13-table cascade, per-session backup window, concurrency lock and SSE
contract are preserved **byte-for-byte and re-driven, never re-implemented**: the v2
module owns only the door (rail entry), the center-pane swap, and the restyle; every
scan and every write delegates to the legacy functions in `docs/js/02-local-ops.js`
via `window.ACBridge` / native DOM events, per the rail-module contract
(`docs/js/v2/workbench/rail.js:9–27`).

Adopted decisions (do not reopen):
- **Confirm modal:** keep and reuse `#duplicates-confirm` (`docs/index.html:586`) —
  it is already a body-level sibling of `#duplicates-section`, NOT inside it, so
  "re-parent" reduces to "leave it in place when the section is removed". All guards
  (250 ms primary-disable, focus-trap, Cancel-focused, ESC-abort, in-place progress)
  ride along untouched.
- **Restore:** the A-layer status-sentence **sheet is canonical**; the inline undo
  banner (`_showDuplicatesUndoToast`, `02-local-ops.js:844`) is **kept** as an
  in-view convenience.
- **Consent gradient:** NOT extended to duplicates-delete (faef4c4 left it out
  deliberately; the 250 ms-disable + per-copy-evidence modal satisfies consent).
- **Workbench is default-on** in local mode (main `c3dcff0`;
  `shell.js` `isWorkbenchOn()` reads `localStorage.ac_workbench !== '0'`).

Source PRD: `.claude/PRPs/prds/v2-p3-duplicates-place.prd.md` (R1–R13; R7 =
"confirm modal preserved"). Branch: **`feature/v2-p3-duplicates-place`**, base
`main` (post-P2; tabs already retired — `#tab-nav` is `display:none` at
`docs/index.html:55` on main).

## Key facts (verified against this repo; line refs from main where the P2 worktree diverges)

- **Legacy duplicates UI** — all in `docs/js/02-local-ops.js`, plain top-level
  functions in the shared classic-script scope (NOT on `window`):
  `_renderDuplicateGroup` (:322, mutable keeper radio + internal `_refresh()` :380
  that recomputes `div.dataset.nonKeeperIds`, delete-pill label, keeper green-wash,
  same-path chips), `scanDuplicates` (:498 — reads `#duplicates-scan-btn`,
  `#duplicates-status-label`, `#duplicates-progress`, `#duplicates-summary`,
  `#duplicates-empty`, `#duplicates-list` by id; builds the dynamic
  `#duplicates-bulk-delete-btn` at :570), `_onTracksDeleted` (:633 — surgical prune;
  **does NOT call `AppState.signal('tracks')` today** — R9 needs a one-line add),
  `_refreshDuplicatesSummaryAfterDelete` (:652), focus trap (:682),
  `_openDuplicatesConfirm` (:709), `_closeDuplicatesConfirm` (:739, ESC-during-delete
  aborts), `_runDuplicatesDelete` (:754 — POSTs `{track_ids, dry_run:false}`, SSE
  progress into the modal, checks `r.ok` so the `_rb_running` 409 surfaces as a
  toast), `_showDuplicatesUndoToast` (:844 — 30 s banner anchored above
  `#duplicates-summary`, POSTs `/api/restore {filename}`). Modal buttons are wired
  once at parse time in `_wireDuplicatesConfirm` (:906–924, incl. the document-level
  ESC handler keyed on `aria-hidden`).
- **Markup**: `#duplicates-section` (`docs/index.html:562`, `display:none`, heavy
  inline styles); confirm modal + backdrop at :585–:609 (body-level siblings —
  survive section removal); scan button wired at
  `docs/js/08-set-builder-boot.js:823`; workbench rail `#wb-rail`
  (`docs/index.html:1509`) with three `.wb-rail-section` groups + `#wb-rail-health`;
  inspector `#wb-inspector` (:1527); grid header `#wb-grid-head` (:410, hidden until
  `body.wb-active`).
- **Bridge**: `window.ACBridge` lives at the END of
  `docs/js/08-set-builder-boot.js:919` (accessor closures over the shared lexical
  scope — the ONLY way v2 modules can reach top-level `let`/function bindings).
  Pattern: read-only state accessors + fn pass-throughs + `setCrate`.
- **Shell**: `docs/js/v2/workbench/shell.js` — `activate()` adds `body.wb-active`,
  un-hides rail/inspector, forces `switchTab('cues')`, `_renderCrates()` paints
  `.wb-crate.active` from `ACBridge.crate()`; `deactivate()` reverses + calls
  `clearInspector()` (`inspector.js:143`). Rail playlists/saved-filters in
  `rail.js` drive legacy controls via value + native events only.
- **Palette**: `docs/js/v2/commands.js` (NOT `palette/commands.js`) —
  `find-duplicates` currently runs `_goto('library','duplicates-section')` +
  `_click('duplicates-scan-btn')`, the orphaned door the PRD describes.
  `tests/e2e/v2-global-layer.spec.ts:36–41` asserts that command scrolls
  `#duplicates-section` into viewport — **must be updated in the same task that
  removes the section**.
- **Drift guards**: `tests/e2e/control-inventory.json` — sections
  `globalControls` / `panelControls.{cues,library,discover}` / `perTrack`;
  `duplicates-scan-btn`, `duplicates-confirm-cancel`, `duplicates-confirm-go` sit
  under `panelControls.library` (:261–:272). The guard reconciles **both
  directions** (missing AND extra). `selectors-exist.spec.ts` requires adding ids
  BEFORE any spec references them. Dynamic-only ids (e.g. `pal-opt-N`) go on the
  inventory spec's ignore-list (`control-inventory.spec.ts:95–135`).
- **Existing duplicates Vitest** (`tests/web/duplicates-panel.test.js`,
  `duplicates-delete-confirm.test.js`, `duplicates-phase3.test.js`) are
  **vendored-logic tests, not DOM-coupled** — they survive the markup move
  unchanged. `tests/web/v2-commands.test.js:24` asserts the `find-duplicates`
  command id exists (id is kept, so it survives).
- **Tokens**: `--danger: #e74c3c` exists (`docs/css/app.css:38`); `--amber` is used
  only with a hex fallback (`var(--amber, #c98a00)`) — the restyle must reference
  tokens only (use `--danger`/`--muted`/`--green-wash`; add nothing new unless a
  token is genuinely missing, in which case define it in the `:root`/`html.dark`
  blocks, both themes). Mono = `--font-mono`; pills `--radius-pill`; data chips 4px;
  group cards `--radius-xl`.
- **Invariants**: center swap must toggle `hidden`/body-class only — `#track-list`
  is never detached or re-parented (Virtualizer `scrollSource:'window'`,
  IntersectionObserver shadow, TASK-033/037). `#action-bar` stays `position:fixed`.
  No backend edits; no new endpoints. e2e runs ALONE (contention flake #189).

## Tasks (execute in order; each merges green on the three-leg stack)

### T1 — Legacy seams: ACBridge pass-throughs + events (additive, zero behavior change)

**Goal**: give v2 the sanctioned handles it needs; fix the R9 invalidation gap.
**Files**: `docs/js/08-set-builder-boot.js` (bridge block :919),
`docs/js/02-local-ops.js`, `tests/web/v2-duplicates-place.test.js` (new).

Steps:
1. Append to `window.ACBridge`:
   - `scanDuplicates: () => scanDuplicates()`,
   - `openDuplicatesConfirm: (opts) => _openDuplicatesConfirm(opts)`,
   - `onTracksDeleted: (ids) => _onTracksDeleted(ids)`.
2. In `_onTracksDeleted` (02-local-ops.js:633), after the surgical prune add
   `if (window.AppState) AppState.signal('tracks');` — repaints rail crate counts +
   status sentence through the existing bus (R9). Verify no double-render side
   effects (the signal only triggers subscribed repaints, not `renderTracks`
   directly — confirm by reading the `AppState.subscribe('tracks', …)` call sites).
3. In `_showDuplicatesUndoToast` (:844), FIRST line: dispatch
   `window.dispatchEvent(new CustomEvent('autocue:duplicates-deleted', { detail: { deleted, requested, cancelled, backup_path } }))`
   — the seam T5's restore sheet consumes. Banner behavior unchanged.
4. New Vitest `tests/web/v2-duplicates-place.test.js` — source-contract section (via
   `loadAppHtml()` from `tests/web/_source.js`): assert the three bridge accessors,
   the `AppState.signal('tracks')` call inside `_onTracksDeleted`, and the
   `autocue:duplicates-deleted` dispatch exist in source.

VALIDATE: `npm test` (new + all existing duplicates tests green); `pytest`
untouched-green. No UI change to verify.

### T2 — Place skeleton: rail entry + center-pane swap + inspector hide (inert scaffold)

**Goal**: R1/R2/R3 mechanics, content-free — the door and the swap, nothing inside yet.
**Files**: `docs/index.html`, `docs/js/v2/workbench/duplicates.js` (new),
`docs/js/v2/workbench/shell.js`, `docs/js/v2/workbench/rail.js`,
`docs/js/v2/main.js`, `docs/css/app.css`, `tests/e2e/selectors-exist.spec.ts`,
`tests/e2e/control-inventory.json`, `tests/web/v2-duplicates-place.test.js`.

Steps:
1. Markup: in `#wb-rail`, add a fourth `.wb-rail-section` ("Maintenance") below
   Saved filters with one `.wb-crate`-styled button `#wb-dupes-place` (label
   "Duplicates"). Insert `<section id="wb-dupes-pane" hidden>` **immediately after
   `#track-list` inside the same parent** (inherits the center-column flow; rail and
   inspector are fixed flanks so no grid re-template needed). Scaffold content:
   toolbar `div` + empty hosts (filled in T3).
2. `docs/js/v2/workbench/duplicates.js` (ES module; export `activate`, `deactivate`,
   `isActive`; register on `window.AC2.duplicates` from `main.js`):
   - `activate()`: guard `ACBridge.isLocalMode()`; `clearInspector()` (import from
     `./inspector.js`); set `hidden` on `#tracks-sticky`, `#track-list`,
     `#wb-grid-head`, `#wb-inspector`; remove `hidden` on `#wb-dupes-pane`; add
     `document.body.classList.add('wb-place-dupes')`; mark `#wb-dupes-place.active`;
     lazy-scan hook (no-op until T3). **Never detach/move `#track-list`.**
   - `deactivate()`: reverse every toggle, remove the body class, re-show inspector
     (empty state), and call `ACBridge.renderTracks()` so the Virtualizer repaints
     the re-shown grid at the current scroll position.
   - Re-clicking the active `#wb-dupes-place` toggles back (place toggle = the
     no-new-id exit).
3. Exits: in `shell.js` `_renderCrates()` crate click handler and `rail.js` playlist
   row click handler, call `window.AC2.duplicates?.deactivate()` first (v2→v2 import
   is also fine inside shell.js). In `shell.js` `deactivate()` (workbench off), also
   deactivate the place. While the place is active, `_renderCrates` paints no crate
   `.active` (check `AC2.duplicates.isActive()`).
4. CSS under the `/* ── v2: workbench ── */` banner: `.wb-rail-section` reuse for
   the new group; `#wb-dupes-pane` layout (max-width matching the grid column);
   belt-and-braces `body.wb-place-dupes #tracks-sticky, body.wb-place-dupes
   #track-list, body.wb-place-dupes #wb-grid-head { display:none !important; }` so a
   legacy `style.display` write can't defeat the `hidden` attribute. Green `.active`
   state on the rail entry (green = signal). Tokens only.
5. Guards: add `#wb-dupes-place`, `#wb-dupes-pane` to `selectors-exist.spec.ts`;
   add `wb-dupes-place` (button) to `control-inventory.json` `globalControls`.
6. Vitest (jsdom): activation toggles (`hidden` flips, body class, `.active`),
   deactivation restores, inspector hidden on activate / restored on deactivate
   (R1, R3); deactivation fires on crate click.

VALIDATE: `npm test`; `pytest`; `cd tests/e2e && npx playwright test
selectors-exist control-inventory` (run alone). Chrome at `127.0.0.1:7432`: click
the rail entry → empty pane swaps in, grid + sticky disappear, inspector gone;
click a crate → grid returns and still virtualizes/sticks. Both themes,
screenshots to the user.

### T3 — Re-host the live machinery (the BIGGEST, atomic PR: markup move + door rewire + inventory reconcile)

**Goal**: R4, R6, R7, R12, R13 — the pane becomes the real Duplicates surface and
the legacy section is gone, in ONE task so the drift guard, the palette and the e2e
baseline reconcile together (PRD risk mitigation).
**Files**: `docs/index.html`, `docs/js/02-local-ops.js`,
`docs/js/08-set-builder-boot.js`, `docs/js/v2/workbench/duplicates.js`,
`docs/js/v2/commands.js`, `tests/e2e/control-inventory.json`,
`tests/e2e/selectors-exist.spec.ts`, `tests/e2e/v2-global-layer.spec.ts`,
`tests/web/v2-duplicates-place.test.js`, `tests/web/v2-commands.test.js`.

Steps:
1. **Move the hosts, keep the ids** (zero edits to the SSE reader): relocate
   `#duplicates-status-label`, `#duplicates-progress`, `#duplicates-summary`,
   `#duplicates-empty`, `#duplicates-list` from `#duplicates-section` into
   `#wb-dupes-pane` (toolbar row + list area). Delete the now-empty
   `#duplicates-section` and its intro copy. **Leave `#duplicates-confirm` +
   `#duplicates-confirm-backdrop` exactly where they are** (:585–:609 — body-level;
   adopted decision). `scanDuplicates`, `_refreshDuplicatesSummaryAfterDelete`,
   `_showDuplicatesUndoToast` and `_wireDuplicatesConfirm` keep working untouched
   because every id they read survives.
2. **Rescan pill**: the legacy scan button BECOMES the toolbar pill — re-id
   `duplicates-scan-btn` → `wb-dupes-rescan` in `docs/index.html` (now inside the
   pane toolbar, label "Rescan") and update its three references:
   `02-local-ops.js:498`, `08-set-builder-boot.js:823`, `commands.js:33` (next
   step rewrites that command anyway). `_setBtnCancellable` / abort behavior rides
   along.
3. **Bulk delete as a static toolbar verb**: replace the dynamically-created
   `#duplicates-bulk-delete-btn` (`02-local-ops.js:569–594`) with a static
   `#wb-dupes-bulk-delete` button in the pane toolbar (disabled by default):
   extract the existing click closure into a named `_onDuplicatesBulkDelete()`
   (same re-collect-`dataset.nonKeeperIds`-at-click-time logic →
   `_openDuplicatesConfirm` → on success `_onTracksDeleted` + re-scan), wire it
   once in `_wireDuplicatesConfirm`; the scan-done branch and
   `_refreshDuplicatesSummaryAfterDelete` (:660) now only update the static
   button's label/disabled. Behavior identical; write path untouched (R6).
4. **Lazy scan**: `duplicates.js` `activate()` calls `ACBridge.scanDuplicates()` on
   FIRST activation only (track a `_scannedOnce` flag; "Rescan" covers the rest).
5. **⌘K (R12)**: in `commands.js`, rewrite `find-duplicates` (keep the id —
   `v2-commands.test.js:24` pins it) and add `go-duplicates` ("Go to Duplicates",
   group "Go to"): both run
   `window.AC2?.workbench?.setWorkbench(true); document.getElementById('wb-dupes-place')?.click();`
   (explicit navigation intent overrides an opt-out flag; delegation via `.click()`
   per the rail rule). Remove the dead `_goto('library', 'duplicates-section')`.
6. **Drift-guard reconcile (both directions)**: in `control-inventory.json` —
   remove `duplicates-scan-btn` from `panelControls.library`; move
   `duplicates-confirm-cancel` / `duplicates-confirm-go` (both
   `safeOnRealDb:false`) from `panelControls.library` → `globalControls`; add
   `wb-dupes-rescan` (button) and `wb-dupes-bulk-delete` (button,
   `safeOnRealDb:false`) to `globalControls`. Per-group delete buttons stay
   class-only (`.duplicates-group-delete`, no id — already invisible to the
   guard; note it in the json `notes` anyway). Update `selectors-exist.spec.ts`
   (`#wb-dupes-rescan`, `#wb-dupes-bulk-delete`; drop none — `#duplicates-section`
   was never in it). Update `v2-global-layer.spec.ts:36–41`: the palette command now
   asserts `#wb-dupes-pane` becomes visible (not `#duplicates-section` in viewport).
7. Vitest: source-contract — `scanDuplicates` reads `wb-dupes-rescan`; no
   `duplicates-section` / `duplicates-scan-btn` / `duplicates-bulk-delete-btn`
   strings remain anywhere in `docs/`; `_onDuplicatesBulkDelete` routes through
   `_openDuplicatesConfirm`; the v2 module contains **no bare**
   `parsedTracks`/`scanDuplicates` outside `ACBridge` (R6 "no parallel delete
   loop": regex-assert `duplicates.js` contains no `fetch('/api/duplicates`
   string at all — only the bridge calls). Command test: `find-duplicates` +
   `go-duplicates` resolve and their `run()` targets `wb-dupes-place`.

VALIDATE: `pytest` (proves zero backend drift); `npm test`; `cd tests/e2e && npx
playwright test` **full suite, run alone** — the inventory + selectors + global-layer
guards are the gate for this task; zero new failures vs baseline. Chrome: rail →
Duplicates → scan streams groups into the pane; keeper radio flips recompute label +
chips; per-group and bulk delete open the confirm modal (Cancel, ESC, 250 ms disable
all observed); delete works end-to-end on a scratch DB; undo banner restores.
Screenshots both themes.

### T4 — Restyle to the five rules (presentation-only legacy edits + pane CSS)

**Goal**: R5 fidelity preserved, R11 satisfied — same DOM contract, token-clean skin.
**Files**: `docs/js/02-local-ops.js`, `docs/css/app.css`,
`tests/web/v2-duplicates-place.test.js`, `tests/web/design-tokens.test.js` (extend
if it sweeps for hex).

Steps:
1. In `_renderDuplicateGroup` / the scan-done summary / `_showDuplicatesUndoToast`,
   replace inline `style.cssText` with classes (`.wb-dup-group`, `.wb-dup-head`,
   `.wb-dup-count-chip`, `.wb-dup-row`, `.wb-dup-meta`, `.wb-dup-path-chip`,
   `.wb-dup-delete`, `.wb-dup-undo-banner`…) defined under the v2 CSS banner.
   **Do not touch the logic**: `dataset.nonKeeperIds`, the radio wiring, `_refresh`'s
   recompute order, `_slideToggle`, the 30 s drain bar all stay byte-identical —
   only presentation attributes move to CSS. Keeper highlight keeps the existing
   green-wash `color-mix(... var(--green) 12% ...)` (move it into the
   `.wb-dup-row.keeper` class; `_refresh` toggles the class instead of
   `row.style.background`).
2. Kill every hardcoded hex: `#e4384e` (group delete :362, old bulk style, progress
   error :607) → `var(--danger)` (exists, `app.css:38`); `var(--amber, #c98a00)`
   fallbacks → a real token (add `--amber` to `:root` + `html.dark` if absent —
   both themes). Destructive verbs (`#wb-dupes-bulk-delete`, `.wb-dup-delete`) =
   **ink-pill with danger tint** (pill radius, never green). Counts, BPM, key,
   duration, paths, ids = `--font-mono`. `N copies` chip = 4px `--radius-sm` mono
   data chip. Group cards `--radius-xl` elevated; controls `--radius-pill`;
   `prefers-reduced-motion` respected (the existing `fade-in-up` + drain transition
   already honor the global rule — verify).
3. Toolbar layout: Rescan (neutral pill) · mono summary sentence
   (`#duplicates-summary` restyled: `N groups · M surplus copies of K scanned`) ·
   `#wb-dupes-bulk-delete` (destructive ink-pill). Empty state ("Library is clean")
   and SSE progress line restyled in place.
4. Vitest: extend the vendored recompute coverage in
   `tests/web/duplicates-phase3.test.js`-style for the chip/label/nonKeeperIds
   derivation on a fixture group (R5); source-contract regex: no `#e4384e` /
   `#c98a00` literals remain in `docs/js/02-local-ops.js` or the new CSS; `_refresh`
   still writes `dataset.nonKeeperIds`.

VALIDATE: `npm test`; Chrome both themes — full five-rules pass on the pane
(mono data, ink-pill destructive verb, 4px chips, `--radius-xl` cards, green only on
keeper/active/success), screenshots to the user; quick e2e smoke
`npx playwright test v2-global-layer` (alone).

### T5 — Restore as a status-sentence sheet (A-layer canonical; banner kept)

**Goal**: R8 — grammar #2's emergency exit hangs off the status sentence.
**Files**: `docs/index.html`, `docs/js/v2/workbench/duplicates.js` (or a small
`docs/js/v2/restore-sheet.js` if cleaner), `docs/css/app.css`,
`tests/e2e/selectors-exist.spec.ts`, `tests/e2e/control-inventory.json`,
`tests/web/v2-duplicates-place.test.js`.

Steps:
1. Markup: add a hidden `#status-restore` fact button (+ `#status-sep-restore`) to
   `#app-status` (`docs/index.html:64–76`, same pattern as `status-needcues`), and a
   static sheet `#wb-restore-sheet` (hidden, anchored under the status strip) with
   mono backup filename, sentence "N tracks deleted — restore?", `#wb-restore-go`
   (ink pill) and a dismiss.
2. v2 logic: listen for `autocue:duplicates-deleted` (T1 seam). When
   `detail.backup_path` exists: show the fact ("`N` deleted · Undo"), clicking it
   opens the sheet; `#wb-restore-go` POSTs `/api/restore` with
   `{ filename: backup_path.split('/').pop() }` (exact legacy body), checks `r.ok`,
   toasts, then hides fact + sheet and re-runs `ACBridge.scanDuplicates()` if the
   place is active. Expire fact + sheet after the same **30 s** window as the
   banner (per-session backup semantics — deletes <30 s apart reuse one backup, so
   a newer event simply replaces the sheet's path/timer). The inline banner stays
   untouched (adopted decision).
3. Guards: `#status-restore`, `#wb-restore-go` → `selectors-exist` +
   `control-inventory.json` (`globalControls`; `wb-restore-go` is a WRITE —
   `safeOnRealDb:false`; `status-restore` is dynamic-visibility but static-id, safe).
4. Vitest: stubbed-fetch test — event with `backup_path` → fact visible, sheet
   shows the filename, click POSTs `/api/restore` with the exact body (R8); expiry
   hides both; event without `backup_path` (cancelled-early case) shows nothing.

VALIDATE: `npm test`; Chrome: delete a group on a scratch DB → fact appears in the
status sentence, sheet restores, library reloads clean; both themes screenshots;
`pytest` (restore endpoint untouched — still green).

### T6 — Playwright spec, both-themes audit, docs, final gate + PR

**Goal**: the layout truths JSDOM can't see (jsdom-layout-blind-spot rule) + parity
sign-off.
**Files**: `tests/e2e/v2-duplicates-place.spec.ts` (new),
`.claude/project/web-ui.md`, `CLAUDE.md` (Library Duplicates paragraph: add the
"reached via the workbench rail place / `#wb-dupes-pane`" sentence — do NOT alter
the cascade/keeper/backup text), PR.

Steps:
1. New e2e `tests/e2e/v2-duplicates-place.spec.ts` (mock `/api/duplicates` SSE +
   `/api/duplicates/delete` routes like the existing discover-v2 mocks):
   - click `#wb-dupes-place` → `#wb-dupes-pane` visible, `#track-list` +
     `#tracks-sticky` + `#wb-grid-head` hidden, `#wb-inspector` hidden,
     `#action-bar` `position` computes `fixed` (R2);
   - swap BACK (click a crate) → grid re-shows, `#tracks-sticky` still pins after
     scrolling (sticky/occlusion invariant survived the hide/show round-trip);
   - mocked scan renders groups; flip a keeper radio → per-group delete label
     recomputes (R5 live);
   - delete pill opens `#duplicates-confirm`; Cancel closes; reopen; ESC closes
     (R7); confirm-go with mocked SSE completes and the undo banner + status fact
     appear (R8 surface);
   - mocked 409 (`_rb_running`) on delete → failure toast, modal closes, zero rows
     "deleted" (R10);
   - network capture: the ONLY scan request is `GET /api/duplicates` (R4);
   - both themes screenshot of the pane.
2. Full both-themes design audit on every new surface (five rules checklist).
3. Docs: `.claude/project/web-ui.md` — new "Duplicates place" paragraph
   (center-pane swap via `hidden` + `body.wb-place-dupes`, the delegation contract,
   host-id reuse, restore sheet); CLAUDE.md track record. Commit AI-asset changes
   with a `Context:` section.
4. Final three-leg gate from repo root, e2e run ALONE (#189):
   `pytest` → `npm test` → `cd tests/e2e && npx playwright test`. Zero new e2e
   failures vs the known baseline. Lighthouse not worse than pre-P3 on a large
   library. Open PR (base `main`):
   `feat(web): P3 duplicates as a place — rail place + center-pane view (AutoCue 2.0)`.

VALIDATE: three legs green; new spec green in both themes; screenshots delivered;
PR open.

## Full-suite validation (per merge AND final)

```bash
pytest                                   # 1446 tests — proves zero backend/cascade drift
npm test                                 # Vitest incl. new v2-duplicates-place.test.js
cd tests/e2e && npx playwright test      # ALONE — contention flake #189
```

Plus per-task Chrome verification in BOTH themes with screenshots to the user, and
the schema-pinned `tests/test_duplicates_integration.py` staying green as the
no-cascade-change proof.

## Risks & mitigations

- **Grid state leaking across the swap** (IntersectionObserver shadow / occlusion
  snap after re-show): toggle `hidden` + body class only, never detach
  `#track-list`; call `ACBridge.renderTracks()` on deactivate; T6 e2e asserts
  sticky pinning after a swap round-trip. If the sticky shadow misbehaves on
  re-show, dispatch a synthetic `scroll` event after un-hiding — escalate before
  inventing anything bigger.
- **Drift-guard churn** (ids moving sections): all markup moves + json reconcile +
  palette rewire + `v2-global-layer.spec.ts` update land atomically in T3, with the
  full e2e suite as that task's gate.
- **Host-id coupling**: `scanDuplicates` & co. find their DOM by id; the move keeps
  every id except the deliberate `duplicates-scan-btn → wb-dupes-rescan` rename
  (3 call sites, source-contract-tested). Any missed reference fails loudly in the
  T3 Chrome pass (null deref on scan).
- **Worktree vs main drift**: this plan was verified partly against the mid-P2
  worktree and partly against `main` (post-c3dcff0). T1 starts with a quick
  re-verify of every cited line number on the fresh branch; numbers may shift a few
  lines — the symbols won't.
- **Restore-sheet/banner double-restore**: both POST the same backup; the second
  restore of an already-restored backup is idempotent server-side, but the sheet
  hides itself on the banner's success too (listen for the banner's removal is
  overkill — instead both paths toast and the 30 s expiry bounds the window;
  acceptable).
- **Opt-out flag (`ac_workbench='0'`) leaves no duplicates door** after the legacy
  section is removed: accepted — the palette command force-activates the workbench;
  the flag is a temporary escape hatch slated for removal.

## Rollback

Each task is an independent green merge on the feature branch; the branch lands as
one PR. Rollback = revert the PR merge commit: no backend files change, no schema,
no localStorage migrations (the only new key usage is reads of existing flags), and
the legacy `#duplicates-section` + ids are restored wholesale by the revert
(control-inventory.json reverts in the same commit, so the drift guard stays
consistent). Mid-branch, T3 is the only task that removes user-facing surface —
revert T3 alone restores the legacy section verbatim.

## Out of scope

- Detection algorithm / keeper ranking / cascade / backup-window / lock / SSE
  contract changes; any backend edit at all.
- Extending the H consent gradient (`_confirmDialog` + `_consentCanConfirm`,
  `07-helpers-events.js:145/:162`) to duplicates-delete — OPEN QUESTION, explicitly
  not done (faef4c4 decision stands).
- Retiring the inline undo banner (kept; program owner may revisit).
- Nightboard (P4), Discover restyle (P5), `AUTOCUE_LLM` (P6); XML/Pages rendering.
