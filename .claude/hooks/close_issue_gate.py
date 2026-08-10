#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Stop hook — CLOSE issue-filing gate.

Enforces: a session that made real git commits must file follow-up GitHub issues
(or explicitly declare there are none) before it's allowed to stop.

Reads the Stop-hook JSON payload from stdin (fields: transcript_path, session_id,
stop_hook_active), then scans the session transcript (JSONL — one event per line)
for:
  - made_commit:  a Bash tool_use command containing "git commit"
  - filed_issue:  a Bash tool_use command containing "gh issue create"
  - declared_none: assistant text containing the exact sentinel "WORKFLOW:no-follow-ups"

If made_commit and neither filed_issue nor declared_none: block the stop with a
JSON decision on stdout, prompting the agent to file issues or state the sentinel.
Otherwise allow the stop silently.

Deliberately does NOT short-circuit on stop_hook_active: the sentinel/issue-filing
is the escape hatch, so a cooperating agent satisfies the gate on its next attempt
and this can't infinite-loop. (An uncooperative agent could loop, but that's true
of any blocking Stop hook and out of scope here.)

Never crashes: any exception, missing/unreadable transcript_path, or malformed JSON
results in a silent exit 0 (best-effort, matches stop_log.py).
"""

import json
import sys


BLOCK_REASON = (
    "CLOSE gate ⛔ — this session committed changes but filed no GitHub "
    "issues for follow-ups/known gaps. Run `gh issue create` for each deferred "
    "item, or if there are genuinely none, state exactly: WORKFLOW:no-follow-ups"
)


def _scan_transcript(path):
    """Return (made_commit, filed_issue, declared_none) booleans for the transcript at path."""
    made_commit = False
    filed_issue = False
    declared_none = False
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(event, dict) or event.get("type") != "assistant":
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
                        if "git commit" in command:
                            made_commit = True
                        if "gh issue create" in command:
                            filed_issue = True
                elif btype == "text":
                    text = block.get("text", "")
                    if isinstance(text, str) and "WORKFLOW:no-follow-ups" in text:
                        declared_none = True
    return made_commit, filed_issue, declared_none


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    transcript_path = data.get("transcript_path")
    if not transcript_path or not isinstance(transcript_path, str):
        sys.exit(0)

    try:
        made_commit, filed_issue, declared_none = _scan_transcript(transcript_path)
    except OSError:
        sys.exit(0)
    except Exception:
        sys.exit(0)

    if made_commit and not filed_issue and not declared_none:
        print(json.dumps({"decision": "block", "reason": BLOCK_REASON}))

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Absolute last-resort guard: never let an unexpected error crash the Stop hook.
        sys.exit(0)
