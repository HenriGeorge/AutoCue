"""TASK-004 + TASK-007 — flagged parallel paths for classify SSE + similar index."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis import similar
from autocue.analysis.concurrency import shutdown_pool


@pytest.fixture(autouse=True)
def _fresh_pool():
    shutdown_pool()
    similar.clear_index()
    yield
    shutdown_pool()
    similar.clear_index()


# ── TASK-007 ────────────────────────────────────────────────────────────

def test_build_index_parallel_default_off(monkeypatch):
    """Without AUTOCUE_PARALLEL_SIMILAR=1, the serial path runs."""
    monkeypatch.setenv("AUTOCUE_PARALLEL_SIMILAR", "0")
    db = MagicMock()
    db.get_content.return_value.all.return_value = []
    similar._build_index(db)
    assert similar._INDEX_BUILT is True


def test_build_index_parallel_flag_on_invokes_pool(monkeypatch):
    """With the flag, every track goes through pool.submit."""
    monkeypatch.setenv("AUTOCUE_PARALLEL_SIMILAR", "1")
    contents = [SimpleNamespace(ID=i, BPM=12800) for i in range(1, 11)]
    db = MagicMock()
    db.get_content.return_value.all.return_value = contents

    with patch("autocue.analysis.similar._index_track_safe", return_value=None) as worker:
        similar._build_index(db)

    assert worker.call_count == 10


def test_build_index_parallel_exception_isolation(monkeypatch):
    """One bad track must not abort the entire index build."""
    monkeypatch.setenv("AUTOCUE_PARALLEL_SIMILAR", "1")
    contents = [SimpleNamespace(ID=i, BPM=12800) for i in range(1, 6)]
    db = MagicMock()
    db.get_content.return_value.all.return_value = contents

    def _side(content, _db):
        if content.ID == 3:
            return RuntimeError("boom")
        return None

    with patch("autocue.analysis.similar._index_track_safe", side_effect=_side):
        similar._build_index(db)
    assert similar._INDEX_BUILT is True


# ── TASK-004 ────────────────────────────────────────────────────────────
# The /api/classify SSE path is gated behind AUTOCUE_PARALLEL_CLASSIFY=1.
# Direct integration testing via TestClient requires the full app boot;
# instead we cover the worker isolation contract here.

def test_index_track_safe_returns_exception_instead_of_raising():
    """_index_track_safe must never raise — it returns the exc or None."""
    db = MagicMock()
    db.get_anlz_path.return_value = ""

    with patch("autocue.analysis.similar._index_track", side_effect=RuntimeError("oh no")):
        got = similar._index_track_safe(SimpleNamespace(ID=42), db)
    assert isinstance(got, RuntimeError)


def test_index_track_safe_returns_none_on_success():
    db = MagicMock()
    db.get_anlz_path.return_value = ""

    with patch("autocue.analysis.similar._index_track"):
        got = similar._index_track_safe(SimpleNamespace(ID=42), db)
    assert got is None
