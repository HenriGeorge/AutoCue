#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PostToolUse grill-nudge — non-blocking WARN when a plan file is written/edited without a
non-empty `## Grill findings` section.

A hook is a deterministic guard, not a skill-runner — it cannot literally invoke `grill-me`. So
"every plan auto-triggers a pressure-test" (rules/workflow-adherence.md #5) is institutionalized as
nudge + existing block, not literal auto-invocation:

Covers BOTH `docs/superpowers/plans/**/*.md` (the repo-committed plan path `grill_gate.py` already
BLOCKS at `git commit` time) AND the plan-mode path `~/.claude/plans/*.md` (currently OUTSIDE
`grill_gate.py`'s reach — that file lives outside any project git repo, so it can never appear in a
project's `git diff --cached` and extending grill_gate's watched globs cannot close this gap; this
nudge, firing on the Write/Edit itself, is the only mechanism that can observe it).

Fires on a Write or Edit to a watched plan path whose current on-disk content has no non-empty
`## Grill findings` section: emits a non-blocking `systemMessage`. Never blocks — no bypass token
needed. TEMPLATE* files are excluded (skeleton files intentionally carry an empty/no findings
section — same exclusion `grill_gate.py`/`_is_spec_path` applies). Fail-open on any error
(malformed stdin, missing/unreadable file, etc.) — exit 0, silent.
"""
import json
import os
import re
import sys

# One level deep under docs/superpowers/plans/ — matches grill_gate.py's own SPEC_PREFIXES shape.
PLAN_PATH_RE = re.compile(r"(^|[/\\])docs[/\\]superpowers[/\\]plans[/\\][^/\\]+\.md$")
# The plan-mode path, anywhere under a `.claude/plans/` dir — deliberately NOT anchored to a
# specific $HOME so it matches regardless of machine/user.
PLAN_MODE_PATH_RE = re.compile(r"(^|[/\\])\.claude[/\\]plans[/\\][^/\\]+\.md$")

# #148: tolerate trailing text on the heading line (mirrors grill_gate.py's widening exactly).
GRILL_HEADER_RE = re.compile(r"^\s*##\s+Grill findings\b.*$", re.MULTILINE)
# Mirrors grill_gate.py's finding-detection exactly (same disposition vocabulary + bullet shapes)
# so a plan that would satisfy the commit-time BLOCK never spuriously nudges here, and vice versa.
DISPOSITION_RE = re.compile(
    r"\b(fixed|parked|deferred|accepted|resolved|mitigated|addressed|acknowledged|wontfix|"
    r"won't\s*fix|noted|ruled)\b",
    re.IGNORECASE,
)
FINDING_BULLET_RE = re.compile(r"^\s*[-*]\s*C\d+\b")
CONTENT_BULLET_RE = re.compile(r"^\s*[-*]\s+\S")

NOTICE = (
    "Notice: this plan has no non-empty '## Grill findings' section yet. "
    "rules/workflow-adherence.md #5 requires grilling the PLAN (not just the design) before BUILD "
    "— run grill-me and record findings + dispositions. This never blocks; it's a reminder."
)


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
    m = GRILL_HEADER_RE.search(text)
    if not m:
        return False
    after = text[m.end():]
    nxt = re.search(r"^\s*##\s+", after, re.MULTILINE)
    body = after[: nxt.start()] if nxt else after
    for raw in body.splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith(">"):
            continue
        if _is_table_separator(s):
            continue
        if s.startswith("|"):
            cells = _table_cells(s)
            if all(c == "" for c in cells):
                continue
            if _is_real_finding(" ".join(cells)):
                return True
            continue
        if _is_real_finding(s):
            return True
    return False


def _is_watched_plan(path) -> bool:
    if not isinstance(path, str) or not path:
        return False
    if os.path.basename(path).upper().startswith("TEMPLATE"):
        return False
    return bool(PLAN_PATH_RE.search(path) or PLAN_MODE_PATH_RE.search(path))


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    try:
        if data.get("tool_name", "") not in ("Write", "Edit"):
            sys.exit(0)
        file_path = data.get("tool_input", {}).get("file_path", "")
        if not _is_watched_plan(file_path):
            sys.exit(0)
        try:
            with open(file_path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            sys.exit(0)  # missing/unreadable -> fail open
        if _has_nonempty_grill_section(text):
            sys.exit(0)
        print(json.dumps({"systemMessage": NOTICE}))  # noqa: T201
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - never brick a session
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
