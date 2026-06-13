/**
 * AutoCue 2.0 — Nightboard joint popover + in-place swap (P4).
 *
 * Clicking a joint opens a popover anchored under it with the real transition
 * detail: the pair, the `overall` score, the three `explanation` strings
 * verbatim from score_transition (fetched lazily on open — build_set only
 * returns the score, not the reasons), and <=2 swap alternatives from
 * /api/setbuilder/alternatives. "Swap in" replaces the INCOMING track, re-scores
 * ONLY the <=2 joints touching the slot (set-model.rescoreJoints), reloads its
 * energy curve, and repaints — never a full rebuild (R7).
 *
 * No analysis here; no new endpoint. transition_advice is not in the REST
 * response, so the footer tip is a band-derived one-liner (presentation only).
 */

import * as model from './set-model.js';
import { render as renderCanvas, jointBand } from './canvas.js';

const PO_WIDTH = 320;
let _el = null;
let _openIdx = null;
let _dismiss = null;
let _onScroll = null;

function _esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
function _fmtBpm(b) { const n = Number(b); return n > 0 ? n.toFixed(1) : '–'; }

// transition_advice is Python-side and not in TransitionResponse — derive a
// short band tip client-side (guidance, clearly generic).
function _tip(overall) {
  switch (jointBand(overall)) {
    case 'good': return 'Strong blend — a long overlay works here.';
    case 'ok': return 'Workable — try an EQ swap or a quicker cut.';
    case 'bad': return 'Rough edge — consider a swap or a hard cut.';
    default: return '';
  }
}

export function isOpen() { return _openIdx != null; }

export function close() {
  if (_el) { _el.remove(); _el = null; }
  if (_openIdx != null) {
    document.querySelector(`.nb-joint[data-joint="${_openIdx}"]`)?.classList.remove('nb-joint-open');
  }
  _openIdx = null;
  if (_dismiss) {
    document.removeEventListener('mousedown', _dismiss, true);
    document.removeEventListener('keydown', _dismiss, true);
    _dismiss = null;
  }
  if (_onScroll) {
    document.getElementById('nb-timeline')?.removeEventListener('scroll', _onScroll);
    _onScroll = null;
  }
}

export async function open(jointIdx) {
  const set = model.getSet();
  const a = set[jointIdx], b = set[jointIdx + 1];
  if (!a || !b) return;
  if (_openIdx === jointIdx) { close(); return; } // re-click toggles
  close();
  _openIdx = jointIdx;
  const jointBtn = document.querySelector(`.nb-joint[data-joint="${jointIdx}"]`);
  jointBtn?.classList.add('nb-joint-open');

  _el = document.createElement('div');
  _el.className = 'nb-popover';
  _el.setAttribute('role', 'dialog');
  _el.dataset.testid = 'nb-popover';
  _el.innerHTML = '<div class="nb-po-loading">Scoring…</div>';
  document.body.appendChild(_el);
  _position(jointBtn);

  _dismiss = (ev) => {
    if (ev.type === 'keydown') { if (ev.key === 'Escape') { ev.stopPropagation(); close(); } return; }
    if (_el && !_el.contains(ev.target) && ev.target !== jointBtn && !jointBtn?.contains(ev.target)) close();
  };
  document.addEventListener('mousedown', _dismiss, true);
  document.addEventListener('keydown', _dismiss, true);
  _onScroll = () => close();
  document.getElementById('nb-timeline')?.addEventListener('scroll', _onScroll, { passive: true });

  const nextId = set[jointIdx + 2] ? set[jointIdx + 2].track_id : undefined;
  const excl = set.map((t) => t.track_id).join(',');
  const [score, alts] = await Promise.all([
    _fetchScore(a.track_id, b.track_id),
    _fetchAlternatives(b.track_id, a.track_id, nextId, excl),
  ]);
  if (_openIdx !== jointIdx || !_el) return; // closed / changed while fetching
  _renderBody(a, b, score, alts, jointIdx);
  _position(jointBtn);
}

async function _fetchScore(aId, bId) {
  try {
    const r = await fetch('/api/transitions/score', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_a_id: aId, track_b_id: bId }),
    });
    if (!r.ok) return null;
    return await r.json();
  } catch (_) { return null; }
}

async function _fetchAlternatives(trackId, prevId, nextId, excludeIds) {
  try {
    const p = new URLSearchParams({ track_id: String(trackId), n: '2' });
    if (prevId) p.set('prev_id', String(prevId));
    if (nextId) p.set('next_id', String(nextId));
    if (excludeIds) p.set('exclude_ids', excludeIds);
    const r = await fetch(`/api/setbuilder/alternatives?${p.toString()}`);
    if (!r.ok) return [];
    const d = await r.json();
    return (d.alternatives || []).slice(0, 2);
  } catch (_) { return []; }
}

function _renderBody(a, b, score, alts, jointIdx) {
  const overall = score && Number.isFinite(score.overall) ? Math.round(score.overall)
    : (b.transition_score != null ? Math.round(b.transition_score) : null);
  const band = jointBand(overall);
  const reasons = (score && Array.isArray(score.explanation)) ? score.explanation : [];
  const altRows = alts.map((alt, k) => `
    <div class="nb-po-alt">
      <div class="nb-po-alt-meta">
        <div class="nb-po-alt-title">${_esc(alt.title || '(untitled)')}</div>
        <div class="nb-po-alt-sub">${_esc(alt.artist || '')} · <span class="nb-mono">${_fmtBpm(alt.bpm)}</span> · <span class="nb-mono">${_esc(alt.key || '')}</span></div>
      </div>
      <span class="nb-po-alt-score nb-band-${jointBand(alt.score)}">${alt.score != null ? Math.round(alt.score) : '–'}</span>
      <button type="button" class="nb-swap" data-testid="nb-swap" data-k="${k}">Swap in</button>
    </div>`).join('');
  _el.innerHTML = `
    <div class="nb-po-head">
      <div class="nb-po-pair">${_esc(a.title || 'A')} <span class="nb-po-arrow">→</span> ${_esc(b.title || 'B')}</div>
      <div class="nb-po-score nb-band-${band}">${overall == null ? '–' : overall}<small>/100</small></div>
    </div>
    ${reasons.length ? `<ul class="nb-po-reasons">${reasons.map((r) => `<li>${_esc(r)}</li>`).join('')}</ul>` : ''}
    ${alts.length ? `<div class="nb-po-alt-label">Swap alternatives</div>${altRows}` : '<div class="nb-po-empty">No swap candidates found</div>'}
    ${_tip(overall) ? `<div class="nb-po-tip">${_esc(_tip(overall))}</div>` : ''}`;
  _el.querySelectorAll('.nb-swap').forEach((btn) => {
    btn.addEventListener('click', () => _swap(jointIdx, alts[Number(btn.dataset.k)]));
  });
}

async function _swap(jointIdx, alt) {
  if (!alt) return;
  const slot = jointIdx + 1; // the incoming track is replaced (R7)
  model.swapAt(slot, {
    track_id: alt.track_id, title: alt.title, artist: alt.artist,
    bpm: alt.bpm, key: alt.key, transition_score: null, relaxed: false,
  });
  close();
  await model.rescoreJoints(slot);  // re-scores ONLY joints slot-1 and slot
  renderCanvas();
  model.loadEnergyCurves([alt.track_id]).then(() => renderCanvas());
}

function _position(jointBtn) {
  if (!_el || !jointBtn || !jointBtn.getBoundingClientRect) return;
  const r = jointBtn.getBoundingClientRect();
  let left = r.left + r.width / 2 - PO_WIDTH / 2;
  left = Math.max(12, Math.min(left, window.innerWidth - PO_WIDTH - 12));
  _el.style.width = PO_WIDTH + 'px';
  _el.style.left = `${left}px`;
  let top = r.bottom + 10;
  const ph = _el.getBoundingClientRect().height;
  if (top + ph > window.innerHeight - 12) top = Math.max(12, r.top - ph - 10);
  _el.style.top = `${top}px`;
}

export function initJointPopover() {
  const tl = document.getElementById('nb-timeline');
  if (!tl) return;
  // Delegated: the timeline element persists across canvas re-renders (only its
  // children are replaced), so one listener survives every repaint.
  tl.addEventListener('click', (ev) => {
    const j = ev.target.closest && ev.target.closest('.nb-joint[data-joint]');
    if (!j) return;
    open(Number(j.dataset.joint));
  });
}
