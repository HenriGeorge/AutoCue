/**
 * AutoCue 2.0 — workbench rail extras (P2).
 *
 * Owns the lower three groups of the Crate Console left rail:
 *   • Playlists      → #wb-playlists
 *   • Saved filters  → #wb-saved-filters  (localStorage-backed)
 *   • Health-ring    → #wb-rail-health
 *
 * Reuse-only: every interactive surface here drives a LEGACY path rather than
 * reimplementing it.
 *   - Playlists: set #playlist-select.value + dispatch('change') → the existing
 *     change handler runs loadTracksFromServer(id). We never fetch/apply a
 *     playlist ourselves.
 *   - Saved filters: capture the live legacy filter inputs (#search-input,
 *     #phrase-only-cb, #audio-only-cb) + the workbench crate (ACBridge.crate());
 *     re-apply by writing those inputs and dispatching their native events, so
 *     the legacy AppState.signal('filters') recompute fires exactly as if the
 *     user toggled them.
 *   - Health: the "Fix it" ink-pill clicks the legacy phrase-fix button inside
 *     #health-fix-row (or, with nothing to fix / no scan yet, #health-scan-btn).
 *
 * Flag-gated + additive: only runs while the workbench is active. Reads legacy
 * state via window.ACBridge; mutates legacy state only through native DOM
 * events + .click() on existing controls.
 *
 * Loaded as part of the v2 module graph via workbench/shell.js (which calls
 * initRail() from activate()).
 */

const SAVED_KEY = 'ac_workbench_saved_filters';

// ── Saved-filter persistence (mirrors the ac_discover_filters pattern) ──────
function _loadSaved() {
  try {
    const raw = JSON.parse(localStorage.getItem(SAVED_KEY) || '[]');
    return Array.isArray(raw) ? raw.filter((f) => f && typeof f.name === 'string') : [];
  } catch (_) { return []; }
}
function _persistSaved(list) {
  try { localStorage.setItem(SAVED_KEY, JSON.stringify(list)); } catch (_) {}
}

// Snapshot the live legacy filter state from the DOM + ACBridge crate.
function _captureFilters() {
  const search = document.getElementById('search-input');
  const phrase = document.getElementById('phrase-only-cb');
  const beats = document.getElementById('audio-only-cb');
  return {
    search: search ? search.value.trim() : '',
    phrase: phrase ? !!phrase.checked : false,
    beats: beats ? !!beats.checked : false,
    crate: window.ACBridge ? window.ACBridge.crate() : 'all',
  };
}

// Re-apply a captured snapshot by driving the legacy inputs + crate path.
function _applyFilters(f) {
  if (!f) return;
  if (window.ACBridge) window.ACBridge.setCrate(f.crate || 'all');
  const search = document.getElementById('search-input');
  if (search) {
    search.value = f.search || '';
    search.dispatchEvent(new Event('input', { bubbles: true }));
  }
  const phrase = document.getElementById('phrase-only-cb');
  if (phrase && phrase.checked !== !!f.phrase) {
    phrase.checked = !!f.phrase;
    phrase.dispatchEvent(new Event('change', { bubbles: true }));
  }
  const beats = document.getElementById('audio-only-cb');
  if (beats && beats.checked !== !!f.beats) {
    beats.checked = !!f.beats;
    beats.dispatchEvent(new Event('change', { bubbles: true }));
  }
}

// A short human label for a captured snapshot, used when the row needs a hint.
export function describeFilter(f) {
  if (!f) return '';
  const CRATE_LABELS = { none: 'no cues', phrase: 'phrase-ready', cued: 'already cued' };
  const parts = [];
  if (f.search) parts.push(`"${f.search}"`);
  if (f.crate && f.crate !== 'all') parts.push(CRATE_LABELS[f.crate] || f.crate);
  if (f.phrase) parts.push('phrase-only');
  if (f.beats) parts.push('beat-grid');
  return parts.join(' · ');
}

function _makeRow({ label, count, active, onClick, extraClass }) {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'wb-crate' + (extraClass ? ' ' + extraClass : '') + (active ? ' active' : '');
  btn.innerHTML =
    `<span class="wb-crate-label">${label}</span>` +
    (count != null ? `<span class="wb-crate-count">${count}</span>` : '');
  btn.addEventListener('click', onClick);
  return btn;
}

// ── Drag-to-playlist (step 5) ─────────────────────────────────────────────────
const DND_MIME = 'application/x-autocue-tracks';

// Make the grid the drag SOURCE: dragging a card carries its track id — or the
// whole current selection when the dragged card is part of it. Wired once on
// #track-list (cards set draggable=true themselves, local-mode only).
function _initDragSource() {
  const list = document.getElementById('track-list');
  if (!list) return;
  list.addEventListener('dragstart', (e) => {
    const card = e.target.closest && e.target.closest('.track-card[data-track-id]');
    if (!card || !e.dataTransfer) return;
    const id = String(card.dataset.trackId);
    let ids = [id];
    try {
      const sel = (window.ACBridge && window.ACBridge.selectedIds)
        ? [...window.ACBridge.selectedIds()].map(String) : [];
      if (sel.includes(id)) ids = sel;
    } catch (_) {}
    e.dataTransfer.setData(DND_MIME, JSON.stringify(ids));
    e.dataTransfer.effectAllowed = 'copy';
    document.body.classList.add('ac-dragging-tracks');
  });
  list.addEventListener('dragend', () => {
    document.body.classList.remove('ac-dragging-tracks');
    document.querySelectorAll('#wb-playlists .wb-crate.drop-ready')
      .forEach((r) => r.classList.remove('drop-ready'));
  });
}

// Make a rail playlist row a drop TARGET: P6 drop gravity (swell + green wash on
// drag-over), then POST the dropped ids via the single ACBridge write path and
// pop the row's count on success.
function _wirePlaylistDrop(row, playlistId) {
  row.dataset.playlistId = playlistId;
  row.addEventListener('dragover', (e) => {
    if (!e.dataTransfer || !Array.from(e.dataTransfer.types || []).includes(DND_MIME)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    row.classList.add('drop-ready');
  });
  row.addEventListener('dragleave', () => row.classList.remove('drop-ready'));
  row.addEventListener('drop', async (e) => {
    row.classList.remove('drop-ready');
    if (!e.dataTransfer) return;
    let ids = [];
    try { ids = JSON.parse(e.dataTransfer.getData(DND_MIME) || '[]'); } catch (_) {}
    if (!Array.isArray(ids) || !ids.length) return;
    e.preventDefault();
    const fn = window.ACBridge && window.ACBridge.addTracksToPlaylist;
    if (typeof fn !== 'function') return;
    const res = await fn(playlistId, ids);
    if (!res) return;
    _renderPlaylists(); // repaint counts from the now-refreshed dropdown
    const fresh = document.querySelector(
      `#wb-playlists .wb-crate[data-playlist-id="${CSS.escape(String(playlistId))}"]`);
    const cnt = fresh && fresh.querySelector('.wb-crate-count');
    if (cnt) { cnt.classList.remove('count-pop'); void cnt.offsetWidth; cnt.classList.add('count-pop'); }
  });
}

// ── Playlists ───────────────────────────────────────────────────────────────
function _renderPlaylists() {
  const host = document.getElementById('wb-playlists');
  const sel = document.getElementById('playlist-select');
  if (!host || !sel) return;
  const current = sel.value || '';
  host.innerHTML = '';
  // Mirror the dropdown's options (id + "Name (count)" text). The first option
  // is the "All tracks" sentinel (value ''), which the Crates group already
  // covers — skip it so the rail isn't redundant.
  for (const opt of Array.from(sel.options)) {
    if (!opt.value) continue;
    const m = /^(.*?)\s*\((\d+)\)\s*$/.exec(opt.textContent || '');
    const name = m ? m[1] : (opt.textContent || '').trim();
    const cnt = m ? Number(m[2]).toLocaleString() : null;
    const row = _makeRow({
      label: name,
      count: cnt,
      active: opt.value === current && current !== '',
      onClick: () => {
        // P3/P5: leaving via a playlist click exits any active centre-pane place.
        if (window.AC2 && window.AC2.duplicates) window.AC2.duplicates.deactivate();
        if (window.AC2 && window.AC2.discover) window.AC2.discover.deactivate();
        if (window.AC2 && window.AC2.library) window.AC2.library.deactivate();
        // Reuse the legacy filter path: set value + fire change. The existing
        // #playlist-select change handler calls loadTracksFromServer(id).
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        _renderPlaylists(); // repaint active state
      },
    });
    _wirePlaylistDrop(row, opt.value); // step 5 — drag tracks onto this playlist
    host.appendChild(row);
  }
}

// ── Saved filters ────────────────────────────────────────────────────────────
function _renderSaved() {
  const host = document.getElementById('wb-saved-filters');
  if (!host) return;
  const list = _loadSaved();
  host.innerHTML = '';
  if (!list.length) {
    const empty = document.createElement('div');
    empty.className = 'wb-saved-empty';
    empty.textContent = 'No saved filters yet';
    host.appendChild(empty);
    return;
  }
  for (const f of list) {
    const row = _makeRow({
      label: f.name,
      count: null,
      active: false,
      extraClass: 'wb-saved-row',
      onClick: () => {
        // P3/P5: applying a saved filter is a grid intent — exit the place first.
        if (window.AC2 && window.AC2.duplicates) window.AC2.duplicates.deactivate();
        if (window.AC2 && window.AC2.discover) window.AC2.discover.deactivate();
        if (window.AC2 && window.AC2.library) window.AC2.library.deactivate();
        _applyFilters(f.state);
      },
    });
    // Inline delete affordance (does not re-apply the filter).
    const del = document.createElement('span');
    del.className = 'wb-saved-del';
    del.setAttribute('role', 'button');
    del.setAttribute('aria-label', `Delete saved filter ${f.name}`);
    del.textContent = '×';
    del.addEventListener('click', (e) => {
      e.stopPropagation();
      _persistSaved(_loadSaved().filter((x) => x.name !== f.name));
      _renderSaved();
    });
    row.appendChild(del);
    host.appendChild(row);
  }
}

function _saveCurrent() {
  const state = _captureFilters();
  const hint = describeFilter(state);
  const name = (window.prompt('Name this filter', hint || 'My filter') || '').trim();
  if (!name) return;
  const list = _loadSaved().filter((x) => x.name !== name); // overwrite same name
  list.push({ name, state });
  _persistSaved(list);
  _renderSaved();
}

// ── Health-ring card ─────────────────────────────────────────────────────────
// Deterministic template lede (G organ — NO LLM).
export function healthLede(s) {
  if (!s) return '';
  const score = Math.round(Number(s.library_score) || 0);
  const noCues = Number(s.no_cues) || 0;
  const state = score >= 90 ? 'Gig-ready' : score >= 70 ? 'Almost gig-ready' : 'Needs work';
  const cues = noCues === 0
    ? 'all tracks cued'
    : `${noCues} track${noCues === 1 ? '' : 's'} need${noCues === 1 ? 's' : ''} cues`;
  return `${state} · ${cues}`;
}

const RH_R = 27;
const RH_CIRC = 2 * Math.PI * RH_R; // ≈169.6

function _renderHealth() {
  const host = document.getElementById('wb-rail-health');
  if (!host) return;
  host.hidden = false;
  const s = window.ACBridge ? window.ACBridge.healthSummary() : null;

  if (!s) {
    host.innerHTML =
      `<div class="rh-lede">Scan to see library health.</div>` +
      `<button type="button" class="rh-fix wb-rail-scan-btn">Scan library</button>`;
    host.querySelector('.wb-rail-scan-btn')?.addEventListener('click', () => {
      document.getElementById('health-scan-btn')?.click();
    });
    return;
  }

  const score = Math.round(Number(s.library_score) || 0);
  const offset = RH_CIRC * (1 - Math.max(0, Math.min(100, score)) / 100);
  const lede = healthLede(s);

  host.innerHTML =
    `<div class="rh-top">` +
      `<div class="rh-ring">` +
        `<svg width="64" height="64" viewBox="0 0 64 64" aria-hidden="true">` +
          `<circle class="rh-track" cx="32" cy="32" r="${RH_R}" fill="none" stroke-width="6"/>` +
          `<circle class="rh-fill" cx="32" cy="32" r="${RH_R}" fill="none" stroke-width="6" ` +
            `stroke-dasharray="${RH_CIRC.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}"/>` +
        `</svg>` +
        `<div class="rh-center"><span class="rh-score">${score}</span><span class="rh-denom">/100</span></div>` +
      `</div>` +
      `<div class="rh-meta"><div class="rh-eyebrow">Library health</div></div>` +
    `</div>` +
    `<p class="rh-lede">${lede}</p>` +
    `<button type="button" class="rh-fix wb-rail-fix-btn">Fix it</button>`;

  host.querySelector('.wb-rail-fix-btn')?.addEventListener('click', () => {
    // Reuse the legacy fix path: the phrase-quality fix button is the first
    // button inside #health-fix-row. Fall back to a fresh scan when there's
    // nothing to fix (no button rendered).
    const fixBtn = document.querySelector('#health-fix-row button');
    if (fixBtn) fixBtn.click();
    else document.getElementById('health-scan-btn')?.click();
  });
}

// ── Init ─────────────────────────────────────────────────────────────────────
let _wired = false;

export function initRail() {
  _renderPlaylists();
  _renderSaved();
  _renderHealth();

  if (_wired) return;
  _wired = true;

  _initDragSource(); // step 5 — grid cards become drag sources for rail playlists
  document.querySelector('.wb-saved-add')?.addEventListener('click', _saveCurrent);

  // Playlist options load asynchronously after local mode; keep counts/active
  // state fresh as the library + selection change.
  if (window.AppState) {
    window.AppState.subscribe('tracks', _renderPlaylists);
    window.AppState.subscribe('filters', _renderPlaylists);
  }
  // Repaint the ring whenever a scan finishes.
  window.addEventListener('autocue:health-summary', _renderHealth);
}
