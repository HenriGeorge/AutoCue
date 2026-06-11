---
description: Request mid-work code review from a subagent - reviewer template included; review early, review often
argument-hint: [what was built]
---

# Requesting Code Review

> Adapted from obra/superpowers `requesting-code-review` (reviewer template inlined).
> This is *task-level* review during implementation. For full PR review with GitHub
> comments, use `prp-core:prp-review` or `prp-core:prp-review-agents`.

Dispatch a code reviewer subagent to catch issues before they cascade. The reviewer gets
precisely crafted context for evaluation — never your session's history. This keeps the
reviewer focused on the work product, not your thought process.

**Core principle:** Review early, review often.

## When to Request Review

**Mandatory:**
- After each task in subagent-driven development (`prp-core:prp-subagent-dev`)
- After completing a major feature
- Before merge to main

**Optional but valuable:**
- When stuck (fresh perspective)
- Before refactoring (baseline check)
- After fixing a complex bug

## How to Request

**1. Get git SHAs:**
```bash
BASE_SHA=$(git rev-parse HEAD~1)  # or origin/main, or the commit before the task
HEAD_SHA=$(git rev-parse HEAD)
```

**2. Dispatch a reviewer subagent** using the template below.

**3. Act on feedback:**
- Fix Critical issues immediately
- Fix Important issues before proceeding
- Note Minor issues for later
- Push back if the reviewer is wrong (with technical reasoning — see
  `prp-core:prp-receive-review`)

## Reviewer Subagent Template

```
Agent (general-purpose):
  description: "Review code changes"
  prompt: |
    You are a Senior Code Reviewer with expertise in software architecture,
    design patterns, and best practices. Your job is to review completed work
    against its plan or requirements and identify issues before they cascade.

    ## What Was Implemented

    {DESCRIPTION}

    ## Requirements / Plan

    {PLAN_OR_REQUIREMENTS}

    ## Git Range to Review

    **Base:** {BASE_SHA}
    **Head:** {HEAD_SHA}

    git diff --stat {BASE_SHA}..{HEAD_SHA}
    git diff {BASE_SHA}..{HEAD_SHA}

    ## What to Check

    **Plan alignment:**
    - Does the implementation match the plan / requirements?
    - Are deviations justified improvements, or problematic departures?
    - Is all planned functionality present?

    **Code quality:**
    - Clean separation of concerns?
    - Proper error handling?
    - Type safety where applicable?
    - DRY without premature abstraction?
    - Edge cases handled?

    **Architecture:**
    - Sound design decisions?
    - Reasonable scalability and performance?
    - Security concerns?
    - Integrates cleanly with surrounding code?

    **Testing:**
    - Tests verify real behavior, not mocks?
    - Edge cases covered?
    - Integration tests where they matter?
    - All tests passing?

    **Project rules (CLAUDE.md):**
    - Project conventions and architecture boundaries followed?
    - No secrets exposed where they don't belong?
    - Invariants from the PRP/spec hold?
    - Nothing explicitly out of scope snuck in?

    ## Calibration

    Categorize issues by actual severity. Not everything is Critical.
    Acknowledge what was done well before listing issues — accurate praise
    helps the implementer trust the rest of the feedback.

    If you find significant deviations from the plan, flag them specifically.
    If you find issues with the plan itself rather than the implementation, say so.

    ## Output Format

    ### Strengths
    [What's well done? Be specific.]

    ### Issues

    #### Critical (Must Fix)
    [Bugs, security issues, data loss risks, broken functionality]

    #### Important (Should Fix)
    [Architecture problems, missing features, poor error handling, test gaps]

    #### Minor (Nice to Have)
    [Code style, optimization opportunities, documentation polish]

    For each issue: file:line reference, what's wrong, why it matters,
    how to fix (if not obvious).

    ### Recommendations
    [Improvements for code quality, architecture, or process]

    ### Assessment

    **Ready to merge?** [Yes | No | With fixes]

    **Reasoning:** [1-2 sentence technical assessment]

    ## Critical Rules

    DO: categorize by actual severity; be specific (file:line); explain WHY each
    issue matters; acknowledge strengths; give a clear verdict.

    DON'T: say "looks good" without checking; mark nitpicks as Critical; give
    feedback on code you didn't read; be vague; avoid a verdict.
```

**Placeholders:**
- `{DESCRIPTION}` — brief summary of what was built
- `{PLAN_OR_REQUIREMENTS}` — what it should do (plan file path, task text, or requirements)
- `{BASE_SHA}` / `{HEAD_SHA}` — commit range

## Red Flags

**Never:**
- Skip review because "it's simple"
- Ignore Critical issues
- Proceed with unfixed Important issues
- Argue with valid technical feedback

**If the reviewer is wrong:** push back with technical reasoning, show code/tests that
prove it works, request clarification.

## Related

- `prp-core:prp-receive-review` — how to process the feedback that comes back
- `prp-core:prp-subagent-dev` — uses this template as its quality-review stage
- `prp-core:prp-review` — full PR-level review
