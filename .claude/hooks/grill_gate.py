#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PreToolUse grill-gate — block committing a spec/plan/ADR with no non-empty `## Grill findings`.

Fires on a `git commit` that stages a file under docs/superpowers/specs/**,
docs/superpowers/plans/** (issue #116 — a plan is a second GATE-1 artifact that must be grilled
post-writing-plans), or docs/decisions/** (an ADR). Each such file must contain a NON-EMPTY
`## Grill findings` section. Bypass a genuinely trivial doc with `WORKFLOW:no-grill` in the commit
command. Fail-open on any error.

NOTE (#146-T3): this hook does NOT (and structurally cannot) cover the plan-mode path
(~/.claude/plans/*.md) — that directory lives outside any project's git repo, so a plan written
there can never appear in `git diff --cached` for a project commit; widening SPEC_PREFIXES to
include it would be a no-op. `grill_nudge.py` (PostToolUse, WARN-only) is the mechanism that covers
that path instead, firing on the Write/Edit itself rather than at commit time.
"""
import json
import os
import re
import shlex
import subprocess
import sys

SPEC_PREFIXES = (
    "docs/superpowers/specs/",
    "docs/superpowers/plans/",  # issue #116 — grill the plan too, not just the spec/ADR
    "docs/decisions/",
)
GIT_COMMIT_RE = re.compile(r"\bgit\s+commit\b")
GIT_DASH_C_RE = re.compile(r"\bgit\s+-C\s+(\S+)")
SHELL_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")
ADD_STAGES_EVERYTHING_FLAGS = ("-A", "--all", "-u", "--update")
COMMIT_ALL_LONG = "--all"
BYPASS_SENTINEL = "WORKFLOW:no-grill"
# A non-empty `## Grill findings` section: the header, then at least one line of real content
# before EOF or the next `## ` header.
# #148: tolerate trailing text on the heading line (e.g. an annotation like
# "## Grill findings   (superpowers:grill-me, this session — required GATE-1 leg)") — the \b after
# "findings" still requires the literal word, `.*` just no longer forces end-of-line right after it.
GRILL_HEADER_RE = re.compile(r"^\s*##\s+Grill findings\b.*$", re.MULTILINE)
# A "real finding" line records a disposition, is a `- C1 —` finding bullet, or (issue #108) is ANY
# content bullet (`- <text>` / `* <text>`) — so a grill section written as prose bullets ("- design
# sound; no material weaknesses") ALLOWs instead of false-blocking. The disposition vocabulary is
# widened past the original fixed/parked/deferred/accepted to the words real grills actually use.
# A copied TEMPLATE with an UNFILLED table still BLOCKS: its skeleton has NO content bullets (only the
# instruction blockquote, the column-header row, the `|---|` separator, and an empty-cell data row).
DISPOSITION_RE = re.compile(
    r"\b(fixed|parked|deferred|accepted|resolved|mitigated|addressed|acknowledged|wontfix|"
    r"won't\s*fix|noted|ruled)\b",
    re.IGNORECASE,
)
FINDING_BULLET_RE = re.compile(r"^\s*[-*]\s*C\d+\b")
# Any bullet with real content after the marker — the general widening for prose-style grills.
CONTENT_BULLET_RE = re.compile(r"^\s*[-*]\s+\S")


def _resolve_git_cwd(segment: str, cwd: str) -> str:
    m = GIT_DASH_C_RE.search(segment)
    if not m:
        return cwd
    target = m.group(1).strip("'\"")
    return target if os.path.isabs(target) else os.path.join(cwd or ".", target)


def _run_git(cwd: str, *args) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd or None, capture_output=True, text=True,
            timeout=5, check=False,
        )
    except Exception:  # noqa: BLE001 - fail open
        return None
    return r.stdout if r.returncode == 0 else None


def _staged_names(cwd: str) -> list:
    out = _run_git(cwd, "diff", "--cached", "--name-only")
    return [p.strip() for p in out.splitlines() if p.strip()] if out else []


def _add_segment_stages_everything(segment: str) -> bool:
    m = re.search(r"\bgit\s+add\b(.*)$", segment)
    if not m:
        return False
    try:
        tokens = shlex.split(m.group(1))
    except ValueError:
        tokens = m.group(1).split()
    if any(t in ADD_STAGES_EVERYTHING_FLAGS for t in tokens):
        return True
    return any(t in (".", "./") for t in tokens if not t.startswith("-"))


def _explicit_add_paths(segment: str) -> list:
    m = re.search(r"\bgit\s+add\b(.*)$", segment)
    if not m:
        return []
    try:
        tokens = shlex.split(m.group(1))
    except ValueError:
        tokens = m.group(1).split()
    return [t for t in tokens if t and not t.startswith("-")]


def _would_be_staged_everything(cwd: str) -> list:
    out = _run_git(cwd, "status", "--porcelain", "--untracked-files=all")
    if not out:
        return []
    paths = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        rest = line[3:].strip()
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1].strip()
        if len(rest) >= 2 and rest[0] == '"' and rest[-1] == '"':
            rest = rest[1:-1]
        if rest:
            paths.append(rest)
    return paths


def _collect_staged(command: str, cwd: str) -> list:
    """Everything already staged, PLUS what a same-command chained `git add` would stage."""
    paths = list(_staged_names(cwd))
    for segment in SHELL_SEGMENT_SPLIT_RE.split(command):
        if not re.search(r"\bgit\s+add\b", segment):
            continue
        if _add_segment_stages_everything(segment):
            paths.extend(_would_be_staged_everything(cwd))
        else:
            paths.extend(_explicit_add_paths(segment))
    return paths


def _is_spec_path(path: str) -> bool:
    norm = path.lstrip("./")
    if not norm.startswith(SPEC_PREFIXES):
        return False
    base = os.path.basename(norm)
    # TEMPLATE* files intentionally carry an empty findings table — never self-block them.
    return not base.upper().startswith("TEMPLATE")


def _commit_stages_all(command: str) -> bool:
    """True if the `git commit` command auto-stages modified tracked files (-a / --all / -am)."""
    seg = next(
        (s for s in SHELL_SEGMENT_SPLIT_RE.split(command) if GIT_COMMIT_RE.search(s)),
        command,
    )
    m = re.search(r"\bcommit\b(.*)$", seg)
    if not m:
        return False
    try:
        tokens = shlex.split(m.group(1))
    except ValueError:
        tokens = m.group(1).split()
    for t in tokens:
        if t == COMMIT_ALL_LONG:
            return True
        if t.startswith("--"):
            continue
        if t.startswith("-") and len(t) > 1:
            for ch in t[1:]:
                if ch == "a":
                    return True
                if ch == "m":  # -m consumes the remainder of the cluster as the message
                    break
    return False


def _modified_tracked(cwd: str) -> list:
    """Tracked files with unstaged modifications — what a `git commit -a` would additionally stage."""
    out = _run_git(cwd, "diff", "--name-only")
    return [p.strip() for p in out.splitlines() if p.strip()] if out else []


def _file_text(cwd: str, path: str, prefer_worktree: bool = False) -> str | None:
    """Text of the version that will be committed.

    Normal commits use the STAGED blob (what `git add` recorded). A `git commit -a` stages the
    WORKTREE version at commit time, so for that path the worktree file is authoritative and must
    win over the stale index blob.
    """
    def _worktree() -> str | None:
        full = path if os.path.isabs(path) else os.path.join(cwd or ".", path)
        try:
            with open(full, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return None

    def _staged() -> str | None:
        return _run_git(cwd, "show", f":{path}")

    if prefer_worktree:
        t = _worktree()
        return t if t is not None else _staged()
    t = _staged()
    return t if t is not None else _worktree()


def _is_table_separator(s: str) -> bool:
    return bool(s) and s.startswith("|") and set(s) <= set("|-: ") and "-" in s


def _table_cells(s: str) -> list:
    return [c.strip() for c in s.strip().strip("|").split("|")]


def _is_real_finding(s: str) -> bool:
    return bool(
        DISPOSITION_RE.search(s)
        or FINDING_BULLET_RE.search(s)
        or CONTENT_BULLET_RE.search(s)
    )


def _has_nonempty_grill_section(text: str) -> bool:
    """A FILLED grill section — not just the TEMPLATE skeleton.

    Excludes the instruction blockquote, the markdown table HEADER row (the row directly above a
    `|---|` separator), the separator itself, and empty-cell rows. Requires at least one remaining
    line that reads as a real finding — a disposition keyword, a `- C\\d` finding bullet, or (issue
    #108) any content bullet. A copied TEMPLATE whose table is unfilled therefore BLOCKS.
    """
    m = GRILL_HEADER_RE.search(text)
    if not m:
        return False
    after = text[m.end():]
    nxt = re.search(r"^\s*##\s+", after, re.MULTILINE)
    body = after[: nxt.start()] if nxt else after
    lines = body.splitlines()
    n = len(lines)
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        if s.startswith(">"):             # instruction blockquote — not content
            continue
        if _is_table_separator(s):        # |---| separator — not content
            continue
        if s.startswith("|"):             # a table row
            cells = _table_cells(s)
            if all(c == "" for c in cells):   # empty-cell data row — not content
                continue
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            if j < n and _is_table_separator(lines[j].strip()):
                continue                       # this is the column-header row — not content
            if _is_real_finding(" ".join(cells)):
                return True
            continue
        if _is_real_finding(s):           # a prose / bullet finding line
            return True
    return False


def _block(reason: str):
    print(json.dumps({"decision": "block", "reason": reason}))  # noqa: T201
    sys.exit(2)


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if data.get("tool_name", "") != "Bash":
        sys.exit(0)
    command = data.get("tool_input", {}).get("command", "")
    cwd = data.get("cwd", "")
    try:
        if not GIT_COMMIT_RE.search(command):
            sys.exit(0)
        if BYPASS_SENTINEL in command:
            sys.exit(0)
        # honor an explicit `git -C <path>` on the commit segment
        commit_seg = next(
            (s for s in SHELL_SEGMENT_SPLIT_RE.split(command) if GIT_COMMIT_RE.search(s)),
            command,
        )
        git_cwd = _resolve_git_cwd(commit_seg, cwd)
        stages_all = _commit_stages_all(command)
        paths = list(_collect_staged(command, git_cwd))
        if stages_all:
            paths.extend(_modified_tracked(git_cwd))
        seen = set()
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            if not _is_spec_path(path):
                continue
            # `git commit -a` will stage the WORKTREE version → read that, not the stale index blob.
            text = _file_text(git_cwd, path, prefer_worktree=stages_all)
            if text is None:
                continue  # unreadable → fail open for this file
            if not _has_nonempty_grill_section(text):
                _block(
                    f"Blocked: '{path}' is a spec/plan/ADR with no non-empty '## Grill findings' "
                    "section. Run grill-me and record findings + dispositions (see "
                    "rules/workflow-adherence.md). For a genuinely trivial doc, add "
                    f"'{BYPASS_SENTINEL}' to the commit command — but prefer recording the grill."
                )
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - never brick a session
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
