/**
 * AutoCue 2.0 — Library place (tab-bar retirement).
 *
 * A rail place that swaps the workbench centre pane from the track grid to the
 * Library tools surface (`#library-tab-content`: Library Health, Cue Library
 * Tools, Discogs Genre Tags, Comment Enrichment, Playlist Suggest, Set Builder).
 * Library is the LAST surface that still lived behind the legacy `#tab-nav`
 * Cues/Library tab bar — moving it into the rail (next to Discover + Duplicates)
 * lets the tab bar be retired, so navigation is entirely the workbench rail +
 * ⌘K + crates.
 *
 * Swap contract: like the Discover place (and unlike Duplicates, which toggles a
 * sibling pane in the same tab body), Library lives in its OWN tab-content block
 * (`#library-tab-content`), so it is shown via `switchTab('library')` — that
 * display-toggles `#cues-tab-content` (which contains `#track-list` +
 * `#tracks-sticky`) and shows the library tools in place. Every tool keeps its
 * own behaviour (health scan, cue tools, …); this module owns ONLY the door +
 * the swap. NO new analysis, NO new endpoint, NO new write path.
 *
 * `#track-list` is NEVER detached or re-parented (TASK-033/037) — only its
 * containing tab body is display-toggled by `switchTab`.
 */

import { clearInspector } from './inspector.js';

// Centre-pane / flank elements hidden while the place owns the centre. The
// library tools span the full centre column, so the row inspector is hidden too
// (mirrors the Duplicates/Discover places). `switchTab('library')` already
// display:none's the cues tab body that holds #track-list/#tracks-sticky/
// #wb-grid-head; hiding them here too is a defensive backstop against a legacy
// style.display write on the sticky bar.
const HIDE_IDS = ['tracks-sticky', 'track-list', 'wb-grid-head', 'wb-inspector'];

let _active = false;

export function isActive() { return _active; }

function _announce() {
  // Shell + rail repaint their active states off this (crates paint no `.active`
  // row while a place owns the centre pane).
  try { window.dispatchEvent(new CustomEvent('autocue:wb-place-change')); } catch (_) {}
}

export function activate() {
  if (_active) return;
  if (!(window.ACBridge && window.ACBridge.isLocalMode())) return;
  // Mutual exclusion: only one place owns the centre. Leave the others first.
  if (window.AC2 && window.AC2.duplicates) window.AC2.duplicates.deactivate();
  if (window.AC2 && window.AC2.discover) window.AC2.discover.deactivate();
  _active = true;
  // The inspector describes a grid row — clear it before hiding it.
  clearInspector();
  // Show the Library tab body in the centre column.
  if (window.switchTab) window.switchTab('library');
  for (const id of HIDE_IDS) document.getElementById(id)?.setAttribute('hidden', '');
  document.body.classList.add('wb-place-library');
  document.getElementById('wb-library-place')?.classList.add('active');
  _announce();
}

export function deactivate() {
  if (!_active) return;
  _active = false;
  // Return to the cue grid (scroll-to-top accepted — same tradeoff as Discover).
  if (window.switchTab) window.switchTab('cues');
  for (const id of HIDE_IDS) document.getElementById(id)?.removeAttribute('hidden');
  document.body.classList.remove('wb-place-library');
  document.getElementById('wb-library-place')?.classList.remove('active');
  // Repaint the re-shown grid.
  if (window.ACBridge) window.ACBridge.renderTracks();
  _announce();
}

export function initLibraryPlace() {
  const btn = document.getElementById('wb-library-place');
  if (!btn) return;
  btn.addEventListener('click', () => {
    // Re-clicking the active place is the no-new-id exit back to the grid.
    if (_active) deactivate();
    else activate();
  });
}
