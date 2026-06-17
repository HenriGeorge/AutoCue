/**
 * AutoCue 2.0 — workbench shell (P2 T3).
 *
 * The B "Crate Console" home: a left rail of smart crates + the existing track
 * list as the document-scrolled centre + a right inspector. Fixed flanks (path
 * a) — the Virtualizer, #tracks-sticky and document scroll are untouched.
 *
 * Default-on in local mode: the workbench is the standard home. An explicit
 * opt-out (localStorage.ac_workbench === '0', written by the toggle) reverts to
 * the legacy tabbed UI, which stays in the DOM (parity). The workbench just owns
 * the screen when active. Reads legacy state ONLY via window.ACBridge.
 */

import { initInspector, clearInspector } from './inspector.js';
import { initRail } from './rail.js';

const FLAG = 'ac_workbench';

export function isWorkbenchOn() {
  // Default-on: the workbench is the standard local-mode home. Only an explicit
  // opt-out ('0', written by the toggle) reverts to the legacy tabbed UI.
  try { return localStorage.getItem(FLAG) !== '0'; } catch (_) { return true; }
}
export function setWorkbench(on) {
  try { localStorage.setItem(FLAG, on ? '1' : '0'); } catch (_) {}
  if (on) activate(); else deactivate();
}
export function toggleWorkbench() { setWorkbench(!isWorkbenchOn()); }

// Structural crates — cheap client-side predicates over ACBridge.tracks().
const CRATES = [
  { id: 'all',    label: 'All tracks',    pred: () => true },
  { id: 'none',   label: 'No cues yet',   pred: (t) => Number(t.existingHotCues) === 0 },
  { id: 'phrase', label: 'Phrase-ready',  pred: (t) => !!t.hasPhrase },
  { id: 'cued',   label: 'Already cued',  pred: (t) => Number(t.existingHotCues) > 0 },
];

let _active = false;
let _placeWired = false;

function _renderCrates() {
  const host = document.getElementById('wb-crates');
  if (!host) return;
  const tracks = window.ACBridge ? window.ACBridge.tracks() : [];
  // P3: while a centre-pane place (Duplicates) is active, no crate row paints
  // `.active` — the place owns the centre, not a crate filter.
  const placeActive = !!(
    (window.AC2 && window.AC2.duplicates && window.AC2.duplicates.isActive()) ||
    (window.AC2 && window.AC2.discover && window.AC2.discover.isActive()) ||
    (window.AC2 && window.AC2.library && window.AC2.library.isActive())
  );
  const current = placeActive ? null : (window.ACBridge ? window.ACBridge.crate() : 'all');
  host.innerHTML = '';
  for (const c of CRATES) {
    const count = c.id === 'all' ? tracks.length : tracks.filter(c.pred).length;
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'wb-crate' + (c.id === current ? ' active' : '');
    btn.innerHTML =
      `<span class="wb-crate-label">${c.label}</span>` +
      `<span class="wb-crate-count">${count.toLocaleString()}</span>`;
    btn.addEventListener('click', () => {
      // P3/P5: leaving via a crate click exits any active centre-pane place.
      if (window.AC2 && window.AC2.duplicates) window.AC2.duplicates.deactivate();
      if (window.AC2 && window.AC2.discover) window.AC2.discover.deactivate();
      if (window.AC2 && window.AC2.library) window.AC2.library.deactivate();
      if (window.ACBridge) window.ACBridge.setCrate(c.id);
      _renderCrates(); // repaint active state
    });
    host.appendChild(btn);
  }
  _renderPlaces();
}

// Places section: primary nav (design). Crate Console is the grid home — active
// when no centre-pane place owns the screen; Library carries the amber "needs
// cues" health badge. Library/Discover/Duplicates buttons are wired by their own
// modules (.active toggling); here we only wire the id-less Crate Console +
// Nightboard launchers and repaint the home/badge state.
let _navWired = false;
function _renderPlaces() {
  const host = document.getElementById('wb-places');
  if (!host) return;
  const tracks = window.ACBridge ? window.ACBridge.tracks() : [];
  const placeActive = !!(
    (window.AC2 && window.AC2.duplicates && window.AC2.duplicates.isActive()) ||
    (window.AC2 && window.AC2.discover && window.AC2.discover.isActive()) ||
    (window.AC2 && window.AC2.library && window.AC2.library.isActive())
  );
  const nbActive = document.body.classList.contains('nb-active');
  const consoleBtn = host.querySelector('[data-place="cues"]');
  if (consoleBtn) consoleBtn.classList.toggle('active', !placeActive && !nbActive);
  const badge = document.getElementById('wb-library-badge');
  if (badge) {
    const need = tracks.filter((t) => Number(t.existingHotCues) === 0).length;
    badge.textContent = need > 0 ? need.toLocaleString() : '';
    badge.hidden = need === 0;
  }
}
function _wirePlaces() {
  if (_navWired) return;
  const host = document.getElementById('wb-places');
  if (!host) return;
  _navWired = true;
  host.querySelector('[data-place="cues"]')?.addEventListener('click', () => {
    if (window.AC2 && window.AC2.duplicates) window.AC2.duplicates.deactivate();
    if (window.AC2 && window.AC2.discover) window.AC2.discover.deactivate();
    if (window.AC2 && window.AC2.library) window.AC2.library.deactivate();
    if (window.switchTab) window.switchTab('cues');
    _renderCrates();
  });
  host.querySelector('[data-place="nightboard"]')?.addEventListener('click', () => {
    if (window.AC2 && window.AC2.nightboard && window.AC2.nightboard.open) window.AC2.nightboard.open();
  });
}

// Inline selection batch bar: tools delegate to existing controls so all legacy
// guards fire on the real path (the palette uses the same delegation pattern).
// Preview/Clear are selection-scoped; Auto-tag/Enrich open the Library tools.
function _wireBatchBar() {
  const bar = document.getElementById('wb-batch-bar');
  if (!bar || bar.dataset.wired) return;
  bar.dataset.wired = '1';
  const go = (id) => document.getElementById(id)?.click();
  const openLibrary = (sectionId) => {
    setWorkbench(true);
    if (!(window.AC2 && window.AC2.library && window.AC2.library.isActive && window.AC2.library.isActive())) {
      document.getElementById('wb-library-place')?.click();
    }
    if (sectionId) document.getElementById(sectionId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };
  bar.querySelector('[data-batch="preview"]')?.addEventListener('click', () => go('action-bar-preview'));
  bar.querySelector('[data-batch="clear"]')?.addEventListener('click', () => go('action-bar-clear'));
  bar.querySelector('[data-batch="autotag"]')?.addEventListener('click', () => openLibrary('cue-tools-section'));
  bar.querySelector('[data-batch="enrich"]')?.addEventListener('click', () => openLibrary('comment-enrich-section'));
}

// Global controls that move into the top-bar toolbar in workbench mode. Their
// original DOM slot is recorded so deactivate() can put them back exactly.
const _TOOL_IDS = ['playlist-filter-bar', 'analysis-mode-bar', 'nb-open-bar'];
const _toolHomes = new Map();

function _relocateTools() {
  const host = document.getElementById('wb-topbar-tools');
  if (!host) return;
  for (const id of _TOOL_IDS) {
    const el = document.getElementById(id);
    if (!el || _toolHomes.has(id)) continue;
    _toolHomes.set(id, { parent: el.parentNode, next: el.nextSibling, display: el.style.display });
    el.style.display = 'flex'; // these default to display:none until local mode reveals them
    host.appendChild(el);
  }
  host.hidden = false;
}
function _restoreTools() {
  const host = document.getElementById('wb-topbar-tools');
  for (const id of _TOOL_IDS) {
    const home = _toolHomes.get(id);
    const el = document.getElementById(id);
    if (home && el) {
      el.style.display = home.display;
      home.parent.insertBefore(el, home.next);
    }
    _toolHomes.delete(id);
  }
  if (host) { host.hidden = true; host.innerHTML = ''; }
}

function activate() {
  if (_active) return;
  if (!(window.ACBridge && window.ACBridge.isLocalMode())) return;
  _active = true;
  document.body.classList.add('wb-active');
  document.getElementById('wb-rail')?.removeAttribute('hidden');
  document.getElementById('wb-inspector')?.removeAttribute('hidden');
  // The workbench centre is the Cues track list — make sure that tab is shown.
  if (window.switchTab) window.switchTab('cues');
  _relocateTools();
  // Re-render so the album-group view collapses into the uniform flat grid.
  if (window.ACBridge) window.ACBridge.renderTracks();
  _renderCrates();
  _wirePlaces();
  _wireBatchBar();
  initRail();
  initInspector();
  // Keep crate counts fresh as the library loads / changes.
  if (window.AppState) window.AppState.subscribe('tracks', _renderCrates);
  // P3: repaint crate active-state when a centre-pane place opens/closes.
  if (!_placeWired) {
    _placeWired = true;
    window.addEventListener('autocue:wb-place-change', _renderCrates);
  }
}

function deactivate() {
  if (!_active) return;
  _active = false;
  // P3/P5: a centre-pane place can't outlive the workbench — restore the grid.
  if (window.AC2 && window.AC2.duplicates) window.AC2.duplicates.deactivate();
  if (window.AC2 && window.AC2.discover) window.AC2.discover.deactivate();
  if (window.AC2 && window.AC2.library) window.AC2.library.deactivate();
  document.body.classList.remove('wb-active');
  document.getElementById('wb-rail')?.setAttribute('hidden', '');
  document.getElementById('wb-inspector')?.setAttribute('hidden', '');
  _restoreTools();
  clearInspector();
  if (window.ACBridge) { window.ACBridge.setCrate('all'); window.ACBridge.renderTracks(); }
}

export function initWorkbench() {
  const start = () => { if (isWorkbenchOn()) activate(); };
  if (window.ACBridge && window.ACBridge.isLocalMode()) start();
  else window.addEventListener('autocue:local-mode', start, { once: true });
}

initWorkbench();
