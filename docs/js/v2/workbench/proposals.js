/**
 * AutoCue 2.0 — workbench proposal organ (P2 "F organ").
 *
 * Owns the per-track *approval* set for PROPOSED (pending) cues. When the user
 * clicks Preview, legacy populates `pendingCues` (read via ACBridge.pending());
 * each pending track renders a PROPOSAL stamp + an approve-tick in its CUES
 * cell (see buildWbRow in 06-render.js). A freshly-pending track is NOT approved
 * by default — the user must tick it. Apply is then gated to approved∩pending
 * (see ACBridge.approvedApplyIds() + applyToRekordbox()).
 *
 * Interop: reads legacy via window.ACBridge; exposes via window.AC2.proposals.
 * Read-only against legacy state — the only mutation is our own `_approved` Set.
 */

const _approved = new Set();

function _pending() {
  try {
    return (window.ACBridge && window.ACBridge.pending()) || {};
  } catch (_) {
    return {};
  }
}

function _isPending(id) {
  const p = _pending();
  const arr = p[String(id)];
  return Array.isArray(arr) && arr.length > 0;
}

/** True when this track's proposed cues have been approved by the user. */
export function isApproved(id) {
  return _approved.has(String(id));
}

/** Flip a track's approval; returns the new state. */
export function toggleApprove(id) {
  const key = String(id);
  if (_approved.has(key)) _approved.delete(key);
  else _approved.add(key);
  return _approved.has(key);
}

/**
 * Track-ids (as Strings) that are BOTH pending AND approved — the exact set the
 * Apply payload should write when proposals exist. Drops any approval whose
 * pending entry has since been cleared (e.g. a re-Preview shrank the set).
 */
export function approvedIntersectPending() {
  const p = _pending();
  const out = [];
  for (const key of _approved) {
    const arr = p[key];
    if (Array.isArray(arr) && arr.length > 0) out.push(key);
  }
  return out;
}

/** Clear all approvals (used when pending is cleared after Apply). */
export function resetApprovals() {
  _approved.clear();
}

/**
 * Paint one row's approve-tick `.on` state from `_approved`, without a full
 * re-render. Falls back to a re-render via ACBridge.renderTracks() when the row
 * isn't mounted (off-screen under virtualization).
 */
function _repaintRow(id) {
  const list = document.getElementById('track-list');
  const row = list && list.querySelector('.wb-row[data-track-id="' + CSS.escape(String(id)) + '"]');
  const tick = row && row.querySelector('.wb-approve-tick');
  if (tick) {
    const on = isApproved(id);
    tick.classList.toggle('on', on);
    tick.setAttribute('aria-pressed', on ? 'true' : 'false');
    return;
  }
  // Row not currently mounted — rebuild so its tick reflects the new state.
  try {
    window.ACBridge && window.ACBridge.renderTracks();
  } catch (_) {}
}

/**
 * Delegate clicks on `#track-list .wb-approve-tick` in the CAPTURE phase so the
 * tick toggles approval *before* the inspector's own capture-phase listener can
 * focus the row. We stopPropagation to be doubly sure the inspector never sees
 * the click (it also excludes <button> targets, but belt-and-suspenders).
 */
export function initProposals() {
  const list = document.getElementById('track-list');
  if (!list) return;
  list.addEventListener(
    'click',
    (e) => {
      const tick = e.target.closest && e.target.closest('.wb-approve-tick');
      if (!tick) return;
      if (!document.body.classList.contains('wb-active')) return;
      e.stopPropagation();
      e.preventDefault();
      const row = tick.closest('[data-track-id]');
      if (!row) return;
      const id = row.dataset.trackId;
      // Only meaningful while the track is actually pending.
      if (!_isPending(id)) return;
      toggleApprove(id);
      _repaintRow(id);
    },
    true
  );
}

// Expose for legacy interop (08-set-builder-boot.js bridge accessor).
window.AC2 = window.AC2 || {};
window.AC2.proposals = {
  isApproved,
  toggleApprove,
  approvedIntersectPending,
  resetApprovals,
  initProposals,
};
