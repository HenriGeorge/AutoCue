# Implementer result — P3 BUILD (fix/design-workbench)

Built the two units from `crew/DESIGN.md` (approved option C) in order, B then A,
each its own commit. TDD for unit A (failing vitest first → impl → green). I own all
source edits. Dev server NOT started (verifier owns the live :3003 driver).

## UNIT B — `c1756f9` fix(design): vendor zone tokens + fold dupes-toolbar inline styles
- **B1** — vendored `--zone-warmup/-build/-peak/-closing` (light `:root` + `html.dark`,
  copied verbatim from `app.css:3508-3519`) into `docs/design/tokens/colors.css`. app.css
  runtime values UNCHANGED (it stays the source of truth; this reconciles the mirror).
  Layout sizings `--nb-tile-height/--nb-joint-size` intentionally NOT vendored (not colours).
- **B2** — added `.wb-toolbar-sm { font-size:12px; padding:4px 12px }` + `.wb-toolbar-spacer
  { margin-left:auto }` in `app.css`; applied both to `#wb-dupes-rescan` (+secondary-btn) and
  `#wb-dupes-bulk-delete` (+primary, +disabled); removed the inline font/padding `style=` from
  both. The pre-existing `#wb-dupes-bulk-delete` ID-rule padding `4px 14px → 4px 12px` was
  aligned so the RENDERED value stays identical (the inline 12px had been masking the 14px;
  the ID rule (1-0-0) still wins over the class, so this was required to keep visuals byte-equal).
  ids + `secondary-btn`/`primary` classes preserved; the two `display:none` status spans untouched.
- Guard test: `tests/web/v2-token-provenance.test.js` (7) — locks B1 byte-equal mirror + B2
  class/no-inline-style/spacer.

## UNIT A — `741cddb` feat(web): inspector anchor-transition card
- New "Transition in" advisory card in `docs/js/v2/workbench/inspector.js` `renderInspector()`
  (track mode only), rendered directly under the header (above Energy curve).
- Anchor = `window.ACBridge.nowPlayingId()` (NEW read-only accessor added to the bridge at
  `docs/js/08-set-builder-boot.js` — the ONLY legacy edit; `nowPlayingId` is a classic `let`,
  not a window prop). Fallback = previously-focused id (`_prevFocusedId`, captured before
  reassigning `_focusedId`). Hidden when: no anchor, anchor === focused, or release mode.
- `POST /api/transitions/score {track_a_id, track_b_id}` (ints; REST per Nightboard precedent).
  `!r.ok` / network error → silent; the placeholder section is removed (no empty header lingers).
  Monotonic `_txToken` + focus/mode recheck discards a stale response if focus changes mid-flight.
- Band cutoffs `good ≥85 / ok ≥70 / weak` replicated LOCALLY (`ANCHOR_BANDS` in inspector.js) —
  NOT imported from nightboard/canvas.js. Colour maps to existing tokens only: good→`--green`
  (signal), ok→`--warn-amber`, weak→`--muted`. NO new colour token. Score + BPM/key fragments are
  mono. Reveal animation `.wb-insp-tx-sec` is `prefers-reduced-motion: reduce`-gated.
- `renderInspector` now sets `_mode='track'` (it is the track renderer; mirrors
  `renderReleaseInspector` setting 'release') so a release→track refocus is clean.
- TASK-033/037 untouched (`#track-list` never detached — inspector-only). The 4 existing inspector
  consumers + the v2-bridge shape test stay green.
- Tests: `tests/web/v2-inspector-anchor.test.js` (9) — present/hidden(no-anchor)/hidden(self)/
  fallback-to-prev/band-mapping(good·ok·weak)/!ok-silent-remove/stale-guard/release-mode-no-card.

## Fresh evidence (run THIS turn)
- `npm test` → **62 files, 931 passed** (baseline 915 + 7 provenance + 9 inspector). exit 0.
- `python -m pytest -q` → **1477 passed, 7 skipped** (count UNCHANGED — no backend edit). exit 0.
- `node --check` on both edited JS files → syntax OK. (No lint/typecheck script in this no-build repo.)

## Handoff to verifier (LIVE @127.0.0.1:3003 — I did NOT drive it)
- B2: Duplicates place toolbar renders identically (Rescan + Delete non-keepers), bulk-delete
  still right-aligned, no inline font/padding `style=` in DOM.
- A: focus a track while another is playing → "Transition in" card shows mono score + band colour
  + reasons; focus with nothing playing falls back to previously-focused or hides; Discover
  release detail shows NO anchor card. Screenshot light + dark.
- Token check: `grep --zone-` now present in `docs/design/tokens/colors.css` matching app.css.

STATUS: DONE

## P6 docs commit

Applied the 6 verbatim doc edits from `crew/researcher.md` "P6 docs-impact" (#1–#6;
skipped #7/#8/#9 — no staleness) and committed the verifier-authored e2e specs. No JS
touched (docs + specs only), so no re-run of the suite (GATE-2 already green this turn).

- **Commit 1 — `6d8ef60`** docs: anchor-transition card + zone-token provenance
  - `.claude/project/web-ui.md`: ACBridge accessor list +`nowPlayingId()` (#1); new
    inspector.js anchor-transition-card bullet (#2).
  - `CLAUDE.md`: zone-token `--zone-*` provenance → mirrored byte-equal into
    `docs/design/tokens/colors.css` (#3); appended the one-clause "Transition in"
    advisory-card note to the line-45 workbench bullet (#4).
  - `docs/FEATURES.md`: Feature 6 user-facing "Transition in" card line (#5).
  - `HANDOFF.md`: design-workbench next-work bullet (#6).
  - Includes a Context: section (CLAUDE.md + web-ui.md AI-layer changes) per the
    context-engineering rule.
- **Commit 2 — `ef0819a`** test(e2e): inspector anchor card + dupes-toolbar specs
  - `tests/e2e/v2-inspector-anchor.spec.ts` (new) + `tests/e2e/v2-duplicates-place.spec.ts`
    (modified). Committed only the two scoped specs — `test-results/` artifacts and
    `tests/e2e/package-lock.json` left untracked (out of scope).

STATUS: DONE
