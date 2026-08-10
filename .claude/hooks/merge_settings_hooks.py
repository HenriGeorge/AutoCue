#!/usr/bin/env python3
"""merge_settings_hooks.py — reconcile a project's .claude/settings.json hook wiring against the
template's canonical set (issue #94, Tier-1 hook-wiring mechanism).

Pure stdlib (json only — no jq dependency, keeps setup.sh's zero-extra-runtime contract for
cli/data/service projects that don't have node).

merge(existing, canonical) -> (merged, added, notes)
  - ADDS canonical (event, matcher, command) wirings that are absent from `existing`.
  - NEVER removes, reorders, or overwrites anything already in `existing` — extra hooks, extra
    permissions, extra top-level keys, and unrelated events/matchers all survive untouched.
  - Identity rule for "already wired" (fix-round 1, issue #94 review finding): COMMAND MATCH
    WITHIN THE SAME EVENT TAKES PRECEDENCE OVER MATCHER-STRING EQUALITY. If the canonical command
    is already wired ANYWHERE under that event — even under a different/narrower matcher (e.g. a
    project customized PostToolUse to "Write" instead of the canonical "Write|Edit") — it counts
    as already-wired and is SKIPPED, never appended a second time. This prevents a customized
    matcher from causing the same hook to double-fire under two matcher blocks. Only when the
    exact command is genuinely absent from the event does a new (matcher, command) block get
    added — in that case, if the event already had OTHER custom matcher(s), a note is emitted so
    the caller can review it (added-alongside-a-custom-matcher is a normal but noteworthy outcome).

CLI:
  merge_settings_hooks.py --in settings.json --canonical canonical-hooks.json [--write] [--diff]
    --diff   print what would be/was added (one line per wiring) + any notes + a summary line
             "N wirings added"
    --write  write the merged result back to --in (pretty JSON, trailing newline)
    (neither flag)  just print the summary line; nothing is written
"""
from __future__ import annotations

import argparse
import copy
import json
import sys


def merge(existing: dict, canonical: dict) -> tuple[dict, list[tuple[str, str, str]], list[str]]:
    """Return (merged_settings, added, notes).

    added is a list of (event, matcher, command); notes are human-readable strings flagging
    outcomes worth a human's attention (currently: a canonical wiring added alongside a
    pre-existing custom matcher for the same event).
    """
    merged = copy.deepcopy(existing)
    merged.setdefault("hooks", {})
    added: list[tuple[str, str, str]] = []
    notes: list[str] = []

    canonical_hooks = canonical.get("hooks", {})
    for event, entries in canonical_hooks.items():
        existing_entries = merged["hooks"].setdefault(event, [])
        # Snapshot BEFORE this event's additions — used only to phrase a "custom matcher(s)
        # already present" note, never for the already-wired decision itself.
        pre_existing_matchers = [e.get("matcher", "") for e in existing_entries]
        for entry in entries:
            matcher = entry.get("matcher", "")
            for hook in entry.get("hooks", []):
                command = hook.get("command")
                # Command-match-anywhere-in-event takes precedence over matcher-string equality:
                # a customized matcher (e.g. "Write" vs canonical "Write|Edit") that already wires
                # this exact command must NOT get a second, duplicate wiring under a different
                # matcher block — that would double-fire the hook on every matching tool call.
                already_wired = any(
                    any(h.get("command") == command for h in e.get("hooks", []))
                    for e in existing_entries
                )
                if already_wired:
                    continue
                # Append into an existing same-matcher block if one exists (keeps the settings.json
                # shape close to what setup.sh scaffolds); otherwise create a new matcher block.
                target = next((e for e in existing_entries if e.get("matcher", "") == matcher), None)
                if target is not None:
                    target.setdefault("hooks", []).append(dict(hook))
                else:
                    existing_entries.append({"matcher": matcher, "hooks": [dict(hook)]})
                added.append((event, matcher, command))
                if pre_existing_matchers and matcher not in pre_existing_matchers:
                    notes.append(
                        f"note: {event} canonical wiring added under matcher {matcher!r} "
                        f"ALONGSIDE existing custom matcher(s) {pre_existing_matchers!r} — "
                        f"review for overlap"
                    )

    return merged, added, notes


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="in_path", required=True, help="path to the project's settings.json")
    ap.add_argument("--canonical", dest="canonical_path", required=True, help="path to the canonical hooks fragment JSON")
    ap.add_argument("--write", action="store_true", help="write the merged result back to --in")
    ap.add_argument("--diff", action="store_true", help="print each added wiring, not just the summary")
    args = ap.parse_args(argv)

    existing = _load(args.in_path)
    canonical = _load(args.canonical_path)

    merged, added, notes = merge(existing, canonical)

    if args.diff:
        for event, matcher, command in added:
            print(f"+ {event} matcher={matcher!r} command={command!r}")
        for note in notes:
            print(note)
    print(f"{len(added)} wirings added")

    if args.write:
        with open(args.in_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
            f.write("\n")
        print(f"wrote {args.in_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
