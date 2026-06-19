/**
 * AutoCue — Review Dock (dev-only in-page feedback bridge).
 *
 * A fixed bottom bar where, on the RUNNING local app, a human types a change
 * request for the current page; submit POSTs it to /api/review-note, which
 * appends a line to crew/REVIEW-NOTES.md for the AI to tail. Two independent
 * guards keep it OFF for real users — this CLIENT gate (local mode AND the
 * localStorage opt-in) plus the server's AUTOCUE_REVIEW_DOCK env-gate (403).
 * On Pages (XML mode) localMode is false → the module no-ops, nothing injected.
 * Mirrors the `autocue_perf` localStorage gate.
 *
 * Enable (dev): start the server with AUTOCUE_REVIEW_DOCK=1, set
 *   localStorage.ac_review_dock = '1' in the local-app tab, reload; then
 *   tail -f crew/REVIEW-NOTES.md to read submitted change requests.
 */

const FLAG = 'ac_review_dock';

function _enabled() {
  try {
    if (!(window.ACBridge && window.ACBridge.isLocalMode())) return false; // Pages/XML → inert
    return localStorage.getItem(FLAG) === '1';
  } catch (_) {
    return false;
  }
}

// Current-page detection — a workbench MODE (Nightboard) or rail PLACE wins;
// otherwise fall back to the active crate, else the default Cues centre.
export function _derivePage() {
  const b = document.body;
  if (b.classList.contains('nb-active')) return 'nightboard';
  if (b.classList.contains('wb-place-dupes')) return 'duplicates';
  if (b.classList.contains('wb-place-discover')) return 'discover';
  if (b.classList.contains('wb-place-library')) return 'library';
  try {
    const c = window.ACBridge && typeof window.ACBridge.crate === 'function' ? window.ACBridge.crate() : '';
    return c || 'cues';
  } catch (_) {
    return 'cues';
  }
}

export function initReviewDock() {
  if (!_enabled()) return; // gate 1 of 2 (server env-gate is gate 2)
  if (document.querySelector('.review-dock')) return; // idempotent

  const form = document.createElement('form');
  form.className = 'review-dock';
  form.setAttribute('role', 'form');
  form.setAttribute('aria-label', 'Review dock');

  const glyph = document.createElement('span');
  glyph.className = 'review-dock-glyph';
  glyph.setAttribute('aria-hidden', 'true');
  glyph.textContent = '✎';

  const label = document.createElement('label');
  label.className = 'sr-only';
  label.setAttribute('for', 'review-dock-input');
  label.textContent = 'Describe a change for this page';

  const badge = document.createElement('span');
  badge.className = 'review-dock-page mono';
  badge.textContent = `[${_derivePage()}]`;

  const input = document.createElement('input');
  input.id = 'review-dock-input';
  input.className = 'review-dock-input';
  input.type = 'text';
  input.autocomplete = 'off';
  input.placeholder = 'describe a change for this page…';

  const send = document.createElement('button');
  send.type = 'submit';
  send.className = 'review-dock-send';
  send.textContent = 'Send';

  // aria-live so "✓ sent" is announced; visually styled as a success signal.
  const status = document.createElement('span');
  status.className = 'review-dock-status';
  status.setAttribute('aria-live', 'polite');

  form.append(glyph, label, badge, input, send, status);
  document.body.appendChild(form);

  let inFlight = false;
  let sentTimer = null;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (inFlight) return; // double-submit guard
    const note = input.value.trim();
    if (!note) return; // empty → no-op
    const page = _derivePage(); // recompute at submit time
    badge.textContent = `[${page}]`;

    inFlight = true;
    send.disabled = true;
    input.disabled = true;
    try {
      const r = await fetch('/api/review-note', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ page, note }),
      });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try {
          const j = await r.json();
          if (j && j.detail) detail = j.detail;
        } catch (_) { /* non-JSON error body */ }
        window.showToast?.(`Review note failed: ${detail}`, true);
        return; // keep the note in the input so it isn't lost
      }
      input.value = '';
      status.textContent = '✓ sent';
      status.classList.add('show');
      if (sentTimer) clearTimeout(sentTimer);
      sentTimer = setTimeout(() => {
        status.textContent = '';
        status.classList.remove('show');
        sentTimer = null;
      }, 2000);
    } catch (err) {
      window.showToast?.(`Review note failed: ${err && err.message ? err.message : err}`, true);
    } finally {
      inFlight = false;
      send.disabled = false;
      input.disabled = false;
    }
  });
}
