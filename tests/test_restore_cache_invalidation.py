"""TASK-017 — /api/restore invalidates the sidecar CacheStore."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from autocue.cache import CacheStore
from autocue.serve.app import create_app


def _client_with_cache(tmp_path, cache_store):
    app = create_app()
    db = MagicMock()
    db._db_dir = str(tmp_path)
    # Stub _rb_running to return False so restore proceeds.
    db.session = MagicMock()
    db._engine = MagicMock()
    app.state.db = db
    app.state.cache_store = cache_store
    return TestClient(app)


def test_restore_invalidates_cache_store(tmp_path):
    # Backup file the route will copy from.
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup_file = backup_dir / "master_20260101T120000.db"
    backup_file.write_bytes(b"backup contents")
    # The master.db that gets overwritten.
    master = tmp_path / "master.db"
    master.write_bytes(b"current contents")

    # Pre-populate sidecar cache.
    cache_store = CacheStore.open_for(str(tmp_path))
    cache_store.put_energy_curve(42, [0.5] * 10, anlz_mtime=100.0)
    cache_store.put_classification(42, "build", {"build": 1.0}, 128.0, 0.5, anlz_mtime=100.0)
    assert cache_store.get_energy_curve(42, 100.0) is not None

    client = _client_with_cache(tmp_path, cache_store)

    with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("pyrekordbox.Rekordbox6Database") as MockDB:
                MockDB.return_value = MagicMock()
                r = client.post("/api/restore", json={"filename": backup_file.name})

    assert r.status_code == 200, r.text
    # CacheStore is invalidated end-to-end.
    assert cache_store.get_energy_curve(42, 100.0) is None
    assert cache_store.get_classification(42, 100.0) is None
    cache_store.close()


def test_restore_with_no_cache_store_succeeds(tmp_path):
    """When cache_store is None on app.state, restore must not crash."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    backup_file = backup_dir / "master_20260101T120000.db"
    backup_file.write_bytes(b"x")
    master = tmp_path / "master.db"
    master.write_bytes(b"y")

    app = create_app()
    db = MagicMock()
    db._db_dir = str(tmp_path)
    db.session = MagicMock()
    db._engine = MagicMock()
    app.state.db = db
    app.state.cache_store = None
    client = TestClient(app)

    with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("pyrekordbox.Rekordbox6Database") as MockDB:
                MockDB.return_value = MagicMock()
                r = client.post("/api/restore", json={"filename": backup_file.name})

    assert r.status_code == 200, r.text
