# P6 docs-impact — review dock (READ-ONLY; no edits applied)

Change on `feat/review-dock` (crew/DESIGN.md + crew/implementer.md):
- NEW dev-only `POST /api/review-note` — **403** unless env `AUTOCUE_REVIEW_DOCK=1`; appends
  `[ts] [page] note` (whitespace-collapsed to one line, note capped, page `[:64]`/`"unknown"`) to
  `crew/REVIEW-NOTES.md`. No auth/DB/Rekordbox. Mirrors the `/api/perf/recent` env-gate precedent.
- NEW frontend dev tool `docs/js/v2/review-dock.js` — renders only when `ACBridge.isLocalMode()`
  **AND** `localStorage.ac_review_dock === '1'`; inert on Pages. `.review-dock*` CSS in `app.css`.
- New flags: env `AUTOCUE_REVIEW_DOCK`, localStorage `ac_review_dock`.

No existing doc mentions the dock (grep: only the source files). It is **dev-only / not user-facing** —
keep all edits terse.

---

## Findings (file:line · class · exact edit text)

### 1. `CLAUDE.md` dev-commands block (after `AUTOCUE_PERF` line 85)  **[MISSING — recommended, 1 line]**
The block lists every dev env-flag (`AUTOCUE_POOL_SIZE`, `AUTOCUE_PERF`); the review-dock flag belongs
here for discoverability, exactly mirroring the `AUTOCUE_PERF` precedent. One line only.

**Edit (insert after line 85):**
```
AUTOCUE_REVIEW_DOCK=1 autocue serve  # enable POST /api/review-note (dev review dock; also set localStorage.ac_review_dock=1)
```

### 2. `.claude/project/api-design.md` (after the `/api/perf/recent` bullet, ~line 72)  **[MISSING — recommended]**
This is the canonical home for the endpoint contract; the perf dev-only endpoint sits right above it.

**Edit (insert new bullet after the `/api/perf/recent` bullet):**
> - **`POST /api/review-note`** (dev-only review dock): returns **403** unless `AUTOCUE_REVIEW_DOCK=1`.
>   Body `{page: str = "", note: str}` (blank/whitespace `note` → 422). Appends
>   `[YYYY-MM-DD HH:MM:SS] [<page>] <note>\n` to `Path.cwd()/crew/REVIEW-NOTES.md` (whitespace/newlines
>   collapsed to one line; `page` stripped+`[:64]`, default `"unknown"`); returns `{"ok": true}`. No
>   auth/DB/Rekordbox surface. Second guard: the hosted Pages deploy has no FastAPI. Tests:
>   `tests/test_review_note.py`.

### 3. HOW-TO enable recipe → `docs/js/v2/review-dock.js` header comment  **[MISSING — recommended; this is the right home]**
The header (lines 1-11) explains WHAT/WHY but not the enable steps. Co-locate the recipe there — do NOT
create a new docs file (a 3-line dev recipe doesn't warrant one) and do NOT bloat CLAUDE.md beyond #1.

**Edit (append inside the header block, before the closing `*/` at line 11):**
> ` *`
> ` * Enable (dev): start the server with AUTOCUE_REVIEW_DOCK=1, set`
> ` *   localStorage.ac_review_dock = '1' in the local-app tab, reload; then`
> ` *   tail -f crew/REVIEW-NOTES.md to read submitted change requests.`

### 4. `HANDOFF.md` open-items tail (after line 212, near the fix/design-workbench line)  **[MISSING — recommended]**
Session-continuity doc; the dock is the current in-flight work and isn't reflected.

**Edit (add a bullet):**
> `- feat/review-dock: dev-only Review Dock shipped — POST /api/review-note (403 unless AUTOCUE_REVIEW_DOCK=1, appends to crew/REVIEW-NOTES.md) + docs/js/v2/review-dock.js (renders only in local mode AND localStorage.ac_review_dock=1). Enable: AUTOCUE_REVIEW_DOCK=1 + the localStorage flag, then tail -f crew/REVIEW-NOTES.md.`

### 5. `CLAUDE.md` must-know constraints (perf bullet line 147 precedent)  **[OK — skip; rationale]**
A full must-know bullet (like the perf one) is **not** warranted: the dock is a dev-time AI-workflow
tool with no product constraint a future session must know to avoid bugs, and the <500-line budget is
better spent elsewhere. #1's dev-commands line covers discoverability. Recommend SKIP.

### 6. `docs/FEATURES.md`  **[OK — no entry]**
Dev-only, never active for real users (two guards) → not a user-facing feature. No edit.

### 7. `docs/reference/rest-api.md`  **[OK — skip]**
That reference documents the user/product API; `/api/perf/recent` (the dev-only precedent) is likewise
absent there. Keep the dev-only endpoint in api-design.md (#2) only. No edit.

---

## Priority
- **Recommended:** #1 (CLAUDE.md dev-commands 1-liner), #2 (api-design.md endpoint bullet),
  #3 (review-dock.js header recipe — the HOW-TO home), #4 (HANDOFF next-work line).
- **Skip (justified):** #5 must-know bullet, #6 FEATURES.md, #7 rest-api.md.

STATUS: DONE
