#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Stop hook — CLOSE-phase workflow gate (P8), sibling to the proven `close_issue_gate.py`.

Scans the session transcript (JSONL) for tool-events + assistant text, then enforces/nudges
THREE distinct P8 CLOSE commitments:

  - **reflect BLOCK** — if this session did a `gh pr merge` but never ran `/dev-reflect` (or
    committed a `docs(lessons):` update), block the Stop with a JSON decision. Bypass:
    `WORKFLOW:no-reflect` in assistant text.
  - **handoff WARN** — if this session did a `gh pr merge` but the PRIMARY worktree's
    HANDOFF.md wasn't touched during the session (fuzzy — filesystem-mtime only), emit a
    non-blocking notice. No bypass (it's advisory, not a gate).
  - **verify WARN** — if this session made a `git commit` but never ran a
    test/lint/validate command, emit a non-blocking notice. Bypass: `WORKFLOW:no-verify`.

Mirrors close_issue_gate.py's transcript-scanner shape and never-crash contract: any exception,
missing/unreadable transcript_path, or malformed JSON silently exits 0. `close_issue_gate.py`
itself is left UNTOUCHED (proven, no behavior-test file — a well-tested sibling is safer).

Non-blocking notices use the same `systemMessage` JSON field skill_nudge.py already established
for a non-SessionStart hook (proven + tested there) — never a `decision` field, so Stop is never
blocked by a WARN-tier finding.

Detection of `git commit` and `gh pr merge` inside a transcript Bash command is TOKEN-based, not a
plain substring/regex — `git -C <path> commit ...` (routine in this repo's own multi-worktree
workflow) and `gh --repo org/repo pr merge ...` / `gh -R ... pr merge ...` both defeated a naive
`"git commit" in command` / `"gh pr merge" in command` check (fix-round 1, CRITICAL-1 + HIGH).

Known limitation (documented, not fixed here — see docs/ENFORCEMENT.md): this scanner only sees
Bash/Skill tool_use blocks the CURRENT session's own transcript recorded directly. A `gh pr merge`
or `/dev-reflect` performed by a delegated subagent/teammate (a separate transcript file) or erased
by mid-session compaction is structurally invisible to it.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone

REFLECT_BLOCK_REASON = (
    "CLOSE gate ⛔ — this session merged a PR but never ran /dev-reflect (no "
    "`docs(lessons):` commit or dev-reflect invocation detected). Harvest wins + lessons "
    "into the project's lessons file before stopping, or state exactly: WORKFLOW:no-reflect"
)

HANDOFF_WARN = (
    "CLOSE notice: this session merged a PR but the primary worktree's HANDOFF.md doesn't "
    "look like it was updated this session — run /handoff for session continuity."
)

VERIFY_WARN = (
    "CLOSE notice: this session committed changes but no test/lint/validate run was "
    "detected — GATE-2 wants fresh evidence before 'done' (run /validate). If verification "
    "genuinely doesn't apply, state: WORKFLOW:no-verify"
)

NO_REFLECT_BYPASS = "WORKFLOW:no-reflect"
NO_VERIFY_BYPASS = "WORKFLOW:no-verify"

TEST_RUNNER_RE = re.compile(
    r"\b(pytest|npm\s+test|vitest|cargo\s+test|go\s+test|bash\s+tests/run\.sh|"
    r"cc-worktrees\s+test|npm\s+run\s+lint|ruff\s+check)\b"
)
REFLECT_MARKER_RE = re.compile(r"dev-reflect|docs\(lessons\):")
SHELL_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||;|\n|\|")
GIT_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
GH_VALUE_FLAGS = {"-R", "--repo", "--hostname"}


def _tokenize(segment):
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def _skip_flags(tokens, i, value_flags):
    while i < len(tokens) and tokens[i].startswith("-") and tokens[i] != "-":
        tok = tokens[i]
        if "=" in tok:
            i += 1
        elif tok in value_flags:
            i += 2
        else:
            i += 1
    return i


def _has_subcommand(command, prog, path, value_flags):
    """True if `command` contains `prog` (optionally flag-prefixed) → `path` in order, in ANY
    shell segment. Token-based — tolerant of intervening flags like `git -C <path> commit` or
    `gh --repo org/repo pr merge` that a plain substring/regex check would miss (fix-round 1)."""
    for segment in SHELL_SEGMENT_SPLIT_RE.split(command):
        tokens = _tokenize(segment)
        for i, t in enumerate(tokens):
            if t != prog:
                continue
            j = i + 1
            ok = True
            for expected in path:
                j = _skip_flags(tokens, j, value_flags)
                if j >= len(tokens) or tokens[j] != expected:
                    ok = False
                    break
                j += 1
            if ok:
                return True
    return False


def _run(args, cwd=None, timeout=5):
    try:
        return subprocess.run(
            args, cwd=cwd or None, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except Exception:  # noqa: BLE001 - fail open
        return None


def _primary_worktree(cwd):
    r = _run(["git", "worktree", "list", "--porcelain"], cwd)
    if r is None or r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if line.startswith("worktree "):
            return line[len("worktree "):].strip()
    return None


def _parse_ts(raw):
    if not isinstance(raw, str) or not raw:
        return None
    try:
        s = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _scan_transcript(path):
    """Returns (made_commit, did_merge, ran_reflect, ran_test, no_reflect, no_verify, first_ts)."""
    made_commit = did_merge = ran_reflect = ran_test = False
    no_reflect = no_verify = False
    first_ts = None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            if first_ts is None:
                ts = _parse_ts(event.get("timestamp"))
                if ts is not None:
                    first_ts = ts
            if event.get("type") != "assistant":
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use" and block.get("name") == "Bash":
                    command = (block.get("input") or {}).get("command", "")
                    if isinstance(command, str):
                        if _has_subcommand(command, "git", ("commit",), GIT_VALUE_FLAGS):
                            made_commit = True
                        if _has_subcommand(command, "gh", ("pr", "merge"), GH_VALUE_FLAGS):
                            did_merge = True
                        if REFLECT_MARKER_RE.search(command):
                            ran_reflect = True
                        if TEST_RUNNER_RE.search(command):
                            ran_test = True
                elif btype == "tool_use" and block.get("name") == "Skill":
                    skill = json.dumps(block.get("input") or {})
                    if "dev-reflect" in skill:
                        ran_reflect = True
                elif btype == "text":
                    text = block.get("text", "")
                    if isinstance(text, str):
                        if REFLECT_MARKER_RE.search(text):
                            ran_reflect = True
                        if NO_REFLECT_BYPASS in text:
                            no_reflect = True
                        if NO_VERIFY_BYPASS in text:
                            no_verify = True
    return made_commit, did_merge, ran_reflect, ran_test, no_reflect, no_verify, first_ts


def _handoff_stale(cwd, first_ts):
    if first_ts is None:
        return False  # can't determine -> don't warn on an unknown
    primary = _primary_worktree(cwd)
    if not primary:
        return False
    handoff = os.path.join(primary, "HANDOFF.md")
    try:
        mtime = os.path.getmtime(handoff)
    except OSError:
        return True  # HANDOFF.md doesn't exist at all -> stale
    mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    return mtime_dt < first_ts


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    transcript_path = data.get("transcript_path")
    if not transcript_path or not isinstance(transcript_path, str):
        sys.exit(0)
    cwd = data.get("cwd", "")

    try:
        (
            made_commit, did_merge, ran_reflect, ran_test, no_reflect, no_verify, first_ts,
        ) = _scan_transcript(transcript_path)
    except OSError:
        sys.exit(0)
    except Exception:  # noqa: BLE001 - never brick a session
        sys.exit(0)

    try:
        if did_merge and not ran_reflect and not no_reflect:
            print(json.dumps({"decision": "block", "reason": REFLECT_BLOCK_REASON}))
            sys.exit(0)

        notices = []
        if did_merge and _handoff_stale(cwd, first_ts):
            notices.append(HANDOFF_WARN)
        if made_commit and not ran_test and not no_verify:
            notices.append(VERIFY_WARN)
        if notices:
            print(json.dumps({"systemMessage": "\n".join(notices)}))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - never brick a session
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(0)
