"""Regression test for the Discover stats response shape (UX audit Issue 4).

Frontend `_formatStatsPercent(n)` does `Math.round(n * 100)`. If backend
returns raw counts (n=11), the UI renders "1100%". Lock the contract:
`novelty_share` MUST be ratios in [0, 1], and `top_labels` /
`top_artists` MUST carry `count` (not `plays`).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fastapi import FastAPI


def _make_app(tmp_path, monkeypatch):
    """Minimal FastAPI app — no lifespan so the heavy similar-index
    pre-warm doesn't run during these focused stats tests."""
    monkeypatch.setenv("AUTOCUE_DISCOVER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DISCOGS_TOKEN", "test-token")
    from autocue.serve.routes import router
    app = FastAPI()
    app.include_router(router)
    return app


def test_novelty_share_returns_ratios_not_counts(tmp_path, monkeypatch):
    """novelty_share must sum to 1.0 (or 0.0 if no scans yet)."""
    app = _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        resp = client.get("/api/discover/stats")
    assert resp.status_code == 200
    body = resp.json()
    share = body["novelty_share"]
    total = sum(share.values())
    # Either no scans yet (0.0) or sums to 1.0 within float tolerance.
    assert abs(total - 1.0) < 0.0001 or total == 0.0, (
        f"novelty_share should be ratios summing to 1.0 (or 0), got {share} (sum={total})"
    )
    # No individual ratio above 1.0.
    for k, v in share.items():
        assert 0.0 <= v <= 1.0, f"novelty_share[{k!r}] = {v}, must be in [0, 1]"


def test_top_artists_uses_count_key(tmp_path, monkeypatch):
    """The frontend reads a.count; the backend must return `count` not `plays`."""
    app = _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        # Pre-seed a save so top_artists has an entry.
        client.post("/api/discover/save", json={
            "release_key": "k1",
            "release_id": 1,
            "artist": "Test Artist",
            "title": "Test Album",
            "label": "Test Label",
        })
        resp = client.get("/api/discover/stats")
    assert resp.status_code == 200
    artists = resp.json()["top_artists"]
    if artists:
        row = artists[0]
        assert "count" in row, f"top_artists row missing 'count' key: {row}"
        assert "plays" not in row, f"top_artists row still has stale 'plays' key: {row}"


def test_top_labels_uses_count_key(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        client.post("/api/discover/save", json={
            "release_key": "k2",
            "release_id": 2,
            "artist": "A",
            "title": "T",
            "label": "Specific Label",
        })
        resp = client.get("/api/discover/stats")
    labels = resp.json()["top_labels"]
    if labels:
        row = labels[0]
        assert "count" in row
        assert "plays" not in row
