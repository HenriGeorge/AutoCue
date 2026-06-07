"""CacheStore.warm_up + find_missing — TASK-018.

These tests stub the pool with a MagicMock to keep them deterministic
and fast; the real-pool path is covered indirectly by the future
TASK-027 warm-up pipeline test.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from autocue.cache import CacheStore


@pytest.fixture
def store():
    s = CacheStore.open_memory()
    yield s
    s.close()


def _stub_pool():
    """Return a pool whose submit() runs the callable inline."""
    pool = MagicMock()

    def _submit(fn, *args, **kwargs):
        fut = MagicMock()
        try:
            result = fn(*args, **kwargs)
            fut.result = lambda r=result: r
            fut.exception = lambda: None
        except Exception as exc:
            fut.result = MagicMock(side_effect=exc)
            fut.exception = lambda e=exc: e
        return fut

    pool.submit.side_effect = _submit
    return pool


def test_find_missing_empty(store):
    assert store.find_missing([]) == []


def test_find_missing_returns_all_when_table_empty(store):
    assert store.find_missing([1, 2, 3]) == [1, 2, 3]


def test_find_missing_skips_present(store):
    store.put_energy_curve(2, [0.5], anlz_mtime=100.0)
    assert store.find_missing([1, 2, 3]) == [1, 3]


def test_warm_up_zero_total_calls_progress_cb_once(store):
    """No work needed → still report (0,0) so the UI knows we're done."""
    pool = _stub_pool()
    progress = []
    done = store.warm_up(
        MagicMock(), [], pool, progress_cb=lambda d, t: progress.append((d, t))
    )
    assert done == 0
    assert progress == [(0, 0)]


def test_warm_up_submits_one_future_per_missing_track(store):
    """With 5 missing tracks and batch_size=2, expect 3 batches → 5 submits."""
    pool = _stub_pool()
    db = MagicMock()
    db.get_content.return_value = None  # _warm_one returns False quickly
    store.warm_up(db, [1, 2, 3, 4, 5], pool, batch_size=2)
    assert pool.submit.call_count == 5


def test_warm_up_skips_present_rows(store):
    pool = _stub_pool()
    # Pre-populate two of three tracks.
    store.put_energy_curve(1, [0.5], anlz_mtime=100.0)
    store.put_energy_curve(3, [0.6], anlz_mtime=100.0)
    db = MagicMock()
    db.get_content.return_value = None
    store.warm_up(db, [1, 2, 3], pool)
    # Only one missing → one submit.
    assert pool.submit.call_count == 1


def test_warm_up_progress_callback_per_batch(store):
    """progress_cb fires once per batch."""
    pool = _stub_pool()
    db = MagicMock()
    db.get_content.return_value = None
    progress = []
    store.warm_up(
        db,
        list(range(1, 11)),  # 10 tracks
        pool,
        progress_cb=lambda d, t: progress.append((d, t)),
        batch_size=4,
    )
    # 10 tracks / 4-per-batch = 3 batches.
    assert len(progress) == 3
    assert progress[-1] == (10, 10)


def test_warm_up_cancellation_short_circuits(store):
    import threading
    pool = _stub_pool()
    db = MagicMock()
    db.get_content.return_value = None
    cancel = threading.Event()
    cancel.set()  # Pre-cancelled.

    done = store.warm_up(
        db,
        list(range(1, 101)),
        pool,
        cancel_event=cancel,
        batch_size=10,
    )
    # Pre-cancelled → break before first batch runs.
    assert done == 0
    assert pool.submit.call_count == 0


def test_warm_up_no_progress_cb_does_not_error(store):
    pool = _stub_pool()
    db = MagicMock()
    db.get_content.return_value = None
    # Just verify no crash when progress_cb is None.
    store.warm_up(db, [1, 2, 3], pool)
