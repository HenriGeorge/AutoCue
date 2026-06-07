"""TASK-021 + TASK-023 — /api/tracks snapshot + ETag/304."""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

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


def _client_with_snapshot(snapshot_payload, mtime: float):
    app = create_app()
    db = MagicMock()
    db._db_dir = "/tmp"
    app.state.db = db
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = {"mtime": mtime, "payload": snapshot_payload}
    return TestClient(app), db


def test_snapshot_hit_skips_sql(monkeypatch):
    items = [_track_item(i) for i in range(1, 4)]
    client, db = _client_with_snapshot(items, mtime=100.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)

    # Spy on db.get_content to verify it's NOT called for the fast path.
    db.get_content = MagicMock()
    r = client.get("/api/tracks")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 3
    assert data[0]["title"] == "Track 1"
    db.get_content.assert_not_called()


def test_snapshot_mtime_mismatch_falls_through(monkeypatch):
    items = [_track_item(1)]
    client, db = _client_with_snapshot(items, mtime=100.0)
    # mtime changed → snapshot stale → fall through.
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 200.0)
    content_q = MagicMock()
    content_q.count.return_value = 0
    content_q.offset.return_value = content_q
    content_q.limit.return_value = content_q
    content_q.filter.return_value = content_q
    content_q.order_by.return_value = content_q
    content_q.all.return_value = []
    db.get_content.return_value = content_q
    db.query.return_value.all.return_value = []

    r = client.get("/api/tracks")
    # Falls through to SQL — at least db.get_content called.
    assert db.get_content.called


def test_etag_returns_304_on_match(monkeypatch):
    items = [_track_item(1)]
    client, db = _client_with_snapshot(items, mtime=100.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)
    r = client.get("/api/tracks", headers={"If-None-Match": '"100"'})
    assert r.status_code == 304
    assert r.headers.get("etag") == '"100"'


def test_etag_emitted_on_response(monkeypatch):
    items = [_track_item(1)]
    client, db = _client_with_snapshot(items, mtime=200.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 200.0)
    r = client.get("/api/tracks")
    assert r.status_code == 200
    assert r.headers.get("etag") == '"200"'


def test_etag_mismatch_returns_full_body(monkeypatch):
    items = [_track_item(1)]
    client, db = _client_with_snapshot(items, mtime=100.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)
    r = client.get("/api/tracks", headers={"If-None-Match": '"99"'})
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_snapshot_respects_offset_limit(monkeypatch):
    items = [_track_item(i) for i in range(1, 11)]  # 10 tracks
    client, db = _client_with_snapshot(items, mtime=100.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)
    r = client.get("/api/tracks?offset=2&limit=3")
    data = r.json()
    assert len(data) == 3
    assert data[0]["title"] == "Track 3"
    # X-Total-Count reflects pre-slice total.
    assert r.headers.get("x-total-count") == "10"


def test_invalidate_helper_clears_snapshot():
    from autocue.serve.routes import _invalidate_tracks_snapshot
    app = create_app()
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = {"mtime": 100.0, "payload": [_track_item(1)]}
    _invalidate_tracks_snapshot(app)
    assert app.state.tracks_snapshot is None


def test_non_default_sort_bypasses_snapshot(monkeypatch):
    """sort_by != 'title' falls through to SQL even with a valid snapshot."""
    items = [_track_item(1)]
    client, db = _client_with_snapshot(items, mtime=100.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)
    content_q = MagicMock()
    content_q.count.return_value = 0
    content_q.filter.return_value = content_q
    content_q.order_by.return_value = content_q
    content_q.offset.return_value = content_q
    content_q.limit.return_value = content_q
    content_q.all.return_value = []
    content_q.outerjoin.return_value = content_q
    db.get_content.return_value = content_q
    db.query.return_value.all.return_value = []

    r = client.get("/api/tracks?sort_by=bpm")
    assert db.get_content.called
