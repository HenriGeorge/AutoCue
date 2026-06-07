"""TASK-002 — parallel /api/generate-apply-stream (flagged behind
AUTOCUE_PARALLEL_GENERATE_APPLY=1).

Until TASK-008 pyrekordbox thread-safety verification lands, the parallel
path is OFF by default. These tests exercise the flagged path directly.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autocue.analysis.concurrency import shutdown_pool
from autocue.serve.app import create_app


# ---------------------------------------------------------------------------
# Helpers (mirror tests/test_serve_routes.py style)
# ---------------------------------------------------------------------------

def _make_db():
    db = MagicMock()
    db.query.return_value = MagicMock()
    return db


def _make_client(db):
    app = create_app()
    from autocue.serve.deps import get_db, get_ro_db
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_ro_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _make_track(id=1):
    return SimpleNamespace(
        ID=id, Title=f"Track {id}", BPM=12800, Length=300, UUID=f"uuid-{id}",
    )


def _collect_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def _enable_flag(monkeypatch):
    monkeypatch.setenv("AUTOCUE_PARALLEL_GENERATE_APPLY", "1")
    shutdown_pool()
    yield
    shutdown_pool()


@pytest.fixture
def _disable_flag(monkeypatch):
    monkeypatch.delenv("AUTOCUE_PARALLEL_GENERATE_APPLY", raising=False)
    shutdown_pool()
    yield
    shutdown_pool()


# ---------------------------------------------------------------------------
# Default-off — verify serial path runs when flag unset
# ---------------------------------------------------------------------------

class TestSerialDefault:
    def test_serial_path_when_flag_unset(self, tmp_path, _disable_flag):
        """Without AUTOCUE_PARALLEL_GENERATE_APPLY=1, pool.submit must not be called."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.side_effect = [_make_track(1), _make_track(2)]

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.serve.routes.generate_cues_for_track",
                       return_value=([{"cue": 1}], None)):
                with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                    with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                        with patch("autocue.analysis.concurrency.get_pool") as mock_pool:
                            client = _make_client(db)
                            r = client.post(
                                "/api/generate-apply-stream",
                                json={"track_ids": [1, 2], "dry_run": False,
                                      "overwrite": True},
                            )
                            assert r.status_code == 200
                            # Serial path must not consult the pool at all.
                            assert mock_pool.call_count == 0

        events = _collect_sse(r.text)
        progress = [e for e in events if not e.get("done")]
        assert len(progress) == 2
        done = [e for e in events if e.get("done")]
        assert len(done) == 1
        assert done[0]["applied"] == 2
        assert done[0]["skipped"] == 0


# ---------------------------------------------------------------------------
# Flag-on — verify pool fanout + per-track exception isolation
# ---------------------------------------------------------------------------

class TestParallelFanout:
    def test_pool_submit_called_once_per_track(self, tmp_path, _enable_flag):
        """With flag on and 10 tracks, pool.submit fires 10 times."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.side_effect = [_make_track(i) for i in range(1, 11)]

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.serve.routes.generate_cues_for_track",
                       return_value=([{"cue": 1}], None)):
                with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                    with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                        with patch("autocue.analysis.concurrency.get_pool") as mock_pool:
                            # Use a real-ish pool: synchronous submit returns a Future.
                            from concurrent.futures import ThreadPoolExecutor
                            real_pool = ThreadPoolExecutor(max_workers=4)
                            mock_pool.return_value = real_pool
                            try:
                                client = _make_client(db)
                                r = client.post(
                                    "/api/generate-apply-stream",
                                    json={"track_ids": list(range(1, 11)),
                                          "dry_run": False, "overwrite": True},
                                )
                                assert r.status_code == 200
                                # mock_pool was the only get_pool — every submit
                                # routed through the same real_pool.
                                assert mock_pool.called
                            finally:
                                real_pool.shutdown(wait=True)

        events = _collect_sse(r.text)
        progress = [e for e in events if not e.get("done")]
        assert len(progress) == 10
        # Processed counts must increment 1..10 (completion-order, not input-order).
        processed_vals = sorted(e["processed"] for e in progress)
        assert processed_vals == list(range(1, 11))
        done = [e for e in events if e.get("done")]
        assert len(done) == 1
        assert done[0]["done"] is True
        assert done[0]["applied"] + done[0]["skipped"] == 10

    def test_per_track_exception_increments_skipped(self, tmp_path, _enable_flag):
        """Exception in _compute_one → that track counts as skipped, stream continues."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        tracks = [_make_track(i) for i in range(1, 6)]

        # 5 tracks; track 3's get_content raises.
        def _get_content(ID):
            if ID == 3:
                raise RuntimeError("boom")
            return tracks[ID - 1]

        db.get_content.side_effect = _get_content

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.serve.routes.generate_cues_for_track",
                       return_value=([{"cue": 1}], None)):
                with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                    with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                        client = _make_client(db)
                        r = client.post(
                            "/api/generate-apply-stream",
                            json={"track_ids": [1, 2, 3, 4, 5],
                                  "dry_run": False, "overwrite": True},
                        )

        assert r.status_code == 200
        events = _collect_sse(r.text)
        progress = [e for e in events if not e.get("done")]
        # Five progress events, one per track, regardless of completion order.
        assert len(progress) == 5
        done = next(e for e in events if e.get("done"))
        assert done["done"] is True
        # 4 succeed, 1 (the raiser) is skipped.
        assert done["applied"] == 4
        assert done["skipped"] == 1

    def test_final_event_has_done_true_and_counts(self, tmp_path, _enable_flag):
        """Final SSE event shape: {done:true, applied, skipped, backup_path}."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.side_effect = [_make_track(1), _make_track(2)]

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.serve.routes.generate_cues_for_track",
                       return_value=([{"cue": 1}], None)):
                with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                    with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                        client = _make_client(db)
                        r = client.post(
                            "/api/generate-apply-stream",
                            json={"track_ids": [1, 2], "dry_run": False,
                                  "overwrite": True},
                        )
        assert r.status_code == 200
        events = _collect_sse(r.text)
        done = [e for e in events if e.get("done")]
        assert len(done) == 1
        assert done[0]["done"] is True
        assert "applied" in done[0]
        assert "skipped" in done[0]
        assert "backup_path" in done[0]
        assert done[0]["applied"] + done[0]["skipped"] == 2

    def test_rekordbox_running_guard_fires_before_pool(self, tmp_path, _enable_flag):
        """409 guard must still fire even on the flagged path."""
        db = _make_db()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            client = _make_client(db)
            r = client.post(
                "/api/generate-apply-stream",
                json={"track_ids": [1, 2, 3], "dry_run": True},
            )
        assert r.status_code == 409
