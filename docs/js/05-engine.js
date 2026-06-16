/* AutoCue app.js — P0 T5 split part 5/8: 05-engine.js
 * Classic script (NOT a module): shares globals, loaded in order by
 * docs/index.html. Concatenating 01..08 in order reproduces the original
 * app.js. Do not reorder. See .claude/project/web-ui.md. */
// ── Parsing ────────────────────────────────────────────────────────────────────
function parseRekordboxXml(xmlString) {
  const parser = new DOMParser();
  const doc = parser.parseFromString(xmlString, 'text/xml');

  if (doc.querySelector('parsererror')) {
    return { error: "This file couldn't be parsed as XML. Make sure it's a valid rekordbox.xml export." };
  }
  if (!doc.querySelector('DJ_PLAYLISTS')) {
    return { error: "This doesn't look like a Rekordbox export. In Rekordbox go to File → Export Collection in rekordbox format." };
  }

  const tracks = [...doc.querySelectorAll('COLLECTION > TRACK')].map(el => {
    const tempoEl = el.querySelector('TEMPO');
    let tempo = null;
    if (tempoEl) {
      const beatsPerBar = parseInt((tempoEl.getAttribute('Metro') || '4/4').split('/')[0], 10) || 4;
      tempo = {
        bpm:        parseFloat(tempoEl.getAttribute('Bpm'))    || 0,
        inizio:     parseFloat(tempoEl.getAttribute('Inizio')) || 0,
        beatsPerBar,
      };
    }

    // Extract filename from Location attribute for audio file matching
    const rawLocation = el.getAttribute('Location') || '';
    let locationFilename = '';
    try { locationFilename = decodeURIComponent(rawLocation.split('/').pop()); }
    catch { locationFilename = rawLocation.split('/').pop(); }

    const existingCueDetails = [...el.querySelectorAll('POSITION_MARK')]
      .filter(pm => parseInt(pm.getAttribute('Num'), 10) >= 0)
      .map(pm => ({
        num:   parseInt(pm.getAttribute('Num'), 10),
        name:  pm.getAttribute('Name') || '',
        start: parseFloat(pm.getAttribute('Start')),
      }));
    const existingHotCues = existingCueDetails.length;

    return {
      el,
      id:               el.getAttribute('TrackID'),
      name:             el.getAttribute('Name')   || '(no title)',
      artist:           el.getAttribute('Artist') || '',
      totalTime:        parseFloat(el.getAttribute('TotalTime')) || 0,
      bpm:              parseFloat(el.getAttribute('AverageBpm')) || (tempo ? tempo.bpm : 0),
      tempo,
      existingHotCues,
      existingCueDetails,
      locationFilename,
    };
  });
  return { doc, tracks };
}

// ── Cue generation ─────────────────────────────────────────────────────────────
function generateCues(track, barsInterval, startBar, maxCues) {
  // local-mode tracks have track.bpm directly; XML tracks use track.tempo
  const bpm = track.tempo?.bpm || track.bpm || 0;
  if (!bpm) return [];
  const inizio = track.tempo?.inizio || 0;
  const beatsPerBar = track.tempo?.beatsPerBar || 4;
  const barDuration = (60.0 / bpm) * beatsPerBar;
  const cues = [];
  let slot = 0;
  for (let i = 0; i < maxCues + 64 && slot < maxCues; i++) {
    const posSec = inizio + (startBar - 1 + i * barsInterval) * barDuration;
    if (posSec < 0) continue;
    if (track.totalTime > 0 && posSec >= track.totalTime) break;
    const barNumber = startBar + i * barsInterval;
    cues.push({ slot, posSec: Math.round(posSec * 1000) / 1000, name: `Bar ${barNumber}`,
                confidence: 0.6, phraseMode: 'bar' });
    slot++;
  }
  return cues;
}

function getSettings() {
  return {
    barsInterval: Math.max(1, parseInt(document.getElementById('bars-interval').value, 10) || 16),
    startBar:     Math.max(1, parseInt(document.getElementById('start-bar').value, 10) || 1),
    maxCues:      Math.min(8, Math.max(1, parseInt(document.getElementById('max-cues').value, 10) || 8)),
  };
}

// ── Audio: file matching & registration ───────────────────────────────────────
function matchFileToTrack(file) {
  const fname = file.name;
  const fnameLower = fname.toLowerCase();
  return parsedTracks.find(t => {
    if (!t.locationFilename) return false;
    if (t.locationFilename === fname) return true;
    return t.locationFilename.toLowerCase() === fnameLower;
  }) || null;
}

function registerAudioFile(track, file) {
  // Revoke old object URL if re-registering
  if (audioState[track.id]?.objectUrl) {
    URL.revokeObjectURL(audioState[track.id].objectUrl);
    blobUrlsToRevoke.delete(audioState[track.id].objectUrl);
  }
  const objectUrl = URL.createObjectURL(file);
  blobUrlsToRevoke.add(objectUrl);
  audioState[track.id] = { file, objectUrl, artworkUrl: null };

  // Skip jsmediatags for WAV (no artwork standard) or if CDN failed
  const isWav = file.name.toLowerCase().endsWith('.wav');
  if (!isWav && window.jsmediatags) {
    jsmediatags.read(file, {
      onSuccess(tag) {
        const pic = tag.tags.picture;
        if (pic && audioState[track.id]) {
          const blob = new Blob([new Uint8Array(pic.data)], { type: pic.format });
          const url  = URL.createObjectURL(blob);
          blobUrlsToRevoke.add(url);
          audioState[track.id].artworkUrl = url;
          updateCardArtwork(track.id);
          if (nowPlayingId === track.id) updateMiniPlayerArtwork();
        }
      },
      onError() {},
    });
  }

  updateCardAudioState(track.id);
}

function handleAudioFiles(fileList) {
  let matched = 0;
  for (const file of fileList) {
    const track = matchFileToTrack(file);
    if (track) { registerAudioFile(track, file); matched++; }
  }
  const countEl = document.getElementById('audio-match-count');
  const total = Object.keys(audioState).length;
  countEl.textContent = total > 0 ? `${total} / ${parsedTracks.length} matched` : '';
  if (matched === 0 && fileList.length > 0) showToast('No files matched tracks in the XML');
}

// ── Audio: playback ────────────────────────────────────────────────────────────
const audioPlayer = document.getElementById('autocue-player');

// D1: RAF loop — updates timeline playhead + mini waveform at ~60fps while playing
function _startPlayRaf() {
  if (_playRafId) return;
  function _rafTick() {
    if (audioPlayer.paused || !nowPlayingId) { _playRafId = null; return; }
    updateTimeline();
    _drawMiniWaveform(nowPlayingId);
    _traceEnergyPlayhead(nowPlayingId);
    _playRafId = requestAnimationFrame(_rafTick);
  }
  _playRafId = requestAnimationFrame(_rafTick);
}
function _stopPlayRaf() {
  if (_playRafId) { cancelAnimationFrame(_playRafId); _playRafId = null; }
}

// D4: Draw energy waveform + progress + playhead on the mini player canvas
function _drawMiniWaveform(trackId) {
  if (isScrubbing) return; // D5 fix: don't overwrite canvas position during user drag
  const canvas = document.getElementById('mini-waveform');
  if (!canvas) return;
  // D8 fix: HiDPI — set canvas physical pixel size once on first call
  const dpr = window.devicePixelRatio || 1;
  const cssW = 120, cssH = 22;
  if (canvas.width !== cssW * dpr) {
    canvas.width  = cssW * dpr;
    canvas.height = cssH * dpr;
  }
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const W = cssW, H = cssH;
  const track = parsedTracksById.get(String(trackId));
  const pct = (track && track.totalTime)
    ? Math.min(audioPlayer.currentTime / track.totalTime, 1) : 0;
  const isDark = document.documentElement.classList.contains('dark');

  ctx.clearRect(0, 0, W, H);

  const curve = _energyCache[trackId];
  if (curve && curve.length > 0) {
    const barW = W / curve.length;
    for (let i = 0; i < curve.length; i++) {
      const barH = Math.max(2, curve[i] * H);
      const x = i * barW;
      const y = (H - barH) / 2;
      const filled = (i / curve.length) <= pct;
      ctx.fillStyle = filled ? 'rgba(40,226,20,0.85)' : (isDark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)');
      ctx.fillRect(x + 0.5, y, Math.max(barW - 1.5, 0.5), barH);
    }
  } else {
    // Fallback: simple progress fill (use fillRect if roundRect unsupported on older browsers)
    const _fillRound = (x, y, w, h, r) => {
      if (ctx.roundRect) { ctx.roundRect(x, y, w, h, r); ctx.fill(); }
      else { ctx.fillRect(x, y, w, h); }
    };
    ctx.fillStyle = isDark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.10)';
    ctx.beginPath(); _fillRound(0, H / 2 - 2, W, 4, 2);
    ctx.fillStyle = 'rgba(40,226,20,0.85)';
    ctx.beginPath(); _fillRound(0, H / 2 - 2, W * pct, 4, 2);
  }

  // Playhead line
  const phX = Math.round(W * pct);
  ctx.strokeStyle = isDark ? 'rgba(255,255,255,0.85)' : 'rgba(0,0,0,0.7)';
  ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(phX, 0); ctx.lineTo(phX, H); ctx.stroke();
}

// Aliveness (step 3, P1) — trace a playhead across the now-playing track's
// energy sparkline(s) (grid row + inspector drawer) and ping each phrase cue
// marker as the playhead crosses it. Additive: acts only when those elements
// exist. The moving line mirrors the existing timeline/mini-waveform playheads,
// so it isn't PRM-gated; the cue-ping *pulse* IS gated (CSS @media).
let _lastPingPct = 0;
function _traceEnergyPlayhead(trackId) {
  const track = parsedTracksById.get(String(trackId));
  if (!track || !track.totalTime) return;
  const pct = Math.min(audioPlayer.currentTime / track.totalTime, 1) * 100;
  const hosts = document.querySelectorAll(
    `.energy-sparkline[data-track-id="${trackId}"], .wb-insp-energy[data-track-id="${trackId}"]`
  );
  hosts.forEach((host) => {
    let ph = host.querySelector(':scope > .energy-playhead');
    if (!ph) { ph = document.createElement('div'); ph.className = 'energy-playhead'; host.appendChild(ph); }
    ph.style.left = pct + '%';
  });
  _pingCrossedCues(trackId, pct);
}

function _pingCrossedCues(trackId, pct) {
  const prev = _lastPingPct;
  _lastPingPct = pct;
  const dp = pct - prev;
  if (dp < 0 || dp > 5) return; // seek / restart — don't ping every cue in between
  const card = document.querySelector(`.track-card[data-track-id="${trackId}"]`);
  if (!card) return;
  card.querySelectorAll('.phrase-cue-tick').forEach((tick) => {
    const tp = parseFloat(tick.style.left);
    if (isNaN(tp) || tp <= prev || tp > pct) return;
    tick.classList.remove('cue-ping');
    void tick.offsetWidth; // restart the animation
    tick.classList.add('cue-ping');
    tick.addEventListener('animationend', () => tick.classList.remove('cue-ping'), { once: true });
  });
}

function _clearEnergyPlayheads() {
  document.querySelectorAll('.energy-playhead').forEach((el) => el.remove());
}

function playTrack(trackId, seekSec = 0) {
  const state = audioState[trackId];
  if (!state) return;
  _clearEnergyPlayheads(); // drop any trace left on a previously-playing card

  const isNewSrc = nowPlayingId !== trackId;
  if (isNewSrc) {
    audioPlayer.src = state.objectUrl;
    // D2 fix: defer seek until metadata is loaded so currentTime sticks on new src
    audioPlayer.addEventListener('loadedmetadata', function onMeta() {
      audioPlayer.removeEventListener('loadedmetadata', onMeta);
      audioPlayer.currentTime = seekSec;
    }, { once: true });
  } else {
    audioPlayer.currentTime = seekSec;
  }
  audioPlayer.play().then(() => {
    _startPlayRaf(); // D1 fix: start RAF only after browser confirms playback started
  }).catch(() => {});

  nowPlayingId = trackId;
  updatePlaybackUI();
  showMiniPlayer(trackId);
}

function pausePlayback() {
  audioPlayer.pause();
  _stopPlayRaf(); // D1
  updatePlaybackUI();
}

// B3: aggregate failed-audio toasts. Multiple ensureLocalAudio failures within
// 1 second collapse to a single toast — eliminates the stacked-toast pileup
// visible in image #7 of the v5 plan context.
function _queueAudioFailToast(track) {
  _audioFailQueue.add(String(track?.id || ''));
  if (_audioFailFlushTimer) return;
  _audioFailFlushTimer = setTimeout(() => {
    const n = _audioFailQueue.size;
    if (n === 1) {
      const t = parsedTracksById.get([..._audioFailQueue][0]);
      const label = t ? `${t.artist || ''} — ${t.name || ''}`.trim().replace(/^—\s*/, '') : '';
      showToast(label ? `Audio file not found: ${label}` : 'Audio file not found on disk', true);
    } else {
      showToast(`Audio file not found for ${n} tracks`, true);
    }
    _audioFailQueue.clear();
    _audioFailFlushTimer = null;
  }, 1000);
}

// B1+B2: lazy verification for tracks visible under the audio-only filter.
// Sends ≤500 ids per request, sequential, with debounce. Results stored in
// _audioProbedAt; tracks the filter would otherwise hide are still surfaced
// when their parent dir is unverifiable (fail-open).
async function _probeAudioForVisibleTracks() {
  // Abort any in-flight chunks.
  if (_audioCheckAbort) _audioCheckAbort.abort();
  _audioCheckAbort = new AbortController();
  const signal = _audioCheckAbort.signal;

  await new Promise(r => setTimeout(r, 200)); // debounce
  if (signal.aborted) return;

  const ids = parsedTracks
    .filter(t => t.source === 'file' && !(t.id in _audioProbedAt))
    .map(t => parseInt(t.id));
  if (!ids.length) { AppState.signal('filters'); return; }

  const CHUNK = 500;
  for (let i = 0; i < ids.length; i += CHUNK) {
    const slice = ids.slice(i, i + CHUNK);
    try {
      const resp = await fetch('/api/tracks/check-audio', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_ids: slice }),
        signal,
      }).then(r => r.json());
      Object.assign(_audioProbedAt, resp.results || {});
      for (const d of (resp.unverified_dirs || [])) _audioUnverifiedDirs.add(d);
      AppState.signal('filters'); // re-render incrementally
    } catch (err) {
      if (err.name === 'AbortError') return;
      showToast(`Audio check failed: ${err.message || 'network error'}`, true);
      return;
    }
  }
}

async function ensureLocalAudio(track) {
  if (audioState[track.id]) return; // already loaded
  // B3: don't even try for streaming / known-missing tracks — saves a fetch
  // and the inevitable toast.
  if (track.source && track.source !== 'file') { _queueAudioFailToast(track); return; }
  if (_audioProbedAt[track.id] === 'missing') { _queueAudioFailToast(track); return; }
  try {
    const resp = await fetch(`/api/tracks/${track.id}/audio`);
    if (!resp.ok) { _queueAudioFailToast(track); return; }
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    blobUrlsToRevoke.add(url);
    audioState[track.id] = { file: null, objectUrl: url, artworkUrl: audioState[track.id]?.artworkUrl || null };
    updateCardAudioState(track.id);
  } catch (e) {
    showToast(`Could not load audio: ${e.message}`);
  }
}

function togglePlayTrack(trackId) {
  if (nowPlayingId === trackId && !audioPlayer.paused) {
    pausePlayback();
  } else {
    playTrack(trackId, nowPlayingId === trackId ? audioPlayer.currentTime : 0);
  }
}

function seekAndPlay(trackId, posSec) {
  if (!audioState[trackId]) {
    showToast('Drop the audio file for this track to enable playback');
    return;
  }
  playTrack(trackId, posSec);
}

// ── Audio: UI updates ──────────────────────────────────────────────────────────
function updatePlaybackUI() {
  const isPlaying = !audioPlayer.paused;

  // Update all card play buttons and art overlay
  document.querySelectorAll('.track-card').forEach(card => {
    const tid = card.dataset.trackId;
    const btn = card.querySelector('.play-btn');
    const overlay = card.querySelector('.art-play-overlay');
    const active = tid === nowPlayingId;
    card.classList.toggle('now-playing', active && isPlaying);
    if (btn) {
      btn.innerHTML = (active && isPlaying) ? SVG_PAUSE : SVG_PLAY;
      btn.setAttribute('aria-label', (active && isPlaying) ? 'Pause' : 'Play');
    }
    if (overlay) {
      overlay.innerHTML = (active && isPlaying) ? SVG_PAUSE : SVG_PLAY;
      overlay.classList.toggle('playing', active && isPlaying);
    }
  });

  // Update mini player play button
  document.getElementById('mini-play-icon')?.parentElement &&
    (document.getElementById('mini-play-btn').innerHTML =
      isPlaying ? SVG_PAUSE : SVG_PLAY);

  // Update the workbench inspector play button (the only play control in the
  // dense wb-row grid). String-coerce both ids — dataset is a string.
  const inspPlay = document.getElementById('wb-insp-play');
  if (inspPlay) {
    const active = String(inspPlay.dataset.trackId) === String(nowPlayingId) && isPlaying;
    inspPlay.textContent = active ? '⏸ Pause' : '▶ Play';
    inspPlay.classList.toggle('playing', active);
  }

  // Update timeline playhead
  updateTimeline();
}

function showMiniPlayer(trackId) {
  const track = parsedTracksById.get(String(trackId));
  if (!track) return;

  document.getElementById('mini-track-name').textContent   = track.name;
  document.getElementById('mini-track-artist').textContent = track.artist;
  document.getElementById('mini-duration').textContent     = fmtTime(track.totalTime);

  const scrubber = document.getElementById('mini-scrubber');
  scrubber.max = track.totalTime || 100;

  updateMiniPlayerArtwork();
  _drawMiniWaveform(trackId); // D4: initial draw (may have cached energy)

  document.getElementById('mini-player').classList.remove('hidden');
  document.getElementById('mini-sep').style.display = '';
}

function updateMiniPlayerArtwork() {
  const state = nowPlayingId ? audioState[nowPlayingId] : null;
  const img = document.getElementById('mini-artwork');
  img.src = state?.artworkUrl || '';
  img.style.visibility = state?.artworkUrl ? 'visible' : 'hidden';
}

function updateCardArtwork(trackId) {
  const card = document.querySelector(`.track-card[data-track-id="${trackId}"]`);
  if (!card) return;
  const img = card.querySelector('.artwork-img');
  const placeholder = card.querySelector('.artwork-placeholder');
  const url = audioState[trackId]?.artworkUrl;
  if (img && url) {
    img.src = url;
    img.style.display = 'block';
    if (placeholder) placeholder.style.display = 'none';
  }
}

function updateCardAudioState(trackId) {
  const card = document.querySelector(`.track-card[data-track-id="${trackId}"]`);
  if (!card) return;
  card.querySelector('.play-btn')?.classList.remove('hidden');
  card.querySelector('.load-audio-btn')?.classList.add('hidden');
  // Make cue badges playable — guard against double-listener if called more than once
  card.querySelectorAll('.cue-badge[data-pos-sec]').forEach(badge => {
    badge.classList.add('playable');
    if (!badge.dataset.listenerAdded) {
      badge.dataset.listenerAdded = '1';
      badge.addEventListener('click', () => seekAndPlay(trackId, parseFloat(badge.dataset.posSec)));
    }
  });
}

function updateTimeline() {
  if (!nowPlayingId) return;
  const card = document.querySelector(`.track-card[data-track-id="${nowPlayingId}"]`);
  if (!card) return;
  const track = parsedTracksById.get(String(nowPlayingId));
  if (!track?.totalTime) return;
  let ph = card.querySelector('.timeline-playhead');
  const tl = card.querySelector('.timeline');
  if (!tl) return;
  if (!ph) { ph = document.createElement('div'); ph.className = 'timeline-playhead'; tl.appendChild(ph); }
  const pct = (audioPlayer.currentTime / track.totalTime) * 100;
  ph.style.left = `${pct}%`;
}

// ── Pyodide / Phrase analysis ──────────────────────────────────────────────────
const ANALYZE_PYTHON = `
from pyrekordbox.anlz import AnlzFile

KIND_MAP = {
    1: {1:'Intro',2:'Up',3:'Down',5:'Chorus',6:'Outro'},
    2: {1:'Intro',2:'Verse',3:'Verse',4:'Verse',5:'Verse',6:'Verse',7:'Verse',8:'Bridge',9:'Chorus',10:'Outro'},
    3: {1:'Intro',2:'Verse',3:'Verse',4:'Verse',5:'Verse',6:'Verse',7:'Verse',8:'Bridge',9:'Chorus',10:'Outro'},
}

DJ_NAMES = {
    'Intro':'Intro', 'Verse':'Verse', 'Bridge':'Bridge',
    'Chorus':'Drop', 'Outro':'Outro', 'Up':'Build', 'Down':'Break', '?':'',
}

def analyze_anlz(ext_bytes, dat_bytes):
    ext = AnlzFile.parse(bytes(ext_bytes))
    dat = AnlzFile.parse(bytes(dat_bytes))
    pssi = next((t for t in ext.body.tags if t.fourcc == 'PSSI'), None)
    pqtz = next((t for t in dat.body.tags if t.fourcc == 'PQTZ'), None)
    if not pssi or not pqtz:
        return []
    beats = pqtz.content.entries
    phrases = pssi.content.entries
    mood = pssi.content.mood

    def beat_ms(n):
        idx = n - 1
        return beats[idx].time if 0 <= idx < len(beats) else None

    def lbl(kind):
        return KIND_MAP.get(mood, {}).get(kind, '?')

    seen, pass1, pass2 = set(), [], []
    for ph in phrases:
        ms = beat_ms(ph.beat)
        if ms is None: continue
        l = lbl(ph.kind)
        if l not in seen:
            seen.add(l)
            pass1.append((ms, l))
        else:
            pass2.append((ms, l))

    combined = sorted(pass1 + pass2[:max(0, 8 - len(pass1))], key=lambda x: x[0])
    from collections import Counter
    counts = Counter(l for _, l in combined[:8])
    seen = {}
    result = []
    for i, (ms, l) in enumerate(combined[:8]):
        seen[l] = seen.get(l, 0) + 1
        base = DJ_NAMES.get(l, '')
        name = '' if not base else (base if counts[l] == 1 else f'{base} {seen[l]}')
        result.append({'position_ms': ms, 'label': l, 'slot': i, 'name': name})
    return result
`;

async function loadPyodideEngine() {
  if (pyodideReady) return pyodideReady;
  const statusEl = document.getElementById('pyodide-status');
  if (statusEl) statusEl.textContent = '⏳ Loading Python engine (first time ~15s)…';
  pyodideReady = (async () => {
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js';
    document.head.appendChild(script);
    await new Promise((res, rej) => { script.onload = res; script.onerror = rej; });
    const py = await loadPyodide();
    await py.loadPackage('micropip');
    const micropip = py.pyimport('micropip');
    await micropip.install('pyrekordbox');
    py.runPython(ANALYZE_PYTHON);
    if (statusEl) statusEl.textContent = '✅ Python engine ready';
    return py;
  })();
  pyodideReady.catch(err => {
    if (statusEl) statusEl.textContent = '❌ Failed — use bar-interval mode';
    showToast('Python engine failed to load — phrase analysis unavailable');
    pyodideReady = null;
    analysisMode = 'bar';
    document.getElementById('mode-bar-btn').classList.add('active');
    document.getElementById('mode-phrase-btn').classList.remove('active');
    if (parsedTracks.length) renderTracks();
  });
  return pyodideReady;
}

function indexAnlzFiles(fileList) {
  anlzFileMap = {};
  let count = 0;
  for (const f of fileList) {
    const parts = (f.webkitRelativePath || f.name).split('/');
    if (parts.length < 2) continue;
    const folder = parts[parts.length - 2].toLowerCase();
    const name = parts[parts.length - 1].toUpperCase();
    if (!anlzFileMap[folder]) anlzFileMap[folder] = {};
    if (name.endsWith('.EXT') || name === 'ANLZ0000.EXT') { anlzFileMap[folder].ext = f; count++; }
    if (name.endsWith('.DAT') || name === 'ANLZ0000.DAT') anlzFileMap[folder].dat = f;
  }
  return count;
}

async function analyzeTrackWithPyodide(trackId) {
  const folder = parseInt(trackId, 10).toString(16).padStart(8, '0');
  const files = anlzFileMap[folder];
  if (!files?.ext || !files?.dat) return null;
  const [extBuf, datBuf] = await Promise.all([files.ext.arrayBuffer(), files.dat.arrayBuffer()]);
  const py = await loadPyodideEngine();
  py.globals.set('_ext', new Uint8Array(extBuf));
  py.globals.set('_dat', new Uint8Array(datBuf));
  const result = await py.runPythonAsync('analyze_anlz(_ext, _dat)');
  return result.toJs({ dict_converter: Object.fromEntries });
}

async function runPhraseAnalysis() {
  const statusEl = document.getElementById('pyodide-status');
  let matched = 0, analyzed = 0;
  phraseCueState = {};
  for (const track of parsedTracks) {
    if (statusEl) statusEl.textContent = `⏳ Analyzing ${analyzed + 1}/${parsedTracks.length}…`;
    try {
      const cues = await analyzeTrackWithPyodide(track.id);
      if (cues && cues.length > 0) {
        phraseCueState[track.id] = cues;
        matched++;
      }
    } catch(e) {
      console.warn('Phrase analysis failed for', track.name, e);
    }
    analyzed++;
  }
  if (statusEl) statusEl.textContent = `✅ ${matched}/${parsedTracks.length} tracks analyzed`;
  document.getElementById('anlz-match-count').textContent =
    matched > 0 ? `${matched} / ${parsedTracks.length} matched` : '';
  renderTracks();
  updateOverwriteWarning();
}

function updateOverwriteWarning() {
  if (!parsedTracks.length) return;
  const { maxCues } = getSettings();
  const skipExisting = document.getElementById('skip-existing-cues').checked;
  const warning = document.getElementById('overwrite-warning');
  if (skipExisting) { warning.style.display = 'none'; return; }
  const usedSlots = new Set(Array.from({length: maxCues}, (_, i) => i));
  const atRisk = parsedTracks.filter(t =>
    t.existingCueDetails && t.existingCueDetails.some(c => usedSlots.has(c.num))
  );
  if (atRisk.length > 0) {
    document.getElementById('overwrite-count').textContent = atRisk.length;
    warning.style.display = '';
  } else {
    warning.style.display = 'none';
  }
}
