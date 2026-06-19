# DESIGN — REVIEW DOCK (dev-only in-page feedback bridge) · approved 2026-06-19

> Prior design (workbench reconcile tail, option C) shipped as PR #245 — its DESIGN is preserved in
> git history of crew/DESIGN.md. This file now holds the CURRENT work: the Review Dock.

Single convergence source of truth (GATE-2 parity). Async human→AI bridge: human types a change
request into an in-page bar on the running local app → it appends a line to `crew/REVIEW-NOTES.md`
→ the AI tails that file, makes the change, human reloads to verify. Stack = AutoCue's vanilla-JS
no-build web app + FastAPI (NOT Next.js); contract identical to the spec (POST → append a line).

**Branch:** build on a NEW branch `feat/review-dock` created FROM the current worktree HEAD (so the
committed `crew/` artifacts persist and the diff stays OFF the open #245 PR). PR base decided at finish.

**Non-negotiable safety line:** the dock must NEVER be active for real users. Two independent guards.

---

## 1. API — `POST /api/review-note` (autocue/serve/routes.py + schemas.py)
- **Dev-gate (403):** if `os.environ.get("AUTOCUE_REVIEW_DOCK") != "1"` → `raise HTTPException(403, …)`.
  Mirrors the `/api/perf/recent` env-gate precedent (routes.py:4691, which 404s unless AUTOCUE_PERF).
  Use **403** per the spec (not 404). The hosted Pages deploy has no FastAPI at all → second guard.
- **Body schema** `ReviewNote` (schemas.py): `page: str = ""`, `note: str`. Reject empty note → 422.
- **Append** one line to `Path.cwd() / "crew" / "REVIEW-NOTES.md"` (create parent+file if missing),
  format **`[YYYY-MM-DD HH:MM:SS] [<page>] <note>\n`** (`datetime.now().strftime("%Y-%m-%d %H:%M:%S")`).
  Sanitize: `note.strip()`, strip newlines from note (one line per note); `page` → `[:64]`, default
  `"unknown"`. cwd = the repo root (server is started from the worktree per HANDOFF).
- **Return** `{"ok": True}`. r.ok-checked on the client.
- Registered on the existing `router = APIRouter(prefix="/api")`. No auth surface, no DB, no Rekordbox.

## 2. UI — `docs/js/v2/review-dock.js` (ES module, dev-only render)
- **Render gate:** only when `localMode === true` (read via `window.ACBridge.isLocalMode()`) AND
  `localStorage.getItem('ac_review_dock') === '1'`. Otherwise the module no-ops (nothing appended to
  the DOM). On Pages (XML mode) localMode is false → never renders. Mirrors the `autocue_perf` gate.
- **Markup (built in JS, appended to `<body>`; NOT added to index.html so Pages markup is untouched):**
  a fixed bottom `<form class="review-dock" role="form">`:
  - a prompt glyph (small inline SVG or "✎", `aria-hidden`),
  - a real `<label class="sr-only" for="review-dock-input">Describe a change for this page</label>`,
  - a page badge `<span class="review-dock-page mono">[<page>]</span>` (the derived current page),
  - `<input id="review-dock-input" type="text" placeholder="describe a change for this page…">`,
  - a submit affordance (Enter submits; an ink-pill "Send" button is fine).
- **Current-page detection** `_derivePage()`: `body.nb-active` → `"nightboard"`; else
  `body.classList.contains('wb-place-dupes')` → `"duplicates"`, `wb-place-discover` → `"discover"`,
  `wb-place-library` → `"library"`; else `window.ACBridge.crate()` (or `"cues"`). Recompute on submit.
- **Submit:** `preventDefault`; `note = input.value.trim()`; if empty, no-op. POST JSON
  `{page, note}` to `/api/review-note`; **check `r.ok`** (CLAUDE.md fetch rule); on ok → clear input
  + show a brief **"✓ sent"** confirmation that auto-clears after ~2s; on !ok → reuse `window.showToast`
  with the error. Guard against double-submit (disable while in flight).
- **A11y:** real `<label>` (sr-only), visible focus ring (reuse the global `--green-ring` focus style),
  fully keyboard-usable (Enter submits, input is a normal tab stop), `aria-live="polite"` on the
  confirmation so "✓ sent" is announced.
- **Motion:** the "✓ sent" fade + any slide is `@media (prefers-reduced-motion: reduce)` → instant.
- **Wiring:** `import { initReviewDock } from './review-dock.js'; initReviewDock();` at the END of
  `docs/js/v2/main.js` (after the workbench/places exist). Expose nothing it doesn't need on `window.AC2`.

## 3. STYLE — `.review-dock*` block in docs/css/app.css (match the design system; tokens only)
- Fixed bottom, full width, ~48px tall, above content (z-index above the action-bar/sticky chrome,
  e.g. 140; action-bar is fixed). Glass chrome like the existing sticky header: `--surface` bg with
  backdrop blur (the project's glass-on-chrome idiom), top `1px solid var(--border)`, soft shadow.
- Input: `--radius-md` (8px) field, `--font-sans`, green focus ring (`--green-ring`). Page badge is
  `--font-mono` + `--muted`. Send button = the **ink pill** (`--ink`/`--on-ink`, `--radius-pill`) —
  NEVER green (green = signal only). "✓ sent" uses `--green` (success signal). `.sr-only` utility if
  not already present. NO hardcoded hexes — `var(--token)` only. Honour both themes (`html.dark`).
- The CSS ships to Pages but is inert (dock never renders there). Acceptable (matches perf-mirror precedent).

## VERIFY / GATE-2 acceptance
- STATIC: `pytest` (+ new TestReviewNote, count grows), `npm test` (+ review-dock vitest). Green.
- BEHAVIORAL: e2e — dock renders pinned at bottom when gated on; typing + submit shows "✓ sent";
  route-mock `/api/review-note` (don't write the real file in e2e). Run ALONE (#189 baseline).
- LIVE @127.0.0.1:3003 (ONE driver), with `AUTOCUE_REVIEW_DOCK=1` + `localStorage.ac_review_dock='1'`:
  - `curl -X POST http://127.0.0.1:3003/api/review-note -H 'content-type: application/json'
     -d '{"page":"test","note":"hello"}'` → `{"ok":true}` AND a line lands in `crew/REVIEW-NOTES.md`.
  - **Prod/disabled proof:** with `AUTOCUE_REVIEW_DOCK` UNSET, the same curl → **403**; and with the
    localStorage flag unset the dock does NOT render (querySelector('.review-dock') is null).
  - Screenshot a page showing the dock pinned at the bottom (light; dark optional).
- Dev-only invariant: index.html markup unchanged (dock is JS-injected); no new prod surface.

## Coverage map → crew/test-designer.md · e2e → crew/test-verifier.md · build log → crew/implementer.md
