"""Command-line interface for AutoCue."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pyrekordbox import Rekordbox6Database as MasterDatabase
from pyrekordbox.db6 import DjmdCue, DjmdPlaylist, DjmdSongPlaylist

from .analyzer import analyze_all, analyze_by_id, analyze_by_title
from .writer import write_xml


def has_existing_hot_cues(content, db: MasterDatabase) -> int:
    """Return the number of existing hot cues for a track (Kind > 0 means hot cue)."""
    return (
        db.query(DjmdCue)
        .filter(DjmdCue.ContentID == content.ID, DjmdCue.Kind > 0)
        .count()
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="autocue",
        description="Automatically place hot cues on tracks in your Rekordbox 7 library.",
    )

    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--track", metavar="TITLE", help="Process a single track by title")
    target.add_argument(
        "--track-id",
        metavar="ID",
        type=int,
        help="Process a single track by Rekordbox track ID",
    )
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
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-generate cues even for tracks that already have hot cues",
    )
    parser.add_argument(
        "--uncued-only",
        action="store_true",
        help=(
            "Only process tracks with zero existing hot cues "
            "(ignored when --overwrite is also set)"
        ),
    )
    parser.add_argument(
        "--playlist",
        metavar="NAME",
        help="Filter --library mode to tracks in the named Rekordbox playlist",
    )
    parser.add_argument(
        "--db-path",
        metavar="PATH",
        help=(
            "Path to master.db (default: auto-detected on macOS). "
            "On Windows, use --db-path to point to your master.db."
        ),
    )

    args = parser.parse_args()

    print("Opening Rekordbox library…")
    try:
        db = MasterDatabase(args.db_path) if args.db_path else MasterDatabase()
    except Exception as e:
        print(f"Error: could not open Rekordbox database — {e}", file=sys.stderr)
        if not args.db_path:
            print(
                "Could not auto-detect Rekordbox database. "
                "On Windows, use --db-path to point to your master.db.",
                file=sys.stderr,
            )
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

    elif args.track_id:
        result = analyze_by_id(args.track_id, db)
        if result is None:
            print(f"Track not found: ID={args.track_id}", file=sys.stderr)
            sys.exit(1)
        content, cues = result
        if not cues:
            print(
                f"No phrase data found for track ID={args.track_id}. "
                "Analyze the track in Rekordbox first."
            )
            sys.exit(0)
        tracks = [(content, cues)]

    else:
        # --library mode
        print("Scanning library for phrase data…")

        if args.playlist:
            tracks = _analyze_playlist(args.playlist, db)
            if tracks is None:
                sys.exit(1)
        else:
            tracks = analyze_all(db)

        if not tracks:
            print("No analyzed tracks found. Analyze your library in Rekordbox first.")
            sys.exit(0)

        # Apply duplicate-cue filtering
        if not args.overwrite:
            filtered = []
            for content, cues in tracks:
                n = has_existing_hot_cues(content, db)
                if n > 0:
                    title = content.Title or content.FileNameL or "Unknown"
                    print(
                        f"  {title}: skipping — already has {n} hot cue(s). "
                        "Use --overwrite to replace."
                    )
                else:
                    filtered.append((content, cues))
            tracks = filtered
        elif args.uncued_only:
            # --overwrite takes precedence: uncued-only is ignored
            pass

        # --uncued-only without --overwrite: further restrict to uncued tracks
        # (already handled above since non-overwrite path skips cued tracks,
        #  but --uncued-only is an explicit tighter filter for the same behaviour)
        if args.uncued_only and not args.overwrite:
            # already filtered above — nothing extra to do
            pass

        if not tracks:
            print("No eligible tracks to process (all already have hot cues). Use --overwrite to re-generate.")
            sys.exit(0)

    _print_summary(tracks)

    if args.dry_run:
        print("\nDry run — no files written.")
        return

    output = write_xml(tracks, args.output)
    print(f"\nWrote {output}")
    print("Import in Rekordbox: File > Import Library > select the XML file above.")


def _analyze_playlist(
    playlist_name: str, db: MasterDatabase
) -> list[tuple] | None:
    """Return (content, cues) for all tracks in the named playlist, or None on error."""
    playlist = db.query(DjmdPlaylist).filter_by(Name=playlist_name).first()
    if playlist is None:
        print(f"Error: playlist {playlist_name!r} not found.", file=sys.stderr)
        available = [p.Name for p in db.query(DjmdPlaylist).all() if p.Name]
        if available:
            print("Available playlists:", file=sys.stderr)
            for name in sorted(available):
                print(f"  {name}", file=sys.stderr)
        return None

    song_entries = (
        db.query(DjmdSongPlaylist)
        .filter(DjmdSongPlaylist.PlaylistID == playlist.ID)
        .all()
    )
    content_ids = {entry.ContentID for entry in song_entries}

    from .analyzer import analyze_track
    from pyrekordbox.db6 import DjmdContent

    results = []
    for content in db.get_content().all():
        if content.ID not in content_ids:
            continue
        cues = analyze_track(content, db)
        if cues:
            results.append((content, cues))
    return results


def _print_summary(tracks: list) -> None:
    total_cues = sum(len(cues) for _, cues in tracks)
    print(f"\n{len(tracks)} track(s) · {total_cues} cue(s) total\n")
    for content, cues in tracks:
        title = content.Title or content.FileNameL or "Unknown"
        print(f"  {title}")
        for cue in cues:
            mins, secs = divmod(cue.position_ms // 1000, 60)
            print(f"    [{cue.slot_name}] {mins:02d}:{secs:02d}  {cue.label.value}")
