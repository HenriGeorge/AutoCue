/**
 * AutoCue 2.0 — workbench inspector (P2 T4).
 *
 * Right pane: everything about the focused track, by RE-HOSTING the existing
 * legacy builders (energy curve, mixability, classification, similar, cue
 * reasoning) — they read `container.dataset.trackId` and fetch their own data,
 * so the inspector just builds the containers and calls them. Reads legacy via
 * window.* per the interop contract.
 */

let _focusedId = null;
let _glowTimer = null; // P2 — harmonic-glow cleanup timer
// P5: the inspector is dual-purpose. 'track' (default) hosts the focused grid
// row; 'release' hosts a Discover release detail (re-hosted from the legacy
// slide-in panel). The mode gates the grid-click handler so a release detail
// isn't clobbered by a stray grid click while the Discover place owns the centre.
let _mode = 'track';
// Where the canonical #disc-v2-detail-body node lived before we relocated it
// into the inspector for a release re-host. Restored on clearInspector().
let _detailBodyHome = null;

export function setInspectorMode(m) { _mode = m === 'release' ? 'release' : 'track'; }
export function inspectorMode() { return _mode; }

// P-design — "Transition in" anchor card. Band cutoffs replicated locally (NOT
// imported from nightboard/canvas.js — avoid cross-feature coupling); same
// presentation thresholds as JOINT_BANDS (good ≥85 / ok ≥70, else weak).
const ANCHOR_BANDS = { good: 85, ok: 70 };
function _anchorBand(score) {
  const s = Number(score);
  if (!Number.isFinite(s)) return 'weak';
  if (s >= ANCHOR_BANDS.good) return 'good';
  if (s >= ANCHOR_BANDS.ok) return 'ok';
  return 'weak';
}
// Monotonic token so a focus change mid-flight discards a stale score response.
let _txToken = 0;

function _chip(text, mono, accent) {
  const s = document.createElement('span');
  s.className = 'wb-insp-chip' + (mono ? ' mono' : '') + (accent ? ' accent' : '');
  s.textContent = text;
  return s;
}
function _section(title) {
  const wrap = document.createElement('div');
  wrap.className = 'wb-insp-section';
  const h = document.createElement('div');
  h.className = 'wb-insp-h';
  h.textContent = title;
  wrap.appendChild(h);
  return wrap;
}

// P2 — harmonic family glow: when a track is selected, briefly outline the
// in-view tracks that are Camelot- + tempo-compatible with it (green = signal).
// Reuses the legacy Camelot helper (window._sbKeyCompat); the CSS gates the
// motion on prefers-reduced-motion. Decorative — wrapped so it can NEVER break
// selection if the helper or grid is absent.
function _glowHarmonic(sel) {
  try {
    document.querySelectorAll('.track-card.harmonic-glow').forEach((c) => c.classList.remove('harmonic-glow'));
    if (_glowTimer) { clearTimeout(_glowTimer); _glowTimer = null; }
    const compat = window._sbKeyCompat;
    if (typeof compat !== 'function' || !sel || !sel.key) return;
    const tracks = window.ACBridge ? window.ACBridge.tracks() : [];
    const byId = new Map(tracks.map((x) => [String(x.id), x]));
    for (const card of document.querySelectorAll('#track-list .track-card[data-track-id]')) {
      const id = card.dataset.trackId;
      if (id === String(sel.id)) continue;
      const other = byId.get(String(id));
      if (!other || !other.key) continue;
      // Harmonic (Camelot) compatibility — the glow lights the key-family in view.
      if (compat(sel.key, other.key) === true) card.classList.add('harmonic-glow');
    }
    _glowTimer = setTimeout(() => {
      document.querySelectorAll('.track-card.harmonic-glow').forEach((c) => c.classList.remove('harmonic-glow'));
      _glowTimer = null;
    }, 1200);
  } catch (_) { /* glow is decorative — never break selection */ }
}

export function renderInspector(trackId) {
  const body = document.getElementById('wb-inspector-body');
  const empty = document.getElementById('wb-inspector-empty');
  if (!body) return;
  const tracks = window.ACBridge ? window.ACBridge.tracks() : [];
  const t = tracks.find((x) => String(x.id) === String(trackId));
  if (!t) return;
  const _prevFocusedId = _focusedId; // anchor fallback (the last focused track)
  _focusedId = String(trackId);
  _mode = 'track'; // this is the track renderer — a prior release re-host is over
  _glowHarmonic(t); // P2 — light the harmonic family in the grid
  document.body.classList.add('wb-inspecting'); // CSS drawer slides in

  if (empty) empty.hidden = true;
  body.hidden = false;
  body.innerHTML = '';

  // ── Header: title / artist + data chips ──
  const head = document.createElement('div');
  head.className = 'wb-insp-head';
  const title = document.createElement('div');
  title.className = 'wb-insp-title';
  title.textContent = t.name || '(untitled)';
  const artist = document.createElement('div');
  artist.className = 'wb-insp-artist';
  artist.textContent = t.artist || '';
  head.appendChild(title);
  head.appendChild(artist);
  const chips = document.createElement('div');
  chips.className = 'wb-insp-chips';
  if (Number(t.bpm) > 0) chips.appendChild(_chip(Number(t.bpm).toFixed(1) + ' BPM', true, true));
  if (t.key) chips.appendChild(_chip(t.key, true));
  if (t.totalTime) {
    const m = Math.floor(t.totalTime / 60), s = Math.floor(t.totalTime % 60);
    chips.appendChild(_chip(`${m}:${String(s).padStart(2, '0')}`, true));
  }
  head.appendChild(chips);

  // ── Play affordance ──
  // The dense workbench grid has no play control, so the inspector is where you
  // start playback — which is what lights up the energy playhead trace (step 3).
  // Reuses the legacy playback path via window.* (classic-script globals);
  // updatePlaybackUI() keeps the icon in sync with play/pause/ended.
  const play = document.createElement('button');
  play.type = 'button';
  play.id = 'wb-insp-play';
  play.className = 'wb-insp-playbtn';
  play.dataset.trackId = _focusedId;
  play.textContent = '▶ Play';
  play.addEventListener('click', () => {
    const toggle = window.togglePlayTrack;
    if (typeof toggle !== 'function') return;
    try {
      if (typeof window.ensureLocalAudio === 'function') {
        window.ensureLocalAudio(t).then(() => toggle(t.id)).catch(() => {});
      } else {
        toggle(t.id);
      }
    } catch (_) { /* playback is best-effort */ }
  });
  head.appendChild(play);
  body.appendChild(head);

  // ── Transition in (anchor → focused) advisory card — directly under header ──
  _renderTransitionIn(body, _focusedId, _prevFocusedId);

  // ── Energy curve (reuse _renderEnergySparkline) ──
  const energySec = _section('Energy curve');
  const energy = document.createElement('div');
  energy.className = 'wb-insp-energy';
  energy.dataset.trackId = _focusedId;
  energySec.appendChild(energy);
  // Time axis under the curve (design): start … duration.
  const axis = document.createElement('div');
  axis.className = 'wb-insp-energy-axis';
  const a0 = document.createElement('span');
  a0.textContent = '0:00';
  const a1 = document.createElement('span');
  if (t.totalTime) {
    const am = Math.floor(t.totalTime / 60), as = Math.floor(t.totalTime % 60);
    a1.textContent = `${am}:${String(as).padStart(2, '0')}`;
  }
  axis.appendChild(a0);
  axis.appendChild(a1);
  energySec.appendChild(axis);
  body.appendChild(energySec);
  try { window._renderEnergySparkline?.(energy); } catch (_) {}

  // ── Mixability + classification (reuse the chip builders) ──
  const scoreSec = _section('Scores');
  const scoreRow = document.createElement('div');
  scoreRow.className = 'wb-insp-scorerow';
  const mix = document.createElement('span');
  mix.className = 'mix-score-chip';
  mix.dataset.trackId = _focusedId;
  mix.textContent = 'Mix …';
  const mixBreak = document.createElement('div');
  mixBreak.className = 'wb-insp-mixbreak';
  const cls = document.createElement('span');
  cls.className = 'category-chip';
  cls.dataset.trackId = _focusedId;
  cls.textContent = '…';
  scoreRow.appendChild(mix);
  scoreRow.appendChild(cls);
  scoreSec.appendChild(scoreRow);
  scoreSec.appendChild(mixBreak);
  body.appendChild(scoreSec);
  try { window._renderMixabilityChip?.(mix, mixBreak); } catch (_) {}
  try { window._renderCategoryChip?.(cls); } catch (_) {}

  // ── Existing cues + reasoning (reuse _explainCue) ──
  const cues = (t.existingCueDetails || []).filter((c) => c.num >= 0);
  if (cues.length) {
    const cueSec = _section(`Cues (${cues.length})`);
    for (const c of cues) {
      const row = document.createElement('div');
      row.className = 'wb-insp-cue';
      const badge = document.createElement('span');
      badge.className = 'wb-insp-cue-badge';
      badge.textContent = String.fromCharCode(65 + (c.num || 0)); // A,B,C…
      const name = document.createElement('span');
      name.className = 'wb-insp-cue-name';
      name.textContent = c.name || `Cue ${(c.num || 0) + 1}`;
      const time = document.createElement('span');
      time.className = 'wb-insp-cue-time mono';
      const sec = c.start || 0;
      time.textContent = `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, '0')}`;
      row.appendChild(badge);
      row.appendChild(name);
      row.appendChild(time);
      cueSec.appendChild(row);
    }
    body.appendChild(cueSec);
  } else {
    const noCue = _section('Cues');
    const p = document.createElement('div');
    p.className = 'wb-insp-muted';
    p.textContent = 'No hot cues yet — select + Preview to generate.';
    noCue.appendChild(p);
    body.appendChild(noCue);
  }

  // ── Similar tracks (reuse _toggleSimilarPanel) ──
  const simSec = _section('Similar');
  const simBtn = document.createElement('button');
  simBtn.type = 'button';
  simBtn.className = 'wb-insp-simbtn';
  simBtn.textContent = '≈ Find similar tracks';
  const simPanel = document.createElement('div');
  simPanel.className = 'similar-panel';
  simBtn.addEventListener('click', () => {
    try { window._toggleSimilarPanel?.(simBtn, simPanel, _focusedId); } catch (_) {}
  });
  simSec.appendChild(simBtn);
  simSec.appendChild(simPanel);
  body.appendChild(simSec);

  // Sync the play button's icon if this track is already the one playing.
  try { window.updatePlaybackUI?.(); } catch (_) {}
}

// P-design — the "Transition in" advisory card. Scores how the focused track
// mixes OUT OF the anchor (anchor = now-playing, fallback = previously-focused),
// reusing the SAME contract Nightboard uses (POST /api/transitions/score). Zero
// backend change; advisory only (silent on failure, hidden with no anchor).
function _renderTransitionIn(body, focusedId, prevFocusedId) {
  if (_mode === 'release') return; // release re-host never shows the card
  const bridge = window.ACBridge;
  const rawAnchor = bridge && typeof bridge.nowPlayingId === 'function' ? bridge.nowPlayingId() : null;
  const anchorId = (rawAnchor != null ? String(rawAnchor) : '')
    || (prevFocusedId != null ? String(prevFocusedId) : '');
  // Hidden: no anchor, or the anchor IS the focused track (don't score self).
  if (!anchorId || anchorId === String(focusedId)) return;

  const tracks = bridge ? bridge.tracks() : [];
  const anchorTrack = tracks.find((x) => String(x.id) === anchorId);

  const sec = _section('Transition in');
  sec.classList.add('wb-insp-tx-sec');
  const row = document.createElement('div');
  row.className = 'wb-insp-tx-row';
  const score = document.createElement('span');
  score.className = 'wb-insp-tx-score mono';
  score.textContent = '…';
  const from = document.createElement('span');
  from.className = 'wb-insp-tx-from';
  const anchorLabel = anchorTrack ? (anchorTrack.name || anchorTrack.artist || '#' + anchorId) : '#' + anchorId;
  from.textContent = 'from ' + anchorLabel;
  row.appendChild(score);
  row.appendChild(from);
  sec.appendChild(row);
  const meta = document.createElement('div');
  meta.className = 'wb-insp-tx-meta mono';
  sec.appendChild(meta);
  const reasons = document.createElement('div');
  reasons.className = 'wb-insp-tx-reasons';
  sec.appendChild(reasons);
  body.appendChild(sec);

  const token = ++_txToken;
  const stale = () => token !== _txToken || _focusedId !== String(focusedId) || _mode !== 'track';
  fetch('/api/transitions/score', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ track_a_id: parseInt(anchorId, 10), track_b_id: parseInt(focusedId, 10) }),
  })
    .then((r) => (r.ok ? r.json() : null))
    .then((data) => {
      if (stale()) return;
      if (!data) { sec.remove(); return; } // !r.ok → silent, no lingering header
      const band = _anchorBand(data.overall);
      score.textContent = String(Math.round(Number(data.overall) || 0));
      score.classList.add('tx-' + band); // green only on good (signal)
      // Mono BPM/key fragments (data, not labels) when present.
      const bits = [];
      if (Number(data.bpm_a) > 0 && Number(data.bpm_b) > 0) {
        bits.push(`${Math.round(data.bpm_a)}→${Math.round(data.bpm_b)} BPM`);
      }
      if (data.key_a && data.key_b) bits.push(`${data.key_a}→${data.key_b}`);
      meta.textContent = bits.join('  ·  ');
      if (!meta.textContent) meta.remove();
      reasons.innerHTML = '';
      for (const e of (Array.isArray(data.explanation) ? data.explanation : []).slice(0, 3)) {
        const line = document.createElement('div');
        line.className = 'wb-insp-tx-reason';
        line.textContent = e;
        reasons.appendChild(line);
      }
    })
    .catch(() => { if (!stale()) sec.remove(); }); // network error → silent
}

// P5: re-host a Discover release detail in the inspector (mode 'release').
// Header + mono data chips (year / label / release-id / styles, R6) are built
// here; the body / tracklist / YouTube / action buttons are delegated wholesale
// to the legacy _renderDetailBody (exposed as window._renderDiscoverRenderDetail)
// so the place never duplicates that markup or its delegation. loadDetail goes
// through ACBridge.discoverLoadDetail — the place never re-fetches releases/{id}.
export function renderReleaseInspector(releaseKey) {
  const body = document.getElementById('wb-inspector-body');
  const empty = document.getElementById('wb-inspector-empty');
  if (!body) return;
  const state = (window.ACBridge && window.ACBridge.discoverState && window.ACBridge.discoverState())
    || (window.DiscoverV2 ? window.DiscoverV2.state : null);
  const release = state && state.cardsByKey ? state.cardsByKey.get(releaseKey) : null;
  if (!release) return;
  _mode = 'release';
  _focusedId = null;
  document.body.classList.add('wb-inspecting'); // CSS drawer slides in

  if (empty) empty.hidden = true;
  body.hidden = false;
  // A re-focus (e.g. a click + focusin firing together, or focusing a second
  // card) must not destroy the shared #disc-v2-detail-body node when we wipe
  // the body — return it home first, then re-relocate below.
  _restoreDetailHost();
  body.innerHTML = '';

  const r = release.release || {};

  // ── Header: title / artist + mono data chips (R6) ──
  const head = document.createElement('div');
  head.className = 'wb-insp-head';
  const title = document.createElement('div');
  title.className = 'wb-insp-title';
  title.textContent = r.title || '(untitled)';
  const artist = document.createElement('div');
  artist.className = 'wb-insp-artist';
  artist.textContent = r.artist || 'Unknown Artist';
  head.appendChild(title);
  head.appendChild(artist);
  const chips = document.createElement('div');
  chips.className = 'wb-insp-chips';
  if (r.year) chips.appendChild(_chip(String(r.year), true));
  if (r.label) chips.appendChild(_chip(r.label, true));
  if (r.id) chips.appendChild(_chip('#' + r.id, true));
  for (const s of (r.styles || []).slice(0, 4)) chips.appendChild(_chip(s, true));
  head.appendChild(chips);
  body.appendChild(head);

  // ── Detail body (tracklist + YouTube + actions) — reuse the legacy renderer ──
  // The legacy _renderDetailBody writes into document.getElementById(
  // 'disc-v2-detail-body'), which is the node inside the (suppressed) legacy
  // slide-in panel. Relocate that real node into the inspector so the renderer
  // (and its delegation) target our pane, not the hidden panel. _restoreDetailHost
  // (clearInspector) puts it back. The wrapper marks where it lives now.
  const wrap = document.createElement('div');
  wrap.className = 'wb-insp-disc-detail';
  body.appendChild(wrap);
  const detailBody = document.getElementById('disc-v2-detail-body');
  if (detailBody) {
    if (!_detailBodyHome) {
      _detailBodyHome = { parent: detailBody.parentNode, next: detailBody.nextSibling };
    }
    wrap.appendChild(detailBody); // move the canonical node into the inspector
  }

  const render = window._renderDiscoverRenderDetail;
  if (typeof render === 'function') {
    try { render(release, null, 'loading'); } catch (_) {}
    const id = r.id;
    if (id && window.ACBridge && window.ACBridge.discoverLoadDetail) {
      Promise.resolve(window.ACBridge.discoverLoadDetail(id))
        .then((detail) => { if (_mode === 'release') { try { render(release, detail, 'loaded'); } catch (_) {} } })
        .catch((e) => { if (_mode === 'release') { try { render(release, null, 'error', String(e)); } catch (_) {} } });
    } else {
      try { render(release, null, 'loaded'); } catch (_) {}
    }
  }
}

export function clearInspector() {
  _focusedId = null;
  _mode = 'track';
  document.body.classList.remove('wb-inspecting'); // CSS drawer slides out
  // Put the relocated legacy detail node back in its panel BEFORE wiping the
  // inspector body (otherwise innerHTML='' would destroy the shared node).
  _restoreDetailHost();
  const body = document.getElementById('wb-inspector-body');
  const empty = document.getElementById('wb-inspector-empty');
  if (body) { body.hidden = true; body.innerHTML = ''; }
  if (empty) empty.hidden = false;
}

function _restoreDetailHost() {
  if (!_detailBodyHome) return;
  const node = document.getElementById('disc-v2-detail-body');
  if (node && _detailBodyHome.parent) {
    node.innerHTML = '';
    _detailBodyHome.parent.insertBefore(node, _detailBodyHome.next);
  }
  _detailBodyHome = null;
}

export function focusedId() { return _focusedId; }

// Row → inspector wiring (capture phase so it pre-empts the legacy card-body
// select-toggle; checkbox/buttons/badges keep their own behaviour).
export function initInspector() {
  const list = document.getElementById('track-list');
  if (!list) return;
  list.addEventListener('click', (e) => {
    if (!document.body.classList.contains('wb-active')) return;
    // P5: while a Discover release is re-hosted in the inspector, a stray grid
    // click must not clobber it with track detail. (The grid is hidden under
    // the Discover place anyway, but guard defensively.)
    if (_mode === 'release') return;
    if (e.target.closest('input, button, a, .cue-badge, .art-play-overlay, .cue-reason-btn')) return;
    const card = e.target.closest('[data-track-id]');
    if (!card) return;
    e.stopPropagation();
    card.classList.add('wb-focused');
    list.querySelectorAll('.track-card.wb-focused').forEach((el) => { if (el !== card) el.classList.remove('wb-focused'); });
    renderInspector(card.dataset.trackId);
  }, true);
}
