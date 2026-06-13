# Web UI internals (docs/index.html + docs/css/ + docs/js/)

- **Web app**: multi-file, NO build step (P0 split, 2026-06-12). Entry `docs/index.html` (markup only); `docs/css/app.css` (all styles); legacy JS in **8 ordered classic scripts** `docs/js/01-core.js` … `08-set-builder-boot.js` (loaded in order; concatenating them reproduces the original `app.js` statement order — shared global lexical environment, so top-level `let`/`const`/`function` are cross-file bare identifiers but only `function`/`var` reach `window`; contains 3 duplicate top-level `_esc`, legal later-wins); `docs/js/v2/*` (ES-module seam: ALL new code, imported by `main.js`, `window.AC2` namespace, reads legacy via `window.ACBridge`). Theme variables use CSS custom properties so dark mode works automatically. Source-reading specs use `loadAppHtml()` from `tests/web/_source.js` (inlines the 8 scripts + css back into one HTML view). `package.json` exists only for dev testing — the deployed app requires no npm.

- **Fetch error handling in JS**: always check `r.ok` before reading typed properties from `r.json()`. A 409 response returns `{detail: "..."}` — reading `resp.applied` or `resp.colored` on an error body yields `undefined` and produces misleading toast messages.

- **filteredTracks()**: Client-side function that applies search, phrase-only, beat-grid-only, rating, plays, last-played, and My-Tag filters to `parsedTracks`. All write operations (apply, delete, color, enrich, tag) use `filteredTracks()` — not `parsedTracks` directly. `parsedTracks` is never mutated by filters.

- **pendingCues**: JS map of `String(trackId) → [{slot,posSec,label,...}]` populated by the "Preview cues" button (calls `/api/generate`). Cleared after Apply completes. Rendered as a secondary timeline bar in each track card.

- **AppState pub/sub bus**: Module-level IIFE `AppState = { subscribe(key, fn), signal(key) }`. Subscribers are coalesced — multiple `signal()` calls in the same synchronous tick produce one flush (via `Promise.resolve().then()`). Keys: `'filters'` (re-renders on filter/sort change), `'settings'` (re-renders + updates overwrite warning), `'tracks'` (re-renders after library load). `subscribe()` returns an unsubscribe function. Subscriber exceptions are caught and logged — one bad subscriber never blocks others.

- **`_cardMap` smart diffing**: `Map<trackId, HTMLElement>` caches flat-mode track cards. `_computeSettingsFingerprint()` covers barsInterval, startBar, maxCues, skipExisting, mcMode, analysisMode, phrase total cue count, pendingCues key count, and healthData key count — fingerprint change clears the map and forces full rebuild. FLIP reorder animation via `Element.animate()` (batch reads before writes to avoid layout thrashing). Enter/exit animations use `.card-enter` / `.card-exit` classes. Large-exit threshold: if >30 cards exit in one render, animations are skipped entirely (instant snap) to prevent DOM bloat on search filter changes. `_sparkObserver`, `_mixObserver`, `_enterObserver` are module-level and `.disconnect()`-ed before reuse to prevent IntersectionObserver leaks.

- **RAF playhead**: `_playRafId` / `_startPlayRaf()` / `_stopPlayRaf()` replace `timeupdate`-driven `updateTimeline()`. RAF loop runs at 60fps while audio plays; started inside `audioPlayer.play().then()` (never before the browser confirms playback). `.timeline-playhead` has **no CSS transition** — with RAF at 60fps, a transition would permanently lag behind the playhead.

- **`_energyCache`**: Module-level `{}` mapping trackId → energy curve array (fetched from `/api/tracks/{id}/energy`). Cleared on library reload and XML upload. Used by `_drawMiniWaveform()` to render the canvas sparkline in the mini player.

- **Mini waveform canvas**: `<canvas id="mini-waveform">` inside `#mini-waveform-wrap`. HiDPI via `devicePixelRatio` scaling (`canvas.width = cssW * dpr`). Overlaid by invisible `<input type="range" id="mini-scrubber">` for seek interaction. The wrap uses `clip-path: inset(0 round 4px)` — **not** `overflow:hidden` — to preserve range input pointer events while still clipping the visual corners.

- **Sticky filter bar**: `#tracks-sticky` uses `position:sticky; top:0` with negative horizontal margins (`margin-left:-24px; margin-right:-24px; padding:0 24px`) to bleed edge-to-edge within `main`'s 24px side padding. The bar carries `border-bottom: 1px solid var(--border)` in **both states** so the track list never appears to clip into it. The `.shadowed` class (toggled by `IntersectionObserver` on a sentinel div) adds shadow + 98% opacity glass blur — the old 90%-transparent background was too see-through and let tracks bleed through. `#track-list` has `position:relative; padding-top:12px` to prevent any collapse with the sticky bar above. Default sort is `album` ascending. There is no slide-in scroll header (`#scroll-header` was removed).

- **App status row (`#app-status`)**: Lives in `#top-bar` next to `#tab-nav`, hidden via CSS until local mode is detected (`updateAppStatus({connected: true})` toggles `.visible`). Shows DB-connected dot, track count, last-scan age (recomputed every 60s via `setInterval`), and Rekordbox-running state. `updateAppStatus({trackCount, didScan, rekordboxRunning})` is the single mutation entrypoint; called from `loadTracksFromServer()` after every fetch.

- **Bottom action bar (`#action-bar`)**: Fixed at `bottom:0`, slides in via `transform: translateY()` when `selectedTrackIds.size > 0` (driven from `updateSelectionBar()`). Renders selection count + neutral Preview + primary green Apply. The Preview / Apply buttons proxy to `#preview-cues-btn.click()` and `#download-btn.click()` so the same backup + Rekordbox-running guards fire. When the legacy `#download-bar` is also visible, CSS `body:has(#download-bar.visible) #action-bar { bottom: 66px }` stacks the action bar above it. Sets `body.has-action-bar` so `#scroll-top-btn` lifts above the stack.

- **SSE consumption in JS**: `_consumeSSE(response, onEvent)` in `index.html` is the shared `fetch`+`ReadableStream` reader used by Discover and Download (POST SSE can't use `EventSource`). New SSE-driven UI should reuse it rather than re-inlining the reader loop. `_esc()` HTML-escapes all server-supplied strings rendered into Discover cards — never interpolate Discogs/YouTube text without it.

## Performance UI scaffolding (Performance v1 PRD)

- **`_perf` helper** (TASK-049/050): module-level IIFE near the top of the script block.
  Wraps `performance.mark` / `performance.measure`. No-op unless
  `localStorage.getItem('autocue_perf') === '1'`; when enabled, each `_perf.measure(name,
  startMark)` logs `[AutoCue Perf] <name>: <duration>ms` to console. API: `_perf.mark`,
  `_perf.measure`, `_perf.getEntries`, `_perf.clear`. Currently wraps `loadTracksFromServer`
  (`library-load`) and `filteredTracks` (`filter-recompute`). Vitest coverage:
  `tests/web/perf-helper.test.js`.

- **`Virtualizer` IIFE** (TASK-031 scaffold; TASK-032/034/035 wiring still pending):
  vanilla-JS list virtualization. API: `Virtualizer.attach({container, itemHeight,
  totalCount, renderItem, buffer})`, `Virtualizer.update({totalCount, scrollToIndex?})`,
  `Virtualizer.detach()`. Uses absolute-positioned cards inside a tall spacer + rAF-coalesced
  scroll + DOM-node recycling pool. `renderItem(index, recycledNode | null)` returns the DOM
  node to mount; off-screen nodes are returned to the pool and reused. Vitest coverage:
  `tests/web/virtualizer.test.js`.
  - **Card-height invariant** (TASK-033, also in CLAUDE.md): all track cards MUST render at
    the same fixed height. The visible-window math is O(1) from `itemHeight`; variable
    heights would force per-card measurement. In-list expand is forbidden — use modal /
    overlay instead.
  - **Sticky-layout invariant** (TASK-037, also in CLAUDE.md): when wiring is done, KEEP the
    scroll source at the document level. Do NOT switch to inner `overflow:auto` on
    `#track-list` — that would break `#tracks-sticky` (position:sticky anchored to
    documentElement) and the shadow-on-scroll IntersectionObserver. The Virtualizer's
    absolute-positioned spacer works fine with document-level scroll.

- **`_warmupPoll` IIFE** (TASK-029): polls `/api/warmup` every 2s while the sidecar cache
  hydrates; updates the new `#status-warmup` chip in `#app-status` to "Indexing N / M
  tracks" with locale-formatted counts; hides on `step === 'done'`. Started from
  `loadTracksFromServer` after the library load completes. Resilient to transient fetch
  errors. Vitest coverage: `tests/web/warmup-badge.test.js`.

- **Search-input debounce** (TASK-036): the previous 200ms `setTimeout` debounce on
  `#search-input` was replaced by an 80ms `requestIdleCallback`. `_scheduleSearchRecompute`
  uses `requestIdleCallback({timeout: 80})` where available and falls back to
  `setTimeout(80)` (e.g. older Safari, jsdom). Coalesces multiple keystrokes into one
  filter recompute. Vitest coverage: `tests/web/search-debounce.test.js`.

- **`#status-warmup` chip**: lives inside `#app-status` next to `#status-rb`. Hidden by
  default (`style="display:none"`); `_warmupPoll` toggles `display` and updates
  `#warmup-progress-text`. Has its own `.status-warmup-sep` separator that hides/shows in
  lockstep.

- **AutoCue Perf in production browser**: ops can flip `localStorage.autocue_perf = '1'` in
  the DevTools console + reload — no server flag required. The console log lines are the
  primary observability surface; `_perf.getEntries()` returns the raw
  `performance.getEntriesByType('measure')` array filtered to AutoCue marks.

## AutoCue 2.0 global layer (P1) — `docs/js/v2/`

- **ES modules only**, imported by `docs/js/v2/main.js` (`<script type="module">`).
  They read legacy state ONLY via `window.ACBridge` (`tracks()`, `healthSummary()`,
  `isLocalMode()`, `selectedCount()` — accessor closures over the classic scripts'
  top-level `let`, defined at the end of `08-set-builder-boot.js`) and listen for two
  CustomEvents the legacy code dispatches: `autocue:health-summary` (in
  `_renderHealthSummary`) and `autocue:local-mode` (in the `detectLocalMode` local-mode
  branch). v2 exposes its surface on `window.AC2.*`. Legacy never imports v2.
- **status-sentence.js**: the `#app-status` facts are now `<button>`s. Pure
  `deriveFacts({tracks, healthSummary})` → `[{needcues}, {health}]` (need-cues =
  `existingHotCues === 0` count, visible once tracks load even at 0; health hidden until
  the first scan, `Math.round(library_score)`). Repaints on `AppState.subscribe('tracks')`
  + `autocue:health-summary`. Polls `GET /api/status?include_rb=1` every 30 s (opt-in
  backend field — default status stays cheap for the 600 ms `detectLocalMode` budget) and
  feeds the EXISTING `updateAppStatus({rekordboxRunning})` renderer. Local mode only.
- **palette.js** (⌘K): opens on ⌘K/Ctrl+K, `/` (when not typing), `#cmdk-hint-btn`; gated
  on `ACBridge.isLocalMode()`. **Strict key priority via a CAPTURE-phase document keydown
  that stopPropagation()s every key while open** — the legacy app shortcuts (and
  Discover's, all bubble-phase) never double-fire. Commands (`commands.js`) delegate to
  existing buttons via `.click()` so every guard fires; track search caps 8 with mono
  `BPM · key` meta. `fuzzy.js` ranks. Empty results render the **inert "Ask AutoCue
  (coming soon)" composer hint** — the seam for the future opt-in `AUTOCUE_LLM` phase
  (program PRD §6/P6); the input + empty-state contract IS that API.
- **e2e**: any new interactive control with an id MUST enter
  `tests/e2e/control-inventory.json` (globalControls) or the drift guard fails; dialog
  internals (e.g. `pal-input`) go in the spec's ignore list. Dynamic `pal-opt-N` option
  buttons exist only while the palette is open (absent during the closed-state scan).
- **Duplicates place (P3)** — `docs/js/v2/workbench/duplicates.js`. A rail *place*
  (`#wb-dupes-place`) that swaps the workbench centre pane from the grid to
  `#wb-dupes-pane`: `activate()` toggles `hidden` on `#tracks-sticky`/`#track-list`/
  `#wb-grid-head`/`#wb-inspector` + adds `body.wb-place-dupes`; `deactivate()` reverses
  and calls `ACBridge.renderTracks()`. **The grid is never detached** (Virtualizer +
  sticky invariants, TASK-033/037) — a CSS `display:none !important` under
  `body.wb-place-dupes` backstops the `hidden` attribute against a legacy
  `style.display` write. Crate/playlist/saved-filter clicks and workbench-off all
  exit the place first; a place owning the centre paints no crate `.active`
  (`autocue:wb-place-change` event → `_renderCrates`). Delegation-only: every scan +
  write goes through `window.ACBridge`; the module never touches `/api/duplicates*`.
  Lazy first-scan on open (the `#duplicates-*` hosts moved INTO the pane in T3, so the
  legacy `scanDuplicates`/confirm-modal/undo-banner keep working by id). Restore is the
  canonical **status-sentence sheet** (`docs/js/v2/restore-sheet.js`): the
  `autocue:duplicates-deleted` event reveals the `#status-restore` fact, which opens
  `#wb-restore-sheet` (`position:fixed`, JS-anchored under the right-aligned fact) to
  POST `/api/restore`; expires with the 30s backup window. JSDOM can't see the swap's
  layout or the sheet anchoring — `tests/e2e/v2-duplicates-place.spec.ts` covers those
  (it mocks `/api/duplicates` to an instant "0 groups" SSE so the lazy scan doesn't
  saturate the single sandbox server across sequential tests).
- **Discover place (P5)** — `docs/js/v2/workbench/discover.js`. A rail *place*
  (`#wb-disc-place`, Maintenance group) that swaps the workbench centre pane from the
  grid to the restyled Discover feed. **Unlike Duplicates** (which toggles `hidden` on
  a sibling pane inside the SAME tab body), Discover lives in a DIFFERENT tab content
  block (`#discover-tab-content`) and MUST be shown via `switchTab('discover')` —
  because the legacy `_handleDiscoverKeydown` gates its `j/k/Enter/s/x/z/D/?/Esc` map on
  `#disc-v2-section.offsetParent !== null`, and `initDiscoverV2` re-parents overlays to
  `<body>` for `position:fixed`; relocating `#disc-v2-grid` would break both. `activate()`
  guards local mode, deactivates Duplicates first (mutual exclusion), `clearInspector()`,
  `switchTab('discover')`, hides `#tracks-sticky`/`#track-list`/`#wb-grid-head` + the
  inspector, adds `body.wb-place-disc`, lazy-loads initial state once. `deactivate()`
  `switchTab('cues')` (accepted scroll-to-top on the grid-return leg — switchTab is
  mandatory for the keyboard-guard invariant, so symmetric reuse beats reimplementing its
  five side-effects) + `renderTracks()`. **Delegation-only**: re-drives the frozen
  `DiscoverV2` IIFE via `window.DiscoverV2` + `ACBridge.discover*`; never fetches the
  Discover REST surface. Release detail re-hosts in the **inspector** (mode flag
  `'track'|'release'` in `inspector.js`): focusing a card → `renderReleaseInspector(key)`
  builds a header + mono data chips and reuses the legacy `_renderDetailBody`
  (`window._renderDiscoverRenderDetail`) by relocating the canonical `#disc-v2-detail-body`
  node into the inspector (restored on `clearInspector()`); the legacy slide-in
  `_openDetailPanel` early-returns to `focusRelease` when the place is active (the flag-off
  path still gets the slide-in). Esc clears the focused release. The Discover/Duplicates
  places are **mutually exclusive** — only one owns the centre. Aliveness r2: save-pop
  pulse + scan-bar/spinner/crossfade all reduced-motion-gated. ⌘K `go-discover`/`find-releases`
  force the workbench on + click `#wb-disc-place`. **`#tab-discover` is retired** (P5) — the
  `switchTab('discover')` map entry + `#discover-tab-content` survive (the place uses them);
  with P2's Cues+Library retirement, **all three legacy tabs are gone**. JSDOM can't see the
  swap/scroll/sticky-pin — `tests/e2e/v2-discover-shell.spec.ts` (mocks every
  `/api/discover/*` + `/api/youtube/search`) covers those + both themes + reduced-motion + R10.
