/**
 * Tests for the in-page _perf helper.
 *
 * The helper is copied verbatim from docs/index.html — if it changes
 * there, update the copy below. See .agent/prd/PERFORMANCE_PRD.md
 * TASK-049 / TASK-050.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// jsdom's localStorage in this Vitest config is missing the standard API; stub it.
if (typeof localStorage === 'undefined' || typeof localStorage.getItem !== 'function') {
  const store = {}
  // eslint-disable-next-line no-global-assign
  globalThis.localStorage = {
    getItem: (k) => Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null,
    setItem: (k, v) => { store[k] = String(v) },
    removeItem: (k) => { delete store[k] },
    clear: () => { for (const k of Object.keys(store)) delete store[k] },
  }
}

// ── Helper, copied from docs/index.html ──────────────────────────────────

function makePerf() {
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
}

// ── Tests ────────────────────────────────────────────────────────────────

describe('_perf helper', () => {
  let perf
  let markSpy, measureSpy, getByNameSpy, getByTypeSpy, logSpy, clearMarksSpy, clearMeasuresSpy

  beforeEach(() => {
    localStorage.clear()
    perf = makePerf()
    markSpy = vi.spyOn(performance, 'mark').mockImplementation(() => {})
    measureSpy = vi.spyOn(performance, 'measure').mockImplementation(() => {})
    getByNameSpy = vi.spyOn(performance, 'getEntriesByName').mockReturnValue([
      { name: 'autocue:work', duration: 12.34 },
    ])
    getByTypeSpy = vi.spyOn(performance, 'getEntriesByType').mockReturnValue([
      { name: 'autocue:work', duration: 12.34 },
      { name: 'other:foo', duration: 99 },
    ])
    clearMarksSpy = vi.spyOn(performance, 'clearMarks').mockImplementation(() => {})
    clearMeasuresSpy = vi.spyOn(performance, 'clearMeasures').mockImplementation(() => {})
    logSpy = vi.spyOn(console, 'log').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('mark is a no-op when localStorage flag is unset', () => {
    perf.mark('work')
    expect(markSpy).not.toHaveBeenCalled()
  })

  it('measure is a no-op and returns null when localStorage flag is unset', () => {
    expect(perf.measure('work', 'work-start')).toBeNull()
    expect(measureSpy).not.toHaveBeenCalled()
  })

  it('mark calls performance.mark with autocue prefix when enabled', () => {
    localStorage.setItem('autocue_perf', '1')
    perf.mark('library-load')
    expect(markSpy).toHaveBeenCalledWith('autocue:library-load')
  })

  it('measure logs [AutoCue Perf] line and returns entry when enabled', () => {
    localStorage.setItem('autocue_perf', '1')
    const result = perf.measure('work', 'work-start')
    expect(measureSpy).toHaveBeenCalledWith('autocue:work', 'autocue:work-start')
    expect(logSpy).toHaveBeenCalledWith(expect.stringContaining('[AutoCue Perf] work: 12.34ms'))
    expect(result).not.toBeNull()
  })

  it('getEntries filters to autocue-prefixed measures only', () => {
    localStorage.setItem('autocue_perf', '1')
    const entries = perf.getEntries()
    expect(entries.length).toBe(1)
    expect(entries[0].name).toBe('autocue:work')
  })

  it('clear() invokes both clearMarks and clearMeasures', () => {
    perf.clear()
    expect(clearMarksSpy).toHaveBeenCalled()
    expect(clearMeasuresSpy).toHaveBeenCalled()
  })

  it('does not throw when performance API throws', () => {
    localStorage.setItem('autocue_perf', '1')
    markSpy.mockImplementation(() => { throw new Error('not supported') })
    measureSpy.mockImplementation(() => { throw new Error('not supported') })
    expect(() => perf.mark('x')).not.toThrow()
    expect(() => perf.measure('x', 'y')).not.toThrow()
  })

  it('does not throw when localStorage throws', () => {
    const original = globalThis.localStorage.getItem
    globalThis.localStorage.getItem = () => { throw new Error('storage error') }
    try {
      expect(perf.enabled()).toBe(false)
      expect(() => perf.mark('x')).not.toThrow()
    } finally {
      globalThis.localStorage.getItem = original
    }
  })
})
