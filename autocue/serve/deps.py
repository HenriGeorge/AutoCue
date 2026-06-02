from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

logger = logging.getLogger(__name__)


def get_db(request: Request):
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(503, "Rekordbox database not connected")
    return db


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
        logger.info("Rekordbox database opened")
        threading.Thread(
            target=_prewarm_index, args=(app.state.db,), daemon=True, name="index-prewarm"
        ).start()
    except Exception as e:
        logger.warning("Could not open Rekordbox DB: %s — server will still start", e)
        app.state.db = None
    yield
    app.state.db = None
