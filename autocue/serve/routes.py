from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from ..analysis.quality import check_library_health, check_track_health
from ..db_writer import has_existing_hot_cues
from ..generator import GenerationPrefs, generate_cues_for_track
from .deps import _get_discover_db_path_safe, get_db, get_ro_db


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
    DownloadEnqueueResponse,
    DownloadCancelResponse,
    DownloadProgressEvent,
    DownloadQueueResponse,
    DownloadQueueActive,
    RevealPathRequest,
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
def status(request: Request, db=Depends(get_ro_db)):
    count = db.get_content().count()
    # Diagnostic field: only returned when the caller explicitly opts in via
    # header. The web UI never sets it. The QA agent sets it to verify the
    # server is bound to a sandbox copy of master.db, not the user's library.
    db_path: str | None = None
    if request.headers.get("x-autocue-diagnostic") == "1":
        from pathlib import Path
        db_dir = getattr(db, "_db_dir", None)
        if db_dir is not None:
            db_path = str(Path(db_dir) / "master.db")
    return StatusResponse(connected=True, track_count=count, db_path=db_path)


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


def _wait_any(in_flight: dict):
    """Wait for ANY future in ``in_flight`` to complete; return (done_set, pending_set).

    Thin wrapper around ``concurrent.futures.wait`` with
    ``return_when=FIRST_COMPLETED`` — retained for the bounded in-flight
    pattern's unit tests; the production code path now uses the explicit
    producer/consumer split via ``_compute_stage`` + ``_writer_stage``.
    """
    from concurrent.futures import FIRST_COMPLETED, wait
    if not in_flight:
        return set(), set()
    return wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)


# ---------------------------------------------------------------------------
# TASK-039 / TASK-040 — explicit producer/consumer split for
# /api/generate-apply-stream. The PRD prescribes:
#
#   * _compute_stage(content_ids, ...) — parallel ANLZ read + cue generation
#     on the shared pool, pushes ``(tid, content, cues, skip)`` tuples through
#     a bounded ``queue.Queue(maxsize=2 * pool_size())``.
#   * _writer_stage(q, ...) — dedicated thread; loops ``q.get()`` until it
#     sees a ``None`` sentinel; commits per-track on the single writer thread
#     and emits SSE events back to the request generator via a second queue.
#
# The shipped TASK-040 code used a single-loop dict-of-futures and was
# functionally equivalent but structurally divergent from the PRD; #107
# tracks the refactor to the spec'd shape.
# ---------------------------------------------------------------------------

# Sentinel pushed onto the compute→writer queue when compute is done.
_COMPUTE_DONE = None


def _compute_stage(
    content_ids,
    *,
    compute_fn,
    q,
    cancel,
    pool,
    in_flight_cap,
):
    """Producer: parallel compute. Pushes results onto ``q``; ends with sentinel.

    ``compute_fn(track_id)`` is a single-arg callable that returns a 4-tuple
    ``(track_id, content_or_none, cues_or_none, skip_reason_or_none)`` and
    MUST NOT raise (callers wrap their per-track exceptions into the tuple).

    Maintains TASK-040's bounded-in-flight memory cap by keeping at most
    ``in_flight_cap`` futures outstanding (typically ``2 * pool_size()``).
    The queue's own ``maxsize`` enforces the second tier of backpressure:
    if the writer falls behind, ``q.put`` blocks and prevents the producer
    from churning the pool faster than results are drained.

    Each result is pushed via ``q.put(result, block=True, timeout=10)``; on
    put timeout we re-check ``cancel`` and retry — we do NOT drop results.

    Always pushes the ``_COMPUTE_DONE`` (``None``) sentinel onto ``q`` before
    returning, including on cancellation, so the writer thread can join.
    """
    try:
        track_iter = iter(content_ids)
        in_flight: dict = {}
        # Prime the in-flight set.
        for _ in range(max(1, in_flight_cap)):
            if cancel.is_set():
                break
            try:
                nxt = next(track_iter)
            except StopIteration:
                break
            in_flight[pool.submit(compute_fn, nxt)] = nxt

        while in_flight:
            if cancel.is_set():
                break
            done_set, _pending = _wait_any(in_flight)
            for fut in done_set:
                in_flight.pop(fut, None)
                try:
                    result = fut.result()
                except Exception as exc:  # noqa: BLE001
                    # compute_fn shouldn't raise, but if it does, surface as a
                    # skip rather than crashing the whole producer.
                    result = (None, None, None, f"err:{exc}")
                # Backpressure-aware put with cancel re-check on timeout.
                while True:
                    if cancel.is_set():
                        break
                    try:
                        q.put(result, block=True, timeout=10)
                        break
                    except Exception:
                        # queue.Full or other transient — re-check cancel and retry.
                        continue
                # Top-up.
                if not cancel.is_set():
                    try:
                        nxt = next(track_iter)
                        in_flight[pool.submit(compute_fn, nxt)] = nxt
                    except StopIteration:
                        pass
    finally:
        # Sentinel ALWAYS flows, even on cancel/exception, so the writer joins.
        try:
            q.put(_COMPUTE_DONE, block=True, timeout=10)
        except Exception:
            pass


def _writer_stage(
    q,
    db,
    *,
    write_fn,
    overwrite,
    dry_run,
    event_q,
    cancel,
    total,
    written_ids: list | None = None,
):
    """Consumer: single-thread writer. Drains ``q`` until the sentinel.

    The writer is the ONLY thread that calls ``write_fn`` / ``db.commit`` on
    a given /api/generate-apply-stream invocation — preserves SQLite's
    single-writer rule on master.db.

    Pushes SSE payload dicts onto ``event_q`` (drained by the request thread
    which is the only thread that can ``yield`` from the SSE generator).
    Returns ``(applied, skipped)``.

    If ``written_ids`` is provided, each successfully-written track id is
    appended — used by the caller to invalidate per-track mixability sidecar
    rows after the stream finishes (issue #106 / PR #143 contract).
    """
    from .. import perf as _perf
    applied = skipped = processed = 0
    while True:
        if cancel.is_set():
            # Drain remaining items so producer's put() doesn't block forever.
            try:
                while True:
                    item = q.get_nowait()
                    if item is _COMPUTE_DONE:
                        break
            except Exception:
                pass
            break
        try:
            item = q.get(timeout=0.5)
        except Exception:
            continue
        if item is _COMPUTE_DONE:
            break
        tid, content, cues, skip = item
        processed += 1
        if processed % 100 == 0:
            try:
                db.session.expire_all()
            except Exception:
                pass
        if skip or content is None or not cues:
            skipped += 1
        else:
            try:
                # TASK-046 — per-track writer span (producer/consumer arch).
                # _writer_stage runs in a single dedicated thread, so this
                # span never violates master.db's single-writer rule.
                with _perf.perf_span("generate_apply.write_one"):
                    n = write_fn(
                        content, cues, db,
                        overwrite=overwrite, dry_run=dry_run,
                    )
                if n > 0:
                    applied += 1
                    if written_ids is not None and tid is not None:
                        written_ids.append(tid)
                else:
                    skipped += 1
            except Exception:
                skipped += 1
        event_q.put({
            "processed": processed,
            "total": total,
            "applied": applied,
            "skipped": skipped,
        })
    return applied, skipped


def _master_db_mtime(db) -> float | None:
    """Return the master.db mtime — used as the snapshot + ETag key (TASK-021/023)."""
    import os
    from pathlib import Path
    db_dir = getattr(db, "_db_dir", None)
    if db_dir is None:
        return None
    try:
        return os.path.getmtime(Path(db_dir) / "master.db")
    except OSError:
        return None


def _invalidate_tracks_snapshot(app) -> None:
    """Drop the in-memory /api/tracks snapshot (TASK-026)."""
    lock = getattr(app.state, "tracks_snapshot_lock", None)
    if lock is None:
        app.state.tracks_snapshot = None
        return
    with lock:
        app.state.tracks_snapshot = None


@router.get("/tracks", response_model=list[TrackItem])
def tracks(
    response: Response,
    request: Request,
    playlist_id: int | None = Query(None),
    sort_by: str = Query("title"),
    sort_order: str = Query("asc"),
    limit: int = Query(5000),
    offset: int = 0,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    accept: str | None = Header(default=None),
    db=Depends(get_ro_db),
):
    # TASK-025 — optional NDJSON streaming response (one JSON object per line)
    # when the client opts in via ``Accept: application/x-ndjson``. The default
    # JSON-array path is preserved for back-compat with autocue-qa harness.
    _ndjson_requested = bool(accept and "application/x-ndjson" in accept)
    from pyrekordbox.db6 import DjmdAlbum, DjmdArtist, DjmdContent, DjmdKey, DjmdPlaylist, DjmdSongPlaylist
    from sqlalchemy import asc, desc, func
    from .. import perf as _perf

    # TASK-023 — ETag/304 revalidation keyed by master.db mtime.
    mtime = _master_db_mtime(db)
    etag = f'"{int(mtime)}"' if mtime is not None else None
    if etag is not None:
        response.headers["ETag"] = etag
        if if_none_match is not None and if_none_match == etag:
            return Response(status_code=304, headers={"ETag": etag})

    # TASK-021 — fast path: serve from in-memory snapshot when the request
    # matches the snapshot's profile (default sort, full library) and the
    # master.db mtime hasn't changed. Filter by playlist + slice in Python.
    if (
        mtime is not None
        and sort_by == "title"
        and sort_order == "asc"
    ):
        app = request.app
        lock = getattr(app.state, "tracks_snapshot_lock", None)
        snapshot = getattr(app.state, "tracks_snapshot", None)
        if (
            lock is not None
            and snapshot is not None
            and snapshot.get("mtime") == mtime
        ):
            with _perf.perf_span("tracks.cached"):
                with lock:
                    payload = snapshot["payload"]
                ids_subset = None
                if playlist_id is not None:
                    pl = db.query(DjmdPlaylist).filter_by(ID=str(playlist_id)).first()
                    if not pl:
                        raise HTTPException(404, f"Playlist {playlist_id} not found")
                    ids_subset = {
                        str(e.ContentID)
                        for e in db.query(DjmdSongPlaylist).filter_by(PlaylistID=pl.ID)
                    }
                filtered = (
                    [item for item in payload if str(item.id) in ids_subset]
                    if ids_subset is not None
                    else payload
                )
                page = filtered[offset:offset + limit]
                if _ndjson_requested:
                    import json as _json
                    from fastapi.responses import StreamingResponse
                    headers = {
                        "X-Total-Count": str(len(filtered)),
                    }
                    if etag is not None:
                        headers["ETag"] = etag

                    def _stream():
                        for item in page:
                            yield (_json.dumps(item.model_dump()) + "\n").encode("utf-8")

                    return StreamingResponse(
                        _stream(),
                        media_type="application/x-ndjson",
                        headers=headers,
                    )
                response.headers["X-Total-Count"] = str(len(filtered))
                return page

    # TASK-046 — wrap the SQL-build path so /api/perf/recent can show the
    # cold-vs-warm cost split.
    _build_span = _perf.perf_span("tracks.build")
    _build_span.__enter__()
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
    items = [_to_item(t, db, key_map, last_played_map, my_tags_map, color_name_map, hot_cue_counts) for t in rows]

    # TASK-021 write-through — populate the snapshot when the request matches
    # the default-sort full-library profile. Later requests with the same
    # profile (and unchanged master.db mtime) skip the SQL pipeline entirely.
    if (
        mtime is not None
        and playlist_id is None
        and sort_by == "title"
        and sort_order == "asc"
        and offset == 0
        and total <= limit
    ):
        app = request.app
        lock = getattr(app.state, "tracks_snapshot_lock", None)
        if lock is not None:
            with lock:
                app.state.tracks_snapshot = {"mtime": mtime, "payload": items}
        # TASK-022 — persist gzipped JSON to CacheStore so cold-start
        # /api/tracks calls (after server restart) skip the SQL pipeline
        # entirely. Failure is non-fatal — the in-memory snapshot still works.
        cache_store = getattr(app.state, "cache_store", None)
        if cache_store is not None:
            try:
                payload_bytes = cache_store.gzip_json(
                    [item.model_dump() for item in items]
                )
                cache_store.put_tracks_snapshot(mtime, payload_bytes)
            except Exception as exc:
                logger.warning("snapshot persist failed: %s", exc)

    _build_span.__exit__(None, None, None)
    return items


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

    # Issue #120 — track exists but no artwork available. Return 204 (not 404)
    # so the browser does NOT log a console error for the failed <img> load.
    # 404 is reserved for the "track ID not in the DB" case above, which is a
    # genuine error worth surfacing in DevTools.
    return Response(status_code=204)


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
def apply(req: ApplyRequest, request: Request, db=Depends(get_db)):
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
            backup_path = str(backup_database(db_path, discover_db_path=_get_discover_db_path_safe()))
        except Exception as e:
            raise HTTPException(500, f"Backup failed — aborting: {e}")

    applied = skipped = 0
    written_ids: list[int] = []
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
            written_ids.append(track_result.id)
        else:
            skipped += 1

    # Issue #106 — cue positions feed intro/outro detection, which the
    # mixability score depends on. Drop stale sidecar rows so the next /score
    # call recomputes against the freshly written cues. Bypass quietly if the
    # sidecar is disabled or any single delete fails (best-effort cache hygiene
    # must never abort a successful write).
    if not req.dry_run and written_ids:
        cache_store = getattr(request.app.state, "cache_store", None)
        if cache_store is not None:
            for cid in written_ids:
                try:
                    cache_store.invalidate_mixability(cid)
                except Exception:
                    pass

    return ApplyResponse(
        applied=applied,
        skipped=skipped,
        dry_run=req.dry_run,
        backup_path=backup_path,
    )


@router.post("/generate-apply", response_model=ApplyResponse)
def generate_apply(req: GenerateAndApplyRequest, request: Request, db=Depends(get_db)):
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
            backup_path = str(backup_database(db_path, discover_db_path=_get_discover_db_path_safe()))
        except Exception as e:
            raise HTTPException(500, f"Backup failed — aborting: {e}")

    applied = skipped = 0
    written_ids: list[int] = []
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
            written_ids.append(tid)
        else:
            skipped += 1

    # Issue #106 — see /api/apply for rationale. Cue writes invalidate the
    # mixability sidecar rows for the touched tracks so /score recomputes.
    if not req.dry_run and written_ids:
        cache_store = getattr(request.app.state, "cache_store", None)
        if cache_store is not None:
            for cid in written_ids:
                try:
                    cache_store.invalidate_mixability(cid)
                except Exception:
                    pass

    return ApplyResponse(
        applied=applied,
        skipped=skipped,
        dry_run=req.dry_run,
        backup_path=backup_path,
    )


@router.post("/generate-apply-stream")
def generate_apply_stream(req: GenerateAndApplyRequest, request: Request, db=Depends(get_db)):
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
            backup_path = str(backup_database(db_path, discover_db_path=_get_discover_db_path_safe()))
        except Exception as e:
            raise HTTPException(500, f"Backup failed — aborting: {e}")

    total = len(req.track_ids)

    import os as _os
    from .. import perf as _perf

    def event_stream():
        applied = skipped = 0
        written_ids: list[int] = []
        # TASK-046 (issue #110) — outer compute span for the whole SSE stream so
        # /api/perf/recent surfaces per-endpoint p50/p95/p99.
        _outer_span = _perf.perf_span("generate_apply.compute")
        _outer_span.__enter__()
        if _os.environ.get("AUTOCUE_PARALLEL_GENERATE_APPLY", "1") != "0":
            # TASK-039 / TASK-040 — explicit producer/consumer split.
            #
            #   compute (pool) ──► queue.Queue(maxsize=2*pool_size) ──► writer (1 thread)
            #                              │                                  │
            #                              └── _COMPUTE_DONE sentinel ────────┘
            #
            # Writer thread is the SOLE owner of master.db writes (SQLite
            # single-writer rule). It pushes SSE payloads onto event_q; the
            # request thread (this generator) drains event_q and yields them —
            # only this thread can yield from the SSE generator.
            #
            # Set AUTOCUE_PARALLEL_GENERATE_APPLY=0 to fall back to the serial
            # path below.
            import queue as _queue
            import threading as _threading
            import time as _time

            from ..analysis.concurrency import get_pool, pool_size
            from ..db_writer import write_cues_to_db as _write_cues_to_db

            pool = get_pool()
            psize = pool_size()
            cap = max(2, 2 * psize)
            work_q: _queue.Queue = _queue.Queue(maxsize=cap)
            event_q: _queue.Queue = _queue.Queue()  # unbounded; events are tiny

            # TASK-041 — cancellation on client disconnect.
            cancel = _threading.Event()
            disconnected_at: list[float | None] = [None]

            def _poll_disconnect():
                while not cancel.is_set():
                    try:
                        if hasattr(request, "_is_disconnected") and getattr(request, "_is_disconnected", False):
                            cancel.set()
                            disconnected_at[0] = _time.time()
                            return
                    except Exception:
                        return
                    _time.sleep(0.2)

            poll_thread = _threading.Thread(target=_poll_disconnect, daemon=True, name="autocue-cancel-poll")
            poll_thread.start()

            # Per-track compute closure (captures prefs + phrase_only).
            def _compute_one(tid):
                try:
                    content = db.get_content(ID=tid)
                    if content is None:
                        return (tid, None, None, "not_found")
                    if req.phrase_only:
                        try:
                            has_ext = bool(db.get_anlz_path(content, "EXT"))
                        except Exception:
                            has_ext = False
                        if not has_ext:
                            return (tid, content, None, "no_phrase")
                    cues, _ = generate_cues_for_track(content, db, prefs)
                    if not cues:
                        return (tid, content, None, "no_cues")
                    return (tid, content, cues, None)
                except Exception as exc:  # noqa: BLE001
                    return (tid, None, None, f"err:{exc}")

            writer_result: dict = {"applied": 0, "skipped": 0}

            def _writer_target():
                a, s = _writer_stage(
                    work_q, db,
                    write_fn=_write_cues_to_db,
                    overwrite=req.overwrite,
                    dry_run=req.dry_run,
                    event_q=event_q,
                    cancel=cancel,
                    total=total,
                    written_ids=written_ids,
                )
                writer_result["applied"] = a
                writer_result["skipped"] = s

            writer_thread = _threading.Thread(
                target=_writer_target, daemon=True, name="autocue-writer"
            )
            writer_thread.start()

            def _producer_target():
                _compute_stage(
                    req.track_ids,
                    compute_fn=_compute_one,
                    q=work_q,
                    cancel=cancel,
                    pool=pool,
                    in_flight_cap=cap,
                )

            producer_thread = _threading.Thread(
                target=_producer_target, daemon=True, name="autocue-producer"
            )
            producer_thread.start()

            # Drain event_q, yielding SSE payloads, until the writer thread
            # finishes (which only happens after the sentinel arrives) AND
            # the event_q is fully drained.
            while True:
                try:
                    payload = event_q.get(timeout=0.25)
                    yield f"data: {json.dumps(payload)}\n\n"
                    continue
                except Exception:
                    pass
                # Timed out — only safe to exit when writer is done and queue
                # is empty (writer may have flushed an event between our
                # get() timing out and our liveness check).
                if not writer_thread.is_alive() and event_q.empty():
                    break

            # Make sure the producer joins cleanly (it should already be done
            # by the time the writer finished, but the join is cheap).
            producer_thread.join(timeout=1.0)
            cancel.set()  # stop the poll thread
            applied = writer_result["applied"]
            skipped = writer_result["skipped"]
            # Issue #106 / PR #143 — mixability sidecar invalidation.
            # The writer stage populates ``written_ids`` for every successful
            # cue commit; we invalidate those rows so /score recomputes on
            # next read. Mirrors the same block in /api/apply.
            if not req.dry_run and written_ids:
                _cs = getattr(request.app.state, "cache_store", None)
                if _cs is not None:
                    for cid in written_ids:
                        try:
                            _cs.invalidate_mixability(cid)
                        except Exception:
                            pass
            _outer_span.__exit__(None, None, None)
            yield f"data: {json.dumps({'done': True, 'applied': applied, 'skipped': skipped, 'backup_path': backup_path})}\n\n"
            return

        # Serial path (default) — unchanged behavior.
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
                        # TASK-046 — per-track writer span (serial path).
                        with _perf.perf_span("generate_apply.write_one"):
                            n = write_cues_to_db(content, cues, db, overwrite=req.overwrite, dry_run=req.dry_run)
                        if n > 0:
                            applied += 1
                            written_ids.append(tid)
                        else:
                            skipped += 1
            yield f"data: {json.dumps({'processed': i + 1, 'total': total, 'applied': applied, 'skipped': skipped})}\n\n"
        # Issue #106 — mixability sidecar invalidation. See /api/apply.
        if not req.dry_run and written_ids:
            _cs = getattr(request.app.state, "cache_store", None)
            if _cs is not None:
                for cid in written_ids:
                    try:
                        _cs.invalidate_mixability(cid)
                    except Exception:
                        pass
        _outer_span.__exit__(None, None, None)
        yield f"data: {json.dumps({'done': True, 'applied': applied, 'skipped': skipped, 'backup_path': backup_path})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/backups", response_model=list[BackupItem])
def list_backups():
    """List master backups. Discover sidecars (PRD §6.7) are reported via
    the optional ``has_discover_sidecar`` flag on each BackupItem — when
    True, a parallel ``discover_<TS>.db`` exists alongside the master file.

    Only master backups appear as top-level entries; the listing groups by
    timestamp so the UI sees one row per backup with the sidecar status as
    metadata, not as a second entry."""
    import re
    from datetime import datetime
    from ..db_writer import BACKUP_DIR
    if not BACKUP_DIR.exists():
        return []
    # Index discover sidecars by timestamp first so the master loop can
    # check existence without a stat per master file.
    discover_by_ts: dict[str, Path] = {}
    for p in BACKUP_DIR.glob("discover_*.db"):
        m = re.match(r"discover_(\d{8}T\d{6})\.db", p.name)
        if m:
            discover_by_ts[m.group(1)] = p

    items = []
    for p in sorted(BACKUP_DIR.glob("master_*.db"),
                    key=lambda f: f.stat().st_mtime, reverse=True):
        stat = p.stat()
        m = re.search(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})", p.stem)
        if m:
            yr, mo, dy, hh, mm, ss = m.groups()
            created_at = f"{yr}-{mo}-{dy} {hh}:{mm}:{ss}"
            ts_key = f"{yr}{mo}{dy}T{hh}{mm}{ss}"
        else:
            created_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            ts_key = None
        items.append(BackupItem(
            path=str(p),
            filename=p.name,
            size_mb=round(stat.st_size / (1024 * 1024), 2),
            created_at=created_at,
            has_discover_sidecar=ts_key in discover_by_ts if ts_key else False,
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
    # Sidecar L2 cache (TASK-010 / TASK-017): wipe all rows so the next read
    # against the restored DB doesn't return stale data.
    _cache_store = getattr(request_obj.app.state, "cache_store", None)
    if _cache_store is not None:
        try:
            _cache_store.invalidate_all()
        except Exception as exc:
            logger.warning("Could not invalidate sidecar cache during restore: %s", exc)

    # PRD §6.7 discover sidecar — restore the parallel discover_<TS>.db when
    # the backup carried one. The 'master_TS.db' filename encodes the TS.
    discover_restored = False
    import re as _re
    m = _re.match(r"master_(\d{8}T\d{6})\.db", req.filename)
    if m:
        ts_key = m.group(1)
        discover_backup = BACKUP_DIR / f"discover_{ts_key}.db"
        if discover_backup.exists():
            try:
                from .deps import discover_data_dir
                discover_target = discover_data_dir() / "discover.db"
                # Close any open store first so the file isn't locked.
                current_store = getattr(request_obj.app.state, "discover_store", None)
                if current_store is not None:
                    current_store.close()
                    request_obj.app.state.discover_store = None
                shutil.copy2(discover_backup, discover_target)
                discover_restored = True
                logger.info("Restored discover sidecar from %s", discover_backup.name)
            except Exception as exc:
                logger.warning("Discover sidecar restore failed (master restored OK): %s", exc)
        else:
            logger.info(
                "No discover.db sidecar for %s — leaving current curation state intact",
                req.filename,
            )

    msg = f"Restored from {req.filename}"
    if discover_restored:
        msg += " (with Discover state)"
    return RestoreResponse(restored=True, message=msg)


@router.delete("/backups/{filename}")
def delete_backup(filename: str):
    """Delete a backup. PRD §6.7: when deleting a master backup, the
    parallel discover_<TS>.db sidecar is removed too. WAL/SHM sidecars on
    the master file are also unlinked."""
    import re as _re
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
    # Discover sidecar — paired by timestamp on master_<TS>.db only.
    deleted_discover = False
    m = _re.match(r"master_(\d{8}T\d{6})\.db", filename)
    if m:
        ts_key = m.group(1)
        discover_path = BACKUP_DIR / f"discover_{ts_key}.db"
        if discover_path.exists():
            discover_path.unlink()
            deleted_discover = True
    return {"deleted": filename, "deleted_discover_sidecar": deleted_discover}


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
            backup_path = str(backup_database(db_path, discover_db_path=_get_discover_db_path_safe()))
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
            backup_path = str(backup_database(db_path, discover_db_path=_get_discover_db_path_safe()))
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
            backup_path = str(backup_database(db_path, discover_db_path=_get_discover_db_path_safe()))
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
        from .. import perf as _perf
        stmt = _text("UPDATE djmdContent SET ColorID = :cid WHERE ID = :tid")
        colored = skipped = 0
        BATCH = 50

        # TASK-046 (issue #110) — outer compute span for the SSE stream.
        _outer_span = _perf.perf_span("color_tracks.compute")
        _outer_span.__enter__()
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
                        # TASK-046 — per-track writer span.
                        with _perf.perf_span("color_tracks.write_one"):
                            db.session.execute(stmt, {"cid": color_id, "tid": tid})
                    colored += 1

                if (i + 1) % BATCH == 0:
                    yield f"data: {_json.dumps({'colored': colored, 'skipped': skipped, 'total': total})}\n\n"

            if not dry_run:
                db.session.expire_all()
                db.session.commit()  # single commit for entire batch

        except BaseException:  # includes GeneratorExit (client disconnect)
            db.session.rollback()
            _outer_span.__exit__(None, None, None)
            raise

        _outer_span.__exit__(None, None, None)
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
    limit: int | None = Query(None, ge=1, le=10000),
    db=Depends(get_ro_db),
):
    """Stream library health as SSE. One JSON event per track, then a summary event.

    Optional ?playlist_id=N limits scan to that playlist — use for incremental rescans
    after re-analyzing a subset of tracks in Rekordbox.

    Optional ?limit=N caps the number of tracks scanned (bounded smoke testing).
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
        from .. import perf as _perf
        # TASK-046 (issue #110) — outer compute span for the library-health SSE stream.
        _outer_span = _perf.perf_span("library_health.compute")
        _outer_span.__enter__()
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

        if limit is not None and total_count is not None:
            total_count = min(total_count, limit)
        if total_count is not None:
            yield f"data: {json.dumps({'total': total_count})}\n\n"

        emitted = 0
        for report in gen:
            if limit is not None and emitted >= limit:
                break
            emitted += 1
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
        _outer_span.__exit__(None, None, None)
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
            backup_path = Path(backup_database(db_path, discover_db_path=_get_discover_db_path_safe())).name  # filename only, not full path
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
        from .. import perf as _perf
        processed = affected = total_changed = total_skipped = 0
        total_reasons: dict[str, int] = {}

        # TASK-046 (issue #110) — outer compute span for cue-tools SSE.
        _outer_span = _perf.perf_span("cue_tools.compute")
        _outer_span.__enter__()
        try:
            for i, tid in enumerate(track_ids):
                content = db.get_content(ID=tid)
                if content is None:
                    processed += 1
                else:
                    try:
                        # TASK-046 — per-track mutation span.
                        with _perf.perf_span("cue_tools.write_one"):
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
            _outer_span.__exit__(None, None, None)
            raise

        _outer_span.__exit__(None, None, None)
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
    from fastapi.responses import StreamingResponse  # issue #110 — was missing; SSE construction NameError'd before any span fired
    from ..analysis.classify import get_classification, CATEGORIES, _class_cache
    from pyrekordbox.db6 import DjmdPlaylist, DjmdSongPlaylist

    if force_refresh:
        _class_cache.clear()

    if playlist_id is not None:
        pl = db.query(DjmdPlaylist).filter(DjmdPlaylist.ID == str(playlist_id)).first()
        if pl is None:
            raise HTTPException(404, f"Playlist {playlist_id} not found")

    def event_stream():
        from .. import perf as _perf
        # TASK-046 (issue #110) — outer compute span for classify SSE.
        _outer_span = _perf.perf_span("classify.compute")
        _outer_span.__enter__()
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
        # TASK-004 — parallel classify path (default-on as of TASK-008
        # verification; set AUTOCUE_PARALLEL_CLASSIFY=0 to disable).
        import os as _os
        if _os.environ.get("AUTOCUE_PARALLEL_CLASSIFY", "1") != "0":
            from concurrent.futures import as_completed as _as_completed
            from ..analysis.concurrency import get_pool as _get_pool

            def _one(content):
                try:
                    data = get_classification(content, db)
                    return (content.ID, data, None)
                except Exception as exc:
                    return (getattr(content, "ID", -1), None, exc)

            pool = _get_pool()
            futures = [pool.submit(_one, c) for c in contents]
            for fut in _as_completed(futures):
                cid, data, err = fut.result()
                total += 1
                if err is not None:
                    logger.exception("classify error track %s: %s", cid, err)
                    continue
                resp = ClassificationResponse(track_id=cid, **data)
                counts[data["primary"]] = counts.get(data["primary"], 0) + 1
                yield f"data: {resp.model_dump_json()}\n\n"
        else:
            for content in contents:
                total += 1
                try:
                    data = get_classification(content, db)
                    resp = ClassificationResponse(track_id=content.ID, **data)
                    counts[data["primary"]] = counts.get(data["primary"], 0) + 1
                    yield f"data: {resp.model_dump_json()}\n\n"
                except Exception as exc:
                    logger.exception("classify error track %s: %s", content.ID, exc)
        _outer_span.__exit__(None, None, None)
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
        import os as _os
        from pyrekordbox.db6 import DjmdContent, DjmdMyTag, DjmdSongMyTag
        from ..analysis.auto_tag import ALL_AUTOCUE_TAG_NAMES
        from .. import perf as _perf
        # TASK-046 (issue #110) — outer compute span for auto-tag/discogs SSE.
        _outer_span = _perf.perf_span("auto_tag_discogs.compute")
        _outer_span.__enter__()
        total   = len(req.track_ids)
        tagged  = 0
        skipped = 0
        errors  = 0

        # Pre-build a map of tag_id → tag_name for the skip_existing check (one query)
        if req.skip_existing:
            tag_name_map = {str(t.ID): t.Name for t in db.session.query(DjmdMyTag).all() if t.Name}
        else:
            tag_name_map = {}

        # Pure-read evaluator: resolve content + check skip + fetch Discogs styles.
        # Returns (tid, content, styles, status). status in {'ok','no_content','skip_existing','no_styles','error'}
        # No DB writes, so safe to run on the shared pool.
        def _eval_one(tid):
            try:
                content = db.get_content(ID=tid)
                if content is None:
                    return (tid, None, [], "no_content", None, None, None)
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
                        return (tid, content, [], "skip_existing", None, None, None)
                artist = str(getattr(content, "ArtistName", "") or "")
                title  = str(getattr(content, "Title", "") or "")
                styles = search_styles(artist, title, req.token)
                if not styles:
                    return (tid, content, [], "no_styles", artist, title, None)
                return (tid, content, styles, "ok", artist, title, None)
            except Exception as exc:
                return (tid, None, [], "error", None, None, exc)

        # Writer-side helper: insert DjmdSongMyTag rows for (tid, styles), commit.
        def _write_one(tid, styles):
            for style in styles:
                tag_id = ensure_tag_by_name(db, style)
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

        parallel = _os.environ.get("AUTOCUE_PARALLEL_AUTO_TAG") == "1"

        if parallel:
            from concurrent.futures import as_completed as _as_completed
            from ..analysis.concurrency import get_pool as _get_pool

            pool = _get_pool()
            futures = [pool.submit(_eval_one, tid) for tid in req.track_ids]
            processed = 0
            for fut in _as_completed(futures):
                processed += 1
                try:
                    tid, content, styles, status, artist, title, exc = fut.result()
                except Exception as outer:
                    errors += 1
                    logger.warning("Discogs tag future failed: %s", outer)
                    yield f"data: {_json.dumps({'processed': processed, 'total': total, 'error': str(outer), 'errors': errors})}\n\n"
                    continue

                if status == "error":
                    errors += 1
                    logger.warning("Discogs tag error for track %d: %s", tid, exc)
                    yield f"data: {_json.dumps({'processed': processed, 'total': total, 'track_id': tid, 'error': str(exc), 'errors': errors})}\n\n"
                    continue
                if status == "no_content":
                    skipped += 1
                    yield f"data: {_json.dumps({'processed': processed, 'total': total, 'track_id': tid, 'styles': [], 'skipped': skipped})}\n\n"
                    continue
                if status == "skip_existing":
                    skipped += 1
                    yield f"data: {_json.dumps({'processed': processed, 'total': total, 'track_id': tid, 'styles': [], 'skipped': skipped})}\n\n"
                    continue
                if status == "no_styles":
                    skipped += 1
                    yield f"data: {_json.dumps({'processed': processed, 'total': total, 'track_id': tid, 'artist': artist, 'title': title, 'styles': [], 'skipped': skipped})}\n\n"
                    continue

                # status == "ok" — writer runs serially in this generator thread.
                if not req.dry_run:
                    try:
                        # TASK-046 — per-track writer span (parallel path).
                        with _perf.perf_span("auto_tag_discogs.write_one"):
                            _write_one(tid, styles)
                    except Exception as werr:
                        errors += 1
                        logger.warning("Discogs tag write error for track %d: %s", tid, werr)
                        try:
                            db.session.rollback()
                        except Exception:
                            pass
                        yield f"data: {_json.dumps({'processed': processed, 'total': total, 'track_id': tid, 'error': str(werr), 'errors': errors})}\n\n"
                        continue
                tagged += 1
                yield f"data: {_json.dumps({'processed': processed, 'total': total, 'track_id': tid, 'artist': artist, 'title': title, 'styles': styles, 'tagged': tagged})}\n\n"
        else:
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
                        # TASK-046 — per-track writer span (serial path).
                        with _perf.perf_span("auto_tag_discogs.write_one"):
                            _write_one(tid, styles)

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

        _outer_span.__exit__(None, None, None)
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
    from .. import perf as _perf

    def event_stream():
        # TASK-046 (issue #110) — outer compute span for enrich-comments SSE.
        _outer_span = _perf.perf_span("enrich_comments.compute")
        _outer_span.__enter__()
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

        # TASK-006 — parallel comment-string build (default-on as of
        # TASK-008 verification; set AUTOCUE_PARALLEL_ENRICH_COMMENTS=0
        # to disable). Read-only string building runs in pool workers;
        # writes (content.Commnt = new_comment + db.commit()) stay
        # sequential in the generator (single-writer rule).
        import os as _os
        if _os.environ.get("AUTOCUE_PARALLEL_ENRICH_COMMENTS", "1") != "0" and track_ids:
            from concurrent.futures import as_completed as _as_completed
            from ..analysis.concurrency import get_pool as _get_pool

            def _build_one(tid):
                try:
                    content = db.get_content(ID=tid)
                    if content is None:
                        return (tid, None, None, "missing", None)
                    previous = str(getattr(content, "Commnt", "") or "")
                    # dry_run=True so the worker never mutates content.Commnt —
                    # the writer below performs the assignment + commit serially.
                    new_comment = enrich_comment(
                        content, db, overwrite=req.overwrite, dry_run=True
                    )
                    if new_comment is None:
                        return (tid, content, previous, "no_change", None)
                    return (tid, content, previous, None, new_comment)
                except Exception as exc:
                    return (tid, None, None, None, exc)

            pool = _get_pool()
            futures = [pool.submit(_build_one, tid) for tid in track_ids]
            processed = 0
            for fut in _as_completed(futures):
                processed += 1
                try:
                    tid, content, previous, skip_reason, payload = fut.result()
                except Exception:
                    errors += 1
                    yield f"data: {_json.dumps({'processed': processed, 'total': total, 'enriched': enriched})}\n\n"
                    continue

                if isinstance(payload, Exception):
                    errors += 1
                elif skip_reason is not None:
                    skipped += 1
                else:
                    new_comment = payload
                    if req.dry_run:
                        enriched += 1
                    else:
                        try:
                            # TASK-046 — per-track writer span (parallel path).
                            with _perf.perf_span("enrich_comments.write_one"):
                                content.Commnt = new_comment
                                db.session.commit()
                            enriched += 1
                            undo_rows.append({"content_id": str(tid), "previous": previous})
                        except Exception as commit_exc:
                            db.session.rollback()
                            errors += 1
                            logger.warning(
                                "Enrichment commit failed for track %s: %s",
                                tid, commit_exc,
                            )
                yield f"data: {_json.dumps({'processed': processed, 'total': total, 'enriched': enriched})}\n\n"
        else:
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
                                    # TASK-046 — per-track writer span (serial path).
                                    with _perf.perf_span("enrich_comments.write_one"):
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

        _outer_span.__exit__(None, None, None)
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


# ===========================================================================
# Download — PRD v1.0 unified API
# ===========================================================================
#
# Architecture (see .agent/prd/DOWNLOAD_PRD.md §6.12 + §7.3):
#
#   POST /api/download/enqueue          → returns {job_id, phase:'queued', position}
#   POST /api/download/album/enqueue    → same shape
#   GET  /api/download/stream/{job_id}  → SSE; single-consumption cache + 410
#   POST /api/download/cancel/{job_id}  → sync; idempotent
#   GET  /api/download/queue            → {active, queued_count, max_concurrency}
#   POST /api/download/reveal           → opens file manager at path
#   GET  /api/download/config           → existing + os_reveal_supported + max_concurrency
#
# Back-compat aliases for one release (removed in v0.3.0):
#   POST /api/download                  → enqueue + stream in one shot
#   POST /api/download/album            → same
# ===========================================================================

@router.get("/download/config", response_model=DownloadConfigResponse)
def download_config(db=Depends(get_ro_db)):
    """Report capability + queue config. Used by the frontend to gate UI."""
    from .. import download as dl
    return DownloadConfigResponse(
        available=dl.ytdlp_available(),
        ffmpeg=dl.ffmpeg_available(),
        default_dir=dl.default_download_dir(),
        music_folder=_detect_music_folder(db),
        os_reveal_supported=dl.reveal_supported(),
        max_concurrency=dl.get_download_queue().max_concurrency if dl.ytdlp_available() else 1,
    )


def _coerce_legacy_format_in_body(body: dict) -> dict:
    """Apply legacy audio_format coercion before pydantic Literal validation.

    Mutates `body` in place and returns it. Logs WARNING once per (process,
    legacy_value), DEBUG thereafter. Unknown strings pass through unchanged
    and let pydantic 422 with the unknown_format error.
    """
    from .. import download as dl
    fmt = body.get("audio_format")
    if fmt and fmt not in dl.ALLOWED_FORMATS:
        try:
            body["audio_format"] = dl.normalize_audio_format(fmt)
        except ValueError:
            pass  # let pydantic Literal validator emit 422
    return body


def _validate_download_body(model_cls, raw: dict):
    """Validate body via pydantic; on ValidationError, raise a friendly 422
    for known migrations (audio_quality removed, audio_format invalid)."""
    from pydantic import ValidationError as _PVE
    from .. import download as dl
    try:
        return model_cls.model_validate(raw)
    except _PVE as exc:
        for err in exc.errors():
            loc = err.get("loc") or ()
            etype = err.get("type") or ""
            if etype == "extra_forbidden" and loc and loc[-1] == "audio_quality":
                raise HTTPException(status_code=422, detail={
                    "error_code": "audio_quality_removed",
                    "error_message": (
                        "audio_quality removed; use audio_format='mp3_320' for 320 kbps "
                        "MP3 or 'wav' for uncompressed."
                    ),
                    "hint": "Update your client to set audio_format instead.",
                })
            if loc and loc[-1] == "audio_format":
                bad = err.get("input") or raw.get("audio_format")
                raise HTTPException(status_code=422, detail={
                    "error_code": "unknown_format",
                    "error_message": (
                        f"Unknown audio_format {bad!r}. "
                        f"Pick one of {', '.join(dl.ALLOWED_FORMATS)}."
                    ),
                    "hint": "WAV is uncompressed; MP3 320 is the default; "
                            "Original keeps the source container.",
                })
        # Fallback — surface the first error verbatim
        raise HTTPException(status_code=422, detail=exc.errors())


def _check_download_available() -> None:
    """Raise HTTPException if yt-dlp/ffmpeg missing — used by every enqueue endpoint."""
    from .. import download as dl
    if not dl.ytdlp_available():
        raise HTTPException(503, 'yt-dlp is not installed. Install with: pip install -e ".[download]"')
    if not dl.ffmpeg_available():
        raise HTTPException(503, "ffmpeg not found on PATH — required to extract audio.")


@router.post("/download/enqueue", response_model=DownloadEnqueueResponse)
async def download_enqueue(request: Request):
    """Enqueue a single-track download. Returns {job_id, phase:'queued', position}
    synchronously (≤200 ms; no yt-dlp work). Client then opens
    GET /api/download/stream/{job_id} for SSE progress."""
    from .. import download as dl
    raw = await request.json()
    raw = _coerce_legacy_format_in_body(raw)
    # Pydantic validation — Literal enforces audio_format ∈ ALLOWED_FORMATS;
    # extra='forbid' raises 422 for stale `audio_quality` field (middleware
    # below rewrites that specific error into a friendlier shape).
    req = _validate_download_body(DownloadRequest, raw)
    _check_download_available()

    if req.allow_playlist:
        # Pre-flight expand. If yt-dlp says it's a playlist, fan out via 'album'.
        entries = dl.expand_playlist(req.query)
        if len(entries) > 1:
            payload = {
                "tracks": [{"query": e["query"], "title": e["title"]} for e in entries],
                "dest_dir": req.dest_dir,
                "audio_format": req.audio_format,
                "normalize": req.normalize,
                "embed_metadata": req.embed_metadata,
            }
            job_id, position = dl.get_download_queue().enqueue("album", payload)
            return DownloadEnqueueResponse(job_id=job_id, position=position)

    payload = {
        "query": req.query,
        "dest_dir": req.dest_dir,
        "audio_format": req.audio_format,
        "normalize": req.normalize,
        "embed_metadata": req.embed_metadata,
        "allow_playlist": req.allow_playlist,
    }
    job_id, position = dl.get_download_queue().enqueue("single", payload)
    return DownloadEnqueueResponse(job_id=job_id, position=position)


@router.post("/download/album/enqueue", response_model=DownloadEnqueueResponse)
async def download_album_enqueue(request: Request):
    """Enqueue an explicit-track-list album download. Used by Discover per-card."""
    from .. import download as dl
    raw = await request.json()
    raw = _coerce_legacy_format_in_body(raw)
    req = _validate_download_body(DownloadAlbumRequest, raw)
    _check_download_available()
    payload = {
        "tracks": [{"query": s.query, "title": s.title} for s in req.tracks],
        "dest_dir": req.dest_dir,
        "audio_format": req.audio_format,
        "normalize": req.normalize,
        "embed_metadata": req.embed_metadata,
    }
    job_id, position = dl.get_download_queue().enqueue("album", payload)
    return DownloadEnqueueResponse(job_id=job_id, position=position)


@router.get("/download/stream/{job_id}")
def download_stream(job_id: str):
    """SSE stream of DownloadProgressEvent for a job.

    - Yields heartbeat comment-line `: keepalive\\n\\n` every 15s (handled by the
      queue's stream() method yielding {type:"_keepalive"} sentinel which we
      convert here). Frontend `_consumeSSE` ignores any line not starting with
      `data:`, so heartbeats are invisible to onEvent handlers.
    - On second connect after `done` (within 60s cache TTL): returns 410 Gone
      with cached final body — frontend renders success/error from cache
      without re-emitting a done event.
    - On unknown job_id: 404.
    """
    import json as _json
    from fastapi.responses import StreamingResponse
    from .. import download as dl

    try:
        gen = dl.get_download_queue().stream(job_id)
    except KeyError:
        raise HTTPException(404, f"unknown job_id {job_id!r}")

    first = next(gen, None)
    if first is None:
        raise HTTPException(404, f"unknown job_id {job_id!r}")

    # Handle the "already_consumed" sentinel by returning 410 with the cached
    # final body. Frontend uses this to render success-from-cache.
    if isinstance(first, dict) and first.get("http_status") == 410:
        body = {
            "error_code": "already_consumed",
            "status": first.get("cached_status"),
            "path": first.get("path"),
            "job_id": job_id,
        }
        return Response(content=_json.dumps(body), status_code=410, media_type="application/json")

    def event_stream():
        # Re-yield the first event we already consumed.
        ev = first
        while True:
            if ev.get("type") == "_keepalive":
                yield ": keepalive\n\n"
            else:
                yield f"data: {_json.dumps(ev)}\n\n"
                if ev.get("type") == "done":
                    return
            ev = next(gen, None)
            if ev is None:
                return

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/download/cancel/{job_id}", response_model=DownloadCancelResponse)
def download_cancel(job_id: str):
    """Synchronously cancel a job. Idempotent. 200 {cancelled:bool}.

    Decoupled from SSE stream lifecycle so HTTP/2 buffering or ffmpeg-pass-1
    silence doesn't strand a cancel.
    """
    from .. import download as dl
    ok = dl.get_download_queue().cancel(job_id)
    if not ok:
        return DownloadCancelResponse(cancelled=False, reason="unknown_or_completed_job")
    return DownloadCancelResponse(cancelled=True)


@router.get("/download/queue", response_model=DownloadQueueResponse)
def download_queue():
    """Snapshot of in-flight + queued jobs. Polled by the frontend queue indicator."""
    from .. import download as dl
    snap = dl.get_download_queue().status()
    return DownloadQueueResponse(
        active=[DownloadQueueActive(**a) for a in snap["active"]],
        queued_count=snap["queued_count"],
        max_concurrency=snap["max_concurrency"],
    )


@router.post("/download/reveal", status_code=204)
def download_reveal(req: RevealPathRequest, db=Depends(get_ro_db)):
    """Open the host's file manager at the given path.

    Path-validation gate (PRD §6.10 + round-4 M4):
    1. Path must exist on disk (Path.resolve(strict=True) → 404 if not).
    2. Path must be under default_download_dir() / detected music_folder /
       AUTOCUE_DOWNLOAD_DIR env (whichever is set). Otherwise 403.
    3. On platforms without a reveal binary → 501.
    """
    from pathlib import Path as _Path
    from .. import download as dl

    if not dl.reveal_supported():
        raise HTTPException(501, "reveal_unsupported_platform")

    try:
        resolved = _Path(req.path).resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise HTTPException(404, "path not found")

    allowed_roots: list[_Path] = []
    try:
        allowed_roots.append(_Path(dl.default_download_dir()).resolve())
    except OSError:
        pass
    mf = _detect_music_folder(db)
    if mf:
        try:
            allowed_roots.append(_Path(mf).resolve())
        except OSError:
            pass
    env_root = os.environ.get("AUTOCUE_DOWNLOAD_DIR")
    if env_root:
        try:
            allowed_roots.append(_Path(env_root).resolve())
        except OSError:
            pass

    def _is_under(p: _Path, roots: list[_Path]) -> bool:
        for r in roots:
            try:
                p.relative_to(r)
                return True
            except ValueError:
                continue
        return False

    if not _is_under(resolved, allowed_roots):
        raise HTTPException(403, "forbidden_path")

    try:
        dl.reveal_path(str(resolved))
    except Exception as exc:  # noqa: BLE001
        logger.warning("reveal_path failed for %r: %s", str(resolved), exc)
        raise HTTPException(500, f"reveal failed: {exc}")
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Back-compat aliases (removed in v0.3.0).
# These open-and-stream in one shot. First SSE event is the queued/job_id pair.
# ---------------------------------------------------------------------------

def _legacy_event_shape(ev: dict) -> dict:
    """Translate the new DownloadProgressEvent shape into the legacy SSE event
    keys (`done: true`, `failed: N`, `status: 'finished'`) so existing callers
    of the deprecated POST /api/download endpoints don't break in v0.2.0.

    Legacy keys preserved for one release; removed in v0.3.0 alongside the
    deprecated alias endpoints themselves.
    """
    out = dict(ev)
    if ev.get("type") == "done":
        out["done"] = True
        status = ev.get("status")
        if status == "success":
            out["status"] = "finished"
            out.setdefault("downloaded", 1)
        elif status in ("error", "cancelled"):
            out.setdefault("failed", 1)
        if ev.get("error_message") and "error" not in out:
            out["error"] = ev.get("error_message")
    return out


@router.post("/download")
async def download_single_legacy(request: Request):
    """[DEPRECATED — removed in v0.3.0] One-shot enqueue + stream.

    Migrate to: POST /api/download/enqueue → GET /api/download/stream/{job_id}.
    """
    import json as _json
    from fastapi.responses import StreamingResponse
    from .. import download as dl

    raw = await request.json()
    raw = _coerce_legacy_format_in_body(raw)
    # Quietly drop audio_quality for back-compat alias (no 422 on legacy field).
    raw.pop("audio_quality", None)
    req = _validate_download_body(DownloadRequest, raw)
    _check_download_available()

    q = dl.get_download_queue()
    if req.allow_playlist:
        entries = dl.expand_playlist(req.query)
        if len(entries) > 1:
            job_id, _ = q.enqueue("album", {
                "tracks": [{"query": e["query"], "title": e["title"]} for e in entries],
                "dest_dir": req.dest_dir,
                "audio_format": req.audio_format,
                "normalize": req.normalize,
                "embed_metadata": req.embed_metadata,
            })
        else:
            job_id, _ = q.enqueue("single", req.model_dump())
    else:
        job_id, _ = q.enqueue("single", req.model_dump())

    def event_stream():
        for ev in q.stream(job_id):
            if ev.get("type") == "_keepalive":
                yield ": keepalive\n\n"
            else:
                yield f"data: {_json.dumps(_legacy_event_shape(ev))}\n\n"
                if ev.get("type") == "done":
                    return
    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/download/album")
async def download_album_legacy(request: Request):
    """[DEPRECATED — removed in v0.3.0] One-shot album enqueue + stream.

    Migrate to: POST /api/download/album/enqueue → GET /api/download/stream/{job_id}.
    """
    import json as _json
    from fastapi.responses import StreamingResponse
    from .. import download as dl

    raw = await request.json()
    raw = _coerce_legacy_format_in_body(raw)
    raw.pop("audio_quality", None)
    req = _validate_download_body(DownloadAlbumRequest, raw)
    _check_download_available()

    q = dl.get_download_queue()
    job_id, _ = q.enqueue("album", {
        "tracks": [{"query": s.query, "title": s.title} for s in req.tracks],
        "dest_dir": req.dest_dir,
        "audio_format": req.audio_format,
        "normalize": req.normalize,
        "embed_metadata": req.embed_metadata,
    })

    def event_stream():
        for ev in q.stream(job_id):
            if ev.get("type") == "_keepalive":
                yield ": keepalive\n\n"
            else:
                yield f"data: {_json.dumps(_legacy_event_shape(ev))}\n\n"
                if ev.get("type") == "done":
                    return
    return StreamingResponse(event_stream(), media_type="text/event-stream")


# =============================================================================
# Discover v2 endpoints (T-015 SSE feed + T-018 release detail/cancel + T-023 token)
# =============================================================================

from .deps import (
    get_discover_store,
    get_cached_token_valid,
    invalidate_token_cache,
    set_cached_token_valid,
)
from .schemas import (
    DiscoverCancelScanResponse,
    DiscoverReleaseDetailResponse,
    DiscoverScanStatusResponse,
    DiscoverTokenValidResponse,
)


def _discogs_token_from_env() -> str:
    """Return the DISCOGS_TOKEN from the environment / .env, or empty string.

    Mirrors the resolution used by /api/config. ``autocue serve`` does NOT
    auto-load .env into the process environment, so reading ``os.environ``
    alone misses tokens that the user only ever wrote to the project .env.
    We duplicate the /api/config inline parse here (rather than importing
    the handler) so the two paths can't drift apart silently — every
    Discogs-token consumer now goes through this resolver.

    Resolution order (last non-empty wins):
      1. .env at the repo root (key=value lines)
      2. os.environ (explicit shell export takes precedence over .env)
    """
    import os
    token = ""
    env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env",
    )
    try:
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DISCOGS_TOKEN="):
                        token = line.split("=", 1)[1].strip()
                        break
    except Exception:
        pass
    return os.environ.get("DISCOGS_TOKEN", token).strip()


@router.get("/discover/token-status", response_model=DiscoverTokenValidResponse)
def discover_token_status(request: Request):
    """Check whether the configured Discogs personal-access token is valid.

    PRD §8: 1h positive cache, instant-invalidate on any 401 from any
    Discogs call. The cache lives in deps.py module state; this endpoint
    is read-only against it (no side-effects beyond writing the cache
    when we DO call /oauth/identity).
    """
    from datetime import datetime, timezone
    cached = get_cached_token_valid()
    if cached is True:
        return DiscoverTokenValidResponse(
            valid=True, checked_at=datetime.now(timezone.utc).isoformat(), cached=True,
        )
    token = _discogs_token_from_env()
    if not token:
        return DiscoverTokenValidResponse(
            valid=False, checked_at=datetime.now(timezone.utc).isoformat(), cached=False,
        )
    from autocue.analysis import discogs as _discogs
    try:
        valid = _discogs.validate_token(token)
    except Exception:
        # Network / Discogs down — treat as "unknown/invalid" but do NOT cache.
        valid = False
    set_cached_token_valid(bool(valid))
    return DiscoverTokenValidResponse(
        valid=bool(valid), checked_at=datetime.now(timezone.utc).isoformat(), cached=False,
    )


@router.get("/discover/feed/status", response_model=DiscoverScanStatusResponse)
def discover_feed_status(store=Depends(get_discover_store)):
    """Return the currently-running scan's metadata, if any.

    Used by the SSE feed endpoint's concurrent-scan guard AND by the UI's
    progress indicator on Discover-tab open (so the user sees an in-flight
    scan rather than starting a duplicate).
    """
    row = store.conn.execute(
        "SELECT scan_id, started_at, feeders, novelty_strategy "
        "FROM scans WHERE finished_at IS NULL LIMIT 1"
    ).fetchone()
    if row is None:
        return DiscoverScanStatusResponse(running=False)
    return DiscoverScanStatusResponse(
        running=True,
        scan_id=row["scan_id"],
        started_at=row["started_at"],
        feeders=row["feeders"],
        novelty_strategy=row["novelty_strategy"],
    )


@router.post("/discover/feed/cancel", response_model=DiscoverCancelScanResponse)
def discover_feed_cancel(store=Depends(get_discover_store)):
    """Force-cancel the currently-running scan.

    Marks the row as status='cancelled' and clears it from the lock. The
    orchestrator's pending column writes are deliberately LEFT in place so
    boot recovery (or the next scan) handles them — cancellation isn't the
    same as a clean finish, so we don't promote pending values.
    """
    row = store.conn.execute(
        "SELECT scan_id FROM scans WHERE finished_at IS NULL LIMIT 1"
    ).fetchone()
    if row is None:
        return DiscoverCancelScanResponse(was_running=False)
    store.finish_scan(int(row["scan_id"]), status="cancelled")
    return DiscoverCancelScanResponse(
        was_running=True, cancelled_scan_id=int(row["scan_id"]),
    )


@router.get("/discover/releases/{release_id}", response_model=DiscoverReleaseDetailResponse)
def discover_release_detail(release_id: int, store=Depends(get_discover_store)):
    """Fetch a Discogs release detail, cached in ``release_details`` (TTL 30 days).

    On cache miss: calls :func:`discogs.get_release_details`, persists the
    result, returns ``cached=False``. On hit: returns from cache with
    ``cached=True``. On rate-limit conditions, the 1h-positive token cache
    is invalidated if Discogs returned 401.
    """
    cached = store.get_release_detail(release_id)
    if cached is not None:
        return DiscoverReleaseDetailResponse(**cached, cached=True)
    token = _discogs_token_from_env()
    if not token:
        raise HTTPException(400, "DISCOGS_TOKEN not configured")
    from autocue.analysis import discogs as _discogs
    try:
        detail = _discogs.get_release_details(release_id, token=token)
    except _discogs.RateLimitNearExhausted as exc:
        detail = exc.data  # the response is valid; we just need to back off
    except _discogs.Discogs429 as exc:
        raise HTTPException(
            503, f"Discogs rate-limited; retry after {exc.retry_after}s",
        )
    except Exception as exc:
        import urllib.error as _urlerr
        if isinstance(exc, _urlerr.HTTPError) and exc.code == 401:
            invalidate_token_cache()
            raise HTTPException(401, "Discogs token rejected")
        raise HTTPException(502, f"Discogs upstream error: {exc}")
    if not detail:
        raise HTTPException(404, f"release {release_id} not found")
    store.record_release_detail(release_id, detail)
    return DiscoverReleaseDetailResponse(**detail, cached=False)


@router.get("/discover/feed")
def discover_feed_sse(
    request: Request,
    sources: str = Query("artist,label,novelty", description="comma-list of feeder names"),
    top_n: int = Query(50, ge=1, le=200),
    year_from: int | None = Query(None),
    db=Depends(get_db),
    store=Depends(get_discover_store),
):
    """Stream a Discover scan as Server-Sent Events.

    Returns ``HTTPException(409)`` if a scan is already running (concurrent-
    scan lock). The UI's status indicator can poll
    :func:`discover_feed_status` to learn the running scan's ID and decide
    whether to wait or cancel.

    Event format mirrors the orchestrator's yields:
    - ``event: progress`` for per-feeder progress
    - ``event: release`` for ranked releases
    - ``event: warning`` / ``event: error`` / ``event: sparse_adjacency``
    - ``event: done`` carries the final feed + telemetry

    Wraps the orchestrator's generator into the SSE wire format; the
    orchestrator itself stays free of FastAPI/SSE coupling for testability.
    """
    import json as _json
    from fastapi.responses import StreamingResponse

    # Concurrent-scan lock check up front — return 409 before doing any work.
    if store.is_scan_running():
        raise HTTPException(409, "A scan is already running for this database")

    token = _discogs_token_from_env()
    if not token:
        raise HTTPException(400, "DISCOGS_TOKEN not configured")

    feeders_list = [f.strip() for f in sources.split(",") if f.strip()]
    if not feeders_list:
        feeders_list = ["artist", "label", "novelty"]

    # Build taste vector + adjacency upfront so the generator's first yield is fast.
    from autocue.analysis.discover.scan_orchestrator import ScanConfig, run_scan
    from autocue.analysis.discover.style_graph import load_style_adjacency
    from autocue.analysis.discover.taste import build_taste_vector
    from .deps import discover_data_dir

    taste_vector = build_taste_vector(db)
    adjacency = load_style_adjacency(discover_data_dir()).adjacency

    # Resolve the prior scan's novelty_strategy so this scan picks up the rotation.
    prev_row = store.conn.execute(
        "SELECT novelty_strategy FROM scans "
        "WHERE status = 'ok' AND novelty_strategy IS NOT NULL "
        "ORDER BY scan_id DESC LIMIT 1"
    ).fetchone()
    previous_novelty_strategy = prev_row["novelty_strategy"] if prev_row else None

    # Pull the followed-labels list for the novelty label-strategy.
    followed = store.list_followed_labels()
    followed_ids = [int(r["label_id"]) for r in followed]
    followed_names = [str(r["name"]) for r in followed]

    cfg = ScanConfig(
        feeders=feeders_list,
        top_n=top_n,
        year_from=year_from,
    )

    def event_stream():
        try:
            for kind, payload in run_scan(
                store, taste_vector, adjacency, token,
                config=cfg,
                followed_label_ids_for_novelty=followed_ids,
                followed_label_names_for_novelty=followed_names,
                previous_novelty_strategy=previous_novelty_strategy,
            ):
                yield f"event: {kind}\ndata: {_json.dumps(payload)}\n\n"
        except Exception as exc:
            logger.exception("discover_feed_sse: orchestrator crashed")
            yield f"event: error\ndata: {_json.dumps({'feeder': 'orchestrator', 'exc': str(exc)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # SSE pass-through (matches existing GZip middleware skip)
    })


# =============================================================================
# Discover v2 — state CRUD (T-016) + follow-labels (T-017) + export/import (T-019) + stats (T-020)
# =============================================================================

from .schemas import (
    DiscoverBlockArtistRequest,
    DiscoverBlockLabelRequest,
    DiscoverDismissRequest,
    DiscoverFollowLabelRequest,
    DiscoverImportResponse,
    DiscoverKeyOnlyRequest,
    DiscoverLabelSearchResponse,
    DiscoverSaveRequest,
    DiscoverSnoozeRequest,
    DiscoverStateRowsResponse,
    DiscoverStatsResponse,
    DiscoverUnblockArtistRequest,
    DiscoverUnblockLabelRequest,
    DiscoverUnfollowLabelRequest,
)


def _snooze_duration_to_until(duration: str) -> str:
    """Map '1w' / '1m' / '3m' to an ISO until_date.

    Limited set keeps the API contract small + matches the UI's button row
    (PRD §6.11). Unknown duration → HTTP 400.
    """
    from datetime import datetime, timedelta, timezone
    deltas = {
        "1w": timedelta(weeks=1),
        "1m": timedelta(days=30),
        "3m": timedelta(days=90),
    }
    if duration not in deltas:
        raise HTTPException(400, f"Unknown snooze duration {duration!r}; use 1w / 1m / 3m")
    return (datetime.now(timezone.utc) + deltas[duration]).isoformat()


# ── State CRUD POSTs ──────────────────────────────────────────────────────────

@router.post("/discover/save")
def discover_save(req: DiscoverSaveRequest, store=Depends(get_discover_store)):
    store.save(
        release_key=req.release_key, release_id=req.release_id,
        artist=req.artist, title=req.title, label=req.label,
    )
    return {"ok": True}


@router.post("/discover/dismiss")
def discover_dismiss(req: DiscoverDismissRequest, store=Depends(get_discover_store)):
    store.dismiss(
        release_key=req.release_key, release_id=req.release_id,
        artist=req.artist, title=req.title, reason=req.reason,
    )
    return {"ok": True}


@router.post("/discover/snooze")
def discover_snooze(req: DiscoverSnoozeRequest, store=Depends(get_discover_store)):
    until = _snooze_duration_to_until(req.duration)
    store.snooze(
        release_key=req.release_key, until_date=until,
        release_id=req.release_id, artist=req.artist, title=req.title,
    )
    return {"ok": True, "until_date": until}


@router.post("/discover/unsave")
def discover_unsave(req: DiscoverKeyOnlyRequest, store=Depends(get_discover_store)):
    store.unsave(req.release_key)
    return {"ok": True}


@router.post("/discover/undismiss")
def discover_undismiss(req: DiscoverKeyOnlyRequest, store=Depends(get_discover_store)):
    store.undismiss(req.release_key)
    return {"ok": True}


@router.post("/discover/unsnooze")
def discover_unsnooze(req: DiscoverKeyOnlyRequest, store=Depends(get_discover_store)):
    store.unsnooze(req.release_key)
    return {"ok": True}


@router.post("/discover/block-artist")
def discover_block_artist(req: DiscoverBlockArtistRequest, store=Depends(get_discover_store)):
    store.block_artist(req.discogs_artist_id, req.name)
    return {"ok": True}


@router.post("/discover/unblock-artist")
def discover_unblock_artist(req: DiscoverUnblockArtistRequest, store=Depends(get_discover_store)):
    store.unblock_artist(req.discogs_artist_id)
    return {"ok": True}


@router.post("/discover/block-label")
def discover_block_label(req: DiscoverBlockLabelRequest, store=Depends(get_discover_store)):
    store.block_label(req.discogs_label_id, req.name)
    return {"ok": True}


@router.post("/discover/unblock-label")
def discover_unblock_label(req: DiscoverUnblockLabelRequest, store=Depends(get_discover_store)):
    store.unblock_label(req.discogs_label_id)
    return {"ok": True}


# ── List GETs ────────────────────────────────────────────────────────────────

@router.get("/discover/saved", response_model=DiscoverStateRowsResponse)
def discover_list_saved(store=Depends(get_discover_store)):
    return DiscoverStateRowsResponse(items=store.list_saved())


@router.get("/discover/dismissed", response_model=DiscoverStateRowsResponse)
def discover_list_dismissed(store=Depends(get_discover_store)):
    return DiscoverStateRowsResponse(items=store.list_dismissed())


@router.get("/discover/snoozed", response_model=DiscoverStateRowsResponse)
def discover_list_snoozed(
    include_resurfaced: bool = Query(False),
    store=Depends(get_discover_store),
):
    return DiscoverStateRowsResponse(
        items=store.list_snoozed(include_resurfaced=include_resurfaced),
    )


@router.get("/discover/downloaded", response_model=DiscoverStateRowsResponse)
def discover_list_downloaded(store=Depends(get_discover_store)):
    return DiscoverStateRowsResponse(items=store.list_downloaded())


@router.get("/discover/blocked-artists", response_model=DiscoverStateRowsResponse)
def discover_list_blocked_artists(store=Depends(get_discover_store)):
    return DiscoverStateRowsResponse(items=store.list_blocked_artists())


@router.get("/discover/blocked-labels", response_model=DiscoverStateRowsResponse)
def discover_list_blocked_labels(store=Depends(get_discover_store)):
    return DiscoverStateRowsResponse(items=store.list_blocked_labels())


# ── Follow labels (T-017) ────────────────────────────────────────────────────

@router.get("/discover/labels", response_model=DiscoverStateRowsResponse)
def discover_list_followed_labels(store=Depends(get_discover_store)):
    return DiscoverStateRowsResponse(items=store.list_followed_labels())


@router.post("/discover/labels/follow")
def discover_follow_label(req: DiscoverFollowLabelRequest, store=Depends(get_discover_store)):
    store.follow_label(req.label_id, req.name)
    return {"ok": True}


@router.post("/discover/labels/unfollow")
def discover_unfollow_label(
    req: DiscoverUnfollowLabelRequest, store=Depends(get_discover_store),
):
    store.unfollow_label(req.label_id)
    return {"ok": True}


@router.get("/discover/labels/search", response_model=DiscoverLabelSearchResponse)
def discover_label_search(
    q: str = Query("", min_length=1, description="label name fragment"),
    per_page: int = Query(20, ge=1, le=50),
):
    """Discogs label autocomplete. Used by the 'Add label by name' UI in
    Discover settings (PRD §6.8). Pass-through to discogs.search_labels."""
    token = _discogs_token_from_env()
    if not token:
        raise HTTPException(400, "DISCOGS_TOKEN not configured")
    from autocue.analysis import discogs as _discogs
    try:
        items = _discogs.search_labels(query=q, token=token, per_page=per_page)
    except _discogs.Discogs429 as exc:
        raise HTTPException(503, f"Discogs rate-limited; retry after {exc.retry_after}s")
    except _discogs.RateLimitNearExhausted as exc:
        items = exc.data
    return DiscoverLabelSearchResponse(items=items)


@router.get("/discover/labels/suggested", response_model=DiscoverStateRowsResponse)
def discover_suggested_labels(
    limit: int = Query(20, ge=1, le=100),
    db=Depends(get_db),
    store=Depends(get_discover_store),
):
    """Top library labels the user hasn't yet explicitly followed.

    Driven by the taste-vector labels Counter; the orchestrator's
    onboarding flow uses this for the 'Pick labels from your library'
    banner. The returned items don't carry Discogs label_ids — those
    have to come from a separate /discover/labels/search lookup per name
    in Tier 1 (no resolver yet)."""
    from autocue.analysis.discover.taste import build_taste_vector
    tv = build_taste_vector(db)
    followed_ids = {r["label_id"] for r in store.list_followed_labels()}
    # Build a name → label_id lookup is out of scope for Tier 1; the UI
    # uses the suggested list for the names and then resolves IDs via the
    # search endpoint on click-to-follow.
    suggestions = [
        {"name": name, "weight": round(score, 3)}
        for name, score in tv.labels.most_common(limit * 2)
    ][:limit]
    # We don't have label_ids for these — UI passes name back through
    # /labels/search to pick the right id at follow time.
    return DiscoverStateRowsResponse(items=suggestions)


# ── State export / import (T-019) ────────────────────────────────────────────

@router.get("/discover/state/export")
def discover_state_export(store=Depends(get_discover_store)):
    """Stream a gzip-compressed snapshot of the discover.db SQLite file.

    Use case is multi-machine transfer (PRD §6.7 multi-machine sync).
    The user can save the .gz, copy it to another machine, and import via
    POST /discover/state/import below.

    We snapshot via SQLite's ``VACUUM INTO`` which produces a consistent
    copy without locking the live connection for a long time.
    """
    import gzip
    import io
    import os as _os
    import tempfile

    from fastapi.responses import Response

    # VACUUM INTO refuses to overwrite an existing file, so the
    # NamedTemporaryFile-allocated path must be unlinked before we run it.
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp:
        snapshot_path = tmp.name
    _os.remove(snapshot_path)
    try:
        store.conn.execute("VACUUM INTO ?", (snapshot_path,))
        with open(snapshot_path, "rb") as f:
            raw = f.read()
    finally:
        try:
            _os.remove(snapshot_path)
        except OSError:
            pass

    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(raw)
    return Response(
        content=buf.getvalue(),
        media_type="application/gzip",
        headers={"Content-Disposition": 'attachment; filename="discover.db.gz"'},
    )


@router.post("/discover/state/import", response_model=DiscoverImportResponse)
async def discover_state_import(
    request: Request,
    store=Depends(get_discover_store),
):
    """Import a previously-exported discover.db.gz.

    The current DiscoverStore is closed, the file swapped in, and a new
    DiscoverStore re-opens against the imported DB so the boot-recovery
    invariants still hold. Reject non-SQLite bodies up front to avoid
    overwriting the live state with junk.
    """
    import gzip

    before = {
        "saved": len(store.list_saved()),
        "dismissed": len(store.list_dismissed()),
        "snoozed": len(store.list_snoozed(include_resurfaced=True)),
        "downloaded": len(store.list_downloaded()),
        "followed_labels": len(store.list_followed_labels()),
        "blocked_artists": len(store.list_blocked_artists()),
        "blocked_labels": len(store.list_blocked_labels()),
    }
    raw = await request.body()
    if not raw:
        raise HTTPException(400, "Empty body")
    try:
        decompressed = gzip.decompress(raw)
    except Exception as exc:
        raise HTTPException(400, f"Body is not a valid gzip stream: {exc}")
    # SQLite magic header check (PRD §9 security).
    if not decompressed.startswith(b"SQLite format 3\x00"):
        raise HTTPException(400, "Decompressed body is not a SQLite database")

    db_path = store.db_path
    store.close()
    request.app.state.discover_store = None  # force re-open on next dep call
    # Write the decompressed bytes into the live data path.
    try:
        with open(db_path, "wb") as f:
            f.write(decompressed)
    except OSError as exc:
        raise HTTPException(500, f"Could not write discover.db: {exc}")

    # Re-open via the dependency factory so boot-recovery runs against the new file.
    from autocue.analysis.discover.store import DiscoverStore as _DiscoverStore
    new_store = _DiscoverStore(db_path=db_path)
    request.app.state.discover_store = new_store

    after = {
        "saved": len(new_store.list_saved()),
        "dismissed": len(new_store.list_dismissed()),
        "snoozed": len(new_store.list_snoozed(include_resurfaced=True)),
        "downloaded": len(new_store.list_downloaded()),
        "followed_labels": len(new_store.list_followed_labels()),
        "blocked_artists": len(new_store.list_blocked_artists()),
        "blocked_labels": len(new_store.list_blocked_labels()),
    }
    return DiscoverImportResponse(before=before, after=after, restored=True)


# ── Stats (T-020) ────────────────────────────────────────────────────────────

@router.get("/discover/stats", response_model=DiscoverStatsResponse)
def discover_stats(store=Depends(get_discover_store)):
    """Aggregate telemetry roll-up for the Settings → Stats panel.

    Scan + saves-correlation queries follow PRD §13. ``saves_per_scan`` is the
    average across scans that successfully finished (status='ok') in the last
    50 scans — the timestamp-window correlation per scan is applied via the
    store.saves_correlated_to_scan helper.
    """
    # Scan totals.
    total_scans = store.conn.execute(
        "SELECT COUNT(*) AS n FROM scans WHERE status = 'ok'"
    ).fetchone()["n"] or 0
    avg_row = store.conn.execute(
        "SELECT AVG(duration_ms) AS d FROM scans WHERE status = 'ok' AND duration_ms IS NOT NULL"
    ).fetchone()
    avg_duration = float(avg_row["d"]) if avg_row and avg_row["d"] is not None else None

    # Saves-per-scan over recent 50 scans.
    recent_scans = store.conn.execute(
        "SELECT scan_id FROM scans WHERE status = 'ok' ORDER BY scan_id DESC LIMIT 50"
    ).fetchall()
    saves_per_scan = None
    if recent_scans:
        per_scan = [store.saves_correlated_to_scan(int(r["scan_id"])) for r in recent_scans]
        saves_per_scan = sum(per_scan) / len(per_scan)

    # Novelty status breakdown — return RATIOS (0..1) keyed by status so the
    # frontend can render percentages directly without needing to know the
    # total. Previously this returned raw counts which the frontend then
    # multiplied by 100, producing "ok 1100%". UX audit Issue 4.
    counts = {"ok": 0, "partial": 0, "sparse_adjacency": 0}
    for row in store.conn.execute(
        "SELECT novelty_status, COUNT(*) AS n FROM scans "
        "WHERE status = 'ok' GROUP BY novelty_status"
    ).fetchall():
        ns = row["novelty_status"]
        if ns in counts:
            counts[ns] = int(row["n"])
    total_novelty = sum(counts.values())
    breakdown = (
        {k: v / total_novelty for k, v in counts.items()}
        if total_novelty > 0 else
        {k: 0.0 for k in counts}
    )

    # Top labels / artists by save frequency — surfaces what the user is
    # ACTUALLY saving rather than what the library merely contains. Returns
    # `count` (not `plays`) so the frontend's a.count renders the right
    # number — UX audit Issue 4 had this rendering "(undefined)".
    top_labels_rows = store.conn.execute(
        "SELECT label AS name, COUNT(*) AS n FROM saved "
        "WHERE label IS NOT NULL AND label != '' "
        "GROUP BY label ORDER BY n DESC LIMIT 10"
    ).fetchall()
    top_artists_rows = store.conn.execute(
        "SELECT artist AS name, COUNT(*) AS n FROM saved "
        "WHERE artist IS NOT NULL AND artist != '' "
        "GROUP BY artist ORDER BY n DESC LIMIT 10"
    ).fetchall()

    return DiscoverStatsResponse(
        total_scans=int(total_scans),
        avg_duration_ms=avg_duration,
        saves_per_scan=saves_per_scan,
        novelty_share=breakdown,
        top_labels=[{"name": r["name"], "count": int(r["n"])} for r in top_labels_rows],
        top_artists=[{"name": r["name"], "count": int(r["n"])} for r in top_artists_rows],
        followed_count=len(store.list_followed_labels()),
        saved_count=len(store.list_saved()),
        dismissed_count=len(store.list_dismissed()),
        snoozed_count=len(store.list_snoozed(include_resurfaced=True)),
        downloaded_count=len(store.list_downloaded()),
        blocked_artist_count=len(store.list_blocked_artists()),
        blocked_label_count=len(store.list_blocked_labels()),
    )


# ── /api/perf/recent (TASK-045) ────────────────────────────────────────────
# Dev-only diagnostic — returns the recent perf ring buffer + per-name p50/p95/p99
# stats. Disabled (404) unless AUTOCUE_PERF=1 was set when the server started.

@router.get("/perf/recent")
def perf_recent(limit: int = 100):
    """Return the most recent perf spans + p50/p95/p99 stats per span name."""
    from .. import perf as _perf

    if not _perf.is_enabled():
        raise HTTPException(status_code=404, detail="AUTOCUE_PERF not enabled")

    limit = max(1, min(1000, int(limit)))
    spans = _perf.recent_spans(limit=limit)

    # Aggregate stats per name from the same ring buffer.
    names: dict[str, list[float]] = {}
    for name, _start, dur_ms in spans:
        names.setdefault(name, []).append(dur_ms)

    stats: dict[str, dict[str, float]] = {}
    for name in names:
        s = _perf.get_stats(name)
        if s is not None:
            stats[name] = s

    return {
        "spans": [
            {"name": n, "start_ts": st, "duration_ms": d}
            for n, st, d in spans
        ],
        "stats": stats,
    }


# ── /api/warmup (TASK-028) ────────────────────────────────────────────────
# Returns the current state of the warm-up pipeline so the UI can show
# an "Indexing N/M" badge while CacheStore is being hydrated.

@router.get("/warmup")
def warmup_progress(request: Request):
    """Return pre-warm pipeline progress: {step, done, total, finished_at}."""
    lock = getattr(request.app.state, "warmup_lock", None)
    progress = getattr(request.app.state, "warmup_progress", None)
    if progress is None:
        # Lifespan hasn't initialized the pipeline (e.g., DB never opened).
        return {"step": "unknown", "done": 0, "total": 0, "finished_at": None}
    if lock is not None:
        with lock:
            return dict(progress)
    return dict(progress)
