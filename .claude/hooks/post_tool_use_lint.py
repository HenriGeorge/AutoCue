#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""PostToolUse hook — auto-lint Python files after Write/Edit."""

import json
import subprocess
import sys


def main():
    # Fail open on unparseable input rather than crashing the PostToolUse hook (matches the Node hook).
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    if tool_name not in ("Write", "Edit"):
        return

    file_path = tool_input.get("file_path", "")
    if not file_path.endswith(".py"):
        return

    result = subprocess.run(
        ["uvx", "ruff", "check", file_path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        errors = result.stdout.strip() or result.stderr.strip()
        print(json.dumps({
            "decision": "block",
            "reason": f"ruff lint errors in {file_path}:\n{errors}\n\nFix these before proceeding.",
        }))
        sys.exit(2)


if __name__ == "__main__":
    main()
