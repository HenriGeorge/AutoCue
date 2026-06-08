# PRD — Download Component Refactor

**Version:** 1.0 (LOCKED — post-grill round 6)
**Owner:** Henri George
**Date:** 2026-06-08
**Audit Score (current):** Overall **3.9 / 10** (Discoverability 4.6 · Clarity 3.9 · A11y 4.3 · Efficiency 4.3 · Consistency 3.5 · User confidence 3.5 · Mobile 3.5) — source: `.agent/ux-audit/download-audit-synthesis.json` (8-persona audit, 2026-06-08).
**Iteration log:** `.agent/prd/DOWNLOAD_PRD_iteration-log.md`

> **Stack constraint** (CLAUDE.md line 40): the AutoCue web app is a **single vanilla-JS HTML file** (`docs/index.html`) with no build step, no React, no Tailwind. This PRD reframes the user's "React + Tailwind" prompt as a same-stack refactor that achieves identical outcomes via a self-contained IIFE module and CSS-variable theming.

---

## 1. Executive Summary

AutoCue exposes a YouTube → audio download capability behind **6 surfaces** in `docs/index.html` (manual panel, per-Discover-card ⬇ Album, Shift-click confirm modal, track-info Download button, YouTube candidate-picker modal, plus a naming-collision risk with "Download XML" / "Download backup XML"). Each surface has its own SSE driver, its own progress visualization, and its own error UX. The audit's hardest finding: the **most user-facing strings leak `yt-dlp`/`ffmpeg` jargon**, the **primary Download CTA is rendered as a ghost button** while a destructive `Download XML` button next to it is the only brand-green primary, the **Shift-click confirm modal paints Cancel as the primary** (inverts color semantics), the backend hardcodes `noplaylist=True` (so the **⬇ Album button silently downloads only track 1**), and the engine supports cancellation but **no surface exposes Cancel mid-flight**.

This PRD ships a **single canonical Download module** (`_Download` IIFE) consumed by every surface, adds **format selection** (MP3 320 default · WAV · Original), **playlist/album batch** with X-of-Y progress, **loudness-normalize-to-LUFS** and **ID3-auto-tag** toggles, explicit **Idle/Loading/Success/Error** states with classified errors + Retry + Reveal-in-Finder, full **WCAG 2.2 AA** compliance, and a **mobile-first** layout pass. The component becomes **context-free** so the same panel can be embedded on Discover, Library, Cues, and any future page without copy edits.

---

## 2. Problem Statement

| # | Pain (current state) | Source |
|---|---|---|
| 1 | "Download" CTA is rendered as ghost while the next-door "Download XML" is the only brand-green primary — primary task looks tertiary | findings #2, #10 |
| 2 | Per-card "⬇ Album" downloads exactly **one track** because `noplaylist=True` is hardcoded in `download_audio()` (`autocue/download.py:154`) — user expects a whole album | finding #7 |
| 3 | Shift-click confirm modal paints **Cancel as the brand-green primary** and "Download anyway" as ghost — inverts color semantics, loaded copy | finding #1 |
| 4 | Raw stderr (`✗ ERROR: ffmpeg returned exit code 1: Conversion failed!`) leaks to users with **no retry, no next-step, no recovery** | finding #3 |
| 5 | Helper copy hardcodes `yt-dlp` jargon and the sibling-component phrase **"Use the ⬇ Album button on any suggestion above"** — blocks reuse on any other page | finding #4 |
| 6 | **Three near-identical SSE drivers** (`runDownload`, `downloadManual`, `_ytDownload`) with divergent AbortController support, error UX, and SSE parsers — ~150 LOC duplicated | finding #5, consistency #2 |
| 7 | **No cancel during in-flight downloads** — engine supports it (`cancel_event` in `download.py:105`) but no surface wires `AbortController` to fetch | finding #6 |
| 8 | **No format / quality / loudness / metadata controls** — silently writes 192 kbps MP3. DJs need WAV; podcasts need -14 LUFS; cataloguing needs proper ID3 | finding #9, user asks 2/4 |
| 9 | **Progress bar has no ARIA** (`role=progressbar`, `aria-valuenow`, `aria-live`); status changes silent to screen readers; modals lack focus trap | a11y violations 1-12 (WCAG 1.3.1, 1.4.3, 2.1.1, 2.4.3, 2.5.8, 3.2.4, 4.1.2, 4.1.3) |
| 10 | **Mobile layout broken**: Download panel sits below the fold on 390 px viewports, destination path overflows, legal disclaimer dominates ~30% of viewport, touch targets <24 px | mobile issues 1-10 |
| 11 | **Backend SSE schemas differ** between `/api/download` (processed/total/query/percent/status) and `/api/download/album` (adds title/path/downloaded) — no shared contract, client branches on key presence | consistency #8 |
| 12 | Success state auto-hides via `setTimeout(…, 1000)` (`docs/index.html:7059`) — user can't see where the file landed long enough to act | UX persona finding |

Closing these turns Download from a "works if you know what you're doing" tool into a **first-class user feature**.

---

## 3. Target Audience

- **Primary**: Henri — DJ, ~5000 Rekordbox tracks, downloads daily for set prep
- **Secondary**: His DJ friends — same workflow, varying technical depth
- **Tertiary**: First-time AutoCue users who land on Discover and want to grab a release they recognize

### Assumptions

- Local-mode server (`autocue serve`) is running. Pages-mode (GitHub-hosted static) **never has Download** (already enforced — the panel hides when `/api/download/config` is absent or `available=false`).
- yt-dlp + ffmpeg are **optional dependencies**; missing-tool banner remains.
- Default music destination is `~/Music/AutoCue` or `AUTOCUE_DOWNLOAD_DIR` env.
- Rekordbox database is NOT touched by Download (audio fetch only — `/api/apply` is the only write path).

---

## 4. Success Metrics

| Metric | Current | Target | How measured |
|---|---|---|---|
| Pain-row regression checklist (§2 rows 1-12) | 0 / 12 closed | **12 / 12 closed** with paired test (vitest or pytest or Playwright) | One test per row asserts the specific behavior is fixed; CI gate |
| Mid-download cancel availability | 0 / 5 surfaces | **5 / 5** | All download buttons swap to Cancel during loading; Playwright covers each |
| Surfaces using a single Download module | 0 / 5 | **5 / 5** | grep asserts `runDownload`, `downloadManual`, `_ytDownload` symbols are deleted |
| Format / loudness / metadata controls available | 0 | **3 controls** | Format `<select>` + 2 `<input type=checkbox>`; persisted in localStorage; vitest |
| Playlist URL → multi-track download | broken (1/N tracks) | **N/N tracks** with X-of-Y progress | E2E test: paste known playlist URL, all entries land |
| Error → recovery path | 0% | **100%** errors classified + show [Retry] | Backend classifier covers ≥ 8 codes with real yt-dlp fixtures (`tests/fixtures/download_errors/*.txt`) |
| WCAG 2.2 AA violations | 12 catalogued | **0** open | axe-core scan of Download surfaces passes; manual screen-reader pass on idle + loading + success + error states |
| Mobile reachability (390 px viewport) | panel below fold | **panel reachable within 1 viewport scroll from Discover tab top** | Playwright + screenshot diff |
| jargon in user-facing copy | "yt-dlp", "ffmpeg", "exit code" present | **0 occurrences** in user-visible strings | `tests/test_download_jargon.py` greps DOM-rendered + JSON event strings (whitelist: install banner only) |
| SSE schema drift between /api/download and /api/download/album | 5 key deltas | **0** (single shared `DownloadProgressEvent` model, jsonschema-tested) | Pytest validates each emitted dict against `DownloadProgressEvent.model_validate` |
| Concurrent-download starvation | unbounded fanout possible | **0** (FIFO queue, concurrency ≤ `AUTOCUE_DOWNLOAD_CONCURRENCY`) | Integration test: 20 simultaneous starts → 19 see `phase: "queued"` |
| Audit re-score | 3.9 / 10 | **≥ 7.5 / 10** | **Post-ship validation only**, not a merge gate. Run once on the final commit; record in `.agent/ux-audit/download-audit-post.json`. Re-runs ±0.4 per persona-noise are acceptable. Failure prompts a Tier 2 follow-up, not a revert. |

---

## 5. Competitive Landscape

| Product | What it does well | What we take / leave |
|---|---|---|
| **yt-dlp GUI front-ends** (Open Video Downloader, Tartube) | Format pickers, batch queues, ID3 tagging baked in | Take: format dropdown, batch queue. Leave: their cluttered chrome. |
| **JDownloader / Folx** | Multi-protocol queue, throughput, history | Take: persistent recent-downloads list with re-download. Leave: anything beyond a single in-flight job (AutoCue is per-track, not a download manager). |
| **Bandcamp app** | One-click download, format choice at purchase | Take: format defaults sticky per-user. Leave: paid model. |
| **Soundiiz / Tunemymusic** | Playlist-aware imports | Take: playlist URL → N-track batch with X-of-Y progress. |
| **macOS Safari downloads bar** | Floating completion shelf with Reveal-in-Finder | Take: success-state Reveal-in-Finder action via `open -R <path>` subprocess. |

**Differentiator**: AutoCue is the only DJ-library tool where Download is **inline with the personalized release feed** and **writes audio + ID3 + LUFS-normalized output** at the speed of "paste a URL, hit Enter." No tab-switching, no separate downloader app, no post-processing chain.

---

## 6. Feature Spec

### 6.1 Single canonical `_Download` IIFE (FE-CORE)

Replaces `runDownload` (`docs/index.html:6974-7016`), `downloadManual` (`7018-7061`), and `_ytDownload` (`~10807-10858`). One state machine, one SSE driver, one AbortController per job, one classified-error renderer.

**State machine** (single source of truth):

```
idle ──submit──▶ loading ──progress*──▶ loading ──done(ok)──▶ success
  ▲                ▲                                              │
  │                └──cancel──▶ idle                              │
  └─reset────────────────────────────────────────────────────────┘
                  ──done(err)──▶ error ──retry──▶ loading
                                       ──dismiss─▶ idle
```

**Public API**:

```js
const job = _Download.start({
  query,               // URL or search term (required)
  mode: 'single' | 'album' | 'playlist',  // default 'single'; 'playlist' allows yt-dlp playlist expansion
  format: 'wav' | 'mp3_320' | 'original', // default = user pref from localStorage
  normalize: bool,     // default = user pref
  embedMeta: bool,     // default = user pref
  dest: string | null, // default = user-selected dest
  tracks: [{query,title}],  // only for mode='album' — explicit track list (Discover use case)
  onState: fn(state),  // {phase, percent, processed, total, currentTitle, path, classifiedError, raw}
});
job.cancel();          // calls POST /api/download/cancel/{job_id} AND abort()s the SSE fetch
```

**Internal fetch shape** (resolves round-4 M1):

```js
// 1. Enqueue: POST returns job_id synchronously (≤200ms; no yt-dlp work)
const ctrl = new AbortController();
const enq = await fetch('/api/download/enqueue', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({query, audio_format, normalize, embed_metadata, allow_playlist, dest_dir}),
  signal: ctrl.signal,
});
if (!enq.ok) throw new Error((await enq.json()).error_message || enq.statusText);
const {job_id} = await enq.json();

// 2. Cancel is now wired BEFORE stream opens
//    job.cancel = async () => { try { await fetch(`/api/download/cancel/${job_id}`, {method:'POST'}); } finally { ctrl.abort(); } }

// 3. Stream: GET SSE
const stream = await fetch(`/api/download/stream/${job_id}`, {signal: ctrl.signal});
if (!stream.ok) throw new Error(`HTTP ${stream.status}`);
await _consumeSSE(stream, onEvent, ctrl.signal);   // _consumeSSE is verb-agnostic (line 4553)
```

`_consumeSSE` at `docs/index.html:4553` takes `(response, onEvent, signal)` and operates on any `Response` object — GET works without modification.

**View binding helpers** (each surface is a thin renderer):

```js
_Download.bindManualPanel(rootEl);          // #download-section
_Download.bindCardButton(btnEl, query, opts); // .disc-dl-btn
_Download.bindTrackInfoFlow(btnEl, query);  // #ti-download
_Download.bindYoutubeModal(modalEl);        // #yt-modal
_Download.bindConfirmModal(modalEl, payload); // #disc-v2-dl-confirm
```

All surfaces share: `aria-busy` on container during loading, `aria-live="polite"` on status, `role="progressbar"` with `aria-valuenow/min/max` on the bar, `role="alert"` on the error container, Cancel button swap during loading.

### 6.2 Format selector (UI-FMT-01)

Native `<select>` (no custom dropdown — works for free with screen readers, mobile pickers, and keyboard). Placed **inline with the URL input** to the right (desktop) or **below the input** (mobile). Three options:

| Value | Label | yt-dlp `preferredcodec` | Postprocessor | Notes |
|---|---|---|---|---|
| `mp3_320` | **MP3 320 kbps** | `mp3` + `preferredquality='320'` | `FFmpegExtractAudio` | **Default** (resolves grill M5: WAV-from-lossy trap; matches storage parity with paid downloads). |
| `wav` | **WAV** (uncompressed) | `wav` | `FFmpegExtractAudio` | First time user picks WAV, show inline explainer: "WAV is uncompressed but the YouTube source is lossy — file is large (~5× MP3) and not actually lossless audio. Use this only if you need PCM for downstream tools." Dismissible; doesn't repeat in the same session. |
| `original` | **Original** (AAC / Opus) | — | **no** postprocessor; yt-dlp keeps `bestaudio/best` container | Fastest, no re-encode. Container varies (m4a / webm). |

**First-paint behavior** (resolves grill m2): when `localStorage.autocue_dl_format` is absent → default to `mp3_320` silently (no toast). When the key holds a value from a previous version that's no longer in the enum (e.g. `mp3`, `m4a`) → coerce to `mp3_320` and show one-time toast "Your saved format is now MP3 320 kbps. Change in the Format dropdown."

**Backend coercion** (resolves grill B1 + round-2 M3): backend accepts these legacy values and maps them. Coercion logged at `WARNING` level on every use; **removed in the release after the one that lands this PRD** (concrete deprecation gate: this PR's version + 1 minor bump). Documented in `docs/reference/youtube-download.md#format-migration`.

| Legacy value | Mapped to | Reason |
|---|---|---|
| `"mp3"` | `"mp3_320"` | Current default; common in scripts |
| `"m4a"`, `"aac"`, `"opus"` | `"original"` | Closest match (no re-encode) |
| `"flac"`, `"alac"`, `"vorbis"`, `"wav"` (case-insensitive) | `"wav"` | Closest lossless container we support; FLAC support deferred to Tier 2 |
| Anything else | **422** with `error_code="unknown_format"`, `error_message="Unknown format '<value>'. Pick one of wav / mp3_320 / original."` |  |

**Log discipline** (resolves round-3 m2): coercion logs at `WARNING` **once per (process lifetime, legacy_value)** tuple. Subsequent occurrences of the same legacy value drop to `DEBUG`. Prevents spam in scripted workflows; the WARNING line still appears once for operators to notice.

**`audio_quality` separately**: pydantic's `extra='forbid'` (§7.2) raises `422` with `{"detail":[{"type":"extra_forbidden","loc":["body","audio_quality"], "msg":"Extra inputs are not permitted"}]}`. Server-side middleware intercepts this specific case and rewrites the response to `{"error_code":"audio_quality_removed", "error_message":"audio_quality removed; use audio_format='mp3_320' for 320 kbps MP3 or 'wav' for uncompressed.", "hint":"Update your client to set audio_format instead."}`. Documented in `docs/reference/youtube-download.md#format-migration`.

Selection persists to `localStorage.autocue_dl_format`. Echo into success status: `"Saved as MP3 320 to /path/file.mp3"`.

### 6.3 Playlist / album batch (BE-PLAYLIST-01 + UI-BATCH-01)

**Backend**: Remove `noplaylist: True` from `download_audio()`. Add a new request shape that **pre-flights the playlist** (yt-dlp `extract_info(download=False)`) → builds a track list → calls `/api/download/album`'s sequential loop. Emit SSE events that carry `processed`, `total`, `current_title`, `current_query`, `percent` per-track.

**Frontend**: Client-side URL classifier (`_classifyDownloadTarget(q)`) returns one of:

- `single_video` — `https://youtu.be/X` / `https://www.youtube.com/watch?v=X`
- `playlist` — `…?list=…` (no `v=`) — treat as full-playlist
- `mixed_video_in_playlist` — `?v=X&list=…` — prompt: "Just this video, or the whole playlist?"
- `search` — bare term, no URL
- `invalid` — empty, multi-line, malformed

`mode` flows from the classifier; `Download` button label changes contextually (`Download` / `Download playlist (N)` / `Search & download`).

**UI**: Progress widget renders **two bars** in batch mode:
- Top: `Downloading track 3 of 12 · The Field — Pink Sun (50%)` with per-track 0-100%
- Bottom: cumulative `3/12` segment fill
Single-track mode hides the bottom bar.

### 6.4 Loudness normalization toggle (BE-LUFS-01 + UI-FMT-02)

Checkbox: **"Normalize loudness to -14 LUFS"**. Default: **off** (changing audio characteristics is surprising; opt-in is safer). When on, after `download_audio()` returns, run a **two-pass ffmpeg loudnorm**:

```python
# Pass 1: measure
ffmpeg -i src -af loudnorm=I=-14:LRA=11:TP=-1:print_format=json -f null -
# Parse measured_I, measured_TP, measured_LRA, measured_thresh, target_offset
# Pass 2: apply
ffmpeg -i src -af loudnorm=I=-14:LRA=11:TP=-1:measured_I=…:measured_TP=…:measured_LRA=…:measured_thresh=…:offset=…:linear=true:print_format=summary -c:a <codec> dst
```

For WAV output: `-c:a pcm_s16le`. For MP3 320: `-c:a libmp3lame -b:a 320k`. For Original: see backend rule below.

**Format × Normalize matrix** (resolves grill M2):

| `audio_format` | `normalize=true` | Backend behavior |
|---|---|---|
| `mp3_320` | yes | Run loudnorm two-pass with `-c:a libmp3lame -b:a 320k`. |
| `wav` | yes | Run loudnorm two-pass with `-c:a pcm_s16le`. |
| `original` | yes | **422** with `error_code="normalize_unsupported_for_original"`, `error_message="Normalization isn't available for Original format. Pick WAV or MP3 320 to normalize."`, `hint="auto_switch_to_mp3_320"`. Frontend disables the toggle (with tooltip same text) when `original` is selected. **Stale-client recovery** (resolves round-4 m2): on receiving this 422, the frontend force-flips `format` to `mp3_320`, shows toast "Normalization requires MP3 320 or WAV — switched to MP3 320", **automatically retries the enqueue ONCE** with the new format, and persists the flip to localStorage. Retry tracked via `job._retriedNormalizeFlip: bool` (per-job, in-memory) — second failure surfaces the second error normally, no further retry. Defends against infinite-loop scenarios where the server returns the same 422 for `mp3_320` (resolves round-5 Min-2). |

**Cancel mid-normalize** (resolves grill M3): the backend keeps a `subprocess.Popen` handle for each ffmpeg pass and an `asyncio.Event`/`threading.Event` watcher thread that calls `proc.terminate()` (then `proc.kill()` after 2 s grace) when `cancel_event.is_set()`. Pass-1 has no native progress callback, so the SSE emits `phase: "normalizing", percent: null` (indeterminate UI). Pass-2 progress is parsed from ffmpeg `-progress pipe:1` (line-based `out_time_ms=...`) and emitted as real `percent`.

**Partial-file cleanup on cancel/error** (resolves grill M4 + round-2 M1/M2): every job tracks `output_artifacts: set[Path]` (yt-dlp's intermediate `.webm`/`.m4a`, the extracted `.mp3`/`.wav`, any `.tmp` / `.part` siblings) populated **at the moment of creation** via yt-dlp progress hooks + ffmpeg-subprocess wrappers. The `finally:` block of the worker thread removes anything in `output_artifacts` whose path **(a) doesn't match the success path returned** AND **(b) was created with `mtime >= job.started_at`**. The mtime gate defends against deleting a user's pre-existing same-named file (e.g. re-downloading a track that's already on disk). No glob sweeps — those are filesystem-unsafe when `AUTOCUE_DOWNLOAD_CONCURRENCY > 1` and `dest_dir` is shared. Documented in `docs/reference/youtube-download.md`.

Emit progress as `phase: "normalizing"` events.

### 6.5 ID3 auto-tag toggle (BE-ID3-01 + UI-FMT-03)

Checkbox: **"Auto-tag metadata"** (renamed from "ID3" — neutral name covers RIFF INFO + Vorbis Comments too). Default: **on** (additive, never destructive). Adds two yt-dlp postprocessors:

```python
{"key": "FFmpegMetadata", "add_metadata": True},
{"key": "MetadataParser", "actions": [
    (yt_dlp.postprocessor.MetadataParserPP.interpretter, "title", "(?P<artist>.+?) ?[-–—] ?(?P<title>.+)"),
    # falls through cleanly when separator not found
]},
```

| `audio_format` | `embed_metadata=true` | Behavior |
|---|---|---|
| `mp3_320` | yes | ID3v2.4 tags written by `FFmpegMetadata`. |
| `original` (m4a) | yes | iTunes-style `udta`/`meta` atoms via `FFmpegMetadata`. |
| `original` (webm/opus) | yes | Vorbis Comments via `FFmpegMetadata`. |
| `wav` | yes | RIFF INFO chunks (LIST/INFO). ffmpeg writes these reliably for WAV PCM. **Toggle stays enabled** for WAV (resolves grill m4). |

Echo result into success state: `Tagged as Artist: X · Title: Y` (omit when MetadataParser regex didn't match, e.g. video title was just "Pink Sun"). When the regex falls through, fall back to embedding the raw video title as Title with no Artist.

### 6.6 Component states

| State | What renders | A11y |
|---|---|---|
| **Idle** | Input + Format select + 2 toggles + primary `Download` button. Helper: "Download a track or album from YouTube as audio." | label-for, autocomplete=off, autofocus when section enters viewport |
| **Loading (single)** | Primary button → "**Cancel**" (with spinner); progress bar 0-100%; status: "Fetching… 45% · 4.2 MB / 9.3 MB · 1.8 MB/s · 3s left" | aria-busy=true on container; aria-live=polite on status; role=progressbar w/ valuenow |
| **Loading (batch)** | Same as loading + second cumulative bar; status: "Downloading track 3 of 12 · The Field — Pink Sun (45%)" | aria-live=polite; bar role=progressbar |
| **Success** | Persistent card (no auto-hide): "✓ Saved as MP3 320 to `<path>`" + actions [Reveal in Finder] [Copy path] [Download another] [Show details ▾]. **"Download another"** resets `#dl-query.value=''` and refocuses input; format dropdown, normalize, and embed_metadata retain their values (sticky user prefs, read from localStorage). Success card persists across queries until explicitly dismissed or replaced by the next job's state. (Resolves round-4 m3.) | role=status; aria-live=polite |
| **Error (classified)** | "Couldn't download. <Human message + concrete next step>" + actions [Retry] [Show technical details ▾] | role=alert; aria-live=assertive; focus moves to error region |
| **Unavailable** | Existing banner: "Download tools are not installed. Run `pip install -e \".[download]\"` and add ffmpeg to PATH." | Existing |

### 6.7 Classified errors (BE-ERR-01)

`autocue/download.py` adds `classify_download_error(exc) -> {code, user_message, hint}`.

**Fixture-driven taxonomy** (resolves grill M7 + round-2 M5): every code below has a real-world fixture under `tests/fixtures/download_errors/<code>.txt` containing the exact stderr / exception message yt-dlp emits today, captured against the pinned yt-dlp version. `test_classify_download_error.py` parametrizes over the fixture directory; adding a new code requires adding a fixture.

**yt-dlp upgrade workflow** (so fixtures don't rot silently):

1. **CI smoke test** (`tests/test_classify_download_error.py`): hard-fails when an existing fixture stops mapping to its expected code. Blocks the upgrade until fixtures are updated.
2. **Quarterly live integration test** (`tests/integration/test_download_classify_live.py`, gated `RUN_LIVE_YTDLP=1`): hits real YouTube URLs known to produce each error (private video, age-gated video, deleted-video URL, region-blocked URL); asserts current classification. Not run in CI. Human-run on yt-dlp version bumps.
3. **Upgrade checklist** in `docs/reference/youtube-download.md#yt-dlp-upgrade`: (a) bump pin, (b) run `RUN_LIVE_YTDLP=1 pytest tests/integration/test_download_classify_live.py`, (c) update fixtures from the new actual strings, (d) re-run `pytest tests/test_classify_download_error.py`, (e) note any new error codes seen in the wild. **Canonical fixture URLs are listed in the doc** so they don't rot silently — sourced from yt-dlp's own test suite where possible.
3.5 **Meta-test** (`tests/integration/test_classify_live_fixtures_distinct.py`, also `RUN_LIVE_YTDLP=1`): asserts each canonical URL still produces a **distinct** error_code. If two URLs collapse to the same code, the fixture is rotting — fails loudly.
4. **Prod error reporting (stub)**: server logs every `error_code="unknown"` classification with a redacted snippet of `raw` (first 200 chars, no URLs). Users grepping their server log can identify new patterns to file as fixtures.

Initial taxonomy:

| Code | Trigger (regex / type) | User message | Hint |
|---|---|---|---|
| `unavailable_video` | `yt_dlp.utils.DownloadError` w/ `"Video unavailable"` / `"This video has been removed"` | "This video isn't available anymore." | "Try another search result." |
| `age_gated` | `"Sign in to confirm your age"` | "YouTube requires sign-in for this video." | "Use a different source." |
| `region_blocked` | `"not available in your country"` | "Not available in your region." | "Try a different upload of the same track." |
| `private_video` | `"This video is private"` | "This video is private." | "Pick another candidate." |
| `network_offline` | `OSError` w/ `errno` ∈ {network unreachable} | "No internet connection." | "Reconnect and retry." |
| `network_timeout` | `socket.timeout` / `URLError` timeout | "YouTube isn't responding." | "Try again in a moment." |
| `disk_full` | `OSError(errno.ENOSPC)` | "No space left on disk." | "Free up space at `<dest>` and retry." |
| `ffmpeg_conversion` | `PostProcessingError` / non-zero ffmpeg exit | "Couldn't convert the audio." | "Try a different format (Original)." |
| `ffmpeg_missing` | `RuntimeError("ffmpeg not found on PATH")` | "ffmpeg isn't installed." | "Install it and restart the server." |
| `unknown` | anything else | "Something went wrong." | "Try again, or open Show details for the raw error." |

Every error response carries `{code, user_message, hint, raw}`. Frontend Retry button calls `_Download.start(originalArgs)`.

### 6.8 Naming-collision cleanup (UI-COPY-01)

Per finding #10. Rename within `docs/index.html`:

| Element | Before | After |
|---|---|---|
| `#download-btn` text | "Download XML" | **"Export XML"** |
| `#download-btn` tooltip | "Download a Rekordbox XML…" | "Export a Rekordbox XML file…" |
| `#backup-btn` text | "💾 Download backup XML" | "**💾 Save backup XML**" |
| Step 4 wizard label | "Download" | "Export" |
| Helper at 2407 | "**Download** the updated XML, then import…" | "**Export** the updated XML, then import…" |

The verb "Download" is reserved exclusively for **audio fetch** going forward.

### 6.9 Mobile layout (UI-MOBILE-01)

Add `@media (max-width: 640px)` block scoped to Download surfaces (chosen to match existing `@media (max-width:640px)` block already in `docs/index.html`; iPad portrait at 768 px keeps desktop layout — acceptable per round-2 m1):

- Input → full-width row 1; Format select → full-width row 2; Download button → full-width row 3 (44 px min height)
- Toggles → 2 full-width rows below
- Destination path uses `text-overflow: ellipsis` + middle-truncation (CSS clamp to ~24 chars) with full path on tap
- Legal disclaimer collapses to single line + "(?) Why?" disclosure (full text on tap)
- All touch targets ≥ 44 × 44 px (WCAG 2.5.5 AAA)
- **`#download-section` mobile sticky strip — coordinated with `#download-bar` and `#action-bar`** (resolves round-3 B1). Three bottom-fixed elements share the screen on Discover-mobile. CSS vars drive the stack:

  ```css
  :root {
    --ds-strip-h: 0px;    /* set to 48px when #download-section.collapsed is active on mobile */
    --db-h: 0px;          /* set to 66px when #download-bar.visible */
    --ab-h: 0px;          /* set to 56px when #action-bar.visible */
    --safe-bottom: env(safe-area-inset-bottom, 0px);
  }
  /* Stack order, top-down on screen: action-bar → download-bar → download-section strip → safe-area */
  body:has(#download-section.collapsed) #download-section { bottom: calc(var(--db-h) + var(--ab-h) + var(--safe-bottom)); }
  body:has(#download-bar.visible)        #download-bar    { bottom: calc(var(--ab-h) + var(--safe-bottom)); }
  body:has(#action-bar[aria-hidden="false"]) #action-bar   { bottom: var(--safe-bottom); }
  ```

  Each element observes its own visibility class and sets the corresponding CSS var. The existing `--ab-rest-y: -66px` calc at `index.html:906` is **replaced** by the var-driven model; that math becomes redundant. Collapsed strip is `48 px` tall; expanded full panel renders in-flow at the bottom of the Discover scroll area (not sticky) — tap on strip scrolls into view and uncollapses.
- **Fallback**: if both `#download-bar.visible` AND `#action-bar[aria-hidden=false]` are true (4 selected tracks + ready-to-import), the sticky-collapsed strip **hides itself** and the `_Download.bindManualPanel` view falls back to its in-flow position. Tap on the existing "Jump to Download" anchor in the Discover header gets there. This keeps the bottom band readable.

### 6.10 Reveal in Finder / Open folder (FE-SUCCESS-01)

Success state actions:
- **Reveal in Finder** → `POST /api/download/reveal {path}` runs `subprocess.run(["open", "-R", path])` on macOS; `subprocess.run(["explorer.exe", "/select,", path])` on Windows; `subprocess.run(["xdg-open", os.path.dirname(path)])` on Linux. Returns 204. Frontend disables the button on platforms where the call fails.
- **Copy path** → `navigator.clipboard.writeText(path)` + toast.
- **Download another** → resets to idle, refocuses input.

**Path-validation gate** (resolves grill M9 + round-4 M4): the `RevealPathRequest` validator MUST:
1. `Path(path).resolve(strict=True)` — must exist on disk; reject otherwise (`404`).
2. Compute `allowed_roots = [Path(default_download_dir()).resolve()]` PLUS `Path(_detect_music_folder(db)).resolve()` if non-null PLUS `Path(os.environ.get("AUTOCUE_DOWNLOAD_DIR")).resolve()` if set. These are **stable roots** that survive server restart — fixes the round-4 day-2 bug where in-process `known_user_dests` would empty after a restart and reject Reveal calls from a still-visible success card.
3. Assert `any(resolved.is_relative_to(root) for root in allowed_roots)` — reject with `403 forbidden_path` otherwise. Specifically defeats `POST /api/download/reveal {path: "/etc/passwd"}` (not under any music root).
4. On platforms without a reveal binary, return `501 reveal_unsupported_platform`. Frontend hides the button.

The earlier in-memory `known_user_dests` is dropped — stable-root validation covers the same threat model without the day-2 bug.

**Platform capability probe** (resolves round-2 M6): `os_reveal_supported: bool` in `/api/download/config` uses **binary presence**, not just `sys.platform`:

```python
def _reveal_supported() -> bool:
    import sys, shutil
    if sys.platform == "darwin":    return shutil.which("open") is not None
    if sys.platform == "win32":     return shutil.which("explorer.exe") is not None
    if sys.platform.startswith("linux"): return shutil.which("xdg-open") is not None
    return False
```

This catches minimal Linux containers where `xdg-open` isn't installed. The endpoint itself also returns `501` on a missing-binary call as a second line of defense.

### 6.11 Recent downloads — DEFERRED to Tier 2

Out of scope for v1 (resolves grill m1). Removed from §6. Tracked in §10 "Out of scope".

### 6.12 Download job queue (FE-QUEUE-01 + BE-QUEUE-01) — resolves grill B2

**Problem**: nothing in v0.1 prevented 20 simultaneous `⬇ Album` clicks across Discover cards from spawning 20 concurrent yt-dlp + ffmpeg processes (with normalize=on for an album → 24 ffmpeg pass-1 + 12 pass-2 per album). Box melts.

**Solution**: a single in-process FIFO queue with **concurrency cap = 1** (Tier 1 — sequential downloads). Configurable via env `AUTOCUE_DOWNLOAD_CONCURRENCY` (default `1`, max enforced `4`).

**Frontend behavior**:
- `_Download.start(args)` always enqueues. If a job is already in-flight, the new job goes into a visible queue.
- Each `bindCardButton` and `bindManualPanel` view subscribes to its own job's lifecycle. While `queued`, the button label = "Queued (N)" with a `[Cancel queued]` action that removes the job before it starts.
- The manual panel additionally renders a queue indicator: `"3 in queue · current: <title>"` with a `[Cancel queue]` global action.
- `phase: "queued"` is the initial state for any non-immediately-started job.

**Backend behavior**:
- `autocue/download.py` adds `DownloadQueue` — a `queue.Queue` + single worker thread (when `AUTOCUE_DOWNLOAD_CONCURRENCY=1`) or a small `ThreadPoolExecutor(max_workers=N)` (when N>1). Jobs identified by uuid4. Each job carries its own `cancel_event`.
- The SSE endpoints become thin shims: they enqueue a job, then stream events from the worker via a `queue.Queue` per-job event bus until `done`/disconnect.
- **Concurrency is process-global** — does NOT use `autocue/analysis/concurrency.py` (the analysis pool). Download is I/O-and-ffmpeg-heavy; mixing it with the analysis pool would starve `/api/classify`, `/api/auto-tag`, etc.
- On server shutdown: drain & cancel all queued + active jobs; emit `status: "cancelled"` SSE before close.

**Locking discipline** (resolves grill round-2 B2): a single `queue_lock: threading.Lock` wraps the critical section of {dequeue → register as active → install per-job `cancel_event` in a shared `cancel_registry: dict[job_id, Event]`}. Cancel requests against a job that has been dequeued-but-not-yet-active are stored in `cancel_pending: set[job_id]`; the worker's first action after acquiring a job is to check `if job.id in cancel_pending: emit cancelled; continue` — under the same lock that registers the active job. This collapses the dequeue → active race.

**Worker crash safety**: the worker thread is wrapped in `while True: try: …; except Exception as exc: logger.exception; emit done(status='error', error_code='worker_crash')`. The queue always advances.

**Watchdog & internal heartbeat** (resolves round-3 M1 + M4):
- **SSE keepalive** (network-only): the SSE stream emits a comment line `: keepalive\n\n` every 15 s. `_consumeSSE` already ignores any line not starting with `data:` (verified `docs/index.html:4567`), so the heartbeat is invisible to the client handler. A new vitest covers `_consumeSSE` against a blob containing `: keepalive\n\n` between data events.
- **Internal worker pulse** (NOT on the wire): the worker thread, while inside any subprocess wait (yt-dlp `extract_info`, ffmpeg pass-1, ffmpeg pass-2), spawns a sidecar heartbeat thread that touches `worker_pulse_at = time.monotonic()` every 5 s as long as the subprocess is alive (`proc.poll() is None`). This proves the worker is alive even when yt-dlp / ffmpeg emit nothing for minutes.
- **Watchdog tiered limits**: a single watchdog thread per `DownloadQueue` checks every 30 s:
  - `time.monotonic() - worker_pulse_at > 60 s` → **kill** (worker thread or subprocess actually dead; emit `done(error_code="worker_crash")`).
  - `time.monotonic() - last_event_at > 30 min` AND `phase ∈ {fetching, normalizing}` → **kill** with `error_code="stuck_phase"`.
  - Otherwise: leave alone. A 10-hour DJ-set ffmpeg pass-1 with active subprocess pulse + clear phase = legitimate; not killed.

**Phantom-job cleanup** (when SSE client vanishes without disconnect signal): `GET /api/download/queue` returns each active/queued job's `last_event_at` timestamp. **Active jobs are NEVER cancelled due to client disconnect** — the user may have closed the tab intentionally and still wants the download to complete to disk. Phantom cleanup only removes **queued** jobs whose enqueueing client hasn't opened a stream in > 60 s.

**Status surface**: new `GET /api/download/queue` returns `{active: {id, title, percent, phase, started_at, last_event_at}, queued: [{id, title, enqueued_at}], max_concurrency: int}` for diagnostics + the manual-panel queue indicator (polled every 2 s while ≥ 1 job is in flight; otherwise idle).

**Queue-indicator behavior** (resolves round-3 m1): the new `#dl-queue-indicator` in §8.2 renders `1 active · N queued (max C concurrent)` using `max_concurrency` from the response. When `N == 0`, hides itself. The `max_concurrency` field thus has an explicit consumer — drift between server config and the indicator becomes visible.

**Polling discipline** (resolves round-4 m1): the 2 s poll runs only while `document.visibilityState === 'visible'`. A `visibilitychange` listener pauses the interval when the tab is hidden, resumes (with an immediate one-shot fetch) when re-shown. Background-tab AutoCue does zero `/api/download/queue` calls.

**Explicit cancel endpoint** (resolves grill round-2 B1): `POST /api/download/cancel/{job_id}` synchronously sets `cancel_registry[job_id].set()` (or moves to `cancel_pending`), returns `200 {cancelled: true}` or `404 {cancelled: false, reason: "unknown_job"}`. **Decoupled from SSE lifecycle** — does NOT depend on the client's SSE stream closing first. Critical because (a) HTTP/2 + buffered responses can delay Starlette's disconnect detection by tens of seconds, and (b) ffmpeg loudnorm doesn't go through yt-dlp's progress hook so the existing `cancel_event` check inside `_hook()` never fires during normalize. Frontend's `job.cancel()` calls this endpoint AND aborts the SSE fetch.

### 6.13 Cross-surface consolidations

| Surface | Change |
|---|---|
| `#download-section` | Becomes `_Download.bindManualPanel()` consumer. Adds format/normalize/ID3 controls. Primary green `Download` button. Persistent success card. |
| `.disc-dl-btn` per-card | Becomes `_Download.bindCardButton()`. Inline progress moves into the row but renders the same component template. Aria-label includes resolved release name. |
| `#disc-v2-dl-confirm` modal | Rebrand: "Download album **{artist} — {title}**?" + body with track count. **Primary button: "Download album"** (brand green). Secondary: "Cancel". Default focus stays on **Cancel** for sticky-Shift safety + add 250 ms disable on the primary to prevent accidental Enter. Add session-only "Don't ask again" checkbox. |
| `#ti-download` track-info | Becomes `_Download.bindTrackInfoFlow()`. Opens yt-modal which is now standardized. |
| `#yt-modal` candidate picker | Add focus trap, Escape close, focus return on close. Replace hand-rolled SSE parser with `_consumeSSE`. Per-candidate `Pick` → `Download this version`. Pre-flight: show MB size + codec on each candidate (yt-dlp `info_dict.formats`). |
| XML buttons | Rename per §6.8. |

---

## 7. Backend Changes

### 7.1 `autocue/download.py`

```python
# New: format → yt-dlp opts builder
def _ytdlp_format_opts(audio_format: str) -> dict:
    """Map a UI format key to yt-dlp opts.
    'wav'      → FFmpegExtractAudio postprocessor with preferredcodec=wav
    'mp3_320'  → FFmpegExtractAudio with preferredcodec=mp3, preferredquality=320
    'original' → no postprocessor; yt-dlp keeps bestaudio/best container
    """

# New: loudness normalization
def normalize_loudness_to_lufs(
    src_path: str, target_lufs: float = -14.0, dest_path: str | None = None,
    progress_cb=None,
) -> str:
    """Two-pass ffmpeg loudnorm. Returns dst path. Raises RuntimeError on ffmpeg failure."""

# New: error classification
def classify_download_error(exc: Exception) -> dict:
    """Map yt-dlp/ffmpeg/OSError instances to {code, user_message, hint, raw}.
    See PRD §6.7 for taxonomy."""

# Updated: download_audio
def download_audio(
    url_or_query: str,
    dest_dir: str | None = None,
    audio_format: str = "mp3_320",          # was "mp3" (with audio_quality="192"); new default per §6.2
    allow_playlist: bool = False,           # was hardcoded noplaylist=True
    embed_metadata: bool = True,            # NEW — adds FFmpegMetadata + MetadataParser PPs
    normalize_lufs: float | None = None,    # NEW — if set, runs loudnorm post-pass
    progress_cb=None,
    cancel_event=None,
) -> str:
    # NOTE: `audio_quality` parameter is intentionally removed. The format key
    # (`mp3_320` / `wav` / `original`) now fully encodes the codec + bitrate
    # decision. Internal mapping in _ytdlp_format_opts() handles the
    # preferredcodec + preferredquality fan-out.
    ...

# New: expand_playlist
def expand_playlist(url: str) -> list[dict]:
    """yt-dlp extract_info(download=False). Returns [{query, title}] per entry.
    Empty list if not a playlist URL or extract fails."""
```

### 7.2 `autocue/serve/schemas.py`

```python
class DownloadRequest(BaseModel):
    query: str
    dest_dir: str | None = None
    audio_format: Literal["wav", "mp3_320", "original"] = "mp3_320"   # default flipped from "mp3" (192 kbps)
    normalize: bool = False
    embed_metadata: bool = True
    allow_playlist: bool = False             # if True, expand & batch
    # NOTE: `audio_quality` is intentionally absent. Resolves round-3 M3:
    # any client posting `audio_quality: "192"` now gets a 422 from the
    # validator below with a migration message:
    #   "audio_quality removed; use audio_format='mp3_320' for 320 kbps MP3
    #    or 'wav' for uncompressed."
    # Pydantic v2: model_config = ConfigDict(extra='forbid') to enforce.

    model_config = ConfigDict(extra='forbid')

class DownloadAlbumRequest(BaseModel):
    tracks: list[DownloadTrackSpec]
    dest_dir: str | None = None
    audio_format: Literal["wav", "mp3_320", "original"] = "mp3_320"   # aligned with DownloadRequest (round-6 F1)
    normalize: bool = False
    embed_metadata: bool = True

    model_config = ConfigDict(extra='forbid')   # same audio_quality migration treatment as DownloadRequest

class DownloadProgressEvent(BaseModel):
    """Standardized SSE event — replaces ad-hoc dicts in both endpoints."""
    type: Literal["progress", "done"]
    phase: Literal["queued", "fetching", "converting", "normalizing", "tagging"] | None = None
    percent: float | None = None             # 0-100 within current track
    processed: int = 0                       # tracks completed (incl current)
    total: int = 1                           # total tracks
    current_title: str | None = None
    current_query: str | None = None
    # done only:
    status: Literal["success", "error", "cancelled"] | None = None
    path: str | None = None
    error_code: str | None = None            # from classify_download_error
    error_message: str | None = None         # user-facing
    error_hint: str | None = None
    error_raw: str | None = None             # behind 'Show details'
    downloaded: int = 0
    failed: int = 0

class RevealPathRequest(BaseModel):
    path: str
```

### 7.3 `autocue/serve/routes.py`

- `POST /api/download/enqueue` — **new; synchronous (≤ 200 ms; does no yt-dlp work).** Accepts `DownloadRequest`. Generates job_id, registers in `cancel_registry`, enqueues. Returns `{job_id: str, phase: "queued", position: int}` immediately. (Resolves round-3 B2: client gets `job_id` BEFORE any progress so cancel is always wired. Resolves round-4 B1: phase is always `"queued"` — no `"starting"` to drift from the §7.2 enum.)
- `POST /api/download/album/enqueue` — same shape; returns `{job_id}` immediately. Body = `DownloadAlbumRequest`.
- `GET /api/download/stream/{job_id}` — **new; SSE.** Streams `DownloadProgressEvent` until `done`. Client opens this immediately after enqueue. If `job_id` unknown → `404`. If the worker has already completed → emits a synthetic `done` event with the cached final status and closes (covers brief reconnects). Cached final status TTL = 60 s.

**Idempotency** (resolves round-4 B2): the cached final status is **single-consumption**. Server marks `cache[job_id].consumed = True` on the first stream open that drains it; subsequent opens within the TTL window return `410 Gone` with `{error_code: "already_consumed", path: …, status: "success"|"error"|"cancelled"}` so the client can still surface the result without re-emitting a `done` event. Frontend additionally tracks `seen_done_for_job: Set<job_id>` in-memory and ignores duplicate `done` events as a defense in depth.

**Frontend 410 handler** (resolves round-5 Maj-1 + Min-3 cross-tab): the `_Download` stream-open code path explicitly checks for `stream.status === 410` BEFORE the generic `if (!stream.ok)` throw:

```js
if (stream.status === 410) {
  const cached = await stream.json();
  // Render success/error state from cached path — NO toast (event was already shown elsewhere)
  if (cached.status === 'success') return view.renderSuccess({path: cached.path, fromCache: true});
  if (cached.status === 'error')   return view.renderError({fromCache: true});
  if (cached.status === 'cancelled') return view.renderIdle();
}
if (!stream.ok) throw new Error(`HTTP ${stream.status}`);
```

This covers tab-switch + queue-poll reconnect, two-tab same-job, and brief disconnect within the 60-s TTL. Both tabs end up showing the success card without competing toasts.
- `POST /api/download` — **deprecated alias**; calls `enqueue` + opens stream in one shot, but the first SSE event is `{type:"queued", job_id}` (instead of starting with `progress`). Maintained for one release for back-compat with existing callers.
- `POST /api/download/album` — same back-compat deprecated alias.
- `POST /api/download/cancel/{job_id}` — **new; sync; idempotent.** Sets the per-job `cancel_event` (or adds to `cancel_pending` if not yet active). Returns `200 {cancelled: true}` or `404 {cancelled: false, reason: "unknown_job"}`. Decoupled from SSE lifecycle.
- `GET /api/download/queue` — returns `{active, queued, max_concurrency, downloads_per_minute_cap}` with `last_event_at` timestamps for the watchdog.
- `POST /api/download/reveal` — new; runs platform open command after path-validation gate (§6.10); 204 ok / 403 forbidden_path / 404 not_found / 501 reveal_unsupported_platform.
- `GET /api/download/config` — extend response with `os_reveal_supported: bool` and `max_concurrency: int` (used by the frontend queue indicator — see §6.12).

All download endpoints emit `DownloadProgressEvent`-shaped SSE. Cancel path: frontend `job.cancel()` → `POST /api/download/cancel/{job_id}` → server sets cancel_event → worker raises `DownloadCancelled` at next tick (yt-dlp progress hook) or `ffmpeg_proc.terminate()` (during loudnorm); then client also aborts the SSE fetch to close the connection.

### 7.4 Cache invalidation

No cache impact — Download writes audio to a destination directory, never touches `master.db` or sidecar cache.

---

## 8. Frontend Changes (vanilla JS, single file)

All changes to `docs/index.html`:

### 8.1 New `_Download` IIFE

Placed near `_consumeSSE` definition. ~250 LOC. Owns state machine, AbortController per job, classified-error renderer, view-bind helpers. Exposed as `window._Download` (so future surfaces can attach without further refactor).

### 8.2 `#download-section` rewrite

```html
<section id="download-section" class="panel-card">
  <header>
    <h2>Download</h2>
    <p class="muted">Download a track or album from YouTube as audio.</p>
  </header>

  <!-- PRESERVED FROM CURRENT MARKUP — toggled by _Download.bindManualPanel
       based on /api/download/config { available, ffmpeg } (resolves round-3 M2) -->
  <div id="download-unavailable" hidden>
    Download tools are not installed. Run <code>pip install -e ".[download]"</code>
    and make sure <code>ffmpeg</code> is on your PATH, then restart the server.
  </div>

  <div id="download-controls">
    <div class="dl-row dl-row-primary">
      <div class="dl-field">
        <label for="dl-query">URL or search</label>
        <input id="dl-query" type="text" autocomplete="off"
               placeholder="https://youtu.be/… or &quot;Artist – Title&quot;" />
      </div>
      <div class="dl-field dl-field-format">
        <label for="dl-format">Format</label>
        <select id="dl-format">
          <option value="mp3_320">MP3 320 kbps (default)</option>
          <option value="wav">WAV (uncompressed)</option>
          <option value="original">Original (AAC/Opus)</option>
        </select>
      </div>
      <button id="dl-go-btn" class="primary" type="button">Download</button>
    </div>

    <div class="dl-row dl-row-options">
      <label class="dl-toggle">
        <input type="checkbox" id="dl-normalize" /> Normalize loudness to -14 LUFS
      </label>
      <label class="dl-toggle">
        <input type="checkbox" id="dl-embed-meta" checked /> Auto-tag metadata
      </label>
    </div>

    <p class="dl-dest-row">
      <span>Saving to <code id="dl-dest"></code></span>
      <!-- PRESERVED: toggles between music_folder and default_dir; wired by _Download.bindManualPanel -->
      <button id="dl-dest-switch" type="button" class="link-btn" hidden></button>
    </p>

    <!-- Queue indicator — visible when ≥ 1 job is in flight; populated from /api/download/queue -->
    <p id="dl-queue-indicator" class="muted" hidden>
      <span id="dl-queue-text"></span>
      <button id="dl-queue-cancel-all" type="button" class="link-btn">Cancel queued</button>
    </p>

    <div id="dl-status-region"
         role="status" aria-live="polite" aria-atomic="true">
      <!-- _Download renders loading/success/error here -->
    </div>
  </div>

  <details class="dl-legal-disclosure">
    <summary>About content rights</summary>
    <p>Downloading copyrighted audio may violate YouTube's Terms of Service…</p>
  </details>
</section>
```

### 8.3 Confirm-modal restyle

```html
<div id="disc-v2-dl-confirm" role="dialog" aria-modal="true">
  <h3>Download album?</h3>
  <p id="disc-v2-dl-confirm-body">
    <strong id="disc-v2-dl-confirm-name">…</strong>
    <span id="disc-v2-dl-confirm-meta">(— tracks)</span>
  </p>
  <label class="dl-toggle">
    <input type="checkbox" id="disc-v2-dl-confirm-dontask" />
    Don't ask again this session
  </label>
  <div class="dl-confirm-actions">
    <button id="disc-v2-dl-confirm-cancel" class="secondary-btn">Cancel</button>
    <button id="disc-v2-dl-confirm-go" class="primary">Download album</button>
  </div>
</div>
```

(Cancel keeps default focus; primary disabled for 250 ms after open to defeat accidental Enter.)

**"Don't ask again" scope** (resolves grill M8 + round-2 m2):
- Per-tab (intentional safety): persisted in `sessionStorage.autocue_dl_skip_confirm = "1"`. Per-tab is documented as a feature, not a bug — opening AutoCue in a second tab restores the safety prompt.
- Permanent opt-out: a new **Download** settings block inserted into `#settings-section` (after the existing Cue Settings rows, before the close-section divider). New `<div class="settings-divider">Download</div>` heading, followed by `[ ] Always skip Shift-click download confirmation` writing `localStorage.autocue_dl_skip_confirm_persistent = "1"`. Reachable from the confirm modal via a tertiary "Always skip in Settings →" link.
- **Two-way mirror** (resolves round-3 m4): toggling the persistent Settings checkbox writes BOTH localStorage and sessionStorage to the same value. Checking persistent → sets sessionStorage to `"1"` (same-tab UX becomes consistent immediately). Unchecking persistent → clears both (user wanting the prompt back doesn't need to close every other tab).
- On Cmd+Z / undo of a download triggered via Shift+click: not applicable — Cmd+Z is not bound by AutoCue, and once a file is on disk we don't auto-delete it. Restore-from-backup-XML is the existing escape hatch for Rekordbox state, not for downloaded audio.

### 8.4 YouTube candidate modal

Replace hand-rolled SSE parser with `_consumeSSE`. Add focus trap (copy pattern from `disc-v2-dl-confirm`), Escape handler, focus return on close. Rename `Pick` → `Download this version`. Pre-flight: show MB size + codec per candidate.

### 8.5 CSS additions

```css
/* All inside existing <style> block */
.dl-row { display:flex; gap:8px; align-items:flex-end; flex-wrap:wrap; }
.dl-row-primary > .dl-field { flex: 1 1 240px; min-width: 0; }
.dl-row-primary > .dl-field-format { flex: 0 0 180px; }
.dl-row-options { gap:16px; margin-top:8px; }
.dl-toggle { display:inline-flex; gap:6px; align-items:center; font-size:13px; cursor:pointer; min-height:24px; min-width:24px; padding:2px 4px; }
#dl-format, #dl-go-btn, #dl-dest-switch { min-height:24px; min-width:24px; }
.dl-dest-row { display:flex; align-items:center; gap:8px; margin:8px 0; font-size:11px; color:var(--muted); }
.dl-confirm-actions { display:flex; gap:8px; justify-content:flex-end; }
.dl-status-card { padding:12px; border-radius:8px; border:1px solid var(--border); background:var(--surface2); }
.dl-status-card[data-state="error"] { border-color:#e0525233; }
.dl-status-card[data-state="success"] { border-color:#1f7a4933; }
.dl-progress-bar { /* role=progressbar */ height:6px; background:var(--surface); border-radius:3px; overflow:hidden; }
.dl-progress-fill { height:100%; background:var(--green); transition:width .2s; }

@media (max-width:640px) {
  .dl-row-primary { flex-direction:column; align-items:stretch; }
  .dl-row-primary > .dl-field,
  .dl-row-primary > .dl-field-format,
  .dl-row-primary > #dl-go-btn { width:100%; }
  #dl-go-btn { min-height:44px; }
  .dl-toggle { min-height:44px; }
  .dl-dest-row > #dl-dest { max-width:60vw; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; direction:rtl; }
  .dl-legal-disclosure { font-size:11px; }
}

@media (prefers-reduced-motion: reduce) {
  .dl-progress-fill { transition:none; }
}
```

### 8.6 Keyboard

- `Enter` in `#dl-query` submits
- `Esc` precedence (resolves grill M6 + round-2 M4): **(1) close topmost modal** first (`#yt-modal`, `#disc-v2-dl-confirm`), focus returns to invoker. **(2)** Otherwise: **the cancel-Esc binding only fires when `document.activeElement` is inside `#download-section` OR is `<body>` (no input focus)** — single Esc in `#dl-query` retains its native "blur / clear" behavior and never triggers cancel. **(3)** When the binding fires AND a manual-panel download is in flight, Esc shows toast "Press Esc again to cancel download" — second Esc within 1.5 s calls `job.cancel()`. Per-card downloads have no Esc binding; users click the per-card `[Cancel]` button. Inside `#yt-modal`, the explicit `[Cancel]` button is the only path to cancel — closing the modal auto-cancels because the surface goes away.
- New shortcut `Shift+D` (advertised in `#kbd-overlay`): focus `#dl-query` from anywhere

---

## 9. Accessibility (WCAG 2.2 AA)

Every audit-flagged violation fixed:

| WCAG SC | Fix |
|---|---|
| 1.3.1 Info & Relationships | `for=`/`id=` on all label-input pairs; `<fieldset><legend>Audio options</legend>…</fieldset>` around format + toggles |
| 1.4.3 Contrast (Min) | Audit text colors; bump `var(--muted)` against `var(--surface2)` to ≥ 4.5:1; ensure primary button text ≥ 4.5:1 |
| 1.4.11 Non-text Contrast | Primary button border + focus ring ≥ 3:1; progress bar fill vs track ≥ 3:1 |
| 2.1.1 / 2.1.2 Keyboard / No trap | Focus trap with cyclic Tab in modals; Escape closes |
| 2.4.3 Focus Order | Initial focus = first input in each modal; close returns focus to invoker |
| 2.4.7 Focus Visible | Inherits existing `:focus-visible` outline rules; verified per element |
| 2.5.5 / 2.5.8 Target Size | **Every interactive control** (incl. desktop `<select id="dl-format">`, every `<input type=checkbox>` wrapped in `.dl-toggle`, link-buttons) gets BOTH `min-width: 24px; min-height: 24px;` (AA, applied via a `.dl-target` utility class). Mobile bumps to 44 × 44 (AAA) via the existing `@media (max-width:640px)` block. Native `<input type=checkbox>` is small (~13 px) — the click target is the wrapping `<label>` which carries the dimensions; tested via Playwright `getBoundingClientRect()` assertion against every download-related interactive control. |
| 3.2.4 Consistent Identification | Confirm modal primary green = Download; ghost = Cancel — matches app convention |
| 3.3.1 / 3.3.3 Error ID & Suggestion | Classified error message + actionable hint shown |
| 4.1.2 Name, Role, Value | `<select>` + native `<input type=checkbox>` + native `<progress>` everywhere |
| 4.1.3 Status Messages | `aria-live=polite` on status; `aria-live=assertive` + `role=alert` on error; `aria-busy=true` during loading |
| 2.3.3 Animation from interactions | `@media (prefers-reduced-motion)` disables progress transition |

Verification: axe-core scan against `/discover` view; manual VoiceOver pass through one full download cycle.

---

## 10. Out of Scope (Tier 2+)

These came up in the audit but are deferred:

- **Recently-downloaded persistent list** (§6.11) — promoted to Tier 2 unless trivially cheap
- **YT-ID dedupe manifest** — Tier 2
- **showDirectoryPicker File System API folder picker** — Tier 2 (Safari support gap)
- **Bulk paste / queue panel** for many URLs at once — Tier 2
- **System Notifications API** desktop ping on finish — Tier 2
- **Pre-flight MB / codec on YT candidates** — Tier 2 (extra info_dict round-trip)
- **Custom artist/title override before tagging** — Tier 2
- **Re-download with different format from history** — Tier 2
- **Mobile floating download FAB** — Tier 2

---

## 11. Constraints, Risks, Assumptions

### Constraints

- **Vanilla JS only** (CLAUDE.md). No framework. `_Download` is an IIFE.
- **Single HTML file**. All CSS goes in existing `<style>`. All JS in existing `<script>`.
- **Backend single-writer rule** for `master.db` not relevant here (Download never writes the Rekordbox DB), but per-job ffmpeg shouldn't fanout — sequential per-track preserves resource predictability.
- **Local-mode only**. The download surface continues to no-op on Pages-mode hosting (already handled).
- **yt-dlp + ffmpeg are optional deps**. Banner remains; new toggles disabled when ffmpeg missing.

### Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| yt-dlp version skew breaks `MetadataParser` API | Medium | Pin minimum version in `pyproject.toml` extras; wrap in try/except + log; ID3 toggle silently degrades to "metadata may not be embedded" |
| Two-pass loudnorm doubles wall-clock for short clips | High | Document expected 2× time; show "Normalizing…" phase explicitly; skip for `original` format with tooltip |
| WAV from lossy source surprises users ("wait, it's still lossy") | High | Format dropdown tooltip: "Re-encoded losslessly from the YouTube source (which is lossy). Use 'Original' to keep the source container." |
| Playlist expansion fails for some URL shapes | Medium | URL classifier returns `mixed_video_in_playlist` and asks user; fallback is single-video download |
| `subprocess.run(['open','-R'])` is macOS-only | Certain | Reveal endpoint detects platform; returns 501 elsewhere; button hidden when unsupported |
| Refactor introduces a regression in the existing per-card download flow | Medium | Vitest covers `_Download.bindCardButton` state transitions; Playwright covers `.disc-dl-btn` end-to-end |
| Default-flip from MP3 192 → MP3 320 changes downstream file sizes (~1.7×) | Low | One-time migration toast on first session after upgrade: "Default format is now MP3 320 kbps. Change in the Format dropdown." Round-6 F3 cleanup. |

### Assumptions

- yt-dlp ≥ 2024.x.x is installed (covers MetadataParser semantics)
- ffmpeg ≥ 4.x is on PATH (covers loudnorm filter)
- **Browser baseline** (resolves round-4 M2): Safari ≥ 15.4 (Mar 2022), Firefox ≥ 121 (Dec 2023), Chromium ≥ 105. This covers CSS `:has()` used in §6.9's bottom-stack model. Older browsers fall back to in-flow placement of `#download-section` (the mobile sticky strip simply doesn't engage; the panel renders at the bottom of the Discover scroll area, identical to the v0.x behavior). The fallback is one `@supports selector(:has(*)) { … }` guard. **§4 Mobile reachability metric is scoped to the supported baseline** (resolves round-5 Min-1) — Firefox 120 users see degraded layout, documented in `docs/reference/youtube-download.md#browser-support`.
- **Release version pinning + process** (resolves round-4 M3 + round-5 Maj-2): this PRD lands in AutoCue **v0.2.0** (`pyproject.toml` bumped from `0.1.0`). First commit also creates `/CHANGELOG.md` (currently absent) with the `0.2.0` entry. Deprecated alias endpoints (`POST /api/download`, `POST /api/download/album`) and the legacy-format coercion table (§6.2) are **removed in v0.3.0**. A pre-emptive `[BREAKING — planned for 0.3.0]` entry lands in the same CHANGELOG immediately, so the deprecation is tracked from day 1, not at the moment 0.3.0 ships. tasks.json includes the CHANGELOG creation as task DL-001 (root of dependency chain).

---

## 12. Acceptance Criteria

The PRD ships when **all** of these pass (each criterion has a paired test or grep so verification is deterministic):

| # | Criterion | Verification |
|---|---|---|
| 1 | Every §2 pain row closes | One named test per row asserts the specific behavior is fixed. Tracked in §4. |
| 2 | `_Download` IIFE is the only download driver | `grep -c "function runDownload\|function downloadManual\|function _ytDownload" docs/index.html == 0` |
| 3 | No jargon in user-visible copy | `tests/test_download_jargon.py` greps DOM-rendered + SSE event strings; whitelist = install banner only |
| 4 | XML buttons renamed (verb "Download" = audio only) | grep: `#download-btn` text == "Export XML"; `#backup-btn` text == "💾 Save backup XML" |
| 5 | Confirm modal: primary green = "Download album"; default focus = Cancel; 250 ms primary delay | Playwright snapshot + key-injection assertion |
| 6 | Format dropdown (MP3 320 default), normalize toggle (off default), metadata toggle (on default) present; persist to localStorage | Vitest: assert defaults, mutate, reload, assert restore |
| 7 | Playlist URL → N-track download with X-of-Y progress | Pytest fixture playlist + assertion total ≥ 2 in `processed/total` events |
| 8 | Mid-download Cancel works on every surface; `cancel_event` fires backend-side | Playwright drives Cancel; backend log asserts `DownloadCancelled` raised |
| 9 | Classified errors with [Retry] for ≥ 8 codes; raw stderr behind `<details>` | `tests/fixtures/download_errors/*.txt` parametrize `classify_download_error`; vitest renders [Retry] |
| 10 | Success state persistent (no auto-hide); [Reveal in Finder] [Copy path] [Download another] actions | Vitest: render success state, advance fake timers 30 s, assert still visible |
| 11 | Mobile (390 × 844): collapsed `#download-section` sticky strip is visible without scroll from Discover top; expands to full panel on tap | Playwright + screenshot diff |
| 12 | axe-core scan: 0 violations on Download surfaces (idle, loading, success, error) | `npx @axe-core/cli` in CI; manual VoiceOver pass logged in PR |
| 13 | Concurrent-download starvation guard | Integration test: 20 simultaneous `_Download.start()` calls → 1 active, 19 queued |
| 14 | Test gates green | Pytest `tests/` ≥ 1109 + new tests; vitest ≥ 435 + new tests |
| 15 | Chrome DevTools live verification | Screenshots of every state on desktop + mobile sent to user via `SendUserFile` per CLAUDE.md |

**Not a merge gate**: audit re-score ≥ 7.5/10 (run once post-ship; failure prompts Tier 2 follow-up, not revert).

---

## 13. Decisions Closed in Round 1

These were open in v0.1 but are now committed:

| Question | Decision | Rationale |
|---|---|---|
| Default format | **MP3 320** | Resolves grill M5. Note: current backend default is MP3 192 (`download.py:103`); the bump to 320 is a third silent default-change vs v0.x. Mitigated by the one-time toast in §6.2 first-paint behavior, which now also fires when previous-version users land on a different audio_quality. **Accept the ~1.7× disk-size bump** because user explicitly asked for "MP3 (320kbps)" in the original prompt. |
| Loudnorm default | **off** | Changing audio characteristics is surprising; opt-in is safer |
| Confirm modal primary | **"Download album" is brand-green primary** + Cancel default focus + 250 ms primary delay | Belt-and-suspenders without inverting color semantics |
| Reveal in Finder | **In scope** | Significant UX win; cheap; macOS-only acceptable with platform-detect hiding |
| Recent downloads list | **Out of scope** (Tier 2) | Resolves grill m1; not part of the user's 5 asks |
| Playlist support | **In scope** for v1 | The user explicitly asked; backend gate (`noplaylist`) is a 2-line change |
| "Download XML" rename | **Rename to "Export XML"** | Necessary to reserve the verb "Download" for audio; one-line release-notes mention |

Round 2+ findings are tracked in `.agent/prd/DOWNLOAD_PRD_iteration-log.md`.
