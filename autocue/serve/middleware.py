"""Pure-ASGI middleware for the AutoCue local server.

Pulled out of ``app.py`` because the original ``@app.middleware("http")``
implementation wrapped the snapshot-invalidation hook in starlette's
``BaseHTTPMiddleware``, which is documented to be incompatible with
``StreamingResponse``: if the response generator aborts or never yields,
``BaseHTTPMiddleware.call_next`` raises ``RuntimeError("No response returned.")``
and the request hangs at the client. See issue #115 / encode/starlette#1925.

The pure-ASGI version below does not buffer the response — it observes the
status code from the first ``http.response.start`` message and forwards every
ASGI message through unchanged. After the response stream completes, it fires
``_invalidate_tracks_snapshot(app)`` iff the original request was a mutating
``POST/PUT/DELETE`` to ``/api/*`` AND the captured status was 2xx.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

ASGIScope = dict[str, Any]
ASGIMessage = dict[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]


class SnapshotInvalidationMiddleware:
    """Invalidate the in-memory ``/api/tracks`` snapshot after 2xx mutations.

    Tracks state for ONE request at a time per instance call — instances are
    created fresh by Starlette for each request, so the captured status code
    never leaks between requests.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(
        self,
        scope: ASGIScope,
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        path = scope.get("path", "")
        is_mutating_api = (
            method in ("POST", "PUT", "DELETE") and path.startswith("/api/")
        )

        # Fast path: GETs and non-/api/* requests don't need any wrapping.
        if not is_mutating_api:
            await self.app(scope, receive, send)
            return

        status_code = {"value": 0}

        async def send_wrapper(message: ASGIMessage) -> None:
            if message.get("type") == "http.response.start":
                status_code["value"] = int(message.get("status", 0))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # Only invalidate on a 2xx response — and never let invalidation
            # bookkeeping break the response itself.
            if 200 <= status_code["value"] < 300:
                try:
                    # Local import avoids a top-level circular dep
                    # (routes -> deps -> app at startup).
                    from .routes import _invalidate_tracks_snapshot

                    # The ASGI scope exposes the FastAPI app instance.
                    app = scope.get("app")
                    if app is not None:
                        _invalidate_tracks_snapshot(app)
                except Exception:
                    # Defensive: never crash the response on bookkeeping.
                    pass
