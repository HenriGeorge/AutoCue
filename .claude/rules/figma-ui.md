# Figma → UI — per-change checklist (Figma MCP integration)

Last updated: 2026-07-10 18:00

Self-contained (this is synced to live; the full _how_ + diagrams live in the repo doc
`docs/FIGMA-UI.md`). GATE 1/GATE 2 (`gates.md`) apply; `CLAUDE.md` wins.

## Pick the bridge by capability (not preference)

- **Write / assemble / read structure / write _literal_ Variables** → **arinspunk bridge**
  (`ClaudeTalkToFigma`, ws `:3055`) — free, unlimited. Relay `set_variable` is **literals-only**
  (no aliases / no modes — verified).
- **Aliases + light/dark Variables** → a **native `figma.variables` plugin run in-file** (imported dev
  plugin, plugin sandbox). ⚠️ `window.figma` from a plain page tab is **unverified** — don't assume it.
- **Semantic codegen / token defs / Code Connect** → **official MCP** (`claude_ai_Figma`) — the only
  three things worth the **metered reads (~6/mo Starter; verify current metering)**. Spend them **only
  on the final, locked design**. Everything else: free path (`gates.md` quota table).

## Reading a design INTO code

1. `get_design_context` for the exact node(s) first.
2. Large/truncated → `get_metadata` for the node map, re-fetch only needed nodes (saves context + reads).
3. `get_screenshot` for the visual reference.
4. Only with BOTH context + screenshot: pull assets, then implement.
5. Output is React+Tailwind = a **representation, NOT final style**. Replace utilities with our shadcn
   tokens; reuse `src/components/ui`; respect our routing/state/data patterns.
6. A returned localhost asset URL → use it directly; do NOT add icon packages or placeholders.
7. Strive for 1:1 visual parity; on conflict prefer design-system tokens, adjust minimally.
8. Validate against the Figma screenshot AND drive the live site (GATE 2) before "done".

## Code Connect first (try it — high leverage, unverified here)

`get_code_connect_suggestions` → review → `send_code_connect_mappings`. Reportedly makes codegen emit
your real components instead of guessing — confirm it actually references `src/components/ui` before
relying on it. Capture the agreed design-system conventions into `rules/figma-ui.md` yourself (there is
no `create_design_system_rules` MCP call).

## Parallel crew on the bridge (if used)

Server-side queue makes parallel agents safe, with two hard rules:

- `set_current_page` is **blocked** in parallel mode.
- Every `create_*`/`set_*` **must pass an explicit `parentId`**. One writer per channel; researchers
  read-only. Connect: `Connect to Figma, channel $FIGMA_CHANNEL` (`join_channel`) — the project's
  stable channel (= repo folder name), exported by cc-worktrees in every pane. The project's Figma
  file is `FIGMA_FILE_KEY` in `.claude/worktrees.conf` (one project = one file = one channel); the
  patched plugin persists a typed channel, so never chase a fresh random id.

## Pushing a preview INTO Figma for sign-off (#104 · #106)

GATE-1 approves a RENDERED design, not a build instruction (#69). To sign off a NEW design beside an
existing baseline — build a dev-only `/preview` route, verify it live (screenshot desktop + mobile), then:

- **Probe before placing (#106):** `get_node_info` every frame's bounds and place in VERIFIED-empty space
  (a new column past max-right, or a verified gap) — NEVER a naive `x = baseline_width + gap` (it collides
  in a tiled file). Re-verify no overlap; loop on collision.
- **Editable frame, not just a PNG (#104):** push an EDITABLE frame (`svg-to-figma`) at those empty coords
  beside the baseline, and **EXCLUDE dev-only chrome** (DEV PREVIEW bar / STATES gallery) from the capture.
  Present the inline screenshot + the editable frame + a delta list.
- Wire into the live route **only after** the user approves those pixels.

## See also

`design-workflow.md` · `gates.md` (quota table) · `docs/FIGMA-UI.md` (full how + mermaid diagrams + reverse leg).
