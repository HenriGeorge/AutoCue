# Canonical Dev Workflow (every project, every session)

Last updated: 2026-08-14 21:30

> **THE LAW — Design → Code → Prove.** Shape it before you build it (GATE 1 ⛔), prove it
> with fresh evidence before you call it done (GATE 2 ⛔). Two hard gates, never skipped.
> The *how* of the Design and Prove legs lives in `design-workflow.md` / `verify-workflow.md`.

One workflow, everywhere — **any stack** (web · service/API · CLI/library · data). The discipline is
universal; only _how you drive the real artifact_ in VERIFY and _make the design concrete_ in GATE 1
change by profile (see `verify-workflow.md` / `design-workflow.md`). The **superpowers methodology is
the ambient engine** — its skills auto-fire as their preconditions match, so you rarely invoke them by
name. Don't fight the gates.

> **Source of truth & sync.** This file is the repo's versioned snapshot of the machine-global
> `~/.claude/rules/workflow.md` (synced via `sync-rules.sh capture`/`install`). The project-facing
> `docs/workflow/WORKFLOW.md` is the stamped copy. Keep the three reconciled in substance — esp. GATE 0 below.

Two laws override everything: a `CLAUDE.md` instruction beats any skill, and the **HARD GATES** are
never skipped.

- **GATE 0 — Baseline before anything.** No design/code until you've synced to the current source of
  truth THIS session: `git fetch`, check the behind-count vs `main` (`git rev-list --count HEAD..origin/main`),
  **rebase if behind**, and diff the live/deployed artifact — not a stale local snapshot. A worktree is a
  snapshot in time; build on a stale one and you redo work that already exists. **Verify the plan/brief
  premise too**, not just the git base — a plan/PRD is a snapshot as well, often staler than the branch.
  Have research characterize the target surface against live code and emit a tagged
  `[EXISTS]/[PARTIAL]/[MISSING]` + file:line gap list; if the target already exists, re-scope to the
  real gap and re-enter GATE 1 before building. A spawning plan is a hypothesis to falsify, not a contract.
- **GATE 1 — Design before code.** No implementation until an approved design exists
  (`brainstorming`; or, for big work, a BMAD PRD + architecture).
- **GATE 2 — Evidence before "done".** No "done/fixed/passing" claim without FRESH output THIS
  turn — unit tests, typecheck, lint, AND the behaviour tests run against the **real artifact**
  (`verification-before-completion` + `/validate`; drive the real artifact via the `run`/`webapp-testing`
  skills — no slash command). **SHOW the evidence in your report** —
  surface what proves it (screenshot for UI · response body for API · stdout/exit for CLI · output
  rows for data); evidence the user can't see is half-wasted. Run VERIFY **once per batch of
  accumulated changes**, not per micro-edit — one full cycle over the staged whole beats five
  partial cycles.

## The 9 phases

```mermaid
flowchart TD
    P0["0 · PRIME — GATE 0 ⛔<br/>read docs/workflow/WORKFLOW.md · invoke superpowers:using-superpowers<br/>git fetch · behind-count vs main · rebase if behind · gh issue list (dup check) · /rc (concurrent-session check) · /prime-core · /project-status"] --> G0
    G0{"GATE 0+ ⛔ — is the plan/brief premise still TRUE vs live code?<br/>research confirms what already exists (gap list)"}
    G0 -->|"stale — already built / wrong scope"| RS["RE-SCOPE<br/>narrow to the REAL gap · re-enter GATE 1"]
    RS --> P1
    G0 -->|"current"| P1
    P1["1 · SPEC — GATE 1 ⛔<br/>brainstorming auto · /bmad-prd (heavy)"] --> P2
    P2["2 · PLAN + COVER (test-first)<br/>writing-plans auto · test-designer → coverage<br/>write the FAILING test · run it alone → RED"] --> P3
    P3["3 · BUILD (red→green)<br/>native worktree (WorktreeCreate hook provisions) · TDD auto · make the COVER test green (web: playwright-tester → e2e/*.spec.ts)"] --> P4
    P4{"4 · VERIFY — GATE 2 ⛔ — regression, once per batch<br/>bin/test-lock -- &lt;test cmd&gt;<br/>drive the REAL artifact (by profile)<br/>typecheck · lint · /validate · run/webapp-testing skills"}
    P4 -->|fails| DBG["systematic-debugging auto"]
    DBG --> P4
    P4 -->|"green + artifact-verified"| P5["5 · REVIEW — panel CONCURRENT, one message<br/>code-reviewer + silent-failure-hunter (req) + simplifier + comment-analyzer (advisory) · /security-review"]
    P5 -->|"panel changed code"| P4
    P5 --> P6["6 · DOCUMENT (impact)<br/>what did this change make STALE? · docs-impact-agent · /write"]
    P6 --> P7["7 · FINISH<br/>finishing-a-development-branch auto · git worktree remove (WorktreeRemove hook backs up) · /merge-prs"]
    P7 --> P8["8 · CLOSE (docs-CLOSE) — GATE ⛔<br/>file follow-ups as GitHub issues or WORKFLOW:no-follow-ups<br/>/handoff · /dev-reflect · /workflow-diagrams (best-effort)"]
    P8 --> Done([Done])
```

## Phase → tools

| # | Phase | Drives it (auto) | Commands / tools |
|---|-------|------------------|------------------|
| 0 | PRIME ⛔ | — | **read `docs/workflow/WORKFLOW.md` first · invoke `superpowers:using-superpowers`** · **sync baseline first** (`git fetch` · behind-count vs `main` · rebase if behind) · **verify the plan premise vs live code** (gap list) · `gh issue list` (already tracked? avoid dup work) · **`/rc` (concurrent-session check — is another session already on this project/item? avoid a shared-checkout collision)** · `/prime-core` · `/project-status` · context-map |
| 1 | SPEC ⛔ | `brainstorming` | **grill-me (required)** · a **Mermaid diagram** of the design (required) · `/bmad-prd` (heavy) · **see `design-workflow.md`** |
| 2 | PLAN + COVER (test-first) | `writing-plans` | **test-designer** (behaviour / user-flow coverage) · **write the failing behaviour test** (web: **playwright-tester** → `e2e/*.spec.ts`) · **run JUST that test** (`bin/test-lock -- <it>`) → confirm **RED** (rest of suite stays green; ⚠ NOT `/validate` — that's the full-suite green gate at P4) · `/bmad-create-story` — **see `design-workflow.md` COVER** |
| 3 | BUILD (red→green) | `test-driven-development` · `executing-plans` | **native worktree** (EnterWorktree / Agent `isolation: worktree` — the `worktree_create` hook provisions fetch·env·PORT·setup) · make the COVER red test green; add tests as code grows · domain skills |
| 4 | VERIFY ⛔ | `verification-before-completion` · `systematic-debugging` | `bin/test-lock -- <test cmd>` (once per batch, not per micro-edit) · **drive the real artifact** (by profile — the `run`/`webapp-testing` skills; no slash command) · typecheck/lint · `/validate` · **see `verify-workflow.md`** |
| 5 | REVIEW | `requesting-code-review` → `receiving-code-review` | dispatch the panel **CONCURRENTLY — all four in ONE message, running async while P6 proceeds**: **required** `code-reviewer` (=`/code-review`, owns the verdict) · `silent-failure-hunter` (may escalate) — **advisory** `code-simplifier` · `comment-analyzer` (one comment, no own marker; docs-only diffs → "no applicable findings") — `/security-review` (distinct). **Panel edits → re-run VERIFY** |
| 6 | DOCUMENT (impact) | — | **docs-impact-agent** → what did this change make STALE? · `/write` · create-readme (heavy: BMAD Paige) |
| 7 | FINISH | `finishing-a-development-branch` | `git worktree remove <path>` (the `worktree_remove` hook backs up untracked files + an uncommitted patch first) · `/merge-prs` |
| 8 | CLOSE (docs-CLOSE) ⛔ | — | **file every follow-up / known gap / deferred nit as a GitHub issue** (`gh issue create`), or state `WORKFLOW:no-follow-ups` — enforced by the `close_issue_gate` Stop hook · HANDOFF/TASKS ← real PR#/merge state · `/handoff` · `/dev-reflect` · **`/workflow-diagrams`** (best-effort — refresh the diagram page when interactive; skip headless) · remember · **emit worktree teardown** — `git worktree remove <path>` (the `worktree_remove` hook backs up untracked files + an uncommitted patch and frees the PORT); the branch survives — delete separately with `git branch -d <branch>` once merged. **Multi-PR sessions run `/dev-reflect` ONCE** — `close_gate` demands it at the FIRST post-merge stop and any reflect in the transcript satisfies later merges that session |

## Behaviour tests & real-artifact verification

Design coverage AND the first failing test happen at COVER (PLAN/P2 — the test-first tail of GATE 1),
BUILD turns them green, and you RUN them **every
VERIFY cycle** so nothing breaks silently — two layers:
- **Codified regression:** `bin/test-lock -- <test cmd>` (per-repo lock spans all worktrees; and in CI).
- **Drive the real artifact** *(green ≠ works — by profile)*: **Web UI** → **Chrome DevTools MCP** at
  `http://127.0.0.1:$PORT` (`webapp-testing`, never the blocked Claude-in-Chrome extension — see
  the `claude-template:local-browser-testing` plugin skill); **Service/API** → hit endpoints (`curl`/HTTP client), assert status+body;
  **CLI/Library** → run it, assert stdout+exit code; **Data** → run on fixtures, assert output
  schema/row counts. Codify anything you verify interactively as a test.

## How much process? (the only thing you decide)

```mermaid
flowchart LR
    Q[New task] --> Q1{Trivial mechanical?}
    Q1 -->|yes| Inline["Do it inline — GATE 2 still applies"]
    Q1 -->|no| Q2{Large / multi-feature / needs design?}
    Q2 -->|no| Full["Run the 9 phases. Skills auto-fire."]
    Q2 -->|yes| Heavy["BMAD planning first, then the 9 phases per story."]
```

- **Trivial** → inline, but GATE 2 still applies.
- **Standard** → the 9 phases; skills fire themselves; don't bypass GATE 1 / GATE 2.
- **Large** → run **BMAD** planning first (`npx bmad-method install --tools claude-code`), then the phases per story.
- **Batch RELATED items into one branch/PR** — the per-PR gates (grill · review panel ·
  docs-impact · CLOSE) amortize across a batch, so five same-surface fixes in one PR pay the
  overhead once. Never bundle UNRELATED concerns to save gate cost: review quality and revert
  granularity lose more than the gates save.
- **Delegate substantial work** to subagents (see `agent-delegation.md`): research → Explore /
  web-researcher; review → code-reviewer (+ silent-failure-hunter); tests → test-designer →
  playwright-tester → browser-tester; docs → docs-impact-agent. Trivial things stay inline.
  Auditors/reviewers hold no locks — **dispatch them async and keep working** (P6 DOCUMENT can
  proceed while the P5 panel runs).

## Worktrees & frameworks (hooks-native)

- **Native worktrees are the isolation mechanism for BUILD** — `EnterWorktree`, or the Agent tool's
  `isolation: worktree` for parallel writers. The **`worktree_create` hook** (WorktreeCreate event)
  provisions each one: `git fetch origin` + behind-warn (GATE 0), gitignored `.env*` carry-in from
  the primary, `SETUP=npm` install, and a collision-free `PORT` written to `.claude/worktree.env`.
  Teardown is `git worktree remove <path>` — the **`worktree_remove` hook** (WorktreeRemove event)
  first backs up untracked/ignored files plus an uncommitted-changes patch to
  `~/.local/share/claude-template/backups/` and frees the PORT. One writer per worktree; read-only
  auditors/researchers need no worktree at all.
- **Suite serialization is `bin/test-lock -- <cmd>`** — one lock per repo (keyed by the git common
  dir, shared across every worktree); a contended run prints the holder and exits 75. The
  `test_lock_enforce` PreToolUse hook denies raw suite runs so the lock can't be forgotten.
- **Teammates are in-process Agent-tool spawns** (SendMessage coordination); the `teammate_idle`
  hook (TeammateIdle event) keeps one working while it still owns tasks. Figma work uses the
  official Figma MCP / claude-in-chrome paths.
- **`gh pr merge` with `main` or the PR branch checked out in ANY worktree** fails its LOCAL
  cleanup as a unit but **the REMOTE merge already succeeded**. Don't re-merge: confirm
  `git log origin/main` shows the squash commit, remove the holding worktree, then finish by hand
  (`git push origin --delete <branch>`). (#179/#196)
- **BMAD** is the optional heavy-planning layer — a planning front-end, not a competing spine. Its
  artifacts (`project-context.md`, PRD, architecture, self-contained stories) feed BUILD.
- **Rules vs. hooks — where does a new guardrail go?** A thing that's judgment/style is a **rule**
  (this file and its siblings) — Claude *should* follow it, enforced by goodwill + review. A thing
  that can be checked deterministically at a tool call is a **hook** — the runtime *won't let* it
  happen, regardless of what Claude decides. See `hooks/README.md` for the full boundary + the
  event model (`PreToolUse` hard-blocks via deny; blocking events like `WorktreeCreate`, `Stop`, and
  `TeammateIdle` block via exit code; the rest inject context or nag at a turn boundary).
