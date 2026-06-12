/* AutoCue app.js — P0 T5 split part 7/8: 07-helpers-events.js
 * Classic script (NOT a module): shares globals, loaded in order by
 * docs/index.html. Concatenating 01..08 in order reproduces the original
 * app.js. Do not reorder. See .claude/project/web-ui.md. */
// ── XML export ─────────────────────────────────────────────────────────────────
function buildOutputXml() {
  if (!parsedDoc || !parsedTracks.length) return '';
  const { barsInterval, startBar, maxCues } = getSettings();
  const skipExisting = document.getElementById('skip-existing-cues').checked;
  const outDoc = parsedDoc.cloneNode(true);

  for (const track of parsedTracks) {
    if (skipExisting && track.existingHotCues > 0) continue;

    let cues = [];
    if (analysisMode === 'phrase' && phraseCueState[track.id]?.length) {
      cues = phraseCueState[track.id].map(c => ({
        slot: c.slot, posSec: c.position_ms / 1000, label: c.label, isPhrase: true,
        name: c.name || '', confidence: c.confidence ?? 1.0,
      }));
    } else {
      cues = generateCues(track, barsInterval, startBar, maxCues);
    }

    const trackEl = outDoc.querySelector(`COLLECTION > TRACK[TrackID="${track.id}"]`);
    if (!trackEl) continue;

    const usedSlots = new Set(cues.map(c => c.slot));
    for (const pm of [...trackEl.querySelectorAll('POSITION_MARK')]) {
      const num = parseInt(pm.getAttribute('Num'), 10);
      if (num >= 0 && usedSlots.has(num)) pm.remove();
    }
    for (const cue of cues) {
      if (cue.slot < 0) continue;  // memory cues have no POSITION_MARK representation
      const pm = outDoc.createElement('POSITION_MARK');
      const cueName = cue.name || cue.label || `Bar ${startBar + cue.slot * barsInterval}`;
      pm.setAttribute('Name',  cueName);
      pm.setAttribute('Type',  '0');
      pm.setAttribute('Start', cue.posSec.toFixed(3));
      pm.setAttribute('Num',   String(cue.slot));
      const c = (cue.isPhrase && PHRASE_COLORS[cue.label])
        ? PHRASE_COLORS[cue.label]
        : (CUE_COLORS[cue.slot] ?? CUE_COLORS[0]);
      pm.setAttribute('Red',   String(c.r));
      pm.setAttribute('Green', String(c.g));
      pm.setAttribute('Blue',  String(c.b));
      trackEl.appendChild(pm);
    }
  }

  const serializer = new XMLSerializer();
  return '<?xml version="1.0" encoding="UTF-8"?>\n' +
    serializer.serializeToString(outDoc).replace(/^<\?xml[^?]*\?>\n?/, '');
}

// ── File load (XML) ────────────────────────────────────────────────────────────
function showDropError(msg) {
  document.querySelector('#drop-zone .drop-error')?.remove();
  const el = document.createElement('p');
  el.className = 'drop-error';
  el.textContent = msg;
  document.getElementById('drop-zone').appendChild(el);
}

function handleFile(file) {
  if (!file) return;
  document.querySelector('#drop-zone .drop-error')?.remove();
  const reader = new FileReader();
  reader.onload = e => {
    const result = parseRekordboxXml(e.target.result);
    if (result.error) { showDropError(result.error); return; }

    const { doc, tracks } = result;
    parsedDoc    = doc;
    _setParsedTracks(tracks);
    _energyCache = {};           // D4 fix: clear stale curves on XML reload
    _cardMap.clear();            // C: force full rebuild on XML reload
    _albumGroupCache.clear();    // #172: album cache wraps cards, drop when cards drop
    _cardSettingsFingerprint = '';
    if (Virtualizer.isAttached()) Virtualizer.detach();
    originalXmlText = e.target.result;

    const withExisting = tracks.filter(t => t.existingHotCues > 0).length;
    const info = document.getElementById('existing-cues-info');
    if (withExisting > 0) {
      document.getElementById('existing-cues-label').innerHTML =
        `<strong>${withExisting}</strong> of ${tracks.length} tracks already have hot cues`;
      info.style.display = 'flex';
    } else {
      info.style.display = 'none';
    }

    document.getElementById('settings-section').classList.add('visible');
    document.getElementById('tracks-section').classList.add('visible');
    document.getElementById('download-bar').classList.add('visible');
    document.getElementById('audio-drop-section').classList.add('visible');
    // Trigger sticky shadow check now that the section is visible
    requestAnimationFrame(() => { if (window._checkStickyHeader) window._checkStickyHeader(); });
    document.getElementById('backup-bar').style.display = '';
    document.getElementById('analysis-mode-bar').style.display = 'flex';

    setStep(3);
    renderTracks();
    updateOverwriteWarning();
  };
  reader.readAsText(file);
}

// ── Steps ──────────────────────────────────────────────────────────────────────
function setStep(n) {
  [1,2,3,4].forEach(i => {
    const el = document.getElementById(`step-${i}`);
    el.classList.remove('active', 'done');
    if (i < n) el.classList.add('done');
    if (i === n) el.classList.add('active');
  });
}

// ── Toast stack ────────────────────────────────────────────────────────────────
// type: true = error, 'success' = green success pill, falsy = neutral.
function showToast(msg, type) {
  const stack = document.getElementById('toast-stack');
  if (!stack) return;
  // Cap stack at 3 — dismiss oldest first
  while (stack.children.length >= 3) _dismissToast(stack.firstChild);
  const el = document.createElement('div');
  el.className = 'toast-item' + (type === 'success' ? ' toast-success' : type ? ' toast-error' : '');
  el.textContent = msg;
  stack.appendChild(el);
  const timer = setTimeout(() => _dismissToast(el), 2800);
  el.addEventListener('click', () => { clearTimeout(timer); _dismissToast(el); });
}
function _dismissToast(el) {
  if (!el || !el.parentNode) return;
  el.classList.add('toast-out');
  el.addEventListener('animationend', () => el.remove(), { once: true });
}

// ── Styled confirm dialog ───────────────────────────────────────────────────────
// Async replacement for window.confirm. Mirrors the duplicates-delete modal's
// safety choreography: primary disabled 250ms after open (defeats accidental
// Enter), Cancel default-focused, two-button focus trap, Esc/backdrop cancel.
// opts: { confirmLabel?: string, danger?: boolean }
function _confirmDialog(message, opts = {}) {
  return new Promise((resolve) => {
    document.getElementById('app-confirm-modal')?.remove();
    const overlay = document.createElement('div');
    overlay.id = 'app-confirm-modal';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:1300;display:flex;align-items:center;justify-content:center;';
    const box = document.createElement('div');
    box.className = 'fade-in-up';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.style.cssText = 'background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px;min-width:340px;max-width:460px;';
    const msg = document.createElement('div');
    msg.style.cssText = 'font-size:13px;line-height:1.6;margin-bottom:16px;white-space:pre-line;';
    msg.textContent = message;
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;';
    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'secondary-btn';
    cancelBtn.textContent = 'Cancel';
    const goBtn = document.createElement('button');
    goBtn.className = 'primary';
    goBtn.textContent = opts.confirmLabel || 'Confirm';
    if (opts.danger) goBtn.style.cssText = 'background:var(--danger);border-color:var(--danger);color:#fff;';
    goBtn.disabled = true;
    setTimeout(() => { goBtn.disabled = false; }, 250);
    const prevFocus = document.activeElement;
    const done = (val) => {
      document.removeEventListener('keydown', onKey, true);
      overlay.remove();
      if (prevFocus && typeof prevFocus.focus === 'function') { try { prevFocus.focus(); } catch (_) {} }
      resolve(val);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); done(false); }
      else if (e.key === 'Tab') {
        e.preventDefault();
        (document.activeElement === cancelBtn ? goBtn : cancelBtn).focus();
      }
    };
    document.addEventListener('keydown', onKey, true);
    cancelBtn.addEventListener('click', () => done(false));
    goBtn.addEventListener('click', () => done(true));
    overlay.addEventListener('click', (e) => { if (e.target === overlay) done(false); });
    row.appendChild(cancelBtn);
    row.appendChild(goBtn);
    box.appendChild(msg);
    box.appendChild(row);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    setTimeout(() => cancelBtn.focus(), 0);
  });
}

// ── Human-readable fetch error messages ────────────────────────────────────────
function _humanFetchError(err) {
  const msg = (err && err.message) || String(err);
  if (msg === 'Failed to fetch' || msg === 'NetworkError when attempting to fetch resource.' || msg.includes('ERR_CONNECTION_REFUSED')) {
    return 'Cannot reach the AutoCue server. Make sure it is running (autocue serve) and try again.';
  }
  if (msg.includes('index is still building') || msg.includes('not ready')) {
    return 'The similarity index is still warming up — please wait a few seconds and try again.';
  }
  if (msg.includes('502') || msg.includes('Bad Gateway')) {
    return 'Server gateway error (502). The server may be restarting — try again shortly.';
  }
  if (msg.includes('503') || msg.includes('Service Unavailable')) {
    return 'Server is temporarily unavailable (503). Try again in a moment.';
  }
  if (msg.includes('timeout') || msg.includes('Timeout')) {
    return 'The request timed out. Your library may be large — try again or reduce the set duration.';
  }
  return msg;
}

// ── Button loading state ────────────────────────────────────────────────────────
function _setBtnLoading(btn, loading, loadingText) {
  if (!btn) return;
  if (loading) {
    if (!btn._origHTML) btn._origHTML = btn.innerHTML;
    btn.disabled = true;
    btn.classList.remove('btn-cancel');
    btn._cancelHandler = null;
    btn.innerHTML = `<span class="btn-spinner"></span>${loadingText || btn.textContent}`;
  } else {
    btn.disabled = false;
    btn.classList.remove('btn-cancel');
    if (btn._cancelHandler) { btn.removeEventListener('click', btn._cancelHandler); btn._cancelHandler = null; }
    if (btn._origHTML !== undefined) { btn.innerHTML = btn._origHTML; delete btn._origHTML; }
  }
}

// ── Cancellable SSE button ──────────────────────────────────────────────────────
// Puts btn into cancel mode: red glow, clickable, fires abort on click.
// beforeAbort (optional): called on click; if it returns false, abort is skipped
//   (use for confirm dialogs — return false to keep the operation running).
// Call _setBtnLoading(btn, false) in finally to restore.
function _setBtnCancellable(btn, progressText, abortCtrl, beforeAbort) {
  if (!btn) return;
  if (!btn._origHTML) btn._origHTML = btn.innerHTML;
  btn.disabled = false;
  btn.classList.add('btn-cancel');
  btn.innerHTML = `✕&nbsp; ${progressText}`;
  if (btn._cancelHandler) btn.removeEventListener('click', btn._cancelHandler);
  btn._cancelHandler = function() {
    // beforeAbort may return a boolean OR a Promise<boolean> (styled confirm
    // dialog). A declined confirm is recovered by the next progress tick,
    // which re-installs this once-handler.
    const verdict = beforeAbort ? beforeAbort() : true;
    if (verdict === false) return;
    if (verdict && typeof verdict.then === 'function') {
      verdict.then(ok => { if (ok) abortCtrl.abort(); });
      return;
    }
    abortCtrl.abort();
  };
  btn.addEventListener('click', btn._cancelHandler, { once: true });
}

// ── Slide accordion animations ─────────────────────────────────────────────────
// _slideOpen / _slideClose work alongside CSS open/visible class toggling.
// The CSS class controls the final display value; JS animates the height+opacity transition.
// Respects prefers-reduced-motion: skips animation entirely so transitionend-based cleanup
// always runs (avoids _slideActive getting permanently stuck).
var _prefersReducedMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

function _slideOpen(el, openClass) {
  openClass = openClass || 'open';
  if (el._slideActive) return;
  el.classList.add(openClass);
  if (_prefersReducedMotion) return;  // instant open — no animation, no lock needed
  el._slideActive = true;
  el.style.overflow = 'hidden';
  el.style.transition = '';
  el.style.height = '';
  var h = el.scrollHeight;            // forces reflow → natural height
  el.style.height = '0px';
  el.style.opacity = '0';
  el.style.transition = 'height 0.24s cubic-bezier(0.4,0,0.2,1), opacity 0.22s ease';
  var done = false;
  function _openCleanup() {
    if (done) return; done = true;
    el.style.height = ''; el.style.overflow = ''; el.style.opacity = ''; el.style.transition = '';
    el._slideActive = false;
  }
  requestAnimationFrame(function() {
    el.style.height = h + 'px';
    el.style.opacity = '1';
    el.addEventListener('transitionend', function handler(e) {
      if (e.propertyName !== 'height') return;
      el.removeEventListener('transitionend', handler);
      _openCleanup();
    });
    setTimeout(_openCleanup, 400);    // safety: fires if transitionend never fires (DOM removal, etc.)
  });
}

function _slideClose(el, openClass, onDone) {
  openClass = openClass || 'open';
  if (el._slideActive || !el.classList.contains(openClass)) {
    if (onDone) onDone();
    return;
  }
  if (_prefersReducedMotion) {        // instant close
    el.classList.remove(openClass);
    if (onDone) onDone();
    return;
  }
  el._slideActive = true;
  el.style.height = el.scrollHeight + 'px';
  el.style.overflow = 'hidden';
  el.style.transition = 'height 0.2s cubic-bezier(0.4,0,0.2,1), opacity 0.18s ease';
  var done = false;
  function _closeCleanup() {
    if (done) return; done = true;
    el.classList.remove(openClass);   // CSS sets display:none
    el.style.height = ''; el.style.overflow = ''; el.style.opacity = ''; el.style.transition = '';
    el._slideActive = false;
    if (onDone) onDone();
  }
  requestAnimationFrame(function() {
    el.style.height = '0px';
    el.style.opacity = '0';
    el.addEventListener('transitionend', function handler(e) {
      if (e.propertyName !== 'height') return;
      el.removeEventListener('transitionend', handler);
      _closeCleanup();
    });
    setTimeout(_closeCleanup, 400);   // safety fallback
  });
}

function _slideToggle(el, openClass) {
  openClass = openClass || 'open';
  if (el.classList.contains(openClass)) { _slideClose(el, openClass); }
  else { _slideOpen(el, openClass); }
}

// ── Tooltip system ──────────────────────────────────────────────────────────────
// Single shared #tooltip element; driven by data-tip attributes.
// 380ms hover delay prevents flicker on fast mouse moves.
// Smart repositioning flips left/up when near viewport edges.
(function() {
  var tip = document.getElementById('tooltip');
  if (!tip) return;
  var _target = null;
  var _delay = null;
  var _tw = 0, _th = 0;        // cached tip size — read once per text change, not per mousemove
  var _lastX = 0, _lastY = 0;  // latest cursor position — used at show-time, not mouseover-time

  function _show(text) {
    tip.textContent = text;
    _tw = tip.offsetWidth + 20;  // read once when text changes; avoids layout thrash on mousemove
    _th = tip.offsetHeight + 12;
    _placeAt(_lastX, _lastY);
    tip.classList.add('tip-visible');
  }
  function _hide() {
    clearTimeout(_delay); _delay = null;
    _target = null;
    tip.classList.remove('tip-visible');
  }
  function _placeAt(mx, my) {
    var vw = window.innerWidth, vh = window.innerHeight;
    var x = mx + 14, y = my + 20;
    if (x + _tw > vw - 6) x = mx - _tw + 6;
    if (y + _th > vh - 6) y = my - _th - 4;
    tip.style.left = Math.max(4, x) + 'px';
    tip.style.top  = Math.max(4, y) + 'px';
  }

  document.addEventListener('mousemove', function(e) {
    _lastX = e.clientX; _lastY = e.clientY;
    if (_target && tip.classList.contains('tip-visible')) _placeAt(_lastX, _lastY);
  });
  document.addEventListener('mouseover', function(e) {
    var el = e.target.closest('[data-tip]');
    if (!el || el === _target) return;
    _hide();
    _target = el;
    _delay = setTimeout(function() {
      var text = el.getAttribute('data-tip');
      if (text) _show(text);  // uses _lastX/_lastY — cursor position at show-time, not entry-time
    }, 380);
  });
  document.addEventListener('mouseout', function(e) {
    if (!_target) return;
    if (e.target.closest('[data-tip]') !== _target) return;
    // Suppress false-positive: cursor moved to a child element (e.g. btn-spinner inside button)
    if (e.relatedTarget && _target.contains(e.relatedTarget)) return;
    _hide();
  });
  document.addEventListener('scroll', _hide, true);
  document.addEventListener('click', _hide, true);
})();

// ── Button ripple ───────────────────────────────────────────────────────────────
// Injects a .btn-ripple span at the click point; animates out and self-removes.
document.addEventListener('click', function(e) {
  var btn = e.target.closest('button.primary, .secondary-btn');
  if (!btn || _prefersReducedMotion) return;
  if (btn.disabled || btn.classList.contains('btn-cancel') || btn.querySelector('.btn-spinner')) return;
  var r = document.createElement('span');
  r.className = 'btn-ripple';
  var rect = btn.getBoundingClientRect();
  r.style.left = (e.clientX - rect.left - 4) + 'px';
  r.style.top  = (e.clientY - rect.top  - 4) + 'px';
  btn.appendChild(r);
  r.addEventListener('animationend', function() { r.remove(); });
});

// ── Keyboard shortcuts ──────────────────────────────────────────────────────────
(function() {
  const overlay = document.getElementById('kbd-overlay');
  const closeBtn = document.getElementById('kbd-close-btn');
  if (!overlay) return;

  function open()  { overlay.classList.add('open'); }
  function close() { overlay.classList.remove('open'); }
  closeBtn && closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', e => { if (e.target === overlay) close(); });
  const hintBtn = document.getElementById('kbd-hint-btn');
  hintBtn && hintBtn.addEventListener('click', () => overlay.classList.contains('open') ? close() : open());

  document.addEventListener('keydown', function(e) {
    const tag = (e.target.tagName || '').toUpperCase();
    const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target.isContentEditable;

    if (e.key === 'Escape') {
      if (overlay.classList.contains('open')) { close(); e.preventDefault(); return; }
    }

    if (inInput) return;

    if (e.key === '?') {
      e.preventDefault();
      overlay.classList.toggle('open');
      return;
    }
    // Focus search on /
    if (e.key === '/') {
      const s = document.getElementById('track-search');
      if (s) { e.preventDefault(); s.focus(); s.select(); }
      return;
    }
    // Tab shortcuts: 1/2/3
    if (e.key === '1') { switchTab('cues'); return; }
    if (e.key === '2') { switchTab('library'); return; }
    if (e.key === '3') { switchTab('discover'); return; }
    // Select all visible tracks
    if ((e.ctrlKey || e.metaKey) && e.key === 'a') {
      const visible = document.getElementById('track-list');
      if (visible) {
        e.preventDefault();
        filteredTracks().forEach(i => selectedTrackIds.add(String(parsedTracks[i].id)));
        updateSelectionBar();
        AppState.signal('filters');
      }
      return;
    }
  });
})();

// ── Blob URL cleanup ───────────────────────────────────────────────────────────
function revokeAllBlobUrls() {
  blobUrlsToRevoke.forEach(u => URL.revokeObjectURL(u));
}
window.addEventListener('beforeunload', revokeAllBlobUrls);

// ── AppState subscriptions ─────────────────────────────────────────────────────
// 'filters'  → re-render track list when any filter changes
// 'settings' → re-render + overwrite warning when cue settings change
// 'tracks'   → re-render after data load (tracks replaced/enriched)
//
// Issue #172: a rapid sequence of filter toggles (search-fill, search-clear,
// phrase-on, phrase-off, beats-on) on a large album-mode library produces
// back-to-back renderTracks() rebuilds — each one clears the list, walks all
// tracks, and re-fires the per-album <img> artwork-probe chains. On a 3,775-
// track library this saturates the main thread long enough that the next
// synthetic click event in a Playwright run never gets dispatched within
// 30 s. Debounce the render to the trailing edge so a burst of filter
// signals collapses into one render. The cadence (80 ms) matches the
// existing _scheduleSearchRecompute debounce on #search-input — already
// shipped, no new perceived latency for human users.
var _filtersRenderTimer = null;
AppState.subscribe('filters', function() {
  if (!parsedTracks.length) return;
  if (_filtersRenderTimer) clearTimeout(_filtersRenderTimer);
  _filtersRenderTimer = setTimeout(function() {
    _filtersRenderTimer = null;
    renderTracks();
  }, 80);
});
AppState.subscribe('settings', function() {
  if (parsedTracks.length) { renderTracks(); updateOverwriteWarning(); }
});
AppState.subscribe('tracks', function() {
  renderTracks();
});

// ── Events: XML drop ───────────────────────────────────────────────────────────
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
fileInput.addEventListener('change', () => handleFile(fileInput.files[0]));
dropZone.addEventListener('dragover',  e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  handleFile(e.dataTransfer.files[0]);
});

// ── Events: Audio drop ─────────────────────────────────────────────────────────
const audioDropZone  = document.getElementById('audio-drop-zone');
const audioFileInput = document.getElementById('audio-file-input');
audioFileInput.addEventListener('change', () => handleAudioFiles(audioFileInput.files));
audioDropZone.addEventListener('dragover',  e => { e.preventDefault(); audioDropZone.classList.add('drag-over'); });
audioDropZone.addEventListener('dragleave', () => audioDropZone.classList.remove('drag-over'));
audioDropZone.addEventListener('drop', e => {
  e.preventDefault();
  audioDropZone.classList.remove('drag-over');
  handleAudioFiles(e.dataTransfer.files);
});

// Folder toggle: switch between file and directory picker
let folderMode = false;
document.getElementById('audio-folder-toggle').addEventListener('click', e => {
  e.stopPropagation();
  folderMode = !folderMode;
  if (folderMode) {
    audioFileInput.setAttribute('webkitdirectory', '');
    audioFileInput.removeAttribute('accept');
    e.target.textContent = '🎵 Files';
    e.target.title = 'Switch back to file select mode';
  } else {
    audioFileInput.removeAttribute('webkitdirectory');
    audioFileInput.setAttribute('accept', 'audio/*');
    e.target.textContent = '📁 Folder';
    e.target.title = 'Switch to folder select mode';
  }
});

// ── Events: Settings ───────────────────────────────────────────────────────────
['bars-interval', 'start-bar', 'max-cues'].forEach(id => {
  document.getElementById(id).addEventListener('input', () => {
    AppState.signal('settings');
  });
});
document.getElementById('skip-existing-cues').addEventListener('change', () => {
  AppState.signal('settings');
});

// ── Sort buttons ────────────────────────────────────────────────────────────────
document.querySelectorAll('.sort-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const by = btn.dataset.sort;
    if (currentSort.by === by) {
      currentSort.order = currentSort.order === 'asc' ? 'desc' : 'asc';
    } else {
      currentSort = { by, order: 'asc' };
      if (by === 'album') expandedAlbums.clear();
    }
    localStorage.setItem('ac_sort', JSON.stringify(currentSort));
    document.querySelectorAll('.sort-btn').forEach(b => {
      b.classList.toggle('active', b.dataset.sort === currentSort.by);
      if (b.dataset.sort === currentSort.by) {
        b.textContent = { title: 'Title', artist: 'Artist', album: 'Album', bpm: 'BPM', key: 'Key', rating: 'Rating', plays: 'Plays' }[by]
          + (currentSort.order === 'asc' ? ' ▲' : ' ▼');
      } else {
        b.textContent = { title: 'Title', artist: 'Artist', album: 'Album', bpm: 'BPM', key: 'Key', rating: 'Rating', plays: 'Plays' }[b.dataset.sort];
      }
    });
    AppState.signal('filters');
  });
});

// ── Events: Download ───────────────────────────────────────────────────────────
document.getElementById('download-btn').addEventListener('click', () => {
  if (localMode) { applyToRekordbox(); return; }
  const xml  = buildOutputXml();
  const blob = new Blob([xml], { type: 'text/xml' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href = url; a.download = 'autocue_import.xml'; a.click();
  URL.revokeObjectURL(url);
  setStep(4);
  document.getElementById('path-warning').classList.add('visible');
});
document.getElementById('path-warning-dismiss').addEventListener('click', () => {
  document.getElementById('path-warning').classList.remove('visible');
});

// ── Events: Delete all cues (local mode only) ──────────────────────────────────
document.getElementById('color-by-bpm-btn').addEventListener('click', colorTracksByBpm);

// F5: Preview cues — calls /api/generate and stores results in pendingCues for card rendering
document.getElementById('preview-cues-btn').addEventListener('click', async () => {
  const btn = document.getElementById('preview-cues-btn');
  btn.textContent = 'Loading…'; btn.disabled = true;
  // Mirror onto the visible action-bar button — #preview-cues-btn is in the
  // settings toolbar, not where the user clicked when using the action bar.
  const abPrev = document.getElementById('action-bar-preview');
  _setBtnLoading(abPrev, true, 'Previewing…');
  const { barsInterval, startBar, maxCues } = getSettings();
  // Issue #173: Preview must target the selection when any cards are
  // checked — otherwise a user with 2 tracks selected sees a toast for
  // 3,775 tracks AND a subsequent Apply silently overwrites the whole
  // visible library. Every other write op (apply/delete/color/download)
  // already calls activeTracks(); Preview was the lone holdout.
  const trackIds = activeTracks().map(t => parseInt(t.id));
  try {
    const r = await fetch('/api/generate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        track_ids: trackIds,
        mode: analysisMode === 'phrase' ? 'auto' : 'bar',
        bars_interval: barsInterval, start_bar: startBar, max_cues: maxCues,
        memory_cue_mode: document.getElementById('memory-cue-mode').value,
      }),
      signal: AbortSignal.timeout(60_000),
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error(e.detail || r.statusText); }
    const data = await r.json();
    pendingCues = {};
    for (const tr of data.tracks) {
      pendingCues[String(tr.id)] = tr.cues.map(c => ({
        slot: c.slot, posSec: c.position_ms / 1000,
        label: c.label, isPhrase: c.is_phrase, name: c.name || '',
        confidence: c.confidence ?? 1.0, phraseMode: tr.mode_used,
        phraseBars: c.phrase_bars ?? 0,
      }));
    }
    renderTracks();
    showToast(`Previewing cues for ${data.tracks.length} track(s) — click Apply to write`);
  } catch (e) { showToast(`Preview failed: ${e.message}`, true); }
  finally { btn.textContent = 'Preview cues'; btn.disabled = false; _setBtnLoading(abPrev, false); }
});

document.getElementById('delete-cues-btn').addEventListener('click', () => {
  const total = activeTracks().length;
  if (!total) return;
  const suffix = selectedTrackIds.size > 0
    ? ` (${total} selected)`
    : total < parsedTracks.length ? ` (${total} visible)` : '';
  document.getElementById('delete-confirm-msg').textContent =
    `Delete all hot cues from ${total} track${total !== 1 ? 's' : ''}${suffix}?`;
  document.getElementById('delete-confirm-bar').classList.add('visible');
  document.getElementById('delete-cues-btn').style.display = 'none';
});

document.getElementById('delete-cancel-btn').addEventListener('click', () => {
  document.getElementById('delete-confirm-bar').classList.remove('visible');
  document.getElementById('delete-cues-btn').style.display = '';
});

document.getElementById('delete-confirm-btn').addEventListener('click', async () => {
  const confirmBtn = document.getElementById('delete-confirm-btn');
  const cancelBtn  = document.getElementById('delete-cancel-btn');
  confirmBtn.disabled = true;
  cancelBtn.disabled  = true;
  confirmBtn.textContent = 'Deleting…';
  try {
    const trackIds = activeTracks().map(t => parseInt(t.id));
    const r = await fetch('/api/delete-cues', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: trackIds, dry_run: false }),
      signal: AbortSignal.timeout(60_000),
    });
    const resp = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(resp.detail || r.statusText);
    showToast(`Deleted ${resp.deleted} cues from ${resp.tracks_affected} tracks — backup saved`);
    // Refresh track list so existing_hot_cues counts update
    await loadTracksFromServer();
  } catch (err) {
    showToast(`Delete failed: ${err.message}`);
  } finally {
    confirmBtn.disabled = false;
    cancelBtn.disabled  = false;
    confirmBtn.textContent = 'Confirm delete';
    document.getElementById('delete-confirm-bar').classList.remove('visible');
    document.getElementById('delete-cues-btn').style.display = '';
  }
});

// ── Events: Mini player ────────────────────────────────────────────────────────
const miniScrubber = document.getElementById('mini-scrubber');

audioPlayer.addEventListener('timeupdate', () => {
  // D1: timeline + waveform updates moved to RAF loop; keep only time display + scrubber
  if (!isScrubbing) miniScrubber.value = audioPlayer.currentTime;
  document.getElementById('mini-current-time').textContent = fmtTime(audioPlayer.currentTime);
});

audioPlayer.addEventListener('ended', () => {
  _stopPlayRaf(); // D1
  nowPlayingId = null;
  updatePlaybackUI();
});

audioPlayer.addEventListener('error', () => {
  _stopPlayRaf(); // D1
  showToast('Could not play this file — format may not be supported');
  nowPlayingId = null;
  updatePlaybackUI();
});

miniScrubber.addEventListener('mousedown',  () => { isScrubbing = true; });
miniScrubber.addEventListener('touchstart', () => { isScrubbing = true; }, { passive: true });
miniScrubber.addEventListener('mouseup',  () => { audioPlayer.currentTime = parseFloat(miniScrubber.value); isScrubbing = false; if (nowPlayingId) _drawMiniWaveform(nowPlayingId); });
miniScrubber.addEventListener('touchend', () => { audioPlayer.currentTime = parseFloat(miniScrubber.value); isScrubbing = false; if (nowPlayingId) _drawMiniWaveform(nowPlayingId); });

document.getElementById('mini-play-btn').addEventListener('click', () => {
  if (nowPlayingId) togglePlayTrack(nowPlayingId);
});

// ── Events: Backup ─────────────────────────────────────────────────────────────
document.getElementById('backup-btn').addEventListener('click', () => {
  if (!originalXmlText) return;
  const blob = new Blob([originalXmlText], { type: 'text/xml' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'rekordbox_backup_' + new Date().toISOString().slice(0,10) + '.xml';
  a.click();
  URL.revokeObjectURL(a.href);
});
document.getElementById('backup-inline-btn').addEventListener('click', () => {
  document.getElementById('backup-btn').click();
});

// ── Events: ANLZ drop ──────────────────────────────────────────────────────────
const anlzDropZone  = document.getElementById('anlz-drop-zone');
const anlzFileInput = document.getElementById('anlz-file-input');
anlzFileInput.addEventListener('change', async () => {
  indexAnlzFiles(anlzFileInput.files);
  await runPhraseAnalysis();
});
anlzDropZone.addEventListener('dragover',  e => { e.preventDefault(); anlzDropZone.classList.add('drag-over'); });
anlzDropZone.addEventListener('dragleave', () => anlzDropZone.classList.remove('drag-over'));
anlzDropZone.addEventListener('drop', async e => {
  e.preventDefault();
  anlzDropZone.classList.remove('drag-over');
  indexAnlzFiles(e.dataTransfer.files);
  await runPhraseAnalysis();
});

// ── Events: Analysis mode toggle ───────────────────────────────────────────────
function _applyModeUI(mode) {
  document.getElementById('bar-mode-fields').style.display = mode === 'phrase' ? 'none' : '';
  document.getElementById('always-fields').style.marginTop = mode === 'phrase' ? '0' : '10px';
}
document.getElementById('mode-bar-btn').addEventListener('click', () => {
  analysisMode = 'bar';
  document.getElementById('mode-bar-btn').classList.add('active');
  document.getElementById('mode-phrase-btn').classList.remove('active');
  document.getElementById('anlz-drop-section').style.display = 'none';
  _applyModeUI('bar');
  AppState.signal('filters');
});
document.getElementById('mode-phrase-btn').addEventListener('click', async () => {
  analysisMode = 'phrase';
  document.getElementById('mode-phrase-btn').classList.add('active');
  document.getElementById('mode-bar-btn').classList.remove('active');
  _applyModeUI('phrase');
  if (localMode) {
    // Server already has ANLZ access — no Pyodide or folder drop needed.
    // Phrase cues are now loaded LAZILY per viewport (see
    // _queuePhraseLazyLoad wired into the Virtualizer's onWindowChange),
    // NOT eagerly for the whole library. Just re-render; the visible
    // cards' phrase cues fetch as they scroll into view, so there's no
    // "Computing phrase cues N/M" full-library pass on mode switch.
    document.getElementById('anlz-drop-section').style.display = 'none';
    if (parsedTracks.length) AppState.signal('filters');
  } else {
    document.getElementById('anlz-drop-section').style.display = '';
    loadPyodideEngine(); // start loading in background
    if (parsedTracks.length) renderTracks();
  }
});

// Module-level so Cancel button can reach it.
let _phraseLoadAbort = null;

// ── Lazy viewport-driven phrase-cue loading ─────────────────────────────────
// Instead of eagerly computing phrase cues for the whole library on mode
// switch (the old "Computing phrase cues 300/2789" banner), we fetch only
// the cards currently in the virtualized window, as they scroll into view —
// the same pattern the energy sparkline + mix chips use. _queuePhraseLazyLoad
// is wired into the Virtualizer's onWindowChange; it collects visible tracks
// that have phrase data but no cached cues, debounces, and batch-fetches them.
const _phraseInFlight = new Set();   // track-id strings with a pending fetch
const _phraseLazyQueue = new Set();  // track-id strings waiting for the next batch
let _phraseLazyTimer = null;

function _collectPhraseLazyIds(visibleMap) {
  // Returns the visible track-id strings that need a phrase fetch:
  // phrase mode + local + hasPhrase + not already cached + not in flight.
  const out = [];
  if (analysisMode !== 'phrase' || !localMode) return out;
  visibleMap.forEach(function(card) {
    if (!card || !card.dataset) return;
    const tid = card.dataset.trackId;
    if (!tid) return;
    const track = parsedTracksById.get(tid);
    if (!track || !track.hasPhrase) return;        // server has no phrase data
    if (phraseCueState[tid] !== undefined) return;  // cached (incl. empty [])
    if (_phraseInFlight.has(tid)) return;           // already fetching
    out.push(tid);
  });
  return out;
}

function _queuePhraseLazyLoad(visibleMap) {
  const ids = _collectPhraseLazyIds(visibleMap);
  if (!ids.length) return;
  ids.forEach(tid => _phraseLazyQueue.add(tid));
  // Debounce so a fast scroll coalesces into one batch rather than one
  // request per onWindowChange tick.
  clearTimeout(_phraseLazyTimer);
  _phraseLazyTimer = setTimeout(_flushPhraseLazyQueue, 120);
}

async function _flushPhraseLazyQueue() {
  if (_phraseLazyQueue.size === 0) return;
  const tidStrs = Array.from(_phraseLazyQueue);
  _phraseLazyQueue.clear();
  tidStrs.forEach(tid => _phraseInFlight.add(tid));
  const ids = tidStrs.map(s => parseInt(s));
  try {
    const r = await fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: ids, mode: 'phrase' }),
    });
    if (!r.ok) throw new Error('phrase fetch ' + r.status);
    const resp = await r.json();
    const seen = new Set();
    for (const result of (resp.tracks || [])) {
      const tid = String(result.id);
      seen.add(tid);
      // Cache even an empty result so a track with no phrase cues isn't
      // re-queued every time it scrolls back into view.
      phraseCueState[tid] =
        (result.mode_used === 'phrase' && result.cues.length)
          ? result.cues.map(c => ({
              position_ms: c.position_ms, label: c.label, slot: c.slot,
              name: c.name || '', confidence: c.confidence ?? 1.0,
              phrase_bars: c.phrase_bars ?? 0,
            }))
          : [];
      _updateTrackCardCues(result.id);
    }
    // Any requested id the server didn't return → cache empty so it won't loop.
    for (const tid of tidStrs) {
      if (!seen.has(tid)) phraseCueState[tid] = [];
    }
  } catch (e) {
    // Best-effort: a failed batch leaves those cards without a phrase strip;
    // clearing in-flight lets them retry on the next scroll into view.
    console.warn('lazy phrase load failed:', e && e.message || e);
  } finally {
    tidStrs.forEach(tid => _phraseInFlight.delete(tid));
  }
}

async function loadPhraseFromServer() {
  // B7 cache short-circuit: if phrase data was already loaded for the current
  // library epoch, skip the network fan-out and just re-render visible cards.
  // Off-screen tracks pick up cues on next natural render (via renderTracks
  // building cards through buildTrackCard which reads phraseCueState).
  if (_phraseLoadedEpoch === _libraryEpoch && Object.keys(phraseCueState).length > 0) {
    for (const tid of _cardMap.keys()) _updateTrackCardCues(tid);
    return;
  }

  const phraseTrackIds = parsedTracks
    .filter(t => t.hasPhrase)
    .map(t => parseInt(t.id));

  if (!phraseTrackIds.length) {
    showToast('No tracks with phrase analysis data found');
    return;
  }

  phraseCueState = {};
  const BATCH = 300;
  const total = phraseTrackIds.length;

  // A6 banner — show, AbortController for Cancel
  _phraseLoadAbort = new AbortController();
  _showPhraseBanner(0, total);

  let networkFailed = false;
  try {
    for (let i = 0; i < total; i += BATCH) {
      const batch = phraseTrackIds.slice(i, i + BATCH);
      const resp = await fetch('/api/generate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_ids: batch, mode: 'phrase' }),
        signal: _phraseLoadAbort.signal,
      }).then(r => r.json());

      for (const result of (resp.tracks || [])) {
        if (result.mode_used === 'phrase' && result.cues.length) {
          phraseCueState[String(result.id)] = result.cues.map(c => ({
            position_ms: c.position_ms,
            label: c.label,
            slot: c.slot,
            name: c.name || '',
            confidence: c.confidence ?? 1.0,
            phrase_bars: c.phrase_bars ?? 0,
          }));
          // Surgical per-card update — no library-wide rebuild.
          _updateTrackCardCues(result.id);
        }
      }
      _showPhraseBanner(Math.min(i + BATCH, total), total);
    }
    // Mark this epoch as having phrase data loaded for the cache hit on next toggle.
    _phraseLoadedEpoch = _libraryEpoch;
  } catch (err) {
    if (err.name === 'AbortError' && _phraseLoadAbort && _phraseLoadAbort.signal.aborted) {
      showToast('Phrase load cancelled');
    } else {
      networkFailed = true;
      showToast('Phrase load failed: ' + (err.message || 'network error'), true);
    }
  } finally {
    _hidePhraseBanner();
    _phraseLoadAbort = null;
  }

  if (!networkFailed && _phraseLoadAbort === null) {
    const matched = Object.keys(phraseCueState).length;
    if (matched > 0) showToast(`Phrase analysis ready — ${matched} track${matched !== 1 ? 's' : ''}`);
  }
}

function _showPhraseBanner(loaded, total) {
  const b = document.getElementById('phrase-progress-banner');
  if (!b) return;
  b.classList.add('visible');
  const count = document.getElementById('phrase-progress-count');
  if (count) count.textContent = `${loaded} / ${total}`;
  const bar = document.getElementById('phrase-progress-bar');
  if (bar) bar.value = total > 0 ? (loaded / total) * 100 : 0;
}

function _hidePhraseBanner() {
  const b = document.getElementById('phrase-progress-banner');
  if (b) b.classList.remove('visible');
}

document.getElementById('phrase-progress-cancel')?.addEventListener('click', () => {
  if (_phraseLoadAbort) _phraseLoadAbort.abort();
});

// B4: Track info modal — instant open + race-safe lazy probe.
let _infoModalRequestId = 0;
let _infoModalOpen = false;
let _infoModalTrack = null;

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function _fmtDuration(sec) {
  if (!sec) return '—';
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2,'0')}`;
}

function openTrackInfoModal(trackId) {
  const track = parsedTracksById.get(String(trackId));
  if (!track) return;
  const modal = document.getElementById('track-info-modal');
  if (!modal) return;
  _infoModalRequestId++;
  const reqId = _infoModalRequestId;
  _infoModalOpen = true;
  _infoModalTrack = track;

  document.getElementById('ti-title').textContent = track.name || '(no title)';
  document.getElementById('ti-artist').textContent = track.artist || '';
  document.getElementById('ti-album').textContent = track.album || '—';
  document.getElementById('ti-genre').textContent = track.genre || '—';
  document.getElementById('ti-bpm').textContent = track.bpm ? track.bpm.toFixed(2) : '—';
  document.getElementById('ti-key').textContent = track.key || '—';
  document.getElementById('ti-duration').textContent = _fmtDuration(track.totalTime);
  document.getElementById('ti-path').textContent = track.locationFilename || '(server-side path)';

  // Source label: open with Checking… placeholder if we haven't probed yet.
  const sourceCell = document.getElementById('ti-source');
  const helpCell = document.getElementById('ti-source-help');
  const dlBtn = document.getElementById('ti-download');

  function applySource(src) {
    if (src === 'file')      { sourceCell.textContent = 'Local file';       helpCell.textContent = ''; dlBtn.style.display = 'none'; }
    else if (src === 'streaming') { sourceCell.textContent = 'Streaming source';  helpCell.textContent = 'Streaming tracks can\'t be analyzed by AutoCue. Download a real audio file via YouTube to enable phrase analysis.'; dlBtn.style.display = ''; }
    else if (src === 'missing')   { sourceCell.textContent = 'File missing';     helpCell.textContent = 'The file at the path above no longer exists. Restore it, update the path in Rekordbox, or download a replacement.'; dlBtn.style.display = ''; }
    else                      { sourceCell.textContent = 'Unknown';         helpCell.textContent = ''; dlBtn.style.display = 'none'; }
  }

  // Optimistic initial state from server-side source.
  if (track.source === 'streaming') applySource('streaming');
  else if (_audioProbedAt[track.id]) applySource(_audioProbedAt[track.id]);
  else { sourceCell.textContent = 'Checking…'; helpCell.textContent = ''; dlBtn.style.display = 'none'; }

  // YouTube search link.
  const q = `${track.artist || ''} ${track.name || ''}`.trim();
  document.getElementById('ti-youtube-search').href = `https://www.youtube.com/results?search_query=${encodeURIComponent(q)}`;

  modal.classList.add('visible');
  modal.setAttribute('aria-hidden', 'false');

  // Race-safe parallel probe for file-source tracks.
  if (track.source === 'file' && !_audioProbedAt[track.id]) {
    fetch('/api/tracks/check-audio', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: [parseInt(track.id)] }),
    }).then(r => r.json()).then(resp => {
      if (reqId !== _infoModalRequestId || !_infoModalOpen) return;
      const verdict = (resp.results || {})[String(track.id)] || 'unknown';
      _audioProbedAt[track.id] = verdict;
      applySource(verdict);
    }).catch(() => {
      if (reqId !== _infoModalRequestId || !_infoModalOpen) return;
      applySource('unknown');
    });
  }
}

function closeTrackInfoModal() {
  const modal = document.getElementById('track-info-modal');
  if (!modal) return;
  modal.classList.remove('visible');
  modal.setAttribute('aria-hidden', 'true');
  _infoModalOpen = false;
  _infoModalTrack = null;
}
document.getElementById('ti-close')?.addEventListener('click', closeTrackInfoModal);
document.getElementById('track-info-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'track-info-modal') closeTrackInfoModal();
});
document.getElementById('ti-download')?.addEventListener('click', () => {
  if (_infoModalTrack) openYoutubeModal(_infoModalTrack);
});

// B5: YouTube candidate-selection modal.
let _ytModalTrack = null;
let _ytSearchAbort = null;
let _ytDownloadAbort = null;
let _ytModalJob = null;        // in-flight _Download job initiated from a modal Pick
let _ytModalJobToken = 0;      // bumped per _ytDownload call so stale onState from a cancelled job can't null the live handle

function openYoutubeModal(track) {
  // A query-modal Pick may still be in-flight; cancel before reusing the modal
  // for a track-card flow, or its onState writes land in the freshly-reset UI.
  if (_ytModalJob) { try { _ytModalJob.cancel(); } catch (_) {} _ytModalJob = null; }
  _ytModalTrack = track;
  const modal = document.getElementById('yt-modal');
  if (!modal) return;
  document.getElementById('yt-track-label').textContent = `${track.artist || ''} — ${track.name || ''}`.trim();
  const queryInput = document.getElementById('yt-query');
  queryInput.value = `${track.artist || ''} ${track.name || ''}`.trim();
  document.getElementById('yt-candidates').innerHTML = '';
  document.getElementById('yt-status').textContent = '';
  document.getElementById('yt-download-progress').style.display = 'none';
  document.getElementById('yt-result').style.display = 'none';
  document.getElementById('yt-search-btn').disabled = false;
  modal.classList.add('visible');
  modal.setAttribute('aria-hidden', 'false');
  closeTrackInfoModal();
}
function closeYoutubeModal() {
  if (_ytSearchAbort) _ytSearchAbort.abort();
  if (_ytDownloadAbort) _ytDownloadAbort.abort();
  // Cancel any in-flight Pick download initiated via _Download (PRP search→modal flow).
  if (_ytModalJob) { try { _ytModalJob.cancel(); } catch (_) {} _ytModalJob = null; }
  const modal = document.getElementById('yt-modal');
  if (modal) { modal.classList.remove('visible'); modal.setAttribute('aria-hidden', 'true'); }
  _ytModalTrack = null;
}

// Open the YouTube candidate picker for a free-text query (no track object).
// Used by _Download.bindManualPanel when the user types a search term in the
// Download panel — instead of auto-picking yt-dlp's first result (which often
// surfaces a random video for ambiguous queries like "Sampha piona"), the
// candidate list lets the user pick the right version.
function openYoutubeModalForQuery(query) {
  const modal = document.getElementById('yt-modal');
  if (!modal) return;
  // Same in-flight-Pick concern as openYoutubeModal — cancel before re-init.
  if (_ytModalJob) { try { _ytModalJob.cancel(); } catch (_) {} _ytModalJob = null; }
  _ytModalTrack = null;  // no track object — purely query-driven
  const trimmed = (query || '').trim();
  document.getElementById('yt-track-label').textContent = trimmed
    ? `Search: ${trimmed}` : 'Search YouTube';
  const queryInput = document.getElementById('yt-query');
  queryInput.value = trimmed;
  document.getElementById('yt-candidates').innerHTML = '';
  document.getElementById('yt-status').textContent = '';
  document.getElementById('yt-download-progress').style.display = 'none';
  document.getElementById('yt-result').style.display = 'none';
  document.getElementById('yt-search-btn').disabled = false;
  modal.classList.add('visible');
  modal.setAttribute('aria-hidden', 'false');
  // Auto-fire the search so the user lands on the candidate list immediately.
  if (trimmed) {
    setTimeout(() => { try { _ytSearch(); } catch (_) {} }, 0);
  } else {
    queryInput.focus();
  }
}
document.getElementById('yt-close')?.addEventListener('click', closeYoutubeModal);
document.getElementById('yt-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'yt-modal') closeYoutubeModal();
});

async function _ytSearch() {
  const q = document.getElementById('yt-query').value.trim();
  if (!q) return;
  if (_ytSearchAbort) _ytSearchAbort.abort();
  _ytSearchAbort = new AbortController();
  const status = document.getElementById('yt-status');
  const candDiv = document.getElementById('yt-candidates');
  const btn = document.getElementById('yt-search-btn');
  btn.disabled = true;
  status.textContent = 'Searching YouTube…';
  candDiv.innerHTML = '';
  try {
    const resp = await fetch(`/api/youtube/search?q=${encodeURIComponent(q)}&n=5`, { signal: _ytSearchAbort.signal });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    status.textContent = '';
    const targetDur = _ytModalTrack?.totalTime || 0;
    let defaultPicked = false;
    for (const c of (data.candidates || [])) {
      const row = document.createElement('div');
      row.className = 'yt-cand';
      const within = targetDur > 0 && c.duration && Math.abs(c.duration - targetDur) <= targetDur * 0.15;
      if (within && !defaultPicked) { row.classList.add('selected'); defaultPicked = true; }
      row.innerHTML = `
        <div class="yt-cand-text">
          <div class="yt-cand-title">${_esc(c.title)}</div>
          <div class="yt-cand-meta">${_esc(c.channel)} · ${c.duration ? _fmtDuration(c.duration) : '—'}</div>
        </div>
        <button class="primary" type="button">Download</button>`;
      row.querySelector('button').addEventListener('click', () => _ytDownload(c.url));
      candDiv.appendChild(row);
    }
    if (!data.candidates?.length) status.textContent = 'No results.';
  } catch (err) {
    if (err.name === 'AbortError') return;
    status.textContent = `Search failed: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}
document.getElementById('yt-search-btn')?.addEventListener('click', _ytSearch);
document.getElementById('yt-query')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') _ytSearch();
});

// Routed through window._Download (PRD v1.0) so the modal Pick flow honors
// the user's format / normalize / embed_metadata prefs and gets classified
// errors + cancel + 410 already_consumed handling for free.
async function _ytDownload(url) {
  if (_ytModalJob) { try { _ytModalJob.cancel(); } catch (_) {} _ytModalJob = null; }
  // Tag this invocation so a later onState from a cancelled prior job can't
  // null out _ytModalJob after we've already assigned a new one below.
  const myToken = ++_ytModalJobToken;
  const progress = document.getElementById('yt-download-progress');
  const progBar = document.getElementById('yt-progress');
  const progText = document.getElementById('yt-progress-text');
  const result = document.getElementById('yt-result');
  const status = document.getElementById('yt-status');
  progress.style.display = 'flex';
  result.style.display = 'none';
  progBar.value = 0;
  progText.textContent = 'Starting…';
  if (status) status.textContent = '';

  // Read manual-panel prefs so the user's chosen format / normalize / metadata
  // toggles drive the modal-initiated download too.
  let fmt = 'mp3_320';
  try { fmt = localStorage.getItem('autocue_dl_format') || 'mp3_320'; } catch (_) {}
  const normEl = document.getElementById('dl-normalize');
  const metaEl = document.getElementById('dl-embed-meta');

  _ytModalJob = window._Download.start({
    query: url,
    format: fmt,
    normalize: !!(normEl && normEl.checked),
    embedMeta: metaEl ? metaEl.checked : true,
    dest: (typeof _dlDestDir !== 'undefined' && _dlDestDir) || undefined,
    onState: function(ev) {
      if (typeof ev.percent === 'number') progBar.value = ev.percent;
      if (ev.phase) progText.textContent = ({
        queued: 'Queued…',
        fetching: `Downloading… ${Math.round(ev.percent || 0)}%`,
        converting: 'Converting…',
        normalizing_pass1: 'Measuring loudness…',
        normalizing_pass2: `Normalizing… ${Math.round(ev.percent || 0)}%`,
        tagging: 'Writing metadata…',
      })[ev.phase] || ev.phase;
      if (ev.type === 'done') {
        progress.style.display = 'none';
        if (ev.status === 'success' && ev.path) {
          result.style.display = '';
          document.getElementById('yt-result-path').textContent = ev.path;
          document.getElementById('yt-result-path-fallback').value = ev.path;
          document.getElementById('yt-result-path-fallback').style.display = 'none';
        } else if (ev.status === 'error') {
          showToast(`Download failed: ${ev.error_message || 'unknown'}`, true);
        } else if (ev.status === 'cancelled') {
          if (status) status.textContent = 'Cancelled';
        }
        if (_ytModalJobToken === myToken) _ytModalJob = null;
      }
    },
  });
}

// B6: Copy path with clipboard fallback.
document.getElementById('yt-copy-path')?.addEventListener('click', async () => {
  const path = document.getElementById('yt-result-path').textContent;
  try {
    await navigator.clipboard.writeText(path);
    showToast('Path copied');
  } catch {
    const fallback = document.getElementById('yt-result-path-fallback');
    fallback.style.display = 'block';
    fallback.focus();
    fallback.select();
    showToast('Clipboard blocked — select the path manually (Cmd-C / Ctrl-C)');
  }
});

// ── Camelot key filter ─────────────────────────────────────────────────────────
(function initCamelotFilter() {
  const CAMELOT_COLORS = [
    '#e05c97','#e0406c','#d94040','#e0682a',
    '#d4a017','#9ec94a','#52c23a','#29b89e',
    '#2e95d9','#4265db','#7b52d9','#b545d4',
  ];
  // Camelot position 1-12 with actual key names
  const KEY_NAMES_A = ['Ab min','Eb min','Bb min','F min','C min','G min','D min','A min','E min','B min','F# min','C# min'];
  const KEY_NAMES_B = ['B maj','F# maj','Db maj','Ab maj','Eb maj','Bb maj','F maj','C maj','G maj','D maj','A maj','E maj'];

  const grid = document.getElementById('camelot-grid');
  if (!grid) return;

  // Header row
  for (let n = 1; n <= 12; n++) {
    const lbl = document.createElement('div');
    lbl.className = 'ck-label'; lbl.textContent = n;
    grid.appendChild(lbl);
  }
  // A row (inner ring / minor)
  for (let n = 1; n <= 12; n++) {
    const key = `${n}A`;
    const btn = document.createElement('button');
    btn.className = 'ck-btn'; btn.dataset.key = key;
    btn.textContent = key;
    btn.title = KEY_NAMES_A[n - 1];
    btn.style.background = CAMELOT_COLORS[n - 1];
    btn.addEventListener('click', () => toggleKey(key));
    grid.appendChild(btn);
  }
  // B row (outer ring / major)
  for (let n = 1; n <= 12; n++) {
    const key = `${n}B`;
    const btn = document.createElement('button');
    btn.className = 'ck-btn'; btn.dataset.key = key;
    btn.textContent = key;
    btn.title = KEY_NAMES_B[n - 1];
    btn.style.background = CAMELOT_COLORS[n - 1];
    btn.addEventListener('click', () => toggleKey(key));
    grid.appendChild(btn);
  }

  function toggleKey(key) {
    if (selectedKeys.has(key)) selectedKeys.delete(key);
    else selectedKeys.add(key);
    updateKeyUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  }

  function updateKeyUI() {
    document.querySelectorAll('.ck-btn').forEach(b => {
      b.classList.toggle('selected', selectedKeys.has(b.dataset.key));
    });
    const btn = document.getElementById('key-filter-btn');
    if (selectedKeys.size === 0) {
      btn.textContent = 'Key: Any ▾';
      btn.classList.remove('active');
    } else {
      btn.textContent = `Key: ${[...selectedKeys].sort().join(', ')} ▾`;
      btn.classList.add('active');
    }
  }

  document.getElementById('ck-clear-btn').addEventListener('click', () => {
    selectedKeys.clear();
    updateKeyUI();
    updateActiveFiltersChip();
    AppState.signal('filters');
  });

  document.getElementById('ck-related-btn').addEventListener('click', () => {
    if (selectedKeys.size === 0) return;
    const toAdd = new Set();
    for (const key of selectedKeys) {
      const m = key.match(/^(\d+)([AB])$/);
      if (!m) continue;
      const n = parseInt(m[1]), ab = m[2];
      toAdd.add(`${n}${ab === 'A' ? 'B' : 'A'}`);
      const prev = n === 1 ? 12 : n - 1;
      const next = n === 12 ? 1 : n + 1;
      toAdd.add(`${prev}${ab}`);
      toAdd.add(`${next}${ab}`);
    }
    for (const k of toAdd) selectedKeys.add(k);
    updateKeyUI();
    AppState.signal('filters');
  });

  // Toggle popup — fixed position, calculated from button rect
  const popup = document.getElementById('key-filter-popup');
  document.getElementById('key-filter-btn').addEventListener('click', e => {
    e.stopPropagation();
    const open = popup.classList.toggle('open');
    if (open) {
      const rect = e.currentTarget.getBoundingClientRect();
      popup.style.top  = (rect.bottom + 4) + 'px';
      popup.style.left = rect.left + 'px';
      requestAnimationFrame(() => {
        const pr = popup.getBoundingClientRect();
        if (pr.right > window.innerWidth - 8)
          popup.style.left = (window.innerWidth - pr.width - 8) + 'px';
      });
    }
  });
  document.addEventListener('click', e => {
    if (!popup.contains(e.target) && e.target.id !== 'key-filter-btn') {
      popup.classList.remove('open');
    }
  });
  window.addEventListener('scroll', () => popup.classList.remove('open'), { passive: true });
})();

// ── Collapsible settings ────────────────────────────────────────────────────────
(function initSettingsToggle() {
  const sec     = document.getElementById('settings-section');
  const toggle  = document.getElementById('settings-title-toggle');
  const summary = document.getElementById('settings-summary');

  function updateSummary() {
    const mode    = document.getElementById('mode-phrase-btn')?.classList.contains('active') ? 'Phrase' : 'Bar intervals';
    const bars    = document.getElementById('bars-interval')?.value || '16';
    const maxCues = document.getElementById('max-cues')?.value || '8';
    if (summary) summary.textContent = `${mode} · every ${bars} bars · max ${maxCues} cues`;
  }

  toggle.addEventListener('click', () => {
    sec.classList.toggle('collapsed');
    updateSummary();
  });

  // Keep summary fresh whenever settings inputs change
  ['bars-interval', 'start-bar', 'max-cues', 'mode-bar-btn', 'mode-phrase-btn'].forEach(id => {
    document.getElementById(id)?.addEventListener('change', updateSummary);
    document.getElementById(id)?.addEventListener('click',  updateSummary);
  });

  // Expose so local-mode init can collapse it
  window._collapseSettings = () => { sec.classList.add('collapsed'); updateSummary(); };
  window._expandSettings   = () => sec.classList.remove('collapsed');

  updateSummary();
})();

// ── Scroll to top ───────────────────────────────────────────────────────────────
(function initScrollToTop() {
  const btn = document.getElementById('scroll-top-btn');
  if (!btn) return;
  window.addEventListener('scroll', () => {
    btn.classList.toggle('visible', window.scrollY > 400);
  }, { passive: true });
  btn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
})();

// ── Scroll header ──────────────────────────────────────────────────────────────
// Sticky shadow — add .shadowed when tracks-sticky is pinned at top
(function initStickyHeader() {
  const tracksSticky = document.getElementById('tracks-sticky');
  if (!tracksSticky) return;
  function checkShadow() {
    // Skip while the section is hidden (getBoundingClientRect returns top:0 for hidden elements)
    if (!document.getElementById('tracks-section')?.classList.contains('visible')) {
      tracksSticky.classList.remove('shadowed');
      return;
    }
    const rect = tracksSticky.getBoundingClientRect();
    const topBarH = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--top-bar-h')) || 0;
    tracksSticky.classList.toggle('shadowed', rect.top <= topBarH + 1);
  }
  let rafPending = false;
  window.addEventListener('scroll', () => {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => { rafPending = false; checkShadow(); });
  }, { passive: true });
  // Expose so loadTracksFromServer can trigger a correct initial check
  window._checkStickyHeader = checkShadow;
})();
