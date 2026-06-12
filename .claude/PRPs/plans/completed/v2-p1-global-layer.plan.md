# Feature: AutoCue 2.0 — P1 Global layer (status sentence, ⌘K palette, dock boundary)

## Summary

Lay the A-layer from the locked program decisions OVER the existing tabbed UI,
before any structural change: (1) evolve the existing `#app-status` strip into
the clickable **status sentence** ("2,789 tracks · 142 need cues · health
78/100 · ● Rekordbox closed"), (2) ship the **⌘K command palette** as ES
modules under `docs/js/v2/` with fuzzy command matching + track search, wired
to EXISTING functions only, with the future-LLM **composer seam** designed in
but rendered inert, and (3) explicitly DON'T rebuild the action bar — P1 only
relabels `#action-bar-apply` contextually; the full glass dock is P2. Local
mode only (PRD: the 2.0 shell never renders in XML/Pages mode). No build step,
no new deps, both themes, a11y + reduced-motion on everything new.
Source PRD: `.claude/PRPs/prds/autocue-2-program.prd.md` (phase P1, decision 7).
Canonical interaction source: `/var/folders/kg/k03ymsv51sjd109wm__rfyt40000gn/T/design-A.html`
(sentence facts as buttons, palette groups/keyboard, veil); `design-E.html`
shows how the layer composes with future modes ('/' opens palette, "Go to"
command group, palette as the LLM-composer seam).

## Key facts (verified against this worktree)

- **P0 is landed here**: `docs/index.html` is 1,465 lines; legacy JS is
  `docs/js/app.js` (10,811 lines, classic script, index.html:1460); the v2 ES
  seam is `docs/js/v2/main.js` (loaded `<script type="module">`,
  index.html:1462) which sets `window.AC2 = {}`; CSS is `docs/css/app.css`
  (2,276 lines). Vitest specs read source via `loadAppHtml()` from
  `tests/web/_source.js` (inlines local css/js back into the HTML).
- **Status strip**: markup at `docs/index.html:53–70` — `#app-status`
  (`aria-live="polite"`) inside `#tab-nav` with `<span>` items `#status-db`,
  `#status-count`, `#status-scan`, `#status-rb`, `#status-warmup` (+
  `#warmup-progress-text`). Renderer `updateAppStatus({connected, trackCount,
  rekordboxRunning, didScan})` at `docs/js/app.js:5786–5815`; it rewrites each
  item's `innerHTML` (tag-agnostic — survives span→button). Callers:
  app.js:597, 600 (loadTracksFromServer) and 5836 (detectLocalMode) — **no
  caller ever passes `rekordboxRunning`**, so `#status-rb` renders "Rekordbox ?"
  forever today. Scan-age refresher interval at app.js:5817–5822. Warm-up chip
  poller `_warmupPoll` at app.js:298 (toggles `#status-warmup` visibility) —
  must keep working untouched.
- **Backend**: `GET /api/status` (`autocue/serve/routes.py:118–130`) returns
  `StatusResponse` (`autocue/serve/schemas.py:8–12`: connected,
  rekordbox_version, track_count, db_path) — **no rekordbox-running field**.
  Helper `_rb_running(db)` exists at routes.py:14–29 → wraps
  `autocue.db_writer.rekordbox_is_running` (db_writer.py:107: psutil
  process-name probe + exclusive file-lock on master.db; not free — the
  600 ms `detectLocalMode` budget at app.js:546 means it must be **opt-in**).
  Existing /api/status pytest coverage: `tests/test_serve_routes.py:55–75`.
- **Globals visibility (classic-script rules)**: top-level `function` and
  `var` ARE on `window` — `switchTab` (app.js:5741), `updateAppStatus`,
  `updateSelectionBar` (7068), `showToast` (8719), `scanLibraryHealth`,
  `scanDuplicates`, `AppState` (var, 484, pub/sub with
  `subscribe('tracks'|'filters'|'settings')`), `_warmupPoll` (var, 298).
  Top-level `let` is NOT — `parsedTracks` (427), `healthLastSummary` (541),
  `localMode` (539), `selectedTrackIds` (478). The guard
  `Array.isArray(window.parsedTracks)` at app.js:1169 is in fact always false
  at runtime. **v2 modules therefore need an explicit read-only bridge** (T2);
  per the main.js interop comment, v2 reads legacy only via `window.*`.
- **Fact sources**: need-cues = `parsedTracks.filter(t => t.existingHotCues
  === 0).length` — `existingHotCues` mapped from API `existing_hot_cues` at
  app.js:610 (same field the existing-cues banner counts at app.js:630).
  Health = `healthLastSummary.library_score` (set on SSE done at app.js:759 +
  785; rendered `Math.round(s.library_score)` at app.js:1474; also `.total`,
  `.no_cues`, `.no_beatgrid`, …) — null until the first scan of the session.
  Track rows for palette search carry `name, artist, album, bpm, key,
  existingHotCues` (app.js:607–626; `key` is Camelot text from the API).
- **Keyboard**: ONE legacy document keydown at app.js:9025–9061 — Escape
  (kbd overlay), `?` overlay toggle, `/` focuses `#track-search` **which does
  not exist in docs/index.html** (real search box is `#search-input`; the `/`
  shortcut is dead code, so the palette can claim `/` without breaking
  anything), `1/2/3` tabs, ⌘A select-all; all gated by `inInput`. Discover has
  its own handlers (detail panel, snooze) — all bubble-phase. **Palette gets
  strict priority by registering ONE document keydown in the CAPTURE phase**
  and calling `stopPropagation()` for every key while open.
- **Action bar**: markup index.html:1427–1438 (`#action-bar-count`,
  `#action-bar-clear`, `#action-bar-preview`, `#action-bar-apply`). Shown by
  `updateSelectionBar()` (app.js:7068–7094: `.visible` class +
  `body.has-action-bar` + count-pop tick). `_wireActionBar` (app.js:7096–7112)
  delegates clicks to `#preview-cues-btn` / `#download-btn` /
  `#deselect-all-btn` so every guard (backup, `_rb_running` 409s, #173
  selection scoping) fires on the existing path — **palette commands must use
  the same `.click()` delegation, never re-implement**.
- **Command targets that exist today**: `#preview-cues-btn` (uses
  `activeTracks()` — issue #173), `#download-btn` (= Apply in local mode,
  app.js:5837), `#health-scan-btn` + `#health-section` (index.html:510–513),
  `#duplicates-scan-btn` + `#duplicates-section` (index.html:535–538),
  `#setbuilder-section` (index.html:825), `#theme-toggle` →
  `applyTheme()`/`ac_theme` (app.js:10797–10811), filter checkboxes
  `#phrase-only-cb` / `#beats-only-cb`, `switchTab('cues'|'library'|'discover')`,
  `#search-input` (drives `_scheduleSearchRecompute`).
- **Test conventions**: Vitest = jsdom, `tests/web/**/*.test.js`
  (vitest.config.js:5–6), `"type": "module"` — **v2 ES modules are directly
  importable in specs** (no vendored-mirror copying needed, unlike app.js
  logic, e.g. tests/web/phrase-lazy-load.test.js). e2e: every required id is
  asserted in `tests/e2e/selectors-exist.spec.ts` (REQUIRED_SELECTORS — add
  BEFORE referencing in other specs); **the drift guard
  `tests/e2e/control-inventory.spec.ts` enumerates every
  button/input/select/textarea WITH an id from the live DOM (forcing hidden
  sections visible) and fails on any id missing from
  `tests/e2e/control-inventory.json`** — converting status spans to buttons
  and adding palette controls REQUIRES inventory entries. e2e baseline: the 8
  known pre-existing failures (P0 plan), zero new.
- **Design rules** (docs/design/README.md + CLAUDE.md): green = signal only
  (focus rings, active palette item wash); primary CTA = ink pill, never
  green; every measured value (counts, scores, BPM·key meta) = `--font-mono`;
  pills 999px, panels 12px, elevated cards 16px (`--radius-xl` — the palette
  panel); glass blur ok on the palette veil/panel (sticky chrome in motion);
  `prefers-reduced-motion` honored (`_prefersReducedMotion` pattern exists at
  app.js:8865; design-A gates its `rise` keyframe in CSS). Reference
  `var(--token)` only. Virtualizer card-height + document-level-scroll sticky
  invariants: untouched (the layer is overlay/fixed chrome, no list changes).

## Out of scope (state of the boundary)

- **No action-dock rebuild.** P1 keeps `#action-bar` exactly as-is structurally;
  the only change is the cheap contextual relabel in T6. The floating glass
  pill dock from design-A/E is P2 (it belongs to the workbench shell).
- No "needs cues" list filter, no sheets (design-A's side sheet = P2/P3), no
  LLM routing (the composer seam renders an inert affordance only — PRD §6).
- No XML/Pages-mode rendering of any of this (PRD decision 5).

## Tasks (execute in order; VALIDATE after each)

### T1 — Backend: opt-in `rekordbox_running` on /api/status
`autocue/serve/schemas.py`: `StatusResponse.rekordbox_running: bool | None =
None`. `autocue/serve/routes.py` `status()`: add query param
`include_rb: bool = False`; when true set the field via `_rb_running(db)`
wrapped in `try/except Exception` → leave `None` on failure. Default call
stays exactly as cheap as today so `detectLocalMode`'s 600 ms budget
(app.js:546) is unaffected.
Tests (`tests/test_serve_routes.py`, next to the existing /api/status tests at
:55): (a) default response has `rekordbox_running is None`; (b)
`?include_rb=1` returns the monkeypatched bool
(patch `autocue.db_writer.rekordbox_is_running` — the deferred import in
`_rb_running` exists exactly so this works, routes.py:22–23); (c) helper
raising → `None`, still 200.
VALIDATE: `pytest tests/test_serve_routes.py -k status`.

### T2 — Legacy bridge in app.js (the only way v2 reads `let`-state)
Append a small block at the END of `docs/js/app.js`:
```js
// ── AutoCue 2.0 bridge (read-only) ── v2 ES modules (docs/js/v2/) may read
// legacy state ONLY through here — top-level `let` never reaches window.
window.ACBridge = {
  tracks: () => parsedTracks,
  healthSummary: () => healthLastSummary,
  isLocalMode: () => localMode,
  selectedCount: () => selectedTrackIds.size,
};
```
Plus two one-line event hooks so v2 never polls legacy internals:
in `_renderHealthSummary` (app.js:1459) dispatch
`window.dispatchEvent(new CustomEvent('autocue:health-summary'))`; in the
`detectLocalMode().then` local-mode branch (app.js:5826, after
`updateAppStatus`) dispatch `new CustomEvent('autocue:local-mode')`.
Vitest `tests/web/v2-bridge.test.js` (source-contract via `loadAppHtml()`):
asserts the `window.ACBridge` block and both CustomEvent names exist in the
app source, and that no file under `docs/js/v2/` references bare
`parsedTracks`/`healthLastSummary` without going through `ACBridge` (regex
sweep of `docs/js/v2/*.js`).
VALIDATE: `npm test`.

### T3 — Status sentence: markup + CSS + `docs/js/v2/status-sentence.js`
Markup (`docs/index.html:59–68`, minimal diff): convert the four `<span
class="status-item">` to `<button type="button" class="status-item">` (ids
unchanged: `status-db/count/scan/rb`; keep `data-tip`s, dots, inner span
structure — `updateAppStatus` writes `innerHTML`, tag-agnostic). Insert two
new fact buttons + seps after `#status-count`:
`<button type="button" class="status-item" id="status-needcues" hidden>` and
`<button type="button" class="status-item" id="status-health" hidden
data-tip="Library health — click to scan">`. `#status-warmup` stays a span
(it's a progress chip, not an action).
CSS: append to `docs/css/app.css` under a new
`/* ── v2: global layer ── */` banner — button reset for `.status-item`
(inherit font, no border/bg, 4px radius, pointer), hover `background:
var(--surface-2); color: var(--text)`, `:focus-visible` ring
`box-shadow: 0 0 0 3px var(--green-ring)`, `.status-item .num
{ font-family: var(--font-mono); color: var(--text); font-weight: 500 }`
(design-A #sentence .fact, adapted to the existing strip), transitions gated
by `@media (prefers-reduced-motion: reduce)`.
Module `docs/js/v2/status-sentence.js` (imported by `main.js`):
- Export pure `deriveFacts({tracks, healthSummary, rbRunning})` →
  `[{id:'needcues', visible, count}, {id:'health', visible, score}]`:
  need-cues = `tracks.filter(t => Number(t.existingHotCues) === 0).length`,
  visible once tracks are loaded (0 stays visible — design-A counts down to
  0 after apply); health visible only when `healthSummary` non-null, score =
  `Math.round(library_score)`.
- `initStatusSentence()`: no-ops until `autocue:local-mode` (local mode only).
  Re-derives + paints on `window.AppState.subscribe('tracks', …)` and on
  `autocue:health-summary`. Counts render in a mono `.num` span via
  `toLocaleString()`.
- Rekordbox fact: poll `GET /api/status?include_rb=1` every 30 s (skip when
  `document.hidden`; first poll immediately on init) and feed the EXISTING
  renderer: `window.updateAppStatus({connected:true, rekordboxRunning})` —
  the dot/text logic at app.js:5807–5814 finally gets a caller; do not
  duplicate it.
- Clicks: `#status-count` → `switchTab('cues')` + scroll `#tracks-section`
  into view; `#status-needcues` and `#status-health` → shared
  `revealHealth()`: `switchTab('library')`, scroll `#health-section`, and if
  `ACBridge.healthSummary()` is null click `#health-scan-btn` (existing
  guarded path); `#status-rb` → small anchored explainer popover (one
  absolutely-positioned div, `role="tooltip"`, id `rb-pop`, ~3 lines: writes
  go to master.db, Rekordbox locks it while open, backup before every apply
  — copy from design-A's rb-status sheet, compressed), closed on Esc /
  outside click / re-click; `#status-db` / `#status-scan` stay
  non-navigating (informational tooltips already exist).
Vitest `tests/web/v2-status-sentence.test.js`: import `deriveFacts` directly
(ES module). Cases: mixed cued/uncued counts; all-cued → visible count 0;
empty tracks → needcues hidden; healthSummary null → health hidden;
library_score 77.6 → 78. Plus a markup spec (via `loadAppHtml()`): the six
status ids exist, the four converted items are `<button type="button">`, new
facts carry `hidden`.
VALIDATE: `npm test`, then Chrome at `http://127.0.0.1:7432` — facts render,
both themes, screenshots to the user.

### T4 — Pure palette logic: `docs/js/v2/fuzzy.js` + `docs/js/v2/commands.js`
`fuzzy.js`: export `fuzzyScore(query, text)` — case-insensitive subsequence
match; bonus for word-boundary hits and consecutive runs; `-1` for no match —
and `rank(query, items, textOf)` (stable sort by score desc). Pure, zero DOM.
`commands.js`: export `buildCommands()` returning descriptors
`{id, group, label, sub, meta, metaMono, run}` — every `run` delegates to an
EXISTING surface via `window.*`/`.click()` so all guards fire (key facts):
- `preview-cues` → `#preview-cues-btn.click()` (selection-scoped per #173);
- `apply` → `#download-btn.click()` (backup + Rekordbox-running checks intact);
- `health-scan` → `switchTab('library')` + scroll + `#health-scan-btn.click()`;
- `find-duplicates` → `switchTab('library')` + scroll `#duplicates-section`
  + `#duplicates-scan-btn.click()`;
- `build-set` → `switchTab('library')` + scroll `#setbuilder-section`;
- `toggle-theme` → `#theme-toggle.click()` (keeps `ac_theme` persistence);
- filter presets: `phrase-only` / `beats-only` → checkbox `.click()`;
- `go-cues` / `go-library` / `go-discover` → `window.switchTab(...)`
  (design-E "Go to" group).
Also export `searchTracks(query, tracks)`: substring on `name + artist`, cap
8, descriptor meta `` `${(+t.bpm).toFixed(1)} · ${t.key || '—'}` `` (mono),
sub from cue state (`N cues` / `no cues`); `run` = `switchTab('cues')`, set
`#search-input.value = query-of-track` and dispatch `input` (rides the
existing `_scheduleSearchRecompute` debounce — no new list machinery, no
virtualizer contact).
Vitest `tests/web/v2-fuzzy.test.js` + `tests/web/v2-commands.test.js`
(direct ES imports): fuzzy — exact > prefix > subsequence, non-match −1,
empty query matches all, ranking stability; commands — registry ids/groups
complete and unique, every command has a `run` function, `searchTracks`
caps at 8 / matches artist / formats mono meta / handles missing key.
VALIDATE: `npm test`.

### T5 — Palette overlay: markup + `docs/js/v2/palette.js`
Markup (static so `selectors-exist` can assert it; keep minimal — list items
render dynamically): before `</body>` add
```html
<div id="cmd-veil" hidden>
  <div id="cmd-palette" role="dialog" aria-modal="true" aria-label="Command palette">
    <div id="pal-input-row">…search svg…
      <input id="pal-input" type="text" role="combobox" aria-expanded="true"
             aria-controls="pal-list" autocomplete="off"
             placeholder="Search tracks, or type a command — fix, duplicates, set…">
      <span id="pal-esc">ESC</span></div>
    <div id="pal-list" role="listbox"></div>
    <div id="pal-foot"><span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
      <span><kbd>↵</kbd> run</span><span><kbd>esc</kbd> close</span></div>
  </div>
</div>
```
plus a header hint pill `<button id="cmdk-hint-btn" type="button"
aria-label="Open command palette">⌘K</button>` next to `#theme-toggle`
(hidden until `autocue:local-mode`).
CSS under the same `/* ── v2: global layer ── */` banner, tokens only,
design-A look: veil `rgba(0,0,0,.28)` (`.55` dark), panel `--surface` /
`--radius-xl`(16px) / `--shadow-lg`, glass blur on the veil, group headers
uppercase `--muted-soft`, active item `background: var(--green-wash)` with
green icon tile, meta `--font-mono`, `rise` animation disabled under
`prefers-reduced-motion`, items are id-less `<button class="pal-item"
role="option">` (drift guard only collects elements WITH ids — verified).
`palette.js` (imports fuzzy + commands; imported by `main.js`):
- Open/close: ⌘K / Ctrl+K toggles anywhere; `/` opens when target is not an
  input (legacy `/` is dead — app.js:9041 targets nonexistent
  `#track-search`); `#cmdk-hint-btn` click; gate every open on
  `window.ACBridge.isLocalMode()` (XML mode: inert). Focus `#pal-input` on
  open; restore the previously-focused element on close.
- **Strict key priority**: one `document.addEventListener('keydown', h,
  {capture:true})`. When open: handle ArrowDown/ArrowUp (clamp, design-A),
  Enter (run active), Escape (close), Tab (trap to input) — and
  `stopPropagation()` on EVERY key so the legacy handler (app.js:9025,
  bubble) and Discover handlers never see them. When closed: only ⌘K and the
  not-in-input `/` are claimed.
- Render on input: `rank()` over `buildCommands()`; when query non-empty
  append `searchTracks(query, ACBridge.tracks())` under a "Tracks" group;
  `aria-activedescendant` on the input tracking the active option id
  (`pal-opt-N`); mouse hover sets active, click runs; veil click closes.
- Commands run AFTER close (design-A `runCommand` order) so focus restore
  doesn't fight `switchTab`'s scroll.
- **Composer seam (inert)**: when results are empty, render a single
  non-interactive `div.pal-composer-hint` (NOT a button, `aria-disabled=
  "true"`, muted): "Ask AutoCue (coming soon) — ⏎ does nothing yet". Enter
  with zero items is a no-op. Block comment documenting the seam: this is
  where the opt-in `AUTOCUE_LLM` phase (program PRD §6, P6) will route
  unmatched free text to the assistant; the input/empty-state contract is
  the API. Render nothing actionable now.
- Export the state-transition helpers (`moveActive(state, delta)`,
  `paletteReduce(state, event)` or equivalent) for unit tests.
Vitest `tests/web/v2-palette.test.js`: arrow clamping at both ends,
active-index reset on query change, Enter-on-empty no-op, composer hint
present only when zero matches, open gated off when `isLocalMode()` false.
Markup spec additions: `#cmd-veil`/`#cmd-palette`/`#pal-input`/`#pal-list`/
`#cmdk-hint-btn` exist via `loadAppHtml()`.
VALIDATE: `npm test` + Chrome: ⌘K open/type/run/Esc, both themes, zero
console errors.

### T6 — Action-bar contextual relabel (the WHOLE P1 dock story)
Cheap relabel only, inside `updateSelectionBar()` (app.js:7068): when count >
0 set `#action-bar-apply` text to `Apply to ${count.toLocaleString()}
track${count===1?'':'s'}` (reset to "Apply to Rekordbox" at 0). No structural
change, no new elements, no CSS beyond nothing. Add a code comment at
`_wireActionBar` marking the P2 boundary: "P2 replaces this bar with the
global action dock — do not grow it here." Verify the count-pop tick still
fires (the label change must not break `dataset.lastCount` logic).
VALIDATE: `npm test` (no spec reads this label today — confirm with grep) +
Chrome: select 2 tracks → label reads "Apply to 2 tracks".

### T7 — e2e: selectors, control inventory, palette smoke
- `tests/e2e/selectors-exist.spec.ts` REQUIRED_SELECTORS += `#status-needcues`,
  `#status-health`, `#status-rb`, `#cmd-veil`, `#cmd-palette`, `#pal-input`,
  `#cmdk-hint-btn` (rule: add here BEFORE any other spec references them).
- `tests/e2e/control-inventory.json` `globalControls` += the ids the drift
  guard will now enumerate: `status-db`, `status-count`, `status-scan`,
  `status-rb`, `status-needcues`, `status-health` (kind button — spans became
  buttons), `cmdk-hint-btn` (button), `pal-input` (kind search/text). Status
  facts are read-only-safe; none need `safeOnRealDb:false` (health/duplicates
  scans are reads). Run the drift guard locally and reconcile until it passes
  in BOTH directions.
- New `tests/e2e/v2-global-layer.spec.ts`: (a) sentence smoke — `#status-count`
  visible with a mono numeral; `#status-health` hidden pre-scan; (b) palette
  smoke — `page.keyboard.press('ControlOrMeta+k')` → `#cmd-veil` visible +
  `#pal-input` focused; type `dupl`; `Enter` → expect `#tab-library.active`
  and `#duplicates-section` in viewport; (c) priority — palette open, press
  `2`, assert tab did NOT switch; `Escape` closes and focus returns; (d) `/`
  opens the palette when no input is focused.
VALIDATE: `cd tests/e2e && npx playwright test` — zero NEW failures vs the
8-known baseline (control-inventory, selectors-exist, v2-global-layer,
qa-smoke all green).

### T8 — Chrome verification: themes, a11y, reduced motion
Against `http://127.0.0.1:7432` (never `localhost` — memory rule), send the
user screenshots at every state: sentence light+dark; palette open
light+dark; rb popover; relabeled action bar. Keyboard-only walk: Tab reaches
every fact button with a visible `--green-ring` focus ring; palette trap +
focus restore; `aria-live="polite"` on `#app-status` not broken by the
button conversion (no announcement spam — counts only change on real
updates, `dataset.lastCount` guard). Emulate `prefers-reduced-motion: reduce`
→ no palette rise animation, no fact hover transforms. Verify green is
signal-only on every new surface (active palette item wash + focus rings
ONLY; the apply pill stays ink) and every numeral/meta is mono.
VALIDATE: screenshots delivered; zero console errors both themes.

### T9 — Docs, three-leg gate, PR
Update `.claude/project/web-ui.md` (status-sentence + palette internals: the
bridge contract, capture-phase priority rule, composer seam) and the
CLAUDE.md track-record only if a must-know constraint changed (the
"new interactive controls must enter control-inventory.json" rule is worth a
bullet if not already implied). Commit AI-asset changes with a `Context:`
section. Full gate from repo root: `pytest` → `npm test` →
`cd tests/e2e && npx playwright test`. Open PR (base: main) titled
`feat(web): P1 global layer — status sentence + ⌘K palette (AutoCue 2.0)`,
body linking the program PRD and stating the T6 dock boundary.
VALIDATE: all three legs green; PR open.

## Acceptance criteria

- Status sentence: in local mode the strip reads as clickable facts —
  track count (mono), "N need cues" (derived client-side from
  `existingHotCues === 0`, live across reloads/deletes via the `tracks`
  signal), "health S/100" hidden until the first scan then live via
  `autocue:health-summary`, Rekordbox state showing real open/closed (dot +
  text) from `?include_rb=1` polling. Clicks navigate/run as specced; XML
  mode shows none of it.
- ⌘K palette: opens on ⌘K/Ctrl+K, `/`, and the header hint; fuzzy commands +
  track search (mono `BPM · key` meta) all wired through existing buttons/
  functions (no bypassed guard — apply still 409s with Rekordbox open);
  arrows/Enter/Esc with strict capture-phase priority over the app shortcuts;
  unmatched input shows the inert "Ask AutoCue (coming soon)" composer
  affordance and Enter does nothing.
- Action bar: unchanged structurally; apply button shows the selection count;
  P2 boundary comment in place.
- All new JS = ES modules under `docs/js/v2/` imported by `main.js`; legacy
  edits limited to the T2 bridge/events + T6 relabel; CSS appended under one
  `/* ── v2: global layer ── */` banner; index.html markup diff minimal; no
  build step, no new runtime deps, virtualizer + sticky invariants untouched;
  green = signal-only and mono-for-data hold on every new surface.
- Tests: new vitest specs (fuzzy, commands registry, palette state, fact
  derivation, bridge/markup contracts) green in `npm test`; e2e
  selectors-exist + control-inventory drift guard + palette smoke green;
  pytest green incl. the new /api/status cases; three-leg stack green with
  zero new e2e failures vs the 8-known baseline; both themes verified in
  Chrome with screenshots delivered.
