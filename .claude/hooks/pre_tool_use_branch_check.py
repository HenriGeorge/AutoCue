#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse hook — block mutating actions on the default branch."""

import json
import re
import subprocess
import sys
from pathlib import Path

# Conservative allowlist of mutating Bash patterns to block on default branch
MUTATING_BASH_PATTERNS = [
    r"\bgit\s+commit\b",
    r"\bgit\s+add\s+[^-]",  # git add <file> but not git add --all (still blocked by safe_git_add.sh)
    r"\bnpm\s+run\s+generate\b",
    r"\bnpx\s+shadcn\b",
    r"\bprisma\s+generate\b",
    r"\bprisma\s+migrate\b",
]

# Tools that are always mutating (Write, Edit, MultiEdit)
MUTATING_TOOLS = {"Write", "Edit", "MultiEdit"}

# Match a leading `cd <path> && ...` or `cd <path>; ...` prefix in a Bash command.
# Used to detect when a mutating command is targeting a sibling worktree outside the
# main checkout — in which case we re-anchor the branch check to that path so the
# user can commit from a worktree on a feature branch even when the main checkout
# is still on `main`. See docs/035_sw-dev-lessons.md lesson #21.
_CD_PREFIX_RE = re.compile(r"^\s*cd\s+(\S+)\s*(?:&&|;)")


def _get_rebase_branch(cwd: str) -> str:
    """Read the original branch name from rebase state files.

    During ``git rebase``, HEAD is detached and ``git branch --show-current``
    returns empty.  However git stores the original branch ref in
    ``rebase-merge/head-name`` (merge-based rebase) or
    ``rebase-apply/head-name`` (apply-based / ``git am``).

    In a worktree, ``.git`` is a *file* pointing to the real git dir, so we
    use ``git rev-parse --git-dir`` to resolve the correct location.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        git_dir = result.stdout.strip()
        if not git_dir:
            return ""
        git_dir_path = (
            Path(git_dir)
            if Path(git_dir).is_absolute()
            else Path(cwd) / git_dir
        )
        for state_dir in ("rebase-merge", "rebase-apply"):
            head_name_file = git_dir_path / state_dir / "head-name"
            if head_name_file.is_file():
                ref = head_name_file.read_text().strip()
                if ref.startswith("refs/heads/"):
                    return ref[len("refs/heads/"):]
                return ref  # unexpected format -- return as-is
    except Exception:
        pass
    return ""


def get_current_branch(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        branch = result.stdout.strip()
        if branch:
            return branch
    except Exception:
        pass
    # Fallback: check if we are mid-rebase (detached HEAD with rebase state)
    return _get_rebase_branch(cwd)


def get_default_branch(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", "remote", "show", "origin"],
            capture_output=True, text=True, cwd=cwd, timeout=10,
        )
        for line in result.stdout.splitlines():
            if "HEAD branch" in line:
                return line.split(":")[-1].strip()
    except Exception:
        pass
    # Fallback: check refs
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True, text=True, cwd=cwd, timeout=5,
        )
        ref = result.stdout.strip()
        if ref:
            return ref.split("/")[-1]
    except Exception:
        pass
    return "main"


def is_mutating_bash(command: str) -> bool:
    return any(re.search(pattern, command) for pattern in MUTATING_BASH_PATTERNS)


def _is_outside_project(file_path: str, project_dir: str) -> bool:
    fp = Path(file_path).expanduser().resolve()
    pd = Path(project_dir).expanduser().resolve()
    try:
        fp.relative_to(pd)
        return False
    except ValueError:
        return True


def _extract_claude_worktree_root(file_path: str) -> str | None:
    """If *file_path* is inside a `.claude/worktrees/agent-XXXXX/` tree,
    return the worktree root directory.  Otherwise return None.

    Claude Code `isolation: "worktree"` creates worktrees at
    ``$PROJECT_DIR/.claude/worktrees/agent-XXXXX/``.  These live *inside*
    the main checkout, so ``_is_outside_project`` returns False.  We need
    to detect them separately and check their own branch.
    """
    fp = Path(file_path).expanduser().resolve()
    parts = fp.parts
    for i, part in enumerate(parts):
        if part == ".claude" and i + 2 < len(parts) and parts[i + 1] == "worktrees":
            # worktree root = everything up to and including the agent-XXXXX dir
            return str(Path(*parts[: i + 3]))
    return None


def _is_worktree_path(path: str) -> bool:
    """Return True if *path* looks like a git worktree (external or .claude-internal).

    Recognised patterns:
    - ``*-worktrees/*`` (external worktrees created by sw-dev / sw-team)
    - ``*/.claude/worktrees/*`` (Claude Code isolation worktrees)
    """
    p = str(Path(path).expanduser().resolve())
    return "-worktrees/" in p or "/.claude/worktrees/" in p


def _get_worktree_branch(worktree_path: str) -> str:
    """Best-effort branch detection for a worktree, even during rebase.

    Strategy:
    1. ``git branch --show-current`` -- works when not in detached HEAD.
       Falls back to rebase state files via ``_get_rebase_branch`` when
       HEAD is detached mid-rebase.
    2. ``git worktree list --porcelain`` -- reports the original branch even
       during an interactive rebase (detached HEAD).
    3. If the path looks like a known worktree pattern, return a sentinel
       ``"__worktree_detached__"`` to signal the caller that the path is
       a legitimate worktree in detached state (likely rebase).
    """
    resolved = str(Path(worktree_path).expanduser().resolve())

    # Strategy 1: normal branch detection (includes rebase fallback)
    branch = get_current_branch(resolved)
    if branch:
        return branch

    # Strategy 2: parse `git worktree list --porcelain` for the branch ref
    try:
        result = subprocess.run(
            ["git", "-C", resolved, "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=5,
        )
        # Find the block whose "worktree" line matches our resolved path
        current_block_matches = False
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                wt_path = line[len("worktree "):]
                current_block_matches = (
                    str(Path(wt_path).resolve()) == resolved
                )
            if current_block_matches and line.startswith("branch refs/heads/"):
                return line[len("branch refs/heads/"):]
    except Exception:
        pass

    # Strategy 3: heuristic -- if it is clearly a worktree path, allow it.
    # Nobody accidentally creates worktrees on the default branch.
    if _is_worktree_path(resolved):
        return "__worktree_detached__"

    return ""


def _extract_cd_target(command: str) -> str | None:
    """Return the path argument of a leading `cd <path> &&` / `cd <path>;` prefix.

    Used to detect when a mutating Bash command is targeting a sibling worktree
    rather than the main checkout, so the branch check can be re-anchored to
    that path. Returns None when there is no leading `cd` prefix or the form
    is unrecognised (e.g. quoted paths with spaces).
    """
    m = _CD_PREFIX_RE.match(command)
    return m.group(1) if m else None


def main():
    data = json.loads(sys.stdin.read())
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    # Get project dir from env or cwd
    import os
    cwd = os.environ.get("CLAUDE_PROJECT_DIR", ".")

    current_branch = get_current_branch(cwd)
    if not current_branch:
        # Can't determine branch — pass through
        return

    default_branch = get_default_branch(cwd)
    if current_branch != default_branch:
        # Not on default branch — all good
        return

    # On default branch — check if this is a mutating action
    if tool_name in MUTATING_TOOLS:
        file_path = tool_input.get("file_path", "")
        if file_path and _is_outside_project(file_path, cwd):
            return  # external file, not a repo mutation

        # Bug 1 fix: check if the file is inside a .claude/worktrees/ tree.
        # These are created by Claude Code `isolation: "worktree"` and live
        # inside the main checkout dir, but have their own branch.
        if file_path:
            wt_root = _extract_claude_worktree_root(file_path)
            if wt_root:
                wt_branch = _get_worktree_branch(wt_root)
                wt_default = get_default_branch(wt_root)
                if wt_branch and wt_branch != wt_default:
                    return  # .claude/worktrees/ agent on a feature branch

        _block(
            f"On default branch '{default_branch}' — run /dev-start <branch-name> first to create a feature branch.",
        )

    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if not is_mutating_bash(command):
            return

        # If the command is targeting a sibling worktree (via a leading
        # `cd <abs-path> &&` prefix) and that target is outside the main
        # checkout AND on a non-default branch, exempt it. This unblocks
        # commits from worktrees while the main checkout sits on `main`,
        # which is the common multi-worktree workflow. The Write/Edit
        # branch above already has the equivalent exemption via
        # `_is_outside_project(file_path, cwd)`.
        cd_target = _extract_cd_target(command)
        if cd_target:
            cd_resolved = str(Path(cd_target).expanduser().resolve())

            # External worktree (outside project dir)
            if _is_outside_project(cd_target, cwd):
                # Bug 2 fix: use _get_worktree_branch which handles
                # detached HEAD during rebase by falling back to
                # `git worktree list --porcelain` and path heuristics.
                target_branch = _get_worktree_branch(cd_resolved)
                target_default = get_default_branch(cd_resolved)
                if target_branch and target_branch != target_default:
                    return  # sibling worktree on a feature branch

            # .claude/worktrees/ inside project dir (Bug 1 for Bash)
            wt_root = _extract_claude_worktree_root(cd_resolved)
            if wt_root:
                wt_branch = _get_worktree_branch(wt_root)
                wt_default = get_default_branch(wt_root)
                if wt_branch and wt_branch != wt_default:
                    return  # .claude/worktrees/ agent on a feature branch

        _block(
            f"On default branch '{default_branch}' — run /dev-start <branch-name> first to create a feature branch.",
        )


def _block(reason: str):
    print(json.dumps({"decision": "block", "reason": reason}))  # noqa: T201
    sys.exit(2)


if __name__ == "__main__":
    main()
