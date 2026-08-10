#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""SubagentStop context-nudge — non-blocking WARN when a subagent's returned text is large.

A subagent should run in its own isolated context and return a CONCISE SUMMARY to the primary
session — every character returned re-enters the primary's own context and compounds across every
subagent spawned in a session (token-driver analysis: 58% of heavy sessions are subagent-heavy, 65%
of those exceed 150K tokens). This hook fires on `SubagentStop` and measures the subagent's final
returned text (`last_assistant_message`); if it exceeds a threshold, it emits a non-blocking
`systemMessage` reminder to hand back a summary, not a dump.

No hook event exposes a subagent's SPAWN PROMPT (only `SubagentStop`'s `last_assistant_message`
is available — verified against the live hooks docs at design time), so this is RETURN-side
detection only; the matching INPUT-side discipline ("fork ≠ free, construct a focused prompt") is
documented guidance in rules/agent-delegation.md, not a hook (see docs/superpowers/specs/
2026-08-10-context-nudge-design.md for the full design + Grill findings).

Threshold: default 16000 chars (~4k estimated tokens, chars/4), overridable via
CONTEXT_NUDGE_RETURN_CHARS. Some agent_types (e.g. crew report-writers) legitimately return large
result files — exclude specific ones via CONTEXT_NUDGE_EXCLUDE_AGENT_TYPES (comma-separated).

Never blocks — no bypass token needed (nothing to bypass). Fail-open on any error (malformed
stdin, missing/empty last_assistant_message, etc.) — exit 0, silent.
"""
import json
import os
import sys

DEFAULT_RETURN_CHARS = 16000
CHARS_PER_TOKEN = 4


def _threshold() -> int:
    raw = os.environ.get("CONTEXT_NUDGE_RETURN_CHARS", "")
    try:
        return int(raw) if raw.strip() else DEFAULT_RETURN_CHARS
    except ValueError:
        return DEFAULT_RETURN_CHARS


def _excluded_agent_types() -> set:
    raw = os.environ.get("CONTEXT_NUDGE_EXCLUDE_AGENT_TYPES", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    try:
        if data.get("hook_event_name", "") != "SubagentStop":
            sys.exit(0)
        message = data.get("last_assistant_message", "")
        if not isinstance(message, str) or not message:
            sys.exit(0)
        agent_type = data.get("agent_type", "") or "unknown"
        if agent_type in _excluded_agent_types():
            sys.exit(0)
        chars = len(message)
        if chars <= _threshold():
            sys.exit(0)
        est_k_tokens = max(1, round(chars / CHARS_PER_TOKEN / 1000))
        print(json.dumps({  # noqa: T201
            "systemMessage": (
                f"subagent ({agent_type}) returned ~{est_k_tokens}k tokens — return a concise "
                "summary, not a dump."
            )
        }))
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001 - never brick a session
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
