from __future__ import annotations

import logging
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from .deps import lifespan
from .routes import router

DOCS_DIR = Path(__file__).parent.parent.parent / "docs"
DEFAULT_PORT = 7432


def _port_is_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _is_our_server(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://localhost:{port}/api/status", timeout=1) as resp:
            return resp.status == 200
    except Exception:
        return False


def create_app(db_path: str | None = None, port: int = DEFAULT_PORT) -> FastAPI:
    app = FastAPI(title="AutoCue", lifespan=lifespan)
    app.state.db_path = db_path
    app.state.port = port
    allowed_origins = [
        "null",  # file:// pages (index.html opened directly from disk)
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    ]
    app.add_middleware(GZipMiddleware, minimum_size=1000)
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

    if not _port_is_free(port):
        if _is_our_server(port):
            url = f"http://localhost:{port}"
            print(f"\n  AutoCue already running  →  {url}\n")
            if open_browser:
                webbrowser.open(url)
            return
        # Try the next 9 ports before giving up
        for alt in range(port + 1, port + 10):
            if _port_is_free(alt):
                print(f"\n  Port {port} is in use — switching to {alt}")
                port = alt
                break
        else:
            print(
                f"\n  Error: port {DEFAULT_PORT} is in use and no alternative found near it.\n"
                "  Stop the conflicting process or use --port to pick a different one.\n",
                file=sys.stderr,
            )
            sys.exit(1)

    url = f"http://localhost:{port}"
    print(f"\n  AutoCue  →  {url}\n")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(db_path, port=port), host="127.0.0.1", port=port, log_level="warning")
