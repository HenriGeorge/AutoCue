# DOWNLOAD_PRD Iteration Log

Tracks each `/grill-me` round and the diffs applied. Source PRD: `DOWNLOAD_PRD.md`.

---

## Round 1 (2026-06-08) — v0.1 → v0.2

Grilled by general-purpose agent against `audit-synthesis.json` + source code.

### Resolved

| Finding | Rank | Resolution |
|---|---|---|
| B1: `audio_format` Literal breaks existing clients (e.g. `"mp3"` → 422) | Blocker | Backend coerces legacy `"mp3"` → `"mp3_320"` for one release; unknown strings → 422 with helpful error. §6.2. |
| B2: 20 simultaneous downloads = melt; no concurrency cap | Blocker | New §6.12 spec: single FIFO `DownloadQueue` with concurrency cap (`AUTOCUE_DOWNLOAD_CONCURRENCY` default 1, max 4); queued state visible to user. |
| B3: Acceptance #1 (audit re-score ≥ 7.5) unverifiable per-PR | Blocker | Replaced with §2-pain-row checklist + paired tests. Audit re-score becomes post-ship one-shot, not merge gate. §4 + §12. |
| M1: WCAG target-size missed desktop `<select>` and underspecified width | Major | §9 row updated to call out all controls including desktop select; CSS sets `min-width: 24px;` on `.dl-toggle`, `#dl-format`, `#dl-go-btn`, `#dl-dest-switch`. Playwright assertion added to §12. |
| M2: Format=original + Normalize=on backend behavior undefined | Major | §6.4 adds explicit Format × Normalize matrix; backend returns 422 `normalize_unsupported_for_original` for stale clients. Frontend disables toggle when `original` selected. |
| M3: Cancel mid-loudnorm has no defined behavior | Major | §6.4: backend keeps `subprocess.Popen` handle, watcher thread calls `proc.terminate()` then `proc.kill()` on `cancel_event`. Pass-2 progress parsed from ffmpeg `-progress pipe:1`. Pass-1 emits indeterminate `phase: "normalizing"`. |
| M4: Partial-file cleanup missing | Major | §6.4 adds `output_artifacts: set[Path]` tracking + `finally:` cleanup of non-success-path files. Belt-and-suspenders glob sweep. Documented in `docs/reference/youtube-download.md`. |
| M5: Default-WAV-from-lossy is a UX trap | Major | Default flipped to **MP3 320**. WAV requires inline one-time explainer. §6.2 + §13. |
| M6: Esc key precedence (cancel vs close) undefined | Major | §8.6: Esc closes topmost modal first; on manual panel needs double-Esc within 1.5 s to cancel an in-flight download. Inside `#yt-modal`, modal close auto-cancels because surface goes away. |
| M7: `classify_download_error` regexes unverified against yt-dlp | Major | §6.7 commits to fixture-driven taxonomy: `tests/fixtures/download_errors/<code>.txt`. Parametrized pytest. Adding a code = adding a fixture. |
| M8: "Don't ask again" sessionStorage scope contradictory | Major | §8.3 spec: per-tab `sessionStorage.autocue_dl_skip_confirm` (intentional), plus persistent Settings checkbox `localStorage.autocue_dl_skip_confirm_persistent`. Cmd+Z section clarified (not applicable). |
| M9: Reveal-in-Finder command-injection surface | Major | §6.10: `Path.resolve(strict=True)` + allow-list check against `default_download_dir()` and in-process `known_user_dests`; reject with 403 otherwise. |
| m1: Recent downloads scope flip-flop | Minor | Deferred to Tier 2; removed from §6, kept in §10. |
| m2: localStorage first-paint unspecified | Minor | §6.2 specs absent-key behavior (silent default to `mp3_320`); legacy values coerce with toast. |
| m3: "Time-to-first-download < 15s" unmeasurable | Minor | Dropped from §4 metrics table. |
| m4: WAV + ID3 contradiction | Minor | §6.5 reframed: "Auto-tag metadata" (no "ID3"); per-format table; toggle stays enabled for WAV via RIFF INFO. |

### Open after round 1

None of the round-1 findings are unresolved.

### Next round focus

Round 2 should probe:
- The §6.12 queue spec for race conditions (job cancellation while in-flight, server shutdown drain semantics)
- Whether the §6.7 fixture-driven approach actually scales — what happens when yt-dlp upgrades and ALL fixtures stop matching?
- §8.6 Esc-twice timing — is 1.5 s the right window?
- Any new contradictions introduced by the round-1 edits

---

## Round 2 (2026-06-08) — v0.2 → v0.3

### Resolved

| Finding | Rank | Resolution |
|---|---|---|
| Round-2 B1: `_consumeSSE` cancel doesn't reach the server reliably (HTTP/2 buffering, ffmpeg outside yt-dlp hook) | Blocker | Added explicit `POST /api/download/cancel/{job_id}` endpoint in §6.12 + §7.3. Decoupled from SSE lifecycle. First SSE event of every download now includes `job_id`. |
| Round-2 B2: Queue cancel-during-pickup race | Blocker | §6.12 specifies `queue_lock`, `cancel_pending: set[job_id]`, ordered ops. Worker's first action under the lock is `cancel_pending` check. |
| Round-2 M1: `output_artifacts` glob sweep is FS-unsafe under concurrency > 1 | Major | §6.4 dropped the glob sweep. Tracking is purely set-based, populated at creation time via hooks. |
| Round-2 M2: Cleanup nukes pre-existing same-named files | Major | §6.4 added `mtime >= job.started_at` gate. Pre-existing files never deleted. |
| Round-2 M3: Format coercion EOL unspecified | Major | §6.2 table now covers `mp3`, `m4a`, `aac`, `opus`, `flac`, `alac`, `vorbis`, `wav` with explicit map. Coercion removed in next minor release. Logged at WARNING. |
| Round-2 M4: Esc binding swallows input-Esc | Major | §8.6: cancel-Esc only fires when `activeElement` is inside `#download-section` or is `<body>`. Single Esc in `#dl-query` keeps native blur/clear. |
| Round-2 M5: yt-dlp fixture upgrade workflow | Major | §6.7 adds quarterly live test (`RUN_LIVE_YTDLP=1`), upgrade checklist in `docs/reference/youtube-download.md`, prod logging of `error_code="unknown"` for new-pattern discovery. |
| Round-2 M6: `os_reveal_supported` uses `sys.platform` only | Major | §6.10 specs `shutil.which()` per-platform check. Endpoint has 501 second-line defense. |
| Round-2 m1: Mobile breakpoint inconsistency | Minor | §6.9 notes the 640 px choice matches existing `@media` block; iPad portrait acceptable in desktop layout. |
| Round-2 m2: Settings checkbox insertion point | Minor | §8.3 specs new "Download" settings divider in `#settings-section`; unchecking persistent toggle also clears sessionStorage. |
| Round-2 m3: "1 viewport scroll" gate unverifiable | Minor | §6.9 + §12 row 11: `#download-section` becomes sticky-collapsed strip on mobile, expandable on tap. New explicit metric. |
| Round-2 m4: 192→320 third silent default change | Minor | §13 row updated to acknowledge; toast in §6.2 now also fires when migrating from old `audio_quality`. |

### Open after round 2

None.

### Next round focus (round 3)

Round 3 should probe:
- The new `POST /api/download/cancel/{job_id}` endpoint — race conditions when client cancel arrives between `_event_stream` first yield (containing job_id) and the worker actually starting? Does the queue need to expose `enqueue → job_id` as a separate sync call?
- Watchdog 5-min idle limit — too aggressive for legit long downloads (10-hour DJ-set videos do exist)?
- Sticky mobile bar (§6.9): how does it interact with the existing sticky bottom action bar (`#action-bar`)? Both would compete for bottom screen real estate. The bottom-bar has explicit `--ab-rest-y` math at index.html:906 — does the mobile sticky download strip break that?
- Coerce-mp3→mp3_320 logging at WARNING — does it actually appear in any user's log workflow? Risk of log spam?

---

## Round 3 (2026-06-08) — v0.3 → v0.4

### Resolved

| Finding | Rank | Resolution |
|---|---|---|
| Round-3 B1: Three-element bottom-stack collision (`#download-section` strip × `#download-bar` × `#action-bar`) | Blocker | §6.9 specifies CSS-var-driven stacking (`--ds-strip-h`, `--db-h`, `--ab-h`); existing `--ab-rest-y` math replaced. Fallback: strip hides when both other bottom-fixed elements are visible. |
| Round-3 B2: `cancel/{job_id}` unreachable before first SSE event | Blocker | §7.3 splits into `POST /api/download/enqueue` (sync, returns `job_id`) + `GET /api/download/stream/{job_id}` (SSE). `POST /api/download` kept as one-shot back-compat alias for one release. |
| Round-3 M1: Watchdog 5-min cap kills legit long downloads | Major | §6.12 replaces single 5-min cap with tiered model: subprocess-pulse watchdog (60 s, proves liveness even when silent) + 30-min stuck-phase cap. 10-hour DJ-set ffmpeg loudnorm now legitimate. |
| Round-3 M2: §8.2 markup regresses `#download-unavailable` | Major | §8.2 now preserves `#download-unavailable` div with explicit note. Also preserves `#dl-dest-switch` (was already there but unhighlighted). |
| Round-3 M3: `audio_quality` silently dropped from wire | Major | §7.2 uses `model_config = ConfigDict(extra='forbid')`; §6.2 specifies middleware that rewrites the 422 into a friendly `audio_quality_removed` error. |
| Round-3 M4: SSE heartbeat format unspecified | Major | §6.12 specifies SSE keepalive = comment line `: keepalive\n\n` (filtered by `_consumeSSE`'s existing `data:` check, verified at line 4567); internal worker pulse is in-process only, never on wire. |
| Round-3 m1: `max_concurrency` field had no consumer | Minor | §6.12 wires it to `#dl-queue-indicator` text "1 active · N queued (max C concurrent)". |
| Round-3 m2: WARNING log on every legacy `"mp3"` request spams | Minor | §6.2 specifies log-once-per-(process, legacy_value) at WARNING; subsequent drops to DEBUG. |
| Round-3 m3: Live-YT-fixture URLs undocumented + can rot | Minor | §6.7 documents canonical URLs in `docs/reference/youtube-download.md`; adds meta-test asserting each URL still produces a distinct error_code. |
| Round-3 m4: Settings persistent-checkbox bidirectional clear was one-sided | Minor | §8.3 specs two-way mirror — checking and unchecking both write/clear sessionStorage too. |

### Open after round 3

None.

### Next round focus (round 4 done)

Round 4 should probe:
- The new `POST /api/download/enqueue` + `GET /api/download/stream/{job_id}` split — what about back-compat for `_consumeSSE` callers that expect a `POST → SSE` shape? Does `_consumeSSE` accept `GET` responses today? Verify against `docs/index.html`. If not, every caller change is more than thought.
- 60-second cached final status TTL on `GET /api/download/stream/{job_id}`: what if a slow client polls every 30 s? The cached "done" event might be consumed twice (UI shows two toasts).
- Sticky-strip CSS `:has()` selector support — Safari 15.4+ only. Does AutoCue's browser baseline include older Safari?
- Migration from `POST /api/download` → `POST /api/download/enqueue + GET /stream/{id}`: spec says "back-compat for one release." When is it removed? The release-process gap from round 2 M3 reappears.

---

## Round 4 (2026-06-08) — v0.4 → v0.5

### Resolved

| Finding | Rank | Resolution |
|---|---|---|
| Round-4 B1: `phase: "starting"` not in §7.2 enum | Blocker | §7.3 enqueue response now always returns `phase: "queued"`. |
| Round-4 B2: Cached `done` event double-emit | Blocker | §7.3: server marks cache `consumed=True` on first stream open; subsequent opens return `410 Gone` with `already_consumed` + path. Frontend also tracks `seen_done_for_job: Set` as defense in depth. |
| Round-4 M1: `_consumeSSE` POST/GET shape unspecified for split | Major | §6.1 adds concrete fetch sketch: POST enqueue → JSON → job_id → GET stream → `_consumeSSE`. Notes `_consumeSSE` is verb-agnostic at line 4553. |
| Round-4 M2: CSS `:has()` baseline unspecified | Major | §11 Assumptions: Safari ≥ 15.4, Firefox ≥ 121, Chromium ≥ 105. `@supports selector(:has(*))` guard wraps the bottom-stack model; fallback is in-flow placement. |
| Round-4 M3: Deprecation version unpinned | Major | §11: PRD lands in v0.2.0; deprecated aliases + legacy format coercion removed in **v0.3.0** with `[BREAKING]` release-notes entry. |
| Round-4 M4: `known_user_dests` empties on restart → Reveal day-2 bug | Major | §6.10 dropped in-process set; allow-list is now stable roots (`default_download_dir`, `_detect_music_folder`, `AUTOCUE_DOWNLOAD_DIR`). Survives restart. |
| Round-4 m1: Queue poll wastes cycles in background tabs | Minor | §6.12: `visibilitychange` listener pauses/resumes the 2 s poll. |
| Round-4 m2: 422 `normalize_unsupported_for_original` dead-ends stale clients | Minor | §6.4: frontend auto-flips to `mp3_320` on that error, toasts user, retries enqueue, persists flip. |
| Round-4 m3: "Download another" form-reset scope undefined | Minor | §6.6: clears URL only, refocuses input, retains sticky user prefs. |

### Open after round 4

None.

### Next round focus (round 5 done)

Round 5 final pass. Probed:
- The new `410 already_consumed` response — what does the frontend show? It carries `path` but no error_message. Spec the UI: re-render success card from cached path, no toast?
- The `@supports selector(:has(*))` guard fallback — what does the mobile layout look like on Firefox 120? Is it usable, or is the panel unreachable?
- v0.3.0 removal in release-notes — is there even a release process? Where does this entry live?
- The auto-retry on `normalize_unsupported_for_original` — what if it fails AGAIN for unrelated reasons? Infinite retry? Single retry cap?
- Any spec-vs-spec contradictions introduced by round-4 edits (especially the dropped in-memory `known_user_dests` — is it referenced anywhere else?)

---

## Round 5 (2026-06-08) — v0.5 → v0.6

Reviewer verdict: **PRD IMPLEMENTABLE — proceed to tasks.json.** Remaining findings folded in directly.

### Resolved

| Finding | Rank | Resolution |
|---|---|---|
| Round-5 Maj-1: `410 already_consumed` frontend behavior unspecified | Major | §7.3 fetch handler explicitly checks `stream.status === 410` BEFORE generic error throw; renders success/error/idle from cached payload, no toast. Covers Min-3 cross-tab as side effect. |
| Round-5 Maj-2: v0.3.0 deprecation release-process gap | Major | §11 commits to creating `/CHANGELOG.md` as DL-001 in tasks.json; bumps `pyproject.toml` to `0.2.0`; pre-emptive `[BREAKING — planned for 0.3.0]` entry lands day 1. |
| Round-5 Min-1: Mobile reachability metric vs `:has()` fallback gap | Minor | §11 + §4: metric scoped to supported baseline; Firefox 120 sees degraded layout, documented. |
| Round-5 Min-2: normalize-flip auto-retry unbounded | Minor | §6.4: per-job `_retriedNormalizeFlip` flag — single retry only, second failure surfaces normally. |
| Round-5 Min-3: Two-tab `seen_done_for_job` cross-tab semantics | Minor | Subsumed by Maj-1 fix — both tabs render success-from-cache without competing toasts. |

### Open after round 5

None.

### Verdict

PRD locked at v0.6. Proceed to `.agent/tasks.json`, then implement. Round 6 would produce diminishing returns; we'll run a brief acceptance probe to confirm the lock is genuine but do not expect material new findings.

---

## Round 6 (2026-06-08) — v0.6 → v1.0 (LOCKED)

Final acceptance pass. Reviewer verdict: **PRD LOCKED — proceed to tasks.json.**

### Resolved (typo-class, no architectural change)

| Finding | Rank | Resolution |
|---|---|---|
| Round-6 F1: `DownloadAlbumRequest.audio_format` default = `"wav"` contradicts `DownloadRequest` = `"mp3_320"` | Major | Both default to `"mp3_320"`; Executive Summary line corrected. |
| Round-6 F2: `download_audio` signature still showed `audio_format="wav"` + `audio_quality="320"` | Major | Signature aligned: `audio_format="mp3_320"`, `audio_quality` parameter removed, inline note documents the migration. |
| Round-6 F3: §11 Risks row mentioned WAV-default flip (stale) | Minor | Row text updated to MP3 192 → MP3 320 + corrected toast. |
| Round-6 F4: No further contradictions | (verified clean) | known_user_dests / confirm modal / :has() baseline all internally consistent. |

### Total rounds: 6 of 10 allowed

The PRD went from v0.1 → v1.0 across 6 grill rounds (3 architectural rounds + 2 detail rounds + 1 final acceptance). 24 findings closed: 5 blockers, 14 majors, 5 minors. Last round produced only typo-class corrections — diminishing returns clearly reached.

### Lock state

DOWNLOAD_PRD.md is **v1.0** and ready for tasks.json generation + implementation. Proceeding.
