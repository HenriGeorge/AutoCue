# P6 docs-impact — fix/design-workbench (READ-ONLY analysis; no edits applied)

Change under review (crew/DESIGN.md + crew/implementer.md):
- **A** — NEW inspector "Transition in" anchor-transition card in `docs/js/v2/workbench/inspector.js`
  (`renderInspector`, track mode). Anchor = now-playing track via **new `window.ACBridge.nowPlayingId()`**
  accessor (added at `docs/js/08-set-builder-boot.js`); fallback = previously-focused id; reuses
  `POST /api/transitions/score`; **no new backend**.
- **B1** — vendored `--zone-warmup/-build/-peak/-closing` (light+dark) into `docs/design/tokens/colors.css`
  (byte-equal mirror of `app.css:3508-3519`; app.css unchanged).
- **B2** — folded dupes-toolbar inline styles into `.wb-toolbar-sm` + `.wb-toolbar-spacer` classes in `app.css`
  (ids/classes/visuals preserved).

---

## Findings (file:line · classification · exact edit text)

### 1. `.claude/project/web-ui.md:81` — ACBridge accessor list  **[STALE — needs edit]**
Current text enumerates the bridge accessors but omits the new one:
> `window.ACBridge` (`tracks()`, `healthSummary()`, `isLocalMode()`, `selectedCount()` — accessor closures …)

**Edit (replace that parenthetical list):**
`window.ACBridge` (`tracks()`, `healthSummary()`, `isLocalMode()`, `selectedCount()`, `nowPlayingId()` — accessor closures over the classic scripts'

### 2. `.claude/project/web-ui.md` (inspector / v2 workbench section, after the P5 release-mode para ~line 145) — **[MISSING — needs new entry]**
No description of the anchor-transition card exists. Add one bullet:

**New bullet text (insert after the `'track'|'release'` inspector paragraph):**
> - **inspector.js anchor-transition card (track mode)** — a "Transition in" advisory section scoring
>   **anchor → focused** via `POST /api/transitions/score` (no new backend). Anchor = `ACBridge.nowPlayingId()`,
>   fallback = previously-focused id; hidden when no anchor / anchor === focused / release mode. Band cutoffs
>   `≥85/≥70` replicated locally (`ANCHOR_BANDS`, NOT imported from `canvas.js`); colour maps to existing
>   tokens only (good→`--green`, ok→`--warn-amber`, weak→`--muted`); score/BPM/key mono; reveal RM-gated.
>   Monotonic `_txToken` discards stale responses on refocus.

### 3. `CLAUDE.md:142` — Nightboard bullet, `--zone-*` provenance  **[STALE — needs edit]**
Current: `Net-new --zone-warmup/-build/-peak/-closing tokens (light+dark) in app.css.`
The tokens are now ALSO vendored into the canonical design system.

**Edit (replace that sentence):**
`Net-new --zone-warmup/-build/-peak/-closing tokens (light+dark) in app.css (runtime source of truth), mirrored byte-equal into docs/design/tokens/colors.css.`

### 4. `CLAUDE.md` workbench bullet (line 45, end) — anchor-transition card  **[MISSING — optional, keep terse]**
CLAUDE.md has no inspector-internals bullet and is budget-constrained (<500 lines), so prefer a single clause
appended to the existing 2.0 workbench bullet rather than a new bullet.

**Edit (append one clause to the line-45 workbench sentence):**
`The workbench inspector shows a "Transition in" advisory card (anchor=now-playing via ACBridge.nowPlayingId(), reuses POST /api/transitions/score, no new backend).`

### 5. `docs/FEATURES.md` Feature 6 "Transition Scoring" (heading at line 261) — **[MISSING — needs new entry]**
The anchor-transition card is a NEW user-facing surface of an existing feature; FEATURES.md describes the
endpoint/modal but not the inspector card.

**Edit (add one line at the end of the Feature 6 section, ~after line 330):**
`In the workbench, focusing a track while another is playing shows a **"Transition in"** card in the inspector — the transition score (0–100, banded), the now-playing anchor, and the human-readable reasons for mixing the focused track in after it. Hidden when nothing is playing or you focus the playing track itself.`

### 6. `HANDOFF.md:212` — "Next build work: only P6 remains"  **[STALE — needs edit]**
HANDOFF doesn't reflect the design-workbench reconcile tail (B1/B2) or the inspector anchor card (A).
Session-continuity doc — coordinator typically updates at finish; flag so it isn't missed.

**Edit (add a bullet under the open-items/next section, ~line 212):**
`- fix/design-workbench: design-reconcile tail shipped — zone tokens vendored into docs/design/tokens/colors.css; dupes-toolbar inline styles → .wb-toolbar-sm/.wb-toolbar-spacer; NEW inspector "Transition in" anchor card (ACBridge.nowPlayingId(), POST /api/transitions/score, no new backend).`

### 7. `docs/reference/web-app.md:1060` — legacy transition modal (`#transition-score-btn` / `showTransitionScore()`)  **[OK — optional]**
Documents the *legacy* transition surface; the new card is a *workbench/v2* surface. No staleness (the legacy
modal still exists). Optional cross-reference only — recommend SKIP to avoid scope creep (web-app.md is the
legacy-UI internals doc).

### 8. `docs/reference/transition-scoring.md`  **[OK]**
Documents the scoring algorithm/endpoint contract, which is unchanged (no new backend). No edit needed —
the card is presentation-only over the same `POST /api/transitions/score` contract.

### 9. `.claude/project/architecture.md` / `api-design.md`  **[OK]**
No new module, endpoint, or schema (`TransitionResponse` reused as-is). B1/B2 are CSS/markup only. No edit.

---

## Priority for the implementer
- **Must:** #1 (web-ui.md ACBridge list — concretely wrong now), #3 (CLAUDE.md zone provenance — concretely stale).
- **Should:** #2 (web-ui.md anchor-card bullet), #5 (FEATURES.md user-facing line), #6 (HANDOFF next-work line).
- **Optional/terse:** #4 (CLAUDE.md one clause). **Skip:** #7, #8, #9 (no staleness).

STATUS: DONE
