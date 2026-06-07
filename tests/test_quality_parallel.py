"""TASK-003 — parallel /api/health SSE (feature-flagged behind AUTOCUE_PARALLEL_HEALTH=1).

Until TASK-008 verification lands, the parallel path is OFF by default so
the existing serial behavior is unchanged for users. These tests exercise
the flagged path directly.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis import quality
from autocue.analysis.concurrency import shutdown_pool


@pytest.fixture(autouse=True)
def _enable_flag(monkeypatch):
    monkeypatch.setenv("AUTOCUE_PARALLEL_HEALTH", "1")
    shutdown_pool()
    yield
    shutdown_pool()


def _mock_db(contents):
    db = MagicMock()
    chain = MagicMock()
    chain.all.return_value = contents
    chain.join.return_value = chain
    chain.filter.return_value = chain
    db.query.return_value = chain
    return db


def _content(track_id):
    return SimpleNamespace(ID=track_id, FolderPath=f"/x/{track_id}.mp3",
                          AnalysisDataPath="x", BPM=12800)


def test_parallel_yields_one_report_per_track():
    contents = [_content(i) for i in range(1, 11)]
    db = _mock_db(contents)
    fake = quality.TrackHealthReport(track_id=0, score=100, issues=[], fix_tier="none")

    with patch.object(quality, "check_track_health", return_value=fake):
        results = list(quality.check_library_health(db))

    assert len(results) == 10


def test_parallel_isolates_per_track_exception():
    """One bad row must not abort the scan — INTERNAL_ERROR yields and stream continues."""
    contents = [_content(i) for i in range(1, 6)]
    db = _mock_db(contents)
    fake = quality.TrackHealthReport(track_id=0, score=100, issues=[], fix_tier="none")
    call_count = {"n": 0}

    def _check(content, _db):
        call_count["n"] += 1
        if content.ID == 3:
            raise RuntimeError("boom")
        return fake

    with patch.object(quality, "check_track_health", side_effect=_check):
        results = list(quality.check_library_health(db))

    assert len(results) == 5
    errors = [r for r in results if any(i.code == "INTERNAL_ERROR" for i in r.issues)]
    assert len(errors) == 1


def test_parallel_disabled_when_flag_unset(monkeypatch):
    """Without AUTOCUE_PARALLEL_HEALTH=1, the serial path runs verbatim."""
    monkeypatch.delenv("AUTOCUE_PARALLEL_HEALTH", raising=False)
    contents = [_content(i) for i in range(1, 4)]
    db = _mock_db(contents)
    fake = quality.TrackHealthReport(track_id=0, score=100, issues=[], fix_tier="none")
    with patch.object(quality, "check_track_health", return_value=fake):
        results = list(quality.check_library_health(db))
    assert len(results) == 3
