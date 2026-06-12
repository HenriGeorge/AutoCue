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

// ── Local mode ────────────────────────────────────────────────────────────────
let localMode = false;
let healthData = {};   // String(trackId) → TrackHealthReport event
let healthLastSummary = null;
let _healthFixInProgress = false;

async function detectLocalMode() {
  try {
    const r = await fetch('/api/status', { signal: AbortSignal.timeout(600) });
    if (r.ok) { const d = await r.json(); return d.connected === true; }
  } catch {}
  return false;
}

async function loadTracksFromServer(playlistId = null) {
  // TASK-050 — perf mark around the full library-load round-trip.
  try { _perf.mark('library-load-start'); } catch (_) {}
  const tracksUrl = playlistId != null
    ? `/api/tracks?limit=10000&playlist_id=${playlistId}&sort_by=${currentSort.by}&sort_order=${currentSort.order}`
    : `/api/tracks?limit=10000&sort_by=${currentSort.by}&sort_order=${currentSort.order}`;

  // Show loading state while fetch is in progress
  const countEl = document.getElementById('local-track-count');
  if (countEl) countEl.textContent = ' · Loading…';
  // First load only: skeleton cards instead of a blank page while the
  // fetch is in flight. Subsequent reloads keep the live list on screen.
  if (!parsedTracks.length) {
    const skelSect = document.getElementById('tracks-section');
    const skelList = document.getElementById('track-list');
    if (skelSect && skelList && !skelList.children.length) {
      skelSect.classList.add('visible');
      skelList.innerHTML = new Array(6).fill(
        '<div class="skeleton-card"><div class="skel-line skel-title"></div>' +
        '<div class="skel-line skel-sub"></div><div class="skel-line skel-chips"></div></div>'
      ).join('');
    }
  }

  let statusData, tracksData;
  try {
    [statusData, tracksData] = await Promise.all([
      fetch('/api/status').then(r => r.json()),
      fetch(tracksUrl).then(r => r.json()),
    ]);
  } catch (err) {
    if (countEl) countEl.textContent = '';
    // Drop the first-load skeletons so a failed fetch doesn't leave ghost cards
    document.querySelectorAll('#track-list .skeleton-card').forEach(el => el.remove());
    try { _perf.measure('library-load', 'library-load-start'); } catch (_) {}
    throw err;
  }
  try { _perf.measure('library-load', 'library-load-start'); } catch (_) {}
  // TASK-029 — start polling the warm-up badge in case the sidecar
  // cache is still hydrating in the background.
  try { _warmupPoll.start(); } catch (_) {}
  // Show playlist track count when filtered, library total when viewing all
  if (playlistId != null) {
    const playlistName = document.getElementById('playlist-select').selectedOptions[0]?.text.replace(/\s*\(\d+\)$/, '') || 'Playlist';
    document.getElementById('local-track-count').textContent = ` · ${tracksData.length} tracks (${playlistName})`;
    updateAppStatus({ connected: true, trackCount: tracksData.length, didScan: true });
  } else {
    document.getElementById('local-track-count').textContent = ` · ${statusData.track_count} tracks`;
    updateAppStatus({ connected: true, trackCount: statusData.track_count, didScan: true });
  }
  _energyCache = {};           // D4 fix: invalidate on reload so stale curves don't persist
  _cardMap.clear();            // C: force full rebuild on library reload
  _albumGroupCache.clear();    // #172: album cache wraps cards, drop when cards drop
  _cardSettingsFingerprint = '';
  if (Virtualizer.isAttached()) Virtualizer.detach();
  _setParsedTracks(tracksData.map(t => ({
    id: String(t.id), name: t.title, artist: t.artist, album: t.album || '',
    bpm: t.bpm, totalTime: t.duration, tempo: null,
    existingHotCues: t.existing_hot_cues, hasPhrase: t.has_phrase, hasBeats: t.has_beats,
    // Map API's existing_cue_details (slot/pos_sec) to the XML-shape used by the
    // chip renderer (num/start). Local + Pages mode now share the same chip code.
    existingCueDetails: (t.existing_cue_details || []).map(c => ({
      num: c.slot, name: c.name || '', start: c.pos_sec, colorName: c.color_name || '',
    })),
    source: t.source || 'file', // B1 — server tells us if it's file/streaming/unknown
    key: t.key || '',
    rating: t.rating || 0,
    playCount: t.play_count || 0,
    lastPlayed: t.last_played || null,
    myTags: t.my_tags || [],
    colorName: t.color_name || '',
    genre: t.genre || '',
    comment: t.comment || '',
    locationFilename: '',
  })));
  parsedDoc = null;
  selectedTrackIds.clear();
  updateSelectionBar();
  const withExisting = parsedTracks.filter(t => t.existingHotCues > 0).length;
  const info = document.getElementById('existing-cues-info');
  if (withExisting > 0) {
    document.getElementById('existing-cues-label').innerHTML =
      `<strong>${withExisting}</strong> of ${parsedTracks.length} tracks already have hot cues`;
    info.style.display = 'flex';
  } else {
    info.style.display = 'none';
  }
  // Staggered fade-in-up on initial connect.
  // #download-bar is the Pages-mode XML round-trip bar ("Ready to import: …").
  // In local mode the canonical bottom bar is #action-bar (selection-driven),
  // so #download-bar must NOT fade in here — it would show stale default text
  // (e.g. "Ready to import: 1 track · 8 cues") before any XML upload and
  // persist across all tabs (Cues / Library / Discover). See issue #15.
  var _fadeSections = ['settings-section', 'tracks-section'];
  if (!localMode) _fadeSections.push('download-bar');
  // Defensive: ensure the bar is hidden in local mode even if a prior code
  // path added .visible.
  if (localMode) {
    var _dlBar = document.getElementById('download-bar');
    if (_dlBar) _dlBar.classList.remove('visible');
  }
  var _sectDelay = 0;
  _fadeSections.forEach(function(id) {
    var el = document.getElementById(id);
    setTimeout(function() {
      el.classList.add('visible');
      el.classList.add('fade-in-up');
      el.addEventListener('animationend', function() { el.classList.remove('fade-in-up'); }, { once: true });
      // In local mode collapse settings so tracks are visible immediately
      if (id === 'settings-section' && localMode) {
        if (window._collapseSettings) window._collapseSettings();
      }
    }, _sectDelay);
    _sectDelay += 70;
  });
  document.getElementById('analysis-mode-bar').style.display = 'flex';
  document.getElementById('sort-bar').style.display = '';
  // Restore persisted sort UI
  const SORT_LABELS = { title: 'Title', artist: 'Artist', album: 'Album', bpm: 'BPM', key: 'Key', rating: 'Rating', plays: 'Plays' };
  document.querySelectorAll('.sort-btn').forEach(b => {
    const isActive = b.dataset.sort === currentSort.by;
    b.classList.toggle('active', isActive);
    b.textContent = SORT_LABELS[b.dataset.sort] + (isActive && currentSort.order !== 'asc' ? ' ▼' : isActive ? ' ▲' : '');
  });
  // Show BPM legend if any track has a color assigned
  const hasColors = parsedTracks.some(t => t.colorName);
  document.getElementById('bpm-legend').classList.toggle('visible', hasColors);
  setStep(3);
  AppState.signal('tracks'); // renders via subscriber; keep updateOverwriteWarning paired
  updateOverwriteWarning();
  // Populate comment enrichment preview dropdown
  const ceSel = document.getElementById('ce-preview-track');
  if (ceSel) {
    ceSel.innerHTML = '<option value="">— select a track —</option>';
    for (const t of parsedTracks) {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = `${t.name || '(untitled)'} — ${t.artist || ''}`;
      ceSel.appendChild(opt);
    }
  }
  // Populate genre filter popup
  const genreChipsEl = document.getElementById('genre-filter-chips');
  const genreBtnEl   = document.getElementById('genre-filter-btn');
  if (genreChipsEl && genreBtnEl) {
    const genres = [...new Set(parsedTracks.map(t => t.genre).filter(Boolean))].sort();
    genreChipsEl.innerHTML = '';
    for (const g of genres) {
      const chip = document.createElement('button');
      chip.className = 'genre-chip' + (genreFilters.has(g) ? ' active' : '');
      chip.textContent = g;
      chip.dataset.genre = g;
      genreChipsEl.appendChild(chip);
    }
    genreBtnEl.style.display = genres.length ? '' : 'none';
  }
}

// ── Library Health ────────────────────────────────────────────────────────────

async function scanLibraryHealth() {
  const btn      = document.getElementById('health-scan-btn');
  const label    = document.getElementById('health-scanning-label');
  const progBar  = document.getElementById('health-progress-bar');
  const fill     = document.getElementById('health-progress-fill');
  const summary  = document.getElementById('health-summary');

  const abortCtrl = new AbortController();
  _setBtnCancellable(btn, 'Scanning…', abortCtrl);
  label.style.display = '';
  label.textContent = 'Scanning…';
  progBar.style.display = '';
  // Invalidate any pending delayed-hide from a previous scan's finally block
  progBar._hideTok = (progBar._hideTok || 0) + 1;
  fill.style.width = '0%';
  summary.style.display = 'none';
  healthData = {};

  const url = activePlaylistId
    ? `/api/health?playlist_id=${activePlaylistId}`
    : '/api/health';

  try {
    const r = await fetch(url, { signal: abortCtrl.signal });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let processed = 0;
    let total = null;
    let receivedDone = false;

    // Cancel the reader stream when abort fires
    abortCtrl.signal.addEventListener('abort', function() { reader.cancel().catch(function(){}); }, { once: true });

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.done) {
          receivedDone = true;
          healthLastSummary = ev.summary;
          total = ev.summary.total;
          fill.style.width = '100%';
          _renderHealthSummary(ev.summary);
        } else if (ev.total && !ev.track_id) {
          total = ev.total;
        } else {
          processed++;
          healthData[String(ev.track_id)] = ev;
          const prog = `Scanning… ${processed.toLocaleString()}`;
          label.textContent = prog;
          _setBtnCancellable(btn, prog, abortCtrl);
          const pct = total
            ? processed / total * 100
            : Math.min(97, processed / Math.max(processed + 50, 1) * 100);
          fill.style.width = `${pct}%`;
        }
      }
    }
    // Flush any data left in buf when the stream closed without a trailing \n\n
    for (const line of buf.split('\n')) {
      if (!line.startsWith('data: ')) continue;
      try {
        const ev = JSON.parse(line.slice(6));
        if (ev.done) {
          receivedDone = true;
          healthLastSummary = ev.summary;
          _renderHealthSummary(ev.summary);
        } else {
          processed++;
          healthData[String(ev.track_id)] = ev;
        }
      } catch {}
    }
    if (!receivedDone) {
      showToast('Health scan ended without a summary — results may be incomplete');
    }
    fill.style.width = '100%';
    renderTracks();
  } catch (err) {
    if (err.name === 'AbortError') {
      showToast(`Health scan cancelled — ${Object.keys(healthData).length.toLocaleString()} tracks scanned`);
      if (Object.keys(healthData).length > 0) renderTracks();
    } else {
      showToast(`Health scan failed: ${err.message}`);
    }
  } finally {
    _setBtnLoading(btn, false);
    // Let the 100% fill paint for a beat — completion used to be hidden in the
    // same tick it was reached, so the bar never visibly finished.
    const hideTok = progBar._hideTok;
    setTimeout(() => {
      if (progBar._hideTok !== hideTok) return; // a newer scan owns the bar now
      label.style.display = 'none';
      progBar.style.display = 'none';
    }, 400);
  }
}

// ── Duplicate Tracks ──────────────────────────────────────────────────────────
//
// Phase 1: scan + display only. The destructive delete path (with backup +
// Rekordbox-closed checks) lands in a follow-up PR after this UX is validated.

function _pickKeeper(copies) {
  // Mirror of autocue.analysis.duplicates.pick_keeper. Keep in sync.
  // Phase 3 WS2 order: cues → plays → last_played → bitrate → -id.
  const keyOf = (c) => [
    c.existing_hot_cues || 0,
    c.play_count || 0,
    c.last_played || '',
    c.bitrate || 0,
    -c.track_id,
  ];
  let best = copies[0];
  for (let i = 1; i < copies.length; i++) {
    const c = copies[i];
    const aKey = keyOf(best);
    const bKey = keyOf(c);
    let take = false;
    for (let j = 0; j < aKey.length; j++) {
      if (bKey[j] > aKey[j]) { take = true; break; }
      if (bKey[j] < aKey[j]) { break; }
    }
    if (take) best = c;
  }
  return best.track_id;
}

// Format a duration in seconds as M:SS for the per-copy detail row.
function _fmtDur(sec) {
  sec = Math.round(sec || 0);
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function _renderDuplicateGroup(group) {
  const div = document.createElement('div');
  div.className = 'duplicates-group panel-card';
  div.style.cssText = 'padding:10px 12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);';
  div.dataset.groupKey = `${(group.artist || '').toLowerCase()}|||${(group.title || '').toLowerCase()}`;

  // WS2 — the keeper is now mutable. It starts at the backend's suggestion
  // (group.keeper_id, echoed via is_keeper) but the user can pick a
  // different copy via the "Keep" radio in the expanded detail rows. All
  // derived state (non-keeper ids, delete-button label, dataset for the
  // bulk-delete walk, same-path chips) recomputes from currentKeeperId.
  let currentKeeperId = (group.copies.find(c => c.is_keeper) || group.copies[0]).track_id;

  const _nonKeepers = () =>
    group.copies.filter(c => c.track_id !== currentKeeperId).map(c => c.track_id);
  const _keeperPath = () => {
    const k = group.copies.find(c => c.track_id === currentKeeperId) || {};
    return `${k.folder_path || ''}${k.file_name || ''}`;
  };

  const head = document.createElement('div');
  head.style.cssText = 'display:flex;align-items:center;gap:10px;flex-wrap:wrap;';
  const title = document.createElement('div');
  title.style.cssText = 'flex:1;min-width:0;';
  const artistEl = document.createElement('span');
  artistEl.style.cssText = 'font-weight:600;';
  artistEl.textContent = group.artist || '(unknown artist)';
  const dashEl = document.createElement('span');
  dashEl.style.cssText = 'margin:0 6px;color:var(--muted);';
  dashEl.textContent = '—';
  const titleEl = document.createElement('span');
  titleEl.textContent = group.title || '(untitled)';
  title.appendChild(artistEl);
  title.appendChild(dashEl);
  title.appendChild(titleEl);
  const countChip = document.createElement('span');
  countChip.style.cssText = 'font-size:11px;background:var(--amber, #c98a00)22;color:var(--amber, #c98a00);border:1px solid var(--amber, #c98a00)55;border-radius:9999px;padding:2px 8px;font-weight:600;';
  countChip.textContent = `${group.copies.length} copies`;
  const deleteBtn = document.createElement('button');
  deleteBtn.className = 'secondary-btn duplicates-group-delete';
  deleteBtn.style.cssText = 'font-size:11px;padding:3px 10px;color:#e4384e;border-color:#e4384e44;';
  deleteBtn.title = 'Opens a confirm dialog; backup is created before any delete';
  const toggle = document.createElement('button');
  toggle.className = 'secondary-btn';
  toggle.style.cssText = 'font-size:11px;padding:3px 10px;';
  toggle.textContent = 'Show details';
  head.appendChild(title);
  head.appendChild(countChip);
  head.appendChild(deleteBtn);
  head.appendChild(toggle);
  div.appendChild(head);

  const table = document.createElement('div');
  table.className = 'dup-details';

  // Re-paint everything that depends on the current keeper: dataset (for
  // the bulk-delete walk), the delete-button label, the row highlight +
  // same-path chips. Called on initial render and on every radio change.
  function _refresh() {
    const nk = _nonKeepers();
    div.dataset.nonKeeperIds = JSON.stringify(nk);
    deleteBtn.textContent = `Delete ${nk.length} non-keeper${nk.length === 1 ? '' : 's'}`;
    deleteBtn.disabled = nk.length === 0;
    const keeperPath = _keeperPath();
    table.querySelectorAll('.dup-copy-row').forEach((row) => {
      const tid = Number(row.dataset.trackId);
      const isKeeper = tid === currentKeeperId;
      row.style.background = isKeeper
        ? 'color-mix(in srgb, var(--green) 12%, transparent)' : '';
      row.style.fontWeight = isKeeper ? '600' : '';
      const star = row.querySelector('.dup-keeper-star');
      if (star) star.style.visibility = isKeeper ? 'visible' : 'hidden';
      // Same-path chip: a NON-keeper whose file path matches the keeper's
      // is safe to delete (no orphan file); a distinct-path non-keeper
      // leaves an audio file on disk. The keeper itself shows no chip.
      const chip = row.querySelector('.dup-path-chip');
      if (chip) {
        const c = group.copies.find(x => x.track_id === tid) || {};
        const samePath = `${c.folder_path || ''}${c.file_name || ''}` === keeperPath;
        if (isKeeper) {
          chip.style.display = 'none';
        } else if (samePath) {
          chip.style.display = '';
          chip.textContent = '🗂 same file as keeper';
          chip.style.color = 'var(--muted)';
        } else {
          chip.style.display = '';
          chip.textContent = '📁 distinct file — stays on disk';
          chip.style.color = 'var(--amber, #c98a00)';
        }
      }
    });
  }

  for (const c of group.copies) {
    const row = document.createElement('div');
    row.className = 'dup-copy-row';
    row.dataset.trackId = c.track_id;
    row.style.cssText = 'display:flex;gap:10px;align-items:center;padding:4px 6px;border-radius:4px;flex-wrap:wrap;';
    // "Keep" radio (WS2 override). One radio group per duplicate group via
    // a name keyed on the group's DOM identity.
    const radioLabel = document.createElement('label');
    radioLabel.style.cssText = 'display:inline-flex;align-items:center;gap:3px;cursor:pointer;min-width:54px;';
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = `dup-keeper-${div.dataset.groupKey}`;
    radio.value = String(c.track_id);
    radio.checked = c.track_id === currentKeeperId;
    radio.className = 'dup-keeper-radio';
    radio.addEventListener('change', () => {
      if (radio.checked) { currentKeeperId = c.track_id; _refresh(); }
    });
    const radioText = document.createElement('span');
    radioText.style.cssText = 'font-size:11px;';
    radioText.textContent = 'Keep';
    radioLabel.appendChild(radio);
    radioLabel.appendChild(radioText);

    const meta = document.createElement('span');
    meta.style.cssText = 'display:flex;gap:10px;flex-wrap:wrap;align-items:baseline;';
    meta.innerHTML =
      `<span style="min-width:84px;">id ${c.track_id}</span>` +
      `<span style="min-width:48px;">${_fmtDur(c.duration)}</span>` +
      `<span style="min-width:60px;">${(c.bpm || 0).toFixed(2)} BPM</span>` +
      `<span style="min-width:36px;">${_esc(c.key || '—')}</span>` +
      `<span style="min-width:48px;">${c.existing_hot_cues || 0} cues</span>` +
      `<span style="min-width:48px;">${c.play_count || 0} plays</span>` +
      (c.bitrate ? `<span style="min-width:60px;">${c.bitrate} kbps</span>` : '') +
      `<span style="min-width:110px;">${_esc((c.last_played || '—').slice(0, 19))}</span>`;

    const star = document.createElement('span');
    star.className = 'dup-keeper-star';
    star.style.cssText = 'color:var(--green);';
    star.textContent = '★ keeper';

    const chip = document.createElement('span');
    chip.className = 'dup-path-chip';
    chip.style.cssText = 'font-size:11px;margin-left:auto;';

    row.appendChild(radioLabel);
    row.appendChild(meta);
    row.appendChild(chip);
    row.appendChild(star);
    table.appendChild(row);
  }
  div.appendChild(table);

  deleteBtn.addEventListener('click', () => {
    const nk = _nonKeepers();
    const keeperPath = _keeperPath();
    const distinct = group.copies.filter(
      c => c.track_id !== currentKeeperId &&
        `${c.folder_path || ''}${c.file_name || ''}` !== keeperPath
    ).length;
    _openDuplicatesConfirm({
      track_ids: nk,
      label: `1 keeper + ${nk.length} non-keeper${nk.length === 1 ? '' : 's'}`,
      meta: ` of ${_esc(group.artist || '?')} — ${_esc(group.title || '?')}`,
      audioNote: distinct > 0
        ? `${distinct} distinct audio file${distinct === 1 ? '' : 's'} will remain on disk.`
        : 'All deleted rows share the keeper\'s audio file — no orphan files.',
      onSuccess: () => { _onTracksDeleted(nk); div.remove(); },
    });
  });

  toggle.addEventListener('click', () => {
    // Accordion slide via the shared helper — same motion as the mixing guide
    _slideToggle(table, 'open');
    toggle.textContent = table.classList.contains('open') ? 'Hide details' : 'Show details';
  });

  _refresh();
  return div;
}

async function scanDuplicates() {
  const btn = document.getElementById('duplicates-scan-btn');
  const statusEl = document.getElementById('duplicates-status-label');
  const progress = document.getElementById('duplicates-progress');
  const summary = document.getElementById('duplicates-summary');
  const empty = document.getElementById('duplicates-empty');
  const list = document.getElementById('duplicates-list');

  const abortCtrl = new AbortController();
  _setBtnCancellable(btn, 'Scanning…', abortCtrl);
  statusEl.style.display = '';
  statusEl.textContent = 'Scanning…';
  progress.style.display = '';
  progress.style.color = ''; // clear any red left by a prior failed scan
  progress.textContent = 'Loading tracks…';
  summary.style.display = 'none';
  empty.style.display = 'none';
  list.innerHTML = '';

  try {
    const r = await fetch('/api/duplicates', { signal: abortCtrl.signal });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || r.statusText);
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let total = null;
    let groupCount = 0;
    abortCtrl.signal.addEventListener('abort', () => reader.cancel().catch(() => {}), { once: true });

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.total !== undefined && !ev.group && !ev.done) {
          total = ev.total;
          progress.textContent = `Scanning ${total.toLocaleString()} tracks…`;
        } else if (ev.group) {
          groupCount++;
          const groupEl = _renderDuplicateGroup(ev.group);
          groupEl.classList.add('fade-in-up'); // groups stream in — match the track-card entrance
          list.appendChild(groupEl);
          progress.textContent = `Found ${groupCount.toLocaleString()} duplicate groups so far…`;
        } else if (ev.done) {
          const s = ev.summary;
          progress.style.display = 'none';
          if (s.groups === 0) {
            empty.style.display = '';
          } else {
            summary.style.display = '';
            // Collect every non-keeper across every group so the bulk
            // delete button can fire one POST instead of N.
            const allNonKeepers = [];
            list.querySelectorAll('.duplicates-group').forEach(g => {
              try { allNonKeepers.push(...JSON.parse(g.dataset.nonKeeperIds || '[]')); }
              catch (_) {}
            });
            summary.innerHTML = '';
            const textEl = document.createElement('div');
            textEl.id = 'duplicates-summary-text';
            textEl.style.cssText = 'flex:1;';
            textEl.innerHTML =
              `<strong>${s.groups.toLocaleString()} duplicate group${s.groups === 1 ? '' : 's'}</strong> ` +
              `· ${s.surplus.toLocaleString()} surplus copies of ${s.scanned.toLocaleString()} scanned tracks` +
              (s.skipped_empty > 0 ? ` · ${s.skipped_empty.toLocaleString()} empty-metadata tracks skipped` : '');
            const bulkBtn = document.createElement('button');
            bulkBtn.id = 'duplicates-bulk-delete-btn';
            bulkBtn.className = 'primary';
            bulkBtn.style.cssText = 'font-size:12px;padding:4px 12px;background:#e4384e;border-color:#e4384e;';
            bulkBtn.textContent = `Delete all ${allNonKeepers.length} non-keepers`;
            bulkBtn.addEventListener('click', () => {
              // Re-collect at click time — per-group keeper-radio changes
              // may have shifted which ids are non-keepers since the scan.
              const ids = [];
              list.querySelectorAll('.duplicates-group').forEach(g => {
                try { ids.push(...JSON.parse(g.dataset.nonKeeperIds || '[]')); }
                catch (_) {}
              });
              _openDuplicatesConfirm({
                track_ids: ids,
                label: `${list.querySelectorAll('.duplicates-group').length} keepers + ${ids.length} non-keepers`,
                meta: ' across the whole library',
                onSuccess: () => {
                  _onTracksDeleted(ids);
                  // Re-scan to reflect the now-shrunken DB ground truth.
                  summary.style.display = 'none';
                  list.innerHTML = '';
                  scanDuplicates();
                },
              });
            });
            summary.style.display = 'flex';
            summary.style.alignItems = 'center';
            summary.style.gap = '10px';
            summary.appendChild(textEl);
            summary.appendChild(bulkBtn);
          }
        }
      }
    }
  } catch (e) {
    // Failure must read as failure, not progress — red text + error toast
    progress.textContent = `Scan failed: ${e.message || e}`;
    progress.style.color = 'var(--danger, #e4384e)';
    showToast(`Duplicate scan failed: ${e.message || e}`, true);
  } finally {
    _setBtnLoading(btn, false);
    btn.textContent = 'Find duplicates';
    statusEl.style.display = 'none';
  }
}

// ── Duplicates: destructive delete (phase 2) ─────────────────────────────────
//
// All deletes route through this single confirm modal. The primary button is
// disabled for 250ms after open so an accidental Enter on the previous-focused
// element can't fire delete by mistake — mirrors the Discover download-confirm
// pattern. The actual POST happens in _runDuplicatesDelete.

let _duplicatesConfirmPending = null;
let _duplicatesPrimaryEnableTimer = null;
let _duplicatesDeleteAbort = null;   // WS5 — AbortController for the in-flight SSE delete
let _duplicatesDeleting = false;      // true while a delete SSE stream is running

// WS7 — centralised library-state invalidation. Every destructive delete
// (per-group or bulk) calls this with the deleted ids so the rest of the
// app doesn't keep stale references that 404 or mis-scope later. Surgical
// (O(deleted)) — avoids a full /api/tracks refetch that would reset the
// Cues-tab scroll + selection.
function _onTracksDeleted(ids) {
  if (!ids || !ids.length) return;
  const gone = new Set(ids.map(Number));
  if (Array.isArray(window.parsedTracks)) {
    parsedTracks = parsedTracks.filter(t => !gone.has(Number(t.id)));
  }
  if (window.parsedTracksById && typeof parsedTracksById.delete === 'function') {
    gone.forEach(id => parsedTracksById.delete(String(id)));
  }
  if (window.healthData) {
    gone.forEach(id => { delete healthData[String(id)]; });
  }
  // Refresh the duplicates summary counter + the bulk-delete label so they
  // stay honest after a per-group delete shrinks the set.
  _refreshDuplicatesSummaryAfterDelete();
}

// Recompute the bulk "Delete all N non-keepers" label + the summary counter
// from the remaining .duplicates-group cards in the DOM.
function _refreshDuplicatesSummaryAfterDelete() {
  const list = document.getElementById('duplicates-list');
  if (!list) return;
  const cards = Array.from(list.querySelectorAll('.duplicates-group'));
  let surplus = 0;
  for (const c of cards) {
    try { surplus += JSON.parse(c.dataset.nonKeeperIds || '[]').length; } catch (_) {}
  }
  const bulkBtn = document.getElementById('duplicates-bulk-delete-btn');
  if (bulkBtn) {
    bulkBtn.textContent = `Delete all ${surplus} non-keepers`;
    bulkBtn.disabled = surplus === 0;
  }
  const textEl = document.getElementById('duplicates-summary-text');
  if (textEl) {
    textEl.innerHTML =
      `<strong>${cards.length.toLocaleString()} duplicate group${cards.length === 1 ? '' : 's'}</strong> ` +
      `· ${surplus.toLocaleString()} surplus copies remaining`;
  }
  if (cards.length === 0) {
    const summary = document.getElementById('duplicates-summary');
    const empty = document.getElementById('duplicates-empty');
    if (summary) summary.style.display = 'none';
    if (empty) empty.style.display = '';
  }
}

// WS8 — focus trap: keep Tab/Shift+Tab cycling between the two modal
// buttons. Installed on open, removed on close.
let _duplicatesTrapHandler = null;
function _installDuplicatesFocusTrap() {
  const cancel = document.getElementById('duplicates-confirm-cancel');
  const go = document.getElementById('duplicates-confirm-go');
  _duplicatesTrapHandler = (ev) => {
    if (ev.key !== 'Tab') return;
    const focusables = [cancel, go].filter(b => b && !b.disabled);
    if (focusables.length === 0) { ev.preventDefault(); return; }
    const first = focusables[0], last = focusables[focusables.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault(); last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault(); first.focus();
    } else if (!focusables.includes(document.activeElement)) {
      ev.preventDefault(); first.focus();
    }
  };
  document.getElementById('duplicates-confirm')
    ?.addEventListener('keydown', _duplicatesTrapHandler);
}
function _removeDuplicatesFocusTrap() {
  if (_duplicatesTrapHandler) {
    document.getElementById('duplicates-confirm')
      ?.removeEventListener('keydown', _duplicatesTrapHandler);
    _duplicatesTrapHandler = null;
  }
}

function _openDuplicatesConfirm({ track_ids, label, meta, audioNote, onSuccess }) {
  if (!track_ids || track_ids.length === 0) return;
  _duplicatesConfirmPending = { track_ids, onSuccess };
  const modal = document.getElementById('duplicates-confirm');
  const backdrop = document.getElementById('duplicates-confirm-backdrop');
  const countEl = document.getElementById('duplicates-confirm-count');
  const metaEl = document.getElementById('duplicates-confirm-meta');
  const audioEl = document.getElementById('duplicates-confirm-audio');
  const goBtn = document.getElementById('duplicates-confirm-go');
  const progress = document.getElementById('duplicates-confirm-progress');
  countEl.textContent = `Delete ${track_ids.length} non-keeper${track_ids.length === 1 ? '' : 's'}`;
  metaEl.innerHTML = `<br><span style="color:var(--muted);font-size:12px;">${label || ''}${meta || ''}</span>`;
  if (audioEl) {
    if (audioNote) { audioEl.textContent = audioNote; audioEl.style.display = ''; }
    else audioEl.style.display = 'none';
  }
  if (progress) progress.style.display = 'none';
  modal.setAttribute('aria-hidden', 'false');
  backdrop.setAttribute('aria-hidden', 'false');
  goBtn.disabled = true;
  goBtn.textContent = 'Delete';
  document.getElementById('duplicates-confirm-cancel').textContent = 'Cancel';
  // Defeat accidental Enter held over from the previous focus target.
  clearTimeout(_duplicatesPrimaryEnableTimer);
  _duplicatesPrimaryEnableTimer = setTimeout(() => { goBtn.disabled = false; }, 250);
  _installDuplicatesFocusTrap();
  // Default focus to Cancel as the second safety layer.
  document.getElementById('duplicates-confirm-cancel').focus();
}

function _closeDuplicatesConfirm() {
  // If a delete is in flight, ESC/Cancel aborts it (WS8). The backend
  // honours the disconnect and the pre-delete backup still restores the
  // pre-session state, so an abort is always safe.
  if (_duplicatesDeleting && _duplicatesDeleteAbort) {
    _duplicatesDeleteAbort.abort();
    return; // the SSE consumer's finally closes the modal + toasts
  }
  _duplicatesConfirmPending = null;
  clearTimeout(_duplicatesPrimaryEnableTimer);
  _removeDuplicatesFocusTrap();
  document.getElementById('duplicates-confirm').setAttribute('aria-hidden', 'true');
  document.getElementById('duplicates-confirm-backdrop').setAttribute('aria-hidden', 'true');
}

async function _runDuplicatesDelete() {
  if (!_duplicatesConfirmPending || _duplicatesDeleting) return;
  const { track_ids, onSuccess } = _duplicatesConfirmPending;
  const goBtn = document.getElementById('duplicates-confirm-go');
  const cancelBtn = document.getElementById('duplicates-confirm-cancel');
  const progress = document.getElementById('duplicates-confirm-progress');
  const fill = document.getElementById('duplicates-confirm-progress-fill');
  const progLabel = document.getElementById('duplicates-confirm-progress-label');

  _duplicatesDeleting = true;
  _duplicatesDeleteAbort = new AbortController();
  goBtn.disabled = true;
  goBtn.textContent = 'Deleting…';
  cancelBtn.textContent = 'Cancel delete';   // ESC/Cancel now aborts the op
  if (progress) progress.style.display = '';

  let summary = null;
  try {
    const r = await fetch('/api/duplicates/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids, dry_run: false }),
      signal: _duplicatesDeleteAbort.signal,
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || r.statusText);
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    const total = track_ids.length;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.done) {
          summary = ev.summary;
        } else if (typeof ev.processed === 'number') {
          const pct = total ? Math.min(100, ev.processed / total * 100) : 100;
          if (fill) fill.style.width = `${pct}%`;
          if (progLabel) progLabel.textContent =
            `Deleted ${ev.deleted} of ${total}…`;
        }
      }
    }
    // Stream ended.
    _duplicatesDeleting = false;
    _duplicatesDeleteAbort = null;
    _removeDuplicatesFocusTrap();
    document.getElementById('duplicates-confirm').setAttribute('aria-hidden', 'true');
    document.getElementById('duplicates-confirm-backdrop').setAttribute('aria-hidden', 'true');
    _duplicatesConfirmPending = null;

    const s = summary || { deleted: 0, cancelled: false, backup_path: null };
    _showDuplicatesUndoToast(s, track_ids.length);
    if (typeof onSuccess === 'function') onSuccess(s);
  } catch (e) {
    _duplicatesDeleting = false;
    const aborted = e && e.name === 'AbortError';
    _duplicatesDeleteAbort = null;
    _removeDuplicatesFocusTrap();
    document.getElementById('duplicates-confirm').setAttribute('aria-hidden', 'true');
    document.getElementById('duplicates-confirm-backdrop').setAttribute('aria-hidden', 'true');
    _duplicatesConfirmPending = null;
    if (aborted) {
      // The backend commits rows staged before the disconnect, so SOME
      // may have been deleted. Safest move: re-scan so the panel reflects
      // ground truth, and tell the user the backup is intact.
      showToast('Delete cancelled — backup is intact. Re-scanning…');
      const list = document.getElementById('duplicates-list');
      const summaryEl = document.getElementById('duplicates-summary');
      if (list) list.innerHTML = '';
      if (summaryEl) summaryEl.style.display = 'none';
      scanDuplicates();
    } else {
      showToast(`Delete failed: ${e.message || e}`);
    }
  }
}

// WS5 — success toast with an inline "Undo this delete" button that
// restores the backup the delete just created. showToast renders plain
// text, so we build a richer transient banner pinned to the duplicates
// summary for 30s.
function _showDuplicatesUndoToast(summary, requested) {
  const deleted = summary.deleted || 0;
  const cancelled = !!summary.cancelled;
  const backupPath = summary.backup_path || null;
  const base = cancelled
    ? `Cancelled — ${deleted} of ${requested} deleted.`
    : `Deleted ${deleted} of ${requested} tracks.`;
  if (!backupPath) { showToast(base); return; }

  // Inline banner above the summary with an Undo button.
  const host = document.getElementById('duplicates-summary');
  if (!host) { showToast(`${base} Backup saved.`); return; }
  let banner = document.getElementById('duplicates-undo-banner');
  if (banner) banner.remove();
  banner = document.createElement('div');
  banner.id = 'duplicates-undo-banner';
  banner.className = 'fade-in-up';
  banner.style.cssText = 'position:relative;overflow:hidden;display:flex;align-items:center;gap:10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:8px 10px;margin-bottom:10px;';
  const text = document.createElement('span');
  text.style.flex = '1';
  text.textContent = `${base} Backup: ${backupPath.split('/').pop()}`;
  const undoBtn = document.createElement('button');
  undoBtn.className = 'secondary-btn';
  undoBtn.style.cssText = 'font-size:11px;padding:3px 10px;';
  undoBtn.textContent = 'Undo this delete';
  undoBtn.addEventListener('click', async () => {
    undoBtn.disabled = true;
    undoBtn.textContent = 'Restoring…';
    try {
      const r = await fetch('/api/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: backupPath.split('/').pop() }),
      });
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
      banner.remove();
      showToast('Restored from backup. Reload to see the recovered tracks.');
    } catch (e) {
      undoBtn.disabled = false;
      undoBtn.textContent = 'Undo this delete';
      showToast(`Restore failed: ${e.message || e}`);
    }
  });
  banner.appendChild(text);
  banner.appendChild(undoBtn);
  // Draining bar signals the 30s window — the banner used to vanish with no
  // warning, mid-reach for the Undo button.
  const drain = document.createElement('div');
  drain.style.cssText = 'position:absolute;left:0;bottom:0;height:2px;width:100%;background:var(--green);transition:width 30s linear;';
  banner.appendChild(drain);
  host.parentNode.insertBefore(banner, host);
  requestAnimationFrame(() => { drain.style.width = '0%'; });
  // Auto-dismiss after 30s — the backup is still in /api/backups if the
  // user wants it later. Fade out instead of snapping away.
  setTimeout(() => {
    if (!banner.isConnected) return;
    banner.style.transition = 'opacity var(--dur-chrome) ease';
    banner.style.opacity = '0';
    setTimeout(() => banner.remove(), 320);
  }, 30000);
}

(function _wireDuplicatesConfirm() {
  // Wire the modal's buttons exactly once at parse time — the IDs are
  // static and the listeners are idempotent against the
  // _duplicatesConfirmPending state, so re-binding on every panel render
  // would just leak listeners.
  document.getElementById('duplicates-confirm-cancel')
    ?.addEventListener('click', _closeDuplicatesConfirm);
  document.getElementById('duplicates-confirm-go')
    ?.addEventListener('click', _runDuplicatesDelete);
  document.getElementById('duplicates-confirm-backdrop')
    ?.addEventListener('click', _closeDuplicatesConfirm);
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    if (document.getElementById('duplicates-confirm')
        ?.getAttribute('aria-hidden') === 'false') {
      _closeDuplicatesConfirm();
    }
  });
})();

function _renderHealthSummary(s) {
  const summary   = document.getElementById('health-summary');
  const ring      = document.getElementById('health-score-ring');
  const titleEl   = document.getElementById('health-summary-title');
  const subEl     = document.getElementById('health-summary-sub');
  const issueList = document.getElementById('health-issue-list');
  const fixRow    = document.getElementById('health-fix-row');

  // Ease the report in when it first appears — it used to pop via a bare display flip
  if (summary.style.display === 'none') {
    summary.classList.add('fade-in-up');
    summary.addEventListener('animationend', () => summary.classList.remove('fade-in-up'), { once: true });
  }
  summary.style.display = '';
  const scannableCount = (s.total || 0) - (s.excluded_missing_audio || 0);
  const score = Math.round(s.library_score);
  if (scannableCount === 0) {
    ring.textContent = '—';
    ring.className = 'health-score-ring hsr-none';
  } else {
    ring.textContent = score;
    ring.className = 'health-score-ring ' +
      (score >= 90 ? 'hsr-good' : score >= 70 ? 'hsr-ok' : 'hsr-bad');
  }

  titleEl.textContent = scannableCount === 0 ? 'No scannable tracks' : `${score}/100 library health`;
  const excl = s.excluded_missing_audio || 0;
  subEl.textContent = `${scannableCount.toLocaleString()} track${scannableCount !== 1 ? 's' : ''} scanned`
    + (excl ? ` · ${excl} excluded (audio missing from disk)` : '');

  issueList.innerHTML = '';
  // scoreIssues affect hasIssues (block "looks great"); infoOnly rows are shown but don't block it
  const issueRows = [
    { count: s.no_cues,        icon: '✗', label: 'tracks have no hot cues',              cls: '#e4384e' },
    { count: excl,             icon: '✗', label: 'tracks — audio file missing',            cls: '#e4384e' },
    { count: s.duplicate_cues, icon: '⚠', label: 'tracks have duplicate cue positions' },
    { count: s.no_phrase,      icon: 'ℹ', label: 'tracks have no phrase analysis',        note: 'Re-analyze in Rekordbox' },
    { count: s.no_beatgrid,    icon: 'ℹ', label: 'tracks have no beat grid',              note: 'Re-analyze in Rekordbox' },
    { count: s.unnamed_cues,   icon: 'ℹ', label: 'tracks have unnamed cues' },
    { count: s.no_memory_cue,  icon: 'ℹ', label: 'tracks missing memory cue', infoOnly: true },
  ];
  let hasIssues = false;
  for (const row of issueRows) {
    if (!row.count) continue;
    if (!row.infoOnly) hasIssues = true;
    const el = document.createElement('div');
    el.className = 'health-issue-row';
    el.innerHTML =
      `<span class="health-issue-icon" style="${row.cls ? 'color:'+row.cls : ''}">${row.icon}</span>` +
      `<span class="health-issue-count">${row.count.toLocaleString()}</span>` +
      `<span class="health-issue-label">${row.label}</span>` +
      (row.note ? `<span class="health-fix-note">${row.note}</span>` : '');
    issueList.appendChild(el);
  }
  if (!hasIssues) {
    const ok = document.createElement('div');
    ok.className = 'health-issue-row';
    ok.innerHTML = `<span class="health-issue-icon" style="color:var(--green)">✓</span>`
      + `<span class="health-issue-label" style="color:var(--green)">No issues — library looks great</span>`;
    issueList.appendChild(ok);
  }

  // Split fix buttons: phrase-quality vs lower-confidence
  fixRow.innerHTML = '';
  const noCuesByTier = { phrase: [], bar: [], heuristic: [] };
  for (const [tid, report] of Object.entries(healthData)) {
    if ((report.issues || []).some(i => i.code === 'NO_CUES') && noCuesByTier[report.fix_tier]) {
      noCuesByTier[report.fix_tier].push(parseInt(tid));
    }
  }
  const phraseIds    = noCuesByTier.phrase;
  const lowerIds     = [...noCuesByTier.bar, ...noCuesByTier.heuristic];

  if (phraseIds.length) {
    const b = document.createElement('button');
    b.className = 'primary';
    b.style.fontSize = '13px';
    b.textContent = `Fix phrase-quality tracks (${phraseIds.length})`;
    b.addEventListener('click', () => _applyHealthFix(phraseIds, false, b));
    fixRow.appendChild(b);
  }
  if (lowerIds.length) {
    const b = document.createElement('button');
    b.className = 'secondary-btn';
    b.style.fontSize = '13px';
    b.textContent = `Fix remaining (${lowerIds.length} — bar/heuristic quality)`;
    b.addEventListener('click', () => _applyHealthFix(lowerIds, true, b));
    fixRow.appendChild(b);
  }
}

async function _applyHealthFix(trackIds, needsConfirm, srcBtn) {
  if (!trackIds.length) return;
  if (_healthFixInProgress) { showToast('A fix is already in progress — please wait'); return; }
  if (needsConfirm && !(await _confirmDialog(
    `Fix ${trackIds.length} track${trackIds.length !== 1 ? 's' : ''} using bar-interval or heuristic cues (lower confidence)?\nA backup will be saved before writing.`,
    { confirmLabel: 'Fix tracks' }
  ))) return;

  // Read DOM before setting the lock — a null-dereference here must not permanently lock the flag
  const maxCues      = parseInt(document.getElementById('max-cues').value) || 8;
  const barsInterval = parseInt(document.getElementById('bars-interval').value) || 16;
  const startBar     = parseInt(document.getElementById('start-bar').value) || 1;
  const memoryCueMode = document.getElementById('memory-cue-mode').value;
  // Progress goes to the button the user clicked + the health progress bar —
  // the old target (#download-btn) is display:none on the Library tab, so the
  // entire multi-track write used to run with zero visible feedback.
  const progBar = document.getElementById('health-progress-bar');
  const fill    = document.getElementById('health-progress-fill');
  const srcLabel = srcBtn ? srcBtn.textContent : '';
  const setFixProgress = (done) => {
    if (srcBtn) srcBtn.textContent = `Fixing… ${done} / ${trackIds.length}`;
    if (fill) fill.style.width = `${Math.round(100 * done / trackIds.length)}%`;
  };

  _healthFixInProgress = true;
  if (srcBtn) srcBtn.disabled = true;
  if (progBar) {
    progBar.style.display = '';
    progBar._hideTok = (progBar._hideTok || 0) + 1; // cancel any pending delayed-hide
  }
  if (fill) fill.style.width = '0%';
  setFixProgress(0);

  try {
    const r = await fetch('/api/generate-apply-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        track_ids: trackIds,
        mode: 'auto',
        bars_interval: barsInterval,
        start_bar: startBar,
        max_cues: maxCues,
        memory_cue_mode: memoryCueMode,
        overwrite: false,
        dry_run: false,
      }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.done) finalData = ev;
        else setFixProgress((ev.applied||0) + (ev.skipped||0) + (ev.errors||0));
      }
    }

    if (finalData) {
      const note = finalData.backup_path ? ' — backup saved' : '';
      showToast(`Fixed ${finalData.applied} track(s)${note}`, 'success');
      await scanLibraryHealth();  // rescan to reflect fixes (also resets the progress bar)
    }
  } catch (err) {
    showToast(`Fix failed: ${err.message}`, true);
  } finally {
    _healthFixInProgress = false;
    // On success the fix row is rebuilt by the rescan; on error restore the
    // clicked button so it doesn't stay stuck at "Fixing… N / M".
    if (srcBtn && srcBtn.isConnected) { srcBtn.disabled = false; srcBtn.textContent = srcLabel; }
    if (progBar) progBar.style.display = 'none';
  }
}

// ── Cue Library Tools ─────────────────────────────────────────────────────────

const CUE_COLOR_NAMES = ['—','Pink','Red','Orange','Yellow','Green','Aqua','Blue','Purple'];

function _initCueTools() {
  const opSel = document.getElementById('cue-tools-op');

  // Build slot-color selects for recolor panel
  const slotRow = document.getElementById('cue-recolor-slots');
  const SLOT_LABELS = ['A','B','C','D','E','F','G','H'];
  SLOT_LABELS.forEach((lbl, i) => {
    const item = document.createElement('div');
    item.className = 'slot-color-item';
    const sel = document.createElement('select');
    sel.id = `cue-recolor-slot-${i}`;
    CUE_COLOR_NAMES.forEach((name, ci) => {
      const opt = document.createElement('option');
      opt.value = ci;
      opt.textContent = name;
      sel.appendChild(opt);
    });
    // Defaults matching AutoCue convention
    const defaults = [5,7,2,4,1,6,3,8]; // A=Green,B=Blue,C=Red,D=Yellow,E=Pink,F=Aqua,G=Orange,H=Purple
    sel.value = defaults[i] || 0;
    item.appendChild(document.createTextNode(lbl));
    item.appendChild(sel);
    slotRow.appendChild(item);
  });

  opSel.addEventListener('change', _updateCueToolsParams);
  document.getElementById('cue-tools-run-btn').addEventListener('click', _runCueTools);
  document.getElementById('auto-tag-undo-btn').addEventListener('click', autoTagUndo);
}

function _updateCueToolsParams() {
  const op = document.getElementById('cue-tools-op').value;
  ['rename','recolor','shift','delete-orphan','auto-classify'].forEach(id => {
    document.getElementById(`cue-tools-params-${id}`).style.display = 'none';
  });
  const map = {rename:'rename', recolor:'recolor', shift:'shift', delete_orphan:'delete-orphan', auto_classify:'auto-classify'};
  document.getElementById(`cue-tools-params-${map[op]}`).style.display = 'flex';
  // Show/hide run button label depending on op
  const runBtn = document.getElementById('cue-tools-run-btn');
  runBtn.textContent = op === 'auto_classify' ? 'Tag visible tracks' : 'Run on visible tracks';
}

async function _runCueTools() {
  const op      = document.getElementById('cue-tools-op').value;
  if (op === 'auto_classify') { autoTagTracks(); return; }
  const dryRun  = document.getElementById('cue-tools-dry-run').checked;
  const btn     = document.getElementById('cue-tools-run-btn');
  const statusEl = document.getElementById('cue-tools-status');
  const progBar  = document.getElementById('cue-tools-progress');
  const progFill = document.getElementById('cue-tools-progress-fill');
  const resultEl = document.getElementById('cue-tools-result');
  const trackIds = activeTracks().map(t => parseInt(t.id));
  const total    = trackIds.length;

  if (!total) { showToast('No tracks to process'); return; }

  // Require confirmation before destructive writes (delete_orphan or shift when not dry-run)
  if (!dryRun && (op === 'delete_orphan' || op === 'shift')) {
    const opLabel = op === 'delete_orphan' ? 'delete cues' : 'shift cues';
    if (!(await _confirmDialog(
      `Apply ${opLabel} to ${total} track${total === 1 ? '' : 's'}? A backup will be created first.`,
      { confirmLabel: op === 'delete_orphan' ? 'Delete cues' : 'Shift cues', danger: true }
    ))) return;
  }

  // Build operation-specific params
  let opParams = {};
  if (op === 'rename') {
    const from = document.getElementById('cue-rename-from').value;
    const to   = document.getElementById('cue-rename-to').value;
    if (!from) { showToast('Enter a cue name to find'); return; }
    opParams = { rename: { from_name: from, to_name: to } };
  } else if (op === 'recolor') {
    const slotColors = {};
    for (let i = 0; i < 8; i++) {
      const v = parseInt(document.getElementById(`cue-recolor-slot-${i}`).value);
      if (v > 0) slotColors[String(i)] = v;  // skip "—" (0 = no change)
    }
    if (!Object.keys(slotColors).length) { showToast('Select at least one slot color'); return; }
    opParams = { recolor: { slot_colors: slotColors } };
  } else if (op === 'shift') {
    const ms = parseInt(document.getElementById('cue-shift-ms').value) || 0;
    if (ms === 0) { showToast('Enter a non-zero shift amount'); return; }
    opParams = { shift: { delta_ms: ms } };
  } else if (op === 'delete_orphan') {
    const keep = parseInt(document.getElementById('cue-keep-slots').value) || 4;
    opParams = { delete_orphan: { keep_slots: keep } };
  }

  const abortCtrl = new AbortController();
  _setBtnCancellable(btn, `Running… 0 / ${total}`, abortCtrl);
  statusEl.style.display = '';
  statusEl.textContent = dryRun ? 'Dry run — no changes will be written' : '';
  progBar.style.display = '';
  progFill.style.width = '0%';
  resultEl.style.display = 'none';

  try {
    const r = await fetch('/api/cue-tools-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ operation: op, track_ids: trackIds, dry_run: dryRun, ...opParams }),
      signal: abortCtrl.signal,
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }

    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    abortCtrl.signal.addEventListener('abort', function() { reader.cancel().catch(function(){}); }, { once: true });

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for (const part of parts) {
        for (const line of part.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.done) {
              const s = ev.summary;
              const verb = {rename:'renamed',recolor:'recolored',shift:'shifted',delete_orphan:'deleted'}[op] || 'changed';
              const dryTag = dryRun ? ' (dry run)' : '';
              resultEl.innerHTML = `<strong>${s.cues_changed} cue(s) ${verb}</strong> across ${s.tracks_affected} track(s)${dryTag}<br>
                <span style="color:var(--muted);font-size:12px">${s.cues_skipped} cues skipped · ${s.tracks_processed} tracks scanned${s.backup_path ? ' · backup saved' : ''}</span>`;
              resultEl.style.display = '';
              // Ease the result in — it used to pop via a bare display flip
              resultEl.classList.add('fade-in-up');
              resultEl.addEventListener('animationend', () => resultEl.classList.remove('fade-in-up'), { once: true });
              progFill.style.width = '100%';
              statusEl.style.display = 'none';
            } else {
              _setBtnCancellable(btn, `Running… ${ev.processed} / ${total}`, abortCtrl);
              progFill.style.width = `${Math.round(ev.processed / total * 100)}%`;
            }
          } catch {}
        }
      }
    }
    // flush residual
    for (const line of buf.split('\n')) {
      if (!line.startsWith('data: ')) continue;
      try {
        const ev = JSON.parse(line.slice(6));
        if (ev.done) {
          const s = ev.summary;
          const verb = {rename:'renamed',recolor:'recolored',shift:'shifted',delete_orphan:'deleted'}[op] || 'changed';
          const dryTag = dryRun ? ' (dry run)' : '';
          resultEl.innerHTML = `<strong>${s.cues_changed} cue(s) ${verb}</strong> across ${s.tracks_affected} track(s)${dryTag}<br>
            <span style="color:var(--muted);font-size:12px">${s.cues_skipped} cues skipped · ${s.tracks_processed} tracks scanned${s.backup_path ? ' · backup saved' : ''}</span>`;
          resultEl.style.display = '';
          resultEl.classList.add('fade-in-up');
          resultEl.addEventListener('animationend', () => resultEl.classList.remove('fade-in-up'), { once: true });
          progFill.style.width = '100%';
          statusEl.style.display = 'none';
        }
      } catch {}
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      showToast('Cue tools cancelled');
      statusEl.style.display = 'none';
    } else {
      showToast(`Cue tools failed: ${err.message}`, true);
      statusEl.style.display = 'none';
    }
  } finally {
    _setBtnLoading(btn, false);
    // Let the 100% fill paint before hiding (same completion beat as health scan)
    setTimeout(() => { progBar.style.display = 'none'; }, 400);
  }
}

// AutoCue tag name → CSS colour for track card pills
const AUTO_TAG_COLORS = {
  // DJ Category
  'Warmup':       '#4a9eff',
  'Build':        '#ff9800',
  'Peak':         '#ff4444',
  'After Hours':  '#aa77ff',
  'Closing':      '#4caf50',
  // Vocal
  'Vocal':        '#ff69b4',
  'Instrumental': '#00bcd4',
  // Energy Level
  'High Energy':  '#ff4444',
  'Mid Energy':   '#ffeb3b',
  'Low Energy':   '#4a9eff',
  // Energy Profile
  'Build Track':  '#ff9800',
  'Wave Track':   '#aa77ff',
  'Flat Track':   '#00bcd4',
  'Drop Track':   '#ff69b4',
  // Intro / Outro
  'Long Intro':   '#4caf50',
  'Short Intro':  '#ffeb3b',
  'Long Outro':   '#ff9800',
  'Short Outro':  '#ff69b4',
  // Decade
  '60s':          '#e91e63',
  '70s':          '#ff5722',
  '80s':          '#9c27b0',
  '90s':          '#3f51b5',
  '00s':          '#009688',
  '10s':          '#607d8b',
  '20s':          '#00bcd4',
  // BPM Tier
  '<120 BPM':     '#4a9eff',
  '120–124 BPM':  '#4caf50',
  '125–128 BPM':  '#ffeb3b',
  '129–135 BPM':  '#ff9800',
  '136–144 BPM':  '#ff5722',
  '>144 BPM':     '#ff4444',
  // Play History
  'Never Played':      '#607d8b',
  'Rarely Played':     '#ff9800',
  'Frequently Played': '#4caf50',
};

let _lastAutoTagUndoData = null;

async function autoTagTracks() {
  const btn     = document.getElementById('cue-tools-run-btn');
  const resultEl = document.getElementById('cue-tools-result');
  const dryRun  = document.getElementById('cue-tools-dry-run').checked;
  const trackIds = activeTracks().map(t => parseInt(t.id));
  const total   = trackIds.length;

  const tagTypeMap = {
    'at-category':      'category',
    'at-vocal':         'vocal',
    'at-energy-level':  'energy_level',
    'at-energy-profile':'energy_profile',
    'at-intro-outro':   'intro_outro',
    'at-decade':        'decade',
    'at-bpm-tier':      'bpm_tier',
    'at-play-history':  'play_history',
  };
  const tagTypes = Object.entries(tagTypeMap)
    .filter(([id]) => document.getElementById(id)?.checked)
    .map(([, val]) => val);

  if (!tagTypes.length) { showToast('Select at least one tag type'); return; }
  if (!total) { showToast('No tracks to process'); return; }

  btn.disabled = true;
  btn.textContent = 'Tagging…';
  resultEl.style.display = 'none';

  try {
    const r = await fetch('/api/auto-tag', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: trackIds, tag_types: tagTypes, overwrite: true, dry_run: dryRun }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    const dryTag = dryRun ? ' (dry run)' : '';
    resultEl.innerHTML = `<strong>${d.tagged} track(s) tagged${dryTag}</strong><br>
      <span style="color:var(--muted);font-size:12px">${d.skipped_no_data} skipped (no data) · ${d.errors} errors</span>`;
    resultEl.style.display = '';

    if (!dryRun && d.undo_data) {
      _lastAutoTagUndoData = d.undo_data;
      const undoRow = document.getElementById('auto-tag-undo-row');
      undoRow.style.display = 'flex';
      document.getElementById('auto-tag-undo-status').textContent = '';
    }
  } catch (err) {
    showToast(`Auto-tag failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Tag visible tracks';
  }
}

async function autoTagUndo() {
  if (!_lastAutoTagUndoData) { showToast('Nothing to undo'); return; }
  const btn    = document.getElementById('auto-tag-undo-btn');
  const status = document.getElementById('auto-tag-undo-status');
  btn.disabled = true;
  status.textContent = 'Undoing…';
  try {
    const r = await fetch('/api/auto-tag/undo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ undo_data: _lastAutoTagUndoData }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    status.textContent = `Undone — ${d.removed} removed, ${d.restored} restored`;
    _lastAutoTagUndoData = null;
    document.getElementById('auto-tag-undo-row').style.display = 'none';
  } catch (err) {
    showToast(`Undo failed: ${err.message}`);
    status.textContent = '';
  } finally {
    btn.disabled = false;
  }
}

async function discogsTagTracks() {
  const btn       = document.getElementById('discogs-run-btn');
  const statusEl  = document.getElementById('discogs-status');
  const resultEl  = document.getElementById('discogs-result');
  const fillEl    = document.getElementById('discogs-progress-fill');
  const token     = (document.getElementById('discogs-token')?.value || '').trim();
  const dryRun       = document.getElementById('discogs-dry-run')?.checked ?? false;
  const skipExisting = document.getElementById('discogs-skip-existing')?.checked ?? true;
  const trackIds  = activeTracks().map(t => parseInt(t.id));
  const total     = trackIds.length;

  if (!token) { showToast('Paste your Discogs token first'); return; }
  if (!total)  { showToast('No tracks to process'); return; }

  let tagged = 0, skipped = 0, errors = 0;

  const abortCtrl = new AbortController();
  const _discogsCancelConfirm = () => {
    if (tagged === 0) return true;  // nothing done yet — cancel immediately, no prompt
    return _confirmDialog(
      `Stop Discogs tagging?\n\n${tagged} track${tagged === 1 ? '' : 's'} already tagged — those changes are saved.`,
      { confirmLabel: 'Stop tagging' }
    );
  };
  _setBtnCancellable(btn, `Tagging… 0 / ${total}`, abortCtrl, _discogsCancelConfirm);
  resultEl.textContent = '';
  fillEl.style.width = '0%';

  try {
    const r = await fetch('/api/auto-tag/discogs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: trackIds, token, dry_run: dryRun, skip_existing: skipExisting }),
      signal: abortCtrl.signal,
    });
    if (!r.ok) { const e = await r.json(); throw new Error(e.detail || r.statusText); }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    abortCtrl.signal.addEventListener('abort', function() { reader.cancel().catch(function(){}); }, { once: true });

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const d = JSON.parse(line.slice(5).trim());
        if (d.done) {
          tagged  = d.tagged;
          skipped = d.skipped;
          errors  = d.errors;
          fillEl.style.width = '100%';
        } else {
          const pct = Math.round((d.processed / total) * 100);
          fillEl.style.width = pct + '%';
          tagged = d.tagged ?? tagged;
          errors = d.errors ?? errors;
          _setBtnCancellable(btn, `Tagging… ${d.processed} / ${total}`, abortCtrl, _discogsCancelConfirm);
        }
      }
    }
    if (abortCtrl.signal.aborted) throw new DOMException('Aborted', 'AbortError');

    const label = dryRun ? ' (dry run)' : '';
    resultEl.textContent = `Done${label}: ${tagged} tagged · ${skipped} no results · ${errors} errors`;
    if (!dryRun) showToast(`Discogs tags written for ${tagged} tracks`);
  } catch (err) {
    if (err.name === 'AbortError') {
      resultEl.textContent = `Stopped at ${tagged} tagged · ${skipped} skipped`;
      if (tagged > 0 && !dryRun) showToast(`Discogs tagging stopped — ${tagged} tracks saved`);
      else showToast('Discogs tagging cancelled');
    } else {
      showToast(`Discogs error: ${err.message}`);
      resultEl.textContent = `Error: ${err.message}`;
    }
  } finally {
    _setBtnLoading(btn, false);
  }
}

const _DISCOGS_TOKEN_KEY = 'autocue_discogs_token';

function discogsSaveToken() {
  const inp = document.getElementById('discogs-token');
  const token = (inp?.value || '').trim();
  const saveStatus = document.getElementById('discogs-save-status');
  if (!token) { saveStatus.textContent = 'Paste a token first'; saveStatus.style.color = 'var(--red, #f55)'; return; }
  localStorage.setItem(_DISCOGS_TOKEN_KEY, token);
  saveStatus.textContent = 'Saved — testing…';
  saveStatus.style.color = 'var(--muted)';
  fetch('/api/auto-tag/discogs/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  }).then(r => r.json().then(d => ({ ok: r.ok, d }))).then(({ ok, d }) => {
    if (ok) {
      saveStatus.textContent = `✓ Connected as ${d.username || 'user'}`;
      saveStatus.style.color = 'var(--green)';
    } else {
      saveStatus.textContent = `✗ ${d.detail || 'invalid token'} — token saved`;
      saveStatus.style.color = 'var(--red, #f55)';
    }
  }).catch(err => {
    saveStatus.textContent = `✗ ${err.message}`;
    saveStatus.style.color = 'var(--red, #f55)';
  });
}

function discogsLoadSavedToken() {
  const saved = localStorage.getItem(_DISCOGS_TOKEN_KEY);
  const inp = document.getElementById('discogs-token');
  const saveStatus = document.getElementById('discogs-save-status');
  if (saved) {
    if (inp) inp.value = saved;
    if (saveStatus) { saveStatus.textContent = 'Token loaded from local storage'; saveStatus.style.color = 'var(--muted)'; }
  } else {
    // Try to load from server config (reads .env on the server side)
    fetch('/api/config').then(r => r.ok ? r.json() : null).then(d => {
      if (d && d.discogs_token) {
        if (inp) inp.value = d.discogs_token;
        if (saveStatus) { saveStatus.textContent = 'Token loaded from .env — click Save & Test to persist'; saveStatus.style.color = 'var(--muted)'; }
      }
    }).catch(() => {});
  }
}

// ── Discover (new releases) + Download ────────────────────────────────────────

let _downloadConfig = { available: false, ffmpeg: false, default_dir: '' };
var _dlDestDir = '';   // active download destination (music folder or AutoCue default)

function _discoverToken() {
  const inp = document.getElementById('discogs-token');
  return (inp && inp.value.trim()) || localStorage.getItem(_DISCOGS_TOKEN_KEY) || '';
}

// Read an SSE response body (fetch + ReadableStream) and invoke onEvent per JSON event.
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
    buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data:')) continue;
      try { onEvent(JSON.parse(line.slice(5).trim())); } catch { /* ignore partial */ }
    }
  }
  // Surface abort so callers can show "Cancelled" feedback
  if (signal && signal.aborted) throw new DOMException('Aborted', 'AbortError');
}

// ── _Download IIFE (PRD .agent/prd/DOWNLOAD_PRD.md v1.0) ─────────────────────
// Single canonical download driver consumed by every surface:
//   - #download-section (manual panel)
//   - .disc-dl-btn (per-Discover-card ⬇ Album)
//   - #ti-download → #yt-modal (track-info + YouTube candidate picker)
//   - #disc-v2-dl-confirm (Shift+click confirm)
// State machine: idle → loading → success | error → idle.
// Each job: enqueue (POST /api/download/enqueue) → stream (GET /api/download/
// stream/{job_id}) → cancel via POST /api/download/cancel/{job_id} +
// AbortController.abort(). 410 already_consumed renders from cached payload.
window._Download = (function() {
  const seenDoneFor = new Set();

  function _classifyDownloadTarget(q) {
    const s = (q || '').trim();
    if (!s) return 'invalid';
    if (s.includes('\n') || s.includes('\r')) return 'invalid';
    const isUrl = /^https?:\/\//i.test(s);
    if (!isUrl) return 'search';
    let listMatch = /[?&]list=([A-Za-z0-9_-]+)/i.exec(s);
    let vMatch    = /[?&]v=([A-Za-z0-9_-]{6,})/i.exec(s);
    if (listMatch && vMatch) return 'mixed_video_in_playlist';
    if (listMatch)            return 'playlist';
    if (vMatch || /youtu\.be\/[A-Za-z0-9_-]{6,}/.test(s)) return 'single_video';
    return 'single_video';
  }

  async function _enqueue(endpoint, body, ctrl) {
    const resp = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = new Error((json && json.detail && json.detail.error_message)
        || json.error_message || (typeof json.detail === 'string' ? json.detail : '')
        || `HTTP ${resp.status}`);
      err.code = (json && json.detail && json.detail.error_code) || json.error_code;
      err.hint = (json && json.detail && json.detail.hint) || json.hint;
      err.status = resp.status;
      throw err;
    }
    return json;
  }

  function start(args) {
    const ctrl = new AbortController();
    const onState = args.onState || function() {};
    let jobId = null;
    let retriedNormalizeFlip = false;
    let finished = false;

    onState({ phase: 'queued', percent: null });

    async function go(body) {
      const endpoint = body.tracks ? '/api/download/album/enqueue' : '/api/download/enqueue';
      let enq;
      try {
        enq = await _enqueue(endpoint, body, ctrl);
      } catch (err) {
        // Auto-retry once on normalize_unsupported_for_original (PRD §6.4 round-5 Min-2)
        if (err.code === 'normalize_unsupported_for_original' && !retriedNormalizeFlip) {
          retriedNormalizeFlip = true;
          body.audio_format = 'mp3_320';
          body.normalize = body.normalize !== false;  // keep normalize=true
          try {
            try { localStorage.setItem('autocue_dl_format', 'mp3_320'); } catch (_) {}
            if (typeof showToast === 'function') {
              showToast('Normalization requires MP3 320 or WAV — switched to MP3 320');
            }
            // Reflect in UI dropdown if present
            const sel = document.getElementById('dl-format');
            if (sel) sel.value = 'mp3_320';
          } catch (_) {}
          return go(body);
        }
        if (err.name === 'AbortError') {
          if (!finished) { finished = true; onState({ status: 'cancelled', phase: 'done', type: 'done' }); }
          return;
        }
        if (!finished) {
          finished = true;
          onState({ type: 'done', status: 'error',
                    error_code: err.code || 'unknown',
                    error_message: err.message,
                    error_hint: err.hint });
        }
        return;
      }
      jobId = enq.job_id;
      try { onState({ phase: 'queued', percent: null, job_id: jobId }); } catch (_) {}
      if (typeof window._dlKickQueuePoller === 'function') window._dlKickQueuePoller();

      // Open SSE stream — handle 410 already_consumed explicitly per PRD §7.3.
      let stream;
      try {
        stream = await fetch(`/api/download/stream/${jobId}`, { signal: ctrl.signal });
      } catch (err) {
        if (err.name !== 'AbortError') {
          finished = true;
          onState({ type: 'done', status: 'error', error_code: 'network',
                    error_message: err.message });
        }
        return;
      }
      if (stream.status === 410) {
        let cached = {};
        try { cached = await stream.json(); } catch (_) {}
        finished = true;
        onState({ type: 'done', status: cached.status || 'success',
                  path: cached.path, from_cache: true, job_id: jobId });
        return;
      }
      if (!stream.ok) {
        finished = true;
        onState({ type: 'done', status: 'error', error_code: 'http',
                  error_message: `HTTP ${stream.status}` });
        return;
      }

      try {
        await _consumeSSE(stream, function(ev) {
          if (ev.type === 'done' && seenDoneFor.has(ev.job_id || jobId)) return;
          if (ev.type === 'done') {
            seenDoneFor.add(ev.job_id || jobId);
            finished = true;
          }
          onState(ev);
        }, ctrl.signal);
      } catch (err) {
        if (err.name !== 'AbortError' && !finished) {
          finished = true;
          onState({ type: 'done', status: 'error', error_code: 'stream',
                    error_message: err.message });
        }
      }
    }

    // Build request body from args
    const body = args.tracks ? {
      tracks: args.tracks,
      dest_dir: args.dest || undefined,
      audio_format: args.format || 'mp3_320',
      normalize: !!args.normalize,
      embed_metadata: args.embedMeta !== false,
    } : {
      query: args.query,
      dest_dir: args.dest || undefined,
      audio_format: args.format || 'mp3_320',
      normalize: !!args.normalize,
      embed_metadata: args.embedMeta !== false,
      allow_playlist: !!args.allowPlaylist,
    };

    go(body);

    return {
      get id() { return jobId; },
      async cancel() {
        try {
          if (jobId) {
            await fetch(`/api/download/cancel/${jobId}`, { method: 'POST' }).catch(function(){});
          }
        } finally {
          ctrl.abort();
        }
      },
      // For tests / clients that want to know the args originally passed
      _args: args,
    };
  }

  // ---- View helpers ----
  function _esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function _formatBytes(b) {
    if (b == null) return '';
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
    if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB';
    return (b / 1073741824).toFixed(2) + ' GB';
  }

  function renderState(regionEl, state, ctx) {
    if (!regionEl) return;
    ctx = ctx || {};
    const phase = state.phase || (state.type === 'done' ? 'done' : null);

    if (state.type === 'done' && state.status === 'success') {
      const path = state.path || (ctx.lastPath || '');
      const folder = path ? path.replace(/[/\\][^/\\]+$/, '') : '';
      const showReveal = ctx.osRevealSupported !== false && path;
      regionEl.innerHTML = `
        <div class="dl-status-card" data-state="success" role="status">
          <div><strong>✓ Saved</strong> ${ctx.formatLabel ? '<span class="dl-status-line">as ' + _esc(ctx.formatLabel) + '</span>' : ''}</div>
          ${path ? '<div class="dl-status-line"><code>' + _esc(path) + '</code></div>' : ''}
          <div class="dl-status-actions">
            ${showReveal ? '<button type="button" data-dl-action="reveal">Reveal in Finder</button>' : ''}
            ${path ? '<button type="button" data-dl-action="copy-path">Copy path</button>' : ''}
            <button type="button" data-dl-action="reset" class="primary">Download another</button>
          </div>
        </div>`;
      regionEl.querySelectorAll('[data-dl-action]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          const action = btn.dataset.dlAction;
          if (action === 'reveal' && path) {
            fetch('/api/download/reveal', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ path: path }),
            }).then(function(r) {
              if (!r.ok) { if (typeof showToast === 'function') showToast("Couldn't open file manager"); }
            });
          } else if (action === 'copy-path' && path) {
            (navigator.clipboard ? navigator.clipboard.writeText(path) : Promise.reject())
              .then(function() { if (typeof showToast === 'function') showToast('Path copied'); })
              .catch(function() { if (typeof showToast === 'function') showToast("Couldn't copy"); });
          } else if (action === 'reset' && ctx.onReset) {
            ctx.onReset();
          }
        });
      });
    } else if (state.type === 'done' && state.status === 'error') {
      const msg = state.error_message || 'Something went wrong.';
      const hint = state.error_hint || '';
      const raw = state.error_raw || '';
      regionEl.innerHTML = `
        <div class="dl-status-card" data-state="error" role="alert">
          <div><strong>Couldn't download.</strong> ${_esc(msg)}</div>
          ${hint && hint !== 'auto_switch_to_mp3_320' ? '<div class="dl-status-line">' + _esc(hint) + '</div>' : ''}
          <div class="dl-status-actions">
            ${ctx.onRetry ? '<button type="button" data-dl-action="retry" class="primary">Retry</button>' : ''}
            <button type="button" data-dl-action="dismiss">Dismiss</button>
          </div>
          ${raw ? '<details><summary>Show technical details</summary><pre>' + _esc(raw) + '</pre></details>' : ''}
        </div>`;
      regionEl.querySelectorAll('[data-dl-action]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          if (btn.dataset.dlAction === 'retry' && ctx.onRetry) ctx.onRetry();
          if (btn.dataset.dlAction === 'dismiss' && ctx.onReset) ctx.onReset();
        });
      });
    } else if (state.type === 'done' && state.status === 'cancelled') {
      regionEl.innerHTML = `
        <div class="dl-status-card" data-state="loading"><div class="dl-status-line">Cancelled</div></div>`;
      setTimeout(function() { if (regionEl && ctx.onReset) ctx.onReset(); }, 1200);
    } else if (phase) {
      const percent = (state.percent == null) ? null : Math.max(0, Math.min(100, state.percent));
      const totalTracks = state.total > 1 ? state.total : null;
      const proc = state.processed || 0;
      const title = state.current_title || state.current_query || '';
      const phaseText = ({
        queued: 'Queued…',
        fetching: 'Downloading…',
        converting: 'Converting…',
        normalizing_pass1: 'Measuring loudness…',
        normalizing_pass2: 'Normalizing loudness…',
        tagging: 'Writing metadata…',
      })[phase] || phase;
      // Update the card in place — rebuilding it per SSE event recreated the
      // <progress> element (so its width transition never fired and the bar
      // stuttered) and destroyed the Cancel button mid-focus.
      let card = regionEl.querySelector('.dl-status-card[data-state="loading"]');
      if (!card) {
        regionEl.innerHTML = `
        <div class="dl-status-card" data-state="loading">
          <div><strong data-dl-phase></strong> <span class="dl-status-line" data-dl-pct></span></div>
          <progress max="100" aria-label="Download progress"></progress>
          <div class="dl-status-line" data-dl-batch style="display:none"></div>
          <div class="dl-status-actions">
            <button type="button" data-dl-action="cancel">Cancel</button>
          </div>
        </div>`;
        card = regionEl.querySelector('.dl-status-card');
        card.querySelectorAll('[data-dl-action="cancel"]').forEach(function(btn) {
          btn.addEventListener('click', function() { if (ctx.onCancel) ctx.onCancel(); });
        });
      }
      card.querySelector('[data-dl-phase]').textContent = phaseText;
      card.querySelector('[data-dl-pct]').textContent = percent != null ? percent.toFixed(0) + '%' : '';
      const progEl = card.querySelector('progress');
      if (percent != null) progEl.value = percent;
      else progEl.removeAttribute('value');
      const batchEl = card.querySelector('[data-dl-batch]');
      if (totalTracks) {
        batchEl.style.display = '';
        batchEl.textContent = `Track ${proc + 1} of ${totalTracks}${title ? ' · ' + title : ''}`;
      } else {
        batchEl.style.display = 'none';
      }
    } else {
      regionEl.innerHTML = '';
    }
  }

  // ---- Surface bindings ----
  function bindManualPanel(rootEl) {
    rootEl = rootEl || document.getElementById('download-section');
    if (!rootEl) return;
    const region = rootEl.querySelector('#dl-status-region');
    const queryEl = rootEl.querySelector('#dl-query');
    const formatEl = rootEl.querySelector('#dl-format');
    const normEl = rootEl.querySelector('#dl-normalize');
    const metaEl = rootEl.querySelector('#dl-embed-meta');
    const goBtn = rootEl.querySelector('#dl-go-btn');
    const controls = rootEl.querySelector('#download-controls');
    const wavWarning = rootEl.querySelector('#dl-wav-warning');
    const wavDismiss = rootEl.querySelector('#dl-wav-warning-dismiss');
    let currentJob = null;
    let lastArgs = null;

    function _readFormat() {
      const v = formatEl ? formatEl.value : 'mp3_320';
      try { localStorage.setItem('autocue_dl_format', v); } catch (_) {}
      return v;
    }
    function _formatLabel(v) {
      return { mp3_320: 'MP3 320', wav: 'WAV', original: 'Original' }[v] || v;
    }

    function _refreshNormalizeAvailability() {
      const fmt = formatEl ? formatEl.value : 'mp3_320';
      if (!normEl) return;
      if (fmt === 'original') {
        normEl.checked = false;
        normEl.disabled = true;
        normEl.setAttribute('aria-disabled', 'true');
        if (normEl.parentElement) normEl.parentElement.title = 'Available only for WAV / MP3 320';
      } else {
        normEl.disabled = false;
        normEl.removeAttribute('aria-disabled');
        if (normEl.parentElement) normEl.parentElement.removeAttribute('title');
      }
    }

    function _reset() {
      if (region) region.innerHTML = '';
      if (queryEl) { queryEl.value = ''; queryEl.focus(); }
      if (controls) controls.removeAttribute('aria-busy');
      currentJob = null;
    }

    function _start() {
      if (currentJob) return;
      const q = (queryEl ? queryEl.value : '').trim();
      if (!q) {
        if (typeof showToast === 'function') showToast('Enter a URL or search term');
        return;
      }
      // Route bare-text search through the YouTube candidate picker instead
      // of letting yt-dlp auto-pick result #1 (which often surfaces a random
      // video for ambiguous queries). URL inputs keep the direct flow.
      // PRP: search→modal route, prp-core/prp-implement.
      const targetKind = _classifyDownloadTarget(q);
      if (targetKind === 'search' && typeof openYoutubeModalForQuery === 'function') {
        openYoutubeModalForQuery(q);
        return;
      }
      const fmt = _readFormat();
      const args = {
        query: q,
        format: fmt,
        normalize: normEl ? normEl.checked : false,
        embedMeta: metaEl ? metaEl.checked : true,
        dest: (typeof _dlDestDir !== 'undefined' && _dlDestDir) || undefined,
        allowPlaylist: ['playlist', 'mixed_video_in_playlist'].includes(targetKind),
      };
      lastArgs = args;
      if (controls) controls.setAttribute('aria-busy', 'true');
      currentJob = start(Object.assign({}, args, {
        onState: function(state) {
          renderState(region, state, {
            osRevealSupported: (window._downloadConfig || {}).os_reveal_supported,
            formatLabel: _formatLabel(fmt),
            onCancel: function() { if (currentJob) currentJob.cancel(); },
            onRetry: function() { _reset(); currentJob = null; queryEl && (queryEl.value = q); _start(); },
            onReset: function() { _reset(); },
          });
          if (state.type === 'done') {
            if (controls) controls.removeAttribute('aria-busy');
            // Job done — null currentJob so a subsequent retry/reset works.
            currentJob = null;
          }
        },
      }));
    }

    if (goBtn) goBtn.addEventListener('click', function(e) { e.preventDefault(); _start(); });
    if (queryEl) queryEl.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _start(); }
    });
    if (formatEl) {
      formatEl.addEventListener('change', function() {
        _refreshNormalizeAvailability();
        _readFormat();
        if (formatEl.value === 'wav' && wavWarning) {
          let seen = false;
          try { seen = sessionStorage.getItem('autocue_dl_seen_wav_warning') === '1'; } catch (_) {}
          if (!seen) wavWarning.hidden = false;
        }
      });
    }
    if (wavDismiss && wavWarning) wavDismiss.addEventListener('click', function() {
      wavWarning.hidden = true;
      try { sessionStorage.setItem('autocue_dl_seen_wav_warning', '1'); } catch (_) {}
    });
    // Restore last format from localStorage; coerce legacy values.
    if (formatEl) {
      let saved = null;
      try { saved = localStorage.getItem('autocue_dl_format'); } catch (_) {}
      const legacy = { mp3: 'mp3_320', m4a: 'original', aac: 'original',
                       opus: 'original', flac: 'wav', alac: 'wav', vorbis: 'wav' };
      if (saved && legacy[saved]) {
        formatEl.value = legacy[saved];
        try { localStorage.setItem('autocue_dl_format', legacy[saved]); } catch (_) {}
        if (typeof showToast === 'function') {
          showToast(`Your saved format is now ${_formatLabel(legacy[saved])}. Change in the Format dropdown.`);
        }
      } else if (saved && ['wav', 'mp3_320', 'original'].includes(saved)) {
        formatEl.value = saved;
      }
      _refreshNormalizeAvailability();
    }

    // Public API for tests / external integration
    return { _start: _start, _reset: _reset,
             get currentJob() { return currentJob; },
             get lastArgs() { return lastArgs; } };
  }

  function bindCardButton(btnEl, query, opts) {
    if (!btnEl) return;
    opts = opts || {};
    let job = null;
    const orig = btnEl.textContent;
    let inlineStatus = btnEl.parentElement && btnEl.parentElement.querySelector('.disc-dl-status');
    let inlineBar    = btnEl.parentElement && btnEl.parentElement.querySelector('.disc-dl-bar');
    let inlineProg   = btnEl.parentElement && btnEl.parentElement.querySelector('.disc-dl-progress');
    btnEl.addEventListener('click', function() {
      if (job) { job.cancel(); return; }
      if (inlineProg) inlineProg.style.display = '';
      btnEl.textContent = 'Cancel';
      job = start({
        query: query,
        format: opts.format || (function() { try { return localStorage.getItem('autocue_dl_format') || 'mp3_320'; } catch(_){ return 'mp3_320'; } })(),
        normalize: !!opts.normalize,
        embedMeta: opts.embedMeta !== false,
        dest: opts.dest || (typeof _dlDestDir !== 'undefined' && _dlDestDir) || undefined,
        onState: function(state) {
          if (state.type === 'done') {
            if (state.status === 'success') {
              btnEl.textContent = '✓ Saved';
              if (inlineStatus) { inlineStatus.textContent = '✓ saved'; inlineStatus.style.color = 'var(--green)'; }
              if (inlineBar) inlineBar.style.width = '100%';
            } else if (state.status === 'cancelled') {
              btnEl.textContent = orig;
              if (inlineStatus) inlineStatus.textContent = 'cancelled';
            } else {
              btnEl.textContent = orig;
              if (inlineStatus) { inlineStatus.textContent = '✗ failed'; inlineStatus.style.color = 'var(--red, #e05252)'; }
              if (typeof showToast === 'function') showToast('Download failed: ' + (state.error_message || ''));
            }
            job = null;
            setTimeout(function() { if (inlineProg) inlineProg.style.display = 'none'; }, 1500);
          } else if (typeof state.percent === 'number') {
            if (inlineBar) inlineBar.style.width = state.percent + '%';
            if (inlineStatus) inlineStatus.textContent = Math.round(state.percent) + '%';
          } else if (state.phase) {
            if (inlineStatus) inlineStatus.textContent = state.phase.replace('_', ' ');
          }
        },
      });
    });
  }

  return {
    start: start,
    bindManualPanel: bindManualPanel,
    bindCardButton: bindCardButton,
    renderState: renderState,
    _classifyDownloadTarget: _classifyDownloadTarget,
  };
})();

function initDiscover() {
  // Default "released since" to last year (v1 control, retained for the
  // Download panel below — Discover v2 has its own filter bar).
  const yearInput = document.getElementById('disc-since-year');
  if (yearInput && !yearInput.value) yearInput.value = String(new Date().getFullYear() - 1);

  // T-024: the v1 "Scan library" button (#disc-scan-btn) was removed from the
  // DOM when the Discover tab was rewritten. Its click handler is now wired
  // up in initDiscoverV2(). The download button below still belongs to the
  // shared YouTube download panel.
  // Wire the manual panel through the canonical _Download IIFE.
  // (Old downloadManual / runDownload functions are kept further down but
  // unreferenced; they are slated for deletion in a follow-up cleanup pass.)
  if (window._Download) window._Download.bindManualPanel(document.getElementById('download-section'));

  // Probe download tool availability and reveal the matching UI.
  fetch('/api/download/config').then(r => r.ok ? r.json() : null).then(cfg => {
    if (!cfg) return;
    _downloadConfig = cfg;
    window._downloadConfig = cfg;
    _dlDestDir = cfg.music_folder || cfg.default_dir || '~/Music/AutoCue';
    const ready = cfg.available && cfg.ffmpeg;
    const controls = document.getElementById('download-controls');
    const unavail  = document.getElementById('download-unavailable');
    if (controls) controls.hidden = !ready;
    if (unavail) unavail.hidden = ready;
    const dest = document.getElementById('dl-dest');
    if (dest) dest.textContent = _dlDestDir;
    if (cfg.music_folder && cfg.default_dir && cfg.music_folder !== cfg.default_dir) {
      const switcher = document.getElementById('dl-dest-switch');
      if (switcher) {
        switcher.hidden = false;
        switcher.dataset.primary = cfg.music_folder;
        switcher.dataset.alt = cfg.default_dir;
        switcher.textContent = 'Switch to AutoCue folder';
      }
    }
    // Start the queue poller (paused when tab hidden)
    _startQueuePoller();
  }).catch(() => {});
}

// Queue indicator (PRD §6.12 + round-4 m1) — polls only while ≥ 1 job in flight
// AND tab is visible. Otherwise idles. Exposed _Download.kickQueuePoller() is
// called by the IIFE whenever a new job is enqueued, so the poller starts
// on demand instead of running unconditionally every 2 s.
let _dlQueuePollId = null;
let _dlQueueIdleTicks = 0;
const _DL_QUEUE_IDLE_MAX = 2;  // stop after 2 consecutive empty polls

function _stopQueuePoller() {
  if (_dlQueuePollId != null) { clearInterval(_dlQueuePollId); _dlQueuePollId = null; }
  _dlQueueIdleTicks = 0;
}

function _startQueuePoller() {
  function tick() {
    if (document.visibilityState !== 'visible') return;
    fetch('/api/download/queue').then(r => r.ok ? r.json() : null).then(snap => {
      const ind = document.getElementById('dl-queue-indicator');
      const txt = document.getElementById('dl-queue-text');
      if (!ind || !txt || !snap) return;
      const total = (snap.active ? snap.active.length : 0) + (snap.queued_count || 0);
      if (total === 0) {
        ind.hidden = true;
        _dlQueueIdleTicks++;
        if (_dlQueueIdleTicks >= _DL_QUEUE_IDLE_MAX) _stopQueuePoller();
        return;
      }
      _dlQueueIdleTicks = 0;
      ind.hidden = false;
      txt.textContent = `${snap.active.length} active · ${snap.queued_count} queued (max ${snap.max_concurrency} concurrent)`;
    }).catch(() => {});
  }
  if (_dlQueuePollId == null) {
    _dlQueuePollId = setInterval(tick, 2000);
    document.addEventListener('visibilitychange', function() {
      if (document.visibilityState === 'visible' && _dlQueuePollId == null) _startQueuePoller();
    }, { once: false });
    tick();
  }
}

// Public hook for the _Download IIFE — call on every successful enqueue.
window._dlKickQueuePoller = _startQueuePoller;

// Toggle download destination between music folder and AutoCue default folder.
document.addEventListener('click', e => {
  const sw = e.target.closest && e.target.closest('#dl-dest-switch');
  if (!sw) return;
  const cur = _dlDestDir;
  const pri = sw.dataset.primary;
  const alt = sw.dataset.alt;
  _dlDestDir = (cur === pri) ? alt : pri;
  const dest = document.getElementById('dl-dest');
  if (dest) dest.textContent = _dlDestDir;
  sw.textContent = (_dlDestDir === pri) ? 'Switch to AutoCue folder' : 'Switch to music folder';
});

// Style filter chips for discovery results.
function _renderStyleFilter(styles) {
  const container = document.getElementById('disc-style-filter');
  if (!container) return;
  if (!styles.length) { container.style.display = 'none'; return; }
  container.style.display = 'flex';
  container.innerHTML = '<span style="font-size:11px;color:var(--muted);margin-right:4px;flex-shrink:0;">Filter:</span>'
    + '<button class="tag-pill disc-sf-btn" style="cursor:pointer;" data-style="">All</button>'
    + [...styles].sort().map(s => `<button class="tag-pill disc-sf-btn" style="cursor:pointer;" data-style="${_esc(s)}">${_esc(s)}</button>`).join('');
  // Mark "All" active on first render
  const allBtn = container.querySelector('[data-style=""]');
  if (allBtn) allBtn.classList.add('active');
}

document.addEventListener('click', e => {
  const btn = e.target.closest && e.target.closest('.disc-sf-btn');
  if (!btn) return;
  document.querySelectorAll('.disc-sf-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const filter = btn.dataset.style;
  document.querySelectorAll('.disc-card').forEach(card => {
    const cardStyles = (card.dataset.styles || '').split(',');
    card.style.display = (!filter || cardStyles.includes(filter)) ? '' : 'none';
  });
});

// ===========================================================================
// Discover v2 (T-024) — DiscoverState + SSE consumer + card renderer
// ===========================================================================
//
// Replaces the v1 'scan library' panel above with a personalised feed driven
// by /api/discover/feed (SSE). State (saved / dismissed / followed / blocked)
// lives in the backend's discover.db; the JS just caches it for the session
// and refreshes on user actions. The CardRenderer + DetailPanel stubs live
// inline here; T-025+ (YouTube carousel, full keyboard shortcuts, settings
// panels) extend them.

const DiscoverV2 = (() => {
  const state = {
    cards: [],                  // in-render-order list of release dicts
    cardsByKey: new Map(),      // release_key → release dict
    savedKeys: new Set(),
    dismissedKeys: new Set(),
    snoozedKeys: new Set(),
    resurfacedKeys: new Set(),       // release_keys whose snooze has expired
    snoozedMeta: new Map(),          // release_key → {until_date}
    followedLabels: [],         // [{label_id, name, last_scanned_at}]
    blockedArtists: [],
    blockedLabels: [],
    scanRunning: false,
    scanFeeder: null,
    scanReleasesSeen: 0,
    scanFeedersDone: [],                                  // ordered list of feeders that have completed
    scanReleasesByFeeder: {artist: 0, label: 0, novelty: 0},
    scanSparseAdjacency: false,
    scanLastSummary: null,                                  // {releases_surfaced, requests_used, duration_ms}
    scanError: null,                                        // {kind, message, status}  — null when no error
    scanWarnings: [],                                       // [{feeder, message}] — non-fatal per-feeder errors
    tokenValid: null,           // null = unknown
    settingsOpen: false,
    youtubeByKey: new Map(),    // release_key → {status, candidates, error}
  };

  const subs = new Set();
  function subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }
  function notify() { for (const fn of subs) { try { fn(state); } catch (e) { console.error(e); } } }

  // Issue #67: track the in-flight scan's AbortController so a new runScan()
  // can immediately abort the prior fetch (closing the SSE reader), instead
  // of racing the server lock.
  let _scanAbort = null;

  // ── HTTP helpers ────────────────────────────────────────────────────────
  async function _post(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : null,
    });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }
  async function _get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }

  // ── Initial state load ──────────────────────────────────────────────────
  async function loadInitialState() {
    try {
      const [saved, dismissed, snoozed, followed, blkA, blkL, tokenStatus] = await Promise.all([
        _get('/api/discover/saved'),
        _get('/api/discover/dismissed'),
        // include_resurfaced=true so we can tag cards that the user previously
        // snoozed and that have since reappeared in the feed.
        _get('/api/discover/snoozed?include_resurfaced=true'),
        _get('/api/discover/labels'),
        _get('/api/discover/blocked-artists'),
        _get('/api/discover/blocked-labels'),
        _get('/api/discover/token-status'),
      ]);
      state.savedKeys = new Set(saved.items.map(r => r.release_key));
      state.dismissedKeys = new Set(dismissed.items.map(r => r.release_key));
      // Split snoozed into still-active vs resurfaced (until_date in the past).
      const nowIso = new Date().toISOString();
      state.snoozedKeys = new Set();
      state.resurfacedKeys = new Set();
      state.snoozedMeta = new Map();
      for (const row of snoozed.items) {
        state.snoozedMeta.set(row.release_key, {until_date: row.until_date});
        if (row.until_date && row.until_date > nowIso) {
          state.snoozedKeys.add(row.release_key);
        } else {
          state.resurfacedKeys.add(row.release_key);
        }
      }
      state.followedLabels = followed.items;
      state.blockedArtists = blkA.items;
      state.blockedLabels = blkL.items;
      state.tokenValid = tokenStatus.valid;
    } catch (e) {
      console.warn('DiscoverV2: initial-state load failed', e);
    }
    notify();
  }

  // ── SSE feed consumer ───────────────────────────────────────────────────
  async function runScan() {
    // Issue #67: rapid filter toggles (e.g. Year "All" → "This year" → "All")
    // raced themselves into a 409. Two changes here:
    //   1. If a scan is in flight when a new one is requested, abort the
    //      prior fetch via its AbortController (closes the SSE reader
    //      immediately and triggers the prior runScan's catch branch, which
    //      flips scanRunning=false). Also fire the server-side cancel so the
    //      lock releases promptly. This kills the user-vs-self race.
    //   2. Do NOT pre-clear the existing cards. We only reset the card grid
    //      once the new fetch is confirmed OK (200). Error branches preserve
    //      the previously-rendered cards so a transient 409 / network blip
    //      does not blow away the user's feed.
    if (_scanAbort) {
      // Mark this as a self-initiated supersede so the aborted scan's catch
      // branch does NOT surface a misleading "network error" toast.
      _scanAbort.autocueSuperseded = true;
      try { _scanAbort.abort(); } catch (_) {}
      _scanAbort = null;
      try { await _post('/api/discover/feed/cancel', {}); } catch (_) {}
      // Yield one microtask so the prior runScan's catch can run and reset
      // scanRunning before we proceed.
      await Promise.resolve();
      // Issue #169: the cancel POST returns immediately but the server-side
      // orchestrator may still be blocked inside a long Discogs API call,
      // so the scan lock is held for another beat. Without waiting, the
      // next fetch lands while the lock is still held and 409s. The 409
      // handler (line ~5702) sets scanError but leaves the prior cards
      // visible — the user gets no signal that the filter change was
      // rejected. Poll /api/discover/feed/status until running=false (or
      // a 3s hard cap, so a stuck scan doesn't freeze the UI forever).
      const tNow = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
      const deadline = tNow() + 3000;
      while (tNow() < deadline) {
        let status;
        try { status = await _get('/api/discover/feed/status'); }
        catch (_) { break; }  // network glitch — let the next fetch handle it
        if (!status || !status.running) break;
        await new Promise(r => setTimeout(r, 150));
      }
    }
    state.scanRunning = true;
    state.scanFeeder = null;
    state.scanReleasesSeen = 0;
    state.scanFeedersDone = [];
    state.scanReleasesByFeeder = {artist: 0, label: 0, novelty: 0};
    state.scanSparseAdjacency = false;
    state.scanLastSummary = null;
    state.scanError = null;
    state.scanWarnings = [];
    notify();

    // Build query from filter chips.
    const sources = Array.from(
      document.querySelectorAll('#disc-v2-filter-bar input[data-source]:checked'),
    ).map(el => el.dataset.source);
    const yearVal = document.getElementById('disc-v2-year')?.value || '';
    const params = new URLSearchParams();
    if (sources.length) params.set('sources', sources.join(','));
    const currentYear = new Date().getFullYear();
    if (yearVal === 'this') params.set('year_from', String(currentYear));
    else if (yearVal === 'last2') params.set('year_from', String(currentYear - 1));
    else if (yearVal === 'last5') params.set('year_from', String(currentYear - 4));
    else if (yearVal === 'custom') {
      const custom = parseInt(document.getElementById('disc-v2-year-custom')?.value || '', 10);
      if (Number.isFinite(custom) && custom >= 1900 && custom <= 2099) {
        params.set('year_from', String(custom));
      }
    }

    const abort = new AbortController();
    _scanAbort = abort;
    let res;
    try {
      res = await fetch('/api/discover/feed?' + params.toString(), {signal: abort.signal});
    } catch (e) {
      // Aborted by a newer runScan() call — exit silently. The newer call
      // owns the state from here on.
      if (abort.autocueSuperseded || (e && e.name === 'AbortError')) {
        if (_scanAbort === abort) _scanAbort = null;
        state.scanRunning = false;
        notify();
        return;
      }
      // Network failure — surface as a structured error so the empty-state can
      // render a Retry button instead of going silent (PRD §9 — every empty
      // state must say WHY it's empty). Cards preserved (issue #67).
      if (_scanAbort === abort) _scanAbort = null;
      state.scanRunning = false;
      state.scanError = {kind: 'network', message: String(e && e.message || e)};
      notify();
      return;
    }
    if (res.status === 409) {
      // Issue #67: do NOT clear cards — keep the prior feed visible. The
      // error surface (empty-state / banner) reads scanError to explain
      // why the latest filter change did not refresh the grid.
      if (_scanAbort === abort) _scanAbort = null;
      state.scanRunning = false;
      state.scanError = {kind: 'conflict', status: 409,
                         message: 'A Discover scan is already running.'};
      notify();
      return;
    }
    if (res.status === 400) {
      let detail = '';
      try { const j = await res.json(); detail = j.detail || ''; } catch (_) {}
      if (_scanAbort === abort) _scanAbort = null;
      state.scanRunning = false;
      state.scanError = {kind: 'bad-request', status: 400,
                         message: detail || 'Bad request — check the filter parameters.'};
      notify();
      return;
    }
    if (!res.ok) {
      if (_scanAbort === abort) _scanAbort = null;
      state.scanRunning = false;
      state.scanError = {kind: 'http', status: res.status,
                         message: `Server returned HTTP ${res.status}.`};
      notify();
      return;
    }

    // Fetch confirmed OK — NOW clear the prior cards so the new stream
    // fills a fresh grid. Doing this here (rather than at the top of
    // runScan) keeps the existing feed visible across error responses
    // (issue #67).
    state.cards = [];
    state.cardsByKey.clear();
    notify();

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    try {
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += decoder.decode(value, {stream: true});
        const chunks = buf.split('\n\n');
        buf = chunks.pop();  // last partial chunk
        for (const chunk of chunks) _handleSSEChunk(chunk);
      }
    } catch (e) {
      // Issue #67: a mid-stream abort means a newer runScan() took over;
      // do NOT surface as an error.
      if (!(abort.autocueSuperseded || (e && e.name === 'AbortError'))) {
        // Reader broke mid-stream — partial results may still be visible.
        state.scanError = {kind: 'stream', message: String(e && e.message || e)};
      }
    }
    if (_scanAbort === abort) _scanAbort = null;
    state.scanRunning = false;
    notify();
  }

  function _handleSSEChunk(chunk) {
    let event = 'message';
    let data = null;
    for (const line of chunk.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7).trim();
      else if (line.startsWith('data: ')) {
        try { data = JSON.parse(line.slice(6)); }
        catch (_) { /* ignore */ }
      }
    }
    if (!data) return;
    if (event === 'progress') {
      // Track transitions: when scanFeeder changes, the prior feeder is "done".
      if (state.scanFeeder && state.scanFeeder !== data.feeder &&
          !state.scanFeedersDone.includes(state.scanFeeder)) {
        state.scanFeedersDone.push(state.scanFeeder);
      }
      state.scanFeeder = data.feeder;
      notify();
    } else if (event === 'release') {
      state.cards.push(data);
      state.cardsByKey.set(data.release_key, data);
      state.scanReleasesSeen++;
      // Bucket release counts by feeder (release.source is "artist" / "label"
      // / "novelty:*" — collapse novelty:* to a single bucket).
      const src = (data.source || '').split(':')[0];
      if (Object.prototype.hasOwnProperty.call(state.scanReleasesByFeeder, src)) {
        state.scanReleasesByFeeder[src]++;
      }
      notify();
    } else if (event === 'sparse_adjacency') {
      state.scanSparseAdjacency = true;
      notify();
    } else if (event === 'warning') {
      // Non-fatal per-feeder warning — record it so the user sees what fell
      // through (e.g., a single artist with no recent releases).
      state.scanWarnings.push({
        feeder: data.feeder || 'unknown',
        message: data.exc || data.message || 'warning',
      });
      notify();
    } else if (event === 'done') {
      state.scanFeeder = null;
      state.scanLastSummary = {
        releases_surfaced: data.releases_surfaced,
        releases_seen: data.releases_seen,
        duration_ms: data.duration_ms,
      };
      notify();
    } else if (event === 'error') {
      // The orchestrator labels its own crashes with feeder === 'orchestrator';
      // a per-feeder error fall-back is non-fatal and goes into scanWarnings.
      state.scanFeeder = null;
      const isFatal = (data.feeder || '') === 'orchestrator';
      if (isFatal) {
        state.scanError = {kind: 'orchestrator', message: data.exc || 'scan crashed'};
      } else {
        state.scanWarnings.push({
          feeder: data.feeder || 'unknown',
          message: data.exc || 'feeder failed',
        });
      }
      notify();
    }
  }

  async function cancelScan() {
    try { await _post('/api/discover/feed/cancel', {}); } catch (_) {}
  }

  // ── State mutations ─────────────────────────────────────────────────────
  async function save(release) {
    await _post('/api/discover/save', {
      release_key: release.release_key,
      release_id: release.release?.id || 0,
      artist: release.release?.artist || '',
      title: release.release?.title || '',
      label: release.release?.label || '',
    });
    state.savedKeys.add(release.release_key);
    notify();
  }

  async function dismiss(release) {
    await _post('/api/discover/dismiss', {
      release_key: release.release_key,
      release_id: release.release?.id || 0,
      artist: release.release?.artist || '',
      title: release.release?.title || '',
    });
    state.dismissedKeys.add(release.release_key);
    notify();
  }

  async function snooze(release, duration) {
    await _post('/api/discover/snooze', {
      release_key: release.release_key,
      duration: duration || '1m',
      release_id: release.release?.id || 0,
      artist: release.release?.artist || '',
      title: release.release?.title || '',
    });
    state.snoozedKeys.add(release.release_key);
    notify();
  }

  async function loadDetail(releaseId) {
    return _get('/api/discover/releases/' + encodeURIComponent(releaseId));
  }

  // YouTube preview: lazy, per-release, cached for the session.
  //
  // Sends `artist` + `album` alongside the raw `q` query so the backend can
  // apply its token-mismatch filter: when at least one candidate is a
  // genuine match, hard mismatches (Vénissieux → corporate-services video,
  // Philip Glass → Schubert, etc.) are dropped server-side and `mismatch:
  // true` is flagged on any survivors. The frontend respects the flag
  // (drops mismatches from the carousel; falls back to "no clean match
  // found" if ALL results are flagged).
  async function searchYouTube(release, n = 3) {
    const key = release.release_key;
    const cached = state.youtubeByKey.get(key);
    if (cached && cached.status !== 'error') return cached;
    const r = release.release || {};
    const artist = r.artist || '';
    const title = r.title || r.album || '';
    const q = [artist, title].filter(Boolean).join(' ').trim();
    if (!q) {
      const empty = {status: 'loaded', candidates: []};
      state.youtubeByKey.set(key, empty);
      return empty;
    }
    state.youtubeByKey.set(key, {status: 'loading', candidates: []});
    const params = new URLSearchParams({q, n: String(n)});
    if (artist) params.set('artist', artist);
    if (title) params.set('album', title);
    try {
      const res = await _get('/api/youtube/search?' + params.toString());
      const entry = {status: 'loaded', candidates: res.candidates || []};
      state.youtubeByKey.set(key, entry);
      return entry;
    } catch (e) {
      const entry = {status: 'error', candidates: [], error: String(e)};
      state.youtubeByKey.set(key, entry);
      return entry;
    }
  }

  async function followLabel(labelId, name) {
    await _post('/api/discover/labels/follow', {label_id: labelId, name: name});
    await refreshFollowed();
  }
  async function unfollowLabel(labelId) {
    await _post('/api/discover/labels/unfollow', {label_id: labelId});
    await refreshFollowed();
  }
  async function refreshFollowed() {
    const r = await _get('/api/discover/labels');
    state.followedLabels = r.items;
    notify();
  }
  async function refreshBlocked() {
    const [a, l] = await Promise.all([
      _get('/api/discover/blocked-artists'),
      _get('/api/discover/blocked-labels'),
    ]);
    state.blockedArtists = a.items || [];
    state.blockedLabels = l.items || [];
    notify();
  }
  async function blockArtist(discogsArtistId, name) {
    await _post('/api/discover/block-artist',
                {discogs_artist_id: discogsArtistId, name: name});
    await refreshBlocked();
  }
  async function unblockArtist(discogsArtistId) {
    await _post('/api/discover/unblock-artist',
                {discogs_artist_id: discogsArtistId});
    await refreshBlocked();
  }
  async function blockLabel(discogsLabelId, name) {
    await _post('/api/discover/block-label',
                {discogs_label_id: discogsLabelId, name: name});
    await refreshBlocked();
  }
  async function unblockLabel(discogsLabelId) {
    await _post('/api/discover/unblock-label',
                {discogs_label_id: discogsLabelId});
    await refreshBlocked();
  }
  async function fetchSuggestedLabels(limit = 10) {
    const r = await _get('/api/discover/labels/suggested?limit=' + limit);
    return r.items;
  }
  async function searchLabels(query) {
    const r = await _get('/api/discover/labels/search?q=' + encodeURIComponent(query));
    return r.items;
  }
  async function refreshStats() {
    return _get('/api/discover/stats');
  }
  async function exportState() {
    const r = await fetch('/api/discover/state/export');
    if (!r.ok) throw new Error('export failed');
    return r.blob();
  }
  async function importState(file) {
    const r = await fetch('/api/discover/state/import', {
      method: 'POST',
      headers: {'Content-Type': 'application/gzip'},
      body: file,
    });
    if (!r.ok) throw new Error('import failed: HTTP ' + r.status);
    await loadInitialState();
    return r.json();
  }

  return {
    state, subscribe,
    loadInitialState, runScan, cancelScan,
    save, dismiss, snooze, loadDetail, searchYouTube,
    followLabel, unfollowLabel, refreshFollowed, fetchSuggestedLabels, searchLabels,
    blockArtist, unblockArtist, blockLabel, unblockLabel, refreshBlocked,
    refreshStats, exportState, importState,
  };
})();


// Card renderer + DOM event wiring -------------------------------------------

// Render the "🔁 Resurfaced" badge for releases the user previously snoozed
// and whose snooze has since expired. The until_date the snooze was set to
// (i.e., the resurface date) is shown as a hover tooltip.
function _resurfacedBadge(release) {
  if (!release || !DiscoverV2.state.resurfacedKeys.has(release.release_key)) return '';
  const meta = DiscoverV2.state.snoozedMeta && DiscoverV2.state.snoozedMeta.get(release.release_key);
  const dateStr = meta && meta.until_date ? meta.until_date.slice(0, 10) : '';
  const titleAttr = dateStr ? ` title="Snooze expired on ${_esc(dateStr)}"` : '';
  return ` <span class="disc-v2-resurfaced-badge"${titleAttr}>🔁 Resurfaced</span>`;
}

function _renderDiscoverV2Card(release) {
  const r = release.release || {};
  const isSaved = DiscoverV2.state.savedKeys.has(release.release_key);
  const card = document.createElement('div');
  card.className = 'disc-v2-card';
  card.setAttribute('data-release-key', release.release_key);
  card.setAttribute('role', 'button');
  card.setAttribute('tabindex', '0');
  const art = r.thumb || r.cover_image || '';
  // Map the feeder source to a human-readable origin label. The raw values
  // ("artist", "label", "novelty:style", "novelty:label", "novelty:artist")
  // came straight from the backend; "via artist" was jargon (UX audit M-5).
  const rawSource = (release.source || '');
  const SOURCE_LABEL = {
    artist:           'Artist match',
    label:            'Label match',
    'novelty':        'Novelty pick',
  };
  const sourceFamily = rawSource.split(':')[0];
  const sourceLabel = SOURCE_LABEL[sourceFamily] || sourceFamily;
  card.innerHTML = `
    <div class="disc-v2-card-art" style="${art ? `background-image:url('${_esc(art)}')` : ''}"></div>
    <div class="disc-v2-card-body">
      <p class="disc-v2-card-title">${_esc(r.title || 'Untitled')}</p>
      <p class="disc-v2-card-artist">${_esc(r.artist || 'Unknown Artist')}</p>
      <p class="disc-v2-card-source">${_esc(sourceLabel)}${r.label ? ' · ' + _esc(r.label) : ''}${r.year ? ' · ' + r.year : ''}${_resurfacedBadge(release)}</p>
    </div>
    <div class="disc-v2-card-actions" data-actions>
      <button class="disc-v2-card-action ${isSaved ? 'saved' : ''}" data-act="save" title="Save">${isSaved ? '✓' : '💚'}</button>
      <button class="disc-v2-card-action" data-act="snooze" title="Snooze (1w / 1m / 3m)">💤</button>
      <button class="disc-v2-card-action" data-act="dismiss" title="Dismiss">✕</button>
    </div>
  `;
  return card;
}

// Client-side resort + Explore-mode interleave.
//
// Backend returns cards already taste-ranked (the default). Switching to
// newest / title / artist re-sorts the existing fetch without re-running
// the scan. Explore mode interleaves novelty:non-novelty 50/50 so the user
// sees adjacent finds at the same rate as taste matches.
//
// Sort modes:
//   taste    no-op (preserve backend order, which is taste-ranked)
//   newest   sort by release.year DESC (releases without year come last)
//   title    sort by release.title (alpha, case-insensitive)
//   artist   sort by release.artist (alpha, case-insensitive)
//   explore  zip novelty + non-novelty round-robin (preserves intra-group order)
function _applyDiscoverV2Sort(cards, sortMode) {
  if (!cards || !cards.length) return cards || [];
  if (!sortMode || sortMode === 'taste') return cards.slice();
  if (sortMode === 'newest') {
    return cards.slice().sort((a, b) => {
      const ay = parseInt((a.release && a.release.year) || 0, 10) || 0;
      const by = parseInt((b.release && b.release.year) || 0, 10) || 0;
      return by - ay;
    });
  }
  const norm = (s) => String((s || '')).toLocaleLowerCase();
  if (sortMode === 'title') {
    return cards.slice().sort((a, b) =>
      norm(a.release && a.release.title).localeCompare(norm(b.release && b.release.title)));
  }
  if (sortMode === 'artist') {
    return cards.slice().sort((a, b) =>
      norm(a.release && a.release.artist).localeCompare(norm(b.release && b.release.artist)));
  }
  if (sortMode === 'explore') {
    const novelty = [];
    const other = [];
    for (const c of cards) {
      if ((c.source || '').startsWith('novelty')) novelty.push(c);
      else other.push(c);
    }
    const out = [];
    const max = Math.max(novelty.length, other.length);
    for (let i = 0; i < max; i++) {
      // Other first so the very first card is still a taste match — this
      // mirrors the PRD-locked "Explore mode (50/50)" expectation.
      if (i < other.length) out.push(other[i]);
      if (i < novelty.length) out.push(novelty[i]);
    }
    return out;
  }
  return cards.slice();
}

// Client-side filter predicate for the Discover feed. Extracted from
// `_renderDiscoverV2Feed` so the filter logic is unit-testable in isolation.
// `state` is the persisted filter state (search, selectedStyles, hideSaved,
// hideDismissed, year, customYear); `s` is the global DiscoverV2.state with
// dismissed/snoozed/saved keys.
function _applyDiscoverV2Filters(cards, filters, s) {
  const search = (filters.search || '').trim().toLowerCase();
  const styles = filters.selectedStyles instanceof Set
    ? filters.selectedStyles
    : new Set(filters.selectedStyles || []);
  const hideSaved = !!filters.hideSaved;
  const hideDismissed = filters.hideDismissed !== false; // default ON
  // Snoozed cards are ALWAYS hidden (they had an explicit "come back later"
  // action and would be noise to surface again until the snooze expires).
  return cards.filter((c) => {
    if (s.snoozedKeys && s.snoozedKeys.has(c.release_key)) return false;
    if (hideDismissed && s.dismissedKeys && s.dismissedKeys.has(c.release_key)) return false;
    if (hideSaved && s.savedKeys && s.savedKeys.has(c.release_key)) return false;
    const r = c.release || {};
    if (search) {
      const hay = (
        (r.artist || '') + ' ' +
        (r.title || '') + ' ' +
        (r.album || '') + ' ' +
        (r.label || '')
      ).toLowerCase();
      if (!hay.includes(search)) return false;
    }
    if (styles.size > 0) {
      const cardStyles = Array.isArray(r.styles) ? r.styles : [];
      if (!cardStyles.some((st) => styles.has(String(st).toLowerCase()))) return false;
    }
    return true;
  });
}

// Persistent filter state for the Discover feed. Loaded from localStorage on
// boot; selectedStyles is reconstituted as a Set so .has() works.
let _discoverFilters = (() => {
  try {
    const raw = JSON.parse(localStorage.getItem('ac_discover_filters') || '{}');
    return {
      search: typeof raw.search === 'string' ? raw.search : '',
      selectedStyles: new Set(Array.isArray(raw.selectedStyles) ? raw.selectedStyles : []),
      hideSaved: !!raw.hideSaved,
      hideDismissed: raw.hideDismissed !== false, // default ON
    };
  } catch {
    return { search: '', selectedStyles: new Set(), hideSaved: false, hideDismissed: true };
  }
})();

function _persistDiscoverFilters() {
  try {
    localStorage.setItem('ac_discover_filters', JSON.stringify({
      search: _discoverFilters.search,
      selectedStyles: Array.from(_discoverFilters.selectedStyles),
      hideSaved: _discoverFilters.hideSaved,
      hideDismissed: _discoverFilters.hideDismissed,
    }));
  } catch {}
}

// Rebuilds the style-chip strip from the styles present in the loaded feed.
// Only the styles that actually appear in the user's current cards become
// chips — no point offering "Bossa Nova" if no card has it. Chips reflect
// selection state from _discoverFilters.selectedStyles.
//
// Ghost-filter guard: after a re-scan the new feed may not contain a
// style the user previously selected. Without pruning, that selection
// stays in _discoverFilters.selectedStyles and silently filters every
// subsequent feed to an empty grid — the user sees no cards, no chip
// to un-toggle the filter, no clue what's wrong. We prune any selected
// style whose key is not present in ANY current card's `release.styles`
// (NOT just the top 16 — a style that survives in card N+17 should keep
// its filter; only truly-vanished styles get dropped).
function _renderDiscoverStyleChips() {
  const container = document.getElementById('disc-v2-style-chips');
  const clearBtn = document.getElementById('disc-v2-styles-clear');
  if (!container) return;
  const counts = new Map();
  const allStyles = new Set();
  for (const c of DiscoverV2.state.cards) {
    const styles = Array.isArray(c.release?.styles) ? c.release.styles : [];
    for (const st of styles) {
      const key = String(st).toLowerCase();
      if (!key) continue;
      counts.set(key, (counts.get(key) || 0) + 1);
      allStyles.add(key);
    }
  }
  // Prune ghost selections — those whose style has fully disappeared from
  // the feed since the user toggled them. Persist the trimmed set so the
  // ghost doesn't come back on the next reload.
  let prunedAny = false;
  for (const key of Array.from(_discoverFilters.selectedStyles)) {
    if (!allStyles.has(key)) {
      _discoverFilters.selectedStyles.delete(key);
      prunedAny = true;
    }
  }
  if (prunedAny) _persistDiscoverFilters();

  if (counts.size === 0) {
    container.style.display = 'none';
    if (clearBtn) clearBtn.style.display = 'none';
    return;
  }
  // Top 16 most common styles — keeps the strip short. Sorted by count desc.
  const top = Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 16);
  container.style.display = 'flex';
  container.innerHTML = top.map(([key, n]) => {
    const active = _discoverFilters.selectedStyles.has(key) ? ' active' : '';
    return `<label class="disc-v2-chip${active}" data-style="${_esc(key)}">` +
      `<input type="checkbox" data-style-key="${_esc(key)}"${active ? ' checked' : ''}> ` +
      `${_esc(key.replace(/\b\w/g, (c) => c.toUpperCase()))} <span style="color:var(--muted)">${n}</span></label>`;
  }).join('');
  if (clearBtn) {
    clearBtn.style.display = _discoverFilters.selectedStyles.size > 0 ? '' : 'none';
  }
}

// Render the inline scan-error banner (issue #169) when a refresh failed
// but the user still has prior cards visible — the empty-state-only path
// in _renderDiscoverV2Feed wouldn't surface anything in that case. Hide
// the banner whenever a new scan is in flight or when scanError is null.
function _renderDiscoverScanErrorInline() {
  const el = document.getElementById('disc-v2-scan-error-inline');
  const msgEl = document.getElementById('disc-v2-scan-error-inline-msg');
  if (!el || !msgEl) return;
  const s = DiscoverV2.state;
  if (s.scanRunning || !s.scanError) {
    el.style.display = 'none';
    return;
  }
  const e = s.scanError;
  let text;
  if (e.kind === 'conflict') {
    text = 'Filter change ignored — a Discover scan is still running. Try again in a moment.';
  } else if (e.kind === 'network') {
    text = 'Network error while updating the feed: ' + (e.message || 'connection failed') + '.';
  } else if (e.kind === 'bad-request') {
    text = 'Filter rejected: ' + (e.message || 'bad request') + '.';
  } else {
    text = 'Feed update failed: ' + (e.message || 'unknown error') + '.';
  }
  msgEl.textContent = text;
  el.style.display = 'flex';
}

function _renderDiscoverV2Feed() {
  const grid = document.getElementById('disc-v2-grid');
  if (!grid) return;
  // Stagger only when the grid populates from empty (post-scan reveal) —
  // re-staggering on every save/dismiss re-render would read as flicker.
  const freshRender = !grid.children.length;
  grid.innerHTML = '';

  const s = DiscoverV2.state;
  const sortMode = document.getElementById('disc-v2-sort')?.value || 'taste';
  // Refresh the style chips from the current feed before filtering so the
  // user can toggle a style that just appeared.
  _renderDiscoverStyleChips();
  _renderDiscoverScanErrorInline();
  // Apply client-side filters (search / styles / hide-saved / hide-dismissed).
  // Snoozed cards are unconditionally hidden inside _applyDiscoverV2Filters.
  const filtered = _applyDiscoverV2Filters(s.cards, _discoverFilters, s);
  const visible = _applyDiscoverV2Sort(filtered, sortMode);

  const emptyEl = document.getElementById('disc-v2-empty-state');
  const emptyMsg = document.getElementById('disc-v2-empty-state-msg');
  const emptyAction = document.getElementById('disc-v2-empty-action');

  if (s.scanRunning) {
    if (emptyEl) emptyEl.style.display = 'none';
  } else if (!visible.length) {
    if (emptyEl) {
      emptyEl.style.display = '';
      emptyAction.style.display = 'none';
      // Order: token missing > scan error > no labels > filters too tight > truly empty.
      if (s.tokenValid === false) {
        emptyMsg.innerHTML = '<strong>Discogs token invalid or missing.</strong> ' +
          'Configure <code>DISCOGS_TOKEN</code> in <code>.env</code> and restart the server.';
      } else if (s.scanError) {
        const e = s.scanError;
        // For 'conflict' (409) errors the suggestion isn't "wait for the
        // other scan" because in practice the lock has usually since cleared
        // (PR #61 prevents orchestrator-crash leaks). Just nudge the user
        // to retry — the next click does the right thing.
        emptyMsg.innerHTML =
          '<strong>Couldn’t finish the scan.</strong> ' + _esc(e.message || '') +
          (e.kind === 'conflict' ? ' Click Refresh to try again.' : '');
        emptyAction.style.display = '';
        emptyAction.textContent = 'Refresh';
        emptyAction.onclick = () => DiscoverV2.runScan();
      } else if (!s.followedLabels.length) {
        emptyMsg.textContent = 'Follow some labels to start seeing releases.';
        emptyAction.style.display = '';
        emptyAction.textContent = 'Pick from your library';
        emptyAction.onclick = () => _openOnboarding();
      } else if (s.cards.length > 0) {
        // We scanned + got cards, but all are dismissed/snoozed. That's an
        // ALL-FILTERED state, not a no-results state.
        emptyMsg.textContent =
          `Everything from this scan is already dismissed or snoozed (${s.cards.length} hidden). ` +
          'Try a different sort or run Refresh.';
        emptyAction.style.display = '';
        emptyAction.textContent = 'Refresh';
        emptyAction.onclick = () => DiscoverV2.runScan();
      } else if (s.scanLastSummary && s.scanLastSummary.releases_surfaced === 0) {
        emptyMsg.textContent =
          'No new releases right now. The labels you watch haven’t posted anything new.';
      } else {
        emptyMsg.textContent =
          'No new releases yet. Click Refresh to run your first scan.';
        emptyAction.style.display = '';
        emptyAction.textContent = 'Refresh';
        emptyAction.onclick = () => DiscoverV2.runScan();
      }
    }
    return;
  } else if (emptyEl) {
    emptyEl.style.display = 'none';
  }

  visible.forEach((release, i) => {
    const card = _renderDiscoverV2Card(release);
    if (freshRender && !_prefersReducedMotion) {
      card.classList.add('fade-in-up');
      card.style.animationDelay = (Math.min(i, 12) * 25) + 'ms'; // cap: rows below the fold don't wait
    }
    grid.appendChild(card);
  });
}

// PRD §4: per-feeder hard budgets. Used to compute the overall progress bar
// (sum of completed-feeder budgets / 60 total). Keep in sync with PRD.
const _DISC_V2_FEEDER_BUDGETS = {artist: 20, label: 15, novelty: 10};

function _feederProgressPercent(scanFeeder, feedersDone) {
  // Estimate "scan progress" as the budget consumed by completed feeders +
  // half of the current feeder's budget. Coarse but correct-direction.
  let consumed = 0;
  let total = 0;
  for (const f of ['artist', 'label', 'novelty']) {
    const budget = _DISC_V2_FEEDER_BUDGETS[f] || 0;
    total += budget;
    if (feedersDone.includes(f)) consumed += budget;
    else if (scanFeeder === f) consumed += Math.round(budget * 0.5);
  }
  return total ? Math.round((consumed / total) * 100) : 0;
}

// Per-feeder non-fatal warning bar — visible until the next scan starts.
function _renderDiscoverV2ScanWarnings() {
  const el = document.getElementById('disc-v2-scan-warnings');
  if (!el) return;
  const w = DiscoverV2.state.scanWarnings || [];
  if (!w.length) {
    el.style.display = 'none';
    return;
  }
  el.style.display = '';
  // Collapse duplicates and surface a count next to each feeder.
  const byFeeder = new Map();
  for (const x of w) byFeeder.set(x.feeder, (byFeeder.get(x.feeder) || 0) + 1);
  const lines = Array.from(byFeeder.entries()).map(
    ([f, n]) => `⚠ ${_esc(f)} (${n})`
  );
  el.innerHTML =
    `<strong>Some feeders had trouble:</strong> ${lines.join(' · ')} ` +
    `<span style="color:var(--muted);">— partial results shown below.</span>`;
}

function _renderDiscoverV2ScanProgress() {
  const el = document.getElementById('disc-v2-scan-progress');
  const label = document.getElementById('disc-v2-scan-progress-label');
  const breakdown = document.getElementById('disc-v2-scan-progress-breakdown');
  const warning = document.getElementById('disc-v2-scan-progress-warning');
  const fill = document.getElementById('disc-v2-scan-progress-fill');
  const delta = document.getElementById('disc-v2-scan-delta');
  if (!el || !label) return;
  const s = DiscoverV2.state;

  if (s.scanRunning) {
    el.style.display = '';
    if (delta) delta.style.display = 'none';

    const feeder = s.scanFeeder || 'starting';
    const count = s.scanReleasesSeen;
    label.textContent = `Scanning ${feeder}… ${count} releases found so far`;

    if (breakdown) {
      const parts = ['artist', 'label', 'novelty'].map(f => {
        const n = s.scanReleasesByFeeder[f] || 0;
        const budget = _DISC_V2_FEEDER_BUDGETS[f];
        const status =
          f === s.scanFeeder ? '🔄' :
          s.scanFeedersDone.includes(f) ? '✓' :
          '·';
        return `<span data-feeder="${f}">${status} ${f} ${n} <span style="color:var(--muted);">(budget ${budget})</span></span>`;
      });
      breakdown.innerHTML = parts.join('');
    }

    if (warning) {
      if (s.scanSparseAdjacency) {
        warning.style.display = '';
        warning.textContent = '⚠ Sparse adjacency — novelty feeder may surface fewer adjacent finds than usual.';
      } else {
        warning.style.display = 'none';
      }
    }

    if (fill) {
      fill.style.width = _feederProgressPercent(s.scanFeeder, s.scanFeedersDone) + '%';
    }
    return;
  }

  // Scan not running.
  el.style.display = 'none';

  // If a scan just completed and we have a summary, surface the delta strip.
  if (delta && s.scanLastSummary) {
    const sum = s.scanLastSummary;
    const seconds = sum.duration_ms != null ? (sum.duration_ms / 1000).toFixed(1) : '?';
    delta.style.display = '';
    delta.textContent =
      `✓ Found ${sum.releases_surfaced} new releases in ${seconds}s ` +
      `(${sum.releases_seen} scanned, ${sum.releases_seen - sum.releases_surfaced} deduped).`;
  } else if (delta) {
    delta.style.display = 'none';
  }
}

// The onboarding banner is auto-loaded the first time it becomes visible per
// session. The flag lives on a module-scope variable (not localStorage) so a
// page reload re-fetches in case the user's library grew.
let _onboardingLoaded = false;

function _renderDiscoverV2Onboarding() {
  const banner = document.getElementById('disc-v2-onboarding-banner');
  if (!banner) return;
  // Show only when no labels are followed AND we haven't been told to skip.
  const shouldShow =
    DiscoverV2.state.followedLabels.length === 0 &&
    !localStorage.getItem('disc-v2-onboarding-skipped');
  if (shouldShow) {
    banner.style.display = '';
    if (!_onboardingLoaded) {
      _onboardingLoaded = true;
      _loadOnboardingSuggestions();
    }
  } else {
    banner.style.display = 'none';
  }
}

// _openOnboarding is the empty-state action ("Pick from your library") that
// reveals the banner regardless of the skipped flag. It re-fires the load too
// so a user who originally skipped sees fresh suggestions.
async function _openOnboarding() {
  const banner = document.getElementById('disc-v2-onboarding-banner');
  if (!banner) return;
  banner.style.display = '';
  // Re-fetch even if previously loaded — the user explicitly asked.
  _onboardingLoaded = true;
  await _loadOnboardingSuggestions();
}

async function _loadOnboardingSuggestions() {
  const container = document.getElementById('disc-v2-onboarding-suggestions');
  if (!container) return;
  container.innerHTML = '<em style="color:var(--muted);">Loading suggestions…</em>';
  try {
    const suggestions = await DiscoverV2.fetchSuggestedLabels(10);
    container.innerHTML = '';
    if (!suggestions.length) {
      container.innerHTML = '<em style="color:var(--muted);">No suggested labels — your library has no Discogs label metadata yet.</em>';
      return;
    }
    suggestions.forEach(sug => {
      const chip = document.createElement('button');
      chip.className = 'secondary-btn';
      chip.style.fontSize = '11px';
      chip.setAttribute('data-suggest-name', sug.name);
      chip.textContent = sug.name;
      // Tooltip + visible chip text both name the suggestion source so the
      // user knows WHY a label appears (UX audit M-3 — recognition-over-recall).
      const tooltip = sug.weight != null
        ? `Suggested from your library (relevance: ${sug.weight.toFixed(1)})`
        : 'Suggested from your library';
      chip.title = tooltip;
      chip.addEventListener('click', async () => {
        chip.disabled = true;
        chip.textContent = '… ' + sug.name;
        const followed = await _followByName(sug.name);
        if (followed) {
          chip.textContent = '✓ ' + sug.name;
        } else {
          // UX audit Issue 10: previously the chip silently re-enabled on
          // failure, hiding the fact that "Add all" only added 8 of 10. Now
          // we mark unresolved chips with ⚠ + a tooltip explaining the
          // reason. The chip stays disabled (clicking it would just retry
          // the same failing search).
          chip.disabled = true;
          chip.classList.add('disc-v2-suggest-failed');
          chip.textContent = '⚠ ' + sug.name;
          chip.title = `Couldn't find "${sug.name}" on Discogs. The library label name may contain a catalog code or disambiguator.`;
        }
      });
      container.appendChild(chip);
    });
  } catch (_) {
    container.innerHTML = '<em style="color:var(--muted);">Could not load suggestions.</em>';
  }
}

// Shared helper: the suggested-labels endpoint returns only `name` + `weight`,
// so to follow we have to resolve a Discogs label_id by searching. Returns
// truthy on success so callers can update their UI to ✓.
async function _followByName(name) {
  if (!name) return false;
  try {
    const hits = await DiscoverV2.searchLabels(name);
    if (!hits || !hits.length) return false;
    const top = hits[0];
    await DiscoverV2.followLabel(top.id, top.name || name);
    return true;
  } catch (_) {
    return false;
  }
}

function _renderDiscoverV2TokenBanner() {
  const banner = document.getElementById('disc-v2-token-banner');
  if (!banner) return;
  if (DiscoverV2.state.tokenValid === false) {
    banner.style.display = '';
    banner.innerHTML = '<strong>Discogs token invalid.</strong> Configure <code>DISCOGS_TOKEN</code> in <code>.env</code> and restart the server.';
  } else {
    banner.style.display = 'none';
  }
}

// Best-effort relative-time formatter for the followed-labels freshness column.
// Returns 'never' when no scan has happened yet so the empty state reads honestly.
function _relativeTime(iso, nowMs) {
  if (!iso) return 'never';
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return 'never';
  const now = nowMs || Date.now();
  const diffSec = Math.max(0, Math.floor((now - then) / 1000));
  if (diffSec < 60) return 'just now';
  const m = Math.floor(diffSec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  const months = Math.floor(d / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.floor(d / 365);
  return `${years}y ago`;
}

function _renderDiscoverV2Followed() {
  const list = document.getElementById('disc-v2-followed-list');
  if (!list) return;
  const labels = DiscoverV2.state.followedLabels || [];
  if (!labels.length) {
    list.innerHTML = '<em style="color:var(--muted);">No labels followed yet. Click <strong>Suggest</strong> to seed from your library, or search for one above.</em>';
    return;
  }
  list.innerHTML = '';
  labels.forEach(label => {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.justifyContent = 'space-between';
    row.style.alignItems = 'center';
    row.style.padding = '4px 0';
    const freshness = _relativeTime(label.last_scanned_at);
    row.innerHTML =
      `<span><strong>${_esc(label.name)}</strong>` +
      `<span style="color:var(--muted);margin-left:6px;font-size:11px;">last scanned ${_esc(freshness)}</span></span>`;
    const unfollow = document.createElement('button');
    unfollow.className = 'secondary-btn';
    unfollow.style.fontSize = '11px';
    unfollow.textContent = 'Unfollow';
    unfollow.addEventListener('click', () => DiscoverV2.unfollowLabel(label.label_id));
    row.appendChild(unfollow);
    list.appendChild(row);
  });
}

// Render the "Suggested from your library" inline list. Each row has a
// Follow button that disables itself on success — so the user can fan-add
// without having to re-render the whole list between clicks. The suggested
// endpoint returns `{name, weight}` only — the Discogs label_id has to be
// resolved via /labels/search at follow time (see _followByName).
function _renderSuggestedLabels(suggestions) {
  const results = document.getElementById('disc-v2-label-suggest-results');
  if (!results) return;
  if (!suggestions || !suggestions.length) {
    results.innerHTML = '<em style="color:var(--muted);">No suggestions — your library has no Discogs label metadata yet.</em>';
    return;
  }
  results.innerHTML = '';
  const followedNames = new Set(
    (DiscoverV2.state.followedLabels || []).map(l => (l.name || '').toLowerCase())
  );
  suggestions.forEach(s => {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.justifyContent = 'space-between';
    row.style.alignItems = 'center';
    row.style.padding = '4px 0';
    const weight = (s.weight != null)
      ? ` <span style="color:var(--muted);font-size:11px;">(score ${_esc(String(s.weight))})</span>`
      : '';
    row.innerHTML = `<span>${_esc(s.name)}${weight}</span>`;
    const btn = document.createElement('button');
    btn.className = 'secondary-btn';
    btn.style.fontSize = '11px';
    if (followedNames.has((s.name || '').toLowerCase())) {
      btn.disabled = true;
      btn.textContent = '✓ Following';
    } else {
      btn.textContent = 'Follow';
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        btn.textContent = '…';
        const followed = await _followByName(s.name);
        if (followed) {
          btn.textContent = '✓ Following';
        } else {
          btn.disabled = false;
          btn.textContent = 'Follow';
        }
      });
    }
    row.appendChild(btn);
    results.appendChild(row);
  });
}

// Format a millisecond duration for the stats block.
function _formatStatsDuration(ms) {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return '–';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec - m * 60);
  return `${m}m ${s}s`;
}

function _formatStatsRatio(n) {
  if (n == null || !Number.isFinite(n)) return '–';
  if (n >= 1) return n.toFixed(1);
  return n.toFixed(2);
}

function _formatStatsPercent(n) {
  if (n == null || !Number.isFinite(n)) return '–';
  // Clamp into [0, 1] so a backend returning raw counts can't render
  // 1000% (UX audit Issue 4). Belt-and-braces: the backend has been
  // changed to return ratios but this clamp catches future regressions.
  const ratio = Math.max(0, Math.min(1, n));
  return `${Math.round(ratio * 100)}%`;
}

// Render the stats block surfaced under Settings → Stats. Skips empty
// sub-sections (no top labels yet, no novelty share recorded yet) so the
// block stays useful even on a fresh install.
function _renderDiscoverV2Stats(stats) {
  const block = document.getElementById('disc-v2-stats-block');
  if (!block) return;
  if (!stats) {
    block.innerHTML = '<em>No stats yet — run a scan first.</em>';
    return;
  }
  const noveltyShare = stats.novelty_share || {};
  const noveltyParts = Object.keys(noveltyShare).sort().map(k =>
    `${_esc(k)} ${_formatStatsPercent(noveltyShare[k])}`
  );
  const topLabels = (stats.top_labels || []).slice(0, 5);
  const topArtists = (stats.top_artists || []).slice(0, 5);

  const counts = [
    `<strong>${stats.total_scans}</strong> scans`,
    `<strong>${stats.saved_count}</strong> saved`,
    `<strong>${stats.dismissed_count}</strong> dismissed`,
    `<strong>${stats.snoozed_count}</strong> snoozed`,
    `<strong>${stats.downloaded_count}</strong> downloaded`,
    `<strong>${stats.followed_count}</strong> followed labels`,
  ];
  if (stats.blocked_artist_count || stats.blocked_label_count) {
    counts.push(
      `<strong>${stats.blocked_artist_count + stats.blocked_label_count}</strong> blocked`
    );
  }

  const rows = [];
  rows.push(`<div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px;">${counts.join(' · ')}</div>`);
  rows.push(
    `<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px;color:var(--muted);">` +
    `<span>avg scan: <strong style="color:var(--text);">${_formatStatsDuration(stats.avg_duration_ms)}</strong></span>` +
    `<span>saves per scan: <strong style="color:var(--text);">${_formatStatsRatio(stats.saves_per_scan)}</strong></span>` +
    `</div>`
  );
  if (noveltyParts.length) {
    rows.push(`<div style="margin-bottom:8px;color:var(--muted);">novelty mix: ${noveltyParts.join(' · ')}</div>`);
  }
  if (topLabels.length) {
    rows.push(
      `<div style="margin-bottom:4px;color:var(--muted);">top label sources: ` +
      topLabels.map(l => `${_esc(l.name || 'unknown')} (${l.count})`).join(' · ') +
      `</div>`
    );
  }
  if (topArtists.length) {
    rows.push(
      `<div style="color:var(--muted);">top artist sources: ` +
      topArtists.map(a => `${_esc(a.name || 'unknown')} (${a.count})`).join(' · ') +
      `</div>`
    );
  }
  block.innerHTML = rows.join('');
}

// Settings → Saved releases (UX audit M-4 — give 💚 Save a destination).
// Renders the result of /api/discover/saved as a compact list with an
// Unsave button per row. Auto-refreshes whenever Settings opens or the
// user saves a new card.
function _renderDiscoverV2Saved(rows) {
  const list = document.getElementById('disc-v2-saved-list');
  const count = document.getElementById('disc-v2-saved-count');
  if (!list) return;
  if (!rows || !rows.length) {
    list.innerHTML = 'No saved releases yet. Click 💚 on any card in the feed.';
    list.style.color = 'var(--muted)';
    if (count) count.textContent = '';
    return;
  }
  list.style.color = 'var(--text)';
  if (count) count.textContent = `(${rows.length})`;
  list.innerHTML = '';
  rows.forEach(r => {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.justifyContent = 'space-between';
    row.style.alignItems = 'center';
    row.style.padding = '4px 0';
    row.style.gap = '8px';
    const meta = document.createElement('span');
    meta.style.flex = '1';
    meta.style.minWidth = '0';
    meta.style.overflow = 'hidden';
    meta.style.textOverflow = 'ellipsis';
    meta.style.whiteSpace = 'nowrap';
    meta.innerHTML = `<strong>${_esc(r.title || 'Untitled')}</strong> <span style="color:var(--muted);">${_esc(r.artist || '')}${r.label ? ' · ' + _esc(r.label) : ''}</span>`;
    row.appendChild(meta);
    if (r.release_id) {
      const link = document.createElement('a');
      link.href = `https://www.discogs.com/release/${r.release_id}`;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = '↗';
      link.style.fontSize = '11px';
      link.title = 'Open on Discogs';
      row.appendChild(link);
    }
    const unsave = document.createElement('button');
    unsave.className = 'secondary-btn';
    unsave.style.fontSize = '11px';
    unsave.textContent = 'Unsave';
    unsave.addEventListener('click', async () => {
      unsave.disabled = true;
      unsave.textContent = '…';
      try {
        // Unsave is a backend mutation that mirrors the save action. The
        // /api/discover/save endpoint accepts {release_key} alone for the
        // delete path; if it doesn't, fall back to dismissing.
        await fetch('/api/discover/unsave', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({release_key: r.release_key}),
        });
      } catch (_) {}
      DiscoverV2.state.savedKeys.delete(r.release_key);
      _refreshSavedFromBackend();
    });
    row.appendChild(unsave);
    list.appendChild(row);
  });
}

async function _refreshSavedFromBackend() {
  try {
    const resp = await fetch('/api/discover/saved');
    const body = await resp.json();
    _renderDiscoverV2Saved(body.items || []);
  } catch (_) {}
}

function _renderDiscoverV2Blocked() {
  const list = document.getElementById('disc-v2-blocked-list');
  if (!list) return;
  const sa = DiscoverV2.state.blockedArtists || [];
  const sl = DiscoverV2.state.blockedLabels || [];
  if (!sa.length && !sl.length) {
    list.innerHTML = 'Nothing blocked. You can 🚫 block an artist or label from the release detail panel.';
    list.style.color = 'var(--muted)';
    return;
  }
  list.style.color = 'var(--text)';
  list.innerHTML = '';

  const _row = (kind, icon, name, id, unblockFn) => {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.justifyContent = 'space-between';
    row.style.alignItems = 'center';
    row.style.padding = '4px 0';
    row.setAttribute('data-blocked-kind', kind);
    row.setAttribute('data-blocked-id', String(id));
    row.innerHTML = `<span>${icon} ${_esc(name || 'unknown')}</span>`;
    const btn = document.createElement('button');
    btn.className = 'secondary-btn';
    btn.style.fontSize = '11px';
    btn.textContent = 'Unblock';
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = '…';
      try {
        await unblockFn(id);
      } catch (_) {
        btn.disabled = false;
        btn.textContent = 'Unblock';
      }
    });
    row.appendChild(btn);
    return row;
  };

  if (sa.length) {
    const h = document.createElement('div');
    h.innerHTML = `<strong style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.04em;">Artists (${sa.length})</strong>`;
    list.appendChild(h);
    sa.forEach(a => list.appendChild(_row('artist', '🎤', a.name, a.discogs_artist_id, DiscoverV2.unblockArtist)));
  }
  if (sl.length) {
    const h = document.createElement('div');
    h.style.marginTop = sa.length ? '8px' : '0';
    h.innerHTML = `<strong style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.04em;">Labels (${sl.length})</strong>`;
    list.appendChild(h);
    sl.forEach(l => list.appendChild(_row('label', '🏷', l.name, l.discogs_label_id, DiscoverV2.unblockLabel)));
  }
}

// Detail panel ── proper dialog: focus trap, return-focus, click-outside-to-close.
// State tracked at module scope so _closeDetailPanel can restore focus.
let _detailReturnFocusEl = null;
let _detailCurrentRelease = null;
let _detailKeydownHandler = null;

async function _openDetailPanel(releaseKey) {
  const panel = document.getElementById('disc-v2-detail-panel');
  const backdrop = document.getElementById('disc-v2-detail-backdrop');
  const body = document.getElementById('disc-v2-detail-body');
  if (!panel || !body) return;
  const release = DiscoverV2.state.cardsByKey.get(releaseKey);
  if (!release) return;

  _detailReturnFocusEl = document.activeElement;
  _detailCurrentRelease = release;

  panel.setAttribute('aria-hidden', 'false');
  if (backdrop) backdrop.setAttribute('aria-hidden', 'false');

  // Render skeleton immediately from cached card data so the panel is responsive.
  _renderDetailBody(release, null, 'loading');

  // Install focus trap + Escape handler.
  _detailKeydownHandler = (ev) => _detailTrapKeydown(ev);
  document.addEventListener('keydown', _detailKeydownHandler);

  // Focus the close button after the panel paints (transform ends).
  setTimeout(() => {
    document.getElementById('disc-v2-detail-close-btn')?.focus();
  }, 50);

  try {
    const r = release.release || {};
    const detail = r.id ? await DiscoverV2.loadDetail(r.id) : null;
    _renderDetailBody(release, detail, 'loaded');
  } catch (e) {
    _renderDetailBody(release, null, 'error', String(e));
  }

  // YouTube preview is lazy: only fired after the Discogs detail has resolved
  // (or failed). It's intentionally fire-and-forget — failure shows an inline
  // message, never blocks the rest of the panel.
  _loadYouTubePreview(release);
}

function _extractYouTubeId(url) {
  if (!url) return null;
  const s = String(url);
  // Common shapes:
  //   https://www.youtube.com/watch?v=XXXXXXXXXXX
  //   https://youtu.be/XXXXXXXXXXX
  //   https://www.youtube.com/embed/XXXXXXXXXXX
  //   https://m.youtube.com/watch?v=XXXXXXXXXXX
  let m = s.match(/[?&]v=([A-Za-z0-9_-]{6,})/);
  if (m) return m[1];
  m = s.match(/youtu\.be\/([A-Za-z0-9_-]{6,})/);
  if (m) return m[1];
  m = s.match(/youtube\.com\/embed\/([A-Za-z0-9_-]{6,})/);
  if (m) return m[1];
  return null;
}

async function _loadYouTubePreview(release) {
  const slot = document.getElementById('disc-v2-detail-youtube-slot');
  if (!slot) return;
  slot.innerHTML = '<div class="disc-v2-yt-placeholder"><span class="disc-v2-spinner" aria-hidden="true"></span> Loading YouTube previews…</div>';
  const entry = await DiscoverV2.searchYouTube(release, 3);
  // The user may have closed (and re-opened a different) panel while we waited.
  // Only paint if this slot still belongs to the same release.
  if (!_detailCurrentRelease || _detailCurrentRelease.release_key !== release.release_key) return;
  if (entry.status === 'error') {
    slot.innerHTML = '<div class="disc-v2-yt-placeholder">Could not load YouTube previews: ' + _esc(entry.error || '') + '</div>';
    return;
  }
  const all = (entry.candidates || []).filter(c => _extractYouTubeId(c.url));
  if (!all.length) {
    slot.innerHTML = '<div class="disc-v2-yt-placeholder">No YouTube previews found for this release.</div>';
    return;
  }
  // The backend already drops mismatches when at least one match exists.
  // When every candidate is flagged `mismatch:true`, the backend kept them
  // ALL — that's the "no clean match found" fallback. Surface that to the
  // user with a clearer placeholder rather than silently playing wrong audio.
  const matches = all.filter(c => c.mismatch !== true);
  if (matches.length === 0) {
    const r = release.release || {};
    slot.innerHTML =
      '<div class="disc-v2-yt-placeholder">' +
      'No YouTube match found for <em>' + _esc(r.artist || '') +
      ' — ' + _esc(r.title || r.album || '') + '</em>. ' +
      'YouTube returned only mismatched results.' +
      '</div>';
    return;
  }
  _renderYouTubeCarousel(slot, matches, 0, release);
}

// Cheap mismatch heuristic for UX audit Issue 3: if neither the album nor
// the artist token appears in the YT result title, mark the result as a
// likely mismatch so the user notices before clicking play. Tokenizes both
// sides on whitespace + lowercases, then checks for any 4+ char overlap.
function _ytLikelyMismatch(ytTitle, expectedArtist, expectedAlbum) {
  const norm = (s) => String(s || '').toLowerCase().split(/[^a-z0-9]+/).filter(t => t.length >= 4);
  const haystack = new Set(norm(ytTitle));
  const needles = [...norm(expectedArtist), ...norm(expectedAlbum)];
  if (!needles.length || !haystack.size) return false;
  return !needles.some(n => haystack.has(n));
}

function _renderYouTubeCarousel(slot, candidates, index, releaseHint) {
  const cur = candidates[index];
  const videoId = _extractYouTubeId(cur.url);
  // rel=0 disables related-video sidebar; do NOT autoplay (avoid alert/dialog
  // analogue and respect user agency).
  const embedUrl = 'https://www.youtube.com/embed/' + encodeURIComponent(videoId) + '?rel=0';
  // UX audit Issue 3: surface YouTube result title + channel ABOVE the
  // iframe (not below) so the user spots an unrelated audiobook before
  // pressing play. Also flag obvious mismatches with a ⚠ icon.
  const r = releaseHint?.release || {};
  const expectedArtist = r.artist || '';
  const expectedAlbum = r.album || r.title || '';
  const mismatch = _ytLikelyMismatch(cur.title, expectedArtist, expectedAlbum);
  const mismatchBadge = mismatch
    ? `<span class="disc-v2-yt-mismatch" title="This result doesn't seem to match the album. Check before downloading." aria-label="Possible mismatch">⚠</span>`
    : '';
  slot.innerHTML = `
    <div class="disc-v2-yt-carousel" data-yt-index="${index}">
      <div class="disc-v2-yt-result-meta">
        ${mismatchBadge}
        <div class="disc-v2-yt-result-text">
          <div class="disc-v2-yt-title" title="${_esc(cur.title || '')}">${_esc(cur.title || 'Untitled')}</div>
          ${cur.channel ? `<div class="disc-v2-yt-channel">${_esc(cur.channel)}</div>` : ''}
        </div>
        <div class="disc-v2-yt-counter">${index + 1} / ${candidates.length}</div>
      </div>
      <div class="disc-v2-yt-frame">
        <iframe src="${_esc(embedUrl)}"
                title="${_esc(cur.title || 'YouTube preview')}"
                allow="encrypted-media; picture-in-picture"
                referrerpolicy="strict-origin-when-cross-origin"
                allowfullscreen></iframe>
      </div>
      <div class="disc-v2-yt-controls">
        <div class="disc-v2-yt-nav">
          <button data-yt-act="prev" aria-label="Previous YouTube candidate" ${index === 0 ? 'disabled' : ''}>‹</button>
          <button data-yt-act="next" aria-label="Next YouTube candidate" ${index === candidates.length - 1 ? 'disabled' : ''}>›</button>
        </div>
      </div>
    </div>
  `;
  slot.querySelectorAll('[data-yt-act]').forEach(btn => {
    btn.addEventListener('click', () => {
      const act = btn.getAttribute('data-yt-act');
      const next = act === 'prev' ? index - 1 : index + 1;
      if (next < 0 || next >= candidates.length) return;
      _renderYouTubeCarousel(slot, candidates, next, releaseHint);
    });
  });
}

function _renderDetailBody(release, detail, status, errorMsg) {
  const body = document.getElementById('disc-v2-detail-body');
  if (!body) return;

  // Prefer Discogs detail when present; fall back to the card's release dict.
  const r = release.release || {};
  const id = (detail && detail.id) || r.id || 0;
  const title = (detail && detail.title) || r.title || 'Untitled';
  const artist = (detail && detail.artist) || r.artist || 'Unknown Artist';
  const year = (detail && detail.year) || r.year || '';
  const label = (detail && detail.label) || r.label || '';
  const labelId = (detail && detail.label_id) || r.label_id || null;
  const cover = (detail && (detail.cover || detail.cover_image)) || r.cover_image || r.thumb || '';
  const styles = (detail && detail.styles) || [];
  const tracks = (detail && detail.tracklist) || [];

  const isSaved = DiscoverV2.state.savedKeys.has(release.release_key);
  const isDismissed = DiscoverV2.state.dismissedKeys.has(release.release_key);
  const followsLabel = labelId &&
    DiscoverV2.state.followedLabels.some(l => l.label_id === labelId);
  const artistId = (detail && detail.artist_id) || r.artist_id || 0;
  const artistBlocked = artistId &&
    (DiscoverV2.state.blockedArtists || []).some(b => b.discogs_artist_id === artistId);
  const labelBlocked = labelId &&
    (DiscoverV2.state.blockedLabels || []).some(b => b.discogs_label_id === labelId);

  const trackHtml = tracks.length
    ? `<ol class="disc-v2-detail-tracklist" aria-label="Tracklist">
         ${tracks.map(t => `
           <li>
             <span class="pos">${_esc(t.position || '')}</span>
             <span class="title">${_esc(t.title || '')}</span>
             <span class="dur">${_esc(t.duration || '')}</span>
           </li>`).join('')}
       </ol>`
    : (status === 'loading'
        ? '<p style="font-size:12px;color:var(--muted);"><span class="disc-v2-spinner" aria-hidden="true"></span> Loading tracklist…</p>'
        : '<p style="font-size:12px;color:var(--muted);">No tracklist available.</p>');

  const errHtml = status === 'error'
    ? `<p class="disc-v2-detail-error" role="alert">Could not load details: ${_esc(errorMsg || '')}</p>`
    : '';

  body.innerHTML = `
    ${cover ? `<img src="${_esc(cover)}" alt="" style="width:100%;max-width:320px;border-radius:8px;margin-bottom:12px;">` : ''}
    <h2 id="disc-v2-detail-heading" style="margin:0 0 4px;font-size:18px;">${_esc(title)}</h2>
    <p style="margin:0 0 6px;color:var(--muted);">
      ${_esc(artist)}${year ? ' · ' + _esc(String(year)) : ''}${label ? ' · ' + _esc(label) : ''}
    </p>
    ${styles.length ? `<p style="margin:0 0 12px;font-size:12px;color:var(--muted);">${styles.map(_esc).join(' · ')}</p>` : ''}
    <div class="disc-v2-detail-actions">
      <button class="disc-v2-detail-action ${isSaved ? 'saved' : 'primary'}" data-detail-act="save">
        ${isSaved ? '✓ Saved' : '💚 Save'}
      </button>
      <button class="disc-v2-detail-action" data-detail-act="download">⬇ Download album</button>
      <button class="disc-v2-detail-action" data-detail-act="snooze">💤 Snooze…</button>
      <button class="disc-v2-detail-action" data-detail-act="dismiss" ${isDismissed ? 'disabled' : ''}>
        ✕ ${isDismissed ? 'Dismissed' : 'Dismiss'}
      </button>
      ${labelId && !followsLabel
        ? `<button class="disc-v2-detail-action" data-detail-act="follow-label" data-label-id="${labelId}" data-label-name="${_esc(label)}">+ Follow ${_esc(label)}</button>`
        : ''}
      ${artistId && !artistBlocked
        ? `<button class="disc-v2-detail-action" data-detail-act="block-artist" data-artist-id="${artistId}" data-artist-name="${_esc(artist)}" title="Stop seeing this artist in Discover">🚫 Block ${_esc(artist)}</button>`
        : ''}
      ${labelId && !labelBlocked
        ? `<button class="disc-v2-detail-action" data-detail-act="block-label" data-label-id="${labelId}" data-label-name="${_esc(label)}" title="Stop seeing this label in Discover">🚫 Block ${_esc(label)}</button>`
        : ''}
    </div>
    <div id="disc-v2-detail-youtube-slot"></div>
    ${errHtml}
    ${trackHtml}
    ${id ? `<p style="margin-top:14px;font-size:12px;"><a href="https://www.discogs.com/release/${id}" target="_blank" rel="noopener noreferrer">View on Discogs ↗</a></p>` : ''}
  `;

  // Wire action buttons via delegation.
  body.querySelectorAll('[data-detail-act]').forEach(btn => {
    btn.addEventListener('click', async (ev) => {
      ev.preventDefault();
      const act = btn.getAttribute('data-detail-act');
      try {
        if (act === 'save') {
          await DiscoverV2.save(release);
        } else if (act === 'snooze') {
          // Don't close the panel — the popover anchors against the button.
          _openSnoozePopover(release, btn);
          return;
        } else if (act === 'dismiss') {
          await DiscoverV2.dismiss(release);
          _closeDetailPanel();
          return;
        } else if (act === 'follow-label') {
          const lid = parseInt(btn.getAttribute('data-label-id'), 10);
          const lname = btn.getAttribute('data-label-name') || '';
          await DiscoverV2.followLabel(lid, lname);
        } else if (act === 'download') {
          // Inside-the-panel download is intentional, not Shift+click bypass —
          // user already navigated here, so we go straight to runDownload.
          const query = _buildDownloadQuery(release);
          if (query && typeof runDownload === 'function') {
            runDownload(query, {});
          }
        } else if (act === 'block-artist') {
          const aid = parseInt(btn.getAttribute('data-artist-id'), 10);
          const aname = btn.getAttribute('data-artist-name') || '';
          await DiscoverV2.blockArtist(aid, aname);
          // Blocking hides this release from future scans — close + remove from feed.
          DiscoverV2.state.dismissedKeys.add(release.release_key);
          _closeDetailPanel();
          return;
        } else if (act === 'block-label') {
          const lid = parseInt(btn.getAttribute('data-label-id'), 10);
          const lname = btn.getAttribute('data-label-name') || '';
          await DiscoverV2.blockLabel(lid, lname);
          DiscoverV2.state.dismissedKeys.add(release.release_key);
          _closeDetailPanel();
          return;
        }
        // Re-render to reflect updated state (e.g., save button → ✓ Saved).
        _renderDetailBody(release, detail, status, errorMsg);
        // The re-render blows away the YouTube slot — repaint from cache.
        _loadYouTubePreview(release);
      } catch (e) {
        const err = document.createElement('p');
        err.className = 'disc-v2-detail-error';
        err.setAttribute('role', 'alert');
        err.textContent = 'Action failed: ' + String(e);
        body.appendChild(err);
      }
    });
  });
}

function _detailTrapKeydown(ev) {
  if (ev.key === 'Escape') {
    _closeDetailPanel();
    return;
  }
  if (ev.key !== 'Tab') return;
  const panel = document.getElementById('disc-v2-detail-panel');
  if (!panel || panel.getAttribute('aria-hidden') !== 'false') return;
  // Cycle Tab focus inside the panel only.
  const focusables = panel.querySelectorAll(
    'button, a, input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (ev.shiftKey && document.activeElement === first) {
    ev.preventDefault();
    last.focus();
  } else if (!ev.shiftKey && document.activeElement === last) {
    ev.preventDefault();
    first.focus();
  }
}

function _closeDetailPanel() {
  const panel = document.getElementById('disc-v2-detail-panel');
  const backdrop = document.getElementById('disc-v2-detail-backdrop');
  if (panel) panel.setAttribute('aria-hidden', 'true');
  if (backdrop) backdrop.setAttribute('aria-hidden', 'true');
  if (_detailKeydownHandler) {
    document.removeEventListener('keydown', _detailKeydownHandler);
    _detailKeydownHandler = null;
  }
  // Return focus to whatever the user was on before opening the panel.
  if (_detailReturnFocusEl && typeof _detailReturnFocusEl.focus === 'function') {
    try { _detailReturnFocusEl.focus(); } catch (_) {}
  }
  _detailReturnFocusEl = null;
  _detailCurrentRelease = null;
}

// Download confirm modal (Shift+click power flow).
// PRD §5.6: modal default focus = Cancel — sticky-Shift + accidental-Enter
// must NOT trigger an unintended download.
let _dlConfirmReturnFocusEl = null;
let _dlConfirmRelease = null;
let _dlConfirmKeydownHandler = null;

function _buildDownloadQuery(release) {
  const r = release?.release || {};
  const artist = (r.artist || '').trim();
  let albumOrTitle = (r.album || '').trim();
  if (!albumOrTitle) {
    // Discogs raw `title` is usually "Artist - Album". Strip the redundant
    // artist prefix so the query doesn't duplicate the artist name — UX
    // audit M-2 saw "soFa elsewhere Sandy B (3) & soFa elsewhere - Forward
    // In Reverse Pt.1" because both fields were concatenated raw.
    const title = (r.title || '').trim();
    albumOrTitle = title.includes(' - ')
      ? title.split(' - ').slice(1).join(' - ').trim()
      : title;
  }
  return [artist, albumOrTitle].filter(Boolean).join(' ');
}

function _openDownloadConfirm(release) {
  const modal = document.getElementById('disc-v2-dl-confirm');
  const backdrop = document.getElementById('disc-v2-dl-confirm-backdrop');
  const cancelBtn = document.getElementById('disc-v2-dl-confirm-cancel');
  const goBtn = document.getElementById('disc-v2-dl-confirm-go');
  const body = document.getElementById('disc-v2-dl-confirm-body');
  if (!modal || !backdrop) return;

  _dlConfirmReturnFocusEl = document.activeElement;
  _dlConfirmRelease = release;

  const r = release?.release || {};
  const query = _buildDownloadQuery(release);
  if (body) {
    // Strip redundant "Artist - " prefix from the displayed title so the
    // first line reads naturally even when Discogs returns the full
    // "Artist - Album" string in `title`.
    const rawTitle = (r.title || '').trim();
    const cleanTitle = rawTitle.includes(' - ')
      ? rawTitle.split(' - ').slice(1).join(' - ').trim()
      : rawTitle;
    body.innerHTML =
      `Download <strong>${_esc(cleanTitle || 'Untitled')}</strong> by ` +
      `<strong>${_esc(r.artist || 'Unknown Artist')}</strong>?` +
      `<br><span style="color:var(--muted);font-size:12px;">` +
      `We'll search YouTube for: <code>${_esc(query)}</code></span>`;
  }
  // Reset the Go button label (in case a prior run left it in progress).
  if (goBtn) {
    goBtn.disabled = false;
    goBtn.textContent = 'Download album';
  }

  modal.setAttribute('aria-hidden', 'false');
  backdrop.setAttribute('aria-hidden', 'false');

  _dlConfirmKeydownHandler = (ev) => _dlConfirmTrapKeydown(ev);
  document.addEventListener('keydown', _dlConfirmKeydownHandler);

  // Critical: Cancel is the default focus.
  setTimeout(() => { cancelBtn?.focus(); }, 30);
}

function _dlConfirmTrapKeydown(ev) {
  if (ev.key === 'Escape') {
    _closeDownloadConfirm();
    return;
  }
  if (ev.key !== 'Tab') return;
  const modal = document.getElementById('disc-v2-dl-confirm');
  if (!modal || modal.getAttribute('aria-hidden') !== 'false') return;
  const focusables = modal.querySelectorAll(
    'button, a, input, [tabindex]:not([tabindex="-1"])'
  );
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (ev.shiftKey && document.activeElement === first) {
    ev.preventDefault();
    last.focus();
  } else if (!ev.shiftKey && document.activeElement === last) {
    ev.preventDefault();
    first.focus();
  }
}

function _closeDownloadConfirm() {
  const modal = document.getElementById('disc-v2-dl-confirm');
  const backdrop = document.getElementById('disc-v2-dl-confirm-backdrop');
  if (modal) modal.setAttribute('aria-hidden', 'true');
  if (backdrop) backdrop.setAttribute('aria-hidden', 'true');
  if (_dlConfirmKeydownHandler) {
    document.removeEventListener('keydown', _dlConfirmKeydownHandler);
    _dlConfirmKeydownHandler = null;
  }
  if (_dlConfirmReturnFocusEl && typeof _dlConfirmReturnFocusEl.focus === 'function') {
    try { _dlConfirmReturnFocusEl.focus(); } catch (_) {}
  }
  _dlConfirmReturnFocusEl = null;
  _dlConfirmRelease = null;
}

async function _runDownloadConfirmGo() {
  const release = _dlConfirmRelease;
  if (!release) return;
  const query = _buildDownloadQuery(release);
  if (!query) {
    if (typeof showToast === 'function') showToast('Cannot build a download query — release has no artist or title');
    return;
  }
  // Hand off to the existing runDownload helper, which speaks SSE + the
  // shared download config. Close the modal immediately so the user can
  // continue browsing; download progress is surfaced via the existing toast.
  _closeDownloadConfirm();
  if (typeof runDownload === 'function') {
    runDownload(query, {});
  } else {
    // Fallback for environments without the v1 download helper: fire-and-forget.
    try {
      await fetch('/api/download', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query}),
      });
    } catch (_) { /* surfaced through network UI elsewhere */ }
  }
}

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// Timestamped export filename so a user with multiple machines doesn't
// overwrite their own backups when they re-export.
function _discoverV2ExportFilename(now) {
  const d = now || new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `discover-${yyyy}-${mm}-${dd}.db.gz`;
}

// Build a human-readable diff line from before/after import counts. Skips
// fields with no change so the toast stays short.
function _formatImportDiff(before, after) {
  const labels = {
    saved: 'saves',
    dismissed: 'dismisses',
    snoozed: 'snoozes',
    downloaded: 'downloads',
    followed_labels: 'followed labels',
    blocked_artists: 'blocked artists',
    blocked_labels: 'blocked labels',
  };
  const parts = [];
  for (const key of Object.keys(labels)) {
    const b = (before && before[key]) || 0;
    const a = (after && after[key]) || 0;
    const delta = a - b;
    if (delta === 0) continue;
    const sign = delta > 0 ? '+' : '';
    parts.push(`${sign}${delta} ${labels[key]}`);
  }
  if (!parts.length) return 'Imported — no changes';
  return 'Imported · ' + parts.join(', ');
}

// ── Keyboard shortcuts (T-028) ──────────────────────────────────────────
// Active-card index lives at module scope so j/k navigation survives feed
// re-renders (after each Save / Dismiss / Snooze the grid is rebuilt).
//
// Issue #69 fix: the numeric index is NOT stable across mutations — when
// a card is removed, the card that was at `index+1` slides into `index`
// and silently inherits the active state. A stray Space/Enter then fires
// dismiss/snooze on the wrong neighbor. We additionally track the
// release_key of the active card and re-derive the numeric index from it
// on every re-render. If the key is gone (because the user just
// dismissed/snoozed it), we drop the active state entirely instead of
// shifting it onto a neighbor.
let _activeCardIndex = -1;
let _activeReleaseKey = null;

function _visibleDiscoverCards() {
  // Use DOM order so the cursor follows what the user actually sees.
  return Array.from(document.querySelectorAll('#disc-v2-grid .disc-v2-card'));
}

function _setActiveCard(index, scroll = true) {
  const cards = _visibleDiscoverCards();
  if (!cards.length) {
    _activeCardIndex = -1;
    _activeReleaseKey = null;
    return;
  }
  if (index < 0) index = 0;
  if (index >= cards.length) index = cards.length - 1;
  cards.forEach(c => c.classList.remove('active'));
  cards[index].classList.add('active');
  if (scroll) {
    cards[index].scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }
  _activeCardIndex = index;
  _activeReleaseKey = cards[index].getAttribute('data-release-key');
}

function _activeRelease() {
  const cards = _visibleDiscoverCards();
  if (_activeCardIndex < 0 || _activeCardIndex >= cards.length) return null;
  const key = cards[_activeCardIndex].getAttribute('data-release-key');
  // Issue #69: defence-in-depth. If the numeric index now points at a card
  // whose release_key differs from the one the user last selected, the
  // grid mutated under us — refuse to act rather than mutating a neighbor.
  if (_activeReleaseKey && key !== _activeReleaseKey) return null;
  return DiscoverV2.state.cardsByKey.get(key) || null;
}

function _kbdIsTextInputActive() {
  const el = document.activeElement;
  if (!el || el === document.body) return false;
  const tag = (el.tagName || '').toUpperCase();
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (el.isContentEditable) return true;
  return false;
}

function _kbdDialogIsOpen() {
  // Any of: detail panel, download confirm, keyboard help.
  return ['disc-v2-detail-panel', 'disc-v2-dl-confirm', 'disc-v2-kbd-help']
    .some(id => document.getElementById(id)?.getAttribute('aria-hidden') === 'false');
}

function _toggleKbdHelp() {
  const modal = document.getElementById('disc-v2-kbd-help');
  const backdrop = document.getElementById('disc-v2-kbd-backdrop');
  if (!modal) return;
  const isOpen = modal.getAttribute('aria-hidden') === 'false';
  modal.setAttribute('aria-hidden', isOpen ? 'true' : 'false');
  if (backdrop) backdrop.setAttribute('aria-hidden', isOpen ? 'true' : 'false');
  if (!isOpen) {
    setTimeout(() => document.getElementById('disc-v2-kbd-help-close')?.focus(), 30);
  }
}

// ── Snooze popover (T-033) ───────────────────────────────────────────────
// PRD §6.11: 1w / 1m / 3m. 1m is the default (the "default" CSS class +
// initial focus); Enter on the default button fires the snooze.
let _snoozePopRelease = null;
let _snoozePopReturnFocusEl = null;
let _snoozePopKeydownHandler = null;

function _openSnoozePopover(release, anchorEl) {
  const pop = document.getElementById('disc-v2-snooze-pop');
  if (!pop) return;
  _snoozePopRelease = release;
  _snoozePopReturnFocusEl = document.activeElement;

  // Anchor the popover near the clicked button. If no anchor was passed
  // (e.g., keyboard z shortcut), center over the current active card.
  const anchor = anchorEl || document.querySelector('.disc-v2-card.active') || document.body;
  const rect = anchor.getBoundingClientRect();
  const top = window.scrollY + rect.bottom + 6;
  // Right-align if the anchor's right edge is past 70% of viewport.
  let left = window.scrollX + rect.left;
  if (rect.left > window.innerWidth * 0.7) {
    left = window.scrollX + rect.right - 220;
  }
  pop.style.top = `${top}px`;
  pop.style.left = `${left}px`;
  pop.setAttribute('aria-hidden', 'false');

  _snoozePopKeydownHandler = (ev) => {
    if (ev.key === 'Escape') {
      ev.preventDefault();
      _closeSnoozePopover();
    }
  };
  document.addEventListener('keydown', _snoozePopKeydownHandler);
  // Click-outside-to-close.
  setTimeout(() => document.addEventListener('mousedown', _snoozePopOutsideHandler), 0);

  // Default focus on the 1-month button.
  setTimeout(() => {
    const def = pop.querySelector('button.default') || pop.querySelector('button');
    def?.focus();
  }, 30);
}

function _snoozePopOutsideHandler(ev) {
  const pop = document.getElementById('disc-v2-snooze-pop');
  if (pop && !pop.contains(ev.target)) _closeSnoozePopover();
}

function _closeSnoozePopover() {
  const pop = document.getElementById('disc-v2-snooze-pop');
  if (pop) pop.setAttribute('aria-hidden', 'true');
  if (_snoozePopKeydownHandler) {
    document.removeEventListener('keydown', _snoozePopKeydownHandler);
    _snoozePopKeydownHandler = null;
  }
  document.removeEventListener('mousedown', _snoozePopOutsideHandler);
  if (_snoozePopReturnFocusEl && typeof _snoozePopReturnFocusEl.focus === 'function') {
    try { _snoozePopReturnFocusEl.focus(); } catch (_) {}
  }
  _snoozePopReturnFocusEl = null;
  _snoozePopRelease = null;
}

async function _runSnoozeWithDuration(duration) {
  // ── Issue #69 fix ────────────────────────────────────────────────────
  // Snooze BEFORE closing the popover. If we closed first, focus would be
  // restored to the original 💤 button; the subsequent feed re-render then
  // destroys that button, the browser hops focus to body, and a stray
  // Space/Enter from the user lands on _activeRelease() — which by then
  // points at an *adjacent* card (see the sticky `_activeReleaseKey`
  // tracking added alongside this fix). Running the snooze first means
  // the re-render happens while the popover is still the focus owner,
  // so when we close the focus restore target is either still in the
  // DOM or harmlessly absent. Either way, no synthetic activation can
  // target an adjacent card.
  // ─────────────────────────────────────────────────────────────────────
  const release = _snoozePopRelease;
  if (!release) {
    _closeSnoozePopover();
    return;
  }
  try {
    _collapseDiscoverCard(release.release_key);
    await DiscoverV2.snooze(release, duration);
  } catch (_) {
    if (typeof showToast === 'function') showToast('Snooze failed', true);
  } finally {
    _closeSnoozePopover();
  }
}

// Acknowledge dismiss/snooze on the card itself before the full grid rebuild —
// the acted-on card used to teleport away in a single frame (aliveness audit).
// Opacity/scale only: the grid reflow still happens at re-render time.
function _collapseDiscoverCard(releaseKey) {
  if (_prefersReducedMotion || !releaseKey) return;
  const card = document.querySelector(
    '#disc-v2-grid .disc-v2-card[data-release-key="' + (window.CSS && CSS.escape ? CSS.escape(releaseKey) : releaseKey) + '"]');
  if (!card) return;
  card.style.transition = 'opacity .18s ease, transform .18s ease';
  card.style.opacity = '0';
  card.style.transform = 'scale(0.96)';
  card.style.pointerEvents = 'none';
}

function _handleDiscoverKeydown(ev) {
  // Don't intercept while the user is typing in a search box.
  if (_kbdIsTextInputActive()) return;

  // The Discover tab must actually be visible. The simplest reliable check
  // is "does the v2 section exist and is it not display:none". The section
  // is hidden when the user is on a different tab.
  const section = document.getElementById('disc-v2-section');
  if (!section) return;
  const visible = section.offsetParent !== null;
  if (!visible) return;

  // `?` opens / closes the help overlay regardless of any other dialog. We
  // both preventDefault AND stopPropagation so the app-wide `?` handler
  // doesn't ALSO fire — without stopPropagation the user saw the Discover
  // help AND the global help open simultaneously (UX audit Issue 1).
  if (ev.key === '?') {
    ev.preventDefault();
    ev.stopPropagation();
    if (typeof ev.stopImmediatePropagation === 'function') ev.stopImmediatePropagation();
    _toggleKbdHelp();
    return;
  }

  // Other dialogs eat their own keys.
  if (_kbdDialogIsOpen()) return;

  switch (ev.key) {
    case 'j': ev.preventDefault(); _setActiveCard(_activeCardIndex + 1); return;
    case 'k': ev.preventDefault(); _setActiveCard(_activeCardIndex - 1); return;
    case 'Enter': {
      const rel = _activeRelease();
      if (!rel) return;
      ev.preventDefault();
      _openDetailPanel(rel.release_key);
      return;
    }
    case 's': {
      const rel = _activeRelease();
      if (!rel) return;
      ev.preventDefault();
      DiscoverV2.save(rel);
      return;
    }
    case 'x': {
      const rel = _activeRelease();
      if (!rel) return;
      ev.preventDefault();
      _collapseDiscoverCard(rel.release_key);
      DiscoverV2.dismiss(rel);
      return;
    }
    case 'z': {
      const rel = _activeRelease();
      if (!rel) return;
      ev.preventDefault();
      _openSnoozePopover(rel, null);
      return;
    }
    case 'D': {  // intentionally uppercase: requires Shift
      const rel = _activeRelease();
      if (!rel) return;
      ev.preventDefault();
      _openDownloadConfirm(rel);
      return;
    }
  }
}

// ── Wiring (DOMContentLoaded) ────────────────────────────────────────────
function initDiscoverV2() {
  if (!document.getElementById('disc-v2-section')) return;

  // Move all overlay elements out of #discover-tab-content so that the tab-
  // switch animation's `transform` doesn't form a new containing block for
  // their `position: fixed` rules. With the overlays underneath that ancestor,
  // the detail panel was resolving top:0 / bottom:0 against the full-content-
  // tall tab body (40,000px+) instead of the viewport, and would scroll OFF
  // the screen the moment the user moused the page. See UX audit Issue 2.
  // Moving to <body> as direct children fixes all overlays at once.
  for (const id of [
    'disc-v2-detail-backdrop',
    'disc-v2-detail-panel',
    'disc-v2-snooze-pop',
    'disc-v2-kbd-backdrop',
    'disc-v2-kbd-help',
    'disc-v2-dl-confirm-backdrop',
    'disc-v2-dl-confirm',
  ]) {
    const el = document.getElementById(id);
    if (el && el.parentElement !== document.body) {
      document.body.appendChild(el);
    }
  }

  // Subscribe each renderer once.
  DiscoverV2.subscribe(_renderDiscoverV2Feed);
  DiscoverV2.subscribe(_renderDiscoverV2ScanProgress);
  DiscoverV2.subscribe(_renderDiscoverV2ScanWarnings);
  DiscoverV2.subscribe(_renderDiscoverV2Onboarding);
  DiscoverV2.subscribe(_renderDiscoverV2TokenBanner);
  DiscoverV2.subscribe(_renderDiscoverV2Followed);
  DiscoverV2.subscribe(_renderDiscoverV2Blocked);

  // Refresh button.
  const refreshBtn = document.getElementById('disc-v2-refresh-btn');
  if (refreshBtn) refreshBtn.addEventListener('click', () => DiscoverV2.runScan());

  // Settings toggle.
  const settingsBtn = document.getElementById('disc-v2-settings-btn');
  const settings = document.getElementById('disc-v2-settings');
  if (settingsBtn && settings) {
    settingsBtn.addEventListener('click', async () => {
      const wasHidden = settings.style.display === 'none';
      settings.style.display = wasHidden ? '' : 'none';
      if (wasHidden) {
        // Lazy-load stats when settings is opened.
        const block = document.getElementById('disc-v2-stats-block');
        if (block) block.innerHTML = '<em>Loading stats…</em>';
        try {
          const stats = await DiscoverV2.refreshStats();
          _renderDiscoverV2Stats(stats);
        } catch (e) {
          if (block) block.innerHTML = '<em>Could not load stats.</em>';
        }
        // Also lazy-load the Saved releases list (UX audit M-4).
        _refreshSavedFromBackend();
      }
    });
  }

  // Filter chips re-trigger scan on change (server-side feeder selection).
  document.querySelectorAll('#disc-v2-filter-bar input[data-source]').forEach(el =>
    el.addEventListener('change', () => DiscoverV2.runScan()));
  // Year filter changes the backend window — re-scan. "custom" reveals an
  // inline number input; only re-scan once a valid 4-digit year is entered.
  const yearSelect = document.getElementById('disc-v2-year');
  const yearCustom = document.getElementById('disc-v2-year-custom');
  yearSelect?.addEventListener('change', () => {
    if (yearCustom) {
      yearCustom.style.display = yearSelect.value === 'custom' ? '' : 'none';
      if (yearSelect.value === 'custom') {
        yearCustom.focus();
        // Don't scan yet — wait for the user to type a year.
        if (!yearCustom.value) return;
      }
    }
    DiscoverV2.runScan();
  });
  yearCustom?.addEventListener('change', () => {
    const v = parseInt(yearCustom.value || '', 10);
    if (Number.isFinite(v) && v >= 1900 && v <= 2099) DiscoverV2.runScan();
  });
  // Sort is purely client-side — just re-render the existing fetch.
  document.getElementById('disc-v2-sort')?.addEventListener('change', _renderDiscoverV2Feed);

  // ── Client-side filter row 2: search / hide-saved / hide-dismissed / styles ──
  // These narrow the loaded feed without re-scanning. State is persisted
  // to localStorage so it survives reloads (matches the sort-persistence
  // pattern at line 3891).
  const searchInput = document.getElementById('disc-v2-search');
  if (searchInput) {
    searchInput.value = _discoverFilters.search || '';
    let searchDebounce = null;
    searchInput.addEventListener('input', () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => {
        _discoverFilters.search = searchInput.value || '';
        _persistDiscoverFilters();
        _renderDiscoverV2Feed();
      }, 180);
    });
  }
  const hideSavedEl = document.getElementById('disc-v2-hide-saved');
  if (hideSavedEl) {
    hideSavedEl.checked = !!_discoverFilters.hideSaved;
    hideSavedEl.addEventListener('change', () => {
      _discoverFilters.hideSaved = hideSavedEl.checked;
      _persistDiscoverFilters();
      _renderDiscoverV2Feed();
    });
  }
  const hideDismissedEl = document.getElementById('disc-v2-hide-dismissed');
  if (hideDismissedEl) {
    hideDismissedEl.checked = _discoverFilters.hideDismissed !== false;
    hideDismissedEl.addEventListener('change', () => {
      _discoverFilters.hideDismissed = hideDismissedEl.checked;
      _persistDiscoverFilters();
      _renderDiscoverV2Feed();
    });
  }
  // Style-chip clicks toggle membership in _discoverFilters.selectedStyles
  // via event delegation (the chips themselves are re-rendered on every feed
  // update by _renderDiscoverStyleChips).
  document.getElementById('disc-v2-style-chips')?.addEventListener('change', (ev) => {
    const target = ev.target;
    if (!target || !target.matches('input[data-style-key]')) return;
    const key = target.getAttribute('data-style-key') || '';
    if (target.checked) _discoverFilters.selectedStyles.add(key);
    else _discoverFilters.selectedStyles.delete(key);
    _persistDiscoverFilters();
    _renderDiscoverV2Feed();
  });
  document.getElementById('disc-v2-styles-clear')?.addEventListener('click', () => {
    _discoverFilters.selectedStyles.clear();
    _persistDiscoverFilters();
    _renderDiscoverV2Feed();
  });

  // Issue #169: retry from the inline error banner (shown when a refresh
  // 409'd after the initial-scan lock didn't clear in time).
  document.getElementById('disc-v2-scan-error-inline-retry')
    ?.addEventListener('click', () => DiscoverV2.runScan());

  // Scan-cancel button.
  document.getElementById('disc-v2-scan-cancel-btn')?.addEventListener('click', () => DiscoverV2.cancelScan());

  // Card grid click delegate — action buttons, Shift+click power flow, or
  // plain click → open panel.
  document.getElementById('disc-v2-grid')?.addEventListener('click', (ev) => {
    const card = ev.target.closest('.disc-v2-card');
    if (!card) return;
    const releaseKey = card.getAttribute('data-release-key');
    const release = DiscoverV2.state.cardsByKey.get(releaseKey);
    if (!release) return;
    const actBtn = ev.target.closest('[data-act]');
    if (actBtn) {
      ev.stopPropagation();
      const act = actBtn.getAttribute('data-act');
      if (act === 'save') DiscoverV2.save(release);
      else if (act === 'dismiss') { _collapseDiscoverCard(releaseKey); DiscoverV2.dismiss(release); }
      else if (act === 'snooze') _openSnoozePopover(release, actBtn);
      return;
    }
    if (ev.shiftKey) {
      ev.preventDefault();
      _openDownloadConfirm(release);
      return;
    }
    _openDetailPanel(releaseKey);
  });

  // Detail panel close: X button + backdrop click. Escape is handled by the
  // per-open focus-trap handler installed inside _openDetailPanel.
  document.getElementById('disc-v2-detail-close-btn')?.addEventListener('click', _closeDetailPanel);
  document.getElementById('disc-v2-detail-backdrop')?.addEventListener('click', _closeDetailPanel);

  // Download confirm modal close + confirm wiring.
  document.getElementById('disc-v2-dl-confirm-cancel')?.addEventListener('click', _closeDownloadConfirm);
  document.getElementById('disc-v2-dl-confirm-backdrop')?.addEventListener('click', _closeDownloadConfirm);
  document.getElementById('disc-v2-dl-confirm-go')?.addEventListener('click', _runDownloadConfirmGo);

  // Keyboard help overlay close (X button + backdrop click).
  document.getElementById('disc-v2-kbd-help-close')?.addEventListener('click', _toggleKbdHelp);
  document.getElementById('disc-v2-kbd-backdrop')?.addEventListener('click', _toggleKbdHelp);

  // Snooze popover buttons. Each button carries its duration in data-snooze-dur.
  // Issue #69: stopPropagation defends against any bubbled click being
  // re-interpreted by ancestor handlers (e.g. the grid delegate).
  document.querySelectorAll('#disc-v2-snooze-pop [data-snooze-dur]').forEach(btn =>
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      _runSnoozeWithDuration(btn.getAttribute('data-snooze-dur'));
    })
  );

  // Keyboard shortcuts: j/k navigate, Enter opens panel, s/x/z mutate,
  // D triggers download confirm modal, ? toggles help overlay.
  // Use capture:true so this fires BEFORE the app-wide ? handler — without
  // it the Discover ? overlay opens AND the app-wide overlay opens (UX
  // audit Issue 1). The stopPropagation inside the handler then suppresses
  // the app-wide listener.
  document.addEventListener('keydown', _handleDiscoverKeydown, true);

  // Whenever the feed re-renders we may need to reset the cursor. The card
  // grid is rebuilt by _renderDiscoverV2Feed — re-subscribe so we drop the
  // active class on cards that were removed.
  //
  // Issue #69: re-derive the active index from `_activeReleaseKey` instead
  // of trusting the numeric index. If the previously-active card was
  // removed (e.g. the user just snoozed/dismissed it) we drop the active
  // state entirely rather than silently transferring it to the card that
  // slid into that index — otherwise a stray Space/Enter would fire the
  // next mutation on an adjacent card (Issue #69 stray-dismiss bug).
  DiscoverV2.subscribe(() => {
    const cards = _visibleDiscoverCards();
    if (!cards.length) {
      _activeCardIndex = -1;
      _activeReleaseKey = null;
      return;
    }
    if (_activeReleaseKey) {
      const idx = cards.findIndex(c => c.getAttribute('data-release-key') === _activeReleaseKey);
      if (idx >= 0) {
        _activeCardIndex = idx;
        cards[idx].classList.add('active');
        return;
      }
      // Previously active card is gone — drop active state.
      _activeCardIndex = -1;
      _activeReleaseKey = null;
      return;
    }
    if (_activeCardIndex >= 0 && _activeCardIndex < cards.length) {
      cards[_activeCardIndex].classList.add('active');
      _activeReleaseKey = cards[_activeCardIndex].getAttribute('data-release-key');
    }
  });

  // Onboarding interactions.
  document.getElementById('disc-v2-onboarding-skip')?.addEventListener('click', () => {
    localStorage.setItem('disc-v2-onboarding-skipped', '1');
    _renderDiscoverV2Onboarding();
  });
  document.getElementById('disc-v2-onboarding-add-all')?.addEventListener('click', async () => {
    const container = document.getElementById('disc-v2-onboarding-suggestions');
    if (!container) return;
    const chips = [...container.querySelectorAll('button:not([disabled])')];
    if (!chips.length) return;
    const total = chips.length;
    // Fire-and-wait each chip click in sequence so the toast at the end
    // sees the final state. The chip click handler awaits an HTTP round-trip.
    for (const chip of chips) {
      chip.click();
      // Give the chip click's async work time to flip the chip state.
      await new Promise(r => setTimeout(r, 250));
    }
    // Count successes vs failures via the per-chip text marker.
    const followed = container.querySelectorAll('button[disabled]:not(.disc-v2-suggest-failed)').length;
    const failed = container.querySelectorAll('.disc-v2-suggest-failed').length;
    if (typeof showToast === 'function') {
      if (failed === 0) {
        showToast(`Followed ${followed} labels.`);
      } else {
        showToast(
          `Followed ${followed} of ${total} labels — ${failed} couldn't be matched on Discogs (⚠ chips show why).`,
          /* isError */ failed === total,
        );
      }
    }
  });

  // Label search.
  // Suggest from library — calls /api/discover/labels/suggested.
  document.getElementById('disc-v2-label-suggest-btn')?.addEventListener('click', async () => {
    const results = document.getElementById('disc-v2-label-suggest-results');
    if (!results) return;
    results.innerHTML = '<em style="color:var(--muted);">Suggesting…</em>';
    try {
      const items = await DiscoverV2.fetchSuggestedLabels(10);
      _renderSuggestedLabels(items);
    } catch (e) {
      results.innerHTML = '<em style="color:var(--muted);">Suggest failed.</em>';
    }
  });

  document.getElementById('disc-v2-label-search-btn')?.addEventListener('click', async () => {
    const q = document.getElementById('disc-v2-label-search')?.value || '';
    const results = document.getElementById('disc-v2-label-search-results');
    if (!q.trim() || !results) return;
    results.innerHTML = '<em style="color:var(--muted);">Searching…</em>';
    try {
      const hits = await DiscoverV2.searchLabels(q);
      results.innerHTML = '';
      if (!hits.length) { results.innerHTML = '<em style="color:var(--muted);">No matches.</em>'; return; }
      hits.slice(0, 8).forEach(h => {
        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.justifyContent = 'space-between';
        row.style.alignItems = 'center';
        row.style.padding = '4px 0';
        row.innerHTML = `<span>${_esc(h.name)}</span>`;
        const btn = document.createElement('button');
        btn.className = 'secondary-btn';
        btn.style.fontSize = '11px';
        btn.textContent = 'Follow';
        btn.addEventListener('click', async () => {
          await DiscoverV2.followLabel(h.id, h.name);
          btn.disabled = true;
          btn.textContent = '✓ Following';
        });
        row.appendChild(btn);
        results.appendChild(row);
      });
    } catch (e) {
      results.innerHTML = '<em style="color:var(--muted);">Search failed.</em>';
    }
  });

  // Export / Import.
  document.getElementById('disc-v2-export-btn')?.addEventListener('click', async () => {
    try {
      const blob = await DiscoverV2.exportState();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = _discoverV2ExportFilename();
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      if (typeof showToast === 'function') {
        showToast('Exported ' + a.download);
      }
    } catch (e) {
      if (typeof showToast === 'function') {
        showToast('Export failed: ' + (e && e.message || e), true);
      }
    }
  });
  document.getElementById('disc-v2-import-input')?.addEventListener('change', async (ev) => {
    const file = ev.target.files?.[0];
    if (!file) return;
    try { ev.target.value = ''; } catch (_) { /* reset so same file re-fires */ }

    // Pre-import audit so the user sees what they'd be replacing.
    const s = DiscoverV2.state;
    const summary =
      `You currently have:\n` +
      `  ${s.savedKeys.size} saved · ${s.dismissedKeys.size} dismissed · ${s.snoozedKeys.size} snoozed\n` +
      `  ${s.followedLabels.length} followed labels · ` +
      `${s.blockedArtists.length + s.blockedLabels.length} blocked entries\n\n` +
      `Importing "${file.name}" will REPLACE all of this. Continue?`;
    if (!(await _confirmDialog(summary, { confirmLabel: 'Replace state', danger: true }))) return;

    try {
      const result = await DiscoverV2.importState(file);
      if (typeof showToast === 'function') {
        showToast(_formatImportDiff(result.before, result.after));
      }
    } catch (e) {
      if (typeof showToast === 'function') {
        showToast('Import failed: ' + (e && e.message || e), true);
      }
    }
  });

  // Initial state + initial scan on tab activation.
  DiscoverV2.loadInitialState().then(async () => {
    // UX audit Issue 9: clear any stale scanError before deciding what to do.
    // If the server says no scan is running, then any in-memory scanError is
    // either from a prior session or from a 409 that has since resolved —
    // wiping it prevents the "Couldn't finish the scan" empty state from
    // appearing on a fresh page load.
    //
    // Issue #121: ALSO use this status response to gate the auto-scan. If a
    // scan is already running for this DB (e.g. another tab / a prior page
    // load is still streaming), kicking off /api/discover/feed here would
    // get a 409 — correctly handled in user-space (issue #67), but the
    // browser still emits a native "Failed to load resource: 409" console
    // error that taints every Playwright run asserting console.errors===[].
    // Skip the auto-scan when running===true; the in-flight scan's results
    // will surface through its existing SSE consumer, and the user can hit
    // Refresh manually if they want a new one.
    let status = null;
    try {
      status = await fetch('/api/discover/feed/status').then(r => r.json());
      if (status && status.running === false) {
        DiscoverV2.state.scanError = null;
      }
    } catch (_) { /* ignore, fall through */ }
    // Auto-scan on first open if labels are followed and the token is valid
    // AND no scan is already in flight (issue #121).
    if (status && status.running === true) return;
    if (DiscoverV2.state.tokenValid && DiscoverV2.state.followedLabels.length > 0) {
      DiscoverV2.runScan();
    }
  });
}

// Expose for tests.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { DiscoverV2, _renderDiscoverV2Card, _esc };
}

async function scanDiscover() {
  // T-024: v1 'scan library' DOM was removed. This function is retained
  // only because legacy code paths reference it; immediately returns when
  // its DOM is gone.
  const btn      = document.getElementById('disc-scan-btn');
  if (!btn) return;
  const statusEl = document.getElementById('disc-status');
  const resultsEl = document.getElementById('disc-results');
  const progress = document.getElementById('disc-progress');
  const fill     = document.getElementById('disc-progress-fill');
  const noToken  = document.getElementById('discover-no-token');

  const token = _discoverToken();
  if (!token) { noToken.style.display = ''; showToast('Add your Discogs token in the Library tab first'); return; }
  noToken.style.display = 'none';

  const sinceYear  = parseInt(document.getElementById('disc-since-year').value, 10) || (new Date().getFullYear() - 1);
  const maxArtists = parseInt(document.getElementById('disc-max-artists').value, 10) || 25;

  btn.disabled = true;
  btn.textContent = 'Scanning…';
  resultsEl.innerHTML = '';
  _renderStyleFilter([]);  // hide filter until results arrive
  document.getElementById('disc-style-filter').style.display = 'none';
  progress.style.display = '';
  fill.style.width = '0%';
  let suggested = 0;
  const seenStyles = new Set();

  const params = new URLSearchParams({
    since_year: String(sinceYear),
    max_artists: String(maxArtists),
    token,
  });

  try {
    const r = await fetch('/api/discover?' + params.toString());
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r.status}`);
    }
    await _consumeSSE(r, d => {
      if (d.error) { showToast('Discover error: ' + d.error); return; }
      if (d.total) fill.style.width = Math.round((d.processed / d.total) * 100) + '%';
      if (d.done) {
        fill.style.width = '100%';
        statusEl.textContent = `${d.suggested} new release${d.suggested === 1 ? '' : 's'} found`;
        _renderStyleFilter([...seenStyles]);
        return;
      }
      if (d.album) {
        suggested++;
        resultsEl.insertAdjacentHTML('beforeend', _renderSuggestion(d));
        (d.styles || []).forEach(s => seenStyles.add(s));
        statusEl.textContent = `${suggested} so far…`;
      }
    });
    if (suggested === 0) resultsEl.innerHTML = '<p style="font-size:13px;color:var(--muted);">No new releases found for your top artists. Try lowering "Released since" or raising "Top artists".</p>';
  } catch (err) {
    showToast('Discover failed: ' + err.message);
    statusEl.textContent = 'Error: ' + err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Scan library for new releases';
    setTimeout(() => { progress.style.display = 'none'; }, 800);
  }
}

function _renderSuggestion(d) {
  const art = d.thumb || d.cover || '';
  const styleList = d.styles || [];
  const styles = styleList.slice(0, 4)
    .map(s => `<span class="tag-pill">${_esc(s)}</span>`).join('');
  const year = d.year ? ` · ${d.year}` : '';
  const dlReady = _downloadConfig.available && _downloadConfig.ffmpeg;
  const dlBtn = dlReady
    ? `<button class="secondary-btn disc-dl-btn" data-query="${_esc(d.query || (d.artist + ' ' + d.album))}" style="font-size:12px;padding:4px 10px;">⬇ Album</button>`
    : '';
  const discogs = d.url ? `<a href="${_esc(d.url)}" target="_blank" rel="noopener" style="font-size:12px;color:var(--green);">Discogs ↗</a>` : '';
  const fmt = d.formats || [];
  const fmtLabel = fmt.includes('Compilation') ? 'Comp' :
                   fmt.includes('EP')          ? 'EP' :
                   fmt.includes('Single')      ? 'Single' :
                   fmt.includes('LP')          ? 'LP' : '';
  const fmtBadge = fmtLabel
    ? `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--surface);color:var(--muted);border:1px solid var(--border);flex-shrink:0;">${fmtLabel}</span>`
    : '';
  return `
    <div class="disc-card" data-styles="${_esc(styleList.join(','))}" style="display:flex;gap:12px;align-items:center;padding:10px;border:1px solid var(--border);border-radius:8px;margin-bottom:8px;background:var(--surface2);">
      ${art ? `<img src="${_esc(art)}" alt="" style="width:54px;height:54px;border-radius:6px;object-fit:cover;flex-shrink:0;">` : '<div style="width:54px;height:54px;border-radius:6px;background:var(--surface);flex-shrink:0;"></div>'}
      <div style="flex:1;min-width:0;">
        <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${_esc(d.album || '')}</div>
        <div style="font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center;flex-wrap:wrap;">${_esc(d.artist || '')}${year} ${fmtBadge}</div>
        <div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap;">${styles}</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end;flex-shrink:0;">
        ${dlBtn}
        ${discogs}
        <div class="disc-dl-progress" style="display:none;width:90px;">
          <div style="height:3px;background:var(--surface);border-radius:2px;overflow:hidden;margin-bottom:2px;">
            <div class="disc-dl-bar" style="height:100%;width:0%;background:var(--green);transition:width .2s;"></div>
          </div>
          <span class="disc-dl-status" style="font-size:11px;color:var(--muted);"></span>
        </div>
      </div>
    </div>`;
}

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Delegate clicks on per-suggestion Download buttons — routed through _Download.
// Track buttons we've already bound so the second click cancels rather than
// re-enqueueing (bindCardButton attaches an instance-level listener).
const _disc_dl_bound = new WeakSet();
document.addEventListener('click', (e) => {
  const btn = e.target.closest && e.target.closest('.disc-dl-btn');
  if (!btn) return;
  if (_disc_dl_bound.has(btn)) return;  // bindCardButton's listener handles future clicks
  _disc_dl_bound.add(btn);
  e.preventDefault();
  if (!(window._downloadConfig && window._downloadConfig.available && window._downloadConfig.ffmpeg)) {
    if (typeof showToast === 'function') showToast('Download tools not installed — see the Download panel');
    return;
  }
  window._Download.bindCardButton(btn, btn.dataset.query, {});
  // The first click is what triggered this listener; replay it on the bound btn.
  btn.click();
});

// Run a single download over SSE, updating a button + optional inline progress bar.
async function runDownload(query, { btn, statusEl, progressEl, barEl } = {}) {
  if (!query) return;
  if (!(_downloadConfig.available && _downloadConfig.ffmpeg)) {
    showToast('Download tools not installed — see the Download panel'); return;
  }
  if (btn) { btn.disabled = true; btn.textContent = 'Downloading…'; }
  if (progressEl) { progressEl.style.display = ''; }
  if (barEl) barEl.style.width = '0%';
  if (statusEl) statusEl.textContent = 'starting…';
  try {
    const r = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, dest_dir: _dlDestDir || undefined }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r.status}`);
    }
    await _consumeSSE(r, d => {
      if (d.done) {
        if (barEl) barEl.style.width = '100%';
        if (d.status === 'error') {
          if (statusEl) { statusEl.textContent = '✗ failed'; statusEl.style.color = 'var(--red, #e05252)'; }
          showToast('Download failed: ' + (d.error || ''));
        } else {
          if (statusEl) { statusEl.textContent = '✓ saved'; statusEl.style.color = 'var(--green)'; }
          showToast('Downloaded to ' + (d.path || _dlDestDir || _downloadConfig.default_dir));
        }
        return;
      }
      if (typeof d.percent === 'number') {
        if (barEl) barEl.style.width = d.percent + '%';
        if (statusEl) statusEl.textContent = d.percent + '%';
      } else if (d.status && statusEl) statusEl.textContent = d.status;
    });
  } catch (err) {
    if (statusEl) { statusEl.textContent = '✗ ' + err.message; statusEl.style.color = 'var(--red, #e05252)'; }
    showToast('Download failed: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⬇ Album'; }
  }
}

async function downloadManual() {
  const query = (document.getElementById('dl-query').value || '').trim();
  if (!query) { showToast('Enter a URL or search term'); return; }
  const btn = document.getElementById('dl-go-btn');
  const statusEl = document.getElementById('dl-status');
  const progress = document.getElementById('dl-progress');
  const fill = document.getElementById('dl-progress-fill');
  progress.style.display = '';
  fill.style.width = '5%';

  // Reuse runDownload, but also drive the dedicated progress bar.
  if (!(_downloadConfig.available && _downloadConfig.ffmpeg)) {
    showToast('Download tools not installed'); return;
  }
  btn.disabled = true; btn.textContent = 'Downloading…';
  statusEl.textContent = 'starting…';
  try {
    const r = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, dest_dir: _dlDestDir || undefined }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r.status}`);
    }
    await _consumeSSE(r, d => {
      if (d.done) {
        fill.style.width = '100%';
        if (d.status === 'error') { statusEl.textContent = '✗ ' + (d.error || 'failed'); showToast('Download failed'); }
        else { statusEl.textContent = '✓ Saved to ' + (d.path || _dlDestDir || _downloadConfig.default_dir); showToast('Download complete'); }
        return;
      }
      if (typeof d.percent === 'number') { fill.style.width = Math.max(5, d.percent) + '%'; statusEl.textContent = d.percent + '%'; }
      else if (d.status) statusEl.textContent = d.status;
    });
  } catch (err) {
    statusEl.textContent = '✗ ' + err.message;
    showToast('Download failed: ' + err.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Download';
    setTimeout(() => { progress.style.display = 'none'; }, 1000);
  }
}

// ── Comment Enrichment ────────────────────────────────────────────────────────

async function enrichComments() {
  const btn      = document.getElementById('ce-run-btn');
  const status   = document.getElementById('ce-status');
  const result   = document.getElementById('ce-result');
  const resultText = document.getElementById('ce-result-text');
  const progBar  = document.getElementById('ce-progress');
  const progFill = document.getElementById('ce-progress-fill');

  const overwrite = document.getElementById('ce-overwrite').checked;
  const dryRun    = document.getElementById('ce-dry-run').checked;
  const ids = filteredTracks().map(i => parsedTracks[i].id);

  if (!ids.length) { showToast('No tracks to enrich', true); return; }

  let enrichedSoFar = 0;
  const abortCtrl = new AbortController();
  const _enrichCancelConfirm = () => {
    if (enrichedSoFar === 0) return true;
    return _confirmDialog(
      `Stop comment enrichment?\n\n${enrichedSoFar} track${enrichedSoFar === 1 ? '' : 's'} already enriched — those changes are saved.`,
      { confirmLabel: 'Stop enriching' }
    );
  };
  _setBtnCancellable(btn, `Enriching… 0 / ${ids.length}`, abortCtrl, _enrichCancelConfirm);
  status.textContent = `0 / ${ids.length}`;
  result.style.display = 'none';
  progBar.style.display = '';
  progFill.style.width = '0%';

  try {
    const r = await fetch('/api/enrich-comments/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: ids, overwrite, dry_run: dryRun }),
      signal: abortCtrl.signal,
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    let finalResult = null;
    await _consumeSSE(r, ev => {
      if (ev.done) {
        finalResult = ev;
        enrichedSoFar = ev.enriched ?? enrichedSoFar;
      } else if (ev.processed != null) {
        const pct = Math.round(ev.processed / ev.total * 100);
        progFill.style.width = pct + '%';
        if (ev.enriched != null) enrichedSoFar = ev.enriched;
        status.textContent = `${ev.processed} / ${ev.total}`;
        _setBtnCancellable(btn, `Enriching… ${ev.processed} / ${ev.total}`, abortCtrl, _enrichCancelConfirm);
      }
    }, abortCtrl.signal);
    progFill.style.width = '100%';
    if (finalResult) {
      const label = dryRun ? ' (dry run)' : '';
      resultText.textContent = `Enriched: ${finalResult.enriched} · Skipped: ${finalResult.skipped} · Errors: ${finalResult.errors}${label}`;
      result.style.display = '';
      status.textContent = '';
      showToast(dryRun ? `Preview: ${finalResult.enriched} tracks would be enriched` : `Enriched ${finalResult.enriched} track comments`);
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      const saved = enrichedSoFar > 0 && !dryRun;
      status.textContent = saved ? `Stopped — ${enrichedSoFar} enriched` : 'Cancelled';
      if (saved) showToast(`Enrichment stopped — ${enrichedSoFar} tracks saved`);
      else showToast('Comment enrichment cancelled');
    } else {
      status.textContent = `Error: ${err.message}`;
      showToast(`Enrichment failed: ${err.message}`, true);
    }
  } finally {
    _setBtnLoading(btn, false);
    setTimeout(() => { progBar.style.display = 'none'; progFill.style.width = '0%'; if (!status.textContent.includes('enriched')) status.textContent = ''; }, 2000);
  }
}

async function previewComment() {
  const sel   = document.getElementById('ce-preview-track');
  const pDiv  = document.getElementById('ce-preview-result');
  const pCur  = document.getElementById('ce-preview-current');
  const pAfter = document.getElementById('ce-preview-after');
  const trackId = parseInt(sel.value, 10);
  if (!trackId) { showToast('Select a track to preview', true); return; }

  try {
    const r = await fetch('/api/enrich-comments/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_id: trackId }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const d = await r.json();
    pCur.textContent  = d.current_comment || '(empty)';
    pAfter.textContent = d.preview || '(no enrichment available)';
    pDiv.style.display = '';
  } catch (err) {
    showToast(`Preview failed: ${err.message}`, true);
  }
}

async function colorTracksByBpm() {
  const btn = document.getElementById('color-by-bpm-btn');
  const trackIds = activeTracks().map(t => parseInt(t.id));
  const total = trackIds.length;

  const abortCtrl = new AbortController();
  _setBtnCancellable(btn, `Coloring… 0 / ${total}`, abortCtrl);

  try {
    const skipColored = document.getElementById('skip-colored-cb')?.checked ?? false;
    const r = await fetch('/api/color-tracks-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: trackIds, dry_run: false, skip_colored: skipColored }),
      signal: abortCtrl.signal,
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.done) {
          finalData = ev;
        } else {
          _setBtnCancellable(btn, `Coloring… ${ev.colored + ev.skipped} / ${ev.total}`, abortCtrl);
        }
      }
    }

    if (finalData) {
      const backupNote = finalData.backup_path ? ' — backup saved to ~/.autocue/backups/' : '';
      showToast(`Colored ${finalData.colored} track(s) by BPM${backupNote}`);
      document.getElementById('bpm-legend').classList.add('visible');
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      showToast('Color by BPM cancelled');
    } else {
      showToast(`Color by BPM failed: ${err.message}`);
    }
  } finally {
    _setBtnLoading(btn, false);
  }
}

async function applyToRekordbox() {
  const barsInterval = parseInt(document.getElementById('bars-interval').value) || 16;
  const startBar = parseInt(document.getElementById('start-bar').value) || 1;
  const maxCues = parseInt(document.getElementById('max-cues').value) || 8;
  const memoryCueMode = document.getElementById('memory-cue-mode').value;
  const addFillCues = document.getElementById('add-fill-cues').checked;
  const tracks = activeTracks();
  const trackCount = tracks.length;

  const btn = document.getElementById('download-btn');
  // #download-btn lives in the Pages-mode bar, which is display:none in local
  // mode — mirror every state change onto the action-bar button the user
  // actually clicked, or the whole apply runs with zero visible feedback.
  const abApply = document.getElementById('action-bar-apply');
  const applyBtns = [btn, abApply].filter(Boolean);
  const setApplyText = (t) => applyBtns.forEach(b => { b.textContent = t; });
  setApplyText(`Applying… 0 / ${trackCount}`);
  applyBtns.forEach(b => { b.disabled = true; });

  const body = JSON.stringify({
    track_ids: tracks.map(t => parseInt(t.id)),
    mode: analysisMode === 'phrase' ? 'auto' : 'bar',
    bars_interval: barsInterval,
    start_bar: startBar,
    max_cues: maxCues,
    memory_cue_mode: memoryCueMode,
    add_fill_cues: addFillCues,
    overwrite: !document.getElementById('skip-existing-cues').checked,
    phrase_only: phraseOnlyFilter,
    dry_run: false,
  });

  try {
    const r = await fetch('/api/generate-apply-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.done) {
          finalData = ev;
        } else {
          setApplyText(`Applying… ${(ev.applied||0) + (ev.skipped||0) + (ev.errors||0)} / ${ev.total}`);
        }
      }
    }

    if (finalData) {
      pendingCues = {};
      setStep(4);
      if (finalData.backup_path) {
        lastAppliedBackupFilename = finalData.backup_path.split('/').pop();
        document.getElementById('undo-btn').style.display = '';
      }
      const backupNote = finalData.backup_path ? ' · backup saved' : '';
      const skippedNote = finalData.skipped > 0 ? `, ${finalData.skipped} skipped (already cued)` : '';
      showToast(`Applied hot cues to ${finalData.applied} track${finalData.applied !== 1 ? 's' : ''}${skippedNote}${backupNote}`, 'success');
      // Success flash: turn both apply buttons green for 2.5s
      applyBtns.forEach(b => {
        b.textContent = `✓ ${finalData.applied} tracks`;
        b.style.background = 'var(--green)';
        b.style.color = '#000';
        b.style.borderColor = 'var(--green)';
      });
      setTimeout(() => {
        applyBtns.forEach(b => {
          b.textContent = 'Apply to Rekordbox';
          b.style.background = '';
          b.style.color = '';
          b.style.borderColor = '';
        });
      }, 2500);
      // Refresh cards so existing-cue counts/chips reflect the write —
      // without this the tracks just written still render as un-cued.
      if (localMode) await loadTracksFromServer(activePlaylistId ?? null).catch(() => {});
    }
  } catch (err) {
    showToast(`Error applying cues: ${err.message}`, true);
  } finally {
    applyBtns.forEach(b => {
      b.disabled = false;
      if (!b.textContent.startsWith('✓')) b.textContent = 'Apply to Rekordbox';
    });
  }
}

// ── Tab navigation ────────────────────────────────────────────────────────────
const TAB_CONTENTS = {
  cues: 'cues-tab-content',
  library: 'library-tab-content',
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
  // Hide the Cues-specific sticky bottom bar on every non-Cues tab — it
  // shows "Apply to Rekordbox" + "Color tracks by BPM" + "Delete all cues"
  // which only make sense on the Cues tab. UX audit Issue 7 (escalated to
  // High by the grill: highest-reach finding, every tab × every visit).
  const dlBar = document.getElementById('download-bar');
  if (dlBar) {
    if (name === 'cues') dlBar.classList.remove('hidden-by-tab');
    else dlBar.classList.add('hidden-by-tab');
  }
  // Tag <body> so CSS can target tab-specific layout adjustments.
  document.body.setAttribute('data-active-tab', name);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
document.getElementById('tab-cues').addEventListener('click', () => switchTab('cues'));
document.getElementById('tab-library').addEventListener('click', () => switchTab('library'));
document.getElementById('tab-discover').addEventListener('click', () => switchTab('discover'));

// ── App status row ─────────────────────────────────────────────────────────
let _lastScanAt = null;
function _formatScanAge(at) {
  if (!at) return 'No scans yet';
  const ms = Date.now() - at;
  if (ms < 60_000) return 'Last scan just now';
  const m = Math.round(ms / 60_000);
  if (m < 60) return `Last scan ${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `Last scan ${h}h ago`;
  return `Last scan ${Math.round(h / 24)}d ago`;
}
function updateAppStatus({ connected, trackCount, rekordboxRunning, didScan } = {}) {
  const bar = document.getElementById('app-status');
  if (!bar) return;
  bar.classList.toggle('visible', !!connected);
  if (!connected) return;
  if (typeof trackCount === 'number') {
    const countItem = document.getElementById('status-count');
    const changed = countItem.dataset.lastCount !== String(trackCount);
    countItem.dataset.lastCount = String(trackCount);
    countItem.innerHTML =
      `<span class="status-text"><strong>${trackCount.toLocaleString()}</strong> tracks</span>`;
    if (changed) {
      // Tick the count when it actually changes — silent swaps read as static chrome
      countItem.classList.remove('count-pop');
      void countItem.offsetWidth;
      countItem.classList.add('count-pop');
    }
  }
  if (didScan) _lastScanAt = Date.now();
  document.getElementById('status-scan').innerHTML =
    `<span class="status-text">${_formatScanAge(_lastScanAt)}</span>`;
  const rb = document.getElementById('status-rb');
  if (rekordboxRunning === true) {
    rb.innerHTML = '<span class="status-dot status-warn"></span><span class="status-text">Rekordbox open</span>';
  } else if (rekordboxRunning === false) {
    rb.innerHTML = '<span class="status-dot status-ok"></span><span class="status-text">Rekordbox closed ✓</span>';
  } else {
    rb.innerHTML = '<span class="status-dot"></span><span class="status-text">Rekordbox ?</span>';
  }
}
// Refresh scan-age label every minute so the relative time stays accurate.
setInterval(() => {
  if (document.getElementById('app-status')?.classList.contains('visible')) {
    document.getElementById('status-scan').innerHTML =
      `<span class="status-text">${_formatScanAge(_lastScanAt)}</span>`;
  }
}, 60_000);

detectLocalMode().then(async connected => {
  localMode = connected;
  if (localMode) {
    // Ease the tab chrome in — it used to pop over the already-painted XML UI
    const _tn = document.getElementById('tab-nav');
    _tn.style.display = '';
    _tn.classList.add('fade-in-up');
    _tn.addEventListener('animationend', () => _tn.classList.remove('fade-in-up'), { once: true });
    document.getElementById('steps').style.display = 'none';
    document.querySelector('.mode-callout')?.setAttribute('style', 'display:none');
    document.getElementById('drop-zone').style.display = 'none';
    document.getElementById('local-mode-banner').style.display = 'inline-flex';
    updateAppStatus({ connected: true });
    document.getElementById('download-btn').textContent = 'Apply to Rekordbox';
    document.getElementById('delete-cues-btn').style.display = '';
    document.getElementById('skip-colored-label').style.display = 'flex';
    document.getElementById('color-by-bpm-btn').style.display = '';
    document.getElementById('preview-cues-btn').style.display = '';
    document.getElementById('apply-sep').style.display = '';
    document.getElementById('how-to').style.display = 'none';
    document.getElementById('local-how-to').style.display = '';
    document.getElementById('health-section').style.display = '';
    document.getElementById('health-scan-btn').addEventListener('click', scanLibraryHealth);
    document.getElementById('duplicates-section').style.display = '';
    document.getElementById('duplicates-scan-btn').addEventListener('click', scanDuplicates);
    document.getElementById('cue-tools-section').style.display = '';
    _initCueTools();
    document.getElementById('discogs-section').style.display = '';
    document.getElementById('discogs-run-btn').addEventListener('click', discogsTagTracks);
    document.getElementById('discogs-save-btn').addEventListener('click', discogsSaveToken);
    discogsLoadSavedToken();
    document.getElementById('comment-enrich-section').style.display = '';
    document.getElementById('ce-run-btn').addEventListener('click', enrichComments);
    document.getElementById('ce-preview-btn').addEventListener('click', previewComment);
    document.getElementById('playlist-suggest-section').style.display = '';
    document.getElementById('ps-suggest-btn').addEventListener('click', () => suggestPlaylist(false));
    document.getElementById('ps-use-selected-btn').addEventListener('click', _useSelectedForPlaylist);
    document.getElementById('ps-more-btn').addEventListener('click', () => suggestPlaylist(true));
    document.getElementById('ps-reset-btn').addEventListener('click', () => {
      _psExcludedIds = [];
      _psSeedTrackIds = [];
      _psTracks = [];
      document.getElementById('ps-tracklist').innerHTML = '';
      document.getElementById('ps-result').style.display = 'none';
      document.getElementById('ps-more-btn').style.display = 'none';
      document.getElementById('ps-reset-btn').style.display = 'none';
      document.getElementById('ps-summary').textContent = '';
      document.getElementById('ps-status').textContent = '';
    });
    document.getElementById('ps-save-playlist-btn').addEventListener('click', psSavePlaylist);
    document.getElementById('setbuilder-section').style.display = '';
    document.getElementById('sb-build-btn').addEventListener('click', buildSet);
    document.getElementById('sb-use-selected-btn').addEventListener('click', _useSelectedForSetBuilder);
    document.getElementById('sb-seed-clear').addEventListener('click', () => {
      _sbSeedTrackId = null;
      _sbAnchorTrackIds = [];
      document.getElementById('sb-seed-row').style.display = 'none';
    });
    document.getElementById('sb-save-playlist-btn').addEventListener('click', sbSavePlaylist);
    initDiscover();
    initDiscoverV2();  // T-024 — wires up the new Discover tab surface

    // Copy track list buttons
    function makeCopyHandler(listId, btnId, tracksRef) {
      const btn = document.getElementById(btnId);
      if (!btn) return;
      btn.addEventListener('click', () => {
        const text = tracksRef().map((t, i) => `${i + 1}. ${t.title || '(untitled)'} — ${t.artist || ''} (${t.bpm ? t.bpm.toFixed(1) + ' BPM' : ''}, ${t.key || '—'})`).join('\n');
        navigator.clipboard.writeText(text).then(() => {
          btn.textContent = '✓ Copied';
          btn.classList.add('copied');
          setTimeout(() => { btn.textContent = 'Copy list'; btn.classList.remove('copied'); }, 2000);
        }).catch(() => showToast('Copy failed'));
      });
    }
    makeCopyHandler('ps-tracklist', 'ps-copy-btn', () => _psTracks);
    makeCopyHandler('sb-tracklist', 'sb-copy-btn', () => _sbTracks);

    // F2: Load playlists into dropdown
    document.getElementById('playlist-filter-bar').style.display = 'flex';
    document.getElementById('filter-bar').style.display = 'flex';
    try {
      const playlists = await fetch('/api/playlists').then(r => r.json());
      const sel = document.getElementById('playlist-select');
      for (const pl of playlists) {
        const opt = document.createElement('option');
        opt.value = pl.id;
        opt.textContent = `${pl.name} (${pl.track_count})`;
        sel.appendChild(opt);
      }
    } catch {}

    try {
      const tags = await fetch('/api/tags').then(r => r.json());
      const chipsEl = document.getElementById('tag-filter-chips');
      for (const tag of tags) {
        const chip = document.createElement('button');
        chip.className = 'tf-chip';
        chip.dataset.tag = tag.name;
        chip.textContent = tag.name;
        const c = (typeof AUTO_TAG_COLORS !== 'undefined') && AUTO_TAG_COLORS[tag.name];
        chip.style.cssText = c
          ? `background:${c}22;border:1px solid ${c}55;color:${c};border-radius:10px;padding:2px 8px;font-size:11px;cursor:pointer;`
          : 'background:var(--surface2);border:1px solid var(--border);color:var(--text);border-radius:10px;padding:2px 8px;font-size:11px;cursor:pointer;';
        chip.addEventListener('click', e => { e.stopPropagation(); window._toggleTagFilter(tag.name); });
        chipsEl.appendChild(chip);
      }
    } catch {}

    loadTracksFromServer();
  }
});

// F2: Playlist filter change
document.getElementById('playlist-select').addEventListener('change', () => {
  const val = document.getElementById('playlist-select').value;
  activePlaylistId = val ? parseInt(val) : null;
  pendingCues = {};
  healthData = {};    // stale health chips don't apply across playlist boundaries
  loadTracksFromServer(activePlaylistId);
});

// F3: Restore backup UI — checkbox list
function _populateChecklist(backups) {
  const list = document.getElementById('backup-checklist');
  list.innerHTML = '';
  const allCb = document.getElementById('backup-select-all');
  allCb.checked = false;
  allCb.indeterminate = false;
  _updateSelectionCount();
  for (const b of backups) {
    const row = document.createElement('div');
    row.className = 'backup-row';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = b.filename;
    cb.addEventListener('change', () => {
      _updateSelectionCount();
      _resetRestoreBtn();
      _resetDeleteBackupBtn();
    });
    const name = document.createElement('span');
    name.className = 'backup-name';
    name.textContent = b.created_at;
    const size = document.createElement('span');
    size.className = 'backup-size';
    size.textContent = b.size_mb + ' MB';
    row.appendChild(cb);
    row.appendChild(name);
    row.appendChild(size);
    row.addEventListener('click', e => {
      if (e.target === cb) return;
      cb.checked = !cb.checked;
      cb.dispatchEvent(new Event('change'));
    });
    list.appendChild(row);
  }
}

function _updateSelectionCount() {
  const checkboxes = document.querySelectorAll('#backup-checklist input[type=checkbox]');
  const checked = [...checkboxes].filter(c => c.checked);
  const count = checked.length;
  const total = checkboxes.length;
  const allCb = document.getElementById('backup-select-all');
  allCb.checked = count === total && total > 0;
  allCb.indeterminate = count > 0 && count < total;
  const countSpan = document.getElementById('backup-select-count');
  countSpan.textContent = count > 0 ? `${count} selected` : '';
}

function _checkedBackups() {
  return [...document.querySelectorAll('#backup-checklist input[type=checkbox]:checked')].map(c => c.value);
}

document.getElementById('restore-btn').addEventListener('click', async () => {
  const bar = document.getElementById('restore-bar');
  const rbtn = document.getElementById('restore-btn');
  if (bar.style.display !== 'none') { bar.style.display = 'none'; return; }
  // Spinner while /api/backups loads — the click used to give zero feedback
  _setBtnLoading(rbtn, true, 'Loading backups…');
  try {
    const backups = await fetch('/api/backups').then(r => r.json());
    if (backups.length === 0) { showToast('No backups found'); return; }
    _populateChecklist(backups);
    _resetRestoreBtn();
    _resetDeleteBackupBtn();
  } catch (e) { showToast('Could not load backups', true); return; }
  finally { _setBtnLoading(rbtn, false); }
  bar.style.display = 'flex';
  bar.classList.add('fade-in-up');
  bar.addEventListener('animationend', () => bar.classList.remove('fade-in-up'), { once: true });
});

document.getElementById('backup-select-all').addEventListener('change', function() {
  document.querySelectorAll('#backup-checklist input[type=checkbox]').forEach(c => { c.checked = this.checked; });
  _updateSelectionCount();
  _resetRestoreBtn();
  _resetDeleteBackupBtn();
});

function _resetRestoreBtn() {
  const btn = document.getElementById('restore-confirm-btn');
  btn.textContent = 'Restore';
  btn.style.outline = '';
  delete btn.dataset.armed;
}

function _resetDeleteBackupBtn() {
  const btn = document.getElementById('delete-backup-btn');
  btn.textContent = 'Delete selected';
  btn.style.outline = '';
  delete btn.dataset.armed;
}

document.getElementById('restore-cancel-btn').addEventListener('click', () => {
  _resetRestoreBtn();
  _resetDeleteBackupBtn();
  document.getElementById('restore-bar').style.display = 'none';
});

document.getElementById('delete-backup-btn').addEventListener('click', async () => {
  const selected = _checkedBackups();
  if (selected.length === 0) { showToast('Select at least one backup to delete'); return; }
  const btn = document.getElementById('delete-backup-btn');
  if (btn.dataset.armed !== 'true') {
    btn.dataset.armed = 'true';
    btn.textContent = `Delete ${selected.length} backup${selected.length > 1 ? 's' : ''} ⚠`;
    btn.style.outline = '2px solid #ff4444';
    return;
  }
  _resetDeleteBackupBtn();
  btn.disabled = true; btn.textContent = 'Deleting…';
  let deletedCount = 0;
  try {
    for (const filename of selected) {
      const r = await fetch(`/api/backups/${encodeURIComponent(filename)}`, { method: 'DELETE' });
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
      deletedCount++;
    }
    showToast(`Deleted ${deletedCount} backup${deletedCount > 1 ? 's' : ''}`);
    const backups = await fetch('/api/backups').then(r => r.json());
    if (backups.length === 0) {
      document.getElementById('restore-bar').style.display = 'none';
    } else {
      _populateChecklist(backups);
    }
  } catch (e) { showToast(`Delete failed: ${e.message}`); }
  finally { btn.disabled = false; btn.textContent = 'Delete selected'; }
});

document.getElementById('restore-confirm-btn').addEventListener('click', async () => {
  const selected = _checkedBackups();
  if (selected.length !== 1) { showToast('Select exactly one backup to restore'); return; }
  const filename = selected[0];
  const btn = document.getElementById('restore-confirm-btn');
  if (btn.dataset.armed !== 'true') {
    btn.dataset.armed = 'true';
    btn.textContent = 'Yes, replace database ⚠';
    btn.style.outline = '2px solid #ff4444';
    return;
  }
  _resetRestoreBtn();
  btn.disabled = true; btn.textContent = 'Restoring…';
  try {
    const r = await fetch('/api/restore', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename }),
    });
    const resp = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(resp.detail || r.statusText);
    showToast(resp.message + ' — reloading tracks…');
    document.getElementById('restore-bar').style.display = 'none';
    pendingCues = {};
    await loadTracksFromServer(activePlaylistId);
  } catch (e) { showToast(`Restore failed: ${e.message}`); }
  finally { btn.disabled = false; btn.textContent = 'Restore'; }
});

// TASK-036 — Search input debounced through requestIdleCallback so the
// browser defers the filter recompute to idle time instead of competing
// with input event paint. Falls back to setTimeout where rIC isn't
// supported (jsdom in tests; older Safari).
let _searchTimer = null;
let _searchRic = null;
const _scheduleSearchRecompute = (fn) => {
  if (typeof window.requestIdleCallback === 'function') {
    if (_searchRic !== null) window.cancelIdleCallback(_searchRic);
    _searchRic = window.requestIdleCallback(() => { _searchRic = null; fn(); }, { timeout: 80 });
    return;
  }
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(fn, 80);
};
document.getElementById('search-input').addEventListener('input', e => {
  const value = e.target.value.trim();
  _scheduleSearchRecompute(() => {
    searchQuery = value;
    updateActiveFiltersChip();
    AppState.signal('filters');
  });
});

// F6: Phrase-only filter
document.getElementById('phrase-only-cb').addEventListener('change', e => {
  phraseOnlyFilter = e.target.checked;
  updateActiveFiltersChip();
  AppState.signal('filters');
});

// Beat-grid-only filter (tracks with BPM > 0 — i.e. analyzed in Rekordbox)
document.getElementById('audio-only-cb').addEventListener('change', e => {
  _audioOnlyFilter = e.target.checked;
  updateActiveFiltersChip();
  AppState.signal('filters');
  if (_audioOnlyFilter) _probeAudioForVisibleTracks();
});

document.getElementById('beats-only-cb').addEventListener('change', e => {
  beatsOnlyFilter = e.target.checked;
  updateActiveFiltersChip();
  AppState.signal('filters');
});

// Genre filter is handled by chip click listeners created in populateGenreChips()

// Rating / plays / last-played / tag filters
document.getElementById('rating-filter').addEventListener('change', e => {
  ratingFilter = parseInt(e.target.value) || 0;
  updateActiveFiltersChip();
  AppState.signal('filters');
});
document.getElementById('plays-filter').addEventListener('change', e => {
  playsFilter = e.target.value;
  updateActiveFiltersChip();
  AppState.signal('filters');
});
document.getElementById('lastplayed-filter').addEventListener('change', e => {
  lastPlayedFilter = e.target.value;
  updateActiveFiltersChip();
  AppState.signal('filters');
});
// Tag filter popup
(function() {
  const popup = document.getElementById('tag-filter-popup');
  const btn   = document.getElementById('tag-filter-btn');

  function toggleTag(tag) {
    if (myTagFilters.has(tag)) myTagFilters.delete(tag);
    else myTagFilters.add(tag);
    updateTagFilterUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  }

  function updateTagFilterUI() {
    document.querySelectorAll('#tag-filter-chips .tf-chip').forEach(b => {
      b.classList.toggle('selected', myTagFilters.has(b.dataset.tag));
    });
    if (myTagFilters.size === 0) {
      btn.textContent = 'Tags: Any ▾';
      btn.style.borderColor = '';
      btn.style.color = '';
    } else {
      btn.textContent = `Tags: ${myTagFilters.size} ▾`;
      btn.style.borderColor = 'var(--green)';
      btn.style.color = 'var(--green)';
    }
  }

  function openPopup() {
    popup.style.display = 'flex';
    const rect = btn.getBoundingClientRect();
    const popupH = 300;
    // Flip above if not enough space below
    if (rect.bottom + popupH + 8 > window.innerHeight) {
      popup.style.top  = (rect.top - popupH - 4) + 'px';
    } else {
      popup.style.top  = (rect.bottom + 4) + 'px';
    }
    popup.style.left = rect.left + 'px';
    requestAnimationFrame(() => {
      const pr = popup.getBoundingClientRect();
      if (pr.right > window.innerWidth - 8)
        popup.style.left = (window.innerWidth - pr.width - 8) + 'px';
      if (pr.left < 8) popup.style.left = '8px';
    });
  }

  function closePopup() { popup.style.display = 'none'; }

  // Called from popup chips — keep popup open for multi-select
  // Called from track card pills — close popup immediately
  window._toggleTagFilter = function(tag, fromCard) {
    toggleTag(tag);
    if (fromCard) closePopup();
  };
  window._updateTagFilterUI = updateTagFilterUI;

  document.getElementById('tf-clear-btn').addEventListener('click', () => {
    myTagFilters.clear();
    updateTagFilterUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  });

  document.getElementById('tag-search').addEventListener('input', function() {
    const q = this.value.toLowerCase();
    document.querySelectorAll('#tag-filter-chips .tf-chip').forEach(chip => {
      chip.style.display = chip.dataset.tag.toLowerCase().includes(q) ? '' : 'none';
    });
  });

  btn.addEventListener('click', e => {
    e.stopPropagation();
    if (popup.style.display === 'flex') closePopup(); else openPopup();
  });

  document.addEventListener('click', e => {
    if (popup.style.display === 'flex' && !popup.contains(e.target) && e.target !== btn)
      closePopup();
  });
})();

// Genre filter popup
(function() {
  const popup = document.getElementById('genre-filter-popup');
  const btn   = document.getElementById('genre-filter-btn');

  function toggleGenre(g) {
    if (genreFilters.has(g)) genreFilters.delete(g);
    else genreFilters.add(g);
    updateGenreFilterUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  }

  function updateGenreFilterUI() {
    document.querySelectorAll('#genre-filter-chips .genre-chip').forEach(function(b) {
      b.classList.toggle('active', genreFilters.has(b.dataset.genre));
    });
    if (!btn) return;
    if (genreFilters.size === 0) {
      btn.textContent = 'Genre: Any ▾';
      btn.style.borderColor = '';
      btn.style.color = '';
    } else {
      btn.textContent = 'Genre: ' + genreFilters.size + ' ▾';
      btn.style.borderColor = 'var(--green)';
      btn.style.color = 'var(--green)';
    }
  }

  function openPopup() {
    popup.style.display = 'flex';
    const rect = btn.getBoundingClientRect();
    if (rect.bottom + 308 > window.innerHeight) {
      popup.style.top = (rect.top - 304) + 'px';
    } else {
      popup.style.top = (rect.bottom + 4) + 'px';
    }
    popup.style.left = rect.left + 'px';
    requestAnimationFrame(function() {
      const pr = popup.getBoundingClientRect();
      if (pr.right > window.innerWidth - 8) popup.style.left = (window.innerWidth - pr.width - 8) + 'px';
      if (pr.left < 8) popup.style.left = '8px';
    });
  }

  function closePopup() { popup.style.display = 'none'; }

  window._updateGenreFilterUI = updateGenreFilterUI;

  document.getElementById('genre-filter-chips').addEventListener('click', function(e) {
    var chip = e.target.closest('.genre-chip');
    if (chip) toggleGenre(chip.dataset.genre);
  });

  document.getElementById('genre-clear-btn').addEventListener('click', function() {
    genreFilters.clear();
    updateGenreFilterUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  });

  document.getElementById('genre-search').addEventListener('input', function() {
    var q = this.value.toLowerCase();
    document.querySelectorAll('#genre-filter-chips .genre-chip').forEach(function(chip) {
      chip.style.display = chip.dataset.genre.toLowerCase().includes(q) ? '' : 'none';
    });
  });

  btn.addEventListener('click', function(e) {
    e.stopPropagation();
    if (popup.style.display === 'flex') closePopup(); else openPopup();
  });

  document.addEventListener('click', function(e) {
    if (popup.style.display === 'flex' && !popup.contains(e.target) && e.target !== btn)
      closePopup();
  });
})();

function updateActiveFiltersChip() {
  const active = ratingFilter > 0 || playsFilter !== 'all' || lastPlayedFilter !== 'all' || myTagFilters.size > 0 || selectedKeys.size > 0 || phraseOnlyFilter || beatsOnlyFilter || _audioOnlyFilter || searchQuery || genreFilters.size > 0;
  const chip = document.getElementById('active-filters-chip');
  if (chip) chip.classList.toggle('visible', !!active);
}

function clearAllFilters() {
  searchQuery = '';
  phraseOnlyFilter = false;
  beatsOnlyFilter = false;
  _audioOnlyFilter = false;
  const ac = document.getElementById('audio-only-cb');
  if (ac) ac.checked = false;
  ratingFilter = 0;
  playsFilter = 'all';
  lastPlayedFilter = 'all';
  myTagFilters.clear();
  selectedKeys.clear();
  const si = document.getElementById('search-input');
  if (si) si.value = '';
  const pc = document.getElementById('phrase-only-cb');
  if (pc) pc.checked = false;
  const bc = document.getElementById('beats-only-cb');
  if (bc) bc.checked = false;
  const rf = document.getElementById('rating-filter');
  if (rf) rf.value = '0';
  const pf = document.getElementById('plays-filter');
  if (pf) pf.value = 'all';
  const lpf = document.getElementById('lastplayed-filter');
  if (lpf) lpf.value = 'all';
  document.querySelectorAll('#tag-filter-chips .tf-chip').forEach(b => b.classList.remove('selected'));
  const tfb = document.getElementById('tag-filter-btn');
  if (tfb) { tfb.textContent = 'Tags: Any ▾'; tfb.style.borderColor = ''; tfb.style.color = ''; }
  // Update Camelot key buttons
  document.querySelectorAll('#camelot-grid button').forEach(b => b.classList.remove('selected'));
  const kfb = document.getElementById('key-filter-btn');
  if (kfb) { kfb.textContent = 'Key: Any ▾'; kfb.classList.remove('active'); }
  genreFilters.clear();
  if (window._updateGenreFilterUI) window._updateGenreFilterUI();
  updateActiveFiltersChip();
  AppState.signal('filters'); // coalesces all the above mutations into one render
}

// F7: Bulk selection — Select all / Deselect all
document.getElementById('select-all-btn').addEventListener('click', () => {
  for (const i of filteredTracks()) selectedTrackIds.add(parsedTracks[i].id);
  AppState.signal('filters');
  updateSelectionBar();
});
document.getElementById('deselect-all-btn').addEventListener('click', () => {
  selectedTrackIds.clear();
  AppState.signal('filters');
  updateSelectionBar();
});

// F8: Undo last apply
document.getElementById('undo-btn').addEventListener('click', async () => {
  if (!lastAppliedBackupFilename) return;
  if (!(await _confirmDialog(
    `Undo last apply? This restores "${lastAppliedBackupFilename}" and replaces your current Rekordbox database.`,
    { confirmLabel: 'Undo apply', danger: true }
  ))) return;
  const btn = document.getElementById('undo-btn');
  btn.disabled = true; btn.textContent = 'Undoing…';
  try {
    const r = await fetch('/api/restore', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: lastAppliedBackupFilename }),
    });
    const resp = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(resp.detail || r.statusText);
    lastAppliedBackupFilename = null;
    btn.style.display = 'none';
    pendingCues = {};
    showToast('Undo successful — tracks reloaded');
    await loadTracksFromServer(activePlaylistId);
  } catch (e) { showToast(`Undo failed: ${e.message}`); }
  finally { btn.disabled = false; btn.textContent = '↩ Undo last apply'; }
});

// ── Parsing ────────────────────────────────────────────────────────────────────
function parseRekordboxXml(xmlString) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(xmlString, 'text/xml');

  if (doc.querySelector('parsererror')) {
    return { error: "This file couldn't be parsed as XML. Make sure it's a valid rekordbox.xml export." };
  }
  if (!doc.querySelector('DJ_PLAYLISTS')) {
    return { error: "This doesn't look like a Rekordbox export. In Rekordbox go to File → Export Collection in rekordbox format." };
  }

  const tracks = [...doc.querySelectorAll('COLLECTION > TRACK')].map(el => {
    const tempoEl = el.querySelector('TEMPO');
    let tempo = null;
    if (tempoEl) {
      const beatsPerBar = parseInt((tempoEl.getAttribute('Metro') || '4/4').split('/')[0], 10) || 4;
      tempo = {
        bpm:        parseFloat(tempoEl.getAttribute('Bpm'))    || 0,
        inizio:     parseFloat(tempoEl.getAttribute('Inizio')) || 0,
        beatsPerBar,
      };
    }

    // Extract filename from Location attribute for audio file matching
    const rawLocation = el.getAttribute('Location') || '';
    let locationFilename = '';
    try { locationFilename = decodeURIComponent(rawLocation.split('/').pop()); }
    catch { locationFilename = rawLocation.split('/').pop(); }

    const existingCueDetails = [...el.querySelectorAll('POSITION_MARK')]
      .filter(pm => parseInt(pm.getAttribute('Num'), 10) >= 0)
      .map(pm => ({
        num:   parseInt(pm.getAttribute('Num'), 10),
        name:  pm.getAttribute('Name') || '',
        start: parseFloat(pm.getAttribute('Start')),
      }));
    const existingHotCues = existingCueDetails.length;

    return {
      el,
      id:               el.getAttribute('TrackID'),
      name:             el.getAttribute('Name')   || '(no title)',
      artist:           el.getAttribute('Artist') || '',
      totalTime:        parseFloat(el.getAttribute('TotalTime')) || 0,
      bpm:              parseFloat(el.getAttribute('AverageBpm')) || (tempo ? tempo.bpm : 0),
      tempo,
      existingHotCues,
      existingCueDetails,
      locationFilename,
    };
  });
  return { doc, tracks };
}

// ── Cue generation ─────────────────────────────────────────────────────────────
function generateCues(track, barsInterval, startBar, maxCues) {
  // local-mode tracks have track.bpm directly; XML tracks use track.tempo
  const bpm = track.tempo?.bpm || track.bpm || 0;
  if (!bpm) return [];
  const inizio = track.tempo?.inizio || 0;
  const beatsPerBar = track.tempo?.beatsPerBar || 4;
  const barDuration = (60.0 / bpm) * beatsPerBar;
  const cues = [];
  let slot = 0;
  for (let i = 0; i < maxCues + 64 && slot < maxCues; i++) {
    const posSec = inizio + (startBar - 1 + i * barsInterval) * barDuration;
    if (posSec < 0) continue;
    if (track.totalTime > 0 && posSec >= track.totalTime) break;
    const barNumber = startBar + i * barsInterval;
    cues.push({ slot, posSec: Math.round(posSec * 1000) / 1000, name: `Bar ${barNumber}`,
                confidence: 0.6, phraseMode: 'bar' });
    slot++;
  }
  return cues;
}

function getSettings() {
  return {
    barsInterval: Math.max(1, parseInt(document.getElementById('bars-interval').value, 10) || 16),
    startBar:     Math.max(1, parseInt(document.getElementById('start-bar').value, 10) || 1),
    maxCues:      Math.min(8, Math.max(1, parseInt(document.getElementById('max-cues').value, 10) || 8)),
  };
}

// ── Audio: file matching & registration ───────────────────────────────────────
function matchFileToTrack(file) {
  const fname = file.name;
  const fnameLower = fname.toLowerCase();
  return parsedTracks.find(t => {
    if (!t.locationFilename) return false;
    if (t.locationFilename === fname) return true;
    return t.locationFilename.toLowerCase() === fnameLower;
  }) || null;
}

function registerAudioFile(track, file) {
  // Revoke old object URL if re-registering
  if (audioState[track.id]?.objectUrl) {
    URL.revokeObjectURL(audioState[track.id].objectUrl);
    blobUrlsToRevoke.delete(audioState[track.id].objectUrl);
  }
  const objectUrl = URL.createObjectURL(file);
  blobUrlsToRevoke.add(objectUrl);
  audioState[track.id] = { file, objectUrl, artworkUrl: null };

  // Skip jsmediatags for WAV (no artwork standard) or if CDN failed
  const isWav = file.name.toLowerCase().endsWith('.wav');
  if (!isWav && window.jsmediatags) {
    jsmediatags.read(file, {
      onSuccess(tag) {
        const pic = tag.tags.picture;
        if (pic && audioState[track.id]) {
          const blob = new Blob([new Uint8Array(pic.data)], { type: pic.format });
          const url  = URL.createObjectURL(blob);
          blobUrlsToRevoke.add(url);
          audioState[track.id].artworkUrl = url;
          updateCardArtwork(track.id);
          if (nowPlayingId === track.id) updateMiniPlayerArtwork();
        }
      },
      onError() {},
    });
  }

  updateCardAudioState(track.id);
}

function handleAudioFiles(fileList) {
  let matched = 0;
  for (const file of fileList) {
    const track = matchFileToTrack(file);
    if (track) { registerAudioFile(track, file); matched++; }
  }
  const countEl = document.getElementById('audio-match-count');
  const total = Object.keys(audioState).length;
  countEl.textContent = total > 0 ? `${total} / ${parsedTracks.length} matched` : '';
  if (matched === 0 && fileList.length > 0) showToast('No files matched tracks in the XML');
}

// ── Audio: playback ────────────────────────────────────────────────────────────
const audioPlayer = document.getElementById('autocue-player');

// D1: RAF loop — updates timeline playhead + mini waveform at ~60fps while playing
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

// D4: Draw energy waveform + progress + playhead on the mini player canvas
function _drawMiniWaveform(trackId) {
  if (isScrubbing) return; // D5 fix: don't overwrite canvas position during user drag
  const canvas = document.getElementById('mini-waveform');
  if (!canvas) return;
  // D8 fix: HiDPI — set canvas physical pixel size once on first call
  const dpr = window.devicePixelRatio || 1;
  const cssW = 120, cssH = 22;
  if (canvas.width !== cssW * dpr) {
    canvas.width  = cssW * dpr;
    canvas.height = cssH * dpr;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = cssW, H = cssH;
  const track = parsedTracksById.get(String(trackId));
  const pct = (track && track.totalTime)
    ? Math.min(audioPlayer.currentTime / track.totalTime, 1) : 0;
  const isDark = document.documentElement.classList.contains('dark');

  ctx.clearRect(0, 0, W, H);

  const curve = _energyCache[trackId];
  if (curve && curve.length > 0) {
    const barW = W / curve.length;
    for (let i = 0; i < curve.length; i++) {
      const barH = Math.max(2, curve[i] * H);
      const x = i * barW;
      const y = (H - barH) / 2;
      const filled = (i / curve.length) <= pct;
      ctx.fillStyle = filled ? 'rgba(40,226,20,0.85)' : (isDark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)');
      ctx.fillRect(x + 0.5, y, Math.max(barW - 1.5, 0.5), barH);
    }
  } else {
    // Fallback: simple progress fill (use fillRect if roundRect unsupported on older browsers)
    const _fillRound = (x, y, w, h, r) => {
      if (ctx.roundRect) { ctx.roundRect(x, y, w, h, r); ctx.fill(); }
      else { ctx.fillRect(x, y, w, h); }
    };
    ctx.fillStyle = isDark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.10)';
    ctx.beginPath(); _fillRound(0, H / 2 - 2, W, 4, 2);
    ctx.fillStyle = 'rgba(40,226,20,0.85)';
    ctx.beginPath(); _fillRound(0, H / 2 - 2, W * pct, 4, 2);
  }

  // Playhead line
  const phX = Math.round(W * pct);
  ctx.strokeStyle = isDark ? 'rgba(255,255,255,0.85)' : 'rgba(0,0,0,0.7)';
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(phX, 0); ctx.lineTo(phX, H); ctx.stroke();
}

function playTrack(trackId, seekSec = 0) {
  const state = audioState[trackId];
  if (!state) return;

  const isNewSrc = nowPlayingId !== trackId;
  if (isNewSrc) {
    audioPlayer.src = state.objectUrl;
    // D2 fix: defer seek until metadata is loaded so currentTime sticks on new src
    audioPlayer.addEventListener('loadedmetadata', function onMeta() {
      audioPlayer.removeEventListener('loadedmetadata', onMeta);
      audioPlayer.currentTime = seekSec;
    }, { once: true });
  } else {
    audioPlayer.currentTime = seekSec;
  }
  audioPlayer.play().then(() => {
    _startPlayRaf(); // D1 fix: start RAF only after browser confirms playback started
  }).catch(() => {});

  nowPlayingId = trackId;
  updatePlaybackUI();
  showMiniPlayer(trackId);
}

function pausePlayback() {
  audioPlayer.pause();
  _stopPlayRaf(); // D1
  updatePlaybackUI();
}

// B3: aggregate failed-audio toasts. Multiple ensureLocalAudio failures within
// 1 second collapse to a single toast — eliminates the stacked-toast pileup
// visible in image #7 of the v5 plan context.
function _queueAudioFailToast(track) {
  _audioFailQueue.add(String(track?.id || ''));
  if (_audioFailFlushTimer) return;
  _audioFailFlushTimer = setTimeout(() => {
    const n = _audioFailQueue.size;
    if (n === 1) {
      const t = parsedTracksById.get([..._audioFailQueue][0]);
      const label = t ? `${t.artist || ''} — ${t.name || ''}`.trim().replace(/^—\s*/, '') : '';
      showToast(label ? `Audio file not found: ${label}` : 'Audio file not found on disk', true);
    } else {
      showToast(`Audio file not found for ${n} tracks`, true);
    }
    _audioFailQueue.clear();
    _audioFailFlushTimer = null;
  }, 1000);
}

// B1+B2: lazy verification for tracks visible under the audio-only filter.
// Sends ≤500 ids per request, sequential, with debounce. Results stored in
// _audioProbedAt; tracks the filter would otherwise hide are still surfaced
// when their parent dir is unverifiable (fail-open).
async function _probeAudioForVisibleTracks() {
  // Abort any in-flight chunks.
  if (_audioCheckAbort) _audioCheckAbort.abort();
  _audioCheckAbort = new AbortController();
  const signal = _audioCheckAbort.signal;

  await new Promise(r => setTimeout(r, 200)); // debounce
  if (signal.aborted) return;

  const ids = parsedTracks
    .filter(t => t.source === 'file' && !(t.id in _audioProbedAt))
    .map(t => parseInt(t.id));
  if (!ids.length) { AppState.signal('filters'); return; }

  const CHUNK = 500;
  for (let i = 0; i < ids.length; i += CHUNK) {
    const slice = ids.slice(i, i + CHUNK);
    try {
      const resp = await fetch('/api/tracks/check-audio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_ids: slice }),
        signal,
      }).then(r => r.json());
      Object.assign(_audioProbedAt, resp.results || {});
      for (const d of (resp.unverified_dirs || [])) _audioUnverifiedDirs.add(d);
      AppState.signal('filters'); // re-render incrementally
    } catch (err) {
      if (err.name === 'AbortError') return;
      showToast(`Audio check failed: ${err.message || 'network error'}`, true);
      return;
    }
  }
}

async function ensureLocalAudio(track) {
  if (audioState[track.id]) return; // already loaded
  // B3: don't even try for streaming / known-missing tracks — saves a fetch
  // and the inevitable toast.
  if (track.source && track.source !== 'file') { _queueAudioFailToast(track); return; }
  if (_audioProbedAt[track.id] === 'missing') { _queueAudioFailToast(track); return; }
  try {
    const resp = await fetch(`/api/tracks/${track.id}/audio`);
    if (!resp.ok) { _queueAudioFailToast(track); return; }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    blobUrlsToRevoke.add(url);
    audioState[track.id] = { file: null, objectUrl: url, artworkUrl: audioState[track.id]?.artworkUrl || null };
    updateCardAudioState(track.id);
  } catch (e) {
    showToast(`Could not load audio: ${e.message}`);
  }
}

function togglePlayTrack(trackId) {
  if (nowPlayingId === trackId && !audioPlayer.paused) {
    pausePlayback();
  } else {
    playTrack(trackId, nowPlayingId === trackId ? audioPlayer.currentTime : 0);
  }
}

function seekAndPlay(trackId, posSec) {
  if (!audioState[trackId]) {
    showToast('Drop the audio file for this track to enable playback');
    return;
  }
  playTrack(trackId, posSec);
}

// ── Audio: UI updates ──────────────────────────────────────────────────────────
function updatePlaybackUI() {
  const isPlaying = !audioPlayer.paused;

  // Update all card play buttons and art overlay
  document.querySelectorAll('.track-card').forEach(card => {
    const tid = card.dataset.trackId;
    const btn = card.querySelector('.play-btn');
    const overlay = card.querySelector('.art-play-overlay');
    const active = tid === nowPlayingId;
    card.classList.toggle('now-playing', active && isPlaying);
    if (btn) {
      btn.innerHTML = (active && isPlaying) ? SVG_PAUSE : SVG_PLAY;
      btn.setAttribute('aria-label', (active && isPlaying) ? 'Pause' : 'Play');
    }
    if (overlay) {
      overlay.innerHTML = (active && isPlaying) ? SVG_PAUSE : SVG_PLAY;
      overlay.classList.toggle('playing', active && isPlaying);
    }
  });

  // Update mini player play button
  document.getElementById('mini-play-icon')?.parentElement &&
    (document.getElementById('mini-play-btn').innerHTML =
      isPlaying ? SVG_PAUSE : SVG_PLAY);

  // Update timeline playhead
  updateTimeline();
}

function showMiniPlayer(trackId) {
  const track = parsedTracksById.get(String(trackId));
  if (!track) return;

  document.getElementById('mini-track-name').textContent   = track.name;
  document.getElementById('mini-track-artist').textContent = track.artist;
  document.getElementById('mini-duration').textContent     = fmtTime(track.totalTime);

  const scrubber = document.getElementById('mini-scrubber');
  scrubber.max = track.totalTime || 100;

  updateMiniPlayerArtwork();
  _drawMiniWaveform(trackId); // D4: initial draw (may have cached energy)

  document.getElementById('mini-player').classList.remove('hidden');
  document.getElementById('mini-sep').style.display = '';
}

function updateMiniPlayerArtwork() {
  const state = nowPlayingId ? audioState[nowPlayingId] : null;
  const img = document.getElementById('mini-artwork');
  img.src = state?.artworkUrl || '';
  img.style.visibility = state?.artworkUrl ? 'visible' : 'hidden';
}

function updateCardArtwork(trackId) {
  const card = document.querySelector(`.track-card[data-track-id="${trackId}"]`);
  if (!card) return;
  const img = card.querySelector('.artwork-img');
  const placeholder = card.querySelector('.artwork-placeholder');
  const url = audioState[trackId]?.artworkUrl;
  if (img && url) {
    img.src = url;
    img.style.display = 'block';
    if (placeholder) placeholder.style.display = 'none';
  }
}

function updateCardAudioState(trackId) {
  const card = document.querySelector(`.track-card[data-track-id="${trackId}"]`);
  if (!card) return;
  card.querySelector('.play-btn')?.classList.remove('hidden');
  card.querySelector('.load-audio-btn')?.classList.add('hidden');
  // Make cue badges playable — guard against double-listener if called more than once
  card.querySelectorAll('.cue-badge[data-pos-sec]').forEach(badge => {
    badge.classList.add('playable');
    if (!badge.dataset.listenerAdded) {
      badge.dataset.listenerAdded = '1';
      badge.addEventListener('click', () => seekAndPlay(trackId, parseFloat(badge.dataset.posSec)));
    }
  });
}

function updateTimeline() {
  if (!nowPlayingId) return;
  const card = document.querySelector(`.track-card[data-track-id="${nowPlayingId}"]`);
  if (!card) return;
  const track = parsedTracksById.get(String(nowPlayingId));
  if (!track?.totalTime) return;
  let ph = card.querySelector('.timeline-playhead');
  const tl = card.querySelector('.timeline');
  if (!tl) return;
  if (!ph) { ph = document.createElement('div'); ph.className = 'timeline-playhead'; tl.appendChild(ph); }
  const pct = (audioPlayer.currentTime / track.totalTime) * 100;
  ph.style.left = `${pct}%`;
}

// ── Pyodide / Phrase analysis ──────────────────────────────────────────────────
const ANALYZE_PYTHON = `
from pyrekordbox.anlz import AnlzFile

KIND_MAP = {
    1: {1:'Intro',2:'Up',3:'Down',5:'Chorus',6:'Outro'},
    2: {1:'Intro',2:'Verse',3:'Verse',4:'Verse',5:'Verse',6:'Verse',7:'Verse',8:'Bridge',9:'Chorus',10:'Outro'},
    3: {1:'Intro',2:'Verse',3:'Verse',4:'Verse',5:'Verse',6:'Verse',7:'Verse',8:'Bridge',9:'Chorus',10:'Outro'},
}

DJ_NAMES = {
    'Intro':'Intro', 'Verse':'Verse', 'Bridge':'Bridge',
    'Chorus':'Drop', 'Outro':'Outro', 'Up':'Build', 'Down':'Break', '?':'',
}

def analyze_anlz(ext_bytes, dat_bytes):
    ext = AnlzFile.parse(bytes(ext_bytes))
    dat = AnlzFile.parse(bytes(dat_bytes))
    pssi = next((t for t in ext.body.tags if t.fourcc == 'PSSI'), None)
    pqtz = next((t for t in dat.body.tags if t.fourcc == 'PQTZ'), None)
    if not pssi or not pqtz:
        return []
    beats = pqtz.content.entries
    phrases = pssi.content.entries
    mood = pssi.content.mood

    def beat_ms(n):
        idx = n - 1
        return beats[idx].time if 0 <= idx < len(beats) else None

    def lbl(kind):
        return KIND_MAP.get(mood, {}).get(kind, '?')

    seen, pass1, pass2 = set(), [], []
    for ph in phrases:
        ms = beat_ms(ph.beat)
        if ms is None: continue
        l = lbl(ph.kind)
        if l not in seen:
            seen.add(l)
            pass1.append((ms, l))
        else:
            pass2.append((ms, l))

    combined = sorted(pass1 + pass2[:max(0, 8 - len(pass1))], key=lambda x: x[0])
    from collections import Counter
    counts = Counter(l for _, l in combined[:8])
    seen = {}
    result = []
    for i, (ms, l) in enumerate(combined[:8]):
        seen[l] = seen.get(l, 0) + 1
        base = DJ_NAMES.get(l, '')
        name = '' if not base else (base if counts[l] == 1 else f'{base} {seen[l]}')
        result.append({'position_ms': ms, 'label': l, 'slot': i, 'name': name})
    return result
`;

async function loadPyodideEngine() {
  if (pyodideReady) return pyodideReady;
  const statusEl = document.getElementById('pyodide-status');
  if (statusEl) statusEl.textContent = '⏳ Loading Python engine (first time ~15s)…';
  pyodideReady = (async () => {
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js';
    document.head.appendChild(script);
    await new Promise((res, rej) => { script.onload = res; script.onerror = rej; });
    const py = await loadPyodide();
    await py.loadPackage('micropip');
    const micropip = py.pyimport('micropip');
    await micropip.install('pyrekordbox');
    py.runPython(ANALYZE_PYTHON);
    if (statusEl) statusEl.textContent = '✅ Python engine ready';
    return py;
  })();
  pyodideReady.catch(err => {
    if (statusEl) statusEl.textContent = '❌ Failed — use bar-interval mode';
    showToast('Python engine failed to load — phrase analysis unavailable');
    pyodideReady = null;
    analysisMode = 'bar';
    document.getElementById('mode-bar-btn').classList.add('active');
    document.getElementById('mode-phrase-btn').classList.remove('active');
    if (parsedTracks.length) renderTracks();
  });
  return pyodideReady;
}

function indexAnlzFiles(fileList) {
  anlzFileMap = {};
  let count = 0;
  for (const f of fileList) {
    const parts = (f.webkitRelativePath || f.name).split('/');
    if (parts.length < 2) continue;
    const folder = parts[parts.length - 2].toLowerCase();
    const name = parts[parts.length - 1].toUpperCase();
    if (!anlzFileMap[folder]) anlzFileMap[folder] = {};
    if (name.endsWith('.EXT') || name === 'ANLZ0000.EXT') { anlzFileMap[folder].ext = f; count++; }
    if (name.endsWith('.DAT') || name === 'ANLZ0000.DAT') anlzFileMap[folder].dat = f;
  }
  return count;
}

async function analyzeTrackWithPyodide(trackId) {
  const folder = parseInt(trackId, 10).toString(16).padStart(8, '0');
  const files = anlzFileMap[folder];
  if (!files?.ext || !files?.dat) return null;
  const [extBuf, datBuf] = await Promise.all([files.ext.arrayBuffer(), files.dat.arrayBuffer()]);
  const py = await loadPyodideEngine();
  py.globals.set('_ext', new Uint8Array(extBuf));
  py.globals.set('_dat', new Uint8Array(datBuf));
  const result = await py.runPythonAsync('analyze_anlz(_ext, _dat)');
  return result.toJs({ dict_converter: Object.fromEntries });
}

async function runPhraseAnalysis() {
  const statusEl = document.getElementById('pyodide-status');
  let matched = 0, analyzed = 0;
  phraseCueState = {};
  for (const track of parsedTracks) {
    if (statusEl) statusEl.textContent = `⏳ Analyzing ${analyzed + 1}/${parsedTracks.length}…`;
    try {
      const cues = await analyzeTrackWithPyodide(track.id);
      if (cues && cues.length > 0) {
        phraseCueState[track.id] = cues;
        matched++;
      }
    } catch(e) {
      console.warn('Phrase analysis failed for', track.name, e);
    }
    analyzed++;
  }
  if (statusEl) statusEl.textContent = `✅ ${matched}/${parsedTracks.length} tracks analyzed`;
  document.getElementById('anlz-match-count').textContent =
    matched > 0 ? `${matched} / ${parsedTracks.length} matched` : '';
  renderTracks();
  updateOverwriteWarning();
}

function updateOverwriteWarning() {
  if (!parsedTracks.length) return;
  const { maxCues } = getSettings();
  const skipExisting = document.getElementById('skip-existing-cues').checked;
  const warning = document.getElementById('overwrite-warning');
  if (skipExisting) { warning.style.display = 'none'; return; }
  const usedSlots = new Set(Array.from({length: maxCues}, (_, i) => i));
  const atRisk = parsedTracks.filter(t =>
    t.existingCueDetails && t.existingCueDetails.some(c => usedSlots.has(c.num))
  );
  if (atRisk.length > 0) {
    document.getElementById('overwrite-count').textContent = atRisk.length;
    warning.style.display = '';
  } else {
    warning.style.display = 'none';
  }
}

// ── Rendering ──────────────────────────────────────────────────────────────────
function fmtTime(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2,'0')}`;
}

function camelotSortKey(key) {
  if (!key) return 9999;
  const num = parseInt(key, 10) || 0;
  const letter = key.slice(-1).toUpperCase();
  return num * 2 + (letter === 'B' ? 1 : 0);
}

// TASK-034: returns number[] of indices into parsedTracks (NOT track objects).
// Dereference via parsedTracks[i]. Call sites updated to map indices → ids/
// objects at the use site. activeTracks() (below) still returns objects — it
// is the public API used by every write op and must stay stable.
function filteredTracks() {
  // TASK-050 — perf mark around filter recompute (called on every render
  // pass; intentionally cheap when AUTOCUE_PERF is disabled in localStorage).
  try { _perf.mark('filter-start'); } catch (_) {}
  const q = searchQuery ? searchQuery.toLowerCase() : '';
  const cutoffISO = (lastPlayedFilter !== 'all' && lastPlayedFilter !== 'never')
    ? new Date(Date.now() - (lastPlayedFilter === '7d' ? 7 : 30) * 86400000).toISOString()
    : null;
  const out = [];
  for (let i = 0; i < parsedTracks.length; i++) {
    const t = parsedTracks[i];
    if (phraseOnlyFilter && !t.hasPhrase) continue;
    if (beatsOnlyFilter && !t.hasBeats) continue;
    if (_audioOnlyFilter) {
      // Fail-open: tracks whose audio hasn't been probed yet, or whose probe
      // came back "unverified", stay visible. Only "missing" + non-file sources hide.
      if (t.source !== 'file') continue;
      if (_audioProbedAt[t.id] === 'missing') continue;
    }
    if (q && !((t.name || '').toLowerCase().includes(q) ||
               (t.artist || '').toLowerCase().includes(q))) continue;
    if (ratingFilter > 0 && !(t.rating >= ratingFilter)) continue;
    if (playsFilter === 'played' && !(t.playCount > 0)) continue;
    else if (playsFilter === 'unplayed' && !(t.playCount === 0)) continue;
    if (lastPlayedFilter === 'never') {
      if (t.lastPlayed) continue;
    } else if (cutoffISO) {
      if (!(t.lastPlayed && t.lastPlayed >= cutoffISO)) continue;
    }
    if (myTagFilters.size > 0) {
      const tags = t.myTags || [];
      let hit = false;
      for (let k = 0; k < tags.length; k++) { if (myTagFilters.has(tags[k])) { hit = true; break; } }
      if (!hit) continue;
    }
    if (selectedKeys.size > 0 && !(t.key && selectedKeys.has(t.key))) continue;
    if (genreFilters.size > 0 && !genreFilters.has(t.genre || '')) continue;
    out.push(i);
  }
  try { _perf.measure('filter-recompute', 'filter-start'); } catch (_) {}
  return out;
}

// Returns the tracks that write operations (apply/color/delete) should target:
// selected subset when any are checked, otherwise all filtered tracks.
// Public API: returns track OBJECTS (stable contract for every write op).
function activeTracks() {
  const indices = filteredTracks();
  if (selectedTrackIds.size === 0) return indices.map(i => parsedTracks[i]);
  const out = [];
  for (const i of indices) {
    const t = parsedTracks[i];
    if (selectedTrackIds.has(t.id)) out.push(t);
  }
  return out;
}

function updateSelectionBar() {
  const count = selectedTrackIds.size;
  const countEl = document.getElementById('selection-count');
  if (countEl) countEl.textContent = count > 0 ? `${count} selected` : '';
  const deselBtn = document.getElementById('deselect-all-btn');
  if (deselBtn) deselBtn.style.display = count > 0 ? '' : 'none';
  const transBtn = document.getElementById('transition-score-btn');
  if (transBtn) transBtn.style.display = (localMode && count === 2) ? '' : 'none';
  // Bottom action bar — show on any selection
  const bar = document.getElementById('action-bar');
  if (bar) {
    const visible = count > 0;
    bar.classList.toggle('visible', visible);
    bar.setAttribute('aria-hidden', visible ? 'false' : 'true');
    document.body.classList.toggle('has-action-bar', visible);
    const c = document.getElementById('action-bar-count');
    if (c && c.dataset.lastCount !== String(count)) {
      c.dataset.lastCount = String(count);
      c.innerHTML = `<strong>${count.toLocaleString()}</strong> selected`;
      // Re-trigger the existing count-pop tick — this number changes more
      // often than any other in the app and used to swap silently.
      c.classList.remove('count-pop');
      void c.offsetWidth;
      c.classList.add('count-pop');
    }
  }
}

// Wire the bottom action bar to existing Preview / Apply handlers.
(function _wireActionBar() {
  const bar = document.getElementById('action-bar');
  if (!bar) return;
  document.getElementById('action-bar-preview')?.addEventListener('click', () => {
    document.getElementById('preview-cues-btn')?.click();
  });
  document.getElementById('action-bar-apply')?.addEventListener('click', () => {
    // #download-btn is renamed to "Apply to Rekordbox" in local mode and
    // routes through applyToRekordbox() — re-use that path so the same
    // backup + Rekordbox-running checks fire.
    document.getElementById('download-btn')?.click();
  });
  document.getElementById('action-bar-clear')?.addEventListener('click', () => {
    document.getElementById('deselect-all-btn')?.click();
  });
})();

async function showTransitionScore() {
  const ids = [...selectedTrackIds];
  if (ids.length !== 2) return;
  const [idA, idB] = ids;
  const lookup = {};
  for (const t of parsedTracks) lookup[String(t.id)] = t;
  const ta = lookup[String(idA)];
  const tb = lookup[String(idB)];

  // Create modal
  let modal = document.getElementById('transition-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'transition-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:999;display:flex;align-items:center;justify-content:center;';
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
    // Esc closes — self-removing listener survives backdrop-click closes too
    const _escClose = (e) => {
      if (!document.getElementById('transition-modal')) { document.removeEventListener('keydown', _escClose); return; }
      if (e.key === 'Escape') { modal.remove(); document.removeEventListener('keydown', _escClose); }
    };
    document.addEventListener('keydown', _escClose);
  }
  modal.innerHTML = '<div class="fade-in-up" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;min-width:360px;max-width:480px;">' +
    '<div style="font-size:13px;font-weight:600;margin-bottom:12px;">⇌ Transition Score</div>' +
    '<div style="font-size:11px;color:var(--muted);margin-bottom:16px;">' +
    `<strong>${ta ? ta.artist + ' — ' + ta.name : idA}</strong> → <strong>${tb ? tb.artist + ' — ' + tb.name : idB}</strong></div>` +
    '<div id="transition-content" style="font-size:12px;"><span class="btn-spinner"></span>Scoring…</div>' +
    '<div style="margin-top:14px;text-align:right;"><button onclick="document.getElementById(\'transition-modal\').remove()" style="font-size:11px;padding:3px 10px;background:none;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--muted);">Close</button></div>' +
    '</div>';

  try {
    const r = await fetch('/api/transitions/score', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_a_id: parseInt(idA), track_b_id: parseInt(idB) }),
    });
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    const bar = (score) => {
      const pct = Math.round(score);
      const color = pct >= 80 ? 'var(--green)' : pct >= 50 ? '#fa0' : '#f44';
      return `<div style="display:flex;align-items:center;gap:8px;margin:4px 0;">` +
        `<span style="min-width:16px;font-weight:600;color:${color}">${pct}</span>` +
        `<div style="flex:1;background:var(--surface2);border-radius:3px;height:6px;">` +
        // fillBar animates width 0 → --tw, same as the mixability bars
        `<div style="--tw:${pct}%;width:${pct}%;background:${color};height:6px;border-radius:3px;animation:fillBar .45s var(--ease-fill) both;"></div></div></div>`;
    };
    const content = document.getElementById('transition-content');
    const explanationHtml = d.explanation && d.explanation.length
      ? `<div style="margin-top:12px;padding:8px;background:var(--surface2);border-radius:4px;font-size:11px;color:var(--muted);line-height:1.7;">` +
        d.explanation.map(s => `• ${s}`).join('<br>') + `</div>`
      : '';
    if (content) content.innerHTML =
      `<div style="font-size:16px;font-weight:700;margin-bottom:12px;color:var(--green)">` +
      `Overall: ${d.overall}/100</div>` +
      `<div style="margin-bottom:2px;color:var(--muted)">BPM: ${d.bpm_a} → ${d.bpm_b}</div>` + bar(d.bpm) +
      `<div style="margin-bottom:2px;color:var(--muted)">Key: ${d.key_a || '?'} → ${d.key_b || '?'}</div>` + bar(d.key) +
      `<div style="margin-bottom:2px;color:var(--muted)">Energy handoff</div>` + bar(d.energy) +
      explanationHtml;
  } catch {
    const content = document.getElementById('transition-content');
    if (content) content.textContent = 'Error loading transition score.';
  }
}

// Returns track OBJECTS (sorted by current sort key). Public API used by
// renderTracks() and any caller that needs the post-filter-post-sort list.
// Internally derives indices from filteredTracks() (TASK-034) then sorts via
// parsedTracks[idx] dereference.
function sortedTracks() {
  const { by, order } = currentSort;
  const indices = filteredTracks().slice();
  indices.sort((ai, bi) => {
    const a = parsedTracks[ai], b = parsedTracks[bi];
    let av, bv;
    if (by === 'bpm') { av = a.bpm || 0; bv = b.bpm || 0; }
    else if (by === 'artist') { av = (a.artist || '').toLowerCase(); bv = (b.artist || '').toLowerCase(); }
    else if (by === 'album') { av = (a.album || '').toLowerCase(); bv = (b.album || '').toLowerCase(); }
    else if (by === 'key') { av = camelotSortKey(a.key); bv = camelotSortKey(b.key); }
    else if (by === 'rating') { av = a.rating || 0; bv = b.rating || 0; }
    else if (by === 'plays') { av = a.playCount || 0; bv = b.playCount || 0; }
    else { av = (a.name || '').toLowerCase(); bv = (b.name || '').toLowerCase(); }
    if (av < bv) return order === 'asc' ? -1 : 1;
    if (av > bv) return order === 'asc' ? 1 : -1;
    return 0;
  });
  return indices.map(i => parsedTracks[i]);
}

// Build human-readable reasoning for a cue badge.
// cue: { slot, label, name, confidence, phraseMode, phraseBars }
// Returns { confidence: string, reasons: string[] }
function _explainCue(cue) {
  const slot = cue.slot;
  const label = cue.label || '';
  const conf = cue.confidence ?? 1.0;
  const mode = cue.phraseMode || (conf >= 0.9 ? 'phrase' : conf >= 0.5 ? 'bar' : 'heuristic');
  const bars = cue.phraseBars ?? 0;

  // Memory cue
  if (slot === -1) {
    return {
      confidence: 'Auto',
      reasons: [
        'CDJ load point (Auto Cue)',
        'Anchored to earliest phrase boundary',
      ],
    };
  }

  // No confidence data → pre-existing cue not generated by AutoCue
  if (cue.confidence == null && cue.phraseMode == null) {
    return { confidence: '—', reasons: ['Manually placed cue'] };
  }

  const confLabel = conf >= 0.9 ? 'High' : conf >= 0.5 ? 'Medium' : 'Low';
  const reasons = [];

  if (mode === 'heuristic') {
    reasons.push('No BPM or phrase data — 30-second interval estimate');
    reasons.push(`Position: ${cue.name || ''}`);
    return { confidence: confLabel, reasons };
  }

  if (mode === 'bar') {
    if (cue.hasPhrase) {
      reasons.push('Using bar intervals — switch to ✨ Phrase mode to use Rekordbox phrase data');
    } else {
      reasons.push('Bar-interval fallback (no Rekordbox phrase analysis)');
      reasons.push('Run analysis in Rekordbox to enable phrase-based cues');
    }
    reasons.push(`Position: ${cue.name || ''}`);
    return { confidence: confLabel, reasons };
  }

  // Phrase mode
  const LABEL_REASONS = {
    'Drop':   'Rekordbox phrase: Chorus (high-energy section)',
    'Build':  'Rekordbox phrase: Up (energy rise)',
    'Break':  'Rekordbox phrase: Down (low-energy break)',
    'Intro':  'Rekordbox phrase: Intro',
    'Verse':  'Rekordbox phrase: Verse',
    'Bridge': 'Rekordbox phrase: Bridge',
    'Outro':  'Rekordbox phrase: Outro',
    'Fill':   'Rekordbox fill beat marker',
  };

  // Determine base label for lookup (strip trailing number, e.g. "Drop 2" → "Drop")
  const baseName = (cue.name || label).replace(/\s+\d+$/, '');
  const phraseReason = LABEL_REASONS[baseName] || `Rekordbox phrase: ${baseName || label}`;
  reasons.push(phraseReason);

  if (bars > 0) reasons.push(`${bars}-bar phrase`);

  if (slot === 0) reasons.push('Slot A: mix-in point (first non-Intro phrase)');
  else if (baseName === 'Drop' || label === 'Chorus') reasons.push('Priority slot: main drop');
  else if (baseName === 'Build' || label === 'Up')    reasons.push('Priority slot: energy build');
  else if (baseName === 'Outro')                       reasons.push('Priority slot: outro/mix-out');

  return { confidence: confLabel, reasons };
}

async function _toggleSimilarPanel(btn, panel, trackId) {
  if (panel.classList.contains('visible')) {
    _slideClose(panel, 'visible');
    return;
  }
  _slideOpen(panel, 'visible');
  if (panel.dataset.loaded) return;
  panel.innerHTML = '<span class="btn-spinner"></span>Finding similar tracks…';
  try {
    const r = await fetch(`/api/tracks/${trackId}/similar?n=5`);
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    // Cache only successful loads — flagging before the fetch pinned a failed
    // "Error loading" message into the panel forever (reopen never retried).
    panel.dataset.loaded = '1';
    panel.innerHTML = '';
    if (!d.results || d.results.length === 0) {
      panel.textContent = 'No similar tracks found within ±8 BPM.';
      return;
    }
    // Build a lookup of track id → title/artist from parsedTracks
    const lookup = {};
    for (const t of parsedTracks) lookup[String(t.id)] = t;
    const seen = new Set();
    const deduped = d.results.filter(item => {
      const t = lookup[String(item.track_id)];
      if (!t) return true;
      // parsedTracks rows expose the title under `name` (matches the API's
      // TrackItem schema → `title` is mapped during ingest). The earlier
      // `t.title` lookup silently produced `undefined`, every key became
      // `"<artist>|||"`, and every same-artist similar match collapsed
      // into one row. Probe-verified against a 3,775-track library: track
      // 212087170's similar results (5 → 1 row) was the surfacing case.
      const artistStr = (t.artist || '').toLowerCase().trim();
      const titleStr  = (t.name   || '').toLowerCase().trim();
      const key = `${artistStr}|||${titleStr}`;
      if (!artistStr && !titleStr) return true;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    const note = document.createElement('div');
    note.style.cssText = 'font-size:10px;color:var(--muted);margin-bottom:6px;';
    note.textContent = 'Scores key + energy + BPM proximity. Harmonic compatibility is scored separately in the Transition panel.';
    panel.appendChild(note);
    for (const item of deduped) {
      const t = lookup[String(item.track_id)];
      const row = document.createElement('div');
      row.className = 'similar-row';
      const scoreEl = document.createElement('span');
      scoreEl.className = 'similar-score';
      scoreEl.textContent = `${Math.round(item.score * 100)}%`;
      const bpmEl = document.createElement('span');
      bpmEl.className = 'similar-bpm';
      bpmEl.textContent = item.bpm_diff === 0 ? '±0' : `±${item.bpm_diff.toFixed(1)}`;
      const nameEl = document.createElement('span');
      nameEl.textContent = t ? `${t.artist} — ${t.name}` : `Track ${item.track_id}`;
      row.appendChild(scoreEl);
      row.appendChild(bpmEl);
      row.appendChild(nameEl);
      panel.appendChild(row);
    }
  } catch {
    panel.textContent = 'Error loading similar tracks — close and reopen to retry.';
  }
}

async function _renderCategoryChip(chip) {
  const trackId = chip.dataset.trackId;
  if (!trackId) return;
  try {
    const r = await fetch(`/api/tracks/${trackId}/classification`);
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    if (!d.primary || d.primary === 'unknown' || d.confidence < 0.1) {
      chip.remove(); return;
    }
    chip.textContent = d.label;
    chip.className = 'category-chip';
    chip.style.color = d.color;
    chip.style.borderColor = d.color;
    chip.style.background = d.color + '18';
    chip.title = `${d.label} · confidence ${Math.round(d.confidence * 100)}%`;
  } catch {
    chip.remove();
  }
}

var _mixCountRafId = null;
function _animateCount(el, to, prefix, suffix, duration) {
  duration = duration || 600;
  if (_mixCountRafId) { cancelAnimationFrame(_mixCountRafId); _mixCountRafId = null; }
  var start = performance.now();
  function step(ts) {
    if (!document.contains(el)) return;
    var p = Math.min((ts - start) / duration, 1);
    var ease = 1 - Math.pow(1 - p, 3);
    el.textContent = prefix + Math.round(to * ease) + suffix;
    if (p < 1) { _mixCountRafId = requestAnimationFrame(step); }
    else { _mixCountRafId = null; }
  }
  _mixCountRafId = requestAnimationFrame(step);
}

async function _renderMixabilityChip(chip, breakdown) {
  const trackId = chip.dataset.trackId;
  if (!trackId) return;
  try {
    const r = await fetch(`/api/tracks/${trackId}/mixability`);
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    if (d.score === null || d.score === undefined) {
      chip.textContent = 'No phrase data';
      chip.className = 'mix-score-chip no-data';
      return;
    }
    chip.className = 'mix-score-chip';
    _animateCount(chip, d.score, 'Mix ', '/100');
    const comp = d.components || {};
    const rows = [
      { label: 'Intro', key: 'intro', extra: d.intro_bars > 0 ? `${d.intro_bars} bars` : '' },
      { label: 'Outro', key: 'outro', extra: d.outro_bars > 0 ? `${d.outro_bars} bars` : '' },
      { label: 'Energy', key: 'energy', extra: '' },
      { label: 'Vocals', key: 'vocals', extra: d.vocal_proxy ? 'vocals detected' : 'instrumental' },
      { label: 'Structure', key: 'structure', extra: `${d.phrase_count} phrases` },
    ];
    breakdown.innerHTML = '';
    for (const row of rows) {
      const val = comp[row.key] ?? 0;
      const rowEl = document.createElement('div');
      rowEl.className = 'mix-breakdown-row';
      const lbl = document.createElement('span');
      lbl.className = 'mix-breakdown-label';
      lbl.textContent = row.label;
      const barBg = document.createElement('div');
      barBg.className = 'mix-breakdown-bar-bg';
      const bar = document.createElement('div');
      bar.className = 'mix-breakdown-bar';
      bar.style.setProperty('--tw', `${val}%`);
      barBg.appendChild(bar);
      const valEl = document.createElement('span');
      valEl.className = 'mix-breakdown-val';
      valEl.textContent = row.extra || `${val}%`;
      rowEl.appendChild(lbl);
      rowEl.appendChild(barBg);
      rowEl.appendChild(valEl);
      breakdown.appendChild(rowEl);
    }
    chip.addEventListener('click', () => _slideToggle(breakdown, 'open'));
  } catch {
    chip.textContent = '—';
    chip.className = 'mix-score-chip no-data';
  }
}

async function _renderEnergySparkline(container) {
  const trackId = container.dataset.trackId;
  if (!trackId) return;
  try {
    const r = await fetch(`/api/tracks/${trackId}/energy`);
    if (!r.ok) throw new Error('fetch failed');
    const data = await r.json();
    container.innerHTML = '';
    if (!data.energy || data.energy.length === 0) {
      const nd = document.createElement('span');
      nd.className = 'no-data';
      nd.textContent = 'no waveform';
      container.appendChild(nd);
      return;
    }
    const pts = data.energy;
    _energyCache[trackId] = pts;  // D4: cache for mini waveform
    const w = container.offsetWidth || 200;
    const h = 16;
    const step = w / (pts.length - 1 || 1);
    const coords = pts.map((v, i) => `${(i * step).toFixed(1)},${(h - v * h).toFixed(1)}`).join(' ');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    poly.setAttribute('points', coords);
    poly.setAttribute('fill', 'none');
    poly.setAttribute('stroke', 'var(--green)');
    poly.setAttribute('stroke-width', '1.5');
    poly.setAttribute('stroke-linecap', 'round');
    poly.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(poly);
    container.appendChild(svg);
    if (data.energy_profile) {
      const profileLabels = { flat: '— flat', build: '↑ build', 'drop-then-flat': '↓ drop', wave: '∿ wave' };
      const lbl = document.createElement('span');
      lbl.style.cssText = 'font-size:9px;color:var(--muted);margin-left:4px;white-space:nowrap;vertical-align:middle;';
      lbl.textContent = profileLabels[data.energy_profile] || data.energy_profile;
      container.appendChild(lbl);
    }
  } catch {
    container.innerHTML = '';
    const nd = document.createElement('span');
    nd.className = 'no-data';
    nd.textContent = '—';
    container.appendChild(nd);
  }
}

// Append the "Phrase structure" strip — a visualization of the track's
// phrase layout (intro / verse / drop / outro …). It describes the TRACK,
// not what AutoCue would write, so it belongs on BOTH the cue-gen path and
// the Skipped path (a skipped track still has phrase structure worth seeing).
// `opts.notes` controls the "no phrase data" / "no ANLZ" informational
// lines — on for the regular path, OFF for skipped cards to keep them
// within the fixed 160px card height (TASK-033). With lazy phrase loading
// the strip appears once the viewport fetch populates phraseCueState and
// _updateTrackCardCues rebuilds the card.
function _appendPhraseStrip(cardMain, track, opts) {
  opts = opts || {};
  if (analysisMode !== 'phrase') return false;
  if (phraseCueState[track.id]?.length && track.totalTime > 0) {
    // `compact` drops the "Phrase structure" caption — used on Skipped cards
    // where the #163 existing-cue chips already consume most of the fixed
    // 160px (TASK-033), so the ~24px caption+margins would push the strip
    // out of the visible box. The coloured segments are self-describing.
    if (!opts.compact) {
      const stripLabel = document.createElement('div');
      stripLabel.style.cssText = 'font-size:10px;color:var(--muted);margin-top:6px;margin-bottom:2px;text-transform:uppercase;letter-spacing:.05em;';
      stripLabel.textContent = 'Phrase structure';
      cardMain.appendChild(stripLabel);
    }
    // `cueTicks` overlays existing hot-cue positions on the strip (Skipped
    // cards merge the #163 chips here so both structure + cues fit 160px).
    const strip = buildPhraseStrip(phraseCueState[track.id], track.totalTime, opts.cueTicks);
    if (strip) {
      if (opts.compact) strip.style.marginTop = '4px';
      cardMain.appendChild(strip);
      return true;
    }
    return false;
  } else if (opts.notes && !localMode) {
    const note = document.createElement('p');
    note.style.cssText = 'font-size:12px;color:var(--muted);margin-top:4px;';
    note.textContent = '⬡ No ANLZ data — drop the analysis folder above to enable phrase analysis';
    cardMain.appendChild(note);
  } else if (opts.notes && !track.hasPhrase) {
    const note = document.createElement('p');
    note.style.cssText = 'font-size:12px;color:var(--muted);margin-top:4px;';
    note.textContent = '⬡ No phrase data — track has not been analyzed by Rekordbox';
    cardMain.appendChild(note);
  }
  return false;
}

// Append the per-track "intelligence" widgets (sparkline, mix score chip,
// classification chip, similar-tracks button + panels) to `cardMain`. No-op
// when not in localMode (the data behind these widgets is local-server
// only). Shared between the cue-gen card path and the Skipped card path —
// both describe the same underlying track, so both should surface the same
// per-track intelligence.
function _appendIntelligenceWidgets(cardMain, track) {
  if (!localMode) return;
  const sparkContainer = document.createElement('div');
  sparkContainer.className = 'energy-sparkline';
  sparkContainer.dataset.trackId = track.id;
  const loading = document.createElement('span');
  loading.className = 'loading';
  loading.textContent = '▁▂▃▄';
  sparkContainer.appendChild(loading);
  cardMain.appendChild(sparkContainer);

  // Mixability score chip — loaded lazily alongside sparkline
  const mixRow = document.createElement('div');
  mixRow.className = 'mix-score-row';
  const mixChip = document.createElement('span');
  mixChip.className = 'mix-score-chip loading';
  mixChip.textContent = '…';
  mixChip.dataset.trackId = track.id;
  mixRow.appendChild(mixChip);

  const catChip = document.createElement('span');
  catChip.className = 'category-chip loading';
  catChip.textContent = '·';
  catChip.dataset.trackId = track.id;
  mixRow.appendChild(catChip);
  catChip._isCategoryChip = true;

  const simBtn = document.createElement('button');
  simBtn.className = 'similar-btn';
  simBtn.textContent = '≈ Similar';
  simBtn.dataset.trackId = track.id;
  mixRow.appendChild(simBtn);

  const mixBreakdown = document.createElement('div');
  mixBreakdown.className = 'mix-breakdown';
  const simPanel = document.createElement('div');
  simPanel.className = 'similar-panel';
  cardMain.appendChild(mixRow);
  cardMain.appendChild(mixBreakdown);
  cardMain.appendChild(simPanel);
  // Store ref on chip for observer callback
  mixChip._breakdown = mixBreakdown;

  simBtn.addEventListener('click', () => _toggleSimilarPanel(simBtn, simPanel, track.id));
}

function buildTrackCard(track, cues, willSkip, opts = {}) {
  const { hideAlbum = false } = opts;
  const hasAudio = !!audioState[track.id];
  const artUrl = audioState[track.id]?.artworkUrl
    || (localMode ? `/api/tracks/${track.id}/artwork` : null);

  const card = document.createElement('div');
  card.className = 'track-card';
  card.dataset.testid = 'track-card';
  card.dataset.trackId = track.id;
  if (track.colorName) card.dataset.color = track.colorName;
  if (nowPlayingId === track.id && !audioPlayer.paused) card.classList.add('now-playing');

  const cardTop = document.createElement('div');
  cardTop.className = 'card-top';

  // Bulk-select checkbox (local mode only)
  if (localMode) {
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'track-select-cb';
    cb.checked = selectedTrackIds.has(track.id);
    if (selectedTrackIds.has(track.id)) card.classList.add('selected');
    cb.addEventListener('change', e => {
      e.stopPropagation();
      if (e.target.checked) { selectedTrackIds.add(track.id); card.classList.add('selected'); }
      else { selectedTrackIds.delete(track.id); card.classList.remove('selected'); }
      updateSelectionBar();
    });
    cardTop.appendChild(cb);
    // Card body toggles selection — the card already advertises cursor:pointer
    // but only the 15px checkbox used to respond. Inner interactive elements
    // (buttons, badges, panels, seek surfaces) keep their own behaviour.
    card.addEventListener('click', e => {
      if (e.target.closest(
        'button, a, input, select, textarea, svg, canvas, .cue-badge, .cue-slots, ' +
        '.tag-pill, .timeline, .phrase-strip, .similar-panel, .cue-reason-panel, .art-play-overlay'
      )) return;
      const sel = window.getSelection && window.getSelection();
      if (sel && String(sel).length) return; // don't hijack text selection
      cb.checked = !cb.checked;
      cb.dispatchEvent(new Event('change'));
    });
  }

  // Artwork
  const artWrap = document.createElement('div');
  artWrap.className = 'artwork-wrap';
  artWrap.style.position = 'relative';
  const ph = document.createElement('div');
  ph.className = 'artwork-placeholder';
  ph.textContent = '♪';
  artWrap.appendChild(ph);
  if (artUrl) {
    const img = document.createElement('img');
    img.className = 'artwork-img';
    img.loading = 'lazy';
    img.src = artUrl;
    img.onload = () => ph.remove();
    img.onerror = () => img.remove();
    artWrap.appendChild(img);
  }
  // Art play overlay (always present; in local mode triggers ensureLocalAudio)
  const artOverlay = document.createElement('button');
  artOverlay.className = 'art-play-overlay' + (nowPlayingId === track.id && !audioPlayer.paused ? ' playing' : '');
  artOverlay.setAttribute('aria-label', 'Play');
  artOverlay.innerHTML = (nowPlayingId === track.id && !audioPlayer.paused) ? SVG_PAUSE : SVG_PLAY;
  artOverlay.addEventListener('click', e => {
    e.stopPropagation();
    if (localMode && !audioState[track.id]) {
      ensureLocalAudio(track).then(() => togglePlayTrack(track.id));
    } else {
      togglePlayTrack(track.id);
    }
  });
  artWrap.appendChild(artOverlay);
  cardTop.appendChild(artWrap);

  // Card main
  const cardMain = document.createElement('div');
  cardMain.className = 'card-main';

  // Track meta row: title + BPM + duration + play btn + load btn
  const meta = document.createElement('div');
  meta.className = 'track-meta';

  const nameEl = document.createElement('span');
  nameEl.className = 'track-name';
  // Streaming-source tracks (Spotify / Tidal / Apple Music links) frequently
  // import into Rekordbox with empty Title / ArtistName / AlbumName columns
  // — the API echoes those through as empty strings and the card otherwise
  // renders as a visually blank row. Show a clear "untitled" placeholder so
  // the user spots which rows need a Rekordbox-side metadata fix instead of
  // wondering why some cards look broken.
  if (!track.name && track.source === 'streaming') {
    nameEl.textContent = '— Untitled streaming track —';
    nameEl.classList.add('untitled');
    nameEl.title = `Track ${track.id} — no title in Rekordbox; streaming source`;
  } else {
    nameEl.title = track.name;
    nameEl.textContent = track.name;
  }
  meta.appendChild(nameEl);

  if (localMode && healthData[String(track.id)]) {
    const h = healthData[String(track.id)];
    const chip = document.createElement('span');
    chip.className = 'health-chip ' + (h.score >= 90 ? 'hc-good' : h.score >= 70 ? 'hc-ok' : 'hc-bad');
    chip.title = `Health: ${h.score}/100\n${(h.issues || []).map(i => i.message || i.code).join('\n')}`;
    chip.textContent = h.score;
    meta.appendChild(chip);
  }

  if (track.colorName) {
    const dot = document.createElement('span');
    dot.className = 'color-dot';
    dot.dataset.color = track.colorName;
    dot.title = track.colorName;
    meta.appendChild(dot);
  }

  if (track.bpm) {
    const b = document.createElement('span');
    b.className = 'track-bpm';
    b.textContent = track.bpm.toFixed(2) + ' BPM';
    meta.appendChild(b);
  }
  if (track.key) {
    const k = document.createElement('span');
    k.className = 'track-key';
    k.textContent = track.key;
    meta.appendChild(k);
  }
  if (track.totalTime) {
    const t = document.createElement('span');
    t.className = 'track-time';
    t.textContent = fmtTime(track.totalTime);
    meta.appendChild(t);
  }

  const playBtn = document.createElement('button');
  playBtn.className = 'play-btn' + (hasAudio ? '' : ' hidden');
  playBtn.innerHTML = (nowPlayingId === track.id && !audioPlayer.paused) ? SVG_PAUSE : SVG_PLAY;
  playBtn.setAttribute('aria-label', 'Play');
  playBtn.addEventListener('click', e => { e.stopPropagation(); togglePlayTrack(track.id); });
  meta.appendChild(playBtn);

  // B3: orphans (streaming or known-missing) get a "No audio" chip that opens
  // the info modal instead of the Load audio button — kills the failed-fetch
  // toast pileup that triggered the user's image #7 sample.
  const isOrphan = (track.source && track.source !== 'file') || _audioProbedAt[track.id] === 'missing';
  if (isOrphan) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'load-audio-btn';
    chip.textContent = 'No audio ⓘ';
    chip.title = 'This track has no playable audio file. Click to see details / rescue via YouTube.';
    chip.addEventListener('click', e => { e.stopPropagation(); openTrackInfoModal(track.id); });
    meta.appendChild(chip);
  } else {
    const loadBtn = document.createElement('label');
    loadBtn.className = 'load-audio-btn' + (hasAudio ? ' hidden' : '');
    loadBtn.textContent = 'Load audio';
    const loadInput = document.createElement('input');
    loadInput.type = 'file';
    loadInput.accept = 'audio/*';
    loadInput.addEventListener('change', () => {
      if (loadInput.files[0]) registerAudioFile(track, loadInput.files[0]);
    });
    loadBtn.appendChild(loadInput);
    meta.appendChild(loadBtn);
  }

  cardMain.appendChild(meta);

  // Artist / album sub-row (hidden in album-group view where album is shown in header)
  const showArtist = track.artist;
  const showAlbumName = !hideAlbum && track.album;
  // Same streaming-empty-metadata case as above: when BOTH name and artist
  // are missing, surface a clear artist placeholder so the row makes sense.
  // (We don't placeholder when ONLY the artist is missing — 187 tracks in a
  // typical library are classical / various-artists with empty artist but
  // valid title; those should keep rendering naturally.)
  const showArtistPlaceholder =
    !track.name && !track.artist && track.source === 'streaming';
  if (showArtist || showAlbumName || showArtistPlaceholder) {
    const sub = document.createElement('div');
    sub.style.cssText = 'display:flex;gap:6px;align-items:baseline;flex-wrap:wrap;margin-top:2px;margin-bottom:4px;';
    if (showArtist) {
      const a = document.createElement('span');
      a.className = 'track-artist';
      a.textContent = track.artist;
      sub.appendChild(a);
    } else if (showArtistPlaceholder) {
      const a = document.createElement('span');
      a.className = 'track-artist untitled';
      a.textContent = 'No artist metadata';
      sub.appendChild(a);
    }
    if (showAlbumName) {
      const al = document.createElement('span');
      al.className = 'track-album';
      al.textContent = '· ' + track.album;
      sub.appendChild(al);
    }
    cardMain.appendChild(sub);
  }

  // Rating / play-count / last-played / My Tags (local mode data)
  if (localMode && (track.rating || track.playCount || track.lastPlayed || (track.myTags && track.myTags.length))) {
    const infoRow = document.createElement('div');
    infoRow.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:4px;';
    if (track.rating > 0) {
      const rEl = document.createElement('span');
      rEl.className = 'track-rating';
      rEl.title = `Rating: ${track.rating}/5`;
      rEl.textContent = '★'.repeat(track.rating) + '☆'.repeat(5 - track.rating);
      infoRow.appendChild(rEl);
    }
    if (track.playCount > 0) {
      const pEl = document.createElement('span');
      pEl.className = 'track-plays';
      pEl.textContent = `${track.playCount} play${track.playCount !== 1 ? 's' : ''}`;
      infoRow.appendChild(pEl);
    }
    if (track.lastPlayed) {
      const lpEl = document.createElement('span');
      lpEl.className = 'track-plays';
      lpEl.title = track.lastPlayed;
      const d = new Date(track.lastPlayed);
      lpEl.textContent = `Last: ${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}`;
      infoRow.appendChild(lpEl);
    }
    for (const tag of (track.myTags || [])) {
      const tp = document.createElement('span');
      tp.className = 'tag-pill';
      tp.textContent = tag;
      tp.title = `Filter by "${tag}"`;
      tp.style.cursor = 'pointer';
      const catColor = (typeof AUTO_TAG_COLORS !== 'undefined') && AUTO_TAG_COLORS[tag];
      if (catColor) {
        tp.style.cssText = `background:${catColor}22;border-color:${catColor}55;color:${catColor};cursor:pointer;`;
      }
      tp.addEventListener('click', e => {
        e.stopPropagation();
        if (typeof window._toggleTagFilter === 'function') window._toggleTagFilter(tag, true);
      });
      infoRow.appendChild(tp);
    }
    cardMain.appendChild(infoRow);
  }

  if (willSkip) {
    const skipped = document.createElement('span');
    skipped.className = 'skipped-badge';
    skipped.textContent = `Skipped — has ${track.existingHotCues} existing hot cue${track.existingHotCues !== 1 ? 's' : ''}`;
    cardMain.appendChild(skipped);

    // Phrase structure strip with the existing hot-cue positions overlaid as
    // ticks (the chosen "merge" layout). The strip describes the track and the
    // ticks show WHERE its existing cues sit — both in one 16px row, so both
    // fit the fixed 160px card (TASK-033). `compact` drops the caption;
    // `notes:false` skips the "no data" lines. Auto-cue badges stay hidden —
    // those ARE the skipped generation outcome. With lazy phrase loading the
    // strip appears once the viewport fetch lands and _updateTrackCardCues
    // rebuilds the card.
    const stripRendered = _appendPhraseStrip(cardMain, track, {
      notes: false, compact: true, cueTicks: track.existingCueDetails,
    });

    // Fallback: when there's NO phrase data (no strip), keep the #163 chip row
    // so the existing cues are still shown — names, slot letters, positions.
    // Cap at SKIPPED_CHIP_LIMIT and add a "+N more" indicator so the row stays
    // single-line inside the Virtualizer's fixed 160px card height (TASK-033).
    if (!stripRendered && track.existingCueDetails && track.existingCueDetails.length > 0) {
      const SKIPPED_CHIP_LIMIT = 9;  // 8 hot cues (A-H) + memory cue
      const chipsRow = document.createElement('div');
      chipsRow.className = 'existing-cues-row';
      const sortedExisting = [...track.existingCueDetails].sort((a, b) => {
        if (a.num === -1) return -1;
        if (b.num === -1) return 1;
        return a.num - b.num;
      });
      const visible = sortedExisting.slice(0, SKIPPED_CHIP_LIMIT);
      const overflowCount = sortedExisting.length - visible.length;
      for (const ec of visible) {
        const chip = document.createElement('span');
        chip.className = 'existing-cue-chip skipped-card-chip';
        const slotLetter = ec.num === -1 ? 'Mem' : (ec.num >= 0 && ec.num <= 7 ? String.fromCharCode(65 + ec.num) : '?');
        const mins = Math.floor((ec.start || 0) / 60);
        const secs = Math.floor((ec.start || 0) % 60);
        chip.textContent = `${slotLetter} ${ec.name || ''} ${mins}:${String(secs).padStart(2,'0')}`.replace(/\s+/g, ' ').trim();
        if (ec.colorName) chip.dataset.color = ec.colorName;
        chipsRow.appendChild(chip);
      }
      if (overflowCount > 0) {
        const ov = document.createElement('span');
        ov.className = 'existing-cues-overflow';
        ov.textContent = `+${overflowCount} more`;
        ov.title = `${overflowCount} more existing cue${overflowCount !== 1 ? 's' : ''} not shown`;
        chipsRow.appendChild(ov);
      }
      cardMain.appendChild(chipsRow);
    }

    // Restore the per-track intelligence widgets on Skipped cards (PR #163
    // regression): sparkline, mix-score chip, classification chip, similar
    // button. These describe the track itself and apply regardless of
    // whether AutoCue would write new cues.
    _appendIntelligenceWidgets(cardMain, track);

    cardTop.appendChild(cardMain);
    card.appendChild(cardTop);
    return card;
  }

  if (!track.tempo && !track.bpm) {
    const w = document.createElement('p');
    w.className = 'warn';
    w.textContent = '⚠ No beat-grid data — open this track in Rekordbox, run BPM analysis, then re-export your XML';
    cardMain.appendChild(w);
    cardTop.appendChild(cardMain);
    card.appendChild(cardTop);
    return card;
  }

  // Cue badges — sorted by slot (A→H, memory cue first) for consistent display
  const badges = document.createElement('div');
  badges.className = 'cue-slots';
  const sortedCues = [...cues].sort((a, b) => {
    if (a.slot === -1) return -1;
    if (b.slot === -1) return 1;
    return a.slot - b.slot;
  });
  for (const cue of sortedCues) {
    const b = document.createElement('span');
    b.className = 'cue-badge' + (hasAudio ? ' playable' : '');
    b.dataset.slot   = cue.slot;
    b.dataset.posSec = cue.posSec;
    const conf = cue.confidence ?? 1.0;
    if (conf < 0.4)      b.dataset.confidence = 'heuristic';
    else if (conf < 0.9) b.dataset.confidence = 'bar';
    // phrase cues (conf=1.0) get no data-confidence → full opacity
    const displayName = cue.name || cue.label || '';
    const slotLabel = cue.slot === -1 ? 'Mem' : (SLOT_NAMES[cue.slot] ?? '?');
    b.textContent = `${slotLabel} ${displayName} ${fmtTime(cue.posSec)}`.trim();
    if (hasAudio) b.addEventListener('click', () => {
      seekAndPlay(track.id, cue.posSec);
      b.classList.remove('seek-flash');
      void b.offsetWidth;
      b.classList.add('seek-flash');
      b.addEventListener('animationend', () => b.classList.remove('seek-flash'), { once: true });
    });

    // ℹ Cue Reasoning button
    const infoBtn = document.createElement('button');
    infoBtn.className = 'cue-reason-btn';
    infoBtn.title = 'Cue reasoning';
    infoBtn.textContent = 'ℹ';
    const reasonPanel = document.createElement('div');
    reasonPanel.className = 'cue-reason-panel';
    infoBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isVisible = reasonPanel.classList.contains('visible');
      // Close all other open panels in this card
      badges.querySelectorAll('.cue-reason-panel.visible').forEach(p => _slideClose(p, 'visible'));
      if (!isVisible) {
        const { confidence: cl, reasons } = _explainCue(cue);
        const header = document.createElement('div');
        header.style.cssText = 'font-weight:700;margin-bottom:4px;';
        header.textContent = `Cue Reasoning — ${cl} confidence`;
        reasonPanel.innerHTML = '';
        reasonPanel.appendChild(header);
        const ul = document.createElement('ul');
        reasons.forEach(r => { const li = document.createElement('li'); li.textContent = r; ul.appendChild(li); });
        reasonPanel.appendChild(ul);
        _slideOpen(reasonPanel, 'visible');
      }
    });
    b.appendChild(infoBtn);
    badges.appendChild(b);
    badges.appendChild(reasonPanel);
  }
  if (cues.length === 0) {
    const b = document.createElement('span');
    b.style.cssText = 'font-size:12px;color:var(--muted)';
    b.textContent = 'No cues fit within track length';
    badges.appendChild(b);
  }
  cardMain.appendChild(badges);

  if (track.existingCueDetails && track.existingCueDetails.length > 0) {
    const { maxCues: mc } = getSettings();
    const usedSlots = new Set(Array.from({length: mc}, (_, i) => i));
    const chipsRow = document.createElement('div');
    chipsRow.style.cssText = 'display:flex;gap:4px;flex-wrap:wrap;margin-top:5px;';
    for (const ec of track.existingCueDetails) {
      const chip = document.createElement('span');
      chip.className = 'existing-cue-chip' + (usedSlots.has(ec.num) ? ' replaced' : '');
      const slotLetter = ec.num >= 0 && ec.num <= 7 ? String.fromCharCode(65 + ec.num) : '?';
      const mins = Math.floor(ec.start / 60), secs = Math.floor(ec.start % 60);
      const icon = usedSlots.has(ec.num) ? '⚠' : '✓';
      chip.textContent = `${icon} ${slotLetter}: ${mins}:${String(secs).padStart(2,'0')}${ec.name ? ' ' + ec.name : ''}`;
      chip.title = usedSlots.has(ec.num) ? 'Will be replaced by AutoCue' : 'Will be preserved';
      chipsRow.appendChild(chip);
    }
    cardMain.appendChild(chipsRow);
  }

  _appendPhraseStrip(cardMain, track, { notes: true });

  // Energy sparkline + mixability + classification + similar tracks — the
  // "intelligence" widgets that describe the TRACK itself (not what AutoCue
  // would write). Shared between the regular cue-gen path AND the Skipped
  // path (PR for #163 regression — Skipped cards previously dropped these).
  _appendIntelligenceWidgets(cardMain, track);

  if (track.totalTime > 0 && cues.length > 0) {
    const tl = document.createElement('div');
    tl.className = 'timeline';
    for (const cue of cues) {
      const pct = (cue.posSec / track.totalTime) * 100;
      const m = document.createElement('div');
      m.className = 'timeline-marker';
      m.style.left = `${pct}%`;
      const c = pickCueColor(cue);
      m.style.background = `rgb(${c.r},${c.g},${c.b})`;
      m.style.color = `rgb(${c.r},${c.g},${c.b})`;
      tl.appendChild(m);
    }
    if (nowPlayingId === track.id) {
      const playhead = document.createElement('div');
      playhead.className = 'timeline-playhead';
      playhead.style.left = `${track.totalTime ? (audioPlayer.currentTime / track.totalTime) * 100 : 0}%`;
      tl.appendChild(playhead);
    }
    cardMain.appendChild(tl);
  }

  // F5: Pending cue preview bar (shown after Preview Cues, cleared after Apply)
  const pending = pendingCues[String(track.id)];
  if (pending && pending.length > 0 && track.totalTime > 0) {
    const label = document.createElement('div');
    label.style.cssText = 'font-size:10px;color:var(--muted);margin-top:6px;margin-bottom:2px;text-transform:uppercase;letter-spacing:.05em;';
    label.textContent = 'Preview (pending apply)';
    cardMain.appendChild(label);
    const ptl = document.createElement('div');
    ptl.className = 'timeline';
    ptl.style.opacity = '0.75';
    for (const cue of pending) {
      const pct = (cue.posSec / track.totalTime) * 100;
      const m = document.createElement('div');
      m.className = 'timeline-marker';
      m.style.left = `${pct}%`;
      // Memory cues (slot=-1) get white; hot cues use slot color
      const c = cue.slot === -1 ? { r: 220, g: 220, b: 220 } : pickCueColor(cue);
      m.style.background = `rgb(${c.r},${c.g},${c.b})`;
      m.style.color = `rgb(${c.r},${c.g},${c.b})`;
      ptl.appendChild(m);
    }
    cardMain.appendChild(ptl);
  }

  cardTop.appendChild(cardMain);
  card.appendChild(cardTop);
  return card;
}

function _computeSettingsFingerprint() {
  var s = getSettings();
  var skipExisting = document.getElementById('skip-existing-cues').checked;
  var mcMode = document.getElementById('memory-cue-mode').value;
  // NOTE: phraseCueState size deliberately NOT in the fingerprint. Surgical
  // per-card updates via _updateTrackCardCues handle phrase-cue arrivals;
  // including phraseTotal here caused the per-batch storm fixed in feat/phrase-storm-orphans.
  console.assert(parsedTracksById.size === parsedTracks.length, 'parsedTracksById drift');
  return s.barsInterval + '|' + s.startBar + '|' + s.maxCues + '|' + skipExisting + '|' + mcMode + '|' + analysisMode + '|' + Object.keys(pendingCues).length + '|' + Object.keys(healthData).length;
}

// Surgical per-card update — used by loadPhraseFromServer to refresh ONE card
// without rebuilding the library. The card must already be mounted (visible);
// off-screen / filtered-out tracks pick up new cues on their next natural
// render via the standard renderTracks() path (which reads phraseCueState).
function _updateTrackCardCues(trackId) {
  const tid = String(trackId);
  const track = parsedTracksById.get(tid);
  if (!track) return;
  const skipExisting = document.getElementById('skip-existing-cues')?.checked;
  const willSkip = !!(skipExisting && track.existingHotCues > 0);
  // Replay the cue computation from renderTracks's inner computeCues — limited to
  // the phrase-mode branch since this entry point only fires while phrase cues land.
  // Skipped cards get NO auto-cue badges (cues stays []) — they only rebuild to
  // surface the phrase structure strip, which buildTrackCard reads straight
  // from phraseCueState in its willSkip branch.
  let cues = [];
  if (!willSkip && analysisMode === 'phrase' && phraseCueState[track.id]?.length) {
    cues = phraseCueState[track.id].map(c => ({
      slot: c.slot, posSec: c.position_ms / 1000,
      label: c.label, isPhrase: true, name: c.name || '',
      confidence: c.confidence ?? 1.0, phraseMode: 'phrase',
      phraseBars: c.phrase_bars ?? 0,
    }));
  }
  // Nothing new to show: non-skipped card with no cues yet, OR skipped card
  // whose phrase data hasn't loaded (the strip would be empty).
  const hasPhrase = !!(phraseCueState[track.id]?.length);
  if (willSkip ? !hasPhrase : !cues.length) return;

  // Lazily-landed phrase data swaps the card under the reader's eyes — fade
  // the new strip/badges in (transform+opacity only: card height untouched,
  // virtualizer fixed-height invariant safe).
  const _fadeFreshCueUI = (card) => {
    if (_prefersReducedMotion) return card;
    card.querySelectorAll('.phrase-strip, .cue-slots').forEach(el => el.classList.add('fade-in-up'));
    return card;
  };

  // Album mode (or any non-virtualized render): patch via _cardMap.
  const albumCard = _cardMap.get(tid);
  if (albumCard && albumCard.parentNode) {
    const newCard = _fadeFreshCueUI(buildTrackCard(track, cues, willSkip, {}));
    albumCard.parentNode.replaceChild(newCard, albumCard);
    _cardMap.set(tid, newCard);
    return;
  }

  // Flat-list (virtualized): find the live node by track-id in the visible
  // index. Off-screen tracks pick up cues on their next natural render.
  if (Virtualizer.isAttached()) {
    const visMap = Virtualizer._visibleNodes();
    let targetIdx = null, targetNode = null;
    visMap.forEach(function(node, idx) {
      if (targetNode === null && node.dataset.trackId === tid) {
        targetNode = node; targetIdx = idx;
      }
    });
    if (targetNode && targetNode.parentNode) {
      const baseTransform = targetNode.style.transform || '';
      const newCard = _fadeFreshCueUI(buildTrackCard(track, cues, willSkip, {}));
      newCard.style.position = 'absolute';
      newCard.style.left = '0';
      newCard.style.right = '0';
      newCard.style.top = '0';
      newCard.style.transform = baseTransform;
      targetNode.parentNode.replaceChild(newCard, targetNode);
      visMap.set(targetIdx, newCard);
    }
  }
}

function renderTracks() {
  const { barsInterval, startBar, maxCues } = getSettings();
  const skipExisting = document.getElementById('skip-existing-cues').checked;
  const list = document.getElementById('track-list');
  if (!parsedTracks.length) {
    if (Virtualizer.isAttached()) Virtualizer.detach();
    list.classList.remove('virtualized');
    list.innerHTML = '';
    _cardMap.clear(); _albumGroupCache.clear(); _cardSettingsFingerprint = '';
    const empty = document.createElement('div');
    empty.className = 'empty-state fade-in-up'; // ease in — the list blanks first
    const icon = document.createElement('div');
    icon.className = 'empty-state-icon';
    icon.textContent = '♪';
    const title = document.createElement('div');
    title.className = 'empty-state-title';
    title.textContent = activePlaylistId != null ? 'No tracks in this playlist' : 'No library loaded';
    const sub = document.createElement('div');
    sub.className = 'empty-state-sub';
    sub.textContent = activePlaylistId != null
      ? 'Switch to a different playlist or load your Rekordbox library.'
      : 'Start the local server and reload, or drop a Rekordbox XML file above.';
    empty.appendChild(icon);
    empty.appendChild(title);
    empty.appendChild(sub);
    list.appendChild(empty);
    return;
  }
  let totalCues = 0, tracksWithCues = 0;

  function computeCues(track) {
    if (skipExisting && track.existingHotCues > 0) return [];
    let cues;
    if (analysisMode === 'phrase' && phraseCueState[track.id]?.length) {
      cues = phraseCueState[track.id].map(c => ({
        slot: c.slot, posSec: c.position_ms / 1000,
        label: c.label, isPhrase: true, name: c.name || '',
        confidence: c.confidence ?? 1.0, phraseMode: 'phrase',
        phraseBars: c.phrase_bars ?? 0,
      }));
    } else {
      cues = generateCues(track, barsInterval, startBar, maxCues).map(c => ({
        ...c,
        hasPhrase: !!(track.has_phrase),
      }));
    }
    const mcMode = document.getElementById('memory-cue-mode').value;
    if (mcMode !== 'none' && cues.length) {
      const hotCues = cues.filter(c => c.slot !== -1);
      const loadPos = analysisMode === 'phrase' && hotCues.length
        ? Math.min(...hotCues.map(c => c.posSec))
        : 0;
      const memCues = [{ slot: -1, posSec: loadPos, label: '', name: 'Load Point', color_id: 0 }];
      if (mcMode === 'all' && analysisMode === 'phrase') {
        // Mix-In: slot-0 hot cue (the mix-in point)
        const mixIn = hotCues.find(c => c.slot === 0);
        if (mixIn && Math.abs(mixIn.posSec - loadPos) > 0.5) {
          memCues.push({ slot: -1, posSec: mixIn.posSec, label: '', name: 'Mix In', color_id: 5 });
        }
        // Mix-Out: last OUTRO cue
        const outros = hotCues.filter(c => c.label === 'Outro');
        if (outros.length) {
          const outroPos = Math.max(...outros.map(c => c.posSec));
          memCues.push({ slot: -1, posSec: outroPos, label: '', name: 'Mix Out', color_id: 3 });
        }
      }
      memCues.sort((a, b) => a.posSec - b.posSec);
      cues = [...memCues, ...cues];
    }
    return cues;
  }

  const sorted = sortedTracks();

  if (!sorted.length) {
    if (Virtualizer.isAttached()) Virtualizer.detach();
    list.classList.remove('virtualized');
    list.innerHTML = '';
    _cardMap.clear(); _albumGroupCache.clear(); _cardSettingsFingerprint = '';
    const empty = document.createElement('div');
    empty.className = 'empty-state fade-in-up'; // ease in when a keystroke crosses the 0-results boundary
    const icon = document.createElement('div');
    icon.className = 'empty-state-icon';
    icon.textContent = '⊘';
    const title = document.createElement('div');
    title.className = 'empty-state-title';
    title.textContent = 'No tracks match';
    const sub = document.createElement('div');
    sub.className = 'empty-state-sub';
    sub.textContent = 'Try adjusting your search or clearing the active filters.';
    empty.appendChild(icon);
    empty.appendChild(title);
    empty.appendChild(sub);
    list.appendChild(empty);
    return;
  }

  if (currentSort.by === 'album') {
    // Album mode is variable-height (album header chrome) — not virtualizable.
    // Drop the virtualizer if we just switched from flat mode.
    if (Virtualizer.isAttached()) Virtualizer.detach();
    list.classList.remove('virtualized');
    const newFingerprint = _computeSettingsFingerprint();
    const newSortKey = sorted.map(t => t.id).join(',');
    const settingsChanged = newFingerprint !== _cardSettingsFingerprint;
    const orderChanged = newSortKey !== _albumSortKey;

    if (settingsChanged) {
      _cardSettingsFingerprint = newFingerprint;
      _cardMap.clear();
      // #172: settings (e.g. analysis mode, max cues) invalidate the cached
      // header DOM via the track cards they wrap, so drop the album-group
      // cache here too. Filter-only changes (which do NOT bump the
      // fingerprint) keep the cache hot.
      _albumGroupCache.clear();
    }
    _albumSortKey = newSortKey;

    // Group consecutive tracks by album name
    const groups = new Map();
    for (const track of sorted) {
      const key = track.album || '';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(track);
    }

    // Only rebuild DOM when something actually changed
    if (settingsChanged || orderChanged || !list.firstChild) {
      list.innerHTML = '';
      // Track which cache entries we still need so we can evict stale ones.
      const usedCacheKeys = new Set();
      for (const [albumName, tracks] of groups) {
        // Count cues for this group
        for (const track of tracks) {
          const cues = computeCues(track);
          if (cues.length) { tracksWithCues++; totalCues += cues.length; }
        }

        // #172: cache key = album name + member track ids in order. If a
        // filter change leaves an album fully intact, we reuse the prior
        // <div.album-group> verbatim — header text, artwork chain, and
        // mounted track cards stay put.
        const cacheKey = albumName + '|' + tracks.map(t => t.id).join(',');
        usedCacheKeys.add(cacheKey);
        let group = _albumGroupCache.get(cacheKey);
        if (group) {
          list.appendChild(group);
          continue;
        }

        group = document.createElement('div');
        group.className = 'album-group';

        // Album header
        const header = document.createElement('div');
        const isOpen = expandedAlbums.has(albumName);
        header.className = 'album-header' + (isOpen ? ' open' : '');

        // Album art (from first track that has one)
        const artBox = document.createElement('div');
        artBox.className = 'album-art-lg';
        if (localMode && tracks.length) {
          const ph = document.createElement('span');
          ph.textContent = '♪';
          artBox.appendChild(ph);
          let artIdx = 0;
          function tryNextArt() {
            if (artIdx >= tracks.length) return;
            const img = document.createElement('img');
            img.src = `/api/tracks/${tracks[artIdx++].id}/artwork`;
            img.onload = () => { ph.remove(); artBox.appendChild(img); };
            img.onerror = tryNextArt;
          }
          tryNextArt();
        }
        header.appendChild(artBox);

        // Info: album name + artist
        const info = document.createElement('div');
        info.className = 'album-header-info';
        const nameDiv = document.createElement('div');
        nameDiv.className = 'album-header-name';
        nameDiv.textContent = albumName || 'Unknown Album';
        info.appendChild(nameDiv);
        const artists = [...new Set(tracks.map(t => t.artist).filter(Boolean))];
        if (artists.length) {
          const sub = document.createElement('div');
          sub.className = 'album-header-sub';
          sub.textContent = artists.slice(0, 3).join(', ') + (artists.length > 3 ? '…' : '');
          info.appendChild(sub);
        }
        header.appendChild(info);

        // Right side: track count + chevron
        const right = document.createElement('div');
        right.className = 'album-header-right';
        const count = document.createElement('span');
        count.className = 'album-track-count';
        count.textContent = `${tracks.length} track${tracks.length !== 1 ? 's' : ''}`;
        right.appendChild(count);
        const chev = document.createElement('span');
        chev.className = 'album-chevron';
        chev.textContent = '▶';
        right.appendChild(chev);
        header.appendChild(right);

        // Track list (shown when expanded) — reuse cached cards when possible
        const tracksDiv = document.createElement('div');
        tracksDiv.className = 'album-tracks' + (isOpen ? ' open' : '');
        for (const track of tracks) {
          const tid = String(track.id);
          let card = _cardMap.get(tid);
          if (!card) {
            const willSkip = skipExisting && track.existingHotCues > 0;
            card = buildTrackCard(track, computeCues(track), willSkip, { hideAlbum: true });
            _cardMap.set(tid, card);
          }
          tracksDiv.appendChild(card);
        }

        header.addEventListener('click', () => {
          const opening = !expandedAlbums.has(albumName);
          if (opening) expandedAlbums.add(albumName); else expandedAlbums.delete(albumName);
          header.classList.toggle('open', opening);
          if (opening) { _slideOpen(tracksDiv, 'open'); } else { _slideClose(tracksDiv, 'open'); }
        });

        group.appendChild(header);
        group.appendChild(tracksDiv);
        _albumGroupCache.set(cacheKey, group);
        list.appendChild(group);
      }
      // Evict cache entries that no longer correspond to a visible album —
      // keeps the cache bounded by the number of distinct filtered slices
      // we've rendered, not by lifetime of the page.
      for (const k of _albumGroupCache.keys()) {
        if (!usedCacheKeys.has(k)) _albumGroupCache.delete(k);
      }
    } else {
      // Nothing changed — just tally cues for the counter
      for (const [, tracks] of groups) {
        for (const track of tracks) {
          const cues = computeCues(track);
          if (cues.length) { tracksWithCues++; totalCues += cues.length; }
        }
      }
    }
  } else {
    // --- Virtualized flat list (TASK-032/034/035) ---
    // _cardMap is the album-mode cache only; flat mode keeps live nodes
    // inside Virtualizer._visibleNodes(). The recycle pool caps mounted DOM
    // at ~viewport+buffer cards.
    //
    // Album-mode DOM (built by the `if (currentSort.by === 'album')` branch
    // above) is invisible to Virtualizer.attach() — it would render the flat
    // window on top of the orphan .album-group children and never recover the
    // memory. Clear it explicitly on the album → flat transition. (Issue #114.)
    if (list.querySelector('.album-group')) {
      list.innerHTML = '';
      _cardMap.clear();
      _albumGroupCache.clear();
    }
    list.classList.add('virtualized');
    const newFingerprint = _computeSettingsFingerprint();
    const settingsChanged = newFingerprint !== _cardSettingsFingerprint;

    // Cue totals are summed across the full sorted list (cheap; no DOM touch).
    for (const track of sorted) {
      const cues = computeCues(track);
      if (cues.length) { tracksWithCues++; totalCues += cues.length; }
    }

    // FLIP snapshot must happen BEFORE the re-attach; bound to currently
    // visible nodes only (off-screen movements are invisible anyway).
    const prevVisible = Virtualizer.isAttached() ? Virtualizer._visibleNodes() : null;
    const snapshots = new Map();
    let exitCount = 0;
    if (prevVisible && !settingsChanged && !_prefersReducedMotion) {
      const newSet = new Set();
      for (const t of sorted) newSet.add(String(t.id));
      prevVisible.forEach(function(node) {
        const tid = node.dataset.trackId;
        if (!tid) return;
        if (!newSet.has(tid)) { exitCount++; return; }
        snapshots.set(tid, node.getBoundingClientRect().top);
      });
    }
    const animateTransitions = !settingsChanged && !_prefersReducedMotion && exitCount <= 30 && snapshots.size > 0;

    if (settingsChanged) {
      _cardSettingsFingerprint = newFingerprint;
      _cardMap.clear();
      _albumGroupCache.clear();
      if (Virtualizer.isAttached()) Virtualizer.detach();
    }

    // (Re)build lazy observers BEFORE attach so onWindowChange can wire
    // newly-rendered cards immediately on first render.
    if (localMode) {
      if (_sparkObserver) _sparkObserver.disconnect();
      _sparkObserver = new IntersectionObserver(function(entries) {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            _sparkObserver.unobserve(entry.target);
            _renderEnergySparkline(entry.target);
          }
        }
      }, { rootMargin: '200px' });

      if (_mixObserver) _mixObserver.disconnect();
      _mixObserver = new IntersectionObserver(function(entries) {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            _mixObserver.unobserve(entry.target);
            if (entry.target._isCategoryChip) {
              _renderCategoryChip(entry.target);
            } else {
              _renderMixabilityChip(entry.target, entry.target._breakdown);
            }
          }
        }
      }, { rootMargin: '200px' });
    }

    const renderItem = function(index, recycledNode) {
      const track = sorted[index];
      if (!track) return null;
      const cues = computeCues(track);
      const willSkip = skipExisting && track.existingHotCues > 0;
      // Rebuilding the card subtree per render is simpler than 20-field surgical
      // updates and still wins big: only ~viewport+buffer cards exist at all.
      const card = buildTrackCard(track, cues, willSkip, {});
      const isSelected = selectedTrackIds.has(track.id) || selectedTrackIds.has(String(track.id));
      card.classList.toggle('selected', isSelected);
      if (recycledNode && recycledNode.parentNode) {
        recycledNode.parentNode.replaceChild(card, recycledNode);
      }
      return card;
    };

    const onWindowChange = function(_first, _last, visibleMap) {
      if (!localMode) return;
      // IntersectionObserver.observe() is idempotent on the same target —
      // safe to call repeatedly as the window shifts.
      visibleMap.forEach(function(card) {
        if (!card || !card.querySelector) return;
        const spark = card.querySelector('.energy-sparkline');
        if (spark && _sparkObserver) _sparkObserver.observe(spark);
        if (_mixObserver) {
          const mixEls = card.querySelectorAll('.mix-score-chip[data-track-id], .category-chip[data-track-id]');
          for (let i = 0; i < mixEls.length; i++) _mixObserver.observe(mixEls[i]);
        }
      });
      // Lazy phrase-cue loading for the visible window (phrase mode only).
      // Replaces the eager full-library pass; fetches just what's on screen.
      _queuePhraseLazyLoad(visibleMap);
    };

    // Reattach every render: the renderItem closure captures `sorted` so we
    // need a fresh closure whenever the order/filter changes. The pool +
    // visible-window math itself is bounded — re-attach is O(viewport).
    if (Virtualizer.isAttached()) Virtualizer.detach();
    Virtualizer.attach({
      container: list,
      itemHeight: CARD_HEIGHT_PX,
      totalCount: sorted.length,
      renderItem: renderItem,
      onWindowChange: onWindowChange,
      scrollSource: 'window',
      // Snap the first visible card to align with the sticky filter bar's
      // bottom edge. Without this, when the sticky pins to the viewport,
      // the first virtualized card flows naturally under it — the user
      // sees only the card's bottom slice (the cue-warning row) poking
      // out below the sticky, looking like an orphan row floating above
      // the next full card. Regression spec: tests/e2e/1-sticky-overlap.
      topOcclusionFn: function() {
        var sticky = document.getElementById('tracks-sticky');
        return sticky ? sticky.getBoundingClientRect().bottom : 0;
      },
    });

    // FLIP for visible-only reorders: nodes that survived the re-attach and
    // changed position get a transform animation. Composes with the inline
    // translateY by sandwiching `translateY(delta)` → `translateY(0)`.
    if (animateTransitions) {
      const flipDeltas = [];
      Virtualizer._visibleNodes().forEach(function(card) {
        const tid = card.dataset.trackId;
        if (!tid || !snapshots.has(tid)) return;
        const newTop = card.getBoundingClientRect().top;
        const delta = snapshots.get(tid) - newTop;
        if (Math.abs(delta) > 0.5) flipDeltas.push({ card: card, delta: delta });
      });
      for (const { card, delta } of flipDeltas) {
        const baseTransform = card.style.transform || '';
        card.animate(
          [
            { transform: `${baseTransform} translateY(${delta}px)` },
            { transform: `${baseTransform} translateY(0)` },
          ],
          { duration: 250, easing: 'ease-out' }
        );
      }
    }
  }

  const visibleIndices = filteredTracks();
  const totalFiltered = visibleIndices.length;
  const totalAll = parsedTracks.length;
  const countLabel = totalFiltered === totalAll
    ? `${totalAll} track${totalAll !== 1 ? 's' : ''}`
    : `${totalFiltered} of ${totalAll} track${totalAll !== 1 ? 's' : ''}`;
  var _countEl = document.getElementById('tracks-count');
  if (_countEl.textContent !== countLabel) {
    _countEl.textContent = countLabel;
    requestAnimationFrame(function() {
      _countEl.classList.remove('count-pop');
      void _countEl.offsetWidth;
      _countEl.classList.add('count-pop');
    });
  }
  document.getElementById('dl-summary').textContent =
    `${tracksWithCues} track${tracksWithCues !== 1 ? 's' : ''} · ${totalCues} cue${totalCues !== 1 ? 's' : ''}`;
  updateSelectionBar();

  // Flat-list mode wires its lazy observers via Virtualizer.onWindowChange,
  // so the post-render observer pass below only fires in album mode.
  if (localMode && !Virtualizer.isAttached()) {
    if (_sparkObserver) { _sparkObserver.disconnect(); }
    _sparkObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          _sparkObserver.unobserve(entry.target);
          _renderEnergySparkline(entry.target);
        }
      }
    }, { rootMargin: '200px' });
    for (const el of list.querySelectorAll('.energy-sparkline')) {
      _sparkObserver.observe(el);
    }

    if (_mixObserver) { _mixObserver.disconnect(); }
    _mixObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          _mixObserver.unobserve(entry.target);
          if (entry.target._isCategoryChip) {
            _renderCategoryChip(entry.target);
          } else {
            _renderMixabilityChip(entry.target, entry.target._breakdown);
          }
        }
      }
    }, { rootMargin: '200px' });
    for (const el of list.querySelectorAll('.mix-score-chip[data-track-id], .category-chip[data-track-id]')) {
      _mixObserver.observe(el);
    }
  }

  // A8: prune _cardMap of trackIds whose card is no longer in the DOM. Bounds
  // memory across long sessions where filters / playlist swaps thin the list.
  // Only meaningful in album mode — flat (virtualized) mode doesn't use _cardMap.
  if (_cardMap.size > 0 && !Virtualizer.isAttached()) {
    const visibleIds = new Set(
      Array.from(list.querySelectorAll('.track-card[data-track-id]'))
        .map(el => el.dataset.trackId)
    );
    for (const id of Array.from(_cardMap.keys())) {
      if (!visibleIds.has(String(id))) _cardMap.delete(id);
    }
  }
}

// ── XML export ─────────────────────────────────────────────────────────────────
function buildOutputXml() {
  if (!parsedDoc || !parsedTracks.length) return '';
  const { barsInterval, startBar, maxCues } = getSettings();
  const skipExisting = document.getElementById('skip-existing-cues').checked;
  const outDoc = parsedDoc.cloneNode(true);

  for (const track of parsedTracks) {
    if (skipExisting && track.existingHotCues > 0) continue;

    let cues = [];
    if (analysisMode === 'phrase' && phraseCueState[track.id]?.length) {
      cues = phraseCueState[track.id].map(c => ({
        slot: c.slot, posSec: c.position_ms / 1000, label: c.label, isPhrase: true,
        name: c.name || '', confidence: c.confidence ?? 1.0,
      }));
    } else {
      cues = generateCues(track, barsInterval, startBar, maxCues);
    }

    const trackEl = outDoc.querySelector(`COLLECTION > TRACK[TrackID="${track.id}"]`);
    if (!trackEl) continue;

    const usedSlots = new Set(cues.map(c => c.slot));
    for (const pm of [...trackEl.querySelectorAll('POSITION_MARK')]) {
      const num = parseInt(pm.getAttribute('Num'), 10);
      if (num >= 0 && usedSlots.has(num)) pm.remove();
    }
    for (const cue of cues) {
      if (cue.slot < 0) continue;  // memory cues have no POSITION_MARK representation
      const pm = outDoc.createElement('POSITION_MARK');
      const cueName = cue.name || cue.label || `Bar ${startBar + cue.slot * barsInterval}`;
      pm.setAttribute('Name',  cueName);
      pm.setAttribute('Type',  '0');
      pm.setAttribute('Start', cue.posSec.toFixed(3));
      pm.setAttribute('Num',   String(cue.slot));
      const c = (cue.isPhrase && PHRASE_COLORS[cue.label])
        ? PHRASE_COLORS[cue.label]
        : (CUE_COLORS[cue.slot] ?? CUE_COLORS[0]);
      pm.setAttribute('Red',   String(c.r));
      pm.setAttribute('Green', String(c.g));
      pm.setAttribute('Blue',  String(c.b));
      trackEl.appendChild(pm);
    }
  }

  const serializer = new XMLSerializer();
  return '<?xml version="1.0" encoding="UTF-8"?>\n' +
    serializer.serializeToString(outDoc).replace(/^<\?xml[^?]*\?>\n?/, '');
}

// ── File load (XML) ────────────────────────────────────────────────────────────
function showDropError(msg) {
  document.querySelector('#drop-zone .drop-error')?.remove();
  const el = document.createElement('p');
  el.className = 'drop-error';
  el.textContent = msg;
  document.getElementById('drop-zone').appendChild(el);
}

function handleFile(file) {
  if (!file) return;
  document.querySelector('#drop-zone .drop-error')?.remove();
  const reader = new FileReader();
  reader.onload = e => {
    const result = parseRekordboxXml(e.target.result);
    if (result.error) { showDropError(result.error); return; }

    const { doc, tracks } = result;
    parsedDoc    = doc;
    _setParsedTracks(tracks);
    _energyCache = {};           // D4 fix: clear stale curves on XML reload
    _cardMap.clear();            // C: force full rebuild on XML reload
    _albumGroupCache.clear();    // #172: album cache wraps cards, drop when cards drop
    _cardSettingsFingerprint = '';
    if (Virtualizer.isAttached()) Virtualizer.detach();
    originalXmlText = e.target.result;

    const withExisting = tracks.filter(t => t.existingHotCues > 0).length;
    const info = document.getElementById('existing-cues-info');
    if (withExisting > 0) {
      document.getElementById('existing-cues-label').innerHTML =
        `<strong>${withExisting}</strong> of ${tracks.length} tracks already have hot cues`;
      info.style.display = 'flex';
    } else {
      info.style.display = 'none';
    }

    document.getElementById('settings-section').classList.add('visible');
    document.getElementById('tracks-section').classList.add('visible');
    document.getElementById('download-bar').classList.add('visible');
    document.getElementById('audio-drop-section').classList.add('visible');
    // Trigger sticky shadow check now that the section is visible
    requestAnimationFrame(() => { if (window._checkStickyHeader) window._checkStickyHeader(); });
    document.getElementById('backup-bar').style.display = '';
    document.getElementById('analysis-mode-bar').style.display = 'flex';

    setStep(3);
    renderTracks();
    updateOverwriteWarning();
  };
  reader.readAsText(file);
}

// ── Steps ──────────────────────────────────────────────────────────────────────
function setStep(n) {
  [1,2,3,4].forEach(i => {
    const el = document.getElementById(`step-${i}`);
    el.classList.remove('active', 'done');
    if (i < n) el.classList.add('done');
    if (i === n) el.classList.add('active');
  });
}

// ── Toast stack ────────────────────────────────────────────────────────────────
// type: true = error, 'success' = green success pill, falsy = neutral.
function showToast(msg, type) {
  const stack = document.getElementById('toast-stack');
  if (!stack) return;
  // Cap stack at 3 — dismiss oldest first
  while (stack.children.length >= 3) _dismissToast(stack.firstChild);
  const el = document.createElement('div');
  el.className = 'toast-item' + (type === 'success' ? ' toast-success' : type ? ' toast-error' : '');
  el.textContent = msg;
  stack.appendChild(el);
  const timer = setTimeout(() => _dismissToast(el), 2800);
  el.addEventListener('click', () => { clearTimeout(timer); _dismissToast(el); });
}
function _dismissToast(el) {
  if (!el || !el.parentNode) return;
  el.classList.add('toast-out');
  el.addEventListener('animationend', () => el.remove(), { once: true });
}

// ── Styled confirm dialog ───────────────────────────────────────────────────────
// Async replacement for window.confirm. Mirrors the duplicates-delete modal's
// safety choreography: primary disabled 250ms after open (defeats accidental
// Enter), Cancel default-focused, two-button focus trap, Esc/backdrop cancel.
// opts: { confirmLabel?: string, danger?: boolean }
function _confirmDialog(message, opts = {}) {
  return new Promise((resolve) => {
    document.getElementById('app-confirm-modal')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'app-confirm-modal';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1300;display:flex;align-items:center;justify-content:center;';
    const box = document.createElement('div');
    box.className = 'fade-in-up';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.style.cssText = 'background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;min-width:340px;max-width:460px;';
    const msg = document.createElement('div');
    msg.style.cssText = 'font-size:13px;line-height:1.6;margin-bottom:16px;white-space:pre-line;';
    msg.textContent = message;
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;';
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'secondary-btn';
    cancelBtn.textContent = 'Cancel';
    const goBtn = document.createElement('button');
    goBtn.className = 'primary';
    goBtn.textContent = opts.confirmLabel || 'Confirm';
    if (opts.danger) goBtn.style.cssText = 'background:var(--danger);border-color:var(--danger);color:#fff;';
    goBtn.disabled = true;
    setTimeout(() => { goBtn.disabled = false; }, 250);
    const prevFocus = document.activeElement;
    const done = (val) => {
      document.removeEventListener('keydown', onKey, true);
      overlay.remove();
      if (prevFocus && typeof prevFocus.focus === 'function') { try { prevFocus.focus(); } catch (_) {} }
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false); }
      else if (e.key === 'Tab') {
        e.preventDefault();
        (document.activeElement === cancelBtn ? goBtn : cancelBtn).focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    cancelBtn.addEventListener('click', () => done(false));
    goBtn.addEventListener('click', () => done(true));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) done(false); });
    row.appendChild(cancelBtn);
    row.appendChild(goBtn);
    box.appendChild(msg);
    box.appendChild(row);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    setTimeout(() => cancelBtn.focus(), 0);
  });
}

// ── Human-readable fetch error messages ────────────────────────────────────────
function _humanFetchError(err) {
  const msg = (err && err.message) || String(err);
  if (msg === 'Failed to fetch' || msg === 'NetworkError when attempting to fetch resource.' || msg.includes('ERR_CONNECTION_REFUSED')) {
    return 'Cannot reach the AutoCue server. Make sure it is running (autocue serve) and try again.';
  }
  if (msg.includes('index is still building') || msg.includes('not ready')) {
    return 'The similarity index is still warming up — please wait a few seconds and try again.';
  }
  if (msg.includes('502') || msg.includes('Bad Gateway')) {
    return 'Server gateway error (502). The server may be restarting — try again shortly.';
  }
  if (msg.includes('503') || msg.includes('Service Unavailable')) {
    return 'Server is temporarily unavailable (503). Try again in a moment.';
  }
  if (msg.includes('timeout') || msg.includes('Timeout')) {
    return 'The request timed out. Your library may be large — try again or reduce the set duration.';
  }
  return msg;
}

// ── Button loading state ────────────────────────────────────────────────────────
function _setBtnLoading(btn, loading, loadingText) {
  if (!btn) return;
  if (loading) {
    if (!btn._origHTML) btn._origHTML = btn.innerHTML;
    btn.disabled = true;
    btn.classList.remove('btn-cancel');
    btn._cancelHandler = null;
    btn.innerHTML = `<span class="btn-spinner"></span>${loadingText || btn.textContent}`;
  } else {
    btn.disabled = false;
    btn.classList.remove('btn-cancel');
    if (btn._cancelHandler) { btn.removeEventListener('click', btn._cancelHandler); btn._cancelHandler = null; }
    if (btn._origHTML !== undefined) { btn.innerHTML = btn._origHTML; delete btn._origHTML; }
  }
}

// ── Cancellable SSE button ──────────────────────────────────────────────────────
// Puts btn into cancel mode: red glow, clickable, fires abort on click.
// beforeAbort (optional): called on click; if it returns false, abort is skipped
//   (use for confirm dialogs — return false to keep the operation running).
// Call _setBtnLoading(btn, false) in finally to restore.
function _setBtnCancellable(btn, progressText, abortCtrl, beforeAbort) {
  if (!btn) return;
  if (!btn._origHTML) btn._origHTML = btn.innerHTML;
  btn.disabled = false;
  btn.classList.add('btn-cancel');
  btn.innerHTML = `✕&nbsp; ${progressText}`;
  if (btn._cancelHandler) btn.removeEventListener('click', btn._cancelHandler);
  btn._cancelHandler = function() {
    // beforeAbort may return a boolean OR a Promise<boolean> (styled confirm
    // dialog). A declined confirm is recovered by the next progress tick,
    // which re-installs this once-handler.
    const verdict = beforeAbort ? beforeAbort() : true;
    if (verdict === false) return;
    if (verdict && typeof verdict.then === 'function') {
      verdict.then(ok => { if (ok) abortCtrl.abort(); });
      return;
    }
    abortCtrl.abort();
  };
  btn.addEventListener('click', btn._cancelHandler, { once: true });
}

// ── Slide accordion animations ─────────────────────────────────────────────────
// _slideOpen / _slideClose work alongside CSS open/visible class toggling.
// The CSS class controls the final display value; JS animates the height+opacity transition.
// Respects prefers-reduced-motion: skips animation entirely so transitionend-based cleanup
// always runs (avoids _slideActive getting permanently stuck).
var _prefersReducedMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

function _slideOpen(el, openClass) {
  openClass = openClass || 'open';
  if (el._slideActive) return;
  el.classList.add(openClass);
  if (_prefersReducedMotion) return;  // instant open — no animation, no lock needed
  el._slideActive = true;
  el.style.overflow = 'hidden';
  el.style.transition = '';
  el.style.height = '';
  var h = el.scrollHeight;            // forces reflow → natural height
  el.style.height = '0px';
  el.style.opacity = '0';
  el.style.transition = 'height 0.24s cubic-bezier(0.4,0,0.2,1), opacity 0.22s ease';
  var done = false;
  function _openCleanup() {
    if (done) return; done = true;
    el.style.height = ''; el.style.overflow = ''; el.style.opacity = ''; el.style.transition = '';
    el._slideActive = false;
  }
  requestAnimationFrame(function() {
    el.style.height = h + 'px';
    el.style.opacity = '1';
    el.addEventListener('transitionend', function handler(e) {
      if (e.propertyName !== 'height') return;
      el.removeEventListener('transitionend', handler);
      _openCleanup();
    });
    setTimeout(_openCleanup, 400);    // safety: fires if transitionend never fires (DOM removal, etc.)
  });
}

function _slideClose(el, openClass, onDone) {
  openClass = openClass || 'open';
  if (el._slideActive || !el.classList.contains(openClass)) {
    if (onDone) onDone();
    return;
  }
  if (_prefersReducedMotion) {        // instant close
    el.classList.remove(openClass);
    if (onDone) onDone();
    return;
  }
  el._slideActive = true;
  el.style.height = el.scrollHeight + 'px';
  el.style.overflow = 'hidden';
  el.style.transition = 'height 0.2s cubic-bezier(0.4,0,0.2,1), opacity 0.18s ease';
  var done = false;
  function _closeCleanup() {
    if (done) return; done = true;
    el.classList.remove(openClass);   // CSS sets display:none
    el.style.height = ''; el.style.overflow = ''; el.style.opacity = ''; el.style.transition = '';
    el._slideActive = false;
    if (onDone) onDone();
  }
  requestAnimationFrame(function() {
    el.style.height = '0px';
    el.style.opacity = '0';
    el.addEventListener('transitionend', function handler(e) {
      if (e.propertyName !== 'height') return;
      el.removeEventListener('transitionend', handler);
      _closeCleanup();
    });
    setTimeout(_closeCleanup, 400);   // safety fallback
  });
}

function _slideToggle(el, openClass) {
  openClass = openClass || 'open';
  if (el.classList.contains(openClass)) { _slideClose(el, openClass); }
  else { _slideOpen(el, openClass); }
}

// ── Tooltip system ──────────────────────────────────────────────────────────────
// Single shared #tooltip element; driven by data-tip attributes.
// 380ms hover delay prevents flicker on fast mouse moves.
// Smart repositioning flips left/up when near viewport edges.
(function() {
  var tip = document.getElementById('tooltip');
  if (!tip) return;
  var _target = null;
  var _delay = null;
  var _tw = 0, _th = 0;        // cached tip size — read once per text change, not per mousemove
  var _lastX = 0, _lastY = 0;  // latest cursor position — used at show-time, not mouseover-time

  function _show(text) {
    tip.textContent = text;
    _tw = tip.offsetWidth + 20;  // read once when text changes; avoids layout thrash on mousemove
    _th = tip.offsetHeight + 12;
    _placeAt(_lastX, _lastY);
    tip.classList.add('tip-visible');
  }
  function _hide() {
    clearTimeout(_delay); _delay = null;
    _target = null;
    tip.classList.remove('tip-visible');
  }
  function _placeAt(mx, my) {
    var vw = window.innerWidth, vh = window.innerHeight;
    var x = mx + 14, y = my + 20;
    if (x + _tw > vw - 6) x = mx - _tw + 6;
    if (y + _th > vh - 6) y = my - _th - 4;
    tip.style.left = Math.max(4, x) + 'px';
    tip.style.top  = Math.max(4, y) + 'px';
  }

  document.addEventListener('mousemove', function(e) {
    _lastX = e.clientX; _lastY = e.clientY;
    if (_target && tip.classList.contains('tip-visible')) _placeAt(_lastX, _lastY);
  });
  document.addEventListener('mouseover', function(e) {
    var el = e.target.closest('[data-tip]');
    if (!el || el === _target) return;
    _hide();
    _target = el;
    _delay = setTimeout(function() {
      var text = el.getAttribute('data-tip');
      if (text) _show(text);  // uses _lastX/_lastY — cursor position at show-time, not entry-time
    }, 380);
  });
  document.addEventListener('mouseout', function(e) {
    if (!_target) return;
    if (e.target.closest('[data-tip]') !== _target) return;
    // Suppress false-positive: cursor moved to a child element (e.g. btn-spinner inside button)
    if (e.relatedTarget && _target.contains(e.relatedTarget)) return;
    _hide();
  });
  document.addEventListener('scroll', _hide, true);
  document.addEventListener('click', _hide, true);
})();

// ── Button ripple ───────────────────────────────────────────────────────────────
// Injects a .btn-ripple span at the click point; animates out and self-removes.
document.addEventListener('click', function(e) {
  var btn = e.target.closest('button.primary, .secondary-btn');
  if (!btn || _prefersReducedMotion) return;
  if (btn.disabled || btn.classList.contains('btn-cancel') || btn.querySelector('.btn-spinner')) return;
  var r = document.createElement('span');
  r.className = 'btn-ripple';
  var rect = btn.getBoundingClientRect();
  r.style.left = (e.clientX - rect.left - 4) + 'px';
  r.style.top  = (e.clientY - rect.top  - 4) + 'px';
  btn.appendChild(r);
  r.addEventListener('animationend', function() { r.remove(); });
});

// ── Keyboard shortcuts ──────────────────────────────────────────────────────────
(function() {
  const overlay = document.getElementById('kbd-overlay');
  const closeBtn = document.getElementById('kbd-close-btn');
  if (!overlay) return;

  function open()  { overlay.classList.add('open'); }
  function close() { overlay.classList.remove('open'); }
  closeBtn && closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  const hintBtn = document.getElementById('kbd-hint-btn');
  hintBtn && hintBtn.addEventListener('click', () => overlay.classList.contains('open') ? close() : open());

  document.addEventListener('keydown', function(e) {
    const tag = (e.target.tagName || '').toUpperCase();
    const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target.isContentEditable;

    if (e.key === 'Escape') {
      if (overlay.classList.contains('open')) { close(); e.preventDefault(); return; }
    }

    if (inInput) return;

    if (e.key === '?') {
      e.preventDefault();
      overlay.classList.toggle('open');
      return;
    }
    // Focus search on /
    if (e.key === '/') {
      const s = document.getElementById('track-search');
      if (s) { e.preventDefault(); s.focus(); s.select(); }
      return;
    }
    // Tab shortcuts: 1/2/3
    if (e.key === '1') { switchTab('cues'); return; }
    if (e.key === '2') { switchTab('library'); return; }
    if (e.key === '3') { switchTab('discover'); return; }
    // Select all visible tracks
    if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
      const visible = document.getElementById('track-list');
      if (visible) {
        e.preventDefault();
        filteredTracks().forEach(i => selectedTrackIds.add(String(parsedTracks[i].id)));
        updateSelectionBar();
        AppState.signal('filters');
      }
      return;
    }
  });
})();

// ── Blob URL cleanup ───────────────────────────────────────────────────────────
function revokeAllBlobUrls() {
  blobUrlsToRevoke.forEach(u => URL.revokeObjectURL(u));
}
window.addEventListener('beforeunload', revokeAllBlobUrls);

// ── AppState subscriptions ─────────────────────────────────────────────────────
// 'filters'  → re-render track list when any filter changes
// 'settings' → re-render + overwrite warning when cue settings change
// 'tracks'   → re-render after data load (tracks replaced/enriched)
//
// Issue #172: a rapid sequence of filter toggles (search-fill, search-clear,
// phrase-on, phrase-off, beats-on) on a large album-mode library produces
// back-to-back renderTracks() rebuilds — each one clears the list, walks all
// tracks, and re-fires the per-album <img> artwork-probe chains. On a 3,775-
// track library this saturates the main thread long enough that the next
// synthetic click event in a Playwright run never gets dispatched within
// 30 s. Debounce the render to the trailing edge so a burst of filter
// signals collapses into one render. The cadence (80 ms) matches the
// existing _scheduleSearchRecompute debounce on #search-input — already
// shipped, no new perceived latency for human users.
var _filtersRenderTimer = null;
AppState.subscribe('filters', function() {
  if (!parsedTracks.length) return;
  if (_filtersRenderTimer) clearTimeout(_filtersRenderTimer);
  _filtersRenderTimer = setTimeout(function() {
    _filtersRenderTimer = null;
    renderTracks();
  }, 80);
});
AppState.subscribe('settings', function() {
  if (parsedTracks.length) { renderTracks(); updateOverwriteWarning(); }
});
AppState.subscribe('tracks', function() {
  renderTracks();
});

// ── Events: XML drop ───────────────────────────────────────────────────────────
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));
dropZone.addEventListener('dragover',  e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  handleFile(e.dataTransfer.files[0]);
});

// ── Events: Audio drop ─────────────────────────────────────────────────────────
const audioDropZone  = document.getElementById('audio-drop-zone');
const audioFileInput = document.getElementById('audio-file-input');
audioFileInput.addEventListener('change', () => handleAudioFiles(audioFileInput.files));
audioDropZone.addEventListener('dragover',  e => { e.preventDefault(); audioDropZone.classList.add('drag-over'); });
audioDropZone.addEventListener('dragleave', () => audioDropZone.classList.remove('drag-over'));
audioDropZone.addEventListener('drop', e => {
  e.preventDefault();
  audioDropZone.classList.remove('drag-over');
  handleAudioFiles(e.dataTransfer.files);
});

// Folder toggle: switch between file and directory picker
let folderMode = false;
document.getElementById('audio-folder-toggle').addEventListener('click', e => {
  e.stopPropagation();
  folderMode = !folderMode;
  if (folderMode) {
    audioFileInput.setAttribute('webkitdirectory', '');
    audioFileInput.removeAttribute('accept');
    e.target.textContent = '🎵 Files';
    e.target.title = 'Switch back to file select mode';
  } else {
    audioFileInput.removeAttribute('webkitdirectory');
    audioFileInput.setAttribute('accept', 'audio/*');
    e.target.textContent = '📁 Folder';
    e.target.title = 'Switch to folder select mode';
  }
});

// ── Events: Settings ───────────────────────────────────────────────────────────
['bars-interval', 'start-bar', 'max-cues'].forEach(id => {
  document.getElementById(id).addEventListener('input', () => {
    AppState.signal('settings');
  });
});
document.getElementById('skip-existing-cues').addEventListener('change', () => {
  AppState.signal('settings');
});

// ── Sort buttons ────────────────────────────────────────────────────────────────
document.querySelectorAll('.sort-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const by = btn.dataset.sort;
    if (currentSort.by === by) {
      currentSort.order = currentSort.order === 'asc' ? 'desc' : 'asc';
    } else {
      currentSort = { by, order: 'asc' };
      if (by === 'album') expandedAlbums.clear();
    }
    localStorage.setItem('ac_sort', JSON.stringify(currentSort));
    document.querySelectorAll('.sort-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.sort === currentSort.by);
      if (b.dataset.sort === currentSort.by) {
        b.textContent = { title: 'Title', artist: 'Artist', album: 'Album', bpm: 'BPM', key: 'Key', rating: 'Rating', plays: 'Plays' }[by]
          + (currentSort.order === 'asc' ? ' ▲' : ' ▼');
      } else {
        b.textContent = { title: 'Title', artist: 'Artist', album: 'Album', bpm: 'BPM', key: 'Key', rating: 'Rating', plays: 'Plays' }[b.dataset.sort];
      }
    });
    AppState.signal('filters');
  });
});

// ── Events: Download ───────────────────────────────────────────────────────────
document.getElementById('download-btn').addEventListener('click', () => {
  if (localMode) { applyToRekordbox(); return; }
  const xml  = buildOutputXml();
  const blob = new Blob([xml], { type: 'text/xml' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url; a.download = 'autocue_import.xml'; a.click();
  URL.revokeObjectURL(url);
  setStep(4);
  document.getElementById('path-warning').classList.add('visible');
});
document.getElementById('path-warning-dismiss').addEventListener('click', () => {
  document.getElementById('path-warning').classList.remove('visible');
});

// ── Events: Delete all cues (local mode only) ──────────────────────────────────
document.getElementById('color-by-bpm-btn').addEventListener('click', colorTracksByBpm);

// F5: Preview cues — calls /api/generate and stores results in pendingCues for card rendering
document.getElementById('preview-cues-btn').addEventListener('click', async () => {
  const btn = document.getElementById('preview-cues-btn');
  btn.textContent = 'Loading…'; btn.disabled = true;
  // Mirror onto the visible action-bar button — #preview-cues-btn is in the
  // settings toolbar, not where the user clicked when using the action bar.
  const abPrev = document.getElementById('action-bar-preview');
  _setBtnLoading(abPrev, true, 'Previewing…');
  const { barsInterval, startBar, maxCues } = getSettings();
  // Issue #173: Preview must target the selection when any cards are
  // checked — otherwise a user with 2 tracks selected sees a toast for
  // 3,775 tracks AND a subsequent Apply silently overwrites the whole
  // visible library. Every other write op (apply/delete/color/download)
  // already calls activeTracks(); Preview was the lone holdout.
  const trackIds = activeTracks().map(t => parseInt(t.id));
  try {
    const r = await fetch('/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        track_ids: trackIds,
        mode: analysisMode === 'phrase' ? 'auto' : 'bar',
        bars_interval: barsInterval, start_bar: startBar, max_cues: maxCues,
        memory_cue_mode: document.getElementById('memory-cue-mode').value,
      }),
      signal: AbortSignal.timeout(60_000),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
    const data = await r.json();
    pendingCues = {};
    for (const tr of data.tracks) {
      pendingCues[String(tr.id)] = tr.cues.map(c => ({
        slot: c.slot, posSec: c.position_ms / 1000,
        label: c.label, isPhrase: c.is_phrase, name: c.name || '',
        confidence: c.confidence ?? 1.0, phraseMode: tr.mode_used,
        phraseBars: c.phrase_bars ?? 0,
      }));
    }
    renderTracks();
    showToast(`Previewing cues for ${data.tracks.length} track(s) — click Apply to write`);
  } catch (e) { showToast(`Preview failed: ${e.message}`, true); }
  finally { btn.textContent = 'Preview cues'; btn.disabled = false; _setBtnLoading(abPrev, false); }
});

document.getElementById('delete-cues-btn').addEventListener('click', () => {
  const total = activeTracks().length;
  if (!total) return;
  const suffix = selectedTrackIds.size > 0
    ? ` (${total} selected)`
    : total < parsedTracks.length ? ` (${total} visible)` : '';
  document.getElementById('delete-confirm-msg').textContent =
    `Delete all hot cues from ${total} track${total !== 1 ? 's' : ''}${suffix}?`;
  document.getElementById('delete-confirm-bar').classList.add('visible');
  document.getElementById('delete-cues-btn').style.display = 'none';
});

document.getElementById('delete-cancel-btn').addEventListener('click', () => {
  document.getElementById('delete-confirm-bar').classList.remove('visible');
  document.getElementById('delete-cues-btn').style.display = '';
});

document.getElementById('delete-confirm-btn').addEventListener('click', async () => {
  const confirmBtn = document.getElementById('delete-confirm-btn');
  const cancelBtn  = document.getElementById('delete-cancel-btn');
  confirmBtn.disabled = true;
  cancelBtn.disabled  = true;
  confirmBtn.textContent = 'Deleting…';
  try {
    const trackIds = activeTracks().map(t => parseInt(t.id));
    const r = await fetch('/api/delete-cues', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: trackIds, dry_run: false }),
      signal: AbortSignal.timeout(60_000),
    });
    const resp = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(resp.detail || r.statusText);
    showToast(`Deleted ${resp.deleted} cues from ${resp.tracks_affected} tracks — backup saved`);
    // Refresh track list so existing_hot_cues counts update
    await loadTracksFromServer();
  } catch (err) {
    showToast(`Delete failed: ${err.message}`);
  } finally {
    confirmBtn.disabled = false;
    cancelBtn.disabled  = false;
    confirmBtn.textContent = 'Confirm delete';
    document.getElementById('delete-confirm-bar').classList.remove('visible');
    document.getElementById('delete-cues-btn').style.display = '';
  }
});

// ── Events: Mini player ────────────────────────────────────────────────────────
const miniScrubber = document.getElementById('mini-scrubber');

audioPlayer.addEventListener('timeupdate', () => {
  // D1: timeline + waveform updates moved to RAF loop; keep only time display + scrubber
  if (!isScrubbing) miniScrubber.value = audioPlayer.currentTime;
  document.getElementById('mini-current-time').textContent = fmtTime(audioPlayer.currentTime);
});

audioPlayer.addEventListener('ended', () => {
  _stopPlayRaf(); // D1
  nowPlayingId = null;
  updatePlaybackUI();
});

audioPlayer.addEventListener('error', () => {
  _stopPlayRaf(); // D1
  showToast('Could not play this file — format may not be supported');
  nowPlayingId = null;
  updatePlaybackUI();
});

miniScrubber.addEventListener('mousedown',  () => { isScrubbing = true; });
miniScrubber.addEventListener('touchstart', () => { isScrubbing = true; }, { passive: true });
miniScrubber.addEventListener('mouseup',  () => { audioPlayer.currentTime = parseFloat(miniScrubber.value); isScrubbing = false; if (nowPlayingId) _drawMiniWaveform(nowPlayingId); });
miniScrubber.addEventListener('touchend', () => { audioPlayer.currentTime = parseFloat(miniScrubber.value); isScrubbing = false; if (nowPlayingId) _drawMiniWaveform(nowPlayingId); });

document.getElementById('mini-play-btn').addEventListener('click', () => {
  if (nowPlayingId) togglePlayTrack(nowPlayingId);
});

// ── Events: Backup ─────────────────────────────────────────────────────────────
document.getElementById('backup-btn').addEventListener('click', () => {
  if (!originalXmlText) return;
  const blob = new Blob([originalXmlText], { type: 'text/xml' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'rekordbox_backup_' + new Date().toISOString().slice(0,10) + '.xml';
  a.click();
  URL.revokeObjectURL(a.href);
});
document.getElementById('backup-inline-btn').addEventListener('click', () => {
  document.getElementById('backup-btn').click();
});

// ── Events: ANLZ drop ──────────────────────────────────────────────────────────
const anlzDropZone  = document.getElementById('anlz-drop-zone');
const anlzFileInput = document.getElementById('anlz-file-input');
anlzFileInput.addEventListener('change', async () => {
  indexAnlzFiles(anlzFileInput.files);
  await runPhraseAnalysis();
});
anlzDropZone.addEventListener('dragover',  e => { e.preventDefault(); anlzDropZone.classList.add('drag-over'); });
anlzDropZone.addEventListener('dragleave', () => anlzDropZone.classList.remove('drag-over'));
anlzDropZone.addEventListener('drop', async e => {
  e.preventDefault();
  anlzDropZone.classList.remove('drag-over');
  indexAnlzFiles(e.dataTransfer.files);
  await runPhraseAnalysis();
});

// ── Events: Analysis mode toggle ───────────────────────────────────────────────
function _applyModeUI(mode) {
  document.getElementById('bar-mode-fields').style.display = mode === 'phrase' ? 'none' : '';
  document.getElementById('always-fields').style.marginTop = mode === 'phrase' ? '0' : '10px';
}
document.getElementById('mode-bar-btn').addEventListener('click', () => {
  analysisMode = 'bar';
  document.getElementById('mode-bar-btn').classList.add('active');
  document.getElementById('mode-phrase-btn').classList.remove('active');
  document.getElementById('anlz-drop-section').style.display = 'none';
  _applyModeUI('bar');
  AppState.signal('filters');
});
document.getElementById('mode-phrase-btn').addEventListener('click', async () => {
  analysisMode = 'phrase';
  document.getElementById('mode-phrase-btn').classList.add('active');
  document.getElementById('mode-bar-btn').classList.remove('active');
  _applyModeUI('phrase');
  if (localMode) {
    // Server already has ANLZ access — no Pyodide or folder drop needed.
    // Phrase cues are now loaded LAZILY per viewport (see
    // _queuePhraseLazyLoad wired into the Virtualizer's onWindowChange),
    // NOT eagerly for the whole library. Just re-render; the visible
    // cards' phrase cues fetch as they scroll into view, so there's no
    // "Computing phrase cues N/M" full-library pass on mode switch.
    document.getElementById('anlz-drop-section').style.display = 'none';
    if (parsedTracks.length) AppState.signal('filters');
  } else {
    document.getElementById('anlz-drop-section').style.display = '';
    loadPyodideEngine(); // start loading in background
    if (parsedTracks.length) renderTracks();
  }
});

// Module-level so Cancel button can reach it.
let _phraseLoadAbort = null;

// ── Lazy viewport-driven phrase-cue loading ─────────────────────────────────
// Instead of eagerly computing phrase cues for the whole library on mode
// switch (the old "Computing phrase cues 300/2789" banner), we fetch only
// the cards currently in the virtualized window, as they scroll into view —
// the same pattern the energy sparkline + mix chips use. _queuePhraseLazyLoad
// is wired into the Virtualizer's onWindowChange; it collects visible tracks
// that have phrase data but no cached cues, debounces, and batch-fetches them.
const _phraseInFlight = new Set();   // track-id strings with a pending fetch
const _phraseLazyQueue = new Set();  // track-id strings waiting for the next batch
let _phraseLazyTimer = null;

function _collectPhraseLazyIds(visibleMap) {
  // Returns the visible track-id strings that need a phrase fetch:
  // phrase mode + local + hasPhrase + not already cached + not in flight.
  const out = [];
  if (analysisMode !== 'phrase' || !localMode) return out;
  visibleMap.forEach(function(card) {
    if (!card || !card.dataset) return;
    const tid = card.dataset.trackId;
    if (!tid) return;
    const track = parsedTracksById.get(tid);
    if (!track || !track.hasPhrase) return;        // server has no phrase data
    if (phraseCueState[tid] !== undefined) return;  // cached (incl. empty [])
    if (_phraseInFlight.has(tid)) return;           // already fetching
    out.push(tid);
  });
  return out;
}

function _queuePhraseLazyLoad(visibleMap) {
  const ids = _collectPhraseLazyIds(visibleMap);
  if (!ids.length) return;
  ids.forEach(tid => _phraseLazyQueue.add(tid));
  // Debounce so a fast scroll coalesces into one batch rather than one
  // request per onWindowChange tick.
  clearTimeout(_phraseLazyTimer);
  _phraseLazyTimer = setTimeout(_flushPhraseLazyQueue, 120);
}

async function _flushPhraseLazyQueue() {
  if (_phraseLazyQueue.size === 0) return;
  const tidStrs = Array.from(_phraseLazyQueue);
  _phraseLazyQueue.clear();
  tidStrs.forEach(tid => _phraseInFlight.add(tid));
  const ids = tidStrs.map(s => parseInt(s));
  try {
    const r = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: ids, mode: 'phrase' }),
    });
    if (!r.ok) throw new Error('phrase fetch ' + r.status);
    const resp = await r.json();
    const seen = new Set();
    for (const result of (resp.tracks || [])) {
      const tid = String(result.id);
      seen.add(tid);
      // Cache even an empty result so a track with no phrase cues isn't
      // re-queued every time it scrolls back into view.
      phraseCueState[tid] =
        (result.mode_used === 'phrase' && result.cues.length)
          ? result.cues.map(c => ({
              position_ms: c.position_ms, label: c.label, slot: c.slot,
              name: c.name || '', confidence: c.confidence ?? 1.0,
              phrase_bars: c.phrase_bars ?? 0,
            }))
          : [];
      _updateTrackCardCues(result.id);
    }
    // Any requested id the server didn't return → cache empty so it won't loop.
    for (const tid of tidStrs) {
      if (!seen.has(tid)) phraseCueState[tid] = [];
    }
  } catch (e) {
    // Best-effort: a failed batch leaves those cards without a phrase strip;
    // clearing in-flight lets them retry on the next scroll into view.
    console.warn('lazy phrase load failed:', e && e.message || e);
  } finally {
    tidStrs.forEach(tid => _phraseInFlight.delete(tid));
  }
}

async function loadPhraseFromServer() {
  // B7 cache short-circuit: if phrase data was already loaded for the current
  // library epoch, skip the network fan-out and just re-render visible cards.
  // Off-screen tracks pick up cues on next natural render (via renderTracks
  // building cards through buildTrackCard which reads phraseCueState).
  if (_phraseLoadedEpoch === _libraryEpoch && Object.keys(phraseCueState).length > 0) {
    for (const tid of _cardMap.keys()) _updateTrackCardCues(tid);
    return;
  }

  const phraseTrackIds = parsedTracks
    .filter(t => t.hasPhrase)
    .map(t => parseInt(t.id));

  if (!phraseTrackIds.length) {
    showToast('No tracks with phrase analysis data found');
    return;
  }

  phraseCueState = {};
  const BATCH = 300;
  const total = phraseTrackIds.length;

  // A6 banner — show, AbortController for Cancel
  _phraseLoadAbort = new AbortController();
  _showPhraseBanner(0, total);

  let networkFailed = false;
  try {
    for (let i = 0; i < total; i += BATCH) {
      const batch = phraseTrackIds.slice(i, i + BATCH);
      const resp = await fetch('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_ids: batch, mode: 'phrase' }),
        signal: _phraseLoadAbort.signal,
      }).then(r => r.json());

      for (const result of (resp.tracks || [])) {
        if (result.mode_used === 'phrase' && result.cues.length) {
          phraseCueState[String(result.id)] = result.cues.map(c => ({
            position_ms: c.position_ms,
            label: c.label,
            slot: c.slot,
            name: c.name || '',
            confidence: c.confidence ?? 1.0,
            phrase_bars: c.phrase_bars ?? 0,
          }));
          // Surgical per-card update — no library-wide rebuild.
          _updateTrackCardCues(result.id);
        }
      }
      _showPhraseBanner(Math.min(i + BATCH, total), total);
    }
    // Mark this epoch as having phrase data loaded for the cache hit on next toggle.
    _phraseLoadedEpoch = _libraryEpoch;
  } catch (err) {
    if (err.name === 'AbortError' && _phraseLoadAbort && _phraseLoadAbort.signal.aborted) {
      showToast('Phrase load cancelled');
    } else {
      networkFailed = true;
      showToast('Phrase load failed: ' + (err.message || 'network error'), true);
    }
  } finally {
    _hidePhraseBanner();
    _phraseLoadAbort = null;
  }

  if (!networkFailed && _phraseLoadAbort === null) {
    const matched = Object.keys(phraseCueState).length;
    if (matched > 0) showToast(`Phrase analysis ready — ${matched} track${matched !== 1 ? 's' : ''}`);
  }
}

function _showPhraseBanner(loaded, total) {
  const b = document.getElementById('phrase-progress-banner');
  if (!b) return;
  b.classList.add('visible');
  const count = document.getElementById('phrase-progress-count');
  if (count) count.textContent = `${loaded} / ${total}`;
  const bar = document.getElementById('phrase-progress-bar');
  if (bar) bar.value = total > 0 ? (loaded / total) * 100 : 0;
}

function _hidePhraseBanner() {
  const b = document.getElementById('phrase-progress-banner');
  if (b) b.classList.remove('visible');
}

document.getElementById('phrase-progress-cancel')?.addEventListener('click', () => {
  if (_phraseLoadAbort) _phraseLoadAbort.abort();
});

// B4: Track info modal — instant open + race-safe lazy probe.
let _infoModalRequestId = 0;
let _infoModalOpen = false;
let _infoModalTrack = null;

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function _fmtDuration(sec) {
  if (!sec) return '—';
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2,'0')}`;
}

function openTrackInfoModal(trackId) {
  const track = parsedTracksById.get(String(trackId));
  if (!track) return;
  const modal = document.getElementById('track-info-modal');
  if (!modal) return;
  _infoModalRequestId++;
  const reqId = _infoModalRequestId;
  _infoModalOpen = true;
  _infoModalTrack = track;

  document.getElementById('ti-title').textContent = track.name || '(no title)';
  document.getElementById('ti-artist').textContent = track.artist || '';
  document.getElementById('ti-album').textContent = track.album || '—';
  document.getElementById('ti-genre').textContent = track.genre || '—';
  document.getElementById('ti-bpm').textContent = track.bpm ? track.bpm.toFixed(2) : '—';
  document.getElementById('ti-key').textContent = track.key || '—';
  document.getElementById('ti-duration').textContent = _fmtDuration(track.totalTime);
  document.getElementById('ti-path').textContent = track.locationFilename || '(server-side path)';

  // Source label: open with Checking… placeholder if we haven't probed yet.
  const sourceCell = document.getElementById('ti-source');
  const helpCell = document.getElementById('ti-source-help');
  const dlBtn = document.getElementById('ti-download');

  function applySource(src) {
    if (src === 'file')      { sourceCell.textContent = 'Local file';       helpCell.textContent = ''; dlBtn.style.display = 'none'; }
    else if (src === 'streaming') { sourceCell.textContent = 'Streaming source';  helpCell.textContent = 'Streaming tracks can\'t be analyzed by AutoCue. Download a real audio file via YouTube to enable phrase analysis.'; dlBtn.style.display = ''; }
    else if (src === 'missing')   { sourceCell.textContent = 'File missing';     helpCell.textContent = 'The file at the path above no longer exists. Restore it, update the path in Rekordbox, or download a replacement.'; dlBtn.style.display = ''; }
    else                      { sourceCell.textContent = 'Unknown';         helpCell.textContent = ''; dlBtn.style.display = 'none'; }
  }

  // Optimistic initial state from server-side source.
  if (track.source === 'streaming') applySource('streaming');
  else if (_audioProbedAt[track.id]) applySource(_audioProbedAt[track.id]);
  else { sourceCell.textContent = 'Checking…'; helpCell.textContent = ''; dlBtn.style.display = 'none'; }

  // YouTube search link.
  const q = `${track.artist || ''} ${track.name || ''}`.trim();
  document.getElementById('ti-youtube-search').href = `https://www.youtube.com/results?search_query=${encodeURIComponent(q)}`;

  modal.classList.add('visible');
  modal.setAttribute('aria-hidden', 'false');

  // Race-safe parallel probe for file-source tracks.
  if (track.source === 'file' && !_audioProbedAt[track.id]) {
    fetch('/api/tracks/check-audio', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: [parseInt(track.id)] }),
    }).then(r => r.json()).then(resp => {
      if (reqId !== _infoModalRequestId || !_infoModalOpen) return;
      const verdict = (resp.results || {})[String(track.id)] || 'unknown';
      _audioProbedAt[track.id] = verdict;
      applySource(verdict);
    }).catch(() => {
      if (reqId !== _infoModalRequestId || !_infoModalOpen) return;
      applySource('unknown');
    });
  }
}

function closeTrackInfoModal() {
  const modal = document.getElementById('track-info-modal');
  if (!modal) return;
  modal.classList.remove('visible');
  modal.setAttribute('aria-hidden', 'true');
  _infoModalOpen = false;
  _infoModalTrack = null;
}
document.getElementById('ti-close')?.addEventListener('click', closeTrackInfoModal);
document.getElementById('track-info-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'track-info-modal') closeTrackInfoModal();
});
document.getElementById('ti-download')?.addEventListener('click', () => {
  if (_infoModalTrack) openYoutubeModal(_infoModalTrack);
});

// B5: YouTube candidate-selection modal.
let _ytModalTrack = null;
let _ytSearchAbort = null;
let _ytDownloadAbort = null;
let _ytModalJob = null;        // in-flight _Download job initiated from a modal Pick
let _ytModalJobToken = 0;      // bumped per _ytDownload call so stale onState from a cancelled job can't null the live handle

function openYoutubeModal(track) {
  // A query-modal Pick may still be in-flight; cancel before reusing the modal
  // for a track-card flow, or its onState writes land in the freshly-reset UI.
  if (_ytModalJob) { try { _ytModalJob.cancel(); } catch (_) {} _ytModalJob = null; }
  _ytModalTrack = track;
  const modal = document.getElementById('yt-modal');
  if (!modal) return;
  document.getElementById('yt-track-label').textContent = `${track.artist || ''} — ${track.name || ''}`.trim();
  const queryInput = document.getElementById('yt-query');
  queryInput.value = `${track.artist || ''} ${track.name || ''}`.trim();
  document.getElementById('yt-candidates').innerHTML = '';
  document.getElementById('yt-status').textContent = '';
  document.getElementById('yt-download-progress').style.display = 'none';
  document.getElementById('yt-result').style.display = 'none';
  document.getElementById('yt-search-btn').disabled = false;
  modal.classList.add('visible');
  modal.setAttribute('aria-hidden', 'false');
  closeTrackInfoModal();
}
function closeYoutubeModal() {
  if (_ytSearchAbort) _ytSearchAbort.abort();
  if (_ytDownloadAbort) _ytDownloadAbort.abort();
  // Cancel any in-flight Pick download initiated via _Download (PRP search→modal flow).
  if (_ytModalJob) { try { _ytModalJob.cancel(); } catch (_) {} _ytModalJob = null; }
  const modal = document.getElementById('yt-modal');
  if (modal) { modal.classList.remove('visible'); modal.setAttribute('aria-hidden', 'true'); }
  _ytModalTrack = null;
}

// Open the YouTube candidate picker for a free-text query (no track object).
// Used by _Download.bindManualPanel when the user types a search term in the
// Download panel — instead of auto-picking yt-dlp's first result (which often
// surfaces a random video for ambiguous queries like "Sampha piona"), the
// candidate list lets the user pick the right version.
function openYoutubeModalForQuery(query) {
  const modal = document.getElementById('yt-modal');
  if (!modal) return;
  // Same in-flight-Pick concern as openYoutubeModal — cancel before re-init.
  if (_ytModalJob) { try { _ytModalJob.cancel(); } catch (_) {} _ytModalJob = null; }
  _ytModalTrack = null;  // no track object — purely query-driven
  const trimmed = (query || '').trim();
  document.getElementById('yt-track-label').textContent = trimmed
    ? `Search: ${trimmed}` : 'Search YouTube';
  const queryInput = document.getElementById('yt-query');
  queryInput.value = trimmed;
  document.getElementById('yt-candidates').innerHTML = '';
  document.getElementById('yt-status').textContent = '';
  document.getElementById('yt-download-progress').style.display = 'none';
  document.getElementById('yt-result').style.display = 'none';
  document.getElementById('yt-search-btn').disabled = false;
  modal.classList.add('visible');
  modal.setAttribute('aria-hidden', 'false');
  // Auto-fire the search so the user lands on the candidate list immediately.
  if (trimmed) {
    setTimeout(() => { try { _ytSearch(); } catch (_) {} }, 0);
  } else {
    queryInput.focus();
  }
}
document.getElementById('yt-close')?.addEventListener('click', closeYoutubeModal);
document.getElementById('yt-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'yt-modal') closeYoutubeModal();
});

async function _ytSearch() {
  const q = document.getElementById('yt-query').value.trim();
  if (!q) return;
  if (_ytSearchAbort) _ytSearchAbort.abort();
  _ytSearchAbort = new AbortController();
  const status = document.getElementById('yt-status');
  const candDiv = document.getElementById('yt-candidates');
  const btn = document.getElementById('yt-search-btn');
  btn.disabled = true;
  status.textContent = 'Searching YouTube…';
  candDiv.innerHTML = '';
  try {
    const resp = await fetch(`/api/youtube/search?q=${encodeURIComponent(q)}&n=5`, { signal: _ytSearchAbort.signal });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    status.textContent = '';
    const targetDur = _ytModalTrack?.totalTime || 0;
    let defaultPicked = false;
    for (const c of (data.candidates || [])) {
      const row = document.createElement('div');
      row.className = 'yt-cand';
      const within = targetDur > 0 && c.duration && Math.abs(c.duration - targetDur) <= targetDur * 0.15;
      if (within && !defaultPicked) { row.classList.add('selected'); defaultPicked = true; }
      row.innerHTML = `
        <div class="yt-cand-text">
          <div class="yt-cand-title">${_esc(c.title)}</div>
          <div class="yt-cand-meta">${_esc(c.channel)} · ${c.duration ? _fmtDuration(c.duration) : '—'}</div>
        </div>
        <button class="primary" type="button">Download</button>`;
      row.querySelector('button').addEventListener('click', () => _ytDownload(c.url));
      candDiv.appendChild(row);
    }
    if (!data.candidates?.length) status.textContent = 'No results.';
  } catch (err) {
    if (err.name === 'AbortError') return;
    status.textContent = `Search failed: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}
document.getElementById('yt-search-btn')?.addEventListener('click', _ytSearch);
document.getElementById('yt-query')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') _ytSearch();
});

// Routed through window._Download (PRD v1.0) so the modal Pick flow honors
// the user's format / normalize / embed_metadata prefs and gets classified
// errors + cancel + 410 already_consumed handling for free.
async function _ytDownload(url) {
  if (_ytModalJob) { try { _ytModalJob.cancel(); } catch (_) {} _ytModalJob = null; }
  // Tag this invocation so a later onState from a cancelled prior job can't
  // null out _ytModalJob after we've already assigned a new one below.
  const myToken = ++_ytModalJobToken;
  const progress = document.getElementById('yt-download-progress');
  const progBar = document.getElementById('yt-progress');
  const progText = document.getElementById('yt-progress-text');
  const result = document.getElementById('yt-result');
  const status = document.getElementById('yt-status');
  progress.style.display = 'flex';
  result.style.display = 'none';
  progBar.value = 0;
  progText.textContent = 'Starting…';
  if (status) status.textContent = '';

  // Read manual-panel prefs so the user's chosen format / normalize / metadata
  // toggles drive the modal-initiated download too.
  let fmt = 'mp3_320';
  try { fmt = localStorage.getItem('autocue_dl_format') || 'mp3_320'; } catch (_) {}
  const normEl = document.getElementById('dl-normalize');
  const metaEl = document.getElementById('dl-embed-meta');

  _ytModalJob = window._Download.start({
    query: url,
    format: fmt,
    normalize: !!(normEl && normEl.checked),
    embedMeta: metaEl ? metaEl.checked : true,
    dest: (typeof _dlDestDir !== 'undefined' && _dlDestDir) || undefined,
    onState: function(ev) {
      if (typeof ev.percent === 'number') progBar.value = ev.percent;
      if (ev.phase) progText.textContent = ({
        queued: 'Queued…',
        fetching: `Downloading… ${Math.round(ev.percent || 0)}%`,
        converting: 'Converting…',
        normalizing_pass1: 'Measuring loudness…',
        normalizing_pass2: `Normalizing… ${Math.round(ev.percent || 0)}%`,
        tagging: 'Writing metadata…',
      })[ev.phase] || ev.phase;
      if (ev.type === 'done') {
        progress.style.display = 'none';
        if (ev.status === 'success' && ev.path) {
          result.style.display = '';
          document.getElementById('yt-result-path').textContent = ev.path;
          document.getElementById('yt-result-path-fallback').value = ev.path;
          document.getElementById('yt-result-path-fallback').style.display = 'none';
        } else if (ev.status === 'error') {
          showToast(`Download failed: ${ev.error_message || 'unknown'}`, true);
        } else if (ev.status === 'cancelled') {
          if (status) status.textContent = 'Cancelled';
        }
        if (_ytModalJobToken === myToken) _ytModalJob = null;
      }
    },
  });
}

// B6: Copy path with clipboard fallback.
document.getElementById('yt-copy-path')?.addEventListener('click', async () => {
  const path = document.getElementById('yt-result-path').textContent;
  try {
    await navigator.clipboard.writeText(path);
    showToast('Path copied');
  } catch {
    const fallback = document.getElementById('yt-result-path-fallback');
    fallback.style.display = 'block';
    fallback.focus();
    fallback.select();
    showToast('Clipboard blocked — select the path manually (Cmd-C / Ctrl-C)');
  }
});

// ── Camelot key filter ─────────────────────────────────────────────────────────
(function initCamelotFilter() {
  const CAMELOT_COLORS = [
    '#e05c97','#e0406c','#d94040','#e0682a',
    '#d4a017','#9ec94a','#52c23a','#29b89e',
    '#2e95d9','#4265db','#7b52d9','#b545d4',
  ];
  // Camelot position 1-12 with actual key names
  const KEY_NAMES_A = ['Ab min','Eb min','Bb min','F min','C min','G min','D min','A min','E min','B min','F# min','C# min'];
  const KEY_NAMES_B = ['B maj','F# maj','Db maj','Ab maj','Eb maj','Bb maj','F maj','C maj','G maj','D maj','A maj','E maj'];

  const grid = document.getElementById('camelot-grid');
  if (!grid) return;

  // Header row
  for (let n = 1; n <= 12; n++) {
    const lbl = document.createElement('div');
    lbl.className = 'ck-label'; lbl.textContent = n;
    grid.appendChild(lbl);
  }
  // A row (inner ring / minor)
  for (let n = 1; n <= 12; n++) {
    const key = `${n}A`;
    const btn = document.createElement('button');
    btn.className = 'ck-btn'; btn.dataset.key = key;
    btn.textContent = key;
    btn.title = KEY_NAMES_A[n - 1];
    btn.style.background = CAMELOT_COLORS[n - 1];
    btn.addEventListener('click', () => toggleKey(key));
    grid.appendChild(btn);
  }
  // B row (outer ring / major)
  for (let n = 1; n <= 12; n++) {
    const key = `${n}B`;
    const btn = document.createElement('button');
    btn.className = 'ck-btn'; btn.dataset.key = key;
    btn.textContent = key;
    btn.title = KEY_NAMES_B[n - 1];
    btn.style.background = CAMELOT_COLORS[n - 1];
    btn.addEventListener('click', () => toggleKey(key));
    grid.appendChild(btn);
  }

  function toggleKey(key) {
    if (selectedKeys.has(key)) selectedKeys.delete(key);
    else selectedKeys.add(key);
    updateKeyUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  }

  function updateKeyUI() {
    document.querySelectorAll('.ck-btn').forEach(b => {
      b.classList.toggle('selected', selectedKeys.has(b.dataset.key));
    });
    const btn = document.getElementById('key-filter-btn');
    if (selectedKeys.size === 0) {
      btn.textContent = 'Key: Any ▾';
      btn.classList.remove('active');
    } else {
      btn.textContent = `Key: ${[...selectedKeys].sort().join(', ')} ▾`;
      btn.classList.add('active');
    }
  }

  document.getElementById('ck-clear-btn').addEventListener('click', () => {
    selectedKeys.clear();
    updateKeyUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  });

  document.getElementById('ck-related-btn').addEventListener('click', () => {
    if (selectedKeys.size === 0) return;
    const toAdd = new Set();
    for (const key of selectedKeys) {
      const m = key.match(/^(\d+)([AB])$/);
      if (!m) continue;
      const n = parseInt(m[1]), ab = m[2];
      toAdd.add(`${n}${ab === 'A' ? 'B' : 'A'}`);
      const prev = n === 1 ? 12 : n - 1;
      const next = n === 12 ? 1 : n + 1;
      toAdd.add(`${prev}${ab}`);
      toAdd.add(`${next}${ab}`);
    }
    for (const k of toAdd) selectedKeys.add(k);
    updateKeyUI();
    AppState.signal('filters');
  });

  // Toggle popup — fixed position, calculated from button rect
  const popup = document.getElementById('key-filter-popup');
  document.getElementById('key-filter-btn').addEventListener('click', e => {
    e.stopPropagation();
    const open = popup.classList.toggle('open');
    if (open) {
      const rect = e.currentTarget.getBoundingClientRect();
      popup.style.top  = (rect.bottom + 4) + 'px';
      popup.style.left = rect.left + 'px';
      requestAnimationFrame(() => {
        const pr = popup.getBoundingClientRect();
        if (pr.right > window.innerWidth - 8)
          popup.style.left = (window.innerWidth - pr.width - 8) + 'px';
      });
    }
  });
  document.addEventListener('click', e => {
    if (!popup.contains(e.target) && e.target.id !== 'key-filter-btn') {
      popup.classList.remove('open');
    }
  });
  window.addEventListener('scroll', () => popup.classList.remove('open'), { passive: true });
})();

// ── Collapsible settings ────────────────────────────────────────────────────────
(function initSettingsToggle() {
  const sec     = document.getElementById('settings-section');
  const toggle  = document.getElementById('settings-title-toggle');
  const summary = document.getElementById('settings-summary');

  function updateSummary() {
    const mode    = document.getElementById('mode-phrase-btn')?.classList.contains('active') ? 'Phrase' : 'Bar intervals';
    const bars    = document.getElementById('bars-interval')?.value || '16';
    const maxCues = document.getElementById('max-cues')?.value || '8';
    if (summary) summary.textContent = `${mode} · every ${bars} bars · max ${maxCues} cues`;
  }

  toggle.addEventListener('click', () => {
    sec.classList.toggle('collapsed');
    updateSummary();
  });

  // Keep summary fresh whenever settings inputs change
  ['bars-interval', 'start-bar', 'max-cues', 'mode-bar-btn', 'mode-phrase-btn'].forEach(id => {
    document.getElementById(id)?.addEventListener('change', updateSummary);
    document.getElementById(id)?.addEventListener('click',  updateSummary);
  });

  // Expose so local-mode init can collapse it
  window._collapseSettings = () => { sec.classList.add('collapsed'); updateSummary(); };
  window._expandSettings   = () => sec.classList.remove('collapsed');

  updateSummary();
})();

// ── Scroll to top ───────────────────────────────────────────────────────────────
(function initScrollToTop() {
  const btn = document.getElementById('scroll-top-btn');
  if (!btn) return;
  window.addEventListener('scroll', () => {
    btn.classList.toggle('visible', window.scrollY > 400);
  }, { passive: true });
  btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
})();

// ── Scroll header ──────────────────────────────────────────────────────────────
// Sticky shadow — add .shadowed when tracks-sticky is pinned at top
(function initStickyHeader() {
  const tracksSticky = document.getElementById('tracks-sticky');
  if (!tracksSticky) return;
  function checkShadow() {
    // Skip while the section is hidden (getBoundingClientRect returns top:0 for hidden elements)
    if (!document.getElementById('tracks-section')?.classList.contains('visible')) {
      tracksSticky.classList.remove('shadowed');
      return;
    }
    const rect = tracksSticky.getBoundingClientRect();
    const topBarH = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--top-bar-h')) || 0;
    tracksSticky.classList.toggle('shadowed', rect.top <= topBarH + 1);
  }
  let rafPending = false;
  window.addEventListener('scroll', () => {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => { rafPending = false; checkShadow(); });
  }, { passive: true });
  // Expose so loadTracksFromServer can trigger a correct initial check
  window._checkStickyHeader = checkShadow;
})();

// ── Set Builder ────────────────────────────────────────────────────────────────

const _CAT_COLORS = {
  warmup: '#7ec8e3', build: '#f4a261', peak: '#e63946',
  after_hours: '#9b5de5', closing: '#52b788', unknown: '#aaa',
};

let _sbSeedTrackId = null;
let _sbAnchorTrackIds = [];

function _bpmToCategory(bpm) {
  if (bpm <= 0) return 'peak';
  if (bpm < 100) return 'warmup';
  if (bpm < 118) return 'build';
  if (bpm < 138) return 'peak';
  return 'peak';
}

function _useSelectedForSetBuilder() {
  const ids = [...selectedTrackIds];
  if (!ids.length) { showToast('Select some tracks first', true); return; }
  const lookup = Object.fromEntries((parsedTracks || []).map(t => [String(t.id), t]));
  const tracks = ids.map(id => lookup[String(id)]).filter(Boolean);
  if (!tracks.length) { showToast('Selected tracks not found', true); return; }

  const bpms = tracks.map(t => t.bpm || 0).filter(b => b > 0).sort((a, b) => a - b);
  if (bpms.length) {
    document.getElementById('sb-start-bpm').value = Math.round(bpms[0]);
    document.getElementById('sb-end-bpm').value = Math.round(bpms[bpms.length - 1]);
  }
  _sbAnchorTrackIds = ids.map(id => parseInt(id, 10));
  const n = tracks.length;
  const names = tracks.slice(0, 2).map(t => t.name || t.title || '(untitled)').join(', ');
  const label = `${n} track${n !== 1 ? 's' : ''}: ${names}${n > 2 ? ` + ${n - 2} more` : ''}`;
  document.getElementById('sb-seed-label').textContent = label;
  document.getElementById('sb-seed-row').style.display = '';
  showToast(`${n} anchor${n !== 1 ? 's' : ''} set for Set Builder`);
}

function _useSelectedForPlaylist() {
  const ids = [...selectedTrackIds];
  if (!ids.length) { showToast('Select some tracks first', true); return; }
  const lookup = Object.fromEntries((parsedTracks || []).map(t => [String(t.id), t]));
  const tracks = ids.map(id => lookup[String(id)]).filter(Boolean);
  if (!tracks.length) { showToast('Selected tracks not found', true); return; }

  _psSeedTrackIds = ids.map(id => parseInt(id, 10));
  _psExcludedIds = [];
  _psCategoryLast = null;

  // Auto-detect category from median BPM
  const bpms = tracks.map(t => t.bpm || 0).filter(b => b > 0);
  if (bpms.length) {
    const medianBpm = bpms.sort((a, b) => a - b)[Math.floor(bpms.length / 2)];
    const cat = _bpmToCategory(medianBpm);
    document.getElementById('ps-category').value = cat;
  }
  const n = tracks.length;
  const status = document.getElementById('ps-status');
  if (status) status.textContent = `${n} seed track${n !== 1 ? 's' : ''} pre-included`;
  showToast(`${n} seed track${n !== 1 ? 's' : ''} set for Playlist Suggest`);
}

let _psExcludedIds = [];
let _psCategoryLast = null;
let _psTracks = [];
let _psDragIdx = null;
let _psSeedTrackIds = [];

async function suggestPlaylist(append) {
  const btn     = document.getElementById('ps-suggest-btn');
  const moreBtn = document.getElementById('ps-more-btn');
  const resetBtn = document.getElementById('ps-reset-btn');
  const status  = document.getElementById('ps-status');
  const result  = document.getElementById('ps-result');
  const summary = document.getElementById('ps-summary');
  const list    = document.getElementById('ps-tracklist');

  const category = document.getElementById('ps-category').value;
  const count    = parseInt(document.getElementById('ps-count').value, 10) || 20;

  // Reset excluded list if category changed
  if (category !== _psCategoryLast) {
    _psExcludedIds = [];
    _psCategoryLast = category;
    list.innerHTML = '';
  }

  if (!append) {
    _psExcludedIds = [];
    list.innerHTML = '';
  }

  _setBtnLoading(btn, true, append ? 'Adding…' : 'Searching…');
  moreBtn.disabled = true;
  status.textContent = '';
  if (!append) result.style.display = 'none';
  const psProgress = document.getElementById('ps-progress');
  if (psProgress) psProgress.style.display = '';

  try {
    const psBody = { category, count, exclude_ids: _psExcludedIds };
    if (_psSeedTrackIds.length) psBody.seed_track_ids = _psSeedTrackIds;
    const r = await fetch('/api/playlists/suggest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(psBody),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    status.textContent = '';
    const trackMap = Object.fromEntries((parsedTracks || []).map(t => [String(t.id), t]));
    if (!append) _psTracks = [];
    for (const item of d.results) {
      _psExcludedIds.push(item.track_id);
      const t = trackMap[String(item.track_id)];
      _psTracks.push({
        track_id:  item.track_id,
        title:     t ? (t.name || t.title || '(untitled)') : '(track ' + item.track_id + ')',
        artist:    t ? (t.artist || '') : '',
        bpm:       t ? t.bpm : 0,
        key:       t ? (t.key || '—') : '—',
        category:  category,
        score:     item.score,
      });
    }
    _psRenderSet();
    const totalShown = _psTracks.length;
    summary.textContent = `${totalShown} tracks for "${category}"${_psExcludedIds.length > totalShown ? ' (excluding ' + (_psExcludedIds.length - totalShown) + ' already shown)' : ''}`;
    result.style.display = '';
    moreBtn.style.display = d.results.length >= count ? '' : 'none';
    resetBtn.style.display = _psExcludedIds.length > 0 ? '' : 'none';
    showToast(append ? `Added ${d.results.length} more ${category} tracks` : `Found ${d.results.length} ${category} tracks`);
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
    showToast(`Suggest failed: ${err.message}`, true);
  } finally {
    if (psProgress) psProgress.style.display = 'none';
    _setBtnLoading(btn, false);
    moreBtn.disabled = false;
  }
}

// ── Playlist Suggest interactive ─────────────────────────────────────────────

function _psRenderSet() {
  const list = document.getElementById('ps-tracklist');
  if (!list) return;
  list.innerHTML = '';

  const allIds = _psTracks.map(t => t.track_id);

  _psTracks.forEach((t, i) => {
    const row = document.createElement('div');
    row.className = 'sb-row';
    row.draggable = true;
    row.dataset.index = i;

    const catColor = _CAT_COLORS[t.category] || '#888';
    const scorePct = t.score != null ? Math.round(t.score * 100) : null;
    const scoreColor = scorePct == null ? 'var(--muted)' : scorePct >= 70 ? 'var(--green)' : scorePct >= 45 ? '#e07000' : '#c03030';

    row.innerHTML = `
      <div class="sb-drag-handle" title="Drag to reorder">⠿</div>
      <div class="sb-row-num">${i + 1}</div>
      <div class="sb-art" data-tid="${t.track_id}">
        <div class="sb-art-ph">♪</div>
        <div class="sb-art-play">▶</div>
      </div>
      <div class="sb-row-main">
        <div class="sb-row-title">${t.title || '(untitled)'}</div>
        <div class="sb-row-artist">${t.artist || ''}</div>
      </div>
      <div class="sb-row-meta">
        <span class="sb-track-bpm">${t.bpm ? t.bpm.toFixed(1) : '—'}</span>
        <span class="sb-track-key">${t.key || '—'}</span>
        <span class="sb-track-cat" style="color:${catColor};border-color:${catColor};background:${catColor}18">${t.category}</span>
        ${scorePct != null ? `<span style="font-size:11px;font-weight:600;color:${scoreColor}">${scorePct}%</span>` : ''}
      </div>
      <button class="sb-row-replace" data-idx="${i}">↻ Replace</button>
    `;

    // Artwork (lazy, always — server is up whenever PS is shown)
    const psArtEl = row.querySelector('.sb-art');
    const psArtImg = document.createElement('img');
    psArtImg.loading = 'lazy';
    psArtImg.src = '/api/tracks/' + t.track_id + '/artwork';
    psArtImg.onload = function() { const ph = psArtEl.querySelector('.sb-art-ph'); if (ph) ph.style.display = 'none'; };
    psArtImg.onerror = function() {};
    psArtEl.insertBefore(psArtImg, psArtEl.querySelector('.sb-art-play'));

    row.addEventListener('dragstart', e => {
      _psDragIdx = i;
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
    });
    row.addEventListener('dragend', () => {
      row.classList.remove('dragging');
      list.querySelectorAll('.sb-row').forEach(r => r.classList.remove('drag-over'));
    });
    row.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      list.querySelectorAll('.sb-row').forEach(r => r.classList.remove('drag-over'));
      row.classList.add('drag-over');
    });
    row.addEventListener('drop', e => {
      e.preventDefault();
      row.classList.remove('drag-over');
      if (_psDragIdx !== null && _psDragIdx !== i) {
        const moved = _psTracks.splice(_psDragIdx, 1)[0];
        _psTracks.splice(i, 0, moved);
        _psDragIdx = null;
        _psRenderSet();
        _psUpdateSummary();
      }
    });

    row.querySelector('.sb-row-replace').addEventListener('click', e => {
      e.stopPropagation();
      _psToggleAltPanel(i, t.track_id, allIds);
    });

    // Playback: hover preloads; click art cell plays (local mode only)
    if (localMode) {
      psArtEl.addEventListener('mouseenter', () => {
        const tr = (parsedTracks || []).find(x => x.id == t.track_id);
        if (tr) ensureLocalAudio(tr).catch(() => {});
      });
      psArtEl.addEventListener('click', e => {
        e.stopPropagation();
        const tr = (parsedTracks || []).find(x => x.id == t.track_id);
        if (!tr) return;
        ensureLocalAudio(tr).then(() => {
          if (audioState[tr.id]) {
            playTrack(tr.id, 0);
            document.querySelectorAll('.sb-art').forEach(el => {
              const isPlaying = el.dataset.tid == t.track_id;
              el.classList.toggle('playing', isPlaying);
              el.querySelector('.sb-art-play').textContent = isPlaying ? '⏸' : '▶';
            });
          }
        });
      });
    }

    // Mark seed tracks visually
    if (_psSeedTrackIds.includes(t.track_id)) row.classList.add('anchor-track');

    list.appendChild(row);
  });
}

async function _psToggleAltPanel(idx, trackId, allIds) {
  const list = document.getElementById('ps-tracklist');
  const existing = list.querySelector('.sb-alt-panel');
  if (existing) {
    const wasIdx = parseInt(existing.dataset.forIdx);
    existing.remove();
    if (wasIdx === idx) return;
  }

  const rows = list.querySelectorAll('.sb-row');
  if (!rows[idx]) return;

  const panel = document.createElement('div');
  panel.className = 'sb-alt-panel';
  panel.dataset.forIdx = idx;
  panel.innerHTML = `<div class="sb-alt-panel-title">↻ Finding best replacements…</div>`;
  list.insertBefore(panel, rows[idx].nextSibling);

  const prev = _psTracks[idx - 1];
  const next = _psTracks[idx + 1];
  const excludeStr = allIds.join(',');

  try {
    const params = new URLSearchParams({ track_id: trackId, exclude_ids: excludeStr, n: 8 });
    if (prev) params.set('prev_id', prev.track_id);
    if (next) params.set('next_id', next.track_id);

    const r = await fetch(`/api/setbuilder/alternatives?${params}`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const d = await r.json();

    if (!d.alternatives.length) {
      panel.innerHTML = `<div class="sb-alt-panel-title">No suitable replacements found in library</div>`;
      return;
    }

    let html = `<div class="sb-alt-panel-title">↻ Best replacements for position ${idx + 1} <span style="font-weight:400;color:var(--muted-soft)">(click to swap)</span></div>`;
    for (const alt of d.alternatives) {
      const scoreColor = alt.score >= 70 ? 'var(--green)' : alt.score >= 45 ? '#e07000' : '#c03030';
      const fromStr = alt.from_prev != null ? `from prev: ${Math.round(alt.from_prev)}` : '';
      const toStr   = alt.to_next != null ? `to next: ${Math.round(alt.to_next)}` : '';
      const reasons = [fromStr, toStr].filter(Boolean).join(' · ');
      const genreColor = alt.genre_match === true ? 'var(--green)' : alt.genre_match === false ? '#c03030' : 'var(--muted)';
      const genreBadge = alt.genre ? `<span style="font-size:10px;padding:1px 5px;border-radius:3px;border:1px solid ${genreColor}44;color:${genreColor};white-space:nowrap;overflow:hidden;max-width:80px;text-overflow:ellipsis;" title="${_esc(alt.genre)}">${_esc(alt.genre)}</span>` : '';
      html += `
        <div class="sb-alt-item" data-alt-idx="${idx}" data-alt='${JSON.stringify(alt).replace(/'/g,"&#39;")}'>
          <div class="sb-alt-main">
            <div class="sb-alt-title">${_esc(alt.title || '(untitled)')}</div>
            <div class="sb-alt-artist">${_esc(alt.artist || '')}</div>
          </div>
          <div class="sb-alt-meta">
            <span style="font-family:var(--mono)">${alt.bpm.toFixed(1)}</span>
            <span style="background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:1px 4px;font-family:var(--mono)">${_esc(alt.key || '—')}</span>
            ${genreBadge}
            ${reasons ? `<span style="color:var(--muted-soft)">${reasons}</span>` : ''}
            <span class="sb-alt-score" style="color:${scoreColor}">${Math.round(alt.score)}</span>
          </div>
        </div>`;
    }
    panel.innerHTML = html;

    panel.querySelectorAll('.sb-alt-item').forEach(el => {
      el.addEventListener('click', () => {
        const altIdx = parseInt(el.dataset.altIdx);
        const alt = JSON.parse(el.dataset.alt);
        _psTracks[altIdx] = {
          track_id: alt.track_id,
          title:    alt.title,
          artist:   alt.artist,
          bpm:      alt.bpm,
          key:      alt.key,
          category: _psTracks[altIdx].category,
          score:    null,
        };
        panel.remove();
        _psRenderSet();
        _psUpdateSummary();
        showToast(`Replaced with ${alt.title || 'track'}`);
      });
    });
  } catch (err) {
    panel.innerHTML = `<div class="sb-alt-panel-title" style="color:#c03030">${_humanFetchError(err)}</div>`;
  }
}

function _psUpdateSummary() {
  const summary = document.getElementById('ps-summary');
  if (!summary) return;
  summary.textContent = `${_psTracks.length} tracks (edited)`;
}

async function psSavePlaylist() {
  const name = prompt('Playlist name:', 'AutoCue Suggest ' + new Date().toLocaleDateString());
  if (!name) return;
  const ids = _psTracks.map(t => t.track_id);
  try {
    const r = await fetch('/api/playlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, track_ids: ids }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    showToast(`Playlist "${d.name}" created (${d.track_count} tracks)`);
  } catch (err) {
    showToast(`Failed to create playlist: ${err.message}`, true);
  }
}

// ── Set Builder state ────────────────────────────────────────────────────────
let _sbTracks = [];       // current mutable set
let _sbDragIdx = null;    // drag source index

function _sbKeyCompat(keyA, keyB) {
  if (!keyA || !keyB || keyA === '—' || keyB === '—') return null;
  // Camelot notation: same number ±1 letter, or same letter ±1 number = compatible
  const m = (k) => k.match(/^(\d+)([AB])$/i);
  const a = m(keyA), b = m(keyB);
  if (!a || !b) return null;
  const [, na, la] = a; const [, nb, lb] = b;
  const dn = Math.abs(parseInt(na) - parseInt(nb));
  const sameKey = na === nb && la.toUpperCase() === lb.toUpperCase();
  const adj = (dn <= 1 || dn === 11) && la.toUpperCase() === lb.toUpperCase();
  const relative = na === nb && la.toUpperCase() !== lb.toUpperCase();
  return sameKey || adj || relative;
}

function _sbRenderSet() {
  const list = document.getElementById('sb-tracklist');
  if (!list) return;
  list.innerHTML = '';

  const allIds = _sbTracks.map(t => t.track_id);

  _sbTracks.forEach((t, i) => {
    // ── Track row ──
    const row = document.createElement('div');
    row.className = 'sb-row';
    row.draggable = true;
    row.dataset.index = i;

    const catColor = _CAT_COLORS[t.category] || '#888';
    const relaxedAttr = t.relaxed
      ? ` title="Placed via relaxed constraints" style="opacity:.6"`
      : '';

    row.innerHTML = `
      <div class="sb-drag-handle" title="Drag to reorder">⠿</div>
      <div class="sb-row-num">${i + 1}</div>
      <div class="sb-art" data-tid="${t.track_id}">
        <div class="sb-art-ph">♪</div>
        <div class="sb-art-play">▶</div>
      </div>
      <div class="sb-row-main">
        <div class="sb-row-title">${t.title || '(untitled)'}${t.relaxed ? ' <span style="font-size:9px;color:var(--muted-soft);font-weight:400">(relaxed)</span>' : ''}</div>
        <div class="sb-row-artist">${t.artist || ''}</div>
      </div>
      <div class="sb-row-meta">
        <span class="sb-track-bpm">${t.bpm.toFixed(1)}</span>
        <span class="sb-track-key">${t.key || '—'}</span>
        <span class="sb-track-cat" style="color:${catColor};border-color:${catColor};background:${catColor}18">${t.category}</span>
      </div>
      <button class="sb-row-replace" data-idx="${i}">↻ Replace</button>
    `;

    // Artwork (lazy)
    const sbArtEl = row.querySelector('.sb-art');
    const sbArtImg = document.createElement('img');
    sbArtImg.loading = 'lazy';
    sbArtImg.src = '/api/tracks/' + t.track_id + '/artwork';
    sbArtImg.onload = function() { const ph = sbArtEl.querySelector('.sb-art-ph'); if (ph) ph.style.display = 'none'; };
    sbArtImg.onerror = function() {};
    sbArtEl.insertBefore(sbArtImg, sbArtEl.querySelector('.sb-art-play'));

    // Drag handlers
    row.addEventListener('dragstart', e => {
      _sbDragIdx = i;
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
    });
    row.addEventListener('dragend', () => {
      row.classList.remove('dragging');
      list.querySelectorAll('.sb-row').forEach(r => r.classList.remove('drag-over'));
    });
    row.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      list.querySelectorAll('.sb-row').forEach(r => r.classList.remove('drag-over'));
      row.classList.add('drag-over');
    });
    row.addEventListener('drop', e => {
      e.preventDefault();
      row.classList.remove('drag-over');
      if (_sbDragIdx !== null && _sbDragIdx !== i) {
        const moved = _sbTracks.splice(_sbDragIdx, 1)[0];
        _sbTracks.splice(i, 0, moved);
        _sbDragIdx = null;
        _sbRenderSet();
        _sbUpdateSummary();
      }
    });

    // Replace button
    row.querySelector('.sb-row-replace').addEventListener('click', e => {
      e.stopPropagation();
      _sbToggleAltPanel(i, t.track_id, allIds);
    });

    // Playback: hover preloads audio; click art cell plays (local mode only)
    if (localMode) {
      sbArtEl.addEventListener('mouseenter', () => {
        const tr = (parsedTracks || []).find(x => x.id == t.track_id);
        if (tr) ensureLocalAudio(tr).catch(() => {});
      });
      sbArtEl.addEventListener('click', e => {
        e.stopPropagation();
        const tr = (parsedTracks || []).find(x => x.id == t.track_id);
        if (!tr) return;
        ensureLocalAudio(tr).then(() => {
          if (audioState[tr.id]) {
            playTrack(tr.id, 0);
            document.querySelectorAll('.sb-art').forEach(el => {
              const isPlaying = el.dataset.tid == t.track_id;
              el.classList.toggle('playing', isPlaying);
              el.querySelector('.sb-art-play').textContent = isPlaying ? '⏸' : '▶';
            });
          }
        });
      });
    }

    // Mark anchor tracks visually
    if (_sbAnchorTrackIds.includes(t.track_id)) row.classList.add('anchor-track');

    list.appendChild(row);

    // ── Transition connector to next track ──
    if (i < _sbTracks.length - 1) {
      const next = _sbTracks[i + 1];
      const conn = document.createElement('div');
      conn.className = 'sb-connector';

      const bpmDiff = next.bpm - t.bpm;
      const bpmStr = `${t.bpm.toFixed(1)} → ${next.bpm.toFixed(1)} BPM (${bpmDiff >= 0 ? '+' : ''}${bpmDiff.toFixed(1)})`;

      const keyCompat = _sbKeyCompat(t.key, next.key);
      const keyStr = (t.key && next.key && t.key !== '—' && next.key !== '—')
        ? `${t.key} → ${next.key}${keyCompat === true ? ' ✓' : keyCompat === false ? ' ✗' : ''}`
        : '';

      const score = next.transition_score;
      const scoreClass = score == null ? '' : score >= 70 ? 'sb-conn-score' : score >= 45 ? 'sb-conn-score low' : 'sb-conn-score bad';
      const scoreStr = score != null ? `<span class="${scoreClass}">${Math.round(score)}</span>` : '';

      const adviceStr = next.mix_advice || '';
      conn.innerHTML = `
        <div class="sb-connector-line"></div>
        <div style="display:flex;flex-direction:column;gap:2px;">
          <div class="sb-connector-info">
            ${bpmStr}
            ${keyStr ? `· ${keyStr}` : ''}
            ${scoreStr ? `· Mix ${scoreStr}` : ''}
          </div>
          ${adviceStr ? `<div style="font-size:10px;color:var(--muted-soft);padding-left:0;font-style:italic;">💡 ${adviceStr}</div>` : ''}
        </div>
      `;
      list.appendChild(conn);
    }
  });
}

async function _sbToggleAltPanel(idx, trackId, allIds) {
  const list = document.getElementById('sb-tracklist');
  // Remove any existing panel
  const existing = list.querySelector('.sb-alt-panel');
  if (existing) {
    const wasIdx = parseInt(existing.dataset.forIdx);
    existing.remove();
    if (wasIdx === idx) return; // toggle off
  }

  // Insert panel after the row at idx (accounting for connectors: row + connector per pair)
  const rows = list.querySelectorAll('.sb-row');
  if (!rows[idx]) return;
  const insertAfter = rows[idx].nextSibling; // may be connector or next row or null

  const panel = document.createElement('div');
  panel.className = 'sb-alt-panel';
  panel.dataset.forIdx = idx;
  panel.innerHTML = `<div class="sb-alt-panel-title">↻ Finding best replacements…</div>`;
  list.insertBefore(panel, insertAfter);

  const prev = _sbTracks[idx - 1];
  const next = _sbTracks[idx + 1];
  const excludeStr = allIds.join(',');

  try {
    const params = new URLSearchParams({ track_id: trackId, exclude_ids: excludeStr, n: 8 });
    if (prev) params.set('prev_id', prev.track_id);
    if (next) params.set('next_id', next.track_id);

    const r = await fetch(`/api/setbuilder/alternatives?${params}`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const d = await r.json();

    if (!d.alternatives.length) {
      panel.innerHTML = `<div class="sb-alt-panel-title">No suitable replacements found in library</div>`;
      return;
    }

    let html = `<div class="sb-alt-panel-title">↻ Best replacements for position ${idx + 1} <span style="font-weight:400;color:var(--muted-soft)">(click to swap)</span></div>`;
    for (const alt of d.alternatives) {
      const scoreColor = alt.score >= 70 ? 'var(--green)' : alt.score >= 45 ? '#e07000' : '#c03030';
      const fromStr = alt.from_prev != null ? `from prev: ${Math.round(alt.from_prev)}` : '';
      const toStr   = alt.to_next != null ? `to next: ${Math.round(alt.to_next)}` : '';
      const reasons = [fromStr, toStr].filter(Boolean).join(' · ');
      const genreColor = alt.genre_match === true ? 'var(--green)' : alt.genre_match === false ? '#c03030' : 'var(--muted)';
      const genreBadge = alt.genre ? `<span style="font-size:10px;padding:1px 5px;border-radius:3px;border:1px solid ${genreColor}44;color:${genreColor};white-space:nowrap;overflow:hidden;max-width:80px;text-overflow:ellipsis;" title="${_esc(alt.genre)}">${_esc(alt.genre)}</span>` : '';
      html += `
        <div class="sb-alt-item" data-alt-idx="${idx}" data-alt='${JSON.stringify(alt).replace(/'/g,"&#39;")}'>
          <div class="sb-alt-main">
            <div class="sb-alt-title">${_esc(alt.title || '(untitled)')}</div>
            <div class="sb-alt-artist">${_esc(alt.artist || '')}</div>
          </div>
          <div class="sb-alt-meta">
            <span style="font-family:var(--mono)">${alt.bpm.toFixed(1)}</span>
            <span style="background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:1px 4px;font-family:var(--mono)">${_esc(alt.key || '—')}</span>
            ${genreBadge}
            ${reasons ? `<span style="color:var(--muted-soft)">${reasons}</span>` : ''}
            <span class="sb-alt-score" style="color:${scoreColor}">${Math.round(alt.score)}</span>
          </div>
        </div>`;
    }
    panel.innerHTML = html;

    panel.querySelectorAll('.sb-alt-item').forEach(el => {
      el.addEventListener('click', () => {
        const altIdx = parseInt(el.dataset.altIdx);
        const alt = JSON.parse(el.dataset.alt);
        const cur = _sbTracks[altIdx];
        _sbTracks[altIdx] = {
          track_id:        alt.track_id,
          title:           alt.title,
          artist:          alt.artist,
          bpm:             alt.bpm,
          key:             alt.key,
          category:        cur.category,
          transition_score: alt.to_next,
          relaxed:         false,
        };
        panel.remove();
        _sbRenderSet();
        _sbUpdateSummary();
        showToast(`Replaced with ${alt.title || 'track'}`);
      });
    });
  } catch (err) {
    panel.innerHTML = `<div class="sb-alt-panel-title" style="color:#c03030">${_humanFetchError(err)}</div>`;
  }
}

function _sbUpdateSummary() {
  const summary = document.getElementById('sb-summary');
  if (!summary) return;
  const n = _sbTracks.length;
  // rough estimate: avg 6 min/track
  const mins = Math.round(n * 6);
  summary.textContent = `${n} tracks · ~${mins} min (edited)`;
}

async function sbSavePlaylist() {
  const name = prompt('Playlist name:', 'AutoCue Set ' + new Date().toLocaleDateString());
  if (!name) return;
  const ids = _sbTracks.map(t => t.track_id);
  try {
    const r = await fetch('/api/playlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, track_ids: ids }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    showToast(`Playlist "${d.name}" created (${d.track_count} tracks)`);
  } catch (err) {
    showToast(`Failed to create playlist: ${err.message}`, true);
  }
}

async function buildSet() {
  const btn      = document.getElementById('sb-build-btn');
  const status   = document.getElementById('sb-status');
  const result   = document.getElementById('sb-result');
  const summary  = document.getElementById('sb-summary');
  const progBar  = document.getElementById('sb-progress');

  const body = {
    start_bpm:        parseFloat(document.getElementById('sb-start-bpm').value) || 110,
    end_bpm:          parseFloat(document.getElementById('sb-end-bpm').value) || 135,
    duration_minutes: parseFloat(document.getElementById('sb-duration').value) || 60,
    energy_mode:      document.getElementById('sb-energy-mode').value,
  };
  if (_sbAnchorTrackIds.length) body.anchor_track_ids = _sbAnchorTrackIds;
  else if (_sbSeedTrackId) body.seed_track_id = _sbSeedTrackId;

  _setBtnLoading(btn, true, 'Building…');
  status.textContent = '';
  result.style.display = 'none';
  progBar.style.display = '';

  try {
    const r = await fetch('/api/setbuilder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    status.textContent = '';
    const terminationLabels = {
      'target_duration_reached':         '',
      'no_candidates_passed_thresholds': ' · ⚠ library too narrow',
      'safety_cap_hit':                  ' · ⚠ search exhausted early',
    };
    const note = terminationLabels[d.terminated_reason] || '';
    summary.textContent = `${d.total_tracks} tracks · ~${d.estimated_duration_minutes} min${note}`;
    _sbTracks = d.tracks.slice();
    _sbRenderSet();
    result.style.display = '';
    showToast(`Set built: ${d.total_tracks} tracks`);
  } catch (err) {
    const human = _humanFetchError(err);
    status.textContent = human;
    showToast(human, true);
  } finally {
    _setBtnLoading(btn, false);
    progBar.style.display = 'none';
  }
}

document.addEventListener('DOMContentLoaded', function() {
  var cards = document.querySelectorAll('.panel-card');
  var sectionObserver = new IntersectionObserver(function(entries) {
    entries.forEach(function(e) {
      if (e.isIntersecting) {
        e.target.style.opacity = '';
        e.target.style.transform = '';
        e.target.classList.add('animate-in');
        sectionObserver.unobserve(e.target);
      }
    });
  }, { threshold: 0.05 });
  cards.forEach(function(el) {
    var rect = el.getBoundingClientRect();
    if (rect.top > window.innerHeight) {
      el.style.opacity = '0';
      el.style.transform = 'translateY(10px)';
    }
    sectionObserver.observe(el);
  });
});

// ── DJ Mixing Guide ───────────────────────────────────────────────────────────
(function initMixingGuide() {
  var header  = document.getElementById('sb-guide-header');
  var body    = document.getElementById('sb-guide-body');
  var chevron = document.getElementById('sb-guide-chevron');
  if (!header) return;

  header.addEventListener('click', function() {
    var open = body.classList.contains('open');
    chevron.classList.toggle('open', !open);
    if (open) { _slideClose(body, 'open'); } else { _slideOpen(body, 'open'); }
  });

  document.querySelectorAll('[data-guide-tab]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var tab = btn.dataset.guideTab;
      document.querySelectorAll('[data-guide-tab]').forEach(function(b) { b.classList.remove('active'); });
      document.querySelectorAll('[data-guide-panel]').forEach(function(p) { p.classList.remove('active'); });
      btn.classList.add('active');
      var panel = document.querySelector('[data-guide-panel="' + tab + '"]');
      if (panel) panel.classList.add('active');
    });
  });
})();

// ── Top bar: sticky glass + height tracking ────────────────────────────────────
(function() {
  var bar = document.getElementById('top-bar');
  if (!bar) return;

  // Glass effect on scroll
  window.addEventListener('scroll', function() {
    bar.classList.toggle('scrolled', window.scrollY > 4);
  }, { passive: true });

  // Keep --top-bar-h in sync so #tracks-sticky sticks below the bar
  function sync() {
    document.documentElement.style.setProperty('--top-bar-h', bar.offsetHeight + 'px');
  }
  sync();
  new ResizeObserver(sync).observe(bar);
})();

// ── Theme toggle ───────────────────────────────────────────────────────────────
const root = document.documentElement;
const themeBtn = document.getElementById('theme-toggle');
function applyTheme(dark) {
  root.classList.toggle('dark', dark);
  themeBtn.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
  try { localStorage.setItem('ac_theme', dark ? 'dark' : 'light'); } catch (_) {}
}
// Saved choice wins; first visit follows the OS preference (a DJ at night
// shouldn't get flashed white); the toggle then pins it.
let _savedTheme = null;
try { _savedTheme = localStorage.getItem('ac_theme'); } catch (_) {}
applyTheme(_savedTheme !== null
  ? _savedTheme === 'dark'
  : !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches));
themeBtn.addEventListener('click', () => applyTheme(!root.classList.contains('dark')));
