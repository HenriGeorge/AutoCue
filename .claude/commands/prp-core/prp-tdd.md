---
description: Test-driven development discipline - failing test first, red/green/refactor, testing anti-patterns to avoid
---

# Test-Driven Development (TDD)

> Adapted from obra/superpowers `test-driven-development` (testing anti-patterns inlined).
> TDD applies above all to core business logic — the invariant tests the project's
> PRPs/specs require are exactly the tests to write FIRST.

## Overview

Write the test first. Watch it fail. Write minimal code to pass.

**Core principle:** If you didn't watch the test fail, you don't know if it tests the right thing.

## When to Use

**Always:**
- New features
- Bug fixes
- Refactoring
- Behavior changes
- Every invariant listed in a PRP

**Exceptions (ask the user):**
- Throwaway prototypes
- Generated code
- Configuration files
- Pure styling/markup with no logic

Thinking "skip TDD just this once"? Stop. That's rationalization.

## The Iron Law

```
NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST
```

Write code before the test? Delete it. Start over. Don't keep it as "reference", don't
"adapt" it while writing tests. Implement fresh from tests.

## Red-Green-Refactor

### RED - Write Failing Test

Write one minimal test showing what should happen.

```typescript
// ✅ Good: clear name, tests real behavior, one thing
test('retries failed operations 3 times', async () => {
  let attempts = 0;
  const operation = () => {
    attempts++;
    if (attempts < 3) throw new Error('fail');
    return 'success';
  };
  const result = await retryOperation(operation);
  expect(result).toBe('success');
  expect(attempts).toBe(3);
});
```

**Requirements:**
- One behavior
- Clear name
- Real code (no mocks unless unavoidable)

### Verify RED - Watch It Fail

**MANDATORY. Never skip.**

Run the test. Confirm:
- Test fails (not errors)
- Failure message is expected
- Fails because the feature is missing (not typos)

**Test passes?** You're testing existing behavior. Fix the test.
**Test errors?** Fix the error, re-run until it fails correctly.

### GREEN - Minimal Code

Write the simplest code to pass the test. Don't add options, features, or "improvements"
beyond the test (YAGNI).

### Verify GREEN - Watch It Pass

**MANDATORY.** Run the test. Confirm: it passes, other tests still pass, output pristine.

**Test fails?** Fix the code, not the test.
**Other tests fail?** Fix now.

### REFACTOR - Clean Up

After green only: remove duplication, improve names, extract helpers. Keep tests green.
Don't add behavior.

### Repeat

Next failing test for the next behavior.

## Good Tests

| Quality | Good | Bad |
|---------|------|-----|
| **Minimal** | One thing. "and" in name? Split it. | `test('validates email and domain and whitespace')` |
| **Clear** | Name describes behavior | `test('test1')` |
| **Shows intent** | Demonstrates desired API | Obscures what code should do |

## Why Order Matters

Tests written after code pass immediately — and passing immediately proves nothing. They
might test the wrong thing, test implementation instead of behavior, or miss the edge cases
you forgot. Test-first forces you to see the test fail, proving it actually tests something.

Tests-after answer "what does this do?" Tests-first answer "what SHOULD this do?"

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Too simple to test" | Simple code breaks. Test takes 30 seconds. |
| "I'll test after" | Tests passing immediately prove nothing. |
| "Already manually tested" | Ad-hoc ≠ systematic. No record, can't re-run. |
| "Deleting X hours is wasteful" | Sunk cost fallacy. Keeping unverified code is technical debt. |
| "Need to explore first" | Fine. Throw away the exploration, start with TDD. |
| "Test hard = design unclear" | Listen to the test. Hard to test = hard to use. |
| "TDD will slow me down" | TDD is faster than debugging. |

## Red Flags - STOP and Start Over

- Code before test
- Test passes immediately
- Can't explain why the test failed
- Tests added "later"
- "I already manually tested it"
- "Keep as reference" or "adapt existing code"

**All of these mean: Delete code. Start over with TDD.**

## Bug Fix Integration

Bug found? Write a failing test reproducing it FIRST. Follow the TDD cycle. The test proves
the fix and prevents regression. Never fix bugs without a test.
(See `prp-core:prp-systematic-debug` — the failing test is Phase 4, step 1.)

## Verification Checklist

Before marking work complete (see `prp-core:prp-verify`):

- [ ] Every new function/method has a test
- [ ] Watched each test fail before implementing
- [ ] Each test failed for the expected reason (feature missing, not typo)
- [ ] Wrote minimal code to pass each test
- [ ] All tests pass
- [ ] Output pristine (no errors, warnings)
- [ ] Tests use real code (mocks only if unavoidable)
- [ ] Edge cases and errors covered

Can't check all boxes? You skipped TDD. Start over.

## When Stuck

| Problem | Solution |
|---------|----------|
| Don't know how to test | Write the wished-for API. Write the assertion first. Ask the user. |
| Test too complicated | Design too complicated. Simplify the interface. |
| Must mock everything | Code too coupled. Use dependency injection. |
| Test setup huge | Extract helpers. Still complex? Simplify the design. |

---

## Testing Anti-Patterns

**Iron laws:**

```
1. NEVER test mock behavior
2. NEVER add test-only methods to production classes
3. NEVER mock without understanding dependencies
```

### Anti-Pattern 1: Testing Mock Behavior

```typescript
// ❌ BAD: verifying the mock exists, not that the component works
expect(screen.getByTestId('sidebar-mock')).toBeInTheDocument();

// ✅ GOOD: test the real component, or don't mock it
expect(screen.getByRole('navigation')).toBeInTheDocument();
```

Gate: before asserting on any mock element, ask "am I testing real behavior or just mock
existence?" If the latter — delete the assertion or unmock the component.

### Anti-Pattern 2: Test-Only Methods in Production

Production classes polluted with methods only tests call (e.g. a `destroy()` used only in
`afterEach`). Move cleanup to test utilities. Gate: before adding any method, ask "is this
only used by tests?" If yes — don't add it.

### Anti-Pattern 3: Mocking Without Understanding

Over-mocking "to be safe" breaks behavior the test depends on (e.g. mocking a method whose
side effect the test needs). Gate: before mocking, ask what side effects the real method
has and whether the test depends on them. Mock at the lowest level that isolates the slow/
external part (mock the external `fetch`, not your own logic around it). If unsure, run
the test against the real implementation first, observe, then mock minimally.

### Anti-Pattern 4: Incomplete Mocks

Mock the COMPLETE data structure as it exists in reality, not just the fields your
immediate test uses — partial mocks fail silently when downstream code reads omitted
fields. Use full real-response fixtures checked into `tests/fixtures/`, never
hand-trimmed objects.

### Anti-Pattern 5: Tests as Afterthought

"Implementation complete, ready for testing" = wrong. Testing is part of implementation.
TDD would have caught this.

### When Mocks Become Too Complex

Warning signs: mock setup longer than test logic; mocking everything; test breaks when the
mock changes. Consider an integration test with real components instead — often simpler.

---

## Final Rule

```
Production code → test exists and failed first
Otherwise → not TDD
```

No exceptions without the user's permission.

## Related

- `prp-core:prp-verify` — fresh evidence before claiming green
- `prp-core:prp-systematic-debug` — failing test before any bug fix
- `vitest-best-practices` skill — vitest-specific patterns
