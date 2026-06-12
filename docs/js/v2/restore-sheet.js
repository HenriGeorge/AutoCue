/**
 * AutoCue 2.0 — Restore sheet (P3, R8).
 *
 * The canonical A-layer emergency exit: after a duplicates delete, a transient
 * "Undo" fact appears in the status sentence (#status-restore) and opens a sheet
 * (#wb-restore-sheet) that POSTs /api/restore for the just-written backup. The
 * in-view duplicates undo banner (legacy, docs/js/02-local-ops.js) stays as the
 * convenience copy — this sheet is the canonical surface per maintenance
 * grammar #2 (emergencies are sheets off the status sentence).
 *
 * Delegation/interop: listens for the `autocue:duplicates-deleted` event the
 * legacy delete path dispatches (T1 seam), and re-scans via window.ACBridge.
 * Reads legacy only via window.*; never imports legacy.
 *
 * The backup window is 30s (matches the legacy banner + the server's
 * per-session backup reuse). A newer delete simply replaces the path + resets
 * the timer — restoring the session's single reused backup rolls back the lot.
 */

const WINDOW_MS = 30000;

let _timer = null;
let _backupFile = null; // basename POSTed to /api/restore

function _el(id) { return document.getElementById(id); }

function _hide() {
  if (_timer) { clearTimeout(_timer); _timer = null; }
  _backupFile = null;
  _el('status-restore')?.setAttribute('hidden', '');
  _el('status-sep-restore')?.setAttribute('hidden', '');
  _el('wb-restore-sheet')?.setAttribute('hidden', '');
}

function _openSheet() {
  const sheet = _el('wb-restore-sheet');
  if (!sheet || !_backupFile) return;
  const line = _el('wb-restore-heading');
  const file = _el('wb-restore-file');
  if (file) file.textContent = _backupFile;
  if (line) line.textContent = line.dataset.msg || 'Restore the deleted tracks?';
  sheet.removeAttribute('hidden');
  // Anchor the sheet under the fact button (the status sentence is right-
  // aligned, so a static origin would drop it in the wrong corner). Clamp to
  // the viewport so a right-edge fact doesn't push it off-screen.
  const fact = _el('status-restore');
  if (fact && fact.getBoundingClientRect) {
    const r = fact.getBoundingClientRect();
    const sw = sheet.offsetWidth || 320;
    let left = r.left;
    if (left + sw > window.innerWidth - 12) left = window.innerWidth - sw - 12;
    sheet.style.top = `${Math.round(r.bottom + 6)}px`;
    sheet.style.left = `${Math.round(Math.max(12, left))}px`;
  }
  _el('wb-restore-go')?.focus();
}

async function _restore() {
  if (!_backupFile) return;
  const go = _el('wb-restore-go');
  if (go) { go.disabled = true; go.textContent = 'Restoring…'; }
  try {
    const r = await fetch('/api/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: _backupFile }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || r.statusText);
    }
    window.showToast?.('Restored from backup. Reload to see the recovered tracks.');
    // If the duplicates place is open, re-scan so the recovered tracks reappear.
    if (window.AC2?.duplicates?.isActive?.() && window.ACBridge?.scanDuplicates) {
      window.ACBridge.scanDuplicates();
    }
    _hide();
  } catch (e) {
    if (go) { go.disabled = false; go.textContent = 'Restore from backup'; }
    window.showToast?.(`Restore failed: ${e.message || e}`, true);
  }
}

function _onDeleted(ev) {
  const d = (ev && ev.detail) || {};
  // No backup path → nothing to restore (e.g. a cancelled-before-write delete).
  if (!d.backup_path) return;
  _backupFile = String(d.backup_path).split('/').pop();
  const deleted = Number(d.deleted) || 0;

  const fact = _el('status-restore');
  const sep = _el('status-sep-restore');
  if (fact) {
    const txt = fact.querySelector('.status-text');
    if (txt) txt.textContent = `${deleted.toLocaleString()} deleted · Undo`;
    fact.removeAttribute('hidden');
  }
  sep?.removeAttribute('hidden');
  const line = _el('wb-restore-heading');
  if (line) line.dataset.msg = `${deleted.toLocaleString()} track${deleted === 1 ? '' : 's'} deleted — restore them?`;

  // Reset the 30s expiry (a newer delete replaces the path + timer).
  if (_timer) clearTimeout(_timer);
  _timer = setTimeout(_hide, WINDOW_MS);
}

export function initRestoreSheet() {
  window.addEventListener('autocue:duplicates-deleted', _onDeleted);
  _el('status-restore')?.addEventListener('click', _openSheet);
  _el('wb-restore-go')?.addEventListener('click', _restore);
  _el('wb-restore-dismiss')?.addEventListener('click', _hide);
}
