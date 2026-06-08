# Download Component Refactor — Summary

## What this is

A full refactor of AutoCue's YouTube → audio Download capability across all six surfaces in `docs/index.html` (manual panel, per-Discover-card ⬇ Album, Shift-click confirm modal, track-info Download button, YouTube candidate-picker modal, sticky XML-export naming-collision). Driven by an 8-persona UX audit that scored the current state **3.9 / 10 overall** (Consistency 3.5, Mobile 3.5, User confidence 3.5).

Full PRD: `DOWNLOAD_PRD.md` (v1.0, locked after 6 grill rounds).
Iteration log: `DOWNLOAD_PRD_iteration-log.md`.

## Main features

1. **Single canonical `_Download` IIFE** replacing three duplicated SSE drivers (`runDownload`, `downloadManual`, `_ytDownload`). One state machine, one AbortController per job, one classified-error renderer. All six surfaces become thin render-callback views.
2. **Format selector**: MP3 320 (default) · WAV (with first-time WAV-from-lossy explainer) · Original (AAC/Opus). Persisted in localStorage.
3. **Quality options**: "Normalize loudness to -14 LUFS" (two-pass ffmpeg `loudnorm`, off by default) and "Auto-tag metadata" (FFmpegMetadata + MetadataParser postprocessors, on by default; works for MP3/M4A/Opus/WAV via RIFF INFO).
4. **Playlist / album batch**: backend drops hardcoded `noplaylist=True`; client URL classifier routes single-video / playlist / mixed paths; SSE emits `processed/total/current_title` for per-track "Downloading 3 of 12 · The Field — Pink Sun (45%)" progress + cumulative bar.
5. **Explicit state machine**: Idle / Loading (with real Cancel mid-flight) / Success (persistent card with [Reveal in Finder] [Copy path] [Download another]) / Error (classified with [Retry] + raw-stderr disclosure) / Unavailable (preserved install banner).
6. **Classified errors** via `classify_download_error()` mapping yt-dlp + ffmpeg failures to 10 user-facing codes (`unavailable_video`, `age_gated`, `region_blocked`, `private_video`, `network_offline`, `network_timeout`, `disk_full`, `ffmpeg_conversion`, `ffmpeg_missing`, `unknown`). Fixture-driven taxonomy under `tests/fixtures/download_errors/` with quarterly live-YT integration test for fixture rot detection.
7. **Job queue** (`DownloadQueue` with `AUTOCUE_DOWNLOAD_CONCURRENCY` default 1, max 4) prevents 20-simultaneous-click meltdowns. Single `queue_lock` + `cancel_pending` set collapses dequeue/cancel races. Worker-crash safety + tiered watchdog (60 s subprocess pulse, 30 min stuck-phase cap).
8. **Enqueue / Stream API split**: `POST /api/download/enqueue` (sync, returns `job_id`) + `GET /api/download/stream/{job_id}` (SSE) decouples cancel from SSE lifecycle. `POST /api/download/cancel/{job_id}` is sync + idempotent. Cached final-status TTL with single-consumption gate prevents double-emit.
9. **Reveal in Finder / Open folder** (macOS `open -R`, Windows `explorer /select`, Linux `xdg-open dirname`) with path-validation gate (stable-root allow-list, survives server restart) and `shutil.which()` capability probe.
10. **Mobile-first layout** with CSS-var-driven bottom stack (`--ds-strip-h`, `--db-h`, `--ab-h`) coordinating `#download-section` sticky strip + `#download-bar` + `#action-bar` so the three bottom-fixed elements never collide. `@supports selector(:has(*))` fallback for Firefox <121.
11. **Naming-collision cleanup**: "Download XML" → "Export XML"; "💾 Download backup XML" → "💾 Save backup XML"; verb "Download" reserved for audio.
12. **Full WCAG 2.2 AA**: label-for, `role=progressbar`/`aria-valuenow`, `aria-live=polite`/`assertive`, focus trap + Escape + focus-return in modals, target-size ≥24px (44px on mobile), `prefers-reduced-motion`, classified-error speech via `role=alert`.

## Key user flows

1. **Idle → first download**: type/paste URL, format dropdown defaults to MP3 320, Enter submits, progress bar with phase + ETA, success card with file path + Reveal-in-Finder, "Download another" clears input and refocuses.
2. **Per-card ⬇ Album from Discover**: click → enqueue → inline progress on card → success (toast + card persistent state). Shift+click triggers safety confirm modal (Cancel default focus, 250 ms primary delay).
3. **Playlist URL**: client classifier detects `?list=…` → `Download playlist (N)` button → backend pre-flights via yt-dlp `extract_info`, fans out, emits per-track SSE → "Downloading 3 of 12 · <title> (45%)" + cumulative bar.
4. **Mid-flight cancel**: any surface's Cancel button → `POST /api/download/cancel/{job_id}` (sync) + `AbortController.abort()` → backend sets `cancel_event` → next yt-dlp progress tick or `ffmpeg_proc.terminate()` → `output_artifacts` cleanup (mtime-gated, never deletes pre-existing files).
5. **Error → retry**: classified message + concrete hint + [Retry] button (calls `_Download.start(originalArgs)`). Raw stderr behind `<details>` for bug reports.
6. **Power user**: `Shift+D` global shortcut focuses URL input; `Enter` submits; format/normalize/metadata toggles sticky via localStorage; Settings checkbox `Always skip Shift-click confirmation` (writes both sessionStorage + localStorage for consistent same-tab UX).

## Key requirements

- Vanilla JS only — all frontend changes confined to `docs/index.html` (CLAUDE.md line 40)
- Backend additions: `autocue/download.py` (queue, classifier, loudnorm two-pass, format opts), `autocue/serve/routes.py` (enqueue/stream/cancel/reveal/queue), `autocue/serve/schemas.py` (DownloadProgressEvent + extra='forbid' coercion + 422 middleware)
- New tests: `tests/fixtures/download_errors/*.txt`, `tests/test_classify_download_error.py`, `tests/test_download_queue.py`, `tests/test_download_jargon.py`, `tests/integration/test_download_classify_live.py` (gated `RUN_LIVE_YTDLP=1`), vitest specs for `_Download` IIFE state machine + bind helpers, Playwright E2E for all 6 surfaces
- New runtime config: `AUTOCUE_DOWNLOAD_CONCURRENCY` env (default 1, max 4)
- CHANGELOG.md created (currently absent in repo); `pyproject.toml` bumped 0.1.0 → 0.2.0; pre-emptive `[BREAKING — planned for 0.3.0]` entry for deprecated alias removal
- Documentation: `docs/reference/youtube-download.md` (yt-dlp upgrade checklist, format migration, browser support); CLAUDE.md "Must-know constraints" gets a Download row; `docs/FEATURES.md` Download section rewrite
- Browser baseline: Safari ≥ 15.4, Firefox ≥ 121, Chromium ≥ 105

## Acceptance criteria (summary)

15 deterministic gates per §12 of PRD — each backed by a paired test or grep. Notable: every §2 pain row has a paired regression test; jargon grep returns 0 hits in user-visible strings; concurrent-download starvation guard verified by 20-simultaneous-start integration test; axe-core scan 0 violations across Idle/Loading/Success/Error states.

Audit re-score ≥ 7.5/10 is **post-ship validation only**, not a merge gate (LLM persona noise makes per-PR gating fragile).

## Known limitations (accepted v1 tradeoffs)

1. **Concurrency cap = 1** by default — power users with multi-core boxes need `AUTOCUE_DOWNLOAD_CONCURRENCY=4`. Higher than 4 is rejected.
2. **WAV is re-encoded losslessly from the lossy YouTube source** — not actually lossless audio. First-time inline explainer surfaces this.
3. **Loudness normalize is off by default** — additive surprise mitigation. DJ users enable + persist.
4. **Reveal-in-Finder is OS-specific** — Linux containers without `xdg-open` get the button hidden via `os_reveal_supported: false`.
5. **`:has()` fallback degrades Firefox <121 mobile** to in-flow (non-sticky) panel — out of supported baseline; documented in `docs/reference/youtube-download.md#browser-support`.
6. **Recent-downloads list** deferred to Tier 2.
7. **FLAC / ALAC formats** deferred to Tier 2 — Tier 1 ships MP3 320 / WAV / Original only.
8. **Bulk-paste queue UX** deferred to Tier 2 — Tier 1 manual panel takes one query at a time; ⬇ Album per-card enqueues are still possible in rapid succession.
