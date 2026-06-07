"""L2 wiring for similar.py — TASK-015."""
from __future__ import annotations

import math
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis import similar
from autocue.cache import CacheStore


@pytest.fixture
def store():
    s = CacheStore.open_memory()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _detach():
    similar.set_cache_store(None)
    similar.clear_index()
    yield
    similar.set_cache_store(None)
    similar.clear_index()


def _content(tmp_path, track_id=1, bpm=12800):
    anlz = tmp_path / f"track-{track_id}.DAT"
    anlz.write_bytes(b"x")
    return SimpleNamespace(
        ID=track_id,
        BPM=bpm,
        Length=300,
        AnalysisDataPath=str(anlz),
        Key=None,
    ), anlz


def test_l2_hit_short_circuits_index_track(store, tmp_path):
    content, anlz = _content(tmp_path)
    similar.set_cache_store(store)
    mtime = os.path.getmtime(anlz)
    # Store a pre-computed feature vector.
    vec = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
    store.put_similarity_vector(content.ID, vec, anlz_mtime=mtime)

    db = MagicMock()
    db.get_anlz_path = MagicMock(side_effect=lambda c, ext: str(anlz) if ext == "DAT" else "")

    with patch("autocue.analysis.similar.get_energy_curve") as cmp_energy:
        with patch("autocue.analysis.similar.get_mixability") as cmp_mix:
            similar._index_track(content, db)

    cmp_energy.assert_not_called()
    cmp_mix.assert_not_called()
    bpm_stored, vec_stored, has_e = similar._INDEX[content.ID]
    # struct.pack('6f', ...) is float32 → tiny precision loss; approximate compare.
    for got, want in zip(vec_stored, vec):
        assert math.isclose(got, want, abs_tol=1e-6)
    assert has_e is True  # energy_mean=0.3 (not NaN) → present


def test_l2_hit_with_nan_energy_recovers_has_energy_false(store, tmp_path):
    content, anlz = _content(tmp_path)
    similar.set_cache_store(store)
    mtime = os.path.getmtime(anlz)
    # energy_mean slot = NaN → no ANLZ data on original compute.
    vec = (0.1, 0.2, float("nan"), 0.0, 0.0, 0.6)
    store.put_similarity_vector(content.ID, vec, anlz_mtime=mtime)

    db = MagicMock()
    db.get_anlz_path = MagicMock(side_effect=lambda c, ext: str(anlz) if ext == "DAT" else "")
    similar._index_track(content, db)

    _, vec_stored, has_e = similar._INDEX[content.ID]
    assert has_e is False
    # NaN must be zeroed out for cosine math; otherwise dot products are NaN.
    assert vec_stored[2] == 0.0
    assert vec_stored[3] == 0.0


def test_l2_miss_falls_through_to_compute(store, tmp_path):
    content, anlz = _content(tmp_path)
    similar.set_cache_store(store)

    db = MagicMock()
    db.get_anlz_path = MagicMock(side_effect=lambda c, ext: str(anlz) if ext == "DAT" else "")

    with patch("autocue.analysis.similar.get_energy_curve", return_value=[0.5] * 50):
        with patch("autocue.analysis.similar.get_mixability", return_value=None):
            with patch("autocue.analysis.classify.get_classification"):
                similar._index_track(content, db)

    # Vector was computed and stored in L2.
    mtime = os.path.getmtime(anlz)
    cached = store.get_similarity_vector(content.ID, expected_anlz_mtime=mtime)
    assert cached is not None
    # Energy was present → no NaN in slot 2.
    assert not math.isnan(cached[2])


def test_write_through_missing_energy_packs_nan(store, tmp_path):
    content, anlz = _content(tmp_path)
    similar.set_cache_store(store)

    db = MagicMock()
    db.get_anlz_path = MagicMock(side_effect=lambda c, ext: str(anlz) if ext == "DAT" else "")

    with patch("autocue.analysis.similar.get_energy_curve", return_value=None):
        with patch("autocue.analysis.similar.get_mixability", return_value=None):
            with patch("autocue.analysis.classify.get_classification"):
                similar._index_track(content, db)

    mtime = os.path.getmtime(anlz)
    cached = store.get_similarity_vector(content.ID, expected_anlz_mtime=mtime)
    assert cached is not None
    assert math.isnan(cached[2])


def test_unwired_store_unchanged_path(tmp_path):
    content, anlz = _content(tmp_path)
    # No store wired.
    db = MagicMock()
    db.get_anlz_path = MagicMock(side_effect=lambda c, ext: "")
    with patch("autocue.analysis.similar.get_energy_curve", return_value=[0.5] * 50):
        with patch("autocue.analysis.similar.get_mixability", return_value=None):
            with patch("autocue.analysis.classify.get_classification"):
                similar._index_track(content, db)
    # Original code path populated _INDEX without exceptions.
    assert content.ID in similar._INDEX
