# AutoCue — Design System

**AutoCue** automatically places hot cues on every track in a Rekordbox 7 library — and
layers on deep DJ intelligence: track energy, mixability, classification, similar-track
discovery, transition scoring, an automatic set builder, library health checks, and
new-release discovery. It ships as a **web app** (drop in a Rekordbox XML, download one
with cues injected), a **local FastAPI server + web UI** (reads/writes `master.db`
directly and unlocks every intelligence feature), and a **Python CLI**.

This design system captures the visual language of that product so new screens, marketing
pages, decks, and prototypes look like they shipped from the same team.

---

## Sources

This system was reverse-engineered from the real AutoCue codebase, which is the single
source of truth for every token, component, and screen here:

- **Codebase:** `AutoCue/` (attached local folder). The entire UI lives in one file —
  `AutoCue/docs/index.html` (~14k lines, no framework, no build step). All tokens below
  were lifted verbatim from its `:root` / `html.dark` blocks.
- **Feature docs:** `AutoCue/docs/FEATURES.md`, `AutoCue/README.md`.
- **GitHub:** [github.com/HenriGeorge/AutoCue](https://github.com/HenriGeorge/AutoCue) —
  the upstream repo. *(Not reachable during authoring; if you have access, browse
  `docs/index.html` and `docs/FEATURES.md` to extend this system with anything not yet
  captured here.)*
- **Live web app:** https://henrigeorge.github.io/AutoCue/

> The product is **a single surface** — the same web UI is served both as the hosted
> GitHub Pages app and by the local server. There is no separate marketing site, mobile
> app, or docs portal with distinct styling. So this system has **one UI kit: the AutoCue
> web app.**

---

## CONTENT FUNDAMENTALS

**Voice: expert DJ-tool, plain and exact.** AutoCue talks like a knowledgeable booth
partner, not a marketer. Copy is dense, technical, and confident, but never hypey.

- **Second person, imperative.** Instructions address the user directly: "Drop your
  `rekordbox.xml`", "Click **Preview cues**", "Rekordbox must be closed before applying."
  Buttons are verbs: *Preview cues*, *Apply to Rekordbox*, *Fix phrase-quality tracks*,
  *Find duplicates*, *Build set*.
- **First person is rare** — only the product naming itself in passing ("AutoCue reads
  Rekordbox's own analysis data"). Never "I" / "we" marketing voice.
- **Numbers are first-class and exact.** Scores are always `0–100` with a slash
  (`Mix 72/100`, `Library Health: 78/100`), BPM to one decimal (`124.5 BPM`), keys in
  Camelot (`8A`, `8A→8B`), confidence as words (`High` / `Medium` / `Low` / `Auto` / `—`).
  Real values, never rounded-for-show placeholders.
- **DJ domain vocabulary, used correctly.** Mix-in / mix-out, phrase, drop, build, break,
  beatgrid, hot cue, memory cue, half-time, harmonic, EQ-kill, beatmatch, crate. The copy
  assumes the reader is a DJ.
- **Casing.** Sentence case for everything conversational. UPPERCASE + wide tracking for
  tiny eyebrow/section labels (`ADVANCED`, `SOURCE`). Title-ish for tab names (*Cues*,
  *Library*, *Discover*).
- **Tone of guidance is reassuring about safety.** Anything destructive is spelled out:
  "A backup of your library is created before the first delete", "rolls back
  automatically on any failure", "Dry run (preview) is on by default". Risk is never
  hidden.
- **Explanations are human-readable strings, not jargon dumps.** Transition reasons read
  like a person talking: *"0.5 BPM difference — perfect"*, *"8A→8B — parallel (same
  number)"*, *"Energy drops slightly (29%)"*. Mixing tips are practical: *"Nudge pitch
  +5.1 BPM — blend over 8–16 bars"*, *"EQ-kill lows/mids before incoming lands"*.
- **Em-dashes and arrows carry meaning.** `→` for transitions/flows ("Intro → Verse",
  "warmup → build → peak"), `—` to append qualifiers.
- **Emoji are used sparingly and functionally** — as iconographic glyphs in front of a
  concept, never decoration. ✨ Phrase analysis, ≈ Similar, ⚓ anchor track, 💡 mixing
  tip, ♪ beat-grid filter, 🔌 audio-available filter, 💤 snooze, ★ keeper, ℹ / ⓘ info,
  🔁 resurfaced. Keep this restraint — no emoji in headings or prose.

---

## VISUAL FOUNDATIONS

The aesthetic is **"ElevenLabs clean" in light mode, warm-stone studio in dark mode.**
Calm neutral canvas, generous whitespace, one confident green accent, and a strict
discipline about where color is allowed to appear.

### Color
- **Two full themes**, toggled on `html.dark`. Light is cool/neutral (`#fafafa` bg, pure
  white surfaces, `#e8e8e8` borders). Dark is **warm stone** (`#0c0a09` bg, `#1c1917`
  surfaces, `#292524` borders) — the Tailwind *stone* family, not blue-grey.
- **One brand accent: green.** `#159a05` (light) / `#28e214` (dark). It is reserved for
  *primary meaning only* — the brand mark, BPM chips, active/selected affordances, focus
  rings, success, the live-data accents. Crucially, **the primary CTA is NOT green** —
  it's a black pill (light) / white pill (dark). Green is the signal color; the action
  button is ink. Don't spend green on neutral toggles (sort buttons use a neutral active
  state on purpose).
- **A full DJ cue palette (A–H)** — eight distinct hues for hot-cue slots 1–8
  (green, blue, cyan, amber, orange, red, magenta, purple), each rendered as a 1.5px
  bordered chip with an 8%-opacity wash of its own color. Plus a separate set of
  Rekordbox track-color dots.
- **Tinted washes, not solid fills.** Accent backgrounds are ~8% opacity of the accent
  over the surface (`rgba(40,226,20,.08)`), keeping the canvas neutral.
- **Semantic colors** stay muted: danger `#e74c3c` (used as outline/ghost, rarely solid),
  warn amber `#f0801a`/`#ffa000`, rating gold `#f0b429`.

### Type
- **Inter** for all UI text (400/500/600/700). **JetBrains Mono** for every piece of
  numeric or technical data — BPM, Camelot keys, timestamps, cue names, file paths, code.
  This sans/mono split is a core identity signal: if it's a measured value, it's mono.
- Compact scale: body 15px, base 14px, small 12px, micro 10–11px for chips. Section/page
  titles ~28px, weight 600, slight negative tracking (`-0.01em`). Tiny eyebrow labels are
  10–12px UPPERCASE with `.08em` tracking.
- Track names are 15px/700 with `-0.2px` tracking and truncate with ellipsis.

### Spacing & layout
- 4-based scale (4/8/12/16/24/40). The app is a **single centered column**, `max-width:
  900px`, generous side padding, lots of breathing room — not a dense dashboard grid.
- Prefer flex/grid with `gap`. Chips, badges, and toolbar controls are laid out as wrap-
  flex rows.

### Corners, borders, cards
- **Radii are intentional by scale:** 4px for data chips (cue/BPM/key), 8px for
  inputs & controls, 12px for panels & drop zones, 16px for elevated cards and the
  track-list container, and **999px pills for every button, tab, and tag.** The pill
  button is a defining trait.
- **Cards** = `--surface` fill, 1px `--border`, 16px radius, soft `--shadow-sm`; on hover
  the border goes to `--border-hover` and shadow lifts to `--shadow-md`. Light, airy,
  never heavy.
- Borders do the structural work; shadows are subtle and only used for elevation/hover,
  never as decoration. (Some track cards carry a colored 3px left border keyed to the
  Rekordbox track color — that's a data signal, not a default card style.)

### Backgrounds, transparency & blur
- Flat neutral fills — **no gradients, no textures, no illustrations** as background.
- **Glass appears only on chrome in motion:** the sticky top bar and sticky track header
  switch to a `color-mix` translucent fill + `backdrop-filter: blur(14–16px)` once you
  scroll. Resting state is solid. The floating bottom action bar and toasts also use blur.

### Motion
- **Snappy and restrained.** Hovers `.15s`; buttons/tabs `.18–.22s ease`; chrome `.3s`.
- Entrances are a gentle fade-up (`opacity 0→1` + `translateY(10px→0)`, ~.35s ease-out).
  Bars fill with a cubic ease (`fillBar`). Score chips count up 0→value over ~600ms.
- Buttons **lift on hover** (`translateY(-1px)` + deeper shadow) and settle on active
  (`translateY(0)`). A subtle ripple plays on primary press. Cue badges scale `1.08` on
  hover. `prefers-reduced-motion` is respected (transitions dropped on the action bar).
- No infinite/looping decorative animation.

### Hover & press states
- **Buttons:** hover lifts + darkens/lightens slightly; active returns to rest.
- **Ghost/secondary controls:** hover brightens border (`--border-hover`) and text
  (`--muted → --text`); accent controls hover toward green border + green text.
- **Chips/tags:** hover `brightness(1.2)` + `translateY(-1px)`.
- **Press:** mostly a return-to-flat (shadow + transform reset); destructive confirms have
  their delete button briefly disabled so a stray Enter can't fire.

---

## ICONOGRAPHY

AutoCue uses **inline line icons in the Feather / Lucide style** — `viewBox="0 0 24 24"`,
`fill="none"`, `stroke="currentColor"`, `stroke-width: 2`, `stroke-linecap/linejoin:
round`. They inherit text color via `currentColor` and sit at 13–24px. There is **no icon
font and no PNG icon set** — every icon is a small hand-written SVG in the markup.

- **The brand mark** is a 5-bar equalizer / cue-marker glyph drawn in green stroke
  (`assets/logo-mark.svg`), shown inside a 32px rounded `--surface-2` tile next to the
  *AutoCue* wordmark.
- **Playback** uses solid-fill glyphs (`fill="currentColor"`) — a play triangle
  (`polygon 5,3 19,12 5,21`) and pause bars — distinct from the stroked UI icons.
- **Functional emoji** stand in for a handful of concepts as inline glyphs (see Content
  Fundamentals): ✨ ≈ ⚓ 💡 ♪ 🔌 💤 ★ ℹ ⓘ 🔁. These are intentional and on-brand in this
  product; keep them rare and meaning-bearing.
- **Recommendation for new work:** match the Feather/Lucide line style. The closest
  CDN-available match is **[Lucide](https://lucide.dev)** (same 24px grid, 2px round
  stroke) — use it directly when you need icons beyond the few the app hand-rolls. This
  is a *substitution* (the app inlines its own SVGs rather than importing a set); flagged
  here so you can swap to exact app SVGs if pixel-fidelity matters.

---

## VISUAL FOUNDATION — FONT NOTE / SUBSTITUTION

Inter and JetBrains Mono are loaded from **Google Fonts** (`tokens/fonts.css`), matching
how the app loads Inter via `<link>`. The app references JetBrains Mono in `--font-mono`
but only system-loads it; this system pins it from Google Fonts for consistent rendering.
**No font binaries are bundled.** If you need self-hosted/offline fonts, drop `.woff2`
files into `assets/fonts/` and replace the `@import` with `@font-face` rules.

---

## INDEX / MANIFEST

**Root**
- `styles.css` — the single global entry point (an `@import` manifest). Consumers link this.
- `readme.md` — this guide.
- `SKILL.md` — Agent-Skill front-matter so this folder works as a downloadable skill.

**`tokens/`** — design tokens (all reachable from `styles.css`)
- `fonts.css` — Inter + JetBrains Mono (Google Fonts).
- `colors.css` — light + dark themes, brand green, cue palette, Rekordbox dots, semantics.
- `typography.css` — font stacks, type scale, weights, line-heights, tracking.
- `spacing.css` — spacing / radius / layout / motion tokens.
- `base.css` — element resets, focus ring, scrollbars, shared keyframes.

**`assets/`**
- `logo-mark.svg` — the green equalizer brand mark.

**`components/`** — reusable React primitives (see each `.prompt.md`)
- `core/` — `Button`, `Badge`, `Chip`, `Card`, `Input`, `Select`, `Toggle`.
- `dj/` — AutoCue-specific data primitives: `CueBadge`, `BpmChip`, `KeyChip`,
  `ScoreChip`, `CategoryBadge`, `EnergySparkline`, `TrackCard`.

**`ui_kits/web-app/`** — the AutoCue web app UI kit (interactive recreation).

**Foundation specimen cards** live alongside the tokens/components and populate the
*Design System* tab (groups: Type, Colors, Spacing, Brand, Components).

---

## How to use this system

1. **Link `styles.css`.** Everything is CSS custom properties — never hardcode a hex;
   reference `var(--green)`, `var(--surface)`, `var(--cue-a)`, etc.
2. **Respect the green discipline.** Green = signal/brand. The primary action is the
   ink pill (`var(--ink)` bg / `var(--on-ink)` text). Don't make CTAs green.
3. **Mono for data.** Any measured value (BPM, key, time, score, path) is `--font-mono`.
4. **Pills for actions.** Buttons, tabs, and tags are fully rounded.
5. **Two themes.** Test both — toggle `html.dark`. Dark is warm stone, not blue-grey.
