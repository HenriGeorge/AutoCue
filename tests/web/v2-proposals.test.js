/**
 * P2 "F organ" — workbench proposal-approval pure logic.
 *
 * proposals.js owns the per-track approved Set. Apply is gated to
 * approved∩pending. These are the load-bearing pure helpers:
 *   - toggleApprove(id)            flips a track's approval, returns new state
 *   - isApproved(id)               reflects the Set
 *   - approvedIntersectPending()   only ids that are BOTH approved AND still
 *                                  pending (drops approvals whose pending
 *                                  entry vanished, e.g. a re-Preview shrank it)
 *
 * The module reads pending state via window.ACBridge.pending(), so we stub it.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import {
  isApproved,
  toggleApprove,
  approvedIntersectPending,
  resetApprovals,
} from '../../docs/js/v2/workbench/proposals.js'

// Stub the legacy bridge the module reads. id keys are Strings (as the real
// pendingCues uses), values are non-empty arrays = "pending".
let PENDING = {}
beforeEach(() => {
  PENDING = {}
  window.ACBridge = { pending: () => PENDING }
  resetApprovals()
})

function setPending(ids) {
  PENDING = {}
  for (const id of ids) PENDING[String(id)] = [{ slot: 'A', posSec: 0 }]
}

describe('toggleApprove / isApproved', () => {
  it('a freshly-pending track is NOT approved by default', () => {
    setPending([1, 2])
    expect(isApproved(1)).toBe(false)
    expect(isApproved(2)).toBe(false)
  })

  it('toggle flips approval and returns the new state', () => {
    expect(toggleApprove(1)).toBe(true)
    expect(isApproved(1)).toBe(true)
    expect(toggleApprove(1)).toBe(false)
    expect(isApproved(1)).toBe(false)
  })

  it('normalises numeric vs string ids to the same key', () => {
    toggleApprove(7)
    expect(isApproved('7')).toBe(true)
    expect(isApproved(7)).toBe(true)
  })
})

describe('approvedIntersectPending', () => {
  it('is empty when nothing is approved (even if tracks are pending)', () => {
    setPending([1, 2, 3])
    expect(approvedIntersectPending()).toEqual([])
  })

  it('returns only ids that are BOTH approved and pending', () => {
    setPending([1, 2, 3])
    toggleApprove(1)
    toggleApprove(3)
    expect(approvedIntersectPending().sort()).toEqual(['1', '3'])
  })

  it('drops an approval whose pending entry has since vanished', () => {
    setPending([1, 2])
    toggleApprove(1)
    toggleApprove(2)
    // Re-Preview shrank pending to just track 2.
    setPending([2])
    expect(approvedIntersectPending()).toEqual(['2'])
  })

  it('ignores a pending entry that is an empty array', () => {
    PENDING = { 1: [], 2: [{ slot: 'A' }] }
    toggleApprove(1)
    toggleApprove(2)
    expect(approvedIntersectPending()).toEqual(['2'])
  })
})

describe('resetApprovals', () => {
  it('clears the whole approved set', () => {
    setPending([1, 2])
    toggleApprove(1)
    toggleApprove(2)
    resetApprovals()
    expect(isApproved(1)).toBe(false)
    expect(approvedIntersectPending()).toEqual([])
  })
})
