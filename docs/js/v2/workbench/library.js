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
// Fix 6: track whether a scan has been triggered by this place (not the full
// scan state — just "did we kick one off?"). Re-entry must NOT rescan.
let _scanTriggered = false;
// Fix 6 race: store the deferred timer id so deactivate() can cancel it.
// Without this, a rapid activate() → deactivate() (e.g. entering Library then
// immediately clicking a crate) leaves the setTimeout in flight; when it fires
// it starts a scan that can conflict with the restored grid state.
let _autoScanTimer = null;

export function isActive() { return _active; }

/** Returns true when a health scan has already completed (health-done box visible). */
function _healthAlreadyDone() {
  const done = document.getElementById('health-done');
  return !!(done && done.style.display !== 'none');
}

/** Kick off the health scan on first place entry — mirrors design §4.
 *  Guards:
 *  • Active guard: no-op if the place is no longer active (rapid exit).
 *  • Re-entry guard: once _scanTriggered, skip entirely; if the user manually
 *    re-ran the scan from within the Library place, that's wired to the
 *    `lh-rescan` button directly and doesn't reset this flag. */
function _maybeAutoScan() {
  _autoScanTimer = null;
  if (!_active) return;                // guard: place deactivated before timer fired
  if (_scanTriggered) return;          // re-entry guard
  if (_healthAlreadyDone()) {
    _scanTriggered = true;             // mark as done without scanning again
    return;
  }
  _scanTriggered = true;
  // Delegate to the existing scan button — no new write path (same contract as
  // the palette "health-scan" command and the rail health-fix-row click path).
  document.getElementById('health-scan-btn')?.click();
}

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
  // Fix 6: auto-scan on FIRST entry if not already done (mirrors design §4).
  // setTimeout(0) lets the tab-body swap render first so the scan progress
  // bar is visible inside the Library place before any scan updates land.
  // The timer id is stored so deactivate() can cancel it if the user exits
  // the place before it fires (rapid enter→exit race — see lib:60 spec).
  _autoScanTimer = setTimeout(_maybeAutoScan, 0);
}

export function deactivate() {
  if (!_active) return;
  _active = false;
  // Fix 6 race: cancel the pending auto-scan timer before restoring the grid.
  // If the user exits the Library place before setTimeout(0) fires, the timer
  // would start a scan while the cue grid is the active view. Cancelling here
  // is safe because _maybeAutoScan also guards on `_active`, but this avoids
  // the scan starting at all (cleaner than checking at fire time alone).
  if (_autoScanTimer !== null) { clearTimeout(_autoScanTimer); _autoScanTimer = null; }
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
