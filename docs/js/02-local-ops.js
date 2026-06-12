/* AutoCue app.js — P0 T5 split part 2/8: 02-local-ops.js
 * Classic script (NOT a module): shares globals, loaded in order by
 * docs/index.html. Concatenating 01..08 in order reproduces the original
 * app.js. Do not reorder. See .claude/project/web-ui.md. */
// ── Local mode ────────────────────────────────────────────────────────────────
let localMode = false;
let healthData = {};   // String(trackId) → TrackHealthReport event
let healthLastSummary = null;
let _healthFixInProgress = false;

async function detectLocalMode() {
  try {
    const r = await fetch('/api/status', { signal: AbortSignal.timeout(600) });
    if (r.ok) { const d = await r.json(); return d.connected === true; }
  } catch {}
  return false;
}

async function loadTracksFromServer(playlistId = null) {
  // TASK-050 — perf mark around the full library-load round-trip.
  try { _perf.mark('library-load-start'); } catch (_) {}
  const tracksUrl = playlistId != null
    ? `/api/tracks?limit=10000&playlist_id=${playlistId}&sort_by=${currentSort.by}&sort_order=${currentSort.order}`
    : `/api/tracks?limit=10000&sort_by=${currentSort.by}&sort_order=${currentSort.order}`;

  // Show loading state while fetch is in progress
  const countEl = document.getElementById('local-track-count');
  if (countEl) countEl.textContent = ' · Loading…';
  // First load only: skeleton cards instead of a blank page while the
  // fetch is in flight. Subsequent reloads keep the live list on screen.
  if (!parsedTracks.length) {
    const skelSect = document.getElementById('tracks-section');
    const skelList = document.getElementById('track-list');
    if (skelSect && skelList && !skelList.children.length) {
      skelSect.classList.add('visible');
      skelList.innerHTML = new Array(6).fill(
        '<div class="skeleton-card"><div class="skel-line skel-title"></div>' +
        '<div class="skel-line skel-sub"></div><div class="skel-line skel-chips"></div></div>'
      ).join('');
    }
  }

  let statusData, tracksData;
  try {
    [statusData, tracksData] = await Promise.all([
      fetch('/api/status').then(r => r.json()),
      fetch(tracksUrl).then(r => r.json()),
    ]);
  } catch (err) {
    if (countEl) countEl.textContent = '';
    // Drop the first-load skeletons so a failed fetch doesn't leave ghost cards
    document.querySelectorAll('#track-list .skeleton-card').forEach(el => el.remove());
    try { _perf.measure('library-load', 'library-load-start'); } catch (_) {}
    throw err;
  }
  try { _perf.measure('library-load', 'library-load-start'); } catch (_) {}
  // TASK-029 — start polling the warm-up badge in case the sidecar
  // cache is still hydrating in the background.
  try { _warmupPoll.start(); } catch (_) {}
  // Show playlist track count when filtered, library total when viewing all
  if (playlistId != null) {
    const playlistName = document.getElementById('playlist-select').selectedOptions[0]?.text.replace(/\s*\(\d+\)$/, '') || 'Playlist';
    document.getElementById('local-track-count').textContent = ` · ${tracksData.length} tracks (${playlistName})`;
    updateAppStatus({ connected: true, trackCount: tracksData.length, didScan: true });
  } else {
    document.getElementById('local-track-count').textContent = ` · ${statusData.track_count} tracks`;
    updateAppStatus({ connected: true, trackCount: statusData.track_count, didScan: true });
  }
  _energyCache = {};           // D4 fix: invalidate on reload so stale curves don't persist
  _cardMap.clear();            // C: force full rebuild on library reload
  _albumGroupCache.clear();    // #172: album cache wraps cards, drop when cards drop
  _cardSettingsFingerprint = '';
  if (Virtualizer.isAttached()) Virtualizer.detach();
  _setParsedTracks(tracksData.map(t => ({
    id: String(t.id), name: t.title, artist: t.artist, album: t.album || '',
    bpm: t.bpm, totalTime: t.duration, tempo: null,
    existingHotCues: t.existing_hot_cues, hasPhrase: t.has_phrase, hasBeats: t.has_beats,
    // Map API's existing_cue_details (slot/pos_sec) to the XML-shape used by the
    // chip renderer (num/start). Local + Pages mode now share the same chip code.
    existingCueDetails: (t.existing_cue_details || []).map(c => ({
      num: c.slot, name: c.name || '', start: c.pos_sec, colorName: c.color_name || '',
    })),
    source: t.source || 'file', // B1 — server tells us if it's file/streaming/unknown
    key: t.key || '',
    rating: t.rating || 0,
    playCount: t.play_count || 0,
    lastPlayed: t.last_played || null,
    myTags: t.my_tags || [],
    colorName: t.color_name || '',
    genre: t.genre || '',
    comment: t.comment || '',
    locationFilename: '',
  })));
  parsedDoc = null;
  selectedTrackIds.clear();
  updateSelectionBar();
  const withExisting = parsedTracks.filter(t => t.existingHotCues > 0).length;
  const info = document.getElementById('existing-cues-info');
  if (withExisting > 0) {
    document.getElementById('existing-cues-label').innerHTML =
      `<strong>${withExisting}</strong> of ${parsedTracks.length} tracks already have hot cues`;
    info.style.display = 'flex';
  } else {
    info.style.display = 'none';
  }
  // Staggered fade-in-up on initial connect.
  // #download-bar is the Pages-mode XML round-trip bar ("Ready to import: …").
  // In local mode the canonical bottom bar is #action-bar (selection-driven),
  // so #download-bar must NOT fade in here — it would show stale default text
  // (e.g. "Ready to import: 1 track · 8 cues") before any XML upload and
  // persist across all tabs (Cues / Library / Discover). See issue #15.
  var _fadeSections = ['settings-section', 'tracks-section'];
  if (!localMode) _fadeSections.push('download-bar');
  // Defensive: ensure the bar is hidden in local mode even if a prior code
  // path added .visible.
  if (localMode) {
    var _dlBar = document.getElementById('download-bar');
    if (_dlBar) _dlBar.classList.remove('visible');
  }
  var _sectDelay = 0;
  _fadeSections.forEach(function(id) {
    var el = document.getElementById(id);
    setTimeout(function() {
      el.classList.add('visible');
      el.classList.add('fade-in-up');
      el.addEventListener('animationend', function() { el.classList.remove('fade-in-up'); }, { once: true });
      // In local mode collapse settings so tracks are visible immediately
      if (id === 'settings-section' && localMode) {
        if (window._collapseSettings) window._collapseSettings();
      }
    }, _sectDelay);
    _sectDelay += 70;
  });
  document.getElementById('analysis-mode-bar').style.display = 'flex';
  document.getElementById('sort-bar').style.display = '';
  // Restore persisted sort UI
  const SORT_LABELS = { title: 'Title', artist: 'Artist', album: 'Album', bpm: 'BPM', key: 'Key', rating: 'Rating', plays: 'Plays' };
  document.querySelectorAll('.sort-btn').forEach(b => {
    const isActive = b.dataset.sort === currentSort.by;
    b.classList.toggle('active', isActive);
    b.textContent = SORT_LABELS[b.dataset.sort] + (isActive && currentSort.order !== 'asc' ? ' ▼' : isActive ? ' ▲' : '');
  });
  // Show BPM legend if any track has a color assigned
  const hasColors = parsedTracks.some(t => t.colorName);
  document.getElementById('bpm-legend').classList.toggle('visible', hasColors);
  setStep(3);
  AppState.signal('tracks'); // renders via subscriber; keep updateOverwriteWarning paired
  updateOverwriteWarning();
  // Populate comment enrichment preview dropdown
  const ceSel = document.getElementById('ce-preview-track');
  if (ceSel) {
    ceSel.innerHTML = '<option value="">— select a track —</option>';
    for (const t of parsedTracks) {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = `${t.name || '(untitled)'} — ${t.artist || ''}`;
      ceSel.appendChild(opt);
    }
  }
  // Populate genre filter popup
  const genreChipsEl = document.getElementById('genre-filter-chips');
  const genreBtnEl   = document.getElementById('genre-filter-btn');
  if (genreChipsEl && genreBtnEl) {
    const genres = [...new Set(parsedTracks.map(t => t.genre).filter(Boolean))].sort();
    genreChipsEl.innerHTML = '';
    for (const g of genres) {
      const chip = document.createElement('button');
      chip.className = 'genre-chip' + (genreFilters.has(g) ? ' active' : '');
      chip.textContent = g;
      chip.dataset.genre = g;
      genreChipsEl.appendChild(chip);
    }
    genreBtnEl.style.display = genres.length ? '' : 'none';
  }
}

// ── Library Health ────────────────────────────────────────────────────────────

async function scanLibraryHealth() {
  const btn      = document.getElementById('health-scan-btn');
  const label    = document.getElementById('health-scanning-label');
  const progBar  = document.getElementById('health-progress-bar');
  const fill     = document.getElementById('health-progress-fill');
  const summary  = document.getElementById('health-summary');

  const abortCtrl = new AbortController();
  _setBtnCancellable(btn, 'Scanning…', abortCtrl);
  label.style.display = '';
  label.textContent = 'Scanning…';
  progBar.style.display = '';
  // Invalidate any pending delayed-hide from a previous scan's finally block
  progBar._hideTok = (progBar._hideTok || 0) + 1;
  fill.style.width = '0%';
  summary.style.display = 'none';
  healthData = {};

  const url = activePlaylistId
    ? `/api/health?playlist_id=${activePlaylistId}`
    : '/api/health';

  try {
    const r = await fetch(url, { signal: abortCtrl.signal });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let processed = 0;
    let total = null;
    let receivedDone = false;

    // Cancel the reader stream when abort fires
    abortCtrl.signal.addEventListener('abort', function() { reader.cancel().catch(function(){}); }, { once: true });

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.done) {
          receivedDone = true;
          healthLastSummary = ev.summary;
          total = ev.summary.total;
          fill.style.width = '100%';
          _renderHealthSummary(ev.summary);
        } else if (ev.total && !ev.track_id) {
          total = ev.total;
        } else {
          processed++;
          healthData[String(ev.track_id)] = ev;
          const prog = `Scanning… ${processed.toLocaleString()}`;
          label.textContent = prog;
          _setBtnCancellable(btn, prog, abortCtrl);
          const pct = total
            ? processed / total * 100
            : Math.min(97, processed / Math.max(processed + 50, 1) * 100);
          fill.style.width = `${pct}%`;
        }
      }
    }
    // Flush any data left in buf when the stream closed without a trailing \n\n
    for (const line of buf.split('\n')) {
      if (!line.startsWith('data: ')) continue;
      try {
        const ev = JSON.parse(line.slice(6));
        if (ev.done) {
          receivedDone = true;
          healthLastSummary = ev.summary;
          _renderHealthSummary(ev.summary);
        } else {
          processed++;
          healthData[String(ev.track_id)] = ev;
        }
      } catch {}
    }
    if (!receivedDone) {
      showToast('Health scan ended without a summary — results may be incomplete');
    }
    fill.style.width = '100%';
    renderTracks();
  } catch (err) {
    if (err.name === 'AbortError') {
      showToast(`Health scan cancelled — ${Object.keys(healthData).length.toLocaleString()} tracks scanned`);
      if (Object.keys(healthData).length > 0) renderTracks();
    } else {
      showToast(`Health scan failed: ${err.message}`);
    }
  } finally {
    _setBtnLoading(btn, false);
    // Let the 100% fill paint for a beat — completion used to be hidden in the
    // same tick it was reached, so the bar never visibly finished.
    const hideTok = progBar._hideTok;
    setTimeout(() => {
      if (progBar._hideTok !== hideTok) return; // a newer scan owns the bar now
      label.style.display = 'none';
      progBar.style.display = 'none';
    }, 400);
  }
}

// ── Duplicate Tracks ──────────────────────────────────────────────────────────
//
// Phase 1: scan + display only. The destructive delete path (with backup +
// Rekordbox-closed checks) lands in a follow-up PR after this UX is validated.

function _pickKeeper(copies) {
  // Mirror of autocue.analysis.duplicates.pick_keeper. Keep in sync.
  // Phase 3 WS2 order: cues → plays → last_played → bitrate → -id.
  const keyOf = (c) => [
    c.existing_hot_cues || 0,
    c.play_count || 0,
    c.last_played || '',
    c.bitrate || 0,
    -c.track_id,
  ];
  let best = copies[0];
  for (let i = 1; i < copies.length; i++) {
    const c = copies[i];
    const aKey = keyOf(best);
    const bKey = keyOf(c);
    let take = false;
    for (let j = 0; j < aKey.length; j++) {
      if (bKey[j] > aKey[j]) { take = true; break; }
      if (bKey[j] < aKey[j]) { break; }
    }
    if (take) best = c;
  }
  return best.track_id;
}

// Format a duration in seconds as M:SS for the per-copy detail row.
function _fmtDur(sec) {
  sec = Math.round(sec || 0);
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function _renderDuplicateGroup(group) {
  const div = document.createElement('div');
  div.className = 'duplicates-group panel-card';
  div.style.cssText = 'padding:10px 12px;border:1px solid var(--border);border-radius:8px;background:var(--surface);';
  div.dataset.groupKey = `${(group.artist || '').toLowerCase()}|||${(group.title || '').toLowerCase()}`;

  // WS2 — the keeper is now mutable. It starts at the backend's suggestion
  // (group.keeper_id, echoed via is_keeper) but the user can pick a
  // different copy via the "Keep" radio in the expanded detail rows. All
  // derived state (non-keeper ids, delete-button label, dataset for the
  // bulk-delete walk, same-path chips) recomputes from currentKeeperId.
  let currentKeeperId = (group.copies.find(c => c.is_keeper) || group.copies[0]).track_id;

  const _nonKeepers = () =>
    group.copies.filter(c => c.track_id !== currentKeeperId).map(c => c.track_id);
  const _keeperPath = () => {
    const k = group.copies.find(c => c.track_id === currentKeeperId) || {};
    return `${k.folder_path || ''}${k.file_name || ''}`;
  };

  const head = document.createElement('div');
  head.style.cssText = 'display:flex;align-items:center;gap:10px;flex-wrap:wrap;';
  const title = document.createElement('div');
  title.style.cssText = 'flex:1;min-width:0;';
  const artistEl = document.createElement('span');
  artistEl.style.cssText = 'font-weight:600;';
  artistEl.textContent = group.artist || '(unknown artist)';
  const dashEl = document.createElement('span');
  dashEl.style.cssText = 'margin:0 6px;color:var(--muted);';
  dashEl.textContent = '—';
  const titleEl = document.createElement('span');
  titleEl.textContent = group.title || '(untitled)';
  title.appendChild(artistEl);
  title.appendChild(dashEl);
  title.appendChild(titleEl);
  const countChip = document.createElement('span');
  countChip.style.cssText = 'font-size:11px;background:var(--amber, #c98a00)22;color:var(--amber, #c98a00);border:1px solid var(--amber, #c98a00)55;border-radius:9999px;padding:2px 8px;font-weight:600;';
  countChip.textContent = `${group.copies.length} copies`;
  const deleteBtn = document.createElement('button');
  deleteBtn.className = 'secondary-btn duplicates-group-delete';
  deleteBtn.style.cssText = 'font-size:11px;padding:3px 10px;color:#e4384e;border-color:#e4384e44;';
  deleteBtn.title = 'Opens a confirm dialog; backup is created before any delete';
  const toggle = document.createElement('button');
  toggle.className = 'secondary-btn';
  toggle.style.cssText = 'font-size:11px;padding:3px 10px;';
  toggle.textContent = 'Show details';
  head.appendChild(title);
  head.appendChild(countChip);
  head.appendChild(deleteBtn);
  head.appendChild(toggle);
  div.appendChild(head);

  const table = document.createElement('div');
  table.className = 'dup-details';

  // Re-paint everything that depends on the current keeper: dataset (for
  // the bulk-delete walk), the delete-button label, the row highlight +
  // same-path chips. Called on initial render and on every radio change.
  function _refresh() {
    const nk = _nonKeepers();
    div.dataset.nonKeeperIds = JSON.stringify(nk);
    deleteBtn.textContent = `Delete ${nk.length} non-keeper${nk.length === 1 ? '' : 's'}`;
    deleteBtn.disabled = nk.length === 0;
    const keeperPath = _keeperPath();
    table.querySelectorAll('.dup-copy-row').forEach((row) => {
      const tid = Number(row.dataset.trackId);
      const isKeeper = tid === currentKeeperId;
      row.style.background = isKeeper
        ? 'color-mix(in srgb, var(--green) 12%, transparent)' : '';
      row.style.fontWeight = isKeeper ? '600' : '';
      const star = row.querySelector('.dup-keeper-star');
      if (star) star.style.visibility = isKeeper ? 'visible' : 'hidden';
      // Same-path chip: a NON-keeper whose file path matches the keeper's
      // is safe to delete (no orphan file); a distinct-path non-keeper
      // leaves an audio file on disk. The keeper itself shows no chip.
      const chip = row.querySelector('.dup-path-chip');
      if (chip) {
        const c = group.copies.find(x => x.track_id === tid) || {};
        const samePath = `${c.folder_path || ''}${c.file_name || ''}` === keeperPath;
        if (isKeeper) {
          chip.style.display = 'none';
        } else if (samePath) {
          chip.style.display = '';
          chip.textContent = '🗂 same file as keeper';
          chip.style.color = 'var(--muted)';
        } else {
          chip.style.display = '';
          chip.textContent = '📁 distinct file — stays on disk';
          chip.style.color = 'var(--amber, #c98a00)';
        }
      }
    });
  }

  for (const c of group.copies) {
    const row = document.createElement('div');
    row.className = 'dup-copy-row';
    row.dataset.trackId = c.track_id;
    row.style.cssText = 'display:flex;gap:10px;align-items:center;padding:4px 6px;border-radius:4px;flex-wrap:wrap;';
    // "Keep" radio (WS2 override). One radio group per duplicate group via
    // a name keyed on the group's DOM identity.
    const radioLabel = document.createElement('label');
    radioLabel.style.cssText = 'display:inline-flex;align-items:center;gap:3px;cursor:pointer;min-width:54px;';
    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.name = `dup-keeper-${div.dataset.groupKey}`;
    radio.value = String(c.track_id);
    radio.checked = c.track_id === currentKeeperId;
    radio.className = 'dup-keeper-radio';
    radio.addEventListener('change', () => {
      if (radio.checked) { currentKeeperId = c.track_id; _refresh(); }
    });
    const radioText = document.createElement('span');
    radioText.style.cssText = 'font-size:11px;';
    radioText.textContent = 'Keep';
    radioLabel.appendChild(radio);
    radioLabel.appendChild(radioText);

    const meta = document.createElement('span');
    meta.style.cssText = 'display:flex;gap:10px;flex-wrap:wrap;align-items:baseline;';
    meta.innerHTML =
      `<span style="min-width:84px;">id ${c.track_id}</span>` +
      `<span style="min-width:48px;">${_fmtDur(c.duration)}</span>` +
      `<span style="min-width:60px;">${(c.bpm || 0).toFixed(2)} BPM</span>` +
      `<span style="min-width:36px;">${_esc(c.key || '—')}</span>` +
      `<span style="min-width:48px;">${c.existing_hot_cues || 0} cues</span>` +
      `<span style="min-width:48px;">${c.play_count || 0} plays</span>` +
      (c.bitrate ? `<span style="min-width:60px;">${c.bitrate} kbps</span>` : '') +
      `<span style="min-width:110px;">${_esc((c.last_played || '—').slice(0, 19))}</span>`;

    const star = document.createElement('span');
    star.className = 'dup-keeper-star';
    star.style.cssText = 'color:var(--green);';
    star.textContent = '★ keeper';

    const chip = document.createElement('span');
    chip.className = 'dup-path-chip';
    chip.style.cssText = 'font-size:11px;margin-left:auto;';

    row.appendChild(radioLabel);
    row.appendChild(meta);
    row.appendChild(chip);
    row.appendChild(star);
    table.appendChild(row);
  }
  div.appendChild(table);

  deleteBtn.addEventListener('click', () => {
    const nk = _nonKeepers();
    const keeperPath = _keeperPath();
    const distinct = group.copies.filter(
      c => c.track_id !== currentKeeperId &&
        `${c.folder_path || ''}${c.file_name || ''}` !== keeperPath
    ).length;
    _openDuplicatesConfirm({
      track_ids: nk,
      label: `1 keeper + ${nk.length} non-keeper${nk.length === 1 ? '' : 's'}`,
      meta: ` of ${_esc(group.artist || '?')} — ${_esc(group.title || '?')}`,
      audioNote: distinct > 0
        ? `${distinct} distinct audio file${distinct === 1 ? '' : 's'} will remain on disk.`
        : 'All deleted rows share the keeper\'s audio file — no orphan files.',
      onSuccess: () => { _onTracksDeleted(nk); div.remove(); },
    });
  });

  toggle.addEventListener('click', () => {
    // Accordion slide via the shared helper — same motion as the mixing guide
    _slideToggle(table, 'open');
    toggle.textContent = table.classList.contains('open') ? 'Hide details' : 'Show details';
  });

  _refresh();
  return div;
}

async function scanDuplicates() {
  const btn = document.getElementById('wb-dupes-rescan');
  const statusEl = document.getElementById('duplicates-status-label');
  const progress = document.getElementById('duplicates-progress');
  const summary = document.getElementById('duplicates-summary');
  const empty = document.getElementById('duplicates-empty');
  const list = document.getElementById('duplicates-list');

  const abortCtrl = new AbortController();
  _setBtnCancellable(btn, 'Scanning…', abortCtrl);
  statusEl.style.display = '';
  statusEl.textContent = 'Scanning…';
  progress.style.display = '';
  progress.style.color = ''; // clear any red left by a prior failed scan
  progress.textContent = 'Loading tracks…';
  summary.style.display = 'none';
  empty.style.display = 'none';
  list.innerHTML = '';

  try {
    const r = await fetch('/api/duplicates', { signal: abortCtrl.signal });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || r.statusText);
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let total = null;
    let groupCount = 0;
    abortCtrl.signal.addEventListener('abort', () => reader.cancel().catch(() => {}), { once: true });

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.total !== undefined && !ev.group && !ev.done) {
          total = ev.total;
          progress.textContent = `Scanning ${total.toLocaleString()} tracks…`;
        } else if (ev.group) {
          groupCount++;
          const groupEl = _renderDuplicateGroup(ev.group);
          groupEl.classList.add('fade-in-up'); // groups stream in — match the track-card entrance
          list.appendChild(groupEl);
          progress.textContent = `Found ${groupCount.toLocaleString()} duplicate groups so far…`;
        } else if (ev.done) {
          const s = ev.summary;
          progress.style.display = 'none';
          const bulkBtn = document.getElementById('wb-dupes-bulk-delete');
          if (s.groups === 0) {
            empty.style.display = '';
            if (bulkBtn) { bulkBtn.disabled = true; bulkBtn.textContent = 'Delete non-keepers'; }
          } else {
            // Collect every non-keeper across every group so the bulk
            // delete button can fire one POST instead of N.
            const allNonKeepers = [];
            list.querySelectorAll('.duplicates-group').forEach(g => {
              try { allNonKeepers.push(...JSON.parse(g.dataset.nonKeeperIds || '[]')); }
              catch (_) {}
            });
            summary.innerHTML = '';
            const textEl = document.createElement('div');
            textEl.id = 'duplicates-summary-text';
            textEl.style.cssText = 'flex:1;';
            textEl.innerHTML =
              `<strong>${s.groups.toLocaleString()} duplicate group${s.groups === 1 ? '' : 's'}</strong> ` +
              `· ${s.surplus.toLocaleString()} surplus copies of ${s.scanned.toLocaleString()} scanned tracks` +
              (s.skipped_empty > 0 ? ` · ${s.skipped_empty.toLocaleString()} empty-metadata tracks skipped` : '');
            // P3: the bulk-delete verb is a STATIC toolbar button
            // (#wb-dupes-bulk-delete, wired once in _wireDuplicatesConfirm to
            // _onDuplicatesBulkDelete) — the scan-done branch only refreshes
            // its label + disabled state. Same write path as before.
            if (bulkBtn) {
              bulkBtn.disabled = allNonKeepers.length === 0;
              bulkBtn.textContent = `Delete all ${allNonKeepers.length} non-keepers`;
            }
            summary.style.display = 'flex';
            summary.style.alignItems = 'center';
            summary.style.gap = '10px';
            summary.appendChild(textEl);
          }
        }
      }
    }
  } catch (e) {
    // Failure must read as failure, not progress — red text + error toast
    progress.textContent = `Scan failed: ${e.message || e}`;
    progress.style.color = 'var(--danger, #e4384e)';
    showToast(`Duplicate scan failed: ${e.message || e}`, true);
  } finally {
    _setBtnLoading(btn, false);
    btn.textContent = 'Rescan';
    statusEl.style.display = 'none';
  }
}

// P3: bulk-delete click handler for the static #wb-dupes-bulk-delete toolbar
// verb (wired once in _wireDuplicatesConfirm). Identical behavior to the old
// dynamically-created button: re-collect non-keeper ids AT CLICK TIME (keeper
// radio flips may have shifted them since the scan), confirm, then surgical
// invalidation + a fresh scan against DB ground truth.
function _onDuplicatesBulkDelete() {
  const list = document.getElementById('duplicates-list');
  const summary = document.getElementById('duplicates-summary');
  if (!list) return;
  const ids = [];
  list.querySelectorAll('.duplicates-group').forEach(g => {
    try { ids.push(...JSON.parse(g.dataset.nonKeeperIds || '[]')); }
    catch (_) {}
  });
  if (ids.length === 0) return;
  _openDuplicatesConfirm({
    track_ids: ids,
    label: `${list.querySelectorAll('.duplicates-group').length} keepers + ${ids.length} non-keepers`,
    meta: ' across the whole library',
    onSuccess: () => {
      _onTracksDeleted(ids);
      // Re-scan to reflect the now-shrunken DB ground truth.
      if (summary) summary.style.display = 'none';
      list.innerHTML = '';
      scanDuplicates();
    },
  });
}

// ── Duplicates: destructive delete (phase 2) ─────────────────────────────────
//
// All deletes route through this single confirm modal. The primary button is
// disabled for 250ms after open so an accidental Enter on the previous-focused
// element can't fire delete by mistake — mirrors the Discover download-confirm
// pattern. The actual POST happens in _runDuplicatesDelete.

let _duplicatesConfirmPending = null;
let _duplicatesPrimaryEnableTimer = null;
let _duplicatesDeleteAbort = null;   // WS5 — AbortController for the in-flight SSE delete
let _duplicatesDeleting = false;      // true while a delete SSE stream is running

// WS7 — centralised library-state invalidation. Every destructive delete
// (per-group or bulk) calls this with the deleted ids so the rest of the
// app doesn't keep stale references that 404 or mis-scope later. Surgical
// (O(deleted)) — avoids a full /api/tracks refetch that would reset the
// Cues-tab scroll + selection.
function _onTracksDeleted(ids) {
  if (!ids || !ids.length) return;
  const gone = new Set(ids.map(Number));
  if (Array.isArray(window.parsedTracks)) {
    parsedTracks = parsedTracks.filter(t => !gone.has(Number(t.id)));
  }
  if (window.parsedTracksById && typeof parsedTracksById.delete === 'function') {
    gone.forEach(id => parsedTracksById.delete(String(id)));
  }
  if (window.healthData) {
    gone.forEach(id => { delete healthData[String(id)]; });
  }
  // Refresh the duplicates summary counter + the bulk-delete label so they
  // stay honest after a per-group delete shrinks the set.
  _refreshDuplicatesSummaryAfterDelete();
  // P3 (R9): repaint everything subscribed to the tracks bus — grid, rail
  // crate counts, playlists, status sentence — so deleted tracks vanish
  // everywhere, not just from the duplicates list.
  if (window.AppState) AppState.signal('tracks');
}

// Recompute the bulk "Delete all N non-keepers" label + the summary counter
// from the remaining .duplicates-group cards in the DOM.
function _refreshDuplicatesSummaryAfterDelete() {
  const list = document.getElementById('duplicates-list');
  if (!list) return;
  const cards = Array.from(list.querySelectorAll('.duplicates-group'));
  let surplus = 0;
  for (const c of cards) {
    try { surplus += JSON.parse(c.dataset.nonKeeperIds || '[]').length; } catch (_) {}
  }
  const bulkBtn = document.getElementById('wb-dupes-bulk-delete');
  if (bulkBtn) {
    bulkBtn.textContent = `Delete all ${surplus} non-keepers`;
    bulkBtn.disabled = surplus === 0;
  }
  const textEl = document.getElementById('duplicates-summary-text');
  if (textEl) {
    textEl.innerHTML =
      `<strong>${cards.length.toLocaleString()} duplicate group${cards.length === 1 ? '' : 's'}</strong> ` +
      `· ${surplus.toLocaleString()} surplus copies remaining`;
  }
  if (cards.length === 0) {
    const summary = document.getElementById('duplicates-summary');
    const empty = document.getElementById('duplicates-empty');
    if (summary) summary.style.display = 'none';
    if (empty) empty.style.display = '';
  }
}

// WS8 — focus trap: keep Tab/Shift+Tab cycling between the two modal
// buttons. Installed on open, removed on close.
let _duplicatesTrapHandler = null;
function _installDuplicatesFocusTrap() {
  const cancel = document.getElementById('duplicates-confirm-cancel');
  const go = document.getElementById('duplicates-confirm-go');
  _duplicatesTrapHandler = (ev) => {
    if (ev.key !== 'Tab') return;
    const focusables = [cancel, go].filter(b => b && !b.disabled);
    if (focusables.length === 0) { ev.preventDefault(); return; }
    const first = focusables[0], last = focusables[focusables.length - 1];
    if (ev.shiftKey && document.activeElement === first) {
      ev.preventDefault(); last.focus();
    } else if (!ev.shiftKey && document.activeElement === last) {
      ev.preventDefault(); first.focus();
    } else if (!focusables.includes(document.activeElement)) {
      ev.preventDefault(); first.focus();
    }
  };
  document.getElementById('duplicates-confirm')
    ?.addEventListener('keydown', _duplicatesTrapHandler);
}
function _removeDuplicatesFocusTrap() {
  if (_duplicatesTrapHandler) {
    document.getElementById('duplicates-confirm')
      ?.removeEventListener('keydown', _duplicatesTrapHandler);
    _duplicatesTrapHandler = null;
  }
}

function _openDuplicatesConfirm({ track_ids, label, meta, audioNote, onSuccess }) {
  if (!track_ids || track_ids.length === 0) return;
  _duplicatesConfirmPending = { track_ids, onSuccess };
  const modal = document.getElementById('duplicates-confirm');
  const backdrop = document.getElementById('duplicates-confirm-backdrop');
  const countEl = document.getElementById('duplicates-confirm-count');
  const metaEl = document.getElementById('duplicates-confirm-meta');
  const audioEl = document.getElementById('duplicates-confirm-audio');
  const goBtn = document.getElementById('duplicates-confirm-go');
  const progress = document.getElementById('duplicates-confirm-progress');
  countEl.textContent = `Delete ${track_ids.length} non-keeper${track_ids.length === 1 ? '' : 's'}`;
  metaEl.innerHTML = `<br><span style="color:var(--muted);font-size:12px;">${label || ''}${meta || ''}</span>`;
  if (audioEl) {
    if (audioNote) { audioEl.textContent = audioNote; audioEl.style.display = ''; }
    else audioEl.style.display = 'none';
  }
  if (progress) progress.style.display = 'none';
  modal.setAttribute('aria-hidden', 'false');
  backdrop.setAttribute('aria-hidden', 'false');
  goBtn.disabled = true;
  goBtn.textContent = 'Delete';
  document.getElementById('duplicates-confirm-cancel').textContent = 'Cancel';
  // Defeat accidental Enter held over from the previous focus target.
  clearTimeout(_duplicatesPrimaryEnableTimer);
  _duplicatesPrimaryEnableTimer = setTimeout(() => { goBtn.disabled = false; }, 250);
  _installDuplicatesFocusTrap();
  // Default focus to Cancel as the second safety layer.
  document.getElementById('duplicates-confirm-cancel').focus();
}

function _closeDuplicatesConfirm() {
  // If a delete is in flight, ESC/Cancel aborts it (WS8). The backend
  // honours the disconnect and the pre-delete backup still restores the
  // pre-session state, so an abort is always safe.
  if (_duplicatesDeleting && _duplicatesDeleteAbort) {
    _duplicatesDeleteAbort.abort();
    return; // the SSE consumer's finally closes the modal + toasts
  }
  _duplicatesConfirmPending = null;
  clearTimeout(_duplicatesPrimaryEnableTimer);
  _removeDuplicatesFocusTrap();
  document.getElementById('duplicates-confirm').setAttribute('aria-hidden', 'true');
  document.getElementById('duplicates-confirm-backdrop').setAttribute('aria-hidden', 'true');
}

async function _runDuplicatesDelete() {
  if (!_duplicatesConfirmPending || _duplicatesDeleting) return;
  const { track_ids, onSuccess } = _duplicatesConfirmPending;
  const goBtn = document.getElementById('duplicates-confirm-go');
  const cancelBtn = document.getElementById('duplicates-confirm-cancel');
  const progress = document.getElementById('duplicates-confirm-progress');
  const fill = document.getElementById('duplicates-confirm-progress-fill');
  const progLabel = document.getElementById('duplicates-confirm-progress-label');

  _duplicatesDeleting = true;
  _duplicatesDeleteAbort = new AbortController();
  goBtn.disabled = true;
  goBtn.textContent = 'Deleting…';
  cancelBtn.textContent = 'Cancel delete';   // ESC/Cancel now aborts the op
  if (progress) progress.style.display = '';

  let summary = null;
  try {
    const r = await fetch('/api/duplicates/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids, dry_run: false }),
      signal: _duplicatesDeleteAbort.signal,
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || r.statusText);
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    const total = track_ids.length;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.done) {
          summary = ev.summary;
        } else if (typeof ev.processed === 'number') {
          const pct = total ? Math.min(100, ev.processed / total * 100) : 100;
          if (fill) fill.style.width = `${pct}%`;
          if (progLabel) progLabel.textContent =
            `Deleted ${ev.deleted} of ${total}…`;
        }
      }
    }
    // Stream ended.
    _duplicatesDeleting = false;
    _duplicatesDeleteAbort = null;
    _removeDuplicatesFocusTrap();
    document.getElementById('duplicates-confirm').setAttribute('aria-hidden', 'true');
    document.getElementById('duplicates-confirm-backdrop').setAttribute('aria-hidden', 'true');
    _duplicatesConfirmPending = null;

    const s = summary || { deleted: 0, cancelled: false, backup_path: null };
    _showDuplicatesUndoToast(s, track_ids.length);
    if (typeof onSuccess === 'function') onSuccess(s);
  } catch (e) {
    _duplicatesDeleting = false;
    const aborted = e && e.name === 'AbortError';
    _duplicatesDeleteAbort = null;
    _removeDuplicatesFocusTrap();
    document.getElementById('duplicates-confirm').setAttribute('aria-hidden', 'true');
    document.getElementById('duplicates-confirm-backdrop').setAttribute('aria-hidden', 'true');
    _duplicatesConfirmPending = null;
    if (aborted) {
      // The backend commits rows staged before the disconnect, so SOME
      // may have been deleted. Safest move: re-scan so the panel reflects
      // ground truth, and tell the user the backup is intact.
      showToast('Delete cancelled — backup is intact. Re-scanning…');
      const list = document.getElementById('duplicates-list');
      const summaryEl = document.getElementById('duplicates-summary');
      if (list) list.innerHTML = '';
      if (summaryEl) summaryEl.style.display = 'none';
      scanDuplicates();
    } else {
      showToast(`Delete failed: ${e.message || e}`);
    }
  }
}

// WS5 — success toast with an inline "Undo this delete" button that
// restores the backup the delete just created. showToast renders plain
// text, so we build a richer transient banner pinned to the duplicates
// summary for 30s.
function _showDuplicatesUndoToast(summary, requested) {
  // P3 seam (R8): announce the completed delete so the v2 status-sentence
  // restore sheet can offer the same backup. Fired before any early return —
  // the banner below is an in-view convenience, the sheet is canonical.
  window.dispatchEvent(new CustomEvent('autocue:duplicates-deleted', {
    detail: {
      deleted: summary.deleted || 0,
      requested,
      cancelled: !!summary.cancelled,
      backup_path: summary.backup_path || null,
    },
  }));
  const deleted = summary.deleted || 0;
  const cancelled = !!summary.cancelled;
  const backupPath = summary.backup_path || null;
  const base = cancelled
    ? `Cancelled — ${deleted} of ${requested} deleted.`
    : `Deleted ${deleted} of ${requested} tracks.`;
  if (!backupPath) { showToast(base); return; }

  // Inline banner above the summary with an Undo button.
  const host = document.getElementById('duplicates-summary');
  if (!host) { showToast(`${base} Backup saved.`); return; }
  let banner = document.getElementById('duplicates-undo-banner');
  if (banner) banner.remove();
  banner = document.createElement('div');
  banner.id = 'duplicates-undo-banner';
  banner.className = 'fade-in-up';
  banner.style.cssText = 'position:relative;overflow:hidden;display:flex;align-items:center;gap:10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:6px;padding:8px 10px;margin-bottom:10px;';
  const text = document.createElement('span');
  text.style.flex = '1';
  text.textContent = `${base} Backup: ${backupPath.split('/').pop()}`;
  const undoBtn = document.createElement('button');
  undoBtn.className = 'secondary-btn';
  undoBtn.style.cssText = 'font-size:11px;padding:3px 10px;';
  undoBtn.textContent = 'Undo this delete';
  undoBtn.addEventListener('click', async () => {
    undoBtn.disabled = true;
    undoBtn.textContent = 'Restoring…';
    try {
      const r = await fetch('/api/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: backupPath.split('/').pop() }),
      });
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
      banner.remove();
      showToast('Restored from backup. Reload to see the recovered tracks.');
    } catch (e) {
      undoBtn.disabled = false;
      undoBtn.textContent = 'Undo this delete';
      showToast(`Restore failed: ${e.message || e}`);
    }
  });
  banner.appendChild(text);
  banner.appendChild(undoBtn);
  // Draining bar signals the 30s window — the banner used to vanish with no
  // warning, mid-reach for the Undo button.
  const drain = document.createElement('div');
  drain.style.cssText = 'position:absolute;left:0;bottom:0;height:2px;width:100%;background:var(--green);transition:width 30s linear;';
  banner.appendChild(drain);
  host.parentNode.insertBefore(banner, host);
  requestAnimationFrame(() => { drain.style.width = '0%'; });
  // Auto-dismiss after 30s — the backup is still in /api/backups if the
  // user wants it later. Fade out instead of snapping away.
  setTimeout(() => {
    if (!banner.isConnected) return;
    banner.style.transition = 'opacity var(--dur-chrome) ease';
    banner.style.opacity = '0';
    setTimeout(() => banner.remove(), 320);
  }, 30000);
}

(function _wireDuplicatesConfirm() {
  // Wire the modal's buttons exactly once at parse time — the IDs are
  // static and the listeners are idempotent against the
  // _duplicatesConfirmPending state, so re-binding on every panel render
  // would just leak listeners.
  document.getElementById('duplicates-confirm-cancel')
    ?.addEventListener('click', _closeDuplicatesConfirm);
  document.getElementById('duplicates-confirm-go')
    ?.addEventListener('click', _runDuplicatesDelete);
  document.getElementById('duplicates-confirm-backdrop')
    ?.addEventListener('click', _closeDuplicatesConfirm);
  // P3: the static bulk-delete toolbar verb (replaces the per-scan
  // dynamically-created button; same click-time re-collect + confirm path).
  document.getElementById('wb-dupes-bulk-delete')
    ?.addEventListener('click', _onDuplicatesBulkDelete);
  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    if (document.getElementById('duplicates-confirm')
        ?.getAttribute('aria-hidden') === 'false') {
      _closeDuplicatesConfirm();
    }
  });
})();

function _renderHealthSummary(s) {
  // AutoCue 2.0: notify v2 modules (status sentence) that a fresh health
  // summary landed — they read it via window.ACBridge.healthSummary().
  try { window.dispatchEvent(new CustomEvent('autocue:health-summary')); } catch (_) {}
  const summary   = document.getElementById('health-summary');
  const ring      = document.getElementById('health-score-ring');
  const titleEl   = document.getElementById('health-summary-title');
  const subEl     = document.getElementById('health-summary-sub');
  const issueList = document.getElementById('health-issue-list');
  const fixRow    = document.getElementById('health-fix-row');

  // Ease the report in when it first appears — it used to pop via a bare display flip
  if (summary.style.display === 'none') {
    summary.classList.add('fade-in-up');
    summary.addEventListener('animationend', () => summary.classList.remove('fade-in-up'), { once: true });
  }
  summary.style.display = '';
  const scannableCount = (s.total || 0) - (s.excluded_missing_audio || 0);
  const score = Math.round(s.library_score);
  if (scannableCount === 0) {
    ring.textContent = '—';
    ring.className = 'health-score-ring hsr-none';
  } else {
    ring.textContent = score;
    ring.className = 'health-score-ring ' +
      (score >= 90 ? 'hsr-good' : score >= 70 ? 'hsr-ok' : 'hsr-bad');
  }

  titleEl.textContent = scannableCount === 0 ? 'No scannable tracks' : `${score}/100 library health`;
  const excl = s.excluded_missing_audio || 0;
  subEl.textContent = `${scannableCount.toLocaleString()} track${scannableCount !== 1 ? 's' : ''} scanned`
    + (excl ? ` · ${excl} excluded (audio missing from disk)` : '');

  issueList.innerHTML = '';
  // scoreIssues affect hasIssues (block "looks great"); infoOnly rows are shown but don't block it
  const issueRows = [
    { count: s.no_cues,        icon: '✗', label: 'tracks have no hot cues',              cls: '#e4384e' },
    { count: excl,             icon: '✗', label: 'tracks — audio file missing',            cls: '#e4384e' },
    { count: s.duplicate_cues, icon: '⚠', label: 'tracks have duplicate cue positions' },
    { count: s.no_phrase,      icon: 'ℹ', label: 'tracks have no phrase analysis',        note: 'Re-analyze in Rekordbox' },
    { count: s.no_beatgrid,    icon: 'ℹ', label: 'tracks have no beat grid',              note: 'Re-analyze in Rekordbox' },
    { count: s.unnamed_cues,   icon: 'ℹ', label: 'tracks have unnamed cues' },
    { count: s.no_memory_cue,  icon: 'ℹ', label: 'tracks missing memory cue', infoOnly: true },
  ];
  let hasIssues = false;
  for (const row of issueRows) {
    if (!row.count) continue;
    if (!row.infoOnly) hasIssues = true;
    const el = document.createElement('div');
    el.className = 'health-issue-row';
    el.innerHTML =
      `<span class="health-issue-icon" style="${row.cls ? 'color:'+row.cls : ''}">${row.icon}</span>` +
      `<span class="health-issue-count">${row.count.toLocaleString()}</span>` +
      `<span class="health-issue-label">${row.label}</span>` +
      (row.note ? `<span class="health-fix-note">${row.note}</span>` : '');
    issueList.appendChild(el);
  }
  if (!hasIssues) {
    const ok = document.createElement('div');
    ok.className = 'health-issue-row';
    ok.innerHTML = `<span class="health-issue-icon" style="color:var(--green)">✓</span>`
      + `<span class="health-issue-label" style="color:var(--green)">No issues — library looks great</span>`;
    issueList.appendChild(ok);
  }

  // Split fix buttons: phrase-quality vs lower-confidence
  fixRow.innerHTML = '';
  const noCuesByTier = { phrase: [], bar: [], heuristic: [] };
  for (const [tid, report] of Object.entries(healthData)) {
    if ((report.issues || []).some(i => i.code === 'NO_CUES') && noCuesByTier[report.fix_tier]) {
      noCuesByTier[report.fix_tier].push(parseInt(tid));
    }
  }
  const phraseIds    = noCuesByTier.phrase;
  const lowerIds     = [...noCuesByTier.bar, ...noCuesByTier.heuristic];

  if (phraseIds.length) {
    const b = document.createElement('button');
    b.className = 'primary';
    b.style.fontSize = '13px';
    b.textContent = `Fix phrase-quality tracks (${phraseIds.length})`;
    b.addEventListener('click', () => _applyHealthFix(phraseIds, false, b));
    fixRow.appendChild(b);
  }
  if (lowerIds.length) {
    const b = document.createElement('button');
    b.className = 'secondary-btn';
    b.style.fontSize = '13px';
    b.textContent = `Fix remaining (${lowerIds.length} — bar/heuristic quality)`;
    b.addEventListener('click', () => _applyHealthFix(lowerIds, true, b));
    fixRow.appendChild(b);
  }
}

async function _applyHealthFix(trackIds, needsConfirm, srcBtn) {
  if (!trackIds.length) return;
  if (_healthFixInProgress) { showToast('A fix is already in progress — please wait'); return; }
  if (needsConfirm && !(await _confirmDialog(
    `Fix ${trackIds.length} track${trackIds.length !== 1 ? 's' : ''} using bar-interval or heuristic cues (lower confidence)?\nA backup will be saved before writing.`,
    { confirmLabel: 'Fix tracks' }
  ))) return;

  // Read DOM before setting the lock — a null-dereference here must not permanently lock the flag
  const maxCues      = parseInt(document.getElementById('max-cues').value) || 8;
  const barsInterval = parseInt(document.getElementById('bars-interval').value) || 16;
  const startBar     = parseInt(document.getElementById('start-bar').value) || 1;
  const memoryCueMode = document.getElementById('memory-cue-mode').value;
  // Progress goes to the button the user clicked + the health progress bar —
  // the old target (#download-btn) is display:none on the Library tab, so the
  // entire multi-track write used to run with zero visible feedback.
  const progBar = document.getElementById('health-progress-bar');
  const fill    = document.getElementById('health-progress-fill');
  const srcLabel = srcBtn ? srcBtn.textContent : '';
  const setFixProgress = (done) => {
    if (srcBtn) srcBtn.textContent = `Fixing… ${done} / ${trackIds.length}`;
    if (fill) fill.style.width = `${Math.round(100 * done / trackIds.length)}%`;
  };

  _healthFixInProgress = true;
  if (srcBtn) srcBtn.disabled = true;
  if (progBar) {
    progBar.style.display = '';
    progBar._hideTok = (progBar._hideTok || 0) + 1; // cancel any pending delayed-hide
  }
  if (fill) fill.style.width = '0%';
  setFixProgress(0);

  try {
    const r = await fetch('/api/generate-apply-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        track_ids: trackIds,
        mode: 'auto',
        bars_interval: barsInterval,
        start_bar: startBar,
        max_cues: maxCues,
        memory_cue_mode: memoryCueMode,
        overwrite: false,
        dry_run: false,
      }),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const ev = JSON.parse(line.slice(6));
        if (ev.done) finalData = ev;
        else setFixProgress((ev.applied||0) + (ev.skipped||0) + (ev.errors||0));
      }
    }

    if (finalData) {
      const note = finalData.backup_path ? ' — backup saved' : '';
      showToast(`Fixed ${finalData.applied} track(s)${note}`, 'success');
      await scanLibraryHealth();  // rescan to reflect fixes (also resets the progress bar)
    }
  } catch (err) {
    showToast(`Fix failed: ${err.message}`, true);
  } finally {
    _healthFixInProgress = false;
    // On success the fix row is rebuilt by the rescan; on error restore the
    // clicked button so it doesn't stay stuck at "Fixing… N / M".
    if (srcBtn && srcBtn.isConnected) { srcBtn.disabled = false; srcBtn.textContent = srcLabel; }
    if (progBar) progBar.style.display = 'none';
  }
}

// ── Cue Library Tools ─────────────────────────────────────────────────────────

const CUE_COLOR_NAMES = ['—','Pink','Red','Orange','Yellow','Green','Aqua','Blue','Purple'];

function _initCueTools() {
  const opSel = document.getElementById('cue-tools-op');

  // Build slot-color selects for recolor panel
  const slotRow = document.getElementById('cue-recolor-slots');
  const SLOT_LABELS = ['A','B','C','D','E','F','G','H'];
  SLOT_LABELS.forEach((lbl, i) => {
    const item = document.createElement('div');
    item.className = 'slot-color-item';
    const sel = document.createElement('select');
    sel.id = `cue-recolor-slot-${i}`;
    CUE_COLOR_NAMES.forEach((name, ci) => {
      const opt = document.createElement('option');
      opt.value = ci;
      opt.textContent = name;
      sel.appendChild(opt);
    });
    // Defaults matching AutoCue convention
    const defaults = [5,7,2,4,1,6,3,8]; // A=Green,B=Blue,C=Red,D=Yellow,E=Pink,F=Aqua,G=Orange,H=Purple
    sel.value = defaults[i] || 0;
    item.appendChild(document.createTextNode(lbl));
    item.appendChild(sel);
    slotRow.appendChild(item);
  });

  opSel.addEventListener('change', _updateCueToolsParams);
  document.getElementById('cue-tools-run-btn').addEventListener('click', _runCueTools);
  document.getElementById('auto-tag-undo-btn').addEventListener('click', autoTagUndo);
}

function _updateCueToolsParams() {
  const op = document.getElementById('cue-tools-op').value;
  ['rename','recolor','shift','delete-orphan','auto-classify'].forEach(id => {
    document.getElementById(`cue-tools-params-${id}`).style.display = 'none';
  });
  const map = {rename:'rename', recolor:'recolor', shift:'shift', delete_orphan:'delete-orphan', auto_classify:'auto-classify'};
  document.getElementById(`cue-tools-params-${map[op]}`).style.display = 'flex';
  // Show/hide run button label depending on op
  const runBtn = document.getElementById('cue-tools-run-btn');
  runBtn.textContent = op === 'auto_classify' ? 'Tag visible tracks' : 'Run on visible tracks';
}

async function _runCueTools() {
  const op      = document.getElementById('cue-tools-op').value;
  if (op === 'auto_classify') { autoTagTracks(); return; }
  const dryRun  = document.getElementById('cue-tools-dry-run').checked;
  const btn     = document.getElementById('cue-tools-run-btn');
  const statusEl = document.getElementById('cue-tools-status');
  const progBar  = document.getElementById('cue-tools-progress');
  const progFill = document.getElementById('cue-tools-progress-fill');
  const resultEl = document.getElementById('cue-tools-result');
  const trackIds = activeTracks().map(t => parseInt(t.id));
  const total    = trackIds.length;

  if (!total) { showToast('No tracks to process'); return; }

  // Build operation-specific params first — destructive confirms need the params
  // (keep-slot / shift delta) to render their "review unlocks apply" evidence.
  let opParams = {};
  if (op === 'rename') {
    const from = document.getElementById('cue-rename-from').value;
    const to   = document.getElementById('cue-rename-to').value;
    if (!from) { showToast('Enter a cue name to find'); return; }
    opParams = { rename: { from_name: from, to_name: to } };
  } else if (op === 'recolor') {
    const slotColors = {};
    for (let i = 0; i < 8; i++) {
      const v = parseInt(document.getElementById(`cue-recolor-slot-${i}`).value);
      if (v > 0) slotColors[String(i)] = v;  // skip "—" (0 = no change)
    }
    if (!Object.keys(slotColors).length) { showToast('Select at least one slot color'); return; }
    opParams = { recolor: { slot_colors: slotColors } };
  } else if (op === 'shift') {
    const ms = parseInt(document.getElementById('cue-shift-ms').value) || 0;
    if (ms === 0) { showToast('Enter a non-zero shift amount'); return; }
    opParams = { shift: { delta_ms: ms } };
  } else if (op === 'delete_orphan') {
    const keep = parseInt(document.getElementById('cue-keep-slots').value) || 4;
    opParams = { delete_orphan: { keep_slots: keep } };
  }

  // Consent gradient (design-H "review unlocks apply"): destructive writes
  // (delete_orphan / shift, non-dry-run) keep Apply DISABLED until the user
  // reveals what will change. The evidence states the exact blast radius.
  if (!dryRun && (op === 'delete_orphan' || op === 'shift')) {
    const SLOTS = ['A','B','C','D','E','F','G','H'];
    let evidence;
    let confirmLabel;
    if (op === 'delete_orphan') {
      const keep = opParams.delete_orphan.keep_slots;
      const deleted = SLOTS.slice(keep).join(', ') || '(none)';
      evidence =
        `<div class="confirm-evidence-line"><strong>${total}</strong> track${total === 1 ? '' : 's'} · keeping slots <span class="mono">${SLOTS.slice(0, keep).join(', ')}</span></div>` +
        `<div class="confirm-evidence-line danger">Deleting cues in slots <span class="mono">${deleted}</span> wherever present.</div>` +
        `<div class="confirm-evidence-foot">A backup is created before any write — nothing is lost permanently.</div>`;
      confirmLabel = `Delete cues · ${total}`;
    } else {
      const ms = opParams.shift.delta_ms;
      const dir = ms > 0 ? 'later' : 'earlier';
      evidence =
        `<div class="confirm-evidence-line">Shifting every cue <strong>${Math.abs(ms)} ms ${dir}</strong> (<span class="mono">${ms > 0 ? '+' : ''}${ms} ms</span>).</div>` +
        `<div class="confirm-evidence-line">Across <strong>${total}</strong> track${total === 1 ? '' : 's'}.</div>` +
        `<div class="confirm-evidence-foot">A backup is created before any write — nothing is lost permanently.</div>`;
      confirmLabel = `Shift cues · ${total}`;
    }
    const opLabel = op === 'delete_orphan' ? 'delete cues' : 'shift cues';
    if (!(await _confirmDialog(
      `Apply ${opLabel} to ${total} track${total === 1 ? '' : 's'}? Review the change below to unlock apply.`,
      { confirmLabel, danger: true, reviewRequired: true, reviewCount: total, evidence }
    ))) return;
  }

  const abortCtrl = new AbortController();
  _setBtnCancellable(btn, `Running… 0 / ${total}`, abortCtrl);
  statusEl.style.display = '';
  statusEl.textContent = dryRun ? 'Dry run — no changes will be written' : '';
  progBar.style.display = '';
  progFill.style.width = '0%';
  resultEl.style.display = 'none';

  try {
    const r = await fetch('/api/cue-tools-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ operation: op, track_ids: trackIds, dry_run: dryRun, ...opParams }),
      signal: abortCtrl.signal,
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }

    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    abortCtrl.signal.addEventListener('abort', function() { reader.cancel().catch(function(){}); }, { once: true });

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const parts = buf.split('\n\n');
      buf = parts.pop();
      for (const part of parts) {
        for (const line of part.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.done) {
              const s = ev.summary;
              const verb = {rename:'renamed',recolor:'recolored',shift:'shifted',delete_orphan:'deleted'}[op] || 'changed';
              const dryTag = dryRun ? ' (dry run)' : '';
              resultEl.innerHTML = `<strong>${s.cues_changed} cue(s) ${verb}</strong> across ${s.tracks_affected} track(s)${dryTag}<br>
                <span style="color:var(--muted);font-size:12px">${s.cues_skipped} cues skipped · ${s.tracks_processed} tracks scanned${s.backup_path ? ' · backup saved' : ''}</span>`;
              resultEl.style.display = '';
              // Ease the result in — it used to pop via a bare display flip
              resultEl.classList.add('fade-in-up');
              resultEl.addEventListener('animationend', () => resultEl.classList.remove('fade-in-up'), { once: true });
              progFill.style.width = '100%';
              statusEl.style.display = 'none';
            } else {
              _setBtnCancellable(btn, `Running… ${ev.processed} / ${total}`, abortCtrl);
              progFill.style.width = `${Math.round(ev.processed / total * 100)}%`;
            }
          } catch {}
        }
      }
    }
    // flush residual
    for (const line of buf.split('\n')) {
      if (!line.startsWith('data: ')) continue;
      try {
        const ev = JSON.parse(line.slice(6));
        if (ev.done) {
          const s = ev.summary;
          const verb = {rename:'renamed',recolor:'recolored',shift:'shifted',delete_orphan:'deleted'}[op] || 'changed';
          const dryTag = dryRun ? ' (dry run)' : '';
          resultEl.innerHTML = `<strong>${s.cues_changed} cue(s) ${verb}</strong> across ${s.tracks_affected} track(s)${dryTag}<br>
            <span style="color:var(--muted);font-size:12px">${s.cues_skipped} cues skipped · ${s.tracks_processed} tracks scanned${s.backup_path ? ' · backup saved' : ''}</span>`;
          resultEl.style.display = '';
          resultEl.classList.add('fade-in-up');
          resultEl.addEventListener('animationend', () => resultEl.classList.remove('fade-in-up'), { once: true });
          progFill.style.width = '100%';
          statusEl.style.display = 'none';
        }
      } catch {}
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      showToast('Cue tools cancelled');
      statusEl.style.display = 'none';
    } else {
      showToast(`Cue tools failed: ${err.message}`, true);
      statusEl.style.display = 'none';
    }
  } finally {
    _setBtnLoading(btn, false);
    // Let the 100% fill paint before hiding (same completion beat as health scan)
    setTimeout(() => { progBar.style.display = 'none'; }, 400);
  }
}

// AutoCue tag name → CSS colour for track card pills
const AUTO_TAG_COLORS = {
  // DJ Category
  'Warmup':       '#4a9eff',
  'Build':        '#ff9800',
  'Peak':         '#ff4444',
  'After Hours':  '#aa77ff',
  'Closing':      '#4caf50',
  // Vocal
  'Vocal':        '#ff69b4',
  'Instrumental': '#00bcd4',
  // Energy Level
  'High Energy':  '#ff4444',
  'Mid Energy':   '#ffeb3b',
  'Low Energy':   '#4a9eff',
  // Energy Profile
  'Build Track':  '#ff9800',
  'Wave Track':   '#aa77ff',
  'Flat Track':   '#00bcd4',
  'Drop Track':   '#ff69b4',
  // Intro / Outro
  'Long Intro':   '#4caf50',
  'Short Intro':  '#ffeb3b',
  'Long Outro':   '#ff9800',
  'Short Outro':  '#ff69b4',
  // Decade
  '60s':          '#e91e63',
  '70s':          '#ff5722',
  '80s':          '#9c27b0',
  '90s':          '#3f51b5',
  '00s':          '#009688',
  '10s':          '#607d8b',
  '20s':          '#00bcd4',
  // BPM Tier
  '<120 BPM':     '#4a9eff',
  '120–124 BPM':  '#4caf50',
  '125–128 BPM':  '#ffeb3b',
  '129–135 BPM':  '#ff9800',
  '136–144 BPM':  '#ff5722',
  '>144 BPM':     '#ff4444',
  // Play History
  'Never Played':      '#607d8b',
  'Rarely Played':     '#ff9800',
  'Frequently Played': '#4caf50',
};

let _lastAutoTagUndoData = null;

async function autoTagTracks() {
  const btn     = document.getElementById('cue-tools-run-btn');
  const resultEl = document.getElementById('cue-tools-result');
  const dryRun  = document.getElementById('cue-tools-dry-run').checked;
  const trackIds = activeTracks().map(t => parseInt(t.id));
  const total   = trackIds.length;

  const tagTypeMap = {
    'at-category':      'category',
    'at-vocal':         'vocal',
    'at-energy-level':  'energy_level',
    'at-energy-profile':'energy_profile',
    'at-intro-outro':   'intro_outro',
    'at-decade':        'decade',
    'at-bpm-tier':      'bpm_tier',
    'at-play-history':  'play_history',
  };
  const tagTypes = Object.entries(tagTypeMap)
    .filter(([id]) => document.getElementById(id)?.checked)
    .map(([, val]) => val);

  if (!tagTypes.length) { showToast('Select at least one tag type'); return; }
  if (!total) { showToast('No tracks to process'); return; }

  btn.disabled = true;
  btn.textContent = 'Tagging…';
  resultEl.style.display = 'none';

  try {
    const r = await fetch('/api/auto-tag', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: trackIds, tag_types: tagTypes, overwrite: true, dry_run: dryRun }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    const dryTag = dryRun ? ' (dry run)' : '';
    resultEl.innerHTML = `<strong>${d.tagged} track(s) tagged${dryTag}</strong><br>
      <span style="color:var(--muted);font-size:12px">${d.skipped_no_data} skipped (no data) · ${d.errors} errors</span>`;
    resultEl.style.display = '';

    if (!dryRun && d.undo_data) {
      _lastAutoTagUndoData = d.undo_data;
      const undoRow = document.getElementById('auto-tag-undo-row');
      undoRow.style.display = 'flex';
      document.getElementById('auto-tag-undo-status').textContent = '';
    }
  } catch (err) {
    showToast(`Auto-tag failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Tag visible tracks';
  }
}

async function autoTagUndo() {
  if (!_lastAutoTagUndoData) { showToast('Nothing to undo'); return; }
  const btn    = document.getElementById('auto-tag-undo-btn');
  const status = document.getElementById('auto-tag-undo-status');
  btn.disabled = true;
  status.textContent = 'Undoing…';
  try {
    const r = await fetch('/api/auto-tag/undo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ undo_data: _lastAutoTagUndoData }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }
    const d = await r.json();
    status.textContent = `Undone — ${d.removed} removed, ${d.restored} restored`;
    _lastAutoTagUndoData = null;
    document.getElementById('auto-tag-undo-row').style.display = 'none';
  } catch (err) {
    showToast(`Undo failed: ${err.message}`);
    status.textContent = '';
  } finally {
    btn.disabled = false;
  }
}

async function discogsTagTracks() {
  const btn       = document.getElementById('discogs-run-btn');
  const statusEl  = document.getElementById('discogs-status');
  const resultEl  = document.getElementById('discogs-result');
  const fillEl    = document.getElementById('discogs-progress-fill');
  const token     = (document.getElementById('discogs-token')?.value || '').trim();
  const dryRun       = document.getElementById('discogs-dry-run')?.checked ?? false;
  const skipExisting = document.getElementById('discogs-skip-existing')?.checked ?? true;
  const trackIds  = activeTracks().map(t => parseInt(t.id));
  const total     = trackIds.length;

  if (!token) { showToast('Paste your Discogs token first'); return; }
  if (!total)  { showToast('No tracks to process'); return; }

  let tagged = 0, skipped = 0, errors = 0;

  const abortCtrl = new AbortController();
  const _discogsCancelConfirm = () => {
    if (tagged === 0) return true;  // nothing done yet — cancel immediately, no prompt
    return _confirmDialog(
      `Stop Discogs tagging?\n\n${tagged} track${tagged === 1 ? '' : 's'} already tagged — those changes are saved.`,
      { confirmLabel: 'Stop tagging' }
    );
  };
  _setBtnCancellable(btn, `Tagging… 0 / ${total}`, abortCtrl, _discogsCancelConfirm);
  resultEl.textContent = '';
  fillEl.style.width = '0%';

  try {
    const r = await fetch('/api/auto-tag/discogs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: trackIds, token, dry_run: dryRun, skip_existing: skipExisting }),
      signal: abortCtrl.signal,
    });
    if (!r.ok) { const e = await r.json(); throw new Error(e.detail || r.statusText); }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    abortCtrl.signal.addEventListener('abort', function() { reader.cancel().catch(function(){}); }, { once: true });

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const d = JSON.parse(line.slice(5).trim());
        if (d.done) {
          tagged  = d.tagged;
          skipped = d.skipped;
          errors  = d.errors;
          fillEl.style.width = '100%';
        } else {
          const pct = Math.round((d.processed / total) * 100);
          fillEl.style.width = pct + '%';
          tagged = d.tagged ?? tagged;
          errors = d.errors ?? errors;
          _setBtnCancellable(btn, `Tagging… ${d.processed} / ${total}`, abortCtrl, _discogsCancelConfirm);
        }
      }
    }
    if (abortCtrl.signal.aborted) throw new DOMException('Aborted', 'AbortError');

    const label = dryRun ? ' (dry run)' : '';
    resultEl.textContent = `Done${label}: ${tagged} tagged · ${skipped} no results · ${errors} errors`;
    if (!dryRun) showToast(`Discogs tags written for ${tagged} tracks`);
  } catch (err) {
    if (err.name === 'AbortError') {
      resultEl.textContent = `Stopped at ${tagged} tagged · ${skipped} skipped`;
      if (tagged > 0 && !dryRun) showToast(`Discogs tagging stopped — ${tagged} tracks saved`);
      else showToast('Discogs tagging cancelled');
    } else {
      showToast(`Discogs error: ${err.message}`);
      resultEl.textContent = `Error: ${err.message}`;
    }
  } finally {
    _setBtnLoading(btn, false);
  }
}

const _DISCOGS_TOKEN_KEY = 'autocue_discogs_token';

function discogsSaveToken() {
  const inp = document.getElementById('discogs-token');
  const token = (inp?.value || '').trim();
  const saveStatus = document.getElementById('discogs-save-status');
  if (!token) { saveStatus.textContent = 'Paste a token first'; saveStatus.style.color = 'var(--red, #f55)'; return; }
  localStorage.setItem(_DISCOGS_TOKEN_KEY, token);
  saveStatus.textContent = 'Saved — testing…';
  saveStatus.style.color = 'var(--muted)';
  fetch('/api/auto-tag/discogs/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  }).then(r => r.json().then(d => ({ ok: r.ok, d }))).then(({ ok, d }) => {
    if (ok) {
      saveStatus.textContent = `✓ Connected as ${d.username || 'user'}`;
      saveStatus.style.color = 'var(--green)';
    } else {
      saveStatus.textContent = `✗ ${d.detail || 'invalid token'} — token saved`;
      saveStatus.style.color = 'var(--red, #f55)';
    }
  }).catch(err => {
    saveStatus.textContent = `✗ ${err.message}`;
    saveStatus.style.color = 'var(--red, #f55)';
  });
}

function discogsLoadSavedToken() {
  const saved = localStorage.getItem(_DISCOGS_TOKEN_KEY);
  const inp = document.getElementById('discogs-token');
  const saveStatus = document.getElementById('discogs-save-status');
  if (saved) {
    if (inp) inp.value = saved;
    if (saveStatus) { saveStatus.textContent = 'Token loaded from local storage'; saveStatus.style.color = 'var(--muted)'; }
  } else {
    // Try to load from server config (reads .env on the server side)
    fetch('/api/config').then(r => r.ok ? r.json() : null).then(d => {
      if (d && d.discogs_token) {
        if (inp) inp.value = d.discogs_token;
        if (saveStatus) { saveStatus.textContent = 'Token loaded from .env — click Save & Test to persist'; saveStatus.style.color = 'var(--muted)'; }
      }
    }).catch(() => {});
  }
}
