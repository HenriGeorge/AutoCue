from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from ..analysis.quality import check_library_health, check_track_health
from ..db_writer import has_existing_hot_cues
from ..generator import GenerationPrefs, generate_cues_for_track
from .deps import get_db
from .schemas import (
    ApplyRequest,
    ApplyResponse,
    BackupItem,
    ColorTracksRequest,
    ColorTracksResponse,
    CueIssueSchema,
    CueItem,
    CueToolsRequest,
    CueToolsSummary,
    DeleteRequest,
    DeleteResponse,
    ClassificationResponse,
    EnergyResponse,
    MixabilityComponents,
    MixabilityResponse,
    PlaylistSuggestItem,
    PlaylistSuggestRequest,
    PlaylistSuggestResponse,
    SetBuilderRequest,
    SetBuilderResponse,
    SetBuilderTrackItem,
    SimilarTrackItem,
    SimilarTracksResponse,
    TransitionRequest,
    TransitionResponse,
    GenerateAndApplyRequest,
    GenerateRequest,
    GenerateResponse,
    LibraryHealthSummary,
    PlaylistItem,
    RestoreRequest,
    RestoreResponse,
    StatusResponse,
    TrackHealthReport,
    TrackItem,
    TrackResult,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.get("/status", response_model=StatusResponse)
def status(db=Depends(get_db)):
    count = db.get_content().count()
    return StatusResponse(connected=True, track_count=count)


@router.get("/playlists", response_model=list[PlaylistItem])
def playlists(db=Depends(get_db)):
    from pyrekordbox.db6 import DjmdPlaylist, DjmdSongPlaylist
    rows = db.query(DjmdPlaylist).filter(DjmdPlaylist.Name.isnot(None)).all()
    return [
        PlaylistItem(
            id=p.ID,
            name=p.Name,
            track_count=db.query(DjmdSongPlaylist).filter_by(PlaylistID=p.ID).count(),
        )
        for p in rows
    ]


@router.get("/tracks", response_model=list[TrackItem])
def tracks(
    response: Response,
    playlist_id: int | None = Query(None),
    sort_by: str = Query("title"),
    sort_order: str = Query("asc"),
    limit: int = Query(5000),
    offset: int = 0,
    db=Depends(get_db),
):
    from pyrekordbox.db6 import DjmdAlbum, DjmdArtist, DjmdContent, DjmdKey, DjmdPlaylist, DjmdSongPlaylist
    from sqlalchemy import asc, desc, func

    q = db.get_content()
    if playlist_id is not None:
        pl = db.query(DjmdPlaylist).filter_by(ID=str(playlist_id)).first()
        if not pl:
            raise HTTPException(404, f"Playlist {playlist_id} not found")
        ids = {
            e.ContentID
            for e in db.query(DjmdSongPlaylist).filter_by(PlaylistID=pl.ID)
        }
        q = q.filter(DjmdContent.ID.in_(ids))

    order_fn = desc if sort_order == "desc" else asc
    if sort_by == "bpm":
        q = q.order_by(order_fn(DjmdContent.BPM))
    elif sort_by == "artist":
        q = q.outerjoin(DjmdArtist, DjmdContent.ArtistID == DjmdArtist.ID)
        q = q.order_by(order_fn(func.lower(DjmdArtist.Name)))
    elif sort_by == "album":
        q = q.outerjoin(DjmdAlbum, DjmdContent.AlbumID == DjmdAlbum.ID)
        q = q.order_by(order_fn(func.lower(DjmdAlbum.Name)))
    elif sort_by == "key":
        q = q.outerjoin(DjmdKey, DjmdContent.KeyID == DjmdKey.ID)
        q = q.order_by(order_fn(DjmdKey.Seq))
    elif sort_by == "rating":
        q = q.order_by(order_fn(DjmdContent.Rating))
    elif sort_by == "plays":
        q = q.order_by(order_fn(DjmdContent.DJPlayCount))
    else:
        q = q.order_by(order_fn(func.lower(DjmdContent.Title)))

    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    # Pre-load the 24-row key table once rather than querying per track
    key_map = {k.ID: k.ScaleName for k in db.query(DjmdKey).all() if k.ScaleName}

    # Pre-load play history: ContentID → latest session DateCreated
    last_played_map: dict[str, str] = {}
    try:
        from pyrekordbox.db6 import DjmdHistory, DjmdSongHistory
        hist_date = {str(h.ID): h.DateCreated for h in db.query(DjmdHistory).all() if h.DateCreated}
        for sh in db.query(DjmdSongHistory).all():
            d = hist_date.get(str(sh.HistoryID))
            if d and sh.ContentID:
                key = str(sh.ContentID)
                if key not in last_played_map or d > last_played_map[key]:
                    last_played_map[key] = d
    except Exception:
        pass

    # Pre-load my tags: ContentID → [tag names]
    my_tags_map: dict[str, list[str]] = {}
    try:
        from pyrekordbox.db6 import DjmdMyTag, DjmdSongMyTag
        tag_names = {str(t.ID): t.Name for t in db.query(DjmdMyTag).all() if t.Name}
        for st in db.query(DjmdSongMyTag).all():
            n = tag_names.get(str(st.MyTagID))
            if n and st.ContentID:
                my_tags_map.setdefault(str(st.ContentID), []).append(n)
    except Exception:
        pass

    # Pre-load color names
    color_name_map: dict[str, str] = {}
    try:
        from pyrekordbox.db6 import DjmdColor
        for c in db.query(DjmdColor).all():
            if c.ID and c.Commnt:
                color_name_map[str(c.ID)] = c.Commnt
    except Exception:
        pass

    # Pre-load hot cue counts in one GROUP BY query instead of one COUNT per track
    from pyrekordbox.db6 import DjmdCue
    from sqlalchemy import func as _func
    hot_cue_counts: dict = dict(
        db.query(DjmdCue.ContentID, _func.count(DjmdCue.ID))
        .filter(DjmdCue.Kind >= 1, DjmdCue.Kind <= 8)
        .group_by(DjmdCue.ContentID)
        .all()
    )

    response.headers["X-Total-Count"] = str(total)
    return [_to_item(t, db, key_map, last_played_map, my_tags_map, color_name_map, hot_cue_counts) for t in rows]


_FOLDER_ART_NAMES = ["cover.jpg", "folder.jpg", "artwork.jpg", "front.jpg",
                     "Cover.jpg", "Folder.jpg", "Artwork.jpg", "Front.jpg",
                     "cover.png", "folder.png", "artwork.png", "front.png"]

@router.get("/tracks/{track_id}/artwork")
def track_artwork(track_id: int, db=Depends(get_db)):
    from pathlib import Path

    content = db.get_content(ID=track_id)
    if content is None:
        raise HTTPException(404, "Track not found")

    ext_types = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif"}

    # Primary: Rekordbox-cached artwork via ImagePath
    image_path = getattr(content, "ImagePath", None)
    if image_path:
        db_dir = getattr(db, "_db_dir", None)
        candidates = [
            Path(image_path),
            *(([Path(db_dir) / image_path.lstrip("/"),
                Path(db_dir) / "share" / image_path.lstrip("/")]) if db_dir else []),
        ]
        for c in candidates:
            if c.exists():
                return FileResponse(str(c), media_type=ext_types.get(c.suffix.lower(), "image/jpeg"))

    # Fallback: look for cover art in the same directory as the audio file
    raw_path = getattr(content, "FolderPath", None) or ""
    raw_path = raw_path.lstrip("/:") if raw_path.startswith("/:") else raw_path
    if raw_path and not raw_path.startswith("/"):
        raw_path = "/" + raw_path
    if raw_path:
        audio_dir = Path(raw_path).parent
        for name in _FOLDER_ART_NAMES:
            candidate = audio_dir / name
            if candidate.exists():
                return FileResponse(str(candidate), media_type=ext_types.get(candidate.suffix.lower(), "image/jpeg"))

    raise HTTPException(404, "No artwork")


@router.get("/tracks/{track_id}/audio")
def track_audio(track_id: int, db=Depends(get_db)):
    from pathlib import Path

    content = db.get_content(ID=track_id)
    if content is None:
        raise HTTPException(404, "Track not found")
    # FolderPath stores the complete file path (despite the name)
    raw = getattr(content, "FolderPath", None) or ""
    if not raw:
        raise HTTPException(404, "No file path in database")
    # Rekordbox on macOS sometimes prefixes volume paths with /:
    raw = raw.lstrip("/:") if raw.startswith("/:") else raw
    if not raw.startswith("/"):
        raw = "/" + raw
    full = Path(raw)
    if not full.exists():
        raise HTTPException(404, f"Audio file not found on disk: {full}")
    ext_types = {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".aac": "audio/aac",
        ".m4a": "audio/mp4", ".flac": "audio/flac", ".ogg": "audio/ogg",
        ".aiff": "audio/aiff", ".aif": "audio/aiff",
    }
    return FileResponse(str(full), media_type=ext_types.get(full.suffix.lower(), "audio/mpeg"))


@router.get("/tags")
def list_tags(db=Depends(get_db)):
    try:
        from pyrekordbox.db6 import DjmdMyTag
        tags = db.query(DjmdMyTag).filter(DjmdMyTag.Name.isnot(None)).all()
        return [{"id": t.ID, "name": t.Name} for t in tags if t.Name]
    except Exception:
        return []


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest, db=Depends(get_db)):
    prefs = GenerationPrefs(
        mode=req.mode,
        bars_interval=req.bars_interval,
        start_bar=req.start_bar,
        max_cues=req.max_cues,
        add_memory_cue=req.add_memory_cue,
        memory_cue_mode=req.memory_cue_mode,
        add_fill_cues=req.add_fill_cues,
    )
    results = []
    for tid in req.track_ids:
        content = db.get_content(ID=tid)
        if content is None:
            continue
        cues, mode_used = generate_cues_for_track(content, db, prefs)
        results.append(
            TrackResult(
                id=tid,
                title=content.Title or "",
                cues=[
                    CueItem(slot=c.slot, label=c.label.value, position_ms=c.position_ms,
                            is_phrase=(mode_used == "phrase"), name=c.name,
                            color_id=c.color_id, confidence=c.confidence,
                            phrase_bars=c.phrase_bars)
                    for c in cues
                ],
                mode_used=mode_used,
                skipped=len(cues) == 0,
            )
        )
    return GenerateResponse(tracks=results)


@router.post("/apply", response_model=ApplyResponse)
def apply(req: ApplyRequest, db=Depends(get_db)):
    from ..db_writer import backup_database, rekordbox_is_running, write_cues_to_db
    from ..models import CuePoint, PhraseLabel

    if rekordbox_is_running():
        raise HTTPException(409, "Rekordbox is running — close it before applying cues")

    backup_path = None
    if not req.dry_run:
        try:
            from pathlib import Path
            db_dir = getattr(db, "_db_dir", None)
            if db_dir is None:
                raise RuntimeError(
                    "Cannot locate master.db: database object has no _db_dir attribute"
                )
            db_path = Path(db_dir) / "master.db"
            if not db_path.exists():
                raise FileNotFoundError(f"master.db not found at {db_path}")
            backup_path = str(backup_database(db_path))
        except Exception as e:
            raise HTTPException(500, f"Backup failed — aborting: {e}")

    applied = skipped = 0
    for track_result in req.tracks:
        content = db.get_content(ID=track_result.id)
        if content is None:
            skipped += 1
            continue
        cues = [
            CuePoint(
                position_ms=c.position_ms,
                label=(
                    PhraseLabel(c.label)
                    if c.label in PhraseLabel._value2member_map_
                    else PhraseLabel.UNKNOWN
                ),
                slot=c.slot,
                name=c.name,
                color_id=c.color_id,
            )
            for c in track_result.cues
        ]
        n = write_cues_to_db(
            content, cues, db, overwrite=req.overwrite, dry_run=req.dry_run
        )
        if n > 0:
            applied += 1
        else:
            skipped += 1

    return ApplyResponse(
        applied=applied,
        skipped=skipped,
        dry_run=req.dry_run,
        backup_path=backup_path,
    )


@router.post("/generate-apply", response_model=ApplyResponse)
def generate_apply(req: GenerateAndApplyRequest, db=Depends(get_db)):
    """Generate cues and write them to the DB in a single pass — avoids the
    large JSON round-trip that the separate /generate + /apply flow requires."""
    from ..db_writer import backup_database, rekordbox_is_running, write_cues_to_db
    from pathlib import Path

    if rekordbox_is_running():
        raise HTTPException(409, "Rekordbox is running — close it before applying cues")

    prefs = GenerationPrefs(
        mode=req.mode,
        bars_interval=req.bars_interval,
        start_bar=req.start_bar,
        max_cues=req.max_cues,
        add_memory_cue=req.add_memory_cue,
        memory_cue_mode=req.memory_cue_mode,
        add_fill_cues=req.add_fill_cues,
    )

    backup_path = None
    if not req.dry_run:
        try:
            db_dir = getattr(db, "_db_dir", None)
            if db_dir is None:
                raise RuntimeError(
                    "Cannot locate master.db: database object has no _db_dir attribute"
                )
            db_path = Path(db_dir) / "master.db"
            if not db_path.exists():
                raise FileNotFoundError(f"master.db not found at {db_path}")
            backup_path = str(backup_database(db_path))
        except Exception as e:
            raise HTTPException(500, f"Backup failed — aborting: {e}")

    applied = skipped = 0
    for tid in req.track_ids:
        content = db.get_content(ID=tid)
        if content is None:
            skipped += 1
            continue
        if req.phrase_only:
            try:
                has_ext = bool(db.get_anlz_path(content, "EXT"))
            except Exception:
                has_ext = False
            if not has_ext:
                skipped += 1
                continue
        cues, _ = generate_cues_for_track(content, db, prefs)
        if not cues:
            skipped += 1
            continue
        n = write_cues_to_db(content, cues, db, overwrite=req.overwrite, dry_run=req.dry_run)
        if n > 0:
            applied += 1
        else:
            skipped += 1

    return ApplyResponse(
        applied=applied,
        skipped=skipped,
        dry_run=req.dry_run,
        backup_path=backup_path,
    )


@router.post("/generate-apply-stream")
def generate_apply_stream(req: GenerateAndApplyRequest, db=Depends(get_db)):
    """SSE endpoint: yields progress events as each track is processed.
    Each event: data: {"processed":N,"total":M,"applied":A,"skipped":S}
    Final event: data: {"done":true,"applied":A,"skipped":S,"backup_path":"..."}
    """
    import json
    from fastapi.responses import StreamingResponse
    from ..db_writer import backup_database, rekordbox_is_running, write_cues_to_db
    from pathlib import Path

    if rekordbox_is_running():
        raise HTTPException(409, "Rekordbox is running — close it before applying cues")

    prefs = GenerationPrefs(
        mode=req.mode,
        bars_interval=req.bars_interval,
        start_bar=req.start_bar,
        max_cues=req.max_cues,
        add_memory_cue=req.add_memory_cue,
        memory_cue_mode=req.memory_cue_mode,
        add_fill_cues=req.add_fill_cues,
    )

    backup_path = None
    if not req.dry_run:
        try:
            db_dir = getattr(db, "_db_dir", None)
            if db_dir is None:
                raise RuntimeError("Cannot locate master.db: database object has no _db_dir attribute")
            db_path = Path(db_dir) / "master.db"
            if not db_path.exists():
                raise FileNotFoundError(f"master.db not found at {db_path}")
            backup_path = str(backup_database(db_path))
        except Exception as e:
            raise HTTPException(500, f"Backup failed — aborting: {e}")

    total = len(req.track_ids)

    def event_stream():
        applied = skipped = 0
        for i, tid in enumerate(req.track_ids):
            # Expire session identity map every 100 tracks to prevent memory accumulation
            if i % 100 == 0 and i > 0:
                db.session.expire_all()
            content = db.get_content(ID=tid)
            if content is None:
                skipped += 1
            else:
                if req.phrase_only:
                    try:
                        has_ext = bool(db.get_anlz_path(content, "EXT"))
                    except Exception:
                        has_ext = False
                    if not has_ext:
                        skipped += 1
                        content = None
                if content is not None:
                    cues, _ = generate_cues_for_track(content, db, prefs)
                    if not cues:
                        skipped += 1
                    else:
                        n = write_cues_to_db(content, cues, db, overwrite=req.overwrite, dry_run=req.dry_run)
                        if n > 0:
                            applied += 1
                        else:
                            skipped += 1
            yield f"data: {json.dumps({'processed': i + 1, 'total': total, 'applied': applied, 'skipped': skipped})}\n\n"
        yield f"data: {json.dumps({'done': True, 'applied': applied, 'skipped': skipped, 'backup_path': backup_path})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/backups", response_model=list[BackupItem])
def list_backups():
    import re
    from datetime import datetime
    from ..db_writer import BACKUP_DIR
    if not BACKUP_DIR.exists():
        return []
    items = []
    for p in sorted(BACKUP_DIR.glob("*.db"), key=lambda f: f.stat().st_mtime, reverse=True):
        stat = p.stat()
        m = re.search(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})", p.stem)
        if m:
            yr, mo, dy, hh, mm, ss = m.groups()
            created_at = f"{yr}-{mo}-{dy} {hh}:{mm}:{ss}"
        else:
            created_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        items.append(BackupItem(
            path=str(p),
            filename=p.name,
            size_mb=round(stat.st_size / (1024 * 1024), 2),
            created_at=created_at,
        ))
    return items


@router.post("/restore", response_model=RestoreResponse)
def restore_backup(req: RestoreRequest, request_obj: Request, db=Depends(get_db)):
    import shutil
    from ..db_writer import BACKUP_DIR, rekordbox_is_running
    from pathlib import Path

    if rekordbox_is_running():
        raise HTTPException(409, "Rekordbox is running — close it before restoring a backup")

    # Path traversal protection: only allow filenames (no path separators), must be within BACKUP_DIR
    if "/" in req.filename or "\\" in req.filename or ".." in req.filename:
        raise HTTPException(400, "Invalid filename")
    backup_path = (BACKUP_DIR / req.filename).resolve()
    if not str(backup_path).startswith(str(BACKUP_DIR.resolve())):
        raise HTTPException(400, "Invalid backup path")
    if not backup_path.exists():
        raise HTTPException(404, f"Backup '{req.filename}' not found")

    db_dir = getattr(db, "_db_dir", None)
    if db_dir is None:
        raise HTTPException(500, "Cannot locate master.db")
    db_path = Path(db_dir) / "master.db"

    # Close the current DB connection so SQLite flushes WAL and we can overwrite the file
    try:
        db.session.close()
        db._engine.dispose()
    except Exception:
        pass

    try:
        shutil.copy2(backup_path, db_path)
        # Copy WAL/SHM sidecars if present in backup, otherwise remove stale ones
        for suf in ("-wal", "-shm"):
            src = Path(str(backup_path) + suf)
            dst = Path(str(db_path) + suf)
            if src.exists():
                shutil.copy2(src, dst)
            elif dst.exists():
                dst.unlink()
    except Exception as e:
        raise HTTPException(500, f"Restore failed: {e}")
    finally:
        # Reopen the database connection
        try:
            from pyrekordbox import Rekordbox6Database
            new_db = Rekordbox6Database(db_path.parent)
            request_obj.app.state.db = new_db
        except Exception as e:
            request_obj.app.state.db = None
            raise HTTPException(500, f"Restore succeeded but could not reopen database: {e}")

    # Invalidate all analysis caches — the restored DB may have different tracks/cues
    from ..analysis import energy as _energy_mod, classify as _classify_mod, score as _score_mod
    from ..analysis import similar as _similar_mod
    _energy_mod.clear_cache()
    _classify_mod._class_cache.clear()
    _score_mod._mixability_cache.clear()
    _similar_mod.clear_index()

    return RestoreResponse(restored=True, message=f"Restored from {req.filename}")


@router.delete("/backups/{filename}")
def delete_backup(filename: str):
    from ..db_writer import BACKUP_DIR
    from pathlib import Path
    backup_path = (BACKUP_DIR / filename).resolve()
    if not str(backup_path).startswith(str(BACKUP_DIR.resolve())):
        raise HTTPException(400, "Invalid backup filename")
    if not backup_path.exists():
        raise HTTPException(404, f"Backup not found: {filename}")
    backup_path.unlink()
    for suf in ("-wal", "-shm"):
        sidecar = Path(str(backup_path) + suf)
        if sidecar.exists():
            sidecar.unlink()
    return {"deleted": filename}


@router.post("/delete-cues", response_model=DeleteResponse)
def delete_cues(req: DeleteRequest, db=Depends(get_db)):
    from ..db_writer import backup_database, delete_cues_from_db, rekordbox_is_running

    if rekordbox_is_running():
        raise HTTPException(409, "Rekordbox is running — close it before deleting cues")

    backup_path = None
    if not req.dry_run:
        try:
            from pathlib import Path
            db_dir = getattr(db, "_db_dir", None)
            if db_dir is None:
                raise RuntimeError(
                    "Cannot locate master.db: database object has no _db_dir attribute"
                )
            db_path = Path(db_dir) / "master.db"
            if not db_path.exists():
                raise FileNotFoundError(f"master.db not found at {db_path}")
            backup_path = str(backup_database(db_path))
        except Exception as e:
            raise HTTPException(500, f"Backup failed — aborting: {e}")

    deleted = 0
    tracks_affected = 0
    for tid in req.track_ids:
        content = db.get_content(ID=tid)
        if content is None:
            continue
        n = delete_cues_from_db(content, db, dry_run=req.dry_run)
        if n > 0:
            deleted += n
            tracks_affected += 1

    return DeleteResponse(
        deleted=deleted,
        tracks_affected=tracks_affected,
        dry_run=req.dry_run,
        backup_path=backup_path,
    )


@router.post("/color-tracks", response_model=ColorTracksResponse)
def color_tracks_ep(req: ColorTracksRequest, db=Depends(get_db)):
    from ..db_writer import backup_database, color_tracks_by_bpm, rekordbox_is_running

    if rekordbox_is_running() and not req.dry_run:
        raise HTTPException(409, "Rekordbox is running — close it before coloring tracks")

    backup_path = None
    if not req.dry_run:
        try:
            from pathlib import Path
            db_dir = getattr(db, "_db_dir", None)
            if db_dir is None:
                raise RuntimeError(
                    "Cannot locate master.db: database object has no _db_dir attribute"
                )
            db_path = Path(db_dir) / "master.db"
            if not db_path.exists():
                raise FileNotFoundError(f"master.db not found at {db_path}")
            backup_path = str(backup_database(db_path))
        except Exception as e:
            raise HTTPException(500, f"Backup failed — aborting: {e}")

    colored, skipped = color_tracks_by_bpm(req.track_ids, db, dry_run=req.dry_run, skip_colored=req.skip_colored)
    return ColorTracksResponse(
        colored=colored, skipped=skipped, dry_run=req.dry_run, backup_path=backup_path
    )


@router.post("/color-tracks-stream")
def color_tracks_stream_ep(req: ColorTracksRequest, db=Depends(get_db)):
    import json as _json
    from fastapi.responses import StreamingResponse
    from ..db_writer import backup_database, rekordbox_is_running, _bpm_to_color_sort_key

    if rekordbox_is_running() and not req.dry_run:
        raise HTTPException(409, "Rekordbox is running — close it before coloring tracks")

    backup_path = None
    if not req.dry_run:
        try:
            from pathlib import Path
            db_dir = getattr(db, "_db_dir", None)
            if db_dir is None:
                raise RuntimeError("Cannot locate master.db")
            db_path = Path(db_dir) / "master.db"
            backup_path = str(backup_database(db_path))
        except Exception as e:
            raise HTTPException(500, f"Backup failed — aborting: {e}")

    from pyrekordbox.db6 import DjmdColor
    color_by_sort_key: dict = {
        c.SortKey: c.ID for c in db.query(DjmdColor).all() if c.SortKey is not None
    }

    track_ids = req.track_ids
    skip_colored = req.skip_colored
    dry_run = req.dry_run
    total = len(track_ids)

    def event_stream():
        from sqlalchemy import text as _text
        stmt = _text("UPDATE djmdContent SET ColorID = :cid WHERE ID = :tid")
        colored = skipped = 0
        BATCH = 50

        for i, tid in enumerate(track_ids):
            content = db.get_content(ID=tid)
            if content is None:
                skipped += 1
            elif skip_colored and getattr(content, "ColorID", None) not in (None, "", "0"):
                skipped += 1
            else:
                bpm_raw = getattr(content, "BPM", None)
                bpm = float(bpm_raw) / 100 if bpm_raw else 0.0
                sort_key = _bpm_to_color_sort_key(bpm)
                color_id = color_by_sort_key.get(sort_key)
                if not dry_run:
                    db.session.execute(stmt, {"cid": color_id, "tid": tid})
                colored += 1

            if (i + 1) % BATCH == 0:
                if not dry_run:
                    db.session.expire_all()
                    db.session.commit()
                yield f"data: {_json.dumps({'colored': colored, 'skipped': skipped, 'total': total})}\n\n"

        if not dry_run:
            db.session.expire_all()
            db.session.commit()

        yield f"data: {_json.dumps({'done': True, 'colored': colored, 'skipped': skipped, 'total': total, 'backup_path': backup_path, 'dry_run': dry_run})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _to_item(
    t, db,
    key_map: dict | None = None,
    last_played_map: dict | None = None,
    my_tags_map: dict | None = None,
    color_name_map: dict | None = None,
    hot_cue_counts: dict | None = None,
) -> TrackItem:
    bpm_raw = getattr(t, "BPM", None)
    key_id = getattr(t, "KeyID", None)
    key = key_map.get(key_id, "") if key_map and key_id else ""
    # AnalysisDataPath being present means the EXT file was written by Rekordbox —
    # avoids a per-track iterdir() call (3764× faster than db.get_anlz_path).
    has_phrase = bool(getattr(t, "AnalysisDataPath", None))
    color_id = str(getattr(t, "ColorID", None) or "")
    # Use pre-loaded counts map; fall back to live query only when map unavailable.
    if hot_cue_counts is not None:
        existing = hot_cue_counts.get(t.ID, 0)
    else:
        existing = has_existing_hot_cues(t, db)
    return TrackItem(
        id=t.ID,
        title=t.Title or "",
        artist=t.ArtistName or "",
        album=getattr(t, "AlbumName", None) or "",
        bpm=float(bpm_raw or 0) / 100,
        duration=float(t.Length or 0),
        has_phrase=has_phrase,
        has_beats=bool(bpm_raw and float(bpm_raw) > 0),
        existing_hot_cues=existing,
        key=key,
        rating=int(getattr(t, "Rating", 0) or 0),
        play_count=int(str(getattr(t, "DJPlayCount", None) or "0") or "0"),
        last_played=(last_played_map or {}).get(str(t.ID)),
        my_tags=(my_tags_map or {}).get(str(t.ID), []),
        color_name=(color_name_map or {}).get(color_id, "") if color_id else "",
    )


# ---------------------------------------------------------------------------
# Cue Quality Checker
# ---------------------------------------------------------------------------

def _report_to_schema(report) -> TrackHealthReport:
    return TrackHealthReport(
        track_id=report.track_id,
        score=report.score,
        issues=[CueIssueSchema(code=i.code, severity=i.severity, message=i.message)
                for i in report.issues],
        fix_tier=report.fix_tier,
        hot_cue_count=report.hot_cue_count,
        memory_cue_count=report.memory_cue_count,
    )


@router.get("/tracks/{track_id}/health", response_model=TrackHealthReport)
def track_health(track_id: int, db=Depends(get_db)):
    """Return health score and issues for a single track."""
    from pyrekordbox.db6 import DjmdContent
    content = db.query(DjmdContent).filter(DjmdContent.ID == track_id).first()
    if content is None:
        raise HTTPException(404, "Track not found")
    try:
        return _report_to_schema(check_track_health(content, db))
    except Exception as exc:
        from ..analysis.quality import CueIssue, TrackHealthReport as _THR
        return _report_to_schema(
            _THR(track_id=track_id, score=0,
                 issues=[CueIssue("INTERNAL_ERROR", "error", str(exc))],
                 fix_tier="none")
        )


@router.get("/health")
async def library_health(
    request: Request,
    playlist_id: int | None = Query(None),
    db=Depends(get_db),
):
    """Stream library health as SSE. One JSON event per track, then a summary event.

    Optional ?playlist_id=N limits scan to that playlist — use for incremental rescans
    after re-analyzing a subset of tracks in Rekordbox.
    """
    import json
    from collections import defaultdict
    from fastapi.responses import StreamingResponse

    if playlist_id is not None:
        from pyrekordbox.db6 import DjmdPlaylist
        pl = db.query(DjmdPlaylist).filter(DjmdPlaylist.ID == str(playlist_id)).first()
        if pl is None:
            raise HTTPException(404, f"Playlist {playlist_id} not found")

    def event_stream():
        scores: list[int] = []
        missing_audio: list[int] = []
        issue_counts: dict[str, int] = defaultdict(int)
        tier_counts: dict[str, int] = defaultdict(int)

        gen = check_library_health(db, playlist_id=playlist_id)
        # Peek at the total by fetching the underlying content count up-front.
        # check_library_health already did the query; emit total before first track.
        try:
            from pyrekordbox.db6 import DjmdContent, DjmdSongPlaylist
            if playlist_id is not None:
                total_count = (
                    db.query(DjmdContent)
                    .join(DjmdSongPlaylist, DjmdSongPlaylist.ContentID == DjmdContent.ID)
                    .filter(DjmdSongPlaylist.PlaylistID == str(playlist_id))
                    .count()
                )
            else:
                total_count = db.query(DjmdContent).count()
        except Exception:
            total_count = None

        if total_count is not None:
            yield f"data: {json.dumps({'total': total_count})}\n\n"

        for report in gen:
            schema = _report_to_schema(report)
            if any(i.code == "NO_AUDIO_FILE" for i in schema.issues):
                missing_audio.append(report.track_id)
            else:
                scores.append(report.score)
            for issue in report.issues:
                issue_counts[issue.code] += 1
            tier_counts[report.fix_tier] += 1
            yield f"data: {schema.model_dump_json()}\n\n"

        library_score = round(sum(scores) / len(scores), 1) if scores else 0.0
        summary = LibraryHealthSummary(
            total=len(scores) + len(missing_audio),
            excluded_missing_audio=len(missing_audio),
            library_score=library_score,
            no_cues=issue_counts.get("NO_CUES", 0),
            no_phrase=issue_counts.get("NO_PHRASE", 0),
            no_beatgrid=issue_counts.get("NO_BEATGRID", 0),
            duplicate_cues=issue_counts.get("DUPLICATE_CUE", 0),
            unnamed_cues=issue_counts.get("UNNAMED_CUES", 0),
            no_memory_cue=issue_counts.get("NO_MEMORY_CUE", 0),
            fix_tier_counts=dict(tier_counts),
        )
        yield f"data: {json.dumps({'done': True, 'summary': summary.model_dump()})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Cue Library Tools
# ---------------------------------------------------------------------------

@router.post("/cue-tools-stream")
def cue_tools_stream(req: CueToolsRequest, db=Depends(get_db)):
    """Stream bulk cue edits (rename / recolor / shift / delete_orphan) as SSE.

    All operations exclude Kind=0 (memory cues). Per-track events carry
    {processed, affected, total}. Final event carries {done:true, summary:{...}}.
    """
    import json as _json
    from fastapi.responses import StreamingResponse
    from ..db_writer import backup_database, rekordbox_is_running
    from pyrekordbox.db6 import DjmdCue

    if rekordbox_is_running() and not req.dry_run:
        raise HTTPException(409, "Rekordbox is running — close it before editing cues")

    if not req.track_ids:
        # Nothing to process — return empty summary immediately, no backup needed
        import json as _json
        from fastapi.responses import StreamingResponse

        def _empty():
            from ..serve.schemas import CueToolsSummary
            summary = CueToolsSummary(
                operation=req.operation, tracks_processed=0, tracks_affected=0,
                cues_changed=0, cues_skipped=0, dry_run=req.dry_run,
            )
            yield f"data: {_json.dumps({'done': True, 'summary': summary.model_dump()})}\n\n"

        return StreamingResponse(
            _empty(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    backup_path = None
    if not req.dry_run:
        try:
            from pathlib import Path
            db_dir = getattr(db, "_db_dir", None)
            if db_dir is None:
                raise RuntimeError("Cannot locate master.db: no _db_dir on db object")
            db_path = Path(db_dir) / "master.db"
            if not db_path.exists():
                raise FileNotFoundError(f"master.db not found at {db_path}")
            backup_path = Path(backup_database(db_path)).name  # filename only, not full path
        except Exception as e:
            raise HTTPException(500, f"Backup failed — aborting: {e}")

    operation = req.operation
    track_ids = req.track_ids
    dry_run = req.dry_run
    total = len(track_ids)
    BATCH = 50

    def _process_track(content_id: int) -> tuple[int, int]:
        """Return (cues_changed, cues_skipped) for one track."""
        hot_cues = (
            db.session.query(DjmdCue)
            .filter(DjmdCue.ContentID == content_id,
                    DjmdCue.Kind >= 1, DjmdCue.Kind <= 8)
            .all()
        )
        changed = skipped = 0

        if operation == "rename":
            p = req.rename
            for cue in hot_cues:
                if (cue.Comment or "") == p.from_name:
                    if not dry_run:
                        cue.Comment = p.to_name
                    changed += 1
                else:
                    skipped += 1

        elif operation == "recolor":
            p = req.recolor
            for cue in hot_cues:
                slot_str = str(cue.Kind - 1)  # Kind=1 → slot 0 (A)
                if slot_str in p.slot_colors:
                    if not dry_run:
                        cue.ColorTableIndex = p.slot_colors[slot_str]
                    changed += 1
                else:
                    skipped += 1

        elif operation == "shift":
            p = req.shift
            for cue in hot_cues:
                new_ms = int(cue.InMsec or 0) + p.delta_ms
                if new_ms < 0:
                    skipped += 1
                    continue
                if not dry_run:
                    cue.InMsec = new_ms
                    cue.InFrame = round(new_ms * 150 / 1000)
                    # Preserve loop length: shift OutMsec by the same delta
                    out_ms = cue.OutMsec if cue.OutMsec is not None else -1
                    if out_ms >= 0:
                        cue.OutMsec = out_ms + p.delta_ms
                changed += 1

        elif operation == "delete_orphan":
            p = req.delete_orphan
            for cue in hot_cues:
                if cue.Kind > p.keep_slots:
                    if not dry_run:
                        db.session.delete(cue)
                    changed += 1
                else:
                    skipped += 1

        return changed, skipped

    def event_stream():
        processed = affected = total_changed = total_skipped = 0

        for i, tid in enumerate(track_ids):
            content = db.get_content(ID=tid)
            if content is None:
                processed += 1
            else:
                try:
                    changed, skipped_count = _process_track(content.ID)
                    processed += 1
                    if changed > 0:
                        affected += 1
                    total_changed += changed
                    total_skipped += skipped_count
                except Exception as exc:
                    db.session.rollback()
                    logger.error("cue-tools %s failed for track %d: %s", operation, tid, exc)
                    processed += 1

            if (i + 1) % BATCH == 0:
                if not dry_run:
                    db.session.commit()
                yield f"data: {_json.dumps({'processed': processed, 'affected': affected, 'total': total})}\n\n"

        if not dry_run:
            db.session.commit()

        summary = CueToolsSummary(
            operation=operation,
            tracks_processed=processed,
            tracks_affected=affected,
            cues_changed=total_changed,
            cues_skipped=total_skipped,
            dry_run=dry_run,
            backup_path=backup_path,
        )
        yield f"data: {_json.dumps({'done': True, 'summary': summary.model_dump()})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tracks/{track_id}/energy", response_model=EnergyResponse)
def track_energy(track_id: int, db=Depends(get_db)):
    from ..analysis.energy import classify_energy_profile, get_energy_curve
    content = db.get_content(ID=track_id)
    if content is None:
        raise HTTPException(404, f"Track {track_id} not found")
    curve = get_energy_curve(content, db)
    return EnergyResponse(
        track_id=track_id,
        energy=curve,
        n_points=len(curve) if curve else 0,
        energy_profile=classify_energy_profile(curve) if curve else None,
    )


@router.get("/tracks/{track_id}/mixability", response_model=MixabilityResponse)
def track_mixability(track_id: int, db=Depends(get_db)):
    from ..analysis.score import get_mixability
    content = db.get_content(ID=track_id)
    if content is None:
        raise HTTPException(404, f"Track {track_id} not found")
    data = get_mixability(content, db)
    if data is None:
        return MixabilityResponse(track_id=track_id, score=None)
    return MixabilityResponse(
        track_id=track_id,
        score=data["score"],
        intro_bars=data["intro_bars"],
        outro_bars=data["outro_bars"],
        phrase_count=data["phrase_count"],
        vocal_proxy=data["vocal_proxy"],
        energy_variance=data["energy_variance"],
        components=MixabilityComponents(**data["components"]),
    )


@router.get("/tracks/{track_id}/classification", response_model=ClassificationResponse)
def track_classification(track_id: int, db=Depends(get_db)):
    from ..analysis.classify import get_classification
    content = db.get_content(ID=track_id)
    if content is None:
        raise HTTPException(404, f"Track {track_id} not found")
    data = get_classification(content, db)
    return ClassificationResponse(track_id=track_id, **data)


@router.post("/playlists/suggest", response_model=PlaylistSuggestResponse)
def playlist_suggest(req: PlaylistSuggestRequest, db=Depends(get_db)):
    """Return top tracks for a DJ set category, sorted by category score."""
    from ..analysis.classify import CATEGORIES, get_classification

    if req.category not in CATEGORIES:
        raise HTTPException(
            400, f"Unknown category '{req.category}'. Valid: {list(CATEGORIES)}"
        )
    if req.count < 1 or req.count > 500:
        raise HTTPException(400, "count must be between 1 and 500")

    if req.playlist_id is not None:
        from pyrekordbox.db6 import DjmdPlaylist, DjmdSongPlaylist
        pl = db.query(DjmdPlaylist).filter(DjmdPlaylist.ID == str(req.playlist_id)).first()
        if pl is None:
            raise HTTPException(404, f"Playlist {req.playlist_id} not found")
        ids = [
            e.ContentID
            for e in db.query(DjmdSongPlaylist).filter_by(PlaylistID=str(req.playlist_id)).all()
        ]
        contents = [db.get_content(ID=i) for i in ids if i]
        contents = [c for c in contents if c is not None]
    else:
        contents = db.get_content().all()

    exclude = set(req.exclude_ids)
    scored: list[tuple[float, int]] = []
    for content in contents:
        cid = int(content.ID)
        if cid in exclude:
            continue
        try:
            data = get_classification(content, db)
            cat_score = data["scores"].get(req.category, 0.0)
            if cat_score > 0:
                scored.append((cat_score, cid))
        except Exception:
            pass

    import random
    scored.sort(reverse=True)
    # Pick from the top pool with weighted randomness so repeated calls give variety.
    # Pool size: 3× the requested count (min 60, capped at available).
    pool_size = min(len(scored), max(req.count * 3, 60))
    pool = scored[:pool_size]
    if len(pool) <= req.count:
        top = pool
    else:
        weights = [s ** 2 for s, _ in pool]  # square to favour high scorers
        chosen: list[tuple[float, int]] = []
        seen: set[int] = set()
        for _ in range(req.count * 4):  # draw with replacement, dedup until we have enough
            if len(chosen) >= req.count:
                break
            pick = random.choices(pool, weights=weights, k=1)[0]
            if pick[1] not in seen:
                seen.add(pick[1])
                chosen.append(pick)
        # If weighted draw didn't fill the quota, append remaining in score order
        if len(chosen) < req.count:
            for item in pool:
                if len(chosen) >= req.count:
                    break
                if item[1] not in seen:
                    seen.add(item[1])
                    chosen.append(item)
        top = chosen

    return PlaylistSuggestResponse(
        category=req.category,
        results=[PlaylistSuggestItem(track_id=tid, score=round(s, 3)) for s, tid in top],
    )


@router.get("/classify")
async def classify_library(
    playlist_id: int | None = None,
    force_refresh: bool = False,
    db=Depends(get_db),
):
    """SSE stream: one ClassificationResponse JSON event per track, then done summary."""
    import json as _json
    from ..analysis.classify import get_classification, CATEGORIES, _class_cache
    from pyrekordbox.db6 import DjmdPlaylist, DjmdSongPlaylist

    if force_refresh:
        _class_cache.clear()

    if playlist_id is not None:
        pl = db.query(DjmdPlaylist).filter(DjmdPlaylist.ID == str(playlist_id)).first()
        if pl is None:
            raise HTTPException(404, f"Playlist {playlist_id} not found")

    def event_stream():
        if playlist_id is not None:
            ids = [
                e.ContentID
                for e in db.query(DjmdSongPlaylist).filter_by(PlaylistID=str(playlist_id)).all()
            ]
            contents = [db.get_content(ID=i) for i in ids if i]
            contents = [c for c in contents if c is not None]
        else:
            contents = db.get_content().all()

        counts: dict[str, int] = {c: 0 for c in CATEGORIES}
        counts["unknown"] = 0
        total = 0
        for content in contents:
            total += 1
            try:
                data = get_classification(content, db)
                resp = ClassificationResponse(track_id=content.ID, **data)
                counts[data["primary"]] = counts.get(data["primary"], 0) + 1
                yield f"data: {resp.model_dump_json()}\n\n"
            except Exception as exc:
                logger.exception("classify error track %s: %s", content.ID, exc)
        yield f"data: {{\"done\":true,\"total\":{total},\"counts\":{_json.dumps(counts)}}}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tracks/{track_id}/similar", response_model=SimilarTracksResponse)
def track_similar(
    track_id: int,
    n: int = 10,
    bpm_gate: float = 8.0,
    force_rebuild: bool = False,
    db=Depends(get_db),
):
    from ..analysis.similar import clear_index, find_similar

    content = db.get_content(ID=track_id)
    if content is None:
        raise HTTPException(status_code=404, detail="Track not found")
    if force_rebuild:
        clear_index()
    results = find_similar(track_id, db, n=n, bpm_gate=bpm_gate)
    return SimilarTracksResponse(
        track_id=track_id,
        results=[SimilarTrackItem(**r) for r in results],
    )


@router.post("/transitions/score", response_model=TransitionResponse)
def transition_score(req: TransitionRequest, db=Depends(get_db)):
    from ..analysis.transitions import score_transition

    if req.track_a_id == req.track_b_id:
        raise HTTPException(400, detail="track_a_id and track_b_id must be different")
    content_a = db.get_content(ID=req.track_a_id)
    content_b = db.get_content(ID=req.track_b_id)
    if content_a is None:
        raise HTTPException(404, detail=f"Track {req.track_a_id} not found")
    if content_b is None:
        raise HTTPException(404, detail=f"Track {req.track_b_id} not found")

    data = score_transition(content_a, content_b, db)
    return TransitionResponse(
        track_a_id=req.track_a_id,
        track_b_id=req.track_b_id,
        **data,
    )


# ---------------------------------------------------------------------------
# Set Builder
# ---------------------------------------------------------------------------

@router.post("/setbuilder", response_model=SetBuilderResponse)
def build_set_endpoint(req: SetBuilderRequest, db=Depends(get_db)):
    """Build a DJ set via beam search over the track library."""
    from ..analysis.setbuilder import build_set

    tracks = build_set(
        db,
        start_bpm=req.start_bpm,
        end_bpm=req.end_bpm,
        duration_minutes=req.duration_minutes,
        energy_mode=req.energy_mode,
        bpm_step_max=req.bpm_step_max,
        seed_track_id=req.seed_track_id,
    )

    if not tracks:
        raise HTTPException(422, "No valid set could be built with the given constraints")

    # Estimate total duration from track lengths
    total_duration_s = 0.0
    for t in tracks:
        try:
            content = db.get_content(ID=t["track_id"])
            if content is not None:
                total_duration_s += float(getattr(content, "Length", 0) or 0)
        except Exception:
            pass

    return SetBuilderResponse(
        tracks=[SetBuilderTrackItem(**t) for t in tracks],
        total_tracks=len(tracks),
        estimated_duration_minutes=round(total_duration_s / 60.0, 1),
    )
