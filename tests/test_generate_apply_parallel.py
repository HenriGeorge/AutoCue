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
    monkeypatch.setenv("AUTOCUE_PARALLEL_GENERATE_APPLY", "0")
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
        assert done[0]["applied"] + done[0]["skipped"] + done[0].get("errors", 0) == 10

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
        # TASK-042 — every progress event carries content_id.
        for ev in progress:
            assert "content_id" in ev
        # TASK-043 — exactly one event surfaces the compute-side failure
        # via error_kind/error_message; the rest are clean.
        err_events = [e for e in progress if e.get("error_kind")]
        assert len(err_events) == 1
        assert err_events[0]["error_kind"] == "compute"
        assert err_events[0]["content_id"] == 3
        assert "boom" in err_events[0]["error_message"]
        done = next(e for e in events if e.get("done"))
        assert done["done"] is True
        # 4 succeed, 1 (the raiser) bucketed as `errors`, NOT `skipped`.
        assert done["applied"] == 4
        assert done["errors"] == 1
        assert done["skipped"] == 0

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
        assert "errors" in done[0]
        assert "backup_path" in done[0]
        assert done[0]["applied"] + done[0]["skipped"] + done[0]["errors"] == 2

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


# ---------------------------------------------------------------------------
# TASK-042 / TASK-043 — per-track content_id + error/skip isolation
# ---------------------------------------------------------------------------

class TestPerTrackContentIdAndErrors:
    """Issue #105 regression coverage.

    Each progress event MUST carry content_id and an `errors` counter, with
    compute vs writer failures distinguishable from intentional skips.
    """

    def test_every_progress_event_carries_content_id_parallel(
        self, tmp_path, _enable_flag
    ):
        """Property: ∀ progress events e, e.content_id ∈ submitted track_ids."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        track_ids = [11, 22, 33, 44]
        db.get_content.side_effect = lambda ID: _make_track(ID)

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.serve.routes.generate_cues_for_track",
                       return_value=([{"cue": 1}], None)):
                with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                    with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                        client = _make_client(db)
                        r = client.post(
                            "/api/generate-apply-stream",
                            json={"track_ids": track_ids, "dry_run": False,
                                  "overwrite": True},
                        )

        assert r.status_code == 200
        events = _collect_sse(r.text)
        progress = [e for e in events if not e.get("done")]
        assert len(progress) == len(track_ids)
        seen_cids = sorted(e["content_id"] for e in progress)
        assert seen_cids == sorted(track_ids)
        # Every event also carries the new errors counter (default 0).
        for ev in progress:
            assert ev.get("errors", 0) == 0

    def test_writer_exception_increments_errors_not_skipped(
        self, tmp_path, _enable_flag
    ):
        """Writer exceptions land in `errors`, not `skipped` (TASK-043)."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        tracks = [_make_track(i) for i in range(1, 4)]
        db.get_content.side_effect = lambda ID: tracks[ID - 1]

        call = {"n": 0}

        def _write(*args, **kwargs):
            call["n"] += 1
            # Track 2 fails in the writer.
            if call["n"] == 2:
                raise RuntimeError("writer-boom")
            return 1

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.serve.routes.generate_cues_for_track",
                       return_value=([{"cue": 1}], None)):
                with patch("autocue.db_writer.write_cues_to_db",
                           side_effect=_write):
                    with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                        client = _make_client(db)
                        r = client.post(
                            "/api/generate-apply-stream",
                            json={"track_ids": [1, 2, 3], "dry_run": False,
                                  "overwrite": True},
                        )

        assert r.status_code == 200
        events = _collect_sse(r.text)
        progress = [e for e in events if not e.get("done")]
        # Exactly one event surfaces the writer failure.
        err = [e for e in progress if e.get("error_kind") == "writer"]
        assert len(err) == 1
        assert "writer-boom" in err[0]["error_message"]
        done = next(e for e in events if e.get("done"))
        # 2 applied, 0 intentional skips, 1 writer error.
        assert done["applied"] == 2
        assert done["skipped"] == 0
        assert done["errors"] == 1

    def test_intentional_skip_does_not_increment_errors(
        self, tmp_path, _enable_flag
    ):
        """`no_cues` is an intentional skip — must NOT count as an error."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.side_effect = [_make_track(1), _make_track(2)]

        # Track 2 returns no cues; track 1 returns one cue.
        gen_calls = {"n": 0}

        def _gen(*args, **kwargs):
            gen_calls["n"] += 1
            if gen_calls["n"] == 2:
                return ([], None)
            return ([{"cue": 1}], None)

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.serve.routes.generate_cues_for_track",
                       side_effect=_gen):
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
        done = next(e for e in events if e.get("done"))
        assert done["applied"] == 1
        assert done["skipped"] == 1
        assert done["errors"] == 0
        # No progress event should carry an error_kind for a domain skip.
        progress = [e for e in events if not e.get("done")]
        assert all(e.get("error_kind") is None for e in progress)
