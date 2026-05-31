from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

logger = logging.getLogger(__name__)


def get_db(request: Request):
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(503, "Rekordbox database not connected")
    return db


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = getattr(app.state, "db_path", None)
    try:
        from pyrekordbox import Rekordbox6Database as MasterDatabase
        app.state.db = MasterDatabase(db_path) if db_path else MasterDatabase()
        logger.info("Rekordbox database opened")
    except Exception as e:
        logger.warning("Could not open Rekordbox DB: %s — server will still start", e)
        app.state.db = None
    yield
    app.state.db = None
