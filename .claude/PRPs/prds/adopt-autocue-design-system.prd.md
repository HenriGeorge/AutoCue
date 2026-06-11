# Adopt the AutoCue Design System in `docs/index.html`

## Problem Statement

The AutoCue web app (`docs/index.html`) carries an informal set of CSS custom
properties that **predate** the now-formalized AutoCue design system (vendored at
`docs/design/`). The live `:root`/`html.dark` blocks are missing tokens the system
defines (e.g. `--ink`/`--on-ink`, `--green-wash`/`--green-ring`, the Rekordbox
`--rb-*` dots, `--radius-xl`, motion-easing tokens, `--text-md`/`--text-2xl`), and
a few components violate the system's five rules. The cost: every new UI change
either reinvents tokens or hardcodes hexes, and the product slowly drifts off-brand.

## Evidence

- The vendored system's `readme.md` states tokens were "lifted **verbatim** from
  `docs/index.html`'s `:root`/`html.dark` blocks" — so the system is the app's own
  language, formalized. Adopting it is reconciliation, not a new look.
- The live file defines **38** custom properties; the design system defines a
  superset (action ink, green washes/rings, Rekordbox dots, `--shadow-lg`,
  `--glass-bg`, full radius scale incl. 16px, motion easing/durations, type scale
  incl. 15px body + 28px titles, weight/line-height/tracking tokens).
- Naming mismatches: app uses `--surface2` / `--font` / `--mono`; system uses
  `--surface-2` / `--font-sans` / `--font-mono`.
- The "five rules" (two themes, green=signal-not-CTA, mono-for-data, pill actions,
  light&airy) are documented in `docs/design/README.md` + `SKILL.md`; some live
  surfaces deviate (to be audited in Phase 2).

## Proposed Solution

A bounded, two-phase reconciliation of `docs/index.html` to the vendored tokens,
executed manually (`prp-implement`) with live Chrome DevTools verification in **both
themes** at each checkpoint. **Phase 1** completes the token layer (add missing
tokens, add design-system names as aliases over the app's existing canonical names —
no mass rename). **Phase 2** corrects clear rule violations (green primary CTAs →
ink pill, non-mono measured values → JetBrains Mono, non-pill buttons/tabs/tags →
`--radius-pill`, cue chips → 4px + 8% color wash). Drift-correction is allowed: where
the app violates a rule, the fix changes the rendered output on purpose.

## Key Hypothesis

We believe formalizing the token layer and correcting rule violations will make the
UI consistently on-brand and make future UI work faster (reference `var(--token)`,
never hardcode). We'll know we're right when every design-system token exists in
`:root`, the five rules hold on the audited surfaces, both themes render correctly,
and the full test suite stays green with no golden-path regressions.

## What We're NOT Building

- **Token renames** — app names (`--surface2`, `--font`, `--mono`) stay canonical;
  system names are added as aliases. Reason: mass find-replace across a ~12.8k-line
  single file is pure churn and regression risk for zero visual gain.
- **Exhaustive component-by-component audit** of every modal/tab against each
  `.prompt.md` — deferred. Phase 2 targets clear, high-signal violations only.
- **React components / UI-kit import** — the app is a single no-build HTML file; the
  vendored `.jsx` are design-intent references, not code to ship.
- **Structural/layout redesign** — the Virtualizer fixed-160px card (TASK-033) and
  sticky-layout invariants (TASK-037) are preserved exactly.

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Token coverage | 100% of `docs/design/tokens/*` present in `:root`/`html.dark` (as canonical or alias) | grep/diff vs vendored tokens |
| Five-rule adherence (audited surfaces) | 0 known violations on primary CTAs, data values, action pills, cue chips | manual audit checklist + Chrome DevTools |
| Theme correctness | Light + dark both render correctly across Cues/Library/Discover | screenshots both themes |
| No regression | Vitest + Playwright e2e green; card height invariant intact (all 160px) | fresh `npm test` + e2e + DevTools probe |

## Open Questions

- [ ] Are there primary CTAs currently rendered green that should become ink? (audit in Phase 2 start)
- [ ] Any measured values currently in sans that should be mono? (audit in Phase 2 start)
- [ ] Does adding `--glass-bg`/`--shadow-lg` change any existing sticky-chrome rendering? (verify on scroll)

---

## Users & Context

**Primary User**
- **Who**: The maintainer (and future Claude sessions) doing UI work in `docs/index.html`.
- **Current behavior**: Hand-picks hexes / reuses ad-hoc vars; new UI risks drift.
- **Trigger**: Any new UI feature or restyle ("add a chip", "style this panel").
- **Success state**: Reach for `var(--token)`; the five rules are obvious and enforced.

**Job to Be Done**
When adding or restyling UI in the AutoCue web app, I want a complete, formalized
token layer and clear brand rules, so I can build on-brand surfaces fast without
reinventing or hardcoding styles.

**Non-Users**
End DJs don't see tokens; the CLI and Python server have no UI. Out of frame.

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | Complete `:root`/`html.dark` token set (canonical + aliases) | Foundation; everything references it |
| Must | Primary CTAs use the ink pill, not green | Rule 2 — the defining brand discipline |
| Must | Measured values (BPM/key/time/score/path/cue name) in mono | Rule 3 — core identity signal |
| Must | Buttons/tabs/tags fully rounded; data chips 4px | Rule 4 — the pill is a signature trait |
| Should | Cue slot chips = 4px bordered + 8% color wash A–H | Consistency with `CueBadge.prompt.md` |
| Should | Add `--green-wash`/`--green-ring` usage to active rows/focus | Rule 2 wash discipline |
| Could | Glass blur on sticky chrome via `--glass-bg` | Already partially present; formalize |
| Won't | Token renames / full component audit / React import | See "NOT Building" |

### MVP Scope

Phase 1 (token layer) alone validates the foundation hypothesis. Phase 2 makes the
brand discipline visible. Both ship in one PR.

### User Flow

Maintainer opens `docs/index.html` → all tokens resolve from the vendored set →
adds a surface using `var(--ink)`, `var(--font-mono)`, `var(--radius-pill)` → it
looks on-brand with no guesswork.

---

## Technical Approach

**Feasibility**: HIGH — the system is the app's own formalized language; changes are
localized to the `<style>` block and targeted component CSS/markup.

**Architecture Notes**
- Single-file, no-build app: all edits land in `docs/index.html`'s `<style>` +
  the relevant component builders.
- Aliases keep existing usages working; new tokens are additive.
- Preserve TASK-033 (160px card) and TASK-037 (sticky/fixed layout) invariants.

**Technical Risks**

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Drift-correction changes a load-bearing layout | L | Verify each change in Chrome DevTools, both themes; keep diffs surgical |
| Adding tokens shifts unintended elements | L | Additive tokens + alias; grep usages before changing a value |
| Card-height invariant broken by chip radius/padding | M | Re-probe all cards = 160px after Phase 2 |
| Dark theme regressions | M | Screenshot both themes at every checkpoint |

---

## Implementation Phases

| # | Phase | Description | Status | Parallel | Depends | PRP Plan |
|---|-------|-------------|--------|----------|---------|----------|
| 1 | Token layer | Add missing tokens + design-system aliases to `:root`/`html.dark`; reconcile value drift | in-progress | - | - | [plan](../plans/adopt-autocue-design-system.plan.md) |
| 2 | Adherence fixes | Mono score chip + token-derived cue washes (CTAs/pills already compliant) | pending | - | 1 | [plan](../plans/adopt-autocue-design-system.plan.md) |
| 3 | Verify | Fresh Vitest + e2e + Chrome DevTools both themes; invariant re-probe | pending | - | 2 | [plan](../plans/adopt-autocue-design-system.plan.md) |

### Phase Details

**Phase 1: Token layer**
- **Goal**: `:root`/`html.dark` is a complete superset of `docs/design/tokens/*`.
- **Scope**: Add `--ink`/`--ink-hover`/`--on-ink`, `--green-wash`/`--green-ring`,
  `--rb-*` dots, `--radius-xl` (16px), `--shadow-lg`, `--glass-bg`, motion
  (`--ease-*`/`--dur-*`), type (`--text-md` 15px, `--text-2xl` 28px), weight/
  line-height/tracking tokens, `--content-max`. Add aliases `--surface-2`,
  `--font-sans`, `--font-mono`. Reconcile any value drift.
- **Success signal**: grep shows every vendored token resolvable; app renders unchanged.

**Phase 2: Adherence fixes**
- **Goal**: The five rules hold on primary CTAs, measured values, action pills, cue chips.
- **Scope**: Audit + fix green primary CTAs → ink pill; non-mono measured values →
  `--font-mono`; non-pill buttons/tabs/tags → `--radius-pill`; cue chips → 4px +
  8% wash. Surgical, verified per change.
- **Success signal**: audit checklist 0 violations; both themes look right.

**Phase 3: Verify**
- **Goal**: Fresh evidence, no regressions.
- **Scope**: `npm test` (Vitest) + Playwright e2e + Chrome DevTools light & dark
  across Cues/Library/Discover; re-probe all cards = 160px.
- **Success signal**: green suites + clean screenshots + invariant intact.

### Parallelism Notes

Sequential — Phase 2 depends on Phase 1 tokens; Phase 3 verifies both. No worktree split.

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| Scope | Tokens + adherence fixes | Token-only; full audit | Adopts the system meaningfully without unbounded churn |
| Token names | Keep app names + alias | Mass rename | Avoid 12.8k-line find-replace churn/risk for zero visual gain |
| Visual change | Allow drift-correction | Pixel-preserve | Fixing violations IS the point; verify each visually |
| Engine | `prp-implement` (manual) | `prp-ralph` | Visual correctness needs human/both-theme judgment, not just test gates |

---

## Research Summary

**Market Context**: N/A — internal design-language reconciliation; the "market" is
the app's own formalized system (`docs/design/`).

**Technical Context**: Tokens live in `docs/index.html` `<style>` `:root`/`html.dark`
(38 props today). Vendored canonical set in `docs/design/tokens/{colors,typography,
spacing,base,fonts}.css`. Component design-intent in
`docs/design/components/**/*.prompt.md`. Invariants to preserve: TASK-033 (160px
card), TASK-037 (sticky/fixed layout).

---

*Status: DRAFT — ready for `/prp-plan`*
