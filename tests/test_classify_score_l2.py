"""L2 (CacheStore) wiring for classify + score — TASK-014, TASK-016."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis import classify, score
from autocue.cache import CacheStore


@pytest.fixture
def store():
    s = CacheStore.open_memory()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _detach():
    classify.set_cache_store(None)
    score.set_cache_store(None)
    yield
    classify.set_cache_store(None)
    score.set_cache_store(None)


def _content(tmp_path, track_id=1, bpm=12800):
    anlz = tmp_path / f"track-{track_id}.DAT"
    anlz.write_bytes(b"fake-anlz")
    content = SimpleNamespace(
        ID=track_id,
        BPM=bpm,
        Length=300,
        AnalysisDataPath=str(anlz),
    )
    return content, anlz


def _db(anlz):
    db = MagicMock()
    db.get_anlz_path = MagicMock(side_effect=lambda c, ext: str(anlz) if ext == "DAT" else "")
    db.read_anlz_file = MagicMock(return_value=None)
    return db


# ── TASK-014 ────────────────────────────────────────────────────────────

def test_classify_l2_hit_short_circuits_compute(store, tmp_path):
    content, anlz = _content(tmp_path)
    db = _db(anlz)
    classify.set_cache_store(store)

    import os
    mtime = os.path.getmtime(anlz)
    pre = {
        "primary": "build",
        "label": "Build",
        "color": "#888",
        "confidence": 0.85,
        "scores": {"warmup": 0.1, "build": 0.85, "peak": 0.05},
        "bpm": 128.0,
        "energy_mean": 0.55,
        "energy_peak": 0.9,
        "vocal_proxy": False,
    }
    store.put_classification(
        content.ID, "build", pre, 128.0, 0.55, anlz_mtime=mtime
    )

    with patch("autocue.analysis.classify._score_category") as compute:
        got = classify.get_classification(content, db)
    compute.assert_not_called()
    assert got["primary"] == "build"
    assert got["scores"]["build"] == 0.85


def test_classify_l2_miss_falls_through(store, tmp_path):
    content, anlz = _content(tmp_path)
    db = _db(anlz)
    classify.set_cache_store(store)

    with patch("autocue.analysis.classify._score_category", return_value=0.5) as compute:
        result = classify.get_classification(content, db)
    compute.assert_called()
    # Write-through populated L2.
    assert store.get_classification(content.ID, expected_anlz_mtime=None) is not None or \
        store._conn.execute("SELECT COUNT(*) FROM classification").fetchone()[0] == 1
    assert "primary" in result


def test_classify_unwired_store_unchanged(tmp_path):
    content, anlz = _content(tmp_path)
    db = _db(anlz)
    with patch("autocue.analysis.classify._score_category", return_value=0.5):
        got = classify.get_classification(content, db)
    assert got is not None


# ── TASK-016 ────────────────────────────────────────────────────────────

def test_mixability_l2_hit_short_circuits(store, tmp_path):
    content, anlz = _content(tmp_path)
    db = _db(anlz)
    score.set_cache_store(store)

    import os
    mtime = os.path.getmtime(anlz)
    pre = {
        "score": 72,
        "intro_bars": 16,
        "outro_bars": 16,
        "phrase_count": 8,
        "vocal_proxy": True,
        "energy_variance": 0.05,
        "outro_length_unknown": False,
        "components": {"intro": 100, "outro": 100, "energy": 75, "vocals": 30, "structure": 100},
    }
    store.put_mixability(content.ID, score=72.0, components=pre, anlz_mtime=mtime)

    with patch("autocue.analyzer._get_pssi_and_pqtz") as compute:
        got = score.get_mixability(content, db)
    compute.assert_not_called()
    assert got["score"] == 72


def test_mixability_anlz_missing_caches_none(store, tmp_path):
    content, anlz = _content(tmp_path)
    db = _db(anlz)
    score.set_cache_store(store)

    from autocue.analysis.anlz_path import MISSING_MTIME
    store.put_mixability(content.ID, score=0.0, components={}, anlz_mtime=MISSING_MTIME)

    with patch("autocue.analyzer._get_pssi_and_pqtz") as compute:
        got = score.get_mixability(content, db)
    compute.assert_not_called()
    assert got is None


def test_mixability_unwired_store_unchanged(tmp_path):
    """No CacheStore → no behavioural change; compute path still runs."""
    content, anlz = _content(tmp_path)
    db = _db(anlz)
    with patch("autocue.analyzer._get_pssi_and_pqtz", return_value=(None, None)):
        got = score.get_mixability(content, db)
    assert got is None
