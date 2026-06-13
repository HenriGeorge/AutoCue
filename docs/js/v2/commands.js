/**
 * AutoCue 2.0 — command registry + track search (P1 T4).
 *
 * Every command's run() delegates to an EXISTING surface via window.* / .click()
 * so all legacy guards (backup, Rekordbox-running 409, #173 selection scoping)
 * fire on the real path — the palette never re-implements a write.
 */

function _click(id) {
  document.getElementById(id)?.click();
}
function _goto(tab, sectionId) {
  if (window.switchTab) window.switchTab(tab);
  if (sectionId) {
    document.getElementById(sectionId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
}

// Descriptor: { id, group, label, sub?, run }. Pure to build (no DOM reads).
export function buildCommands() {
  return [
    { id: 'preview-cues', group: 'Cues', label: 'Preview cues',
      sub: 'Generate cue positions for the selection (no write)',
      run: () => _click('preview-cues-btn') },
    { id: 'apply', group: 'Cues', label: 'Apply to Rekordbox',
      sub: 'Write cues — backup first; Rekordbox must be closed',
      run: () => _click('download-btn') },
    { id: 'health-scan', group: 'Library', label: 'Scan library health',
      sub: 'Score every track 0–100',
      run: () => { _goto('library', 'health-section'); _click('health-scan-btn'); } },
    // P3: duplicates is a workbench rail place — both commands open it via
    // the rail entry's own click (delegation, no parallel path). Explicit
    // navigation intent overrides an ac_workbench opt-out, so force the
    // workbench on first. The isActive() guard keeps the toggle-button
    // semantics of #wb-dupes-place from CLOSING the place on a repeat run.
    { id: 'find-duplicates', group: 'Library', label: 'Find duplicates',
      sub: 'Group by artist + title + duration',
      run: () => {
        window.AC2?.workbench?.setWorkbench(true);
        if (!window.AC2?.duplicates?.isActive?.()) _click('wb-dupes-place');
      } },
    { id: 'go-duplicates', group: 'Go to', label: 'Go to Duplicates',
      sub: 'The duplicates place in the workbench rail',
      run: () => {
        window.AC2?.workbench?.setWorkbench(true);
        if (!window.AC2?.duplicates?.isActive?.()) _click('wb-dupes-place');
      } },
    { id: 'build-set', group: 'Library', label: 'Build a set',
      sub: 'Beam-search a DJ set from your library',
      run: () => _goto('library', 'setbuilder-section') },
    { id: 'toggle-theme', group: 'View', label: 'Toggle light / dark',
      run: () => _click('theme-toggle') },
    // Label reflects the action from the current state (buildCommands() is
    // re-evaluated on every palette render, so it stays fresh).
    { id: 'toggle-workbench', group: 'View',
      label: window.AC2?.workbench?.isWorkbenchOn?.()
        ? 'Switch to classic view'
        : 'Switch to workbench',
      sub: 'The Crate Console — rail + grid + inspector',
      run: () => window.AC2?.workbench?.toggleWorkbench() },
    { id: 'filter-phrase', group: 'Filter', label: 'Filter: phrase-ready only',
      run: () => _click('phrase-only-cb') },
    { id: 'filter-beats', group: 'Filter', label: 'Filter: has beat grid only',
      run: () => _click('beats-only-cb') },
    { id: 'go-cues', group: 'Go to', label: 'Go to Cues', run: () => _goto('cues') },
    { id: 'go-library', group: 'Go to', label: 'Go to Library', run: () => _goto('library') },
    { id: 'go-discover', group: 'Go to', label: 'Go to Discover', run: () => _goto('discover') },
  ];
}

const MAX_TRACK_RESULTS = 8;

// Track search: substring on "artist name", cap 8, mono BPM·key meta.
// Returns palette descriptors (same shape as commands, + meta/metaMono).
export function searchTracks(query, tracks) {
  const q = String(query || '').toLowerCase().trim();
  if (!q) return [];
  const list = Array.isArray(tracks) ? tracks : [];
  const out = [];
  for (const t of list) {
    const hay = `${t.artist || ''} ${t.name || ''}`.toLowerCase();
    if (!hay.includes(q)) continue;
    const bpm = Number(t.bpm) > 0 ? Number(t.bpm).toFixed(1) : '—';
    const cued = Number(t.existingHotCues) > 0 ? `${t.existingHotCues} cues` : 'no cues';
    out.push({
      id: `track-${t.id}`,
      group: 'Tracks',
      label: `${t.artist ? t.artist + ' — ' : ''}${t.name || '(untitled)'}`,
      sub: cued,
      meta: `${bpm} · ${t.key || '—'}`,
      metaMono: true,
      run: () => {
        if (window.switchTab) window.switchTab('cues');
        const box = document.getElementById('search-input');
        if (box) {
          box.value = t.name || t.artist || '';
          box.dispatchEvent(new Event('input', { bubbles: true }));
        }
      },
    });
    if (out.length >= MAX_TRACK_RESULTS) break;
  }
  return out;
}
