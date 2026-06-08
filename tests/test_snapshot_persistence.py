"""TASK-022 + TASK-026 + TASK-048 — snapshot persistence + invalidation middleware + perf marker."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autocue.cache import CacheStore
from autocue.serve.app import create_app
from autocue.serve.schemas import TrackItem


def _track_item(track_id: int) -> TrackItem:
    return TrackItem(
        id=track_id,
        title=f"Track {track_id}",
        artist="DJ",
        album="",
        bpm=128.0,
        duration=300.0,
        has_phrase=False,
        has_beats=True,
        existing_hot_cues=0,
    )


# ── TASK-022 — persist + hydrate snapshot ────────────────────────────────

def test_snapshot_persists_to_cachestore_on_write_through(monkeypatch):
    app = create_app()
    db = MagicMock()
    db._db_dir = "/tmp"
    app.state.db = db
    cache = CacheStore.open_memory()
    app.state.cache_store = cache
    app.state.tracks_snapshot = None
    app.state.tracks_snapshot_lock = threading.Lock()
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)
    # Stub the SQL path so it returns a small library.
    items = [_track_item(i) for i in range(1, 4)]
    content_q = MagicMock()
    content_q.count.return_value = 3
    content_q.offset.return_value = content_q
    content_q.limit.return_value = content_q
    content_q.filter.return_value = content_q
    content_q.order_by.return_value = content_q
    content_q.outerjoin.return_value = content_q
    content_q.all.return_value = [MagicMock() for _ in range(3)]
    db.get_content.return_value = content_q
    db.query.return_value.all.return_value = []
    db.query.return_value.filter_by.return_value.first.return_value = None
    # Stub _to_item to skip the heavy mapping.
    with patch("autocue.serve.routes._to_item", side_effect=lambda *a, **kw: items.pop(0)):
        client = TestClient(app)
        r = client.get("/api/tracks")

    assert r.status_code == 200
    # Snapshot now persisted to CacheStore.
    blob = cache.get_tracks_snapshot(expected_master_db_mtime=100.0)
    assert blob is not None
    rows = cache.ungzip_json(blob)
    assert len(rows) == 3
    cache.close()


# ── TASK-026 — middleware invalidates snapshot on 2xx mutating call ──────

def test_middleware_invalidates_after_successful_post():
    """Build a minimal app fragment with only the middleware + a stub POST
    so the static-files mount doesn't shadow the test route."""
    from fastapi import FastAPI
    from autocue.serve.routes import _invalidate_tracks_snapshot
    app = FastAPI()
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = {"mtime": 100.0, "payload": [_track_item(1)]}

    @app.middleware("http")
    async def _hook(request, call_next):
        response = await call_next(request)
        method = request.method.upper()
        path = request.url.path
        if (
            method in ("POST", "PUT", "DELETE")
            and path.startswith("/api/")
            and 200 <= response.status_code < 300
        ):
            _invalidate_tracks_snapshot(request.app)
        return response

    @app.post("/api/_test_mutating")
    def _ep():
        return {"ok": True}

    client = TestClient(app)
    r = client.post("/api/_test_mutating")
    assert r.status_code == 200
    assert app.state.tracks_snapshot is None


def test_middleware_does_not_invalidate_on_get():
    """GET requests must not invalidate the snapshot — they're read-only."""
    app = create_app()
    app.state.tracks_snapshot_lock = threading.Lock()
    snapshot = {"mtime": 100.0, "payload": [_track_item(1)]}
    app.state.tracks_snapshot = snapshot
    app.state.db = MagicMock()
    client = TestClient(app)
    r = client.get("/api/perf/recent")  # returns 404 in disabled mode but it's a GET
    # Whatever the status code, the snapshot must still be present.
    assert app.state.tracks_snapshot is snapshot


def _stub_middleware_app(routes_fn):
    from fastapi import FastAPI
    from autocue.serve.routes import _invalidate_tracks_snapshot
    app = FastAPI()
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = {"mtime": 100.0, "payload": [_track_item(1)]}

    @app.middleware("http")
    async def _hook(request, call_next):
        response = await call_next(request)
        method = request.method.upper()
        path = request.url.path
        if (
            method in ("POST", "PUT", "DELETE")
            and path.startswith("/api/")
            and 200 <= response.status_code < 300
        ):
            _invalidate_tracks_snapshot(request.app)
        return response

    routes_fn(app)
    return app


def test_middleware_does_not_invalidate_on_non_api_path():
    def _add(app):
        @app.post("/some-other-endpoint")
        def _e():
            return {"ok": True}
    app = _stub_middleware_app(_add)
    snapshot = app.state.tracks_snapshot
    client = TestClient(app)
    client.post("/some-other-endpoint")
    assert app.state.tracks_snapshot is snapshot


def test_middleware_does_not_invalidate_on_5xx():
    from fastapi import HTTPException
    def _add(app):
        @app.post("/api/_test_fail")
        def _e():
            raise HTTPException(500, "boom")
    app = _stub_middleware_app(_add)
    snapshot = app.state.tracks_snapshot
    client = TestClient(app)
    client.post("/api/_test_fail")
    assert app.state.tracks_snapshot is snapshot


# ── issue #115 — pure-ASGI middleware survives StreamingResponse aborts ──

def _asgi_middleware_app():
    """Build a minimal app wired through the real SnapshotInvalidationMiddleware
    (the pure-ASGI version from autocue.serve.app), so we exercise the actual
    production middleware path, not an inline stub."""
    from fastapi import FastAPI
    from autocue.serve.app import SnapshotInvalidationMiddleware
    app = FastAPI()
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = {"mtime": 100.0, "payload": [_track_item(1)]}
    app.add_middleware(SnapshotInvalidationMiddleware)
    return app


def test_pure_asgi_middleware_invalidates_after_successful_post():
    """Regression coverage for the real production middleware path."""
    app = _asgi_middleware_app()

    @app.post("/api/_ok")
    def _ep():
        return {"ok": True}

    client = TestClient(app)
    r = client.post("/api/_ok")
    assert r.status_code == 200
    assert app.state.tracks_snapshot is None


def test_pure_asgi_middleware_does_not_invalidate_on_get():
    app = _asgi_middleware_app()

    @app.get("/api/_ping")
    def _ep():
        return {"ok": True}

    snapshot = app.state.tracks_snapshot
    client = TestClient(app)
    r = client.get("/api/_ping")
    assert r.status_code == 200
    assert app.state.tracks_snapshot is snapshot


def test_pure_asgi_middleware_skips_non_api_path():
    app = _asgi_middleware_app()

    @app.post("/elsewhere")
    def _ep():
        return {"ok": True}

    snapshot = app.state.tracks_snapshot
    client = TestClient(app)
    client.post("/elsewhere")
    assert app.state.tracks_snapshot is snapshot


def test_pure_asgi_middleware_skips_on_5xx():
    from fastapi import HTTPException
    app = _asgi_middleware_app()

    @app.post("/api/_boom")
    def _ep():
        raise HTTPException(500, "boom")

    snapshot = app.state.tracks_snapshot
    client = TestClient(app)
    client.post("/api/_boom")
    assert app.state.tracks_snapshot is snapshot


def test_pure_asgi_middleware_streaming_response_aborts_without_no_response_error():
    """Regression for issue #115. With the old @app.middleware("http") wrapper
    (BaseHTTPMiddleware), a StreamingResponse whose generator raises mid-stream
    surfaces as ``RuntimeError: No response returned`` at the await of
    ``call_next``. With the pure-ASGI middleware, the start event is forwarded
    before the generator raises, so the client sees a normal response and the
    error propagates as the exception that the generator raised — never as
    "No response returned".
    """
    from fastapi.responses import StreamingResponse

    app = _asgi_middleware_app()

    def _gen():
        yield b"first-chunk\n"
        raise RuntimeError("body generator exploded")

    @app.post("/api/_stream")
    def _ep():
        return StreamingResponse(_gen(), media_type="text/plain")

    client = TestClient(app)

    # The key invariant: whatever happens, the failure mode must NOT be the
    # specific "No response returned" RuntimeError that BaseHTTPMiddleware
    # raised. Either we get a normal-looking response (because the start
    # event landed first) or we get the underlying generator error — but
    # never the false-positive "No response returned".
    caught = None
    try:
        r = client.post("/api/_stream")
        # If we got a response, the status_code observation already happened
        # at http.response.start (200), so invalidation runs even though the
        # body generator later raised — that matches what a real client would
        # see (a 200 with a truncated body).
        assert r.status_code == 200
    except RuntimeError as e:
        caught = str(e)
        assert "No response returned" not in caught, (
            "issue #115 regressed: BaseHTTPMiddleware-style "
            "'No response returned' surfaced from a streaming response"
        )


def test_pure_asgi_middleware_passes_through_non_http_scopes():
    """Lifespan / websocket scopes must be forwarded untouched — the
    middleware only short-circuits for scope['type'] == 'http'."""
    from autocue.serve.app import SnapshotInvalidationMiddleware

    seen = []

    async def downstream(scope, receive, send):
        seen.append(scope["type"])

    mw = SnapshotInvalidationMiddleware(downstream)

    async def _noop_receive():
        return {"type": "lifespan.startup"}

    async def _noop_send(_msg):
        return None

    import asyncio
    asyncio.run(mw({"type": "lifespan"}, _noop_receive, _noop_send))
    assert seen == ["lifespan"]


# ── TASK-048 — perf marker gating ────────────────────────────────────────

@pytest.mark.perf
def test_marker_perf_is_registered_and_skipped_by_default():
    """This test is itself marked @pytest.mark.perf — should skip unless RUN_PERF=1."""
    assert False, "this should never run without RUN_PERF=1"
