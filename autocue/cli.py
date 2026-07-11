"""Command-line interface for AutoCue."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from pyrekordbox import MasterDatabase
except ImportError:
    from pyrekordbox import Rekordbox6Database as MasterDatabase  # type: ignore[no-redef]
from pyrekordbox.db6 import DjmdCue, DjmdPlaylist, DjmdSongPlaylist

from .analyzer import analyze_by_id, analyze_by_title
from .db_writer import has_existing_hot_cues
from .generator import GenerationPrefs, generate_cues_for_track
from .writer import write_xml


def _build_parser() -> argparse.ArgumentParser:
    """Build the (non-serve) CLI argument parser. Extracted for testability."""
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
        "--playlist",
        metavar="NAME",
        help="Filter --library mode to tracks in the named Rekordbox playlist",
    )
    parser.add_argument(
        "--serato",
        action="store_true",
        help=(
            "Write cues into the audio files as Serato DJ Pro tags "
            "instead of producing a Rekordbox XML"
        ),
    )
    parser.add_argument(
        "--loops",
        action="store_true",
        help=(
            "Also emit named, bar-length loop regions at phrase edges "
            "(Intro/Outro/Break). Currently written with --serato as Serato "
            "LOOP tags; existing Serato loops are preserved."
        ),
    )
    parser.add_argument(
        "--db-path",
        metavar="PATH",
        help=(
            "Path to master.db (default: auto-detected on macOS). "
            "On Windows, use --db-path to point to your master.db."
        ),
    )
    return parser


def _merge_loops(cues: list, loops: list) -> list:
    """Layer generated loop CuePoints onto a cue list.

    Drops a generated loop only when its start collides with an existing LOOP's
    start (mirror-first: a DJ's saved loop wins). A memory loop (Num=-1) and a
    hot/point cue (Num 0-7) are DIFFERENT Rekordbox objects and COEXIST at the
    same downbeat — generated phrase cues and generated loops share downbeats,
    so colliding against point cues wiped every loop (the XMLWIRE root cause)."""
    loop_starts = {c.position_ms for c in cues if getattr(c, "is_loop", False)}
    merged = list(cues)
    for loop in loops:
        if loop.position_ms in loop_starts:
            continue
        merged.append(loop)
        loop_starts.add(loop.position_ms)
    return merged


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        from .serve.app import serve as _serve
        import argparse as _ap
        p = _ap.ArgumentParser(prog="autocue serve")
        p.add_argument("--port", type=int, default=7432)
        p.add_argument("--no-browser", action="store_true")
        p.add_argument("--db-path", metavar="PATH")
        p.add_argument(
            "--reset-cache",
            action="store_true",
            help="Delete the sidecar analysis cache (autocue_cache.sqlite "
                 "+ WAL/SHM sidecars) before starting. No effect if absent.",
        )
        a = p.parse_args(sys.argv[2:])
        if a.reset_cache:
            from .cache_reset import reset_sidecar_cache
            reset_sidecar_cache(a.db_path)
        _serve(port=a.port, open_browser=not a.no_browser, db_path=a.db_path)
        return

    args = _build_parser().parse_args()

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

    prefs = GenerationPrefs()

    if args.track:
        result = analyze_by_title(args.track, db)
        if result is None:
            print(f"Track not found: {args.track!r}", file=sys.stderr)
            sys.exit(1)
        content, _ = result
        cues, mode = generate_cues_for_track(content, db, prefs)
        if not cues and not args.serato:
            # --serato can still mirror existing Rekordbox cues below
            print(f"No cue data generated for {args.track!r}.")
            sys.exit(0)
        tracks = [(content, cues, mode)]

    elif args.track_id:
        result = analyze_by_id(args.track_id, db)
        if result is None:
            print(f"Track not found: ID={args.track_id}", file=sys.stderr)
            sys.exit(1)
        content, _ = result
        cues, mode = generate_cues_for_track(content, db, prefs)
        if not cues and not args.serato:
            # --serato can still mirror existing Rekordbox cues below
            print(f"No cue data generated for track ID={args.track_id}.")
            sys.exit(0)
        tracks = [(content, cues, mode)]

    else:
        # --library mode
        print("Scanning library…")

        if args.playlist:
            tracks = _process_playlist(args.playlist, db, prefs)
            if tracks is None:
                sys.exit(1)
        else:
            tracks = _process_all(db, prefs)

        if not tracks:
            print("No tracks found in library.")
            sys.exit(0)

        # For Serato export, tracks with existing Rekordbox cues are exactly
        # the ones to mirror — the skip filter only applies to the XML path.
        if not args.overwrite and not args.serato:
            filtered = []
            for content, cues, mode in tracks:
                n = has_existing_hot_cues(content, db)
                if n > 0:
                    title = content.Title or content.FileNameL or "Unknown"
                    print(
                        f"  {title}: skipping — already has {n} hot cue(s). "
                        "Use --overwrite to replace."
                    )
                else:
                    filtered.append((content, cues, mode))
            tracks = filtered

        if not tracks:
            print("No eligible tracks to process (all already have hot cues). Use --overwrite to re-generate.")
            sys.exit(0)

    _print_summary(tracks)

    if args.dry_run:
        # C-3: preview loop placements too (they are otherwise computed only
        # inside the real --serato write branch, which sits after this return).
        if args.loops:
            from .analyzer import analyze_loops
            for content, _generated, _ in tracks:
                title = content.Title or content.FileNameL or "Unknown"
                for loop in analyze_loops(content, db):
                    smin, ssec = divmod(loop.position_ms // 1000, 60)
                    emin, esec = divmod((loop.loop_end_ms or 0) // 1000, 60)
                    bars = (loop.loop_beats // 4) if loop.loop_beats else "?"
                    print(
                        f"  {title}: loop [{loop.name}] "
                        f"{smin:02d}:{ssec:02d}–{emin:02d}:{esec:02d} ({bars} bars)"
                    )
        print("\nDry run — no files written.")
        return

    if args.serato:
        if _serato_running():
            print(
                "Error: Serato DJ appears to be running. Close Serato DJ first — "
                "it caches file tags and may overwrite or ignore the new cues.",
                file=sys.stderr,
            )
            sys.exit(1)
        # Mirror-first: Serato receives the exact cues already in the
        # Rekordbox library; only uncued tracks get freshly generated cues.
        from .db_writer import read_hot_cues
        if args.loops:
            from .analyzer import analyze_loops
        export_pairs = []
        print()
        for content, generated, _ in tracks:
            title = content.Title or content.FileNameL or "Unknown"
            existing = read_hot_cues(content, db)
            if existing:
                print(f"  {title}: mirroring {len(existing)} cue(s) from Rekordbox")
                cues = existing
            elif generated:
                print(f"  {title}: no Rekordbox cues — using {len(generated)} generated cue(s)")
                cues = generated
            else:
                print(f"  {title}: no Rekordbox cues and none generated — skipped")
                continue
            if args.loops:
                loops = analyze_loops(content, db)
                if loops:
                    cues = _merge_loops(cues, loops)
                    print(f"    + {len(loops)} loop(s): {', '.join(loop.name for loop in loops)}")
            export_pairs.append((content, cues))
        try:
            from .serato_writer import write_serato
            summary = write_serato(export_pairs, overwrite=args.overwrite)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(
            f"\nSerato export: {summary.written} written · "
            f"{summary.skipped_existing} skipped (already cued) · "
            f"{summary.comments_updated} comment(s) updated · "
            f"{summary.unsupported} unsupported · {summary.missing} missing · "
            f"{len(summary.errors)} error(s)"
        )
        print(
            "Tracks already in Serato's library need Files panel > "
            '"Rescan ID3 Tags" in Serato before the new cues appear.'
        )
        return

    if args.loops:
        print(
            "\nNote: --loops currently writes loops only with --serato "
            "(Rekordbox XML loop export arrives in a later increment)."
        )

    output = write_xml([(c, cues) for c, cues, _ in tracks], args.output)
    print(f"\nWrote {output}")
    print("Import in Rekordbox: File > Import Library > select the XML file above.")


def _serato_running() -> bool:
    """True if a Serato DJ process is running (tags are cached while open)."""
    import psutil

    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if "serato" in name.lower():
            return True
    return False


def _process_all(db: MasterDatabase, prefs: GenerationPrefs) -> list[tuple]:
    """Return (content, cues, mode) for every track in the library."""
    from pyrekordbox.db6 import DjmdContent
    results = []
    for content in db.get_content().all():
        cues, mode = generate_cues_for_track(content, db, prefs)
        if cues:
            results.append((content, cues, mode))
    return results


def _process_playlist(
    playlist_name: str, db: MasterDatabase, prefs: GenerationPrefs
) -> list[tuple] | None:
    """Return (content, cues, mode) for all tracks in the named playlist, or None on error."""
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

    results = []
    for content in db.get_content().all():
        if content.ID not in content_ids:
            continue
        cues, mode = generate_cues_for_track(content, db, prefs)
        if cues:
            results.append((content, cues, mode))
    return results


def _print_summary(tracks: list) -> None:
    total_cues = sum(len(cues) for _, cues, _ in tracks)
    print(f"\n{len(tracks)} track(s) · {total_cues} cue(s) total\n")
    for content, cues, mode in tracks:
        title = content.Title or content.FileNameL or "Unknown"
        print(f"  {title}  [{mode}]")
        for cue in cues:
            mins, secs = divmod(cue.position_ms // 1000, 60)
            print(f"    [{cue.slot_name}] {mins:02d}:{secs:02d}  {cue.label.value}")
