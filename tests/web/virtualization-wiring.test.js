/**
 * TASK-032 + TASK-034 + TASK-035 — Virtualizer wiring + filteredTracks indices.
 *
 * Mirrors the Virtualizer IIFE from docs/index.html in window-mode (the mode
 * the production track-list wires up). The container-mode contract is tested
 * in tests/web/virtualizer.test.js. Update both in lock-step if the
 * implementation changes.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// ── Verbatim Virtualizer from docs/index.html ────────────────────────────────

function makeVirtualizer() {
  var state = null;

  function _computeWindow(s) {
    if (s.scrollSource === 'window') {
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

  function _render() {
    if (state === null) return;
    if (state.scrollSource === 'window') {
      state.viewportHeight = window.innerHeight || state.viewportHeight;
    }
    var win = _computeWindow(state);
    var needed = new Set();
    for (var i = win.first; i < win.last; i++) needed.add(i);

    var stale = [];
    state.live.forEach(function(node, idx) {
      if (!needed.has(idx)) stale.push(idx);
    });
    stale.forEach(function(idx) {
      var node = state.live.get(idx);
      state.live.delete(idx);
      state.pool.push(node);
    });

    for (var j = win.first; j < win.last; j++) {
      if (state.live.has(j)) continue;
      var recycled = state.pool.pop() || null;
      var rendered = state.renderItem(j, recycled);
      if (rendered) {
        rendered.style.position = 'absolute';
        rendered.style.top = '0';
        rendered.style.left = '0';
        rendered.style.right = '0';
        rendered.style.transform = 'translateY(' + (j * state.itemHeight) + 'px)';
        if (!rendered.parentNode) state.container.appendChild(rendered);
        state.live.set(j, rendered);
      }
    }

    state.spacer.style.height = (state.totalCount * state.itemHeight) + 'px';

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
      this.detach();
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
        scrollSource: scrollSource,
        pool: [],
        live: new Map(),
        rafScheduled: false,
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
      state.pool.forEach(function(node) {
        if (node && node.parentNode) node.parentNode.removeChild(node);
      });
      if (state.spacer && state.spacer.parentNode) {
        state.spacer.parentNode.removeChild(state.spacer);
      }
      state = null;
    },
    isAttached: function() { return state !== null; },
    _visibleNodes: function() { return state ? state.live : new Map(); },
    _state: function() { return state; },
  };
}

// jsdom always reports zeros from getBoundingClientRect; stub for the test.
function stubRect(el, { top = 0 } = {}) {
  el.getBoundingClientRect = () => ({
    top, left: 0, right: 1000, bottom: 1000, width: 1000, height: 1000, x: 0, y: top,
  });
}

function buildSampleCard(index, trackId) {
  const div = document.createElement('div');
  div.className = 'track-card';
  div.dataset.trackId = String(trackId != null ? trackId : index);
  div.dataset.idx = String(index);
  return div;
}

const CARD_HEIGHT_PX = 160;

// ── filteredTracks indices port (TASK-034) ──────────────────────────────────
// Mirrors the production predicate; the production `_perf.measure` shim is
// dropped because it's not under test.
function filteredTracks(parsedTracks, state = {}) {
  const {
    phraseOnlyFilter = false,
    beatsOnlyFilter = false,
    searchQuery = '',
    ratingFilter = 0,
    playsFilter = 'all',
    lastPlayedFilter = 'all',
    myTagFilters = new Set(),
    selectedKeys = new Set(),
    genreFilters = new Set(),
  } = state;
  const q = searchQuery ? searchQuery.toLowerCase() : '';
  const cutoffISO = (lastPlayedFilter !== 'all' && lastPlayedFilter !== 'never')
    ? new Date(Date.now() - (lastPlayedFilter === '7d' ? 7 : 30) * 86400000).toISOString()
    : null;
  const out = [];
  for (let i = 0; i < parsedTracks.length; i++) {
    const t = parsedTracks[i];
    if (phraseOnlyFilter && !t.hasPhrase) continue;
    if (beatsOnlyFilter && !t.hasBeats) continue;
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
  return out;
}

describe('filteredTracks returns indices into parsedTracks (TASK-034)', () => {
  const sample = [
    { id: '1', name: 'Acid Rain', artist: 'Burial', hasPhrase: true,  hasBeats: true,  rating: 5, playCount: 0 },
    { id: '2', name: 'Midnight',  artist: 'Aphex',  hasPhrase: false, hasBeats: true,  rating: 3, playCount: 5 },
    { id: '3', name: 'Flux',      artist: 'Burial', hasPhrase: true,  hasBeats: false, rating: 1, playCount: 0 },
  ];

  it('returns a plain array of numeric indices', () => {
    const idx = filteredTracks(sample);
    expect(Array.isArray(idx)).toBe(true);
    expect(idx).toEqual([0, 1, 2]);
    for (const i of idx) expect(typeof i).toBe('number');
  });

  it('dereferenced indices match the expected track objects', () => {
    const idx = filteredTracks(sample, { searchQuery: 'burial' });
    const tracks = idx.map(i => sample[i]);
    expect(tracks).toHaveLength(2);
    expect(tracks[0].id).toBe('1');
    expect(tracks[1].id).toBe('3');
  });

  it('parsedTracks is not mutated by filtering', () => {
    const before = sample.slice();
    filteredTracks(sample, { phraseOnlyFilter: true, ratingFilter: 5 });
    expect(sample).toEqual(before);
  });

  it('sort applied to indices via dereference produces expected order', () => {
    const idx = filteredTracks(sample);
    const byRating = idx.slice().sort((a, b) => (sample[b].rating || 0) - (sample[a].rating || 0));
    expect(byRating.map(i => sample[i].id)).toEqual(['1', '2', '3']);
  });
});

// ── Virtualizer window-mode contract ────────────────────────────────────────

describe('Virtualizer window-mode wiring (TASK-032 / TASK-037)', () => {
  let V, container;
  beforeEach(() => {
    V = makeVirtualizer();
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
    window.scrollTo = vi.fn();
    container = document.createElement('div');
    stubRect(container, { top: 0 });
    document.body.appendChild(container);
    vi.stubGlobal('requestAnimationFrame', (fn) => { fn(); return 1; });
  });
  afterEach(() => {
    V.detach();
    container.remove();
    vi.restoreAllMocks();
  });

  it('mounts only ~viewport+buffer cards, not the whole 10k library (~40 cap)', () => {
    let calls = 0;
    V.attach({
      container,
      itemHeight: CARD_HEIGHT_PX,
      totalCount: 10000,
      buffer: 5,
      renderItem: (i, node) => { calls++; return node || buildSampleCard(i); },
      scrollSource: 'window',
    });
    expect(calls).toBeLessThan(40);
    expect(V._visibleNodes().size).toBeLessThan(40);
    expect(container.querySelectorAll('.track-card').length).toBeLessThan(40);
  });

  it('spacer height equals totalCount * itemHeight to preserve native scrollbar', () => {
    V.attach({
      container, itemHeight: CARD_HEIGHT_PX, totalCount: 500,
      renderItem: (i, node) => node || buildSampleCard(i),
      scrollSource: 'window',
    });
    const spacer = container.firstChild;
    expect(spacer.style.height).toBe(String(500 * CARD_HEIGHT_PX) + 'px');
  });

  it('shifts the window when the container scrolls past the viewport top', () => {
    V.attach({
      container, itemHeight: CARD_HEIGHT_PX, totalCount: 10000, buffer: 5,
      renderItem: (i, node) => {
        const el = node || buildSampleCard(i);
        el.dataset.idx = String(i);
        return el;
      },
      scrollSource: 'window',
    });
    const firstWindow = new Set();
    V._visibleNodes().forEach((_, idx) => firstWindow.add(idx));

    // Simulate page scroll: container's top moves up.
    stubRect(container, { top: -5000 });
    window.dispatchEvent(new Event('scroll'));

    const newWindow = new Set();
    V._visibleNodes().forEach((_, idx) => newWindow.add(idx));
    const overlap = [...newWindow].filter(i => firstWindow.has(i)).length;
    expect(overlap).toBeLessThan(newWindow.size);
    expect(newWindow.size).toBeLessThan(40);
  });

  it('recycles DOM nodes — total node count stays bounded across long scrolls', () => {
    const created = new Set();
    V.attach({
      container, itemHeight: CARD_HEIGHT_PX, totalCount: 10000,
      renderItem: (i, node) => {
        let el = node;
        if (!el) { el = buildSampleCard(i); created.add(el); }
        el.dataset.idx = String(i);
        return el;
      },
      scrollSource: 'window',
    });
    for (let s = 1; s <= 5; s++) {
      stubRect(container, { top: -s * 2000 });
      window.dispatchEvent(new Event('scroll'));
    }
    const visible = V._visibleNodes().size;
    expect(visible).toBeLessThan(40);
    // Recycling caps total node creation at ~viewport size, not 5x viewport.
    expect(created.size).toBeLessThan(80);
  });

  it('fires onWindowChange when the visible window shifts', () => {
    const events = [];
    V.attach({
      container, itemHeight: CARD_HEIGHT_PX, totalCount: 10000, buffer: 5,
      renderItem: (i, node) => node || buildSampleCard(i),
      onWindowChange: (first, last) => { events.push({ first, last }); },
      scrollSource: 'window',
    });
    expect(events.length).toBe(1);
    const firstEvt = events[0];

    stubRect(container, { top: -5000 });
    window.dispatchEvent(new Event('scroll'));
    expect(events.length).toBeGreaterThanOrEqual(2);
    const lastEvt = events[events.length - 1];
    expect(lastEvt.first).not.toBe(firstEvt.first);
  });

  it('detach removes the window scroll listener and clears state', () => {
    const removeSpy = vi.spyOn(window, 'removeEventListener');
    V.attach({
      container, itemHeight: CARD_HEIGHT_PX, totalCount: 100,
      renderItem: (i, node) => node || buildSampleCard(i),
      scrollSource: 'window',
    });
    V.detach();
    expect(removeSpy).toHaveBeenCalledWith('scroll', expect.any(Function));
    expect(V.isAttached()).toBe(false);
    expect(V._visibleNodes().size).toBe(0);
  });

  it('detach purges pool nodes from the container so the next attach() does not stack ghost cards', () => {
    // Force a state where pool ends up non-empty: scroll near the end of a
    // 10k-row list so the new window is smaller than the old one (the live
    // → pool transfer is large; the pool → live drain is small; leftover
    // pool nodes would orphan into the DOM unless detach() cleans them).
    V.attach({
      container, itemHeight: CARD_HEIGHT_PX, totalCount: 10000, buffer: 5,
      renderItem: (i, node) => node || buildSampleCard(i),
      scrollSource: 'window',
    });
    const initialLive = V._visibleNodes().size;
    expect(initialLive).toBeGreaterThan(0);

    // Scroll to within itemHeight*2 of the end so window shrinks to ~2 items.
    stubRect(container, { top: -(10000 * CARD_HEIGHT_PX - CARD_HEIGHT_PX * 2) });
    window.dispatchEvent(new Event('scroll'));

    // Pool should now hold the surplus.
    expect(V._state().pool.length).toBeGreaterThan(0);
    const totalCardsInDom = container.querySelectorAll('.track-card').length;
    expect(totalCardsInDom).toBe(V._visibleNodes().size + V._state().pool.length);

    V.detach();
    // Container must be empty: no orphan pool nodes left behind.
    expect(container.querySelectorAll('.track-card').length).toBe(0);
  });

  it('isAttached() reflects attach/detach lifecycle', () => {
    expect(V.isAttached()).toBe(false);
    V.attach({
      container, itemHeight: CARD_HEIGHT_PX, totalCount: 10,
      renderItem: (i, node) => node || buildSampleCard(i),
      scrollSource: 'window',
    });
    expect(V.isAttached()).toBe(true);
    V.detach();
    expect(V.isAttached()).toBe(false);
  });
});

// ── Selection state preservation across recycling (TASK-032 step 4) ─────────

describe('selection state survives DOM-node recycling', () => {
  let V, container, selectedTrackIds;
  beforeEach(() => {
    V = makeVirtualizer();
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
    container = document.createElement('div');
    stubRect(container, { top: 0 });
    document.body.appendChild(container);
    vi.stubGlobal('requestAnimationFrame', (fn) => { fn(); return 1; });
    selectedTrackIds = new Set();
  });
  afterEach(() => {
    V.detach();
    container.remove();
    vi.restoreAllMocks();
  });

  it('a recycled node assigned a selected track-id shows the .selected class', () => {
    // Build a tracks array; pick one that lives far down the list and mark it selected.
    const tracks = [];
    for (let i = 0; i < 1000; i++) tracks.push({ id: 't' + i, name: 'T' + i });
    const selectedIdx = 500;
    selectedTrackIds.add(tracks[selectedIdx].id);

    V.attach({
      container, itemHeight: CARD_HEIGHT_PX, totalCount: tracks.length,
      renderItem: (i, recycled) => {
        const card = recycled || document.createElement('div');
        card.className = 'track-card';
        card.dataset.trackId = String(tracks[i].id);
        const isSelected = selectedTrackIds.has(tracks[i].id);
        card.classList.toggle('selected', isSelected);
        return card;
      },
      scrollSource: 'window',
    });

    // Initial window contains low indices; nothing selected yet on-screen.
    let visibleSelected = 0;
    V._visibleNodes().forEach((node) => {
      if (node.classList.contains('selected')) visibleSelected++;
    });
    expect(visibleSelected).toBe(0);

    // Scroll until the selected track enters the window.
    stubRect(container, { top: -(selectedIdx * CARD_HEIGHT_PX) });
    window.dispatchEvent(new Event('scroll'));

    // Find the now-visible card for the selected track-id.
    let found = null;
    V._visibleNodes().forEach((node) => {
      if (node.dataset.trackId === 't' + selectedIdx) found = node;
    });
    expect(found).not.toBeNull();
    expect(found.classList.contains('selected')).toBe(true);
  });
});

// ── Settings-fingerprint change triggers detach+reattach (TASK-035 step 1) ──

describe('settings-fingerprint change forces a full repool reset', () => {
  let V, container;
  beforeEach(() => {
    V = makeVirtualizer();
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
    container = document.createElement('div');
    stubRect(container, { top: 0 });
    document.body.appendChild(container);
    vi.stubGlobal('requestAnimationFrame', (fn) => { fn(); return 1; });
  });
  afterEach(() => {
    V.detach();
    container.remove();
    vi.restoreAllMocks();
  });

  it('detach() then attach() rebuilds the pool from zero', () => {
    let fingerprint = 'a';
    let cardSettingsFingerprint = '';
    const reset = () => {
      const next = fingerprint;
      const changed = next !== cardSettingsFingerprint;
      if (changed) {
        cardSettingsFingerprint = next;
        if (V.isAttached()) V.detach();
      }
      V.attach({
        container, itemHeight: CARD_HEIGHT_PX, totalCount: 50,
        renderItem: (i, node) => {
          const el = node || document.createElement('div');
          el.className = 'track-card';
          el.dataset.fingerprint = next;
          return el;
        },
        scrollSource: 'window',
      });
    };
    reset();
    const firstNode = V._visibleNodes().get(0);
    expect(firstNode.dataset.fingerprint).toBe('a');

    fingerprint = 'b';
    reset();
    const secondNode = V._visibleNodes().get(0);
    // Different DOM node identity → pool was reset, not reused.
    expect(secondNode).not.toBe(firstNode);
    expect(secondNode.dataset.fingerprint).toBe('b');
  });
});
