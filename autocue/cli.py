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
        "--write-db",
        action="store_true",
        help=(
            "Write the generated loops DIRECTLY into the Rekordbox database as "
            "named memory loops (requires --loops). Rekordbox must be closed; a "
            "backup is taken first and its path printed — that backup is your only "
            "undo. Existing memory cues/loops are never touched."
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


def _default_db_path():
    """Resolve Rekordbox's master.db WITHOUT opening it (mirrors pyrekordbox's own
    lookup: rekordbox7 config, else rekordbox6)."""
    from pathlib import Path
    try:
        from pyrekordbox.config import get_config
        cfg = get_config("rekordbox7") or get_config("rekordbox6")
        path = (cfg or {}).get("db_path", "") if isinstance(cfg, dict) else ""
        return Path(path) if path else None
    except Exception:
        return None


def _preflight_write_db(args):
    """Resolve master.db and run the single-writer guards BEFORE the DB is opened.

    🔴 BL-1: ``rekordbox_is_running()`` probes an EXCLUSIVE FILE LOCK on master.db.
    Once AutoCue has opened the DB and issued a query, SQLAlchemy's autobegin
    transaction holds a SQLite lock — so the guard detected **AutoCue's own handle**
    and aborted every real run with a false "Rekordbox is running". The guard is
    therefore run here, before ``MasterDatabase(...)`` is ever constructed. That is
    also the semantically correct place: Rekordbox must be closed before we even
    open the database.

    Returns the resolved ``Path`` to the file that the guard, the backup AND the
    write all target (auditor #85: they must be the SAME file).
    """
    from pathlib import Path

    from . import db_writer

    if not args.loops:
        print(
            "Error: --write-db requires --loops. This increment writes LOOPS "
            "only — it never writes cues to the database.",
            file=sys.stderr,
        )
        sys.exit(1)

    # The file actually opened — NOT a reconstructed _db_dir/"master.db".
    db_path = Path(args.db_path) if args.db_path else _default_db_path()
    if db_path is None:
        print(
            "Error: could not locate master.db — pass --db-path.", file=sys.stderr
        )
        sys.exit(1)
    if not db_path.exists():
        print(f"Error: master.db not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    if db_writer.rekordbox_is_running(db_path):
        print(
            "Error: Rekordbox is running — close it before writing to the "
            "database (the DB is locked while Rekordbox is open).",
            file=sys.stderr,
        )
        sys.exit(1)

    # rekordbox_is_running does NOT detect a running `autocue serve`, which holds
    # its own read-write handle — writing now would break the single-writer rule.
    if db_writer.autocue_serve_is_running():
        print(
            "Error: a local `autocue serve` is running and holds the database "
            "open. Stop the server before using --write-db (single-writer rule).",
            file=sys.stderr,
        )
        sys.exit(1)

    return db_path


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

    # BL-1: for --write-db the Rekordbox/serve guards MUST run BEFORE we open the
    # DB ourselves — an open handle + an autobegin txn holds a SQLite lock that the
    # lock probe would mistake for Rekordbox. Only --write-db is affected; every
    # other CLI path is unchanged.
    write_db_path = _preflight_write_db(args) if args.write_db else None

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
            for content, generated, _ in tracks:
                title = content.Title or content.FileNameL or "Unknown"
                # Preview the MERGED/collision-filtered set (== what is written),
                # not the raw policy output.
                merged = _merge_loops(list(generated), analyze_loops(content, db))
                for loop in (c for c in merged if c.is_loop):
                    smin, ssec = divmod(loop.position_ms // 1000, 60)
                    emin, esec = divmod((loop.loop_end_ms or 0) // 1000, 60)
                    bars = (loop.loop_beats // 4) if loop.loop_beats else "?"
                    print(
                        f"  {title}: loop [{loop.name}] "
                        f"{smin:02d}:{ssec:02d}–{emin:02d}:{esec:02d} ({bars} bars)"
                    )
        print("\nDry run — no files written.")
        return

    # ---- DB-DIRECT loop write (INC-3) — the ONLY path that mutates master.db ----
    # Safety contract mirrors the server apply route (routes.py:975-997):
    # Rekordbox-closed guard → single-writer guard → backup-or-abort → print the
    # backup path (the user's only undo). Reached only when NOT --dry-run (the
    # dry-run block above already returned), so --write-db --dry-run writes nothing.
    if args.write_db:
        from .analyzer import analyze_loops
        from . import db_writer

        # The --loops gate, the Rekordbox guard and the serve guard already ran in
        # _preflight_write_db() — BEFORE the DB was opened (BL-1). write_db_path is
        # the file actually opened, so guard/backup/write all target the SAME file.
        db_path = write_db_path

        # Never write without a successful backup.
        try:
            backup = db_writer.backup_database(db_path)
        except Exception as e:
            print(f"Error: backup failed — aborting, nothing written: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"\nBackup: {backup}")
        print("  ^ your ONLY undo — keep it until you've checked the result in Rekordbox.\n")

        written = skipped = 0
        failed: list[str] = []
        for content, _cues, _ in tracks:
            title = content.Title or content.FileNameL or "Unknown"
            try:
                loops = analyze_loops(content, db)
                if not loops:
                    print(f"  {title}: no eligible loops — skipped")
                    continue
                n = db_writer.write_loops_to_db(content, loops, db)
            except Exception as e:
                # N1: don't dump a raw traceback mid-library — report the failing
                # track, keep going, and remind the user where their undo lives.
                failed.append(title)
                print(f"  {title}: ERROR — {e}", file=sys.stderr)
                continue
            written += n
            skipped += len(loops) - n
            note = "" if n == len(loops) else f" ({len(loops) - n} already had an entry at that start)"
            print(f"  {title}: wrote {n} loop(s){note}")

        print(f"\nDatabase write: {written} named memory loop(s) added · {skipped} skipped.")
        if failed:
            print(
                f"{len(failed)} track(s) FAILED: {', '.join(failed)} — "
                f"earlier tracks are already committed. Backup: {backup}",
                file=sys.stderr,
            )
        print("Existing memory cues/loops were left untouched. Open Rekordbox to see them.")
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

    # XML import path. INC-2 taught writer.py to emit <POSITION_MARK Type="loop"
    # End=…> for loop cues; layer the generated loops onto each track's cues
    # here (mirrors the --serato block) so `autocue --loops` actually writes
    # memory loops to the XML — otherwise write_xml receives loop-free cues.
    if args.loops:
        from .analyzer import analyze_loops
        xml_pairs = []
        added = 0
        for content, cues, _ in tracks:
            loops = analyze_loops(content, db)
            merged = _merge_loops(cues, loops) if loops else cues
            added += len(merged) - len(cues)
            xml_pairs.append((content, merged))
        output = write_xml(xml_pairs, args.output)
        print(f"\nWrote {output} — {added} named loop(s) added")
    else:
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
