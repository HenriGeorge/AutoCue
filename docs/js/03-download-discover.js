/* AutoCue app.js — P0 T5 split part 3/8: 03-download-discover.js
 * Classic script (NOT a module): shares globals, loaded in order by
 * docs/index.html. Concatenating 01..08 in order reproduces the original
 * app.js. Do not reorder. See .claude/project/web-ui.md. */
// ── Discover (new releases) + Download ────────────────────────────────────────

let _downloadConfig = { available: false, ffmpeg: false, default_dir: '' };
var _dlDestDir = '';   // active download destination (music folder or AutoCue default)

function _discoverToken() {
  const inp = document.getElementById('discogs-token');
  return (inp && inp.value.trim()) || localStorage.getItem(_DISCOGS_TOKEN_KEY) || '';
}

// Read an SSE response body (fetch + ReadableStream) and invoke onEvent per JSON event.
async function _consumeSSE(response, onEvent, signal) {
  const reader  = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  if (signal) {
    signal.addEventListener('abort', function() { reader.cancel().catch(function(){}); }, { once: true });
  }
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split('\n');
    buf = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data:')) continue;
      try { onEvent(JSON.parse(line.slice(5).trim())); } catch { /* ignore partial */ }
    }
  }
  // Surface abort so callers can show "Cancelled" feedback
  if (signal && signal.aborted) throw new DOMException('Aborted', 'AbortError');
}

// ── _Download IIFE (PRD .agent/prd/DOWNLOAD_PRD.md v1.0) ─────────────────────
// Single canonical download driver consumed by every surface:
//   - #download-section (manual panel)
//   - .disc-dl-btn (per-Discover-card ⬇ Album)
//   - #ti-download → #yt-modal (track-info + YouTube candidate picker)
//   - #disc-v2-dl-confirm (Shift+click confirm)
// State machine: idle → loading → success | error → idle.
// Each job: enqueue (POST /api/download/enqueue) → stream (GET /api/download/
// stream/{job_id}) → cancel via POST /api/download/cancel/{job_id} +
// AbortController.abort(). 410 already_consumed renders from cached payload.
window._Download = (function() {
  const seenDoneFor = new Set();

  function _classifyDownloadTarget(q) {
    const s = (q || '').trim();
    if (!s) return 'invalid';
    if (s.includes('\n') || s.includes('\r')) return 'invalid';
    const isUrl = /^https?:\/\//i.test(s);
    if (!isUrl) return 'search';
    let listMatch = /[?&]list=([A-Za-z0-9_-]+)/i.exec(s);
    let vMatch    = /[?&]v=([A-Za-z0-9_-]{6,})/i.exec(s);
    if (listMatch && vMatch) return 'mixed_video_in_playlist';
    if (listMatch)            return 'playlist';
    if (vMatch || /youtu\.be\/[A-Za-z0-9_-]{6,}/.test(s)) return 'single_video';
    return 'single_video';
  }

  async function _enqueue(endpoint, body, ctrl) {
    const resp = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    const json = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = new Error((json && json.detail && json.detail.error_message)
        || json.error_message || (typeof json.detail === 'string' ? json.detail : '')
        || `HTTP ${resp.status}`);
      err.code = (json && json.detail && json.detail.error_code) || json.error_code;
      err.hint = (json && json.detail && json.detail.hint) || json.hint;
      err.status = resp.status;
      throw err;
    }
    return json;
  }

  function start(args) {
    const ctrl = new AbortController();
    const onState = args.onState || function() {};
    let jobId = null;
    let retriedNormalizeFlip = false;
    let finished = false;

    onState({ phase: 'queued', percent: null });

    async function go(body) {
      const endpoint = body.tracks ? '/api/download/album/enqueue' : '/api/download/enqueue';
      let enq;
      try {
        enq = await _enqueue(endpoint, body, ctrl);
      } catch (err) {
        // Auto-retry once on normalize_unsupported_for_original (PRD §6.4 round-5 Min-2)
        if (err.code === 'normalize_unsupported_for_original' && !retriedNormalizeFlip) {
          retriedNormalizeFlip = true;
          body.audio_format = 'mp3_320';
          body.normalize = body.normalize !== false;  // keep normalize=true
          try {
            try { localStorage.setItem('autocue_dl_format', 'mp3_320'); } catch (_) {}
            if (typeof showToast === 'function') {
              showToast('Normalization requires MP3 320 or WAV — switched to MP3 320');
            }
            // Reflect in UI dropdown if present
            const sel = document.getElementById('dl-format');
            if (sel) sel.value = 'mp3_320';
          } catch (_) {}
          return go(body);
        }
        if (err.name === 'AbortError') {
          if (!finished) { finished = true; onState({ status: 'cancelled', phase: 'done', type: 'done' }); }
          return;
        }
        if (!finished) {
          finished = true;
          onState({ type: 'done', status: 'error',
                    error_code: err.code || 'unknown',
                    error_message: err.message,
                    error_hint: err.hint });
        }
        return;
      }
      jobId = enq.job_id;
      try { onState({ phase: 'queued', percent: null, job_id: jobId }); } catch (_) {}
      if (typeof window._dlKickQueuePoller === 'function') window._dlKickQueuePoller();

      // Open SSE stream — handle 410 already_consumed explicitly per PRD §7.3.
      let stream;
      try {
        stream = await fetch(`/api/download/stream/${jobId}`, { signal: ctrl.signal });
      } catch (err) {
        if (err.name !== 'AbortError') {
          finished = true;
          onState({ type: 'done', status: 'error', error_code: 'network',
                    error_message: err.message });
        }
        return;
      }
      if (stream.status === 410) {
        let cached = {};
        try { cached = await stream.json(); } catch (_) {}
        finished = true;
        onState({ type: 'done', status: cached.status || 'success',
                  path: cached.path, from_cache: true, job_id: jobId });
        return;
      }
      if (!stream.ok) {
        finished = true;
        onState({ type: 'done', status: 'error', error_code: 'http',
                  error_message: `HTTP ${stream.status}` });
        return;
      }

      try {
        await _consumeSSE(stream, function(ev) {
          if (ev.type === 'done' && seenDoneFor.has(ev.job_id || jobId)) return;
          if (ev.type === 'done') {
            seenDoneFor.add(ev.job_id || jobId);
            finished = true;
          }
          onState(ev);
        }, ctrl.signal);
      } catch (err) {
        if (err.name !== 'AbortError' && !finished) {
          finished = true;
          onState({ type: 'done', status: 'error', error_code: 'stream',
                    error_message: err.message });
        }
      }
    }

    // Build request body from args
    const body = args.tracks ? {
      tracks: args.tracks,
      dest_dir: args.dest || undefined,
      audio_format: args.format || 'mp3_320',
      normalize: !!args.normalize,
      embed_metadata: args.embedMeta !== false,
    } : {
      query: args.query,
      dest_dir: args.dest || undefined,
      audio_format: args.format || 'mp3_320',
      normalize: !!args.normalize,
      embed_metadata: args.embedMeta !== false,
      allow_playlist: !!args.allowPlaylist,
    };

    go(body);

    return {
      get id() { return jobId; },
      async cancel() {
        try {
          if (jobId) {
            await fetch(`/api/download/cancel/${jobId}`, { method: 'POST' }).catch(function(){});
          }
        } finally {
          ctrl.abort();
        }
      },
      // For tests / clients that want to know the args originally passed
      _args: args,
    };
  }

  // ---- View helpers ----
  function _esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function _formatBytes(b) {
    if (b == null) return '';
    if (b < 1024) return b + ' B';
    if (b < 1048576) return (b / 1024).toFixed(1) + ' KB';
    if (b < 1073741824) return (b / 1048576).toFixed(1) + ' MB';
    return (b / 1073741824).toFixed(2) + ' GB';
  }

  function renderState(regionEl, state, ctx) {
    if (!regionEl) return;
    ctx = ctx || {};
    const phase = state.phase || (state.type === 'done' ? 'done' : null);

    if (state.type === 'done' && state.status === 'success') {
      const path = state.path || (ctx.lastPath || '');
      const folder = path ? path.replace(/[/\\][^/\\]+$/, '') : '';
      const showReveal = ctx.osRevealSupported !== false && path;
      regionEl.innerHTML = `
        <div class="dl-status-card" data-state="success" role="status">
          <div><strong>✓ Saved</strong> ${ctx.formatLabel ? '<span class="dl-status-line">as ' + _esc(ctx.formatLabel) + '</span>' : ''}</div>
          ${path ? '<div class="dl-status-line"><code>' + _esc(path) + '</code></div>' : ''}
          <div class="dl-status-actions">
            ${showReveal ? '<button type="button" data-dl-action="reveal">Reveal in Finder</button>' : ''}
            ${path ? '<button type="button" data-dl-action="copy-path">Copy path</button>' : ''}
            <button type="button" data-dl-action="reset" class="primary">Download another</button>
          </div>
        </div>`;
      regionEl.querySelectorAll('[data-dl-action]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          const action = btn.dataset.dlAction;
          if (action === 'reveal' && path) {
            fetch('/api/download/reveal', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ path: path }),
            }).then(function(r) {
              if (!r.ok) { if (typeof showToast === 'function') showToast("Couldn't open file manager"); }
            });
          } else if (action === 'copy-path' && path) {
            (navigator.clipboard ? navigator.clipboard.writeText(path) : Promise.reject())
              .then(function() { if (typeof showToast === 'function') showToast('Path copied'); })
              .catch(function() { if (typeof showToast === 'function') showToast("Couldn't copy"); });
          } else if (action === 'reset' && ctx.onReset) {
            ctx.onReset();
          }
        });
      });
    } else if (state.type === 'done' && state.status === 'error') {
      const msg = state.error_message || 'Something went wrong.';
      const hint = state.error_hint || '';
      const raw = state.error_raw || '';
      regionEl.innerHTML = `
        <div class="dl-status-card" data-state="error" role="alert">
          <div><strong>Couldn't download.</strong> ${_esc(msg)}</div>
          ${hint && hint !== 'auto_switch_to_mp3_320' ? '<div class="dl-status-line">' + _esc(hint) + '</div>' : ''}
          <div class="dl-status-actions">
            ${ctx.onRetry ? '<button type="button" data-dl-action="retry" class="primary">Retry</button>' : ''}
            <button type="button" data-dl-action="dismiss">Dismiss</button>
          </div>
          ${raw ? '<details><summary>Show technical details</summary><pre>' + _esc(raw) + '</pre></details>' : ''}
        </div>`;
      regionEl.querySelectorAll('[data-dl-action]').forEach(function(btn) {
        btn.addEventListener('click', function() {
          if (btn.dataset.dlAction === 'retry' && ctx.onRetry) ctx.onRetry();
          if (btn.dataset.dlAction === 'dismiss' && ctx.onReset) ctx.onReset();
        });
      });
    } else if (state.type === 'done' && state.status === 'cancelled') {
      regionEl.innerHTML = `
        <div class="dl-status-card" data-state="loading"><div class="dl-status-line">Cancelled</div></div>`;
      setTimeout(function() { if (regionEl && ctx.onReset) ctx.onReset(); }, 1200);
    } else if (phase) {
      const percent = (state.percent == null) ? null : Math.max(0, Math.min(100, state.percent));
      const totalTracks = state.total > 1 ? state.total : null;
      const proc = state.processed || 0;
      const title = state.current_title || state.current_query || '';
      const phaseText = ({
        queued: 'Queued…',
        fetching: 'Downloading…',
        converting: 'Converting…',
        normalizing_pass1: 'Measuring loudness…',
        normalizing_pass2: 'Normalizing loudness…',
        tagging: 'Writing metadata…',
      })[phase] || phase;
      // Update the card in place — rebuilding it per SSE event recreated the
      // <progress> element (so its width transition never fired and the bar
      // stuttered) and destroyed the Cancel button mid-focus.
      let card = regionEl.querySelector('.dl-status-card[data-state="loading"]');
      if (!card) {
        regionEl.innerHTML = `
        <div class="dl-status-card" data-state="loading">
          <div><strong data-dl-phase></strong> <span class="dl-status-line" data-dl-pct></span></div>
          <progress max="100" aria-label="Download progress"></progress>
          <div class="dl-status-line" data-dl-batch style="display:none"></div>
          <div class="dl-status-actions">
            <button type="button" data-dl-action="cancel">Cancel</button>
          </div>
        </div>`;
        card = regionEl.querySelector('.dl-status-card');
        card.querySelectorAll('[data-dl-action="cancel"]').forEach(function(btn) {
          btn.addEventListener('click', function() { if (ctx.onCancel) ctx.onCancel(); });
        });
      }
      card.querySelector('[data-dl-phase]').textContent = phaseText;
      card.querySelector('[data-dl-pct]').textContent = percent != null ? percent.toFixed(0) + '%' : '';
      const progEl = card.querySelector('progress');
      if (percent != null) progEl.value = percent;
      else progEl.removeAttribute('value');
      const batchEl = card.querySelector('[data-dl-batch]');
      if (totalTracks) {
        batchEl.style.display = '';
        batchEl.textContent = `Track ${proc + 1} of ${totalTracks}${title ? ' · ' + title : ''}`;
      } else {
        batchEl.style.display = 'none';
      }
    } else {
      regionEl.innerHTML = '';
    }
  }

  // ---- Surface bindings ----
  function bindManualPanel(rootEl) {
    rootEl = rootEl || document.getElementById('download-section');
    if (!rootEl) return;
    const region = rootEl.querySelector('#dl-status-region');
    const queryEl = rootEl.querySelector('#dl-query');
    const formatEl = rootEl.querySelector('#dl-format');
    const normEl = rootEl.querySelector('#dl-normalize');
    const metaEl = rootEl.querySelector('#dl-embed-meta');
    const goBtn = rootEl.querySelector('#dl-go-btn');
    const controls = rootEl.querySelector('#download-controls');
    const wavWarning = rootEl.querySelector('#dl-wav-warning');
    const wavDismiss = rootEl.querySelector('#dl-wav-warning-dismiss');
    let currentJob = null;
    let lastArgs = null;

    function _readFormat() {
      const v = formatEl ? formatEl.value : 'mp3_320';
      try { localStorage.setItem('autocue_dl_format', v); } catch (_) {}
      return v;
    }
    function _formatLabel(v) {
      return { mp3_320: 'MP3 320', wav: 'WAV', original: 'Original' }[v] || v;
    }

    function _refreshNormalizeAvailability() {
      const fmt = formatEl ? formatEl.value : 'mp3_320';
      if (!normEl) return;
      if (fmt === 'original') {
        normEl.checked = false;
        normEl.disabled = true;
        normEl.setAttribute('aria-disabled', 'true');
        if (normEl.parentElement) normEl.parentElement.title = 'Available only for WAV / MP3 320';
      } else {
        normEl.disabled = false;
        normEl.removeAttribute('aria-disabled');
        if (normEl.parentElement) normEl.parentElement.removeAttribute('title');
      }
    }

    function _reset() {
      if (region) region.innerHTML = '';
      if (queryEl) { queryEl.value = ''; queryEl.focus(); }
      if (controls) controls.removeAttribute('aria-busy');
      currentJob = null;
    }

    function _start() {
      if (currentJob) return;
      const q = (queryEl ? queryEl.value : '').trim();
      if (!q) {
        if (typeof showToast === 'function') showToast('Enter a URL or search term');
        return;
      }
      // Route bare-text search through the YouTube candidate picker instead
      // of letting yt-dlp auto-pick result #1 (which often surfaces a random
      // video for ambiguous queries). URL inputs keep the direct flow.
      // PRP: search→modal route, prp-core/prp-implement.
      const targetKind = _classifyDownloadTarget(q);
      if (targetKind === 'search' && typeof openYoutubeModalForQuery === 'function') {
        openYoutubeModalForQuery(q);
        return;
      }
      const fmt = _readFormat();
      const args = {
        query: q,
        format: fmt,
        normalize: normEl ? normEl.checked : false,
        embedMeta: metaEl ? metaEl.checked : true,
        dest: (typeof _dlDestDir !== 'undefined' && _dlDestDir) || undefined,
        allowPlaylist: ['playlist', 'mixed_video_in_playlist'].includes(targetKind),
      };
      lastArgs = args;
      if (controls) controls.setAttribute('aria-busy', 'true');
      currentJob = start(Object.assign({}, args, {
        onState: function(state) {
          renderState(region, state, {
            osRevealSupported: (window._downloadConfig || {}).os_reveal_supported,
            formatLabel: _formatLabel(fmt),
            onCancel: function() { if (currentJob) currentJob.cancel(); },
            onRetry: function() { _reset(); currentJob = null; queryEl && (queryEl.value = q); _start(); },
            onReset: function() { _reset(); },
          });
          if (state.type === 'done') {
            if (controls) controls.removeAttribute('aria-busy');
            // Job done — null currentJob so a subsequent retry/reset works.
            currentJob = null;
          }
        },
      }));
    }

    if (goBtn) goBtn.addEventListener('click', function(e) { e.preventDefault(); _start(); });
    if (queryEl) queryEl.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); _start(); }
    });
    if (formatEl) {
      formatEl.addEventListener('change', function() {
        _refreshNormalizeAvailability();
        _readFormat();
        if (formatEl.value === 'wav' && wavWarning) {
          let seen = false;
          try { seen = sessionStorage.getItem('autocue_dl_seen_wav_warning') === '1'; } catch (_) {}
          if (!seen) wavWarning.hidden = false;
        }
      });
    }
    if (wavDismiss && wavWarning) wavDismiss.addEventListener('click', function() {
      wavWarning.hidden = true;
      try { sessionStorage.setItem('autocue_dl_seen_wav_warning', '1'); } catch (_) {}
    });
    // Restore last format from localStorage; coerce legacy values.
    if (formatEl) {
      let saved = null;
      try { saved = localStorage.getItem('autocue_dl_format'); } catch (_) {}
      const legacy = { mp3: 'mp3_320', m4a: 'original', aac: 'original',
                       opus: 'original', flac: 'wav', alac: 'wav', vorbis: 'wav' };
      if (saved && legacy[saved]) {
        formatEl.value = legacy[saved];
        try { localStorage.setItem('autocue_dl_format', legacy[saved]); } catch (_) {}
        if (typeof showToast === 'function') {
          showToast(`Your saved format is now ${_formatLabel(legacy[saved])}. Change in the Format dropdown.`);
        }
      } else if (saved && ['wav', 'mp3_320', 'original'].includes(saved)) {
        formatEl.value = saved;
      }
      _refreshNormalizeAvailability();
    }

    // Public API for tests / external integration
    return { _start: _start, _reset: _reset,
             get currentJob() { return currentJob; },
             get lastArgs() { return lastArgs; } };
  }

  function bindCardButton(btnEl, query, opts) {
    if (!btnEl) return;
    opts = opts || {};
    let job = null;
    const orig = btnEl.textContent;
    let inlineStatus = btnEl.parentElement && btnEl.parentElement.querySelector('.disc-dl-status');
    let inlineBar    = btnEl.parentElement && btnEl.parentElement.querySelector('.disc-dl-bar');
    let inlineProg   = btnEl.parentElement && btnEl.parentElement.querySelector('.disc-dl-progress');
    btnEl.addEventListener('click', function() {
      if (job) { job.cancel(); return; }
      if (inlineProg) inlineProg.style.display = '';
      btnEl.textContent = 'Cancel';
      job = start({
        query: query,
        format: opts.format || (function() { try { return localStorage.getItem('autocue_dl_format') || 'mp3_320'; } catch(_){ return 'mp3_320'; } })(),
        normalize: !!opts.normalize,
        embedMeta: opts.embedMeta !== false,
        dest: opts.dest || (typeof _dlDestDir !== 'undefined' && _dlDestDir) || undefined,
        onState: function(state) {
          if (state.type === 'done') {
            if (state.status === 'success') {
              btnEl.textContent = '✓ Saved';
              if (inlineStatus) { inlineStatus.textContent = '✓ saved'; inlineStatus.style.color = 'var(--green)'; }
              if (inlineBar) inlineBar.style.width = '100%';
            } else if (state.status === 'cancelled') {
              btnEl.textContent = orig;
              if (inlineStatus) inlineStatus.textContent = 'cancelled';
            } else {
              btnEl.textContent = orig;
              if (inlineStatus) { inlineStatus.textContent = '✗ failed'; inlineStatus.style.color = 'var(--red, #e05252)'; }
              if (typeof showToast === 'function') showToast('Download failed: ' + (state.error_message || ''));
            }
            job = null;
            setTimeout(function() { if (inlineProg) inlineProg.style.display = 'none'; }, 1500);
          } else if (typeof state.percent === 'number') {
            if (inlineBar) inlineBar.style.width = state.percent + '%';
            if (inlineStatus) inlineStatus.textContent = Math.round(state.percent) + '%';
          } else if (state.phase) {
            if (inlineStatus) inlineStatus.textContent = state.phase.replace('_', ' ');
          }
        },
      });
    });
  }

  return {
    start: start,
    bindManualPanel: bindManualPanel,
    bindCardButton: bindCardButton,
    renderState: renderState,
    _classifyDownloadTarget: _classifyDownloadTarget,
  };
})();

function initDiscover() {
  // Default "released since" to last year (v1 control, retained for the
  // Download panel below — Discover v2 has its own filter bar).
  const yearInput = document.getElementById('disc-since-year');
  if (yearInput && !yearInput.value) yearInput.value = String(new Date().getFullYear() - 1);

  // T-024: the v1 "Scan library" button (#disc-scan-btn) was removed from the
  // DOM when the Discover tab was rewritten. Its click handler is now wired
  // up in initDiscoverV2(). The download button below still belongs to the
  // shared YouTube download panel.
  // Wire the manual panel through the canonical _Download IIFE.
  // (Old downloadManual / runDownload functions are kept further down but
  // unreferenced; they are slated for deletion in a follow-up cleanup pass.)
  if (window._Download) window._Download.bindManualPanel(document.getElementById('download-section'));

  // Probe download tool availability and reveal the matching UI.
  fetch('/api/download/config').then(r => r.ok ? r.json() : null).then(cfg => {
    if (!cfg) return;
    _downloadConfig = cfg;
    window._downloadConfig = cfg;
    _dlDestDir = cfg.music_folder || cfg.default_dir || '~/Music/AutoCue';
    const ready = cfg.available && cfg.ffmpeg;
    const controls = document.getElementById('download-controls');
    const unavail  = document.getElementById('download-unavailable');
    if (controls) controls.hidden = !ready;
    if (unavail) unavail.hidden = ready;
    const dest = document.getElementById('dl-dest');
    if (dest) dest.textContent = _dlDestDir;
    if (cfg.music_folder && cfg.default_dir && cfg.music_folder !== cfg.default_dir) {
      const switcher = document.getElementById('dl-dest-switch');
      if (switcher) {
        switcher.hidden = false;
        switcher.dataset.primary = cfg.music_folder;
        switcher.dataset.alt = cfg.default_dir;
        switcher.textContent = 'Switch to AutoCue folder';
      }
    }
    // Start the queue poller (paused when tab hidden)
    _startQueuePoller();
  }).catch(() => {});
}

// Queue indicator (PRD §6.12 + round-4 m1) — polls only while ≥ 1 job in flight
// AND tab is visible. Otherwise idles. Exposed _Download.kickQueuePoller() is
// called by the IIFE whenever a new job is enqueued, so the poller starts
// on demand instead of running unconditionally every 2 s.
let _dlQueuePollId = null;
let _dlQueueIdleTicks = 0;
const _DL_QUEUE_IDLE_MAX = 2;  // stop after 2 consecutive empty polls

function _stopQueuePoller() {
  if (_dlQueuePollId != null) { clearInterval(_dlQueuePollId); _dlQueuePollId = null; }
  _dlQueueIdleTicks = 0;
}

function _startQueuePoller() {
  function tick() {
    if (document.visibilityState !== 'visible') return;
    fetch('/api/download/queue').then(r => r.ok ? r.json() : null).then(snap => {
      const ind = document.getElementById('dl-queue-indicator');
      const txt = document.getElementById('dl-queue-text');
      if (!ind || !txt || !snap) return;
      const total = (snap.active ? snap.active.length : 0) + (snap.queued_count || 0);
      if (total === 0) {
        ind.hidden = true;
        _dlQueueIdleTicks++;
        if (_dlQueueIdleTicks >= _DL_QUEUE_IDLE_MAX) _stopQueuePoller();
        return;
      }
      _dlQueueIdleTicks = 0;
      ind.hidden = false;
      txt.textContent = `${snap.active.length} active · ${snap.queued_count} queued (max ${snap.max_concurrency} concurrent)`;
    }).catch(() => {});
  }
  if (_dlQueuePollId == null) {
    _dlQueuePollId = setInterval(tick, 2000);
    document.addEventListener('visibilitychange', function() {
      if (document.visibilityState === 'visible' && _dlQueuePollId == null) _startQueuePoller();
    }, { once: false });
    tick();
  }
}

// Public hook for the _Download IIFE — call on every successful enqueue.
window._dlKickQueuePoller = _startQueuePoller;

// Toggle download destination between music folder and AutoCue default folder.
document.addEventListener('click', e => {
  const sw = e.target.closest && e.target.closest('#dl-dest-switch');
  if (!sw) return;
  const cur = _dlDestDir;
  const pri = sw.dataset.primary;
  const alt = sw.dataset.alt;
  _dlDestDir = (cur === pri) ? alt : pri;
  const dest = document.getElementById('dl-dest');
  if (dest) dest.textContent = _dlDestDir;
  sw.textContent = (_dlDestDir === pri) ? 'Switch to AutoCue folder' : 'Switch to music folder';
});

// Style filter chips for discovery results.
function _renderStyleFilter(styles) {
  const container = document.getElementById('disc-style-filter');
  if (!container) return;
  if (!styles.length) { container.style.display = 'none'; return; }
  container.style.display = 'flex';
  container.innerHTML = '<span style="font-size:11px;color:var(--muted);margin-right:4px;flex-shrink:0;">Filter:</span>'
    + '<button class="tag-pill disc-sf-btn" style="cursor:pointer;" data-style="">All</button>'
    + [...styles].sort().map(s => `<button class="tag-pill disc-sf-btn" style="cursor:pointer;" data-style="${_esc(s)}">${_esc(s)}</button>`).join('');
  // Mark "All" active on first render
  const allBtn = container.querySelector('[data-style=""]');
  if (allBtn) allBtn.classList.add('active');
}

document.addEventListener('click', e => {
  const btn = e.target.closest && e.target.closest('.disc-sf-btn');
  if (!btn) return;
  document.querySelectorAll('.disc-sf-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const filter = btn.dataset.style;
  document.querySelectorAll('.disc-card').forEach(card => {
    const cardStyles = (card.dataset.styles || '').split(',');
    card.style.display = (!filter || cardStyles.includes(filter)) ? '' : 'none';
  });
});

// ===========================================================================
// Discover v2 (T-024) — DiscoverState + SSE consumer + card renderer
// ===========================================================================
//
// Replaces the v1 'scan library' panel above with a personalised feed driven
// by /api/discover/feed (SSE). State (saved / dismissed / followed / blocked)
// lives in the backend's discover.db; the JS just caches it for the session
// and refreshes on user actions. The CardRenderer + DetailPanel stubs live
// inline here; T-025+ (YouTube carousel, full keyboard shortcuts, settings
// panels) extend them.

const DiscoverV2 = (() => {
  const state = {
    cards: [],                  // in-render-order list of release dicts
    cardsByKey: new Map(),      // release_key → release dict
    savedKeys: new Set(),
    dismissedKeys: new Set(),
    snoozedKeys: new Set(),
    resurfacedKeys: new Set(),       // release_keys whose snooze has expired
    snoozedMeta: new Map(),          // release_key → {until_date}
    followedLabels: [],         // [{label_id, name, last_scanned_at}]
    blockedArtists: [],
    blockedLabels: [],
    scanRunning: false,
    scanFeeder: null,
    scanReleasesSeen: 0,
    scanFeedersDone: [],                                  // ordered list of feeders that have completed
    scanReleasesByFeeder: {artist: 0, label: 0, novelty: 0},
    scanSparseAdjacency: false,
    scanLastSummary: null,                                  // {releases_surfaced, requests_used, duration_ms}
    scanError: null,                                        // {kind, message, status}  — null when no error
    scanWarnings: [],                                       // [{feeder, message}] — non-fatal per-feeder errors
    tokenValid: null,           // null = unknown
    settingsOpen: false,
    youtubeByKey: new Map(),    // release_key → {status, candidates, error}
  };

  const subs = new Set();
  function subscribe(fn) { subs.add(fn); return () => subs.delete(fn); }
  function notify() { for (const fn of subs) { try { fn(state); } catch (e) { console.error(e); } } }

  // Issue #67: track the in-flight scan's AbortController so a new runScan()
  // can immediately abort the prior fetch (closing the SSE reader), instead
  // of racing the server lock.
  let _scanAbort = null;

  // ── HTTP helpers ────────────────────────────────────────────────────────
  async function _post(path, body) {
    const r = await fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: body ? JSON.stringify(body) : null,
    });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }
  async function _get(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  }

  // ── Initial state load ──────────────────────────────────────────────────
  async function loadInitialState() {
    try {
      const [saved, dismissed, snoozed, followed, blkA, blkL, tokenStatus] = await Promise.all([
        _get('/api/discover/saved'),
        _get('/api/discover/dismissed'),
        // include_resurfaced=true so we can tag cards that the user previously
        // snoozed and that have since reappeared in the feed.
        _get('/api/discover/snoozed?include_resurfaced=true'),
        _get('/api/discover/labels'),
        _get('/api/discover/blocked-artists'),
        _get('/api/discover/blocked-labels'),
        _get('/api/discover/token-status'),
      ]);
      state.savedKeys = new Set(saved.items.map(r => r.release_key));
      state.dismissedKeys = new Set(dismissed.items.map(r => r.release_key));
      // Split snoozed into still-active vs resurfaced (until_date in the past).
      const nowIso = new Date().toISOString();
      state.snoozedKeys = new Set();
      state.resurfacedKeys = new Set();
      state.snoozedMeta = new Map();
      for (const row of snoozed.items) {
        state.snoozedMeta.set(row.release_key, {until_date: row.until_date});
        if (row.until_date && row.until_date > nowIso) {
          state.snoozedKeys.add(row.release_key);
        } else {
          state.resurfacedKeys.add(row.release_key);
        }
      }
      state.followedLabels = followed.items;
      state.blockedArtists = blkA.items;
      state.blockedLabels = blkL.items;
      state.tokenValid = tokenStatus.valid;
    } catch (e) {
      console.warn('DiscoverV2: initial-state load failed', e);
    }
    notify();
  }

  // ── SSE feed consumer ───────────────────────────────────────────────────
  async function runScan() {
    // Issue #67: rapid filter toggles (e.g. Year "All" → "This year" → "All")
    // raced themselves into a 409. Two changes here:
    //   1. If a scan is in flight when a new one is requested, abort the
    //      prior fetch via its AbortController (closes the SSE reader
    //      immediately and triggers the prior runScan's catch branch, which
    //      flips scanRunning=false). Also fire the server-side cancel so the
    //      lock releases promptly. This kills the user-vs-self race.
    //   2. Do NOT pre-clear the existing cards. We only reset the card grid
    //      once the new fetch is confirmed OK (200). Error branches preserve
    //      the previously-rendered cards so a transient 409 / network blip
    //      does not blow away the user's feed.
    if (_scanAbort) {
      // Mark this as a self-initiated supersede so the aborted scan's catch
      // branch does NOT surface a misleading "network error" toast.
      _scanAbort.autocueSuperseded = true;
      try { _scanAbort.abort(); } catch (_) {}
      _scanAbort = null;
      try { await _post('/api/discover/feed/cancel', {}); } catch (_) {}
      // Yield one microtask so the prior runScan's catch can run and reset
      // scanRunning before we proceed.
      await Promise.resolve();
      // Issue #169: the cancel POST returns immediately but the server-side
      // orchestrator may still be blocked inside a long Discogs API call,
      // so the scan lock is held for another beat. Without waiting, the
      // next fetch lands while the lock is still held and 409s. The 409
      // handler (line ~5702) sets scanError but leaves the prior cards
      // visible — the user gets no signal that the filter change was
      // rejected. Poll /api/discover/feed/status until running=false (or
      // a 3s hard cap, so a stuck scan doesn't freeze the UI forever).
      const tNow = () => (typeof performance !== 'undefined' ? performance.now() : Date.now());
      const deadline = tNow() + 3000;
      while (tNow() < deadline) {
        let status;
        try { status = await _get('/api/discover/feed/status'); }
        catch (_) { break; }  // network glitch — let the next fetch handle it
        if (!status || !status.running) break;
        await new Promise(r => setTimeout(r, 150));
      }
    }
    state.scanRunning = true;
    state.scanFeeder = null;
    state.scanReleasesSeen = 0;
    state.scanFeedersDone = [];
    state.scanReleasesByFeeder = {artist: 0, label: 0, novelty: 0};
    state.scanSparseAdjacency = false;
    state.scanLastSummary = null;
    state.scanError = null;
    state.scanWarnings = [];
    notify();

    // Build query from filter chips.
    const sources = Array.from(
      document.querySelectorAll('#disc-v2-filter-bar input[data-source]:checked'),
    ).map(el => el.dataset.source);
    const yearVal = document.getElementById('disc-v2-year')?.value || '';
    const params = new URLSearchParams();
    if (sources.length) params.set('sources', sources.join(','));
    const currentYear = new Date().getFullYear();
    if (yearVal === 'this') params.set('year_from', String(currentYear));
    else if (yearVal === 'last2') params.set('year_from', String(currentYear - 1));
    else if (yearVal === 'last5') params.set('year_from', String(currentYear - 4));
    else if (yearVal === 'custom') {
      const custom = parseInt(document.getElementById('disc-v2-year-custom')?.value || '', 10);
      if (Number.isFinite(custom) && custom >= 1900 && custom <= 2099) {
        params.set('year_from', String(custom));
      }
    }

    const abort = new AbortController();
    _scanAbort = abort;
    let res;
    try {
      res = await fetch('/api/discover/feed?' + params.toString(), {signal: abort.signal});
    } catch (e) {
      // Aborted by a newer runScan() call — exit silently. The newer call
      // owns the state from here on.
      if (abort.autocueSuperseded || (e && e.name === 'AbortError')) {
        if (_scanAbort === abort) _scanAbort = null;
        state.scanRunning = false;
        notify();
        return;
      }
      // Network failure — surface as a structured error so the empty-state can
      // render a Retry button instead of going silent (PRD §9 — every empty
      // state must say WHY it's empty). Cards preserved (issue #67).
      if (_scanAbort === abort) _scanAbort = null;
      state.scanRunning = false;
      state.scanError = {kind: 'network', message: String(e && e.message || e)};
      notify();
      return;
    }
    if (res.status === 409) {
      // Issue #67: do NOT clear cards — keep the prior feed visible. The
      // error surface (empty-state / banner) reads scanError to explain
      // why the latest filter change did not refresh the grid.
      if (_scanAbort === abort) _scanAbort = null;
      state.scanRunning = false;
      state.scanError = {kind: 'conflict', status: 409,
                         message: 'A Discover scan is already running.'};
      notify();
      return;
    }
    if (res.status === 400) {
      let detail = '';
      try { const j = await res.json(); detail = j.detail || ''; } catch (_) {}
      if (_scanAbort === abort) _scanAbort = null;
      state.scanRunning = false;
      state.scanError = {kind: 'bad-request', status: 400,
                         message: detail || 'Bad request — check the filter parameters.'};
      notify();
      return;
    }
    if (!res.ok) {
      if (_scanAbort === abort) _scanAbort = null;
      state.scanRunning = false;
      state.scanError = {kind: 'http', status: res.status,
                         message: `Server returned HTTP ${res.status}.`};
      notify();
      return;
    }

    // Fetch confirmed OK — NOW clear the prior cards so the new stream
    // fills a fresh grid. Doing this here (rather than at the top of
    // runScan) keeps the existing feed visible across error responses
    // (issue #67).
    state.cards = [];
    state.cardsByKey.clear();
    notify();

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    try {
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += decoder.decode(value, {stream: true});
        const chunks = buf.split('\n\n');
        buf = chunks.pop();  // last partial chunk
        for (const chunk of chunks) _handleSSEChunk(chunk);
      }
    } catch (e) {
      // Issue #67: a mid-stream abort means a newer runScan() took over;
      // do NOT surface as an error.
      if (!(abort.autocueSuperseded || (e && e.name === 'AbortError'))) {
        // Reader broke mid-stream — partial results may still be visible.
        state.scanError = {kind: 'stream', message: String(e && e.message || e)};
      }
    }
    if (_scanAbort === abort) _scanAbort = null;
    state.scanRunning = false;
    notify();
  }

  function _handleSSEChunk(chunk) {
    let event = 'message';
    let data = null;
    for (const line of chunk.split('\n')) {
      if (line.startsWith('event: ')) event = line.slice(7).trim();
      else if (line.startsWith('data: ')) {
        try { data = JSON.parse(line.slice(6)); }
        catch (_) { /* ignore */ }
      }
    }
    if (!data) return;
    if (event === 'progress') {
      // Track transitions: when scanFeeder changes, the prior feeder is "done".
      if (state.scanFeeder && state.scanFeeder !== data.feeder &&
          !state.scanFeedersDone.includes(state.scanFeeder)) {
        state.scanFeedersDone.push(state.scanFeeder);
      }
      state.scanFeeder = data.feeder;
      notify();
    } else if (event === 'release') {
      state.cards.push(data);
      state.cardsByKey.set(data.release_key, data);
      state.scanReleasesSeen++;
      // Bucket release counts by feeder (release.source is "artist" / "label"
      // / "novelty:*" — collapse novelty:* to a single bucket).
      const src = (data.source || '').split(':')[0];
      if (Object.prototype.hasOwnProperty.call(state.scanReleasesByFeeder, src)) {
        state.scanReleasesByFeeder[src]++;
      }
      notify();
    } else if (event === 'sparse_adjacency') {
      state.scanSparseAdjacency = true;
      notify();
    } else if (event === 'warning') {
      // Non-fatal per-feeder warning — record it so the user sees what fell
      // through (e.g., a single artist with no recent releases).
      state.scanWarnings.push({
        feeder: data.feeder || 'unknown',
        message: data.exc || data.message || 'warning',
      });
      notify();
    } else if (event === 'done') {
      state.scanFeeder = null;
      state.scanLastSummary = {
        releases_surfaced: data.releases_surfaced,
        releases_seen: data.releases_seen,
        duration_ms: data.duration_ms,
      };
      notify();
    } else if (event === 'error') {
      // The orchestrator labels its own crashes with feeder === 'orchestrator';
      // a per-feeder error fall-back is non-fatal and goes into scanWarnings.
      state.scanFeeder = null;
      const isFatal = (data.feeder || '') === 'orchestrator';
      if (isFatal) {
        state.scanError = {kind: 'orchestrator', message: data.exc || 'scan crashed'};
      } else {
        state.scanWarnings.push({
          feeder: data.feeder || 'unknown',
          message: data.exc || 'feeder failed',
        });
      }
      notify();
    }
  }

  async function cancelScan() {
    try { await _post('/api/discover/feed/cancel', {}); } catch (_) {}
  }

  // ── State mutations ─────────────────────────────────────────────────────
  async function save(release) {
    await _post('/api/discover/save', {
      release_key: release.release_key,
      release_id: release.release?.id || 0,
      artist: release.release?.artist || '',
      title: release.release?.title || '',
      label: release.release?.label || '',
    });
    state.savedKeys.add(release.release_key);
    notify();
  }

  async function dismiss(release) {
    await _post('/api/discover/dismiss', {
      release_key: release.release_key,
      release_id: release.release?.id || 0,
      artist: release.release?.artist || '',
      title: release.release?.title || '',
    });
    state.dismissedKeys.add(release.release_key);
    notify();
  }

  async function snooze(release, duration) {
    await _post('/api/discover/snooze', {
      release_key: release.release_key,
      duration: duration || '1m',
      release_id: release.release?.id || 0,
      artist: release.release?.artist || '',
      title: release.release?.title || '',
    });
    state.snoozedKeys.add(release.release_key);
    notify();
  }

  async function loadDetail(releaseId) {
    return _get('/api/discover/releases/' + encodeURIComponent(releaseId));
  }

  // YouTube preview: lazy, per-release, cached for the session.
  //
  // Sends `artist` + `album` alongside the raw `q` query so the backend can
  // apply its token-mismatch filter: when at least one candidate is a
  // genuine match, hard mismatches (Vénissieux → corporate-services video,
  // Philip Glass → Schubert, etc.) are dropped server-side and `mismatch:
  // true` is flagged on any survivors. The frontend respects the flag
  // (drops mismatches from the carousel; falls back to "no clean match
  // found" if ALL results are flagged).
  async function searchYouTube(release, n = 3) {
    const key = release.release_key;
    const cached = state.youtubeByKey.get(key);
    if (cached && cached.status !== 'error') return cached;
    const r = release.release || {};
    const artist = r.artist || '';
    const title = r.title || r.album || '';
    const q = [artist, title].filter(Boolean).join(' ').trim();
    if (!q) {
      const empty = {status: 'loaded', candidates: []};
      state.youtubeByKey.set(key, empty);
      return empty;
    }
    state.youtubeByKey.set(key, {status: 'loading', candidates: []});
    const params = new URLSearchParams({q, n: String(n)});
    if (artist) params.set('artist', artist);
    if (title) params.set('album', title);
    try {
      const res = await _get('/api/youtube/search?' + params.toString());
      const entry = {status: 'loaded', candidates: res.candidates || []};
      state.youtubeByKey.set(key, entry);
      return entry;
    } catch (e) {
      const entry = {status: 'error', candidates: [], error: String(e)};
      state.youtubeByKey.set(key, entry);
      return entry;
    }
  }

  async function followLabel(labelId, name) {
    await _post('/api/discover/labels/follow', {label_id: labelId, name: name});
    await refreshFollowed();
  }
  async function unfollowLabel(labelId) {
    await _post('/api/discover/labels/unfollow', {label_id: labelId});
    await refreshFollowed();
  }
  async function refreshFollowed() {
    const r = await _get('/api/discover/labels');
    state.followedLabels = r.items;
    notify();
  }
  async function refreshBlocked() {
    const [a, l] = await Promise.all([
      _get('/api/discover/blocked-artists'),
      _get('/api/discover/blocked-labels'),
    ]);
    state.blockedArtists = a.items || [];
    state.blockedLabels = l.items || [];
    notify();
  }
  async function blockArtist(discogsArtistId, name) {
    await _post('/api/discover/block-artist',
                {discogs_artist_id: discogsArtistId, name: name});
    await refreshBlocked();
  }
  async function unblockArtist(discogsArtistId) {
    await _post('/api/discover/unblock-artist',
                {discogs_artist_id: discogsArtistId});
    await refreshBlocked();
  }
  async function blockLabel(discogsLabelId, name) {
    await _post('/api/discover/block-label',
                {discogs_label_id: discogsLabelId, name: name});
    await refreshBlocked();
  }
  async function unblockLabel(discogsLabelId) {
    await _post('/api/discover/unblock-label',
                {discogs_label_id: discogsLabelId});
    await refreshBlocked();
  }
  async function fetchSuggestedLabels(limit = 10) {
    const r = await _get('/api/discover/labels/suggested?limit=' + limit);
    return r.items;
  }
  async function searchLabels(query) {
    const r = await _get('/api/discover/labels/search?q=' + encodeURIComponent(query));
    return r.items;
  }
  async function refreshStats() {
    return _get('/api/discover/stats');
  }
  async function exportState() {
    const r = await fetch('/api/discover/state/export');
    if (!r.ok) throw new Error('export failed');
    return r.blob();
  }
  async function importState(file) {
    const r = await fetch('/api/discover/state/import', {
      method: 'POST',
      headers: {'Content-Type': 'application/gzip'},
      body: file,
    });
    if (!r.ok) throw new Error('import failed: HTTP ' + r.status);
    await loadInitialState();
    return r.json();
  }

  return {
    state, subscribe,
    loadInitialState, runScan, cancelScan,
    save, dismiss, snooze, loadDetail, searchYouTube,
    followLabel, unfollowLabel, refreshFollowed, fetchSuggestedLabels, searchLabels,
    blockArtist, unblockArtist, blockLabel, unblockLabel, refreshBlocked,
    refreshStats, exportState, importState,
  };
})();

// P5: expose the IIFE's public surface so the v2 Discover place
// (docs/js/v2/workbench/discover.js) can re-drive scans / state / detail
// through it — the place NEVER re-implements an /api/discover/* fetch or the
// SSE consumer (R10). Read-mostly; the returned object above is unchanged.
window.DiscoverV2 = DiscoverV2;
// P5: expose the detail-body renderer so the inspector re-host (T3) reuses the
// exact legacy tracklist/YouTube/actions markup instead of duplicating it.
window._renderDiscoverRenderDetail = _renderDetailBody;


// Card renderer + DOM event wiring -------------------------------------------

// Render the "🔁 Resurfaced" badge for releases the user previously snoozed
// and whose snooze has since expired. The until_date the snooze was set to
// (i.e., the resurface date) is shown as a hover tooltip.
function _resurfacedBadge(release) {
  if (!release || !DiscoverV2.state.resurfacedKeys.has(release.release_key)) return '';
  const meta = DiscoverV2.state.snoozedMeta && DiscoverV2.state.snoozedMeta.get(release.release_key);
  const dateStr = meta && meta.until_date ? meta.until_date.slice(0, 10) : '';
  const titleAttr = dateStr ? ` title="Snooze expired on ${_esc(dateStr)}"` : '';
  return ` <span class="disc-v2-resurfaced-badge"${titleAttr}>🔁 Resurfaced</span>`;
}

function _renderDiscoverV2Card(release) {
  const r = release.release || {};
  const isSaved = DiscoverV2.state.savedKeys.has(release.release_key);
  const card = document.createElement('div');
  card.className = 'disc-v2-card';
  card.setAttribute('data-release-key', release.release_key);
  card.setAttribute('role', 'button');
  card.setAttribute('tabindex', '0');
  const art = r.thumb || r.cover_image || '';
  // Map the feeder source to a human-readable origin label. The raw values
  // ("artist", "label", "novelty:style", "novelty:label", "novelty:artist")
  // came straight from the backend; "via artist" was jargon (UX audit M-5).
  const rawSource = (release.source || '');
  const SOURCE_LABEL = {
    artist:           'Artist match',
    label:            'Label match',
    'novelty':        'Novelty pick',
  };
  const sourceFamily = rawSource.split(':')[0];
  const sourceLabel = SOURCE_LABEL[sourceFamily] || sourceFamily;
  card.innerHTML = `
    <div class="disc-v2-card-art" style="${art ? `background-image:url('${_esc(art)}')` : ''}"></div>
    <div class="disc-v2-card-body">
      <p class="disc-v2-card-title">${_esc(r.title || 'Untitled')}</p>
      <p class="disc-v2-card-artist">${_esc(r.artist || 'Unknown Artist')}</p>
      <p class="disc-v2-card-source">${_esc(sourceLabel)}${r.label ? ' · ' + _esc(r.label) : ''}${r.year ? ' · ' + r.year : ''}${_resurfacedBadge(release)}</p>
    </div>
    <div class="disc-v2-card-actions" data-actions>
      <button class="disc-v2-card-action ${isSaved ? 'saved' : ''}" data-act="save" title="Save">${isSaved ? '✓' : '💚'}</button>
      <button class="disc-v2-card-action" data-act="snooze" title="Snooze (1w / 1m / 3m)">💤</button>
      <button class="disc-v2-card-action" data-act="dismiss" title="Dismiss">✕</button>
    </div>
  `;
  return card;
}

// Client-side resort + Explore-mode interleave.
//
// Backend returns cards already taste-ranked (the default). Switching to
// newest / title / artist re-sorts the existing fetch without re-running
// the scan. Explore mode interleaves novelty:non-novelty 50/50 so the user
// sees adjacent finds at the same rate as taste matches.
//
// Sort modes:
//   taste    no-op (preserve backend order, which is taste-ranked)
//   newest   sort by release.year DESC (releases without year come last)
//   title    sort by release.title (alpha, case-insensitive)
//   artist   sort by release.artist (alpha, case-insensitive)
//   explore  zip novelty + non-novelty round-robin (preserves intra-group order)
function _applyDiscoverV2Sort(cards, sortMode) {
  if (!cards || !cards.length) return cards || [];
  if (!sortMode || sortMode === 'taste') return cards.slice();
  if (sortMode === 'newest') {
    return cards.slice().sort((a, b) => {
      const ay = parseInt((a.release && a.release.year) || 0, 10) || 0;
      const by = parseInt((b.release && b.release.year) || 0, 10) || 0;
      return by - ay;
    });
  }
  const norm = (s) => String((s || '')).toLocaleLowerCase();
  if (sortMode === 'title') {
    return cards.slice().sort((a, b) =>
      norm(a.release && a.release.title).localeCompare(norm(b.release && b.release.title)));
  }
  if (sortMode === 'artist') {
    return cards.slice().sort((a, b) =>
      norm(a.release && a.release.artist).localeCompare(norm(b.release && b.release.artist)));
  }
  if (sortMode === 'explore') {
    const novelty = [];
    const other = [];
    for (const c of cards) {
      if ((c.source || '').startsWith('novelty')) novelty.push(c);
      else other.push(c);
    }
    const out = [];
    const max = Math.max(novelty.length, other.length);
    for (let i = 0; i < max; i++) {
      // Other first so the very first card is still a taste match — this
      // mirrors the PRD-locked "Explore mode (50/50)" expectation.
      if (i < other.length) out.push(other[i]);
      if (i < novelty.length) out.push(novelty[i]);
    }
    return out;
  }
  return cards.slice();
}

// Client-side filter predicate for the Discover feed. Extracted from
// `_renderDiscoverV2Feed` so the filter logic is unit-testable in isolation.
// `state` is the persisted filter state (search, selectedStyles, hideSaved,
// hideDismissed, year, customYear); `s` is the global DiscoverV2.state with
// dismissed/snoozed/saved keys.
function _applyDiscoverV2Filters(cards, filters, s) {
  const search = (filters.search || '').trim().toLowerCase();
  const styles = filters.selectedStyles instanceof Set
    ? filters.selectedStyles
    : new Set(filters.selectedStyles || []);
  const hideSaved = !!filters.hideSaved;
  const hideDismissed = filters.hideDismissed !== false; // default ON
  // Snoozed cards are ALWAYS hidden (they had an explicit "come back later"
  // action and would be noise to surface again until the snooze expires).
  return cards.filter((c) => {
    if (s.snoozedKeys && s.snoozedKeys.has(c.release_key)) return false;
    if (hideDismissed && s.dismissedKeys && s.dismissedKeys.has(c.release_key)) return false;
    if (hideSaved && s.savedKeys && s.savedKeys.has(c.release_key)) return false;
    const r = c.release || {};
    if (search) {
      const hay = (
        (r.artist || '') + ' ' +
        (r.title || '') + ' ' +
        (r.album || '') + ' ' +
        (r.label || '')
      ).toLowerCase();
      if (!hay.includes(search)) return false;
    }
    if (styles.size > 0) {
      const cardStyles = Array.isArray(r.styles) ? r.styles : [];
      if (!cardStyles.some((st) => styles.has(String(st).toLowerCase()))) return false;
    }
    return true;
  });
}

// Persistent filter state for the Discover feed. Loaded from localStorage on
// boot; selectedStyles is reconstituted as a Set so .has() works.
let _discoverFilters = (() => {
  try {
    const raw = JSON.parse(localStorage.getItem('ac_discover_filters') || '{}');
    return {
      search: typeof raw.search === 'string' ? raw.search : '',
      selectedStyles: new Set(Array.isArray(raw.selectedStyles) ? raw.selectedStyles : []),
      hideSaved: !!raw.hideSaved,
      hideDismissed: raw.hideDismissed !== false, // default ON
    };
  } catch {
    return { search: '', selectedStyles: new Set(), hideSaved: false, hideDismissed: true };
  }
})();

function _persistDiscoverFilters() {
  try {
    localStorage.setItem('ac_discover_filters', JSON.stringify({
      search: _discoverFilters.search,
      selectedStyles: Array.from(_discoverFilters.selectedStyles),
      hideSaved: _discoverFilters.hideSaved,
      hideDismissed: _discoverFilters.hideDismissed,
    }));
  } catch {}
}

// Rebuilds the style-chip strip from the styles present in the loaded feed.
// Only the styles that actually appear in the user's current cards become
// chips — no point offering "Bossa Nova" if no card has it. Chips reflect
// selection state from _discoverFilters.selectedStyles.
//
// Ghost-filter guard: after a re-scan the new feed may not contain a
// style the user previously selected. Without pruning, that selection
// stays in _discoverFilters.selectedStyles and silently filters every
// subsequent feed to an empty grid — the user sees no cards, no chip
// to un-toggle the filter, no clue what's wrong. We prune any selected
// style whose key is not present in ANY current card's `release.styles`
// (NOT just the top 16 — a style that survives in card N+17 should keep
// its filter; only truly-vanished styles get dropped).
function _renderDiscoverStyleChips() {
  const container = document.getElementById('disc-v2-style-chips');
  const clearBtn = document.getElementById('disc-v2-styles-clear');
  if (!container) return;
  const counts = new Map();
  const allStyles = new Set();
  for (const c of DiscoverV2.state.cards) {
    const styles = Array.isArray(c.release?.styles) ? c.release.styles : [];
    for (const st of styles) {
      const key = String(st).toLowerCase();
      if (!key) continue;
      counts.set(key, (counts.get(key) || 0) + 1);
      allStyles.add(key);
    }
  }
  // Prune ghost selections — those whose style has fully disappeared from
  // the feed since the user toggled them. Persist the trimmed set so the
  // ghost doesn't come back on the next reload.
  let prunedAny = false;
  for (const key of Array.from(_discoverFilters.selectedStyles)) {
    if (!allStyles.has(key)) {
      _discoverFilters.selectedStyles.delete(key);
      prunedAny = true;
    }
  }
  if (prunedAny) _persistDiscoverFilters();

  if (counts.size === 0) {
    container.style.display = 'none';
    if (clearBtn) clearBtn.style.display = 'none';
    return;
  }
  // Top 16 most common styles — keeps the strip short. Sorted by count desc.
  const top = Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, 16);
  container.style.display = 'flex';
  container.innerHTML = top.map(([key, n]) => {
    const active = _discoverFilters.selectedStyles.has(key) ? ' active' : '';
    return `<label class="disc-v2-chip${active}" data-style="${_esc(key)}">` +
      `<input type="checkbox" data-style-key="${_esc(key)}"${active ? ' checked' : ''}> ` +
      `${_esc(key.replace(/\b\w/g, (c) => c.toUpperCase()))} <span style="color:var(--muted)">${n}</span></label>`;
  }).join('');
  if (clearBtn) {
    clearBtn.style.display = _discoverFilters.selectedStyles.size > 0 ? '' : 'none';
  }
}

// Render the inline scan-error banner (issue #169) when a refresh failed
// but the user still has prior cards visible — the empty-state-only path
// in _renderDiscoverV2Feed wouldn't surface anything in that case. Hide
// the banner whenever a new scan is in flight or when scanError is null.
function _renderDiscoverScanErrorInline() {
  const el = document.getElementById('disc-v2-scan-error-inline');
  const msgEl = document.getElementById('disc-v2-scan-error-inline-msg');
  if (!el || !msgEl) return;
  const s = DiscoverV2.state;
  if (s.scanRunning || !s.scanError) {
    el.style.display = 'none';
    return;
  }
  const e = s.scanError;
  let text;
  if (e.kind === 'conflict') {
    text = 'Filter change ignored — a Discover scan is still running. Try again in a moment.';
  } else if (e.kind === 'network') {
    text = 'Network error while updating the feed: ' + (e.message || 'connection failed') + '.';
  } else if (e.kind === 'bad-request') {
    text = 'Filter rejected: ' + (e.message || 'bad request') + '.';
  } else {
    text = 'Feed update failed: ' + (e.message || 'unknown error') + '.';
  }
  msgEl.textContent = text;
  el.style.display = 'flex';
}

function _renderDiscoverV2Feed() {
  const grid = document.getElementById('disc-v2-grid');
  if (!grid) return;
  // Stagger only when the grid populates from empty (post-scan reveal) —
  // re-staggering on every save/dismiss re-render would read as flicker.
  const freshRender = !grid.children.length;
  grid.innerHTML = '';

  const s = DiscoverV2.state;
  const sortMode = document.getElementById('disc-v2-sort')?.value || 'taste';
  // Refresh the style chips from the current feed before filtering so the
  // user can toggle a style that just appeared.
  _renderDiscoverStyleChips();
  _renderDiscoverScanErrorInline();
  // Apply client-side filters (search / styles / hide-saved / hide-dismissed).
  // Snoozed cards are unconditionally hidden inside _applyDiscoverV2Filters.
  const filtered = _applyDiscoverV2Filters(s.cards, _discoverFilters, s);
  const visible = _applyDiscoverV2Sort(filtered, sortMode);

  const emptyEl = document.getElementById('disc-v2-empty-state');
  const emptyMsg = document.getElementById('disc-v2-empty-state-msg');
  const emptyAction = document.getElementById('disc-v2-empty-action');

  if (s.scanRunning) {
    if (emptyEl) emptyEl.style.display = 'none';
  } else if (!visible.length) {
    if (emptyEl) {
      emptyEl.style.display = '';
      emptyAction.style.display = 'none';
      // Order: token missing > scan error > no labels > filters too tight > truly empty.
      if (s.tokenValid === false) {
        emptyMsg.innerHTML = '<strong>Discogs token invalid or missing.</strong> ' +
          'Configure <code>DISCOGS_TOKEN</code> in <code>.env</code> and restart the server.';
      } else if (s.scanError) {
        const e = s.scanError;
        // For 'conflict' (409) errors the suggestion isn't "wait for the
        // other scan" because in practice the lock has usually since cleared
        // (PR #61 prevents orchestrator-crash leaks). Just nudge the user
        // to retry — the next click does the right thing.
        emptyMsg.innerHTML =
          '<strong>Couldn’t finish the scan.</strong> ' + _esc(e.message || '') +
          (e.kind === 'conflict' ? ' Click Refresh to try again.' : '');
        emptyAction.style.display = '';
        emptyAction.textContent = 'Refresh';
        emptyAction.onclick = () => DiscoverV2.runScan();
      } else if (!s.followedLabels.length) {
        emptyMsg.textContent = 'Follow some labels to start seeing releases.';
        emptyAction.style.display = '';
        emptyAction.textContent = 'Pick from your library';
        emptyAction.onclick = () => _openOnboarding();
      } else if (s.cards.length > 0) {
        // We scanned + got cards, but all are dismissed/snoozed. That's an
        // ALL-FILTERED state, not a no-results state.
        emptyMsg.textContent =
          `Everything from this scan is already dismissed or snoozed (${s.cards.length} hidden). ` +
          'Try a different sort or run Refresh.';
        emptyAction.style.display = '';
        emptyAction.textContent = 'Refresh';
        emptyAction.onclick = () => DiscoverV2.runScan();
      } else if (s.scanLastSummary && s.scanLastSummary.releases_surfaced === 0) {
        emptyMsg.textContent =
          'No new releases right now. The labels you watch haven’t posted anything new.';
      } else {
        emptyMsg.textContent =
          'No new releases yet. Click Refresh to run your first scan.';
        emptyAction.style.display = '';
        emptyAction.textContent = 'Refresh';
        emptyAction.onclick = () => DiscoverV2.runScan();
      }
    }
    return;
  } else if (emptyEl) {
    emptyEl.style.display = 'none';
  }

  visible.forEach((release, i) => {
    const card = _renderDiscoverV2Card(release);
    if (freshRender && !_prefersReducedMotion) {
      card.classList.add('fade-in-up');
      card.style.animationDelay = (Math.min(i, 12) * 25) + 'ms'; // cap: rows below the fold don't wait
    }
    grid.appendChild(card);
  });
}

// PRD §4: per-feeder hard budgets. Used to compute the overall progress bar
// (sum of completed-feeder budgets / 60 total). Keep in sync with PRD.
const _DISC_V2_FEEDER_BUDGETS = {artist: 20, label: 15, novelty: 10};

function _feederProgressPercent(scanFeeder, feedersDone) {
  // Estimate "scan progress" as the budget consumed by completed feeders +
  // half of the current feeder's budget. Coarse but correct-direction.
  let consumed = 0;
  let total = 0;
  for (const f of ['artist', 'label', 'novelty']) {
    const budget = _DISC_V2_FEEDER_BUDGETS[f] || 0;
    total += budget;
    if (feedersDone.includes(f)) consumed += budget;
    else if (scanFeeder === f) consumed += Math.round(budget * 0.5);
  }
  return total ? Math.round((consumed / total) * 100) : 0;
}

// Per-feeder non-fatal warning bar — visible until the next scan starts.
function _renderDiscoverV2ScanWarnings() {
  const el = document.getElementById('disc-v2-scan-warnings');
  if (!el) return;
  const w = DiscoverV2.state.scanWarnings || [];
  if (!w.length) {
    el.style.display = 'none';
    return;
  }
  el.style.display = '';
  // Collapse duplicates and surface a count next to each feeder.
  const byFeeder = new Map();
  for (const x of w) byFeeder.set(x.feeder, (byFeeder.get(x.feeder) || 0) + 1);
  const lines = Array.from(byFeeder.entries()).map(
    ([f, n]) => `⚠ ${_esc(f)} (${n})`
  );
  el.innerHTML =
    `<strong>Some feeders had trouble:</strong> ${lines.join(' · ')} ` +
    `<span style="color:var(--muted);">— partial results shown below.</span>`;
}

function _renderDiscoverV2ScanProgress() {
  const el = document.getElementById('disc-v2-scan-progress');
  const label = document.getElementById('disc-v2-scan-progress-label');
  const breakdown = document.getElementById('disc-v2-scan-progress-breakdown');
  const warning = document.getElementById('disc-v2-scan-progress-warning');
  const fill = document.getElementById('disc-v2-scan-progress-fill');
  const delta = document.getElementById('disc-v2-scan-delta');
  if (!el || !label) return;
  const s = DiscoverV2.state;

  if (s.scanRunning) {
    el.style.display = '';
    if (delta) delta.style.display = 'none';

    const feeder = s.scanFeeder || 'starting';
    const count = s.scanReleasesSeen;
    label.textContent = `Scanning ${feeder}… ${count} releases found so far`;

    if (breakdown) {
      const parts = ['artist', 'label', 'novelty'].map(f => {
        const n = s.scanReleasesByFeeder[f] || 0;
        const budget = _DISC_V2_FEEDER_BUDGETS[f];
        const status =
          f === s.scanFeeder ? '🔄' :
          s.scanFeedersDone.includes(f) ? '✓' :
          '·';
        return `<span data-feeder="${f}">${status} ${f} ${n} <span style="color:var(--muted);">(budget ${budget})</span></span>`;
      });
      breakdown.innerHTML = parts.join('');
    }

    if (warning) {
      if (s.scanSparseAdjacency) {
        warning.style.display = '';
        warning.textContent = '⚠ Sparse adjacency — novelty feeder may surface fewer adjacent finds than usual.';
      } else {
        warning.style.display = 'none';
      }
    }

    if (fill) {
      fill.style.width = _feederProgressPercent(s.scanFeeder, s.scanFeedersDone) + '%';
    }
    return;
  }

  // Scan not running.
  el.style.display = 'none';

  // If a scan just completed and we have a summary, surface the delta strip.
  if (delta && s.scanLastSummary) {
    const sum = s.scanLastSummary;
    const seconds = sum.duration_ms != null ? (sum.duration_ms / 1000).toFixed(1) : '?';
    delta.style.display = '';
    delta.textContent =
      `✓ Found ${sum.releases_surfaced} new releases in ${seconds}s ` +
      `(${sum.releases_seen} scanned, ${sum.releases_seen - sum.releases_surfaced} deduped).`;
  } else if (delta) {
    delta.style.display = 'none';
  }
}

// The onboarding banner is auto-loaded the first time it becomes visible per
// session. The flag lives on a module-scope variable (not localStorage) so a
// page reload re-fetches in case the user's library grew.
let _onboardingLoaded = false;

function _renderDiscoverV2Onboarding() {
  const banner = document.getElementById('disc-v2-onboarding-banner');
  if (!banner) return;
  // Show only when no labels are followed AND we haven't been told to skip.
  const shouldShow =
    DiscoverV2.state.followedLabels.length === 0 &&
    !localStorage.getItem('disc-v2-onboarding-skipped');
  if (shouldShow) {
    banner.style.display = '';
    if (!_onboardingLoaded) {
      _onboardingLoaded = true;
      _loadOnboardingSuggestions();
    }
  } else {
    banner.style.display = 'none';
  }
}

// _openOnboarding is the empty-state action ("Pick from your library") that
// reveals the banner regardless of the skipped flag. It re-fires the load too
// so a user who originally skipped sees fresh suggestions.
async function _openOnboarding() {
  const banner = document.getElementById('disc-v2-onboarding-banner');
  if (!banner) return;
  banner.style.display = '';
  // Re-fetch even if previously loaded — the user explicitly asked.
  _onboardingLoaded = true;
  await _loadOnboardingSuggestions();
}

async function _loadOnboardingSuggestions() {
  const container = document.getElementById('disc-v2-onboarding-suggestions');
  if (!container) return;
  container.innerHTML = '<em style="color:var(--muted);">Loading suggestions…</em>';
  try {
    const suggestions = await DiscoverV2.fetchSuggestedLabels(10);
    container.innerHTML = '';
    if (!suggestions.length) {
      container.innerHTML = '<em style="color:var(--muted);">No suggested labels — your library has no Discogs label metadata yet.</em>';
      return;
    }
    suggestions.forEach(sug => {
      const chip = document.createElement('button');
      chip.className = 'secondary-btn';
      chip.style.fontSize = '11px';
      chip.setAttribute('data-suggest-name', sug.name);
      chip.textContent = sug.name;
      // Tooltip + visible chip text both name the suggestion source so the
      // user knows WHY a label appears (UX audit M-3 — recognition-over-recall).
      const tooltip = sug.weight != null
        ? `Suggested from your library (relevance: ${sug.weight.toFixed(1)})`
        : 'Suggested from your library';
      chip.title = tooltip;
      chip.addEventListener('click', async () => {
        chip.disabled = true;
        chip.textContent = '… ' + sug.name;
        const followed = await _followByName(sug.name);
        if (followed) {
          chip.textContent = '✓ ' + sug.name;
        } else {
          // UX audit Issue 10: previously the chip silently re-enabled on
          // failure, hiding the fact that "Add all" only added 8 of 10. Now
          // we mark unresolved chips with ⚠ + a tooltip explaining the
          // reason. The chip stays disabled (clicking it would just retry
          // the same failing search).
          chip.disabled = true;
          chip.classList.add('disc-v2-suggest-failed');
          chip.textContent = '⚠ ' + sug.name;
          chip.title = `Couldn't find "${sug.name}" on Discogs. The library label name may contain a catalog code or disambiguator.`;
        }
      });
      container.appendChild(chip);
    });
  } catch (_) {
    container.innerHTML = '<em style="color:var(--muted);">Could not load suggestions.</em>';
  }
}

// Shared helper: the suggested-labels endpoint returns only `name` + `weight`,
// so to follow we have to resolve a Discogs label_id by searching. Returns
// truthy on success so callers can update their UI to ✓.
async function _followByName(name) {
  if (!name) return false;
  try {
    const hits = await DiscoverV2.searchLabels(name);
    if (!hits || !hits.length) return false;
    const top = hits[0];
    await DiscoverV2.followLabel(top.id, top.name || name);
    return true;
  } catch (_) {
    return false;
  }
}

function _renderDiscoverV2TokenBanner() {
  const banner = document.getElementById('disc-v2-token-banner');
  if (!banner) return;
  if (DiscoverV2.state.tokenValid === false) {
    banner.style.display = '';
    banner.innerHTML = '<strong>Discogs token invalid.</strong> Configure <code>DISCOGS_TOKEN</code> in <code>.env</code> and restart the server.';
  } else {
    banner.style.display = 'none';
  }
}

// Best-effort relative-time formatter for the followed-labels freshness column.
// Returns 'never' when no scan has happened yet so the empty state reads honestly.
function _relativeTime(iso, nowMs) {
  if (!iso) return 'never';
  const then = Date.parse(iso);
  if (!Number.isFinite(then)) return 'never';
  const now = nowMs || Date.now();
  const diffSec = Math.max(0, Math.floor((now - then) / 1000));
  if (diffSec < 60) return 'just now';
  const m = Math.floor(diffSec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  const months = Math.floor(d / 30);
  if (months < 12) return `${months}mo ago`;
  const years = Math.floor(d / 365);
  return `${years}y ago`;
}

function _renderDiscoverV2Followed() {
  const list = document.getElementById('disc-v2-followed-list');
  if (!list) return;
  const labels = DiscoverV2.state.followedLabels || [];
  if (!labels.length) {
    list.innerHTML = '<em style="color:var(--muted);">No labels followed yet. Click <strong>Suggest</strong> to seed from your library, or search for one above.</em>';
    return;
  }
  list.innerHTML = '';
  labels.forEach(label => {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.justifyContent = 'space-between';
    row.style.alignItems = 'center';
    row.style.padding = '4px 0';
    const freshness = _relativeTime(label.last_scanned_at);
    row.innerHTML =
      `<span><strong>${_esc(label.name)}</strong>` +
      `<span style="color:var(--muted);margin-left:6px;font-size:11px;">last scanned ${_esc(freshness)}</span></span>`;
    const unfollow = document.createElement('button');
    unfollow.className = 'secondary-btn';
    unfollow.style.fontSize = '11px';
    unfollow.textContent = 'Unfollow';
    unfollow.addEventListener('click', () => DiscoverV2.unfollowLabel(label.label_id));
    row.appendChild(unfollow);
    list.appendChild(row);
  });
}

// Render the "Suggested from your library" inline list. Each row has a
// Follow button that disables itself on success — so the user can fan-add
// without having to re-render the whole list between clicks. The suggested
// endpoint returns `{name, weight}` only — the Discogs label_id has to be
// resolved via /labels/search at follow time (see _followByName).
function _renderSuggestedLabels(suggestions) {
  const results = document.getElementById('disc-v2-label-suggest-results');
  if (!results) return;
  if (!suggestions || !suggestions.length) {
    results.innerHTML = '<em style="color:var(--muted);">No suggestions — your library has no Discogs label metadata yet.</em>';
    return;
  }
  results.innerHTML = '';
  const followedNames = new Set(
    (DiscoverV2.state.followedLabels || []).map(l => (l.name || '').toLowerCase())
  );
  suggestions.forEach(s => {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.justifyContent = 'space-between';
    row.style.alignItems = 'center';
    row.style.padding = '4px 0';
    const weight = (s.weight != null)
      ? ` <span style="color:var(--muted);font-size:11px;">(score ${_esc(String(s.weight))})</span>`
      : '';
    row.innerHTML = `<span>${_esc(s.name)}${weight}</span>`;
    const btn = document.createElement('button');
    btn.className = 'secondary-btn';
    btn.style.fontSize = '11px';
    if (followedNames.has((s.name || '').toLowerCase())) {
      btn.disabled = true;
      btn.textContent = '✓ Following';
    } else {
      btn.textContent = 'Follow';
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        btn.textContent = '…';
        const followed = await _followByName(s.name);
        if (followed) {
          btn.textContent = '✓ Following';
        } else {
          btn.disabled = false;
          btn.textContent = 'Follow';
        }
      });
    }
    row.appendChild(btn);
    results.appendChild(row);
  });
}

// Format a millisecond duration for the stats block.
function _formatStatsDuration(ms) {
  if (ms == null || !Number.isFinite(ms) || ms <= 0) return '–';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec - m * 60);
  return `${m}m ${s}s`;
}

function _formatStatsRatio(n) {
  if (n == null || !Number.isFinite(n)) return '–';
  if (n >= 1) return n.toFixed(1);
  return n.toFixed(2);
}

function _formatStatsPercent(n) {
  if (n == null || !Number.isFinite(n)) return '–';
  // Clamp into [0, 1] so a backend returning raw counts can't render
  // 1000% (UX audit Issue 4). Belt-and-braces: the backend has been
  // changed to return ratios but this clamp catches future regressions.
  const ratio = Math.max(0, Math.min(1, n));
  return `${Math.round(ratio * 100)}%`;
}

// Render the stats block surfaced under Settings → Stats. Skips empty
// sub-sections (no top labels yet, no novelty share recorded yet) so the
// block stays useful even on a fresh install.
function _renderDiscoverV2Stats(stats) {
  const block = document.getElementById('disc-v2-stats-block');
  if (!block) return;
  if (!stats) {
    block.innerHTML = '<em>No stats yet — run a scan first.</em>';
    return;
  }
  const noveltyShare = stats.novelty_share || {};
  const noveltyParts = Object.keys(noveltyShare).sort().map(k =>
    `${_esc(k)} ${_formatStatsPercent(noveltyShare[k])}`
  );
  const topLabels = (stats.top_labels || []).slice(0, 5);
  const topArtists = (stats.top_artists || []).slice(0, 5);

  const counts = [
    `<strong>${stats.total_scans}</strong> scans`,
    `<strong>${stats.saved_count}</strong> saved`,
    `<strong>${stats.dismissed_count}</strong> dismissed`,
    `<strong>${stats.snoozed_count}</strong> snoozed`,
    `<strong>${stats.downloaded_count}</strong> downloaded`,
    `<strong>${stats.followed_count}</strong> followed labels`,
  ];
  if (stats.blocked_artist_count || stats.blocked_label_count) {
    counts.push(
      `<strong>${stats.blocked_artist_count + stats.blocked_label_count}</strong> blocked`
    );
  }

  const rows = [];
  rows.push(`<div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px;">${counts.join(' · ')}</div>`);
  rows.push(
    `<div style="display:flex;gap:14px;flex-wrap:wrap;margin-bottom:8px;color:var(--muted);">` +
    `<span>avg scan: <strong style="color:var(--text);">${_formatStatsDuration(stats.avg_duration_ms)}</strong></span>` +
    `<span>saves per scan: <strong style="color:var(--text);">${_formatStatsRatio(stats.saves_per_scan)}</strong></span>` +
    `</div>`
  );
  if (noveltyParts.length) {
    rows.push(`<div style="margin-bottom:8px;color:var(--muted);">novelty mix: ${noveltyParts.join(' · ')}</div>`);
  }
  if (topLabels.length) {
    rows.push(
      `<div style="margin-bottom:4px;color:var(--muted);">top label sources: ` +
      topLabels.map(l => `${_esc(l.name || 'unknown')} (${l.count})`).join(' · ') +
      `</div>`
    );
  }
  if (topArtists.length) {
    rows.push(
      `<div style="color:var(--muted);">top artist sources: ` +
      topArtists.map(a => `${_esc(a.name || 'unknown')} (${a.count})`).join(' · ') +
      `</div>`
    );
  }
  block.innerHTML = rows.join('');
}

// Settings → Saved releases (UX audit M-4 — give 💚 Save a destination).
// Renders the result of /api/discover/saved as a compact list with an
// Unsave button per row. Auto-refreshes whenever Settings opens or the
// user saves a new card.
function _renderDiscoverV2Saved(rows) {
  const list = document.getElementById('disc-v2-saved-list');
  const count = document.getElementById('disc-v2-saved-count');
  if (!list) return;
  if (!rows || !rows.length) {
    list.innerHTML = 'No saved releases yet. Click 💚 on any card in the feed.';
    list.style.color = 'var(--muted)';
    if (count) count.textContent = '';
    return;
  }
  list.style.color = 'var(--text)';
  if (count) count.textContent = `(${rows.length})`;
  list.innerHTML = '';
  rows.forEach(r => {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.justifyContent = 'space-between';
    row.style.alignItems = 'center';
    row.style.padding = '4px 0';
    row.style.gap = '8px';
    const meta = document.createElement('span');
    meta.style.flex = '1';
    meta.style.minWidth = '0';
    meta.style.overflow = 'hidden';
    meta.style.textOverflow = 'ellipsis';
    meta.style.whiteSpace = 'nowrap';
    meta.innerHTML = `<strong>${_esc(r.title || 'Untitled')}</strong> <span style="color:var(--muted);">${_esc(r.artist || '')}${r.label ? ' · ' + _esc(r.label) : ''}</span>`;
    row.appendChild(meta);
    if (r.release_id) {
      const link = document.createElement('a');
      link.href = `https://www.discogs.com/release/${r.release_id}`;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = '↗';
      link.style.fontSize = '11px';
      link.title = 'Open on Discogs';
      row.appendChild(link);
    }
    const unsave = document.createElement('button');
    unsave.className = 'secondary-btn';
    unsave.style.fontSize = '11px';
    unsave.textContent = 'Unsave';
    unsave.addEventListener('click', async () => {
      unsave.disabled = true;
      unsave.textContent = '…';
      try {
        // Unsave is a backend mutation that mirrors the save action. The
        // /api/discover/save endpoint accepts {release_key} alone for the
        // delete path; if it doesn't, fall back to dismissing.
        await fetch('/api/discover/unsave', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({release_key: r.release_key}),
        });
      } catch (_) {}
      DiscoverV2.state.savedKeys.delete(r.release_key);
      _refreshSavedFromBackend();
    });
    row.appendChild(unsave);
    list.appendChild(row);
  });
}

async function _refreshSavedFromBackend() {
  try {
    const resp = await fetch('/api/discover/saved');
    const body = await resp.json();
    _renderDiscoverV2Saved(body.items || []);
  } catch (_) {}
}

function _renderDiscoverV2Blocked() {
  const list = document.getElementById('disc-v2-blocked-list');
  if (!list) return;
  const sa = DiscoverV2.state.blockedArtists || [];
  const sl = DiscoverV2.state.blockedLabels || [];
  if (!sa.length && !sl.length) {
    list.innerHTML = 'Nothing blocked. You can 🚫 block an artist or label from the release detail panel.';
    list.style.color = 'var(--muted)';
    return;
  }
  list.style.color = 'var(--text)';
  list.innerHTML = '';

  const _row = (kind, icon, name, id, unblockFn) => {
    const row = document.createElement('div');
    row.style.display = 'flex';
    row.style.justifyContent = 'space-between';
    row.style.alignItems = 'center';
    row.style.padding = '4px 0';
    row.setAttribute('data-blocked-kind', kind);
    row.setAttribute('data-blocked-id', String(id));
    row.innerHTML = `<span>${icon} ${_esc(name || 'unknown')}</span>`;
    const btn = document.createElement('button');
    btn.className = 'secondary-btn';
    btn.style.fontSize = '11px';
    btn.textContent = 'Unblock';
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      btn.textContent = '…';
      try {
        await unblockFn(id);
      } catch (_) {
        btn.disabled = false;
        btn.textContent = 'Unblock';
      }
    });
    row.appendChild(btn);
    return row;
  };

  if (sa.length) {
    const h = document.createElement('div');
    h.innerHTML = `<strong style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.04em;">Artists (${sa.length})</strong>`;
    list.appendChild(h);
    sa.forEach(a => list.appendChild(_row('artist', '🎤', a.name, a.discogs_artist_id, DiscoverV2.unblockArtist)));
  }
  if (sl.length) {
    const h = document.createElement('div');
    h.style.marginTop = sa.length ? '8px' : '0';
    h.innerHTML = `<strong style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:0.04em;">Labels (${sl.length})</strong>`;
    list.appendChild(h);
    sl.forEach(l => list.appendChild(_row('label', '🏷', l.name, l.discogs_label_id, DiscoverV2.unblockLabel)));
  }
}

// Detail panel ── proper dialog: focus trap, return-focus, click-outside-to-close.
// State tracked at module scope so _closeDetailPanel can restore focus.
let _detailReturnFocusEl = null;
let _detailCurrentRelease = null;
let _detailKeydownHandler = null;

async function _openDetailPanel(releaseKey) {
  // P5: when the v2 Discover place owns the workbench centre, the release
  // detail is re-hosted in the right inspector — suppress the legacy slide-in
  // panel entirely and route to the place's inspector re-host instead. The
  // flag-off / legacy-tab path still gets the slide-in below.
  if (window.AC2 && window.AC2.discover && window.AC2.discover.isActive && window.AC2.discover.isActive()) {
    window.AC2.discover.focusRelease(releaseKey);
    return;
  }
  const panel = document.getElementById('disc-v2-detail-panel');
  const backdrop = document.getElementById('disc-v2-detail-backdrop');
  const body = document.getElementById('disc-v2-detail-body');
  if (!panel || !body) return;
  const release = DiscoverV2.state.cardsByKey.get(releaseKey);
  if (!release) return;

  _detailReturnFocusEl = document.activeElement;
  _detailCurrentRelease = release;

  panel.setAttribute('aria-hidden', 'false');
  if (backdrop) backdrop.setAttribute('aria-hidden', 'false');

  // Render skeleton immediately from cached card data so the panel is responsive.
  _renderDetailBody(release, null, 'loading');

  // Install focus trap + Escape handler.
  _detailKeydownHandler = (ev) => _detailTrapKeydown(ev);
  document.addEventListener('keydown', _detailKeydownHandler);

  // Focus the close button after the panel paints (transform ends).
  setTimeout(() => {
    document.getElementById('disc-v2-detail-close-btn')?.focus();
  }, 50);

  try {
    const r = release.release || {};
    const detail = r.id ? await DiscoverV2.loadDetail(r.id) : null;
    _renderDetailBody(release, detail, 'loaded');
  } catch (e) {
    _renderDetailBody(release, null, 'error', String(e));
  }

  // YouTube preview is lazy: only fired after the Discogs detail has resolved
  // (or failed). It's intentionally fire-and-forget — failure shows an inline
  // message, never blocks the rest of the panel.
  _loadYouTubePreview(release);
}

function _extractYouTubeId(url) {
  if (!url) return null;
  const s = String(url);
  // Common shapes:
  //   https://www.youtube.com/watch?v=XXXXXXXXXXX
  //   https://youtu.be/XXXXXXXXXXX
  //   https://www.youtube.com/embed/XXXXXXXXXXX
  //   https://m.youtube.com/watch?v=XXXXXXXXXXX
  let m = s.match(/[?&]v=([A-Za-z0-9_-]{6,})/);
  if (m) return m[1];
  m = s.match(/youtu\.be\/([A-Za-z0-9_-]{6,})/);
  if (m) return m[1];
  m = s.match(/youtube\.com\/embed\/([A-Za-z0-9_-]{6,})/);
  if (m) return m[1];
  return null;
}

async function _loadYouTubePreview(release) {
  const slot = document.getElementById('disc-v2-detail-youtube-slot');
  if (!slot) return;
  slot.innerHTML = '<div class="disc-v2-yt-placeholder"><span class="disc-v2-spinner" aria-hidden="true"></span> Loading YouTube previews…</div>';
  const entry = await DiscoverV2.searchYouTube(release, 3);
  // The user may have closed (and re-opened a different) panel while we waited.
  // Only paint if this slot still belongs to the same release.
  if (!_detailCurrentRelease || _detailCurrentRelease.release_key !== release.release_key) return;
  if (entry.status === 'error') {
    slot.innerHTML = '<div class="disc-v2-yt-placeholder">Could not load YouTube previews: ' + _esc(entry.error || '') + '</div>';
    return;
  }
  const all = (entry.candidates || []).filter(c => _extractYouTubeId(c.url));
  if (!all.length) {
    slot.innerHTML = '<div class="disc-v2-yt-placeholder">No YouTube previews found for this release.</div>';
    return;
  }
  // The backend already drops mismatches when at least one match exists.
  // When every candidate is flagged `mismatch:true`, the backend kept them
  // ALL — that's the "no clean match found" fallback. Surface that to the
  // user with a clearer placeholder rather than silently playing wrong audio.
  const matches = all.filter(c => c.mismatch !== true);
  if (matches.length === 0) {
    const r = release.release || {};
    slot.innerHTML =
      '<div class="disc-v2-yt-placeholder">' +
      'No YouTube match found for <em>' + _esc(r.artist || '') +
      ' — ' + _esc(r.title || r.album || '') + '</em>. ' +
      'YouTube returned only mismatched results.' +
      '</div>';
    return;
  }
  _renderYouTubeCarousel(slot, matches, 0, release);
}

// Cheap mismatch heuristic for UX audit Issue 3: if neither the album nor
// the artist token appears in the YT result title, mark the result as a
// likely mismatch so the user notices before clicking play. Tokenizes both
// sides on whitespace + lowercases, then checks for any 4+ char overlap.
function _ytLikelyMismatch(ytTitle, expectedArtist, expectedAlbum) {
  const norm = (s) => String(s || '').toLowerCase().split(/[^a-z0-9]+/).filter(t => t.length >= 4);
  const haystack = new Set(norm(ytTitle));
  const needles = [...norm(expectedArtist), ...norm(expectedAlbum)];
  if (!needles.length || !haystack.size) return false;
  return !needles.some(n => haystack.has(n));
}

function _renderYouTubeCarousel(slot, candidates, index, releaseHint) {
  const cur = candidates[index];
  const videoId = _extractYouTubeId(cur.url);
  // rel=0 disables related-video sidebar; do NOT autoplay (avoid alert/dialog
  // analogue and respect user agency).
  const embedUrl = 'https://www.youtube.com/embed/' + encodeURIComponent(videoId) + '?rel=0';
  // UX audit Issue 3: surface YouTube result title + channel ABOVE the
  // iframe (not below) so the user spots an unrelated audiobook before
  // pressing play. Also flag obvious mismatches with a ⚠ icon.
  const r = releaseHint?.release || {};
  const expectedArtist = r.artist || '';
  const expectedAlbum = r.album || r.title || '';
  const mismatch = _ytLikelyMismatch(cur.title, expectedArtist, expectedAlbum);
  const mismatchBadge = mismatch
    ? `<span class="disc-v2-yt-mismatch" title="This result doesn't seem to match the album. Check before downloading." aria-label="Possible mismatch">⚠</span>`
    : '';
  slot.innerHTML = `
    <div class="disc-v2-yt-carousel" data-yt-index="${index}">
      <div class="disc-v2-yt-result-meta">
        ${mismatchBadge}
        <div class="disc-v2-yt-result-text">
          <div class="disc-v2-yt-title" title="${_esc(cur.title || '')}">${_esc(cur.title || 'Untitled')}</div>
          ${cur.channel ? `<div class="disc-v2-yt-channel">${_esc(cur.channel)}</div>` : ''}
        </div>
        <div class="disc-v2-yt-counter">${index + 1} / ${candidates.length}</div>
      </div>
      <div class="disc-v2-yt-frame">
        <iframe src="${_esc(embedUrl)}"
                title="${_esc(cur.title || 'YouTube preview')}"
                allow="encrypted-media; picture-in-picture"
                referrerpolicy="strict-origin-when-cross-origin"
                allowfullscreen></iframe>
      </div>
      <div class="disc-v2-yt-controls">
        <div class="disc-v2-yt-nav">
          <button data-yt-act="prev" aria-label="Previous YouTube candidate" ${index === 0 ? 'disabled' : ''}>‹</button>
          <button data-yt-act="next" aria-label="Next YouTube candidate" ${index === candidates.length - 1 ? 'disabled' : ''}>›</button>
        </div>
      </div>
    </div>
  `;
  slot.querySelectorAll('[data-yt-act]').forEach(btn => {
    btn.addEventListener('click', () => {
      const act = btn.getAttribute('data-yt-act');
      const next = act === 'prev' ? index - 1 : index + 1;
      if (next < 0 || next >= candidates.length) return;
      _renderYouTubeCarousel(slot, candidates, next, releaseHint);
    });
  });
}

function _renderDetailBody(release, detail, status, errorMsg) {
  const body = document.getElementById('disc-v2-detail-body');
  if (!body) return;

  // Prefer Discogs detail when present; fall back to the card's release dict.
  const r = release.release || {};
  const id = (detail && detail.id) || r.id || 0;
  const title = (detail && detail.title) || r.title || 'Untitled';
  const artist = (detail && detail.artist) || r.artist || 'Unknown Artist';
  const year = (detail && detail.year) || r.year || '';
  const label = (detail && detail.label) || r.label || '';
  const labelId = (detail && detail.label_id) || r.label_id || null;
  const cover = (detail && (detail.cover || detail.cover_image)) || r.cover_image || r.thumb || '';
  const styles = (detail && detail.styles) || [];
  const tracks = (detail && detail.tracklist) || [];

  const isSaved = DiscoverV2.state.savedKeys.has(release.release_key);
  const isDismissed = DiscoverV2.state.dismissedKeys.has(release.release_key);
  const followsLabel = labelId &&
    DiscoverV2.state.followedLabels.some(l => l.label_id === labelId);
  const artistId = (detail && detail.artist_id) || r.artist_id || 0;
  const artistBlocked = artistId &&
    (DiscoverV2.state.blockedArtists || []).some(b => b.discogs_artist_id === artistId);
  const labelBlocked = labelId &&
    (DiscoverV2.state.blockedLabels || []).some(b => b.discogs_label_id === labelId);

  const trackHtml = tracks.length
    ? `<ol class="disc-v2-detail-tracklist" aria-label="Tracklist">
         ${tracks.map(t => `
           <li>
             <span class="pos">${_esc(t.position || '')}</span>
             <span class="title">${_esc(t.title || '')}</span>
             <span class="dur">${_esc(t.duration || '')}</span>
           </li>`).join('')}
       </ol>`
    : (status === 'loading'
        ? '<p style="font-size:12px;color:var(--muted);"><span class="disc-v2-spinner" aria-hidden="true"></span> Loading tracklist…</p>'
        : '<p style="font-size:12px;color:var(--muted);">No tracklist available.</p>');

  const errHtml = status === 'error'
    ? `<p class="disc-v2-detail-error" role="alert">Could not load details: ${_esc(errorMsg || '')}</p>`
    : '';

  body.innerHTML = `
    ${cover ? `<img src="${_esc(cover)}" alt="" style="width:100%;max-width:320px;border-radius:8px;margin-bottom:12px;">` : ''}
    <h2 id="disc-v2-detail-heading" style="margin:0 0 4px;font-size:18px;">${_esc(title)}</h2>
    <p style="margin:0 0 6px;color:var(--muted);">
      ${_esc(artist)}${year ? ' · ' + _esc(String(year)) : ''}${label ? ' · ' + _esc(label) : ''}
    </p>
    ${styles.length ? `<p style="margin:0 0 12px;font-size:12px;color:var(--muted);">${styles.map(_esc).join(' · ')}</p>` : ''}
    <div class="disc-v2-detail-actions">
      <button class="disc-v2-detail-action ${isSaved ? 'saved' : 'primary'}" data-detail-act="save">
        ${isSaved ? '✓ Saved' : '💚 Save'}
      </button>
      <button class="disc-v2-detail-action" data-detail-act="download">⬇ Download album</button>
      <button class="disc-v2-detail-action" data-detail-act="snooze">💤 Snooze…</button>
      <button class="disc-v2-detail-action" data-detail-act="dismiss" ${isDismissed ? 'disabled' : ''}>
        ✕ ${isDismissed ? 'Dismissed' : 'Dismiss'}
      </button>
      ${labelId && !followsLabel
        ? `<button class="disc-v2-detail-action" data-detail-act="follow-label" data-label-id="${labelId}" data-label-name="${_esc(label)}">+ Follow ${_esc(label)}</button>`
        : ''}
      ${artistId && !artistBlocked
        ? `<button class="disc-v2-detail-action" data-detail-act="block-artist" data-artist-id="${artistId}" data-artist-name="${_esc(artist)}" title="Stop seeing this artist in Discover">🚫 Block ${_esc(artist)}</button>`
        : ''}
      ${labelId && !labelBlocked
        ? `<button class="disc-v2-detail-action" data-detail-act="block-label" data-label-id="${labelId}" data-label-name="${_esc(label)}" title="Stop seeing this label in Discover">🚫 Block ${_esc(label)}</button>`
        : ''}
    </div>
    <div id="disc-v2-detail-youtube-slot"></div>
    ${errHtml}
    ${trackHtml}
    ${id ? `<p style="margin-top:14px;font-size:12px;"><a href="https://www.discogs.com/release/${id}" target="_blank" rel="noopener noreferrer">View on Discogs ↗</a></p>` : ''}
  `;

  // Wire action buttons via delegation.
  body.querySelectorAll('[data-detail-act]').forEach(btn => {
    btn.addEventListener('click', async (ev) => {
      ev.preventDefault();
      const act = btn.getAttribute('data-detail-act');
      try {
        if (act === 'save') {
          await DiscoverV2.save(release);
        } else if (act === 'snooze') {
          // Don't close the panel — the popover anchors against the button.
          _openSnoozePopover(release, btn);
          return;
        } else if (act === 'dismiss') {
          await DiscoverV2.dismiss(release);
          _closeDetailPanel();
          return;
        } else if (act === 'follow-label') {
          const lid = parseInt(btn.getAttribute('data-label-id'), 10);
          const lname = btn.getAttribute('data-label-name') || '';
          await DiscoverV2.followLabel(lid, lname);
        } else if (act === 'download') {
          // Inside-the-panel download is intentional, not Shift+click bypass —
          // user already navigated here, so we go straight to runDownload.
          const query = _buildDownloadQuery(release);
          if (query && typeof runDownload === 'function') {
            runDownload(query, {});
          }
        } else if (act === 'block-artist') {
          const aid = parseInt(btn.getAttribute('data-artist-id'), 10);
          const aname = btn.getAttribute('data-artist-name') || '';
          await DiscoverV2.blockArtist(aid, aname);
          // Blocking hides this release from future scans — close + remove from feed.
          DiscoverV2.state.dismissedKeys.add(release.release_key);
          _closeDetailPanel();
          return;
        } else if (act === 'block-label') {
          const lid = parseInt(btn.getAttribute('data-label-id'), 10);
          const lname = btn.getAttribute('data-label-name') || '';
          await DiscoverV2.blockLabel(lid, lname);
          DiscoverV2.state.dismissedKeys.add(release.release_key);
          _closeDetailPanel();
          return;
        }
        // Re-render to reflect updated state (e.g., save button → ✓ Saved).
        _renderDetailBody(release, detail, status, errorMsg);
        // The re-render blows away the YouTube slot — repaint from cache.
        _loadYouTubePreview(release);
      } catch (e) {
        const err = document.createElement('p');
        err.className = 'disc-v2-detail-error';
        err.setAttribute('role', 'alert');
        err.textContent = 'Action failed: ' + String(e);
        body.appendChild(err);
      }
    });
  });
}

function _detailTrapKeydown(ev) {
  if (ev.key === 'Escape') {
    _closeDetailPanel();
    return;
  }
  if (ev.key !== 'Tab') return;
  const panel = document.getElementById('disc-v2-detail-panel');
  if (!panel || panel.getAttribute('aria-hidden') !== 'false') return;
  // Cycle Tab focus inside the panel only.
  const focusables = panel.querySelectorAll(
    'button, a, input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (ev.shiftKey && document.activeElement === first) {
    ev.preventDefault();
    last.focus();
  } else if (!ev.shiftKey && document.activeElement === last) {
    ev.preventDefault();
    first.focus();
  }
}

function _closeDetailPanel() {
  const panel = document.getElementById('disc-v2-detail-panel');
  const backdrop = document.getElementById('disc-v2-detail-backdrop');
  if (panel) panel.setAttribute('aria-hidden', 'true');
  if (backdrop) backdrop.setAttribute('aria-hidden', 'true');
  if (_detailKeydownHandler) {
    document.removeEventListener('keydown', _detailKeydownHandler);
    _detailKeydownHandler = null;
  }
  // Return focus to whatever the user was on before opening the panel.
  if (_detailReturnFocusEl && typeof _detailReturnFocusEl.focus === 'function') {
    try { _detailReturnFocusEl.focus(); } catch (_) {}
  }
  _detailReturnFocusEl = null;
  _detailCurrentRelease = null;
}

// Download confirm modal (Shift+click power flow).
// PRD §5.6: modal default focus = Cancel — sticky-Shift + accidental-Enter
// must NOT trigger an unintended download.
let _dlConfirmReturnFocusEl = null;
let _dlConfirmRelease = null;
let _dlConfirmKeydownHandler = null;

function _buildDownloadQuery(release) {
  const r = release?.release || {};
  const artist = (r.artist || '').trim();
  let albumOrTitle = (r.album || '').trim();
  if (!albumOrTitle) {
    // Discogs raw `title` is usually "Artist - Album". Strip the redundant
    // artist prefix so the query doesn't duplicate the artist name — UX
    // audit M-2 saw "soFa elsewhere Sandy B (3) & soFa elsewhere - Forward
    // In Reverse Pt.1" because both fields were concatenated raw.
    const title = (r.title || '').trim();
    albumOrTitle = title.includes(' - ')
      ? title.split(' - ').slice(1).join(' - ').trim()
      : title;
  }
  return [artist, albumOrTitle].filter(Boolean).join(' ');
}

function _openDownloadConfirm(release) {
  const modal = document.getElementById('disc-v2-dl-confirm');
  const backdrop = document.getElementById('disc-v2-dl-confirm-backdrop');
  const cancelBtn = document.getElementById('disc-v2-dl-confirm-cancel');
  const goBtn = document.getElementById('disc-v2-dl-confirm-go');
  const body = document.getElementById('disc-v2-dl-confirm-body');
  if (!modal || !backdrop) return;

  _dlConfirmReturnFocusEl = document.activeElement;
  _dlConfirmRelease = release;

  const r = release?.release || {};
  const query = _buildDownloadQuery(release);
  if (body) {
    // Strip redundant "Artist - " prefix from the displayed title so the
    // first line reads naturally even when Discogs returns the full
    // "Artist - Album" string in `title`.
    const rawTitle = (r.title || '').trim();
    const cleanTitle = rawTitle.includes(' - ')
      ? rawTitle.split(' - ').slice(1).join(' - ').trim()
      : rawTitle;
    body.innerHTML =
      `Download <strong>${_esc(cleanTitle || 'Untitled')}</strong> by ` +
      `<strong>${_esc(r.artist || 'Unknown Artist')}</strong>?` +
      `<br><span style="color:var(--muted);font-size:12px;">` +
      `We'll search YouTube for: <code>${_esc(query)}</code></span>`;
  }
  // Reset the Go button label (in case a prior run left it in progress).
  if (goBtn) {
    goBtn.disabled = false;
    goBtn.textContent = 'Download album';
  }

  modal.setAttribute('aria-hidden', 'false');
  backdrop.setAttribute('aria-hidden', 'false');

  _dlConfirmKeydownHandler = (ev) => _dlConfirmTrapKeydown(ev);
  document.addEventListener('keydown', _dlConfirmKeydownHandler);

  // Critical: Cancel is the default focus.
  setTimeout(() => { cancelBtn?.focus(); }, 30);
}

function _dlConfirmTrapKeydown(ev) {
  if (ev.key === 'Escape') {
    _closeDownloadConfirm();
    return;
  }
  if (ev.key !== 'Tab') return;
  const modal = document.getElementById('disc-v2-dl-confirm');
  if (!modal || modal.getAttribute('aria-hidden') !== 'false') return;
  const focusables = modal.querySelectorAll(
    'button, a, input, [tabindex]:not([tabindex="-1"])'
  );
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (ev.shiftKey && document.activeElement === first) {
    ev.preventDefault();
    last.focus();
  } else if (!ev.shiftKey && document.activeElement === last) {
    ev.preventDefault();
    first.focus();
  }
}

function _closeDownloadConfirm() {
  const modal = document.getElementById('disc-v2-dl-confirm');
  const backdrop = document.getElementById('disc-v2-dl-confirm-backdrop');
  if (modal) modal.setAttribute('aria-hidden', 'true');
  if (backdrop) backdrop.setAttribute('aria-hidden', 'true');
  if (_dlConfirmKeydownHandler) {
    document.removeEventListener('keydown', _dlConfirmKeydownHandler);
    _dlConfirmKeydownHandler = null;
  }
  if (_dlConfirmReturnFocusEl && typeof _dlConfirmReturnFocusEl.focus === 'function') {
    try { _dlConfirmReturnFocusEl.focus(); } catch (_) {}
  }
  _dlConfirmReturnFocusEl = null;
  _dlConfirmRelease = null;
}

async function _runDownloadConfirmGo() {
  const release = _dlConfirmRelease;
  if (!release) return;
  const query = _buildDownloadQuery(release);
  if (!query) {
    if (typeof showToast === 'function') showToast('Cannot build a download query — release has no artist or title');
    return;
  }
  // Hand off to the existing runDownload helper, which speaks SSE + the
  // shared download config. Close the modal immediately so the user can
  // continue browsing; download progress is surfaced via the existing toast.
  _closeDownloadConfirm();
  if (typeof runDownload === 'function') {
    runDownload(query, {});
  } else {
    // Fallback for environments without the v1 download helper: fire-and-forget.
    try {
      await fetch('/api/download', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({query}),
      });
    } catch (_) { /* surfaced through network UI elsewhere */ }
  }
}

function _esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

// Timestamped export filename so a user with multiple machines doesn't
// overwrite their own backups when they re-export.
function _discoverV2ExportFilename(now) {
  const d = now || new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `discover-${yyyy}-${mm}-${dd}.db.gz`;
}

// Build a human-readable diff line from before/after import counts. Skips
// fields with no change so the toast stays short.
function _formatImportDiff(before, after) {
  const labels = {
    saved: 'saves',
    dismissed: 'dismisses',
    snoozed: 'snoozes',
    downloaded: 'downloads',
    followed_labels: 'followed labels',
    blocked_artists: 'blocked artists',
    blocked_labels: 'blocked labels',
  };
  const parts = [];
  for (const key of Object.keys(labels)) {
    const b = (before && before[key]) || 0;
    const a = (after && after[key]) || 0;
    const delta = a - b;
    if (delta === 0) continue;
    const sign = delta > 0 ? '+' : '';
    parts.push(`${sign}${delta} ${labels[key]}`);
  }
  if (!parts.length) return 'Imported — no changes';
  return 'Imported · ' + parts.join(', ');
}

// ── Keyboard shortcuts (T-028) ──────────────────────────────────────────
// Active-card index lives at module scope so j/k navigation survives feed
// re-renders (after each Save / Dismiss / Snooze the grid is rebuilt).
//
// Issue #69 fix: the numeric index is NOT stable across mutations — when
// a card is removed, the card that was at `index+1` slides into `index`
// and silently inherits the active state. A stray Space/Enter then fires
// dismiss/snooze on the wrong neighbor. We additionally track the
// release_key of the active card and re-derive the numeric index from it
// on every re-render. If the key is gone (because the user just
// dismissed/snoozed it), we drop the active state entirely instead of
// shifting it onto a neighbor.
let _activeCardIndex = -1;
let _activeReleaseKey = null;

function _visibleDiscoverCards() {
  // Use DOM order so the cursor follows what the user actually sees.
  return Array.from(document.querySelectorAll('#disc-v2-grid .disc-v2-card'));
}

function _setActiveCard(index, scroll = true) {
  const cards = _visibleDiscoverCards();
  if (!cards.length) {
    _activeCardIndex = -1;
    _activeReleaseKey = null;
    return;
  }
  if (index < 0) index = 0;
  if (index >= cards.length) index = cards.length - 1;
  cards.forEach(c => c.classList.remove('active'));
  cards[index].classList.add('active');
  if (scroll) {
    cards[index].scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }
  _activeCardIndex = index;
  _activeReleaseKey = cards[index].getAttribute('data-release-key');
}

function _activeRelease() {
  const cards = _visibleDiscoverCards();
  if (_activeCardIndex < 0 || _activeCardIndex >= cards.length) return null;
  const key = cards[_activeCardIndex].getAttribute('data-release-key');
  // Issue #69: defence-in-depth. If the numeric index now points at a card
  // whose release_key differs from the one the user last selected, the
  // grid mutated under us — refuse to act rather than mutating a neighbor.
  if (_activeReleaseKey && key !== _activeReleaseKey) return null;
  return DiscoverV2.state.cardsByKey.get(key) || null;
}

function _kbdIsTextInputActive() {
  const el = document.activeElement;
  if (!el || el === document.body) return false;
  const tag = (el.tagName || '').toUpperCase();
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (el.isContentEditable) return true;
  return false;
}

function _kbdDialogIsOpen() {
  // Any of: detail panel, download confirm, keyboard help.
  return ['disc-v2-detail-panel', 'disc-v2-dl-confirm', 'disc-v2-kbd-help']
    .some(id => document.getElementById(id)?.getAttribute('aria-hidden') === 'false');
}

function _toggleKbdHelp() {
  const modal = document.getElementById('disc-v2-kbd-help');
  const backdrop = document.getElementById('disc-v2-kbd-backdrop');
  if (!modal) return;
  const isOpen = modal.getAttribute('aria-hidden') === 'false';
  modal.setAttribute('aria-hidden', isOpen ? 'true' : 'false');
  if (backdrop) backdrop.setAttribute('aria-hidden', isOpen ? 'true' : 'false');
  if (!isOpen) {
    setTimeout(() => document.getElementById('disc-v2-kbd-help-close')?.focus(), 30);
  }
}

// ── Snooze popover (T-033) ───────────────────────────────────────────────
// PRD §6.11: 1w / 1m / 3m. 1m is the default (the "default" CSS class +
// initial focus); Enter on the default button fires the snooze.
let _snoozePopRelease = null;
let _snoozePopReturnFocusEl = null;
let _snoozePopKeydownHandler = null;

function _openSnoozePopover(release, anchorEl) {
  const pop = document.getElementById('disc-v2-snooze-pop');
  if (!pop) return;
  _snoozePopRelease = release;
  _snoozePopReturnFocusEl = document.activeElement;

  // Anchor the popover near the clicked button. If no anchor was passed
  // (e.g., keyboard z shortcut), center over the current active card.
  const anchor = anchorEl || document.querySelector('.disc-v2-card.active') || document.body;
  const rect = anchor.getBoundingClientRect();
  const top = window.scrollY + rect.bottom + 6;
  // Right-align if the anchor's right edge is past 70% of viewport.
  let left = window.scrollX + rect.left;
  if (rect.left > window.innerWidth * 0.7) {
    left = window.scrollX + rect.right - 220;
  }
  pop.style.top = `${top}px`;
  pop.style.left = `${left}px`;
  pop.setAttribute('aria-hidden', 'false');

  _snoozePopKeydownHandler = (ev) => {
    if (ev.key === 'Escape') {
      ev.preventDefault();
      _closeSnoozePopover();
    }
  };
  document.addEventListener('keydown', _snoozePopKeydownHandler);
  // Click-outside-to-close.
  setTimeout(() => document.addEventListener('mousedown', _snoozePopOutsideHandler), 0);

  // Default focus on the 1-month button.
  setTimeout(() => {
    const def = pop.querySelector('button.default') || pop.querySelector('button');
    def?.focus();
  }, 30);
}

function _snoozePopOutsideHandler(ev) {
  const pop = document.getElementById('disc-v2-snooze-pop');
  if (pop && !pop.contains(ev.target)) _closeSnoozePopover();
}

function _closeSnoozePopover() {
  const pop = document.getElementById('disc-v2-snooze-pop');
  if (pop) pop.setAttribute('aria-hidden', 'true');
  if (_snoozePopKeydownHandler) {
    document.removeEventListener('keydown', _snoozePopKeydownHandler);
    _snoozePopKeydownHandler = null;
  }
  document.removeEventListener('mousedown', _snoozePopOutsideHandler);
  if (_snoozePopReturnFocusEl && typeof _snoozePopReturnFocusEl.focus === 'function') {
    try { _snoozePopReturnFocusEl.focus(); } catch (_) {}
  }
  _snoozePopReturnFocusEl = null;
  _snoozePopRelease = null;
}

async function _runSnoozeWithDuration(duration) {
  // ── Issue #69 fix ────────────────────────────────────────────────────
  // Snooze BEFORE closing the popover. If we closed first, focus would be
  // restored to the original 💤 button; the subsequent feed re-render then
  // destroys that button, the browser hops focus to body, and a stray
  // Space/Enter from the user lands on _activeRelease() — which by then
  // points at an *adjacent* card (see the sticky `_activeReleaseKey`
  // tracking added alongside this fix). Running the snooze first means
  // the re-render happens while the popover is still the focus owner,
  // so when we close the focus restore target is either still in the
  // DOM or harmlessly absent. Either way, no synthetic activation can
  // target an adjacent card.
  // ─────────────────────────────────────────────────────────────────────
  const release = _snoozePopRelease;
  if (!release) {
    _closeSnoozePopover();
    return;
  }
  try {
    _collapseDiscoverCard(release.release_key);
    await DiscoverV2.snooze(release, duration);
  } catch (_) {
    if (typeof showToast === 'function') showToast('Snooze failed', true);
  } finally {
    _closeSnoozePopover();
  }
}

// Acknowledge dismiss/snooze on the card itself before the full grid rebuild —
// the acted-on card used to teleport away in a single frame (aliveness audit).
// Opacity/scale only: the grid reflow still happens at re-render time.
function _collapseDiscoverCard(releaseKey) {
  if (_prefersReducedMotion || !releaseKey) return;
  const card = document.querySelector(
    '#disc-v2-grid .disc-v2-card[data-release-key="' + (window.CSS && CSS.escape ? CSS.escape(releaseKey) : releaseKey) + '"]');
  if (!card) return;
  card.style.transition = 'opacity .18s ease, transform .18s ease';
  card.style.opacity = '0';
  card.style.transform = 'scale(0.96)';
  card.style.pointerEvents = 'none';
}

function _handleDiscoverKeydown(ev) {
  // Don't intercept while the user is typing in a search box.
  if (_kbdIsTextInputActive()) return;

  // The Discover tab must actually be visible. The simplest reliable check
  // is "does the v2 section exist and is it not display:none". The section
  // is hidden when the user is on a different tab.
  const section = document.getElementById('disc-v2-section');
  if (!section) return;
  const visible = section.offsetParent !== null;
  if (!visible) return;

  // `?` opens / closes the help overlay regardless of any other dialog. We
  // both preventDefault AND stopPropagation so the app-wide `?` handler
  // doesn't ALSO fire — without stopPropagation the user saw the Discover
  // help AND the global help open simultaneously (UX audit Issue 1).
  if (ev.key === '?') {
    ev.preventDefault();
    ev.stopPropagation();
    if (typeof ev.stopImmediatePropagation === 'function') ev.stopImmediatePropagation();
    _toggleKbdHelp();
    return;
  }

  // Other dialogs eat their own keys.
  if (_kbdDialogIsOpen()) return;

  switch (ev.key) {
    case 'j': ev.preventDefault(); _setActiveCard(_activeCardIndex + 1); return;
    case 'k': ev.preventDefault(); _setActiveCard(_activeCardIndex - 1); return;
    case 'Enter': {
      const rel = _activeRelease();
      if (!rel) return;
      ev.preventDefault();
      _openDetailPanel(rel.release_key);
      return;
    }
    case 's': {
      const rel = _activeRelease();
      if (!rel) return;
      ev.preventDefault();
      DiscoverV2.save(rel);
      return;
    }
    case 'x': {
      const rel = _activeRelease();
      if (!rel) return;
      ev.preventDefault();
      _collapseDiscoverCard(rel.release_key);
      DiscoverV2.dismiss(rel);
      return;
    }
    case 'z': {
      const rel = _activeRelease();
      if (!rel) return;
      ev.preventDefault();
      _openSnoozePopover(rel, null);
      return;
    }
    case 'D': {  // intentionally uppercase: requires Shift
      const rel = _activeRelease();
      if (!rel) return;
      ev.preventDefault();
      _openDownloadConfirm(rel);
      return;
    }
  }
}

// ── Wiring (DOMContentLoaded) ────────────────────────────────────────────
function initDiscoverV2() {
  if (!document.getElementById('disc-v2-section')) return;

  // Move all overlay elements out of #discover-tab-content so that the tab-
  // switch animation's `transform` doesn't form a new containing block for
  // their `position: fixed` rules. With the overlays underneath that ancestor,
  // the detail panel was resolving top:0 / bottom:0 against the full-content-
  // tall tab body (40,000px+) instead of the viewport, and would scroll OFF
  // the screen the moment the user moused the page. See UX audit Issue 2.
  // Moving to <body> as direct children fixes all overlays at once.
  for (const id of [
    'disc-v2-detail-backdrop',
    'disc-v2-detail-panel',
    'disc-v2-snooze-pop',
    'disc-v2-kbd-backdrop',
    'disc-v2-kbd-help',
    'disc-v2-dl-confirm-backdrop',
    'disc-v2-dl-confirm',
  ]) {
    const el = document.getElementById(id);
    if (el && el.parentElement !== document.body) {
      document.body.appendChild(el);
    }
  }

  // Subscribe each renderer once.
  DiscoverV2.subscribe(_renderDiscoverV2Feed);
  DiscoverV2.subscribe(_renderDiscoverV2ScanProgress);
  DiscoverV2.subscribe(_renderDiscoverV2ScanWarnings);
  DiscoverV2.subscribe(_renderDiscoverV2Onboarding);
  DiscoverV2.subscribe(_renderDiscoverV2TokenBanner);
  DiscoverV2.subscribe(_renderDiscoverV2Followed);
  DiscoverV2.subscribe(_renderDiscoverV2Blocked);

  // Refresh button.
  const refreshBtn = document.getElementById('disc-v2-refresh-btn');
  if (refreshBtn) refreshBtn.addEventListener('click', () => DiscoverV2.runScan());

  // Settings toggle.
  const settingsBtn = document.getElementById('disc-v2-settings-btn');
  const settings = document.getElementById('disc-v2-settings');
  if (settingsBtn && settings) {
    settingsBtn.addEventListener('click', async () => {
      const wasHidden = settings.style.display === 'none';
      settings.style.display = wasHidden ? '' : 'none';
      if (wasHidden) {
        // Lazy-load stats when settings is opened.
        const block = document.getElementById('disc-v2-stats-block');
        if (block) block.innerHTML = '<em>Loading stats…</em>';
        try {
          const stats = await DiscoverV2.refreshStats();
          _renderDiscoverV2Stats(stats);
        } catch (e) {
          if (block) block.innerHTML = '<em>Could not load stats.</em>';
        }
        // Also lazy-load the Saved releases list (UX audit M-4).
        _refreshSavedFromBackend();
      }
    });
  }

  // Filter chips re-trigger scan on change (server-side feeder selection).
  document.querySelectorAll('#disc-v2-filter-bar input[data-source]').forEach(el =>
    el.addEventListener('change', () => DiscoverV2.runScan()));
  // Year filter changes the backend window — re-scan. "custom" reveals an
  // inline number input; only re-scan once a valid 4-digit year is entered.
  const yearSelect = document.getElementById('disc-v2-year');
  const yearCustom = document.getElementById('disc-v2-year-custom');
  yearSelect?.addEventListener('change', () => {
    if (yearCustom) {
      yearCustom.style.display = yearSelect.value === 'custom' ? '' : 'none';
      if (yearSelect.value === 'custom') {
        yearCustom.focus();
        // Don't scan yet — wait for the user to type a year.
        if (!yearCustom.value) return;
      }
    }
    DiscoverV2.runScan();
  });
  yearCustom?.addEventListener('change', () => {
    const v = parseInt(yearCustom.value || '', 10);
    if (Number.isFinite(v) && v >= 1900 && v <= 2099) DiscoverV2.runScan();
  });
  // Sort is purely client-side — just re-render the existing fetch.
  document.getElementById('disc-v2-sort')?.addEventListener('change', _renderDiscoverV2Feed);

  // ── Client-side filter row 2: search / hide-saved / hide-dismissed / styles ──
  // These narrow the loaded feed without re-scanning. State is persisted
  // to localStorage so it survives reloads (matches the sort-persistence
  // pattern at line 3891).
  const searchInput = document.getElementById('disc-v2-search');
  if (searchInput) {
    searchInput.value = _discoverFilters.search || '';
    let searchDebounce = null;
    searchInput.addEventListener('input', () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(() => {
        _discoverFilters.search = searchInput.value || '';
        _persistDiscoverFilters();
        _renderDiscoverV2Feed();
      }, 180);
    });
  }
  const hideSavedEl = document.getElementById('disc-v2-hide-saved');
  if (hideSavedEl) {
    hideSavedEl.checked = !!_discoverFilters.hideSaved;
    hideSavedEl.addEventListener('change', () => {
      _discoverFilters.hideSaved = hideSavedEl.checked;
      _persistDiscoverFilters();
      _renderDiscoverV2Feed();
    });
  }
  const hideDismissedEl = document.getElementById('disc-v2-hide-dismissed');
  if (hideDismissedEl) {
    hideDismissedEl.checked = _discoverFilters.hideDismissed !== false;
    hideDismissedEl.addEventListener('change', () => {
      _discoverFilters.hideDismissed = hideDismissedEl.checked;
      _persistDiscoverFilters();
      _renderDiscoverV2Feed();
    });
  }
  // Style-chip clicks toggle membership in _discoverFilters.selectedStyles
  // via event delegation (the chips themselves are re-rendered on every feed
  // update by _renderDiscoverStyleChips).
  document.getElementById('disc-v2-style-chips')?.addEventListener('change', (ev) => {
    const target = ev.target;
    if (!target || !target.matches('input[data-style-key]')) return;
    const key = target.getAttribute('data-style-key') || '';
    if (target.checked) _discoverFilters.selectedStyles.add(key);
    else _discoverFilters.selectedStyles.delete(key);
    _persistDiscoverFilters();
    _renderDiscoverV2Feed();
  });
  document.getElementById('disc-v2-styles-clear')?.addEventListener('click', () => {
    _discoverFilters.selectedStyles.clear();
    _persistDiscoverFilters();
    _renderDiscoverV2Feed();
  });

  // Issue #169: retry from the inline error banner (shown when a refresh
  // 409'd after the initial-scan lock didn't clear in time).
  document.getElementById('disc-v2-scan-error-inline-retry')
    ?.addEventListener('click', () => DiscoverV2.runScan());

  // Scan-cancel button.
  document.getElementById('disc-v2-scan-cancel-btn')?.addEventListener('click', () => DiscoverV2.cancelScan());

  // Card grid click delegate — action buttons, Shift+click power flow, or
  // plain click → open panel.
  document.getElementById('disc-v2-grid')?.addEventListener('click', (ev) => {
    const card = ev.target.closest('.disc-v2-card');
    if (!card) return;
    const releaseKey = card.getAttribute('data-release-key');
    const release = DiscoverV2.state.cardsByKey.get(releaseKey);
    if (!release) return;
    const actBtn = ev.target.closest('[data-act]');
    if (actBtn) {
      ev.stopPropagation();
      const act = actBtn.getAttribute('data-act');
      if (act === 'save') DiscoverV2.save(release);
      else if (act === 'dismiss') { _collapseDiscoverCard(releaseKey); DiscoverV2.dismiss(release); }
      else if (act === 'snooze') _openSnoozePopover(release, actBtn);
      return;
    }
    if (ev.shiftKey) {
      ev.preventDefault();
      _openDownloadConfirm(release);
      return;
    }
    _openDetailPanel(releaseKey);
  });

  // Detail panel close: X button + backdrop click. Escape is handled by the
  // per-open focus-trap handler installed inside _openDetailPanel.
  document.getElementById('disc-v2-detail-close-btn')?.addEventListener('click', _closeDetailPanel);
  document.getElementById('disc-v2-detail-backdrop')?.addEventListener('click', _closeDetailPanel);

  // Download confirm modal close + confirm wiring.
  document.getElementById('disc-v2-dl-confirm-cancel')?.addEventListener('click', _closeDownloadConfirm);
  document.getElementById('disc-v2-dl-confirm-backdrop')?.addEventListener('click', _closeDownloadConfirm);
  document.getElementById('disc-v2-dl-confirm-go')?.addEventListener('click', _runDownloadConfirmGo);

  // Keyboard help overlay close (X button + backdrop click).
  document.getElementById('disc-v2-kbd-help-close')?.addEventListener('click', _toggleKbdHelp);
  document.getElementById('disc-v2-kbd-backdrop')?.addEventListener('click', _toggleKbdHelp);

  // Snooze popover buttons. Each button carries its duration in data-snooze-dur.
  // Issue #69: stopPropagation defends against any bubbled click being
  // re-interpreted by ancestor handlers (e.g. the grid delegate).
  document.querySelectorAll('#disc-v2-snooze-pop [data-snooze-dur]').forEach(btn =>
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      _runSnoozeWithDuration(btn.getAttribute('data-snooze-dur'));
    })
  );

  // Keyboard shortcuts: j/k navigate, Enter opens panel, s/x/z mutate,
  // D triggers download confirm modal, ? toggles help overlay.
  // Use capture:true so this fires BEFORE the app-wide ? handler — without
  // it the Discover ? overlay opens AND the app-wide overlay opens (UX
  // audit Issue 1). The stopPropagation inside the handler then suppresses
  // the app-wide listener.
  document.addEventListener('keydown', _handleDiscoverKeydown, true);

  // Whenever the feed re-renders we may need to reset the cursor. The card
  // grid is rebuilt by _renderDiscoverV2Feed — re-subscribe so we drop the
  // active class on cards that were removed.
  //
  // Issue #69: re-derive the active index from `_activeReleaseKey` instead
  // of trusting the numeric index. If the previously-active card was
  // removed (e.g. the user just snoozed/dismissed it) we drop the active
  // state entirely rather than silently transferring it to the card that
  // slid into that index — otherwise a stray Space/Enter would fire the
  // next mutation on an adjacent card (Issue #69 stray-dismiss bug).
  DiscoverV2.subscribe(() => {
    const cards = _visibleDiscoverCards();
    if (!cards.length) {
      _activeCardIndex = -1;
      _activeReleaseKey = null;
      return;
    }
    if (_activeReleaseKey) {
      const idx = cards.findIndex(c => c.getAttribute('data-release-key') === _activeReleaseKey);
      if (idx >= 0) {
        _activeCardIndex = idx;
        cards[idx].classList.add('active');
        return;
      }
      // Previously active card is gone — drop active state.
      _activeCardIndex = -1;
      _activeReleaseKey = null;
      return;
    }
    if (_activeCardIndex >= 0 && _activeCardIndex < cards.length) {
      cards[_activeCardIndex].classList.add('active');
      _activeReleaseKey = cards[_activeCardIndex].getAttribute('data-release-key');
    }
  });

  // Onboarding interactions.
  document.getElementById('disc-v2-onboarding-skip')?.addEventListener('click', () => {
    localStorage.setItem('disc-v2-onboarding-skipped', '1');
    _renderDiscoverV2Onboarding();
  });
  document.getElementById('disc-v2-onboarding-add-all')?.addEventListener('click', async () => {
    const container = document.getElementById('disc-v2-onboarding-suggestions');
    if (!container) return;
    const chips = [...container.querySelectorAll('button:not([disabled])')];
    if (!chips.length) return;
    const total = chips.length;
    // Fire-and-wait each chip click in sequence so the toast at the end
    // sees the final state. The chip click handler awaits an HTTP round-trip.
    for (const chip of chips) {
      chip.click();
      // Give the chip click's async work time to flip the chip state.
      await new Promise(r => setTimeout(r, 250));
    }
    // Count successes vs failures via the per-chip text marker.
    const followed = container.querySelectorAll('button[disabled]:not(.disc-v2-suggest-failed)').length;
    const failed = container.querySelectorAll('.disc-v2-suggest-failed').length;
    if (typeof showToast === 'function') {
      if (failed === 0) {
        showToast(`Followed ${followed} labels.`);
      } else {
        showToast(
          `Followed ${followed} of ${total} labels — ${failed} couldn't be matched on Discogs (⚠ chips show why).`,
          /* isError */ failed === total,
        );
      }
    }
  });

  // Label search.
  // Suggest from library — calls /api/discover/labels/suggested.
  document.getElementById('disc-v2-label-suggest-btn')?.addEventListener('click', async () => {
    const results = document.getElementById('disc-v2-label-suggest-results');
    if (!results) return;
    results.innerHTML = '<em style="color:var(--muted);">Suggesting…</em>';
    try {
      const items = await DiscoverV2.fetchSuggestedLabels(10);
      _renderSuggestedLabels(items);
    } catch (e) {
      results.innerHTML = '<em style="color:var(--muted);">Suggest failed.</em>';
    }
  });

  document.getElementById('disc-v2-label-search-btn')?.addEventListener('click', async () => {
    const q = document.getElementById('disc-v2-label-search')?.value || '';
    const results = document.getElementById('disc-v2-label-search-results');
    if (!q.trim() || !results) return;
    results.innerHTML = '<em style="color:var(--muted);">Searching…</em>';
    try {
      const hits = await DiscoverV2.searchLabels(q);
      results.innerHTML = '';
      if (!hits.length) { results.innerHTML = '<em style="color:var(--muted);">No matches.</em>'; return; }
      hits.slice(0, 8).forEach(h => {
        const row = document.createElement('div');
        row.style.display = 'flex';
        row.style.justifyContent = 'space-between';
        row.style.alignItems = 'center';
        row.style.padding = '4px 0';
        row.innerHTML = `<span>${_esc(h.name)}</span>`;
        const btn = document.createElement('button');
        btn.className = 'secondary-btn';
        btn.style.fontSize = '11px';
        btn.textContent = 'Follow';
        btn.addEventListener('click', async () => {
          await DiscoverV2.followLabel(h.id, h.name);
          btn.disabled = true;
          btn.textContent = '✓ Following';
        });
        row.appendChild(btn);
        results.appendChild(row);
      });
    } catch (e) {
      results.innerHTML = '<em style="color:var(--muted);">Search failed.</em>';
    }
  });

  // Export / Import.
  document.getElementById('disc-v2-export-btn')?.addEventListener('click', async () => {
    try {
      const blob = await DiscoverV2.exportState();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = _discoverV2ExportFilename();
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      if (typeof showToast === 'function') {
        showToast('Exported ' + a.download);
      }
    } catch (e) {
      if (typeof showToast === 'function') {
        showToast('Export failed: ' + (e && e.message || e), true);
      }
    }
  });
  document.getElementById('disc-v2-import-input')?.addEventListener('change', async (ev) => {
    const file = ev.target.files?.[0];
    if (!file) return;
    try { ev.target.value = ''; } catch (_) { /* reset so same file re-fires */ }

    // Pre-import audit so the user sees what they'd be replacing.
    const s = DiscoverV2.state;
    const summary =
      `You currently have:\n` +
      `  ${s.savedKeys.size} saved · ${s.dismissedKeys.size} dismissed · ${s.snoozedKeys.size} snoozed\n` +
      `  ${s.followedLabels.length} followed labels · ` +
      `${s.blockedArtists.length + s.blockedLabels.length} blocked entries\n\n` +
      `Importing "${file.name}" will REPLACE all of this. Continue?`;
    if (!(await _confirmDialog(summary, { confirmLabel: 'Replace state', danger: true }))) return;

    try {
      const result = await DiscoverV2.importState(file);
      if (typeof showToast === 'function') {
        showToast(_formatImportDiff(result.before, result.after));
      }
    } catch (e) {
      if (typeof showToast === 'function') {
        showToast('Import failed: ' + (e && e.message || e), true);
      }
    }
  });

  // Initial state + initial scan on tab activation.
  DiscoverV2.loadInitialState().then(async () => {
    // UX audit Issue 9: clear any stale scanError before deciding what to do.
    // If the server says no scan is running, then any in-memory scanError is
    // either from a prior session or from a 409 that has since resolved —
    // wiping it prevents the "Couldn't finish the scan" empty state from
    // appearing on a fresh page load.
    //
    // Issue #121: ALSO use this status response to gate the auto-scan. If a
    // scan is already running for this DB (e.g. another tab / a prior page
    // load is still streaming), kicking off /api/discover/feed here would
    // get a 409 — correctly handled in user-space (issue #67), but the
    // browser still emits a native "Failed to load resource: 409" console
    // error that taints every Playwright run asserting console.errors===[].
    // Skip the auto-scan when running===true; the in-flight scan's results
    // will surface through its existing SSE consumer, and the user can hit
    // Refresh manually if they want a new one.
    let status = null;
    try {
      status = await fetch('/api/discover/feed/status').then(r => r.json());
      if (status && status.running === false) {
        DiscoverV2.state.scanError = null;
      }
    } catch (_) { /* ignore, fall through */ }
    // Auto-scan on first open if labels are followed and the token is valid
    // AND no scan is already in flight (issue #121).
    if (status && status.running === true) return;
    if (DiscoverV2.state.tokenValid && DiscoverV2.state.followedLabels.length > 0) {
      DiscoverV2.runScan();
    }
  });
}

// Expose for tests.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { DiscoverV2, _renderDiscoverV2Card, _esc };
}

async function scanDiscover() {
  // T-024: v1 'scan library' DOM was removed. This function is retained
  // only because legacy code paths reference it; immediately returns when
  // its DOM is gone.
  const btn      = document.getElementById('disc-scan-btn');
  if (!btn) return;
  const statusEl = document.getElementById('disc-status');
  const resultsEl = document.getElementById('disc-results');
  const progress = document.getElementById('disc-progress');
  const fill     = document.getElementById('disc-progress-fill');
  const noToken  = document.getElementById('discover-no-token');

  const token = _discoverToken();
  if (!token) { noToken.style.display = ''; showToast('Add your Discogs token in the Library tab first'); return; }
  noToken.style.display = 'none';

  const sinceYear  = parseInt(document.getElementById('disc-since-year').value, 10) || (new Date().getFullYear() - 1);
  const maxArtists = parseInt(document.getElementById('disc-max-artists').value, 10) || 25;

  btn.disabled = true;
  btn.textContent = 'Scanning…';
  resultsEl.innerHTML = '';
  _renderStyleFilter([]);  // hide filter until results arrive
  document.getElementById('disc-style-filter').style.display = 'none';
  progress.style.display = '';
  fill.style.width = '0%';
  let suggested = 0;
  const seenStyles = new Set();

  const params = new URLSearchParams({
    since_year: String(sinceYear),
    max_artists: String(maxArtists),
    token,
  });

  try {
    const r = await fetch('/api/discover?' + params.toString());
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r.status}`);
    }
    await _consumeSSE(r, d => {
      if (d.error) { showToast('Discover error: ' + d.error); return; }
      if (d.total) fill.style.width = Math.round((d.processed / d.total) * 100) + '%';
      if (d.done) {
        fill.style.width = '100%';
        statusEl.textContent = `${d.suggested} new release${d.suggested === 1 ? '' : 's'} found`;
        _renderStyleFilter([...seenStyles]);
        return;
      }
      if (d.album) {
        suggested++;
        resultsEl.insertAdjacentHTML('beforeend', _renderSuggestion(d));
        (d.styles || []).forEach(s => seenStyles.add(s));
        statusEl.textContent = `${suggested} so far…`;
      }
    });
    if (suggested === 0) resultsEl.innerHTML = '<p style="font-size:13px;color:var(--muted);">No new releases found for your top artists. Try lowering "Released since" or raising "Top artists".</p>';
  } catch (err) {
    showToast('Discover failed: ' + err.message);
    statusEl.textContent = 'Error: ' + err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Scan library for new releases';
    setTimeout(() => { progress.style.display = 'none'; }, 800);
  }
}

function _renderSuggestion(d) {
  const art = d.thumb || d.cover || '';
  const styleList = d.styles || [];
  const styles = styleList.slice(0, 4)
    .map(s => `<span class="tag-pill">${_esc(s)}</span>`).join('');
  const year = d.year ? ` · ${d.year}` : '';
  const dlReady = _downloadConfig.available && _downloadConfig.ffmpeg;
  const dlBtn = dlReady
    ? `<button class="secondary-btn disc-dl-btn" data-query="${_esc(d.query || (d.artist + ' ' + d.album))}" style="font-size:12px;padding:4px 10px;">⬇ Album</button>`
    : '';
  const discogs = d.url ? `<a href="${_esc(d.url)}" target="_blank" rel="noopener" style="font-size:12px;color:var(--green);">Discogs ↗</a>` : '';
  const fmt = d.formats || [];
  const fmtLabel = fmt.includes('Compilation') ? 'Comp' :
                   fmt.includes('EP')          ? 'EP' :
                   fmt.includes('Single')      ? 'Single' :
                   fmt.includes('LP')          ? 'LP' : '';
  const fmtBadge = fmtLabel
    ? `<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:var(--surface);color:var(--muted);border:1px solid var(--border);flex-shrink:0;">${fmtLabel}</span>`
    : '';
  return `
    <div class="disc-card" data-styles="${_esc(styleList.join(','))}" style="display:flex;gap:12px;align-items:center;padding:10px;border:1px solid var(--border);border-radius:8px;margin-bottom:8px;background:var(--surface2);">
      ${art ? `<img src="${_esc(art)}" alt="" style="width:54px;height:54px;border-radius:6px;object-fit:cover;flex-shrink:0;">` : '<div style="width:54px;height:54px;border-radius:6px;background:var(--surface);flex-shrink:0;"></div>'}
      <div style="flex:1;min-width:0;">
        <div style="font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${_esc(d.album || '')}</div>
        <div style="font-size:12px;color:var(--muted);display:flex;gap:6px;align-items:center;flex-wrap:wrap;">${_esc(d.artist || '')}${year} ${fmtBadge}</div>
        <div style="margin-top:4px;display:flex;gap:4px;flex-wrap:wrap;">${styles}</div>
      </div>
      <div style="display:flex;flex-direction:column;gap:6px;align-items:flex-end;flex-shrink:0;">
        ${dlBtn}
        ${discogs}
        <div class="disc-dl-progress" style="display:none;width:90px;">
          <div style="height:3px;background:var(--surface);border-radius:2px;overflow:hidden;margin-bottom:2px;">
            <div class="disc-dl-bar" style="height:100%;width:0%;background:var(--green);transition:width .2s;"></div>
          </div>
          <span class="disc-dl-status" style="font-size:11px;color:var(--muted);"></span>
        </div>
      </div>
    </div>`;
}

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Delegate clicks on per-suggestion Download buttons — routed through _Download.
// Track buttons we've already bound so the second click cancels rather than
// re-enqueueing (bindCardButton attaches an instance-level listener).
const _disc_dl_bound = new WeakSet();
document.addEventListener('click', (e) => {
  const btn = e.target.closest && e.target.closest('.disc-dl-btn');
  if (!btn) return;
  if (_disc_dl_bound.has(btn)) return;  // bindCardButton's listener handles future clicks
  _disc_dl_bound.add(btn);
  e.preventDefault();
  if (!(window._downloadConfig && window._downloadConfig.available && window._downloadConfig.ffmpeg)) {
    if (typeof showToast === 'function') showToast('Download tools not installed — see the Download panel');
    return;
  }
  window._Download.bindCardButton(btn, btn.dataset.query, {});
  // The first click is what triggered this listener; replay it on the bound btn.
  btn.click();
});

// Run a single download over SSE, updating a button + optional inline progress bar.
async function runDownload(query, { btn, statusEl, progressEl, barEl } = {}) {
  if (!query) return;
  if (!(_downloadConfig.available && _downloadConfig.ffmpeg)) {
    showToast('Download tools not installed — see the Download panel'); return;
  }
  if (btn) { btn.disabled = true; btn.textContent = 'Downloading…'; }
  if (progressEl) { progressEl.style.display = ''; }
  if (barEl) barEl.style.width = '0%';
  if (statusEl) statusEl.textContent = 'starting…';
  try {
    const r = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, dest_dir: _dlDestDir || undefined }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r.status}`);
    }
    await _consumeSSE(r, d => {
      if (d.done) {
        if (barEl) barEl.style.width = '100%';
        if (d.status === 'error') {
          if (statusEl) { statusEl.textContent = '✗ failed'; statusEl.style.color = 'var(--red, #e05252)'; }
          showToast('Download failed: ' + (d.error || ''));
        } else {
          if (statusEl) { statusEl.textContent = '✓ saved'; statusEl.style.color = 'var(--green)'; }
          showToast('Downloaded to ' + (d.path || _dlDestDir || _downloadConfig.default_dir));
        }
        return;
      }
      if (typeof d.percent === 'number') {
        if (barEl) barEl.style.width = d.percent + '%';
        if (statusEl) statusEl.textContent = d.percent + '%';
      } else if (d.status && statusEl) statusEl.textContent = d.status;
    });
  } catch (err) {
    if (statusEl) { statusEl.textContent = '✗ ' + err.message; statusEl.style.color = 'var(--red, #e05252)'; }
    showToast('Download failed: ' + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '⬇ Album'; }
  }
}

async function downloadManual() {
  const query = (document.getElementById('dl-query').value || '').trim();
  if (!query) { showToast('Enter a URL or search term'); return; }
  const btn = document.getElementById('dl-go-btn');
  const statusEl = document.getElementById('dl-status');
  const progress = document.getElementById('dl-progress');
  const fill = document.getElementById('dl-progress-fill');
  progress.style.display = '';
  fill.style.width = '5%';

  // Reuse runDownload, but also drive the dedicated progress bar.
  if (!(_downloadConfig.available && _downloadConfig.ffmpeg)) {
    showToast('Download tools not installed'); return;
  }
  btn.disabled = true; btn.textContent = 'Downloading…';
  statusEl.textContent = 'starting…';
  try {
    const r = await fetch('/api/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, dest_dir: _dlDestDir || undefined }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      throw new Error(e.detail || `HTTP ${r.status}`);
    }
    await _consumeSSE(r, d => {
      if (d.done) {
        fill.style.width = '100%';
        if (d.status === 'error') { statusEl.textContent = '✗ ' + (d.error || 'failed'); showToast('Download failed'); }
        else { statusEl.textContent = '✓ Saved to ' + (d.path || _dlDestDir || _downloadConfig.default_dir); showToast('Download complete'); }
        return;
      }
      if (typeof d.percent === 'number') { fill.style.width = Math.max(5, d.percent) + '%'; statusEl.textContent = d.percent + '%'; }
      else if (d.status) statusEl.textContent = d.status;
    });
  } catch (err) {
    statusEl.textContent = '✗ ' + err.message;
    showToast('Download failed: ' + err.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Download';
    setTimeout(() => { progress.style.display = 'none'; }, 1000);
  }
}
