from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import event, text

logger = logging.getLogger(__name__)


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
            target=_prewarm_index, args=(app.state.db,), daemon=True, name="index-prewarm"
        ).start()
    except Exception as e:
        logger.warning("Could not open Rekordbox DB: %s — server will still start", e)
        app.state.db = None
        app.state.ro_db = None
    yield
    app.state.db = None
    app.state.ro_db = None
