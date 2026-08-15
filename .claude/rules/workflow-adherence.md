# Workflow Adherence (mandatory, not optional)

Last updated: 2026-08-10 02:05

> **Source of truth & sync.** Repo snapshot of the machine-global `~/.claude/rules/workflow-adherence.md`
> (via `sync-rules.sh`). Listed in `sync-rules.sh`'s `HAND_RECONCILED` — captured once to its global counterpart (2026-08-09); now
> hand-maintained on BOTH sides (kept in `HAND_RECONCILED` so the blind name-sync won't clobber it).

This rule exists because the gates in `workflow.md` are easy to *know about* and still skip under
time pressure. They are not advisory.

**A growing subset of these commitments are machine-enforced, not just rule-mandated** — see
`docs/workflow/ENFORCEMENT.md` for the current gate roster (what BLOCKs, what WARNs, and every
`WORKFLOW:no-*` bypass token).

## The seven commitments

1. **Design → Code → Prove is mandatory, not a suggestion.** `workflow.md`'s GATE 1 (design before
   code) and GATE 2 (evidence before "done") apply to every task, trivial ones included at reduced
   weight (see `workflow.md`'s "How much process?"). Skipping a gate because a change "looks simple"
   is itself the failure mode the gate exists to catch.
2. **GATE 0 reads the workflow docs, not just the code.** Before design or code starts this session,
   read `docs/workflow/WORKFLOW.md` (project) and `~/.claude/rules/workflow.md` (global) — not from memory of
   a prior session. The two can drift (see `workflow.md`'s "Source of truth & sync" note); the live
   files are authoritative, not your recollection of them.
3. **Creative/feature work always starts with `superpowers:brainstorming`, scaled to the task.**
   Never skip straight to code for anything that adds behavior. Scale the *depth*, not whether it
   runs: a one-line internal tweak gets a one-sentence intent check; a real feature gets a full
   brainstorming pass (requirements, design, sign-off). The skill auto-fires on its own
   preconditions — don't route around it because a task feels obviously small.
4. **GATE 0 baseline-sync order, every session, before touching anything:**
   1. `git fetch`
   2. behind-count vs `origin/main` — `git rev-list --count HEAD..origin/main`
   3. **rebase if behind** — never a raw `git pull` (merge-commit) and never `git reset --hard`
   4. confirm `git status` is clean
   5. diff the live/deployed artifact, not a stale local snapshot

   **Always compare against `origin/main` (the fetched remote ref), NEVER bare local `main`.** A
   local `main` ref is itself a snapshot — `git fetch` updates `origin/main` but NOT your local
   `main`, so `git diff main` / `HEAD..main` silently compares against merged-away state and misreads
   what already landed. Every diff / compare / behind-count in GATE 0 names `origin/main`. (The
   SessionStart hook now surfaces a stale local `main` ref for you — issue #117.) A worktree is a
   snapshot in time; skipping this order redoes work that already landed on `main` or builds on a
   base that's already stale.
5. **PRESSURE-TEST is mandatory at TWO gates — grill the design AND grill the plan.** Running
   `grill-me` once on the design is not enough: implementation-level flaws first surface when the
   design becomes a concrete plan, so both artifacts get their own grill.
   1. **Grill the DESIGN (pre-spec).** Before writing the spec/ADR, run `grill-me` on the design and
      record each finding + disposition in that document's `## Grill findings` section.
   2. **Grill the PLAN (post-`writing-plans`, pre-BUILD).** After `writing-plans` produces the
      implementation plan, run `grill-me` again on the plan itself — the step sequencing, the
      interfaces it commits to, the failure modes it must handle — and record those findings in the
      plan's own `## Grill findings` section before any code is written.

   Disposition vocabulary: fixed / parked-with-ruling / deferred / accepted / resolved / mitigated /
   addressed (a prose bullet list is fine — issue #108). This is GATE-1's PRESSURE-TEST leg
   (`gates.md`) made explicit and machine-checkable: the `grill_gate` PreToolUse hook blocks
   committing a spec, **plan** (`docs/superpowers/plans/**` — issue #116), or ADR whose
   `## Grill findings` section is missing or empty (bypass a genuinely trivial doc with
   `WORKFLOW:no-grill` in the commit command). **The grill auto-fires at plan creation, not just at
   commit time:** the `grill_nudge` PostToolUse hook WARNs (non-blocking) the moment a plan file is
   written/edited without a non-empty `## Grill findings` section — covering both the repo-committed
   path AND the plan-mode path (`~/.claude/plans/*.md`, which `grill_gate`'s commit-time BLOCK
   structurally cannot reach, since that path lives outside any project git repo). A hook can't
   literally invoke `grill-me` (it's a deterministic guard, not a skill-runner) — "automatic" here
   means nudge-at-write plus block-at-commit, not literal auto-invocation.
6. **P6 DOCUMENT is a HARD GATE, not a nicety.** After any change, run `docs-impact-agent` to find
   the docs the change made stale, then update `docs/` (ARCHITECTURE · README · TESTING) via
   `/write` — BEFORE FINISH/merge. This is obligatory, not optional: it elevates lesson #173 (P6 =
   run the impact agent, never a grep sweep) from recommended to required. A change that ships
   without reconciling the docs it invalidated is not done. (No machine guard for this one yet —
   the discipline is the rule.)
7. **Authoring or editing a skill goes through `superpowers:writing-skills`.** Creating a new skill,
   changing a `SKILL.md`, or verifying a skill before deployment MUST use the skill-authoring
   methodology — don't hand-edit skills ad-hoc. (Pairs with the skill-quality checklist in
   `skill-creator`.)

## See also
`workflow.md` (the 9 phases, the two hard gates) · `design-workflow.md` / `verify-workflow.md` (the
*how* of GATE 1/GATE 2) · `agent-delegation.md` (who runs brainstorming, research, review).
