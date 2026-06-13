/**
 * AutoCue 2.0 — Nightboard canvas render (P4).
 *
 * Pure render from the set-model state into the #nb-canvas shells: stats strip,
 * zone bands, the set-wide energy arc, and the timeline of tiles + scored
 * joints. No analysis here — joint scores are the engine's `transition_score`
 * (initial paint, no round-trips) and the energy curves come from
 * /api/tracks/{id}/energy via set-model. Per-track duration + cue-status are
 * looked up through window.ACBridge (the sanctioned legacy-state accessor).
 *
 * The pure helpers (jointBand / zoneFractions / buildArcPath) are exported for
 * Vitest; the DOM painters verify in the browser + the T7 Playwright spec.
 */

import * as model from './set-model.js';

// Presentation-only joint band cutoffs (design-D:405). The product's `overall`
// runs lower than the mockup in practice — calibration is a documented
// fast-follow, not v1. Tunable here in one place.
export const JOINT_BANDS = { good: 85, ok: 70 };

export function jointBand(score) {
  if (score == null || !Number.isFinite(Number(score))) return 'na';
  const s = Number(score);
  if (s >= JOINT_BANDS.good) return 'good';
  if (s >= JOINT_BANDS.ok) return 'ok';
  return 'bad';
}

// build_set categories → the four zone buckets (design-D zone washes).
const ZONE_OF = { warmup: 'warmup', build: 'build', peak: 'peak', after_hours: 'closing', closing: 'closing' };
const ZONES = ['warmup', 'build', 'peak', 'closing'];
const ZONE_LABEL = { warmup: 'Warm-up', build: 'Build', peak: 'Peak', closing: 'Closing' };

/** Fraction of the set in each zone bucket, weighted by track duration (→1 when
 *  a duration is missing). Returns a {warmup,build,peak,closing} map summing ~1. */
export function zoneFractions(set, durMap) {
  const acc = { warmup: 0, build: 0, peak: 0, closing: 0 };
  let total = 0;
  for (const t of (set || [])) {
    const z = ZONE_OF[t.category] || 'build';
    const w = (durMap && durMap.get(Number(t.track_id))) || 1;
    acc[z] += w; total += w;
  }
  const out = {};
  for (const z of ZONES) out[z] = total > 0 ? acc[z] / total : 0;
  return out;
}

/**
 * Stitch every track's energy curve into one duration-weighted polyline across
 * the set width. NaN-guarded: a track without a curve contributes a flat
 * mid-segment, a non-finite sample clamps to 0 — the `d` string never carries
 * NaN (R5). Returns {line, area} path strings for a viewBox 0 0 W H.
 */
export function buildArcPath(set, durMap, W = 1000, H = 84) {
  const list = set || [];
  if (!list.length) return { line: '', area: '' };
  const weights = list.map((t) => (durMap && durMap.get(Number(t.track_id))) || 1);
  const totalW = weights.reduce((a, b) => a + b, 0) || 1;
  const pts = [];
  let x = 0;
  list.forEach((t, i) => {
    const segW = (weights[i] / totalW) * W;
    const curve = model.energyFor(t.track_id);
    const samples = (Array.isArray(curve) && curve.length) ? curve : [0.5, 0.5];
    const n = samples.length;
    samples.forEach((v, j) => {
      const vv = Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0;
      const px = x + (n > 1 ? (j / (n - 1)) : 0) * segW;
      const py = H - vv * H;
      pts.push([px, py]);
    });
    x += segW;
  });
  const line = pts.map(([px, py], i) => `${i === 0 ? 'M' : 'L'}${px.toFixed(1)},${py.toFixed(1)}`).join(' ');
  const area = `${line} L${W.toFixed(1)},${H.toFixed(1)} L0,${H.toFixed(1)} Z`;
  return { line, area };
}

// ── DOM render ────────────────────────────────────────────────────────────

function _esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
function _fmtBpm(bpm) { const b = Number(bpm); return b > 0 ? b.toFixed(1) : '–'; }
function _fmtDur(sec) {
  if (!sec || sec <= 0) return '–';
  const m = Math.floor(sec / 60), s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}
function _fmtClock(sec) {
  if (!sec || sec <= 0) return '0 min';
  const total = Math.round(sec / 60);
  if (total < 60) return `${total} min`;
  return `${Math.floor(total / 60)}h ${String(total % 60).padStart(2, '0')}m`;
}

// Per-track meta (duration seconds + cues placed) via the legacy bridge. The
// in-memory parsed tracks use camelCase (totalTime / existingHotCues), NOT the
// snake_case of /api/tracks — ACBridge.tracks() returns the parsed objects.
function _meta() {
  const map = new Map();
  try {
    for (const t of (window.ACBridge && window.ACBridge.tracks ? window.ACBridge.tracks() : [])) {
      if (t && t.id != null) map.set(Number(t.id), { dur: Number(t.totalTime) || 0, cues: (t.existingHotCues || 0) > 0 });
    }
  } catch (_) { /* bridge not ready → empty meta, tiles still render */ }
  return map;
}
function _durMap(meta) { const m = new Map(); for (const [id, v] of meta) m.set(id, v.dur || 1); return m; }

function _sparkSVG(id) {
  const curve = model.energyFor(id);
  const samples = (Array.isArray(curve) && curve.length) ? curve : null;
  if (!samples) return '<svg class="nb-spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"></svg>';
  const n = samples.length;
  const pts = samples.map((v, j) => {
    const vv = Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0;
    return [(n > 1 ? j / (n - 1) : 0) * 100, 40 - vv * 40];
  });
  const line = pts.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const area = `${line} L100,40 L0,40 Z`;
  return `<svg class="nb-spark" viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden="true"><path class="nb-spark-area" d="${area}"/><path class="nb-spark-line" d="${line}"/></svg>`;
}

function _tile(t, meta) {
  const m = meta.get(Number(t.track_id)) || {};
  const tile = document.createElement('div');
  tile.className = 'nb-tile';
  tile.setAttribute('role', 'listitem');
  tile.dataset.trackId = String(t.track_id);
  tile.dataset.testid = 'nb-tile';
  tile.innerHTML = `
    <div class="nb-tile-title" title="${_esc(t.title)}">${_esc(t.title || '(untitled)')}</div>
    <div class="nb-tile-artist" title="${_esc(t.artist)}">${_esc(t.artist || '')}</div>
    <div class="nb-tile-chips">
      <span class="nb-chip nb-chip-bpm">${_fmtBpm(t.bpm)}</span>
      ${t.key ? `<span class="nb-chip nb-chip-key">${_esc(t.key)}</span>` : ''}
      ${t.category ? `<span class="nb-chip nb-chip-cat">${_esc(t.category)}</span>` : ''}
      ${t.relaxed ? '<span class="nb-chip nb-chip-relaxed">relaxed</span>' : ''}
    </div>
    <div class="nb-tile-spark">${_sparkSVG(t.track_id)}</div>
    <div class="nb-tile-foot">
      <span class="nb-foot-dur">${_fmtDur(m.dur)}</span>
      <span class="${m.cues ? 'nb-cue-ok' : 'nb-cue-warn'}">${m.cues ? '✓ cues' : 'no cues'}</span>
    </div>`;
  return tile;
}

function _joint(score, jointIdx) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = `nb-joint nb-joint-${jointBand(score)}`;
  b.dataset.testid = 'nb-joint';
  b.dataset.joint = String(jointIdx);
  const txt = (score == null || !Number.isFinite(Number(score))) ? '–' : Math.round(Number(score));
  b.innerHTML = `<span class="nb-joint-score">${txt}</span><small>/100</small>`;
  return b;
}

function _renderStats(set, durMap) {
  const el = document.getElementById('nb-stats');
  if (!el) return;
  const n = set.length;
  const totalSec = set.reduce((a, t) => a + (durMap.get(Number(t.track_id)) || 0), 0);
  const bpms = set.map((t) => Number(t.bpm)).filter((b) => b > 0);
  const bpmRange = bpms.length ? `${Math.round(Math.min(...bpms))}–${Math.round(Math.max(...bpms))}` : '–';
  const joints = set.slice(1).map((t) => Number(t.transition_score)).filter((s) => Number.isFinite(s));
  const avg = joints.length ? Math.round(joints.reduce((a, b) => a + b, 0) / joints.length) : null;
  el.innerHTML = `
    <span class="nb-stat"><b>${n}</b> tracks</span>
    <span class="nb-stat"><b>${_fmtClock(totalSec)}</b></span>
    <span class="nb-stat"><b>${bpmRange}</b> BPM</span>
    <span class="nb-stat">avg mix <b>${avg == null ? '–' : avg}</b></span>`;
}

function _renderZones(set, durMap) {
  const el = document.getElementById('nb-zones');
  if (!el) return;
  el.innerHTML = '';
  if (!set.length) return;
  const fr = zoneFractions(set, durMap);
  for (const z of ZONES) {
    if (fr[z] <= 0) continue;
    const d = document.createElement('div');
    d.className = `nb-zone nb-zone-${z}`;
    d.style.flex = `${fr[z]} 0 0`;
    d.innerHTML = `<span class="nb-zone-label">${ZONE_LABEL[z]}</span>`;
    el.appendChild(d);
  }
}

function _renderArc(set, durMap) {
  const svg = document.getElementById('nb-arc');
  if (!svg) return;
  if (!set.length) { svg.innerHTML = ''; return; }
  const { line, area } = buildArcPath(set, durMap);
  svg.innerHTML = `<path class="nb-arc-area" d="${area}"/><path class="nb-arc-line" d="${line}"/>`;
}

function _renderTimeline(set) {
  const tl = document.getElementById('nb-timeline');
  if (!tl) return;
  tl.innerHTML = '';
  const meta = _meta();
  set.forEach((t, i) => {
    // joint i-1 sits between tile i-1 and tile i; its score is on SET[i].
    if (i > 0) tl.appendChild(_joint(set[i].transition_score, i - 1));
    tl.appendChild(_tile(t, meta));
  });
}

/** Repaint the whole canvas from the current set-model state. */
export function render() {
  const set = model.getSet();
  const meta = _meta();
  const durMap = _durMap(meta);
  _renderStats(set, durMap);
  _renderZones(set, durMap);
  _renderArc(set, durMap);
  _renderTimeline(set);
}
