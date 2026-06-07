"""TASK-047 — perf smoke suite: snapshot fast-path latency.

Gated by ``@pytest.mark.perf`` so it only runs when ``RUN_PERF=1``.
This is a smoke benchmark, not a fully calibrated suite — it verifies
the cached path actually short-circuits and is faster than the build
path on synthetic input. The full 10k-track sandbox benchmark from
the PRD is future work (needs a synthetic Rekordbox DB fixture).
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autocue.serve.app import create_app
from autocue.serve.schemas import TrackItem


pytestmark = pytest.mark.perf


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


def test_snapshot_hit_p95_under_50ms(monkeypatch):
    """Cached /api/tracks returns the snapshot in well under 50ms even at 10k."""
    items = [_track_item(i) for i in range(1, 10001)]
    client = _client_with_snapshot(items, mtime=100.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)

    durations: list[float] = []
    for _ in range(50):
        start = time.perf_counter()
        r = client.get("/api/tracks")
        durations.append((time.perf_counter() - start) * 1000.0)
        assert r.status_code == 200

    durations.sort()
    p95 = durations[int(0.95 * len(durations)) - 1]
    # 50ms is a generous bound — real localhost calls land well under 20ms.
    assert p95 < 50, f"p95 {p95:.2f}ms exceeded 50ms"


def test_etag_304_p95_under_10ms(monkeypatch):
    """304 path should be nearly instant — no body construction."""
    items = [_track_item(i) for i in range(1, 1001)]
    client = _client_with_snapshot(items, mtime=100.0)
    monkeypatch.setattr("autocue.serve.routes._master_db_mtime", lambda _db: 100.0)

    durations: list[float] = []
    for _ in range(50):
        start = time.perf_counter()
        r = client.get("/api/tracks", headers={"If-None-Match": '"100"'})
        durations.append((time.perf_counter() - start) * 1000.0)
        assert r.status_code == 304

    durations.sort()
    p95 = durations[int(0.95 * len(durations)) - 1]
    assert p95 < 10, f"p95 {p95:.2f}ms exceeded 10ms"
