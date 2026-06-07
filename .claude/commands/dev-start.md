---
description: Start a new feature branch with worktree and state marker (AutoCue port of SplitWave's /dev-start)
argument-hint: <branch-name>
---

# Dev Start (AutoCue)

**Branch**: $ARGUMENTS

---

## Your Mission

Set up a clean feature branch in an isolated git worktree so multiple agents can work in parallel without stepping on each other. Write a branch-ready state marker so downstream commands (`/dev-prd`, `/dev-plan`, future) can verify the workflow was followed.

Ported from SplitWave's `/dev-start`. The SplitWave-specific doc-sync hook (alembic + sub-router prefix mapping + separate UI repo) does not apply to AutoCue — AutoCue has a single `routes.py`, no alembic, no separate frontend repo. Doc-sync is stubbed for now.

---

## Phase 1: PARSE BRANCH NAME

If `$ARGUMENTS` is empty:
- Derive a slug from any pending task context and show it: "Suggested branch: `feat/my-slug` — confirm or provide a different name"
- STOP and wait for input if no slug can be derived

Strip any leading `feature/`, `feat/`, `fix/`, `chore/`, `docs/` prefix if the user included it — then re-add the appropriate prefix based on the kind of change (default `feat/`).

---

## Phase 2: DETECT DEFAULT BRANCH

```bash
git remote show origin | grep 'HEAD branch' | awk '{print $NF}'
```

Store as `{default-branch}`. Fallback: `main`.

---

## Phase 3: SYNC DEFAULT BRANCH

```bash
git fetch origin
git checkout {default-branch}
git pull --rebase
```

If pull fails due to local changes on the default branch (it shouldn't — main should always be clean), STOP and warn.

---

## Phase 4: GUARD AGAINST CONFLICTS

Check if branch already exists:
```bash
git branch --list {branch-name}
git worktree list
```

If the branch exists locally or in a worktree → **STOP**:
> "Branch `{branch-name}` already exists. Use a different name or clean up the existing branch first."

Check worktree path:
```bash
ls ../AutoCue-worktrees/{branch-name} 2>/dev/null
```

If worktree path exists → **STOP**:
> "Worktree path `../AutoCue-worktrees/{branch-name}` already exists."

Check for similar open PRs (warn only, don't block):
```bash
gh pr list --state open --search "{branch-name}" --limit 5
```

If results found → show warning but continue.

---

## Phase 5: CREATE WORKTREE

```bash
mkdir -p ../AutoCue-worktrees
git worktree add ../AutoCue-worktrees/{branch-name} -b {branch-name}
```

---

## Phase 6: DOC SYNC CHECK

Run the AutoCue-specific doc-sync check (much lighter than SplitWave's — no alembic, no sub-router maze, no separate UI repo).

```bash
bash .claude/hooks/check_doc_sync.sh
```

What it checks:
- Every `@router.<method>("/path")` in `autocue/serve/routes.py` appears in `CLAUDE.md`
- Every `/api/...` mentioned in `CLAUDE.md` still exists in `routes.py` (no stale doc entries)
- Every Python module under `autocue/` (excluding `__init__.py` / `__main__.py`) is mentioned in `CLAUDE.md`
- Every `tests/test_*.py` is mentioned in `CLAUDE.md`'s test inventory

Show the output. **Warn but don't block** unless run with `--strict`. Gaps surface as a clear list so you can decide whether to update `CLAUDE.md` before continuing.

---

## Phase 7: WRITE STATE MARKER

Write `.claude/state/{branch-name}/branch-ready.json` **inside the new worktree**:

```json
{
  "branch": "{branch-name}",
  "timestamp": "{ISO-8601}",
  "default_branch": "{default-branch}",
  "worktree_path": "../AutoCue-worktrees/{branch-name}",
  "approved_by": "user"
}
```

Create the directory first: `mkdir -p ../AutoCue-worktrees/{branch-name}/.claude/state/{branch-name}`

---

## Phase 8: OUTPUT

```
✅ Branch '{branch-name}' ready
   Worktree: ../AutoCue-worktrees/{branch-name}
   cd into the worktree to continue working on this feature.

Next: /prd-creator <idea>   — write a PRD for this feature
   or /prp-core:prp-plan <prd-path>  — if PRD already exists
```

---

## Notes

- Worktrees live at `../AutoCue-worktrees/<branch-name>` (sibling to the main checkout) so each branch has its own filesystem path. Multiple Claude agents in different worktrees can run `autocue serve` on different ports without conflict.
- Branch-ready state marker is the contract: downstream `/dev-*` commands should refuse to run if the marker is missing.
- To clean up: `git worktree remove ../AutoCue-worktrees/<branch-name>` then `git branch -d <branch-name>`.
