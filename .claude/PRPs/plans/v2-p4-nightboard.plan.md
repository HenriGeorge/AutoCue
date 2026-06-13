# Feature: AutoCue 2.0 — P4 Nightboard (full-bleed set-builder canvas mode)

## Summary

Realise locked program decision #3 ("Full Nightboard"): a **full-bleed canvas
mode** the workbench swaps into, visualizing the analysis the engine **already
produces** — a horizontal set timeline of energy tiles, scored transition joints
with explanation popovers + swap alternatives, a set-wide energy arc, four zone
bands, and a gravity tray of ranked next-track candidates. **No new analysis math,
no new backend endpoint, no LLM.** Set construction stays in `setbuilder.py`;
scoring stays in `transitions.py`; Nightboard is presentation + orchestration over
the existing REST surface (`POST /api/setbuilder`, `POST /api/transitions/score`,
`GET /api/setbuilder/alternatives`, `GET /api/tracks/{id}/energy`,
`POST /api/playlists`).

Unlike the P3/P5 **places** (which swap only the centre pane), Nightboard is a
**mode**: it hides rail + grid + inspector and owns the body, keeping only the
global A-layer (sticky topbar: status sentence + ⌘K + ink-pill action dock). It is
entered by a **verb** ("Open Nightboard" toolbar button + ⌘K). Local-mode only,
behind the workbench gate. Additive — the legacy vertical `#setbuilder-section`
stays reachable until parity (rollout).

**Source PRD:** `.claude/PRPs/prds/v2-p4-nightboard.prd.md` (R1–R13).
**Pattern reference:** `v2-p5-discover-shell.plan.md` + shipped `discover.js`/`duplicates.js`.
**Visual source:** `docs/design/mockups/design-D.html` (mockup, not shippable — port its
visual language + interaction model to real data + tokens).
**Branch:** `feature/v2-p4-nightboard`, base `main` (`aff2538`, post-P3+P5 merge).

## Adopted decisions (resolve the PRD's open questions — do not reopen)

- **Mode vs place.** Nightboard is full-bleed. `#nb-canvas` renders in normal
  document flow below the sticky topbar; `body.nb-active` hides `#wb-rail`,
  `#wb-inspector`, and `#cues-tab-content` (the centre/grid) via a CSS backstop.
  The grid is **hidden, never detached** — `#track-list` stays mounted (TASK-033/037
  preserved). It is NOT an overlay on a live grid. The topbar (global A-layer) stays.
- **Joint thresholds (OQ1).** Ship the mockup's absolute cutoffs in v1 — **≥85 good
  / ≥70 ok / <70 bad** — as named constants in `canvas.js` (`JOINT_BANDS`). Document
  that the product's `overall` runs lower in practice (energy-missing caps), so real
  sets may read amber; calibration (possibly relative-to-set-distribution) is a
  fast-follow, NOT v1. Tunable in one place.
- **Initial joint scoring (OQ2).** Reuse `SetBuilderTrackItem.transition_score`
  (`schemas.py:428`, already returned by `/api/setbuilder`) for the initial paint —
  **zero** N−1 round-trips on build. Only call `/api/transitions/score` on
  swap/insert, and only for the ≤2 joints touching the changed slot (R7).
- **Energy-curve fetch (OQ3).** The arc + tile sparklines need every set track's
  energy. Fetch `GET /api/tracks/{id}/energy` for all set tracks in **parallel**
  (`Promise.allSettled`) on build; a failed/empty curve degrades to a flat segment
  (NaN-guarded, R5), never a broken path. Accept the N-call cost for v1 (sets are
  ~10–30, curves L2-cached); a bulk endpoint is a P-later optimization, not built.
- **Anchors vs seed (OQ4).** Grid-selection → `anchor_track_ids` (mirror
  `_useSelectedForSetBuilder` semantics via `ACBridge.selectedIds()`). `seed_track_id`
  is left null in v1 (no now-playing concept on the canvas yet). Documented.
- **Drag-to-reorder.** Out of scope v1. Click **Add** (tray) + **Swap** (popover)
  only. Full timeline drag (re-score all joints on drop) is a P-later open question.
- **`relaxed` honesty (OQ5).** Net-new: a small mono `relaxed` tag chip on any tile
  whose set dict carries `relaxed: true` (`setbuilder.py:288`). The mockup has no such
  element — add it (token-driven, muted).
- **Export.** "Export set + cues to Rekordbox" delegates to the **existing**
  create-playlist path (`#sb-save-playlist-btn` → `POST /api/playlists`) via a new
  `ACBridge.createSetPlaylist(name, trackIds)` pass-through. 409s honestly when
  Rekordbox runs (`routes.py:2942`); `r.ok` checked before typed reads. No new write path.
- **Legacy parity.** `#setbuilder-section` stays reachable in v1. The static "DJ
  Mixing Guide" prose (`index.html` ~:922+) and the legacy section are retired in a
  **later** parity pass, NOT this branch (keeps v1 additive + revertible).

## Key facts (verified against `main` / `aff2538`)

- **Analysis (reused as-is):** `build_set(...)` `setbuilder.py:101` → `{tracks[],
  terminated_reason}`; each track dict carries `track_id, title, artist, bpm, key,
  category, transition_score, mix_advice, relaxed`. `score_transition(a,b,db)`
  `transitions.py:272` → `{overall,bpm,key,energy,bpm_a,bpm_b,key_a,key_b,
  end_energy_a,start_energy_b,explanation}` (weights 0.40/0.35/0.25 `:311`,
  `explanation` 3-list `:313–317`). `transition_advice(ts)` `:194`.
- **REST (no backend change for render):** `POST /api/setbuilder` `routes.py:2785`
  (`SetBuilderRequest` `schemas.py:411`, `SetBuilderResponse` `:433`,
  `SetBuilderTrackItem` `:421`, `transition_score` `:428`); `POST
  /api/transitions/score` `routes.py:2760` (`TransitionResponse` `schemas.py:270`,
  incl. `explanation`); `GET /api/setbuilder/alternatives` `routes.py:2825`
  (`SetAlternativeItem` `schemas.py:515`: `…score, from_prev, to_next, genre,
  genre_match`; −20 genre penalty `routes.py:2896`); `GET /api/tracks/{id}/energy`
  `routes.py:2515`; `POST /api/playlists` `routes.py:2928` (`CreatePlaylistResponse`
  `schemas.py:509`; 409 `routes.py:2942`).
- **Bridge:** `window.ACBridge` `08-set-builder-boot.js:921`; exposes `tracks(),
  pending(), activeTracks(), selectedIds(), buildTrackCard(...), explainCue(),
  showTransitionScore(), crate(), isLocalMode(), renderTracks()`. `exportSet` /
  `anchorsFromSelection` / `createSetPlaylist` do NOT yet exist → T1 adds them.
  `_useSelectedForSetBuilder` `:23`, `_CAT_COLORS` `:7`, `_bpmToCategory` `:15`.
- **Shell/wiring:** `main.js` places wired at tail (`initDiscoverPlace()` `:55`) —
  Nightboard import appends after. `shell.js` `activate()` `:103`, `deactivate()`
  `:127`, `_renderCrates` place-aware `:41`, `autocue:wb-place-change` `:123`.
  `isWorkbenchOn()` `!== '0'` default-on `:22`.
- **Markup anchors:** `#wb-topbar-tools` `index.html:101` (workbench relocates tools
  here — the "Open Nightboard" verb mounts here, shown only `body.wb-active`).
  `#cues-tab-content` `:234`, `#track-list` `:442`, `#wb-rail` `:1528`,
  `#wb-inspector` `:1559`. `#setbuilder-section` `:874` (kept). New `#nb-canvas`
  appended as a top-level section sibling, `display:none` default.
- **Commands:** existing `build-set` `commands.js:48` (→ `_goto('library',
  'setbuilder-section')`) is **repurposed** to open Nightboard; add an explicit
  `open-nightboard` ("Open Nightboard", group "Go to"). Force-activate pattern as
  P5: `setWorkbench(true)` then open. CSS backstop precedent `app.css` place block.
- **Inspector reuse:** `inspector.js renderInspector(trackId)` `:29`,
  `clearInspector()` `:143` (resets `_mode`), `setInspectorMode()` — tile-focus calls
  these. Mode flag is `'track'|'release'`; Nightboard uses `'track'`.
- **Design-D port spec** (see commit body / research): canvas `min-width:1240px`,
  tile 186px / `min-width:168px`, joint 46px circle (`margin:0 -8px` floats on the
  wire), popover 330px (anchored under joint, clamped), arc `viewBox 0 0 1000 84`
  duration-weighted polyline (`fill var(--green-wash)`, `stroke var(--green)`,
  `vector-effect:non-scaling-stroke`), 4 zone washes (~5%), tray cards 236px.
  `--zone-warmup/-build/-peak/-closing` are **net-new tokens** (light+dark).
- **Invariants:** centre HIDDEN not detached; `#action-bar` `position:fixed`;
  document-scroll (TASK-033/037); no backend edits; e2e runs ALONE (#189); every new
  interactive id → `control-inventory.json` + `selectors-exist.spec.ts` (drift guard).

## Tasks (execute in order; each merges green on the three-leg stack)

### T1 — Legacy seams: ACBridge accessors (additive, no UI)
**Files:** `docs/js/08-set-builder-boot.js` (bridge `:921`), `tests/web/nightboard-interop.test.js` (new).
1. Append to `window.ACBridge` (all delegating, read/pass-through only):
   - `anchorsFromSelection: () => (ACBridge.selectedIds?.() || [])` — selection → anchor ids.
   - `createSetPlaylist(name, trackIds)` — delegates to the existing
     `#sb-save-playlist-btn` create-playlist path (POST `/api/playlists`); resolve the
     exact legacy fn during impl (pin its name), do NOT re-implement the write.
   - `scoreTransition(aId, bId)` — thin `fetch('/api/transitions/score', …)` helper
     **OR** leave the fetch in `set-model.js` (fetching REST is allowed; R11 bans only
     bare *state* reads). Decision: keep fetches in `set-model.js`; bridge only adds the
     two non-fetch accessors above. (Confirm during impl.)
2. Vitest source-contract (`nightboard-interop.test.js`): the new accessors exist in
   `loadAppHtml()` source; no signature change to existing bridge members.

**VALIDATE:** `npm test` (new test green; existing untouched); `pytest` untouched. No UI change.

### T2 — Mode skeleton + set-model: verb + ⌘K + inert `#nb-canvas` (build works, empty render)
**Files:** `docs/index.html`, `docs/js/v2/nightboard/mode.js` (new), `docs/js/v2/nightboard/set-model.js` (new),
`docs/js/v2/main.js`, `docs/css/app.css`, `commands.js`, `selectors-exist.spec.ts`, `control-inventory.json`,
`tests/web/nightboard-set-model.test.js` (new).
1. Markup: append top-level `<section id="nb-canvas" hidden>` (sibling of the tab
   contents) with the design-D skeleton shells: `#nb-topstrip` (set-name input
   `#nb-set-name`, stat chips `#nb-stats`, ink-pill `#nb-export-btn`), `#nb-build-bar`
   (start/end BPM `#nb-start-bpm`/`#nb-end-bpm`, duration `#nb-duration`, energy-mode
   `#nb-energy-mode`, `#nb-build-btn`), `#nb-zones`, `#nb-arc`, `#nb-timeline`,
   `#nb-tray`. Add the verb `<button id="nb-open-btn">` into `#wb-topbar-tools`.
2. `set-model.js` (pure state + fetch, Vitest-unit-testable): in-memory `SET` order +
   `POOL`; `buildSet(cfg)` POSTs `/api/setbuilder` mapping `{start_bpm,end_bpm,
   duration_minutes,energy_mode,anchor_track_ids}` from canvas inputs, parses
   `SetBuilderResponse`, surfaces `terminated_reason` (R3) as a notice string (not a
   throw); `swapAt(idx, track)` / `insertAfter(idx, track)` mutators;
   `rescoreJoints(idx)` POSTs `/api/transitions/score` for only the ≤2 affected joints
   (R7). `r.ok`-checked fetches; 422 → toast string, not crash.
3. `mode.js`: `initNightboard()` / `openNightboard()` / `closeNightboard()` /
   `isNightboardOpen()`. Open: guard `ACBridge.isLocalMode()`; `setWorkbench(true)`;
   `body.classList.add('nb-active')`; un-hide `#nb-canvas`; seed BPM/duration defaults;
   dispatch `autocue:nb-change`. Close: reverse; `ACBridge.renderTracks()`. Wire
   `#nb-open-btn` + `#nb-build-btn` (→ `set-model.buildSet` → `canvas.render`, stubbed
   in T2 to show count). Register `window.AC2.nightboard` in `main.js` (import after
   `:55`).
3. CSS (one banner): `body.nb-active #wb-rail, … #wb-inspector, … #cues-tab-content
   { display:none !important; }` and `#nb-canvas` full-width layout. Tokens only.
4. ⌘K: repurpose `build-set` `commands.js:48` → `openNightboard()` (via
   `setWorkbench(true)` + open); add `open-nightboard` ("Open Nightboard", group "Go
   to"). Remove the dead `_goto('library','setbuilder-section')`.
5. Guards: `nb-open-btn`, `nb-build-btn`, `nb-set-name`, `nb-start-bpm`, `nb-end-bpm`,
   `nb-duration`, `nb-energy-mode`, `nb-export-btn` → `control-inventory` (write-bearing
   export/build → `safeOnRealDb:false`); `#nb-canvas` + `#nb-open-btn` → `selectors-exist`.
6. Vitest (`nightboard-set-model.test.js`, jsdom + fetch stub): `buildSet` maps
   request fields + parses response; `terminated_reason` branches → notice (R3);
   `swapAt`/`insertAfter` mutate order; `rescoreJoints(idx)` touches only ≤2 joints (R7);
   open/close toggles `body.nb-active` + `#nb-canvas[hidden]`.

**VALIDATE:** `npm test`; `pytest`; `playwright test selectors-exist control-inventory` (alone).
Chrome `127.0.0.1:7433`: ⌘K "Open Nightboard" → canvas shows, rail/grid/inspector gone, topbar stays;
Build → tile count appears; close → grid returns + virtualizes. Both themes, screenshots.

### T3 — Canvas render: stats, zone bands, energy arc, timeline tiles + joints (R2/R4/R5/R6 paint)
**Files:** `docs/js/v2/nightboard/canvas.js` (new), `set-model.js` (energy fetch), `app.css` (`--zone-*` tokens + nb layout), `tests/web/nightboard-canvas.test.js` (new).
1. `canvas.js` — pure render from `set-model` state into `#nb-canvas` shells:
   - **Stats strip:** track count, total duration, BPM range, **avg mix score** (mono).
   - **Zone bands:** four washes proportioned by the fraction of set duration per
     `category` bucket (warmup/build/peak/closing); fractions sum to 1; degrade when a
     bucket is empty.
   - **Energy arc:** one SVG `viewBox="0 0 1000 84"` polyline stitching every track's
     energy curve, duration-weighted; `Promise.allSettled` fetch of
     `/api/tracks/{id}/energy` in `set-model`; NaN-guard → flat segment (R5);
     `fill var(--green-wash)`, `stroke var(--green)`, `vector-effect:non-scaling-stroke`.
   - **Timeline:** each track → fixed tile (title, artist, BPM green-wash mono chip,
     key chip, category chip, mini energy sparkline, clock + cue-status footer,
     `relaxed` tag if set). Cue-status from `ACBridge.pending()`. Joints between tiles
     show `transition_score` (mono /100) banded by `JOINT_BANDS` (≥85/≥70/else).
2. Tokens: add `--zone-warmup/-build/-peak/-closing` (light `:root` + `html.dark`) and
   `--nb-tile-height`/`--nb-joint-size`/`--nb-crate-card-width` under a single
   `/* Nightboard (P4) */` banner in `app.css`. No hardcoded hex.
3. Vitest (`nightboard-canvas.test.js`): zone fractions sum to 1 + category→bucket map;
   arc builder produces no `NaN` when a track lacks energy (R5); `JOINT_BANDS`
   thresholds map score→band (R6); tile renders mono BPM + relaxed tag when flagged (R4).

**VALIDATE:** `npm test`; Chrome both themes — build a real set, see tiles + joints + arc +
zones + stats; reduced-motion. Screenshots light + dark.

### T4 — Joint popover + in-place swap (R6/R7)
**Files:** `docs/js/v2/nightboard/joint-popover.js` (new), `canvas.js` (joint click wiring), `set-model.js`, `control-inventory.json`, `tests/web/nightboard-set-model.test.js`.
1. `joint-popover.js`: click a joint → popover (330px, anchored under the joint,
   clamped to canvas) showing the pair, `overall` score, the **three `explanation`
   strings verbatim**, the `transition_advice` tip, and **≤2 swap alternatives** from
   `GET /api/setbuilder/alternatives?track_id&prev_id&next_id&n=2`. Dismiss on
   outside-click + Escape (mirror discover scoping).
2. "Swap in": `set-model.swapAt(idx, alt)` → `rescoreJoints(idx)` (≤2 joints via
   `/api/transitions/score`) → `canvas` repaints the two tiles + joints + arc + zones +
   stats; new joint score shown; **no full rebuild** (R7).
3. Guards: joint buttons carry a stable `data-testid="nb-joint"`; swap pills
   `data-testid="nb-swap"` (class-keyed delegation; note in control-inventory).
4. Vitest: `rescoreJoints` touches only ≤2 joints; alternatives parse; popover band
   color matches score.

**VALIDATE:** `npm test`; Chrome — click joint → reasons + alternatives; Swap in →
tile + joint update, arc/stats shift, set not rebuilt. Both themes, screenshots.

### T5 — Gravity tray + tile-focus inspector (R8/R9)
**Files:** `docs/js/v2/nightboard/tray.js` (new), `canvas.js` (tile focus), `inspector.js` (reuse), `control-inventory.json`, `tests/web/nightboard-canvas.test.js`.
1. `tray.js`: sticky bottom shelf of ranked candidates for the **focused tile** (or
   last tile) from `/api/setbuilder/alternatives`; each card = title/artist + sim/score
   + "Add →" pill → `set-model.insertAfter(anchorIdx, cand)` + rescore affected joints +
   repaint. Collapsible (`#nb-tray-toggle`).
2. Tile-focus inspector: clicking a tile un-hides `#wb-inspector` and calls the
   **existing** `renderInspector(trackId)` (mode 'track') for in-context cue prep
   (phrase strip + A–H ticks, cue reasoning, energy, similar). Cue generation delegates
   to the existing preview/apply pipeline + H consent gate — **no new write path** (R9).
   Active tile gets the green ring; Escape clears focus.
3. Guards: `nb-tray-toggle`, `nb-tray-add` (`data-testid`) → control-inventory.
4. Vitest: tray builds candidate cards from alternatives; Add inserts after anchor.

**VALIDATE:** `npm test`; Chrome — tray lists candidates, Add inserts a tile; tile click
opens inspector with phrase strip; collapse works. Both themes, screenshots.

### T6 — Export + terminated-reason honesty + five-rules + both-themes audit (R3/R10/R12)
**Files:** `mode.js` (export), `set-model.js`, `app.css`, `canvas.js`, `.claude/project/web-ui.md`.
1. Export: `#nb-export-btn` (ink pill) → `ACBridge.createSetPlaylist(name, setIds)` →
   `POST /api/playlists`; `r.ok`-checked; 409 when Rekordbox running → honest toast (R10).
2. `terminated_reason` (R3): `safety_cap_hit`/`no_candidates_passed_thresholds` →
   visible non-error notice on the canvas (not a silent empty canvas); 422 → toast.
3. Five-rules + both-themes audit over every nb surface (green=signal only — ink-pill is
   the only CTA; mono-for-data; pills/radii; light&airy; `prefers-reduced-motion`
   disables canvas transitions, R12). Fix violations; tokens only. Dual screenshots.
4. Docs: `web-ui.md` "Nightboard" paragraph.

**VALIDATE:** `npm test`; Chrome both themes + reduced-motion (no transitions); export 409
path with Rekordbox open (or stubbed); screenshots.

### T7 — Tests + schema-pin + e2e + control-inventory + docs + final gate + PR (R11/R13)
**Files:** `tests/web/nightboard-interop.test.js`, `tests/test_nightboard_contract.py` (new pytest), `tests/e2e/v2-nightboard.spec.ts` (new), `control-inventory.json`, `selectors-exist.spec.ts`, `.claude/project/web-ui.md`, `CLAUDE.md`, program PRD parity note.
1. Interop sweep (R11): regex over `docs/js/v2/nightboard/*.js` finds no bare
   `parsedTracks`/`pendingCues`/`selectedTrackIds` outside `window.ACBridge`; asserts the
   T1 accessors exist.
2. Pytest schema-pin (`test_nightboard_contract.py`, style of
   `test_duplicates_integration.py`): assert `SetBuilderResponse`/`SetBuilderTrackItem`
   keep `transition_score`; `TransitionResponse` keeps `explanation`;
   `SetAlternativeItem` keeps `from_prev`/`to_next`/`genre_match`. A future field-drop
   fails loudly. (No new endpoint.)
3. e2e `v2-nightboard.spec.ts` (force `ac_workbench`; stub `/api/setbuilder` +
   `/api/setbuilder/alternatives` + `/api/transitions/score` + `/api/tracks/*/energy`,
   #189-safe): (a) open via verb → N tiles + (N−1) joints; (b) joint click → popover with
   reasons + alternatives; (c) Swap in → tile + joint score change; (d) tray Add →
   inserts tile; (e) tile click → inspector with phrase strip; (f) close → grid returns,
   `#action-bar` fixed + document scroll (TASK-037); (g) both themes screenshot. Canvas
   is NOT virtualized (no TASK-033 interaction).
4. Drift reconcile: all `nb-` ids in `control-inventory.json` + `selectors-exist`.
5. Docs: `CLAUDE.md` Nightboard paragraph + workbench-mode sentence; program PRD parity
   note (legacy `#setbuilder-section` retained). AI-asset commit with `Context:` section.
6. Final three-leg gate from root, e2e ALONE (#189): `pytest` → `npm test` →
   `playwright test`. Zero new e2e failures vs baseline. Open PR (base `main`):
   `feat(web): P4 Nightboard — full-bleed set canvas (energy arc, scored joints, swap, tray) (AutoCue 2.0)`.

**VALIDATE:** three legs green; new specs green both themes; existing setbuilder/transitions
pytest untouched-green (zero backend drift); screenshots; PR open.

## Full-suite validation (per merge AND final)
```bash
pytest                                   # setbuilder/transitions suites untouched + new schema-pin
npm test                                 # Vitest incl. nightboard-{set-model,canvas,interop}.test.js
cd tests/e2e && npx playwright test      # ALONE — #189; new v2-nightboard.spec.ts + reconciled inventory
```

## Risks & mitigations
- **JSDOM layout blind spot** — full-bleed swap, `#action-bar` fixed, document-scroll,
  grid-returns-and-virtualizes verifiable ONLY in Playwright; spec (a)/(f) mandatory.
- **Joint thresholds read mostly-amber on real libraries** — accepted for v1 (named
  constants); calibration is a documented fast-follow, not a blocker.
- **Energy-fetch volume** — `Promise.allSettled`, failures degrade to flat segments;
  curves L2-cached. Revisit with a bulk endpoint only if measured slow.
- **Mode vs place containing-block** — Nightboard hides the grid (never detaches);
  `#track-list` stays mounted; inspector reused in 'track' mode. T7 e2e asserts the grid
  returns and re-virtualizes.
- **Worktree vs main drift** — line numbers verified against `main` `aff2538`; T1
  re-verifies symbols on the branch.

## Rollback
Each task is an independent green merge; the branch lands as one PR. Rollback = revert the
merge commit: no backend/schema/localStorage migration (only reads of `ac_workbench`). The
legacy `#setbuilder-section` is untouched (additive); the repurposed `build-set` command
reverts in the same commit.

## Out of scope
- Any new analysis math or backend endpoint (presentation + orchestration only).
- Drag-to-reorder the timeline (click Add/Swap only in v1).
- Retiring `#setbuilder-section` or the static DJ Mixing Guide prose (later parity pass).
- A bulk `POST /api/transitions/score-batch` (P-later optimization).
- XML/Pages-mode rendering (program decision #5 — local-mode only).
- `AUTOCUE_LLM` composer (P6); now-playing `seed_track_id` UX.
