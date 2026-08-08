# Overview — how we work, at a glance

A single visual atlas of how this project works: the **Design → Code → Prove** workflow it follows,
and where every other doc fits. Each section links to its deep-dive. **The two laws:** _design before
code_ (GATE 1), _evidence before "done"_ (GATE 2) — and before either, _sync the baseline_ (GATE 0).

---

## 1. What this is — three layers, one lifecycle

The same **Design → Code → Prove** workflow applies to **any** stack — web, service/API, CLI/library,
or data. Three layers (Superpowers, optional BMAD, cc-worktrees) compose into one spine.

```mermaid
flowchart TD
    You["You: 'add a login page' / 'fix this bug'"] --> SP
    subgraph layers["The three layers"]
      SP["Superpowers<br/>execution engine — skills auto-fire"]
      BMAD["BMAD (optional)<br/>heavy upfront planning for epics"]
      CW["cc-worktrees<br/>isolation + parallelism in BUILD"]
    end
    SP --> Spine["The 9-phase spine + the gates"]
    BMAD -.feeds.-> Spine
    CW -.isolates.-> Spine
    Spine --> Out["shipped, verified work"]
    classDef law fill:#ffe9e9,stroke:#d33,stroke-width:2px;
    class Spine law;
```

**Zoomed out — the whole machine, end to end.** Entry stamps the workflow in; the runtime carries a
task through the three gates while **hooks** guard every tool call; ship deploys it and `/dev-reflect`
feeds hard-won lessons back to the top of the next loop.

```mermaid
flowchart TB
  subgraph ENTRY["ENTRY · set up"]
    direction LR
    S1["workflow stamped in<br/>+ baseline synced"]
    S2["stack scaffolded<br/>(web / service / CLI / data)"]
    SY["agents · rules · tokens synced"]
  end
  subgraph RUNTIME["RUNTIME · the 9-phase spine"]
    direction LR
    G0["GATE 0 ⛔<br/>sync baseline"] --> G1["GATE 1 ⛔<br/>design"] --> BUILD["BUILD<br/>cc-worktrees + crew"] --> G2["GATE 2 ⛔<br/>prove"]
  end
  subgraph SHIP["SHIP &amp; LEARN"]
    direction LR
    GO["deploy / ship<br/>(per stack)"]
    DR["/dev-reflect<br/>→ lessons.md"]
  end
  ENTRY ==> RUNTIME ==> SHIP
  HK["hooks<br/>Pre · Post · Session · Stop<br/>guard every tool call"] -.enforce.-> BUILD
  FIG["Figma bridge<br/>code ⇄ design"] -.web GATE 1.-> G1
  DR -.promote → rule / doc / test.-> G0
  classDef gate fill:#ffe9e9,stroke:#d33,stroke-width:2px,color:#3a0f0b;
  class G0,G1,G2 gate;
```

→ Deep dive: [`WORKFLOW.md`](WORKFLOW.md) · run `/workflow-diagrams` to generate this project's browsable diagram page

---

## 2. The lifecycle — 9 phases, 3 gates

The spine every task runs through. **GATE 0** (sync), **GATE 1** (design), **GATE 2** (evidence)
are never skipped; trivial changes go inline but GATE 2 still applies.

```mermaid
flowchart LR
    P0["0 · PRIME<br/>GATE 0 ⛔ sync baseline"] --> P1["1 · SPEC<br/>GATE 1 ⛔ design approved"]
    P1 --> P2["2 · PLAN<br/>+ test design"] --> P3["3 · BUILD<br/>+ write tests"]
    P3 --> P4{"4 · VERIFY<br/>GATE 2 ⛔ fresh evidence"}
    P4 -->|red| DBG["systematic-debugging"] --> P4
    P4 -->|green| P5["5 · REVIEW"] --> P6["6 · DOCUMENT"] --> P7["7 · FINISH"] --> P8["8 · CLOSE"]
    P8 --> Done([Done])
    classDef gate fill:#ffe9e9,stroke:#d33,stroke-width:2px;
    class P0,P1,P4 gate;
```

**How much process?** The only thing you decide:

```mermaid
flowchart LR
    Q[New task] --> Q1{Trivial mechanical?}
    Q1 -->|yes| Inline["inline — GATE 2 still applies"]
    Q1 -->|no| Q2{Large / multi-feature?}
    Q2 -->|no| Std["the 9 phases — skills auto-fire"]
    Q2 -->|yes| Heavy["BMAD planning first,<br/>then 9 phases per story"]
```

→ [`WORKFLOW.md`](WORKFLOW.md)

---

## 3. GATE 0 — sync the baseline first

A worktree is a snapshot in time. Build on a stale one and you redo work that already exists.

```mermaid
flowchart LR
    F["git fetch origin"] --> C{"behind origin/main?"}
    C -->|"yes (>0)"| S{"branch pushed / shared?"}
    S -->|"solo"| RB["git rebase origin/main"]
    S -->|"shared"| MG["git merge origin/main"]
    C -->|no| OK["baseline current"]
    RB --> OK
    MG --> OK
    OK --> Build["start designing / building"]
```

→ [`WORKFLOW.md` § Phase 0](WORKFLOW.md#phase-0--prime-the-baseline-first)

---

## 4. GATE 1 — design before code

Turn intent into something concrete enough to build without guessing. The spine is universal; only
**MAKE CONCRETE** changes by stack.

```mermaid
flowchart LR
    S["SHAPE<br/>design-an-interface"] --> G["PRESSURE-TEST<br/>grill-me (required)"]
    G --> M["MAKE CONCRETE<br/>(by profile)"]
    M --> D["DIAGRAM<br/>Mermaid diagram (required)"]
    D --> B["BUILD<br/>→ WORKFLOW.md"]
    B --> R["REVIEW<br/>code-reviewer + silent-failure-hunter"]
    M -.web only.-> WUI["art-direct · tokens · polish<br/>(Figma machinery)"]
```

What "concrete enough" means per profile:

```mermaid
flowchart TD
    M["MAKE CONCRETE"] --> W["Web UI → rendered design<br/>(pixels + tokens + states)"]
    M --> A["Service/API → API contract<br/>(OpenAPI / schema)"]
    M --> C["CLI/Library → public interface<br/>(signatures · flags · exit codes)"]
    M --> D["Data → data contract<br/>(schema · idempotency · lineage)"]
```

> **Approve the RENDERED design, not the MECHANISM** (lesson #69): pixels for UI, the contract for
> an API, the `--help` for a CLI — before wiring real routes/data.

→ [`DESIGN-WORKFLOW.md`](DESIGN-WORKFLOW.md)

---

## 5. GATE 2 — evidence before "done"

**RUN it → READ it → CLAIM it.** Green tests ≠ "works" — drive the real artifact too. The pipeline
runs whole, every cycle.

```mermaid
flowchart LR
    ST["STATIC<br/>typecheck · lint · unit"] --> BE["BEHAVIORAL<br/>e2e/behaviour regression"]
    BE --> EX["EXERCISE<br/>drive the REAL artifact (by profile)"]
    EX --> Q{evidence green?}
    Q -->|no| DBG["systematic-debugging<br/>root cause → 1 fix → re-run all"]
    DBG --> ST
    Q -->|yes| CL["CLAIM with evidence<br/>→ REVIEW"]
    classDef gate fill:#ffe9e9,stroke:#d33,stroke-width:2px;
    class CL gate;
```

"Drive the real artifact" by profile:

```mermaid
flowchart TD
    EX["EXERCISE"] --> W["Web → browser @127.0.0.1:PORT<br/>Chrome DevTools MCP · screenshot"]
    EX --> A["Service/API → hit endpoints<br/>assert status + body/contract"]
    EX --> C["CLI/Library → run it<br/>assert stdout + exit code"]
    EX --> D["Data → run on fixtures<br/>assert schema · row counts · metrics"]
```

→ [`VERIFY-WORKFLOW.md`](VERIFY-WORKFLOW.md)

---

## 6. Stack profiles — web is one profile, not the framing

The spine (gates, 9 phases, TDD, cc-worktrees) is identical for all. Only VERIFY and GATE 1 vary.

| Profile | GATE 1 "concrete" | GATE 2 "drive the artifact" | Figma |
|---|---|---|---|
| **Web UI** | rendered design + tokens | browser @`127.0.0.1` (Playwright/DevTools) | ✅ |
| **Service / API** | API contract / schema | hit endpoints, assert status+body | ❌ |
| **CLI / Library** | interface (flags, exit codes) | run it, assert stdout + exit code | ❌ |
| **Data / Pipeline** | data contract / schema | run on fixtures, assert output | ❌ |

Set per project in `.claude/worktrees.conf` → `STACK_PROFILE`.
→ [`WORKFLOW.md` § profile table](WORKFLOW.md#exercise-the-real-artifact-by-profile)

---

## 7. cc-worktrees — isolation & parallelism

Sibling worktrees (`<repo>-worktrees/<name>`), each on its own port + tmux pane, with a per-repo
test lock. Solo, shell, or a team-v2 AI crew.

```mermaid
flowchart TD
    CMD["cc-worktrees NAME"] --> Q1{"-c ?"}
    Q1 -->|yes| SOLO["SOLO — worktree + 1 Claude"]
    Q1 -->|no| Q2{"-x ?"}
    Q2 -->|yes| SHELL["SHELL — terminal + free PORT"]
    Q2 -->|no| CREW["CREW — team-v2 AI crew"]
```

The team-v2 crew (durable records via `crew/*.md`, coordination via SendMessage):

```mermaid
flowchart TD
    CO["coordinator — enforces the gates, never edits code"]
    CO --> IM["implementer — writes ALL code"]
    CO --> RE["researcher — read-only"]
    CO --> AU["auditor — review + security"]
    CO --> TD["test-designer — coverage plan"]
    CO --> TV["test-verifier — e2e + live app"]
    IM & RE & AU & TD & TV -. write .-> BUS["crew/*.md + BOARD.md"]
    BUS -. coordinator reads .-> CO
```

Always run suites via `cc-worktrees test -- <cmd>` (holds the lock). Scaling a crew past one
implementer? The coordinator provisions each extra implementer's worktree with `cc-worktrees add`
(worktree + PORT only — no session, no claude; teammates never run cc-worktrees themselves).
→ [`CC-WORKTREES.md`](CC-WORKTREES.md)

---

## 8. Figma toolkit — two directions

The hardened toolkit lives at `scripts/` (a symlink → `cc-worktrees-scaffold/figma/`), or is
scaffolded into a worktree via `cc-worktrees --figma`.

```mermaid
flowchart LR
    subgraph code2fig["A. Code → Figma (this repo's toolkit)"]
      App["running web app / component"] --> SVG["page-to-svg.mjs<br/>→ sharp vector SVG"]
      SVG --> Push["svg-to-figma / figma-export.mjs<br/>→ stream to plugin :3055"]
    end
    subgraph fig2code["B. Figma → Code (quota-first ladder)"]
      Port["port Claude Design export · cost 0"] --> WAPI["window.figma API · cost 0"]
      WAPI --> Shot["Chrome DevTools screenshot · cost 0"]
      Shot --> MCP["Figma MCP get_design_context<br/>⚠ quota — FINAL design only"]
    end
```

The three composable pipelines (A → variables, B → import, C → bind):

```mermaid
flowchart LR
    A["A · tokens-to-figma.mjs<br/>CSS --prefix-* → Figma color variables"]
    B["B · figma-export / svg-to-figma<br/>page → sharp SVG → Figma frame"]
    C["C · rebind-svg-vars.mjs<br/>bind imported fills → those variables"]
    A --> C
    B --> C
    C --> Result["variable-driven Figma import<br/>(edit a variable → recolors the SVG)"]
```

> Use **A** to push CSS tokens → Figma Variables; **B** to copy a live page/component into Figma as a vector frame. (Both go code → Figma; the Figma → code direction is the quota ladder above.)
> GATE 1 still applies: approve the rendered design before wiring data.

→ _(web projects only)_ [`FIGMA-EXPORT.md`](FIGMA-EXPORT.md) · [`figma-copy.md`](figma-copy.md) · [`FIGMA-UI.md`](FIGMA-UI.md)

---

## 9. Agents & delegation — one per phase

Delegate substantial work to in-process subagents; trivial things stay inline.

```mermaid
flowchart LR
    R["research (in-repo)"] --> CE["codebase-explorer"]
    RW["research (web)"] --> WR["web-researcher"]
    TP["test design"] --> TDz["test-designer"]
    BU["build"] --> CI["crew-implementer"]
    VE["verify / live"] --> PT["playwright-tester"]
    RV["review"] --> CR["code-reviewer + silent-failure-hunter"]
    DO["docs"] --> DI["docs-impact-agent"]
```

→ `~/.claude/rules/agent-delegation.md` (machine-global, auto-loaded each session)

---

## 10. Lessons loop — capture what bit you

Hard-won lessons are appended to `docs/lessons.md` by `/dev-reflect` at CLOSE, then promoted to a
durable home (a rule, a doc, a test) so they enforce themselves next time. CLOSE also files
follow-ups / known gaps / deferred nits as GitHub issues (`gh issue create`) — a blocking Stop hook
(`close_issue_gate`) enforces this for any session that made a commit, unless it states
`WORKFLOW:no-follow-ups`.

```mermaid
flowchart LR
    Work["a session's wins + gotchas"] --> DR["/dev-reflect (CLOSE)"]
    DR --> LL["docs/lessons.md<br/>numbered entry + source + durable home"]
    LL --> Promote["promote → rule / doc / test"]
    Promote --> Future["enforced automatically next time"]
```

→ [`lessons.md`](lessons.md)

---

## Doc map — which page when

```mermaid
flowchart TD
    O["OVERVIEW.md (you are here)"]
    O --> WF["WORKFLOW.md<br/>the 9 phases + profile table"]
    WF --> DW["DESIGN-WORKFLOW.md<br/>GATE 1 (the how)"]
    WF --> VW["VERIFY-WORKFLOW.md<br/>GATE 2 (the how)"]
    O --> CW["CC-WORKTREES.md<br/>every flag + the crew"]
    CW --> GR["crew-workflow-guardrails.md<br/>crew-ops decision diagrams"]
    O --> FU["FIGMA-UI.md / FIGMA-EXPORT.md<br/>figma mechanics + pipelines (web)"]
    O --> LL["lessons.md<br/>the lessons log"]
```

| Doc | Read it when |
|---|---|
| [`WORKFLOW.md`](WORKFLOW.md) | new here, or the canonical 9-phase workflow + stack profiles |
| [`DESIGN-WORKFLOW.md`](DESIGN-WORKFLOW.md) | satisfying GATE 1 (idea → approved design) |
| [`VERIFY-WORKFLOW.md`](VERIFY-WORKFLOW.md) | satisfying GATE 2 (proving it works) |
| [`CC-WORKTREES.md`](CC-WORKTREES.md) | every cc-worktrees flag, config, crew internals |
| [`crew-workflow-guardrails.md`](crew-workflow-guardrails.md) | crew-ops decision diagrams — idle-pane triage, port ownership, fresh-keyed wait, test partition |
| [`FIGMA-EXPORT.md`](FIGMA-EXPORT.md) / [`figma-copy.md`](figma-copy.md) _(web)_ | code → Figma export pipeline |
| [`FIGMA-UI.md`](FIGMA-UI.md) _(web)_ | Figma-MCP mechanics, quota strategy |
| [`lessons.md`](lessons.md) | the hard-won lessons log |
| `/workflow-diagrams` | run this command to generate this project's browsable page of every committed Mermaid diagram, by topic |
