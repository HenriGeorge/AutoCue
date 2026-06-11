# Agent Rules

Rules for all autonomous agents working on Resonanz Records.

## Scope
- Fix ONLY what the task describes. No refactoring, no "improvements", no scope creep.
- Prefer the simplest solution. If your change is >50 lines, consider a simpler approach first.
- Do NOT edit CLAUDE.md or docs/ unless the task explicitly requires it.
- Do NOT create standalone docs commits — docs ride with the code change that caused them.

## Commits & PRs
- Conventional commits: `<type>(<scope>): <summary>` — imperative mood, max 72 chars.
- Always include `Closes #N` in the commit body when resolving an issue.
- PR descriptions must include: what changed, why, and a step-by-step test plan.
- Always `git add <specific-files>` — never `git add .` or `git add -A`.

## Dedup
- Before implementing, check for existing work: `gh pr list --state all --search "<keywords>"` and `git log --oneline -20`.
- If already done, close with a reference — do not re-implement.

## Safety
- Never commit `.env` files or credentials.
- Never run destructive DB operations without explicit user confirmation.
- Never force-push to main.

## Coordination
- Always start fresh: `git fetch origin && git rebase origin/main` before any work.
- ALL work uses feature branches (`feature/*`, `fix/*`, `chore/*`). Never commit directly to `main`.
- Before editing a shared file, check for open PRs touching it: `gh pr list --state open`.

## Autonomy Model

The controller session acts as CTO — makes technical and architectural calls autonomously with stated rationale. Only escalates to Henri for:
- Irreversible destructive operations
- Legal or licensing decisions
- Subjective taste calls only Henri can rank (naming, copy, feature priority)
- Resource commitments not already authorized

**Decide, don't ask.** Replace "should we X or Y?" with "Decision: X, because {rationale}. Alternative Y rejected because {reason}." Proceed unless it meets escalation criteria above.
