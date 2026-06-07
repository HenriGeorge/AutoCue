"""Tests for the discover sidecar in the existing backup/restore flow — T-022.

Covers:
- backup_database(db_path, discover_db_path=…) writes both with matching TS.
- backup without discover_db_path leaves no sidecar (pre-v2 backup shape).
- /api/backups groups by timestamp and surfaces has_discover_sidecar.
- /api/restore restores the sidecar when present; logs and skips when absent.
- DELETE /api/backups/{filename} removes both files.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocue.analysis.discover.store import DiscoverStore
from autocue.db_writer import backup_database


# --------------------------------------------------------------------------- #
# backup_database — unit tests against the function directly
# --------------------------------------------------------------------------- #

class TestBackupDatabaseWritesSidecar:
    def test_with_sidecar_writes_both(self, tmp_path, monkeypatch):
        monkeypatch.setattr("autocue.db_writer.BACKUP_DIR", tmp_path / "backups")
        master_src = tmp_path / "master.db"
        master_src.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)
        discover_src = tmp_path / "discover.db"
        discover_src.write_bytes(b"SQLite format 3\x00" + b"\x11" * 64)

        master_dest = backup_database(master_src, discover_db_path=discover_src)
        ts = re.search(r"master_(\d{8}T\d{6})\.db", master_dest.name).group(1)
        discover_dest = master_dest.parent / f"discover_{ts}.db"
        assert master_dest.exists()
        assert discover_dest.exists()

    def test_without_sidecar_writes_only_master(self, tmp_path, monkeypatch):
        monkeypatch.setattr("autocue.db_writer.BACKUP_DIR", tmp_path / "backups")
        master_src = tmp_path / "master.db"
        master_src.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)

        master_dest = backup_database(master_src)
        assert master_dest.exists()
        ts = re.search(r"master_(\d{8}T\d{6})\.db", master_dest.name).group(1)
        assert not (master_dest.parent / f"discover_{ts}.db").exists()

    def test_with_nonexistent_sidecar_path_skips_silently(self, tmp_path, monkeypatch):
        """First-run scenario: discover.db hasn't been created yet, but the
        path is supplied. The backup should still succeed for the master file."""
        monkeypatch.setattr("autocue.db_writer.BACKUP_DIR", tmp_path / "backups")
        master_src = tmp_path / "master.db"
        master_src.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)

        master_dest = backup_database(master_src,
                                      discover_db_path=tmp_path / "nonexistent-discover.db")
        assert master_dest.exists()
        ts = re.search(r"master_(\d{8}T\d{6})\.db", master_dest.name).group(1)
        assert not (master_dest.parent / f"discover_{ts}.db").exists()


# --------------------------------------------------------------------------- #
# /api/backups + /api/restore + DELETE — through the FastAPI surface
# --------------------------------------------------------------------------- #

@pytest.fixture
def app(tmp_path, monkeypatch):
    """Build a FastAPI test app with the backup dir + discover dir pointed at
    tmp_path. We stub the Rekordbox DB minimally — restore tries to reopen it
    and we patch that call inside the test where needed.
    """
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    discover_dir = tmp_path / "discover-data"
    discover_dir.mkdir()
    monkeypatch.setattr("autocue.db_writer.BACKUP_DIR", backup_dir)
    monkeypatch.setenv("AUTOCUE_DISCOVER_DATA_DIR", str(discover_dir))

    # Patch _rb_running to always return False (no Rekordbox in the test env).
    from autocue.serve import routes as _routes
    monkeypatch.setattr(_routes, "_rb_running", lambda _db: False)

    from autocue.serve.routes import router

    app = FastAPI()
    app.state.db = _FakeRekordboxDB(tmp_path / "rekordbox")
    app.state.ro_db = app.state.db
    app.state.discover_store = DiscoverStore(db_path=discover_dir / "discover.db")
    app.include_router(router)
    app._test_backup_dir = backup_dir
    app._test_discover_dir = discover_dir
    app._tmp = tmp_path
    yield app
    if app.state.discover_store is not None:
        app.state.discover_store.close()


@pytest.fixture
def client(app):
    return TestClient(app)


class _FakeRekordboxDB:
    def __init__(self, dir_):
        self._db_dir = dir_
        Path(dir_).mkdir(parents=True, exist_ok=True)
        (Path(dir_) / "master.db").write_bytes(b"SQLite format 3\x00" + b"\x99" * 256)

    def query(self, _model):
        return _EmptyQuery()

    @property
    def session(self):
        return _NullSession()

    @property
    def _engine(self):
        return _NullEngine()


class _EmptyQuery:
    def all(self):
        return []


class _NullSession:
    def close(self):
        pass


class _NullEngine:
    def dispose(self):
        pass


class TestListAndDeleteWithSidecar:
    def test_list_reports_has_discover_sidecar_true(self, client, app):
        # Manually create paired files.
        ts = "20260607T100000"
        (app._test_backup_dir / f"master_{ts}.db").write_bytes(b"SQLite format 3\x00" + b"\x00" * 64)
        (app._test_backup_dir / f"discover_{ts}.db").write_bytes(b"SQLite format 3\x00" + b"\x11" * 64)
        items = client.get("/api/backups").json()
        assert len(items) == 1
        assert items[0]["filename"] == f"master_{ts}.db"
        assert items[0]["has_discover_sidecar"] is True

    def test_list_pre_v2_backup_has_no_sidecar(self, client, app):
        ts = "20260101T100000"
        (app._test_backup_dir / f"master_{ts}.db").write_bytes(b"SQLite format 3\x00")
        items = client.get("/api/backups").json()
        assert items[0]["has_discover_sidecar"] is False

    def test_list_skips_orphan_discover_files(self, client, app):
        """A leftover discover_TS.db with no matching master file should NOT
        appear as a top-level entry."""
        (app._test_backup_dir / "discover_20260101T120000.db").write_bytes(b"x")
        items = client.get("/api/backups").json()
        assert items == []

    def test_delete_removes_both_files(self, client, app):
        ts = "20260607T100000"
        master = app._test_backup_dir / f"master_{ts}.db"
        sidecar = app._test_backup_dir / f"discover_{ts}.db"
        master.write_bytes(b"SQLite format 3\x00")
        sidecar.write_bytes(b"SQLite format 3\x00")

        resp = client.delete(f"/api/backups/master_{ts}.db")
        assert resp.status_code == 200
        body = resp.json()
        assert body["deleted"] == f"master_{ts}.db"
        assert body["deleted_discover_sidecar"] is True
        assert not master.exists()
        assert not sidecar.exists()

    def test_delete_handles_missing_sidecar_gracefully(self, client, app):
        ts = "20260607T100000"
        master = app._test_backup_dir / f"master_{ts}.db"
        master.write_bytes(b"SQLite format 3\x00")

        body = client.delete(f"/api/backups/master_{ts}.db").json()
        assert body["deleted_discover_sidecar"] is False


class TestRestoreWithSidecar:
    def test_restore_carries_discover_sidecar(self, client, app, monkeypatch):
        """End-to-end: pre-seed both files in the backup dir, mark the live
        discover state as 'gone'; restore should bring it back."""
        # Construct a real SQLite discover.db blob — easiest via DiscoverStore.
        seed_path = app._tmp / "seed-discover.db"
        seed_store = DiscoverStore(db_path=seed_path)
        seed_store.save(release_key="k", release_id=1, artist="A", title="T")
        seed_store.close()

        ts = "20260607T100000"
        master = app._test_backup_dir / f"master_{ts}.db"
        sidecar = app._test_backup_dir / f"discover_{ts}.db"
        master.write_bytes(b"SQLite format 3\x00" + b"\x00" * 256)
        sidecar.write_bytes(seed_path.read_bytes())

        # Clear live state so we can verify the restore brought it back.
        app.state.discover_store.unsave("k")
        assert app.state.discover_store.list_saved() == []

        # Stub the pyrekordbox reopen so the test doesn't need a real Rekordbox.
        class _StubRDB:
            def __init__(self, dir_):
                self._db_dir = dir_
        monkeypatch.setattr("pyrekordbox.Rekordbox6Database", _StubRDB)

        resp = client.post("/api/restore", json={"filename": f"master_{ts}.db"})
        assert resp.status_code == 200
        assert "Discover" in resp.json()["message"]

        # The lazy get_discover_store reopens against the restored file.
        from autocue.serve.routes import get_discover_store
        # Fake the dependency invocation manually.
        from fastapi import Request as _Req
        import io
        # Easier: hit a list endpoint, which goes through get_discover_store.
        items = client.get("/api/discover/saved").json()["items"]
        assert {r["release_key"] for r in items} == {"k"}

    def test_restore_logs_and_skips_when_no_sidecar(self, client, app, monkeypatch, caplog):
        ts = "20260607T100000"
        master = app._test_backup_dir / f"master_{ts}.db"
        master.write_bytes(b"SQLite format 3\x00" + b"\x00" * 256)

        # Live discover state stays untouched.
        app.state.discover_store.save(release_key="keep", release_id=1, artist="A", title="T")

        class _StubRDB:
            def __init__(self, dir_):
                self._db_dir = dir_
        monkeypatch.setattr("pyrekordbox.Rekordbox6Database", _StubRDB)

        import logging
        with caplog.at_level(logging.INFO):
            resp = client.post("/api/restore", json={"filename": f"master_{ts}.db"})
        assert resp.status_code == 200
        # Live discover state preserved.
        items = client.get("/api/discover/saved").json()["items"]
        assert {r["release_key"] for r in items} == {"keep"}
        assert "Discover" not in resp.json()["message"]
