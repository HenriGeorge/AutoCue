#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Stop hook — log session summary to .claude/session-logs/."""

import json
import sys
from datetime import datetime
from pathlib import Path


def main():
    # Best-effort logging: unparseable stdin or a read-only/permission-denied filesystem must not
    # crash the Stop hook (matches the Node counterpart, which wraps the whole body in try/catch).
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    session_id = data.get("session_id", "unknown")
    stop_reason = data.get("stop_reason", "unknown")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_entry = {
        "timestamp": timestamp,
        "session_id": session_id,
        "stop_reason": stop_reason,
    }
    date_str = datetime.now().strftime("%Y-%m-%d")

    try:
        log_dir = Path(".claude") / "session-logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"{date_str}.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
        # Keep only last 30 days
        logs = sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        for old in logs[:-30]:
            old.unlink()
    except OSError as e:
        # best-effort logging (never crash the Stop hook) — but leave a breadcrumb so a missing
        # log trail is diagnosable rather than silent.
        print(f"stop_log: could not write session log: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
