from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse

from ..db_writer import has_existing_hot_cues
from ..generator import GenerationPrefs, generate_cues_for_track
from .deps import get_db
from .schemas import (
    ApplyRequest,
    ApplyResponse,
    ColorTracksRequest,
    ColorTracksResponse,
    CueItem,
    DeleteRequest,
    DeleteResponse,
    GenerateRequest,
    GenerateResponse,
    PlaylistItem,
    StatusResponse,
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
    playlist: str | None = Query(None),
    sort_by: str = Query("title"),
    sort_order: str = Query("asc"),
    limit: int = Query(5000),
    offset: int = 0,
    db=Depends(get_db),
):
    from pyrekordbox.db6 import DjmdAlbum, DjmdArtist, DjmdContent, DjmdKey, DjmdPlaylist, DjmdSongPlaylist
    from sqlalchemy import asc, desc, func

    q = db.get_content()
    if playlist:
        pl = db.query(DjmdPlaylist).filter_by(Name=playlist).first()
        if not pl:
            raise HTTPException(404, f"Playlist '{playlist}' not found")
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
    else:
        q = q.order_by(order_fn(func.lower(DjmdContent.Title)))

    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    # Pre-load the 24-row key table once rather than querying per track
    key_map = {k.ID: k.ScaleName for k in db.query(DjmdKey).all() if k.ScaleName}
    response.headers["X-Total-Count"] = str(total)
    return [_to_item(t, db, key_map) for t in rows]


@router.get("/tracks/{track_id}/artwork")
def track_artwork(track_id: int, db=Depends(get_db)):
    from pathlib import Path

    content = db.get_content(ID=track_id)
    if content is None:
        raise HTTPException(404, "Track not found")
    image_path = getattr(content, "ImagePath", None)
    if not image_path:
        raise HTTPException(404, "No artwork")
    db_dir = getattr(db, "_db_dir", None)
    if db_dir is None:
        raise HTTPException(500, "Cannot resolve artwork path")
    full = Path(db_dir) / "share" / image_path.lstrip("/")
    if not full.exists():
        raise HTTPException(404, "Artwork file not found")
    return FileResponse(str(full), media_type="image/jpeg")


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest, db=Depends(get_db)):
    prefs = GenerationPrefs(
        mode=req.mode,
        bars_interval=req.bars_interval,
        start_bar=req.start_bar,
        max_cues=req.max_cues,
        add_memory_cue=req.add_memory_cue,
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
                            color_id=c.color_id)
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

    colored, skipped = color_tracks_by_bpm(req.track_ids, db, dry_run=req.dry_run)
    return ColorTracksResponse(
        colored=colored, skipped=skipped, dry_run=req.dry_run, backup_path=backup_path
    )


def _to_item(t, db, key_map: dict | None = None) -> TrackItem:
    bpm_raw = getattr(t, "BPM", None)
    key_id = getattr(t, "KeyID", None)
    key = key_map.get(key_id, "") if key_map and key_id else ""
    return TrackItem(
        id=t.ID,
        title=t.Title or "",
        artist=t.ArtistName or "",
        album=getattr(t, "AlbumName", None) or "",
        bpm=float(bpm_raw or 0) / 100,
        duration=float(t.Length or 0),
        has_phrase=False,   # determined on-demand via /api/generate, not at list time
        has_beats=bool(bpm_raw and float(bpm_raw) > 0),
        existing_hot_cues=has_existing_hot_cues(t, db),
        key=key,
    )
