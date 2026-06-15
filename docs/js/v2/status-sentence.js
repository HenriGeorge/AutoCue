/**
 * AutoCue 2.0 — status sentence (P1 T3).
 *
 * Turns the #app-status strip into clickable facts. Two NEW derived facts —
 * "N need cues" (client-side from existingHotCues) and "health S/100" — plus a
 * 30 s Rekordbox-running poll that finally feeds the EXISTING updateAppStatus
 * renderer (no caller passes rekordboxRunning today). Local mode only.
 *
 * Reads legacy state ONLY via window.ACBridge / window.* (ES-module scope can't
 * see the classic scripts' top-level `let`). See docs/js/v2/main.js interop.
 */

// Pure: derive the two new facts from current state. Exported for unit tests.
export function deriveFacts({ tracks, healthSummary } = {}) {
  const list = Array.isArray(tracks) ? tracks : [];
  const loaded = list.length > 0;
  const needCues = loaded
    ? list.filter((t) => Number(t.existingHotCues) === 0).length
    : 0;
  const score =
    healthSummary && typeof healthSummary.library_score === 'number'
      ? Math.round(healthSummary.library_score)
      : null;
  return [
    // 0 stays visible (design-A counts down to 0 after a fix) once tracks load.
    { id: 'needcues', visible: loaded, count: needCues },
    { id: 'health', visible: score != null, score },
  ];
}

function _setFact(id, visible, html) {
  const btn = document.getElementById('status-' + id);
  const sep = document.getElementById('status-sep-' + id);
  if (!btn) return;
  btn.hidden = !visible;
  if (sep) sep.hidden = !visible;
  if (visible) btn.querySelector('.status-text').innerHTML = html;
}

function _paint() {
  const b = window.ACBridge;
  if (!b) return;
  const [needcues, health] = deriveFacts({
    tracks: b.tracks(),
    healthSummary: b.healthSummary(),
  });
  _setFact('needcues', needcues.visible,
    `<span class="num">${needcues.count.toLocaleString()}</span> need cues`);
  _setFact('health', health.visible,
    health.score != null ? `health <span class="num">${health.score}</span>/100` : '');
}

// Navigate to the Library health section; scan if no summary yet.
function _revealHealth() {
  // Open the Library place (the health section lives in the Library surface).
  window.AC2?.workbench?.setWorkbench(true);
  if (!window.AC2?.library?.isActive?.()) document.getElementById('wb-library-place')?.click();
  document.getElementById('health-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (window.ACBridge && window.ACBridge.healthSummary() == null) {
    document.getElementById('health-scan-btn')?.click();
  }
}

let _rbTimer = null;
function _pollRb() {
  if (document.hidden) return;
  fetch('/api/status?include_rb=1')
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => {
      if (d && typeof d.rekordbox_running === 'boolean' && window.updateAppStatus) {
        // Feed the EXISTING renderer (app.js) — don't duplicate the dot logic.
        window.updateAppStatus({ connected: true, rekordboxRunning: d.rekordbox_running });
      }
    })
    .catch(() => { /* transient — next tick retries */ });
}

let _inited = false;
export function initStatusSentence() {
  if (_inited) return;
  _inited = true;

  // Click wiring (idempotent — the markup ids are static).
  document.getElementById('status-count')?.addEventListener('click', () => {
    // Go to the cue grid — exit any active centre-pane place first.
    window.AC2?.library?.deactivate?.();
    window.AC2?.discover?.deactivate?.();
    window.AC2?.duplicates?.deactivate?.();
    if (window.switchTab) window.switchTab('cues');
    document.getElementById('tracks-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
  document.getElementById('status-needcues')?.addEventListener('click', _revealHealth);
  document.getElementById('status-health')?.addEventListener('click', _revealHealth);

  // Repaint the derived facts when tracks change or a health scan completes.
  if (window.AppState) window.AppState.subscribe('tracks', _paint);
  window.addEventListener('autocue:health-summary', _paint);

  // Start the Rekordbox poll (immediately, then every 30 s).
  _pollRb();
  if (_rbTimer) clearInterval(_rbTimer);
  _rbTimer = setInterval(_pollRb, 30_000);

  _paint();
}

// Local mode only — the global layer never renders in XML/Pages mode.
if (window.ACBridge && window.ACBridge.isLocalMode()) {
  initStatusSentence();
} else {
  window.addEventListener('autocue:local-mode', initStatusSentence, { once: true });
}
