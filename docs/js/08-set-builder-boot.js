/* AutoCue app.js — P0 T5 split part 8/8: 08-set-builder-boot.js
 * Classic script (NOT a module): shares globals, loaded in order by
 * docs/index.html. Concatenating 01..08 in order reproduces the original
 * app.js. Do not reorder. See .claude/project/web-ui.md. */
// ── Set Builder ────────────────────────────────────────────────────────────────

const _CAT_COLORS = {
  warmup: '#7ec8e3', build: '#f4a261', peak: '#e63946',
  after_hours: '#9b5de5', closing: '#52b788', unknown: '#aaa',
};

let _sbSeedTrackId = null;
let _sbAnchorTrackIds = [];

function _bpmToCategory(bpm) {
  if (bpm <= 0) return 'peak';
  if (bpm < 100) return 'warmup';
  if (bpm < 118) return 'build';
  if (bpm < 138) return 'peak';
  return 'peak';
}

function _useSelectedForSetBuilder() {
  const ids = [...selectedTrackIds];
  if (!ids.length) { showToast('Select some tracks first', true); return; }
  const lookup = Object.fromEntries((parsedTracks || []).map(t => [String(t.id), t]));
  const tracks = ids.map(id => lookup[String(id)]).filter(Boolean);
  if (!tracks.length) { showToast('Selected tracks not found', true); return; }

  const bpms = tracks.map(t => t.bpm || 0).filter(b => b > 0).sort((a, b) => a - b);
  if (bpms.length) {
    document.getElementById('sb-start-bpm').value = Math.round(bpms[0]);
    document.getElementById('sb-end-bpm').value = Math.round(bpms[bpms.length - 1]);
  }
  _sbAnchorTrackIds = ids.map(id => parseInt(id, 10));
  const n = tracks.length;
  const names = tracks.slice(0, 2).map(t => t.name || t.title || '(untitled)').join(', ');
  const label = `${n} track${n !== 1 ? 's' : ''}: ${names}${n > 2 ? ` + ${n - 2} more` : ''}`;
  document.getElementById('sb-seed-label').textContent = label;
  document.getElementById('sb-seed-row').style.display = '';
  showToast(`${n} anchor${n !== 1 ? 's' : ''} set for Set Builder`);
}

function _useSelectedForPlaylist() {
  const ids = [...selectedTrackIds];
  if (!ids.length) { showToast('Select some tracks first', true); return; }
  const lookup = Object.fromEntries((parsedTracks || []).map(t => [String(t.id), t]));
  const tracks = ids.map(id => lookup[String(id)]).filter(Boolean);
  if (!tracks.length) { showToast('Selected tracks not found', true); return; }

  _psSeedTrackIds = ids.map(id => parseInt(id, 10));
  _psExcludedIds = [];
  _psCategoryLast = null;

  // Auto-detect category from median BPM
  const bpms = tracks.map(t => t.bpm || 0).filter(b => b > 0);
  if (bpms.length) {
    const medianBpm = bpms.sort((a, b) => a - b)[Math.floor(bpms.length / 2)];
    const cat = _bpmToCategory(medianBpm);
    document.getElementById('ps-category').value = cat;
  }
  const n = tracks.length;
  const status = document.getElementById('ps-status');
  if (status) status.textContent = `${n} seed track${n !== 1 ? 's' : ''} pre-included`;
  showToast(`${n} seed track${n !== 1 ? 's' : ''} set for Playlist Suggest`);
}

let _psExcludedIds = [];
let _psCategoryLast = null;
let _psTracks = [];
let _psDragIdx = null;
let _psSeedTrackIds = [];

async function suggestPlaylist(append) {
  const btn     = document.getElementById('ps-suggest-btn');
  const moreBtn = document.getElementById('ps-more-btn');
  const resetBtn = document.getElementById('ps-reset-btn');
  const status  = document.getElementById('ps-status');
  const result  = document.getElementById('ps-result');
  const summary = document.getElementById('ps-summary');
  const list    = document.getElementById('ps-tracklist');

  const category = document.getElementById('ps-category').value;
  const count    = parseInt(document.getElementById('ps-count').value, 10) || 20;

  // Reset excluded list if category changed
  if (category !== _psCategoryLast) {
    _psExcludedIds = [];
    _psCategoryLast = category;
    list.innerHTML = '';
  }

  if (!append) {
    _psExcludedIds = [];
    list.innerHTML = '';
  }

  _setBtnLoading(btn, true, append ? 'Adding…' : 'Searching…');
  moreBtn.disabled = true;
  status.textContent = '';
  if (!append) result.style.display = 'none';
  const psProgress = document.getElementById('ps-progress');
  if (psProgress) psProgress.style.display = '';

  try {
    const psBody = { category, count, exclude_ids: _psExcludedIds };
    if (_psSeedTrackIds.length) psBody.seed_track_ids = _psSeedTrackIds;
    const r = await fetch('/api/playlists/suggest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(psBody),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    status.textContent = '';
    const trackMap = Object.fromEntries((parsedTracks || []).map(t => [String(t.id), t]));
    if (!append) _psTracks = [];
    for (const item of d.results) {
      _psExcludedIds.push(item.track_id);
      const t = trackMap[String(item.track_id)];
      _psTracks.push({
        track_id:  item.track_id,
        title:     t ? (t.name || t.title || '(untitled)') : '(track ' + item.track_id + ')',
        artist:    t ? (t.artist || '') : '',
        bpm:       t ? t.bpm : 0,
        key:       t ? (t.key || '—') : '—',
        category:  category,
        score:     item.score,
      });
    }
    _psRenderSet();
    const totalShown = _psTracks.length;
    summary.textContent = `${totalShown} tracks for "${category}"${_psExcludedIds.length > totalShown ? ' (excluding ' + (_psExcludedIds.length - totalShown) + ' already shown)' : ''}`;
    result.style.display = '';
    moreBtn.style.display = d.results.length >= count ? '' : 'none';
    resetBtn.style.display = _psExcludedIds.length > 0 ? '' : 'none';
    showToast(append ? `Added ${d.results.length} more ${category} tracks` : `Found ${d.results.length} ${category} tracks`);
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
    showToast(`Suggest failed: ${err.message}`, true);
  } finally {
    if (psProgress) psProgress.style.display = 'none';
    _setBtnLoading(btn, false);
    moreBtn.disabled = false;
  }
}

// ── Playlist Suggest interactive ─────────────────────────────────────────────

function _psRenderSet() {
  const list = document.getElementById('ps-tracklist');
  if (!list) return;
  list.innerHTML = '';

  const allIds = _psTracks.map(t => t.track_id);

  _psTracks.forEach((t, i) => {
    const row = document.createElement('div');
    row.className = 'sb-row';
    row.draggable = true;
    row.dataset.index = i;

    const catColor = _CAT_COLORS[t.category] || '#888';
    const scorePct = t.score != null ? Math.round(t.score * 100) : null;
    const scoreColor = scorePct == null ? 'var(--muted)' : scorePct >= 70 ? 'var(--green)' : scorePct >= 45 ? '#e07000' : '#c03030';

    row.innerHTML = `
      <div class="sb-drag-handle" title="Drag to reorder">⠿</div>
      <div class="sb-row-num">${i + 1}</div>
      <div class="sb-art" data-tid="${t.track_id}">
        <div class="sb-art-ph">♪</div>
        <div class="sb-art-play">▶</div>
      </div>
      <div class="sb-row-main">
        <div class="sb-row-title">${t.title || '(untitled)'}</div>
        <div class="sb-row-artist">${t.artist || ''}</div>
      </div>
      <div class="sb-row-meta">
        <span class="sb-track-bpm">${t.bpm ? t.bpm.toFixed(1) : '—'}</span>
        <span class="sb-track-key">${t.key || '—'}</span>
        <span class="sb-track-cat" style="color:${catColor};border-color:${catColor};background:${catColor}18">${t.category}</span>
        ${scorePct != null ? `<span style="font-size:11px;font-weight:600;color:${scoreColor}">${scorePct}%</span>` : ''}
      </div>
      <button class="sb-row-replace" data-idx="${i}">↻ Replace</button>
    `;

    // Artwork (lazy, always — server is up whenever PS is shown)
    const psArtEl = row.querySelector('.sb-art');
    const psArtImg = document.createElement('img');
    psArtImg.loading = 'lazy';
    psArtImg.src = '/api/tracks/' + t.track_id + '/artwork';
    psArtImg.onload = function() { const ph = psArtEl.querySelector('.sb-art-ph'); if (ph) ph.style.display = 'none'; };
    psArtImg.onerror = function() {};
    psArtEl.insertBefore(psArtImg, psArtEl.querySelector('.sb-art-play'));

    row.addEventListener('dragstart', e => {
      _psDragIdx = i;
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
    });
    row.addEventListener('dragend', () => {
      row.classList.remove('dragging');
      list.querySelectorAll('.sb-row').forEach(r => r.classList.remove('drag-over'));
    });
    row.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      list.querySelectorAll('.sb-row').forEach(r => r.classList.remove('drag-over'));
      row.classList.add('drag-over');
    });
    row.addEventListener('drop', e => {
      e.preventDefault();
      row.classList.remove('drag-over');
      if (_psDragIdx !== null && _psDragIdx !== i) {
        const moved = _psTracks.splice(_psDragIdx, 1)[0];
        _psTracks.splice(i, 0, moved);
        _psDragIdx = null;
        _psRenderSet();
        _psUpdateSummary();
      }
    });

    row.querySelector('.sb-row-replace').addEventListener('click', e => {
      e.stopPropagation();
      _psToggleAltPanel(i, t.track_id, allIds);
    });

    // Playback: hover preloads; click art cell plays (local mode only)
    if (localMode) {
      psArtEl.addEventListener('mouseenter', () => {
        const tr = (parsedTracks || []).find(x => x.id == t.track_id);
        if (tr) ensureLocalAudio(tr).catch(() => {});
      });
      psArtEl.addEventListener('click', e => {
        e.stopPropagation();
        const tr = (parsedTracks || []).find(x => x.id == t.track_id);
        if (!tr) return;
        ensureLocalAudio(tr).then(() => {
          if (audioState[tr.id]) {
            playTrack(tr.id, 0);
            document.querySelectorAll('.sb-art').forEach(el => {
              const isPlaying = el.dataset.tid == t.track_id;
              el.classList.toggle('playing', isPlaying);
              el.querySelector('.sb-art-play').textContent = isPlaying ? '⏸' : '▶';
            });
          }
        });
      });
    }

    // Mark seed tracks visually
    if (_psSeedTrackIds.includes(t.track_id)) row.classList.add('anchor-track');

    list.appendChild(row);
  });
}

async function _psToggleAltPanel(idx, trackId, allIds) {
  const list = document.getElementById('ps-tracklist');
  const existing = list.querySelector('.sb-alt-panel');
  if (existing) {
    const wasIdx = parseInt(existing.dataset.forIdx);
    existing.remove();
    if (wasIdx === idx) return;
  }

  const rows = list.querySelectorAll('.sb-row');
  if (!rows[idx]) return;

  const panel = document.createElement('div');
  panel.className = 'sb-alt-panel';
  panel.dataset.forIdx = idx;
  panel.innerHTML = `<div class="sb-alt-panel-title">↻ Finding best replacements…</div>`;
  list.insertBefore(panel, rows[idx].nextSibling);

  const prev = _psTracks[idx - 1];
  const next = _psTracks[idx + 1];
  const excludeStr = allIds.join(',');

  try {
    const params = new URLSearchParams({ track_id: trackId, exclude_ids: excludeStr, n: 8 });
    if (prev) params.set('prev_id', prev.track_id);
    if (next) params.set('next_id', next.track_id);

    const r = await fetch(`/api/setbuilder/alternatives?${params}`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const d = await r.json();

    if (!d.alternatives.length) {
      panel.innerHTML = `<div class="sb-alt-panel-title">No suitable replacements found in library</div>`;
      return;
    }

    let html = `<div class="sb-alt-panel-title">↻ Best replacements for position ${idx + 1} <span style="font-weight:400;color:var(--muted-soft)">(click to swap)</span></div>`;
    for (const alt of d.alternatives) {
      const scoreColor = alt.score >= 70 ? 'var(--green)' : alt.score >= 45 ? '#e07000' : '#c03030';
      const fromStr = alt.from_prev != null ? `from prev: ${Math.round(alt.from_prev)}` : '';
      const toStr   = alt.to_next != null ? `to next: ${Math.round(alt.to_next)}` : '';
      const reasons = [fromStr, toStr].filter(Boolean).join(' · ');
      const genreColor = alt.genre_match === true ? 'var(--green)' : alt.genre_match === false ? '#c03030' : 'var(--muted)';
      const genreBadge = alt.genre ? `<span style="font-size:10px;padding:1px 5px;border-radius:3px;border:1px solid ${genreColor}44;color:${genreColor};white-space:nowrap;overflow:hidden;max-width:80px;text-overflow:ellipsis;" title="${_esc(alt.genre)}">${_esc(alt.genre)}</span>` : '';
      html += `
        <div class="sb-alt-item" data-alt-idx="${idx}" data-alt='${JSON.stringify(alt).replace(/'/g,"&#39;")}'>
          <div class="sb-alt-main">
            <div class="sb-alt-title">${_esc(alt.title || '(untitled)')}</div>
            <div class="sb-alt-artist">${_esc(alt.artist || '')}</div>
          </div>
          <div class="sb-alt-meta">
            <span style="font-family:var(--mono)">${alt.bpm.toFixed(1)}</span>
            <span style="background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:1px 4px;font-family:var(--mono)">${_esc(alt.key || '—')}</span>
            ${genreBadge}
            ${reasons ? `<span style="color:var(--muted-soft)">${reasons}</span>` : ''}
            <span class="sb-alt-score" style="color:${scoreColor}">${Math.round(alt.score)}</span>
          </div>
        </div>`;
    }
    panel.innerHTML = html;

    panel.querySelectorAll('.sb-alt-item').forEach(el => {
      el.addEventListener('click', () => {
        const altIdx = parseInt(el.dataset.altIdx);
        const alt = JSON.parse(el.dataset.alt);
        _psTracks[altIdx] = {
          track_id: alt.track_id,
          title:    alt.title,
          artist:   alt.artist,
          bpm:      alt.bpm,
          key:      alt.key,
          category: _psTracks[altIdx].category,
          score:    null,
        };
        panel.remove();
        _psRenderSet();
        _psUpdateSummary();
        showToast(`Replaced with ${alt.title || 'track'}`);
      });
    });
  } catch (err) {
    panel.innerHTML = `<div class="sb-alt-panel-title" style="color:#c03030">${_humanFetchError(err)}</div>`;
  }
}

function _psUpdateSummary() {
  const summary = document.getElementById('ps-summary');
  if (!summary) return;
  summary.textContent = `${_psTracks.length} tracks (edited)`;
}

async function psSavePlaylist() {
  const name = prompt('Playlist name:', 'AutoCue Suggest ' + new Date().toLocaleDateString());
  if (!name) return;
  const ids = _psTracks.map(t => t.track_id);
  try {
    const r = await fetch('/api/playlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, track_ids: ids }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    showToast(`Playlist "${d.name}" created (${d.track_count} tracks)`);
  } catch (err) {
    showToast(`Failed to create playlist: ${err.message}`, true);
  }
}

// ── Set Builder state ────────────────────────────────────────────────────────
let _sbTracks = [];       // current mutable set
let _sbDragIdx = null;    // drag source index

function _sbKeyCompat(keyA, keyB) {
  if (!keyA || !keyB || keyA === '—' || keyB === '—') return null;
  // Camelot notation: same number ±1 letter, or same letter ±1 number = compatible
  const m = (k) => k.match(/^(\d+)([AB])$/i);
  const a = m(keyA), b = m(keyB);
  if (!a || !b) return null;
  const [, na, la] = a; const [, nb, lb] = b;
  const dn = Math.abs(parseInt(na) - parseInt(nb));
  const sameKey = na === nb && la.toUpperCase() === lb.toUpperCase();
  const adj = (dn <= 1 || dn === 11) && la.toUpperCase() === lb.toUpperCase();
  const relative = na === nb && la.toUpperCase() !== lb.toUpperCase();
  return sameKey || adj || relative;
}

function _sbRenderSet() {
  const list = document.getElementById('sb-tracklist');
  if (!list) return;
  list.innerHTML = '';

  const allIds = _sbTracks.map(t => t.track_id);

  _sbTracks.forEach((t, i) => {
    // ── Track row ──
    const row = document.createElement('div');
    row.className = 'sb-row';
    row.draggable = true;
    row.dataset.index = i;

    const catColor = _CAT_COLORS[t.category] || '#888';
    const relaxedAttr = t.relaxed
      ? ` title="Placed via relaxed constraints" style="opacity:.6"`
      : '';

    row.innerHTML = `
      <div class="sb-drag-handle" title="Drag to reorder">⠿</div>
      <div class="sb-row-num">${i + 1}</div>
      <div class="sb-art" data-tid="${t.track_id}">
        <div class="sb-art-ph">♪</div>
        <div class="sb-art-play">▶</div>
      </div>
      <div class="sb-row-main">
        <div class="sb-row-title">${t.title || '(untitled)'}${t.relaxed ? ' <span style="font-size:9px;color:var(--muted-soft);font-weight:400">(relaxed)</span>' : ''}</div>
        <div class="sb-row-artist">${t.artist || ''}</div>
      </div>
      <div class="sb-row-meta">
        <span class="sb-track-bpm">${t.bpm.toFixed(1)}</span>
        <span class="sb-track-key">${t.key || '—'}</span>
        <span class="sb-track-cat" style="color:${catColor};border-color:${catColor};background:${catColor}18">${t.category}</span>
      </div>
      <button class="sb-row-replace" data-idx="${i}">↻ Replace</button>
    `;

    // Artwork (lazy)
    const sbArtEl = row.querySelector('.sb-art');
    const sbArtImg = document.createElement('img');
    sbArtImg.loading = 'lazy';
    sbArtImg.src = '/api/tracks/' + t.track_id + '/artwork';
    sbArtImg.onload = function() { const ph = sbArtEl.querySelector('.sb-art-ph'); if (ph) ph.style.display = 'none'; };
    sbArtImg.onerror = function() {};
    sbArtEl.insertBefore(sbArtImg, sbArtEl.querySelector('.sb-art-play'));

    // Drag handlers
    row.addEventListener('dragstart', e => {
      _sbDragIdx = i;
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed = 'move';
    });
    row.addEventListener('dragend', () => {
      row.classList.remove('dragging');
      list.querySelectorAll('.sb-row').forEach(r => r.classList.remove('drag-over'));
    });
    row.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      list.querySelectorAll('.sb-row').forEach(r => r.classList.remove('drag-over'));
      row.classList.add('drag-over');
    });
    row.addEventListener('drop', e => {
      e.preventDefault();
      row.classList.remove('drag-over');
      if (_sbDragIdx !== null && _sbDragIdx !== i) {
        const moved = _sbTracks.splice(_sbDragIdx, 1)[0];
        _sbTracks.splice(i, 0, moved);
        _sbDragIdx = null;
        _sbRenderSet();
        _sbUpdateSummary();
      }
    });

    // Replace button
    row.querySelector('.sb-row-replace').addEventListener('click', e => {
      e.stopPropagation();
      _sbToggleAltPanel(i, t.track_id, allIds);
    });

    // Playback: hover preloads audio; click art cell plays (local mode only)
    if (localMode) {
      sbArtEl.addEventListener('mouseenter', () => {
        const tr = (parsedTracks || []).find(x => x.id == t.track_id);
        if (tr) ensureLocalAudio(tr).catch(() => {});
      });
      sbArtEl.addEventListener('click', e => {
        e.stopPropagation();
        const tr = (parsedTracks || []).find(x => x.id == t.track_id);
        if (!tr) return;
        ensureLocalAudio(tr).then(() => {
          if (audioState[tr.id]) {
            playTrack(tr.id, 0);
            document.querySelectorAll('.sb-art').forEach(el => {
              const isPlaying = el.dataset.tid == t.track_id;
              el.classList.toggle('playing', isPlaying);
              el.querySelector('.sb-art-play').textContent = isPlaying ? '⏸' : '▶';
            });
          }
        });
      });
    }

    // Mark anchor tracks visually
    if (_sbAnchorTrackIds.includes(t.track_id)) row.classList.add('anchor-track');

    list.appendChild(row);

    // ── Transition connector to next track ──
    if (i < _sbTracks.length - 1) {
      const next = _sbTracks[i + 1];
      const conn = document.createElement('div');
      conn.className = 'sb-connector';

      const bpmDiff = next.bpm - t.bpm;
      const bpmStr = `${t.bpm.toFixed(1)} → ${next.bpm.toFixed(1)} BPM (${bpmDiff >= 0 ? '+' : ''}${bpmDiff.toFixed(1)})`;

      const keyCompat = _sbKeyCompat(t.key, next.key);
      const keyStr = (t.key && next.key && t.key !== '—' && next.key !== '—')
        ? `${t.key} → ${next.key}${keyCompat === true ? ' ✓' : keyCompat === false ? ' ✗' : ''}`
        : '';

      const score = next.transition_score;
      const scoreClass = score == null ? '' : score >= 70 ? 'sb-conn-score' : score >= 45 ? 'sb-conn-score low' : 'sb-conn-score bad';
      const scoreStr = score != null ? `<span class="${scoreClass}">${Math.round(score)}</span>` : '';

      const adviceStr = next.mix_advice || '';
      conn.innerHTML = `
        <div class="sb-connector-line"></div>
        <div style="display:flex;flex-direction:column;gap:2px;">
          <div class="sb-connector-info">
            ${bpmStr}
            ${keyStr ? `· ${keyStr}` : ''}
            ${scoreStr ? `· Mix ${scoreStr}` : ''}
          </div>
          ${adviceStr ? `<div style="font-size:10px;color:var(--muted-soft);padding-left:0;font-style:italic;">💡 ${adviceStr}</div>` : ''}
        </div>
      `;
      list.appendChild(conn);
    }
  });
}

async function _sbToggleAltPanel(idx, trackId, allIds) {
  const list = document.getElementById('sb-tracklist');
  // Remove any existing panel
  const existing = list.querySelector('.sb-alt-panel');
  if (existing) {
    const wasIdx = parseInt(existing.dataset.forIdx);
    existing.remove();
    if (wasIdx === idx) return; // toggle off
  }

  // Insert panel after the row at idx (accounting for connectors: row + connector per pair)
  const rows = list.querySelectorAll('.sb-row');
  if (!rows[idx]) return;
  const insertAfter = rows[idx].nextSibling; // may be connector or next row or null

  const panel = document.createElement('div');
  panel.className = 'sb-alt-panel';
  panel.dataset.forIdx = idx;
  panel.innerHTML = `<div class="sb-alt-panel-title">↻ Finding best replacements…</div>`;
  list.insertBefore(panel, insertAfter);

  const prev = _sbTracks[idx - 1];
  const next = _sbTracks[idx + 1];
  const excludeStr = allIds.join(',');

  try {
    const params = new URLSearchParams({ track_id: trackId, exclude_ids: excludeStr, n: 8 });
    if (prev) params.set('prev_id', prev.track_id);
    if (next) params.set('next_id', next.track_id);

    const r = await fetch(`/api/setbuilder/alternatives?${params}`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const d = await r.json();

    if (!d.alternatives.length) {
      panel.innerHTML = `<div class="sb-alt-panel-title">No suitable replacements found in library</div>`;
      return;
    }

    let html = `<div class="sb-alt-panel-title">↻ Best replacements for position ${idx + 1} <span style="font-weight:400;color:var(--muted-soft)">(click to swap)</span></div>`;
    for (const alt of d.alternatives) {
      const scoreColor = alt.score >= 70 ? 'var(--green)' : alt.score >= 45 ? '#e07000' : '#c03030';
      const fromStr = alt.from_prev != null ? `from prev: ${Math.round(alt.from_prev)}` : '';
      const toStr   = alt.to_next != null ? `to next: ${Math.round(alt.to_next)}` : '';
      const reasons = [fromStr, toStr].filter(Boolean).join(' · ');
      const genreColor = alt.genre_match === true ? 'var(--green)' : alt.genre_match === false ? '#c03030' : 'var(--muted)';
      const genreBadge = alt.genre ? `<span style="font-size:10px;padding:1px 5px;border-radius:3px;border:1px solid ${genreColor}44;color:${genreColor};white-space:nowrap;overflow:hidden;max-width:80px;text-overflow:ellipsis;" title="${_esc(alt.genre)}">${_esc(alt.genre)}</span>` : '';
      html += `
        <div class="sb-alt-item" data-alt-idx="${idx}" data-alt='${JSON.stringify(alt).replace(/'/g,"&#39;")}'>
          <div class="sb-alt-main">
            <div class="sb-alt-title">${_esc(alt.title || '(untitled)')}</div>
            <div class="sb-alt-artist">${_esc(alt.artist || '')}</div>
          </div>
          <div class="sb-alt-meta">
            <span style="font-family:var(--mono)">${alt.bpm.toFixed(1)}</span>
            <span style="background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:1px 4px;font-family:var(--mono)">${_esc(alt.key || '—')}</span>
            ${genreBadge}
            ${reasons ? `<span style="color:var(--muted-soft)">${reasons}</span>` : ''}
            <span class="sb-alt-score" style="color:${scoreColor}">${Math.round(alt.score)}</span>
          </div>
        </div>`;
    }
    panel.innerHTML = html;

    panel.querySelectorAll('.sb-alt-item').forEach(el => {
      el.addEventListener('click', () => {
        const altIdx = parseInt(el.dataset.altIdx);
        const alt = JSON.parse(el.dataset.alt);
        const cur = _sbTracks[altIdx];
        _sbTracks[altIdx] = {
          track_id:        alt.track_id,
          title:           alt.title,
          artist:          alt.artist,
          bpm:             alt.bpm,
          key:             alt.key,
          category:        cur.category,
          transition_score: alt.to_next,
          relaxed:         false,
        };
        panel.remove();
        _sbRenderSet();
        _sbUpdateSummary();
        showToast(`Replaced with ${alt.title || 'track'}`);
      });
    });
  } catch (err) {
    panel.innerHTML = `<div class="sb-alt-panel-title" style="color:#c03030">${_humanFetchError(err)}</div>`;
  }
}

function _sbUpdateSummary() {
  const summary = document.getElementById('sb-summary');
  if (!summary) return;
  const n = _sbTracks.length;
  // rough estimate: avg 6 min/track
  const mins = Math.round(n * 6);
  summary.textContent = `${n} tracks · ~${mins} min (edited)`;
}

async function sbSavePlaylist() {
  const name = prompt('Playlist name:', 'AutoCue Set ' + new Date().toLocaleDateString());
  if (!name) return;
  const ids = _sbTracks.map(t => t.track_id);
  try {
    const r = await fetch('/api/playlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, track_ids: ids }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    showToast(`Playlist "${d.name}" created (${d.track_count} tracks)`);
  } catch (err) {
    showToast(`Failed to create playlist: ${err.message}`, true);
  }
}

async function buildSet() {
  const btn      = document.getElementById('sb-build-btn');
  const status   = document.getElementById('sb-status');
  const result   = document.getElementById('sb-result');
  const summary  = document.getElementById('sb-summary');
  const progBar  = document.getElementById('sb-progress');

  const body = {
    start_bpm:        parseFloat(document.getElementById('sb-start-bpm').value) || 110,
    end_bpm:          parseFloat(document.getElementById('sb-end-bpm').value) || 135,
    duration_minutes: parseFloat(document.getElementById('sb-duration').value) || 60,
    energy_mode:      document.getElementById('sb-energy-mode').value,
  };
  if (_sbAnchorTrackIds.length) body.anchor_track_ids = _sbAnchorTrackIds;
  else if (_sbSeedTrackId) body.seed_track_id = _sbSeedTrackId;

  _setBtnLoading(btn, true, 'Building…');
  status.textContent = '';
  result.style.display = 'none';
  progBar.style.display = '';

  try {
    const r = await fetch('/api/setbuilder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    status.textContent = '';
    const terminationLabels = {
      'target_duration_reached':         '',
      'no_candidates_passed_thresholds': ' · ⚠ library too narrow',
      'safety_cap_hit':                  ' · ⚠ search exhausted early',
    };
    const note = terminationLabels[d.terminated_reason] || '';
    summary.textContent = `${d.total_tracks} tracks · ~${d.estimated_duration_minutes} min${note}`;
    _sbTracks = d.tracks.slice();
    _sbRenderSet();
    result.style.display = '';
    showToast(`Set built: ${d.total_tracks} tracks`);
  } catch (err) {
    const human = _humanFetchError(err);
    status.textContent = human;
    showToast(human, true);
  } finally {
    _setBtnLoading(btn, false);
    progBar.style.display = 'none';
  }
}

document.addEventListener('DOMContentLoaded', function() {
  var cards = document.querySelectorAll('.panel-card');
  var sectionObserver = new IntersectionObserver(function(entries) {
    entries.forEach(function(e) {
      if (e.isIntersecting) {
        e.target.style.opacity = '';
        e.target.style.transform = '';
        e.target.classList.add('animate-in');
        sectionObserver.unobserve(e.target);
      }
    });
  }, { threshold: 0.05 });
  cards.forEach(function(el) {
    var rect = el.getBoundingClientRect();
    if (rect.top > window.innerHeight) {
      el.style.opacity = '0';
      el.style.transform = 'translateY(10px)';
    }
    sectionObserver.observe(el);
  });
});

// ── DJ Mixing Guide ───────────────────────────────────────────────────────────
(function initMixingGuide() {
  var header  = document.getElementById('sb-guide-header');
  var body    = document.getElementById('sb-guide-body');
  var chevron = document.getElementById('sb-guide-chevron');
  if (!header) return;

  header.addEventListener('click', function() {
    var open = body.classList.contains('open');
    chevron.classList.toggle('open', !open);
    if (open) { _slideClose(body, 'open'); } else { _slideOpen(body, 'open'); }
  });

  document.querySelectorAll('[data-guide-tab]').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var tab = btn.dataset.guideTab;
      document.querySelectorAll('[data-guide-tab]').forEach(function(b) { b.classList.remove('active'); });
      document.querySelectorAll('[data-guide-panel]').forEach(function(p) { p.classList.remove('active'); });
      btn.classList.add('active');
      var panel = document.querySelector('[data-guide-panel="' + tab + '"]');
      if (panel) panel.classList.add('active');
    });
  });
})();

// ── Top bar: sticky glass + height tracking ────────────────────────────────────
(function() {
  var bar = document.getElementById('top-bar');
  if (!bar) return;

  // Glass effect on scroll
  window.addEventListener('scroll', function() {
    bar.classList.toggle('scrolled', window.scrollY > 4);
  }, { passive: true });

  // Keep --top-bar-h in sync so #tracks-sticky sticks below the bar
  function sync() {
    document.documentElement.style.setProperty('--top-bar-h', bar.offsetHeight + 'px');
  }
  sync();
  new ResizeObserver(sync).observe(bar);
})();

// ── Theme toggle ───────────────────────────────────────────────────────────────
const root = document.documentElement;
const themeBtn = document.getElementById('theme-toggle');
function applyTheme(dark) {
  root.classList.toggle('dark', dark);
  themeBtn.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
  try { localStorage.setItem('ac_theme', dark ? 'dark' : 'light'); } catch (_) {}
}
// Saved choice wins; first visit follows the OS preference (a DJ at night
// shouldn't get flashed white); the toggle then pins it.
let _savedTheme = null;
try { _savedTheme = localStorage.getItem('ac_theme'); } catch (_) {}
applyTheme(_savedTheme !== null
  ? _savedTheme === 'dark'
  : !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches));
themeBtn.addEventListener('click', () => applyTheme(!root.classList.contains('dark')));

/* P0 T5 relocation: detectLocalMode().then(...) moved here from its
 * original position (was app.js 5824-5935, segment 04). Its async
 * callback references functions declared in segments 05-08 (buildSet,
 * psSavePlaylist, loadTracksFromServer, ...); placing it last guarantees
 * every top-level declaration exists before the callback can fire. */
detectLocalMode().then(async connected => {
  localMode = connected;
  if (localMode) {
    // AutoCue 2.0: signal v2 modules that local mode is confirmed (they gate
    // their UI on this — the global layer never renders in XML/Pages mode).
    try { window.dispatchEvent(new CustomEvent('autocue:local-mode')); } catch (_) {}
    // Ease the tab chrome in — it used to pop over the already-painted XML UI
    const _tn = document.getElementById('tab-nav');
    _tn.style.display = '';
    _tn.classList.add('fade-in-up');
    _tn.addEventListener('animationend', () => _tn.classList.remove('fade-in-up'), { once: true });
    document.getElementById('steps').style.display = 'none';
    document.querySelector('.mode-callout')?.setAttribute('style', 'display:none');
    document.getElementById('drop-zone').style.display = 'none';
    document.getElementById('local-mode-banner').style.display = 'inline-flex';
    updateAppStatus({ connected: true });
    document.getElementById('download-btn').textContent = 'Apply to Rekordbox';
    document.getElementById('delete-cues-btn').style.display = '';
    document.getElementById('skip-colored-label').style.display = 'flex';
    document.getElementById('color-by-bpm-btn').style.display = '';
    document.getElementById('preview-cues-btn').style.display = '';
    document.getElementById('apply-sep').style.display = '';
    document.getElementById('how-to').style.display = 'none';
    document.getElementById('local-how-to').style.display = '';
    document.getElementById('health-section').style.display = '';
    document.getElementById('health-scan-btn').addEventListener('click', scanLibraryHealth);
    // P3: the duplicates surface lives in the workbench centre-pane place
    // (#wb-dupes-pane); the rescan pill is its toolbar verb. The first scan
    // is lazy (fired by the place's activate()), not eager at boot.
    document.getElementById('wb-dupes-rescan').addEventListener('click', scanDuplicates);
    document.getElementById('cue-tools-section').style.display = '';
    _initCueTools();
    document.getElementById('discogs-section').style.display = '';
    document.getElementById('discogs-run-btn').addEventListener('click', discogsTagTracks);
    document.getElementById('discogs-save-btn').addEventListener('click', discogsSaveToken);
    discogsLoadSavedToken();
    document.getElementById('comment-enrich-section').style.display = '';
    document.getElementById('ce-run-btn').addEventListener('click', enrichComments);
    document.getElementById('ce-preview-btn').addEventListener('click', previewComment);
    document.getElementById('playlist-suggest-section').style.display = '';
    document.getElementById('ps-suggest-btn').addEventListener('click', () => suggestPlaylist(false));
    document.getElementById('ps-use-selected-btn').addEventListener('click', _useSelectedForPlaylist);
    document.getElementById('ps-more-btn').addEventListener('click', () => suggestPlaylist(true));
    document.getElementById('ps-reset-btn').addEventListener('click', () => {
      _psExcludedIds = [];
      _psSeedTrackIds = [];
      _psTracks = [];
      document.getElementById('ps-tracklist').innerHTML = '';
      document.getElementById('ps-result').style.display = 'none';
      document.getElementById('ps-more-btn').style.display = 'none';
      document.getElementById('ps-reset-btn').style.display = 'none';
      document.getElementById('ps-summary').textContent = '';
      document.getElementById('ps-status').textContent = '';
    });
    document.getElementById('ps-save-playlist-btn').addEventListener('click', psSavePlaylist);
    document.getElementById('setbuilder-section').style.display = '';
    document.getElementById('sb-build-btn').addEventListener('click', buildSet);
    document.getElementById('sb-use-selected-btn').addEventListener('click', _useSelectedForSetBuilder);
    document.getElementById('sb-seed-clear').addEventListener('click', () => {
      _sbSeedTrackId = null;
      _sbAnchorTrackIds = [];
      document.getElementById('sb-seed-row').style.display = 'none';
    });
    document.getElementById('sb-save-playlist-btn').addEventListener('click', sbSavePlaylist);
    initDiscover();
    initDiscoverV2();  // T-024 — wires up the new Discover tab surface

    // Copy track list buttons
    function makeCopyHandler(listId, btnId, tracksRef) {
      const btn = document.getElementById(btnId);
      if (!btn) return;
      btn.addEventListener('click', () => {
        const text = tracksRef().map((t, i) => `${i + 1}. ${t.title || '(untitled)'} — ${t.artist || ''} (${t.bpm ? t.bpm.toFixed(1) + ' BPM' : ''}, ${t.key || '—'})`).join('\n');
        navigator.clipboard.writeText(text).then(() => {
          btn.textContent = '✓ Copied';
          btn.classList.add('copied');
          setTimeout(() => { btn.textContent = 'Copy list'; btn.classList.remove('copied'); }, 2000);
        }).catch(() => showToast('Copy failed'));
      });
    }
    makeCopyHandler('ps-tracklist', 'ps-copy-btn', () => _psTracks);
    makeCopyHandler('sb-tracklist', 'sb-copy-btn', () => _sbTracks);

    // F2: Load playlists into dropdown
    document.getElementById('playlist-filter-bar').style.display = 'flex';
    document.getElementById('filter-bar').style.display = 'flex';
    try {
      const playlists = await fetch('/api/playlists').then(r => r.json());
      const sel = document.getElementById('playlist-select');
      for (const pl of playlists) {
        const opt = document.createElement('option');
        opt.value = pl.id;
        opt.textContent = `${pl.name} (${pl.track_count})`;
        sel.appendChild(opt);
      }
    } catch {}

    try {
      const tags = await fetch('/api/tags').then(r => r.json());
      const chipsEl = document.getElementById('tag-filter-chips');
      for (const tag of tags) {
        const chip = document.createElement('button');
        chip.className = 'tf-chip';
        chip.dataset.tag = tag.name;
        chip.textContent = tag.name;
        const c = (typeof AUTO_TAG_COLORS !== 'undefined') && AUTO_TAG_COLORS[tag.name];
        chip.style.cssText = c
          ? `background:${c}22;border:1px solid ${c}55;color:${c};border-radius:10px;padding:2px 8px;font-size:11px;cursor:pointer;`
          : 'background:var(--surface2);border:1px solid var(--border);color:var(--text);border-radius:10px;padding:2px 8px;font-size:11px;cursor:pointer;';
        chip.addEventListener('click', e => { e.stopPropagation(); window._toggleTagFilter(tag.name); });
        chipsEl.appendChild(chip);
      }
    } catch {}

    loadTracksFromServer();
  }
});

// ── AutoCue 2.0 bridge (read-only) ──────────────────────────────────────────
// v2 ES modules (docs/js/v2/) read legacy state ONLY through here. Top-level
// `let` bindings (parsedTracks, healthLastSummary, localMode, selectedTrackIds)
// live in the shared global lexical environment across the classic scripts but
// are NOT properties of `window`, so v2 (a module, separate scope) can't see
// them directly. These accessor closures capture them lexically. Read-only by
// contract: v2 never mutates legacy state through this object.
window.ACBridge = {
  tracks: () => parsedTracks,
  healthSummary: () => healthLastSummary,
  isLocalMode: () => localMode,
  selectedCount: () => selectedTrackIds.size,
  // P2 workbench shell reads these (still read-only for state; the fn
  // pass-throughs let renderInspector/the rail call legacy builders without
  // poking module internals).
  selectedIds: () => selectedTrackIds,
  pending: () => pendingCues,
  activePlaylistId: () => activePlaylistId,
  // P2 proposal organ: parsed track-ids (ints) that are BOTH pending AND
  // approved — the Apply gate. Returns null (meaning "fall back to the normal
  // activeTracks() path") UNLESS the proposals module exists AND there are
  // pending cues. Flag-off / no-pending → null → legacy behaviour untouched.
  approvedApplyIds: () => {
    var prop = window.AC2 && window.AC2.proposals;
    if (!prop) return null;
    if (!pendingCues || Object.keys(pendingCues).length === 0) return null;
    return prop.approvedIntersectPending().map(function (id) { return parseInt(id, 10); });
  },
  // function pass-throughs
  filteredTracks: () => filteredTracks(),
  sortedTracks: () => sortedTracks(),
  activeTracks: () => activeTracks(),
  renderTracks: () => renderTracks(),
  buildTrackCard: (...a) => buildTrackCard(...a),
  explainCue: (cue) => _explainCue(cue),
  showTransitionScore: () => showTransitionScore(),
  // P2 workbench rail crate filter (cue-state). Mutates the legacy global +
  // re-renders via the existing AppState bus — the one sanctioned write path.
  setCrate: (kind) => { _wbCrate = kind || 'all'; AppState.signal('filters'); },
  crate: () => _wbCrate,
  // P3 duplicates place — sanctioned pass-throughs into the legacy duplicates
  // machinery (scan SSE reader, confirm modal, surgical invalidation). The v2
  // place module drives THESE, never /api/duplicates* directly.
  scanDuplicates: () => scanDuplicates(),
  openDuplicatesConfirm: (opts) => _openDuplicatesConfirm(opts),
  onTracksDeleted: (ids) => _onTracksDeleted(ids),
  // P5 Discover place — minimal delegating accessors into the legacy DiscoverV2
  // IIFE (exposed as window.DiscoverV2). The place re-drives scan / initial-load
  // / detail through THESE; every write (save/dismiss/snooze) still flows through
  // the legacy grid delegation + detail-panel buttons, never re-implemented.
  discoverRunScan: () => window.DiscoverV2?.runScan(),
  discoverLoadInitialState: () => window.DiscoverV2?.loadInitialState(),
  discoverState: () => (window.DiscoverV2 ? window.DiscoverV2.state : null),
  discoverLoadDetail: (id) => window.DiscoverV2?.loadDetail(id),
};
