/**
 * AutoCue 2.0 — workbench shell (P2 T3).
 *
 * The B "Crate Console" home: a left rail of smart crates + the existing track
 * list as the document-scrolled centre + a right inspector. Fixed flanks (path
 * a) — the Virtualizer, #tracks-sticky and document scroll are untouched.
 *
 * Flag-gated + additive: hidden until local mode AND localStorage.ac_workbench
 * === '1'. The old tabbed UI stays in the DOM (parity); the workbench just owns
 * the screen when active. Reads legacy state ONLY via window.ACBridge.
 */

import { initInspector, clearInspector } from './inspector.js';
import { initRail } from './rail.js';

const FLAG = 'ac_workbench';

export function isWorkbenchOn() {
  try { return localStorage.getItem(FLAG) === '1'; } catch (_) { return false; }
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

function _renderCrates() {
  const host = document.getElementById('wb-crates');
  if (!host) return;
  const tracks = window.ACBridge ? window.ACBridge.tracks() : [];
  const current = window.ACBridge ? window.ACBridge.crate() : 'all';
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
      if (window.ACBridge) window.ACBridge.setCrate(c.id);
      _renderCrates(); // repaint active state
    });
    host.appendChild(btn);
  }
}

// Global controls that move into the top-bar toolbar in workbench mode. Their
// original DOM slot is recorded so deactivate() can put them back exactly.
const _TOOL_IDS = ['playlist-filter-bar', 'analysis-mode-bar'];
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
  initRail();
  initInspector();
  // Keep crate counts fresh as the library loads / changes.
  if (window.AppState) window.AppState.subscribe('tracks', _renderCrates);
}

function deactivate() {
  if (!_active) return;
  _active = false;
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
