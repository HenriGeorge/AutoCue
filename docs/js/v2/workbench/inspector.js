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

function _chip(text, mono) {
  const s = document.createElement('span');
  s.className = 'wb-insp-chip' + (mono ? ' mono' : '');
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

export function renderInspector(trackId) {
  const body = document.getElementById('wb-inspector-body');
  const empty = document.getElementById('wb-inspector-empty');
  if (!body) return;
  const tracks = window.ACBridge ? window.ACBridge.tracks() : [];
  const t = tracks.find((x) => String(x.id) === String(trackId));
  if (!t) return;
  _focusedId = String(trackId);

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
  if (Number(t.bpm) > 0) chips.appendChild(_chip(Number(t.bpm).toFixed(1) + ' BPM', true));
  if (t.key) chips.appendChild(_chip(t.key, true));
  if (t.totalTime) {
    const m = Math.floor(t.totalTime / 60), s = Math.floor(t.totalTime % 60);
    chips.appendChild(_chip(`${m}:${String(s).padStart(2, '0')}`, true));
  }
  head.appendChild(chips);
  body.appendChild(head);

  // ── Energy curve (reuse _renderEnergySparkline) ──
  const energySec = _section('Energy');
  const energy = document.createElement('div');
  energy.className = 'wb-insp-energy';
  energy.dataset.trackId = _focusedId;
  energySec.appendChild(energy);
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
}

export function clearInspector() {
  _focusedId = null;
  const body = document.getElementById('wb-inspector-body');
  const empty = document.getElementById('wb-inspector-empty');
  if (body) { body.hidden = true; body.innerHTML = ''; }
  if (empty) empty.hidden = false;
}

export function focusedId() { return _focusedId; }

// Row → inspector wiring (capture phase so it pre-empts the legacy card-body
// select-toggle; checkbox/buttons/badges keep their own behaviour).
export function initInspector() {
  const list = document.getElementById('track-list');
  if (!list) return;
  list.addEventListener('click', (e) => {
    if (!document.body.classList.contains('wb-active')) return;
    if (e.target.closest('input, button, a, .cue-badge, .art-play-overlay, .cue-reason-btn')) return;
    const card = e.target.closest('[data-track-id]');
    if (!card) return;
    e.stopPropagation();
    card.classList.add('wb-focused');
    list.querySelectorAll('.track-card.wb-focused').forEach((el) => { if (el !== card) el.classList.remove('wb-focused'); });
    renderInspector(card.dataset.trackId);
  }, true);
}
