---
description: Systematic debugging discipline - root cause before any fix, four phases, 3-strike architecture rule
argument-hint: [issue description]
---

# Systematic Debugging

> Adapted from obra/superpowers `systematic-debugging` (with root-cause-tracing,
> defense-in-depth, and condition-based-waiting techniques inlined). This is the standing
> *discipline* for any failure; for a deep one-off investigation artifact, use
> `prp-core:prp-debug`.

## Overview

Random fixes waste time and create new bugs. Quick patches mask underlying issues.

**Core principle:** ALWAYS find root cause before attempting fixes. Symptom fixes are failure.

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If you haven't completed Phase 1, you cannot propose fixes.

## When to Use

Use for ANY technical issue: test failures, bugs, unexpected behavior, performance problems,
build failures, integration issues.

**Use this ESPECIALLY when:**
- Under time pressure (emergencies make guessing tempting)
- "Just one quick fix" seems obvious
- You've already tried multiple fixes
- Previous fix didn't work
- You don't fully understand the issue

**Don't skip when:**
- Issue seems simple (simple bugs have root causes too)
- You're in a hurry (rushing guarantees rework)
- A validation failed mid-Ralph-loop (systematic is faster than thrashing)

## The Four Phases

You MUST complete each phase before proceeding to the next.

### Phase 1: Root Cause Investigation

**BEFORE attempting ANY fix:**

1. **Read Error Messages Carefully**
   - Don't skip past errors or warnings — they often contain the exact solution
   - Read stack traces completely; note line numbers, file paths, error codes

2. **Reproduce Consistently**
   - Can you trigger it reliably? What are the exact steps?
   - If not reproducible → gather more data, don't guess

3. **Check Recent Changes**
   - What changed that could cause this? Git diff, recent commits
   - New dependencies, config changes, environmental differences

4. **Gather Evidence in Multi-Component Systems**

   WHEN the system has multiple components (API → service → database, pipeline stage →
   stage), add diagnostic instrumentation BEFORE proposing fixes:

   ```
   For EACH component boundary:
     - Log what data enters the component
     - Log what data exits the component
     - Verify environment/config propagation
     - Check state at each layer

   Run once to gather evidence showing WHERE it breaks
   THEN analyze evidence to identify the failing component
   THEN investigate that specific component
   ```

5. **Trace Data Flow** (root-cause tracing — see Techniques below)
   - Where does the bad value originate?
   - What called this with the bad value?
   - Keep tracing up until you find the source
   - Fix at source, not at symptom

### Phase 2: Pattern Analysis

1. **Find Working Examples** — locate similar working code in this codebase
2. **Compare Against References** — if implementing a pattern, read the reference
   implementation COMPLETELY, not skimmed
3. **Identify Differences** — list every difference between working and broken, however
   small; don't assume "that can't matter"
4. **Understand Dependencies** — what settings, config, environment does this need?

### Phase 3: Hypothesis and Testing

1. **Form Single Hypothesis** — "I think X is the root cause because Y." Write it down.
2. **Test Minimally** — the SMALLEST possible change to test the hypothesis, one variable
   at a time
3. **Verify Before Continuing** — worked? → Phase 4. Didn't? → NEW hypothesis. DON'T stack
   fixes on top of fixes.
4. **When You Don't Know** — say "I don't understand X." Don't pretend. Research or ask.

### Phase 4: Implementation

1. **Create Failing Test Case** — simplest reproduction, automated if possible. MUST exist
   before fixing. Use `prp-core:prp-tdd` for writing proper failing tests.
2. **Implement Single Fix** — address the root cause. ONE change at a time. No "while I'm
   here" improvements.
3. **Verify Fix** — test passes? No other tests broken? Issue actually resolved? (Apply
   `prp-core:prp-verify` — fresh evidence.)
4. **If Fix Doesn't Work** — STOP. Count attempts. If < 3: return to Phase 1 with new
   information. **If ≥ 3: STOP and question the architecture (below).**

### The 3-Strike Architecture Rule

**Pattern indicating an architectural problem:**
- Each fix reveals a new shared-state/coupling problem somewhere else
- Fixes require "massive refactoring" to implement
- Each fix creates new symptoms elsewhere

**STOP and question fundamentals:**
- Is this pattern fundamentally sound?
- Are we sticking with it through sheer inertia?
- Should we refactor the architecture vs. continue fixing symptoms?

**Discuss with the user before attempting more fixes.** This is NOT a failed hypothesis —
this is a wrong architecture. In a Ralph loop: log it in the progress log, end the
iteration WITHOUT the completion signal, and surface the question.

## Red Flags - STOP and Follow Process

If you catch yourself thinking:
- "Quick fix for now, investigate later"
- "Just try changing X and see if it works"
- "Add multiple changes, run tests"
- "Skip the test, I'll manually verify"
- "It's probably X, let me fix that"
- "I don't fully understand but this might work"
- Proposing solutions before tracing data flow
- **"One more fix attempt" (when already tried 2+)**

**ALL of these mean: STOP. Return to Phase 1.**

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Issue is simple, don't need process" | Simple issues have root causes too. Process is fast for simple bugs. |
| "Emergency, no time for process" | Systematic debugging is FASTER than guess-and-check thrashing. |
| "Just try this first, then investigate" | First fix sets the pattern. Do it right from the start. |
| "I'll write the test after confirming the fix" | Untested fixes don't stick. Test first proves it. |
| "Multiple fixes at once saves time" | Can't isolate what worked. Causes new bugs. |
| "I see the problem, let me fix it" | Seeing symptoms ≠ understanding root cause. |
| "One more fix attempt" (after 2+ failures) | 3+ failures = architectural problem. Question the pattern. |

## Quick Reference

| Phase | Key Activities | Success Criteria |
|-------|---------------|------------------|
| **1. Root Cause** | Read errors, reproduce, check changes, gather evidence | Understand WHAT and WHY |
| **2. Pattern** | Find working examples, compare | Identify differences |
| **3. Hypothesis** | Form theory, test minimally | Confirmed or new hypothesis |
| **4. Implementation** | Create failing test, fix, verify | Bug resolved, tests pass |

## When Process Reveals "No Root Cause"

If systematic investigation reveals the issue is truly environmental, timing-dependent, or
external: document what you investigated, implement appropriate handling (retry, timeout,
clear error message), add logging for future investigation.

**But:** 95% of "no root cause" cases are incomplete investigation.

---

## Technique: Root-Cause Tracing

Bugs often manifest deep in the call stack. Your instinct is to fix where the error
appears — that's treating a symptom.

**Trace backward through the call chain until you find the original trigger, then fix at
the source:**

1. **Observe the symptom** — e.g. a record inserted with a fabricated default instead of `null`
2. **Find the immediate cause** — what code directly produces this?
3. **Ask: what called this?** — walk the chain up, one level at a time
4. **Keep tracing up** — what value was passed? Where did it come from?
5. **Find the original trigger** — fix there, not where the error surfaced

When you can't trace manually, add instrumentation BEFORE the dangerous operation
(`console.error` in tests — loggers may be suppressed), capture `new Error().stack`,
include context (params, cwd, env), then run and grep the output.

**NEVER fix just where the error appears.**

## Technique: Defense-in-Depth Validation

After finding the root cause, validate at EVERY layer the bad data passed through — single
checks get bypassed by other code paths, refactoring, or mocks:

- **Layer 1 — entry point:** reject obviously invalid input at the API boundary
  (route handler / server action)
- **Layer 2 — business logic:** ensure the data makes sense for the operation (`lib/`)
- **Layer 3 — environment guards:** prevent dangerous operations in specific contexts
  (DB constraints — check clauses, enums — are the final guard)
- **Layer 4 — debug instrumentation:** capture context for forensics

Single validation = "we fixed the bug". Multiple layers = "we made the bug impossible".

## Technique: Condition-Based Waiting

Flaky tests usually guess at timing with arbitrary delays. Wait for the actual condition
instead:

```typescript
// ❌ BEFORE: guessing at timing
await new Promise(r => setTimeout(r, 50));
expect(getResult()).toBeDefined();

// ✅ AFTER: waiting for the condition
await waitFor(() => getResult() !== undefined, 'result available');
```

Generic polling helper: loop checking the condition every ~10ms with an overall timeout
that throws a descriptive error. Arbitrary timeouts are only acceptable when testing
actual timing behavior (debounce/throttle) — and then documented with WHY.

---

## Related

- `prp-core:prp-debug` — deep one-off root-cause investigation with artifact output
- `prp-core:prp-tdd` — failing test before the fix (Phase 4, step 1)
- `prp-core:prp-verify` — fresh evidence before claiming the fix worked
