/**
 * AutoCue 2.0 — Nightboard mode (P4).
 *
 * Nightboard is a full-bleed canvas MODE (not a P3/P5 centre-pane place): it
 * hides the workbench rail + grid + inspector and owns the body, keeping only
 * the global A-layer (sticky topbar: status sentence + ⌘K + action dock). The
 * grid is HIDDEN via `body.nb-active`, never detached — #track-list stays
 * mounted (TASK-033/037 preserved). Entered by a verb (#nb-open-btn in the
 * workbench toolbar) + a ⌘K command. Local-mode only, behind the workbench gate.
 *
 * T2 ships the mode skeleton + a stub render that proves the /api/setbuilder
 * round-trip (track count + terminated_reason notice). T3 replaces _renderStub
 * with canvas.render(); swap/tray/inspector land in T4/T5.
 */

import * as model from './set-model.js';

let _open = false;
let _escWired = false;

export function isNightboardOpen() { return _open; }

function _announce() {
  try { window.dispatchEvent(new CustomEvent('autocue:nb-change')); } catch (_) {}
}

export function openNightboard() {
  if (_open) return;
  if (!(window.ACBridge && window.ACBridge.isLocalMode && window.ACBridge.isLocalMode())) return;
  // Nightboard lives inside the workbench — force it on (the ac_workbench='0'
  // opt-out yields to explicit navigation, same as the P5 discover command).
  if (window.AC2 && window.AC2.workbench && window.AC2.workbench.setWorkbench) {
    window.AC2.workbench.setWorkbench(true);
  }
  _open = true;
  document.body.classList.add('nb-active');
  document.getElementById('nb-canvas')?.removeAttribute('hidden');
  document.getElementById('nb-open-btn')?.classList.add('active');
  _announce();
}

export function closeNightboard() {
  if (!_open) return;
  _open = false;
  document.body.classList.remove('nb-active');
  document.getElementById('nb-canvas')?.setAttribute('hidden', '');
  document.getElementById('nb-open-btn')?.classList.remove('active');
  // Repaint the re-shown cue grid.
  if (window.ACBridge && window.ACBridge.renderTracks) window.ACBridge.renderTracks();
  _announce();
}

function _readConfig() {
  const val = (id) => document.getElementById(id)?.value;
  return {
    start_bpm: val('nb-start-bpm'),
    end_bpm: val('nb-end-bpm'),
    duration_minutes: val('nb-duration'),
    energy_mode: val('nb-energy-mode'),
    anchor_track_ids: (window.ACBridge?.anchorsFromSelection?.() || []),
  };
}

async function _build() {
  const btn = document.getElementById('nb-build-btn');
  const status = document.getElementById('nb-status');
  if (btn) btn.disabled = true;
  if (status) status.textContent = 'Building…';
  try {
    const res = await model.buildSet(_readConfig());
    _renderStub(res);
  } catch (err) {
    if (status) status.textContent = 'Build failed: ' + (err && err.message ? err.message : err);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// T2 stub: prove the round-trip + surface terminated_reason honestly (R3).
// Replaced by canvas.render(getSet()) in T3.
function _renderStub(res) {
  const status = document.getElementById('nb-status');
  if (!status) return;
  const n = res.tracks.length;
  let msg = `${n} track${n !== 1 ? 's' : ''}`;
  const tr = res.terminatedReason;
  if (tr && tr !== 'target_duration_reached') msg += ` · ${tr.replace(/_/g, ' ')}`;
  status.textContent = msg;
}

export function initNightboard() {
  document.getElementById('nb-open-btn')?.addEventListener('click', () => {
    _open ? closeNightboard() : openNightboard();
  });
  document.getElementById('nb-build-btn')?.addEventListener('click', _build);
  if (!_escWired) {
    _escWired = true;
    document.addEventListener('keydown', (ev) => {
      if (!_open || ev.key !== 'Escape') return;
      const tag = (ev.target && ev.target.tagName) || '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      closeNightboard();
    });
  }
}
