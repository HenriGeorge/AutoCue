/**
 * AutoCue 2.0 — Duplicates place (P3).
 *
 * A rail place that swaps the workbench centre pane from the track grid to the
 * duplicates view. This module owns ONLY the door (rail entry), the swap, and
 * the lazy first scan — every scan and every write delegates to the legacy
 * machinery in docs/js/02-local-ops.js via window.ACBridge (.scanDuplicates /
 * .openDuplicatesConfirm / .onTracksDeleted). NO parallel implementation: this
 * file never fetches the duplicates endpoints itself (R6 — guarded by a
 * source-contract test that bans the endpoint path and network calls).
 *
 * Swap contract (TASK-033/037): toggle `hidden` + body.wb-place-dupes only.
 * #track-list is NEVER detached or re-parented — the Virtualizer's
 * document-level scroll source, the #tracks-sticky pin and the
 * IntersectionObserver shadow all survive a hide/show round-trip.
 * deactivate() calls ACBridge.renderTracks() so the grid repaints at the
 * current scroll position when it returns.
 */

import { clearInspector } from './inspector.js';

// Centre-pane elements hidden while the place is active. #wb-grid-head lives
// inside #tracks-sticky, but hide it explicitly too (belt and braces against
// a legacy style.display write on the sticky bar).
const HIDE_IDS = ['tracks-sticky', 'track-list', 'wb-grid-head', 'wb-inspector'];

let _active = false;
let _scannedOnce = false;

export function isActive() { return _active; }

function _announce() {
  // Shell + rail repaint their active states off this (e.g. crates paint no
  // `.active` row while a place owns the centre pane).
  try { window.dispatchEvent(new CustomEvent('autocue:wb-place-change')); } catch (_) {}
}

export function activate() {
  if (_active) return;
  if (!(window.ACBridge && window.ACBridge.isLocalMode())) return;
  // P5 mutual exclusion: only one place owns the centre. Leave Discover first.
  if (window.AC2 && window.AC2.discover) window.AC2.discover.deactivate();
  if (window.AC2 && window.AC2.library) window.AC2.library.deactivate();
  _active = true;
  // The inspector describes a grid row — clear it before hiding the pane.
  clearInspector();
  for (const id of HIDE_IDS) document.getElementById(id)?.setAttribute('hidden', '');
  document.getElementById('wb-dupes-pane')?.removeAttribute('hidden');
  document.body.classList.add('wb-place-dupes');
  document.getElementById('wb-dupes-place')?.classList.add('active');
  _announce();
  // Lazy first scan: scan once on first entry; the Rescan pill covers the
  // rest. Delegated — the legacy SSE reader owns the fetch. Gated on the
  // duplicates hosts actually living inside the pane (T3 markup move), so the
  // T2 scaffold stays inert.
  const pane = document.getElementById('wb-dupes-pane');
  const list = document.getElementById('duplicates-list');
  if (!_scannedOnce && pane && list && pane.contains(list)
      && window.ACBridge && window.ACBridge.scanDuplicates) {
    _scannedOnce = true;
    window.ACBridge.scanDuplicates();
  }
}

export function deactivate() {
  if (!_active) return;
  _active = false;
  for (const id of HIDE_IDS) document.getElementById(id)?.removeAttribute('hidden');
  document.getElementById('wb-dupes-pane')?.setAttribute('hidden', '');
  document.body.classList.remove('wb-place-dupes');
  document.getElementById('wb-dupes-place')?.classList.remove('active');
  // Repaint the re-shown grid at the current scroll position.
  if (window.ACBridge) window.ACBridge.renderTracks();
  _announce();
}

export function initDuplicatesPlace() {
  const btn = document.getElementById('wb-dupes-place');
  if (!btn) return;
  btn.addEventListener('click', () => {
    // Re-clicking the active place is the no-new-id exit back to the grid.
    if (_active) deactivate();
    else activate();
  });
}
