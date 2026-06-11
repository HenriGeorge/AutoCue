---
description: Subagent-driven plan execution - fresh subagent per task with two-stage review (spec, then quality); alternative engine to Ralph for long slices
argument-hint: [plan file path]
---

# Subagent-Driven Development

> Adapted from obra/superpowers `subagent-driven-development` (prompt templates inlined).
> This is an **alternative execution engine to `prp-core:prp-ralph`** for the same plans.
> Use it when a slice is long enough that a single-session Ralph loop would degrade from
> context rot — fresh subagents per task sidestep that. For short slices, Ralph's state
> file + completion protocol is simpler.

Execute a plan by dispatching a fresh subagent per task, with two-stage review after each:
spec compliance review first, then code quality review.

**Why subagents:** Each task gets isolated context with precisely crafted instructions.
Subagents never inherit your session's history — you construct exactly what they need.
This also preserves your own context for coordination.

**Core principle:** Fresh subagent per task + two-stage review (spec then quality) = high
quality, fast iteration.

**Continuous execution:** Do not pause to check in with the user between tasks. The only
reasons to stop: a BLOCKED status you cannot resolve, ambiguity that genuinely prevents
progress, or all tasks complete.

## When to Use

- Have an implementation plan (from `prp-core:prp-plan`)? If no → write one first.
- Tasks mostly independent? If tightly coupled → Ralph or manual execution.
- Slice long (many tasks, hours of work)? → this. Short slice? → `prp-core:prp-ralph`.

**Before starting (per CLAUDE.md):** work on a feature branch, never main. If dispatching
parallel work, run the dedup/file-overlap check first.

## The Process

1. Read the plan ONCE; extract ALL tasks with full text + context. Create a task list.
2. Per task:
   a. Dispatch implementer subagent (template below) with the FULL task text — never make
      the subagent read the plan file.
   b. Answer any questions it raises before it starts.
   c. Implementer implements (TDD per `prp-core:prp-tdd`), tests, commits, self-reviews.
   d. Dispatch **spec compliance reviewer** (template below). Issues → implementer fixes →
      re-review. Loop until ✅.
   e. Dispatch **code quality reviewer** (template below) — only AFTER spec compliance ✅.
      Issues → implementer fixes → re-review. Loop until approved.
   f. Mark task complete.
3. After all tasks: dispatch a final code reviewer over the entire implementation
   (`prp-core:prp-request-review`), then finish via `prp-core:prp-pr`.

## Model Selection

Use the least powerful model that can handle each role:
- **Mechanical tasks** (isolated functions, clear spec, 1-2 files) → fast, cheap model
- **Integration/judgment tasks** (multi-file, pattern matching, debugging) → standard model
- **Architecture, design, review** → most capable model

## Handling Implementer Status

- **DONE:** proceed to spec compliance review.
- **DONE_WITH_CONCERNS:** read the concerns first. Correctness/scope concerns → address
  before review. Observations → note and proceed.
- **NEEDS_CONTEXT:** provide the missing context, re-dispatch.
- **BLOCKED:** assess — context problem → more context, same model; reasoning problem →
  more capable model; task too large → split it; plan wrong → escalate to the user.
  **Never** force the same model to retry without changing something.

## Red Flags

**Never:**
- Start implementation on main (use a feature branch)
- Skip either review stage, or run quality review before spec compliance is ✅
- Proceed with unfixed issues; skip the re-review loop
- Dispatch multiple implementation subagents in parallel on overlapping files
- Make a subagent read the plan file (provide full text)
- Ignore subagent questions
- Let implementer self-review replace actual review (both are needed)
- Trust "success" reports — verify the diff yourself (`prp-core:prp-verify`)

---

## Template: Implementer Subagent

```
Agent (general-purpose):
  description: "Implement Task N: [task name]"
  prompt: |
    You are implementing Task N: [task name]

    ## Task Description

    [FULL TEXT of task from plan - paste it here, don't make subagent read file]

    ## Context

    [Scene-setting: where this fits, dependencies, architectural context.
     Include the relevant CLAUDE.md constraints and project invariants.]

    ## Before You Begin

    If you have questions about requirements, approach, dependencies, or anything
    unclear in the task description — **ask them now.**

    ## Your Job

    Once you're clear on requirements:
    1. Implement exactly what the task specifies
    2. Write tests first (TDD: failing test → minimal code → green)
    3. Verify implementation works (fresh command runs, read the output)
    4. Commit your work (named files only — no bulk staging)
    5. Self-review (see below)
    6. Report back

    Work from: [directory]

    **While you work:** if you encounter something unexpected or unclear, ask.
    Don't guess or make assumptions.

    ## Code Organization

    - Follow the file structure defined in the plan
    - Each file should have one clear responsibility with a well-defined interface
    - If a file you're creating grows beyond the plan's intent, stop and report
      DONE_WITH_CONCERNS — don't split files on your own without plan guidance
    - Follow established patterns; don't restructure outside your task

    ## When You're in Over Your Head

    It is always OK to stop and say "this is too hard for me." Bad work is worse
    than no work.

    **STOP and escalate when:**
    - The task requires architectural decisions with multiple valid approaches
    - You need to understand code beyond what was provided and can't find clarity
    - You feel uncertain whether your approach is correct
    - You've been reading file after file without progress

    **How:** report status BLOCKED or NEEDS_CONTEXT with what you're stuck on,
    what you've tried, and what kind of help you need.

    ## Before Reporting Back: Self-Review

    - Completeness: fully implemented everything in the spec? missed requirements?
      unhandled edge cases?
    - Quality: best work? clear names? clean and maintainable?
    - Discipline: avoided overbuilding (YAGNI)? only built what was requested?
      followed existing patterns?
    - Testing: tests verify real behavior (not mock behavior)? followed TDD?

    Fix anything you find BEFORE reporting.

    ## Report Format

    - **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
    - What you implemented (or attempted, if blocked)
    - What you tested and test results (actual output)
    - Files changed
    - Self-review findings (if any)
    - Any issues or concerns

    Never silently produce work you're unsure about.
```

## Template: Spec Compliance Reviewer Subagent

**Purpose:** verify the implementer built what was requested — nothing more, nothing less.

```
Agent (general-purpose):
  description: "Review spec compliance for Task N"
  prompt: |
    You are reviewing whether an implementation matches its specification.

    ## What Was Requested

    [FULL TEXT of task requirements]

    ## What Implementer Claims They Built

    [From implementer's report]

    ## CRITICAL: Do Not Trust the Report

    The implementer's report may be incomplete, inaccurate, or optimistic.
    Verify everything independently:
    - Read the actual code they wrote
    - Compare actual implementation to requirements line by line
    - Check for missing pieces they claimed to implement
    - Look for extra features they didn't mention

    ## Your Job

    **Missing requirements:** everything requested implemented? anything skipped?
    claimed-but-not-implemented?

    **Extra/unneeded work:** things built that weren't requested? over-engineering?
    "nice to haves" not in spec?

    **Misunderstandings:** requirements interpreted differently than intended?
    wrong problem solved? right feature, wrong way?

    Verify by reading code, not by trusting the report.

    Report:
    - ✅ Spec compliant (if everything matches after code inspection)
    - ❌ Issues found: [specifically what's missing or extra, with file:line references]
```

## Template: Code Quality Reviewer Subagent

**Purpose:** verify the implementation is well-built. **Only dispatch after spec
compliance ✅.**

Use the reviewer template in `prp-core:prp-request-review` with:
- `{DESCRIPTION}`: task summary from the implementer's report
- `{PLAN_OR_REQUIREMENTS}`: Task N from [plan file]
- `{BASE_SHA}`: commit before the task — `git rev-parse HEAD~1` or the recorded SHA
- `{HEAD_SHA}`: current commit

In addition to standard quality concerns, check:
- Does each file have one clear responsibility with a well-defined interface?
- Are units decomposed so they can be understood and tested independently?
- Does the implementation follow the file structure from the plan?
- Did this change create or significantly grow large files? (Don't flag pre-existing
  sizes — focus on what this change contributed.)
- Project invariants from CLAUDE.md and the PRP respected? No secrets exposed?

Reviewer returns: Strengths, Issues (Critical/Important/Minor), Assessment.

---

## Related

- `prp-core:prp-ralph` — single-session loop engine (default for short slices)
- `prp-core:prp-plan` — produces the plan this executes
- `prp-core:prp-request-review` — reviewer template + when to request review
- `prp-core:prp-verify` — verify subagent claims against the actual diff
- `prp-core:prp-pr` — finish the branch
