"""TASK-022 + TASK-026 + TASK-048 + issue #115 — snapshot persistence +
invalidation middleware (now pure-ASGI) + perf marker."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autocue.cache import CacheStore
from autocue.serve.app import create_app
from autocue.serve.middleware import SnapshotInvalidationMiddleware
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
    so the static-files mount doesn't shadow the test route. Uses the same
    SnapshotInvalidationMiddleware class that ships in create_app(), so this
    exercises the production code path."""
    from fastapi import FastAPI
    app = FastAPI()
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = {"mtime": 100.0, "payload": [_track_item(1)]}
    app.add_middleware(SnapshotInvalidationMiddleware)

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
    app = FastAPI()
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = {"mtime": 100.0, "payload": [_track_item(1)]}
    app.add_middleware(SnapshotInvalidationMiddleware)
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


def _make_streaming_app(generator_factory):
    """Build a stub app whose POST /api/_stream returns a StreamingResponse
    backed by generator_factory(). Uses the production middleware class."""
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    app = FastAPI()
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = {"mtime": 100.0, "payload": [_track_item(1)]}
    app.add_middleware(SnapshotInvalidationMiddleware)

    @app.post("/api/_stream")
    def _ep():
        return StreamingResponse(generator_factory(), media_type="text/event-stream")

    return app


def test_streaming_response_that_aborts_does_not_raise_runtime_error():
    """Regression for issue #115. Previously, @app.middleware('http') wrapped
    the invalidation hook in BaseHTTPMiddleware, which raised
    RuntimeError('No response returned.') whenever a StreamingResponse
    generator aborted mid-stream. The pure-ASGI middleware must not.

    This test FAILS against the old BaseHTTPMiddleware implementation:
    starlette would surface the generator's RuntimeError as a 500 with a
    nested "No response returned." traceback.
    """
    def aborting_gen():
        yield b"data: first\n\n"
        raise RuntimeError("simulated generator abort mid-stream")

    app = _make_streaming_app(aborting_gen)
    client = TestClient(app)

    # The pure-ASGI middleware MUST propagate the generator's RuntimeError
    # cleanly without dressing it up as "No response returned." — and any
    # subsequent requests on the same app must still work.
    try:
        client.post("/api/_stream")
    except RuntimeError as e:
        # The original generator's RuntimeError may bubble — that's fine.
        # The critical invariant is that it's NOT the BaseHTTPMiddleware
        # "No response returned." failure mode.
        assert "No response returned" not in str(e), (
            f"middleware regressed to BaseHTTPMiddleware behavior: {e!r}"
        )

    # Boundary check: after an aborted stream, the app must still serve
    # subsequent requests (BaseHTTPMiddleware sometimes wedged the loop).
    @app.post("/api/_ok")
    def _ok():
        return {"ok": True}

    r2 = client.post("/api/_ok")
    assert r2.status_code == 200
    # And the snapshot must have been invalidated by the second 2xx mutation.
    assert app.state.tracks_snapshot is None


def test_streaming_response_completing_cleanly_invalidates_snapshot():
    """Boundary: a StreamingResponse that completes normally with a 2xx
    must still trigger snapshot invalidation. This protects the original
    TASK-026 behavior under the new middleware."""
    def good_gen():
        yield b"data: one\n\n"
        yield b"data: two\n\n"

    app = _make_streaming_app(good_gen)
    client = TestClient(app)
    r = client.post("/api/_stream")
    assert r.status_code == 200
    # Drain to ensure the response stream actually completed.
    _ = r.content
    assert app.state.tracks_snapshot is None


def test_middleware_invalidates_on_201_created_boundary():
    """Boundary: status_code == 200 is in, status_code == 299 is in,
    status_code == 300 is OUT. Uses 201 to confirm the inclusive 2xx range."""
    from fastapi import FastAPI

    app = FastAPI()
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = {"mtime": 100.0, "payload": [_track_item(1)]}
    app.add_middleware(SnapshotInvalidationMiddleware)

    @app.post("/api/_created", status_code=201)
    def _ep():
        return {"ok": True}

    client = TestClient(app)
    r = client.post("/api/_created")
    assert r.status_code == 201
    assert app.state.tracks_snapshot is None


def test_middleware_does_not_invalidate_on_300_redirect_boundary():
    """Boundary: 3xx must NOT invalidate. The original middleware used
    `200 <= response.status_code < 300`; the pure-ASGI version must
    preserve that exact half-open interval."""
    from fastapi import FastAPI
    from fastapi.responses import RedirectResponse

    app = FastAPI()
    app.state.tracks_snapshot_lock = threading.Lock()
    snapshot = {"mtime": 100.0, "payload": [_track_item(1)]}
    app.state.tracks_snapshot = snapshot
    app.add_middleware(SnapshotInvalidationMiddleware)

    @app.post("/api/_redirect")
    def _ep():
        return RedirectResponse(url="/api/_other", status_code=303)

    client = TestClient(app)
    r = client.post("/api/_redirect", follow_redirects=False)
    assert r.status_code == 303
    assert app.state.tracks_snapshot is snapshot


# ── TASK-048 — perf marker gating ────────────────────────────────────────

@pytest.mark.perf
def test_marker_perf_is_registered_and_skipped_by_default():
    """This test is itself marked @pytest.mark.perf — should skip unless RUN_PERF=1."""
    assert False, "this should never run without RUN_PERF=1"
