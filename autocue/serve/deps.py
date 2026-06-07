from __future__ import annotations

import logging
import os
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import event, text

logger = logging.getLogger(__name__)


def discover_data_dir() -> Path:
    """Platform-native data dir for Discover v2 state. See PRD §6.7.

    Override via $AUTOCUE_DISCOVER_DATA_DIR (used by tests + multi-machine
    install setups). Parent dirs are NOT created here — DiscoverStore does
    that on first construction.
    """
    override = os.environ.get("AUTOCUE_DISCOVER_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AutoCue"
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "AutoCue"
        return Path.home() / "AppData" / "Roaming" / "AutoCue"
    # Linux / other Unix
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "autocue"


def get_db(request: Request):
    """Read-write DB handle — for write endpoints only."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(503, "Rekordbox database not connected")
    return db


def get_ro_db(request: Request):
    """Read-only DB handle — for analysis/health endpoints.

    Uses a separate Rekordbox6Database connection with PRAGMA query_only=ON
    applied at the connection level, so analysis bugs cannot mutate the library.
    Falls back to the read-write handle if the read-only instance is unavailable.
    """
    ro = getattr(request.app.state, "ro_db", None)
    if ro is None:
        ro = getattr(request.app.state, "db", None)
    if ro is None:
        raise HTTPException(503, "Rekordbox database not connected")
    return ro


def _apply_query_only(engine) -> None:
    """Register a connect event that sets PRAGMA query_only=ON on every new connection."""
    @event.listens_for(engine, "connect")
    def _set_readonly(dbapi_conn, _conn_record):
        try:
            dbapi_conn.execute("PRAGMA query_only=ON")
        except Exception as exc:
            logger.warning("Could not set PRAGMA query_only on read-only connection: %s", exc)


def _prewarm_index(db) -> None:
    try:
        from ..analysis.similar import _build_index
        _build_index(db)
    except Exception as e:
        logger.warning("Similarity index pre-warm failed: %s", e)


def _run_warmup_pipeline(app, db) -> None:
    """Multi-step warm-up pipeline — TASK-027.

    Steps (each updates ``app.state.warmup_progress`` so /api/warmup can
    report progress, and each checks ``app.state.warmup_cancel_event``
    so shutdown lands quickly):
      1. ``cache``    — hydrate CacheStore rows for any track with stale/
         missing entries (uses CacheStore.warm_up + the shared pool).
      2. ``index``    — build similarity index from cached vectors.
      3. ``done``     — pipeline finished; UI hides the indexing badge.
    """
    from datetime import datetime, timezone

    def _set(step: str, done: int, total: int):
        with app.state.warmup_lock:
            app.state.warmup_progress = {
                "step": step,
                "done": done,
                "total": total,
                "finished_at": None,
            }

    try:
        cancel_event = app.state.warmup_cancel_event
        # Step 1 — cache hydration.
        _set("cache", 0, 0)
        cache_store = getattr(app.state, "cache_store", None)
        if cache_store is not None:
            try:
                from ..analysis.concurrency import get_pool
                contents = []
                try:
                    contents = [int(c.ID) for c in db.get_content().all()]
                except Exception as exc:
                    logger.warning("warmup: get_content failed: %s", exc)

                def _progress(done: int, total: int) -> None:
                    _set("cache", done, total)

                cache_store.warm_up(
                    db,
                    contents,
                    get_pool(),
                    progress_cb=_progress,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                logger.warning("warmup: cache hydration failed: %s", exc)
        if cancel_event.is_set():
            return
        # Step 2 — similarity index.
        _set("index", 0, 1)
        try:
            from ..analysis.similar import _build_index
            _build_index(db)
        except Exception as exc:
            logger.warning("warmup: similarity index failed: %s", exc)
        if cancel_event.is_set():
            return
        # Step 3 — done.
        with app.state.warmup_lock:
            app.state.warmup_progress = {
                "step": "done",
                "done": 1,
                "total": 1,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("warmup pipeline crashed: %s", exc)


def _get_discover_db_path_safe() -> Path | None:
    """Return the active discover.db path if it exists, else None.

    Used by the cue-write endpoints so their existing safety backups
    automatically capture the discover sidecar (PRD §6.7) without having
    to thread the DiscoverStore singleton through every call site.
    """
    try:
        path = discover_data_dir() / "discover.db"
    except Exception:
        return None
    return path if path.exists() else None


_discover_store_lock = threading.Lock()


def get_discover_store(request: Request):
    """Lazy-construct a DiscoverStore singleton on first call.

    Lives on app.state so multiple requests share the same connection
    + boot-recovery only runs once per server lifetime. Construction is
    cheap (just opens SQLite + runs migrations if needed) but we still
    avoid doing it eagerly in `lifespan` because some endpoints (e.g.
    `/api/status`) don't need it and we want server-startup to be fast
    even when the data dir is unavailable.

    Concurrent first-load races: FastAPI dispatches sync ``Depends`` calls
    onto a thread-pool, so the first page-load (which fires 7 parallel
    ``/api/discover/*`` fetches via loadInitialState) can have N threads
    all see ``store is None`` and all race to construct. The losers then
    fail at ``CREATE TABLE schema_version`` because the winner has
    already created it. Double-checked locking serializes the construct
    while keeping the hot path lock-free after first init.
    """
    store = getattr(request.app.state, "discover_store", None)
    if store is None:
        with _discover_store_lock:
            store = getattr(request.app.state, "discover_store", None)
            if store is None:
                from autocue.analysis.discover.store import DiscoverStore
                try:
                    store = DiscoverStore()
                except Exception as exc:
                    raise HTTPException(503, f"Discover store unavailable: {exc}")
                request.app.state.discover_store = store
    return store


# ── Discogs token validation cache (T-023) ──────────────────────────────────
# 1-hour positive cache; instant invalidation on any 401 from any Discogs call.
# Keeps the silent-failure window bounded to one request, not 1 hour.

import time as _time
from threading import Lock as _Lock

_TOKEN_VALID_TTL_SECONDS = 3600
_token_state_lock = _Lock()
_token_cached_valid: bool | None = None  # None = unchecked
_token_cached_at: float = 0.0


def get_cached_token_valid() -> bool | None:
    """Return the cached validation result, or None if no valid cache entry.

    True / False are valid cached results. None means "no positive cache OR
    cache has expired". The TTL is bound on TRUE only — a False result is
    NOT cached (we don't want a transient 5xx to lock the user out for 1h).
    """
    with _token_state_lock:
        global _token_cached_valid, _token_cached_at
        if _token_cached_valid is True and (_time.time() - _token_cached_at) < _TOKEN_VALID_TTL_SECONDS:
            return True
        return None


def set_cached_token_valid(value: bool) -> None:
    """Record a fresh validation result. Only ``True`` is cached with TTL —
    ``False`` clears the cache so the next request retries."""
    with _token_state_lock:
        global _token_cached_valid, _token_cached_at
        if value:
            _token_cached_valid = True
            _token_cached_at = _time.time()
        else:
            _token_cached_valid = None
            _token_cached_at = 0.0


def invalidate_token_cache() -> None:
    """Forget the cached validation. Called by any code path that hits a 401
    from a Discogs API call so the next request re-validates."""
    set_cached_token_valid(False)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = getattr(app.state, "db_path", None)
    try:
        from pyrekordbox import Rekordbox6Database as MasterDatabase
        app.state.db = MasterDatabase(db_path) if db_path else MasterDatabase()
        logger.info("Rekordbox database opened (read-write)")

        # Open a second connection as the read-only analysis handle
        try:
            ro = MasterDatabase(db_path) if db_path else MasterDatabase()
            _apply_query_only(ro.engine)
            # Apply PRAGMA directly on the DBAPI connection (outside ORM transaction context)
            # to guarantee it takes effect on the existing connection immediately.
            with ro.engine.connect() as raw_conn:
                raw_conn.exec_driver_sql("PRAGMA query_only=ON")
                raw_conn.commit()
            app.state.ro_db = ro
            logger.info("Rekordbox database opened (read-only analysis handle)")
        except Exception as e:
            logger.warning("Could not open read-only DB handle: %s — falling back to shared handle", e)
            app.state.ro_db = None

        # Open the sidecar analysis cache (TASK-010) and wire the L2 hook into
        # analysis modules (TASK-013..016). Failure here is non-fatal — server
        # still starts; analysis falls back to L1 + compute on every call.
        try:
            from ..cache import CacheStore
            from ..analysis import energy as _energy
            from ..analysis import classify as _classify
            from ..analysis import score as _score
            from ..analysis import similar as _similar
            db_dir = getattr(app.state.db, "_db_dir", None)
            if db_dir is not None:
                app.state.cache_store = CacheStore.open_for(str(db_dir))
                _energy.set_cache_store(app.state.cache_store)
                _classify.set_cache_store(app.state.cache_store)
                _score.set_cache_store(app.state.cache_store)
                _similar.set_cache_store(app.state.cache_store)
                logger.info("Sidecar analysis cache opened at %s", db_dir)
            else:
                app.state.cache_store = None
                logger.info("No db_dir resolvable; sidecar cache disabled")
        except Exception as exc:
            logger.warning("Could not open sidecar cache: %s", exc)
            app.state.cache_store = None

        # TASK-027 — multi-step warm-up pipeline. State lives on app.state so
        # /api/warmup can report progress; cancel_event lets shutdown land
        # within ~5s. Falls back to the legacy single-step _prewarm_index path
        # when the cache_store could not be opened.
        # TASK-021 — /api/tracks snapshot scaffolding. Built lazily by the
        # handler on first matching request.
        app.state.tracks_snapshot = None
        app.state.tracks_snapshot_lock = threading.Lock()

        # TASK-022 — hydrate the snapshot from CacheStore on startup so the
        # first /api/tracks call after `autocue serve` lands sub-second.
        try:
            from ..cache import CacheStore as _CS  # noqa
            cache_store = getattr(app.state, "cache_store", None)
            db_dir = getattr(app.state.db, "_db_dir", None)
            if cache_store is not None and db_dir is not None:
                import os
                master_path = os.path.join(str(db_dir), "master.db")
                if os.path.exists(master_path):
                    mtime = os.path.getmtime(master_path)
                    blob = cache_store.get_tracks_snapshot(mtime)
                    if blob is not None:
                        try:
                            raw = cache_store.ungzip_json(blob)
                            from .schemas import TrackItem as _TI
                            payload = [_TI(**row) for row in raw]
                            with app.state.tracks_snapshot_lock:
                                app.state.tracks_snapshot = {
                                    "mtime": mtime, "payload": payload,
                                }
                            logger.info("tracks snapshot hydrated from CacheStore (%d items)", len(payload))
                        except Exception as exc:
                            logger.warning("tracks snapshot hydrate failed: %s", exc)
        except Exception as exc:
            logger.warning("tracks snapshot hydration skipped: %s", exc)

        app.state.warmup_lock = threading.Lock()
        app.state.warmup_cancel_event = threading.Event()
        app.state.warmup_progress = {
            "step": "init", "done": 0, "total": 0, "finished_at": None
        }
        warmup_db = app.state.ro_db or app.state.db
        if getattr(app.state, "cache_store", None) is not None:
            app.state.warmup_thread = threading.Thread(
                target=_run_warmup_pipeline,
                args=(app, warmup_db),
                daemon=True,
                name="warmup-pipeline",
            )
            app.state.warmup_thread.start()
        else:
            app.state.warmup_thread = threading.Thread(
                target=_prewarm_index, args=(warmup_db,), daemon=True, name="index-prewarm"
            )
            app.state.warmup_thread.start()
    except Exception as e:
        logger.warning("Could not open Rekordbox DB: %s — server will still start", e)
        app.state.db = None
        app.state.ro_db = None
        app.state.cache_store = None
    yield
    app.state.db = None
    app.state.ro_db = None
    # Close the discover store if it was lazily opened during the lifespan.
    discover_store = getattr(app.state, "discover_store", None)
    if discover_store is not None:
        try:
            discover_store.close()
        except Exception as exc:  # pragma: no cover — best-effort shutdown
            logger.warning("Could not close discover store cleanly: %s", exc)
    app.state.discover_store = None
    # TASK-030 — cancel the warm-up pipeline and join the daemon thread
    # so a partial cache leaves cleanly committed rows behind.
    cancel = getattr(app.state, "warmup_cancel_event", None)
    if cancel is not None:
        cancel.set()
    warmup_thread = getattr(app.state, "warmup_thread", None)
    if warmup_thread is not None:
        try:
            warmup_thread.join(timeout=5.0)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("warmup thread join failed: %s", exc)
    # Tear down the shared analysis thread-pool (TASK-001).
    try:
        from ..analysis.concurrency import shutdown_pool
        shutdown_pool()
    except Exception as exc:  # pragma: no cover — best-effort shutdown
        logger.warning("Could not shut down analysis pool cleanly: %s", exc)
    # Close the sidecar analysis cache (TASK-010).
    cache_store = getattr(app.state, "cache_store", None)
    if cache_store is not None:
        try:
            from ..analysis import energy as _energy
            from ..analysis import classify as _classify
            from ..analysis import score as _score
            from ..analysis import similar as _similar
            _energy.set_cache_store(None)
            _classify.set_cache_store(None)
            _score.set_cache_store(None)
            _similar.set_cache_store(None)
            cache_store.close()
        except Exception as exc:  # pragma: no cover — best-effort shutdown
            logger.warning("Could not close sidecar cache cleanly: %s", exc)
    app.state.cache_store = None
