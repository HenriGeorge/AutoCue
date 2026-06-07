/**
 * TASK-036 — Search input debounce via requestIdleCallback.
 *
 * Helper copied from docs/index.html — update both in lock-step.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'


function makeScheduler() {
  let _searchTimer = null;
  let _searchRic = null;
  return {
    schedule(fn) {
      if (typeof window.requestIdleCallback === 'function') {
        if (_searchRic !== null) window.cancelIdleCallback(_searchRic);
        _searchRic = window.requestIdleCallback(() => { _searchRic = null; fn(); }, { timeout: 80 });
        return;
      }
      clearTimeout(_searchTimer);
      _searchTimer = setTimeout(fn, 80);
    },
    _state() { return { _searchRic, _searchTimer } },
  };
}


describe('search-debounce scheduler', () => {
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); })

  it('coalesces multiple inputs into one rIC callback', () => {
    const calls = []
    const rics = []
    vi.stubGlobal('requestIdleCallback', (cb) => {
      rics.push(cb); return rics.length
    })
    vi.stubGlobal('cancelIdleCallback', (handle) => {
      rics[handle - 1] = null
    })
    const s = makeScheduler()
    s.schedule(() => calls.push('a'))
    s.schedule(() => calls.push('b'))
    s.schedule(() => calls.push('c'))
    // Only the LAST callback survived cancellation.
    const live = rics.filter(Boolean)
    expect(live.length).toBe(1)
    live[0]()
    expect(calls).toEqual(['c'])
  })

  it('falls back to setTimeout when requestIdleCallback unavailable', () => {
    vi.useFakeTimers()
    // Force absence of rIC.
    vi.stubGlobal('requestIdleCallback', undefined)
    const calls = []
    const s = makeScheduler()
    s.schedule(() => calls.push('first'))
    s.schedule(() => calls.push('second'))
    // Only second timer should fire.
    vi.advanceTimersByTime(80)
    expect(calls).toEqual(['second'])
  })

  it('rIC timeout is 80ms', () => {
    let captured = null
    vi.stubGlobal('requestIdleCallback', (cb, opts) => { captured = opts; return 1 })
    vi.stubGlobal('cancelIdleCallback', () => {})
    const s = makeScheduler()
    s.schedule(() => {})
    expect(captured?.timeout).toBe(80)
  })
})
