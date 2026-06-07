"""L2 (CacheStore) wiring tests for autocue.analysis.energy — TASK-013."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autocue.analysis import energy
from autocue.cache import CacheStore


@pytest.fixture
def store():
    s = CacheStore.open_memory()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _detach_store():
    """Default: no L2 wired — existing tests stay unaffected."""
    energy.set_cache_store(None)
    yield
    energy.set_cache_store(None)


def _content_with_anlz(tmp_path, track_id=1):
    """Build a content fixture whose ANLZ file exists on disk."""
    anlz = tmp_path / f"track-{track_id}.DAT"
    anlz.write_bytes(b"fake-anlz")
    content = SimpleNamespace(ID=track_id, AnalysisDataPath=str(anlz))
    return content, anlz


def _db_with_anlz_path(anlz_path):
    db = MagicMock()
    db.get_anlz_path = MagicMock(side_effect=lambda c, ext: str(anlz_path) if ext == "DAT" else "")
    db.read_anlz_file = MagicMock(return_value=None)
    return db


def test_l2_hit_short_circuits_compute(store, tmp_path):
    content, anlz = _content_with_anlz(tmp_path)
    db = _db_with_anlz_path(anlz)
    energy.set_cache_store(store)

    # Pre-populate L2 with a known curve.
    import os
    mtime = os.path.getmtime(anlz)
    pre_curve = [0.0, 0.5, 1.0, 0.5, 0.0] * 10
    store.put_energy_curve(content.ID, pre_curve, anlz_mtime=mtime)

    got = energy.get_energy_curve(content, db)
    assert got is not None
    assert len(got) == 50
    # Compute path should not have been hit (read_anlz_file not called).
    db.read_anlz_file.assert_not_called()


def test_l2_miss_falls_through_to_compute(store, tmp_path):
    content, anlz = _content_with_anlz(tmp_path)
    db = _db_with_anlz_path(anlz)
    energy.set_cache_store(store)

    # Cache is empty → fall through. Mock read_anlz_file to return None,
    # so compute also produces None. Verify read path was attempted.
    energy.get_energy_curve(content, db)
    db.read_anlz_file.assert_called()


def test_anlz_missing_sentinel_short_circuits(store, tmp_path):
    content, anlz = _content_with_anlz(tmp_path)
    db = _db_with_anlz_path(anlz)
    energy.set_cache_store(store)

    # Sentinel: anlz_mtime=-1 means "ANLZ missing, don't retry."
    from autocue.analysis.anlz_path import MISSING_MTIME
    store.put_energy_curve(content.ID, [], anlz_mtime=MISSING_MTIME)

    got = energy.get_energy_curve(content, db)
    assert got is None
    db.read_anlz_file.assert_not_called()


def test_unwired_store_does_not_affect_existing_path(tmp_path):
    """Without set_cache_store, behavior is identical to v0."""
    content, anlz = _content_with_anlz(tmp_path)
    db = _db_with_anlz_path(anlz)
    # No store wired.
    energy.get_energy_curve(content, db)
    db.read_anlz_file.assert_called()


def test_set_cache_store_none_detaches(store, tmp_path):
    """Passing None should detach the L2 layer cleanly."""
    content, anlz = _content_with_anlz(tmp_path)
    db = _db_with_anlz_path(anlz)

    energy.set_cache_store(store)
    energy.set_cache_store(None)

    # Even though store has data, the detached L2 isn't consulted.
    import os
    mtime = os.path.getmtime(anlz)
    store.put_energy_curve(content.ID, [0.9] * 50, anlz_mtime=mtime)

    energy.get_energy_curve(content, db)
    db.read_anlz_file.assert_called()


def test_non_default_n_points_bypasses_l2(store, tmp_path):
    """Sidecar only stores at n_points=50; other callers go straight to compute."""
    content, anlz = _content_with_anlz(tmp_path)
    db = _db_with_anlz_path(anlz)
    energy.set_cache_store(store)

    import os
    mtime = os.path.getmtime(anlz)
    store.put_energy_curve(content.ID, [0.5] * 50, anlz_mtime=mtime)

    energy.get_energy_curve(content, db, n_points=30)
    db.read_anlz_file.assert_called()
