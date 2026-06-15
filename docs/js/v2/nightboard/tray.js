/**
 * AutoCue 2.0 — Nightboard gravity tray + tile-focus inspector (P4).
 *
 * The tray is a shelf of ranked next-track candidates for the FOCUSED tile (or
 * the last tile when none is focused), from /api/setbuilder/alternatives. "Add"
 * inserts a candidate after the anchor (set-model.insertAfter), re-scores the
 * touched joints (rescoreJoints), reloads its energy, and repaints — no rebuild
 * (R8). Clicking a tile focuses it: the existing P2 inspector (workbench/
 * inspector.js, mode 'track') is re-shown for in-context cue prep — no second
 * cue engine (R9). Cue generation still flows through the legacy preview/apply
 * pipeline + H consent gate.
 */

import * as model from './set-model.js';
import { render as renderCanvas } from './canvas.js';
import { renderInspector, clearInspector, setInspectorMode } from '../workbench/inspector.js';

let _anchorIdx = null; // focused tile index → tray anchor; null means "last tile"
let _adding = false;   // in-flight guard: one tray-Add at a time (no double-insert)

function _esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function _fmtBpm(b) { const n = Number(b); return n > 0 ? n.toFixed(1) : '–'; }

export function focusedIdx() { return _anchorIdx; }

export function clearFocus() {
  _anchorIdx = null;
  document.querySelectorAll('.nb-tile.nb-tile-active').forEach((e) => e.classList.remove('nb-tile-active'));
  document.body.classList.remove('nb-inspecting');
  clearInspector();
}

export function focusTile(idx) {
  const set = model.getSet();
  const t = set[idx];
  if (!t) return;
  _anchorIdx = idx;
  document.querySelectorAll('.nb-tile.nb-tile-active').forEach((e) => e.classList.remove('nb-tile-active'));
  document.querySelector(`.nb-tile[data-track-id="${t.track_id}"]`)?.classList.add('nb-tile-active');
  // Re-host the existing P2 inspector (mode 'track') for in-context cue prep.
  document.body.classList.add('nb-inspecting');
  document.getElementById('wb-inspector')?.removeAttribute('hidden');
  setInspectorMode('track');
  renderInspector(t.track_id);
  renderTray(idx);
}

async function _fetchCandidates(anchor, set, idx) {
  try {
    const prevId = anchor.track_id;                 // candidate sits AFTER the anchor
    const nextId = set[idx + 1] ? set[idx + 1].track_id : undefined;
    const p = new URLSearchParams({ track_id: String(anchor.track_id), n: '6' });
    p.set('prev_id', String(prevId));
    if (nextId) p.set('next_id', String(nextId));
    p.set('exclude_ids', set.map((t) => t.track_id).join(','));
    const r = await fetch(`/api/setbuilder/alternatives?${p.toString()}`);
    if (!r.ok) return [];
    const d = await r.json();
    return d.alternatives || [];
  } catch (_) { return []; }
}

export async function renderTray(anchorIdx) {
  const set = model.getSet();
  if (!set.length) return;
  const idx = (anchorIdx != null && set[anchorIdx]) ? anchorIdx : set.length - 1;
  const anchor = set[idx];
  const tray = document.getElementById('nb-tray');
  const row = document.getElementById('nb-tray-row');
  const ctx = document.getElementById('nb-tray-context');
  if (!tray || !row || !anchor) return;
  tray.removeAttribute('hidden');
  if (ctx) ctx.innerHTML = `after <b>${_esc(anchor.title || 'the last track')}</b>`;
  row.innerHTML = '<div class="nb-tray-loading">Finding tracks…</div>';

  const cands = await _fetchCandidates(anchor, set, idx);
  if (!cands.length) { row.innerHTML = '<div class="nb-tray-loading">No candidates found</div>'; return; }
  row.innerHTML = cands.map((c, k) => `
    <div class="nb-crate-card" data-k="${k}">
      <div class="nb-crate-title">${_esc(c.title || '(untitled)')}</div>
      <div class="nb-crate-sub">${_esc(c.artist || '')}</div>
      <div class="nb-crate-foot">
        <span class="nb-chip nb-chip-bpm">${_fmtBpm(c.bpm)}</span>
        <span class="nb-chip nb-chip-key">${_esc(c.key || '')}</span>
        <span class="nb-crate-score">${c.score != null ? Math.round(c.score) : '–'}</span>
        <span class="nb-topstrip-spacer"></span>
        <button type="button" class="nb-tray-add" data-testid="nb-tray-add" data-k="${k}">Add →</button>
      </div>
    </div>`).join('');
  // store the anchor + candidates for the Add handler
  row._nbAnchorIdx = idx;
  row._nbCands = cands;
}

async function _add(idx, cand) {
  if (!cand) return;
  model.insertAfter(idx, {
    track_id: cand.track_id, title: cand.title, artist: cand.artist,
    bpm: cand.bpm, key: cand.key, transition_score: null, relaxed: false,
  });
  const slot = idx + 1; // the inserted slot
  await model.rescoreJoints(slot);
  renderCanvas();
  await model.loadEnergyCurves([cand.track_id]);
  renderCanvas();
  renderTray(idx); // refresh candidates (exclude the just-added)
}

export function initTray() {
  // Tile focus + Add are delegated so they survive canvas re-renders.
  const tl = document.getElementById('nb-timeline');
  if (tl) {
    tl.addEventListener('click', (ev) => {
      if (ev.target.closest && ev.target.closest('.nb-joint')) return; // joints own their click
      const tile = ev.target.closest && ev.target.closest('.nb-tile[data-track-id]');
      if (!tile) return;
      const set = model.getSet();
      const id = Number(tile.dataset.trackId);
      const idx = set.findIndex((t) => Number(t.track_id) === id);
      if (idx >= 0) focusTile(idx);
    });
  }
  const row = document.getElementById('nb-tray-row');
  if (row) {
    row.addEventListener('click', async (ev) => {
      const btn = ev.target.closest && ev.target.closest('.nb-tray-add');
      if (!btn || _adding) return;            // ignore re-entrant clicks while an Add is in flight
      const cands = row._nbCands || [];
      _adding = true;
      btn.disabled = true;                    // immediate affordance; renderTray rebuilds fresh buttons
      try { await _add(row._nbAnchorIdx, cands[Number(btn.dataset.k)]); }
      finally { _adding = false; }
    });
  }
  const toggle = document.getElementById('nb-tray-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      const tray = document.getElementById('nb-tray');
      const collapsed = tray.classList.toggle('nb-tray-collapsed');
      toggle.textContent = collapsed ? 'Show' : 'Hide';
    });
  }
}
