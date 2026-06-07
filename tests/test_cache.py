"""Tests for autocue.cache.CacheStore.

See .agent/prd/PERFORMANCE_PRD.md TASK-010 / TASK-011 / TASK-012.
"""
from __future__ import annotations

import math
import os

import pytest

from autocue import cache as cache_mod
from autocue.cache import CACHE_FILENAME, MISSING, SCHEMA_DDL, SCHEMA_VERSION, CacheStore


@pytest.fixture
def store():
    s = CacheStore.open_memory()
    yield s
    s.close()


# ---------------------------------------------------------------------------
# TASK-010 — lifecycle
# ---------------------------------------------------------------------------

def test_open_for_resolves_canonical_path(tmp_path):
    s = CacheStore.open_for(str(tmp_path))
    try:
        assert (tmp_path / CACHE_FILENAME).exists()
    finally:
        s.close()


def test_reopen_preserves_data(tmp_path):
    s1 = CacheStore.open_for(str(tmp_path))
    s1.put_energy_curve(42, [0.1, 0.2, 0.3], anlz_mtime=100.0)
    s1.close()

    s2 = CacheStore.open_for(str(tmp_path))
    try:
        got = s2.get_energy_curve(42, expected_anlz_mtime=100.0)
        assert got is not None
        assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(got, [0.1, 0.2, 0.3]))
    finally:
        s2.close()


def test_close_idempotent(store):
    store.close()
    store.close()  # no exception


# ---------------------------------------------------------------------------
# TASK-011 — schema
# ---------------------------------------------------------------------------

def test_schema_dump_contains_all_tables(store):
    dump = store.dump_schema()
    for table in ("meta", "energy_curve", "classification",
                  "similarity_vector", "mixability", "tracks_snapshot"):
        assert table in dump, f"missing table: {table}"
    assert "idx_energy_mtime" in dump
    assert "idx_class_mtime" in dump


def test_schema_version_recorded(store):
    cur = store._conn.execute("SELECT value FROM meta WHERE key='schema_version'")
    assert cur.fetchone()[0] == str(SCHEMA_VERSION)


def test_schema_ddl_constants_match(store):
    # SCHEMA_DDL is the canonical source; if a table is added, this list must update.
    ddl_text = " ".join(s.lower() for s in SCHEMA_DDL)
    for table in ("meta", "energy_curve", "classification",
                  "similarity_vector", "mixability", "tracks_snapshot"):
        assert f"create table if not exists {table}" in ddl_text


# ---------------------------------------------------------------------------
# TASK-012 — anlz_mtime invalidation + sentinel
# ---------------------------------------------------------------------------

def test_energy_curve_roundtrip(store):
    curve = [0.0, 0.25, 0.5, 0.75, 1.0]
    store.put_energy_curve(1, curve, anlz_mtime=100.0)
    got = store.get_energy_curve(1, expected_anlz_mtime=100.0)
    assert got is not None
    assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(got, curve))


def test_energy_curve_mtime_mismatch_returns_none(store):
    store.put_energy_curve(1, [0.5], anlz_mtime=100.0)
    assert store.get_energy_curve(1, expected_anlz_mtime=101.0) is None


def test_energy_curve_missing_sentinel(store):
    store.put_energy_curve(2, [], anlz_mtime=-1.0)
    # With expected None, the sentinel is returned distinctly from None.
    assert store.get_energy_curve(2, expected_anlz_mtime=None) is MISSING
    # With any expected mtime, the sentinel still wins because anlz_mtime<0.
    assert store.get_energy_curve(2, expected_anlz_mtime=100.0) is MISSING


def test_energy_curve_miss_returns_none(store):
    assert store.get_energy_curve(999, expected_anlz_mtime=100.0) is None


def test_classification_roundtrip(store):
    store.put_classification(
        content_id=1,
        primary_cat="build",
        scores={"warmup": 0.1, "build": 0.7, "peak": 0.2},
        bpm=128.0,
        energy_mean=0.6,
        anlz_mtime=200.0,
    )
    got = store.get_classification(1, expected_anlz_mtime=200.0)
    assert got is not None
    assert got["primary"] == "build"
    assert got["scores"]["build"] == 0.7
    assert got["bpm"] == 128.0
    assert got["energy_mean"] == 0.6


def test_classification_mtime_mismatch(store):
    store.put_classification(1, "build", {"build": 1.0}, 128.0, 0.5, anlz_mtime=200.0)
    assert store.get_classification(1, expected_anlz_mtime=201.0) is None


def test_similarity_vector_roundtrip(store):
    vec = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
    store.put_similarity_vector(1, vec, anlz_mtime=300.0)
    got = store.get_similarity_vector(1, expected_anlz_mtime=300.0)
    assert got is not None
    assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(got, vec))


def test_similarity_vector_wrong_dim_raises(store):
    with pytest.raises(ValueError, match="6 floats"):
        store.put_similarity_vector(1, (0.1, 0.2, 0.3), anlz_mtime=300.0)


def test_mixability_roundtrip(store):
    components = {"key": 1.0, "bpm": 0.9, "energy": 0.8}
    store.put_mixability(1, score=0.85, components=components, anlz_mtime=400.0)
    got = store.get_mixability(1, expected_anlz_mtime=400.0)
    assert got is not None
    assert got["score"] == 0.85
    assert got["components"]["key"] == 1.0


# ---------------------------------------------------------------------------
# tracks_snapshot
# ---------------------------------------------------------------------------

def test_tracks_snapshot_roundtrip(store):
    payload = CacheStore.gzip_json([{"id": 1, "title": "Track 1"}])
    store.put_tracks_snapshot(master_db_mtime=500.0, payload=payload)
    got = store.get_tracks_snapshot(expected_master_db_mtime=500.0)
    assert got == payload
    decoded = CacheStore.ungzip_json(got)
    assert decoded == [{"id": 1, "title": "Track 1"}]


def test_tracks_snapshot_mtime_mismatch_returns_none(store):
    payload = CacheStore.gzip_json([{"id": 1}])
    store.put_tracks_snapshot(500.0, payload)
    assert store.get_tracks_snapshot(501.0) is None


def test_tracks_snapshot_replaces_old(store):
    store.put_tracks_snapshot(100.0, b"old")
    store.put_tracks_snapshot(200.0, b"new")
    # Old mtime no longer hits; new mtime does.
    assert store.get_tracks_snapshot(100.0) is None
    assert store.get_tracks_snapshot(200.0) == b"new"


# ---------------------------------------------------------------------------
# invalidation
# ---------------------------------------------------------------------------

def test_invalidate_all_clears_every_table(store):
    store.put_energy_curve(1, [0.5], anlz_mtime=100.0)
    store.put_classification(1, "build", {"build": 1.0}, 128.0, 0.5, anlz_mtime=100.0)
    store.put_similarity_vector(1, (1.0,) * 6, anlz_mtime=100.0)
    store.put_mixability(1, 0.8, {}, anlz_mtime=100.0)
    store.put_tracks_snapshot(100.0, b"x")
    store.invalidate_all()
    assert store.get_energy_curve(1, 100.0) is None
    assert store.get_classification(1, 100.0) is None
    assert store.get_similarity_vector(1, 100.0) is None
    assert store.get_mixability(1, 100.0) is None
    assert store.get_tracks_snapshot(100.0) is None


def test_invalidate_track_clears_only_that_id(store):
    store.put_energy_curve(1, [0.5], anlz_mtime=100.0)
    store.put_energy_curve(2, [0.7], anlz_mtime=100.0)
    store.put_mixability(1, 0.8, {}, anlz_mtime=100.0)
    store.invalidate_track(1)
    assert store.get_energy_curve(1, 100.0) is None
    assert store.get_energy_curve(2, 100.0) == [pytest.approx(0.7, abs=1e-6)]
    assert store.get_mixability(1, 100.0) is None


def test_invalidate_mixability_preserves_other_tables(store):
    """Cue edits (per PRD §5 Flow E) invalidate mixability ONLY."""
    store.put_energy_curve(1, [0.5], anlz_mtime=100.0)
    store.put_classification(1, "build", {"build": 1.0}, 128.0, 0.5, anlz_mtime=100.0)
    store.put_mixability(1, 0.8, {}, anlz_mtime=100.0)
    store.invalidate_mixability(1)
    assert store.get_mixability(1, 100.0) is None
    assert store.get_energy_curve(1, 100.0) is not None
    assert store.get_classification(1, 100.0) is not None


# ---------------------------------------------------------------------------
# concurrent access (WAL + threading.Lock)
# ---------------------------------------------------------------------------

def test_concurrent_writes_no_corruption(store):
    import threading
    def _worker(start):
        for i in range(50):
            store.put_energy_curve(start + i, [float(start + i)] * 5, anlz_mtime=100.0)
    threads = [threading.Thread(target=_worker, args=(n * 100,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    # Verify 200 distinct rows.
    cur = store._conn.execute("SELECT COUNT(*) FROM energy_curve")
    assert cur.fetchone()[0] == 200


# ---------------------------------------------------------------------------
# schema bump → drop+recreate
# ---------------------------------------------------------------------------

def test_schema_version_bump_drops_existing(tmp_path, monkeypatch):
    path = str(tmp_path / "cache.sqlite")
    s1 = CacheStore._open(path)
    s1.put_energy_curve(1, [0.5], anlz_mtime=100.0)
    s1.close()

    monkeypatch.setattr(cache_mod, "SCHEMA_VERSION", 999)
    s2 = CacheStore._open(path)
    try:
        assert s2.get_energy_curve(1, 100.0) is None
    finally:
        s2.close()
