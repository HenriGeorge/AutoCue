# Auditor — P5 adversarial review · fix/design-workbench

Scope: `git diff main..HEAD` (c1756f9 unit B token-provenance+dupes-toolbar, 741cddb unit A
inspector anchor card) + uncommitted e2e specs. Lens: /code-review + /security-review +
silent-failure-hunter. Reviewed the DIFF, not whole files. **Only 80+ confidence findings reported.**

## Verdict: **PASS** — no findings ≥80. Cleared for merge.

I tried hard to break the five flagged surfaces + the usual destructive/concurrency/malformed-input
vectors. Every one is correctly guarded. Details of what I attacked and why it holds:

### (1) Stale-fetch guard in `_renderTransitionIn` — `inspector.js:282-290` — SOUND
`const stale = () => token !== _txToken || _focusedId !== String(focusedId) || _mode !== 'track'`.
- **Out-of-order / paint-over:** `_txToken` is monotonic (`++_txToken` per render), so an older
  response is discarded even if it resolves after a newer one paints. Verified for A→B, B→A-again,
  and same-track re-focus (token catches it when `_focusedId` cannot).
- **After clearInspector:** `clearInspector()` (`:394`) sets `_focusedId=null` AND wipes
  `body.innerHTML=''` (`:402`). A late response → `_focusedId(null) !== "X"` → stale → early return;
  the `sec` node is already detached by the innerHTML wipe. No paint after close.
- **Release re-host mid-flight:** `_mode!=='track'` branch of the guard catches it; release renderer
  also clears the body. No cross-mode bleed.
- No `body.innerHTML=''` duplicate-section risk: `renderInspector` wipes the body each call (`:98`),
  so exactly one card exists.

### (2) Silent `!r.ok` / network-error path — `inspector.js:286-318` — SOUND (no lingering header)
- `!r.ok` → `r.json()` short-circuited to `null` → `if (!data){ sec.remove(); return; }` — the
  placeholder section (header + "…") is REMOVED, not left half-rendered.
- Network error / malformed-JSON reject → `.catch(() => { if (!stale()) sec.remove(); })` — same clean
  removal. Silent-on-failure is intentional (advisory card, DESIGN line 70) and complete — no empty
  "Transition in" header survives either failure path.
- Degenerate success (`data.overall` undefined/null): `_anchorBand(NaN)→'weak'`,
  `Math.round(…||0)→0` → renders "0" weak, no reasons. Non-crashing; sub-80 cosmetic only.

### (3) B2 `#wb-dupes-bulk-delete` padding `4px 14px → 4px 12px` — `app.css:3326` — NO OTHER CONSUMER SHIFTS
- The ID rule's old 14px was **dead**: the removed inline `padding:4px 12px` (1-0-0 inline beats the
  1-0-0 ID rule) rendered 12px in *every* state, including `:hover`/`:disabled` (inline persists across
  pseudo-classes). Changing the ID rule to 12px keeps the rendered value byte-identical.
- Only two other `#wb-dupes-bulk-delete` rules exist (`:hover:not(:disabled)` `:3331`, `:disabled`
  `:3334`) and **neither sets padding** — nothing else consumed the 14px.
- `#wb-dupes-rescan`: `.wb-toolbar-sm` (`:3322`, 0-1-0) beats `.secondary-btn` base `padding:8px 16px`
  (`:1366`, 0-1-0) purely by **source order** (3322 > 1366) → resolves to 4px 12px / 12px. Confirmed by
  the verifier's live computed values. No regression.

### (4) `ACBridge.nowPlayingId()` accessor — `08-set-builder-boot.js:1064` — READ-ONLY, NO LEAK
- Arrow closure returning the `nowPlayingId` primitive; mirrors the sibling read-only pass-throughs
  (`tracks`/`selectedIds`/`activePlaylistId`). No setter, no reference handed out, no mutation surface.
- Consumer (`inspector.js:251`) guards `typeof bridge.nowPlayingId === 'function'` and `!= null`, so a
  null now-playing (nothing playing) cleanly falls through to the `prevFocusedId` fallback. `0` or
  non-numeric → `parseInt` → NaN → JSON `null` → backend 422 → `!r.ok` → silent. No crash path.

### (5) Interop / no-build / TASK-033-037 — CLEAN
- v2 reaches the classic `nowPlayingId` `let` ONLY through `window.ACBridge` (the lone legacy edit);
  no new `import` of legacy from v2. Classic-globals-via-`window.*` invariant intact.
- Inspector-only change; `#track-list` never detached, sticky/virtualizer untouched (TASK-033/037).
- `_mode='track'` added at `:92` is defensive-correct: `renderInspector` IS the track renderer, and the
  grid click handler already `return`s in release mode (`:429`), so no consumer relied on the old
  non-reset behaviour. Mirrors `renderReleaseInspector` setting `_mode='release'`.

### Security (XSS / injection) — CLEAN
- All untrusted strings (anchor label, `data.explanation[]`, BPM/key meta) written via `textContent`;
  `reasons.innerHTML=''` is a clear, not a sink. Track ids `parseInt(_,10)` before the POST body — no
  injection. No new endpoint, no new auth surface.

### B1 token provenance — VERIFIED byte-equal
- `colors.css:49-52/105-108` == `app.css:3528-3531/3536-3539` both themes (incl. dark peak `.06` edge).
  `--warn-amber`/`--green`/`--muted` all resolve (no dangling `var()`). app.css runtime values unchanged.

## Sub-80 observations (NOT findings — recorded for transparency, no action required)
- No `AbortController`: rapid arrow-through focus fires N orphaned POSTs to `/api/transitions/score`
  (all token-discarded; DESIGN line 71 accepts token-guard over abort). Conf ~55.
- Missing `data.overall` renders a silent "0" weak card rather than hiding. Cosmetic. Conf ~50.

STATUS: DONE
