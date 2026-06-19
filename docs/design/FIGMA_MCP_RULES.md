# AutoCue — Figma MCP Integration Rules

> Rules for translating Figma designs into AutoCue code (and reading code back into
> Figma) via the Model Context Protocol. AutoCue is a **no-build, framework-less**
> vanilla-JS web app — so the usual "generate a React component" Figma codegen flow does
> **not** apply here. Read this before any `get_design_context` / `use_figma` round-trip.
> Companion to `docs/design/README.md` (the human design guide) and the root `CLAUDE.md`
> "Design reference" section.

---

## TL;DR — the five hard rules (Figma → AutoCue)

1. **No framework, no build.** Do NOT emit React/JSX/Vue/Tailwind-config or a bundler step.
   Output = plain semantic HTML + a CSS class in `docs/css/app.css` + (if interactive) a
   vanilla-JS DOM builder. The `.prompt.md` files' JSX is *illustration only*, never shipped.
2. **Tokens are CSS custom properties, referenced — never inlined.** Every colour, size,
   radius, font is `var(--token)`. **Never** paste a Figma hex/px literal into a rule.
   If Figma surfaces a value with no matching token, STOP and reconcile the token first.
3. **Two source-of-truth layers.** Runtime truth = `docs/css/app.css` `:root` / `html.dark`.
   Canonical mirror = `docs/design/tokens/*.css`. A new token must land in **both**, byte-equal.
4. **The five brand rules win** (see `CLAUDE.md`): two themes · green = signal only (CTA is the
   ink pill, never green) · mono for all data · pills for actions · light & airy. A Figma frame
   that violates these is wrong — flag it, don't reproduce it.
5. **Honour invariants:** `prefers-reduced-motion`-gate every animation; never hardcode hexes;
   never adopt Figma's inner-scroll containers (breaks the Virtualizer / sticky bars — TASK-033/037).

---

## 1. Token Definitions

**Two layers, manually mirrored (no Style Dictionary / no transform pipeline).**

| Layer | Path | Role |
|---|---|---|
| **Runtime source of truth** | `docs/css/app.css` `:root` (light) + `html.dark` (dark) | What the app actually renders. Plain CSS custom properties. |
| **Canonical mirror (the design system)** | `docs/design/tokens/*.css` | Vendored, reverse-engineered FROM the `:root` block. The Figma-facing token set. |
| **Manifest** | `docs/design/styles.css` | `@import` list only — consumers link this one file. |

```css
/* docs/design/styles.css — import order is load-bearing (tokens before base) */
@import url('./tokens/fonts.css');       /* Google Fonts: Inter + JetBrains Mono */
@import url('./tokens/colors.css');      /* surfaces, green, ink, semantic, cue A–H, zones */
@import url('./tokens/typography.css');  /* --font-sans/-mono, --text-* scale, weights */
@import url('./tokens/spacing.css');     /* --sp-*, --radius-*, --ease-*/--dur-*, motion */
@import url('./tokens/base.css');        /* reset + element defaults (button=pill, data=mono) */
```

**Format:** CSS custom properties only. No JSON tokens, no `tokens.json`, no Tailwind theme
extension. Theming is a `html.dark` **class** toggle (not media query) — every colour token is
redefined under `html.dark`.

**⚠️ Naming aliases — the one gotcha.** The runtime (`app.css`) and the mirror use slightly
different *primary* names; the mirror provides aliases:

| Runtime (`app.css`) | Canonical (`tokens/`) | Note |
|---|---|---|
| `--surface2` | `--surface-2` | aliased `--surface-2: var(--surface2)` in app.css |
| `--font` | `--font-sans` | aliased |
| `--mono` | `--font-mono` | aliased |

**When writing new app code, prefer the canonical `--surface-2` / `--font-sans` / `--font-mono`.**

**Adding a token from Figma (`get_variable_defs`):** add it to `app.css` `:root` AND `html.dark`,
then mirror byte-equal into the matching `docs/design/tokens/*.css` file. Example (the Nightboard
zone tokens did exactly this):

```css
/* docs/css/app.css  AND  docs/design/tokens/colors.css — must match byte-for-byte, both themes */
:root      { --zone-peak: rgba(192, 32, 16, .05); }
html.dark  { --zone-peak: rgba(224, 48, 30, .06); }  /* note: .06 in dark, not .05 */
```

**Key token groups** (all in `tokens/colors.css` / `spacing.css` / `typography.css`):
- Surfaces/neutrals: `--bg --surface --surface-2 --surface-card --border --border-hover`
- Brand green (SIGNAL ONLY): `--green --green-dim --green-wash --green-ring`
- Action ink (the CTA): `--ink --ink-hover --on-ink`
- Semantic: `--danger --warn --warn-amber --error --rating`
- Cue palette A–H: `--cue-a … --cue-h` · Nightboard zones: `--zone-warmup/-build/-peak/-closing`
- Radii: `--radius-sm 4 / -md 8 / -lg 12 / -xl 16 / -pill 999`
- Type: `--text-xs 10 … --text-2xl 28`; `--fw-*`; `--lh-*`; `--tracking-*`
- Motion: `--ease-out/-enter/-fill`, `--dur-fast .15 / -btn .18 / -chrome .3`

---

## 2. Component Library

**There is no runtime component framework.** Components exist in two non-shipping forms +
one shipping form:

| Form | Path | What it is |
|---|---|---|
| **Design-intent spec** | `docs/design/components/{core,dj}/*.prompt.md` | Markdown + *illustrative* JSX showing variants/props/intent. **NOT compiled** — a spec for humans/Figma, never imported. |
| **HTML exemplar** | `docs/design/components/*/*.card.html` | Static rendered reference markup. |
| **Shipping component** | `docs/css/app.css` classes + vanilla-JS DOM builders | The real thing: a CSS class + a JS function that builds the DOM node. |

Core components: `Button Card Chip Badge Input Select Toggle`.
DJ components: `TrackCard BpmChip KeyChip CueBadge CategoryBadge ScoreChip EnergySparkline`.

```jsx
// docs/design/components/core/Button.prompt.md — ILLUSTRATIVE ONLY (no React in the app)
<Button variant="primary">Apply to Rekordbox</Button>   // ink pill — the CTA
<Button variant="secondary">Preview cues</Button>        // surface + border
<Button variant="danger">Delete non-keepers</Button>     // red outline
```

```js
// The SHIPPING equivalent — a vanilla DOM builder (docs/js/v2/workbench/inspector.js)
function _chip(text, mono, accent) {
  const s = document.createElement('span');
  s.className = 'wb-insp-chip' + (mono ? ' mono' : '') + (accent ? ' accent' : '');
  s.textContent = text;            // textContent — never innerHTML for untrusted data
  return s;
}
```

**Figma → component mapping:** translate a Figma component to **(a)** a CSS class in `app.css`
following the `.prompt.md` variant/size contract, and **(b)** a DOM-builder function if it needs
behaviour. Do not generate a JSX/React component file. Match an existing `.prompt.md` spec where one
exists; if a Figma component has no `.prompt.md`, add the spec there too.

**Preview/storybook:** `docs/design/preview/index.html` (+ `preview/components.css`) renders the
system; `docs/design/mockups/design-{A..H,Z}.html` are full-screen design explorations.

---

## 3. Frameworks & Libraries

- **UI framework: NONE.** No React, Vue, Svelte. Plain DOM. Two JS layers:
  - **Classic scripts** `docs/js/01-core.js … 08-set-builder-boot.js` — load in order, share
    globals via the global scope (NOT modules).
  - **Native ES modules** `docs/js/v2/**` — rooted at `js/v2/main.js`, loaded `<script type="module">`.
    Interop contract: **v2 reads legacy via `window.*` (e.g. `window.ACBridge`); legacy never imports v2.**
- **Styling libs:** plain CSS custom properties. **Tailwind is present via CDN** but constrained —
  `preflight` disabled, `darkMode: 'class'` — used only for occasional utility classes; **tokens are
  CSS vars, not Tailwind theme**. Do not add a `tailwind.config` / PostCSS build.

  ```html
  <!-- docs/index.html -->
  <script src="https://cdn.tailwindcss.com"></script>
  <script>tailwind.config = { corePlugins: { preflight: false }, darkMode: 'class' }</script>
  ```
- **Build system / bundler: NONE.** "No build step" is a hard architectural constraint (no bundler,
  no transpile, no framework — ever). `package.json` exists **only** for Vitest dev-testing.
- **Script load order** (`docs/index.html`): classic `01→08`, then `<script type="module" src="js/v2/main.js">`.

---

## 4. Asset Management

- **Minimal, no pipeline.** No image-optimisation step, no sprite generation, no asset bundler.
- **Fonts** load from Google Fonts CDN (`tokens/fonts.css` `@import` + a `<link>` in `index.html`):
  Inter (400/500/600/700) + JetBrains Mono. To self-host, swap the `@import` for `@font-face` →
  local `.woff2` (documented in `fonts.css`).
- **Runtime imagery** is data, not design assets — e.g. `<img id="mini-artwork">` is track artwork
  pulled at runtime; album art via the `jsmediatags` CDN lib. There are **no** bundled product
  images/illustrations (brand rule #5: "no gradients/textures/illustrations").
- **CDN configs:** Google Fonts, `cdn.tailwindcss.com`, `cdnjs` jsmediatags. That's the full set —
  do not add new CDNs without cause.
- **Figma image exports:** AutoCue's flat, illustration-free aesthetic means you almost never import
  raster assets from Figma. If a design needs an icon, draw it as inline SVG (§5), don't export a PNG.

---

## 5. Icon System

- **Inline SVG only.** No icon font, no SVG sprite sheet, no `@svgr`, no icon package.
- Reusable glyphs are **string constants in JS** (build the icon by setting `innerHTML`/`textContent`):

  ```js
  // docs/js/01-core.js
  const SVG_PLAY  = `<svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>`;
  const SVG_PAUSE = `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`;
  ```

- One-off icons are **inline `<svg>` directly in `docs/index.html`** (≈12 today).
- **Convention:** `viewBox="0 0 24 24"`, `fill="currentColor"` (so icons inherit text colour and
  theme automatically), sized 14–16px when leading a button (per `Button.prompt.md`). No `width`/
  `height` attrs — size via CSS. **Naming:** `SVG_<NAME>` SCREAMING_SNAKE for JS constants.
- **Figma → icon:** copy the SVG path/polygon, wrap in the 24×24 `currentColor` template, add a
  `SVG_*` constant (reused) or inline it (one-off). Never introduce an icon-font dependency.

---

## 6. Styling Approach

- **Method:** a single global stylesheet, `docs/css/app.css` (~3,740 lines), of **plain CSS with
  custom properties**. No CSS Modules, no Styled Components, no CSS-in-JS, no SCSS.
- **Global base** lives in `docs/design/tokens/base.css` (reset; `button{border-radius:--radius-pill}`;
  `code,kbd,samp,pre{font-family:--font-mono}`; green focus ring). The app's runtime base is the top
  of `app.css`.
- **Theming = `html.dark` class** (not `prefers-color-scheme`); every colour token re-declared under
  `html.dark`. Always test both themes.
- **Responsive:** this is a **data-dense desktop tool**, not a fluid marketing site — fixed
  `--content-max: 900px` centre column, **virtualized** track list (fixed card height — TASK-033),
  `position: sticky`/`fixed` chrome anchored to the document (TASK-037). **Do NOT** adopt a Figma
  frame's inner-scroll container or variable card heights — they break virtualization.
- **Motion:** use `--ease-*` / `--dur-*` tokens; **every animation must be `prefers-reduced-motion`
  gated.** Example pattern:

  ```css
  .wb-insp-tx-sec { animation: ac-fade-slide-in var(--dur-btn) var(--ease-enter); }
  @media (prefers-reduced-motion: reduce) { .wb-insp-tx-sec { animation: none; } }
  ```
- **Class naming (observed):** workbench v2 uses `wb-` prefixes (`wb-insp-*`, `wb-dupes-*`,
  `wb-toolbar-*`), Nightboard `nb-*`, set-builder `sb2-*`, Discover `disc-v2-*`, library `lh-*`.
  Follow the surface's existing prefix; keep styling in classes (no inline `style=` font/padding —
  fold into a class, e.g. `.wb-toolbar-sm`).

---

## 7. Project Structure

```
AutoCue/
├─ docs/                         # the web app (GitHub-Pages root; NO build step)
│  ├─ index.html                 # single-entry markup (~14k lines); script load order matters
│  ├─ css/app.css                # ALL runtime styles + runtime token :root/html.dark (source of truth)
│  ├─ js/
│  │  ├─ 01-core.js … 08-*.js     # classic scripts, ordered, shared globals (incl. window.ACBridge)
│  │  └─ v2/                      # native ES modules (the 2.0 "Crate Console" workbench)
│  │     ├─ main.js               # module root
│  │     ├─ palette.js commands.js fuzzy.js status-sentence.js restore-sheet.js
│  │     ├─ workbench/            # shell, rail, inspector, library, duplicates, discover, proposals
│  │     └─ nightboard/           # mode, set-model, canvas, joint-popover, tray
│  ├─ design/                     # ← THE DESIGN SYSTEM (Figma-facing)
│  │  ├─ styles.css               # @import manifest
│  │  ├─ tokens/                  # colors / typography / spacing / fonts / base
│  │  ├─ components/{core,dj}/    # *.prompt.md design-intent + *.card.html exemplars
│  │  ├─ mockups/                 # design-A..H + Z-endstate full-screen explorations
│  │  ├─ preview/                 # the "storybook" (index.html + components.css)
│  │  └─ README.md                # human design guide
│  ├─ FEATURES.md  reference/  manual/
├─ autocue/                       # Python backend (CLI + FastAPI server) — not design-facing
└─ tests/  (web/ vitest · e2e/ playwright)
```

**Feature-organisation pattern (v2 "places"):** each workbench surface is a self-contained ES
module under `docs/js/v2/workbench/` that owns only its rail entry + a centre-pane swap, re-driving
legacy machinery via `window.ACBridge` — it never re-implements fetches or imports legacy. Copy this
pattern for any new surface. Nightboard is a full-bleed **mode** (`body.nb-active`), not a place.

---

## Figma MCP workflow notes (read `design-workflow.md` for the quota rules)

- **Direction:** `get_design_context` / `get_variable_defs` read **Figma → code**; `use_figma` /
  `generate_figma_design` go **code → Figma**. Don't confuse them.
- **Quota:** Figma MCP is ~6 calls/month on Starter — spend it ONLY on the final approved design.
  Iterate for free via the `window.figma` browser API in a logged-in tab, or screenshot the live app
  with Chrome DevTools MCP at `http://127.0.0.1:PORT` (never the blocked Claude-in-Chrome extension on
  localhost). See `local-browser-testing.md`.
- **Codegen target:** when generating from Figma, emit **HTML + an `app.css` class (+ a vanilla DOM
  builder if interactive)** using `var(--token)` — NOT a React component. Reconcile any new token into
  both `app.css` and `docs/design/tokens/` before using it.
- **Verify after import:** changes go through the worktree → PR flow; run the three-leg gate
  (`pytest` · `npm test` · `npx playwright test`) and screenshot both themes before "done".
```
