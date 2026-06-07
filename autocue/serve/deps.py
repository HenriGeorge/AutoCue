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


def get_discover_store(request: Request):
    """Lazy-construct a DiscoverStore singleton on first call.

    Lives on app.state so multiple requests share the same connection
    + boot-recovery only runs once per server lifetime. Construction is
    cheap (just opens SQLite + runs migrations if needed) but we still
    avoid doing it eagerly in `lifespan` because some endpoints (e.g.
    `/api/status`) don't need it and we want server-startup to be fast
    even when the data dir is unavailable.
    """
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

        threading.Thread(
            target=_prewarm_index, args=(app.state.ro_db or app.state.db,), daemon=True, name="index-prewarm"
        ).start()
    except Exception as e:
        logger.warning("Could not open Rekordbox DB: %s — server will still start", e)
        app.state.db = None
        app.state.ro_db = None
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
    # Tear down the shared analysis thread-pool (TASK-001).
    try:
        from ..analysis.concurrency import shutdown_pool
        shutdown_pool()
    except Exception as exc:  # pragma: no cover — best-effort shutdown
        logger.warning("Could not shut down analysis pool cleanly: %s", exc)
