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


class SnapshotInvalidationMiddleware:
    """Pure-ASGI middleware that drops the /api/tracks snapshot after any
    successful 2xx POST/PUT/DELETE to /api/...

    Pure ASGI (rather than ``@app.middleware("http")`` / BaseHTTPMiddleware)
    because BaseHTTPMiddleware materialises the response and is incompatible
    with StreamingResponse — when the body generator aborts mid-stream it
    raises ``RuntimeError: No response returned.`` (issue #115). We forward
    ASGI events untouched, observing only the status code on
    ``http.response.start`` and triggering invalidation after the final
    body chunk.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        path = scope.get("path", "")
        is_candidate = (
            method in ("POST", "PUT", "DELETE")
            and path.startswith("/api/")
        )
        if not is_candidate:
            await self.app(scope, receive, send)
            return

        status_code = 0
        invalidated = False
        fastapi_app = scope.get("app")

        def _maybe_invalidate() -> None:
            nonlocal invalidated
            if invalidated:
                return
            if not (200 <= status_code < 300):
                return
            if fastapi_app is None:
                return
            try:
                from .routes import _invalidate_tracks_snapshot
                _invalidate_tracks_snapshot(fastapi_app)
            except Exception:
                # Never let bookkeeping break the response.
                pass
            invalidated = True

        async def _send(message):
            nonlocal status_code
            mtype = message.get("type")
            if mtype == "http.response.start":
                status_code = int(message.get("status", 0))
            await send(message)
            if mtype == "http.response.body" and not message.get("more_body", False):
                _maybe_invalidate()

        await self.app(scope, receive, _send)


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

    # TASK-026 / issue #115 — invalidate the /api/tracks snapshot after any
    # successful POST / DELETE / PUT to /api/*. The handler's mtime check
    # already catches master.db changes; this is defense-in-depth so the
    # snapshot clears immediately after the mutating call returns 2xx, even
    # before the OS flushes the new mtime.
    #
    # Implemented as a pure-ASGI middleware (NOT @app.middleware("http"),
    # which wraps in BaseHTTPMiddleware). BaseHTTPMiddleware is incompatible
    # with StreamingResponse: when the body generator aborts or raises
    # mid-stream, BaseHTTPMiddleware.call_next raises "No response
    # returned." (see encode/starlette#1925). Pure ASGI middleware
    # sidesteps that by forwarding ASGI events as they happen — we observe
    # the status code via the http.response.start event and run the
    # invalidation after http.response.body (more_body=False) completes.
    app.add_middleware(SnapshotInvalidationMiddleware)

    # NOTE: friendly 422 rewriting for the removed `audio_quality` field is
    # done INLINE in autocue/serve/routes.py :: _validate_download_body() so
    # the rest of the API keeps the stock FastAPI 422 behavior unchanged.
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
