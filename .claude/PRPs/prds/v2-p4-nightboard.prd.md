# AutoCue 2.0 — P4: Nightboard (phase PRD)

## Problem statement

AutoCue already builds DJ sets and scores transitions, but the set-builder UI is a
**vertical form** buried in a panel: `#setbuilder-section` in `docs/index.html:852`
(Start/End BPM + duration + energy-mode inputs at `index.html:857–877`, a "Build Set"
button at `index.html:880`, a flat result list rendered into `#sb-tracklist`
`index.html:897`). The transition scorer (`score_transition`,
`autocue/analysis/transitions.py:272`) already returns rich, human-readable reasons
(`explanation`, `transitions.py:313–317`) and the backend already serves swap
candidates per slot (`GET /api/setbuilder/alternatives`, `routes.py:2825`), but the
current UI throws almost all of that away: it shows a list of titles + a static
"DJ Mixing Guide" of generic prose (`index.html:900–970`). The DJ cannot **see** the
set's energy arc, cannot **see** how strong each joint is, cannot click a weak joint
to understand *why* it's weak, and cannot audition the already-computed swap
alternatives in place.

The locked program decision #3 ("Full Nightboard") commits to a real canvas mode
that visualizes the analysis the engine already produces. This PRD specifies that
mode: a horizontal set timeline of energy tiles, scored transition joints with
explanation popovers + swap alternatives, a gravity tray of ranked next-track
candidates, zone bands, and a set-wide energy arc — built on the existing
`setbuilder.py` / `transitions.py` analysis and its REST surface, **not** new
analysis.

## Goals / Non-goals

**Goals**
- Ship Nightboard as a **mode** of the workbench (a full-bleed canvas the workbench
  swaps to), additive — not a retired tab, not a new top-level app.
- Visualize what the engine already returns: per-track energy curve, per-joint
  transition score + reasons, swap alternatives, the set-wide arc, zone bands.
- Make every joint inspectable (popover with the real `explanation` strings) and
  every swap actionable (re-score the joint after a swap).
- Reuse the existing inspector builders for in-context cue prep when a tile is
  focused — no second cue engine.
- Both themes; the five design rules; the three-leg merge gate; control-inventory parity.

**Non-goals**
- No new analysis math. BPM/key/energy scoring stays in `transitions.py`; set
  construction stays in `setbuilder.py`. Nightboard is presentation + orchestration.
- No drag-and-drop reorder of the whole set in v1 (the mockup hints at draggable
  crate cards; v1 ships **Add** + **Swap** click affordances, drag is an open
  question — see below).
- No write to Rekordbox from the canvas beyond the **existing** apply/export and
  create-playlist paths; Nightboard composes a set and hands it to those paths.
- No LLM. The popover reasons are the deterministic `explanation` list, exactly as
  the rest of the app (program decision #6 forbids LLM before P6).
- Not retiring anything. The vertical `#setbuilder-section` may remain reachable
  until Nightboard reaches parity (rollout section).

## Alignment with locked program decisions

- **#3 Full Nightboard** — this PRD *is* the realization of decision #3. The scope
  (horizontal timeline, energy-curve tiles, scored joints with explanation popovers +
  swap alternatives, gravity tray, zone bands, set-wide arc) is copied verbatim from
  the locked text (program PRD lines 23–25).
- **#2 Maintenance grammar** — Nightboard is a **mode** (per decision #3's "ships
  as a real mode"), a grammar category of its own: a full-bleed canvas that
  temporarily replaces rail + grid + inspector, distinct from the rail-*place*
  pattern P3/P5 share (which swaps only the center pane). It is entered via a
  *verb* ("Open Nightboard" / "Build a set" in the grid toolbar + ⌘K); building a
  set and swapping a track are *operations* (verbs); export/apply stays a
  sheet/dock action off the existing pipeline.
- **#4 Multi-file, no build step** — all new code is native ES modules under
  `docs/js/v2/nightboard/`, imported by `docs/js/v2/main.js` (the only module entry,
  `main.js:13`). No bundler, no framework. CSS appends under one banner in
  `docs/css/app.css`.
- **#5 XML/Pages frozen** — Nightboard needs `/api/setbuilder`, `/api/transitions/score`
  and `/api/setbuilder/alternatives`, all server-only. The mode renders in **local
  mode only**, gated the same way the workbench is (program decision #5).
- **#7 Global A-layer** — the canvas keeps the clickable status sentence + ⌘K + the
  single ink-pill action dock (Export/Apply). "Open Nightboard" is a ⌘K command.

## Current-state inventory (file:line refs to logic Nightboard builds on)

**Analysis — set construction (existing, reused as-is)**
- `build_set(db, start_bpm, end_bpm, duration_minutes, energy_mode, bpm_step_max,
  seed_track_id, anchor_track_ids)` — `autocue/analysis/setbuilder.py:101`. Returns
  `{"tracks": [...], "terminated_reason": str}` (`setbuilder.py:114–120`,
  `setbuilder.py:328–344`). Each track dict carries `track_id, title, artist, bpm,
  key, category, transition_score, mix_advice, relaxed` (`setbuilder.py:329–342`).
  Beam search, no O(n²) graph; relaxation ladder `setbuilder.py:397`; anchors merged
  by `_merge_anchors` `setbuilder.py:352`.

**Analysis — transition scoring (existing, reused as-is)**
- `score_transition(content_a, content_b, db)` — `autocue/analysis/transitions.py:272`.
  Returns `{overall, bpm, key, energy, bpm_a, bpm_b, key_a, key_b, end_energy_a,
  start_energy_b, explanation}` (`transitions.py:319–331`). Weights 0.40 BPM / 0.35
  key / 0.25 energy (`transitions.py:311`). `explanation` is a 3-element list of
  human strings (`transitions.py:313–317`) — this is the popover body, no LLM.
- `transition_advice(ts)` — `transitions.py:194` — single-sentence mixing tip from a
  scored dict (the popover footer tip).

**REST surface (existing — no backend change required for v1)**
- `POST /api/setbuilder` → `SetBuilderResponse` — `routes.py:2785`. Request schema
  `SetBuilderRequest` (`schemas.py:411`: `start_bpm=110, end_bpm=135,
  duration_minutes=60, energy_mode∈{build,flat,drop}, bpm_step_max=0.08,
  seed_track_id, anchor_track_ids=[]`). Response `SetBuilderResponse` (`schemas.py:433`:
  `tracks[SetBuilderTrackItem], total_tracks, estimated_duration_minutes,
  terminated_reason∈{target_duration_reached, no_candidates_passed_thresholds,
  safety_cap_hit}`). `SetBuilderTrackItem` `schemas.py:421`.
- `POST /api/transitions/score` → `TransitionResponse` — `routes.py:2760` /
  `schemas.py:270` (mirrors `score_transition` plus `track_a_id/track_b_id`). Used to
  re-score a single joint after an in-place swap without rebuilding the whole set.
- `GET /api/setbuilder/alternatives?track_id&prev_id&next_id&exclude_ids&n` →
  `SetAlternativesResponse` — `routes.py:2825` / `schemas.py:528`. Each
  `SetAlternativeItem` (`schemas.py:515`): `track_id, title, artist, bpm, key, score,
  from_prev, to_next, genre, genre_match`. This is the swap-alternatives + gravity-tray
  data source (scored on fit to both neighbours, −20 genre-mismatch penalty
  `routes.py:2896`).
- `POST /api/playlists` → `CreatePlaylistResponse` — `routes.py:2928` /
  `schemas.py:504`. 409s when Rekordbox is running (`routes.py:2941`). The "save set as
  Rekordbox playlist" path (already wired to `#sb-save-playlist-btn`, `index.html:895`).

**Existing set-builder UI (the thing Nightboard supersedes, kept for parity)**
- `#setbuilder-section` `index.html:852` (display:none until local mode). Inputs
  `#sb-start-bpm/#sb-end-bpm/#sb-duration/#sb-energy-mode` `index.html:860–876`;
  `#sb-build-btn` `index.html:880`; `#sb-use-selected-btn` (anchors) `index.html:881`;
  result into `#sb-tracklist` `index.html:897`; `#sb-save-playlist-btn`
  `index.html:895`; `#sb-copy-btn` `index.html:894`.
- Boot/logic: `docs/js/08-set-builder-boot.js` (`_useSelectedForSetBuilder`
  `08-set-builder-boot.js:23`, category colors `_CAT_COLORS` `08-set-builder-boot.js:7`,
  `_bpmToCategory` `08-set-builder-boot.js:15`). Already in control-inventory (9 `sb-`
  ids).

**Inspector builders (reused for tile-focus cue prep — already exposed)**
- `window.ACBridge` (`08-set-builder-boot.js:919`) exposes `tracks()`, `pending()`,
  `activeTracks()`, `buildTrackCard(...)`, `explainCue(cue)`, `showTransitionScore()`
  and the crate setter. The six P2 inspector builders (energy sparkline, mixability,
  classification, similar, phrase strip + A–H ticks, cue reasoning) are re-hosted by
  `docs/js/v2/workbench/inspector.js` — Nightboard's tile-focus inspector calls the
  same module so cue prep is one implementation.

**Shell / flag plumbing (the mode hangs off this)**
- `docs/js/v2/main.js:28` imports `initWorkbench/toggleWorkbench/isWorkbenchOn/
  setWorkbench` from `workbench/shell.js`. The workbench flag is `localStorage.ac_workbench`
  (`shell.js:16`; **default-on** in local mode with explicit opt-out `'0'` — `!== '0'`,
  shipped to main in c3dcff0). Nightboard is a sub-mode the workbench enters; it must NOT
  break the path-(a) document-scroll / sticky / Virtualizer invariants of the grid
  (CLAUDE.md TASK-033/TASK-037) — it is a **separate full-bleed view**, not an overlay
  on the grid.

**Canonical interaction source (design-D, vendored in-repo)**
- `docs/design/mockups/design-D.html` — the working
  concept. Token block `design-D.html:14–45` (incl. net-new zone washes
  `--zone-warmup/-build/-peak/-closing`, line 28–30 / 43–44). Canvas + zones + arc
  + timeline + joints + popover + inspector + tray + toast are all there
  (`design-D.html:93–315`), with the same scoring voice as the product
  (`scoreTransition` `design-D.html:383`). **This is a mockup, not shippable code** —
  it uses mock `SET`/`POOL` arrays and inline styles; Nightboard ports its *visual
  language and interaction model* to real data + tokens.

## Proposed design

Nightboard is a **full-bleed canvas mode** the workbench swaps into (the grid + rail
+ inspector recede; the canvas owns the screen). Entered via a *verb*
("Open Nightboard" in the grid toolbar + a ⌘K command). It is local-mode only.

**Layout (ported from design-D, both themes):**

1. **Top strip** — set name (editable inline), live stat chips (track count, total
   duration, BPM range, **avg mix score** in mono), and the single ink-pill action
   ("Export set + cues to Rekordbox" → existing apply/playlist path). Mirrors
   `design-D.html:270–285` but uses the real status-sentence + action-dock chrome.
2. **Zone bands** — four background washes (warmup/build/peak/closing) sized by the
   fraction of set duration in each `category` bucket, using net-new `--zone-*`
   tokens (~5% tints, design-D:28–30/43–44). Pure data-signal, behind the arc/tiles.
3. **Set-wide energy arc** — one SVG path stitching every track's energy curve into a
   continuous line across the set width, weighted by track duration
   (design-D:434–447). Data: per-track energy curves via the existing
   `GET /api/tracks/{id}/energy` (the same source the P2 sparkline uses).
4. **Timeline of tiles + joints** — each track is a fixed-size **tile** (title,
   artist, BPM chip in green-wash mono, key chip, category chip, a mini energy
   sparkline, clock + cue-status footer; design-D:120–137). Between tiles sit circular
   **joints** showing the transition `overall` score (mono, /100) colored by band
   (good/ok/bad, design-D:140–152). Joint color thresholds are presentation-only
   (proposal: ≥85 green, ≥70 amber, else danger — tunable, see open questions).
5. **Joint popover** — click a joint → a popover anchored to it (design-D:154–182,
   496–537) showing the pair, the `overall` score, the three `explanation` reasons
   (from `score_transition`), and **2 swap alternatives** (from
   `/api/setbuilder/alternatives` with `prev_id`/`next_id` set to the joint's
   neighbours). Each alternative has a "Swap in" pill → replaces the incoming track,
   re-scores only the two affected joints via `/api/transitions/score`, repaints.
   Footer tip = `transition_advice`.
6. **Gravity tray** — sticky bottom shelf of ranked "what mixes out of the focused
   tile" candidate cards (design-D:221–250, 604–633), each with sim/score + an
   "Add →" pill that inserts after the anchor. Source: `/api/setbuilder/alternatives`
   for the focused tile (or the last tile when none focused).
7. **Tile-focus inspector** — clicking a tile opens the **existing** P2 inspector
   (`workbench/inspector.js`) for in-context cue prep (phrase strip + A–H ticks, cue
   reasoning, energy curve, similar). No second cue engine; the "Generate/Re-place
   cues" action delegates to the existing preview/apply pipeline.

**Maintenance-grammar placement:** the canvas is a **mode** (`#nb-canvas`), entered
by a **verb** ("Open Nightboard" toolbar button + ⌘K). Build/Swap/Add are **verbs**.
Export is the dock action (a sheet/confirm if it overwrites cues, reusing the
shipped H consent gate: `_confirmDialog(message, {reviewRequired, evidence})` +
`_consentCanConfirm` in `docs/js/07-helpers-events.js:145/:162` — a legacy helper
driven from v2 via `window.*` per the interop contract, not a v2 module).

**Design-rule conformance:** green = signal only (active tile ring, joint-good,
arc/sparkline stroke, success toast — never a CTA; the only CTA is the ink pill,
design-D:86–91). Every measured value (BPM, key, score, time, duration) is
`--font-mono`. Pills 999px; tiles 12px; canvas/inspector cards 16px (`--radius-xl`);
chips 4px. Zone/green washes are token-driven, no hardcoded hex. `prefers-reduced-motion`
honored (design-D:262–264). Both themes verified.

## Requirements (numbered, testable)

- **R1** — A "Open Nightboard" verb exists in the grid toolbar **and** as a ⌘K
  command; both reveal `#nb-canvas` and hide the grid/rail/inspector (workbench
  recedes). Verb is local-mode only (hidden/disabled in XML/Pages mode).
- **R2** — Building a set from Nightboard calls `POST /api/setbuilder` with the
  canvas's start/end BPM, duration, energy-mode, and optional anchors; the response's
  `tracks[]` render as tiles left-to-right in returned order.
- **R3** — `terminated_reason` is surfaced honestly: `safety_cap_hit` /
  `no_candidates_passed_thresholds` produce a visible non-error notice (not a silent
  empty canvas); a 422 from the endpoint (`routes.py:2805`) shows a toast, not a crash.
- **R4** — Each tile renders, from the set track dict: title, artist, BPM (mono,
  green-wash chip), key, category, a mini energy sparkline, and a cue-status footer
  (cues placed ✓ vs "no cues"). `relaxed` tracks carry a visible marker.
- **R5** — The set-wide arc and the four zone bands render from real per-track data
  (energy curves + `category`), proportioned by track duration; bands degrade
  gracefully when a track has no energy data (no NaN paths).
- **R6** — Each joint shows the `overall` transition score (mono /100) and a band
  color; clicking it opens a popover with the pair, the score, the three
  `explanation` strings verbatim from `score_transition`, the `transition_advice`
  tip, and ≤2 swap alternatives from `/api/setbuilder/alternatives`.
- **R7** — "Swap in" replaces the incoming track, re-scores **only** the (≤2) joints
  touching the swapped slot via `/api/transitions/score`, updates the arc/zones/stats,
  and shows the new joint score; it does NOT rebuild the whole set.
- **R8** — The gravity tray lists ranked candidates for the focused tile (or last
  tile) from `/api/setbuilder/alternatives`; "Add →" inserts the candidate after the
  anchor and re-scores affected joints. Tray is collapsible.
- **R9** — Clicking a tile opens the existing P2 inspector for that track (cue prep in
  context) via `workbench/inspector.js`; cue generation delegates to the existing
  preview/apply pipeline and the H consent gate — no new write path.
- **R10** — "Export set + cues to Rekordbox" delegates to the existing apply/
  create-playlist pipeline; it 409s honestly when Rekordbox is running (the endpoints
  already do, `routes.py:2941`); the response `r.ok` is checked before reading typed
  fields (CLAUDE.md JS fetch rule).
- **R11** — No bare `parsedTracks`/`pendingCues`/`selectedTrackIds` reads in
  `docs/js/v2/nightboard/*` — only via `window.ACBridge` (interop contract).
- **R12** — Both themes render correctly; five design rules hold (green=signal,
  ink-pill CTA, mono-for-data, pills/radii scale, light&airy); `prefers-reduced-motion`
  disables canvas transitions.
- **R13** — Every new interactive id is registered in
  `tests/e2e/control-inventory.json` (write-bearing → `safeOnRealDb:false`) and
  `selectors-exist.spec.ts`; the drift guard stays green.

## Architecture & interop

**Modules (all new, ES modules under `docs/js/v2/nightboard/`):**
- `nightboard/mode.js` — `initNightboard()` / `openNightboard()` / `closeNightboard()`;
  registers the toolbar verb + ⌘K command; owns show/hide of `#nb-canvas`. Imported by
  `main.js` (one new import line; mirrors the `workbench/shell.js` import at `main.js:28`).
- `nightboard/set-model.js` — the in-memory set state (`SET` order, `POOL` of
  candidates), `buildSet()` (POSTs `/api/setbuilder`), `swapAt()/insertAfter()`
  mutators, and the `rescoreJoints(idx)` helper (POSTs `/api/transitions/score`). Pure
  state + fetch; unit-testable in Vitest.
- `nightboard/canvas.js` — render: zones, arc SVG, timeline (tiles + joints), stats
  strip. Pure render from `set-model` state.
- `nightboard/joint-popover.js` — popover build + positioning + swap wiring (fetches
  `/api/setbuilder/alternatives`).
- `nightboard/tray.js` — gravity tray render + Add wiring.
- Tile-focus inspector reuses `workbench/inspector.js` (no copy).

**Interop (window.* contract):**
- **Reads** legacy state ONLY via `window.ACBridge` (`08-set-builder-boot.js:919`):
  `tracks()` for title/artist/BPM lookups, `selectedIds()` for seeding anchors from a
  grid selection, `pending()` for cue-status on tiles, `activePlaylistId()`.
- **Exposes** its surface via `window.AC2.nightboard = { openNightboard,
  closeNightboard, isOpen, buildSet, ... }` (mirrors `main.js:22/25/29`).
- **New ACBridge accessors needed** (append to the bridge block,
  `08-set-builder-boot.js:919`): a fn pass-through to the existing apply/export path
  so Export delegates without re-implementing the write (e.g. `exportSet(trackIds)`
  → the same path `#sb-save-playlist-btn` uses), and `anchorsFromSelection()` reusing
  `_useSelectedForSetBuilder` semantics (`08-set-builder-boot.js:23`). Legacy never
  imports v2.
- **No build step**: all files are `<script type="module">` reachable from `main.js`;
  `python -m http.server` + FastAPI StaticFiles keep serving (program decision #4).

**No backend change for v1.** All three endpoints + create-playlist already exist
and return the shapes Nightboard needs. (Open question: a future bulk
`POST /api/transitions/score-batch` to score all joints in one round-trip — v1 scores
joints client-side-sequentially or reuses the scores already on `SetBuilderTrackItem.
transition_score`, `schemas.py:428`, for the initial render and only calls
`/api/transitions/score` on swap.)

## Test plan

**Vitest (`tests/web/`, ES-module imports of the pure parts):**
- `nightboard-set-model.test.js` — `swapAt`/`insertAfter` mutate order correctly;
  `buildSet` maps `SetBuilderRequest` fields from canvas inputs and parses
  `SetBuilderResponse`; `terminated_reason` branches produce the right notice (R3);
  `rescoreJoints(idx)` touches only the ≤2 affected joints (R7).
- `nightboard-canvas.test.js` — zone-band fractions sum to 1 and map categories to
  the four buckets; arc path builder produces no `NaN` when a track lacks energy data
  (R5); joint band-color thresholds (R6).
- `nightboard-interop.test.js` — regex sweep of `docs/js/v2/nightboard/*.js` finds no
  bare `parsedTracks`/`pendingCues`/`selectedTrackIds` outside `window.ACBridge`
  (R11); asserts the new ACBridge accessors exist in source via `loadAppHtml()`.

**Pytest (backend contract — guards the shapes Nightboard depends on):**
- Assert `SetBuilderResponse`/`SetAlternativesResponse`/`TransitionResponse` keep the
  fields Nightboard reads (a schema-pin test in the style of the existing
  `test_duplicates_integration.py` schema pin) so a future backend refactor that drops
  `explanation` or `from_prev`/`to_next` fails loudly. (No new endpoints in v1.)

**Playwright e2e (`tests/e2e/`, the JSDOM-layout blind spot — new spec):**
- `v2-nightboard.spec.ts`: open Nightboard via the toolbar verb; build a stubbed set;
  assert (a) the right number of tiles + (tiles−1) joints render; (b) clicking a joint
  opens the popover with reasons + alternatives; (c) "Swap in" changes the tile and the
  joint score updates; (d) the gravity tray lists candidates and "Add →" inserts a tile;
  (e) clicking a tile opens the inspector with the phrase strip; (f) both themes render
  (screenshot light + dark). Tiles are uniform-size but the canvas is NOT virtualized
  (a set is ~10–30 tracks) — no TASK-033 interaction.

**Control inventory:** add `nb-` ids (open verb, set-name input, build button,
start/end BPM, duration, energy-mode select, export button, joint buttons via a stable
`data-testid`, tray add/swap pills) to `control-inventory.json` + `selectors-exist.spec.ts`;
write-bearing (`export`, `save playlist`) → `safeOnRealDb:false`. e2e baseline = the
8 known pre-existing failures; zero new.

## Rollout & parity

- Nightboard ships **behind the workbench** (local-mode + `ac_workbench` gate,
  `shell.js:16`). No separate flag is strictly required, but a `localStorage.ac_nightboard`
  dev toggle (default off) is recommended so the canvas can merge inert before the
  toolbar verb is exposed, mirroring the P2 task-by-task additive discipline
  (`v2-p2-workbench.plan.md` "additive and local-mode-only behind a flag").
- **Tab-retirement parity:** the legacy `#setbuilder-section` (`index.html:852`) stays
  reachable until Nightboard reaches parity on: build a set, save as Rekordbox
  playlist, copy list, use-selected-as-anchors. When parity is met, the vertical
  set-builder section is removed and its `sb-` control-inventory entries reconciled
  (the drift guard fails in both directions, so removal must update the json) — the
  Nightboard `nb-` ids replace them. The static "DJ Mixing Guide" prose
  (`index.html:900–970`) is dropped (its content is superseded by live joint reasons).
- XML/Pages mode is unaffected — the canvas never renders there (program decision #5).

## Open questions & risks

- **Joint score thresholds** (good/ok/bad cutoffs ≥85/≥70) are presentation choices
  copied from the mockup (`design-D.html:405`); the product's own `overall` scale runs
  lower in practice (energy-missing tracks cap energy at 50–75, `transitions.py:113–127`),
  so real sets may show mostly amber. **Proposal:** calibrate thresholds against a real
  library before locking; possibly band relative to the set's own joint distribution.
- **Joint scoring round-trips** — scoring N−1 joints on initial render via N−1 calls to
  `/api/transitions/score` is chatty. v1 should **reuse `SetBuilderTrackItem.
  transition_score`** (`schemas.py:428`, already returned by `/api/setbuilder`) for the
  initial paint and only call `/api/transitions/score` on swap/insert. A bulk
  score-batch endpoint is a possible P-later optimization (assumption, not built).
- **Drag-to-reorder** — design-D marks crate cards `draggable` (`design-D.html:613`) but
  its handlers only do Add/Swap. v1 ships click Add/Swap; full drag reorder of the
  timeline is an **open question** (re-scoring all joints on drop is the cost).
- **Energy-curve fetch volume** — the arc needs every set track's energy curve.
  `/api/tracks/{id}/energy` is per-track; for a 30-track set that is 30 calls.
  **Assumption:** acceptable for v1 (sets are small, curves are L2-cached,
  `analysis-and-testing.md` energy L2); revisit if slow.
- **Anchors vs seed** — `build_set` takes `seed_track_id` (single) AND
  `anchor_track_ids` (many) (`setbuilder.py:108–109`). The grid-selection → Nightboard
  path should map selection to `anchor_track_ids` (mirroring `_useSelectedForSetBuilder`
  `08-set-builder-boot.js:35`); whether the focused/now-playing track becomes the
  `seed` is an open UX question.
- **`relaxed` honesty** — relaxed-placement tracks (`setbuilder.py:288`) must be marked
  on the tile so the DJ knows a transition was placed under loosened constraints; the
  exact marker is a design detail (proposal: a small mono "relaxed" tag).

## Success metrics

- A DJ can build a set, see its energy arc + zone bands, click any joint to read the
  real transition reasons, swap an alternative in place and watch the joint re-score —
  all without leaving the canvas, all on data the engine already produces.
- Mockup-to-product parity judged against `design-D.html` (program success metric:
  "mockup-to-product parity … (+ B/C/D for their modes)").
- Three-leg gate green (pytest · vitest · playwright) with zero new e2e failures vs the
  8-known baseline; both themes verified with screenshots.
- No new analysis code; no backend endpoint added in v1; no build step introduced;
  `docs/js/v2/nightboard/*` reads legacy only via `window.ACBridge`.
- Lighthouse perf not worse than baseline when the canvas is open (program success
  metric).
