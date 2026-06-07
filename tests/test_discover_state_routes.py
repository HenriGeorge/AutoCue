"""Tests for the Discover v2 state-CRUD / follow / export-import / stats
endpoints — T-016 + T-017 + T-019 + T-020.

Uses the same TestClient pattern as test_discover_routes.py.
"""

from __future__ import annotations

import gzip
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocue.analysis import discogs as discogs_client
from autocue.analysis.discover.store import DiscoverStore


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCUE_DISCOVER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
    monkeypatch.setattr(discogs_client, "_acquire_token", lambda: None)
    discogs_client.reset_rate_limit_state()

    from autocue.serve.routes import router

    app = FastAPI()
    app.state.db = _FakeRekordboxDB()
    app.state.ro_db = app.state.db
    app.state.discover_store = DiscoverStore(db_path=tmp_path / "data" / "discover.db")
    app.include_router(router)
    yield app
    if app.state.discover_store is not None:
        app.state.discover_store.close()


@pytest.fixture
def client(app):
    return TestClient(app)


class _FakeRekordboxDB:
    def query(self, _model):
        return _EmptyQuery()


class _EmptyQuery:
    def all(self):
        return []


# --------------------------------------------------------------------------- #
# State CRUD round-trips
# --------------------------------------------------------------------------- #

class TestStateCRUD:
    def test_save_then_unsave(self, client):
        client.post("/api/discover/save", json={
            "release_key": "madvillain|||madvillainy",
            "release_id": 11125, "artist": "Madvillain", "title": "Madvillainy",
            "label": "Stones Throw",
        })
        items = client.get("/api/discover/saved").json()["items"]
        assert any(r["release_key"] == "madvillain|||madvillainy" for r in items)
        client.post("/api/discover/unsave", json={"release_key": "madvillain|||madvillainy"})
        items = client.get("/api/discover/saved").json()["items"]
        assert items == []

    def test_dismiss_then_undismiss(self, client):
        client.post("/api/discover/dismiss", json={
            "release_key": "k", "release_id": 1, "artist": "A", "title": "T",
            "reason": "not for me",
        })
        items = client.get("/api/discover/dismissed").json()["items"]
        assert items[0]["reason"] == "not for me"
        client.post("/api/discover/undismiss", json={"release_key": "k"})
        assert client.get("/api/discover/dismissed").json()["items"] == []

    def test_snooze_duration_1m(self, client):
        resp = client.post("/api/discover/snooze", json={
            "release_key": "k", "duration": "1m",
            "release_id": 1, "artist": "A", "title": "T",
        })
        assert resp.status_code == 200
        until = resp.json()["until_date"]
        # 30 days out — verify it's at least 25 days in the future.
        from datetime import datetime, timezone, timedelta
        target = datetime.fromisoformat(until)
        delta = target - datetime.now(timezone.utc)
        assert delta > timedelta(days=25)

    def test_snooze_bad_duration_rejected(self, client):
        resp = client.post("/api/discover/snooze", json={
            "release_key": "k", "duration": "10years",
        })
        assert resp.status_code == 400

    def test_snoozed_list_excludes_expired_by_default(self, client, app):
        # Insert one past + one future snooze directly via the store.
        from datetime import datetime, timedelta, timezone
        future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        app.state.discover_store.snooze(
            release_key="future", until_date=future, artist="A", title="T",
        )
        app.state.discover_store.snooze(
            release_key="past", until_date=past, artist="A", title="T",
        )
        active = client.get("/api/discover/snoozed").json()["items"]
        assert {r["release_key"] for r in active} == {"future"}

        with_resurfaced = client.get(
            "/api/discover/snoozed?include_resurfaced=true",
        ).json()["items"]
        assert {r["release_key"] for r in with_resurfaced} == {"future", "past"}


class TestBlockLists:
    def test_block_artist_round_trip(self, client):
        client.post("/api/discover/block-artist", json={
            "discogs_artist_id": 42, "name": "Anjunabeats",
        })
        items = client.get("/api/discover/blocked-artists").json()["items"]
        assert items[0]["name"] == "Anjunabeats"
        client.post("/api/discover/unblock-artist", json={"discogs_artist_id": 42})
        assert client.get("/api/discover/blocked-artists").json()["items"] == []

    def test_block_label_round_trip(self, client):
        client.post("/api/discover/block-label", json={
            "discogs_label_id": 99, "name": "BoringLabel",
        })
        assert client.get("/api/discover/blocked-labels").json()["items"][0]["name"] == "BoringLabel"


# --------------------------------------------------------------------------- #
# Follow labels
# --------------------------------------------------------------------------- #

class TestFollowLabels:
    def test_follow_unfollow_round_trip(self, client):
        client.post("/api/discover/labels/follow", json={"label_id": 1, "name": "Stones Throw"})
        items = client.get("/api/discover/labels").json()["items"]
        assert items[0]["name"] == "Stones Throw"
        client.post("/api/discover/labels/unfollow", json={"label_id": 1})
        assert client.get("/api/discover/labels").json()["items"] == []

    def test_label_search_passes_through_discogs(self, client):
        with patch("autocue.analysis.discogs.search_labels", return_value=[
            {"id": 1, "name": "Stones Throw", "thumb": "", "resource_url": ""},
        ]) as m:
            resp = client.get("/api/discover/labels/search?q=Stones")
        assert resp.status_code == 200
        assert resp.json()["items"][0]["name"] == "Stones Throw"
        m.assert_called_once()


# --------------------------------------------------------------------------- #
# Export / Import
# --------------------------------------------------------------------------- #

class TestExportImport:
    def test_export_returns_valid_gzip_sqlite_blob(self, client):
        # Save something so the round-trip carries non-empty state.
        client.post("/api/discover/save", json={
            "release_key": "k", "release_id": 1, "artist": "A", "title": "T",
        })
        resp = client.get("/api/discover/state/export")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/gzip")
        decompressed = gzip.decompress(resp.content)
        assert decompressed.startswith(b"SQLite format 3\x00")

    def test_import_round_trip_preserves_saved_state(self, client):
        # Round 1: save in the live store, export, unsave (clear), import the export, verify saved came back.
        client.post("/api/discover/save", json={
            "release_key": "k", "release_id": 1, "artist": "A", "title": "T",
        })
        export = client.get("/api/discover/state/export")
        archive = export.content

        # Clear state.
        client.post("/api/discover/unsave", json={"release_key": "k"})
        assert client.get("/api/discover/saved").json()["items"] == []

        # Import — POST body is the raw .gz.
        resp = client.post("/api/discover/state/import", content=archive)
        assert resp.status_code == 200
        body = resp.json()
        assert body["restored"] is True
        # The 'saved' table the export carries had 1 row; after import we should see it again.
        assert body["after"]["saved"] == 1
        items = client.get("/api/discover/saved").json()["items"]
        assert {r["release_key"] for r in items} == {"k"}

    def test_import_rejects_non_sqlite_body(self, client):
        bad = gzip.compress(b"not a sqlite database")
        resp = client.post("/api/discover/state/import", content=bad)
        assert resp.status_code == 400
        assert "not a SQLite database" in resp.json()["detail"]

    def test_import_rejects_non_gzip_body(self, client):
        resp = client.post("/api/discover/state/import", content=b"not gzip")
        assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #

class TestStats:
    def test_empty_db_returns_zeros(self, client):
        resp = client.get("/api/discover/stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_scans"] == 0
        assert body["saves_per_scan"] is None
        assert body["saved_count"] == 0
        assert body["followed_count"] == 0
        assert body["novelty_share"] == {"ok": 0, "partial": 0, "sparse_adjacency": 0}

    def test_stats_count_scans_and_saves(self, client, app):
        # Seed a scan with status='ok' + a save inside its window.
        scan_id = app.state.discover_store.start_scan(
            feeders=["artist"], started_at="2026-06-07T10:00:00+00:00",
        )
        app.state.discover_store.finish_scan(
            scan_id, status="ok",
            finished_at="2026-06-07T10:01:00+00:00",
            duration_ms=5000, novelty_status="ok",
        )
        app.state.discover_store.save(
            release_key="k", release_id=1, artist="A", title="T", label="L",
            saved_at="2026-06-07T10:02:00+00:00",
        )
        body = client.get("/api/discover/stats").json()
        assert body["total_scans"] == 1
        assert body["avg_duration_ms"] == 5000
        assert body["saves_per_scan"] == 1
        # novelty_share is now a ratio (0..1), not a raw count — UX audit
        # Issue 4 fix. With 1 scan of status="ok", ratio is 1.0.
        assert body["novelty_share"]["ok"] == 1.0
        # top_labels / top_artists now expose `count` (not `plays`) — the
        # frontend reads `.count` and was rendering "(undefined)" before.
        assert body["top_labels"] == [{"name": "L", "count": 1}]
        assert body["top_artists"] == [{"name": "A", "count": 1}]
        assert body["saved_count"] == 1
