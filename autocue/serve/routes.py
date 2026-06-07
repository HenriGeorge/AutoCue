from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from ..analysis.quality import check_library_health, check_track_health
from ..db_writer import has_existing_hot_cues
from ..generator import GenerationPrefs, generate_cues_for_track
from .deps import get_db, get_ro_db


def _rb_running(db) -> bool:
    """Check whether Rekordbox is holding the master.db.

    Forwards the live DB path to :func:`autocue.db_writer.rekordbox_is_running`
    so the file-lock check fires alongside the process-name probe — catches
    renamed Rekordbox builds and the race window where Rekordbox opens after
    the process check fired.

    The import is deferred to call time so unit tests that patch
    ``autocue.db_writer.rekordbox_is_running`` still take effect.
    """
    from pathlib import Path as _Path
    from .. import db_writer as _dbw
    db_dir = getattr(db, "_db_dir", None)
    db_path = _Path(db_dir) / "master.db" if db_dir else None
    return _dbw.rekordbox_is_running(db_path=db_path)
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
    AutoTagRequest,
    AutoTagResponse,
    AutoTagUndoRequest,
    AutoTagUndoResponse,
    DiscogsTagRequest,
    DiscogsTagEvent,
    SetBuilderRequest,
    CreatePlaylistRequest,
    CreatePlaylistResponse,
    SetAlternativeItem,
    SetAlternativesResponse,
    SetBuilderResponse,
    EnrichCommentsRequest,
    EnrichCommentsResponse,
    EnrichCommentsUndoRequest,
    EnrichCommentsUndoResponse,
    CommentPreviewRequest,
    CommentPreviewResponse,
    DiscoverItem,
    DownloadConfigResponse,
    DownloadRequest,
    DownloadAlbumRequest,
    DownloadEvent,
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
    CheckAudioRequest,
    CheckAudioResponse,
    YoutubeSearchCandidate,
    YoutubeSearchResponse,
)

# B1: process-local cache for /api/tracks/check-audio. Keyed by
# (content_id, parent_mtime). Cleared on /api/restore alongside the other
# analysis caches.
_audio_check_cache: dict[tuple[str, float], str] = {}

# B5: in-flight YouTube searches keyed by exact query so two concurrent clicks
# with the same query share one yt-dlp invocation. Cleared on completion,
# exception, or timeout.
_inflight_yt_searches: dict[str, "concurrent.futures.Future"] = {}
# B5: bounded concurrent yt-dlp searches (2 at a time). Excess returns 429.
import threading as _threading
_yt_search_semaphore = _threading.BoundedSemaphore(2)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@router.get("/status", response_model=StatusResponse)
def status(db=Depends(get_ro_db)):
    count = db.get_content().count()
    return StatusResponse(connected=True, track_count=count)


@router.get("/playlists", response_model=list[PlaylistItem])
def playlists(db=Depends(get_ro_db)):
    from pyrekordbox.db6 import DjmdPlaylist, DjmdSongPlaylist
    from sqlalchemy import func as _func
    rows = db.query(DjmdPlaylist).filter(DjmdPlaylist.Name.isnot(None)).all()
    counts = dict(
        db.query(DjmdSongPlaylist.PlaylistID, _func.count(DjmdSongPlaylist.ID))
        .group_by(DjmdSongPlaylist.PlaylistID)
        .all()
    )
    return [
        PlaylistItem(id=p.ID, name=p.Name, track_count=counts.get(str(p.ID), 0))
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
    db=Depends(get_ro_db),
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
    row_ids = {str(t.ID) for t in rows}

    # Pre-load the 24-row key table once rather than querying per track
    key_map = {k.ID: k.ScaleName for k in db.query(DjmdKey).all() if k.ScaleName}

    # Pre-load play history: ContentID → latest DateCreated
    # Fetch entire history tables (no IN clause) — faster than 3k-item IN for full-library loads
    last_played_map: dict[str, str] = {}
    try:
        from pyrekordbox.db6 import DjmdHistory, DjmdSongHistory
        hist_date = {str(h.ID): h.DateCreated for h in db.query(DjmdHistory).all() if h.DateCreated}
        for sh in db.query(DjmdSongHistory).all():
            if sh.ContentID not in row_ids:
                continue
            d = hist_date.get(str(sh.HistoryID))
            if d and sh.ContentID:
                key = str(sh.ContentID)
                if key not in last_played_map or d > last_played_map[key]:
                    last_played_map[key] = d
    except Exception:
        pass

    # Pre-load my tags: ContentID → [tag names]
    # Fetch all SongMyTag rows at once (no IN clause) — avoids large IN for full-library loads
    my_tags_map: dict[str, list[str]] = {}
    try:
        from pyrekordbox.db6 import DjmdMyTag, DjmdSongMyTag
        tag_names = {str(t.ID): t.Name for t in db.query(DjmdMyTag).all() if t.Name}
        for st in db.query(DjmdSongMyTag).all():
            if str(st.ContentID) not in row_ids:
                continue
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
def track_artwork(track_id: int, db=Depends(get_ro_db)):
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
def track_audio(track_id: int, db=Depends(get_ro_db)):
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
def list_tags(db=Depends(get_ro_db)):
    try:
        from pyrekordbox.db6 import DjmdMyTag, DjmdSongMyTag
        from sqlalchemy import distinct
        # Only return tags that are actually applied to at least one track
        used_ids = {
            str(r[0])
            for r in db.query(distinct(DjmdSongMyTag.MyTagID)).all()
            if r[0] is not None
        }
        tags = db.query(DjmdMyTag).filter(DjmdMyTag.Name.isnot(None)).all()
        return [{"id": t.ID, "name": t.Name} for t in tags if t.Name and str(t.ID) in used_ids]
    except Exception:
        return []


@router.post("/tracks/check-audio", response_model=CheckAudioResponse)
def check_audio(req: CheckAudioRequest, db=Depends(get_ro_db)):
    """Lazy verification of audio file presence for a batch of tracks.

    See `docs/reference/rest-api.md` for the three-state response shape.
    Caps at 1000 IDs per request (returns 429 above) and groups stat calls
    by parent directory so a folder with 200 tracks is one syscall not 200.
    """
    import os
    from pathlib import Path
    if len(req.track_ids) > 1000:
        raise HTTPException(429, "Too many IDs — split into chunks of 1000")
    from pyrekordbox.db6 import DjmdContent
    rows = (
        db.query(DjmdContent)
        .filter(DjmdContent.ID.in_([str(i) for i in req.track_ids]))
        .all()
    )
    # Group paths by parent_dir for batch scandir.
    by_parent: dict[Path, list[tuple[str, str]]] = {}
    results: dict[str, str] = {}
    for r in rows:
        fp = getattr(r, "FolderPath", None)
        if not fp:
            continue
        try:
            p = Path(str(fp))
            if not p.is_absolute():
                continue
            by_parent.setdefault(p.parent, []).append((str(r.ID), p.name))
        except Exception:
            continue

    unverified: list[str] = []
    for parent, items in by_parent.items():
        # Cache key includes parent's mtime to invalidate when files
        # are added/removed at that directory.
        try:
            mtime = parent.stat().st_mtime
        except OSError:
            for cid, _ in items:
                results[cid] = "unverified"
            unverified.append(str(parent))
            continue
        cached_all = True
        for cid, _ in items:
            if (cid, mtime) not in _audio_check_cache:
                cached_all = False
                break
        if cached_all:
            for cid, _ in items:
                results[cid] = _audio_check_cache[(cid, mtime)]
            continue
        try:
            entries = {e.name for e in os.scandir(parent)}
        except (OSError, PermissionError):
            for cid, _ in items:
                results[cid] = "unverified"
                _audio_check_cache[(cid, mtime)] = "unverified"
            unverified.append(str(parent))
            continue
        for cid, name in items:
            verdict = "file" if name in entries else "missing"
            results[cid] = verdict
            _audio_check_cache[(cid, mtime)] = verdict

    return CheckAudioResponse(results=results, unverified_dirs=unverified)


@router.get("/youtube/search", response_model=YoutubeSearchResponse)
def youtube_search(q: str = Query(..., min_length=1), n: int = Query(5, ge=1, le=10)):
    """Search YouTube for downloadable candidates (read-only).

    Bounded by a 2-process semaphore; excess returns 429. In-flight searches
    dedupe by exact query so two clicks with the same query share one yt-dlp
    process. Each search has a 30-second hard timeout; the slot is freed on
    timeout (504) so a hung YouTube response can't permanently jam the cap.
    """
    import concurrent.futures as _cf
    from .. import download as dl
    if not dl.ytdlp_available():
        raise HTTPException(503, "yt-dlp is not installed")

    query = q.strip()
    if not query:
        raise HTTPException(400, "Empty query")

    # In-flight dedupe.
    existing = _inflight_yt_searches.get(query)
    if existing is not None:
        try:
            results = existing.result(timeout=30)
            return YoutubeSearchResponse(candidates=[YoutubeSearchCandidate(**r) for r in results])
        except _cf.TimeoutError:
            raise HTTPException(504, "YouTube search timed out (network slow or YouTube unreachable). Try again.")
        except Exception as exc:
            raise HTTPException(500, f"YouTube search failed: {exc}") from exc

    # Acquire semaphore non-blocking.
    if not _yt_search_semaphore.acquire(blocking=False):
        raise HTTPException(429, "YouTube search busy, retry in a moment")

    executor = _cf.ThreadPoolExecutor(max_workers=1)
    fut = executor.submit(dl.search_youtube, query, n)
    _inflight_yt_searches[query] = fut
    try:
        results = fut.result(timeout=30)
    except _cf.TimeoutError:
        # Future keeps running in background; we release the slot for the next caller.
        raise HTTPException(504, "YouTube search timed out (network slow or YouTube unreachable). Try again.")
    except Exception as exc:
        raise HTTPException(500, f"YouTube search failed: {exc}") from exc
    finally:
        _inflight_yt_searches.pop(query, None)
        _yt_search_semaphore.release()
        executor.shutdown(wait=False)

    candidates = []
    for r in results or []:
        candidates.append(YoutubeSearchCandidate(
            url=r.get("url", ""),
            title=r.get("title", ""),
            channel=r.get("uploader", ""),
            duration=r.get("duration"),
            thumbnail=None,
        ))
    return YoutubeSearchResponse(candidates=candidates)


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest, db=Depends(get_ro_db)):
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

    if _rb_running(db):
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

    if _rb_running(db):
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

    if _rb_running(db):
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

    if _rb_running(db):
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
    _audio_check_cache.clear()  # B1: drop stale audio-existence verdicts

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

    if _rb_running(db):
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

    if _rb_running(db) and not req.dry_run:
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

    if _rb_running(db) and not req.dry_run:
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

        try:
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
                    yield f"data: {_json.dumps({'colored': colored, 'skipped': skipped, 'total': total})}\n\n"

            if not dry_run:
                db.session.expire_all()
                db.session.commit()  # single commit for entire batch

        except BaseException:  # includes GeneratorExit (client disconnect)
            db.session.rollback()
            raise

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
        play_count=int(getattr(t, "DJPlayCount", 0) or 0),
        last_played=(last_played_map or {}).get(str(t.ID)),
        my_tags=(my_tags_map or {}).get(str(t.ID), []),
        color_name=(color_name_map or {}).get(color_id, "") if color_id else "",
        genre=getattr(t, "GenreName", None) or "",
        comment=getattr(t, "Commnt", None) or "",
        source=_classify_source(getattr(t, "FolderPath", None)),
    )


def _classify_source(folder_path: str | None) -> str:
    """Cheap string-only classification of DjmdContent.FolderPath.

    Streaming-source tracks (Spotify, Tidal, Apple Music) have empty paths or
    use URI schemes that don't point at the filesystem. Tracks with a real
    file path are reported as "file" without confirming the file exists —
    actual existence is verified lazily via POST /api/tracks/check-audio.
    """
    if not folder_path:
        return "streaming"
    s = str(folder_path).strip()
    if not s:
        return "streaming"
    low = s.lower()
    if low.startswith(("spotify:", "tidal:", "applemusic:", "http://", "https://")):
        return "streaming"
    try:
        # An absolute filesystem path is the expected "file" shape.
        from pathlib import Path
        if Path(s).is_absolute():
            return "file"
    except Exception:
        return "unknown"
    return "unknown"


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
def track_health(track_id: int, db=Depends(get_ro_db)):
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
    db=Depends(get_ro_db),
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

    if _rb_running(db) and not req.dry_run:
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

    def _process_track(content_id: int) -> tuple[int, int, dict]:
        """Return (cues_changed, cues_skipped, skip_reasons) for one track."""
        hot_cues = (
            db.session.query(DjmdCue)
            .filter(DjmdCue.ContentID == content_id,
                    DjmdCue.Kind >= 1, DjmdCue.Kind <= 8)
            .all()
        )
        changed = skipped = 0
        reasons: dict[str, int] = {}

        if operation == "rename":
            p = req.rename
            for cue in hot_cues:
                if (cue.Comment or "") == p.from_name:
                    if not dry_run:
                        cue.Comment = p.to_name
                    changed += 1
                else:
                    skipped += 1
                    reasons["no_match"] = reasons.get("no_match", 0) + 1

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
                    reasons["no_match"] = reasons.get("no_match", 0) + 1

        elif operation == "shift":
            p = req.shift
            policy = p.negative_policy
            if policy == "abort_track":
                # Check first: if any cue would go negative, skip the whole track
                if any(int(cue.InMsec or 0) + p.delta_ms < 0 for cue in hot_cues):
                    reasons["track_aborted"] = reasons.get("track_aborted", 0) + len(hot_cues)
                    return 0, len(hot_cues), reasons
            for cue in hot_cues:
                original_in_ms = int(cue.InMsec or 0)
                new_ms = original_in_ms + p.delta_ms
                if new_ms < 0:
                    if policy == "clamp_to_zero":
                        new_ms = 0
                    else:  # "skip"
                        skipped += 1
                        reasons["would_be_negative"] = reasons.get("would_be_negative", 0) + 1
                        continue
                if not dry_run:
                    cue.InMsec = new_ms
                    cue.InFrame = round(new_ms * 150 / 1000)
                    out_ms = cue.OutMsec if cue.OutMsec is not None else -1
                    if out_ms >= 0:
                        # Use effective shift to keep loop length intact
                        effective_shift = new_ms - original_in_ms
                        new_out_ms = max(0, out_ms + effective_shift)
                        cue.OutMsec = new_out_ms
                        cue.OutFrame = round(new_out_ms * 150 / 1000)
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
                    reasons["beyond_keep_slots"] = reasons.get("beyond_keep_slots", 0) + 1

        return changed, skipped, reasons

    def event_stream():
        processed = affected = total_changed = total_skipped = 0
        total_reasons: dict[str, int] = {}

        try:
            for i, tid in enumerate(track_ids):
                content = db.get_content(ID=tid)
                if content is None:
                    processed += 1
                else:
                    try:
                        changed, skipped_count, reasons = _process_track(content.ID)
                        processed += 1
                        if changed > 0:
                            affected += 1
                        total_changed += changed
                        total_skipped += skipped_count
                        for k, v in reasons.items():
                            total_reasons[k] = total_reasons.get(k, 0) + v
                    except Exception as exc:
                        logger.error("cue-tools %s failed for track %d: %s", operation, tid, exc)
                        processed += 1

                if (i + 1) % BATCH == 0:
                    yield f"data: {_json.dumps({'processed': processed, 'affected': affected, 'total': total})}\n\n"

            if not dry_run:
                db.session.commit()  # single commit for entire batch

        except BaseException:  # includes GeneratorExit (client disconnect) — must not leave dirty session
            db.session.rollback()
            raise

        summary = CueToolsSummary(
            operation=operation,
            tracks_processed=processed,
            tracks_affected=affected,
            cues_changed=total_changed,
            cues_skipped=total_skipped,
            skip_reasons=total_reasons,
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
def track_energy(track_id: int, db=Depends(get_ro_db)):
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
def track_mixability(track_id: int, db=Depends(get_ro_db)):
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
def track_classification(track_id: int, db=Depends(get_ro_db)):
    from ..analysis.classify import get_classification
    content = db.get_content(ID=track_id)
    if content is None:
        raise HTTPException(404, f"Track {track_id} not found")
    data = get_classification(content, db)
    return ClassificationResponse(track_id=track_id, **data)


@router.post("/playlists/suggest", response_model=PlaylistSuggestResponse)
def playlist_suggest(req: PlaylistSuggestRequest, db=Depends(get_ro_db)):
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
        from pyrekordbox.db6 import DjmdContent as _DjmdContent
        ids = [
            e.ContentID
            for e in db.query(DjmdSongPlaylist).filter_by(PlaylistID=str(req.playlist_id)).all()
        ]
        valid_ids = [i for i in ids if i]
        contents = (
            db.session.query(_DjmdContent).filter(_DjmdContent.ID.in_(valid_ids)).all()
            if valid_ids else []
        )
    else:
        contents = db.get_content().all()

    exclude = set(req.exclude_ids)
    seed_set = set(req.seed_track_ids)
    scored: list[tuple[float, int]] = []
    seed_scored: dict[int, float] = {}
    for content in contents:
        cid = int(content.ID)
        # Seeds bypass exclude_ids; non-seeds skip if excluded
        if cid in exclude and cid not in seed_set:
            continue
        try:
            data = get_classification(content, db)
            cat_score = data["scores"].get(req.category, 0.0)
            if cid in seed_set:
                seed_scored[cid] = cat_score
            elif cat_score > 0:
                scored.append((cat_score, cid))
        except Exception:
            if cid in seed_set:
                seed_scored[cid] = 0.0

    # Seed results maintain original selection order
    seed_items: list[tuple[float, int]] = [
        (seed_scored.get(sid, 0.0), sid) for sid in req.seed_track_ids
    ]
    fill_count = max(0, req.count - len(seed_items))

    import random
    scored.sort(reverse=True)
    pool_size = min(len(scored), max(fill_count * 3, 60))
    pool = scored[:pool_size]
    fill: list[tuple[float, int]] = []
    if pool and fill_count > 0:
        if len(pool) <= fill_count:
            fill = pool
        else:
            weights = [s ** 2 for s, _ in pool]
            seen: set[int] = set()
            for _ in range(fill_count * 4):
                if len(fill) >= fill_count:
                    break
                pick = random.choices(pool, weights=weights, k=1)[0]
                if pick[1] not in seen:
                    seen.add(pick[1])
                    fill.append(pick)
            if len(fill) < fill_count:
                for item in pool:
                    if len(fill) >= fill_count:
                        break
                    if item[1] not in seen:
                        seen.add(item[1])
                        fill.append(item)

    top = seed_items + fill

    return PlaylistSuggestResponse(
        category=req.category,
        results=[PlaylistSuggestItem(track_id=tid, score=round(s, 3)) for s, tid in top],
    )


@router.get("/classify")
async def classify_library(
    playlist_id: int | None = None,
    force_refresh: bool = False,
    db=Depends(get_ro_db),
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
            from pyrekordbox.db6 import DjmdContent as _DjmdContent
            ids = [
                e.ContentID
                for e in db.query(DjmdSongPlaylist).filter_by(PlaylistID=str(playlist_id)).all()
            ]
            valid_ids = [i for i in ids if i]
            contents = (
                db.session.query(_DjmdContent).filter(_DjmdContent.ID.in_(valid_ids)).all()
                if valid_ids else []
            )
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
    n: int = Query(10, ge=1, le=100),
    bpm_gate: float = Query(8.0, ge=0.0, le=50.0),
    force_rebuild: bool = False,
    db=Depends(get_ro_db),
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
def transition_score(req: TransitionRequest, db=Depends(get_ro_db)):
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
def build_set_endpoint(req: SetBuilderRequest, db=Depends(get_ro_db)):
    """Build a DJ set via beam search over the track library."""
    from ..analysis.setbuilder import build_set

    result = build_set(
        db,
        start_bpm=req.start_bpm,
        end_bpm=req.end_bpm,
        duration_minutes=req.duration_minutes,
        energy_mode=req.energy_mode,
        bpm_step_max=req.bpm_step_max,
        seed_track_id=req.seed_track_id,
        anchor_track_ids=req.anchor_track_ids or None,
    )

    tracks = result["tracks"]
    terminated_reason = result["terminated_reason"]

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
        terminated_reason=terminated_reason,
    )


@router.get("/setbuilder/alternatives", response_model=SetAlternativesResponse)
def setbuilder_alternatives(
    track_id: int,
    prev_id: int | None = Query(None),
    next_id: int | None = Query(None),
    exclude_ids: str = Query(""),
    n: int = Query(8, ge=1, le=20),
    db=Depends(get_ro_db),
):
    """Return best replacement candidates for a track given its neighbours in the set."""
    from ..analysis.similar import find_similar, _build_index
    from ..analysis import similar as _sim_mod
    from ..analysis.transitions import score_transition

    if not _sim_mod._INDEX_BUILT:
        _build_index(db)

    exclude: set[int] = {track_id}
    for x in exclude_ids.split(","):
        x = x.strip()
        if x:
            try:
                exclude.add(int(x))
            except ValueError:
                pass

    # Gather candidates from similarity to prev, next, and current track
    candidate_ids: set[int] = set()
    for ref_id in filter(None, [prev_id, next_id, track_id]):
        try:
            for item in find_similar(ref_id, db, n=25):
                candidate_ids.add(item["track_id"])
        except Exception:
            pass
    candidate_ids -= exclude

    prev_content = db.get_content(ID=prev_id) if prev_id else None
    next_content = db.get_content(ID=next_id) if next_id else None
    ref_content  = db.get_content(ID=track_id)

    def _genre(content) -> str:
        if content is None:
            return ""
        try:
            return str(getattr(content, "GenreName", "") or "")
        except Exception:
            return ""

    ref_genre = _genre(ref_content)
    # Preferred genre = genre of the track being replaced; fall back to neighbours
    neighbour_genres = {g for g in [_genre(prev_content), _genre(next_content)] if g}

    results: list[SetAlternativeItem] = []
    for cid in list(candidate_ids)[:60]:
        try:
            cand = db.get_content(ID=cid)
            if cand is None:
                continue
            from_prev = score_transition(prev_content, cand, db)["overall"] if prev_content else None
            to_next   = score_transition(cand, next_content, db)["overall"] if next_content else None
            scores = [s for s in [from_prev, to_next] if s is not None]
            combined = sum(scores) / len(scores) if scores else 50.0

            cand_genre = _genre(cand)
            # Genre match: compare against the track being replaced first, then neighbours
            if not ref_genre and not neighbour_genres:
                genre_match = None
            elif cand_genre and (cand_genre == ref_genre or cand_genre in neighbour_genres):
                genre_match = True
            elif cand_genre:
                genre_match = False
                combined = max(0.0, combined - 20.0)  # 20-point penalty for genre mismatch
            else:
                genre_match = None  # candidate has no genre — no penalty, no bonus

            raw_bpm = getattr(cand, "BPM", 0) or 0
            bpm = float(raw_bpm) / 100.0
            key = ""
            try:
                k = getattr(cand, "Key", None)
                if k:
                    key = str(getattr(k, "ScaleName", "") or "")
            except Exception:
                pass
            results.append(SetAlternativeItem(
                track_id=cid,
                title=str(getattr(cand, "Title", "") or ""),
                artist=str(getattr(cand, "ArtistName", "") or ""),
                bpm=round(bpm, 2),
                key=key,
                score=round(combined, 1),
                from_prev=round(from_prev, 1) if from_prev is not None else None,
                to_next=round(to_next, 1) if to_next is not None else None,
                genre=cand_genre,
                genre_match=genre_match,
            ))
        except Exception:
            pass

    results.sort(key=lambda x: -x.score)
    return SetAlternativesResponse(alternatives=results[:n])


@router.post("/playlists", response_model=CreatePlaylistResponse)
def create_playlist(req: CreatePlaylistRequest, db=Depends(get_db)):
    """Create a new Rekordbox playlist from a list of track IDs."""
    import os
    from datetime import datetime
    from uuid import uuid4
    from sqlalchemy import func as _func
    from ..db_writer import rekordbox_is_running

    if not req.name.strip():
        raise HTTPException(400, "Playlist name is required")
    if not req.track_ids:
        raise HTTPException(400, "No tracks provided")
    if _rb_running(db):
        raise HTTPException(409, "Rekordbox is running — close it before saving playlists")

    try:
        from pyrekordbox.db6.tables import DjmdPlaylist, DjmdSongPlaylist

        max_seq = db.session.query(_func.max(DjmdPlaylist.Seq)).scalar() or 0
        now = datetime.utcnow()

        pl_id = db.generate_unused_id(DjmdPlaylist)
        playlist = DjmdPlaylist(
            ID=str(pl_id),
            Seq=int(max_seq) + 1,
            Name=req.name.strip(),
            Attribute=0,
            ParentID="root",
            UUID=str(uuid4()),
            created_at=now,
            updated_at=now,
        )
        db.session.add(playlist)

        for track_no, tid in enumerate(req.track_ids, start=1):
            sp_id = db.generate_unused_id(DjmdSongPlaylist)
            db.session.add(DjmdSongPlaylist(
                ID=str(sp_id),
                PlaylistID=str(pl_id),
                ContentID=str(tid),
                TrackNo=track_no,
                UUID=str(uuid4()),
                created_at=now,
                updated_at=now,
            ))

        db.session.commit()
        return CreatePlaylistResponse(
            playlist_id=pl_id,
            name=req.name.strip(),
            track_count=len(req.track_ids),
        )
    except Exception as exc:
        db.session.rollback()
        raise HTTPException(500, f"Failed to create playlist: {exc}")


# ── Auto-Tag ────────────────────────────────────────────────────────────────

@router.post("/auto-tag", response_model=AutoTagResponse)
def auto_tag(req: AutoTagRequest, db=Depends(get_db)):
    from ..analysis.auto_tag import apply_tags
    from ..db_writer import rekordbox_is_running

    if _rb_running(db) and not req.dry_run:
        raise HTTPException(409, "Rekordbox is running — close it before applying tags")
    try:
        result = apply_tags(
            db,
            track_ids=req.track_ids,
            tag_types=req.tag_types,
            overwrite=req.overwrite,
            dry_run=req.dry_run,
        )
        if not req.dry_run:
            db.session.commit()
        return AutoTagResponse(**result)
    except Exception as exc:
        db.session.rollback()
        raise HTTPException(500, str(exc)) from exc


@router.get("/config")
def get_config():
    """Return non-sensitive client config, including Discogs token from env (local server only)."""
    import os as _os
    # Load .env from the project root if present (one-time, lightweight)
    env_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), ".env")
    token = ""
    try:
        if _os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DISCOGS_TOKEN="):
                        token = line.split("=", 1)[1].strip()
                        break
    except Exception:
        pass
    # Also check actual environment (takes precedence)
    token = _os.environ.get("DISCOGS_TOKEN", token)
    return {"discogs_token": token}


@router.post("/auto-tag/discogs/test")
def auto_tag_discogs_test(req: dict):
    """Verify a Discogs personal access token by calling the identity endpoint."""
    import json as _json
    import urllib.request as _urlreq
    token = (req.get("token") or "").strip()
    if not token:
        raise HTTPException(400, "token is required")
    try:
        request = _urlreq.Request(
            "https://api.discogs.com/oauth/identity",
            headers={"Authorization": f"Discogs token={token}", "User-Agent": "AutoCue/1.0"},
        )
        with _urlreq.urlopen(request, timeout=8) as resp:
            data = _json.loads(resp.read().decode())
        return {"ok": True, "username": data.get("username", "")}
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/auto-tag/discogs")
def auto_tag_discogs(req: DiscogsTagRequest, db=Depends(get_db)):
    """Stream Discogs style tags to Rekordbox My Tags via SSE.

    Each event: data: {"processed":N,"total":M,"track_id":ID,"styles":[...]}
    Final event: data: {"done":true,"tagged":T,"skipped":S,"errors":E}
    """
    import json as _json
    from fastapi.responses import StreamingResponse
    from ..analysis.discogs import search_styles
    from ..analysis.auto_tag import ensure_tag_by_name
    from ..db_writer import rekordbox_is_running

    if _rb_running(db) and not req.dry_run:
        raise HTTPException(409, "Rekordbox is running — close it before applying tags")

    def event_stream():
        from pyrekordbox.db6 import DjmdContent, DjmdMyTag, DjmdSongMyTag
        from ..analysis.auto_tag import ALL_AUTOCUE_TAG_NAMES
        total   = len(req.track_ids)
        tagged  = 0
        skipped = 0
        errors  = 0

        # Pre-build a map of tag_id → tag_name for the skip_existing check (one query)
        if req.skip_existing:
            tag_name_map = {str(t.ID): t.Name for t in db.session.query(DjmdMyTag).all() if t.Name}
        else:
            tag_name_map = {}

        for i, tid in enumerate(req.track_ids):
            try:
                content = db.get_content(ID=tid)
                if content is None:
                    skipped += 1
                    yield f"data: {_json.dumps({'processed': i+1, 'total': total, 'track_id': tid, 'styles': [], 'skipped': skipped})}\n\n"
                    continue

                # Skip tracks that already have Discogs-style tags (non-AutoCue My Tags)
                if req.skip_existing:
                    existing_song_tags = db.session.query(DjmdSongMyTag).filter(
                        DjmdSongMyTag.ContentID == str(tid)
                    ).all()
                    has_discogs_tag = any(
                        tag_name_map.get(str(st.MyTagID), "") not in ALL_AUTOCUE_TAG_NAMES
                        and tag_name_map.get(str(st.MyTagID), "") != ""
                        for st in existing_song_tags
                    )
                    if has_discogs_tag:
                        skipped += 1
                        yield f"data: {_json.dumps({'processed': i+1, 'total': total, 'track_id': tid, 'styles': [], 'skipped': skipped})}\n\n"
                        continue

                artist = str(getattr(content, "ArtistName", "") or "")
                title  = str(getattr(content, "Title", "") or "")
                styles = search_styles(artist, title, req.token)

                if not styles:
                    skipped += 1
                    yield f"data: {_json.dumps({'processed': i+1, 'total': total, 'track_id': tid, 'artist': artist, 'title': title, 'styles': [], 'skipped': skipped})}\n\n"
                    continue

                if not req.dry_run:
                    for style in styles:
                        tag_id = ensure_tag_by_name(db, style)
                        from pyrekordbox.db6 import DjmdSongMyTag
                        existing = db.session.query(DjmdSongMyTag).filter(
                            DjmdSongMyTag.ContentID == str(tid),
                            DjmdSongMyTag.MyTagID   == str(tag_id),
                        ).first()
                        if not existing:
                            new_id = db.generate_unused_id(DjmdSongMyTag)
                            row = DjmdSongMyTag(
                                ID=str(new_id),
                                ContentID=str(tid),
                                MyTagID=str(tag_id),
                            )
                            db.session.add(row)
                    db.session.commit()

                tagged += 1
                yield f"data: {_json.dumps({'processed': i+1, 'total': total, 'track_id': tid, 'artist': artist, 'title': title, 'styles': styles, 'tagged': tagged})}\n\n"

            except Exception as exc:
                errors += 1
                logger.warning("Discogs tag error for track %d: %s", tid, exc)
                try:
                    db.session.rollback()
                except Exception:
                    pass
                yield f"data: {_json.dumps({'processed': i+1, 'total': total, 'track_id': tid, 'error': str(exc), 'errors': errors})}\n\n"

        yield f"data: {_json.dumps({'done': True, 'tagged': tagged, 'skipped': skipped, 'errors': errors})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/auto-tag/undo", response_model=AutoTagUndoResponse)
def auto_tag_undo(req: AutoTagUndoRequest, db=Depends(get_db)):
    from ..analysis.auto_tag import undo_tag_run
    from ..db_writer import rekordbox_is_running

    if _rb_running(db):
        raise HTTPException(409, "Rekordbox is running — close it before undoing tags")
    try:
        result = undo_tag_run(db, req.undo_data.model_dump())
        db.session.commit()
        return AutoTagUndoResponse(**result)
    except Exception as exc:
        db.session.rollback()
        raise HTTPException(500, str(exc)) from exc


# ---------------------------------------------------------------------------
# Comment enrichment
# ---------------------------------------------------------------------------

@router.post("/enrich-comments", response_model=EnrichCommentsResponse)
def enrich_comments(req: EnrichCommentsRequest, db=Depends(get_db)):
    from ..analysis.comment import enrich_comments_batch
    from ..db_writer import rekordbox_is_running

    if _rb_running(db) and not req.dry_run:
        raise HTTPException(409, "Rekordbox is running — close it before enriching comments")
    try:
        result = enrich_comments_batch(
            req.track_ids, db,
            overwrite=req.overwrite,
            dry_run=req.dry_run,
        )
        return EnrichCommentsResponse(dry_run=req.dry_run, **result)
    except Exception as exc:
        db.session.rollback()
        raise HTTPException(500, str(exc)) from exc


@router.post("/enrich-comments/stream")
async def enrich_comments_stream(req: EnrichCommentsRequest, db=Depends(get_db)):
    """SSE version of enrich-comments — streams per-track progress events."""
    from fastapi.responses import StreamingResponse
    from ..analysis.comment import enrich_comment
    from ..db_writer import rekordbox_is_running, backup_database

    if _rb_running(db) and not req.dry_run:
        raise HTTPException(409, "Rekordbox is running — close it before enriching comments")

    import json as _json

    def event_stream():
        enriched = 0
        skipped = 0
        errors = 0
        backup_path = None
        track_ids = req.track_ids or []
        total = len(track_ids)
        undo_rows: list[dict] = []

        if not req.dry_run and track_ids:
            from pathlib import Path as _Path
            try:
                db_dir = getattr(db, "_db_dir", None)
                if db_dir:
                    backup_path = str(backup_database(_Path(db_dir) / "master.db"))
            except Exception:
                pass

        for i, tid in enumerate(track_ids):
            try:
                content = db.get_content(ID=tid)
                if content is None:
                    skipped += 1
                else:
                    previous = str(getattr(content, "Commnt", "") or "")
                    result = enrich_comment(content, db, overwrite=req.overwrite, dry_run=req.dry_run)
                    if result is None:
                        skipped += 1
                    else:
                        enriched += 1
                        if not req.dry_run:
                            try:
                                db.session.commit()
                                undo_rows.append({"content_id": str(tid), "previous": previous})
                            except Exception as commit_exc:
                                db.session.rollback()
                                errors += 1
                                enriched -= 1
                                logger.warning("Enrichment commit failed for track %s: %s", tid, commit_exc)
            except Exception:
                errors += 1
            yield f"data: {_json.dumps({'processed': i + 1, 'total': total, 'enriched': enriched})}\n\n"

        yield f"data: {_json.dumps({'done': True, 'enriched': enriched, 'skipped': skipped, 'errors': errors, 'backup_path': backup_path, 'dry_run': req.dry_run, 'undo_data': {'modified': undo_rows}})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/enrich-comments/undo", response_model=EnrichCommentsUndoResponse)
def enrich_comments_undo(req: EnrichCommentsUndoRequest, db=Depends(get_db)):
    """Reverse a prior enrich_comments run using its returned undo_data."""
    from ..analysis.comment import restore_comments
    from ..db_writer import rekordbox_is_running

    if _rb_running(db):
        raise HTTPException(409, "Rekordbox is running — close it before undoing enrichment")
    try:
        payload = req.undo_data.model_dump()
        result = restore_comments(db, payload)
        return EnrichCommentsUndoResponse(**result)
    except Exception as exc:
        db.session.rollback()
        raise HTTPException(500, str(exc)) from exc


@router.post("/enrich-comments/preview", response_model=CommentPreviewResponse)
def enrich_comments_preview(req: CommentPreviewRequest, db=Depends(get_ro_db)):
    from ..analysis.comment import build_comment_string, enrich_comment
    content = db.get_content(ID=req.track_id)
    if content is None:
        raise HTTPException(404, f"Track {req.track_id} not found")
    current = str(getattr(content, "Commnt", "") or "")
    enrichment = build_comment_string(content, db)
    # Compute preview without writing
    preview = enrich_comment(content, db, overwrite=False, dry_run=True) or current
    return CommentPreviewResponse(
        track_id=req.track_id,
        current_comment=current,
        preview=preview or enrichment,
    )


# ---------------------------------------------------------------------------
# Discovery — new releases from library artists (Discogs)
# ---------------------------------------------------------------------------

@router.get("/discover")
def discover_new_releases(
    since_year: int | None = Query(None),
    max_artists: int = Query(25, ge=1, le=100),
    per_artist: int = Query(5, ge=1, le=20),
    token: str = Query(""),
    db=Depends(get_ro_db),
):
    """Stream suggested new releases for the library's top artists via SSE.

    Each event is a DiscoverItem; the final event has ``done=true``. The Discogs
    token comes from the query param, else the DISCOGS_TOKEN env / project .env.
    """
    import json as _json
    from fastapi.responses import StreamingResponse
    from ..analysis.discovery import iter_new_releases

    tok = (token or "").strip() or _resolve_discogs_token()
    if not tok:
        raise HTTPException(400, "Discogs token required (set it in the Discogs panel or DISCOGS_TOKEN).")

    def event_stream():
        suggested = 0
        last_total = 0
        try:
            for processed, total, item in iter_new_releases(
                db, tok, since_year=since_year,
                max_artists=max_artists, per_artist=per_artist,
            ):
                last_total = total
                if item is None:
                    # progress-only tick (artist with no new releases)
                    yield f"data: {_json.dumps({'processed': processed, 'total': total, 'suggested': suggested})}\n\n"
                    continue
                suggested += 1
                payload = {"processed": processed, "total": total, "suggested": suggested, **{
                    k: item.get(k) for k in
                    ("artist", "album", "title", "year", "thumb", "cover", "genres", "styles", "formats", "url", "query")
                }}
                yield f"data: {_json.dumps(payload)}\n\n"
        except Exception as exc:
            logger.warning("discover error: %s", exc)
            yield f"data: {_json.dumps({'error': str(exc)})}\n\n"
        yield f"data: {_json.dumps({'done': True, 'total': last_total, 'suggested': suggested})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _resolve_discogs_token() -> str:
    """Read DISCOGS_TOKEN from the project .env then the environment."""
    import os as _os
    env_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), ".env")
    token = ""
    try:
        if _os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DISCOGS_TOKEN="):
                        token = line.split("=", 1)[1].strip()
                        break
    except Exception:
        pass
    return _os.environ.get("DISCOGS_TOKEN", token)


# ---------------------------------------------------------------------------
# Download — YouTube audio via yt-dlp (optional dependency)
# ---------------------------------------------------------------------------

def _detect_music_folder(db) -> str | None:
    """Find the common ancestor directory of tracked audio files.

    Samples up to 30 DjmdContent.FolderPath values (absolute paths) and returns
    os.path.commonpath() over their parent directories. This is a good proxy for
    the DJ's music root (e.g. ~/Music/Rekordbox). Returns None on any failure.
    """
    import os
    from pathlib import Path
    paths: list[str] = []
    try:
        from pyrekordbox.db6 import DjmdContent
        for row in db.query(DjmdContent).limit(30).all():
            raw = getattr(row, "FolderPath", None) or ""
            if raw and os.path.isabs(raw):
                paths.append(str(Path(raw).parent))
    except Exception:
        return None
    if not paths:
        return None
    try:
        return os.path.commonpath(paths)
    except (ValueError, TypeError):
        return None


@router.get("/download/config", response_model=DownloadConfigResponse)
def download_config(db=Depends(get_ro_db)):
    """Report whether yt-dlp + ffmpeg are available, the default download dir, and the detected music folder."""
    from .. import download as dl
    return DownloadConfigResponse(
        available=dl.ytdlp_available(),
        ffmpeg=dl.ffmpeg_available(),
        default_dir=dl.default_download_dir(),
        music_folder=_detect_music_folder(db),
    )


def _percent_from_hook(d: dict) -> float | None:
    """Derive a 0–100 percent from a yt-dlp progress dict, if possible."""
    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    got = d.get("downloaded_bytes")
    if total and got is not None:
        try:
            return round(min(100.0, got / total * 100.0), 1)
        except (TypeError, ZeroDivisionError):
            return None
    return None


@router.post("/download")
def download_single(req: DownloadRequest):
    """Download one track's audio from YouTube via SSE progress events."""
    import json as _json
    import queue
    import threading
    from fastapi.responses import StreamingResponse
    from .. import download as dl

    if not dl.ytdlp_available():
        raise HTTPException(503, "yt-dlp is not installed. Install with: pip install -e \".[download]\"")
    if not dl.ffmpeg_available():
        raise HTTPException(503, "ffmpeg not found on PATH — required to extract audio.")

    def event_stream():
        events: "queue.Queue[dict]" = queue.Queue()
        cancel_event = threading.Event()

        def progress(d: dict) -> None:
            events.put({
                "status": d.get("status"),
                "percent": _percent_from_hook(d),
            })

        result: dict = {}

        def worker() -> None:
            try:
                path = dl.download_audio(
                    req.query, dest_dir=req.dest_dir,
                    audio_format=req.audio_format, progress_cb=progress,
                    cancel_event=cancel_event,
                )
                result["path"] = path
            except dl.DownloadCancelled:
                result["cancelled"] = True
            except Exception as exc:  # noqa: BLE001
                result["error"] = str(exc)
            finally:
                events.put({"_end": True})

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        try:
            while True:
                ev = events.get()
                if ev.get("_end"):
                    break
                yield f"data: {_json.dumps({'processed': 0, 'total': 1, 'query': req.query, **ev})}\n\n"
        except BaseException:  # includes GeneratorExit (client disconnect)
            # Signal the worker; it'll abort at the next yt-dlp progress tick.
            # No yield can run here — Starlette has closed the stream.
            cancel_event.set()
            raise

        if result.get("cancelled"):
            yield f"data: {_json.dumps({'done': True, 'status': 'cancelled', 'failed': 1})}\n\n"
        elif "error" in result:
            yield f"data: {_json.dumps({'done': True, 'status': 'error', 'error': result['error'], 'failed': 1})}\n\n"
        else:
            yield f"data: {_json.dumps({'done': True, 'status': 'finished', 'path': result.get('path'), 'downloaded': 1})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/download/album")
def download_album(req: DownloadAlbumRequest):
    """Download multiple tracks (an album) sequentially via SSE progress events."""
    import json as _json
    from fastapi.responses import StreamingResponse
    from .. import download as dl

    if not dl.ytdlp_available():
        raise HTTPException(503, "yt-dlp is not installed. Install with: pip install -e \".[download]\"")
    if not dl.ffmpeg_available():
        raise HTTPException(503, "ffmpeg not found on PATH — required to extract audio.")

    def event_stream():
        import threading as _threading
        total = len(req.tracks)
        downloaded = 0
        failed = 0
        cancel_event = _threading.Event()
        try:
            for i, spec in enumerate(req.tracks):
                label = spec.title or spec.query
                try:
                    path = dl.download_audio(
                        spec.query, dest_dir=req.dest_dir, audio_format=req.audio_format,
                        cancel_event=cancel_event,
                    )
                    downloaded += 1
                    yield f"data: {_json.dumps({'processed': i+1, 'total': total, 'title': label, 'query': spec.query, 'status': 'finished', 'path': path, 'downloaded': downloaded})}\n\n"
                except dl.DownloadCancelled:
                    # Client disconnected mid-track — bubble out of the loop.
                    raise
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.warning("album download failed for %r: %s", spec.query, exc)
                    yield f"data: {_json.dumps({'processed': i+1, 'total': total, 'title': label, 'query': spec.query, 'status': 'error', 'error': str(exc), 'failed': failed})}\n\n"
            yield f"data: {_json.dumps({'done': True, 'downloaded': downloaded, 'failed': failed, 'total': total})}\n\n"
        except (BaseException, dl.DownloadCancelled):
            cancel_event.set()
            raise

    return StreamingResponse(event_stream(), media_type="text/event-stream")
