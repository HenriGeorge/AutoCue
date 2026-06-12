/* AutoCue app.js — P0 T5 split part 1/8: 01-core.js
 * Classic script (NOT a module): shares globals, loaded in order by
 * docs/index.html. Concatenating 01..08 in order reproduces the original
 * app.js. Do not reorder. See .claude/project/web-ui.md. */
// ── Perf instrumentation ──────────────────────────────────────────────────────
// In-page performance helper that wraps performance.mark / performance.measure.
// No-op unless localStorage.getItem('autocue_perf') === '1' so it never affects
// production sessions. See .agent/prd/PERFORMANCE_PRD.md TASK-049 / TASK-050.
var _perf = (function() {
  var PREFIX = 'autocue:';
  function _enabled() {
    try { return localStorage.getItem('autocue_perf') === '1'; }
    catch (_) { return false; }
  }
  return {
    enabled: _enabled,
    mark: function(name) {
      if (!_enabled()) return;
      try { performance.mark(PREFIX + name); } catch (_) {}
    },
    measure: function(name, startMark) {
      if (!_enabled()) return null;
      try {
        performance.measure(PREFIX + name, PREFIX + startMark);
        var entries = performance.getEntriesByName(PREFIX + name, 'measure');
        var entry = entries[entries.length - 1];
        if (entry) console.log('[AutoCue Perf] ' + name + ': ' + entry.duration.toFixed(2) + 'ms');
        return entry || null;
      } catch (_) { return null; }
    },
    getEntries: function() {
      try {
        return performance.getEntriesByType('measure').filter(function(e) {
          return e.name.indexOf(PREFIX) === 0;
        });
      } catch (_) { return []; }
    },
    clear: function() {
      try { performance.clearMarks(); performance.clearMeasures(); } catch (_) {}
    },
  };
})();

// Fixed card height for the virtualized flat list (TASK-033). Keep in sync
// with the CSS rule `#track-list.virtualized .track-card { height: 160px }`.
var CARD_HEIGHT_PX = 160;

// ── Virtualizer (TASK-031 scaffold + TASK-032/034/035 wiring) ─────────────
// Vanilla-JS virtualization helper: renders only the viewport + buffer rows
// of a large list. Off-screen rows are absent from the DOM. Card height is
// fixed (TASK-033) so the visible window can be computed in O(1).
//
// API:
//   Virtualizer.attach({container, itemHeight, totalCount, renderItem,
//                       buffer, scrollSource, onWindowChange, topOcclusionFn})
//   Virtualizer.update({totalCount, scrollToIndex?})
//   Virtualizer.detach()
//
// scrollSource: 'container' (default; container's own overflow) or 'window'
// (document-level scroll — required by TASK-037 to preserve the sticky filter
// bar and fixed action bar). In window mode the container does NOT scroll —
// the body does — and visible-window math is derived from
// container.getBoundingClientRect().top + window.innerHeight.
//
// renderItem(index, recycledNode | null) returns the DOM node to mount for
// `index`. Off-screen nodes are returned to a pool and reused.
//
// onWindowChange(firstIdx, lastIdx, liveMap) fires after _render whenever
// the visible window's index range changes (used by the track-list wiring
// to re-attach IntersectionObservers on newly-visible cards).
//
// topOcclusionFn: optional () => number returning the viewport y-coordinate
// of the bottom edge of any sticky/fixed element that visually overlays the
// container's top. When provided, cards are snapped UP so the next card
// boundary aligns with the occluder's bottom — eliminates the "orphan
// half-card poking out below the sticky filter bar" regression that JSDOM
// tests can't catch (no layout). See `tests/e2e/1-sticky-overlap.spec.ts`.
var Virtualizer = (function() {
  var state = null;

  function _computeWindow(s) {
    if (s.scrollSource === 'window') {
      // Container's top in viewport coords. Negative once we've scrolled past
      // it (e.g., -300 means 300px of the container is above the viewport).
      var rect = s.container.getBoundingClientRect();
      var pastTop = Math.max(0, -rect.top);
      var first = Math.max(0, Math.floor(pastTop / s.itemHeight) - s.buffer);
      var visible = Math.ceil(s.viewportHeight / s.itemHeight) + s.buffer * 2;
      var last = Math.min(s.totalCount, first + visible);
      return { first: first, last: last };
    }
    var firstC = Math.max(0, Math.floor(s.scrollTop / s.itemHeight) - s.buffer);
    var visibleC = Math.ceil(s.viewportHeight / s.itemHeight) + s.buffer * 2;
    var lastC = Math.min(s.totalCount, firstC + visibleC);
    return { first: firstC, last: lastC };
  }

  // Compute the snap-offset that aligns the next card boundary with the
  // bottom of any sticky/fixed element occluding the viewport top.
  //
  // When `topOcclusionFn` is provided and the container has scrolled
  // partially under the occluder (listRect.top < occluderBottom), the
  // first card naturally rendered at translateY(0) would have its TOP
  // hidden behind the sticky while its BOTTOM pokes out below — the
  // "orphan ⚠ row" regression. Shifting ALL cards UP by `itemHeight -
  // (occluderBottom - listRect.top) mod itemHeight` snaps the next
  // card boundary to align with the occluder's bottom edge, so the
  // user sees full cards starting cleanly below the sticky.
  function _computeCardOffset(s) {
    if (s.scrollSource !== 'window') return 0;
    if (typeof s.topOcclusionFn !== 'function') return 0;
    var occluderBottom = s.topOcclusionFn();
    if (!occluderBottom || occluderBottom <= 0) return 0;
    var listTop = s.container.getBoundingClientRect().top;
    var gap = occluderBottom - listTop;
    if (gap <= 0) return 0;
    var remainder = gap % s.itemHeight;
    if (remainder === 0) return 0;
    // Shift cards UP so the next boundary lands at occluderBottom.
    return -(s.itemHeight - remainder);
  }

  function _render() {
    if (state === null) return;
    if (state.scrollSource === 'window') {
      state.viewportHeight = window.innerHeight || state.viewportHeight;
    }
    state.cardOffset = _computeCardOffset(state);
    var win = _computeWindow(state);
    var needed = new Set();
    for (var i = win.first; i < win.last; i++) needed.add(i);

    // Recycle nodes that scrolled out of the window.
    var stale = [];
    state.live.forEach(function(node, idx) {
      if (!needed.has(idx)) stale.push(idx);
    });
    stale.forEach(function(idx) {
      var node = state.live.get(idx);
      state.live.delete(idx);
      state.pool.push(node);
    });

    // Re-position cards already in the live map — their translateY needs
    // to reflect the current cardOffset, which moves on every scroll.
    if (state.cardOffset !== state._lastCardOffset) {
      state.live.forEach(function(node, idx) {
        node.style.transform =
          'translateY(' + (idx * state.itemHeight + state.cardOffset) + 'px)';
      });
      state._lastCardOffset = state.cardOffset;
    }

    for (var j = win.first; j < win.last; j++) {
      if (state.live.has(j)) continue;
      var recycled = state.pool.pop() || null;
      var rendered = state.renderItem(j, recycled);
      if (rendered) {
        rendered.style.position = 'absolute';
        rendered.style.top = '0';
        rendered.style.left = '0';
        rendered.style.right = '0';
        rendered.style.transform =
          'translateY(' + (j * state.itemHeight + state.cardOffset) + 'px)';
        if (!rendered.parentNode) state.container.appendChild(rendered);
        state.live.set(j, rendered);
      }
    }

    // Spacer keeps the scrollbar honest. Add one itemHeight of buffer so the
    // last card stays reachable when cardOffset shifts cards up by up to
    // (itemHeight - 1) to snap-align with the sticky bar.
    state.spacer.style.height =
      (state.totalCount * state.itemHeight + state.itemHeight) + 'px';

    if (state.onWindowChange && (state._lastFirst !== win.first || state._lastLast !== win.last)) {
      state._lastFirst = win.first;
      state._lastLast = win.last;
      try { state.onWindowChange(win.first, win.last, state.live); }
      catch (e) { console.error('[Virtualizer] onWindowChange error', e); }
    }
  }

  function _scheduleRender() {
    if (state === null || state.rafScheduled) return;
    state.rafScheduled = true;
    var raf = window.requestAnimationFrame || function(fn) { return setTimeout(fn, 0); };
    raf(function() {
      if (state === null) return;
      state.rafScheduled = false;
      _render();
    });
  }

  function _onContainerScroll() {
    if (state === null) return;
    state.scrollTop = state.container.scrollTop;
    _scheduleRender();
  }

  function _onWindowScroll() { _scheduleRender(); }
  function _onWindowResize() { _scheduleRender(); }

  return {
    attach: function(opts) {
      if (!opts || !opts.container) throw new Error('Virtualizer.attach: container required');
      Virtualizer.detach();
      var container = opts.container;
      container.style.position = container.style.position || 'relative';
      var spacer = document.createElement('div');
      spacer.style.position = 'relative';
      spacer.style.width = '100%';
      container.appendChild(spacer);

      var scrollSource = opts.scrollSource === 'window' ? 'window' : 'container';
      state = {
        container: container,
        spacer: spacer,
        itemHeight: opts.itemHeight,
        totalCount: opts.totalCount || 0,
        viewportHeight: scrollSource === 'window'
          ? (window.innerHeight || 800)
          : (container.clientHeight || 800),
        scrollTop: container.scrollTop || 0,
        buffer: opts.buffer != null ? opts.buffer : 5,
        renderItem: opts.renderItem,
        onWindowChange: opts.onWindowChange || null,
        topOcclusionFn: typeof opts.topOcclusionFn === 'function'
          ? opts.topOcclusionFn : null,
        scrollSource: scrollSource,
        pool: [],
        live: new Map(),
        rafScheduled: false,
        cardOffset: 0,
        _lastCardOffset: 0,
        _lastFirst: -1,
        _lastLast: -1,
        _scrollHandler: scrollSource === 'window' ? _onWindowScroll : _onContainerScroll,
        _resizeHandler: scrollSource === 'window' ? _onWindowResize : null,
      };
      if (scrollSource === 'window') {
        window.addEventListener('scroll', state._scrollHandler, { passive: true });
        window.addEventListener('resize', state._resizeHandler, { passive: true });
      } else {
        container.addEventListener('scroll', state._scrollHandler, { passive: true });
      }
      _render();
    },
    update: function(opts) {
      if (state === null) return;
      if (opts && typeof opts.totalCount === 'number') state.totalCount = opts.totalCount;
      if (opts && typeof opts.scrollToIndex === 'number') {
        if (state.scrollSource === 'window') {
          var rect = state.container.getBoundingClientRect();
          var targetTop = (window.scrollY || window.pageYOffset || 0) + rect.top + opts.scrollToIndex * state.itemHeight;
          window.scrollTo({ top: targetTop, behavior: 'auto' });
        } else {
          state.container.scrollTop = opts.scrollToIndex * state.itemHeight;
          state.scrollTop = state.container.scrollTop;
        }
      }
      // Force onWindowChange to refire even when window indices didn't move
      // (e.g., re-attach after a fingerprint reset where listeners need rewiring).
      state._lastFirst = -1; state._lastLast = -1;
      _render();
    },
    detach: function() {
      if (state === null) return;
      if (state.scrollSource === 'window') {
        window.removeEventListener('scroll', state._scrollHandler);
        if (state._resizeHandler) window.removeEventListener('resize', state._resizeHandler);
      } else {
        state.container.removeEventListener('scroll', state._scrollHandler);
      }
      state.live.forEach(function(node) {
        if (node.parentNode) node.parentNode.removeChild(node);
      });
      // Pool nodes stay attached to the container during in-session recycling
      // (their translateY just moves off-screen); detach must purge them too,
      // or the next attach()+renderItem run leaves ghost cards stacked at old
      // positions over the fresh content.
      state.pool.forEach(function(node) {
        if (node && node.parentNode) node.parentNode.removeChild(node);
      });
      if (state.spacer && state.spacer.parentNode) {
        state.spacer.parentNode.removeChild(state.spacer);
      }
      state = null;
    },
    isAttached: function() { return state !== null; },
    // Inspection: returns live Map<index, node>. Used by _updateTrackCardCues
    // and FLIP scoping to find currently-mounted cards.
    _visibleNodes: function() { return state ? state.live : new Map(); },
    // Test-only inspection.
    _state: function() { return state; },
  };
})();

// ── Warm-up badge poller (TASK-029) ───────────────────────────────────────
// Polls /api/warmup every 2s while the sidecar cache hydrates; hides the
// #status-warmup chip once the pipeline reports step === 'done'.
var _warmupPoll = (function() {
  var handle = null;
  function _stop() {
    if (handle !== null) { clearInterval(handle); handle = null; }
  }
  function _tick() {
    fetch('/api/warmup').then(function(r) {
      if (!r.ok) return null;
      return r.json();
    }).then(function(j) {
      if (!j) return;
      var sep = document.querySelector('.status-warmup-sep');
      var chip = document.getElementById('status-warmup');
      var text = document.getElementById('warmup-progress-text');
      if (!chip || !text) return;
      if (j.step === 'done' || j.step === 'unknown') {
        // Fade the chip out (CSS transition on .status-warmup-item) before
        // dropping it — it used to vanish in a single frame.
        chip.style.opacity = '0';
        if (sep) sep.style.opacity = '0';
        setTimeout(function() {
          chip.style.display = 'none';
          if (sep) sep.style.display = 'none';
        }, 320);
        _stop();
        return;
      }
      chip.style.display = '';
      chip.style.opacity = '1';
      if (sep) { sep.style.display = ''; sep.style.opacity = '1'; }
      var done = (j.done || 0).toLocaleString();
      var total = (j.total || 0).toLocaleString();
      text.textContent = done + ' / ' + total;
    }).catch(function() { /* transient network errors: keep polling */ });
  }
  return {
    start: function() {
      if (handle !== null) return;
      _tick();
      handle = setInterval(_tick, 2000);
    },
    stop: _stop,
  };
})();

// ── SVG icons ──────────────────────────────────────────────────────────────────
const SVG_PLAY  = `<svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>`;
const SVG_PAUSE = `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`;

// ── Constants ──────────────────────────────────────────────────────────────────
const CUE_COLORS = [
  { r: 40,  g: 226, b: 20  },
  { r: 48,  g: 90,  b: 255 },
  { r: 0,   g: 224, b: 255 },
  { r: 255, g: 160, b: 0   },
  { r: 255, g: 100, b: 0   },
  { r: 224, g: 48,  b: 30  },
  { r: 245, g: 30,  b: 140 },
  { r: 230, g: 0,   b: 255 },
];
const PHRASE_COLORS = {
  'Intro':   { r: 72,  g: 175, b: 65  },  // muted green
  'Verse':   { r: 65,  g: 120, b: 210 },  // muted blue
  'Bridge':  { r: 25,  g: 165, b: 185 },  // muted teal
  'Chorus':  { r: 195, g: 65,  b: 65  },  // muted rose
  'Outro':   { r: 200, g: 125, b: 35  },  // muted amber
  'Up':      { r: 185, g: 65,  b: 135 },  // muted mauve
  'Down':    { r: 130, g: 75,  b: 190 },  // muted purple
  '?':       { r: 160, g: 160, b: 160 },  // gray
};

function pickCueColor(cue) {
  if (cue.isPhrase && PHRASE_COLORS[cue.label]) return PHRASE_COLORS[cue.label];
  const safeSlot = Math.max(0, Math.min(7, cue.slot || 0));
  return CUE_COLORS[safeSlot];
}

function buildPhraseStrip(phrases, totalTime, cueTicks) {
  if (!phrases.length || !totalTime) return null;
  const strip = document.createElement('div');
  strip.className = 'phrase-strip';
  strip.title = 'Phrase sections detected by Rekordbox';
  for (let i = 0; i < phrases.length; i++) {
    const ph = phrases[i];
    const startPct = (ph.position_ms / 1000 / totalTime) * 100;
    const endSec = i < phrases.length - 1 ? phrases[i + 1].position_ms / 1000 : totalTime;
    const widthPct = ((endSec - ph.position_ms / 1000) / totalTime) * 100;
    if (widthPct <= 0) continue;
    const c = PHRASE_COLORS[ph.label] || PHRASE_COLORS['?'];
    const seg = document.createElement('div');
    seg.className = 'phrase-seg';
    seg.style.left = `${startPct}%`;
    seg.style.width = `${widthPct}%`;
    seg.style.background = `rgb(${c.r},${c.g},${c.b})`;
    seg.title = `${ph.label} @ ${fmtTime(ph.position_ms / 1000)}`;
    if (widthPct > 5) {
      const lbl = document.createElement('span');
      lbl.className = 'phrase-seg-lbl';
      lbl.textContent = ph.label;
      seg.appendChild(lbl);
    }
    strip.appendChild(seg);
  }
  // Overlay existing hot-cue positions as tick marks (Skipped cards merge the
  // #163 existing-cue chips into the strip so both fit the fixed 160px card).
  // Name/time go in the title (hover); the slot letter caps the tick.
  if (Array.isArray(cueTicks)) {
    for (const ec of cueTicks) {
      const startSec = ec.start || 0;
      if (startSec < 0 || startSec > totalTime) continue;
      const leftPct = (startSec / totalTime) * 100;
      const slotLetter = ec.num === -1 ? 'M' : (ec.num >= 0 && ec.num <= 7 ? String.fromCharCode(65 + ec.num) : '?');
      const tick = document.createElement('div');
      tick.className = 'phrase-cue-tick';
      tick.style.left = `${leftPct}%`;
      tick.title = `${slotLetter === 'M' ? 'Memory cue' : 'Slot ' + slotLetter}${ec.name ? ' — ' + ec.name : ''} @ ${fmtTime(startSec)}`;
      const slot = document.createElement('span');
      slot.className = 'phrase-cue-slot';
      slot.textContent = slotLetter;
      tick.appendChild(slot);
      strip.appendChild(tick);
    }
  }
  return strip;
}
const SLOT_NAMES = ['A','B','C','D','E','F','G','H'];

// ── State ──────────────────────────────────────────────────────────────────────
let parsedDoc    = null;
let parsedTracks = [];
// O(1) lookup index for parsedTracks, kept in sync via _setParsedTracks.
// Replaces parsedTracks.find(t => t.id === id) which is O(n).
let parsedTracksById = new Map();
function _setParsedTracks(arr) {
  parsedTracks = arr;
  parsedTracksById = new Map(arr.map(t => [String(t.id), t]));
  _libraryEpoch++;  // invalidates the phrase-result cache (B7)
}
let originalXmlText = null;

// Monotonic counter incremented on every library reload (or playlist change).
// loadPhraseFromServer stashes its value into _phraseLoadedEpoch; the next call
// short-circuits the network fan-out when the epoch hasn't changed.
let _libraryEpoch = 0;
let _phraseLoadedEpoch = -1;

let analysisMode = 'bar'; // 'bar' | 'phrase'
let phraseCueState = {};  // trackId → [{position_ms, label, slot}]
let currentSort = (() => { try { return JSON.parse(localStorage.getItem('ac_sort')) || { by: 'album', order: 'asc' }; } catch { return { by: 'album', order: 'asc' }; } })();
let expandedAlbums = new Set();
let anlzFileMap = {};     // folder-hex → { ext: File, dat: File }
let pyodideReady = null;  // Promise<pyodide> once requested

// F1/F5: pending cues from preview generate (string trackId → [{slot,posSec,label,isPhrase,name}])
let pendingCues = {};
// F2: active playlist filter (null = all)
let activePlaylistId = null;
// F4: active search query
let searchQuery = '';
// F6: phrase-only filter
let phraseOnlyFilter = false;
let beatsOnlyFilter = false;
// B1/B2: lazy audio-availability probe. _audioProbedAt[trackId] = "file" | "missing" | "unverified".
// Keys present here override the track's server-side `source`. Filter relies on this state.
let _audioProbedAt = {};
let _audioOnlyFilter = false;
let _audioCheckAbort = null;          // AbortController for in-flight /check-audio chunks
let _audioUnverifiedDirs = new Set(); // surfaced via the "unverified" soft chip
// B3: aggregated "Audio file not found" toast — collects failed IDs over a 1-second window.
let _audioFailQueue = new Set();
let _audioFailFlushTimer = null;
// Rating/plays/lastplayed/tag filters (local mode)
let ratingFilter = 0;
let playsFilter = 'all';   // 'all' | 'played' | 'unplayed'
let lastPlayedFilter = 'all'; // 'all' | '7d' | '30d' | 'never'
let myTagFilters = new Set(); // empty = all tags
let selectedKeys = new Set(); // Camelot keys e.g. "8A", "8B"
let genreFilters = new Set(); // empty = all genres; OR logic
// F7: bulk selection (track id strings)
let selectedTrackIds = new Set();
// F8: filename of the last successful apply backup (for one-click undo)
let lastAppliedBackupFilename = null;

// ── AppState: lightweight coalescing pub/sub ───────────────────────────────────
// AppState.signal(key) notifies subscribers on the next microtask, coalescing
// multiple signals in the same tick into a single flush per subscriber.
var AppState = (function() {
  var _subs  = new Map(); // key -> Set<fn>
  var _dirty = new Set();
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

// Audio state: trackId → { file, objectUrl, artworkUrl }
let audioState   = {};
let nowPlayingId = null;
let isScrubbing  = false;
let _energyCache = {};  // D4: trackId → Float32Array of energy curve
let _playRafId   = null; // D1: RAF loop for smooth playhead
var _cardMap = new Map();           // C: trackId → card element (shared cache for flat+album modes)
var _cardSettingsFingerprint = '';  // C: fingerprint of settings affecting card content
var _albumSortKey = '';             // C: last-rendered album sort key; skip rebuild if unchanged
// #172: cache the entire album-group element keyed by (albumName + memberTrackIds).
// On filter changes that leave an album untouched, we reuse the cached group
// (header + artwork chain + track cards). Without this, every filter toggle
// rebuilds every album group — which restarts the per-album `<img>` artwork
// fetch chain (one image request per album, sequential fail-over) and pegs
// the main thread + network until enough work piles up that subsequent
// synthetic clicks cannot land within the 30 s Playwright budget.
var _albumGroupCache = new Map();   // key: `${name}|${memberIdsCommaJoined}` → <div.album-group>
var _sparkObserver = null;          // C: reused observers prevent IntersectionObserver leaks
var _mixObserver   = null;
var _enterObserver = null;
const blobUrlsToRevoke = new Set();
