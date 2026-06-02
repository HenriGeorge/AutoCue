from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path

from .models import CuePoint

BACKUP_DIR = Path.home() / ".autocue" / "backups"
logger = logging.getLogger(__name__)


def backup_database(db_path: Path) -> Path:
    """Copy master.db (and WAL/SHM sidecars) to ~/.autocue/backups/master_TIMESTAMP.db."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = BACKUP_DIR / f"master_{ts}.db"
    shutil.copy2(db_path, dest)
    for suf in ("-wal", "-shm"):
        src = Path(str(db_path) + suf)
        if src.exists():
            shutil.copy2(src, Path(str(dest) + suf))
    logger.info("Backup → %s", dest)
    return dest


def rekordbox_is_running() -> bool:
    """Return True if a Rekordbox process is running."""
    try:
        import psutil
        return any("rekordbox" in p.name().lower() for p in psutil.process_iter(["name"]))
    except ImportError:
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
