/**
 * AutoCue 2.0 — Discover place (P5).
 *
 * A rail place that swaps the workbench centre pane from the track grid to the
 * restyled Discover release feed. This module owns ONLY the door (rail entry),
 * the centre-pane swap, and the lazy first load — every scan / save / dismiss /
 * snooze / detail flows through the legacy DiscoverV2 IIFE (exposed as
 * window.DiscoverV2) and its existing grid delegation + detail buttons. NO
 * parallel implementation: this file never fetches the Discover REST surface
 * itself (R10 — guarded by a source-contract test that bans the endpoint path
 * + any bare network call).
 *
 * Swap contract (OQ3): unlike the Duplicates place (which toggles `hidden` on a
 * sibling pane inside the SAME tab body), Discover lives in a DIFFERENT tab
 * content block (#discover-tab-content). It MUST be shown via switchTab so:
 *   - #discover-tab-content's display state drives _handleDiscoverKeydown's
 *     `#disc-v2-section offsetParent !== null` visibility guard (the whole
 *     j/k/Enter/s/x/z/D/?/Esc map), and
 *   - the seven overlays initDiscoverV2 re-parents to <body> keep resolving
 *     their position:fixed against the viewport.
 * Relocating #disc-v2-grid into the centre would break both, so we reuse
 * switchTab('discover') to show the block in place; the rail + inspector are
 * position:fixed flanks, so the centre column already accommodates it.
 *
 * switchTab-scroll decision (review note 1): switchTab('cues') on deactivate
 * resets the document scroll to top — i.e. returning to the grid does NOT
 * preserve the prior cue-grid scroll position. ACCEPTED consciously: switchTab
 * is mandatory on the *entry* leg (the offsetParent keyboard-guard invariant),
 * and reusing the symmetric switchTab('cues') on the *return* leg is far
 * lower-risk than hand-reimplementing switchTab's five side effects (display,
 * tab-entering anim, .tab-btn.active, #download-bar hide, body[data-active-tab])
 * just to keep scroll. The grid re-virtualizes from the top fine. Documented in
 * the PR.
 *
 * #track-list is NEVER detached or re-parented (TASK-033/037) — only its
 * containing tab body is display-toggled by switchTab.
 */

import { clearInspector, renderReleaseInspector, setInspectorMode, inspectorMode } from './inspector.js';

// Centre-pane elements hidden while the place is active. switchTab('discover')
// already display:none's #cues-tab-content (which contains all three), so this
// is a defensive backstop against a legacy style.display write on the sticky
// bar — NOT the load-bearing hide (the CSS body-class backstop + switchTab are).
const HIDE_IDS = ['tracks-sticky', 'track-list', 'wb-grid-head'];

let _active = false;
let _loadedOnce = false;
let _escHandler = null;

export function isActive() { return _active; }

// Escape clears a focused release back to the inspector empty state. Scoped:
// only installed while the place is active, only acts when a release is
// re-hosted (mode 'release'), and yields to any open Discover dialog (the
// detail-panel/snooze/help handlers run in capture and stop their own keys —
// but with the place active the slide-in is suppressed, so the only release
// surface is the inspector). Installed in the bubble phase so legacy capture
// dialogs win first.
function _onKeydown(ev) {
  if (ev.key !== 'Escape') return;
  if (inspectorMode() !== 'release') return;
  clearInspector();              // resets mode → 'track' + empty state
  document.getElementById('wb-inspector')?.removeAttribute('hidden');
}

function _announce() {
  // Shell + rail repaint their active states off this (e.g. crates paint no
  // `.active` row while a place owns the centre pane).
  try { window.dispatchEvent(new CustomEvent('autocue:wb-place-change')); } catch (_) {}
}

export function activate() {
  if (_active) return;
  if (!(window.ACBridge && window.ACBridge.isLocalMode())) return;
  // Mutual exclusion: only one place owns the centre. Leaving Duplicates first.
  if (window.AC2 && window.AC2.duplicates) window.AC2.duplicates.deactivate();
  _active = true;
  // The inspector describes a grid row — clear it before re-hosting release
  // detail (the focus path un-hides + repopulates it in 'release' mode, T3).
  clearInspector();
  // Show the Discover tab body in the centre column (keyboard-guard invariant).
  if (window.switchTab) window.switchTab('discover');
  for (const id of HIDE_IDS) document.getElementById(id)?.setAttribute('hidden', '');
  // The inspector is empty until a release is focused — hide it for now.
  document.getElementById('wb-inspector')?.setAttribute('hidden', '');
  document.body.classList.add('wb-place-disc');
  document.getElementById('wb-disc-place')?.classList.add('active');
  if (!_escHandler) { _escHandler = _onKeydown; document.addEventListener('keydown', _escHandler); }
  _announce();
  // Lazy first load: Discover already loads its state at boot
  // (initDiscoverV2), so this is normally a no-op refresh — but the
  // _loadedOnce guard makes the place self-sufficient if entered before boot
  // completes. Delegated; never fetch the Discover REST surface directly.
  if (!_loadedOnce && window.ACBridge && window.ACBridge.discoverLoadInitialState) {
    _loadedOnce = true;
    try { window.ACBridge.discoverLoadInitialState(); } catch (_) {}
  }
}

export function deactivate() {
  if (!_active) return;
  _active = false;
  // Return to the cue grid (scroll-to-top accepted — see header).
  if (window.switchTab) window.switchTab('cues');
  for (const id of HIDE_IDS) document.getElementById(id)?.removeAttribute('hidden');
  document.getElementById('wb-inspector')?.removeAttribute('hidden');
  document.body.classList.remove('wb-place-disc');
  document.getElementById('wb-disc-place')?.classList.remove('active');
  if (_escHandler) { document.removeEventListener('keydown', _escHandler); _escHandler = null; }
  // Reset the inspector back to 'track' mode + empty state.
  clearInspector();
  // Repaint the re-shown grid.
  if (window.ACBridge) window.ACBridge.renderTracks();
  _announce();
}

// T3: a release card was focused in the feed — re-host its detail in the
// inspector (mode 'release'). Called by the legacy _openDetailPanel guard when
// the place is active, and by the place's own grid focus listener.
export function focusRelease(releaseKey) {
  if (!_active || !releaseKey) return;
  document.getElementById('wb-inspector')?.removeAttribute('hidden');
  setInspectorMode('release');
  renderReleaseInspector(releaseKey);
}

export function initDiscoverPlace() {
  const btn = document.getElementById('wb-disc-place');
  if (!btn) return;
  btn.addEventListener('click', () => {
    // Re-clicking the active place is the no-new-id exit back to the grid.
    if (_active) deactivate();
    else activate();
  });
  // T3: focusing a release card in the feed re-hosts its detail in the
  // inspector. Delegated, active-only; the legacy grid delegation still owns
  // the action buttons (save/dismiss/snooze) + Shift-click download.
  const grid = document.getElementById('disc-v2-grid');
  if (grid) {
    grid.addEventListener('click', (ev) => {
      if (!_active) return;
      if (ev.target.closest('[data-act]')) return; // action buttons keep their own path
      if (ev.shiftKey) return;                       // Shift-click download keeps its path
      const card = ev.target.closest('.disc-v2-card[data-release-key]');
      if (!card) return;
      focusRelease(card.getAttribute('data-release-key'));
    });
    grid.addEventListener('focusin', (ev) => {
      if (!_active) return;
      const card = ev.target.closest('.disc-v2-card[data-release-key]');
      if (!card) return;
      focusRelease(card.getAttribute('data-release-key'));
    });
  }
}
