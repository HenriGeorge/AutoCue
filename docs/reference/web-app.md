# Web App UI

Technical reference for the AutoCue web app — the entire single-page UI that
lives at [`docs/index.html`](../index.html).

This document is for developers working on the UI. End-user feature
descriptions live in [`docs/FEATURES.md`](../FEATURES.md). For the API surface
the JS calls into, see [`rest-api.md`](./rest-api.md).

## Table of Contents

- [1. Overview](#1-overview)
- [2. Two modes](#2-two-modes)
- [3. Mode detection — `detectLocalMode()`](#3-mode-detection--detectlocalmode)
- [4. Three tabs — Cues / Library / Discover](#4-three-tabs--cues--library--discover)
- [5. Tab-specific panels](#5-tab-specific-panels)
- [6. Track card structure](#6-track-card-structure)
- [7. AppState pub/sub bus](#7-appstate-pubsub-bus)
- [8. `filteredTracks()` — the universal filter](#8-filteredtracks--the-universal-filter)
- [9. `pendingCues` — preview state](#9-pendingcues--preview-state)
- [10. `_cardMap` — smart diffing & FLIP reorder](#10-_cardmap--smart-diffing--flip-reorder)
- [11. IntersectionObservers — lazy enrichment](#11-intersectionobservers--lazy-enrichment)
- [12. Mini player + RAF playhead](#12-mini-player--raf-playhead)
- [13. Mini waveform canvas](#13-mini-waveform-canvas)
- [14. `_energyCache` — energy curves for the mini waveform](#14-_energycache--energy-curves-for-the-mini-waveform)
- [15. `_explainCue(cue)` — the cue badge ℹ panel](#15-_explaincuecue--the-cue-badge--panel)
- [16. `_consumeSSE(response, onEvent, signal)` — SSE reader](#16-_consumesseresponse-onevent-signal--sse-reader)
- [17. `_esc()` — HTML escaping for server-supplied strings](#17-_esc--html-escaping-for-server-supplied-strings)
- [18. Sticky filter bar — `#tracks-sticky`](#18-sticky-filter-bar--tracks-sticky)
- [19. Sticky action bar — `#action-bar` (selection) + `#download-bar` (legacy)](#19-sticky-action-bar--action-bar-selection--download-bar-legacy)
- [20. Status row — `#app-status`](#20-status-row--app-status)
- [21. Theme variables — CSS custom properties](#21-theme-variables--css-custom-properties)
- [22. Multi-select backup delete](#22-multi-select-backup-delete)
- [23. Fetch error handling](#23-fetch-error-handling)
- [24. Example flow — Apply phrase cues in local mode](#24-example-flow--apply-phrase-cues-in-local-mode)
- [25. Testing](#25-testing)
- [26. Related references](#26-related-references)

---

## 1. Overview

The web app is a **single self-contained HTML file** at `docs/index.html`
(~8 000 lines). It bundles its CSS, HTML markup, and JavaScript inline so it
can be hosted on GitHub Pages as static files with no build step and no
framework — just vanilla DOM APIs, `fetch`, and `<script>`.

| Section            | `docs/index.html` lines | Purpose                                     |
| ------------------ | ----------------------- | ------------------------------------------- |
| `<style>` block    | 1–1450                  | All CSS (theme vars, layout, animations)    |
| `<body>` markup    | 1450–2700               | Tabs, panels, drop zones, modals            |
| `<script>` block   | 2700–7968               | Mode detection, render loop, AppState, SSE  |

The only external runtime dependency is **jsmediatags** (CDN, `index.html:7`)
used to extract embedded artwork from dropped audio files in XML mode. If the
CDN fails the script sets `window.jsmediatags = null` and the rest of the app
continues to work without artwork.

The file is shipped two ways:

1. **GitHub Pages** — the static `docs/` directory is served as a website.
   This is the *XML mode* (default) — the user uploads a `rekordbox.xml`,
   parses it in-browser, generates cues client-side, and downloads a modified
   XML for re-import.
2. **Local server** — `autocue serve` mounts `docs/` and exposes
   `/api/...` endpoints at `localhost:7432`. The same `index.html` detects
   the API and unlocks server-backed panels: set builder, transition scoring,
   similar tracks, library health, auto-tag, comment enrichment, Discogs
   genre tagging, playlist suggestions, cue tools, discovery, and YouTube
   download.

Because there is no build step, **all UI code changes go to
`docs/index.html`** — there is no JSX, no module bundler, no transpiler.
`package.json` exists only to install Vitest + jsdom for the test suite in
`tests/web/`.

---

## 2. Two modes

### XML mode (default — GitHub Pages)

| Element              | Behaviour                                          |
| -------------------- | -------------------------------------------------- |
| `#drop-zone`         | Visible. User drops `rekordbox.xml`.               |
| Apply button         | `#download-btn` reads "Download XML".              |
| `#audio-drop-zone`   | Visible — user can drop audio files for playback.  |
| `#anlz-drop-zone`    | Visible — user can drop the analysis folder for phrase mode. |
| Server-only panels   | Hidden (`#health-section`, `#setbuilder-section`, `#discogs-section`, etc.). |
| `parsedDoc`          | The parsed XMLDocument is kept in memory so apply rewrites the same DOM. |

The flow:

1. User drops `rekordbox.xml`. `handleFile()` (`index.html:6342`) reads it as
   text, runs `parseRekordboxXml()`, and populates `parsedTracks` /
   `parsedDoc`.
2. The user adjusts cue settings; `AppState.signal('settings')` triggers
   `renderTracks()`.
3. Clicking "Download XML" calls `buildOutputXml()` (`index.html:6283`),
   which clones `parsedDoc`, mutates `POSITION_MARK` elements per track, and
   serializes back to a `Blob` for download as `autocue_import.xml`.

XML parsing and cue generation are pure functions and have full Vitest
coverage in [`tests/web/xml-processing.test.js`](../../tests/web/xml-processing.test.js).

### Local mode (`autocue serve`)

| Element              | Behaviour                                          |
| -------------------- | -------------------------------------------------- |
| `#drop-zone`         | Hidden. `#local-mode-banner` shown inside `#upload-section`; `#app-status` row populated. |
| `#tab-nav`           | Visible — exposes Cues / Library / Discover tabs.  |
| `#app-status`        | Status row in `#top-bar` — DB connected · N tracks · scan age · Rekordbox state. |
| `#action-bar`        | Sticky bottom selection bar — slides in when ≥1 track is selected. |
| Apply button         | `#download-btn` reads "Apply to Rekordbox".        |
| `#preview-cues-btn`  | Visible — calls `/api/generate` and stores result in `pendingCues`. |
| `#delete-cues-btn`   | Visible — wipes all hot cues on filtered tracks.   |
| `#color-by-bpm-btn`  | Visible — assigns Rekordbox track colors by BPM.   |
| Server panels        | Mounted (Library Health, Set Builder, Auto-Tag, etc.). |
| `parsedDoc`          | `null`. The DB is the source of truth.             |
| `track.id`           | Database row ID (integer, stringified).            |

The flow:

1. `detectLocalMode()` runs on page load.
2. If `connected: true`, the script flips visibility on the local-mode-only
   panels and calls `loadTracksFromServer()` (`index.html:2616`).
3. `loadTracksFromServer()` fetches `/api/tracks` and `/api/status` in
   parallel, fills `parsedTracks` with normalized rows, then signals
   `AppState.signal('tracks')`.
4. Apply, Delete, Color, Preview, Health, Set Builder, Auto-Tag, and all
   other write paths POST to the server. The server makes a DB backup on
   every write (see [`backup-and-restore.md`](./backup-and-restore.md)).

The two modes are **mutually exclusive at boot** — once `localMode` is set,
the upload section never re-enables.

---

## 3. Mode detection — `detectLocalMode()`

Defined at `index.html:2608`:

```js
async function detectLocalMode() {
  try {
    const r = await fetch('/api/status', { signal: AbortSignal.timeout(600) });
    if (r.ok) { const d = await r.json(); return d.connected === true; }
  } catch {}
  return false;
}
```

Three properties:

- **600 ms timeout** via `AbortSignal.timeout(600)`. The GitHub Pages
  deployment can never reach a server, so we want to fail fast and stay in
  XML mode without blocking the UI.
- **Requires `connected: true`** — a 200 OK is not enough. If the server is
  running but the Rekordbox DB is missing or locked, the response has
  `connected: false` and the UI stays in XML mode.
- **Errors are swallowed.** Any exception (timeout, CORS, ECONNREFUSED) falls
  through to `return false` — there is never a "Failed to load mode" toast
  on the GitHub Pages build.

The returned boolean is assigned to the module-level `localMode` flag
(`index.html:2603`) at `detectLocalMode().then(connected => { localMode = connected; ... })`
(`index.html:4015`).

---

## 4. Three tabs — Cues / Library / Discover

The tab bar (`#tab-nav`) is hidden by default and only shown when
`localMode === true` (`index.html:4018`). Tabs are coordinated by a small
map and a `switchTab(name)` helper:

```js
// index.html:3988
const TAB_CONTENTS = {
  cues:     'cues-tab-content',
  library:  'library-tab-content',
  discover: 'discover-tab-content',
};
function switchTab(name) {
  Object.entries(TAB_CONTENTS).forEach(([tab, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (tab === name) {
      el.style.display = '';
      el.classList.remove('tab-entering');
      void el.offsetWidth; // force reflow to restart animation
      el.classList.add('tab-entering');
    } else {
      el.style.display = 'none';
    }
  });
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.toggle('active', b.id === 'tab-' + name);
  });
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
```

Things to note:

- The `void el.offsetWidth` line is intentional — it forces a synchronous
  layout flush so removing then re-adding `.tab-entering` actually restarts
  the CSS keyframe animation. Without the reflow read, the browser
  short-circuits the class change and the entrance animation never replays.
- The smooth `scrollTo` is fired on every tab change so the user always lands
  at the top of the new content, even when the previous tab scrolled deep.
- Tab buttons are bound at startup (`index.html:4011–4013`).
- Keyboard shortcuts `1`, `2`, `3` switch tabs from the global key handler
  (`index.html:6657`).

---

## 5. Tab-specific panels

| Tab        | Section IDs (DOM)                                                                                                    |
| ---------- | -------------------------------------------------------------------------------------------------------------------- |
| Cues       | `#upload-section`, `#audio-drop-section`, `#anlz-drop-section`, `#analysis-mode-bar`, `#settings-section`, `#tracks-section`, `#how-to` / `#local-how-to` |
| Library    | `#health-section`, `#cue-tools-section`, `#setbuilder-section`, `#playlist-suggest-section`, `#auto-tag-section`, `#comment-enrich-section`, `#discogs-section`, DJ Mixing Guide |
| Discover   | `#discover-section` (new releases via Discogs), `#download-section` (yt-dlp)                                          |

The Cues tab is the only one shown in XML mode. The other two only have
content when `localMode === true` — most of their `<section>` markup is
emitted with `style="display:none"` and revealed by `index.html:4031–4067`
during the local-mode bootstrap.

---

## 6. Track card structure

Track cards are produced by `buildTrackCard(track, cues, willSkip, opts)`
(starts near `index.html:5466`). Each card is a `<div class="track-card">`
with `data-track-id` set so the FLIP / observer logic and click handlers can
look it up. The general structure:

```
.track-card[data-track-id=<id>]
├── .track-card-top
│   ├── .artwork-box
│   │   ├── <img class="artwork-img" src="/api/tracks/<id>/artwork">  (local mode only)
│   │   └── .art-play-overlay  (Play / Pause SVG, fires togglePlayTrack)
│   ├── .track-card-main
│   │   ├── header row
│   │   │   ├── <input.track-select-cb type=checkbox>  (toggles selectedTrackIds)
│   │   │   ├── .track-title  (name)
│   │   │   ├── .track-artist
│   │   │   ├── .bpm-badge
│   │   │   ├── .key-badge   (Camelot)
│   │   │   ├── .duration
│   │   │   ├── .rating-stars (★★★★)
│   │   │   ├── .play-count   (▶ N)
│   │   │   ├── .last-played
│   │   │   └── .my-tag-pill * N
│   │   ├── .cue-badges  (ABCDEFGH + memory cue + ℹ explain panel)
│   │   ├── .energy-sparkline  (rendered lazily by _sparkObserver)
│   │   ├── .mix-score-chip[data-track-id]  (mixability, observed)
│   │   ├── .category-chip[data-track-id]   (warmup/build/peak/…)
│   │   ├── .track-timeline  (primary cue positions)
│   │   └── .preview-timeline  (secondary — when pendingCues[id] exists)
│   └── .skipped-badge  (when willSkip is true)
└── .similar-panel  (collapsed; populated on demand by _toggleSimilarPanel)
```

Key data sources:

- **Album art** — `/api/tracks/{id}/artwork` in local mode; the artwork
  comes from a parsed audio file (via `jsmediatags`) in XML mode.
- **Energy sparkline** — fetched lazily via `_sparkObserver` when the card
  scrolls into the viewport (`index.html:6250`). The fetched curve is
  cached in `_energyCache[trackId]` so the mini-waveform can reuse it.
- **Mixability / category chips** — same lazy observer pattern via
  `_mixObserver` (`index.html:6263`) hitting `/api/tracks/{id}/mixability`
  and `/api/tracks/{id}/classification`.
- **Cue badges** — every badge has an ℹ panel that calls `_explainCue()`
  to render a human-readable explanation (see [`_explainCue(cue)`](#15-_explaincuecue--the-cue-badge--panel)).

---

## 7. AppState pub/sub bus

`AppState` is a module-level IIFE at `index.html:2553`:

```js
var AppState = (function() {
  var _subs    = new Map();    // key → Set<fn>
  var _dirty   = new Set();
  var _pending = null;

  function subscribe(key, fn) {
    if (!_subs.has(key)) _subs.set(key, new Set());
    _subs.get(key).add(fn);
    return function() { var s = _subs.get(key); if (s) s.delete(fn); };
  }

  function _flush() {
    _pending = null;
    var toCall = new Set();
    _dirty.forEach(function(k) {
      var s = _subs.get(k);
      if (s) s.forEach(function(fn) { toCall.add(fn); });
    });
    _dirty.clear();
    toCall.forEach(function(fn) {
      try { fn(); } catch (e) { console.error('[AppState] subscriber error', e); }
    });
  }

  function signal(key) {
    _dirty.add(key);
    if (!_pending) _pending = Promise.resolve().then(_flush);
  }

  return { subscribe: subscribe, signal: signal };
})();
```

Properties to keep in mind when extending it:

- **Coalescing via microtask.** `signal()` schedules at most one
  `_flush` per tick via `Promise.resolve().then(_flush)`. Multiple
  `signal('filters')` calls inside the same synchronous flow produce a
  single re-render. The "Clear filters" button at `index.html:4485` mutates
  six filter state variables back-to-back and only the final
  `AppState.signal('filters')` matters — there is no flicker.
- **Subscriber-level dedup.** `_flush` builds a `Set<fn>` so a subscriber
  that registered for two keys signaled in the same tick fires once.
- **`subscribe()` returns an unsubscribe function.** Currently no code uses
  it, but the return value is preserved for symmetry — feel free to
  unsubscribe if you add a panel that registers/un-registers dynamically.
- **Exceptions are swallowed (logged).** A buggy subscriber cannot break
  the others. This is enforced by the `try/catch` in `_flush`.
- **Three keys are in use:**

  | Key        | Fired by                                                  | Subscriber action                          |
  | ---------- | --------------------------------------------------------- | ------------------------------------------ |
  | `filters`  | Search, sort, rating/plays/last-played/tag/key/genre toggles, select all, clear filters, playlist filter change | `renderTracks()` (`index.html:6684`)        |
  | `settings` | `bars-interval`, `start-bar`, `max-cues`, `skip-existing-cues` inputs (`index.html:6737`) | `renderTracks()` + `updateOverwriteWarning()` |
  | `tracks`   | `loadTracksFromServer()` after the library is replaced (`index.html:2699`), `handleFile()` after XML parse | `renderTracks()`                            |

- **AppState has its own jsdom test.** A standalone `makeAppState()` helper
  in [`tests/web/ui-logic.test.js`](../../tests/web/ui-logic.test.js)
  duplicates the production logic; update it if you change the bus.

---

## 8. `filteredTracks()` — the universal filter

`filteredTracks()` at `index.html:5085` is the **only** function that
combines all client-side filters into a single ordered list. Every render
path and every write path goes through it.

```js
function filteredTracks() {
  let tracks = parsedTracks;
  if (phraseOnlyFilter) tracks = tracks.filter(t => t.hasPhrase);
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    tracks = tracks.filter(t =>
      (t.name || '').toLowerCase().includes(q) ||
      (t.artist || '').toLowerCase().includes(q)
    );
  }
  if (ratingFilter > 0) tracks = tracks.filter(t => t.rating >= ratingFilter);
  if (playsFilter === 'played')   tracks = tracks.filter(t => t.playCount > 0);
  else if (playsFilter === 'unplayed') tracks = tracks.filter(t => t.playCount === 0);
  if (lastPlayedFilter !== 'all') {
    if (lastPlayedFilter === 'never') {
      tracks = tracks.filter(t => !t.lastPlayed);
    } else {
      const days   = lastPlayedFilter === '7d' ? 7 : 30;
      const cutoff = new Date(Date.now() - days * 86400000).toISOString();
      tracks = tracks.filter(t => t.lastPlayed && t.lastPlayed >= cutoff);
    }
  }
  if (myTagFilters.size > 0)   tracks = tracks.filter(t => (t.myTags || []).some(tag => myTagFilters.has(tag)));
  if (selectedKeys.size > 0)   tracks = tracks.filter(t => t.key && selectedKeys.has(t.key));
  if (genreFilters.size > 0)   tracks = tracks.filter(t => genreFilters.has(t.genre || ''));
  return tracks;
}
```

Important invariants:

- **`parsedTracks` is never mutated by filters.** Filtering returns a new
  array; the source of truth stays intact. Re-running with a different
  filter never throws away data.
- **All write operations call `filteredTracks()`** — Apply, Color,
  Delete, Preview, Enrich Comments, Auto-Tag, etc. Bulk delete via the
  selection checkboxes is layered on top via `activeTracks()`
  (`index.html:5115`), which intersects `filteredTracks()` with
  `selectedTrackIds`. There is no code path that reaches the server with
  hidden tracks.
- **Order matters.** `phraseOnlyFilter` runs first because it's the most
  selective on a typical library; the OR-logic `genreFilters` runs last so
  the cheap text search short-circuits early.
- **`filteredTracks()` is fully covered by `ui-logic.test.js`** including
  the cross-filter matrix (search ∩ rating ∩ plays ∩ last-played ∩ tags ∩
  keys ∩ genres).

Sorting is applied by `sortedTracks()` (`index.html:5191`), which wraps
`filteredTracks()` with the active sort.

---

## 9. `pendingCues` — preview state

`pendingCues` is a plain object at `index.html:2534`:

```js
let pendingCues = {};   // String(trackId) → [{ slot, posSec, label, isPhrase, name, ... }]
```

It is populated by the "Preview cues" button (`#preview-cues-btn`,
local mode only):

1. The handler at `index.html:6790` calls `/api/generate` with the active
   filter selection.
2. Each track's resulting cues are mapped into the preview shape and stored
   under `pendingCues[String(tr.id)]` (`index.html:6810`).
3. `renderTracks()` reads `pendingCues[String(track.id)]` (`index.html:5871`)
   and renders a secondary `.preview-timeline` on each card alongside the
   primary timeline. The preview timeline is visually distinct
   (dashed/translucent in CSS) so the user can compare "what's there now"
   vs. "what we'd write".

`pendingCues` is cleared in three places:

- After Apply completes (`index.html:3958`).
- When the playlist filter changes (`index.html:4124`).
- When a fresh XML is dropped (and `parsedDoc` is replaced).

The map key is **always `String(trackId)`** — the same convention as
`healthData` and `_cardMap`. Mixing integer vs string keys here will cause
silent rendering misses.

---

## 10. `_cardMap` — smart diffing & FLIP reorder

`_cardMap` is a `Map<trackIdString, HTMLElement>` cache shared between flat
and album-grouped renders (`index.html:2594`). Its purpose: avoid rebuilding
the entire `<div id="track-list">` when only the sort order or a filter
changes.

### Fingerprint

```js
// index.html:5899
function _computeSettingsFingerprint() {
  var s = getSettings();
  var skipExisting = document.getElementById('skip-existing-cues').checked;
  var mcMode = document.getElementById('memory-cue-mode').value;
  // Sum total phrase cue count (not just key count) so different cue positions force a rebuild
  var phraseTotal = Object.values(phraseCueState).reduce(function(a, arr) { return a + arr.length; }, 0);
  return s.barsInterval + '|' + s.startBar + '|' + s.maxCues + '|' + skipExisting + '|'
       + mcMode + '|' + analysisMode + '|' + phraseTotal + '|'
       + Object.keys(pendingCues).length + '|' + Object.keys(healthData).length;
}
```

If the fingerprint differs from `_cardSettingsFingerprint`, every cached
card is stale (because the cue layout, memory-cue mode, or pending preview
changed), so `_cardMap.clear()` is called and every card is rebuilt from
scratch.

When the fingerprint matches but the **sort order** changed,
`renderTracks()` runs the FLIP reorder path: it snapshots
`getBoundingClientRect().top` of every reused card *before* DOM changes,
calls `list.replaceChildren(...newCards, ...exitingCards)`, then runs a
single batched read of post-move positions and applies
`card.animate([{ transform: translateY(Δ) }, { transform: translateY(0) }], …)`
to each moved card (`index.html:6189`). **All reads are batched before any
animate() calls** to avoid forced reflow on each iteration.

### Enter / exit animations

- New cards: `.card-enter` class added with staggered `animationDelay`
  (max 8 immediate cards; the rest are revealed by `_enterObserver` as they
  scroll in).
- Exiting cards: `.card-exit` class triggers a CSS exit animation; the card
  is removed on `animationend` (with a 300 ms timeout fallback so
  forever-pending animations cannot leak DOM).
- **Large-exit threshold:** if more than 30 cards are exiting at once,
  animations are skipped entirely (`exitCount <= 30`,
  `index.html:6133`). This is to prevent a catastrophic DOM-keepalive burst
  when the user clears a filter or types a one-character search that
  removes 1 000 cards from view.

### Cache invalidation

`_cardMap.clear()` and `_cardSettingsFingerprint = ''` are called on:

- Library reload via `loadTracksFromServer()` (`index.html:2643`).
- XML drop via `handleFile()` (`index.html:6354`).
- Empty render fallbacks (no tracks / no matches) so a future "back to
  results" renders fresh cards.

---

## 11. IntersectionObservers — lazy enrichment

Three module-level observers, all re-created (after `.disconnect()`) every
`renderTracks()` call. The reason they are stored on the module instead of
re-created locally per card is to **prevent leaks** — an observer with no
explicit `disconnect()` will keep its callback alive forever holding
references to detached DOM nodes.

```js
// index.html:2597
var _sparkObserver = null;   // energy sparkline canvas
var _mixObserver   = null;   // mixability chip + category chip
var _enterObserver = null;   // card enter animation for items > 8
```

Pattern (all three are identical):

```js
if (_sparkObserver) { _sparkObserver.disconnect(); }
_sparkObserver = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (entry.isIntersecting) {
      _sparkObserver.unobserve(entry.target);  // one-shot
      _renderEnergySparkline(entry.target);
    }
  }
}, { rootMargin: '200px' });
for (const el of list.querySelectorAll('.energy-sparkline')) {
  _sparkObserver.observe(el);
}
```

`rootMargin: '200px'` pre-loads the next ~3 cards so by the time they enter
the viewport, the API response has already returned. The observers
self-disconnect targets after they fire (`unobserve(entry.target)`) so each
chip / sparkline only triggers a fetch once.

---

## 12. Mini player + RAF playhead

The mini player lives inside `#download-bar` (`index.html:2392`). It is
hidden until `playTrack()` is called.

### `_startPlayRaf` / `_stopPlayRaf`

The playhead is driven by `requestAnimationFrame` — **not** the audio
element's `timeupdate` event, which fires only ~4 times per second and
results in a visibly stuttering playhead.

```js
// index.html:4692
function _startPlayRaf() {
  if (_playRafId) return;
  function _rafTick() {
    if (audioPlayer.paused || !nowPlayingId) { _playRafId = null; return; }
    updateTimeline();
    _drawMiniWaveform(nowPlayingId);
    _playRafId = requestAnimationFrame(_rafTick);
  }
  _playRafId = requestAnimationFrame(_rafTick);
}
function _stopPlayRaf() {
  if (_playRafId) { cancelAnimationFrame(_playRafId); _playRafId = null; }
}
```

Key invariants:

- **Started inside `audioPlayer.play().then(...)`** at `index.html:4773` —
  never before. The browser may reject `play()` (autoplay policy,
  inaudible-but-failed load), and starting RAF before `.then()` resolves
  would tick on a paused player.
- **`_stopPlayRaf()` runs on `pausePlayback()`, on `ended`, and on every
  scrub end.** The loop also self-terminates when it observes
  `audioPlayer.paused || !nowPlayingId`, so a stale RAF id from a closed
  tab is safe.
- **The `.timeline-playhead` element has NO CSS transition.** Because RAF
  runs at the display refresh rate (typically 60 fps), a CSS transition
  would visibly lag behind the actual playhead position. The playhead is
  updated by JS every frame; the browser composes that as smoothly as a
  transition would.

---

## 13. Mini waveform canvas

The mini waveform is a small `<canvas id="mini-waveform">` (120×22 CSS px)
overlaid by an invisible `<input type="range" id="mini-scrubber">` for
seek interaction. It is wrapped in `#mini-waveform-wrap`.

```css
/* index.html:848 */
#mini-waveform-wrap {
  position: relative; width: 120px; height: 22px;
  clip-path: inset(0 round 4px); cursor: pointer;
  background: var(--surface2);
}
#mini-waveform   { display: block; width: 120px; height: 22px; image-rendering: pixelated; }
#mini-scrubber   {
  position: absolute; inset: 0; opacity: 0;
  width: 100%; height: 100%; cursor: pointer;
  margin: 0; padding: 0;
  -webkit-appearance: none; appearance: none;
}
```

Two non-obvious tricks:

- **`clip-path: inset(0 round 4px)` instead of `overflow: hidden`.**
  `overflow: hidden` on the wrap would intercept pointer events for the
  rounded corners, defeating the invisible range slider on top. `clip-path`
  clips the visual without touching hit-testing.
- **HiDPI scaling via `devicePixelRatio`.** `_drawMiniWaveform()` at
  `index.html:4707` sets the canvas's *physical* pixel dimensions to
  `cssW * dpr` × `cssH * dpr` and uses `ctx.setTransform(dpr, 0, 0, dpr, 0, 0)`
  so the drawing API uses CSS-pixel units while the bitmap is high-res.
  The size is only assigned when it actually changes, so resizing on every
  RAF tick is a no-op.

The render is one of two states:

1. **Cached curve.** If `_energyCache[trackId]` exists, draw N bars
   (one per curve sample) and color the bars before the playhead green,
   the bars after the playhead with the theme's faint colour.
2. **No curve yet.** Fall back to a simple green progress bar (`fillRound`)
   so the mini player is always visually responsive even before
   `/api/tracks/{id}/energy` returns.

A vertical playhead line is drawn on top in both cases.

While the user is scrubbing (`isScrubbing === true`), `_drawMiniWaveform()`
early-returns. This prevents the RAF redraw from overwriting the scrubber
position the user is dragging.

---

## 14. `_energyCache` — energy curves for the mini waveform

```js
// index.html:2592
let _energyCache = {};   // trackId → Float32Array (or array) of energy curve
```

Populated in two places:

- `_renderEnergySparkline()` (`index.html:5445`) — fired by
  `_sparkObserver` as cards enter the viewport. The fetched curve is both
  rendered into the card SVG and cached in `_energyCache`.
- Implicitly via `_drawMiniWaveform()` — reads from `_energyCache` only;
  it does not fetch.

The cache is **cleared on**:

- Library reload (`loadTracksFromServer`, `index.html:2642`).
- XML drop (`handleFile`, `index.html:6353`).

So when the user switches playlists or imports a new XML, stale curves
cannot leak into the new library's mini player.

---

## 15. `_explainCue(cue)` — the cue badge ℹ panel

`_explainCue()` at `index.html:5212` produces a short
`{ confidence, reasons[] }` tuple describing why each cue is at its
position. It powers the ℹ tooltip that the user can click on any cue badge
to understand AutoCue's reasoning.

It handles five modes:

| Mode         | Trigger                                                           | Example reasons[]                                                                 |
| ------------ | ----------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `memory`     | `cue.slot === -1`                                                 | "CDJ load point (Auto Cue)", "Anchored to earliest phrase boundary"               |
| `manual`     | `cue.confidence == null && cue.phraseMode == null`                | "Manually placed cue"                                                             |
| `heuristic`  | Inferred when `confidence < 0.5` or `phraseMode === 'heuristic'`  | "No BPM or phrase data — 30-second interval estimate", "Position: …"             |
| `bar`        | `phraseMode === 'bar'`                                            | "Using bar intervals — switch to Phrase mode…" or "Bar-interval fallback (no Rekordbox phrase analysis)" |
| `phrase`     | `phraseMode === 'phrase'` or `confidence >= 0.9`                  | "Rekordbox phrase: Chorus (high-energy section)", "16-bar phrase", "Priority slot: main drop" |

The confidence label is one of `'High'` (≥ 0.9), `'Medium'` (≥ 0.5),
`'Low'`, `'Auto'` (memory cue), or `'—'` (manual).

The phrase reasons are looked up via `LABEL_REASONS` (`index.html:5256`),
which maps user-facing labels back to Rekordbox phrase names. The base name
is stripped of trailing numbers (e.g. `"Drop 2"` → `"Drop"`) before
lookup.

Slot A always gets the extra reason "Slot A: mix-in point (first non-Intro
phrase)" because of the smart-slot ordering invariant in `generator.py`
(see [`cue-generation.md`](./cue-generation.md)). Other high-priority
slots ("Drop", "Build", "Outro") also append a "Priority slot: …" reason.

All five modes are covered by `ui-logic.test.js`.

---

## 16. `_consumeSSE(response, onEvent, signal)` — SSE reader

POST requests cannot use the `EventSource` API, so the app uses
`fetch` + `ReadableStream`. The reader is centralised at `index.html:3433`:

```js
async function _consumeSSE(response, onEvent, signal) {
  const reader  = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  if (signal) {
    signal.addEventListener('abort', function() { reader.cancel().catch(function(){}); }, { once: true });
  }
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();        // last entry may be a partial line — keep for next chunk
    for (const line of lines) {
      if (!line.startsWith('data:')) continue;
      try { onEvent(JSON.parse(line.slice(5).trim())); } catch { /* ignore partial */ }
    }
  }
  if (signal && signal.aborted) throw new DOMException('Aborted', 'AbortError');
}
```

Consumers:

- `/api/generate-apply-stream` (Apply progress)
- `/api/color-tracks-stream` (Color by BPM progress)
- `/api/health` (Library health scan)
- `/api/cue-tools-stream` (Cue Tools batch operations)
- `/api/classify` (Auto-classification scan)
- `/api/enrich-comments/stream` (Comment enrichment progress)
- `/api/auto-tag/discogs` (Discogs genre tagging)
- `/api/discover` (New-release discovery)
- `/api/download`, `/api/download/album` (yt-dlp progress)

Important properties:

- **Newline split with re-buffered tail.** SSE events are line-delimited
  `data: {...}\n` blocks, but `decoder.decode(..., { stream: true })`
  may slice mid-line. The trailing `buf = lines.pop()` keeps the partial
  line for the next chunk.
- **JSON parse errors are swallowed.** If a chunk arrives with a
  half-event, the next chunk will complete it; in the meantime the
  `JSON.parse` throws and we skip it silently rather than abort the whole
  stream.
- **Abort propagation.** Callers can pass an `AbortSignal`; the reader
  will be cancelled on abort and a `DOMException('Aborted')` will be
  re-thrown after the loop exits so callers can show "Cancelled" feedback.

Every new SSE consumer in the app should reuse `_consumeSSE` rather than
re-implementing the reader loop.

---

## 17. `_esc()` — HTML escaping for server-supplied strings

`_esc()` at `index.html:3630` is the **only** safe path for interpolating
server-supplied (and Discogs / YouTube) text into HTML. It is used by:

- `_renderSuggestion(d)` for Discover cards (album / artist / styles / URL
  / Discogs links / format chips).
- The Set Builder alternatives panel (`alt.title`, `alt.artist`,
  `alt.genre`, `alt.key`).
- The download button's `data-query` payload.

Rule of thumb: **never interpolate Discogs Style names, YouTube titles,
[`DjmdContent`](./GLOSSARY.md#djmdcontent).Title/Artist, or anything else that originated outside the
app's own constants into HTML without `_esc`**. The function is small but
catches all five injection-relevant codepoints (`&`, `<`, `>`, `"`, `'`).

`_esc` is covered by `ui-logic.test.js` (`_renderSuggestion` and `_esc`
escaping suite).

---

## 18. Sticky filter bar — `#tracks-sticky`

The filter bar above the track list stays glued to the top of the page as
the user scrolls. Its CSS lives at `index.html:705`:

```css
#tracks-sticky {
  position: sticky; top: var(--top-bar-h, 0px); z-index: 100;
  background: var(--surface-card);
  /* bleed beyond main's 24px side padding so background is seamless edge-to-edge */
  margin-left: -24px; margin-right: -24px;
  padding: 16px 24px 0;
  border-radius: 16px 16px 0 0;
  transition: background 300ms ease, box-shadow 300ms ease,
              border-color 300ms ease, border-radius 300ms ease,
              backdrop-filter 300ms ease;
}
#tracks-sticky.shadowed {
  border-bottom: 1px solid var(--border);
  padding-bottom: 14px;
  background: color-mix(in srgb, var(--surface-card) 90%, transparent);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  box-shadow: 0 4px 20px rgba(0,0,0,.10);
  border-radius: 0;
}
```

Key choices:

- **Negative horizontal margins (`-24px`)** bleed the sticky bar out past
  `<main>`'s default 24 px side padding so the background is seamless
  edge-to-edge with no white gutters. Inner padding restores the content
  alignment. On narrow viewports the bleed value drops to `-16px` and
  `-12px` at the breakpoints set at `index.html:1326` and `:1335`.
- **`top: var(--top-bar-h, 0px)`** — the sticky position respects the
  variable height of `#top-bar`. That height is kept in sync by a
  `ResizeObserver` on the top bar (`index.html:7951`).
- **`.shadowed` toggled by a scroll listener.** `initStickyHeader()` at
  `index.html:7167` listens for `scroll` events, throttles via RAF, and
  toggles `.shadowed` based on whether the sticky bar's bounding rect is
  pinned at the top. This avoids `box-shadow` which would bleed
  horizontally beyond the negative margins; instead the shadowed state
  uses a `border-bottom`, a 90 % `color-mix` glass background, and a
  14 px backdrop blur for the frosted-glass look.

---

## 19. Sticky action bar — `#action-bar` (selection) + `#download-bar` (legacy)

Two bars live at the bottom of the viewport. They stack rather than
replace each other so the selection-driven CTA never hides the always-on
controls.

### 19.1 `#action-bar` — selection-driven (added in the UI refresh)

A fixed-position bar that slides in via `transform: translateY()` when
`selectedTrackIds.size > 0`. Driven from `updateSelectionBar()`:

```css
#action-bar {
  position: fixed; left: 0; right: 0; bottom: 0;
  z-index: 350; pointer-events: none;
  padding: 12px 20px calc(12px + env(safe-area-inset-bottom, 0px));
  --ab-rest-y: 0px;                       /* shifted up by 66px when #download-bar is also visible */
  transform: translateY(110%);
  transition: transform 240ms cubic-bezier(.2,.7,.2,1);
}
body:has(#download-bar.visible) #action-bar { --ab-rest-y: -66px; }
#action-bar.visible { transform: translateY(var(--ab-rest-y)); pointer-events: auto; }

@media (prefers-reduced-motion: reduce) {
  #action-bar { transition: none; }
}
```

Contents (left → right):

- **`#action-bar-count`** — `<strong>N</strong> selected`, with
  `font-variant-numeric: tabular-nums` and `toLocaleString()` for the
  thousands separator.
- **`#action-bar-clear`** — "Clear" link; clicks the existing
  `#deselect-all-btn`.
- **`#action-bar-preview`** — neutral secondary button "Preview Cues";
  proxies to `#preview-cues-btn` so the same `/api/generate` path runs.
- **`#action-bar-apply`** — primary green "Apply to Rekordbox"; proxies
  to `#download-btn` so the same backup + Rekordbox-running guards fire.

The buttons proxy via `.click()` rather than duplicating handlers, so
all existing wiring — backup creation, 409 toasts, success / error
animations — kicks in unchanged.

Body classes:
- `.has-action-bar` is set whenever the bar is visible. It lifts
  `#scroll-top-btn` 96 px so it doesn't collide with the bar.

### 19.2 `#download-bar` — always-on (legacy)

The pre-existing bottom bar is still in place and unchanged. It holds the
mini player + "Ready to import" summary + Color / Preview / Delete /
Apply buttons. When both bars are visible, `body:has(#download-bar.visible)`
shifts `#action-bar` 66 px up so they stack cleanly.

```css
#download-bar {
  position: fixed; bottom: 0; left: 0; right: 0; z-index: 200;
  background: rgba(255,255,255,.96); border-top: 1px solid var(--border);
  box-shadow: 0 -4px 20px rgba(0,0,0,.06);
  padding: 12px 24px;
  display: none; align-items: center; gap: 12px;
  flex-wrap: wrap; backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
}
#download-bar.visible { display: flex; }
```

Contents (unchanged from the original implementation):

- **Mini player** (`#mini-player`) — artwork, play/pause button, name +
  artist, current time, `#mini-waveform-wrap`, duration. Hidden until a
  track plays.
- **`dl-info`** — live "Ready to import: N tracks · M cues" summary fed
  by `renderTracks()`.
- **`#undo-btn`** — quick restore of the most recent apply backup
  (visible only after a successful apply that set
  `lastAppliedBackupFilename`).
- **`#skip-colored-label`** — checkbox shown only in local mode to skip
  already-coloured tracks during the Color-by-BPM operation.
- **`#color-by-bpm-btn`** (local mode).
- **`#preview-cues-btn`** (local mode).
- **`#delete-cues-btn`** (local mode) — wipes all hot cues (A–H) on the
  filtered selection. Backed by a confirm bar (`#delete-confirm-bar`).
- **`#download-btn`** — labelled "Download XML" in XML mode and
  "Apply to Rekordbox" in local mode. Acts as the primary CTA.

The legacy `<span id="selection-count">` inside the sticky filter bar
still reflects `selectedTrackIds.size`. When 2 tracks are selected in
local mode the `#transition-score-btn` reveals itself; clicking it opens
the transition modal via `showTransitionScore()`.

---

## 20. Status row — `#app-status`

`#app-status` (added in the UI refresh) lives inside `#top-bar` next to
`#tab-nav` and is the canonical status surface in local mode. It is
hidden via CSS until `updateAppStatus({connected: true})` toggles
`.visible`:

| Span                | Content                                       |
| ------------------- | --------------------------------------------- |
| `#status-db`        | "DB connected" with a green dot               |
| `#status-count`     | `<strong>{N.toLocaleString()}</strong> tracks` |
| `#status-scan`      | "Last scan just now / Xm ago / Xh ago / Xd ago" — recomputed every 60 s via `setInterval` |
| `#status-rb`        | "Rekordbox closed ✓" (green dot), "Rekordbox open" (orange dot), or "Rekordbox ?" (idle) |

```js
function updateAppStatus({connected, trackCount, didScan, rekordboxRunning})
```

Single mutation entrypoint. Called from `loadTracksFromServer()` after
every fetch (passing `didScan: true`). The Rekordbox-running flag is set
by the UI's HTTP-409 handlers when a write endpoint reports
`rekordbox_is_running()` truthy.

The original "Connected to Rekordbox" `#local-mode-banner` and
`#local-track-count` elements still exist inside `#upload-section`
(toggled at `index.html:4022`) for backwards compatibility with the
on-boarding flow, but `#app-status` is the surface a user actually
reads once tracks are loaded.

Rekordbox-running detection is server-side
([`rekordbox_is_running()`](./backup-and-restore.md#what-the-guard-actually-checks))
and combines a `psutil` process probe with an `fcntl`/`msvcrt`
exclusive-lock attempt on `master.db`. The UI shows a descriptive toast
via `_humanFetchError()` on every 409.

The `tracks-count` chip near the top of the list still shows
`"N tracks"` or `"N of M tracks"` when filtering is active, with the
`count-pop` animation on change.

---

## 21. Theme variables — CSS custom properties

All visual styling reads from CSS custom properties declared at
`index.html:16` (`:root`) and `index.html:45` (`html.dark`):

| Var                | Light          | Dark           | Used for                                |
| ------------------ | -------------- | -------------- | --------------------------------------- |
| `--bg`             | `#fafafa`      | `#0c0a09`      | body background                          |
| `--surface`        | `#ffffff`      | `#1c1917`      | inputs, modals, dropdowns                |
| `--surface2`       | `#f2f2f2`      | `#231f1d`      | recessed surfaces (sparkline bg, etc.)   |
| `--surface-card`   | `#ffffff`      | `#1c1917`      | track cards, sticky filter bar           |
| `--border`         | `#e8e8e8`      | `#292524`      | all 1 px borders                         |
| `--border-hover`   | `#c8c8c8`      | `#3d3830`      | hover state of inputs / buttons          |
| `--green`          | `#159a05`      | `#28e214`      | brand colour (Apply, selected, success)  |
| `--green-dim`      | `#0f6e03`      | `#1a9a0d`      | hover variant                            |
| `--text`           | `#1a1a1a`      | `#fafaf9`      | primary copy                             |
| `--muted`          | `#737373`      | `#a8a29e`      | secondary copy, placeholder              |
| `--muted-soft`     | `#a3a3a3`      | `#78716c`      | scrollbar thumb, disabled                |
| `--font`           | Inter          | Inter          | UI font                                  |
| `--mono`           | JetBrains Mono | JetBrains Mono | code, BPM/Key badges                     |
| `--cue-a` ··· `-h` | Cue palette    | Cue palette    | A–H badge colours                        |
| `--top-bar-h`      | dynamic        | dynamic        | the height of `#top-bar`, set by `ResizeObserver` |

Dark mode is enabled by toggling the `dark` class on `<html>`:

```js
// index.html:7959
const root = document.documentElement;
function applyTheme(dark) { root.classList.toggle('dark', dark); ... }
```

**Always use `var(...)` references for any new element.** Hard-coded
colours skip the theme system and break dark mode. The only escape hatches
are intentional brand pills (e.g. the red "danger" delete button uses
`#e74c3c` literals because they need to read the same in either theme).

The toast stack, modals, and all interactive surfaces inherit the variables,
so a new dark-mode value in `html.dark { … }` applies everywhere with no
JS coordination.

---

## 22. Multi-select backup delete

The Restore-backup panel supports multi-selection plus per-backup delete.
The three helpers form a small state machine:

| Helper                       | Location              | Role                                                                |
| ---------------------------- | --------------------- | ------------------------------------------------------------------- |
| `_populateChecklist(backups)`| `index.html:4130`     | Rebuilds `#backup-checklist` rows with checkbox + name + size.       |
| `_updateSelectionCount()`    | `index.html:4166`     | Reads all `<input type="checkbox">` in the checklist, updates the "select all" indeterminate / checked state and the `#backup-select-count` span. |
| `_checkedBackups()`          | `index.html:4178`     | Returns an array of currently checked filenames.                     |

The Restore confirm button (`index.html:4223`) iterates the array and
POSTs `/api/restore` per filename. The Delete-selected button
(`index.html:4253`) issues `DELETE /api/backups/{filename}` per filename.
Both surface a consolidated toast at the end.

Path-traversal safety lives on the server (`/api/backups/{filename}` only
accepts bare filenames — no slashes — and validates the resolved path is
inside `BACKUP_DIR`). The UI does not attempt to defend against this; it
simply sends what the user picked.

---

## 23. Fetch error handling

Every `fetch` call in the app follows the same defensive pattern:

```js
const r = await fetch(url, opts);
if (!r.ok) {
  const e = await r.json().catch(() => ({}));
  throw new Error(e.detail || `HTTP ${r.status}`);
}
const d = await r.json();
```

Reasons:

- **Always check `r.ok` before reading typed properties.** A 409 from
  `/api/apply` returns `{detail: "Rekordbox is running"}`, not the success
  shape. Reading `resp.applied` on that body yields `undefined` and was
  the source of a real bug ("Applied undefined" toast — see the comment
  at `tests/web/ui-logic.test.js:7`). This pattern is enforced by tests in
  `ui-logic.test.js` for `applyToRekordbox`, `colorTracksByBpm`, and
  several other write paths.
- **`r.json().catch(() => ({}))`** so a non-JSON error body (e.g. raw
  Starlette traceback HTML on a 500) does not throw a second exception
  during error display.
- **`_humanFetchError(err)`** at `index.html:6414` maps the common
  failure modes to friendly messages: `Failed to fetch`,
  `ERR_CONNECTION_REFUSED`, "index is still building", 502, 503, timeout.

Errors are then displayed via `showToast(message, isError=true)`
(`index.html:6395`). The toast stack is capped at 3 (oldest dismissed
first) so a flurry of errors doesn't bury the rest of the UI.

---

## 24. Example flow — Apply phrase cues in local mode

Walking through what happens when a user clicks "Apply to Rekordbox":

1. Page load → `detectLocalMode()` returns `true` → `loadTracksFromServer()`
   fetches `/api/tracks` + `/api/status` → `AppState.signal('tracks')` →
   `renderTracks()` builds cards (FLIP / observer setup) → `#tab-nav`
   visible.
2. User flips the "✨ Phrase analysis" toggle → `analysisMode = 'phrase'`
   → `AppState.signal('settings')` → re-render flips every card's preview
   timeline to use phrase data.
3. User filters by playlist via `#playlist-select` → `pendingCues = {};
   healthData = {};` → `loadTracksFromServer(activePlaylistId)` →
   `parsedTracks` replaced → `AppState.signal('tracks')` → re-render.
4. User checks 30 cards → each click adds the id to `selectedTrackIds`
   → `updateSelectionBar()` updates the count.
5. User clicks `#preview-cues-btn` → fetch POST `/api/generate` with
   `track_ids: activeTracks().map(t => t.id)` → store result in
   `pendingCues` → `AppState.signal('filters')` → re-render with secondary
   timelines.
6. User clicks `#download-btn` ("Apply to Rekordbox") → `applyToRekordbox()`:
   - Makes a DB backup via `/api/generate-apply-stream` (the endpoint
     backs up first, then writes).
   - Reads the SSE stream via `_consumeSSE(response, ev => { … })`,
     updating a progress bar from `ev.processed / ev.total`.
   - On the final `{done: true, applied: N, backup: "…"}` event,
     sets `lastAppliedBackupFilename`, shows a success toast, and clears
     `pendingCues`.
   - If the response is 409 ("Rekordbox is running"), `r.ok` is false →
     `_humanFetchError` returns the friendly message.
7. User clicks "↩ Undo last apply" (visible because `lastAppliedBackupFilename`
   is set) → POST `/api/restore {filename: lastAppliedBackupFilename}` →
   server closes the engine, copies the file, reopens, resets caches
   (including `similar.clear_index()`).
8. Client calls `loadTracksFromServer()` to re-fetch — the analysis state
   on the server has already been cleared so similarity scores
   correspond to the restored DB.

Throughout, every render hop is a single `renderTracks()` invocation
because all signals were coalesced by AppState.

---

## 25. Testing

JS tests live in [`tests/web/`](../../tests/web/) and run under Vitest with
the jsdom environment (`vitest.config.js`).

| File                     | Tests | Scope                                                              |
| ------------------------ | ----- | ------------------------------------------------------------------ |
| `xml-processing.test.js` | 65    | `parseRekordboxXml`, `generateCues`, `pickCueColor`                |
| `ui-logic.test.js`       | 126   | `filteredTracks`, backup multi-select, SSE apply, sort labels, memory cue, `colorTracksByBpm`, `add_fill_cues`, `ensureLocalAudio`, HTTP error handling, `_explainCue` (all modes), `_esc`, `_renderSuggestion`, `AppState` pub/sub bus |

The test files **copy functions verbatim** from `docs/index.html` — they
are not imported (the HTML file is not a JS module). If you change any of
the following functions or expressions in `docs/index.html`, update the
corresponding copy in the test files:

- `parseRekordboxXml`, `generateCues`, `computeCues`, `colorTracksByBpm`,
  `applyToRekordbox`, `pickCueColor` — `xml-processing.test.js`.
- `filteredTracks`, `ensureLocalAudio`, `_explainCue`, `_esc`,
  `_renderSuggestion`, `_populateChecklist`/`_updateSelectionCount`/
  `_checkedBackups`, the `SORT_LABELS` map, the `makeAppState()` helper —
  `ui-logic.test.js`.

The `makeAppState()` helper inside `ui-logic.test.js` is a standalone
duplicate of the production `AppState` IIFE; it exists so the test can
exercise coalescing, unsubscribe, multi-key dispatch, and exception
isolation without bootstrapping the entire HTML file.

Run all JS tests:

```bash
npm install     # one-time
npm test
```

Run a single file:

```bash
npx vitest run tests/web/ui-logic.test.js
```

---

## 26. Related references

- [`rest-api.md`](./rest-api.md) — every endpoint the UI calls.
- [`cue-generation.md`](./cue-generation.md) — phrase / bar / heuristic
  fallback strategy used by `/api/generate` and the smart-slot ordering
  that drives `_explainCue`'s "Slot A: mix-in point" reason.
- [`backup-and-restore.md`](./backup-and-restore.md) — the `master.db`
  backup format, retention, and restore behaviour invoked by the
  multi-select backup panel.
- [`energy-and-mixability.md`](./energy-and-mixability.md) — what
  `/api/tracks/{id}/energy` returns and how the curve maps onto the mini
  waveform.
- [`set-builder.md`](./set-builder.md) — `/api/setbuilder` payload shape
  consumed by the Set Builder panel in the Library tab.
- [`discogs-and-discovery.md`](./discogs-and-discovery.md) — the
  Discogs / Discover panel data model and `_renderSuggestion` field
  mappings.
- [`youtube-download.md`](./youtube-download.md) — yt-dlp integration
  surfaced by the Discover tab's Download section.
