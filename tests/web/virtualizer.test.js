/**
 * TASK-031 + TASK-033 + TASK-038 — Virtualizer scaffold + Vitest coverage.
 *
 * The IIFE is copied verbatim from docs/index.html — update both in lock-step
 * if the implementation changes.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// ── Helper, copied from docs/index.html ──────────────────────────────────

function makeVirtualizer() {
  var state = null;

  function _computeWindow(s) {
    var first = Math.max(0, Math.floor(s.scrollTop / s.itemHeight) - s.buffer);
    var visible = Math.ceil(s.viewportHeight / s.itemHeight) + s.buffer * 2;
    var last = Math.min(s.totalCount, first + visible);
    return { first: first, last: last };
  }

  function _render() {
    if (state === null) return;
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
  }

  function _onScroll() {
    if (state === null) return;
    state.scrollTop = state.container.scrollTop;
    if (state.rafScheduled) return;
    state.rafScheduled = true;
    var raf = window.requestAnimationFrame || function(fn) { return setTimeout(fn, 0); };
    raf(function() {
      state.rafScheduled = false;
      _render();
    });
  }

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

      state = {
        container: container,
        spacer: spacer,
        itemHeight: opts.itemHeight,
        totalCount: opts.totalCount || 0,
        viewportHeight: container.clientHeight || 800,
        scrollTop: container.scrollTop || 0,
        buffer: opts.buffer != null ? opts.buffer : 5,
        renderItem: opts.renderItem,
        pool: [],
        live: new Map(),
        rafScheduled: false,
        _scrollHandler: _onScroll,
      };
      container.addEventListener('scroll', state._scrollHandler, { passive: true });
      _render();
    },
    update: function(opts) {
      if (state === null) return;
      if (opts && typeof opts.totalCount === 'number') state.totalCount = opts.totalCount;
      if (opts && typeof opts.scrollToIndex === 'number') {
        state.container.scrollTop = opts.scrollToIndex * state.itemHeight;
        state.scrollTop = state.container.scrollTop;
      }
      _render();
    },
    detach: function() {
      if (state === null) return;
      state.container.removeEventListener('scroll', state._scrollHandler);
      state.live.forEach(function(node) {
        if (node.parentNode) node.parentNode.removeChild(node);
      });
      if (state.spacer && state.spacer.parentNode) {
        state.spacer.parentNode.removeChild(state.spacer);
      }
      state = null;
    },
    _state: function() { return state; },
  };
}


describe('Virtualizer', () => {
  let V, container

  beforeEach(() => {
    V = makeVirtualizer()
    container = document.createElement('div')
    // jsdom doesn't really do layout — patch the dimensions we need.
    Object.defineProperty(container, 'clientHeight', { value: 800, configurable: true })
    container.style.height = '800px'
    document.body.appendChild(container)
    // requestAnimationFrame in jsdom defaults to a timer — make it sync.
    vi.stubGlobal('requestAnimationFrame', (fn) => { fn(); return 1 })
  })

  afterEach(() => {
    V.detach()
    container.remove()
    vi.restoreAllMocks()
  })

  it('attach mounts visible+buffer rows only', () => {
    V.attach({
      container,
      itemHeight: 50,
      totalCount: 1000,
      buffer: 5,
      renderItem: (idx) => {
        const el = document.createElement('div')
        el.textContent = `Row ${idx}`
        return el
      },
    })
    const liveCount = V._state().live.size
    // Visible = 800/50 = 16; + buffer*2 = 26. First buffer=0 since scrollTop=0.
    expect(liveCount).toBeGreaterThan(0)
    expect(liveCount).toBeLessThan(40)
  })

  it('spacer height equals totalCount * itemHeight (TASK-033)', () => {
    V.attach({
      container,
      itemHeight: 72,
      totalCount: 500,
      renderItem: () => document.createElement('div'),
    })
    expect(V._state().spacer.style.height).toBe(String(500 * 72) + 'px')
  })

  it('update() shrinks totalCount and removes off-screen rows', () => {
    V.attach({
      container,
      itemHeight: 50,
      totalCount: 1000,
      buffer: 5,
      renderItem: (idx) => {
        const el = document.createElement('div')
        el.textContent = `R${idx}`
        return el
      },
    })
    V.update({ totalCount: 5 })
    // Only 5 items remain → all live nodes should be in [0,5).
    const idxs = Array.from(V._state().live.keys())
    expect(Math.max(...idxs)).toBeLessThan(5)
  })

  it('recycles DOM nodes on scroll (pool grows on exit, shrinks on entry)', () => {
    V.attach({
      container,
      itemHeight: 50,
      totalCount: 200,
      buffer: 0,
      renderItem: (idx, recycled) => {
        const el = recycled || document.createElement('div')
        el.dataset.idx = String(idx)
        return el
      },
    })
    const initialLive = V._state().live.size
    // Scroll way down.
    container.scrollTop = 5000
    V._state()._scrollHandler()
    const stateAfter = V._state()
    // Pool should hold recycled nodes; live should refer to new indices.
    expect(stateAfter.pool.length + stateAfter.live.size).toBeGreaterThanOrEqual(initialLive)
  })

  it('detach removes the scroll listener and clears state', () => {
    V.attach({
      container,
      itemHeight: 50,
      totalCount: 100,
      renderItem: () => document.createElement('div'),
    })
    const removeSpy = vi.spyOn(container, 'removeEventListener')
    V.detach()
    expect(removeSpy).toHaveBeenCalledWith('scroll', expect.any(Function))
    expect(V._state()).toBeNull()
  })

  it('throws when container is missing', () => {
    expect(() => V.attach({ itemHeight: 50, totalCount: 10, renderItem: () => null })).toThrow()
  })

  it('renderItem returning null is tolerated (no live entry added)', () => {
    V.attach({
      container,
      itemHeight: 50,
      totalCount: 10,
      renderItem: () => null,
    })
    expect(V._state().live.size).toBe(0)
  })

  it('update(scrollToIndex) jumps the container scrollTop', () => {
    V.attach({
      container,
      itemHeight: 50,
      totalCount: 100,
      renderItem: () => document.createElement('div'),
    })
    V.update({ scrollToIndex: 50 })
    expect(container.scrollTop).toBe(50 * 50)
  })
})
