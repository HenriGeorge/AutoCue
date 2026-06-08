/**
 * Tests for Virtualizer + filteredTracks() indices return type.
 *
 * Virtualizer is copied verbatim from docs/index.html. If you change it there,
 * update the copy here. The test exercises:
 *   - attach renders only the visible + buffer window
 *   - scroll shifts the window; pool recycles nodes
 *   - update({totalCount}) resizes the spacer + drops out-of-range cards
 *   - detach removes listeners + clears state
 *   - renderItem receives recycled nodes across different indices
 *   - filteredTracks() returns indices into parsedTracks
 *   - track-card uniform height invariant (TASK-033)
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

// ── Verbatim Virtualizer from docs/index.html ─────────────────────────────────
const CARD_HEIGHT_PX = 160;
const Virtualizer = (function() {
  var BUFFER = 5;
  var _container = null;
  var _spacer = null;
  var _itemHeight = 0;
  var _totalCount = 0;
  var _renderItem = null;
  var _onWindowChange = null;
  var _visibleIndex = new Map();
  var _cardPool = [];
  var _rafId = null;
  var _lastStart = -1;
  var _lastEnd = -1;
  var _onScroll = null;
  var _onResize = null;
  var _enabled = false;

  function _computeWindow() {
    if (!_container || !_itemHeight || !_totalCount) return { start: 0, end: 0 };
    var rect = _container.getBoundingClientRect();
    var viewportH = window.innerHeight || document.documentElement.clientHeight;
    var top = -rect.top;
    var visibleStart = Math.floor(top / _itemHeight);
    var visibleEnd = Math.ceil((top + viewportH) / _itemHeight);
    var start = Math.max(0, visibleStart - BUFFER);
    var end = Math.min(_totalCount, visibleEnd + BUFFER);
    if (start > end) start = end;
    return { start: start, end: end };
  }

  function _scheduleRender() {
    if (_rafId != null) return;
    _rafId = requestAnimationFrame(function() {
      _rafId = null;
      _render();
    });
  }

  function _render() {
    if (!_enabled || !_container) return;
    var win = _computeWindow();
    var start = win.start, end = win.end;

    var toRecycle = [];
    _visibleIndex.forEach(function(node, idx) {
      if (idx < start || idx >= end) toRecycle.push(idx);
    });
    for (var i = 0; i < toRecycle.length; i++) {
      var idx = toRecycle[i];
      var node = _visibleIndex.get(idx);
      _visibleIndex.delete(idx);
      _cardPool.push(node);
    }

    for (var j = start; j < end; j++) {
      var existing = _visibleIndex.get(j);
      if (existing) {
        existing.style.transform = 'translateY(' + (j * _itemHeight) + 'px)';
        continue;
      }
      var poolNode = _cardPool.pop() || null;
      var rendered = _renderItem(j, poolNode);
      if (!rendered) continue;
      rendered.style.position = 'absolute';
      rendered.style.left = '0';
      rendered.style.right = '0';
      rendered.style.top = '0';
      rendered.style.transform = 'translateY(' + (j * _itemHeight) + 'px)';
      if (!rendered.parentNode) _container.appendChild(rendered);
      _visibleIndex.set(j, rendered);
    }

    if (_lastStart !== start || _lastEnd !== end) {
      _lastStart = start; _lastEnd = end;
      if (_onWindowChange) {
        try { _onWindowChange(start, end, _visibleIndex); }
        catch (e) { console.error('[Virtualizer] onWindowChange error', e); }
      }
    }
  }

  function attach(container, opts) {
    if (_enabled) detach();
    _container = container;
    _itemHeight = opts.itemHeight;
    _totalCount = opts.totalCount;
    _renderItem = opts.renderItem;
    _onWindowChange = opts.onWindowChange || null;
    _visibleIndex = new Map();
    _cardPool = [];
    _lastStart = -1; _lastEnd = -1;
    _container.style.position = 'relative';
    _spacer = document.createElement('div');
    _spacer.className = 'virt-spacer';
    _spacer.style.cssText = 'height:' + (_totalCount * _itemHeight) + 'px;width:1px;pointer-events:none;';
    _container.innerHTML = '';
    _container.appendChild(_spacer);
    _onScroll = function() { _scheduleRender(); };
    _onResize = function() { _scheduleRender(); };
    window.addEventListener('scroll', _onScroll, { passive: true });
    window.addEventListener('resize', _onResize, { passive: true });
    _enabled = true;
    _render();
  }

  function update(opts) {
    if (!_enabled) return;
    if (typeof opts.totalCount === 'number' && opts.totalCount !== _totalCount) {
      _totalCount = opts.totalCount;
      if (_spacer) _spacer.style.height = (_totalCount * _itemHeight) + 'px';
      var stale = [];
      _visibleIndex.forEach(function(node, idx) {
        if (idx >= _totalCount) stale.push(idx);
      });
      for (var i = 0; i < stale.length; i++) {
        var n = _visibleIndex.get(stale[i]);
        _visibleIndex.delete(stale[i]);
        _cardPool.push(n);
      }
    }
    if (typeof opts.scrollToIndex === 'number' && _container) {
      var rect = _container.getBoundingClientRect();
      var target = (window.scrollY || window.pageYOffset || 0) + rect.top + opts.scrollToIndex * _itemHeight;
      window.scrollTo({ top: target, behavior: opts.smooth ? 'smooth' : 'auto' });
    }
    _lastStart = -1; _lastEnd = -1;
    _render();
  }

  function detach() {
    if (_onScroll) window.removeEventListener('scroll', _onScroll);
    if (_onResize) window.removeEventListener('resize', _onResize);
    if (_rafId != null) { cancelAnimationFrame(_rafId); _rafId = null; }
    _enabled = false;
    _visibleIndex.forEach(function(node) {
      if (node && node.parentNode) node.parentNode.removeChild(node);
    });
    _visibleIndex.clear();
    _cardPool.length = 0;
    if (_container) { _container.innerHTML = ''; _container.style.position = ''; }
    _container = null; _spacer = null;
    _renderItem = null; _onWindowChange = null;
    _lastStart = -1; _lastEnd = -1;
  }

  function visibleIndex() { return _visibleIndex; }
  function poolSize() { return _cardPool.length; }
  function isAttached() { return _enabled; }

  return { attach, update, detach, visibleIndex, poolSize, isAttached };
})();

// jsdom's getBoundingClientRect always returns zeros; stub for container.
function stubContainer(container, { top = 0 } = {}) {
  container.getBoundingClientRect = () => ({
    top, left: 0, right: 1000, bottom: 1000, width: 1000, height: 1000, x: 0, y: top,
  });
}

function buildSampleCard(index) {
  const div = document.createElement('div');
  div.className = 'track-card';
  div.dataset.trackId = String(index);
  div.dataset.idx = String(index);
  return div;
}

beforeEach(() => {
  // Stable viewport height for window math.
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
  window.scrollTo = vi.fn();
});

afterEach(() => {
  if (Virtualizer.isAttached()) Virtualizer.detach();
});

describe('Virtualizer.attach', () => {
  it('mounts a spacer of height totalCount * itemHeight', () => {
    const container = document.createElement('div');
    stubContainer(container);
    document.body.appendChild(container);

    Virtualizer.attach(container, {
      itemHeight: CARD_HEIGHT_PX,
      totalCount: 10000,
      renderItem: (i, node) => node || buildSampleCard(i),
    });

    const spacer = container.querySelector('.virt-spacer');
    expect(spacer).toBeTruthy();
    expect(spacer.style.height).toBe(10000 * CARD_HEIGHT_PX + 'px');
  });

  it('renders only the visible + buffer window, not all 10k', () => {
    const container = document.createElement('div');
    stubContainer(container);
    document.body.appendChild(container);

    const renderCalls = [];
    Virtualizer.attach(container, {
      itemHeight: CARD_HEIGHT_PX,
      totalCount: 10000,
      renderItem: (i, node) => { renderCalls.push(i); return node || buildSampleCard(i); },
    });

    // viewport 800 / 160 = 5 visible rows + 5 buffer top (clamped 0) + 5 buffer bottom
    expect(renderCalls.length).toBeLessThan(40);
    expect(container.querySelectorAll('.track-card').length).toBeLessThan(40);
  });
});

describe('Virtualizer scroll behavior', () => {
  it('shifts the visible window when the container moves off-screen and recycles nodes', () => {
    const container = document.createElement('div');
    stubContainer(container, { top: 0 });
    document.body.appendChild(container);

    Virtualizer.attach(container, {
      itemHeight: CARD_HEIGHT_PX,
      totalCount: 10000,
      renderItem: (i, node) => {
        const el = node || buildSampleCard(i);
        el.dataset.idx = String(i);
        return el;
      },
    });

    const firstWindow = new Set();
    Virtualizer.visibleIndex().forEach((_, idx) => firstWindow.add(idx));

    // Simulate scroll: container's bounding rect now reads as "scrolled past".
    stubContainer(container, { top: -5000 });
    Virtualizer.update({ totalCount: 10000 });

    const newWindow = new Set();
    Virtualizer.visibleIndex().forEach((_, idx) => newWindow.add(idx));

    // The window must have moved (some indices in the new window not in the original).
    const overlap = [...newWindow].filter(i => firstWindow.has(i)).length;
    expect(overlap).toBeLessThan(newWindow.size);
    // Window count still bounded.
    expect(newWindow.size).toBeLessThan(40);
  });

  it('pool size + visible nodes stays bounded across long scrolls', () => {
    const container = document.createElement('div');
    stubContainer(container, { top: 0 });
    document.body.appendChild(container);

    Virtualizer.attach(container, {
      itemHeight: CARD_HEIGHT_PX,
      totalCount: 10000,
      renderItem: (i, node) => {
        const el = node || buildSampleCard(i);
        el.dataset.idx = String(i);
        return el;
      },
    });

    // Scroll in 5 chunks.
    for (let s = 1; s <= 5; s++) {
      stubContainer(container, { top: -s * 2000 });
      Virtualizer.update({ totalCount: 10000 });
    }

    const visible = Virtualizer.visibleIndex().size;
    const pool = Virtualizer.poolSize();
    // Whole-app DOM card count is visible-only (pool nodes are reused via
    // renderItem swap, not retained as orphaned DOM).
    expect(visible).toBeLessThan(40);
    expect(visible + pool).toBeLessThan(80);
  });
});

describe('Virtualizer.update', () => {
  it('shrinking totalCount resizes the spacer and drops out-of-range cards', () => {
    const container = document.createElement('div');
    stubContainer(container, { top: 0 });
    document.body.appendChild(container);

    Virtualizer.attach(container, {
      itemHeight: CARD_HEIGHT_PX,
      totalCount: 10000,
      renderItem: (i, node) => node || buildSampleCard(i),
    });

    // Scroll deep, then filter shrinks list to 5.
    stubContainer(container, { top: -5000 });
    Virtualizer.update({ totalCount: 5 });

    const spacer = container.querySelector('.virt-spacer');
    expect(spacer.style.height).toBe(5 * CARD_HEIGHT_PX + 'px');
    // No visible index can be >= 5.
    Virtualizer.visibleIndex().forEach((_, idx) => {
      expect(idx).toBeLessThan(5);
    });
  });

  it('scrollToIndex calls window.scrollTo with the right target', () => {
    const container = document.createElement('div');
    stubContainer(container, { top: 100 });
    document.body.appendChild(container);

    Virtualizer.attach(container, {
      itemHeight: CARD_HEIGHT_PX,
      totalCount: 1000,
      renderItem: (i, node) => node || buildSampleCard(i),
    });
    window.scrollTo.mockClear();

    Virtualizer.update({ totalCount: 1000, scrollToIndex: 250 });
    expect(window.scrollTo).toHaveBeenCalledWith({
      top: 100 + 250 * CARD_HEIGHT_PX,
      behavior: 'auto',
    });
  });
});

describe('Virtualizer.detach', () => {
  it('clears state and stops responding to scroll', () => {
    const container = document.createElement('div');
    stubContainer(container, { top: 0 });
    document.body.appendChild(container);

    let renderCount = 0;
    Virtualizer.attach(container, {
      itemHeight: CARD_HEIGHT_PX,
      totalCount: 1000,
      renderItem: (i, node) => { renderCount++; return node || buildSampleCard(i); },
    });
    const baseline = renderCount;

    Virtualizer.detach();
    expect(Virtualizer.isAttached()).toBe(false);
    expect(Virtualizer.visibleIndex().size).toBe(0);
    expect(Virtualizer.poolSize()).toBe(0);

    // Fire a scroll event — no more renders should happen.
    window.dispatchEvent(new Event('scroll'));
    // Give RAF a chance (if any).
    return new Promise(r => setTimeout(r, 30)).then(() => {
      expect(renderCount).toBe(baseline);
    });
  });
});

describe('Virtualizer renderItem recycling', () => {
  it('reuses the same DOM node across different indices', () => {
    const container = document.createElement('div');
    stubContainer(container, { top: 0 });
    document.body.appendChild(container);

    const renderCalls = [];
    const trackedNodes = new Set();
    Virtualizer.attach(container, {
      itemHeight: CARD_HEIGHT_PX,
      totalCount: 10000,
      renderItem: (i, node) => {
        renderCalls.push({ i, recycled: !!node });
        let el = node;
        if (!el) {
          el = buildSampleCard(i);
          trackedNodes.add(el);
        }
        el.dataset.idx = String(i);
        return el;
      },
    });

    const initialPoolNodes = trackedNodes.size;

    // Scroll far enough that nodes recycle.
    stubContainer(container, { top: -10000 });
    Virtualizer.update({ totalCount: 10000 });

    const recycledCalls = renderCalls.filter(c => c.recycled);
    expect(recycledCalls.length).toBeGreaterThan(0);
    // Total node count stays bounded (< 2x viewport window); recycling, not
    // unbounded growth, is what keeps DOM size flat across long scrolls.
    expect(trackedNodes.size).toBeLessThan(40);
  });

  it('keeps node identity stable for indices that stay in window', () => {
    const container = document.createElement('div');
    stubContainer(container, { top: 0 });
    document.body.appendChild(container);

    Virtualizer.attach(container, {
      itemHeight: CARD_HEIGHT_PX,
      totalCount: 100,
      renderItem: (i, node) => {
        const el = node || buildSampleCard(i);
        el.dataset.idx = String(i);
        return el;
      },
    });

    // index 0 should be in the initial window.
    const node0Before = Virtualizer.visibleIndex().get(0);
    expect(node0Before).toBeTruthy();

    // Trigger a no-op re-render.
    Virtualizer.update({ totalCount: 100 });

    const node0After = Virtualizer.visibleIndex().get(0);
    expect(node0After).toBe(node0Before);
  });
});

// ── filteredTracks() returns indices (TASK-034) ───────────────────────────────
// Mirror of the production filter; verifies the indices contract.
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
  const cutoffISO = (lastPlayedFilter !== 'all' && lastPlayedFilter !== 'never')
    ? new Date(Date.now() - (lastPlayedFilter === '7d' ? 7 : 30) * 86400000).toISOString()
    : null;
  const q = searchQuery ? searchQuery.toLowerCase() : '';
  const out = [];
  for (let i = 0; i < parsedTracks.length; i++) {
    const t = parsedTracks[i];
    if (phraseOnlyFilter && !t.hasPhrase) continue;
    if (beatsOnlyFilter && !t.hasBeats) continue;
    if (q) {
      if (!((t.name || '').toLowerCase().includes(q) || (t.artist || '').toLowerCase().includes(q))) continue;
    }
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

describe('filteredTracks returns indices into parsedTracks', () => {
  const sample = [
    { id: '1', name: 'Acid Rain', artist: 'Burial', hasPhrase: true, hasBeats: true, rating: 5 },
    { id: '2', name: 'Midnight',  artist: 'Aphex',  hasPhrase: false, hasBeats: true, rating: 3 },
    { id: '3', name: 'Flux',      artist: 'Burial', hasPhrase: true, hasBeats: false, rating: 1 },
  ];

  it('returns a plain array of numeric indices', () => {
    const idx = filteredTracks(sample);
    expect(Array.isArray(idx)).toBe(true);
    expect(idx).toEqual([0, 1, 2]);
    for (const i of idx) expect(typeof i).toBe('number');
  });

  it('dereferenced indices match the original track objects', () => {
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

  it('sort applied to indices via dereference', () => {
    const idx = filteredTracks(sample);
    const byRating = idx
      .slice()
      .sort((a, b) => (sample[b].rating || 0) - (sample[a].rating || 0));
    expect(byRating.map(i => sample[i].id)).toEqual(['1', '2', '3']);
  });
});

// ── TASK-033: track-card uniform height ──────────────────────────────────────
describe('track-card height invariant', () => {
  it('all rendered cards have identical offsetHeight when CSS height is locked', () => {
    // Apply the same CSS lock that ships in docs/index.html.
    const style = document.createElement('style');
    style.textContent = `.track-card { height: ${CARD_HEIGHT_PX}px; box-sizing: border-box; overflow: hidden; padding: 12px 16px; }`;
    document.head.appendChild(style);
    const container = document.createElement('div');
    document.body.appendChild(container);
    for (let i = 0; i < 20; i++) {
      const c = buildSampleCard(i);
      // Some cards carry "more content" to verify overflow:hidden + fixed height clamp.
      if (i % 2 === 0) {
        for (let k = 0; k < 12; k++) c.appendChild(document.createElement('p'));
      }
      container.appendChild(c);
    }
    // jsdom doesn't lay out, but it does report computed-style height when set.
    for (const c of container.querySelectorAll('.track-card')) {
      const h = getComputedStyle(c).height;
      expect(h).toBe(`${CARD_HEIGHT_PX}px`);
    }
    document.head.removeChild(style);
  });
});
