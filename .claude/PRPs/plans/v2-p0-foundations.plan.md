# Feature: AutoCue 2.0 — P0 Foundations (file split, test migration, XML freeze)

## Summary

End the single-file constraint while preserving the no-build rule. Split
`docs/index.html` (14.5k lines: one `<style>` block at 13–2290, one main
`<script>` at 3734–14546) into `docs/css/app.css` + `docs/js/*.js`, migrate the
38 Vitest specs that read the HTML directly, freeze XML mode with a server
hint, and update CLAUDE.md. Zero behavior change — the page must render
byte-identically and the three-leg stack must stay green after every task.
Source PRD: `.claude/PRPs/prds/autocue-2-program.prd.md` (phase P0).

## Key facts (verified)

- `autocue/serve/app.py:78` mounts `StaticFiles(directory=DOCS_DIR, html=True)`
  → subdirectories (`docs/css/`, `docs/js/`) are already served; no backend change.
- e2e `pages-smoke` uses `python -m http.server` → also fine with subdirs.
- 38 specs in `tests/web/` read `docs/index.html` via two uniform patterns:
  `readFileSync(resolve(__dirname, '..', '..', 'docs', 'index.html'), 'utf8')`
  and `readFileSync(resolve(root, 'docs/index.html'), 'utf8')`.
- CDN scripts (jsmediatags line 7, tailwind line 2291) stay in the HTML.
- Classic-script split preserves runtime global sharing, but **function hoisting
  does not cross file boundaries**: any top-level immediate call that references
  a later-declared function breaks. Validation per slice: zero console errors on
  load (Chrome) + e2e `selectors-exist` + `qa-smoke`.

## Tasks (execute in order; VALIDATE after each)

### T1 — Test-source helper (before any split, so the swap is atomic)
Create `tests/web/_source.js` exporting `loadAppHtml()`: reads
`docs/index.html`; for each local `<link rel="stylesheet" href="css/...">`
inlines the file content into a `<style>` block, and for each local
`<script src="js/...">` (non-`http`) inlines into a `<script>` block —
reconstructing the single-file view the specs expect. Today (pre-split) it
returns the file unchanged (identity). Add `tests/web/source-helper.test.js`
asserting identity now + inlining behavior with a fixture.
VALIDATE: `npm test`.

### T2 — Migrate the 38 specs to the helper
Mechanical swap of the two `readFileSync` patterns to
`loadAppHtml()` (import from `./_source.js`). Keep everything else untouched.
VALIDATE: `npm test` (all 671+ green — helper is identity, so this proves the
swap itself is safe).

### T3 — CSS extraction
Move `<style>` body (lines 14–2289) → `docs/css/app.css`; replace the block
with `<link rel="stylesheet" href="css/app.css">`. No CSS edits whatsoever.
VALIDATE: `npm test` + `pytest` + Chrome load (both themes, zero console
errors, visual spot-check) + e2e `selectors-exist` + `pages-smoke`.

### T4 — JS extraction (single classic file first)
Move the main `<script>` body (3735–14545) → `docs/js/app.js`; replace with
`<script src="js/app.js"></script>` at the same position. Classic script —
identical semantics (globals, hoisting, inline-handler access all preserved).
VALIDATE: `npm test` + `pytest` + Chrome load + full e2e suite (the 8 known
pre-existing failures are the accepted baseline; ZERO new failures).

### T5 — Feature-file split of app.js (order-preserving)
Split `docs/js/app.js` into sequential classic scripts loaded in order:
`01-core.js` (tokens/constants/state/AppState/utils), `02-api.js` (fetch/SSE
helpers, toasts, confirm dialog, slide helpers), `03-cards.js` (buildTrackCard,
Virtualizer, renderTracks, phrase strips), `04-library-ops.js` (health,
duplicates, cue tools, auto-tag, comments, backups), `05-discover.js`,
`06-player-download.js`, `07-boot.js` (detectLocalMode, wiring, theme).
Rule: concatenating the files in order must reproduce app.js statement order.
Where a top-level immediate call references a later file's declaration, move
the call into `07-boot.js` (document each move in the commit).
VALIDATE after each extracted file: Chrome console clean + `npm test`;
full stack at the end.

### T6 — v2 module seam
Create `docs/js/v2/main.js` loaded last via `<script type="module"
src="js/v2/main.js">` — empty bootstrap (comment documenting the seam: all NEW
v2 code is ES modules here; legacy classic files migrate opportunistically).
VALIDATE: `npm test` + Chrome console clean.

### T7 — XML-mode freeze hint
In the no-server (Pages/XML) view only, add one muted line under the drop
zone: "This is the simple XML converter. Run `autocue serve` for the full
app — cues written directly, library intelligence, set builder."
VALIDATE: `npm test` + e2e `pages-smoke`.

### T8 — Docs + constraint update
Update `CLAUDE.md` ("Web app is a single self-contained HTML file" → the new
multi-file no-build rule: entry `docs/index.html`, `docs/css/`, `docs/js/`
classic legacy + `docs/js/v2/` ES modules, no bundler ever, tests use
`tests/web/_source.js`), and the matching paragraphs in
`.claude/project/web-ui.md` + `architecture.md`. Commit with a `Context:`
section (rule: AI-asset commits document the AI-layer change).
VALIDATE: grep CLAUDE.md for stale "single HTML file" claims.

### T9 — Final gate + PR
`pytest` + `npm test` + full Playwright; Chrome both themes; open PR
(base: main) titled `refactor(web): P0 foundations — multi-file no-build split`.

## Acceptance criteria
- Page renders identically (both themes) served by `autocue serve` AND
  `python -m http.server`.
- Three-leg stack green (e2e baseline: the 8 known pre-existing failures, no new).
- No build step introduced; no CDN/network dependency added.
- `docs/index.html` ≤ ~3,800 lines (markup + links); no inline `<style>`/main
  `<script>` blocks remain.
