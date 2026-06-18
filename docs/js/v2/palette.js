/**
 * AutoCue 2.0 — ⌘K command palette (P1 T5).
 *
 * Opens on ⌘K / Ctrl+K / "/" (when not typing) / the header hint. Fuzzy
 * commands + track search, wired to existing surfaces only. Strict keyboard
 * priority via a CAPTURE-phase keydown that stopPropagation()s every key while
 * open, so the legacy app shortcuts (and Discover's) never double-fire.
 *
 * The empty-results state renders an INERT "Ask AutoCue (coming soon)" hint —
 * the composer seam for the future opt-in AUTOCUE_LLM phase (program PRD §6/P6):
 * that phase will route unmatched free text to the assistant. The input +
 * empty-state contract here IS that seam's API; nothing actionable now.
 */
import { rank } from './fuzzy.js';
import { buildCommands, searchTracks } from './commands.js';

// ── Pure helpers (exported for unit tests) ─────────────────────────────────
export function clampActive(index, length) {
  if (length <= 0) return 0;
  return ((index % length) + length) % length; // wrap-around
}

// Build the flat, ordered result list (commands ranked, then matching tracks).
// Returns [{...descriptor}] — track items already carry group 'Tracks'.
export function buildResults(query, { commands, tracks }) {
  const cmds = rank(query, commands, (c) => `${c.group} ${c.label}`);
  const trackHits = searchTracks(query, tracks);
  return [...cmds, ...trackHits];
}

// ── DOM wiring ─────────────────────────────────────────────────────────────
let _open = false;
let _active = 0;
let _results = [];
let _prevFocus = null;

function _els() {
  return {
    veil: document.getElementById('cmd-veil'),
    input: document.getElementById('pal-input'),
    list: document.getElementById('pal-list'),
  };
}

// Presentation-only glyphs for the result tiles (design): the icon belongs in
// the view, not the command registry. Falls back by group, then a generic mark.
const _ICONS = {
  'preview-cues': '✨', 'apply': '✓', 'health-scan': '♥', 'find-duplicates': '⧉',
  'go-duplicates': '⧉', 'build-set': '⛓', 'toggle-theme': '◐', 'toggle-workbench': '⌗',
  'filter-phrase': '✨', 'filter-beats': '▦', 'find-releases': '◎', 'open-nightboard': '◳',
  'go-cues': '⌗', 'go-library': '♥', 'go-discover': '◎',
};
function _iconFor(r) {
  if (r.group === 'Tracks') return '♪';
  return _ICONS[r.id] || (r.group === 'Go to' ? '→' : '⌘');
}
function _updateFootCount(n) {
  const foot = document.getElementById('pal-foot');
  if (!foot) return;
  let c = foot.querySelector('.pal-count');
  if (!c) { c = document.createElement('span'); c.className = 'pal-count'; foot.appendChild(c); }
  c.textContent = n > 0 ? `${n} result${n === 1 ? '' : 's'}` : '';
}

function _render() {
  const { input, list } = _els();
  if (!list) return;
  const tracks = window.ACBridge ? window.ACBridge.tracks() : [];
  _results = buildResults(input.value, { commands: buildCommands(), tracks });
  _active = clampActive(_active, _results.length);
  list.innerHTML = '';

  if (!_results.length) {
    // Composer seam (inert) — see file header.
    const hint = document.createElement('div');
    hint.className = 'pal-composer-hint';
    hint.setAttribute('aria-disabled', 'true');
    hint.textContent = input.value.trim()
      ? 'Ask AutoCue (coming soon) — ⏎ does nothing yet'
      : 'Type a command or search your tracks…';
    list.appendChild(hint);
    input.removeAttribute('aria-activedescendant');
    _updateFootCount(0);
    return;
  }

  let lastGroup = null;
  _results.forEach((r, i) => {
    if (r.group !== lastGroup) {
      lastGroup = r.group;
      const g = document.createElement('div');
      g.className = 'pal-group';
      g.textContent = r.group;
      list.appendChild(g);
    }
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'pal-item' + (i === _active ? ' active' : '');
    item.setAttribute('role', 'option');
    item.id = 'pal-opt-' + i;
    item.setAttribute('aria-selected', i === _active ? 'true' : 'false');
    const ico = document.createElement('span');
    ico.className = 'pal-ico' + (r.group === 'Tracks' ? ' track' : '');
    ico.setAttribute('aria-hidden', 'true');
    ico.textContent = _iconFor(r);
    item.appendChild(ico);
    const label = document.createElement('span');
    label.className = 'pal-label';
    label.textContent = r.label;
    item.appendChild(label);
    if (r.sub) {
      const sub = document.createElement('span');
      sub.className = 'pal-sub';
      sub.textContent = r.sub;
      item.appendChild(sub);
    }
    if (r.meta) {
      const meta = document.createElement('span');
      meta.className = 'pal-meta' + (r.metaMono ? ' mono' : '');
      meta.textContent = r.meta;
      item.appendChild(meta);
    }
    item.addEventListener('mousemove', () => { _active = i; _syncActive(); });
    item.addEventListener('click', () => _runActive(i));
    list.appendChild(item);
  });
  _updateFootCount(_results.length);
  _syncActive();
}

function _syncActive() {
  const { input, list } = _els();
  if (!list) return;
  list.querySelectorAll('.pal-item').forEach((el, i) => {
    const on = i === _active;
    el.classList.toggle('active', on);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
    if (on) {
      input?.setAttribute('aria-activedescendant', el.id);
      el.scrollIntoView({ block: 'nearest' });
    }
  });
}

function _runActive(i) {
  const r = _results[typeof i === 'number' ? i : _active];
  closePalette();          // close BEFORE running so focus-restore doesn't fight switchTab scroll
  if (r && typeof r.run === 'function') r.run();
}

export function openPalette() {
  if (_open) return;
  if (window.ACBridge && !window.ACBridge.isLocalMode()) return; // XML mode: inert
  const { veil, input } = _els();
  if (!veil) return;
  _prevFocus = document.activeElement;
  veil.hidden = false;
  _open = true;
  _active = 0;
  if (input) { input.value = ''; input.focus(); }
  _render();
}

export function closePalette() {
  if (!_open) return;
  const { veil } = _els();
  if (veil) veil.hidden = true;
  _open = false;
  if (_prevFocus && typeof _prevFocus.focus === 'function') {
    try { _prevFocus.focus(); } catch (_) {}
  }
  _prevFocus = null;
}

export function isOpen() { return _open; }

function _onKeydownCapture(e) {
  if (_open) {
    // While open, the palette owns every key (priority over legacy shortcuts).
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closePalette(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); e.stopPropagation(); _active = clampActive(_active + 1, _results.length); _syncActive(); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); e.stopPropagation(); _active = clampActive(_active - 1, _results.length); _syncActive(); return; }
    if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); if (_results.length) _runActive(); return; }
    if (e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); document.getElementById('pal-input')?.focus(); return; }
    e.stopPropagation(); // swallow everything else so legacy handlers stay silent
    return;
  }
  // Closed: claim the openers only.
  const meta = e.metaKey || e.ctrlKey;
  if (meta && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); e.stopPropagation(); openPalette(); return; }
  if (e.key === '/' && !_isTextTarget(e.target)) { e.preventDefault(); e.stopPropagation(); openPalette(); }
}

function _isTextTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

export function initPalette() {
  document.addEventListener('keydown', _onKeydownCapture, { capture: true });
  document.getElementById('cmdk-hint-btn')?.addEventListener('click', openPalette);
  document.getElementById('pal-input')?.addEventListener('input', _render);
  document.getElementById('cmd-veil')?.addEventListener('click', (e) => {
    if (e.target.id === 'cmd-veil') closePalette();
  });
  // Reveal the ⌘K hint once local mode confirms.
  const hint = document.getElementById('cmdk-hint-btn');
  if (hint) {
    if (window.ACBridge && window.ACBridge.isLocalMode()) hint.hidden = false;
    else window.addEventListener('autocue:local-mode', () => { hint.hidden = false; }, { once: true });
  }
}

initPalette();
