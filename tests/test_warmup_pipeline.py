"""TASK-027 / TASK-028 / TASK-030 — warm-up pipeline + endpoint + shutdown."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from autocue.serve.app import create_app


def _client_with_warmup_state(step="cache", done=42, total=100, finished_at=None):
    app = create_app()
    app.state.db = MagicMock()
    app.state.warmup_lock = threading.Lock()
    app.state.warmup_progress = {
        "step": step,
        "done": done,
        "total": total,
        "finished_at": finished_at,
    }
    return TestClient(app)


# ── TASK-028 ────────────────────────────────────────────────────────────

def test_warmup_endpoint_returns_progress():
    client = _client_with_warmup_state("cache", 42, 100)
    r = client.get("/api/warmup")
    assert r.status_code == 200
    body = r.json()
    assert body["step"] == "cache"
    assert body["done"] == 42
    assert body["total"] == 100
    assert body["finished_at"] is None


def test_warmup_endpoint_returns_done_state():
    client = _client_with_warmup_state("done", 1, 1, "2026-06-07T19:00:00+00:00")
    body = client.get("/api/warmup").json()
    assert body["step"] == "done"
    assert body["finished_at"] == "2026-06-07T19:00:00+00:00"


def test_warmup_endpoint_without_pipeline_returns_unknown():
    """Lifespan didn't initialize → endpoint reports 'unknown' rather than 500."""
    app = create_app()
    app.state.db = MagicMock()
    client = TestClient(app)
    r = client.get("/api/warmup")
    assert r.status_code == 200
    body = r.json()
    assert body["step"] == "unknown"


# ── TASK-027 + TASK-030 ─────────────────────────────────────────────────

def test_run_warmup_pipeline_progresses_through_steps():
    from autocue.serve.deps import _run_warmup_pipeline

    app = MagicMock()
    app.state.warmup_lock = threading.Lock()
    app.state.warmup_cancel_event = threading.Event()
    app.state.warmup_progress = {"step": "init", "done": 0, "total": 0, "finished_at": None}
    app.state.cache_store = None  # bypass cache step entirely
    db = MagicMock()
    db.get_content.return_value.all.return_value = []

    with patch("autocue.analysis.similar._build_index"):
        _run_warmup_pipeline(app, db)

    # Pipeline reached the 'done' marker.
    assert app.state.warmup_progress["step"] == "done"
    assert app.state.warmup_progress["finished_at"] is not None


def test_run_warmup_pipeline_respects_cancel_event_before_index_step():
    """Cancel after cache step → never reaches similarity index."""
    from autocue.serve.deps import _run_warmup_pipeline

    app = MagicMock()
    app.state.warmup_lock = threading.Lock()
    app.state.warmup_cancel_event = threading.Event()
    app.state.warmup_progress = {"step": "init", "done": 0, "total": 0, "finished_at": None}
    cancel = app.state.warmup_cancel_event
    cancel.set()  # cancel before pipeline starts
    app.state.cache_store = None
    db = MagicMock()
    db.get_content.return_value.all.return_value = []

    with patch("autocue.analysis.similar._build_index") as build:
        _run_warmup_pipeline(app, db)

    # Cancel landed before index step, so _build_index was never called.
    build.assert_not_called()
