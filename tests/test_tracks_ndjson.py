"""TASK-025 — NDJSON streaming response for /api/tracks."""
from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock

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


def _client_with_snapshot(items, mtime: float):
    app = create_app()
    db = MagicMock()
    db._db_dir = "/tmp"
    app.state.db = db
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = {"mtime": mtime, "payload": items}
    return TestClient(app)


def test_ndjson_returns_one_object_per_line(monkeypatch):
    items = [_track_item(i) for i in range(1, 4)]
    client = _client_with_snapshot(items, mtime=100.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)

    r = client.get("/api/tracks", headers={"Accept": "application/x-ndjson"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    assert len(lines) == 3
    decoded = [json.loads(ln) for ln in lines]
    assert decoded[0]["title"] == "Track 1"


def test_ndjson_preserves_etag_header(monkeypatch):
    from autocue.cache import SCHEMA_VERSION
    items = [_track_item(1)]
    client = _client_with_snapshot(items, mtime=200.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 200.0)
    r = client.get("/api/tracks", headers={"Accept": "application/x-ndjson"})
    assert r.headers.get("etag") == f'"200-v{SCHEMA_VERSION}"'


def test_ndjson_returns_x_total_count(monkeypatch):
    items = [_track_item(i) for i in range(1, 6)]
    client = _client_with_snapshot(items, mtime=100.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)
    r = client.get("/api/tracks", headers={"Accept": "application/x-ndjson"})
    assert r.headers.get("x-total-count") == "5"


def test_ndjson_offset_limit_applied(monkeypatch):
    items = [_track_item(i) for i in range(1, 11)]
    client = _client_with_snapshot(items, mtime=100.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)
    r = client.get(
        "/api/tracks?offset=4&limit=3",
        headers={"Accept": "application/x-ndjson"},
    )
    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    assert len(lines) == 3
    assert json.loads(lines[0])["title"] == "Track 5"


def test_default_json_response_when_accept_unset(monkeypatch):
    """Without Accept: application/x-ndjson, default to JSON array (back-compat)."""
    items = [_track_item(1)]
    client = _client_with_snapshot(items, mtime=100.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)
    r = client.get("/api/tracks")
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert isinstance(body, list)
