/* AutoCue app.js — P0 T5 split part 6/8: 06-render.js
 * Classic script (NOT a module): shares globals, loaded in order by
 * docs/index.html. Concatenating 01..08 in order reproduces the original
 * app.js. Do not reorder. See .claude/project/web-ui.md. */
// ── Rendering ──────────────────────────────────────────────────────────────────
function fmtTime(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2,'0')}`;
}

function camelotSortKey(key) {
  if (!key) return 9999;
  const num = parseInt(key, 10) || 0;
  const letter = key.slice(-1).toUpperCase();
  return num * 2 + (letter === 'B' ? 1 : 0);
}

// TASK-034: returns number[] of indices into parsedTracks (NOT track objects).
// Dereference via parsedTracks[i]. Call sites updated to map indices → ids/
// objects at the use site. activeTracks() (below) still returns objects — it
// is the public API used by every write op and must stay stable.
function filteredTracks() {
  // TASK-050 — perf mark around filter recompute (called on every render
  // pass; intentionally cheap when AUTOCUE_PERF is disabled in localStorage).
  try { _perf.mark('filter-start'); } catch (_) {}
  const q = searchQuery ? searchQuery.toLowerCase() : '';
  const cutoffISO = (lastPlayedFilter !== 'all' && lastPlayedFilter !== 'never')
    ? new Date(Date.now() - (lastPlayedFilter === '7d' ? 7 : 30) * 86400000).toISOString()
    : null;
  const out = [];
  for (let i = 0; i < parsedTracks.length; i++) {
    const t = parsedTracks[i];
    if (phraseOnlyFilter && !t.hasPhrase) continue;
    if (beatsOnlyFilter && !t.hasBeats) continue;
    if (_audioOnlyFilter) {
      // Fail-open: tracks whose audio hasn't been probed yet, or whose probe
      // came back "unverified", stay visible. Only "missing" + non-file sources hide.
      if (t.source !== 'file') continue;
      if (_audioProbedAt[t.id] === 'missing') continue;
    }
    if (q && !((t.name || '').toLowerCase().includes(q) ||
               (t.artist || '').toLowerCase().includes(q))) continue;
    if (ratingFilter > 0 && !(t.rating >= ratingFilter)) continue;
    if (playsFilter === 'played' && !(t.playCount > 0)) continue;
    else if (playsFilter === 'unplayed' && !(t.playCount === 0)) continue;
    if (lastPlayedFilter === 'never') {
      if (t.lastPlayed) continue;
    } else if (cutoffISO) {
      if (!(t.lastPlayed && t.lastPlayed >= cutoffISO)) continue;
    }
    if (myTagFilters.size > 0) {
      const tags = t.myTags || [];
      let hit = false;
      for (let k = 0; k < tags.length; k++) { if (myTagFilters.has(tags[k])) { hit = true; break; } }
      if (!hit) continue;
    }
    if (selectedKeys.size > 0 && !(t.key && selectedKeys.has(t.key))) continue;
    if (genreFilters.size > 0 && !genreFilters.has(t.genre || '')) continue;
    out.push(i);
  }
  try { _perf.measure('filter-recompute', 'filter-start'); } catch (_) {}
  return out;
}

// Returns the tracks that write operations (apply/color/delete) should target:
// selected subset when any are checked, otherwise all filtered tracks.
// Public API: returns track OBJECTS (stable contract for every write op).
function activeTracks() {
  const indices = filteredTracks();
  if (selectedTrackIds.size === 0) return indices.map(i => parsedTracks[i]);
  const out = [];
  for (const i of indices) {
    const t = parsedTracks[i];
    if (selectedTrackIds.has(t.id)) out.push(t);
  }
  return out;
}

function updateSelectionBar() {
  const count = selectedTrackIds.size;
  const countEl = document.getElementById('selection-count');
  if (countEl) countEl.textContent = count > 0 ? `${count} selected` : '';
  const deselBtn = document.getElementById('deselect-all-btn');
  if (deselBtn) deselBtn.style.display = count > 0 ? '' : 'none';
  const transBtn = document.getElementById('transition-score-btn');
  if (transBtn) transBtn.style.display = (localMode && count === 2) ? '' : 'none';
  // Bottom action bar — show on any selection
  const bar = document.getElementById('action-bar');
  if (bar) {
    const visible = count > 0;
    bar.classList.toggle('visible', visible);
    bar.setAttribute('aria-hidden', visible ? 'false' : 'true');
    document.body.classList.toggle('has-action-bar', visible);
    const c = document.getElementById('action-bar-count');
    if (c && c.dataset.lastCount !== String(count)) {
      c.dataset.lastCount = String(count);
      c.innerHTML = `<strong>${count.toLocaleString()}</strong> selected`;
      // Re-trigger the existing count-pop tick — this number changes more
      // often than any other in the app and used to swap silently.
      c.classList.remove('count-pop');
      void c.offsetWidth;
      c.classList.add('count-pop');
    }
    // P1 T6: contextual relabel only — the apply button states its target.
    // (P2 replaces this whole bar with the global action dock; do not grow it.)
    const abApply = document.getElementById('action-bar-apply');
    if (abApply && !abApply.disabled) {
      abApply.textContent = visible
        ? `Apply to ${count.toLocaleString()} track${count === 1 ? '' : 's'}`
        : 'Apply to Rekordbox';
    }
  }
}

// Wire the bottom action bar to existing Preview / Apply handlers.
(function _wireActionBar() {
  const bar = document.getElementById('action-bar');
  if (!bar) return;
  document.getElementById('action-bar-preview')?.addEventListener('click', () => {
    document.getElementById('preview-cues-btn')?.click();
  });
  document.getElementById('action-bar-apply')?.addEventListener('click', () => {
    // #download-btn is renamed to "Apply to Rekordbox" in local mode and
    // routes through applyToRekordbox() — re-use that path so the same
    // backup + Rekordbox-running checks fire.
    document.getElementById('download-btn')?.click();
  });
  document.getElementById('action-bar-clear')?.addEventListener('click', () => {
    document.getElementById('deselect-all-btn')?.click();
  });
})();

async function showTransitionScore() {
  const ids = [...selectedTrackIds];
  if (ids.length !== 2) return;
  const [idA, idB] = ids;
  const lookup = {};
  for (const t of parsedTracks) lookup[String(t.id)] = t;
  const ta = lookup[String(idA)];
  const tb = lookup[String(idB)];

  // Create modal
  let modal = document.getElementById('transition-modal');
  if (!modal) {
    modal = document.createElement('div');
    modal.id = 'transition-modal';
    modal.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:999;display:flex;align-items:center;justify-content:center;';
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
    document.body.appendChild(modal);
    // Esc closes — self-removing listener survives backdrop-click closes too
    const _escClose = (e) => {
      if (!document.getElementById('transition-modal')) { document.removeEventListener('keydown', _escClose); return; }
      if (e.key === 'Escape') { modal.remove(); document.removeEventListener('keydown', _escClose); }
    };
    document.addEventListener('keydown', _escClose);
  }
  modal.innerHTML = '<div class="fade-in-up" style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px;min-width:360px;max-width:480px;">' +
    '<div style="font-size:13px;font-weight:600;margin-bottom:12px;">⇌ Transition Score</div>' +
    '<div style="font-size:11px;color:var(--muted);margin-bottom:16px;">' +
    `<strong>${ta ? ta.artist + ' — ' + ta.name : idA}</strong> → <strong>${tb ? tb.artist + ' — ' + tb.name : idB}</strong></div>` +
    '<div id="transition-content" style="font-size:12px;"><span class="btn-spinner"></span>Scoring…</div>' +
    '<div style="margin-top:14px;text-align:right;"><button onclick="document.getElementById(\'transition-modal\').remove()" style="font-size:11px;padding:3px 10px;background:none;border:1px solid var(--border);border-radius:4px;cursor:pointer;color:var(--muted);">Close</button></div>' +
    '</div>';

  try {
    const r = await fetch('/api/transitions/score', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_a_id: parseInt(idA), track_b_id: parseInt(idB) }),
    });
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    const bar = (score) => {
      const pct = Math.round(score);
      const color = pct >= 80 ? 'var(--green)' : pct >= 50 ? '#fa0' : '#f44';
      return `<div style="display:flex;align-items:center;gap:8px;margin:4px 0;">` +
        `<span style="min-width:16px;font-weight:600;color:${color}">${pct}</span>` +
        `<div style="flex:1;background:var(--surface2);border-radius:3px;height:6px;">` +
        // fillBar animates width 0 → --tw, same as the mixability bars
        `<div style="--tw:${pct}%;width:${pct}%;background:${color};height:6px;border-radius:3px;animation:fillBar .45s var(--ease-fill) both;"></div></div></div>`;
    };
    const content = document.getElementById('transition-content');
    const explanationHtml = d.explanation && d.explanation.length
      ? `<div style="margin-top:12px;padding:8px;background:var(--surface2);border-radius:4px;font-size:11px;color:var(--muted);line-height:1.7;">` +
        d.explanation.map(s => `• ${s}`).join('<br>') + `</div>`
      : '';
    if (content) content.innerHTML =
      `<div style="font-size:16px;font-weight:700;margin-bottom:12px;color:var(--green)">` +
      `Overall: ${d.overall}/100</div>` +
      `<div style="margin-bottom:2px;color:var(--muted)">BPM: ${d.bpm_a} → ${d.bpm_b}</div>` + bar(d.bpm) +
      `<div style="margin-bottom:2px;color:var(--muted)">Key: ${d.key_a || '?'} → ${d.key_b || '?'}</div>` + bar(d.key) +
      `<div style="margin-bottom:2px;color:var(--muted)">Energy handoff</div>` + bar(d.energy) +
      explanationHtml;
  } catch {
    const content = document.getElementById('transition-content');
    if (content) content.textContent = 'Error loading transition score.';
  }
}

// Returns track OBJECTS (sorted by current sort key). Public API used by
// renderTracks() and any caller that needs the post-filter-post-sort list.
// Internally derives indices from filteredTracks() (TASK-034) then sorts via
// parsedTracks[idx] dereference.
function sortedTracks() {
  const { by, order } = currentSort;
  const indices = filteredTracks().slice();
  indices.sort((ai, bi) => {
    const a = parsedTracks[ai], b = parsedTracks[bi];
    let av, bv;
    if (by === 'bpm') { av = a.bpm || 0; bv = b.bpm || 0; }
    else if (by === 'artist') { av = (a.artist || '').toLowerCase(); bv = (b.artist || '').toLowerCase(); }
    else if (by === 'album') { av = (a.album || '').toLowerCase(); bv = (b.album || '').toLowerCase(); }
    else if (by === 'key') { av = camelotSortKey(a.key); bv = camelotSortKey(b.key); }
    else if (by === 'rating') { av = a.rating || 0; bv = b.rating || 0; }
    else if (by === 'plays') { av = a.playCount || 0; bv = b.playCount || 0; }
    else { av = (a.name || '').toLowerCase(); bv = (b.name || '').toLowerCase(); }
    if (av < bv) return order === 'asc' ? -1 : 1;
    if (av > bv) return order === 'asc' ? 1 : -1;
    return 0;
  });
  return indices.map(i => parsedTracks[i]);
}

// Build human-readable reasoning for a cue badge.
// cue: { slot, label, name, confidence, phraseMode, phraseBars }
// Returns { confidence: string, reasons: string[] }
function _explainCue(cue) {
  const slot = cue.slot;
  const label = cue.label || '';
  const conf = cue.confidence ?? 1.0;
  const mode = cue.phraseMode || (conf >= 0.9 ? 'phrase' : conf >= 0.5 ? 'bar' : 'heuristic');
  const bars = cue.phraseBars ?? 0;

  // Memory cue
  if (slot === -1) {
    return {
      confidence: 'Auto',
      reasons: [
        'CDJ load point (Auto Cue)',
        'Anchored to earliest phrase boundary',
      ],
    };
  }

  // No confidence data → pre-existing cue not generated by AutoCue
  if (cue.confidence == null && cue.phraseMode == null) {
    return { confidence: '—', reasons: ['Manually placed cue'] };
  }

  const confLabel = conf >= 0.9 ? 'High' : conf >= 0.5 ? 'Medium' : 'Low';
  const reasons = [];

  if (mode === 'heuristic') {
    reasons.push('No BPM or phrase data — 30-second interval estimate');
    reasons.push(`Position: ${cue.name || ''}`);
    return { confidence: confLabel, reasons };
  }

  if (mode === 'bar') {
    if (cue.hasPhrase) {
      reasons.push('Using bar intervals — switch to ✨ Phrase mode to use Rekordbox phrase data');
    } else {
      reasons.push('Bar-interval fallback (no Rekordbox phrase analysis)');
      reasons.push('Run analysis in Rekordbox to enable phrase-based cues');
    }
    reasons.push(`Position: ${cue.name || ''}`);
    return { confidence: confLabel, reasons };
  }

  // Phrase mode
  const LABEL_REASONS = {
    'Drop':   'Rekordbox phrase: Chorus (high-energy section)',
    'Build':  'Rekordbox phrase: Up (energy rise)',
    'Break':  'Rekordbox phrase: Down (low-energy break)',
    'Intro':  'Rekordbox phrase: Intro',
    'Verse':  'Rekordbox phrase: Verse',
    'Bridge': 'Rekordbox phrase: Bridge',
    'Outro':  'Rekordbox phrase: Outro',
    'Fill':   'Rekordbox fill beat marker',
  };

  // Determine base label for lookup (strip trailing number, e.g. "Drop 2" → "Drop")
  const baseName = (cue.name || label).replace(/\s+\d+$/, '');
  const phraseReason = LABEL_REASONS[baseName] || `Rekordbox phrase: ${baseName || label}`;
  reasons.push(phraseReason);

  if (bars > 0) reasons.push(`${bars}-bar phrase`);

  if (slot === 0) reasons.push('Slot A: mix-in point (first non-Intro phrase)');
  else if (baseName === 'Drop' || label === 'Chorus') reasons.push('Priority slot: main drop');
  else if (baseName === 'Build' || label === 'Up')    reasons.push('Priority slot: energy build');
  else if (baseName === 'Outro')                       reasons.push('Priority slot: outro/mix-out');

  return { confidence: confLabel, reasons };
}

async function _toggleSimilarPanel(btn, panel, trackId) {
  if (panel.classList.contains('visible')) {
    _slideClose(panel, 'visible');
    return;
  }
  _slideOpen(panel, 'visible');
  if (panel.dataset.loaded) return;
  panel.innerHTML = '<span class="btn-spinner"></span>Finding similar tracks…';
  try {
    const r = await fetch(`/api/tracks/${trackId}/similar?n=5`);
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    // Cache only successful loads — flagging before the fetch pinned a failed
    // "Error loading" message into the panel forever (reopen never retried).
    panel.dataset.loaded = '1';
    panel.innerHTML = '';
    if (!d.results || d.results.length === 0) {
      panel.textContent = 'No similar tracks found within ±8 BPM.';
      return;
    }
    // Build a lookup of track id → title/artist from parsedTracks
    const lookup = {};
    for (const t of parsedTracks) lookup[String(t.id)] = t;
    const seen = new Set();
    const deduped = d.results.filter(item => {
      const t = lookup[String(item.track_id)];
      if (!t) return true;
      // parsedTracks rows expose the title under `name` (matches the API's
      // TrackItem schema → `title` is mapped during ingest). The earlier
      // `t.title` lookup silently produced `undefined`, every key became
      // `"<artist>|||"`, and every same-artist similar match collapsed
      // into one row. Probe-verified against a 3,775-track library: track
      // 212087170's similar results (5 → 1 row) was the surfacing case.
      const artistStr = (t.artist || '').toLowerCase().trim();
      const titleStr  = (t.name   || '').toLowerCase().trim();
      const key = `${artistStr}|||${titleStr}`;
      if (!artistStr && !titleStr) return true;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    const note = document.createElement('div');
    note.style.cssText = 'font-size:10px;color:var(--muted);margin-bottom:6px;';
    note.textContent = 'Scores key + energy + BPM proximity. Harmonic compatibility is scored separately in the Transition panel.';
    panel.appendChild(note);
    for (const item of deduped) {
      const t = lookup[String(item.track_id)];
      const row = document.createElement('div');
      row.className = 'similar-row';
      const scoreEl = document.createElement('span');
      scoreEl.className = 'similar-score';
      scoreEl.textContent = `${Math.round(item.score * 100)}%`;
      const bpmEl = document.createElement('span');
      bpmEl.className = 'similar-bpm';
      bpmEl.textContent = item.bpm_diff === 0 ? '±0' : `±${item.bpm_diff.toFixed(1)}`;
      const nameEl = document.createElement('span');
      nameEl.textContent = t ? `${t.artist} — ${t.name}` : `Track ${item.track_id}`;
      row.appendChild(scoreEl);
      row.appendChild(bpmEl);
      row.appendChild(nameEl);
      panel.appendChild(row);
    }
  } catch {
    panel.textContent = 'Error loading similar tracks — close and reopen to retry.';
  }
}

async function _renderCategoryChip(chip) {
  const trackId = chip.dataset.trackId;
  if (!trackId) return;
  try {
    const r = await fetch(`/api/tracks/${trackId}/classification`);
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    if (!d.primary || d.primary === 'unknown' || d.confidence < 0.1) {
      chip.remove(); return;
    }
    chip.textContent = d.label;
    chip.className = 'category-chip';
    chip.style.color = d.color;
    chip.style.borderColor = d.color;
    chip.style.background = d.color + '18';
    chip.title = `${d.label} · confidence ${Math.round(d.confidence * 100)}%`;
  } catch {
    chip.remove();
  }
}

var _mixCountRafId = null;
function _animateCount(el, to, prefix, suffix, duration) {
  duration = duration || 600;
  if (_mixCountRafId) { cancelAnimationFrame(_mixCountRafId); _mixCountRafId = null; }
  var start = performance.now();
  function step(ts) {
    if (!document.contains(el)) return;
    var p = Math.min((ts - start) / duration, 1);
    var ease = 1 - Math.pow(1 - p, 3);
    el.textContent = prefix + Math.round(to * ease) + suffix;
    if (p < 1) { _mixCountRafId = requestAnimationFrame(step); }
    else { _mixCountRafId = null; }
  }
  _mixCountRafId = requestAnimationFrame(step);
}

async function _renderMixabilityChip(chip, breakdown) {
  const trackId = chip.dataset.trackId;
  if (!trackId) return;
  try {
    const r = await fetch(`/api/tracks/${trackId}/mixability`);
    if (!r.ok) throw new Error('fetch failed');
    const d = await r.json();
    if (d.score === null || d.score === undefined) {
      chip.textContent = 'No phrase data';
      chip.className = 'mix-score-chip no-data';
      return;
    }
    chip.className = 'mix-score-chip';
    _animateCount(chip, d.score, 'Mix ', '/100');
    const comp = d.components || {};
    const rows = [
      { label: 'Intro', key: 'intro', extra: d.intro_bars > 0 ? `${d.intro_bars} bars` : '' },
      { label: 'Outro', key: 'outro', extra: d.outro_bars > 0 ? `${d.outro_bars} bars` : '' },
      { label: 'Energy', key: 'energy', extra: '' },
      { label: 'Vocals', key: 'vocals', extra: d.vocal_proxy ? 'vocals detected' : 'instrumental' },
      { label: 'Structure', key: 'structure', extra: `${d.phrase_count} phrases` },
    ];
    breakdown.innerHTML = '';
    for (const row of rows) {
      const val = comp[row.key] ?? 0;
      const rowEl = document.createElement('div');
      rowEl.className = 'mix-breakdown-row';
      const lbl = document.createElement('span');
      lbl.className = 'mix-breakdown-label';
      lbl.textContent = row.label;
      const barBg = document.createElement('div');
      barBg.className = 'mix-breakdown-bar-bg';
      const bar = document.createElement('div');
      bar.className = 'mix-breakdown-bar';
      bar.style.setProperty('--tw', `${val}%`);
      barBg.appendChild(bar);
      const valEl = document.createElement('span');
      valEl.className = 'mix-breakdown-val';
      valEl.textContent = row.extra || `${val}%`;
      rowEl.appendChild(lbl);
      rowEl.appendChild(barBg);
      rowEl.appendChild(valEl);
      breakdown.appendChild(rowEl);
    }
    chip.addEventListener('click', () => _slideToggle(breakdown, 'open'));
  } catch {
    chip.textContent = '—';
    chip.className = 'mix-score-chip no-data';
  }
}

async function _renderEnergySparkline(container) {
  const trackId = container.dataset.trackId;
  if (!trackId) return;
  try {
    const r = await fetch(`/api/tracks/${trackId}/energy`);
    if (!r.ok) throw new Error('fetch failed');
    const data = await r.json();
    container.innerHTML = '';
    if (!data.energy || data.energy.length === 0) {
      const nd = document.createElement('span');
      nd.className = 'no-data';
      nd.textContent = 'no waveform';
      container.appendChild(nd);
      return;
    }
    const pts = data.energy;
    _energyCache[trackId] = pts;  // D4: cache for mini waveform
    const w = container.offsetWidth || 200;
    const h = 16;
    const step = w / (pts.length - 1 || 1);
    const coords = pts.map((v, i) => `${(i * step).toFixed(1)},${(h - v * h).toFixed(1)}`).join(' ');
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    svg.setAttribute('preserveAspectRatio', 'none');
    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    poly.setAttribute('points', coords);
    poly.setAttribute('fill', 'none');
    poly.setAttribute('stroke', 'var(--green)');
    poly.setAttribute('stroke-width', '1.5');
    poly.setAttribute('stroke-linecap', 'round');
    poly.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(poly);
    container.appendChild(svg);
    if (data.energy_profile) {
      const profileLabels = { flat: '— flat', build: '↑ build', 'drop-then-flat': '↓ drop', wave: '∿ wave' };
      const lbl = document.createElement('span');
      lbl.style.cssText = 'font-size:9px;color:var(--muted);margin-left:4px;white-space:nowrap;vertical-align:middle;';
      lbl.textContent = profileLabels[data.energy_profile] || data.energy_profile;
      container.appendChild(lbl);
    }
  } catch {
    container.innerHTML = '';
    const nd = document.createElement('span');
    nd.className = 'no-data';
    nd.textContent = '—';
    container.appendChild(nd);
  }
}

// Append the "Phrase structure" strip — a visualization of the track's
// phrase layout (intro / verse / drop / outro …). It describes the TRACK,
// not what AutoCue would write, so it belongs on BOTH the cue-gen path and
// the Skipped path (a skipped track still has phrase structure worth seeing).
// `opts.notes` controls the "no phrase data" / "no ANLZ" informational
// lines — on for the regular path, OFF for skipped cards to keep them
// within the fixed 160px card height (TASK-033). With lazy phrase loading
// the strip appears once the viewport fetch populates phraseCueState and
// _updateTrackCardCues rebuilds the card.
function _appendPhraseStrip(cardMain, track, opts) {
  opts = opts || {};
  if (analysisMode !== 'phrase') return false;
  if (phraseCueState[track.id]?.length && track.totalTime > 0) {
    // `compact` drops the "Phrase structure" caption — used on Skipped cards
    // where the #163 existing-cue chips already consume most of the fixed
    // 160px (TASK-033), so the ~24px caption+margins would push the strip
    // out of the visible box. The coloured segments are self-describing.
    if (!opts.compact) {
      const stripLabel = document.createElement('div');
      stripLabel.style.cssText = 'font-size:10px;color:var(--muted);margin-top:6px;margin-bottom:2px;text-transform:uppercase;letter-spacing:.05em;';
      stripLabel.textContent = 'Phrase structure';
      cardMain.appendChild(stripLabel);
    }
    // `cueTicks` overlays existing hot-cue positions on the strip (Skipped
    // cards merge the #163 chips here so both structure + cues fit 160px).
    const strip = buildPhraseStrip(phraseCueState[track.id], track.totalTime, opts.cueTicks);
    if (strip) {
      if (opts.compact) strip.style.marginTop = '4px';
      cardMain.appendChild(strip);
      return true;
    }
    return false;
  } else if (opts.notes && !localMode) {
    const note = document.createElement('p');
    note.style.cssText = 'font-size:12px;color:var(--muted);margin-top:4px;';
    note.textContent = '⬡ No ANLZ data — drop the analysis folder above to enable phrase analysis';
    cardMain.appendChild(note);
  } else if (opts.notes && !track.hasPhrase) {
    const note = document.createElement('p');
    note.style.cssText = 'font-size:12px;color:var(--muted);margin-top:4px;';
    note.textContent = '⬡ No phrase data — track has not been analyzed by Rekordbox';
    cardMain.appendChild(note);
  }
  return false;
}

// Append the per-track "intelligence" widgets (sparkline, mix score chip,
// classification chip, similar-tracks button + panels) to `cardMain`. No-op
// when not in localMode (the data behind these widgets is local-server
// only). Shared between the cue-gen card path and the Skipped card path —
// both describe the same underlying track, so both should surface the same
// per-track intelligence.
function _appendIntelligenceWidgets(cardMain, track) {
  if (!localMode) return;
  const sparkContainer = document.createElement('div');
  sparkContainer.className = 'energy-sparkline';
  sparkContainer.dataset.trackId = track.id;
  const loading = document.createElement('span');
  loading.className = 'loading';
  loading.textContent = '▁▂▃▄';
  sparkContainer.appendChild(loading);
  cardMain.appendChild(sparkContainer);

  // Mixability score chip — loaded lazily alongside sparkline
  const mixRow = document.createElement('div');
  mixRow.className = 'mix-score-row';
  const mixChip = document.createElement('span');
  mixChip.className = 'mix-score-chip loading';
  mixChip.textContent = '…';
  mixChip.dataset.trackId = track.id;
  mixRow.appendChild(mixChip);

  const catChip = document.createElement('span');
  catChip.className = 'category-chip loading';
  catChip.textContent = '·';
  catChip.dataset.trackId = track.id;
  mixRow.appendChild(catChip);
  catChip._isCategoryChip = true;

  const simBtn = document.createElement('button');
  simBtn.className = 'similar-btn';
  simBtn.textContent = '≈ Similar';
  simBtn.dataset.trackId = track.id;
  mixRow.appendChild(simBtn);

  const mixBreakdown = document.createElement('div');
  mixBreakdown.className = 'mix-breakdown';
  const simPanel = document.createElement('div');
  simPanel.className = 'similar-panel';
  cardMain.appendChild(mixRow);
  cardMain.appendChild(mixBreakdown);
  cardMain.appendChild(simPanel);
  // Store ref on chip for observer callback
  mixChip._breakdown = mixBreakdown;

  simBtn.addEventListener('click', () => _toggleSimilarPanel(simBtn, simPanel, track.id));
}

function buildTrackCard(track, cues, willSkip, opts = {}) {
  const { hideAlbum = false } = opts;
  const hasAudio = !!audioState[track.id];
  const artUrl = audioState[track.id]?.artworkUrl
    || (localMode ? `/api/tracks/${track.id}/artwork` : null);

  const card = document.createElement('div');
  card.className = 'track-card';
  card.dataset.testid = 'track-card';
  card.dataset.trackId = track.id;
  if (track.colorName) card.dataset.color = track.colorName;
  if (nowPlayingId === track.id && !audioPlayer.paused) card.classList.add('now-playing');

  const cardTop = document.createElement('div');
  cardTop.className = 'card-top';

  // Bulk-select checkbox (local mode only)
  if (localMode) {
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.className = 'track-select-cb';
    cb.checked = selectedTrackIds.has(track.id);
    if (selectedTrackIds.has(track.id)) card.classList.add('selected');
    cb.addEventListener('change', e => {
      e.stopPropagation();
      if (e.target.checked) { selectedTrackIds.add(track.id); card.classList.add('selected'); }
      else { selectedTrackIds.delete(track.id); card.classList.remove('selected'); }
      updateSelectionBar();
    });
    cardTop.appendChild(cb);
    // Card body toggles selection — the card already advertises cursor:pointer
    // but only the 15px checkbox used to respond. Inner interactive elements
    // (buttons, badges, panels, seek surfaces) keep their own behaviour.
    card.addEventListener('click', e => {
      if (e.target.closest(
        'button, a, input, select, textarea, svg, canvas, .cue-badge, .cue-slots, ' +
        '.tag-pill, .timeline, .phrase-strip, .similar-panel, .cue-reason-panel, .art-play-overlay'
      )) return;
      const sel = window.getSelection && window.getSelection();
      if (sel && String(sel).length) return; // don't hijack text selection
      cb.checked = !cb.checked;
      cb.dispatchEvent(new Event('change'));
    });
  }

  // Artwork
  const artWrap = document.createElement('div');
  artWrap.className = 'artwork-wrap';
  artWrap.style.position = 'relative';
  const ph = document.createElement('div');
  ph.className = 'artwork-placeholder';
  ph.textContent = '♪';
  artWrap.appendChild(ph);
  if (artUrl) {
    const img = document.createElement('img');
    img.className = 'artwork-img';
    img.loading = 'lazy';
    img.src = artUrl;
    img.onload = () => ph.remove();
    img.onerror = () => img.remove();
    artWrap.appendChild(img);
  }
  // Art play overlay (always present; in local mode triggers ensureLocalAudio)
  const artOverlay = document.createElement('button');
  artOverlay.className = 'art-play-overlay' + (nowPlayingId === track.id && !audioPlayer.paused ? ' playing' : '');
  artOverlay.setAttribute('aria-label', 'Play');
  artOverlay.innerHTML = (nowPlayingId === track.id && !audioPlayer.paused) ? SVG_PAUSE : SVG_PLAY;
  artOverlay.addEventListener('click', e => {
    e.stopPropagation();
    if (localMode && !audioState[track.id]) {
      ensureLocalAudio(track).then(() => togglePlayTrack(track.id));
    } else {
      togglePlayTrack(track.id);
    }
  });
  artWrap.appendChild(artOverlay);
  cardTop.appendChild(artWrap);

  // Card main
  const cardMain = document.createElement('div');
  cardMain.className = 'card-main';

  // Track meta row: title + BPM + duration + play btn + load btn
  const meta = document.createElement('div');
  meta.className = 'track-meta';

  const nameEl = document.createElement('span');
  nameEl.className = 'track-name';
  // Streaming-source tracks (Spotify / Tidal / Apple Music links) frequently
  // import into Rekordbox with empty Title / ArtistName / AlbumName columns
  // — the API echoes those through as empty strings and the card otherwise
  // renders as a visually blank row. Show a clear "untitled" placeholder so
  // the user spots which rows need a Rekordbox-side metadata fix instead of
  // wondering why some cards look broken.
  if (!track.name && track.source === 'streaming') {
    nameEl.textContent = '— Untitled streaming track —';
    nameEl.classList.add('untitled');
    nameEl.title = `Track ${track.id} — no title in Rekordbox; streaming source`;
  } else {
    nameEl.title = track.name;
    nameEl.textContent = track.name;
  }
  meta.appendChild(nameEl);

  if (localMode && healthData[String(track.id)]) {
    const h = healthData[String(track.id)];
    const chip = document.createElement('span');
    chip.className = 'health-chip ' + (h.score >= 90 ? 'hc-good' : h.score >= 70 ? 'hc-ok' : 'hc-bad');
    chip.title = `Health: ${h.score}/100\n${(h.issues || []).map(i => i.message || i.code).join('\n')}`;
    chip.textContent = h.score;
    meta.appendChild(chip);
  }

  if (track.colorName) {
    const dot = document.createElement('span');
    dot.className = 'color-dot';
    dot.dataset.color = track.colorName;
    dot.title = track.colorName;
    meta.appendChild(dot);
  }

  if (track.bpm) {
    const b = document.createElement('span');
    b.className = 'track-bpm';
    b.textContent = track.bpm.toFixed(2) + ' BPM';
    meta.appendChild(b);
  }
  if (track.key) {
    const k = document.createElement('span');
    k.className = 'track-key';
    k.textContent = track.key;
    meta.appendChild(k);
  }
  if (track.totalTime) {
    const t = document.createElement('span');
    t.className = 'track-time';
    t.textContent = fmtTime(track.totalTime);
    meta.appendChild(t);
  }

  const playBtn = document.createElement('button');
  playBtn.className = 'play-btn' + (hasAudio ? '' : ' hidden');
  playBtn.innerHTML = (nowPlayingId === track.id && !audioPlayer.paused) ? SVG_PAUSE : SVG_PLAY;
  playBtn.setAttribute('aria-label', 'Play');
  playBtn.addEventListener('click', e => { e.stopPropagation(); togglePlayTrack(track.id); });
  meta.appendChild(playBtn);

  // B3: orphans (streaming or known-missing) get a "No audio" chip that opens
  // the info modal instead of the Load audio button — kills the failed-fetch
  // toast pileup that triggered the user's image #7 sample.
  const isOrphan = (track.source && track.source !== 'file') || _audioProbedAt[track.id] === 'missing';
  if (isOrphan) {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'load-audio-btn';
    chip.textContent = 'No audio ⓘ';
    chip.title = 'This track has no playable audio file. Click to see details / rescue via YouTube.';
    chip.addEventListener('click', e => { e.stopPropagation(); openTrackInfoModal(track.id); });
    meta.appendChild(chip);
  } else {
    const loadBtn = document.createElement('label');
    loadBtn.className = 'load-audio-btn' + (hasAudio ? ' hidden' : '');
    loadBtn.textContent = 'Load audio';
    const loadInput = document.createElement('input');
    loadInput.type = 'file';
    loadInput.accept = 'audio/*';
    loadInput.addEventListener('change', () => {
      if (loadInput.files[0]) registerAudioFile(track, loadInput.files[0]);
    });
    loadBtn.appendChild(loadInput);
    meta.appendChild(loadBtn);
  }

  cardMain.appendChild(meta);

  // Artist / album sub-row (hidden in album-group view where album is shown in header)
  const showArtist = track.artist;
  const showAlbumName = !hideAlbum && track.album;
  // Same streaming-empty-metadata case as above: when BOTH name and artist
  // are missing, surface a clear artist placeholder so the row makes sense.
  // (We don't placeholder when ONLY the artist is missing — 187 tracks in a
  // typical library are classical / various-artists with empty artist but
  // valid title; those should keep rendering naturally.)
  const showArtistPlaceholder =
    !track.name && !track.artist && track.source === 'streaming';
  if (showArtist || showAlbumName || showArtistPlaceholder) {
    const sub = document.createElement('div');
    sub.style.cssText = 'display:flex;gap:6px;align-items:baseline;flex-wrap:wrap;margin-top:2px;margin-bottom:4px;';
    if (showArtist) {
      const a = document.createElement('span');
      a.className = 'track-artist';
      a.textContent = track.artist;
      sub.appendChild(a);
    } else if (showArtistPlaceholder) {
      const a = document.createElement('span');
      a.className = 'track-artist untitled';
      a.textContent = 'No artist metadata';
      sub.appendChild(a);
    }
    if (showAlbumName) {
      const al = document.createElement('span');
      al.className = 'track-album';
      al.textContent = '· ' + track.album;
      sub.appendChild(al);
    }
    cardMain.appendChild(sub);
  }

  // Rating / play-count / last-played / My Tags (local mode data)
  if (localMode && (track.rating || track.playCount || track.lastPlayed || (track.myTags && track.myTags.length))) {
    const infoRow = document.createElement('div');
    infoRow.style.cssText = 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:4px;';
    if (track.rating > 0) {
      const rEl = document.createElement('span');
      rEl.className = 'track-rating';
      rEl.title = `Rating: ${track.rating}/5`;
      rEl.textContent = '★'.repeat(track.rating) + '☆'.repeat(5 - track.rating);
      infoRow.appendChild(rEl);
    }
    if (track.playCount > 0) {
      const pEl = document.createElement('span');
      pEl.className = 'track-plays';
      pEl.textContent = `${track.playCount} play${track.playCount !== 1 ? 's' : ''}`;
      infoRow.appendChild(pEl);
    }
    if (track.lastPlayed) {
      const lpEl = document.createElement('span');
      lpEl.className = 'track-plays';
      lpEl.title = track.lastPlayed;
      const d = new Date(track.lastPlayed);
      lpEl.textContent = `Last: ${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}`;
      infoRow.appendChild(lpEl);
    }
    for (const tag of (track.myTags || [])) {
      const tp = document.createElement('span');
      tp.className = 'tag-pill';
      tp.textContent = tag;
      tp.title = `Filter by "${tag}"`;
      tp.style.cursor = 'pointer';
      const catColor = (typeof AUTO_TAG_COLORS !== 'undefined') && AUTO_TAG_COLORS[tag];
      if (catColor) {
        tp.style.cssText = `background:${catColor}22;border-color:${catColor}55;color:${catColor};cursor:pointer;`;
      }
      tp.addEventListener('click', e => {
        e.stopPropagation();
        if (typeof window._toggleTagFilter === 'function') window._toggleTagFilter(tag, true);
      });
      infoRow.appendChild(tp);
    }
    cardMain.appendChild(infoRow);
  }

  if (willSkip) {
    const skipped = document.createElement('span');
    skipped.className = 'skipped-badge';
    skipped.textContent = `Skipped — has ${track.existingHotCues} existing hot cue${track.existingHotCues !== 1 ? 's' : ''}`;
    cardMain.appendChild(skipped);

    // Phrase structure strip with the existing hot-cue positions overlaid as
    // ticks (the chosen "merge" layout). The strip describes the track and the
    // ticks show WHERE its existing cues sit — both in one 16px row, so both
    // fit the fixed 160px card (TASK-033). `compact` drops the caption;
    // `notes:false` skips the "no data" lines. Auto-cue badges stay hidden —
    // those ARE the skipped generation outcome. With lazy phrase loading the
    // strip appears once the viewport fetch lands and _updateTrackCardCues
    // rebuilds the card.
    const stripRendered = _appendPhraseStrip(cardMain, track, {
      notes: false, compact: true, cueTicks: track.existingCueDetails,
    });

    // Fallback: when there's NO phrase data (no strip), keep the #163 chip row
    // so the existing cues are still shown — names, slot letters, positions.
    // Cap at SKIPPED_CHIP_LIMIT and add a "+N more" indicator so the row stays
    // single-line inside the Virtualizer's fixed 160px card height (TASK-033).
    if (!stripRendered && track.existingCueDetails && track.existingCueDetails.length > 0) {
      const SKIPPED_CHIP_LIMIT = 9;  // 8 hot cues (A-H) + memory cue
      const chipsRow = document.createElement('div');
      chipsRow.className = 'existing-cues-row';
      const sortedExisting = [...track.existingCueDetails].sort((a, b) => {
        if (a.num === -1) return -1;
        if (b.num === -1) return 1;
        return a.num - b.num;
      });
      const visible = sortedExisting.slice(0, SKIPPED_CHIP_LIMIT);
      const overflowCount = sortedExisting.length - visible.length;
      for (const ec of visible) {
        const chip = document.createElement('span');
        chip.className = 'existing-cue-chip skipped-card-chip';
        const slotLetter = ec.num === -1 ? 'Mem' : (ec.num >= 0 && ec.num <= 7 ? String.fromCharCode(65 + ec.num) : '?');
        const mins = Math.floor((ec.start || 0) / 60);
        const secs = Math.floor((ec.start || 0) % 60);
        chip.textContent = `${slotLetter} ${ec.name || ''} ${mins}:${String(secs).padStart(2,'0')}`.replace(/\s+/g, ' ').trim();
        if (ec.colorName) chip.dataset.color = ec.colorName;
        chipsRow.appendChild(chip);
      }
      if (overflowCount > 0) {
        const ov = document.createElement('span');
        ov.className = 'existing-cues-overflow';
        ov.textContent = `+${overflowCount} more`;
        ov.title = `${overflowCount} more existing cue${overflowCount !== 1 ? 's' : ''} not shown`;
        chipsRow.appendChild(ov);
      }
      cardMain.appendChild(chipsRow);
    }

    // Restore the per-track intelligence widgets on Skipped cards (PR #163
    // regression): sparkline, mix-score chip, classification chip, similar
    // button. These describe the track itself and apply regardless of
    // whether AutoCue would write new cues.
    _appendIntelligenceWidgets(cardMain, track);

    cardTop.appendChild(cardMain);
    card.appendChild(cardTop);
    return card;
  }

  if (!track.tempo && !track.bpm) {
    const w = document.createElement('p');
    w.className = 'warn';
    w.textContent = '⚠ No beat-grid data — open this track in Rekordbox, run BPM analysis, then re-export your XML';
    cardMain.appendChild(w);
    cardTop.appendChild(cardMain);
    card.appendChild(cardTop);
    return card;
  }

  // Cue badges — sorted by slot (A→H, memory cue first) for consistent display
  const badges = document.createElement('div');
  badges.className = 'cue-slots';
  const sortedCues = [...cues].sort((a, b) => {
    if (a.slot === -1) return -1;
    if (b.slot === -1) return 1;
    return a.slot - b.slot;
  });
  for (const cue of sortedCues) {
    const b = document.createElement('span');
    b.className = 'cue-badge' + (hasAudio ? ' playable' : '');
    b.dataset.slot   = cue.slot;
    b.dataset.posSec = cue.posSec;
    const conf = cue.confidence ?? 1.0;
    if (conf < 0.4)      b.dataset.confidence = 'heuristic';
    else if (conf < 0.9) b.dataset.confidence = 'bar';
    // phrase cues (conf=1.0) get no data-confidence → full opacity
    const displayName = cue.name || cue.label || '';
    const slotLabel = cue.slot === -1 ? 'Mem' : (SLOT_NAMES[cue.slot] ?? '?');
    b.textContent = `${slotLabel} ${displayName} ${fmtTime(cue.posSec)}`.trim();
    if (hasAudio) b.addEventListener('click', () => {
      seekAndPlay(track.id, cue.posSec);
      b.classList.remove('seek-flash');
      void b.offsetWidth;
      b.classList.add('seek-flash');
      b.addEventListener('animationend', () => b.classList.remove('seek-flash'), { once: true });
    });

    // ℹ Cue Reasoning button
    const infoBtn = document.createElement('button');
    infoBtn.className = 'cue-reason-btn';
    infoBtn.title = 'Cue reasoning';
    infoBtn.textContent = 'ℹ';
    const reasonPanel = document.createElement('div');
    reasonPanel.className = 'cue-reason-panel';
    infoBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isVisible = reasonPanel.classList.contains('visible');
      // Close all other open panels in this card
      badges.querySelectorAll('.cue-reason-panel.visible').forEach(p => _slideClose(p, 'visible'));
      if (!isVisible) {
        const { confidence: cl, reasons } = _explainCue(cue);
        const header = document.createElement('div');
        header.style.cssText = 'font-weight:700;margin-bottom:4px;';
        header.textContent = `Cue Reasoning — ${cl} confidence`;
        reasonPanel.innerHTML = '';
        reasonPanel.appendChild(header);
        const ul = document.createElement('ul');
        reasons.forEach(r => { const li = document.createElement('li'); li.textContent = r; ul.appendChild(li); });
        reasonPanel.appendChild(ul);
        _slideOpen(reasonPanel, 'visible');
      }
    });
    b.appendChild(infoBtn);
    badges.appendChild(b);
    badges.appendChild(reasonPanel);
  }
  if (cues.length === 0) {
    const b = document.createElement('span');
    b.style.cssText = 'font-size:12px;color:var(--muted)';
    b.textContent = 'No cues fit within track length';
    badges.appendChild(b);
  }
  cardMain.appendChild(badges);

  if (track.existingCueDetails && track.existingCueDetails.length > 0) {
    const { maxCues: mc } = getSettings();
    const usedSlots = new Set(Array.from({length: mc}, (_, i) => i));
    const chipsRow = document.createElement('div');
    chipsRow.style.cssText = 'display:flex;gap:4px;flex-wrap:wrap;margin-top:5px;';
    for (const ec of track.existingCueDetails) {
      const chip = document.createElement('span');
      chip.className = 'existing-cue-chip' + (usedSlots.has(ec.num) ? ' replaced' : '');
      const slotLetter = ec.num >= 0 && ec.num <= 7 ? String.fromCharCode(65 + ec.num) : '?';
      const mins = Math.floor(ec.start / 60), secs = Math.floor(ec.start % 60);
      const icon = usedSlots.has(ec.num) ? '⚠' : '✓';
      chip.textContent = `${icon} ${slotLetter}: ${mins}:${String(secs).padStart(2,'0')}${ec.name ? ' ' + ec.name : ''}`;
      chip.title = usedSlots.has(ec.num) ? 'Will be replaced by AutoCue' : 'Will be preserved';
      chipsRow.appendChild(chip);
    }
    cardMain.appendChild(chipsRow);
  }

  _appendPhraseStrip(cardMain, track, { notes: true });

  // Energy sparkline + mixability + classification + similar tracks — the
  // "intelligence" widgets that describe the TRACK itself (not what AutoCue
  // would write). Shared between the regular cue-gen path AND the Skipped
  // path (PR for #163 regression — Skipped cards previously dropped these).
  _appendIntelligenceWidgets(cardMain, track);

  if (track.totalTime > 0 && cues.length > 0) {
    const tl = document.createElement('div');
    tl.className = 'timeline';
    for (const cue of cues) {
      const pct = (cue.posSec / track.totalTime) * 100;
      const m = document.createElement('div');
      m.className = 'timeline-marker';
      m.style.left = `${pct}%`;
      const c = pickCueColor(cue);
      m.style.background = `rgb(${c.r},${c.g},${c.b})`;
      m.style.color = `rgb(${c.r},${c.g},${c.b})`;
      tl.appendChild(m);
    }
    if (nowPlayingId === track.id) {
      const playhead = document.createElement('div');
      playhead.className = 'timeline-playhead';
      playhead.style.left = `${track.totalTime ? (audioPlayer.currentTime / track.totalTime) * 100 : 0}%`;
      tl.appendChild(playhead);
    }
    cardMain.appendChild(tl);
  }

  // F5: Pending cue preview bar (shown after Preview Cues, cleared after Apply)
  const pending = pendingCues[String(track.id)];
  if (pending && pending.length > 0 && track.totalTime > 0) {
    const label = document.createElement('div');
    label.style.cssText = 'font-size:10px;color:var(--muted);margin-top:6px;margin-bottom:2px;text-transform:uppercase;letter-spacing:.05em;';
    label.textContent = 'Preview (pending apply)';
    cardMain.appendChild(label);
    const ptl = document.createElement('div');
    ptl.className = 'timeline';
    ptl.style.opacity = '0.75';
    for (const cue of pending) {
      const pct = (cue.posSec / track.totalTime) * 100;
      const m = document.createElement('div');
      m.className = 'timeline-marker';
      m.style.left = `${pct}%`;
      // Memory cues (slot=-1) get white; hot cues use slot color
      const c = cue.slot === -1 ? { r: 220, g: 220, b: 220 } : pickCueColor(cue);
      m.style.background = `rgb(${c.r},${c.g},${c.b})`;
      m.style.color = `rgb(${c.r},${c.g},${c.b})`;
      ptl.appendChild(m);
    }
    cardMain.appendChild(ptl);
  }

  cardTop.appendChild(cardMain);
  card.appendChild(cardTop);
  return card;
}

function _computeSettingsFingerprint() {
  var s = getSettings();
  var skipExisting = document.getElementById('skip-existing-cues').checked;
  var mcMode = document.getElementById('memory-cue-mode').value;
  // NOTE: phraseCueState size deliberately NOT in the fingerprint. Surgical
  // per-card updates via _updateTrackCardCues handle phrase-cue arrivals;
  // including phraseTotal here caused the per-batch storm fixed in feat/phrase-storm-orphans.
  console.assert(parsedTracksById.size === parsedTracks.length, 'parsedTracksById drift');
  return s.barsInterval + '|' + s.startBar + '|' + s.maxCues + '|' + skipExisting + '|' + mcMode + '|' + analysisMode + '|' + Object.keys(pendingCues).length + '|' + Object.keys(healthData).length;
}

// Surgical per-card update — used by loadPhraseFromServer to refresh ONE card
// without rebuilding the library. The card must already be mounted (visible);
// off-screen / filtered-out tracks pick up new cues on their next natural
// render via the standard renderTracks() path (which reads phraseCueState).
function _updateTrackCardCues(trackId) {
  const tid = String(trackId);
  const track = parsedTracksById.get(tid);
  if (!track) return;
  const skipExisting = document.getElementById('skip-existing-cues')?.checked;
  const willSkip = !!(skipExisting && track.existingHotCues > 0);
  // Replay the cue computation from renderTracks's inner computeCues — limited to
  // the phrase-mode branch since this entry point only fires while phrase cues land.
  // Skipped cards get NO auto-cue badges (cues stays []) — they only rebuild to
  // surface the phrase structure strip, which buildTrackCard reads straight
  // from phraseCueState in its willSkip branch.
  let cues = [];
  if (!willSkip && analysisMode === 'phrase' && phraseCueState[track.id]?.length) {
    cues = phraseCueState[track.id].map(c => ({
      slot: c.slot, posSec: c.position_ms / 1000,
      label: c.label, isPhrase: true, name: c.name || '',
      confidence: c.confidence ?? 1.0, phraseMode: 'phrase',
      phraseBars: c.phrase_bars ?? 0,
    }));
  }
  // Nothing new to show: non-skipped card with no cues yet, OR skipped card
  // whose phrase data hasn't loaded (the strip would be empty).
  const hasPhrase = !!(phraseCueState[track.id]?.length);
  if (willSkip ? !hasPhrase : !cues.length) return;

  // Lazily-landed phrase data swaps the card under the reader's eyes — fade
  // the new strip/badges in (transform+opacity only: card height untouched,
  // virtualizer fixed-height invariant safe).
  const _fadeFreshCueUI = (card) => {
    if (_prefersReducedMotion) return card;
    card.querySelectorAll('.phrase-strip, .cue-slots').forEach(el => el.classList.add('fade-in-up'));
    return card;
  };

  // Album mode (or any non-virtualized render): patch via _cardMap.
  const albumCard = _cardMap.get(tid);
  if (albumCard && albumCard.parentNode) {
    const newCard = _fadeFreshCueUI(buildTrackCard(track, cues, willSkip, {}));
    albumCard.parentNode.replaceChild(newCard, albumCard);
    _cardMap.set(tid, newCard);
    return;
  }

  // Flat-list (virtualized): find the live node by track-id in the visible
  // index. Off-screen tracks pick up cues on their next natural render.
  if (Virtualizer.isAttached()) {
    const visMap = Virtualizer._visibleNodes();
    let targetIdx = null, targetNode = null;
    visMap.forEach(function(node, idx) {
      if (targetNode === null && node.dataset.trackId === tid) {
        targetNode = node; targetIdx = idx;
      }
    });
    if (targetNode && targetNode.parentNode) {
      const baseTransform = targetNode.style.transform || '';
      const newCard = _fadeFreshCueUI(buildTrackCard(track, cues, willSkip, {}));
      newCard.style.position = 'absolute';
      newCard.style.left = '0';
      newCard.style.right = '0';
      newCard.style.top = '0';
      newCard.style.transform = baseTransform;
      targetNode.parentNode.replaceChild(newCard, targetNode);
      visMap.set(targetIdx, newCard);
    }
  }
}

function renderTracks() {
  const { barsInterval, startBar, maxCues } = getSettings();
  const skipExisting = document.getElementById('skip-existing-cues').checked;
  const list = document.getElementById('track-list');
  if (!parsedTracks.length) {
    if (Virtualizer.isAttached()) Virtualizer.detach();
    list.classList.remove('virtualized');
    list.innerHTML = '';
    _cardMap.clear(); _albumGroupCache.clear(); _cardSettingsFingerprint = '';
    const empty = document.createElement('div');
    empty.className = 'empty-state fade-in-up'; // ease in — the list blanks first
    const icon = document.createElement('div');
    icon.className = 'empty-state-icon';
    icon.textContent = '♪';
    const title = document.createElement('div');
    title.className = 'empty-state-title';
    title.textContent = activePlaylistId != null ? 'No tracks in this playlist' : 'No library loaded';
    const sub = document.createElement('div');
    sub.className = 'empty-state-sub';
    sub.textContent = activePlaylistId != null
      ? 'Switch to a different playlist or load your Rekordbox library.'
      : 'Start the local server and reload, or drop a Rekordbox XML file above.';
    empty.appendChild(icon);
    empty.appendChild(title);
    empty.appendChild(sub);
    list.appendChild(empty);
    return;
  }
  let totalCues = 0, tracksWithCues = 0;

  function computeCues(track) {
    if (skipExisting && track.existingHotCues > 0) return [];
    let cues;
    if (analysisMode === 'phrase' && phraseCueState[track.id]?.length) {
      cues = phraseCueState[track.id].map(c => ({
        slot: c.slot, posSec: c.position_ms / 1000,
        label: c.label, isPhrase: true, name: c.name || '',
        confidence: c.confidence ?? 1.0, phraseMode: 'phrase',
        phraseBars: c.phrase_bars ?? 0,
      }));
    } else {
      cues = generateCues(track, barsInterval, startBar, maxCues).map(c => ({
        ...c,
        hasPhrase: !!(track.has_phrase),
      }));
    }
    const mcMode = document.getElementById('memory-cue-mode').value;
    if (mcMode !== 'none' && cues.length) {
      const hotCues = cues.filter(c => c.slot !== -1);
      const loadPos = analysisMode === 'phrase' && hotCues.length
        ? Math.min(...hotCues.map(c => c.posSec))
        : 0;
      const memCues = [{ slot: -1, posSec: loadPos, label: '', name: 'Load Point', color_id: 0 }];
      if (mcMode === 'all' && analysisMode === 'phrase') {
        // Mix-In: slot-0 hot cue (the mix-in point)
        const mixIn = hotCues.find(c => c.slot === 0);
        if (mixIn && Math.abs(mixIn.posSec - loadPos) > 0.5) {
          memCues.push({ slot: -1, posSec: mixIn.posSec, label: '', name: 'Mix In', color_id: 5 });
        }
        // Mix-Out: last OUTRO cue
        const outros = hotCues.filter(c => c.label === 'Outro');
        if (outros.length) {
          const outroPos = Math.max(...outros.map(c => c.posSec));
          memCues.push({ slot: -1, posSec: outroPos, label: '', name: 'Mix Out', color_id: 3 });
        }
      }
      memCues.sort((a, b) => a.posSec - b.posSec);
      cues = [...memCues, ...cues];
    }
    return cues;
  }

  const sorted = sortedTracks();

  if (!sorted.length) {
    if (Virtualizer.isAttached()) Virtualizer.detach();
    list.classList.remove('virtualized');
    list.innerHTML = '';
    _cardMap.clear(); _albumGroupCache.clear(); _cardSettingsFingerprint = '';
    const empty = document.createElement('div');
    empty.className = 'empty-state fade-in-up'; // ease in when a keystroke crosses the 0-results boundary
    const icon = document.createElement('div');
    icon.className = 'empty-state-icon';
    icon.textContent = '⊘';
    const title = document.createElement('div');
    title.className = 'empty-state-title';
    title.textContent = 'No tracks match';
    const sub = document.createElement('div');
    sub.className = 'empty-state-sub';
    sub.textContent = 'Try adjusting your search or clearing the active filters.';
    empty.appendChild(icon);
    empty.appendChild(title);
    empty.appendChild(sub);
    list.appendChild(empty);
    return;
  }

  if (currentSort.by === 'album') {
    // Album mode is variable-height (album header chrome) — not virtualizable.
    // Drop the virtualizer if we just switched from flat mode.
    if (Virtualizer.isAttached()) Virtualizer.detach();
    list.classList.remove('virtualized');
    const newFingerprint = _computeSettingsFingerprint();
    const newSortKey = sorted.map(t => t.id).join(',');
    const settingsChanged = newFingerprint !== _cardSettingsFingerprint;
    const orderChanged = newSortKey !== _albumSortKey;

    if (settingsChanged) {
      _cardSettingsFingerprint = newFingerprint;
      _cardMap.clear();
      // #172: settings (e.g. analysis mode, max cues) invalidate the cached
      // header DOM via the track cards they wrap, so drop the album-group
      // cache here too. Filter-only changes (which do NOT bump the
      // fingerprint) keep the cache hot.
      _albumGroupCache.clear();
    }
    _albumSortKey = newSortKey;

    // Group consecutive tracks by album name
    const groups = new Map();
    for (const track of sorted) {
      const key = track.album || '';
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(track);
    }

    // Only rebuild DOM when something actually changed
    if (settingsChanged || orderChanged || !list.firstChild) {
      list.innerHTML = '';
      // Track which cache entries we still need so we can evict stale ones.
      const usedCacheKeys = new Set();
      for (const [albumName, tracks] of groups) {
        // Count cues for this group
        for (const track of tracks) {
          const cues = computeCues(track);
          if (cues.length) { tracksWithCues++; totalCues += cues.length; }
        }

        // #172: cache key = album name + member track ids in order. If a
        // filter change leaves an album fully intact, we reuse the prior
        // <div.album-group> verbatim — header text, artwork chain, and
        // mounted track cards stay put.
        const cacheKey = albumName + '|' + tracks.map(t => t.id).join(',');
        usedCacheKeys.add(cacheKey);
        let group = _albumGroupCache.get(cacheKey);
        if (group) {
          list.appendChild(group);
          continue;
        }

        group = document.createElement('div');
        group.className = 'album-group';

        // Album header
        const header = document.createElement('div');
        const isOpen = expandedAlbums.has(albumName);
        header.className = 'album-header' + (isOpen ? ' open' : '');

        // Album art (from first track that has one)
        const artBox = document.createElement('div');
        artBox.className = 'album-art-lg';
        if (localMode && tracks.length) {
          const ph = document.createElement('span');
          ph.textContent = '♪';
          artBox.appendChild(ph);
          let artIdx = 0;
          function tryNextArt() {
            if (artIdx >= tracks.length) return;
            const img = document.createElement('img');
            img.src = `/api/tracks/${tracks[artIdx++].id}/artwork`;
            img.onload = () => { ph.remove(); artBox.appendChild(img); };
            img.onerror = tryNextArt;
          }
          tryNextArt();
        }
        header.appendChild(artBox);

        // Info: album name + artist
        const info = document.createElement('div');
        info.className = 'album-header-info';
        const nameDiv = document.createElement('div');
        nameDiv.className = 'album-header-name';
        nameDiv.textContent = albumName || 'Unknown Album';
        info.appendChild(nameDiv);
        const artists = [...new Set(tracks.map(t => t.artist).filter(Boolean))];
        if (artists.length) {
          const sub = document.createElement('div');
          sub.className = 'album-header-sub';
          sub.textContent = artists.slice(0, 3).join(', ') + (artists.length > 3 ? '…' : '');
          info.appendChild(sub);
        }
        header.appendChild(info);

        // Right side: track count + chevron
        const right = document.createElement('div');
        right.className = 'album-header-right';
        const count = document.createElement('span');
        count.className = 'album-track-count';
        count.textContent = `${tracks.length} track${tracks.length !== 1 ? 's' : ''}`;
        right.appendChild(count);
        const chev = document.createElement('span');
        chev.className = 'album-chevron';
        chev.textContent = '▶';
        right.appendChild(chev);
        header.appendChild(right);

        // Track list (shown when expanded) — reuse cached cards when possible
        const tracksDiv = document.createElement('div');
        tracksDiv.className = 'album-tracks' + (isOpen ? ' open' : '');
        for (const track of tracks) {
          const tid = String(track.id);
          let card = _cardMap.get(tid);
          if (!card) {
            const willSkip = skipExisting && track.existingHotCues > 0;
            card = buildTrackCard(track, computeCues(track), willSkip, { hideAlbum: true });
            _cardMap.set(tid, card);
          }
          tracksDiv.appendChild(card);
        }

        header.addEventListener('click', () => {
          const opening = !expandedAlbums.has(albumName);
          if (opening) expandedAlbums.add(albumName); else expandedAlbums.delete(albumName);
          header.classList.toggle('open', opening);
          if (opening) { _slideOpen(tracksDiv, 'open'); } else { _slideClose(tracksDiv, 'open'); }
        });

        group.appendChild(header);
        group.appendChild(tracksDiv);
        _albumGroupCache.set(cacheKey, group);
        list.appendChild(group);
      }
      // Evict cache entries that no longer correspond to a visible album —
      // keeps the cache bounded by the number of distinct filtered slices
      // we've rendered, not by lifetime of the page.
      for (const k of _albumGroupCache.keys()) {
        if (!usedCacheKeys.has(k)) _albumGroupCache.delete(k);
      }
    } else {
      // Nothing changed — just tally cues for the counter
      for (const [, tracks] of groups) {
        for (const track of tracks) {
          const cues = computeCues(track);
          if (cues.length) { tracksWithCues++; totalCues += cues.length; }
        }
      }
    }
  } else {
    // --- Virtualized flat list (TASK-032/034/035) ---
    // _cardMap is the album-mode cache only; flat mode keeps live nodes
    // inside Virtualizer._visibleNodes(). The recycle pool caps mounted DOM
    // at ~viewport+buffer cards.
    //
    // Album-mode DOM (built by the `if (currentSort.by === 'album')` branch
    // above) is invisible to Virtualizer.attach() — it would render the flat
    // window on top of the orphan .album-group children and never recover the
    // memory. Clear it explicitly on the album → flat transition. (Issue #114.)
    if (list.querySelector('.album-group')) {
      list.innerHTML = '';
      _cardMap.clear();
      _albumGroupCache.clear();
    }
    list.classList.add('virtualized');
    const newFingerprint = _computeSettingsFingerprint();
    const settingsChanged = newFingerprint !== _cardSettingsFingerprint;

    // Cue totals are summed across the full sorted list (cheap; no DOM touch).
    for (const track of sorted) {
      const cues = computeCues(track);
      if (cues.length) { tracksWithCues++; totalCues += cues.length; }
    }

    // FLIP snapshot must happen BEFORE the re-attach; bound to currently
    // visible nodes only (off-screen movements are invisible anyway).
    const prevVisible = Virtualizer.isAttached() ? Virtualizer._visibleNodes() : null;
    const snapshots = new Map();
    let exitCount = 0;
    if (prevVisible && !settingsChanged && !_prefersReducedMotion) {
      const newSet = new Set();
      for (const t of sorted) newSet.add(String(t.id));
      prevVisible.forEach(function(node) {
        const tid = node.dataset.trackId;
        if (!tid) return;
        if (!newSet.has(tid)) { exitCount++; return; }
        snapshots.set(tid, node.getBoundingClientRect().top);
      });
    }
    const animateTransitions = !settingsChanged && !_prefersReducedMotion && exitCount <= 30 && snapshots.size > 0;

    if (settingsChanged) {
      _cardSettingsFingerprint = newFingerprint;
      _cardMap.clear();
      _albumGroupCache.clear();
      if (Virtualizer.isAttached()) Virtualizer.detach();
    }

    // (Re)build lazy observers BEFORE attach so onWindowChange can wire
    // newly-rendered cards immediately on first render.
    if (localMode) {
      if (_sparkObserver) _sparkObserver.disconnect();
      _sparkObserver = new IntersectionObserver(function(entries) {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            _sparkObserver.unobserve(entry.target);
            _renderEnergySparkline(entry.target);
          }
        }
      }, { rootMargin: '200px' });

      if (_mixObserver) _mixObserver.disconnect();
      _mixObserver = new IntersectionObserver(function(entries) {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            _mixObserver.unobserve(entry.target);
            if (entry.target._isCategoryChip) {
              _renderCategoryChip(entry.target);
            } else {
              _renderMixabilityChip(entry.target, entry.target._breakdown);
            }
          }
        }
      }, { rootMargin: '200px' });
    }

    const renderItem = function(index, recycledNode) {
      const track = sorted[index];
      if (!track) return null;
      const cues = computeCues(track);
      const willSkip = skipExisting && track.existingHotCues > 0;
      // Rebuilding the card subtree per render is simpler than 20-field surgical
      // updates and still wins big: only ~viewport+buffer cards exist at all.
      const card = buildTrackCard(track, cues, willSkip, {});
      const isSelected = selectedTrackIds.has(track.id) || selectedTrackIds.has(String(track.id));
      card.classList.toggle('selected', isSelected);
      if (recycledNode && recycledNode.parentNode) {
        recycledNode.parentNode.replaceChild(card, recycledNode);
      }
      return card;
    };

    const onWindowChange = function(_first, _last, visibleMap) {
      if (!localMode) return;
      // IntersectionObserver.observe() is idempotent on the same target —
      // safe to call repeatedly as the window shifts.
      visibleMap.forEach(function(card) {
        if (!card || !card.querySelector) return;
        const spark = card.querySelector('.energy-sparkline');
        if (spark && _sparkObserver) _sparkObserver.observe(spark);
        if (_mixObserver) {
          const mixEls = card.querySelectorAll('.mix-score-chip[data-track-id], .category-chip[data-track-id]');
          for (let i = 0; i < mixEls.length; i++) _mixObserver.observe(mixEls[i]);
        }
      });
      // Lazy phrase-cue loading for the visible window (phrase mode only).
      // Replaces the eager full-library pass; fetches just what's on screen.
      _queuePhraseLazyLoad(visibleMap);
    };

    // Reattach every render: the renderItem closure captures `sorted` so we
    // need a fresh closure whenever the order/filter changes. The pool +
    // visible-window math itself is bounded — re-attach is O(viewport).
    if (Virtualizer.isAttached()) Virtualizer.detach();
    Virtualizer.attach({
      container: list,
      itemHeight: CARD_HEIGHT_PX,
      totalCount: sorted.length,
      renderItem: renderItem,
      onWindowChange: onWindowChange,
      scrollSource: 'window',
      // Snap the first visible card to align with the sticky filter bar's
      // bottom edge. Without this, when the sticky pins to the viewport,
      // the first virtualized card flows naturally under it — the user
      // sees only the card's bottom slice (the cue-warning row) poking
      // out below the sticky, looking like an orphan row floating above
      // the next full card. Regression spec: tests/e2e/1-sticky-overlap.
      topOcclusionFn: function() {
        var sticky = document.getElementById('tracks-sticky');
        return sticky ? sticky.getBoundingClientRect().bottom : 0;
      },
    });

    // FLIP for visible-only reorders: nodes that survived the re-attach and
    // changed position get a transform animation. Composes with the inline
    // translateY by sandwiching `translateY(delta)` → `translateY(0)`.
    if (animateTransitions) {
      const flipDeltas = [];
      Virtualizer._visibleNodes().forEach(function(card) {
        const tid = card.dataset.trackId;
        if (!tid || !snapshots.has(tid)) return;
        const newTop = card.getBoundingClientRect().top;
        const delta = snapshots.get(tid) - newTop;
        if (Math.abs(delta) > 0.5) flipDeltas.push({ card: card, delta: delta });
      });
      for (const { card, delta } of flipDeltas) {
        const baseTransform = card.style.transform || '';
        card.animate(
          [
            { transform: `${baseTransform} translateY(${delta}px)` },
            { transform: `${baseTransform} translateY(0)` },
          ],
          { duration: 250, easing: 'ease-out' }
        );
      }
    }
  }

  const visibleIndices = filteredTracks();
  const totalFiltered = visibleIndices.length;
  const totalAll = parsedTracks.length;
  const countLabel = totalFiltered === totalAll
    ? `${totalAll} track${totalAll !== 1 ? 's' : ''}`
    : `${totalFiltered} of ${totalAll} track${totalAll !== 1 ? 's' : ''}`;
  var _countEl = document.getElementById('tracks-count');
  if (_countEl.textContent !== countLabel) {
    _countEl.textContent = countLabel;
    requestAnimationFrame(function() {
      _countEl.classList.remove('count-pop');
      void _countEl.offsetWidth;
      _countEl.classList.add('count-pop');
    });
  }
  document.getElementById('dl-summary').textContent =
    `${tracksWithCues} track${tracksWithCues !== 1 ? 's' : ''} · ${totalCues} cue${totalCues !== 1 ? 's' : ''}`;
  updateSelectionBar();

  // Flat-list mode wires its lazy observers via Virtualizer.onWindowChange,
  // so the post-render observer pass below only fires in album mode.
  if (localMode && !Virtualizer.isAttached()) {
    if (_sparkObserver) { _sparkObserver.disconnect(); }
    _sparkObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          _sparkObserver.unobserve(entry.target);
          _renderEnergySparkline(entry.target);
        }
      }
    }, { rootMargin: '200px' });
    for (const el of list.querySelectorAll('.energy-sparkline')) {
      _sparkObserver.observe(el);
    }

    if (_mixObserver) { _mixObserver.disconnect(); }
    _mixObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          _mixObserver.unobserve(entry.target);
          if (entry.target._isCategoryChip) {
            _renderCategoryChip(entry.target);
          } else {
            _renderMixabilityChip(entry.target, entry.target._breakdown);
          }
        }
      }
    }, { rootMargin: '200px' });
    for (const el of list.querySelectorAll('.mix-score-chip[data-track-id], .category-chip[data-track-id]')) {
      _mixObserver.observe(el);
    }
  }

  // A8: prune _cardMap of trackIds whose card is no longer in the DOM. Bounds
  // memory across long sessions where filters / playlist swaps thin the list.
  // Only meaningful in album mode — flat (virtualized) mode doesn't use _cardMap.
  if (_cardMap.size > 0 && !Virtualizer.isAttached()) {
    const visibleIds = new Set(
      Array.from(list.querySelectorAll('.track-card[data-track-id]'))
        .map(el => el.dataset.trackId)
    );
    for (const id of Array.from(_cardMap.keys())) {
      if (!visibleIds.has(String(id))) _cardMap.delete(id);
    }
  }
}
