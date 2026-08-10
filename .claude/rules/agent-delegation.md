# Agent Delegation

Last updated: 2026-08-10 04:32

Delegate to a subagent for **substantial** work; do trivial things inline. The runner
(`cc-worktrees`) is the guaranteed win — roles are opt-in leverage, not ceremony.

## Roles → which agent (concepts; use the real global agents)
- **researcher** — a tight, cited answer without loading 40 files: `web-researcher` (web/docs),
  `codebase-explorer` or `Explore` (in-repo). Terse, `file:line`, read-only.
- **auditor** — the P5 REVIEW panel over a diff: **required** `code-reviewer` + `silent-failure-hunter`
  (error-handling/fallbacks); **recommended** `code-simplifier` (behavior-preserving cleanups) +
  `comment-analyzer` (comment rot). Reviews `git diff`, tries to break it. If the panel changes code,
  re-run VERIFY before merge.
- **test-designer** — coverage map (flow/state diagrams + checklist) BEFORE tests: `test-designer`.
- **skill-author** — creating or editing a skill? Follow the **skill-quality checklist** in
  `skill-creator` — scripts for deterministic steps · a running gotchas list · a pass/fail verify
  step · minimum-viable-model + `context:fork` · **AskUserQuestion** for clarifying Qs (one at a time).
  A skill SHOULD (advisory, no hard gate): declare a min-viable `model:` when its work is
  grunt/deterministic rather than judgment-heavy, prefer a script over prose for any fixed
  procedure, keep a running `## Gotchas` section, and end with an explicit pass/fail verdict step
  where the skill has a real success/failure outcome. `model:`/`context:`/`effort:` are
  Claude-Code-only frontmatter — don't add them to a skill meant to be uploaded via the
  Agent-Skills spec (claude.ai / Skills API), which hard-fails on unrecognized fields. Re-check
  this periodically with `/skill-audit` (issue #154) — it scans `.claude/skills/**/SKILL.md` and
  proposes per-skill rows for a human to approve/decline; it never auto-applies.
- **test-writer** — real tests in the repo's framework: `playwright-tester` / the `frontend-testing`
  + `vitest-best-practices` skills / pytest. Each test must be able to fail.
- **browser-tester** — run tests against the *running* app on the worktree's port: `playwright-tester`
  + the `webapp-testing` skill. Test-auth contract = three inputs: `BASE_URL=http://localhost:$PORT`
  (runner-exported), creds in git-ignored `.env.test` (+ `TEST_AUTH_MODE`), and a seeded user via
  `npm run seed:test` (idempotent, Admin-API, localhost-only). **Hold the per-repo test lock**: run the
  suite via `cc-worktrees test -- <cmd>` so two automated runs can't collide on the shared DB
  (`cc-worktrees ls` shows the holder). Missing `.env.test` → fail loud; never test unauthenticated.

## Fork ≠ free — construct a focused prompt, don't re-send session state

Spawning a subagent isn't a discount: the subagent processes whatever context it's given from
scratch, and its RETURN re-enters the primary session's own context (see `context_nudge`,
`docs/ENFORCEMENT.md` — a WARN when a subagent's return exceeds ~4k estimated tokens). No hook can
see or shrink the SPAWN prompt itself, so this is a discipline the delegator owns, not something
machine-enforced:

- **Construct EXACTLY what the subagent needs** — "review these 3 files for X," not the whole
  session's back-and-forth re-sent as context. Pair with `superpowers:dispatching-parallel-agents`
  for the fan-out shape once the prompt itself is scoped tight.
- **Ask for a summary back, not a dump.** A subagent's job is to do the wide/deep work in its OWN
  context and return the distilled result — not to hand the primary session everything it read.
  `context_nudge` flags an oversized return after the fact; the cheaper fix is prompting for a
  summary up front.

## When to delegate (the model judges)
- **Substantial research** (multi-file / multi-source) → a researcher subagent. A **one-off lookup
  → inline `WebSearch`** — don't spawn an agent for a single web query.
- **`/deep-research` is the HEAVYWEIGHT** — reserve it for genuinely deep, multi-source,
  fact-checked questions. A how-to / single-topic question → one `web-researcher` subagent or inline.
- **Before a commit or PR**, or a risky diff → an auditor.
- **A net-new feature with real flows/state** → test-designer → (human review) → test-writer →
  browser-tester. Skip the pipeline for trivial changes.

## How to delegate (prefer in-process)
- **Default to in-process subagents (Agent/Task tool):** their result returns into your transcript
  (so it's visible over `/rc`), no extra tmux pane, no experimental flag. Do **not** spawn a separate
  `claude` in a tmux window for a sub-task.
- **Forming a crew — two real modes, pick by need:**
  - **In-process teammates (default for sub-tasks):** spawn named teammates via the **Agent tool**
    (a per-session implicit team forms on first spawn; coordinate with `SendMessage`; results
    return to the leader's transcript). Cheap, no panes, but they share the leader's lifecycle and
    their output pressures the leader's context.
  - **cc-worktrees crews (the standing cockpit):** one coordinator pane spawns the rest of the team
    as **Agent-tool teammates** under `teammateMode: tmux` — each still gets its own visible pane
    (the live cockpit survives), but coordination is **SendMessage**, not tmux send-keys; replies
    return fresh and complete, so the old dispatch-script / stale-status-wait apparatus (lessons
    #98/#105/#107) doesn't arise. Still the right call when you want per-role models, separate
    context windows per teammate, or the live multi-pane view. **Scaling past one implementer:**
    count writers, not agents — read-only auditors/researchers share worktrees; every extra
    implementer gets its OWN worktree, provisioned by the COORDINATOR via `cc-worktrees add`
    (worktree + PORT only, no session/claude) — never by the teammate (the create default spawns a
    whole crew, lesson #162). ⚠ Recovery after the coordinator
    itself crashes or a session compacts relies on its RECONCILE-ON-RESUME contract re-reading
    `crew/BOARD.md` before spawning anything — that path has NOT been verified end-to-end under a
    real crash (see `docs/CC-WORKTREES.md` → Team-v2 for the honest caveat).
