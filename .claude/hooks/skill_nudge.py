#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PostToolUse skill-nudge — non-blocking notice when a Write/Edit touches a SKILL.md.

Fires on a Write or Edit whose file_path matches `**/skills/*/SKILL.md` (any depth): emits a
non-blocking `systemMessage` pointing at `superpowers:writing-skills` + the skill-quality
checklist. Never blocks — no bypass token needed because there's nothing to bypass.

Fail-open on any error (malformed stdin, missing fields, etc.) — exit 0, silent.
"""
import json
import re
import sys

# Matches .../skills/<name>/SKILL.md at any depth, forward or backward slashes.
SKILL_MD_RE = re.compile(r"(^|[/\\])skills[/\\][^/\\]+[/\\]SKILL\.md$")

NOTICE = (
    "Notice: you're editing a SKILL.md directly. Skill authoring/edits should go through the "
    "superpowers:writing-skills methodology (not ad-hoc edits) — see skill-creator's quality "
    "checklist before shipping this skill."
)


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    try:
        tool_name = data.get("tool_name", "")
        if tool_name not in ("Write", "Edit"):
            sys.exit(0)
        file_path = data.get("tool_input", {}).get("file_path", "")
        if not isinstance(file_path, str) or not SKILL_MD_RE.search(file_path):
            sys.exit(0)
        print(json.dumps({"systemMessage": NOTICE}))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - never brick a session
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
