# DESIGN — fix/design-workbench (APPROVED by human 2026-06-19, option **C**)

Single convergence source of truth. Build must match THIS exactly (GATE-2 parity check).
Goal = finish the workbench design-reconcile tail. Two units, build **B first, then A**.
Both: token-first (no hardcoded hexes), mono for data, green=signal only, the only CTA is the
ink pill, every animation `prefers-reduced-motion`-gated. NO new backend endpoint, pytest stays
untouched-green as drift proof. No-build (native ES modules under `docs/js/v2/`, classic globals
reached only via `window.*`/`window.ACBridge`).

Baseline evidence: `crew/researcher.md` — workbench already strongly conformant; these are the
last two gaps + the one design-mockup feature that was never built.

---

## UNIT B — token-provenance + cleanup (low risk, do first, its own commit)

**B1 — Vendor the Nightboard zone tokens into the canonical design system.**
- Source of truth today: `docs/css/app.css:3508-3519` defines `--zone-warmup/-build/-peak/-closing`
  (light `:root` + `html.dark`), the only place those literals live.
- Action: ADD the same four tokens (light + dark values, copied verbatim) to
  `docs/design/tokens/colors.css` so the vendored mirror matches the live `:root`. Do NOT change
  the live app.css values — app.css stays the runtime source; this reconciles the mirror.
- `--nb-tile-height`/`--nb-joint-size` are layout sizings, NOT colours → leave them out of colors.css
  (they belong to spacing/sizing if anywhere; out of scope — only the 4 colour zone tokens).
- Parity: vendored `--zone-*` values byte-equal the app.css values, both themes.

**B2 — Fold the Duplicates-toolbar inline styles into a token-layer class.**
- `docs/index.html:479` `#wb-dupes-rescan` and `:482` `#wb-dupes-bulk-delete` carry inline
  `style="font-size:12px;padding:4px 12px;"` (bulk-delete also `margin-left:auto`).
- Action: introduce a shared class (e.g. `.wb-toolbar-sm`) in `app.css` carrying
  `font-size:12px; padding:4px 12px;` (use `var(--text-*)`/scale tokens if an exact match exists;
  the values are on-scale). Apply it to both buttons; keep `margin-left:auto` on bulk-delete
  (either a small `.wb-toolbar-spacer`/`margin-left:auto` utility or keep that ONE declaration —
  prefer a utility class). Remove the inline `style=` font/padding from both buttons.
- Do NOT touch the two `display:none` spans (`#duplicates-status-label`, `#duplicates-summary`) —
  those are JS-toggled state, not styling nits. Leave them.
- Invariant: `#wb-dupes-rescan` / `#wb-dupes-bulk-delete` ids unchanged (control-inventory + e2e
  reference them); button classes `secondary-btn` / `primary` preserved; visual result identical.

---

## UNIT A — Inspector "anchor-transition card" (the design-mockup feature, its own commit)

**What:** a new section inside the workbench inspector (`docs/js/v2/workbench/inspector.js`,
`renderInspector()`, mode 'track' only) that scores the transition **anchor → focused track** and
shows the band + reasons — so the inspector tells you how the selected track mixes out of what's
playing. Reuses the SAME contract Nightboard uses; zero backend change.

**Anchor semantics (APPROVED):**
- anchor = the **now-playing track** (`nowPlayingId`).
- fallback = the **previously-focused** track id (the inspector remembers the last `_focusedId`
  before the current one).
- If anchor resolves to the focused track itself, or there is no anchor → the section is **hidden**
  (don't score a track against itself / nothing).

**Anchor source / interop:**
- `nowPlayingId` is a classic `let` (`docs/js/01-core.js:522`) — NOT on `window`. Add a tiny
  read-only accessor to the sanctioned bridge: `nowPlayingId: () => nowPlayingId,` in
  `window.ACBridge` (`docs/js/08-set-builder-boot.js:1053` block). This is the ONLY legacy edit in
  unit A and it mirrors the existing read-only pass-throughs (`tracks`, `selectedIds`, …).
- The inspector reads `window.ACBridge.nowPlayingId()` for the anchor; tracks resolved from
  `window.ACBridge.tracks()` (already used in `renderInspector`).

**Data / API (REST is allowed for v2 — Nightboard precedent):**
- `POST /api/transitions/score` body `{track_a_id: <anchorId>, track_b_id: <focusedId>}` (ints).
- Response (`autocue/serve/schemas.py:270 TransitionResponse`): `{overall, bpm, key, energy,
  bpm_a, bpm_b, key_a, key_b, end_energy_a?, start_energy_b?, explanation: string[]}`.
- Use `overall` for the band; render the `explanation` strings as the reason list.
- Guard: `if (!r.ok) return;` (silent, no toast — it's an advisory card). Fetch lazily when the
  inspector renders a track that HAS an anchor; abort/ignore stale responses if the focus changes
  before the fetch resolves (track focus token, like the release-mode `_mode` guard).

**Visual (matches the design language; reuse existing inspector idiom):**
- A `_section('Transition in')` block (reuse the existing `_section()` helper) placed right after
  the header chips / before or after "Energy curve" — pick the position that reads as "how this
  mixes in" (recommend directly under the header, above Energy curve).
- One row: small mono **score number** + band class, anchor track name ("from <anchor title>"),
  then up to 3 `explanation` strings as muted lines.
- **Band cutoffs ≥85 / ≥70** — same as Nightboard `JOINT_BANDS` (`canvas.js`). Do NOT import from
  nightboard (avoid cross-feature coupling); replicate the two cutoffs as a local const in
  inspector.js (`good ≥85`, `ok ≥70`, else `weak`) and map to existing band colour tokens (reuse
  the green=good / amber / muted signal scale already in app.css — NO new colour token needed;
  if a `.wb-insp-*` band class is added it references existing `--green`/`--amber`/`--muted`).
- Score value + BPM/key fragments are `var(--font-mono)`; labels are sans. Green only on the
  good-band score (signal). Any reveal animation `prefers-reduced-motion`-gated; a static card is
  acceptable (no animation required).
- Hidden state must be clean: no empty section header when there's no anchor.

**Invariants (must not regress):**
- TASK-033/037: `#track-list` never detached; sticky/virtualizer untouched (this is inspector-only).
- The 4 existing inspector consumers (track focus, release mode, clearInspector, play button) stay
  green. Release mode (`_mode==='release'`) must NOT render the anchor card.
- No new backend endpoint; `pytest` count unchanged.

---

## VERIFY / GATE-2 acceptance (both units)
- STATIC: `pytest` (unchanged count, green) · `npm test` (vitest green, +new unit tests).
- BEHAVIORAL: `tests/e2e/` green run ALONE (Discover #189 contention flakes are the known baseline,
  not regressions).
- LIVE @127.0.0.1:3003 (Chrome DevTools MCP, ONE driver at a time):
  - B2: Duplicates place toolbar renders identically (Rescan + Delete non-keepers), no inline
    font/padding styles in DOM, bulk-delete still right-aligned.
  - A: select a track while another is playing → "Transition in" card shows a mono score + band
    colour + reasons; selecting with nothing playing falls back to previously-focused or hides;
    release detail (Discover) shows NO anchor card. Screenshot light + dark.
- Token check: `grep` shows `--zone-*` now present in `docs/design/tokens/colors.css` matching app.css.

## Coverage map → crew/test-designer.md · e2e specs → crew/test-verifier.md
