/* AutoCue app.js — P0 T5 split part 4/8: 04-app-chrome.js
 * Classic script (NOT a module): shares globals, loaded in order by
 * docs/index.html. Concatenating 01..08 in order reproduces the original
 * app.js. Do not reorder. See .claude/project/web-ui.md. */
// ── Comment Enrichment ────────────────────────────────────────────────────────

async function enrichComments() {
  const btn      = document.getElementById('ce-run-btn');
  const status   = document.getElementById('ce-status');
  const result   = document.getElementById('ce-result');
  const resultText = document.getElementById('ce-result-text');
  const progBar  = document.getElementById('ce-progress');
  const progFill = document.getElementById('ce-progress-fill');

  const overwrite = document.getElementById('ce-overwrite').checked;
  const dryRun    = document.getElementById('ce-dry-run').checked;
  const ids = filteredTracks().map(i => parsedTracks[i].id);

  if (!ids.length) { showToast('No tracks to enrich', true); return; }

  let enrichedSoFar = 0;
  const abortCtrl = new AbortController();
  const _enrichCancelConfirm = () => {
    if (enrichedSoFar === 0) return true;
    return _confirmDialog(
      `Stop comment enrichment?\n\n${enrichedSoFar} track${enrichedSoFar === 1 ? '' : 's'} already enriched — those changes are saved.`,
      { confirmLabel: 'Stop enriching' }
    );
  };
  _setBtnCancellable(btn, `Enriching… 0 / ${ids.length}`, abortCtrl, _enrichCancelConfirm);
  status.textContent = `0 / ${ids.length}`;
  result.style.display = 'none';
  progBar.style.display = '';
  progFill.style.width = '0%';

  try {
    const r = await fetch('/api/enrich-comments/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: ids, overwrite, dry_run: dryRun }),
      signal: abortCtrl.signal,
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    let finalResult = null;
    await _consumeSSE(r, ev => {
      if (ev.done) {
        finalResult = ev;
        enrichedSoFar = ev.enriched ?? enrichedSoFar;
      } else if (ev.processed != null) {
        const pct = Math.round(ev.processed / ev.total * 100);
        progFill.style.width = pct + '%';
        if (ev.enriched != null) enrichedSoFar = ev.enriched;
        status.textContent = `${ev.processed} / ${ev.total}`;
        _setBtnCancellable(btn, `Enriching… ${ev.processed} / ${ev.total}`, abortCtrl, _enrichCancelConfirm);
      }
    }, abortCtrl.signal);
    progFill.style.width = '100%';
    if (finalResult) {
      const label = dryRun ? ' (dry run)' : '';
      resultText.textContent = `Enriched: ${finalResult.enriched} · Skipped: ${finalResult.skipped} · Errors: ${finalResult.errors}${label}`;
      result.style.display = '';
      status.textContent = '';
      showToast(dryRun ? `Preview: ${finalResult.enriched} tracks would be enriched` : `Enriched ${finalResult.enriched} track comments`);
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      const saved = enrichedSoFar > 0 && !dryRun;
      status.textContent = saved ? `Stopped — ${enrichedSoFar} enriched` : 'Cancelled';
      if (saved) showToast(`Enrichment stopped — ${enrichedSoFar} tracks saved`);
      else showToast('Comment enrichment cancelled');
    } else {
      status.textContent = `Error: ${err.message}`;
      showToast(`Enrichment failed: ${err.message}`, true);
    }
  } finally {
    _setBtnLoading(btn, false);
    setTimeout(() => { progBar.style.display = 'none'; progFill.style.width = '0%'; if (!status.textContent.includes('enriched')) status.textContent = ''; }, 2000);
  }
}

async function previewComment() {
  const sel   = document.getElementById('ce-preview-track');
  const pDiv  = document.getElementById('ce-preview-result');
  const pCur  = document.getElementById('ce-preview-current');
  const pAfter = document.getElementById('ce-preview-after');
  const trackId = parseInt(sel.value, 10);
  if (!trackId) { showToast('Select a track to preview', true); return; }

  try {
    const r = await fetch('/api/enrich-comments/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_id: trackId }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const d = await r.json();
    pCur.textContent  = d.current_comment || '(empty)';
    pAfter.textContent = d.preview || '(no enrichment available)';
    pDiv.style.display = '';
  } catch (err) {
    showToast(`Preview failed: ${err.message}`, true);
  }
}

async function colorTracksByBpm() {
  const btn = document.getElementById('color-by-bpm-btn');
  const trackIds = activeTracks().map(t => parseInt(t.id));
  const total = trackIds.length;

  const abortCtrl = new AbortController();
  _setBtnCancellable(btn, `Coloring… 0 / ${total}`, abortCtrl);

  try {
    const skipColored = document.getElementById('skip-colored-cb')?.checked ?? false;
    const r = await fetch('/api/color-tracks-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: trackIds, dry_run: false, skip_colored: skipColored }),
      signal: abortCtrl.signal,
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }

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
        if (ev.done) {
          finalData = ev;
        } else {
          _setBtnCancellable(btn, `Coloring… ${ev.colored + ev.skipped} / ${ev.total}`, abortCtrl);
        }
      }
    }

    if (finalData) {
      const backupNote = finalData.backup_path ? ' — backup saved to ~/.autocue/backups/' : '';
      showToast(`Colored ${finalData.colored} track(s) by BPM${backupNote}`);
      document.getElementById('bpm-legend').classList.add('visible');
    }
  } catch (err) {
    if (err.name === 'AbortError') {
      showToast('Color by BPM cancelled');
    } else {
      showToast(`Color by BPM failed: ${err.message}`);
    }
  } finally {
    _setBtnLoading(btn, false);
  }
}

async function applyToRekordbox() {
  const barsInterval = parseInt(document.getElementById('bars-interval').value) || 16;
  const startBar = parseInt(document.getElementById('start-bar').value) || 1;
  const maxCues = parseInt(document.getElementById('max-cues').value) || 8;
  const memoryCueMode = document.getElementById('memory-cue-mode').value;
  const addFillCues = document.getElementById('add-fill-cues').checked;
  const tracks = activeTracks();

  // ── P2 proposal-organ Apply gate ────────────────────────────────────────
  // When the workbench is active AND proposals exist (pendingCues non-empty),
  // the write is restricted to approved∩pending track-ids. ACBridge.approved-
  // ApplyIds() returns null unless BOTH the v2 proposals module is loaded AND
  // there are pending cues — so flag-off / no-pending falls straight through to
  // the existing activeTracks() behaviour, untouched.
  let trackIds = tracks.map(t => parseInt(t.id));
  const wbActive = document.body.classList.contains('wb-active');
  const hasPending = (typeof pendingCues !== 'undefined') && Object.keys(pendingCues || {}).length > 0;
  if (wbActive && hasPending && window.ACBridge && typeof window.ACBridge.approvedApplyIds === 'function') {
    const approvedIds = window.ACBridge.approvedApplyIds();
    if (approvedIds !== null) {
      if (!approvedIds.length) {
        showToast('Approve at least one proposed track, or clear the preview', true);
        return;
      }
      trackIds = approvedIds;
    }
  }
  const trackCount = trackIds.length;

  const btn = document.getElementById('download-btn');
  // #download-btn lives in the Pages-mode bar, which is display:none in local
  // mode — mirror every state change onto the action-bar button the user
  // actually clicked, or the whole apply runs with zero visible feedback.
  const abApply = document.getElementById('action-bar-apply');
  const applyBtns = [btn, abApply].filter(Boolean);
  const setApplyText = (t) => applyBtns.forEach(b => { b.textContent = t; });
  setApplyText(`Applying… 0 / ${trackCount}`);
  applyBtns.forEach(b => { b.disabled = true; });

  const body = JSON.stringify({
    track_ids: trackIds,
    mode: analysisMode === 'phrase' ? 'auto' : 'bar',
    bars_interval: barsInterval,
    start_bar: startBar,
    max_cues: maxCues,
    memory_cue_mode: memoryCueMode,
    add_fill_cues: addFillCues,
    overwrite: !document.getElementById('skip-existing-cues').checked,
    phrase_only: phraseOnlyFilter,
    dry_run: false,
  });

  try {
    const r = await fetch('/api/generate-apply-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }

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
        if (ev.done) {
          finalData = ev;
        } else {
          setApplyText(`Applying… ${(ev.applied||0) + (ev.skipped||0) + (ev.errors||0)} / ${ev.total}`);
        }
      }
    }

    if (finalData) {
      pendingCues = {};
      setStep(4);
      if (finalData.backup_path) {
        lastAppliedBackupFilename = finalData.backup_path.split('/').pop();
        document.getElementById('undo-btn').style.display = '';
      }
      const backupNote = finalData.backup_path ? ' · backup saved' : '';
      const skippedNote = finalData.skipped > 0 ? `, ${finalData.skipped} skipped (already cued)` : '';
      showToast(`Applied hot cues to ${finalData.applied} track${finalData.applied !== 1 ? 's' : ''}${skippedNote}${backupNote}`, 'success');
      // Success flash: turn both apply buttons green for 2.5s
      applyBtns.forEach(b => {
        b.textContent = `✓ ${finalData.applied} tracks`;
        b.style.background = 'var(--green)';
        b.style.color = '#000';
        b.style.borderColor = 'var(--green)';
      });
      setTimeout(() => {
        applyBtns.forEach(b => {
          b.textContent = 'Apply to Rekordbox';
          b.style.background = '';
          b.style.color = '';
          b.style.borderColor = '';
        });
      }, 2500);
      // Refresh cards so existing-cue counts/chips reflect the write —
      // without this the tracks just written still render as un-cued.
      if (localMode) await loadTracksFromServer(activePlaylistId ?? null).catch(() => {});
    }
  } catch (err) {
    showToast(`Error applying cues: ${err.message}`, true);
  } finally {
    applyBtns.forEach(b => {
      b.disabled = false;
      if (!b.textContent.startsWith('✓')) b.textContent = 'Apply to Rekordbox';
    });
  }
}

// ── Tab navigation ────────────────────────────────────────────────────────────
const TAB_CONTENTS = {
  cues: 'cues-tab-content',
  library: 'library-tab-content',
  discover: 'discover-tab-content',
};
function switchTab(name) {
  Object.entries(TAB_CONTENTS).forEach(([tab, id]) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (tab === name) {
      el.style.display = '';
      el.classList.remove('tab-entering');
      void el.offsetWidth; // force reflow to restart animation
      el.classList.add('tab-entering');
    } else {
      el.style.display = 'none';
    }
  });
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.toggle('active', b.id === 'tab-' + name);
  });
  // Hide the Cues-specific sticky bottom bar on every non-Cues tab — it
  // shows "Apply to Rekordbox" + "Color tracks by BPM" + "Delete all cues"
  // which only make sense on the Cues tab. UX audit Issue 7 (escalated to
  // High by the grill: highest-reach finding, every tab × every visit).
  const dlBar = document.getElementById('download-bar');
  if (dlBar) {
    if (name === 'cues') dlBar.classList.remove('hidden-by-tab');
    else dlBar.classList.add('hidden-by-tab');
  }
  // Tag <body> so CSS can target tab-specific layout adjustments.
  document.body.setAttribute('data-active-tab', name);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
document.getElementById('tab-cues').addEventListener('click', () => switchTab('cues'));
document.getElementById('tab-library').addEventListener('click', () => switchTab('library'));
document.getElementById('tab-discover').addEventListener('click', () => switchTab('discover'));

// ── App status row ─────────────────────────────────────────────────────────
let _lastScanAt = null;
function _formatScanAge(at) {
  if (!at) return 'No scans yet';
  const ms = Date.now() - at;
  if (ms < 60_000) return 'Last scan just now';
  const m = Math.round(ms / 60_000);
  if (m < 60) return `Last scan ${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `Last scan ${h}h ago`;
  return `Last scan ${Math.round(h / 24)}d ago`;
}
function updateAppStatus({ connected, trackCount, rekordboxRunning, didScan } = {}) {
  const bar = document.getElementById('app-status');
  if (!bar) return;
  bar.classList.toggle('visible', !!connected);
  if (!connected) return;
  if (typeof trackCount === 'number') {
    const countItem = document.getElementById('status-count');
    const changed = countItem.dataset.lastCount !== String(trackCount);
    countItem.dataset.lastCount = String(trackCount);
    countItem.innerHTML =
      `<span class="status-text"><strong>${trackCount.toLocaleString()}</strong> tracks</span>`;
    if (changed) {
      // Tick the count when it actually changes — silent swaps read as static chrome
      countItem.classList.remove('count-pop');
      void countItem.offsetWidth;
      countItem.classList.add('count-pop');
    }
  }
  if (didScan) _lastScanAt = Date.now();
  document.getElementById('status-scan').innerHTML =
    `<span class="status-text">${_formatScanAge(_lastScanAt)}</span>`;
  const rb = document.getElementById('status-rb');
  if (rekordboxRunning === true) {
    rb.innerHTML = '<span class="status-dot status-warn"></span><span class="status-text">Rekordbox open</span>';
  } else if (rekordboxRunning === false) {
    rb.innerHTML = '<span class="status-dot status-ok"></span><span class="status-text">Rekordbox closed ✓</span>';
  } else {
    rb.innerHTML = '<span class="status-dot"></span><span class="status-text">Rekordbox ?</span>';
  }
}
// Refresh scan-age label every minute so the relative time stays accurate.
setInterval(() => {
  if (document.getElementById('app-status')?.classList.contains('visible')) {
    document.getElementById('status-scan').innerHTML =
      `<span class="status-text">${_formatScanAge(_lastScanAt)}</span>`;
  }
}, 60_000);


// F2: Playlist filter change
document.getElementById('playlist-select').addEventListener('change', () => {
  const val = document.getElementById('playlist-select').value;
  activePlaylistId = val ? parseInt(val) : null;
  pendingCues = {};
  healthData = {};    // stale health chips don't apply across playlist boundaries
  loadTracksFromServer(activePlaylistId);
});

// F3: Restore backup UI — checkbox list
function _populateChecklist(backups) {
  const list = document.getElementById('backup-checklist');
  list.innerHTML = '';
  const allCb = document.getElementById('backup-select-all');
  allCb.checked = false;
  allCb.indeterminate = false;
  _updateSelectionCount();
  for (const b of backups) {
    const row = document.createElement('div');
    row.className = 'backup-row';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = b.filename;
    cb.addEventListener('change', () => {
      _updateSelectionCount();
      _resetRestoreBtn();
      _resetDeleteBackupBtn();
    });
    const name = document.createElement('span');
    name.className = 'backup-name';
    name.textContent = b.created_at;
    const size = document.createElement('span');
    size.className = 'backup-size';
    size.textContent = b.size_mb + ' MB';
    row.appendChild(cb);
    row.appendChild(name);
    row.appendChild(size);
    row.addEventListener('click', e => {
      if (e.target === cb) return;
      cb.checked = !cb.checked;
      cb.dispatchEvent(new Event('change'));
    });
    list.appendChild(row);
  }
}

function _updateSelectionCount() {
  const checkboxes = document.querySelectorAll('#backup-checklist input[type=checkbox]');
  const checked = [...checkboxes].filter(c => c.checked);
  const count = checked.length;
  const total = checkboxes.length;
  const allCb = document.getElementById('backup-select-all');
  allCb.checked = count === total && total > 0;
  allCb.indeterminate = count > 0 && count < total;
  const countSpan = document.getElementById('backup-select-count');
  countSpan.textContent = count > 0 ? `${count} selected` : '';
}

function _checkedBackups() {
  return [...document.querySelectorAll('#backup-checklist input[type=checkbox]:checked')].map(c => c.value);
}

document.getElementById('restore-btn').addEventListener('click', async () => {
  const bar = document.getElementById('restore-bar');
  const rbtn = document.getElementById('restore-btn');
  if (bar.style.display !== 'none') { bar.style.display = 'none'; return; }
  // Spinner while /api/backups loads — the click used to give zero feedback
  _setBtnLoading(rbtn, true, 'Loading backups…');
  try {
    const backups = await fetch('/api/backups').then(r => r.json());
    if (backups.length === 0) { showToast('No backups found'); return; }
    _populateChecklist(backups);
    _resetRestoreBtn();
    _resetDeleteBackupBtn();
  } catch (e) { showToast('Could not load backups', true); return; }
  finally { _setBtnLoading(rbtn, false); }
  bar.style.display = 'flex';
  bar.classList.add('fade-in-up');
  bar.addEventListener('animationend', () => bar.classList.remove('fade-in-up'), { once: true });
});

document.getElementById('backup-select-all').addEventListener('change', function() {
  document.querySelectorAll('#backup-checklist input[type=checkbox]').forEach(c => { c.checked = this.checked; });
  _updateSelectionCount();
  _resetRestoreBtn();
  _resetDeleteBackupBtn();
});

function _resetRestoreBtn() {
  const btn = document.getElementById('restore-confirm-btn');
  btn.textContent = 'Restore';
  btn.style.outline = '';
  delete btn.dataset.armed;
}

function _resetDeleteBackupBtn() {
  const btn = document.getElementById('delete-backup-btn');
  btn.textContent = 'Delete selected';
  btn.style.outline = '';
  delete btn.dataset.armed;
}

document.getElementById('restore-cancel-btn').addEventListener('click', () => {
  _resetRestoreBtn();
  _resetDeleteBackupBtn();
  document.getElementById('restore-bar').style.display = 'none';
});

document.getElementById('delete-backup-btn').addEventListener('click', async () => {
  const selected = _checkedBackups();
  if (selected.length === 0) { showToast('Select at least one backup to delete'); return; }
  const btn = document.getElementById('delete-backup-btn');
  if (btn.dataset.armed !== 'true') {
    btn.dataset.armed = 'true';
    btn.textContent = `Delete ${selected.length} backup${selected.length > 1 ? 's' : ''} ⚠`;
    btn.style.outline = '2px solid #ff4444';
    return;
  }
  _resetDeleteBackupBtn();
  btn.disabled = true; btn.textContent = 'Deleting…';
  let deletedCount = 0;
  try {
    for (const filename of selected) {
      const r = await fetch(`/api/backups/${encodeURIComponent(filename)}`, { method: 'DELETE' });
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
      deletedCount++;
    }
    showToast(`Deleted ${deletedCount} backup${deletedCount > 1 ? 's' : ''}`);
    const backups = await fetch('/api/backups').then(r => r.json());
    if (backups.length === 0) {
      document.getElementById('restore-bar').style.display = 'none';
    } else {
      _populateChecklist(backups);
    }
  } catch (e) { showToast(`Delete failed: ${e.message}`); }
  finally { btn.disabled = false; btn.textContent = 'Delete selected'; }
});

document.getElementById('restore-confirm-btn').addEventListener('click', async () => {
  const selected = _checkedBackups();
  if (selected.length !== 1) { showToast('Select exactly one backup to restore'); return; }
  const filename = selected[0];
  const btn = document.getElementById('restore-confirm-btn');
  if (btn.dataset.armed !== 'true') {
    btn.dataset.armed = 'true';
    btn.textContent = 'Yes, replace database ⚠';
    btn.style.outline = '2px solid #ff4444';
    return;
  }
  _resetRestoreBtn();
  btn.disabled = true; btn.textContent = 'Restoring…';
  try {
    const r = await fetch('/api/restore', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename }),
    });
    const resp = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(resp.detail || r.statusText);
    showToast(resp.message + ' — reloading tracks…');
    document.getElementById('restore-bar').style.display = 'none';
    pendingCues = {};
    await loadTracksFromServer(activePlaylistId);
  } catch (e) { showToast(`Restore failed: ${e.message}`); }
  finally { btn.disabled = false; btn.textContent = 'Restore'; }
});

// TASK-036 — Search input debounced through requestIdleCallback so the
// browser defers the filter recompute to idle time instead of competing
// with input event paint. Falls back to setTimeout where rIC isn't
// supported (jsdom in tests; older Safari).
let _searchTimer = null;
let _searchRic = null;
const _scheduleSearchRecompute = (fn) => {
  if (typeof window.requestIdleCallback === 'function') {
    if (_searchRic !== null) window.cancelIdleCallback(_searchRic);
    _searchRic = window.requestIdleCallback(() => { _searchRic = null; fn(); }, { timeout: 80 });
    return;
  }
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(fn, 80);
};
document.getElementById('search-input').addEventListener('input', e => {
  const value = e.target.value.trim();
  _scheduleSearchRecompute(() => {
    searchQuery = value;
    updateActiveFiltersChip();
    AppState.signal('filters');
  });
});

// F6: Phrase-only filter
document.getElementById('phrase-only-cb').addEventListener('change', e => {
  phraseOnlyFilter = e.target.checked;
  updateActiveFiltersChip();
  AppState.signal('filters');
});

// Beat-grid-only filter (tracks with BPM > 0 — i.e. analyzed in Rekordbox)
document.getElementById('audio-only-cb').addEventListener('change', e => {
  _audioOnlyFilter = e.target.checked;
  updateActiveFiltersChip();
  AppState.signal('filters');
  if (_audioOnlyFilter) _probeAudioForVisibleTracks();
});

document.getElementById('beats-only-cb').addEventListener('change', e => {
  beatsOnlyFilter = e.target.checked;
  updateActiveFiltersChip();
  AppState.signal('filters');
});

// Genre filter is handled by chip click listeners created in populateGenreChips()

// Rating / plays / last-played / tag filters
document.getElementById('rating-filter').addEventListener('change', e => {
  ratingFilter = parseInt(e.target.value) || 0;
  updateActiveFiltersChip();
  AppState.signal('filters');
});
document.getElementById('plays-filter').addEventListener('change', e => {
  playsFilter = e.target.value;
  updateActiveFiltersChip();
  AppState.signal('filters');
});
document.getElementById('lastplayed-filter').addEventListener('change', e => {
  lastPlayedFilter = e.target.value;
  updateActiveFiltersChip();
  AppState.signal('filters');
});
// Tag filter popup
(function() {
  const popup = document.getElementById('tag-filter-popup');
  const btn   = document.getElementById('tag-filter-btn');

  function toggleTag(tag) {
    if (myTagFilters.has(tag)) myTagFilters.delete(tag);
    else myTagFilters.add(tag);
    updateTagFilterUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  }

  function updateTagFilterUI() {
    document.querySelectorAll('#tag-filter-chips .tf-chip').forEach(b => {
      b.classList.toggle('selected', myTagFilters.has(b.dataset.tag));
    });
    if (myTagFilters.size === 0) {
      btn.textContent = 'Tags: Any ▾';
      btn.style.borderColor = '';
      btn.style.color = '';
    } else {
      btn.textContent = `Tags: ${myTagFilters.size} ▾`;
      btn.style.borderColor = 'var(--green)';
      btn.style.color = 'var(--green)';
    }
  }

  function openPopup() {
    popup.style.display = 'flex';
    const rect = btn.getBoundingClientRect();
    const popupH = 300;
    // Flip above if not enough space below
    if (rect.bottom + popupH + 8 > window.innerHeight) {
      popup.style.top  = (rect.top - popupH - 4) + 'px';
    } else {
      popup.style.top  = (rect.bottom + 4) + 'px';
    }
    popup.style.left = rect.left + 'px';
    requestAnimationFrame(() => {
      const pr = popup.getBoundingClientRect();
      if (pr.right > window.innerWidth - 8)
        popup.style.left = (window.innerWidth - pr.width - 8) + 'px';
      if (pr.left < 8) popup.style.left = '8px';
    });
  }

  function closePopup() { popup.style.display = 'none'; }

  // Called from popup chips — keep popup open for multi-select
  // Called from track card pills — close popup immediately
  window._toggleTagFilter = function(tag, fromCard) {
    toggleTag(tag);
    if (fromCard) closePopup();
  };
  window._updateTagFilterUI = updateTagFilterUI;

  document.getElementById('tf-clear-btn').addEventListener('click', () => {
    myTagFilters.clear();
    updateTagFilterUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  });

  document.getElementById('tag-search').addEventListener('input', function() {
    const q = this.value.toLowerCase();
    document.querySelectorAll('#tag-filter-chips .tf-chip').forEach(chip => {
      chip.style.display = chip.dataset.tag.toLowerCase().includes(q) ? '' : 'none';
    });
  });

  btn.addEventListener('click', e => {
    e.stopPropagation();
    if (popup.style.display === 'flex') closePopup(); else openPopup();
  });

  document.addEventListener('click', e => {
    if (popup.style.display === 'flex' && !popup.contains(e.target) && e.target !== btn)
      closePopup();
  });
})();

// Genre filter popup
(function() {
  const popup = document.getElementById('genre-filter-popup');
  const btn   = document.getElementById('genre-filter-btn');

  function toggleGenre(g) {
    if (genreFilters.has(g)) genreFilters.delete(g);
    else genreFilters.add(g);
    updateGenreFilterUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  }

  function updateGenreFilterUI() {
    document.querySelectorAll('#genre-filter-chips .genre-chip').forEach(function(b) {
      b.classList.toggle('active', genreFilters.has(b.dataset.genre));
    });
    if (!btn) return;
    if (genreFilters.size === 0) {
      btn.textContent = 'Genre: Any ▾';
      btn.style.borderColor = '';
      btn.style.color = '';
    } else {
      btn.textContent = 'Genre: ' + genreFilters.size + ' ▾';
      btn.style.borderColor = 'var(--green)';
      btn.style.color = 'var(--green)';
    }
  }

  function openPopup() {
    popup.style.display = 'flex';
    const rect = btn.getBoundingClientRect();
    if (rect.bottom + 308 > window.innerHeight) {
      popup.style.top = (rect.top - 304) + 'px';
    } else {
      popup.style.top = (rect.bottom + 4) + 'px';
    }
    popup.style.left = rect.left + 'px';
    requestAnimationFrame(function() {
      const pr = popup.getBoundingClientRect();
      if (pr.right > window.innerWidth - 8) popup.style.left = (window.innerWidth - pr.width - 8) + 'px';
      if (pr.left < 8) popup.style.left = '8px';
    });
  }

  function closePopup() { popup.style.display = 'none'; }

  window._updateGenreFilterUI = updateGenreFilterUI;

  document.getElementById('genre-filter-chips').addEventListener('click', function(e) {
    var chip = e.target.closest('.genre-chip');
    if (chip) toggleGenre(chip.dataset.genre);
  });

  document.getElementById('genre-clear-btn').addEventListener('click', function() {
    genreFilters.clear();
    updateGenreFilterUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  });

  document.getElementById('genre-search').addEventListener('input', function() {
    var q = this.value.toLowerCase();
    document.querySelectorAll('#genre-filter-chips .genre-chip').forEach(function(chip) {
      chip.style.display = chip.dataset.genre.toLowerCase().includes(q) ? '' : 'none';
    });
  });

  btn.addEventListener('click', function(e) {
    e.stopPropagation();
    if (popup.style.display === 'flex') closePopup(); else openPopup();
  });

  document.addEventListener('click', function(e) {
    if (popup.style.display === 'flex' && !popup.contains(e.target) && e.target !== btn)
      closePopup();
  });
})();

function updateActiveFiltersChip() {
  const active = ratingFilter > 0 || playsFilter !== 'all' || lastPlayedFilter !== 'all' || myTagFilters.size > 0 || selectedKeys.size > 0 || phraseOnlyFilter || beatsOnlyFilter || _audioOnlyFilter || searchQuery || genreFilters.size > 0;
  const chip = document.getElementById('active-filters-chip');
  if (chip) chip.classList.toggle('visible', !!active);
}

function clearAllFilters() {
  searchQuery = '';
  phraseOnlyFilter = false;
  beatsOnlyFilter = false;
  _audioOnlyFilter = false;
  const ac = document.getElementById('audio-only-cb');
  if (ac) ac.checked = false;
  ratingFilter = 0;
  playsFilter = 'all';
  lastPlayedFilter = 'all';
  myTagFilters.clear();
  selectedKeys.clear();
  const si = document.getElementById('search-input');
  if (si) si.value = '';
  const pc = document.getElementById('phrase-only-cb');
  if (pc) pc.checked = false;
  const bc = document.getElementById('beats-only-cb');
  if (bc) bc.checked = false;
  const rf = document.getElementById('rating-filter');
  if (rf) rf.value = '0';
  const pf = document.getElementById('plays-filter');
  if (pf) pf.value = 'all';
  const lpf = document.getElementById('lastplayed-filter');
  if (lpf) lpf.value = 'all';
  document.querySelectorAll('#tag-filter-chips .tf-chip').forEach(b => b.classList.remove('selected'));
  const tfb = document.getElementById('tag-filter-btn');
  if (tfb) { tfb.textContent = 'Tags: Any ▾'; tfb.style.borderColor = ''; tfb.style.color = ''; }
  // Update Camelot key buttons
  document.querySelectorAll('#camelot-grid button').forEach(b => b.classList.remove('selected'));
  const kfb = document.getElementById('key-filter-btn');
  if (kfb) { kfb.textContent = 'Key: Any ▾'; kfb.classList.remove('active'); }
  genreFilters.clear();
  if (window._updateGenreFilterUI) window._updateGenreFilterUI();
  updateActiveFiltersChip();
  AppState.signal('filters'); // coalesces all the above mutations into one render
}

// F7: Bulk selection — Select all / Deselect all
document.getElementById('select-all-btn').addEventListener('click', () => {
  for (const i of filteredTracks()) selectedTrackIds.add(parsedTracks[i].id);
  AppState.signal('filters');
  updateSelectionBar();
});
document.getElementById('deselect-all-btn').addEventListener('click', () => {
  selectedTrackIds.clear();
  AppState.signal('filters');
  updateSelectionBar();
});

// F8: Undo last apply
document.getElementById('undo-btn').addEventListener('click', async () => {
  if (!lastAppliedBackupFilename) return;
  if (!(await _confirmDialog(
    `Undo last apply? This restores "${lastAppliedBackupFilename}" and replaces your current Rekordbox database.`,
    { confirmLabel: 'Undo apply', danger: true }
  ))) return;
  const btn = document.getElementById('undo-btn');
  btn.disabled = true; btn.textContent = 'Undoing…';
  try {
    const r = await fetch('/api/restore', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: lastAppliedBackupFilename }),
    });
    const resp = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(resp.detail || r.statusText);
    lastAppliedBackupFilename = null;
    btn.style.display = 'none';
    pendingCues = {};
    showToast('Undo successful — tracks reloaded');
    await loadTracksFromServer(activePlaylistId);
  } catch (e) { showToast(`Undo failed: ${e.message}`); }
  finally { btn.disabled = false; btn.textContent = '↩ Undo last apply'; }
});
