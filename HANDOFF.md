# HANDOFF — AutoCue 2.0 redesign (2026-06-16)

Autonomous phased build of the B "Crate Console" redesign. **All planned build
phases P0–P5 are MERGED to `main`** (P6 LLM composer is deferred by design — see
table). The workbench is the default local-mode home; the legacy Cues/Library tab
bar is **retired** — Library is now the `#wb-library-place` rail place (#224).
A post-program **UI aliveness sequence** (steps 1+1b merged; **steps 2–5
implemented on `feat/aliveness-steps-2-5`, PR open**). See the section after the
phase table. Decisions locked via socratic grill — see
`.claude/PRPs/prds/autocue-2-program.prd.md` + memory `project_autocue_2_redesign.md`.
Do NOT re-litigate them.

## ⚠️ Workflow rule (new, enforced) — never commit to `main`
A tracked pre-commit hook (`.githooks/pre-commit`, activated via
`git config core.hooksPath .githooks`) **blocks `git commit` on main/master**.
Everything — code, docs, PRDs, plans — lands via: branch → commit → push →
`gh pr create` → `gh pr merge`. No local ff-merges to `main`. See the CLAUDE.md
"Worktree + PR workflow" section. Emergency override: `git commit --no-verify`.
(One-time per fresh clone: `git config core.hooksPath .githooks`.)

## Program status (phase table: `.claude/PRPs/prds/autocue-2-program.prd.md`)
| Phase | State |
|---|---|
| P0 foundations | ✅ merged #208 |
| P1 global layer (status sentence + ⌘K) | ✅ merged #209 |
| P2 workbench-as-home | ✅ merged #211; **default-on** shipped after (c3dcff0) |
| **P3 Duplicates as a place** | ✅ merged **#212** |
| **P4 Nightboard canvas mode** | ✅ merged **#217** (full-bleed set canvas, visualize-only) |
| **P5 Discover into the shell** (retires `#tab-discover`) | ✅ merged **#215** |
| P6 AUTOCUE_LLM composer | 📋 PRD only, deferred by design |

`main` HEAD at handoff: `cfb7390` (post #226, aliveness step 1b). Synced with origin.

## Post-program: UI aliveness sequence (motion polish) — ACTIVE next work
A separate, post-v2 effort to make the UI feel more dynamic WITHOUT breaking the
restrained ElevenLabs-clean ethos. Audit + 7 propositions designed 2026-06-15.
NOT part of the P0–P6 program. **Start here for the next session.**

**DONE:**
- **Step 1 (#225)** — A1 inspector reveal stagger (`app.css` `#wb-inspector-body > *`
  fadeSlideIn) · P2 harmonic glow (selecting a track outlines its Camelot-compatible
  neighbours green; `inspector.js _glowHarmonic` reuses `window._sbKeyCompat`; key-only)
  · A3-lite rail hover green edge.
- **Step 1b (#226)** — P6 commit wave (`06-render.js _commitWave`, fired AFTER the
  primary apply path re-renders the cards, `04-app-chrome.js`) · A2 directional
  place-swap (`body[class*=wb-place-] .tab-entering` → `_placeSlideIn`).
- **Steps 2–5 (`feat/aliveness-steps-2-5`, PR open)** — see below.
- All additive + `prefers-reduced-motion`-gated; CSS in the "Aliveness" blocks in
  `app.css` (just after the `.wb-crate` PRM line). NOTE: many tracks lack a Camelot key,
  so P2 only fires on keyed tracks (correct — it lights where the data exists).

**Steps 2–5 — IMPLEMENTED on `feat/aliveness-steps-2-5` (one commit each):**

2. **Step 2 — Inspector slide-over drawer** (`338e73f` + review fix `c2b9908`).
   `#wb-inspector` no longer reserves a column — grid is full-width; `body.wb-inspecting`
   (added in `renderInspector`/`renderReleaseInspector`, removed in `clearInspector`)
   slides the drawer in via `transform:translateX` OVER the grid (no reflow,
   TASK-033/037). Scoped `:not(.nb-active)` so Nightboard's reserved flank is unchanged;
   z-index 105 clears `#tracks-sticky` (100); action-bar reserves its right edge only
   while inspecting (wide only). All 4 inspector consumers re-verified green in e2e.
   **P4 View Transitions were prototyped then DROPPED** (review fix): the title-morph's
   ~300ms render-suppression window swallowed a rapid second track-click (the selection
   was lost — verified the same click lands under reduced-motion / no-VT; pointer-events
   on the whole `::view-transition` pseudo-tree did not recover it). The morph is an
   optional flourish; the translateX slide is the real reveal and has no such regression.
   Don't re-add a VT here without solving the during-transition click loss.

3. **Step 3 — energy playhead trace + cue ping + cue drop-in** (`b618c95`).
   `_traceEnergyPlayhead` (in `_rafTick`, `05-engine.js`) draws a green playhead across
   the now-playing energy sparkline(s) (grid row + inspector) and pings `.phrase-cue-tick`
   markers as it crosses them (colour flash — the strip is overflow:hidden). Cleared on
   track-switch/ended/error. P3: `_fadeFreshCueUI` (`06-render.js`) staggers cue badges +
   ticks A→H via `--cue-drop-delay`. Ping/drop PRM-gated; the playhead line is not (it's a
   functional indicator like the existing playheads).

4. **Step 4 — health-scan score roll-up + staggered chips** (`bff3415`).
   `_animateScoreRing` counts the overall ring 0→score (easeOutCubic) then pops (reuses
   `_countPop`, extended to `.health-score-ring`); `_staggerHealthChips` pops the visible
   per-track chips top-to-bottom (`--land-delay`) so the "sweep" emerges from the chips —
   works with the virtualizer, no-ops behind the Library place. PRM-gated.

5. **Step 5 — drag-to-playlist** (`7f57b4f`).
   NEW `POST /api/playlists/{id}/tracks {track_ids}` (`routes.py`) — mirrors
   `create_playlist` (the additive, reversible sibling): `_rb_running` 409, single-writer
   commit, rollback on error, **NO backup** (matched the sibling; a backup would be
   inconsistent + over-engineered for an additive op), appends after max `TrackNo`, skips
   tracks already present. 6 tests (`TestAddTracksToPlaylist`). UI: cards `draggable` in
   local mode; rail playlist rows are drop targets (`rail.js _wirePlaylistDrop`) with P6
   drop gravity (swell + green wash), one write path `ACBridge.addTracksToPlaylist`
   (r.ok-checked toast, silent dropdown-count refresh), count pops on drop. Green wash
   always (signal); swell PRM-gated.

**Validation (all green):** pytest 1455 passed / 7 skipped (+6 new); vitest 886 passed;
e2e — full blast radius (grid + 3 places + nightboard + discover = 32 specs) green in
isolation; full run 213 passed + the documented 12 Discover #189 contention flakes (each
green re-run alone, NOT regressions).

**Ethos for all steps:** green = signal only; the only CTA is the ink pill; mono for
data; honour `prefers-reduced-motion` on every new animation; reuse the
`--ease-*`/`--dur-*`/`--shadow-*` tokens; no new deps (no-build).

## Tab nav — RETIRED via the Library place (2026-06-15)
The Cues/Library tab bar is now **fully retired**. (It had been live in local mode
— `#tab-nav` un-hidden on boot — which an earlier handoff wrongly called "gone";
this slice made it actually gone.)
- **Library is a workbench rail place** (`#wb-library-place`,
  `docs/js/v2/workbench/library.js`) that swaps the centre to the Library tools
  (`#library-tab-content`: health/cue-tools/discogs/comments/playlist-suggest/
  set-builder) via `switchTab('library')` — mirrors the Duplicates/Discover places.
- **The tab BUTTONS are hidden** (`#tab-group { display:none }`). `#tab-nav` stays
  (it hosts `#app-status` — the status sentence), so the top row now shows only the
  status sentence. **Cues is the default centre.**
- `#tab-cues`/`#tab-library` markup + `switchTab` REMAIN (load-bearing: the places
  call `switchTab`, and `.tab-btn` is still queried). They are inventoried as
  `skipSweep` (still in the DOM). **Don't "clean up" by deleting them** — that
  breaks `switchTab` and the rail places.
- Callers repointed to the place: ⌘K `go-library`/`health-scan` + the
  status-sentence health fact click `#wb-library-place`; `go-cues`/status-count
  deactivate places. Mutual exclusion is 3-way (Library/Discover/Duplicates).
- Tests: `tests/e2e/v2-library-place.spec.ts`; the harness navigates via the rail
  place (not the hidden tab buttons).
- True retirement (workbench owns Cues↔Library, tab bar removed) is unbuilt
  follow-up work, NOT a cosmetic cleanup.

## The "place" pattern (P3 + P5 — copy this for any future place)
A rail place swaps the workbench **centre pane** (not full-bleed):
- `docs/js/v2/workbench/{duplicates,discover}.js` — ES modules; own ONLY the
  rail entry + the swap + lazy first-load. Drive legacy via `window.ACBridge` /
  `window.DiscoverV2`; **never fetch endpoints directly, never import legacy**.
- Swap = toggle `hidden` + a `body.wb-place-*` class ONLY; `#track-list` is NEVER
  detached (Virtualizer/sticky invariants, TASK-033/037); CSS
  `display:none !important` backstop under the body class.
- Places are **mutually exclusive** (Duplicates ↔ Discover): cross-deactivate in
  both `activate()`s + every rail/crate exit (`shell.js`, `rail.js`).
  `_renderCrates` paints no crate `.active` while a place owns the centre
  (`autocue:wb-place-change` event).
- P3 restore = A-layer **status-sentence sheet** (`docs/js/v2/restore-sheet.js`,
  `#status-restore` fact → `#wb-restore-sheet`), fed by `autocue:duplicates-deleted`.
- P5 release detail re-hosts in `#wb-inspector` via a `_mode` ('track'|'release')
  flag on `inspector.js`; `switchTab('discover')` scroll-resets to top on
  grid-return (accepted tradeoff, documented in `discover.js` header).

## P4 Nightboard — SHIPPED (#217, 2026-06-13)
Full-bleed set-builder canvas **mode** (not a rail place): `body.nb-active` hides
rail+grid+inspector (grid HIDDEN, never detached — TASK-033/037), canvas owns the
body, global topbar stays. Entered by the `#nb-open-btn` verb + `open-nightboard`/
`build-set` ⌘K commands; local-mode only. VISUALIZE-only over the FROZEN
`setbuilder.py` + `transitions.py` + REST surface (`/api/setbuilder`,
`/api/transitions/score`, `/api/setbuilder/alternatives`, `/api/tracks/{id}/energy`,
`/api/playlists`) — **zero backend edits; pytest untouched-green = drift proof.**
Modules: `docs/js/v2/nightboard/{mode,set-model,canvas,joint-popover,tray}.js`.
Full module detail lives in the CLAUDE.md "Nightboard (P4)" bullet.
- **How the 3 plan open-questions resolved:** (1) no `transition_advice` REST
  field — popover uses `mix_advice` + the 3 `explanation` strings, no JS port;
  (2) joint bands ≥85/≥70 shipped as presentation constants (`JOINT_BANDS` in
  `canvas.js`) — real-library calibration left as a future tweak; (3) inspector
  reused at mode 'track' via `body.nb-inspecting`, no collision with P5's `_mode`.

## Proven implementer workflow (reuse for P6 / any future slice)
Launch an implementer via the Agent tool with `isolation: "worktree"`, hand it
the plan + any open questions, tell it to **commit per task** (the P3 agent wedged
before committing and nearly lost a day). Arm a **liveness watchdog** (Monitor
polling branch commit count + transcript byte-staleness + PR-open) — this
caught/avoided the wedge on P5 and worked again for P4. On PR open: independent
code-review (code-reviewer agent), **verify any "critical" empirically** before
acting (the P5 reviewer's "critical" was a false positive — the flagged tests
were network-gated skips, not failures), fix real findings on the branch, then
admin-merge.

## Validate / gate
Three-leg stack (run e2e ALONE — #189 contention flake):
```
pytest                                   # 1455 passed, 7 skipped (2026-06-16)
npm test                                 # 886 passed (2026-06-16)
cd tests/e2e && npx playwright test      # run alone
```
No linter is wired (no ruff/eslint); the three-leg stack is the gate.
**Baseline e2e behaviour under full-suite contention (#189 — NOT regressions):**
a contended full run drops ~12 tests, ALL in Discover; each passes on an
isolation re-run (verified 2026-06-15). The set:
- `v2-discover-shell.spec.ts` — the full 7-test P5 place suite (a–h)
- `discover-v2.spec.ts` — feed-render / inspector-rehost / `?` help / Save (4)
- `per-control-sweep` — `disc-v2-refresh-btn`
Re-run any failing Discover spec file ALONE (`npx playwright test <file>`) to
confirm green before assuming a regression. P4 `v2-nightboard.spec.ts` is NOT in
this set — it passes in the full run. (The two `action-bar-*` per-control rows
that used to fail deterministically were fixed in #221 — issue #219.)

## Open items
- **Aliveness steps 2–5 PR (`feat/aliveness-steps-2-5`) awaits merge** — three-leg gate
  green (Discover e2e flakes are the documented #189 baseline, green in isolation).
- New endpoint `POST /api/playlists/{id}/tracks` is now live — there is still **no
  remove-from-playlist endpoint** (a misdrop is undone in Rekordbox, not via API).
- Optional cleanup: delete the inert `#tab-cues`/`#tab-library` buttons (cosmetic).
- Next build work: only **P6** (AUTOCUE_LLM composer) remains, deferred by design.

_Resolved 2026-06-15: #214 closed (plan merged via #217); merged-branch worktrees
pruned; the two action-bar per-control e2e failures fixed (#221, closing #219);
**#187 closed** — its "159px overlap" was a test false-positive (guard
card-picking; fixed via `VISIBLE_THRESHOLD_PX=5`), product layout verified clean
(re-measured 0.13px in title-sort + visual confirm)._

## Run / verify locally
`python -m autocue serve --port 7434 --no-browser` (use 127.0.0.1, never
localhost — memory rule). Real master.db; read-only browse is safe; NEVER run a
real (non-dry-run) delete or a real Discover scan against it — use route mocks /
a scratch DB copy. To serve a worktree's code, run `python -m autocue serve` from
that worktree (the installed `autocue` resolves to the main repo's `docs/`).
