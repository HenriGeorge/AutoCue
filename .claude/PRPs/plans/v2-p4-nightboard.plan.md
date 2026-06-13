# Feature: AutoCue 2.0 — P4 Nightboard (full-bleed canvas MODE of the workbench)

## Summary

Ship **Nightboard** — a new full-bleed canvas **mode** of the P2 workbench that *visualizes* the analysis the engine already produces. A horizontal set timeline of energy-curve tiles, circular transition joints scored by the existing `score_transition`, a click-to-open joint popover with the engine's real `explanation` strings + `transition_advice` + ≤2 swap alternatives, a gravity tray of ranked next-track candidates, four zone bands, and a set-wide energy arc. It is a **presentation + orchestration** layer over `setbuilder.py` + `transitions.py` and their existing REST surface — **no new analysis math, no new backend endpoint in v1**. `pytest` stays untouched-green as the zero-drift proof.

Per program decision #3, Nightboard is a **MODE** (a grammar category of its own), NOT a rail place: it temporarily replaces rail+grid+inspector full-bleed, entered via a *verb* (toolbar button + ⌘K). Unlike P3/P5 — which swap only the centre pane and were re-drivers of finished features — P4 is **net-new UI** over a frozen backend. The same interop discipline applies: native ES modules under `docs/js/v2/nightboard/`, imported by `docs/js/v2/main.js`; reads legacy ONLY via `window.ACBridge`; never imports legacy.

**Source PRD:** `.claude/PRPs/prds/v2-p4-nightboard.prd.md` (R1–R13).
**Pattern reference:** `.claude/PRPs/plans/v2-p3-duplicates-place.plan.md`, `.claude/PRPs/plans/v2-p5-discover-shell.plan.md` (structure + interop/swap discipline) + shipped `docs/js/v2/workbench/{duplicates,inspector}.js`.
**Interaction source:** `docs/design/mockups/design-D.html` (vendored mockup — visual language only, NOT shippable code).
**Branch:** `feature/v2-p4-nightboard`, base `main` (post-P2 workbench + P3 duplicates-place; **P5 may merge first** — see Risks for the inspector mode-flag coordination).

## Key facts (verified against this repo at the planning HEAD)

### Analysis — reused as-is, never modified (the zero-drift contract)
- **`build_set(db, start_bpm=110.0, end_bpm=135.0, duration_minutes=60.0, energy_mode="build", bpm_step_max=0.08, seed_track_id=None, anchor_track_ids=None)`** — `autocue/analysis/setbuilder.py:101`. Beam search. Returns `{"tracks": [...], "terminated_reason": str}` (docstring `:114–120`; build `:328–344`). Each track dict carries: `track_id, title, artist, bpm (round 2), key, category, transition_score, mix_advice, relaxed` (`:331–339`). Anchors merged by `_merge_anchors` (`:352`). Empty/no-seed → `{"tracks": [], "terminated_reason": "no_candidates_passed_thresholds"}` (`:139`, `:325`). Safety cap → `terminated_reason="safety_cap_hit"` (`:180`).
- **`score_transition(content_a, content_b, db)`** — `autocue/analysis/transitions.py:272`. Weights 0.40 BPM / 0.35 key / 0.25 energy (`:311`). Returns `{overall, bpm, key, energy, bpm_a, bpm_b, key_a, key_b, end_energy_a, start_energy_b, explanation}` (`:319–331`). **`explanation` is a 3-element `list[str]`** — `[_bpm_explanation, _key_explanation, _energy_explanation]` (`:313–317`) — the popover body, deterministic, no LLM.
- **`transition_advice(ts)`** — `transitions.py:194`, returns a single string (`"; ".join(parts)` `:269`). **NO REST field named `transition_advice`** exists. OPEN QUESTION: v1 either (a) ports its logic to JS, or (b) shows `mix_advice` from `/api/setbuilder` + the three `explanation` strings and skips a separate advice line. **Recommend (b)** to avoid duplicating scoring logic in JS. Flagged for the program owner.

### REST surface — all four exist, verified; no backend change for v1
- **`POST /api/transitions/score` → `TransitionResponse`** — `routes.py:2760`. Request `TransitionRequest {track_a_id, track_b_id}` (`schemas.py:265`); 400 if equal ids (`routes.py:2764`), 404 if a track missing (`:2768`). Response mirrors `score_transition` + `track_a_id/track_b_id` (`schemas.py:270–283`, incl. `explanation: list[str]`).
- **`POST /api/setbuilder` → `SetBuilderResponse`** — `routes.py:2785`. Request `SetBuilderRequest` (`schemas.py:411`: `start_bpm=110, end_bpm=135, duration_minutes=60, energy_mode∈{build,flat,drop}, bpm_step_max=0.08, seed_track_id, anchor_track_ids=[]`). Response (`schemas.py:433`): `tracks[SetBuilderTrackItem], total_tracks, estimated_duration_minutes, terminated_reason`. `SetBuilderTrackItem` (`schemas.py:421`): `track_id, title, artist, bpm, key, category, transition_score (float|None), mix_advice (str|None), relaxed (bool)`. **Empty set → 422** `"No valid set could be built with the given constraints"` (`routes.py:2804`).
- **`GET /api/setbuilder/alternatives?track_id&prev_id&next_id&exclude_ids&n` → `SetAlternativesResponse`** — `routes.py:2825`. `n` default 8, clamped `ge=1, le=20` (`:2831`). `SetAlternativeItem` (`schemas.py:515`): `track_id, title, artist, bpm, key, score, from_prev (float|None), to_next (float|None), genre (str), genre_match (bool|None)`. Genre mismatch −20 penalty (`routes.py:2896`). BOTH the swap-alternatives AND gravity-tray data source.
- **`POST /api/playlists` → `CreatePlaylistResponse`** — `routes.py:2928`. Request `{name, track_ids}` (`schemas.py:504`). **409 when Rekordbox running** (`:2941`, `_rb_running(db)`). The existing "save set as Rekordbox playlist" path.
- **`GET /api/tracks/{track_id}/energy` → `EnergyResponse`** — `routes.py:2515`. The per-track energy curve the P2 sparkline + the Nightboard arc consume. (Arc needs N curves = N calls for an N-track set; see Risks.)

### Frontend seams (the mode hangs off these)
- **`window.ACBridge`** — `docs/js/08-set-builder-boot.js:921` (accessor closures; the ONLY way v2 reaches legacy bindings). Exposes `tracks()`, `pending()`, `selectedIds()`, `activePlaylistId()`, `isLocalMode()`, `buildTrackCard(...)`, `explainCue(cue)`, `showTransitionScore()`, `renderTracks()`, `setCrate(kind)`, P3 duplicates pass-throughs (`:957–959`). New P4 accessors append after `:959`.
- **`docs/js/v2/main.js`** — the only module entry. P2 `:28`; P3 duplicates `:40–42`; restore-sheet `:46`. P4 adds ONE import block. `window.AC2.workbench` exposes `setWorkbench` (`:29`).
- **Inspector** — `docs/js/v2/workbench/inspector.js`: `renderInspector(trackId)` (`:29`, reads `ACBridge.tracks()`, populates `#wb-inspector-body`), `clearInspector()` (`:143`), `focusedId()` (`:151`), `initInspector()` (`:155`, capture-phase grid click). **No `_mode` flag on this branch** (P5 work, may merge first — see Risks).
- **Grid toolbar host** — `#wb-grid-head` (`docs/index.html:425`). Rail `#wb-rail` `.wb-rail-section` groups (`:1528,1532,1536,1545`). Inspector `#wb-inspector` (`:1555`), body `#wb-inspector-body` (`:1561`).
- **Commands** — `docs/js/v2/commands.js`: existing `build-set` (`:48`) runs `_goto('library','setbuilder-section')` — the orphaned door P4 repoints. `find-duplicates`/`go-duplicates` (`:36`/`:42`) show the `setWorkbench(true)` + `.click()` force-activate idiom.
- **Consent gate** — `_consentCanConfirm(reviewRequired, reviewed, elapsedSinceReveal)` (`docs/js/07-helpers-events.js:145`) + `_confirmDialog(message, opts={})` (`:162`) — legacy helpers, NOT v2 modules; driven via `window._confirmDialog`.
- **Mockup** — `docs/design/mockups/design-D.html` (663 lines): `--zone-warmup/-build/-peak/-closing` (`:28–30` light, `:43–44` dark); `#canvas`/`#zones`/`#arc`/`#timeline`/`.joint`/`#popover` CSS (`:93–170`); `prefers-reduced-motion` block (`:262`); markup (`:291–298`); `scoreTransition` (`:383`, **mock math — do NOT port; product math lives in `transitions.py`**); `scoreClass=s=>s>=85?'s-good':s>=70?'s-ok':'s-bad'` (`:405`, joint colour thresholds); `renderZones` (`:428`), `renderArc` (`:434`), `renderTimeline` (`:449`).

### Invariants (NON-NEGOTIABLE)
- No build step; native ES modules; `python -m http.server` + FastAPI StaticFiles keep serving (decision #4).
- Nightboard is a **separate full-bleed view**, NOT an overlay — must NOT touch path-(a) document-scroll / sticky / Virtualizer invariants (TASK-033/037). It hides the grid; never detaches/re-parents `#track-list`.
- Local-mode only (decision #5; gate on `ACBridge.isLocalMode()`).
- BPM math guards `parseFloat(bpm) > 0` (Rekordbox stores `"0.0"`).
- Five design rules: green = signal only (active-tile ring, joint-good, arc/sparkline stroke, success toast); the ONLY CTA is the ink pill; mono for every measured value; pills 999px, tiles 12px, canvas/cards 16px (`--radius-xl`), chips 4px; tokens only, both themes; `prefers-reduced-motion` honoured.
- Every new interactive id → `tests/e2e/control-inventory.json` AND `tests/e2e/selectors-exist.spec.ts`. Three-leg gate per merge (pytest · npm test · playwright e2e ALONE — #189).
- `r.ok` checked before reading typed fields on every fetch.

## Tasks (execute in order; each merges green on the three-leg stack)

### T1 — Legacy seams: ACBridge pass-throughs for export + anchors (additive)
**Files:** `docs/js/08-set-builder-boot.js` (bridge `:921–960`), `tests/web/v2-nightboard.test.js` (new).
1. Append to `window.ACBridge` (after `:959`), all delegating — verify each target symbol exists before wiring; do NOT invent a write:
   - `exportSetAsPlaylist: (name, trackIds) => …` — pass-through to the SAME path `#sb-save-playlist-btn` uses (the legacy create-playlist handler POSTing `/api/playlists`). If it's an inline closure, extract a named `_saveSetAsPlaylist(name, ids)` (presentation-neutral) and pass through. OPEN QUESTION until T1 re-verify: confirm the legacy save-playlist handler symbol name on the fresh branch.
   - `anchorsFromSelection: () => Array.from(selectedTrackIds)` — seeds `anchor_track_ids` from grid selection.
2. Vitest source-contract (via `loadAppHtml()`): the two accessors exist; `_saveSetAsPlaylist` routes through `/api/playlists`.

**VALIDATE:** `npm test` (new + existing set-builder tests untouched-green); `pytest` untouched. No UI.

### T2 — Mode skeleton: toolbar verb + ⌘K + full-bleed swap (inert)
**Files:** `docs/index.html`, `docs/js/v2/nightboard/mode.js` (new), `docs/js/v2/main.js`, `docs/js/v2/commands.js`, `docs/css/app.css`, `selectors-exist.spec.ts`, `control-inventory.json`, `tests/web/v2-nightboard.test.js`.
1. Markup: `<section id="nb-canvas" hidden aria-label="Nightboard">` body-level (sibling of the workbench shell, NOT inside `#track-list`'s parent). Scaffold: `#nb-topbar`, `#nb-zones`, `#nb-arc-row`, `#nb-timeline`, `#nb-tray`, `#nb-popover`. Add `#nb-open` button to `#wb-grid-head` (local-mode only) + `#nb-close` in `#nb-topbar`.
2. `docs/js/v2/nightboard/mode.js` (export `initNightboard`/`openNightboard`/`closeNightboard`/`isOpen`; register `window.AC2.nightboard`):
   - `openNightboard()`: guard `ACBridge.isLocalMode()`; add `body.nb-mode`; un-hide `#nb-canvas`; the body class hides `#wb-rail`/`#tracks-sticky`/`#track-list`/`#wb-grid-head`/`#wb-inspector`/`#action-bar` (display:none — belt-and-braces). NEVER detach `#track-list`. Focus canvas for ⌘K/Esc.
   - `closeNightboard()`: reverse + `ACBridge.renderTracks()` (repaint grid at scroll position). Esc closes.
   - `isOpen()`: `body.classList.contains('nb-mode')`.
3. main.js: one import block after `:42` mirroring `:28`/`:40`; register `window.AC2.nightboard`; `initNightboard()`.
4. commands.js (R1): repoint `build-set` (`:48`) → `setWorkbench(true); openNightboard()` (drop dead `_goto`); add `open-nightboard` command. Explicit nav overrides `ac_workbench='0'`.
5. CSS `/* ── v2: nightboard ── */`: `#nb-canvas` full-bleed; `body.nb-mode #wb-rail, … #tracks-sticky, … #track-list, … #wb-grid-head, … #wb-inspector, … #action-bar { display:none !important; }`. `#nb-open` neutral ghost pill (NOT green — verb; ink pill reserved for Export).
6. Guards: `#nb-open`/`#nb-close`/`#nb-canvas`/`#nb-zones`/`#nb-arc-row`/`#nb-timeline`/`#nb-tray` → `selectors-exist`; `nb-open`/`nb-close` → `control-inventory` `globalControls`.
7. Vitest (jsdom): `openNightboard` adds `nb-mode` + un-hides canvas; `closeNightboard` reverses + `renderTracks()`; guard early-returns when not local mode.

**VALIDATE:** `npm test`; `pytest`; `playwright test selectors-exist control-inventory` (alone). Chrome: toolbar "Open Nightboard" → grid/rail/inspector/action-bar gone, empty canvas fills screen; Esc/close → grid returns, virtualizes/sticks after scroll round-trip. Both themes, screenshots.

### T3 — Set model + build: tiles + zones + arc (R2, R4, R5)
**Files:** `docs/js/v2/nightboard/set-model.js` (new), `docs/js/v2/nightboard/canvas.js` (new), `docs/index.html` (build-controls in `#nb-topbar`), `docs/css/app.css`, `control-inventory.json`, `selectors-exist.spec.ts`, `tests/web/v2-nightboard.test.js`.
1. `set-model.js` (pure state + fetch, unit-testable): in-memory `SET` + `POOL`; `buildSet({...})` POSTs `/api/setbuilder`, `r.ok`-guarded, 422 → `{error, terminated_reason}` for R3, success stores tracks/total/duration/terminated_reason; `swapAt`/`insertAfter` mutators; `rescoreJoints(idx)` POSTs `/api/transitions/score` for ONLY the ≤2 joints touching idx; `fetchEnergyCurve(id)` GET `/api/tracks/{id}/energy`, returns `[]` on failure (arc degrades, no NaN). Initial paint reuses `SetBuilderTrackItem.transition_score` — do NOT score all joints on build.
2. `canvas.js` (pure render): stats strip (set name + mono count/duration/BPM-range/avg-score chips + ink-pill `#nb-export`); zone bands (`category`→warmup/build/peak/closing, width = duration fraction, sum to 1, `--zone-*`, pointer-events:none); arc (SVG path stitching each track's energy curve, duration-weighted, skip empty curves, green stroke + `--green-wash`); tiles (title/artist/BPM chip [mono, green-wash, `parseFloat(bpm)>0`]/key/category/sparkline/cue-status via `ACBridge.pending()`/`relaxed` marker; each tile a `<button>` `data-testid="nb-tile"` `data-id`); joints scaffolded with `transition_score` + `scoreClass` thresholds.
3. Build controls in `#nb-topbar`: `#nb-start-bpm`/`#nb-end-bpm`/`#nb-duration`/`#nb-energy-mode`/`#nb-build-btn`/`#nb-use-selected` (seeds anchors via `ACBridge.anchorsFromSelection()`).
4. R3 honesty: `safety_cap_hit`/`no_candidates_passed_thresholds` → visible non-error notice (not a blank board); 422 → toast, not a crash.
5. CSS: `.nb-tile` (12px), `.nb-zone`, `.nb-arc`, `.nb-stat-chip` (4px), `.nb-joint` (circular, `.s-good/.s-ok/.s-bad` from `--green`/`--warn-amber`/`--danger`). Tokens only, both themes.
6. Guards: `nb-build-btn`/`nb-use-selected`/`nb-start-bpm`/`nb-end-bpm`/`nb-duration`/`nb-energy-mode`/`nb-set-name` → `control-inventory` + `selectors-exist`. Dynamic `nb-tile`/joint use `data-testid` + ignore-list.
7. Vitest: `buildSet` maps fields + branches `terminated_reason` (R3); BPM guard rejects `"0.0"`; zone fractions sum to 1, all four buckets (R5); arc yields no NaN when a track lacks energy (R5); joint thresholds 85/70 (R6).

**VALIDATE:** `npm test`; `pytest`; `playwright test selectors-exist control-inventory` (alone). Chrome: open → build against a scratch local DB → tiles render in order with sparklines/zones/arc/stats; over-constrained build shows the `safety_cap_hit` notice (not blank). Both themes, screenshots.

### T4 — Joint popover: real `explanation` + swap alternatives (R6)
**Files:** `docs/js/v2/nightboard/joint-popover.js` (new), `docs/js/v2/nightboard/canvas.js` (joint click), `docs/css/app.css`, `control-inventory.json`, `selectors-exist.spec.ts`, `tests/web/v2-nightboard.test.js`.
1. `joint-popover.js`: `openJointPopover(jointEl, leftIdx)` anchors a popover; shows pair titles, `overall` (mono /100), the three `explanation` strings verbatim from `/api/transitions/score`, and a footer using `mix_advice` (the resolved OQ — do NOT port `transition_advice` math to JS in v1). Each `explanation` colour-codes good/warn. Fetch ≤2 alternatives `GET /api/setbuilder/alternatives?track_id={incoming}&prev_id={left}&next_id={right}&exclude_ids={setIds}&n=2`, `r.ok`-guarded; render mono `from_prev`/`to_next`/`score` + a "Swap in" pill (T5). Esc/click-outside closes; reduced-motion disables the open transition.
2. canvas.js: joint `<button>` click → `openJointPopover`; joints carry `data-testid="nb-joint"` + `data-left-idx`.
3. CSS: `#nb-popover` (`--radius-xl`, `--shadow-lg`), bullet dots (`--green`/`--warn-amber`), alternative rows. Mono scores.
4. Guards: `#nb-popover` (added T2) + dynamic "Swap in" (`data-testid="nb-swap"`) ignore-listed.
5. Vitest: popover renders all three `explanation` strings from a stub; alternatives request includes right `prev_id`/`next_id`/`exclude_ids`; `n=2` cap (R6).

**VALIDATE:** `npm test`; `pytest`. Chrome: build → click joint → popover shows real reasons + ≤2 alternatives with mono fit scores; Esc closes. Both themes, screenshots.

### T5 — Swap-in re-scores only affected joints (R7)
**Files:** `set-model.js`, `joint-popover.js`, `canvas.js`, `tests/web/v2-nightboard.test.js`.
1. set-model: `swapAt(idx, altItem)` replaces `SET[idx]`; `rescoreJoints(idx)` issues ≤2 `POST /api/transitions/score` (left+right), `r.ok`-guarded, writes new `overall` onto affected joints only.
2. joint-popover/canvas: "Swap in" → `swapAt` → repaint only the affected tile + ≤2 joints + recompute arc/zones/stats (no rebuild). New joint score; success toast (green=signal). Close popover.
3. Vitest: `swapAt` mutates order; `rescoreJoints(idx)` touches exactly ≤2 joints and issues NO `/api/setbuilder` (R7).

**VALIDATE:** `npm test`; `pytest`. Chrome: open joint popover → Swap in → incoming tile changes, joint score updates, arc/stats shift, network shows ONLY ≤2 `/api/transitions/score` (no `/api/setbuilder`). Both themes, screenshots.

### T6 — Gravity tray + tile-focus inspector reuse (R8, R9)
**Files:** `docs/js/v2/nightboard/tray.js` (new), `canvas.js`, `docs/js/v2/workbench/inspector.js` (additive param), `docs/css/app.css`, `control-inventory.json`, `selectors-exist.spec.ts`, `tests/web/v2-nightboard.test.js`.
1. `tray.js` (sticky bottom shelf): ranked candidates for the focused tile from `GET /api/setbuilder/alternatives` (track_id=focused, prev/next=neighbours); each card mono `score`/`from_prev`/`to_next` + "Add →" pill (`data-testid="nb-add"`) → `insertAfter` + `rescoreJoints`. Collapsible (`#nb-tray-toggle`). **v1 = click Add/Swap only, NO drag-to-reorder** (the mockup's `draggable` is decorative).
2. Tile-focus inspector (R9): tile click → `renderInspector(trackId)` (the SAME P2 module). Since `nb-mode` hides `#wb-inspector`, add an OPTIONAL target param `renderInspector(trackId, hostId='wb-inspector-body')` (additive, default-preserving — zero P2/P3 regression) so Nightboard passes `#nb-inspector-body` (a `#nb-inspector` host inside `#nb-canvas`). Cue generation delegates to the existing preview/apply pipeline + H consent gate via `window._confirmDialog` — NO new write path.
3. CSS: `.nb-tray` sticky shelf, candidate cards (`--radius-xl`), Add pills (999px). Active-tile ring = `--green-ring` (signal).
4. Guards: `#nb-tray-toggle`/`#nb-inspector`/`#nb-inspector-body` → `selectors-exist`; `nb-tray-toggle` → `control-inventory`; dynamic `nb-add` ignore-listed.
5. Vitest: tray builds cards from a stub; "Add →" calls `insertAfter` + `rescoreJoints`, no `/api/setbuilder` (R8); `renderInspector(id, '#nb-inspector-body')` populates the Nightboard host while the legacy default still targets `#wb-inspector-body` (no regression).

**VALIDATE:** `npm test`; `pytest`. Chrome: focus tile → tray lists candidates → Add inserts + re-scores; click tile → inspector phrase strip/cue reasoning renders in the canvas; collapse tray. Both themes, screenshots.

### T7 — Export delegation + interop sweep + R11/R12 + Playwright spec + final gate (R10–R13)
**Files:** `mode.js` (export), `tests/e2e/v2-nightboard.spec.ts` (new), `tests/web/v2-nightboard.test.js`, `.claude/project/web-ui.md`, `CLAUDE.md`, PR.
1. Export (R10): `#nb-export` ink pill → if it would overwrite cues, gate via `window._confirmDialog(message, {reviewRequired, evidence})` + `_consentCanConfirm` (`07-helpers-events.js:145/:162`); on confirm delegate to `ACBridge.exportSetAsPlaylist(name, ids)` (T1) → POST `/api/playlists`. `r.ok`-checked; 409 (`_rb_running`) → toast, not crash. Success toast.
2. Interop sweep (R11): Vitest regex — `docs/js/v2/nightboard/*.js` has NO bare `parsedTracks`/`pendingCues`/`selectedTrackIds` reads (only via ACBridge), NO legacy import; the new ACBridge accessors exist.
3. Design conformance (R12): both themes; `prefers-reduced-motion` disables every canvas/joint/popover/tray transition (gate inside `@media (prefers-reduced-motion: no-preference)`); token-purity sweep over the new CSS (no `#`-hex, no raw `rgba(` outside token defs); Vitest source-contract for the reduced-motion gating.
4. New e2e `tests/e2e/v2-nightboard.spec.ts` (mock `/api/setbuilder` + `/api/transitions/score` + `/api/setbuilder/alternatives` + `/api/tracks/*/energy`; force `ac_workbench` on):
   - (a) open via `#nb-open` → `#nb-canvas` visible; grid/sticky/grid-head/rail/inspector hidden; `#action-bar` hidden (R1);
   - (b) build a stubbed set → N tiles + (N−1) joints (R2);
   - (c) click joint → popover with three `explanation` reasons + ≤2 alternatives (R6);
   - (d) Swap in → tile changes + joint updates; network shows ONLY ≤2 `/api/transitions/score`, no `/api/setbuilder` (R7);
   - (e) tray "Add →" inserts a tile (R8);
   - (f) tile click → inspector phrase strip in the canvas (R9);
   - (g) close → grid re-shows + `#tracks-sticky` pins after scroll round-trip (TASK-033/037);
   - (h) reduced-motion → no canvas animation (R12);
   - (i) both themes screenshot.
   Canvas is NOT virtualized (sets ~10–30 tracks) — no TASK-033 interaction inside it.
5. Control inventory (R13): final reconcile; `nb-export` → `safeOnRealDb:false`; dynamic `nb-tile`/`nb-joint`/`nb-swap`/`nb-add` ignore-listed. Zero new e2e failures vs baseline.
6. Docs: `.claude/project/web-ui.md` "Nightboard mode" paragraph (full-bleed `body.nb-mode` swap, visualize-only contract, ≤2-joint re-score, inspector reuse, export delegation); `CLAUDE.md` Nightboard sentence (don't alter setbuilder/transitions/score text). AI-asset commit with `Context:` section.
7. Final three-leg gate from root, e2e ALONE (#189): `pytest` → `npm test` → `playwright test`. Zero new e2e failures; Lighthouse not worse with the canvas open. Open PR (base `main`): `feat(web): P4 Nightboard — full-bleed canvas mode (AutoCue 2.0)`.

**VALIDATE:** three legs green; new spec green both themes; `pytest` untouched-green (visualize-only proof); screenshots; PR open.

## Full-suite validation (per merge AND final)
```bash
pytest                                   # proves zero backend/analysis drift (no setbuilder/transitions/routes edit)
npm test                                 # Vitest incl. new v2-nightboard.test.js (set-model + canvas + interop)
cd tests/e2e && npx playwright test      # ALONE — #189; new v2-nightboard.spec.ts + reconciled control-inventory
```

## Risks & mitigations
- **Grid invariants across the full-bleed swap** (TASK-033/037): hide via `body.nb-mode` + `hidden` only, never detach `#track-list`; `ACBridge.renderTracks()` on close; T7 e2e (g) asserts sticky pinning after a close round-trip. If the sticky shadow misbehaves, dispatch a synthetic `scroll` after un-hiding — escalate before inventing anything bigger.
- **Inspector mode-flag collision with P5:** P5 adds a `_mode` ('track'|'release') flag to `inspector.js`. P4's only inspector change is the additive optional `hostId` param on `renderInspector` (T6) — orthogonal. If P5 lands first, re-verify `renderInspector`'s signature on rebase, keep the param additive; only add a `'tile'` mode (behaving like `'track'`) if P5's `_mode` early-return would otherwise hijack tile clicks. Coordinate at rebase; flagged.
- **`transition_advice` has no REST field** (OPEN QUESTION): the popover footer uses `mix_advice` + the three `explanation` strings, NOT a separate advice round-trip — porting `transition_advice`'s join into JS would duplicate scoring voice and risk drift. Confirm dropping the standalone advice line for v1 OR adding a future `transition_advice` field to `TransitionResponse` (a P-later backend change, out of scope).
- **Joint-score thresholds** (≥85 good / ≥70 amber, `design-D.html:405`) are presentation choices; the product's `overall` runs lower in practice (energy-missing tracks cap energy), so real sets may show mostly amber. v1 ships the mockup cutoffs as the proposal; calibration against a real library (or banding relative to the set's own distribution) is an OPEN QUESTION to settle before locking.
- **Initial-paint chattiness:** scoring N−1 joints = N−1 calls. v1 REUSES `SetBuilderTrackItem.transition_score` for the initial paint and only calls `/api/transitions/score` on swap/insert (T3/T5). A bulk `score-batch` endpoint is a P-later optimization (NOT built).
- **Arc energy-curve volume:** the arc needs N curves = N `/api/tracks/{id}/energy` calls. Assumption: acceptable for v1 (small sets, L2-cached curves); fetch in parallel, degrade per-track on failure (no NaN). Revisit if slow.
- **Worktree vs branch drift:** line numbers verified at the planning HEAD. T1 re-verifies cited symbols on the fresh branch; numbers may shift, symbols won't. The one symbol NOT yet pinned: the legacy save-playlist click handler name (T1 step 1) — re-verify and extract if inline.

## Rollback
Each task is an independent green merge; the branch lands as one PR. Rollback = revert the merge commit. **No backend file changes, no schema, no analysis math, no new endpoint, no localStorage migration** (only in-memory `SET`/`POOL` + reads of existing `ac_workbench`). The legacy vertical `#setbuilder-section` is **left fully intact** (P4 is additive; retirement of the vertical builder is deferred), so reverting P4 removes only the Nightboard module + its markup/CSS/ids; `control-inventory.json` reverts in the same commit. Mid-branch, no task removes user-facing surface — every task is purely additive.

## Out of scope
- Any analysis change: `setbuilder.py` / `transitions.py` math, weights, beam search, relaxation ladder, genre penalty — VISUALIZE only.
- New `/api/*` endpoints or schema (incl. a bulk `transitions/score-batch` and a `transition_advice` REST field); any backend edit.
- Drag-to-reorder the timeline (v1 ships click Add/Swap only).
- Retiring or reaching parity with the legacy vertical `#setbuilder-section` / `#sb-*` controls and the static "DJ Mixing Guide" prose — kept reachable; retirement is a separate later slice.
- XML/Pages-mode rendering (decision #5 — Nightboard is local-mode only).
- `AUTOCUE_LLM` composer (P6); any LLM in the popover (deterministic `explanation` only, decision #6).
