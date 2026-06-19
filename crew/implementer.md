# Implementer result

## Review Dock build (branch `feat/review-dock`)

Built the 3 parts from `crew/DESIGN.md` (REVIEW DOCK, approved 2026-06-19) with TDD,
committing per part. New branch `feat/review-dock` was cut FROM the current worktree HEAD
(NOT main) so the committed `crew/` artifacts persist and the diff stays OFF the open #245 PR.
I own all source edits. Dev server NOT started (verifier owns live :3003).

### PART 1 — API `268e474` feat(serve): dev-only POST /api/review-note
- `schemas.ReviewNote {page: str = "", note: str}` — `field_validator` rejects a blank/
  whitespace note (→422, no empty log lines).
- `routes.review_note`: **403** unless `os.environ.get("AUTOCUE_REVIEW_DOCK") == "1"`
  (mirrors the `/api/perf/recent` env-gate precedent at routes.py:~4691; 403 per spec, not 404).
  Appends `"[%Y-%m-%d %H:%M:%S] [<page>] <note>\n"` to `Path.cwd()/crew/REVIEW-NOTES.md`
  (mkdir parents, create if missing). Sanitises: collapses ALL whitespace runs (incl. injected
  newlines) to one line; `page` stripped + capped `[:64]`, default `"unknown"`. Returns `{"ok": True}`.
  No auth/DB/Rekordbox surface.
- TDD: `tests/test_review_note.py` (11) — 403 env unset / !="1", append format + `{"ok":true}`,
  crew-dir creation, multi-line append, 422 empty/missing, newline-injection→one line, page
  truncation, unknown default.

### PARTS 2+3 — UI + STYLE `6331a8c` feat(web): dev-only review dock feedback bar
- `docs/js/v2/review-dock.js` (new): renders ONLY when `ACBridge.isLocalMode()` AND
  `localStorage.ac_review_dock==='1'` (client gate; server env-gate is the 2nd independent guard;
  no-ops on Pages/XML). `<form.review-dock>` = prompt glyph (aria-hidden) + sr-only `<label>` +
  mono page badge + input (placeholder "describe a change for this page…") + ink-pill Send +
  aria-live status. `_derivePage()`: `nb-active`→nightboard / `wb-place-dupes|discover|library`→
  those / else `ACBridge.crate()||'cues'` (recomputed at submit). Submit: `POST {page,note}`,
  **r.ok-checked** → clear + "✓ sent" (~2s aria-live); `!ok`→`window.showToast` + input retained;
  double-submit guard (disable in flight); idempotent.
- `docs/js/v2/main.js`: `import { initReviewDock }` + `initReviewDock()` at the END (places exist first).
- `docs/css/app.css`: `.review-dock*` block — glass chrome (`--glass-bg` + backdrop blur, top border,
  soft shadow) like the sticky header; `--radius-md` input; `--green-ring` focus; Send = ink pill
  (`--ink`/`--on-ink`/`--radius-pill`, NEVER green); "✓ sent" in `--green`; both themes; motion
  PRM-gated; adds the missing `.sr-only` utility; z-index 140 per design. Tokens only, no hex.
  Ships to Pages but inert (dock never renders there).
- `index.html` markup UNCHANGED (dock is JS-injected) — dev-only invariant held.
- TDD: `tests/web/v2-review-dock.test.js` (14) — render-gate off by default / not "1" / non-local;
  renders + a11y when gated on; idempotent; all `_derivePage` cases; submit posts+clears+"✓ sent";
  empty no-op; `!ok`→showToast + input retained.

### Fresh evidence (run THIS turn)
- `python -m pytest -q` → **1488 passed, 7 skipped** (1477 + 11 new review-note). exit 0.
- `npm test` → **63 files, 946 passed** (incl. +14 review-dock). exit 0.
- `node --check` on `review-dock.js` + `main.js` → syntax OK. (No lint/typecheck script — no-build repo.)

### Handoff to verifier (LIVE @127.0.0.1:3003 — I did NOT drive it)
- With `AUTOCUE_REVIEW_DOCK=1` + `localStorage.ac_review_dock='1'`:
  `curl -X POST .../api/review-note -d '{"page":"test","note":"hello"}'` → `{"ok":true}` AND a line
  in `crew/REVIEW-NOTES.md`. Dock renders pinned at bottom; type + Send → "✓ sent". Screenshot light(+dark).
- Prod/disabled proof: `AUTOCUE_REVIEW_DOCK` UNSET → same curl 403; localStorage flag unset →
  `querySelector('.review-dock')` is null.

### Commits
- `268e474` feat(serve): dev-only POST /api/review-note
- `6331a8c` feat(web): dev-only review dock feedback bar

STATUS: DONE

## P4 FIX — ST-5b review-dock z-index (`477a9ad`)

Blocker: `.review-dock` z-index 140 was BELOW `#action-bar` (350) → action-bar overlaid/hid the
dock when a track is selected. DESIGN §STYLE requires the dock ABOVE the action-bar.

- **Chosen z-index: `360`** (raised from 140).
- **Z-order neighbours (docs/css/app.css):**
  - BELOW the dock now: `#action-bar` 350 (the conflict — now under the dock). Also `#tracks-sticky`
    100, `#top-bar`/`#download-bar` 200.
  - ABOVE the dock (still overlay it, as intended): `#scroll-top-btn` 400, `#track-info-modal`/
    `#yt-modal` 500, `#toast-stack` 9999, `#tooltip`/`#key-filter-popup` 9999, `#kbd-overlay` 99998,
    `#cmd-veil` 10000.
  - 360 is the tight slot: just above `#action-bar` (350), comfortably below the next layer (400).
- **e2e (alone):** `npx playwright test v2-review-dock.spec.ts` → **11 passed** (incl. ST-5b
  z-index-above-action-bar). exit 0.

STATUS: DONE

## P5 fix — auditor #1 review-note page sanitisation (`446f9ee`)

Closed auditor Finding 1 (90/100): `page` was sanitised with `.strip()[:64]`, which leaves internal
newlines intact → a `\n` in `page` forged a second physical line in `crew/REVIEW-NOTES.md` mimicking
a real timestamped entry (log-line injection into the human→AI channel; violates DESIGN "one line
per note", which must hold for the WHOLE line).

- **Failing→green TDD** (`tests/test_review_note.py`, +4):
  - `test_page_newline_injection_cannot_forge_a_second_line` — POST page `"home\n[2099-…] [admin]
    FORGED"` → file gains EXACTLY ONE line, no embedded newline (was: 2 lines). 
  - `test_page_strips_bracket_framing_chars` — `[`/`]` removed from the `[page]` segment.
  - `test_long_note_is_capped` — note len 5000 → **422** (schema cap).
  - `test_note_at_cap_is_accepted_and_bounded` — note len 2000 accepted, written ≤2000.
- **Diff:**
  - `routes.review_note`: `page = " ".join((body.page or "").split()).replace("[","").replace("]","")[:64] or "unknown"`
    (collapse all whitespace incl `\n`/`\r`, drop bracket framing, cap 64). `note = " ".join(body.note.split())[:2000]`
    (defensive re-cap).
  - `schemas.ReviewNote`: `note: str = Field(max_length=2000)` (oversized → 422 before the writer).
- **pytest:** `tests/test_review_note.py` → 15 passed; FULL `python -m pytest -q` → **1492 passed,
  7 skipped** (was 1488). exit 0.

STATUS: DONE

## P6 docs commit

Applied the 4 verbatim doc edits from `crew/researcher.md` "P6 docs-impact" (#1–#4; skipped
#5/#6/#7 — dev-only, not user-facing) and committed the verifier-authored e2e spec. No JS/py
behaviour touched (the review-dock.js edit is a header-comment recipe only) → no suite re-run
(GATE-2 already green incl. the P5 fix). `node --check review-dock.js` → OK.

- **Commit 1 — `a1ffae9`** docs: review dock — dev-flag, api-design endpoint, enable recipe
  - `CLAUDE.md`: `AUTOCUE_REVIEW_DOCK=1` dev-commands line after the `AUTOCUE_PERF` line (#1).
  - `.claude/project/api-design.md`: `POST /api/review-note` bullet after `/api/perf/recent` (#2).
  - `docs/js/v2/review-dock.js`: 3-line Enable/tail recipe appended to the header comment (#3).
  - `HANDOFF.md`: feat/review-dock next-work bullet (#4).
  - Carries a Context: section (CLAUDE.md + api-design.md + review-dock.js AI-layer changes).
- **Commit 2 — `bc128dc`** test(e2e): review dock specs
  - `tests/e2e/v2-review-dock.spec.ts` (new). Left `test-results/` + `tests/e2e/package-lock.json`
    untracked (out of scope).

STATUS: DONE
