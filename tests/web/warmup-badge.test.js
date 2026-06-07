/**
 * TASK-029 — UI: warm-up progress badge.
 *
 * Copy of the _warmupPoll helper from docs/index.html — update both in
 * lock-step if the implementation changes.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

// jsdom in this Vitest config has a broken localStorage; not needed here.

function makeWarmupPoll() {
  let handle = null
  function _stop() {
    if (handle !== null) { clearInterval(handle); handle = null }
  }
  function _tick() {
    return fetch('/api/warmup').then(function(r) {
      if (!r.ok) return null
      return r.json()
    }).then(function(j) {
      if (!j) return
      const sep = document.querySelector('.status-warmup-sep')
      const chip = document.getElementById('status-warmup')
      const text = document.getElementById('warmup-progress-text')
      if (!chip || !text) return
      if (j.step === 'done' || j.step === 'unknown') {
        chip.style.display = 'none'
        if (sep) sep.style.display = 'none'
        _stop()
        return
      }
      chip.style.display = ''
      if (sep) sep.style.display = ''
      const done = (j.done || 0).toLocaleString()
      const total = (j.total || 0).toLocaleString()
      text.textContent = done + ' / ' + total
    }).catch(function() { /* keep polling */ })
  }
  return {
    start: function() {
      if (handle !== null) return
      _tick()
      handle = setInterval(_tick, 2000)
    },
    stop: _stop,
  }
}


describe('_warmupPoll', () => {
  let chip, text, sep

  beforeEach(() => {
    document.body.innerHTML = `
      <span class="status-warmup-sep" style="display:none">·</span>
      <span id="status-warmup" style="display:none">
        <span id="warmup-progress-text">0 / 0</span>
      </span>
    `
    chip = document.getElementById('status-warmup')
    text = document.getElementById('warmup-progress-text')
    sep = document.querySelector('.status-warmup-sep')
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.useRealTimers()
  })

  it('updates badge text while step is running', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, json: () => Promise.resolve({ step: 'cache', done: 250, total: 1000, finished_at: null }),
    })))
    const poll = makeWarmupPoll()
    poll.start()
    await new Promise(r => setTimeout(r, 0))
    expect(chip.style.display).toBe('')
    expect(sep.style.display).toBe('')
    expect(text.textContent).toBe('250 / 1,000')
    poll.stop()
  })

  it('hides badge and stops polling on done step', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, json: () => Promise.resolve({ step: 'done', done: 1, total: 1, finished_at: '2026-06-07T19:00:00Z' }),
    })))
    chip.style.display = ''
    sep.style.display = ''
    const poll = makeWarmupPoll()
    poll.start()
    await new Promise(r => setTimeout(r, 0))
    expect(chip.style.display).toBe('none')
    expect(sep.style.display).toBe('none')
  })

  it('hides badge on unknown step', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, json: () => Promise.resolve({ step: 'unknown', done: 0, total: 0, finished_at: null }),
    })))
    chip.style.display = ''
    const poll = makeWarmupPoll()
    poll.start()
    await new Promise(r => setTimeout(r, 0))
    expect(chip.style.display).toBe('none')
  })

  it('start is idempotent — second call is no-op', () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, json: () => Promise.resolve({ step: 'cache', done: 0, total: 0 }),
    })))
    vi.useFakeTimers()
    const poll = makeWarmupPoll()
    poll.start()
    poll.start()
    // setInterval should have been called only once.
    expect(vi.getTimerCount()).toBe(1)
    poll.stop()
  })

  it('does not throw on fetch network errors', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('network down'))))
    const poll = makeWarmupPoll()
    expect(() => poll.start()).not.toThrow()
    await new Promise(r => setTimeout(r, 0))
    poll.stop()
  })

  it('does not throw on non-ok response', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: false, json: () => Promise.resolve({}) })))
    const poll = makeWarmupPoll()
    expect(() => poll.start()).not.toThrow()
    await new Promise(r => setTimeout(r, 0))
    poll.stop()
  })
})
