#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""SessionStart hook — load project status into context, incl. GATE-0 behind-count (H4) + HANDOFF.

SessionStart is INJECT-ONLY (researcher §4) — it can never block, so every code path here must
exit 0 and never raise. Best-effort `git fetch` (short timeout, fails SILENTLY offline), then
compute the local branch's behind-count vs the default remote branch and inject it as an
additionalContext line. Also surfaces the PRIMARY worktree's HANDOFF.md (session continuity) so a
fresh cc-worktrees session reads it even though HANDOFF.md is gitignored and lives only in the main
checkout. Gracefully skips (no crash, no exception) when: not a git repo, detached HEAD / no branch,
no `origin` remote configured, no HANDOFF.md, or any git/read call fails.

Template — instantiated per project by setup.sh (AutoCue / {{DB_CHECK_SECTION}} placeholders).
claude_template dogfoods its own copy at .claude/hooks/session_start.py; regenerate it after editing
this template with:
    sed -e 's|AutoCue|claude_template|g' -e '/{{DB_CHECK_SECTION}}/d' \
        hooks/session_start.py.tmpl > .claude/hooks/session_start.py
"""

import os
import subprocess


def _run(args, cwd, timeout=5):
    """subprocess.run wrapper that never raises — returns None on any failure."""
    try:
        return subprocess.run(args, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    except Exception:
        return None


def _default_branch(project_dir):
    """Best-effort remote default branch name (e.g. 'main'), or None if it can't be determined."""
    ref = _run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], project_dir)
    if ref is not None and ref.returncode == 0 and ref.stdout.strip():
        return ref.stdout.strip().rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        check = _run(["git", "rev-parse", "--verify", "--quiet", f"origin/{candidate}"], project_dir)
        if check is not None and check.returncode == 0:
            return candidate
    return None


def _gate0_line(project_dir):
    """Returns a GATE-0 additionalContext line, or None to skip (never raises)."""
    inside = _run(["git", "rev-parse", "--is-inside-work-tree"], project_dir, timeout=3)
    if inside is None or inside.returncode != 0 or inside.stdout.strip() != "true":
        return None  # not a git repo — H4.5

    branch_r = _run(["git", "branch", "--show-current"], project_dir, timeout=3)
    branch = branch_r.stdout.strip() if branch_r is not None else ""
    if not branch:
        return "GATE-0: detached HEAD or no current branch — skipping behind-count."  # H4.3

    remote_r = _run(["git", "remote", "get-url", "origin"], project_dir, timeout=3)
    if remote_r is None or remote_r.returncode != 0 or not remote_r.stdout.strip():
        return "GATE-0: no origin remote configured — skipping behind-count."  # H4.3

    fetch_r = _run(["git", "fetch", "--quiet", "origin"], project_dir, timeout=8)
    fetch_ok = fetch_r is not None and fetch_r.returncode == 0  # H4.4 fail-open on offline/timeout

    default_branch = _default_branch(project_dir)
    if not default_branch:
        return "GATE-0: no origin default branch found — skipping behind-count."

    ref = f"origin/{default_branch}"
    exists = _run(["git", "rev-parse", "--verify", "--quiet", ref], project_dir, timeout=3)
    if exists is None or exists.returncode != 0:
        note = " (git fetch failed — offline?)" if not fetch_ok else ""
        return f"GATE-0: no {ref} ref available{note} — skipping behind-count."

    count_r = _run(["git", "rev-list", "--count", f"HEAD..{ref}"], project_dir, timeout=3)
    if count_r is None or count_r.returncode != 0:
        return None  # can't compute — skip silently rather than guess

    try:
        n = int(count_r.stdout.strip() or "0")
    except ValueError:
        return None

    suffix = " (git fetch failed — count may be stale)" if not fetch_ok else ""
    if n > 0:
        return (
            f"⚠ GATE-0: you are {n} commit(s) behind {default_branch}{suffix} — rebase before "
            "building (docs/WORKFLOW.md Phase 0)."
        )
    return f"✓ GATE-0: up to date with {default_branch}{suffix}."


def _stale_local_main_line(project_dir):
    """#117 — warn when the LOCAL default-branch ref has drifted from origin/<branch>.

    A stale local `main` (common in cc-worktrees: `git fetch` updates origin/main but not the local
    ref) makes `git diff main` compare against merged-away state — the exact misread this closes.
    Inject-only, never raises; returns None to skip (no default branch, either ref missing, refs
    equal, or any failure). Reuses the origin/<branch> that _gate0_line already fetched — no 2nd
    fetch here.
    """
    default_branch = _default_branch(project_dir)
    if not default_branch:
        return None
    local = _run(["git", "rev-parse", "--verify", "--quiet", default_branch], project_dir, timeout=3)
    remote = _run(
        ["git", "rev-parse", "--verify", "--quiet", f"origin/{default_branch}"], project_dir, timeout=3
    )
    if local is None or remote is None or local.returncode != 0 or remote.returncode != 0:
        return None
    lsha = local.stdout.strip()
    rsha = remote.stdout.strip()
    if not lsha or not rsha or lsha == rsha:
        return None
    return (
        f"⚠ GATE-0: your LOCAL '{default_branch}' ref ({lsha[:7]}) differs from "
        f"origin/{default_branch} ({rsha[:7]}) — it's stale. Run `git fetch` and rebase it, and "
        f"always diff against `origin/{default_branch}`, never bare local `{default_branch}`."
    )


def _primary_worktree(project_dir):
    """Absolute path of the PRIMARY worktree (first entry of `git worktree list`), or None.

    Works whether the hook runs in the main checkout or a sibling cc-worktrees worktree — the first
    porcelain entry is always the primary, which is where gitignored files like HANDOFF.md live.
    """
    r = _run(["git", "worktree", "list", "--porcelain"], project_dir, timeout=3)
    if r is None or r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):].strip()
    return None


def _handoff_context(project_dir):
    """Return the primary worktree's HANDOFF.md (+ TASKS.md) as a labelled block, or None.

    Best-effort and never raises: skips silently on any failure or when the file is absent/empty.
    Caps very large files (head + a pointer) so a giant handoff can't flood the context window.
    """
    primary = _primary_worktree(project_dir)
    if not primary:
        return None

    MAX_LINES = 200
    blocks = []
    for fname in ("HANDOFF.md", "TASKS.md"):
        path = os.path.join(primary, fname)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except Exception:
            continue
        if not text.strip():
            continue
        lines = text.splitlines()
        if len(lines) > MAX_LINES:
            text = "\n".join(lines[:MAX_LINES]) + f"\n… [truncated — full file at {path}]"
        blocks.append(f"[{fname} — session continuity, from {path}]\n{text}")

    return "\n\n".join(blocks) if blocks else None


def main():
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    status_lines = ["[AutoCue Session Context]"]

    # Git branch
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=project_dir,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=project_dir,
        ).stdout.strip()
        status_lines.append(f"Branch: {branch}" + (" (dirty)" if dirty else " (clean)"))
    except Exception:
        pass

    # H4 — GATE-0 behind-count injector. Fully isolated try/except: a bug here must never crash
    # the hook or block the session (SessionStart is inject-only, never blocking).
    try:
        gate0 = _gate0_line(project_dir)
        if gate0:
            status_lines.append(gate0)
    except Exception:
        pass

    # #117 — GATE-0 code-freshness: warn when the LOCAL default-branch ref is stale vs origin/<branch>.
    # Isolated try/except (inject-only, never blocks).
    try:
        stale = _stale_local_main_line(project_dir)
        if stale:
            status_lines.append(stale)
    except Exception:
        pass

    # Project-specific status (customize this section)
    # Example: DB stats, file counts, pipeline progress
    # {{DB_CHECK_SECTION}}

    # HANDOFF surfacing — isolated try/except so a bug here never crashes the inject-only hook.
    handoff = None
    try:
        handoff = _handoff_context(project_dir)
    except Exception:
        handoff = None

    print("\n".join(status_lines))
    if handoff:
        print("\n" + handoff)


if __name__ == "__main__":
    main()
