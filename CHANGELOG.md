# Changelog

All notable changes to AutoCue are documented here. Format roughly follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### [BREAKING — planned for 0.3.0]

- **`POST /api/download`** legacy alias **removed**. Use `POST /api/download/enqueue` + `GET /api/download/stream/{job_id}` instead. The split is necessary so `POST /api/download/cancel/{job_id}` can target a job before the first SSE event arrives.
- **`POST /api/download/album`** legacy alias **removed**. Use `POST /api/download/album/enqueue` + `GET /api/download/stream/{job_id}`.
- **Legacy `audio_format` coercion table removed.** Calls with `audio_format="mp3"`, `"m4a"`, `"aac"`, `"opus"`, `"flac"`, `"alac"`, `"vorbis"` will 422 instead of being silently mapped. Migrate to one of `wav` / `mp3_320` / `original`.
- **`audio_quality` request field already removed** in 0.2.0 (422 with `audio_quality_removed`). This entry is a reminder, not a new change in 0.3.0.

---

## [0.2.0] — 2026-06-08

### Download component refactor (PRD: `.agent/prd/DOWNLOAD_PRD.md` v1.0)

The Download capability across all six surfaces in the web app has been rewritten end-to-end, driven by an 8-persona UX audit that scored the prior state 3.9 / 10. The audit synthesis lives at `.agent/ux-audit/download-audit-synthesis.json`.

#### Added

- **Single canonical `_Download` IIFE** in `docs/index.html` replaces `runDownload`, `downloadManual`, and `_ytDownload`. One state machine (`idle | loading | success | error`), one `AbortController` per job, one classified-error renderer. All five interactive download surfaces are now thin views over this module.
- **Format selector** in the manual panel: `MP3 320 kbps` (default), `WAV` (uncompressed, with first-time-use explainer), `Original` (AAC / Opus, no re-encode). Persisted in `localStorage.autocue_dl_format`.
- **Quality toggles**: "Normalize loudness to -14 LUFS" (two-pass `ffmpeg loudnorm`, default off) and "Auto-tag metadata" (FFmpegMetadata + MetadataParser postprocessors; ID3v2 / iTunes atoms / Vorbis Comments / RIFF INFO depending on container; default on).
- **Playlist / album batch**: yt-dlp `noplaylist=True` is no longer hardcoded; client-side URL classifier routes single-video / playlist / mixed URLs; SSE emits `processed` / `total` / `current_title` for "Downloading track 3 of 12" progress with a cumulative bar.
- **Job queue** (`DownloadQueue`) with configurable concurrency cap via `AUTOCUE_DOWNLOAD_CONCURRENCY` env (default 1, clamped to ≤ 4) prevents the 20-simultaneous-click meltdown. Single `queue_lock` + `cancel_pending` set collapses the dequeue/cancel race.
- **Explicit cancel endpoint**: `POST /api/download/cancel/{job_id}` (sync, idempotent). Decoupled from SSE lifecycle so HTTP/2 buffering or ffmpeg-pass-1 silence doesn't strand a cancel. Mid-flight cancel now works on every surface.
- **Watchdog with tiered limits**: subprocess-pulse heartbeat (60 s) catches worker hangs while legitimate long jobs (10-hour DJ-set normalize) keep running. 30-min stuck-phase cap is the final safety net.
- **Classified errors**: `classify_download_error()` maps yt-dlp + ffmpeg failures to 10 user-facing codes (`unavailable_video`, `age_gated`, `region_blocked`, `private_video`, `network_offline`, `network_timeout`, `disk_full`, `ffmpeg_conversion`, `ffmpeg_missing`, `unknown`). Frontend renders message + concrete hint + `[Retry]` button; raw stderr behind a `<details>` disclosure.
- **Persistent success state** with `[Reveal in Finder]` / `[Copy path]` / `[Download another]` actions. Reveal-in-Finder works on macOS / Windows / Linux via `POST /api/download/reveal` with platform-detect + `shutil.which` probe + stable-root path validation.
- **Mobile-first layout** with CSS-var-driven bottom stack (`--ds-strip-h`, `--db-h`, `--ab-h`) coordinating `#download-section` sticky strip, `#download-bar`, and `#action-bar` so the three bottom-fixed elements never collide. `@supports selector(:has(*))` guard with class-toggle fallback for Firefox < 121.
- **Settings → Download** block with "Always skip Shift-click download confirmation" checkbox (two-way mirror to sessionStorage).
- **Keyboard**: `Enter` submits in URL input; `Shift+D` global shortcut focuses Download from any tab; Esc precedence properly defined (modal-close first, double-Esc cancels in-flight on manual panel).
- **`#dl-queue-indicator`** shows `N active · M queued (max C concurrent)` with `[Cancel queued]` action. Polling paused via `visibilitychange` when tab is hidden.

#### Changed

- **Default audio format** flipped from MP3 192 kbps to MP3 320 kbps. One-time migration toast on first session for users with a previous `autocue_dl_format` value.
- **Confirm modal restyled**: "Download album {name}?" with primary green "Download album" + ghost "Cancel" (was inverted). Cancel keeps default keyboard focus; primary disabled for 250 ms after open as a sticky-Shift safety. Drops the loaded word "anyway".
- **Naming-collision cleanup**: `#download-btn` "Download XML" → **"Export XML"**; `#backup-btn` "💾 Download backup XML" → **"💾 Save backup XML"**; step-4 wizard label updated; "Download backup first →" link updated. The verb "Download" is reserved for audio fetch.
- **Helper copy** drops `yt-dlp` jargon and the sibling-component phrase "Use the ⬇ Album button on any suggestion above". Component is portable across pages.
- **`#yt-modal` candidate picker**: "Pick" → "Download this version"; replaces hand-rolled SSE parser with `_consumeSSE`; gains focus trap + Escape + focus-return.
- **SSE event schema unified**: `DownloadProgressEvent` Pydantic model is the contract for both `/api/download/stream/{job_id}` paths. Schema-driven pytest catches drift.
- **Error rendering**: `ffmpeg returned exit code 1: Conversion failed!` no longer surfaces as user copy. Raw stderr lives behind `<details>Show technical details</details>`.

#### Removed

- **`audio_quality` request field** removed from `DownloadRequest` and `DownloadAlbumRequest`. Migration: encode the choice via `audio_format` (`mp3_320` for 320 kbps MP3, `wav` for uncompressed). Stale clients receive a 422 with `error_code="audio_quality_removed"` and a one-line migration hint.
- **Hardcoded `noplaylist=True`** in `download_audio()`. Playlist behavior is now opt-in via `allow_playlist=True` in the request.

#### Fixed

- Confirm modal no longer inverts color semantics (the audit's #1 critical finding).
- Per-card ⬇ Album button no longer silently downloads only track 1 of an album playlist URL.
- Mid-download Cancel works on every surface; backend `cancel_event` fires reliably via the explicit cancel endpoint instead of relying on SSE disconnect detection.
- Partial-file cleanup on cancel uses an mtime gate (`mtime >= job.started_at`) so pre-existing same-named user files are never deleted.

#### Accessibility (WCAG 2.2 AA)

- `<label for=>` associations on every input.
- `<progress>` element with `aria-labelledby`, `aria-live="polite"` status region, `role="alert"` + `aria-live="assertive"` error region, `aria-busy="true"` container during loading, `aria-disabled="true"` on Download button during loading (replaces native `disabled` to keep focusability).
- Focus trap + Escape + focus-return on `#yt-modal` and `#disc-v2-dl-confirm`.
- Touch targets ≥ 24 × 24 px (AA) on desktop; ≥ 44 × 44 px (AAA) on mobile.
- `prefers-reduced-motion` honored.

#### Docs

- New `docs/reference/youtube-download.md` covering format migration, yt-dlp upgrade checklist, browser support baseline.
- Updated `CLAUDE.md` "Must-know constraints" with Download row.
- Updated `docs/FEATURES.md` Download section.

#### Browser support

- **Baseline**: Safari ≥ 15.4 (Mar 2022), Firefox ≥ 121 (Dec 2023), Chromium ≥ 105. Older browsers fall back to in-flow placement of `#download-section` (mobile sticky strip disabled).

---

## [0.1.0] — Earlier

Initial AutoCue release. See git history for details.
