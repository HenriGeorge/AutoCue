/**
 * Tests for the duplicates-delete confirm modal interlock.
 *
 * The primary "Delete" button is disabled for 250 ms after the modal
 * opens to defeat an accidental Enter held over from the previous focus
 * target (the per-group delete button) — same pattern as the Discover
 * download-confirm dialog.
 *
 * Mirrors `_openDuplicatesConfirm` in docs/index.html. Keep in sync.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

// Vendored helper — matches docs/index.html.
function openDuplicatesConfirm(state) {
  state.modalAriaHidden = 'false'
  state.backdropAriaHidden = 'false'
  state.goButtonDisabled = true
  state.goButtonText = 'Delete'
  clearTimeout(state.primaryTimer)
  state.primaryTimer = setTimeout(() => {
    state.goButtonDisabled = false
  }, 250)
  // Default focus to Cancel (the second safety layer).
  state.focused = 'cancel'
}

function closeDuplicatesConfirm(state) {
  state.modalAriaHidden = 'true'
  state.backdropAriaHidden = 'true'
  clearTimeout(state.primaryTimer)
  state.pending = null
}

describe('_openDuplicatesConfirm interlock', () => {
  let state

  beforeEach(() => {
    vi.useFakeTimers()
    state = {
      modalAriaHidden: 'true',
      backdropAriaHidden: 'true',
      goButtonDisabled: false,
      goButtonText: 'Delete',
      focused: null,
      primaryTimer: null,
      pending: null,
    }
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('opens the modal and backdrop', () => {
    openDuplicatesConfirm(state)
    expect(state.modalAriaHidden).toBe('false')
    expect(state.backdropAriaHidden).toBe('false')
  })

  it('starts with the primary button DISABLED', () => {
    openDuplicatesConfirm(state)
    expect(state.goButtonDisabled).toBe(true)
  })

  it('keeps the primary button disabled for the full 250 ms', () => {
    openDuplicatesConfirm(state)
    vi.advanceTimersByTime(249)
    expect(state.goButtonDisabled).toBe(true)
  })

  it('enables the primary button after 250 ms', () => {
    openDuplicatesConfirm(state)
    vi.advanceTimersByTime(250)
    expect(state.goButtonDisabled).toBe(false)
  })

  it('defaults focus to Cancel (second safety layer)', () => {
    openDuplicatesConfirm(state)
    expect(state.focused).toBe('cancel')
  })

  it('cancelling clears the enable timer so a re-open re-arms the 250 ms', () => {
    openDuplicatesConfirm(state)
    // Cancel before the 250 ms elapses.
    vi.advanceTimersByTime(100)
    closeDuplicatesConfirm(state)
    // Re-open — interlock must restart, NOT carry over the original
    // 150 ms remaining.
    openDuplicatesConfirm(state)
    vi.advanceTimersByTime(150)
    expect(state.goButtonDisabled).toBe(true) // would have enabled if timer leaked
    vi.advanceTimersByTime(100)
    expect(state.goButtonDisabled).toBe(false)
  })

  it('Escape on a closed modal is a no-op (handler is idempotent)', () => {
    closeDuplicatesConfirm(state)
    expect(state.modalAriaHidden).toBe('true')
    closeDuplicatesConfirm(state)
    expect(state.modalAriaHidden).toBe('true')
  })
})
