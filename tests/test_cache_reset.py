"""Tests for autocue.cache_reset — TASK-020 (autocue serve --reset-cache)."""
from __future__ import annotations

import os

import pytest

from autocue.cache import CACHE_FILENAME
from autocue.cache_reset import reset_sidecar_cache


def test_removes_all_three_cache_files(tmp_path):
    main = tmp_path / CACHE_FILENAME
    wal = tmp_path / (CACHE_FILENAME + "-wal")
    shm = tmp_path / (CACHE_FILENAME + "-shm")
    main.write_bytes(b"x")
    wal.write_bytes(b"x")
    shm.write_bytes(b"x")

    removed = reset_sidecar_cache(str(tmp_path))
    assert sorted(os.path.basename(p) for p in removed) == sorted(
        [CACHE_FILENAME, CACHE_FILENAME + "-wal", CACHE_FILENAME + "-shm"]
    )
    assert not main.exists()
    assert not wal.exists()
    assert not shm.exists()


def test_missing_files_noop(tmp_path):
    removed = reset_sidecar_cache(str(tmp_path))
    assert removed == []


def test_accepts_master_db_path(tmp_path):
    """--db-path may point at master.db itself; we strip to the directory."""
    master = tmp_path / "master.db"
    master.write_bytes(b"fake")
    cache = tmp_path / CACHE_FILENAME
    cache.write_bytes(b"x")

    removed = reset_sidecar_cache(str(master))
    assert len(removed) == 1
    assert not cache.exists()


def test_returns_empty_when_db_path_none_and_pyrekordbox_unavailable(monkeypatch):
    """Without --db-path and without a discoverable Rekordbox install,
    we no-op rather than guessing — better than rm-ing the wrong thing."""
    monkeypatch.setattr(
        "autocue.cache_reset._resolve_rekordbox_dir",
        lambda _: None,
    )
    assert reset_sidecar_cache(None) == []


def test_preserves_other_files_in_directory(tmp_path):
    """Only the three cache files are removed — never master.db, never anything else."""
    (tmp_path / "master.db").write_bytes(b"keep")
    (tmp_path / "important.txt").write_bytes(b"keep")
    cache = tmp_path / CACHE_FILENAME
    cache.write_bytes(b"go")
    reset_sidecar_cache(str(tmp_path))
    assert (tmp_path / "master.db").exists()
    assert (tmp_path / "important.txt").exists()
    assert not cache.exists()
