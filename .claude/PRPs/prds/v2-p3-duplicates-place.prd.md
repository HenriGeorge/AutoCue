# AutoCue 2.0 — P3: Duplicates as a place (phase PRD)

## Problem statement

Library duplicate-detection is a finished, well-tested capability — backend
grouping + keeper heuristic (`autocue/analysis/duplicates.py`), an SSE scan and a
safety-hardened cascade-delete (`autocue/serve/routes.py:1959`, `:2160`), and a
complete delete/undo UX (`docs/js/02-local-ops.js:322`–`:890`). But it lives
entirely inside the **Library tab**, in a `display:none` `<section
id="duplicates-section">` (`docs/index.html:562`) that the P2 workbench retires:
T9 of `v2-p2-workbench.plan.md` removes the Cues + Library tabs and makes the
three-pane workbench the local-mode home (program PRD locked decision #1, #5).
After P2 lands, Duplicates has **no door** in the new shell — the only ways in are
the legacy `#duplicates-scan-btn` (orphaned when its parent tab is gone) and the
⌘K palette text seam (`docs/index.html:1494` placeholder mentions "duplicates"
but no command is wired — `palette/commands.js` has no duplicates command).

The redesign's locked **maintenance grammar** (program PRD decision #2) names
Duplicates explicitly: *"Duplicates = rail place (center-pane view)"* — a place you
navigate to from the rail, not a section buried in a tab. P3 closes that gap by
re-surfacing the **existing logic, unchanged**, as a rail place that opens a
center-pane view, styled to the five design rules, with delete as a verb and
restore reachable as a sheet off the status sentence.

This phase changes **where** duplicate-detection is reached and **how** it looks —
**not** the algorithm, the keeper ranking, the 13-table cascade, the per-session
backup window, the concurrency lock, or the SSE contract. Those are preserved
byte-for-byte and re-driven, never re-implemented.

## Goals / Non-goals

**Goals**
- Add a **Duplicates rail place** that opens a **center-pane view** replacing the
  track grid (not a modal, not an inline-expand).
- Re-host the existing scan → group-list → per-group/bulk delete → undo flow in the
  workbench shell, styled to the five rules in both themes.
- Express **delete as a verb** (a destructive ink-pill in the view's toolbar / per
  group) and **restore as a sheet** reachable from the status-sentence A-layer.
- Reuse `GET /api/duplicates` and `POST /api/duplicates/delete` exactly; reuse the
  keeper-radio, same-path chips, confirm modal, undo banner, and `_onTracksDeleted`
  invalidation.
- Reach tab-retirement parity: once the place exists, the legacy
  `#duplicates-section` is removed from the Library tab path (which P2/T9 already
  retires), with the drift guard reconciled in both directions.

**Non-goals**
- **No new detection algorithm, key, keeper ranking, or cascade change.** Grouping
  stays `(artist, title, duration_bucket)`; keeper stays
  `(existing_hot_cues, play_count, last_played, bitrate, -track_id)`.
- **No backend route signature change.** `find_duplicates`, `duplicates_delete`,
  `DeleteRequest` (`schemas.py:171`) are untouched.
- **No new safety mechanism.** The 30 s backup window, per-row savepoint,
  concurrency `_duplicates_delete_inflight` lock, and `_rb_running` 409 are reused
  as-is.
- **No XML/Pages-mode rendering** — the shell is local-mode only (program decision
  #5).
- Not the Nightboard (P4), not the Discover restyle (P5), not `AUTOCUE_LLM` (P6).

## Alignment with locked program decisions

- **Decision #1 (Home = B workbench, rail / center / inspector):** Duplicates
  becomes a rail place whose selection swaps the **center pane** away from the track
  grid to the duplicate-group view. The right inspector is irrelevant in this view
  (a group has no single focused track) and is hidden while the place is active.
- **Decision #2 (maintenance grammar — places / verbs / sheets):** Duplicates is a
  **place** (rail → center pane). Delete is a **verb** (toolbar + per-group ink-pill,
  plus a ⌘K "Delete duplicates" command). Restore is a **sheet** off the
  status-sentence A-layer (the existing `/api/restore` undo, surfaced as the
  emergency exit rather than buried "in the Cues tab").
- **Decision #4 (multi-file, no build step):** all new code is native ES modules
  under `docs/js/v2/workbench/`, imported by `main.js`; CSS appended under the
  existing `/* ── v2: workbench ── */` banner in `docs/css/app.css`; tokens only.
- **Decision #5 (XML/Pages frozen):** the place renders only when
  `ACBridge.isLocalMode()` — same gate as the rest of the shell (`shell.js:92`).
- **Decision #7 (global A-layer):** restore hangs off the status sentence as a sheet;
  the place is also reachable via ⌘K.

## Current-state inventory (file:line refs to the logic being restyled)

**Backend — preserve exactly (no edits expected):**
- Grouping + keeper + projection dataclasses: `autocue/analysis/duplicates.py`
  — `normalize_key` (`:45`, `(artist, title, duration_bucket)`), `pick_keeper`
  (`:155`, the 5-key tuple), `find_duplicate_groups` (`:189`), `DuplicateGroup.to_dict`
  (`:108`, emits `is_keeper` / `same_path_as_keeper` / raw path columns per copy).
- Scan SSE: `autocue/serve/routes.py:1959` `GET /api/duplicates` — read-only, emits
  `{total}` → `{group}` per bucket → `{done, summary:{groups, surplus, scanned,
  skipped_empty}}`.
- Delete SSE: `autocue/serve/routes.py:2160` `POST /api/duplicates/delete` — 409 when
  `_rb_running` (`:2196`), per-session backup `_session_backup` (`:2124`, 30 s window
  `_DUPLICATES_BACKUP_WINDOW_S` `:2107`), concurrency lock `_duplicates_delete_inflight`
  (`:2121`, released in `finally` `:2310`), batched progress (`BATCH=25` `:2257`),
  cancel via disconnect poll (`:2224`); body schema `DeleteRequest` (`schemas.py:171`,
  `{track_ids:list[int], dry_run:bool=False}`).
- 13-table cascade lives in `db_writer.delete_tracks` (documented in CLAUDE.md
  "Library Duplicates"); **not touched by this phase.**

**Frontend — restyle / re-host:**
- Legacy markup to retire: `docs/index.html:562` `#duplicates-section`
  (`display:none`), confirm modal `#duplicates-confirm` (`:586`) + backdrop (`:585`),
  status label, progress, summary, empty, list (`:566`–`:579`).
- Scan handler `scanDuplicates` (`docs/js/02-local-ops.js:498`) — reads the SSE,
  appends groups via `_renderDuplicateGroup` (`:322`), builds the summary +
  `#duplicates-bulk-delete-btn` (`:570`).
- Per-group render with mutable keeper radio + `_refresh()` (`:380`) recomputing
  `nonKeeperIds` dataset, delete-button label, same-path chips on every flip
  (`:386`–`:413`).
- Confirm flow: `_openDuplicatesConfirm` (`:711`, 250 ms primary-disable, focus-trap
  `_duplicatesTrapHandler` `:681`, Cancel focused `:736`), `_runDuplicatesDelete`
  (`:755`, SSE + in-place progress fill, ESC-aborts), success undo banner
  `_showDuplicatesUndoToast` (`:844`, 30 s, POSTs `/api/restore`).
- Frontend invalidation `_onTracksDeleted` (`:633`) — surgical prune; called at
  `:483` (per-group) and `:587` (bulk).

**Workbench host primitives (the seams this phase plugs into):**
- Rail markup: `docs/index.html:1509` `#wb-rail` with `.wb-rail-section`
  Crates/Playlists/Saved + `#wb-rail-health` card (`:1525`).
- Rail crate predicates + active state: `shell.js:27` `CRATES`, `_renderCrates`
  (`:37`), `ACBridge.setCrate` / `crate()` (used `:41`,`:52`).
- Center pane today = the legacy `#tracks-sticky` + `#track-list` (path-(a)
  document scroll; `web-ui.md` TASK-037); right inspector `#wb-inspector`
  (`index.html:1527`), `renderInspector` (`inspector.js:29`), `clearInspector`
  (imported `shell.js:13`).
- Module entry: `docs/js/v2/main.js:28` imports `shell.js`; `window.AC2.workbench`
  surface (`:29`). Palette command registry: `docs/js/v2/palette/commands.js`
  (no duplicates command today).
- Rail reuse pattern to mirror: `rail.js` — every interactive surface drives a
  **legacy** path via `.click()` / native events, never re-implements
  (`rail.js:9`–`:27` header contract; `_renderHealth` "Fix it" → legacy button
  `:229`).

**Drift guards:** `tests/e2e/control-inventory.json` already lists
`duplicates-scan-btn` / `duplicates-confirm-cancel` / `duplicates-confirm-go` under
`library` (`:261`–`:272`); P3 moves/renames these and must reconcile the json in
both directions (T9-style).

## Proposed design

**A rail place (the door).** Add a fourth rail group below Saved filters — a single
**"Duplicates"** entry styled like a `.wb-crate` row but acting as a *place toggle*
rather than a grid predicate. Selecting it:
1. swaps the **center pane**: hide `#track-list` + `#tracks-sticky`, show a new
   `#wb-dupes-pane` host that occupies the same center column;
2. hides the right inspector (`#wb-inspector` → `hidden`) — a place about groups, not
   a focused track; `clearInspector()` first;
3. marks the rail entry `.active` (green = signal, rule 2) and de-activates crate
   highlights;
4. lazily kicks the existing scan on first open.

Re-selecting a crate / playlist (or an explicit "Back to library" affordance) swaps
the center pane back to the track grid and restores the inspector. This is a
**center-pane swap, not a route** — the document-scroll architecture (TASK-037) and
the Virtualizer call (`06-render.js`) are untouched because the grid is hidden, not
rewired.

**The center-pane view (restyled, same content).** `#wb-dupes-pane` renders, top to
bottom:
- a **toolbar** (pill controls, rule 4): a neutral "Rescan" pill, a mono summary
  sentence (`N groups · M surplus copies of K scanned`, rule 3 mono for the counts),
  and the **destructive ink-pill verb** "Delete all N non-keepers" (delete is a
  *verb*; the destructive tint uses a token, not a hardcoded `#e4384e` as the legacy
  inline style does at `02-local-ops.js:572`);
- a **group list** of cards at `--radius-xl` elevated-card radius, each: artist —
  title (sans), a mono `N copies` data chip (4px, rule 3+4), per-copy rows with the
  **"Keep" radio**, mono BPM/key/duration/path, the live **same-path chip**, and a
  per-group destructive **"Delete N non-keepers"** pill;
- empty state ("Library is clean") and an SSE progress line.

**Delete as a verb → confirm → restore as a sheet.** The toolbar / per-group delete
pills open the **existing confirm modal** (re-driven, same `_openDuplicatesConfirm`
guards: 250 ms primary-disable, focus-trap, Cancel-focused, ESC-aborts, in-place
progress, the H consent-gradient already grafted in P2/T8 for destructive cue-tools
applies — extend it to cover this delete so review-unlocks-delete is consistent).
On success the **undo affordance is promoted to a status-sentence sheet**: instead of
(or in addition to) the inline banner, the A-layer surfaces a "Restored?
N tracks deleted — Undo" sheet that POSTs `/api/restore` against the returned
`backup_path`, matching grammar #2 (restore = sheet off the status sentence).

**Both themes.** Light = cool-neutral surfaces, white group cards, ink-pill delete
with destructive token tint; dark = warm stone surfaces. The keeper row highlight
reuses the green-wash (`color-mix(... var(--green) 12% ...)` already at
`02-local-ops.js:390`) — green = active/selected signal, consistent with rule 2. All
borders/radii/shadows via tokens; no gradients.

## Requirements (numbered, testable)

- **R1 — Rail place.** A "Duplicates" rail entry (`#wb-dupes-place`,
  `kind:"button"`) renders in `#wb-rail` in local mode; clicking it activates the
  Duplicates place (center-pane swap) and sets `.active`. Vitest asserts render +
  active-state toggle; e2e asserts the click swaps the center pane.
- **R2 — Center-pane swap, grid untouched.** Activating the place hides
  `#track-list` + `#tracks-sticky` and shows `#wb-dupes-pane`; deactivating restores
  them. The Virtualizer attach call and `scrollSource:'window'` are byte-identical
  (no edits to `06-render.js`). Playwright asserts grid hidden ↔ pane shown and that
  `#action-bar` `position` still computes `fixed`.
- **R3 — Inspector hidden in-place.** On activation `clearInspector()` runs and
  `#wb-inspector` is `hidden`; on deactivation the inspector host is restored
  (re-shown empty). Vitest asserts the hidden toggle.
- **R4 — Scan reuse.** First activation (and "Rescan") consumes `GET /api/duplicates`
  via the existing SSE reader and renders groups; **no new endpoint**. A network test
  asserts the only scan request is to `/api/duplicates`.
- **R5 — Group view fidelity.** Each group shows artist/title, mono `N copies` chip,
  per-copy keeper radio, mono BPM/key/duration/path, and the live same-path chip;
  flipping the keeper radio recomputes non-keeper ids, the delete-pill label, and the
  same-path chips (reusing the `_refresh` logic). Vitest asserts the recompute on a
  fixture group.
- **R6 — Delete verb reuses the write path.** Per-group and bulk delete POST
  `/api/duplicates/delete` with `{track_ids, dry_run:false}` via the existing
  `_runDuplicatesDelete` (or a thin wrapper delegating to it) — the backup window,
  `_rb_running` 409, concurrency lock, per-row savepoint, and SSE progress are
  unchanged. A test asserts the request body shape and that no parallel
  re-implementation of the delete loop exists.
- **R7 — Confirm modal preserved.** Delete routes through the existing confirm modal
  with all guards (250 ms primary-disable, focus-trap, Cancel-focused, ESC-abort,
  in-place progress). E2e asserts the modal opens and Cancel/ESC paths. *Note:* the
  shipped H gradient is `_confirmDialog(message, {reviewRequired, evidence})` +
  `_consentCanConfirm` in `docs/js/07-helpers-events.js:145/:162` — a legacy
  classic-script helper reached from v2 via `window.*`, NOT a v2 module — and
  commit faef4c4 deliberately left the duplicates modal out of it ("left as-is: it
  already shows full per-copy evidence + 250 ms disable"). Extending the gradient
  to this delete is an open question (see below), not a requirement of this phase.
- **R8 — Restore as a sheet.** On delete success the undo is reachable from the
  status-sentence A-layer as a sheet that POSTs `/api/restore` with the returned
  `backup_path`; it expires on the existing 30 s window. Vitest asserts the sheet
  appears with the backup path and the restore POST body.
- **R9 — Invalidation reuse.** After a successful delete the frontend prunes via the
  existing `_onTracksDeleted(ids)` (no `/api/tracks` refetch); rail crate counts and
  the status sentence repaint via the existing `AppState.signal('tracks')` /
  `autocue:tracks-loaded` path.
- **R10 — Rekordbox-open safety surfaced.** When the delete 409s (`_rb_running`), the
  view shows the existing "close Rekordbox" toast (via the shared `r.ok` check) and
  no rows are deleted. e2e (mock) asserts the 409 toast path.
- **R11 — Five design rules, both themes.** Counts/BPM/key/duration/path are
  `--font-mono`; delete is an ink-pill (destructive token), not green; chips are 4px,
  group cards `--radius-xl`, controls pills; green only signals keeper/active; no
  hardcoded hex; `prefers-reduced-motion` honored. Manual both-theme audit +
  screenshots.
- **R12 — ⌘K command.** A palette command "Go to Duplicates" activates the place
  (delegates to the rail entry `.click()`); the existing palette placeholder text
  (`index.html:1494`) is satisfied by a real command. Vitest asserts the command
  resolves.
- **R13 — Parity retirement.** The legacy `#duplicates-section` scan path is removed
  from the (already-retired) Library tab; its ids are reconciled in
  `control-inventory.json` (removed where superseded, new ids added). The confirm
  modal markup may be **kept and re-parented** (re-used by the place) — if kept, its
  ids stay in the inventory; if recreated under v2, the old ids are removed and new
  ones added.

## Architecture & interop

**New ES module:** `docs/js/v2/workbench/duplicates.js`, imported by `main.js`
alongside the other workbench modules; exposed on `window.AC2.duplicates`. It owns
the place: `activate()` (center-pane swap + lazy scan), `deactivate()` (restore
grid + inspector), `renderGroups(groups)`, and the toolbar/summary builders. It
**delegates** every write to the legacy functions rather than re-implementing them.

**ACBridge additions (read-only pass-throughs, appended to the bridge block —
extend, never poke internals):**
- `scanDuplicates()` → legacy `scanDuplicates` (`02-local-ops.js:498`),
- `openDuplicatesConfirm(opts)` → `_openDuplicatesConfirm` (`:711`),
- `onTracksDeleted(ids)` → `_onTracksDeleted` (`:633`).

This mirrors the established contract: v2 reads/drives legacy **only** via
`window.ACBridge` and native DOM events / `.click()` (the rail-module rule,
`rail.js:9`–`:27`); legacy never imports v2; no bare globals in v2 modules.
The confirm modal markup (`#duplicates-confirm`) is shared DOM — the v2 place reuses
it by id (or it is moved into the shell). **No backend code changes.** No build step;
CSS under the existing v2 banner in `docs/css/app.css`, tokens only.

**Center-pane host.** Add `#wb-dupes-pane` to `docs/index.html` inside the workbench
center column, `hidden` by default. The swap is `hidden`-attribute toggling +
`document.body.classList` (e.g. `wb-place-dupes`) so CSS hides the grid — the grid is
never detached, preserving the Virtualizer/sticky invariants (no DOM moves of
`#track-list`).

## Test plan

**pytest (no new backend logic — guard against accidental drift):**
- Assert `GET /api/duplicates` SSE shape unchanged (existing `TestDuplicatesEndpoint`
  still green).
- Assert `POST /api/duplicates/delete` body + safety contract unchanged (existing
  `TestDuplicatesDeleteEndpoint` + `tests/test_duplicates_integration.py` 13-table
  cascade still green — schema-pinned test must stay green, proving no cascade
  change).

**Vitest — new `tests/web/v2-duplicates-place.test.js` (ES module import):**
- rail entry renders + place toggles active (R1);
- center-pane swap hides grid / shows pane; inspector hidden (R2, R3);
- group-render fidelity + keeper-radio recompute (`nonKeeperIds`, label, same-path
  chip) on a fixture group (R5);
- delete delegates to `_openDuplicatesConfirm` / `_runDuplicatesDelete` with body
  `{track_ids, dry_run:false}` (R6) — assert via stubbed fetch;
- consent-gate state machine: delete disabled until reviewed (R7);
- restore sheet body POSTs `/api/restore` with `backup_path` (R8);
- ⌘K command resolves to the place activation (R12).
- Source-contract sweep (via `loadAppHtml()` / regex): v2 module has **no bare**
  `parsedTracks`/`scanDuplicates` outside `ACBridge`.

**Playwright — extend e2e (JSDOM can't see layout — jsdom-layout-blind-spot rule):**
- new `tests/e2e/v2-duplicates-place.spec.ts`: with the workbench flag on, click the
  rail Duplicates entry → `#wb-dupes-pane` visible, `#track-list` not visible,
  `#action-bar` `position:fixed` (R2); confirm modal opens on delete and Cancel/ESC
  close it (R7); both themes screenshot.
- `selectors-exist.spec.ts`: add `#wb-dupes-place`, `#wb-dupes-pane`,
  `#wb-dupes-rescan`, `#wb-dupes-bulk-delete`.

**New control-inventory ids** (`tests/e2e/control-inventory.json`, `globalControls`
since the workbench is global-mode chrome; `safeOnRealDb:false` on every destructive
verb):
- `wb-dupes-place` (button), `wb-dupes-rescan` (button),
- `wb-dupes-bulk-delete` (button, `safeOnRealDb:false`),
- per-group delete buttons are dynamic (no fixed id) → spec ignore-list, as
  `pal-opt-N` is handled today;
- reconcile the existing `duplicates-*` confirm ids (kept if the modal is reused;
  moved from `library` → `globalControls` once the Library tab retires).

## Rollout & parity

P3 is **gated by the same workbench flag** as the rest of the shell
(`shell.js` `ac_workbench`; program-locked to default-on in local mode by P2/T9).
The place is **additive** — until it lands, Duplicates is still reachable via the
legacy `#duplicates-section` (which P2/T9 retires). The phase reaches parity when:
the rail place + center-pane view fully reproduce the scan → keeper-override →
delete → undo flow; the legacy section is removed from the active path; the drift
guard reconciles in both directions; three legs green (`pytest` · `npm test` ·
Playwright e2e) with zero new e2e failures vs the known baseline. No backend rollout
risk — the routes are unchanged. Update `.claude/project/web-ui.md` (Duplicates place:
center-pane swap, delegation contract) and the CLAUDE.md track record; commit
AI-asset changes with a `Context:` section.

## Open questions & risks

- ~~Workbench default state~~ **RESOLVED (2026-06-12, main c3dcff0)**: the
  workbench is **default-on** in local mode (`isWorkbenchOn()` reads `!== '0'`,
  opt-out `'0'`). P3's assumption holds; no flag gymnastics needed.
- **Confirm modal ownership.** The `#duplicates-confirm` markup lives near the legacy
  section (`index.html:585`). Decision: **re-parent and reuse** (lowest risk, keeps
  every guard) vs recreate under the v2 shell. *Proposal: re-parent/reuse;* recreate
  only if the retirement removes its DOM neighborhood.
- **Restore-as-sheet vs the existing inline undo banner.** The legacy undo is an
  inline banner (`_showDuplicatesUndoToast` `:844`). Grammar #2 says restore = a sheet
  off the status sentence. *Proposal: promote to a status-sentence sheet AND keep an
  in-view banner;* the A-layer sheet is the canonical surface, the in-view banner is a
  convenience. Confirm with the program owner whether the inline banner is retired.
- **H consent gradient scope.** P2/T8 grafted review-unlocks-apply onto destructive
  cue-tools via `_confirmDialog(message, {reviewRequired, evidence})` +
  `_consentCanConfirm` (`docs/js/07-helpers-events.js:145/:162`), and faef4c4
  deliberately left the duplicates modal out. Extending it to duplicate-delete is a
  *proposal* that would supersede that decision — confirm whether the
  `reviewRequired` opt-in is appropriate for a non-cue destructive op, or whether
  the existing 250 ms-disable + per-copy-evidence modal already satisfies the
  consent requirement.
- **Risk — center-pane swap leaking grid state.** Hiding `#track-list` (not
  detaching) must not break the IntersectionObserver shadow / occlusion snap when the
  grid re-shows. *Mitigation:* toggle `hidden` only, never move the node; e2e asserts
  the sticky bar still pins after swap-back.
- **Risk — drift-guard churn.** Moving `duplicates-*` ids between inventory sections
  can fail the reconcile in both directions. *Mitigation:* do the inventory move and
  the markup move in one task, with the spec run as the gate.

## Success metrics

- Duplicates is a **rail place opening a center-pane view** (not a tab section); the
  full scan → keeper-override → delete → undo flow works end-to-end through the
  **existing** backend + write path (no new endpoint, no cascade change — the
  schema-pinned integration test stays green).
- The five design rules hold on the new view in **both themes** (mono data, ink-pill
  destructive verb, pill controls, 4px chips, `--radius-xl` cards, green=signal,
  no hardcoded hex), verified by screenshots.
- Delete is a **verb** (toolbar + ⌘K), restore is a **sheet** off the status
  sentence — grammar #2 satisfied.
- Three-leg stack green (`pytest` · `npm test` · Playwright e2e), zero new e2e
  failures vs baseline; every new interactive id in `control-inventory.json`;
  Lighthouse not worse than the pre-P3 baseline.
