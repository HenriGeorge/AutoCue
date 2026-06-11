#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Stop hook — log session summary and remind about unpushed work."""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def get_current_branch(project_dir: str) -> str:
    try:
        return subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=project_dir, timeout=5,
        ).stdout.strip()
    except Exception:
        return ""


def get_default_branch(project_dir: str) -> str:
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True, text=True, cwd=project_dir, timeout=5,
        )
        ref = result.stdout.strip()
        if ref:
            return ref.split("/")[-1]
    except Exception:
        pass
    return "main"


def has_unpushed_work(project_dir: str, branch: str) -> bool:
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=project_dir, timeout=5,
        ).stdout.strip()
        if dirty:
            return True
        unpushed = subprocess.run(
            ["git", "log", f"origin/{branch}..HEAD", "--oneline"],
            capture_output=True, text=True, cwd=project_dir, timeout=5,
        ).stdout.strip()
        return bool(unpushed)
    except Exception:
        return False


def has_open_pr(branch: str) -> bool:
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number"],
            capture_output=True, text=True, timeout=10,
        )
        return len(json.loads(result.stdout or "[]")) > 0
    except Exception:
        return False


def main():
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")

    try:
        data = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        data = {}

    log_dir = Path(project_dir) / ".claude" / "session-logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_entry = {
        "timestamp": timestamp,
        "session_id": data.get("session_id", "unknown"),
        "stop_reason": data.get("stop_reason", "unknown"),
    }

    date_str = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"{date_str}.jsonl"
    with open(log_file, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    # Keep last 30 days of logs
    logs = sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    for old in logs[:-30]:
        old.unlink()

    branch = get_current_branch(project_dir)
    default_branch = get_default_branch(project_dir)

    if branch and branch != default_branch:
        finish_check = Path(project_dir) / ".claude" / "state" / branch / "finish-check.json"
        if not finish_check.exists() and has_unpushed_work(project_dir, branch) and not has_open_pr(branch):
            print(f"\nBranch '{branch}' has unpushed work and no PR — run /prp-core:prp-pr when ready.")


if __name__ == "__main__":
    main()
