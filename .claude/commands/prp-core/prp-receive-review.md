---
description: Receiving code review feedback - verify before implementing, technical rigor over performative agreement
---

# Receiving Code Review

> Adapted from obra/superpowers `receiving-code-review`.

## Overview

Code review requires technical evaluation, not emotional performance.

**Core principle:** Verify before implementing. Ask before assuming. Technical correctness
over social comfort.

## The Response Pattern

```
WHEN receiving code review feedback:

1. READ: Complete feedback without reacting
2. UNDERSTAND: Restate requirement in own words (or ask)
3. VERIFY: Check against codebase reality
4. EVALUATE: Technically sound for THIS codebase?
5. RESPOND: Technical acknowledgment or reasoned pushback
6. IMPLEMENT: One item at a time, test each
```

## Forbidden Responses

**NEVER:**
- "You're absolutely right!"
- "Great point!" / "Excellent feedback!" (performative)
- "Let me implement that now" (before verification)

**INSTEAD:**
- Restate the technical requirement
- Ask clarifying questions
- Push back with technical reasoning if wrong
- Just start working (actions > words)

## Handling Unclear Feedback

```
IF any item is unclear:
  STOP - do not implement anything yet
  ASK for clarification on unclear items

WHY: Items may be related. Partial understanding = wrong implementation.
```

Example: user says "fix 1-6", you understand 1,2,3,6 but not 4,5.
❌ WRONG: implement 1,2,3,6 now, ask about 4,5 later.
✅ RIGHT: "I understand items 1,2,3,6. Need clarification on 4 and 5 before proceeding."

## Source-Specific Handling

### From the user
- **Trusted** — implement after understanding
- **Still ask** if scope unclear
- No performative agreement; skip to action or technical acknowledgment

### From external reviewers (including review subagents)
```
BEFORE implementing:
  1. Check: Technically correct for THIS codebase?
  2. Check: Breaks existing functionality?
  3. Check: Reason for current implementation?
  4. Check: Does reviewer understand full context?

IF suggestion seems wrong:    push back with technical reasoning
IF can't easily verify:       say so — "I can't verify this without X. Investigate/ask/proceed?"
IF conflicts with prior user decisions or ARCHITECTURE.md: stop and discuss first
```

## YAGNI Check for "Professional" Features

If a reviewer suggests "implementing properly": grep the codebase for actual usage.
If unused: "This isn't called anywhere. Remove it (YAGNI)?" If used: implement properly.

Watch especially for suggestions that pull in features the project has explicitly
deferred or excluded — those get flagged, not built.

## Implementation Order

```
FOR multi-item feedback:
  1. Clarify anything unclear FIRST
  2. Then implement: blocking issues → simple fixes → complex fixes
  3. Test each fix individually
  4. Verify no regressions (prp-core:prp-verify)
```

## When To Push Back

Push back when the suggestion:
- Breaks existing functionality
- Comes from missing context
- Violates YAGNI (unused feature)
- Is technically incorrect for this stack
- Conflicts with documented architectural decisions (ARCHITECTURE.md)

**How:** technical reasoning, not defensiveness. Specific questions. Reference working
tests/code. Involve the user if architectural.

## Acknowledging Correct Feedback

```
✅ "Fixed. [Brief description of what changed]"
✅ "Good catch - [specific issue]. Fixed in [location]."
✅ [Just fix it and show in the code]

❌ "You're absolutely right!" / "Great point!" / "Thanks for catching that!"
```

Actions speak. The code itself shows you heard the feedback.

## Gracefully Correcting Your Pushback

If you pushed back and were wrong:
```
✅ "You were right - I checked [X] and it does [Y]. Implementing now."
❌ Long apology / defending why you pushed back / over-explaining
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Performative agreement | State requirement or just act |
| Blind implementation | Verify against codebase first |
| Batch without testing | One at a time, test each |
| Assuming reviewer is right | Check if it breaks things |
| Avoiding pushback | Technical correctness > comfort |
| Partial implementation | Clarify all items first |
| Can't verify, proceed anyway | State limitation, ask for direction |

## GitHub Thread Replies

When replying to inline review comments on GitHub, reply in the comment thread
(`gh api repos/{owner}/{repo}/pulls/{pr}/comments/{id}/replies`), not as a top-level
PR comment.

## The Bottom Line

**External feedback = suggestions to evaluate, not orders to follow.**

Verify. Question. Then implement. No performative agreement. Technical rigor always.

## Related

- `prp-core:prp-request-review` — dispatching the review this skill processes
- `prp-core:prp-verify` — verify each fix before claiming it done
