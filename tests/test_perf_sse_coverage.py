"""Regression test for issue #110 — TASK-046 perf_span coverage on SSE endpoints.

Asserts that every SSE endpoint in PRD §4.1 emits an outer ``<endpoint>.compute``
span (and, where applicable, a per-track ``<endpoint>.write_one`` span) into the
``perf`` ring buffer when ``AUTOCUE_PERF=1`` is set.

Without the fix, only ``tracks.cached`` / ``tracks.build`` were present and this
test fails on every SSE endpoint's compute span.

The assertions are property-based (count > 0 + name present) rather than
specific-value, per the agent's invariant-test rule.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autocue import perf
from autocue.analysis.concurrency import shutdown_pool
from autocue.serve.app import create_app


@pytest.fixture(autouse=True)
def _enable_perf(monkeypatch, tmp_path):
    perf.clear()
    monkeypatch.setattr(perf, "_enabled", True)
    monkeypatch.setattr(perf, "_sample_rate", 1.0)
    # Force serial paths so each SSE endpoint exercises the writer span on
    # the main generator thread — keeps the regression test deterministic
    # and independent of pool worker scheduling.
    monkeypatch.setenv("AUTOCUE_PARALLEL_GENERATE_APPLY", "0")
    monkeypatch.setenv("AUTOCUE_PARALLEL_CLASSIFY", "0")
    monkeypatch.setenv("AUTOCUE_PARALLEL_ENRICH_COMMENTS", "0")
    monkeypatch.delenv("AUTOCUE_PARALLEL_AUTO_TAG", raising=False)
    shutdown_pool()
    yield
    shutdown_pool()
    perf.clear()


def _span_names() -> set[str]:
    return {name for name, _ts, _dur in perf.recent_spans(limit=1000)}


def _content(track_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        ID=track_id, Title=f"t{track_id}", BPM=12800, Length=300, UUID=f"u{track_id}",
        ArtistName="A", Commnt="", ColorID=None,
    )


def _mk_app_with_db(db) -> TestClient:
    app = create_app()
    app.state.db = db
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/generate-apply-stream
# ---------------------------------------------------------------------------

def test_generate_apply_stream_emits_compute_and_write_one_spans(tmp_path):
    db = MagicMock()
    db._db_dir = tmp_path
    db.session = MagicMock()
    db.get_content.return_value = _content(1)
    (tmp_path / "master.db").write_bytes(b"x")
    (tmp_path / "backups").mkdir(exist_ok=True)

    with patch("autocue.db_writer.rekordbox_is_running", return_value=False), \
         patch("autocue.serve.routes.generate_cues_for_track",
               return_value=([{"slot": 0, "posSec": 0, "label": "Drop"}], None)), \
         patch("autocue.db_writer.write_cues_to_db", return_value=1), \
         patch("autocue.db_writer.backup_database", return_value=str(tmp_path / "backup.db")):
        client = _mk_app_with_db(db)
        r = client.post(
            "/api/generate-apply-stream",
            json={"track_ids": [1, 2, 3], "dry_run": False, "overwrite": True},
        )
    assert r.status_code == 200
    names = _span_names()
    # Outer compute span MUST be present.
    assert "generate_apply.compute" in names, f"missing generate_apply.compute; saw: {names}"
    # Per-track writer span MUST fire at least once.
    assert "generate_apply.write_one" in names, f"missing generate_apply.write_one; saw: {names}"


# ---------------------------------------------------------------------------
# /api/color-tracks-stream
# ---------------------------------------------------------------------------

def test_color_tracks_stream_emits_compute_and_write_one_spans(tmp_path):
    db = MagicMock()
    db._db_dir = tmp_path
    db.session = MagicMock()
    db.get_content.return_value = _content(1)
    # color map
    db.query.return_value.all.return_value = []
    (tmp_path / "master.db").write_bytes(b"x")
    (tmp_path / "backups").mkdir(exist_ok=True)

    with patch("autocue.db_writer.rekordbox_is_running", return_value=False), \
         patch("autocue.db_writer.backup_database", return_value=str(tmp_path / "backup.db")):
        client = _mk_app_with_db(db)
        r = client.post(
            "/api/color-tracks-stream",
            json={"track_ids": [1, 2], "dry_run": False, "skip_colored": False},
        )
    assert r.status_code == 200
    names = _span_names()
    assert "color_tracks.compute" in names, f"missing color_tracks.compute; saw: {names}"
    assert "color_tracks.write_one" in names, f"missing color_tracks.write_one; saw: {names}"


# ---------------------------------------------------------------------------
# /api/health (library_health SSE)
# ---------------------------------------------------------------------------

def test_library_health_emits_compute_span(monkeypatch):
    fake_report = SimpleNamespace(
        track_id=1, score=100, issues=[], fix_tier="none",
    )

    def _fake_gen(db, playlist_id=None):
        yield fake_report

    with patch("autocue.serve.routes.check_library_health", _fake_gen), \
         patch("autocue.serve.routes._report_to_schema") as mock_schema:
        mock_schema.return_value = SimpleNamespace(
            issues=[], model_dump_json=lambda: '{"track_id":1}',
        )
        db = MagicMock()
        db.query.return_value.count.return_value = 1
        client = _mk_app_with_db(db)
        r = client.get("/api/health")
    assert r.status_code == 200
    names = _span_names()
    assert "library_health.compute" in names, f"missing library_health.compute; saw: {names}"


# ---------------------------------------------------------------------------
# /api/cue-tools-stream
# ---------------------------------------------------------------------------

def test_cue_tools_stream_emits_compute_and_write_one_spans(tmp_path):
    db = MagicMock()
    db._db_dir = tmp_path
    db.session = MagicMock()
    db.session.query.return_value.filter.return_value.all.return_value = []
    db.get_content.return_value = _content(1)
    (tmp_path / "master.db").write_bytes(b"x")
    (tmp_path / "backups").mkdir(exist_ok=True)

    with patch("autocue.db_writer.rekordbox_is_running", return_value=False), \
         patch("autocue.db_writer.backup_database", return_value=str(tmp_path / "backup.db")):
        client = _mk_app_with_db(db)
        r = client.post(
            "/api/cue-tools-stream",
            json={
                "operation": "rename",
                "track_ids": [1, 2],
                "rename": {"from_name": "old", "to_name": "new"},
                "dry_run": True,
            },
        )
    assert r.status_code == 200
    names = _span_names()
    assert "cue_tools.compute" in names, f"missing cue_tools.compute; saw: {names}"
    assert "cue_tools.write_one" in names, f"missing cue_tools.write_one; saw: {names}"


# ---------------------------------------------------------------------------
# /api/classify
# ---------------------------------------------------------------------------

def test_classify_library_emits_compute_span():
    db = MagicMock()
    db.get_content.return_value.all.return_value = [_content(1), _content(2)]
    with patch("autocue.analysis.classify.get_classification",
               return_value={"primary": "house", "scores": {}}):
        client = _mk_app_with_db(db)
        r = client.get("/api/classify")
    assert r.status_code == 200
    names = _span_names()
    assert "classify.compute" in names, f"missing classify.compute; saw: {names}"


# ---------------------------------------------------------------------------
# /api/enrich-comments/stream
# ---------------------------------------------------------------------------

def test_enrich_comments_stream_emits_compute_and_write_one_spans(tmp_path):
    db = MagicMock()
    db._db_dir = tmp_path
    db.session = MagicMock()
    db.get_content.return_value = _content(1)
    (tmp_path / "master.db").write_bytes(b"x")
    (tmp_path / "backups").mkdir(exist_ok=True)

    with patch("autocue.db_writer.rekordbox_is_running", return_value=False), \
         patch("autocue.db_writer.backup_database", return_value=str(tmp_path / "backup.db")), \
         patch("autocue.analysis.comment.enrich_comment", return_value="new comment"):
        client = _mk_app_with_db(db)
        r = client.post(
            "/api/enrich-comments/stream",
            json={"track_ids": [1, 2], "overwrite": False, "dry_run": False},
        )
    assert r.status_code == 200
    names = _span_names()
    assert "enrich_comments.compute" in names, f"missing enrich_comments.compute; saw: {names}"
    assert "enrich_comments.write_one" in names, f"missing enrich_comments.write_one; saw: {names}"


# ---------------------------------------------------------------------------
# Boundary: when AUTOCUE_PERF is OFF, no spans are buffered (zero overhead).
# This is the threshold case at the exact boundary where behavior changes.
# ---------------------------------------------------------------------------

def test_no_spans_buffered_when_perf_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(perf, "_enabled", False)
    perf.clear()

    db = MagicMock()
    db._db_dir = tmp_path
    db.session = MagicMock()
    db.get_content.return_value = _content(1)
    (tmp_path / "master.db").write_bytes(b"x")
    (tmp_path / "backups").mkdir(exist_ok=True)

    with patch("autocue.db_writer.rekordbox_is_running", return_value=False), \
         patch("autocue.serve.routes.generate_cues_for_track",
               return_value=([{"slot": 0, "posSec": 0, "label": "Drop"}], None)), \
         patch("autocue.db_writer.write_cues_to_db", return_value=1), \
         patch("autocue.db_writer.backup_database", return_value=str(tmp_path / "backup.db")):
        client = _mk_app_with_db(db)
        r = client.post(
            "/api/generate-apply-stream",
            json={"track_ids": [1], "dry_run": False, "overwrite": True},
        )
    assert r.status_code == 200
    # With perf disabled, the buffer stays empty — zero overhead invariant.
    assert perf.recent_spans(limit=1000) == []
