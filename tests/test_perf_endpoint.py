"""Tests for /api/perf/recent — TASK-045.

The endpoint is dev-only: returns 404 when AUTOCUE_PERF is unset, 200 with
recent spans + per-name p50/p95/p99 stats when enabled.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from autocue import perf
from autocue.serve.app import create_app


@pytest.fixture(autouse=True)
def _reset_perf(monkeypatch):
    perf.clear()
    monkeypatch.setattr(perf, "_enabled", False)
    monkeypatch.setattr(perf, "_sample_rate", 1.0)
    yield
    perf.clear()


def _client():
    app = create_app()
    app.state.db = MagicMock()
    return TestClient(app)


def test_returns_404_when_disabled():
    client = _client()
    r = client.get("/api/perf/recent")
    assert r.status_code == 404
    assert "AUTOCUE_PERF" in r.json()["detail"]


def test_returns_200_with_spans_when_enabled(monkeypatch):
    monkeypatch.setattr(perf, "_enabled", True)
    for _ in range(5):
        with perf.perf_span("test.work"):
            pass

    client = _client()
    r = client.get("/api/perf/recent")
    assert r.status_code == 200
    body = r.json()
    assert "spans" in body
    assert "stats" in body
    assert len(body["spans"]) == 5
    assert all("name" in s and "duration_ms" in s and "start_ts" in s for s in body["spans"])
    assert "test.work" in body["stats"]
    stat = body["stats"]["test.work"]
    assert stat["count"] == 5.0
    assert "p50" in stat
    assert "p95" in stat
    assert "p99" in stat


def test_limit_param_clamps_to_bounds(monkeypatch):
    monkeypatch.setattr(perf, "_enabled", True)
    for _ in range(20):
        with perf.perf_span("clamp.test"):
            pass
    client = _client()

    # Below floor → clamped to 1.
    r = client.get("/api/perf/recent?limit=0")
    assert r.status_code == 200
    assert len(r.json()["spans"]) == 1

    # Above ceiling → clamped to 1000 (we only have 20 in buffer).
    r = client.get("/api/perf/recent?limit=5000")
    assert r.status_code == 200
    assert len(r.json()["spans"]) == 20


def test_groups_stats_by_span_name(monkeypatch):
    monkeypatch.setattr(perf, "_enabled", True)
    for _ in range(3):
        with perf.perf_span("a.fast"):
            pass
    for _ in range(7):
        with perf.perf_span("b.slow"):
            pass
    client = _client()
    body = client.get("/api/perf/recent").json()
    assert body["stats"]["a.fast"]["count"] == 3.0
    assert body["stats"]["b.slow"]["count"] == 7.0
