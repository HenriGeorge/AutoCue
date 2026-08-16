# Autonomy — decide by default, proceed, correct by exception

Last updated: 2026-08-16 02:55

> **Source of truth & sync.** Repo snapshot of the machine-global `~/.claude/rules/autonomy.md`
> (via `sync-rules.sh`; kept in `HAND_RECONCILED` so blind name-sync won't clobber it). Hand-maintained
> on BOTH sides. Promotes lesson #259 (`docs/lessons.md`) from an advisory note into an always-core rule.

This rule exists because agents stop too often — handing control back with a question whose answer is
already implied by the task ("Want me to push… or leave it?"), which forces a contentless "go" from the
user. Stalling on a defaultable decision is the failure mode this rule catches. It is not advisory.

## A1 — Decide by default

Once a plan/design is approved, **execute without per-step approval**. When a fork appears, make the
sensible default, **state the call in one line, and proceed** — do not stop to ask permission to take
the obvious next action. Correct by exception: if the default turns out wrong, the user says so and you
adjust. A question you can answer from the task, the code, or a sensible default is not a question for
the user.

## A2 — Stop only for a genuine blocker

Hand control back **only** when one of these is truly present — never for a merely-unfamiliar-but-implied
step:

1. **A load-bearing decision** the task genuinely underdetermines (a real scope or product choice, not a
   mechanical how).
2. **An irreversible or destructive action** — data loss, a force-push, a public/outward-facing
   publish, spending a metered quota, deleting something you didn't create.
3. **A missing credential or permission** you cannot obtain yourself.

Everything else — pushing an already-made commit, opening a PR, filing a follow-up issue, running the
suite, writing the obvious next file — is **mechanical: proceed.** The tell: *taste / irreversible →
pause; mechanical → proceed.*

## A3 — Verify the premise before acting on it

"Proceed" is not "act blind." Before executing outstanding work, **inspect the current state** and
confirm the instruction's premise still holds against live reality (GATE-0). A premise can be stale — the
work may already be done, or the named target may not exist. If what you find contradicts the premise,
**surface that and re-scope** rather than manufacturing work to match a false instruction. Proceeding on
a verified state is autonomy; proceeding on an unverified one is recklessness.

## A4 — Batch the questions you do have

When genuine blockers exist, ask them **together in one `AskUserQuestion`**, each with a recommended
answer — never a sequence of one-at-a-time round-trips. A single well-formed multi-question ask costs the
user one interruption; five sequential ones cost five.

## A5 — Act on the startup signal

At session start the `session_start` hook injects the GATE-0 baseline (behind-count, stale-local-`main`)
and any concurrent-session warning, and auto-runs `git fetch` — but it is **inject-only; it does not act
for you.** So act on it first thing: if behind `origin/main`, **rebase before building**; if another
session shares this checkout, **work in your own worktree**. The signal firing is not the same as you
having honored it.

## Boundaries

This rule governs **when to pause vs proceed**, not what to build (that's `engineering-conventions.md`
R7/Ponytail) or how tersely to speak (`lean-output.md`/Caveman). It never overrides a HARD GATE: GATE-1
design sign-off, the GATE-1 rendered-design pixel approval, and a skill's `## Interview` are *supposed*
to pause — A2's "load-bearing decision" covers them. The goal is fewer **invented** stops, not a silent
agent that ships an unreviewed design. A `CLAUDE.md` instruction still wins over this rule.

## See also

`docs/lessons.md` (lesson #259, the seed) · `workflow-adherence.md` (GATE-0 baseline-sync order) ·
`engineering-conventions.md` (R7 — Ponytail, the effort-side sibling) · `gates.md` (the HARD GATES this
rule defers to) · `docs/workflow/ENFORCEMENT.md` (the CLOSE Stop-gates whose reasons now carry a
continuation hint).
