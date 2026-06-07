"""Tests for the Discover v2 endpoints in serve/routes.py.

Covers T-015 (SSE feed) + T-018 (release detail / scan status / cancel) +
T-023 (token validation). Built on FastAPI's TestClient with a mock DB +
the real DiscoverStore against a tmp_path SQLite file.

The SSE test consumes the stream and asserts on event structure rather than
mocking the whole orchestrator — that gives us real end-to-end coverage of
the wrapper + orchestrator wiring with mocked Discogs HTTP.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocue.analysis import discogs as discogs_client
from autocue.analysis.discover.store import DiscoverStore
from autocue.serve import deps as deps_mod


# --------------------------------------------------------------------------- #
# App factory: build a minimal FastAPI app with just the routes we need.
# --------------------------------------------------------------------------- #

@pytest.fixture
def app(tmp_path, monkeypatch):
    """Spin up a TestClient-friendly FastAPI instance with a stub DB + a real
    tmp-path DiscoverStore."""
    monkeypatch.setenv("AUTOCUE_DISCOVER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("DISCOGS_TOKEN", "test-token")

    # Reset deps-level token cache between tests.
    deps_mod.set_cached_token_valid(False)
    monkeypatch.setattr(discogs_client, "_acquire_token", lambda: None)
    discogs_client.reset_rate_limit_state()

    from autocue.serve.routes import router

    app = FastAPI()
    # Stub Rekordbox DB so get_db doesn't 503. Anything truthy works since
    # the discover endpoints we test here either don't touch it directly or
    # we patch the surfaces they DO touch.
    app.state.db = _FakeRekordboxDB()
    app.state.ro_db = app.state.db
    app.state.discover_store = DiscoverStore(db_path=tmp_path / "data" / "discover.db")
    app.include_router(router)
    yield app
    app.state.discover_store.close()


@pytest.fixture
def client(app):
    return TestClient(app)


class _FakeRekordboxDB:
    """Tiny duck-typed stand-in for pyrekordbox.Rekordbox6Database — just
    enough for build_taste_vector to walk and yield nothing."""

    def query(self, _model):
        return _EmptyQuery()


class _EmptyQuery:
    def all(self):
        return []


# --------------------------------------------------------------------------- #
# /api/discover/token-status (T-023)
# --------------------------------------------------------------------------- #

class TestTokenStatus:
    def test_valid_token_returns_true(self, client):
        with patch.object(discogs_client, "validate_token", return_value=True):
            resp = client.get("/api/discover/token-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["cached"] is False  # first call doesn't come from cache

    def test_cached_on_second_call(self, client):
        with patch.object(discogs_client, "validate_token", return_value=True) as m:
            client.get("/api/discover/token-status")
            r2 = client.get("/api/discover/token-status")
        # The 2nd call returns the cached True without hitting validate_token again.
        assert m.call_count == 1
        assert r2.json()["cached"] is True

    def test_invalid_token_not_cached(self, client):
        """False results MUST NOT be cached — a transient 5xx shouldn't lock
        the user out for 1h."""
        with patch.object(discogs_client, "validate_token", return_value=False) as m:
            client.get("/api/discover/token-status")
            client.get("/api/discover/token-status")
        assert m.call_count == 2

    def test_empty_token_short_circuits_to_false(self, client, monkeypatch):
        monkeypatch.setenv("DISCOGS_TOKEN", "")
        with patch.object(discogs_client, "validate_token") as m:
            resp = client.get("/api/discover/token-status")
        # Never even tried to call validate_token.
        m.assert_not_called()
        assert resp.json()["valid"] is False


# --------------------------------------------------------------------------- #
# /api/discover/feed/status + /cancel  (T-018)
# --------------------------------------------------------------------------- #

class TestFeedStatusAndCancel:
    def test_status_when_no_scan_running(self, client):
        resp = client.get("/api/discover/feed/status")
        assert resp.status_code == 200
        assert resp.json() == {
            "running": False, "scan_id": None, "started_at": None,
            "feeders": None, "novelty_strategy": None,
        }

    def test_status_reflects_running_scan(self, client, app):
        scan_id = app.state.discover_store.start_scan(feeders=["artist", "label"])
        resp = client.get("/api/discover/feed/status")
        body = resp.json()
        assert body["running"] is True
        assert body["scan_id"] == scan_id
        assert body["feeders"] == "artist,label"

    def test_cancel_marks_running_scan_cancelled(self, client, app):
        scan_id = app.state.discover_store.start_scan(feeders=["artist"])
        resp = client.post("/api/discover/feed/cancel")
        body = resp.json()
        assert body["was_running"] is True
        assert body["cancelled_scan_id"] == scan_id

        # Status now reflects no running scan.
        assert client.get("/api/discover/feed/status").json()["running"] is False

    def test_cancel_when_nothing_running_is_a_no_op(self, client):
        resp = client.post("/api/discover/feed/cancel")
        assert resp.status_code == 200
        assert resp.json() == {"was_running": False, "cancelled_scan_id": None}


# --------------------------------------------------------------------------- #
# /api/discover/releases/{release_id}  (T-018)
# --------------------------------------------------------------------------- #

class TestReleaseDetail:
    PAYLOAD = {
        "id": 11125,
        "master_id": 99,
        "title": "Madvillainy",
        "artists": [{"name": "Madvillain"}],
        "labels": [{"name": "Stones Throw"}],
        "year": 2004,
        "country": "US",
        "formats": [{"name": "Vinyl"}],
        "genres": ["Hip Hop"],
        "styles": ["Boom Bap"],
        "tracklist": [{"position": "A1", "title": "Accordion",
                       "duration": "1:58", "type_": "track"}],
        "videos": [],
        "notes": "",
        "thumb": "/t.jpg",
        "cover_image": "/c.jpg",
    }

    def test_fresh_fetch_then_cache_hit(self, client, app):
        with patch("autocue.analysis.discogs.get_release_details") as m:
            from autocue.analysis.discogs import _extract_release_details
            m.return_value = _extract_release_details(self.PAYLOAD)
            r1 = client.get("/api/discover/releases/11125")
            r2 = client.get("/api/discover/releases/11125")
        assert r1.status_code == 200
        assert r1.json()["master_id"] == 99
        assert r1.json()["cached"] is False
        assert r2.json()["cached"] is True
        # Only one fetch — the second came from the 30-day cache.
        assert m.call_count == 1

    def test_404_when_discogs_returns_empty(self, client, app):
        with patch("autocue.analysis.discogs.get_release_details", return_value={}):
            resp = client.get("/api/discover/releases/99999")
        assert resp.status_code == 404

    def test_429_returned_as_503(self, client):
        with patch("autocue.analysis.discogs.get_release_details") as m:
            m.side_effect = discogs_client.Discogs429(retry_after=42)
            resp = client.get("/api/discover/releases/11125")
        assert resp.status_code == 503
        assert "42" in resp.json()["detail"]


# --------------------------------------------------------------------------- #
# /api/discover/feed (SSE) — T-015
# --------------------------------------------------------------------------- #

class TestDiscoverFeedSSE:
    def test_409_when_scan_already_running(self, client, app):
        # Pre-seed a running scan row to exercise the concurrent-scan guard.
        app.state.discover_store.start_scan(feeders=["artist"])
        resp = client.get("/api/discover/feed")
        assert resp.status_code == 409

    def test_empty_library_yields_done_event_with_no_releases(self, client):
        """The orchestrator should run, produce no releases (empty library),
        and emit a final 'done' event with telemetry."""
        # Mock Discogs so no real network calls happen if the orchestrator
        # tries to fetch (it won't — taste vector is empty so feeders skip).
        with patch.object(discogs_client, "search_artist_releases", return_value=[]):
            with patch.object(discogs_client, "search_label_releases", return_value=[]):
                resp = client.get("/api/discover/feed")
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        kinds = [e["event"] for e in events]
        assert "done" in kinds
        done = next(e for e in events if e["event"] == "done")
        assert done["data"]["status"] == "ok"
        assert done["data"]["releases_seen"] == 0


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _parse_sse(text: str) -> list[dict]:
    """Tiny SSE parser — returns [{event, data}, …]. Handles only what our
    endpoint emits (no multi-line data: blocks, no id: fields)."""
    events = []
    for chunk in text.split("\n\n"):
        ev = {}
        for line in chunk.splitlines():
            if line.startswith("event: "):
                ev["event"] = line[7:].strip()
            elif line.startswith("data: "):
                try:
                    ev["data"] = json.loads(line[6:])
                except json.JSONDecodeError:
                    ev["data"] = line[6:]
        if ev:
            events.append(ev)
    return events
