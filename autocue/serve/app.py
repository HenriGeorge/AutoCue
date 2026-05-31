from __future__ import annotations

import logging
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .deps import lifespan
from .routes import router

DOCS_DIR = Path(__file__).parent.parent.parent / "docs"
DEFAULT_PORT = 7432


def create_app(db_path: str | None = None, port: int = DEFAULT_PORT) -> FastAPI:
    app = FastAPI(title="AutoCue", lifespan=lifespan)
    app.state.db_path = db_path
    app.state.port = port
    allowed_origins = [
        "null",  # file:// pages (index.html opened directly from disk)
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    if DOCS_DIR.exists():
        app.mount("/", StaticFiles(directory=str(DOCS_DIR), html=True), name="ui")
    return app


def serve(
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    db_path: str | None = None,
) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
    url = f"http://localhost:{port}"
    print(f"\n  AutoCue  →  {url}\n")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(db_path, port=port), host="127.0.0.1", port=port, log_level="warning")
