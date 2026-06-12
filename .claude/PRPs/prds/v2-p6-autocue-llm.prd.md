# AutoCue 2.0 — P6: `AUTOCUE_LLM` opt-in palette assistant (phase PRD)

> **Status: SCOPING / hypothesis-driven.** The program PRD scopes this phase as
> *"design after P2 usage"* (decision #6, phase table P6). This is deliberately a
> **lighter, pre-usage-data PRD**: it frames the work as HYPOTHESES + a decision
> framework + open questions, NOT a committed spec. It locks only the *hard
> constraints* (opt-in, no-cloud-by-default, deterministic palette unchanged) and
> the *interop seam* that already exists; everything else is a proposal to be
> validated against real P2-workbench usage before a plan is cut. Do not treat
> the requirements below as final until the "Decision framework" gate is run.

## Problem statement

The ⌘K palette (P1) routes typed input through two deterministic surfaces only:
ranked **commands** and **track search** (`docs/js/v2/palette.js:25-29` →
`buildResults`; `docs/js/v2/commands.js:20-50` `buildCommands` + `:56-85`
`searchTracks`). When a query matches **neither** — a free-text question or an
instruction the grammar can't parse ("which of my house tracks need cues?",
"build me a 90-minute peak-time set from my deep-house crate") — the palette
renders an **inert** empty-results hint: `"Ask AutoCue (coming soon) — ⏎ does
nothing yet"` (`docs/js/v2/palette.js:53-64`). The file header already names this
the **composer seam** for "the future opt-in `AUTOCUE_LLM` phase (program PRD
§6/P6)" and states "The input + empty-state contract here IS that seam's API"
(`palette.js:9-13`).

So the gap is concrete and self-documented: the door is built and labelled, but
unmatched natural language dead-ends. The program PRD's framing — **"Conversational
= a door"** (decision #6) — is the design stance: conversation is an *entrance to
the deterministic surfaces*, not a replacement for them. P6 decides whether and
how to open that door, and it must do so without (a) breaking the deterministic
palette, (b) introducing a cloud dependency for users who don't opt in, or (c)
leaking the Rekordbox library off-device by default.

## Goals / Non-goals

**Goals**
- Decide — against real P2 usage — *whether* routing unmatched NL to a Claude-API
  assistant is worth building, and *what* the smallest valuable version is.
- If built: route **only unmatched** palette input to an **opt-in** assistant that
  answers with **artifacts** (structured, actionable results) rather than free
  prose — e.g. a filtered crate, a proposed cue plan, a set draft — that drop the
  user back onto an existing deterministic surface.
- Keep the existing deterministic command + track-search palette **byte-identical
  in behavior** whether or not the assistant is enabled.
- Gate all cloud behavior behind an explicit, off-by-default `AUTOCUE_LLM` flag;
  zero network egress to Anthropic when it is unset.

**Non-goals**
- A chat surface that *replaces* the workbench. The assistant is a router/composer,
  not a new home (the workbench is the home — program decision #1).
- A background agent / watcher (explicitly out of program scope — program PRD
  "What we're NOT building": H's Stagehand daemon).
- Shipping a cloud dependency in the default install, or in XML/Pages mode.
- Re-litigating the deterministic palette grammar. NL is additive; the typed
  command path is the primary path.
- An autonomous tool-executing agent that writes to Rekordbox unattended. Any
  write still flows through the existing F-stamp / H-consent apply pipeline.

## Alignment with locked program decisions

- **Decision #6 (Conversational = a door; `AUTOCUE_LLM` opt-in).** This phase *is*
  the realization of #6. The phrase "a later opt-in `AUTOCUE_LLM` feature (Claude
  API) may route unmatched natural-language input to an assistant. No cloud
  dependency before that phase" is the literal scope. We honor "may" — the
  decision framework can conclude *not yet*.
- **Decision #7 (Global A-layer: ⌘K palette).** The assistant lives inside the
  existing ⌘K palette; it does not add a second global entry point. The single
  contextual ink-pill action dock and the status sentence are untouched.
- **Decision #2 (Maintenance grammar: places / verbs / sheets).** The assistant is
  a **verb amplifier** reached from ⌘K (the verb surface), and its artifacts route
  the user to *places* (a rail crate) or stage *verbs* (a cue/apply proposal).
  Conversation never becomes a *place* and never bypasses *sheets* for emergencies.
- **Decision #5 (XML/Pages frozen; shell local-mode only).** The assistant only
  initializes in local mode — same gate the palette already enforces
  (`palette.js:126` `if (window.ACBridge && !window.ACBridge.isLocalMode()) return`).
- **Decision #4 (multi-file, no build step, ES modules).** All new code is native
  ES modules under `docs/js/v2/`; any backend proxy is additive FastAPI routing on
  the existing `/api` router. No bundler, no runtime web deps.

## Current-state inventory (file:line refs to the seam being built on)

- **The composer seam (the empty-results hint).** `docs/js/v2/palette.js:45-64`:
  `_render()` computes `_results = buildResults(input.value, …)`; when
  `!_results.length` it appends a `.pal-composer-hint` div, `aria-disabled="true"`,
  text `'Ask AutoCue (coming soon) — ⏎ does nothing yet'`. The `Enter` handler
  (`palette.js:156`) only runs when `_results.length` — so today Enter on an
  unmatched query is a no-op. **This is the exact insertion point.**
- **Deterministic result builder.** `palette.js:25-29` `buildResults(query, {commands,
  tracks})` = ranked commands (`fuzzy.js` `rank`) ++ `searchTracks` hits. Pure,
  unit-tested. P6 must not alter its output.
- **Command registry.** `commands.js:20-50` `buildCommands()` returns descriptors
  `{id, group, label, sub?, run}` whose `run()` delegates to existing surfaces via
  `_click(id)` / `_goto(tab, section)` / `window.AC2.workbench.toggleWorkbench()`.
  Artifacts the assistant proposes should resolve to *these same descriptors*
  (run an existing command) rather than novel side effects.
- **Palette keyboard ownership.** `palette.js:150-165` capture-phase keydown:
  while open the palette `stopPropagation()`s every key. A new "ask" affordance
  must live inside this owned keyspace (e.g. a distinct Enter branch on the hint),
  not add a competing global listener.
- **Local-mode gate + reveal.** `palette.js:126`, `:181-185` — palette is inert in
  XML mode and reveals the ⌘K hint only on `autocue:local-mode`. Same gate applies
  to the assistant.
- **Interop bridge (read-only).** `docs/js/08-set-builder-boot.js:919-952`
  `window.ACBridge` exposes `tracks()`, `healthSummary()`, `isLocalMode()`,
  `selectedIds()`, `pending()`, `crate()`/`setCrate()`, `filteredTracks()`,
  `activeTracks()`, etc. The assistant reads library/selection context **only**
  through this bridge (interop contract: `main.js:8-14`).
- **Module entry.** `docs/js/v2/main.js:18-35` wires every v2 surface onto
  `window.AC2` and calls `initProposals()`. A P6 assistant module imports here.
- **Existing apply/consent pipeline (for any write artifact).** `pendingCues`
  proposals + per-track approve ticks (`docs/js/v2/workbench/proposals.js`),
  `approvedApplyIds()` gate (`08-set-builder-boot.js:934-939`), apply at
  `04-app-chrome.js:167`. The assistant proposes; this pipeline disposes.
- **Server API surface (if a proxy is chosen).** `autocue/serve/routes.py:115`
  `router = APIRouter(prefix="/api")`; SSE streaming precedent at
  `/generate-apply-stream` (`routes.py:1128`), `/color-tracks-stream`
  (`routes.py:1680`), `/cue-tools-stream` (`routes.py:2324`). Env-var feature
  gating precedent: `_os.environ.get("AUTOCUE_PARALLEL_*", "1")` (e.g.
  `routes.py:1213`), `AUTOCUE_DISCOVER_DATA_DIR` (`deps.py:23`). A new
  `AUTOCUE_LLM` route follows these patterns.

## Proposed design

**Where it lives in the workbench (maintenance grammar):** the assistant is a
**verb reached from ⌘K**. It does not add a place or a sheet. The flow is:

1. User types in ⌘K. Deterministic commands + track hits rank first, exactly as
   today (the typed path is always primary).
2. When `buildResults` returns empty (no command, no track match), the
   `.pal-composer-hint` becomes **active** instead of inert — *only if*
   `AUTOCUE_LLM` is enabled (server-confirmed; see Architecture). Label changes
   from "coming soon" to an actionable "Ask AutoCue — ⏎ to ask".
3. Enter routes the raw query to the assistant. A result region renders **inside
   the palette** (reusing `#pal-list`), streaming the response.
4. The response is an **artifact**, not a wall of prose. Proposed artifact types
   (hypotheses, to be narrowed by the decision framework):
   - **Crate filter** — "house tracks that need cues" → resolves to a workbench
     crate predicate; one click applies it via `ACBridge.setCrate(...)` /
     deep-links the No-cues crate. Routes the user to a *place*.
   - **Command dispatch** — "scan health" / "find duplicates" expressed in NL →
     maps to an existing `buildCommands()` descriptor and runs its `run()`. The
     assistant becomes a fuzzy front-end to the deterministic registry.
   - **Cue/apply proposal** — "prep cues for the 12 tracks I just imported" →
     stages `pendingCues` and hands off to the F-stamp + H-consent pipeline. The
     assistant *never* writes directly; it fills the proposal tray and the user
     reviews + approves as today.
5. Closing the palette discards the conversation (stateless per-invocation in the
   MVP; see open questions on multi-turn).

**Both themes.** The assistant region reuses palette tokens. Streaming text is
`--font-sans`; any measured value it surfaces (counts, BPM, scores, paths) is
`--font-mono` (rule 3). The "Ask" affordance and any artifact CTA is an **ink
pill** (`--ink` / `--on-ink`), never green (rule 2); green stays signal-only
(a "match found" tick, a focus ring). Backgrounds flat, washes ≤ 8%, glass blur
only on the existing sticky palette chrome (rules 1, 5). Reference `var(--token)`;
no hardcoded hex. Honour `prefers-reduced-motion` for the streaming caret.

**Model + API stance (per the claude-api skill).** Use `claude-opus-4-8` with
`thinking: {type: "adaptive"}`; **stream** the response (`messages.stream()` /
`.get_final_message()`) so a long answer never hits a request timeout. Constrain
artifacts with **structured outputs** (`output_config: {format: {type:
"json_schema", schema: …}}`) so the front-end can render a crate/proposal
deterministically rather than parsing prose. Where the assistant must *act* (run a
command, stage cues), prefer **strict tool use** (`strict: true`) with a small,
gated tool surface that maps 1:1 to existing `buildCommands()` descriptors and the
proposal pipeline — the harness (server proxy) decides which tools are even
exposed, and write-bearing tools are gated behind the existing consent gradient,
never auto-executed.

## Requirements (numbered; each testable; MVP unless tagged)

- **R1** When `AUTOCUE_LLM` is unset/disabled, the palette is **byte-identical** to
  P1+P2 behavior: deterministic commands + track search only; the empty-results
  hint stays inert ("coming soon"); Enter on an unmatched query is a no-op.
  *Test:* Vitest snapshot of `_render()` output for an unmatched query with the
  flag off equals the current inert hint.
- **R2** No network request to any Anthropic endpoint (or proxy) is issued unless
  `AUTOCUE_LLM` is enabled **and** the user explicitly submits an unmatched query.
  Typing, ranking, and track search never call out. *Test:* Vitest with a `fetch`
  spy asserts zero calls during typing/ranking with the flag in any state.
- **R3** The deterministic `buildResults` output and `buildCommands()` registry are
  unmodified by P6 (no new commands required for the MVP; the assistant is a
  consumer of the registry, not a contributor). *Test:* existing `palette`/
  `commands` Vitest specs pass unchanged.
- **R4** The assistant only initializes in local mode (`ACBridge.isLocalMode()`),
  mirroring `palette.js:126`. In XML/Pages mode the hint stays inert regardless of
  flag. *Test:* Vitest asserts the ask-path is gated off when `isLocalMode()` is
  false.
- **R5** Library context the assistant receives is read **only** via `window.ACBridge`
  (never bare `parsedTracks`/`pendingCues`). *Test:* regex sweep of the new module
  finds no bare legacy globals (mirrors the P2 bridge-contract sweep).
- **R6** No write to Rekordbox originates from the assistant. Any cue/apply artifact
  stages `pendingCues` and routes through the existing F-stamp + H-consent +
  `approvedApplyIds()` gate; `_rb_running` 409 and the per-session backup still
  fire. *Test:* Vitest asserts an "apply" artifact dispatches the existing apply
  command, not a novel write call; e2e asserts apply stays consent-gated.
- **R7** The assistant streams its response (no blocking spinner for long answers)
  and renders artifacts from a **structured** payload, not by parsing prose.
  *Test:* the artifact renderer is unit-tested against a fixture JSON payload.
- **R8** Every new interactive id (the ask affordance, artifact CTAs, any apply
  button) is added to `tests/e2e/control-inventory.json` and `selectors-exist.spec.ts`;
  write-bearing ids carry `safeOnRealDb:false`.
- **R9** *(server, if proxy chosen)* The `AUTOCUE_LLM` route returns 404/disabled
  when the env flag is unset (mirroring `/api/perf/recent` gating). The API key is
  read server-side from the environment; it is never sent to or stored in the
  browser. *Test:* pytest asserts the route 404s with the flag unset and that no
  key value appears in any response body.
- **R10** *(privacy)* Only the minimum context needed for the query leaves the
  device, and a first-run consent affordance states plainly what is sent (query +
  derived library facts) and that it goes to Anthropic. *Open:* exact payload
  shape — see open questions.

## Architecture & interop

**Front-end (ES module, no build step).** New module
`docs/js/v2/assistant.js` (or `docs/js/v2/palette-assistant.js`), imported by
`main.js` and exposed as `window.AC2.assistant`. It:
- reads enablement from a server-confirmed capability (see below), gated to local
  mode;
- reads library/selection context via `window.ACBridge` only (R5);
- renders inside `#pal-list`, owns no new global keydown (lives inside the
  palette's capture-phase keyspace, R1's no-op branch becomes the ask branch only
  when enabled);
- resolves artifacts to existing `buildCommands()` descriptors / `ACBridge.setCrate`
  / the proposal pipeline — it never re-implements a write (R6).

The palette stays the owner of open/close/focus; the assistant is a pluggable
result-renderer for the empty branch. Interop direction is unchanged: v2 reads
legacy via `window.*`, exposes via `window.AC2`; legacy never imports v2
(`main.js:8-14`).

**Cloud access — two candidate shapes (decision-framework output, not yet locked):**

- **(A) Server proxy (recommended default).** A new gated route on the existing
  `/api` router (`autocue/serve/routes.py:115`), e.g. `POST /api/assistant` (SSE,
  following `/generate-apply-stream` at `routes.py:1128`). It reads `ANTHROPIC_API_KEY`
  from the server environment, calls the Anthropic Python SDK (`anthropic`,
  `claude-opus-4-8`, adaptive thinking, streaming, structured outputs / strict
  tools), and streams artifacts back. Enablement is surfaced to the front-end via
  the existing `GET /api/status` or `GET /api/config` so the browser never holds a
  key (R9). Env-gated exactly like `AUTOCUE_PARALLEL_*` (`routes.py:1213`).
  **Pros:** key stays server-side; library context never round-trips through the
  browser to a third party; consistent with "intelligence features are local-mode
  only" (CLAUDE.md). **This is the strongly-preferred shape** because it is the
  only one that satisfies R9 cleanly.
- **(B) Direct browser → Anthropic.** Rejected for the MVP: would require a key in
  the browser (violates R9) and CORS/streaming friction. Listed only to document
  why (A) wins.

`anthropic` (and its transitive deps) would become an **optional** install extra
(e.g. `pip install -e ".[llm]"`), never a default dependency — the server import
is lazy and guarded so a stock install with the flag unset imports nothing
Anthropic-related (parity with the `[download]` extra pattern).

**No new runtime web dependency.** The browser uses native `fetch` + the SSE
parsing already used by `_consumeSSE` (web-ui internals). No SDK ships to the client.

## Test plan

Three-leg gate per phase (CLAUDE.md): `pytest` · `npm test` (vitest) · playwright e2e.

- **Vitest (`tests/web/v2-assistant.test.js`):** R1 inert-hint snapshot with flag
  off; R2 fetch-spy zero-call during typing; R3 deterministic builders unchanged;
  R4 local-mode gate; R5 bridge-only sweep; R7 artifact renderer against fixture
  payloads (crate filter, command dispatch, cue proposal); R6 "apply" artifact
  dispatches the existing apply command rather than a novel write.
- **pytest (`tests/test_assistant_endpoint.py`, only if proxy chosen):** R9 route
  404s with `AUTOCUE_LLM` unset; key never appears in a response body; the
  Anthropic call is mocked (no live API in CI — there is no CI; mock in the local
  stack); structured-output schema validates; the route refuses to write to
  Rekordbox (it returns proposals only).
- **Playwright (`tests/e2e/v2-assistant.spec.ts`):** with the flag stubbed on,
  ⌘K → unmatched query → ask affordance appears (ink pill, both themes) →
  Enter renders a streamed artifact region; an apply artifact stays consent-gated
  (cannot apply until reviewed); flag off → hint inert, Enter no-op. Both themes.
- **control-inventory:** add ask-affordance + artifact-CTA + any apply id to
  `tests/e2e/control-inventory.json` (write ids `safeOnRealDb:false`) and
  `selectors-exist.spec.ts` (R8). Drift guard reconciles both directions.
- **Baseline:** zero new e2e failures vs the known baseline; Lighthouse not worse
  (the assistant module is lazy-loaded / inert when disabled).

## Rollout & parity

- **Flag:** `AUTOCUE_LLM` is an **environment flag on the server** (`autocue serve`),
  off by default. Unset → the route is absent/404 and the front-end keeps the inert
  hint. This is the single source of truth; the browser learns enablement from
  `/api/status` (or `/api/config`), never from a client-side toggle that could
  diverge from server capability.
- **Optional dependency:** `anthropic` ships only under an opt-in extra; the
  default install and the default `autocue serve` have **no** cloud capability and
  **no** Anthropic import.
- **Parity / tab-retirement:** P6 is **post-v1** (v1 = P2 workbench-as-home). It
  adds no surface the workbench depends on and removes nothing; the workbench
  reaches parity without it. P6 is purely additive polish on the ⌘K door. If the
  decision framework says "not yet", v1 is unaffected.
- **Staged enablement:** ship behind the flag; dogfood on the maintainer's own
  library before any default-on consideration (which is explicitly out of scope —
  the program PRD says "No cloud dependency before that phase," implying off-by-
  default remains the steady state until a later, separate decision).

### Decision framework (run this gate before cutting a plan)

Build P6 only if, after real P2-workbench usage, **all** hold:

1. **Demand is observed.** Unmatched ⌘K queries actually occur in daily use (worth
   instrumenting a local-only counter of empty-result submissions during P2 — no
   network, just a `localStorage` tally — to quantify before building).
2. **The deterministic palette can't absorb it cheaper.** If the top unmatched
   queries are really just missing commands or filters, add those to
   `buildCommands()` instead (cheaper, offline, no cloud) — that may close the gap
   without any LLM at all.
3. **Artifacts beat prose.** The valuable answers are *structured and actionable*
   (a crate, a proposal, a dispatched command), not chat. If users want open-ended
   chat, that's a different (and likely out-of-scope) product.
4. **The opt-in cost is acceptable** to the user (an env flag + an optional
   dependency + sending derived library facts to Anthropic) for the value
   returned.

If (2) absorbs most demand, **prefer extending the deterministic registry** and
defer or cancel the LLM path. The cheapest door is the one that needs no key.

## Open questions & risks

- **OQ1 — Payload minimization (R10).** What exactly leaves the device? Options:
  (a) just the query + a tiny derived summary (counts per crate, health score);
  (b) the query + a sampled/filtered track list; (c) tool-use round-trips where
  the server fetches context on demand. Leaning (a)→(c): send the *least* by
  default, let strict tools pull more only when needed. Needs a concrete schema.
- **OQ2 — Multi-turn vs stateless.** MVP is stateless per palette invocation
  (closing discards). Is follow-up ("now narrow to 124–128 BPM") worth the added
  state + caching complexity? Defer; the palette's close-on-run ergonomics
  (`palette.js:120`) favor stateless.
- **OQ3 — Artifact taxonomy.** Which artifact types ship first? Hypothesis order:
  command-dispatch (lowest risk, maps to existing registry) → crate-filter →
  cue/apply proposal (highest value, highest risk). The framework's "artifacts beat
  prose" gate decides the set.
- **OQ4 — Tool surface for actions.** If the assistant dispatches commands via
  strict tool use, the exposed tool set must be a curated subset of
  `buildCommands()` — never raw bash, never a direct DB write. Which commands are
  safe to expose? (Read-only + proposal-staging only; apply stays behind consent.)
- **OQ5 — Offline / no-key degradation.** With the flag on but no `ANTHROPIC_API_KEY`
  present, or the API unreachable, the assistant must degrade to the inert hint
  with a clear one-line reason — never a broken spinner. Mirror the JS
  `r.ok`-before-read discipline (CLAUDE.md fetch rule).
- **OQ6 — Cost / rate-limit UX.** Streaming, adaptive thinking, and `effort` tuning
  affect latency and cost; surface nothing scary, but cap `max_tokens` and handle
  429 (SDK auto-retries; show a calm "busy, try again" on exhaustion).
- **Risk — scope creep into a chatbot.** The strongest mitigation is the artifact
  constraint (R7) + the "door, not a room" framing: every assistant turn must end
  by *handing off to a deterministic surface*. Guard this in review.
- **Risk — privacy perception.** Even opt-in, sending library facts to a cloud LLM
  is a trust step for a tool that otherwise touches only the local DB. The first-run
  consent (R10) and payload minimization (OQ1) are load-bearing.
- **Risk — deterministic-path regression.** The single biggest failure mode is
  subtly changing the typed-command path. R1–R3 + the existing palette/commands
  Vitest specs are the guardrail; the empty branch is the *only* code path that
  may change.

## Success metrics

- **Guardrail (must hold):** with `AUTOCUE_LLM` unset, the palette and the whole
  app are behaviorally and visually unchanged from v1; three-leg stack green; zero
  Anthropic import in the default install; zero network egress.
- **If built:** ≥ X% of *previously-unmatched* ⌘K submissions (baseline measured
  per the decision framework's instrumentation) resolve to an artifact the user
  *acts on* (applies the crate, runs the command, approves the proposal) rather
  than reading and dismissing — i.e. the door leads somewhere.
- **Latency:** first streamed token within a calm threshold; no blocking UI on long
  answers (streaming, R7).
- **Discipline:** all new JS is ES modules under `docs/js/v2/` via `main.js`;
  bridge-only context reads; both themes verified; five design rules hold on the
  assistant region; the consent/apply pipeline is delegated, never re-implemented.

---

*Status: SCOPING DRAFT — gate on the Decision framework against P2 usage before
`/prp-plan`. This PRD deliberately leaves the artifact taxonomy and payload schema
open; it locks only the hard constraints (opt-in, no-cloud-by-default,
deterministic palette unchanged) and the seam (`palette.js` empty branch +
`window.ACBridge`).*
