#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""SessionStart hook — print branch status and any pending handoff."""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def find_handoff(project_dir: str) -> tuple[str, str] | None:
    state_root = Path(project_dir) / ".claude" / "state"
    if not state_root.is_dir():
        return None

    candidates: list[tuple[float, str, Path]] = []
    for branch_dir in state_root.iterdir():
        if not branch_dir.is_dir():
            continue
        handoff = branch_dir / "handoff.md"
        if not handoff.is_file():
            continue

        try:
            content = handoff.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        consumed_recently = False
        for line in reversed(content.splitlines()):
            if line.startswith("consumed_at:"):
                try:
                    ts = line.split(":", 1)[1].strip()
                    consumed = datetime.fromisoformat(ts)
                    if (datetime.now(tz=UTC) - consumed).total_seconds() < 86400:
                        consumed_recently = True
                except (ValueError, IndexError):
                    pass
                break
        if consumed_recently:
            continue

        try:
            mtime = handoff.stat().st_mtime
        except OSError:
            continue
        candidates.append((mtime, branch_dir.name, handoff))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    _, branch_name, handoff_path = candidates[0]

    try:
        content = handoff_path.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()[:80]
        summary = "\n".join(lines)
        with handoff_path.open("a", encoding="utf-8") as f:
            f.write(f"\nconsumed_at: {datetime.now(tz=UTC).isoformat()}\n")
    except OSError:
        return None

    return branch_name, summary


def main():
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    lines = ["[Resonanz Records Session Context]"]

    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, cwd=project_dir,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=project_dir,
        ).stdout.strip()
        if branch:
            lines.append(f"Branch: {branch}" + (" (dirty)" if dirty else ""))
    except Exception:
        pass

    handoff = find_handoff(project_dir)
    if handoff:
        handoff_branch, handoff_content = handoff
        lines.append("")
        lines.append(f"Handoff from prior session (branch: {handoff_branch}):")
        lines.append("-" * 60)
        lines.append(handoff_content)
        lines.append("-" * 60)

    print("\n".join(lines))


if __name__ == "__main__":
    main()
