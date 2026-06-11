from __future__ import annotations

import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .models import CuePoint

BACKUP_DIR = Path.home() / ".autocue" / "backups"
logger = logging.getLogger(__name__)


def backup_database(
    db_path: Path,
    *,
    discover_db_path: Path | None = None,
) -> Path:
    """Copy master.db (and WAL/SHM sidecars) to ~/.autocue/backups/master_TIMESTAMP.db.

    Per PRD §6.7, when ``discover_db_path`` is supplied AND the file exists,
    a parallel ``discover_<TIMESTAMP>.db`` is written alongside the master
    backup. The two files share the same timestamp so the /api/backups
    listing endpoint can group them as one logical backup. No tarball — the
    flat-file pattern is preserved so the existing UI keeps working.

    Returns the master backup path (the function's historical contract).
    Callers that need the discover path can derive it from the timestamp.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = BACKUP_DIR / f"master_{ts}.db"
    shutil.copy2(db_path, dest)
    for suf in ("-wal", "-shm"):
        src = Path(str(db_path) + suf)
        if src.exists():
            shutil.copy2(src, Path(str(dest) + suf))
    logger.info("Backup → %s", dest)

    # Discover sidecar — PRD §6.7. Skip silently if the discover file doesn't
    # exist yet (first run before any DiscoverStore activity).
    if discover_db_path is not None and Path(discover_db_path).exists():
        discover_dest = BACKUP_DIR / f"discover_{ts}.db"
        shutil.copy2(discover_db_path, discover_dest)
        logger.info("Discover sidecar backup → %s", discover_dest)

    return dest


def _process_name_check() -> bool:
    """Process-name probe — fast but defeatable by renamed Rekordbox builds."""
    try:
        import psutil
        return any("rekordbox" in p.name().lower() for p in psutil.process_iter(["name"]))
    except ImportError:
        return False
    except Exception:
        # psutil itself raised — treat as unknown (fail open)
        return False


def _db_file_is_locked(db_path: Path | str) -> bool:
    """Try to acquire an exclusive non-blocking lock on master.db.

    Returns True when another process is holding the file open with an
    exclusive lock — strong evidence that Rekordbox (or a Rekordbox-like
    application) currently owns the DB even if its process name is unusual.

    Returns False when the lock attempt succeeds (which means nobody else
    is holding it). The lock is released immediately.

    Cross-platform: uses ``fcntl`` on Unix-likes and ``msvcrt`` on Windows.
    Any error opening the file (missing, permission denied) returns False
    so the legacy process-name check remains the safety net.
    """
    p = Path(db_path)
    if not p.exists():
        return False
    try:
        if sys.platform == "win32":
            import msvcrt  # type: ignore[import-not-found]
            with open(p, "r+b") as fh:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                except OSError:
                    return True
                else:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                    return False
        else:
            import fcntl
            with open(p, "r+b") as fh:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, BlockingIOError):
                    return True
                else:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    return False
    except Exception:
        # Permission denied / unknown OS / etc. — leave the verdict to the
        # process-name check rather than blocking writes on a spurious error.
        return False


def rekordbox_is_running(db_path: Path | str | None = None) -> bool:
    """Return True if Rekordbox appears to be running.

    Two signals are combined:

    1. A ``psutil`` process-name probe (fast, but a renamed Rekordbox
       build can slip through).
    2. An exclusive file-lock attempt on ``master.db`` when ``db_path`` is
       supplied (catches renamed builds and races where the process started
       after the name check fired).

    ``db_path`` is optional for backwards compatibility — callers that
    can provide it (e.g. SSE write endpoints with ``app.state.db`` in
    scope) should pass it to enable the lock check.
    """
    if _process_name_check():
        return True
    if db_path is not None and _db_file_is_locked(db_path):
        return True
    return False


def has_existing_hot_cues(content, db) -> int:
    from pyrekordbox.db6 import DjmdCue
    return (
        db.query(DjmdCue)
        .filter(DjmdCue.ContentID == content.ID, DjmdCue.Kind > 0)
        .count()
    )


def has_existing_memory_cues(content, db) -> int:
    from pyrekordbox.db6 import DjmdCue
    return (
        db.query(DjmdCue)
        .filter(DjmdCue.ContentID == content.ID, DjmdCue.Kind == 0)
        .count()
    )


def delete_tracks(db, track_ids: list[int], *, dry_run: bool = False) -> dict:
    """Delete one or more tracks from the Rekordbox library.

    Phase 2 of the duplicates feature. Safety:
      * caller MUST verify ``rekordbox_is_running(db_path)`` and create a
        backup via :func:`backup_database` BEFORE calling this — same
        contract every write op enforces at the route layer.
      * each delete is wrapped in a savepoint via ``begin_nested()``; on
        any per-track exception the row's nested transaction rolls back
        while previously-committed rows survive.
      * cues (``DjmdCue``) AND history-song rows (``DjmdSongHistory``,
        ``DjmdSongPlaylist``, ``DjmdSongTagList``) are deleted first to
        satisfy FK constraints, then the ``DjmdContent`` row goes via
        ``db.delete(content)`` so pyrekordbox's registry stays consistent.
      * the user's existing ``/api/restore`` flow IS the undo path —
        backup_path is plumbed up to the route and surfaced in the toast
        so the user knows exactly which file to roll back to.

    Returns ``{"deleted": <count>, "skipped": <count>, "dry_run": bool}``.
    """
    # Every pyrekordbox table that has a ContentID FK to DjmdContent.ID.
    # If this list drops a table, the corresponding child rows orphan when
    # the parent goes — silently when SQLite's FK enforcement is off (the
    # default on Rekordbox's SQLCipher DB), or with an IntegrityError when
    # it's on. We started with 4 here and missed 9; restoring the full set.
    # Verified via:
    #
    #   for name in dir(pyrekordbox.db6.tables):
    #     cls = getattr(t, name)
    #     if 'ContentID' in [c.name for c in cls.__table__.columns]: ...
    #
    # If pyrekordbox adds a new ContentID-bearing table in a future
    # release, the integration test in tests/test_duplicates_integration.py
    # will surface the orphan rows on the next pytest run.
    from pyrekordbox.db6 import (
        ContentActiveCensor,
        ContentCue,
        ContentFile,
        DjmdActiveCensor,
        DjmdContent,
        DjmdCue,
        DjmdMixerParam,
        DjmdSongHistory,
        DjmdSongHotCueBanklist,
        DjmdSongMyTag,
        DjmdSongPlaylist,
        DjmdSongRelatedTracks,
        DjmdSongSampler,
        DjmdSongTagList,
    )

    deleted = skipped = 0
    titles_for_log: list[str] = []

    for tid in track_ids:
        try:
            content = db.get_content(ID=tid)
        except Exception:
            skipped += 1
            continue
        if content is None:
            skipped += 1
            continue

        title_for_log = str(getattr(content, "Title", "") or f"id={tid}")

        if dry_run:
            deleted += 1
            titles_for_log.append(title_for_log)
            continue

        try:
            sp = db.session.begin_nested()
            # Cascade by hand: clear every FK-bearing child row, then the
            # parent. Each filter is keyed on ContentID so a typo here
            # would just delete nothing — the row count is captured via
            # the rowcount on the bulk delete.
            for child_model in (
                # Cue / waveform / fingerprint side
                DjmdCue,
                ContentCue,
                ContentActiveCensor,
                ContentFile,
                DjmdActiveCensor,
                DjmdSongHotCueBanklist,
                DjmdMixerParam,
                DjmdSongSampler,
                # Play / library / tagging side
                DjmdSongHistory,
                DjmdSongPlaylist,
                DjmdSongTagList,
                DjmdSongMyTag,
                DjmdSongRelatedTracks,
            ):
                db.session.query(child_model).filter(
                    child_model.ContentID == content.ID
                ).delete(synchronize_session=False)
            db.delete(content)
            sp.commit()
            deleted += 1
            titles_for_log.append(title_for_log)
        except Exception:
            # Roll the savepoint back — subsequent track IDs in the list
            # remain delete-able. The route-level handler decides whether
            # to abort the overall transaction.
            db.session.rollback()
            logger.exception(
                "delete_tracks: failed on id=%s (%r) — rolled back this row",
                tid, title_for_log,
            )
            skipped += 1

    if not dry_run and deleted > 0:
        try:
            db.session.commit()
            logger.info(
                "delete_tracks: deleted %d tracks (sample: %s)",
                deleted, ", ".join(repr(t) for t in titles_for_log[:3]),
            )
        except Exception:
            db.session.rollback()
            logger.exception(
                "delete_tracks: top-level commit failed — entire batch rolled back"
            )
            raise

    return {"deleted": deleted, "skipped": skipped, "dry_run": dry_run}


def delete_cues_from_db(content, db, *, dry_run: bool = False) -> int:
    """Delete all hot cues (Kind 1-8) for a track. Returns count deleted."""
    from pyrekordbox.db6 import DjmdCue

    count = (
        db.query(DjmdCue)
        .filter(DjmdCue.ContentID == content.ID, DjmdCue.Kind >= 1, DjmdCue.Kind <= 8)
        .count()
    )
    if dry_run:
        logger.info("[dry-run] Would delete %d cues from %r", count, content.Title)
        return count
    if count == 0:
        return 0
    try:
        sp = db.session.begin_nested()
        (
            db.session.query(DjmdCue)
            .filter(DjmdCue.ContentID == content.ID, DjmdCue.Kind >= 1, DjmdCue.Kind <= 8)
            .delete(synchronize_session=False)
        )
        sp.commit()
        db.session.commit()
        logger.info("Deleted %d cues from %r", count, content.Title)
        return count
    except Exception:
        db.session.rollback()
        logger.exception("Delete failed for %r — rolled back", content.Title)
        raise


def _bpm_to_color_sort_key(bpm: float) -> int:
    """Map BPM to DjmdColor.SortKey (0 = no color, 1-8 = Pink/Red/Orange/Yellow/Green/Aqua/Blue/Purple)."""
    if bpm <= 0:  return 0
    if bpm < 90:  return 6   # Aqua
    if bpm < 115: return 5   # Green
    if bpm < 125: return 7   # Blue
    if bpm < 135: return 3   # Orange
    if bpm < 150: return 2   # Red
    return 1                  # Pink


def color_tracks_by_bpm(track_ids: list, db, *, dry_run: bool = False, skip_colored: bool = False) -> tuple[int, int]:
    """Set DjmdContent.ColorID based on BPM range. Returns (colored, skipped).

    Looks up actual DjmdColor.ID strings by SortKey at runtime so UUID-format IDs
    are resolved correctly regardless of what Rekordbox stored.
    """
    from pyrekordbox.db6 import DjmdColor

    color_by_sort_key: dict[int, str | None] = {
        c.SortKey: c.ID for c in db.query(DjmdColor).all() if c.SortKey is not None
    }

    colored = skipped = 0
    pending: list[tuple] = []

    for tid in track_ids:
        content = db.get_content(ID=tid)
        if content is None:
            skipped += 1
            continue
        if skip_colored and getattr(content, "ColorID", None):
            skipped += 1
            continue
        bpm_raw = getattr(content, "BPM", None)
        bpm = float(bpm_raw) / 100 if bpm_raw else 0.0
        sort_key = _bpm_to_color_sort_key(bpm)
        color_id = color_by_sort_key.get(sort_key)  # None → clear color
        pending.append((content, color_id))
        colored += 1

    if dry_run:
        return colored, skipped

    if not pending:
        return colored, skipped

    try:
        from sqlalchemy import text as _text
        stmt = _text("UPDATE djmdContent SET ColorID = :cid WHERE ID = :tid")
        for content, color_id in pending:
            db.session.execute(stmt, {"cid": color_id, "tid": content.ID})
        # Expire all loaded ORM objects so autoflush cannot overwrite the raw UPDATEs
        db.session.expire_all()
        db.session.commit()
        logger.info("Colored %d tracks by BPM", colored)
    except Exception:
        db.session.rollback()
        logger.exception("Color-by-BPM failed — rolled back")
        raise

    return colored, skipped


def write_cues_to_db(
    content,
    cues: list[CuePoint],
    db,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> int:
    """
    Write CuePoints into DjmdCue for the given track.
    Returns number of cues written (0 on dry_run or skip).
    Raises on write failure after automatic session rollback.
    """
    from pyrekordbox.db6 import DjmdCue

    if not overwrite and has_existing_hot_cues(content, db) > 0:
        logger.debug("Skip %r — existing hot cues", content.Title)
        return 0

    if dry_run:
        logger.info("[dry-run] Would write %d cues to %r", len(cues), content.Title)
        return 0

    if not cues:
        return 0

    # In DjmdCue: Kind encodes the hot cue slot — Kind=slot+1 (A=1, B=2, …, H=8).
    # Kind=0 is a memory cue. There is no separate "Num" column.
    from uuid import uuid4
    mem_cues = [c for c in cues if c.slot == -1]
    hot_cue_list = [c for c in cues if c.slot >= 0]
    hot_kinds = {c.slot + 1 for c in hot_cue_list}  # Kind 1-8 only — never includes 0

    # Memory cues are only overwritten when explicitly requested or none exist yet.
    # This prevents silently destroying manually-placed DJ memory cues.
    write_memory = bool(mem_cues) and (overwrite or has_existing_memory_cues(content, db) == 0)
    cues_to_write = hot_cue_list + (mem_cues if write_memory else [])

    content_uuid = getattr(content, "UUID", None) or ""
    try:
        sp = db.session.begin_nested()
        if hot_kinds:
            (
                db.session.query(DjmdCue)
                .filter(
                    DjmdCue.ContentID == content.ID,
                    DjmdCue.Kind.in_(hot_kinds),
                )
                .delete(synchronize_session=False)
            )
        if write_memory:
            (
                db.session.query(DjmdCue)
                .filter(DjmdCue.ContentID == content.ID, DjmdCue.Kind == 0)
                .delete(synchronize_session=False)
            )
        for cue in cues_to_write:
            # InFrame: CDJ uses 150 sub-frames per second for cue precision
            in_frame = int(round(cue.position_ms * 150.0 / 1000.0))
            db.session.add(
                DjmdCue(
                    ID=str(db.generate_unused_id(DjmdCue)),
                    ContentID=content.ID,
                    ContentUUID=content_uuid,
                    UUID=str(uuid4()),
                    InMsec=cue.position_ms,
                    InFrame=in_frame,
                    InMpegFrame=0,
                    InMpegAbs=0,
                    OutMsec=-1,
                    OutFrame=0,
                    OutMpegFrame=0,
                    OutMpegAbs=0,
                    Kind=cue.slot + 1,
                    Color=0,
                    ColorTableIndex=cue.color_id,
                    ActiveLoop=0,
                    BeatLoopSize=0,
                    CueMicrosec=0,
                    Comment=cue.name or cue.label.value,
                )
            )
        sp.commit()
        db.session.commit()
        logger.info("Wrote %d cues to %r", len(cues_to_write), content.Title)
        return len(cues_to_write)
    except Exception:
        db.session.rollback()
        logger.exception("Write failed for %r — rolled back", content.Title)
        raise
