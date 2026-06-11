# Feature: Adopt the AutoCue Design System in `docs/index.html`

## Summary

Reconcile the AutoCue web app's CSS to its own formalized design system (vendored
at `docs/design/`). The app is already ~90% compliant (pill buttons, ink primary
CTA, mono BPM/key/time). This plan completes the **token layer** (Phase 1 — add the
tokens the system defines but the live `:root` lacks, plus design-system-name
aliases) and applies a small set of **adherence fixes** (Phase 2 — the `Mix NN/100`
score chip should be mono; cue-badge washes should derive from the `--cue-*` tokens).
Verified in both themes (Phase 3). Source PRD:
`.claude/PRPs/prds/adopt-autocue-design-system.prd.md` (Phases 1–3).

## User Story

As the maintainer (and future Claude sessions) doing UI work in `docs/index.html`,
I want a complete, formalized token layer and enforced brand rules,
so I can build on-brand surfaces fast without hardcoding hexes.

## Problem Statement

The live `:root`/`html.dark` (lines 16–61) defines 38 properties but is missing
tokens the design system formalizes (`--ink`/`--on-ink`, `--green-wash`/`--green-ring`,
`--rb-*` dots, `--radius-xl`, `--shadow-lg`, `--glass-bg`, motion easing/durations,
`--text-md`/`--text-2xl`, weight/line-height/tracking, `--content-max`). A couple of
data surfaces also drift from rule 3 (mono-for-data).

## Solution Statement

Additive token completion + aliases (no renames), then surgical adherence fixes,
each verified live in light and dark. Preserve TASK-033 (160px card) and TASK-037
(sticky/fixed layout) invariants.

## Metadata

| Field | Value |
|---|---|
| Type | ENHANCEMENT (design-system reconciliation) |
| Complexity | LOW–MEDIUM (localized to `<style>` + 2 component rules) |
| Systems Affected | `docs/index.html` (`<style>` block + `.mix-score-chip`, cue-badge slot rules) |
| Dependencies | none (no-build, no external libs) |
| Estimated Tasks | 6 |

---

## UX Design

### Before State
```
:root (16–43) + html.dark (45–61): 38 tokens, AutoCue look already in place.
Gaps: no --ink alias, no --green-wash/ring, no --rb-* dots, no --radius-xl,
no motion tokens; .mix-score-chip "Mix 72/100" renders in Inter (sans);
cue-badge wash uses hardcoded rgba literals.
```

### After State
```
:root/html.dark = complete superset of docs/design/tokens/* (canonical app names
kept; --surface-2/--font-sans/--font-mono added as aliases). Score chip "Mix 72/100"
in JetBrains Mono. Cue washes derive from --cue-* via color-mix. Both themes verified.
No layout/structure change; cards still 160px.
```

### Interaction Changes
| Location | Before | After | User Impact |
|---|---|---|---|
| `.mix-score-chip` (708–712) | `Mix 72/100` in sans | same in mono | Score reads as a measured value (rule 3) |
| cue-badge slots (686–693) | hardcoded `rgba(...,.08)` wash | `color-mix(... --cue-X 8% ...)` | Identical look, token-derived (no drift) |
| `:root`/`html.dark` (16–61) | 38 tokens | full system superset + aliases | New UI work can `var(--token)` everything |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `docs/index.html` | 16–61 | The `:root`/`html.dark` blocks to extend (the ONLY token block) |
| P0 | `docs/design/tokens/colors.css` | all | Canonical color tokens incl. `--ink`, washes, `--rb-*`, `--shadow-lg`, `--glass-bg` |
| P0 | `docs/design/tokens/spacing.css` | all | `--radius-xl` (16px), `--content-max`, motion `--ease-*`/`--dur-*` |
| P0 | `docs/design/tokens/typography.css` | all | `--text-md`/`--text-2xl`, weight/line-height/tracking, `--font-sans`/`--font-mono` |
| P1 | `docs/index.html` | 708–712 | `.mix-score-chip` (mono fix target) |
| P1 | `docs/index.html` | 674–693 | cue-badge + slot wash rules (tokenize wash) |
| P2 | `docs/design/components/dj/ScoreChip.prompt.md` | all | Score chip is the green-outline `NN/100` data chip |

---

## Patterns to Mirror

**Existing token block style** (`docs/index.html:16-43`) — keep the compact, grouped,
aligned-comment format; append new groups in the same style:
```css
--bg:          #fafafa;
--surface:     #ffffff;
--surface2:    #f2f2f2;
/* ... */
--cue-a: #18a80c; --cue-b: #2246e0; /* ... */
```

**Token-derived wash already used elsewhere** (e.g. `1301-1311 .genre-chip.active`,
`1829 status-dot`) uses `color-mix(in srgb, var(--green) 14%, transparent)` — mirror
this for the cue washes instead of hardcoded rgba.

**Mono data chips already correct** (`636-646 .track-bpm/.track-key/.track-time`,
`675-679 .cue-badge`) all set `font-family: var(--mono)` — mirror for `.mix-score-chip`.

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `docs/index.html` (`:root` 16–43) | UPDATE | Add missing light-theme tokens + aliases |
| `docs/index.html` (`html.dark` 45–61) | UPDATE | Add dark overrides for new themed tokens (`--ink`/`--on-ink`/washes/`--glass-bg`/`--shadow-lg`) |
| `docs/index.html` (`.mix-score-chip` 708–712) | UPDATE | `font-family: var(--mono)` (rule 3) |
| `docs/index.html` (cue slot rules 686–693) | UPDATE | wash → `color-mix` of `--cue-*` (no visual change) |
| `tests/web/design-tokens.test.js` | CREATE | Assert the token set is complete + aliases resolve |

---

## NOT Building (Scope Limits)

- **Token renames** — app names stay canonical; only add aliases.
- **Radius churn** — soft 10px chips (`.tf-chip`, `.category-chip`) left as-is; not a clear violation, changing them is visual noise.
- **`.category-chip` font** — renders a word label, not a number; sans is correct.
- **kbd literal `monospace`** (1733/2139/2209) — acceptable; not measured-value data.
- React components, layout redesign, the 18MB UI-kit/images.

---

## Step-by-Step Tasks

### Task 1: Extend `:root` (light) with missing tokens + aliases
- **ACTION**: UPDATE `docs/index.html` `:root` block (16–43)
- **IMPLEMENT** (values verbatim from `docs/design/tokens/*`):
  - Action ink: `--ink:#0a0a0a; --ink-hover:#2a2a2a; --on-ink:#fafafa;`
  - Green washes: `--green-wash:rgba(21,154,5,.08); --green-ring:rgba(21,154,5,.14);`
  - Semantics: `--warn:#f0801a; --warn-amber:#ffa000; --error:#e53935; --danger:#e74c3c; --rating:#f0b429;` (add only those not already present)
  - Rekordbox dots: `--rb-pink/red/orange/yellow/green/aqua/blue/purple` per colors.css
  - Elevation/glass: `--shadow-lg: 0 10px 32px rgba(0,0,0,.18), 0 2px 8px rgba(0,0,0,.08); --glass-bg: rgba(255,255,255,.86);`
  - Radius: `--radius-xl:16px;`
  - Type: `--text-md:15px; --text-2xl:28px;` + `--fw-regular/medium/semibold/bold`, `--lh-tight/snug/body`, `--tracking-tight/-label`
  - Layout/motion: `--content-max:900px; --ease-out/-enter/-fill; --dur-fast/-btn/-chrome`
  - Aliases: `--surface-2: var(--surface2); --font-sans: var(--font); --font-mono: var(--mono);`
- **GOTCHA**: Do NOT redefine tokens already present (`--bg`,`--green`,`--cue-*`,`--sp-*`,`--radius-sm/md/lg/pill`,`--text-xs..xl`,`--shadow-sm/md`). Additive only.
- **VALIDATE**: grep each vendored token name resolves in `:root`; load page, confirm unchanged.

### Task 2: Extend `html.dark` with dark overrides for new themed tokens
- **ACTION**: UPDATE `html.dark` block (45–61)
- **IMPLEMENT**: `--ink:#fafafa; --ink-hover:#e2e2e2; --on-ink:#0a0a0a;`
  `--green-wash:rgba(40,226,20,.08); --green-ring:rgba(40,226,20,.14);`
  `--shadow-lg: 0 10px 32px rgba(0,0,0,.45), 0 2px 8px rgba(0,0,0,.3); --glass-bg: rgba(12,10,9,.80);`
  (cue palette dark overrides already present at 58–60; leave.)
- **GOTCHA**: aliases need no dark override (they point at canonical names that already flip).
- **VALIDATE**: toggle `html.dark`; confirm `getComputedStyle` of `--ink` flips white↔black.

### Task 3: Score chip → mono (rule 3)
- **ACTION**: UPDATE `.mix-score-chip` (708–712)
- **IMPLEMENT**: add `font-family: var(--mono);`
- **MIRROR**: `.track-bpm` (637) / `.cue-badge` (676).
- **VALIDATE**: live — a score chip renders `Mix NN/100` in JetBrains Mono; card still 160px.

### Task 4: Cue-badge wash → token-derived
- **ACTION**: UPDATE cue slot rules (686–693)
- **IMPLEMENT**: replace each `background: rgba(<lit>,.08)` with
  `background: color-mix(in srgb, var(--cue-X) 8%, transparent)` for slot X.
- **MIRROR**: `.genre-chip.active` (1306) color-mix pattern.
- **GOTCHA**: keep `color`/`border-color: var(--cue-X)` unchanged; verify washes look identical in BOTH themes (dark `--cue-*` differ, so color-mix is *better* than the light-only hardcoded rgba).
- **VALIDATE**: live — cue badges A–H look unchanged (light) and correctly tinted (dark).

### Task 5: CREATE `tests/web/design-tokens.test.js`
- **ACTION**: CREATE a Vitest that reads `docs/index.html`, extracts the `:root` block, and asserts every token name from `docs/design/tokens/{colors,spacing,typography}.css` is present (as canonical OR alias), and that aliases (`--surface-2`,`--font-sans`,`--font-mono`) are defined.
- **MIRROR**: existing `tests/web/*.test.js` structure (vendor logic, plain asserts).
- **VALIDATE**: `npx vitest run tests/web/design-tokens.test.js` green.

### Task 6: Full validation (Phase 3)
- **ACTION**: run the three-leg stack + live both-theme check.
- **VALIDATE**: see Validation Commands.

---

## Validation Commands

### Level 1: Token coverage
```bash
# every vendored token name appears in docs/index.html :root (canonical or alias)
for t in $(grep -oE '^\s*--[a-z0-9-]+' docs/design/tokens/colors.css docs/design/tokens/spacing.css docs/design/tokens/typography.css | grep -oE '\-\-[a-z0-9-]+' | sort -u); do
  grep -q -- "$t" docs/index.html || echo "MISSING: $t"
done
```
**EXPECT**: no `MISSING:` lines.

### Level 2: Unit tests
```bash
npx vitest run --reporter=dot
```
**EXPECT**: all pass (current baseline 666 + new design-tokens specs).

### Level 3: e2e
```bash
cd tests/e2e && npx playwright test
```
**EXPECT**: green (or no new failures vs baseline).

### Level 4: Browser (both themes) — MANDATORY for this feature
Chrome DevTools at 127.0.0.1:7432:
- [ ] Cues / Library / Discover render correctly in **light**
- [ ] Toggle `html.dark` → all three render correctly in **warm stone** dark
- [ ] Score chip renders `Mix NN/100` in mono
- [ ] Cue badges A–H look right in both themes
- [ ] Re-probe: every `.track-card` is exactly 160px (TASK-033)
- [ ] Sticky top bar + track header still stick on scroll (TASK-037)
- [ ] Screenshots of all three tabs in BOTH themes sent to user

---

## Acceptance Criteria
- [ ] Level-1 token coverage: 0 missing
- [ ] Vitest + e2e green, no regressions
- [ ] Score chip mono; cue washes token-derived; both themes correct
- [ ] Card 160px + sticky invariants intact
- [ ] Both-theme screenshots delivered

## Risks and Mitigations
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Adding a token accidentally overrides an in-use value | LOW | MED | Additive only; never redefine existing names; grep before each value |
| color-mix wash differs visibly from old rgba | LOW | LOW | Compare side-by-side in light; dark is strictly improved |
| Card height invariant broken | LOW | HIGH | Re-probe all cards = 160px in Phase 3 |
| Dark-theme regression from new `--glass-bg`/`--shadow-lg` | MED | MED | Screenshot both themes; these tokens are additive, only used where wired |

## Notes
The design system was reverse-engineered from this file, so Phase 1 is near-invisible
by construction. The value is completeness (future `var(--token)` work) + the two
small rule-3/consistency fixes. Engine: `prp-implement` (manual) with live both-theme
verification — chosen over Ralph because visual correctness needs human judgment.

*Confidence: 9/10 for one-pass success — bounded, additive, well-grounded; only risk is visual nuance, mitigated by both-theme screenshots.*
