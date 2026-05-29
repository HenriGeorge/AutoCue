"""Command-line interface for AutoCue."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pyrekordbox import MasterDatabase

from .analyzer import analyze_all, analyze_by_title
from .writer import write_xml


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="autocue",
        description="Automatically place hot cues on tracks in your Rekordbox 7 library.",
    )

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--track", metavar="TITLE", help="Process a single track by title")
    target.add_argument("--library", action="store_true", help="Process all analyzed tracks")

    parser.add_argument(
        "--output",
        metavar="FILE",
        default="autocue_import.xml",
        help="Output XML file path (default: autocue_import.xml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print cue placements without writing any files",
    )

    args = parser.parse_args()

    print("Opening Rekordbox library…")
    try:
        db = MasterDatabase()
    except Exception as e:
        print(f"Error: could not open Rekordbox database — {e}", file=sys.stderr)
        print("Make sure Rekordbox is closed before running AutoCue.", file=sys.stderr)
        sys.exit(1)

    if args.track:
        result = analyze_by_title(args.track, db)
        if result is None:
            print(f"Track not found: {args.track!r}", file=sys.stderr)
            sys.exit(1)
        content, cues = result
        if not cues:
            print(f"No phrase data found for {args.track!r}. Analyze the track in Rekordbox first.")
            sys.exit(0)
        tracks = [(content, cues)]
    else:
        print("Scanning library for phrase data…")
        tracks = analyze_all(db)
        if not tracks:
            print("No analyzed tracks found. Analyze your library in Rekordbox first.")
            sys.exit(0)

    _print_summary(tracks)

    if args.dry_run:
        print("\nDry run — no files written.")
        return

    output = write_xml(tracks, args.output)
    print(f"\nWrote {output}")
    print("Import in Rekordbox: File > Import Library > select the XML file above.")


def _print_summary(tracks: list) -> None:
    total_cues = sum(len(cues) for _, cues in tracks)
    print(f"\n{len(tracks)} track(s) · {total_cues} cue(s) total\n")
    for content, cues in tracks:
        title = content.Title or content.FileNameL or "Unknown"
        print(f"  {title}")
        for cue in cues:
            mins, secs = divmod(cue.position_ms // 1000, 60)
            print(f"    [{cue.slot_name}] {mins:02d}:{secs:02d}  {cue.label.value}")
