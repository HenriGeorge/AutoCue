"""Concurrency invariants for AutoCue's shared analysis thread-pool.

Covers TASK-001 (pool primitive), TASK-008 (pyrekordbox thread-safety
verification — gated by RUN_ANLZ_STRESS), and TASK-009 (broader invariants:
pool config, exception isolation, completion ordering, no resource leaks).

The single-writer-invariant tests from TASK-009 land alongside TASK-002..007
when the SSE endpoints are refactored; this file focuses on the primitives
those refactors depend on.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import as_completed

import pytest

from autocue.analysis import concurrency as conc


# ---------------------------------------------------------------------------
# Autouse fixture: every test gets a fresh pool.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _fresh_pool():
    """Shut down the singleton pool before AND after each test.

    Tests that mutate AUTOCUE_POOL_SIZE need to re-init the pool to observe
    the change; tests that submit work need a clean shutdown to avoid
    cross-test thread-state bleed.
    """
    conc.shutdown_pool()
    yield
    conc.shutdown_pool()


# ---------------------------------------------------------------------------
# TASK-001 — pool_size() resolution
# ---------------------------------------------------------------------------

def test_pool_size_default_no_env(monkeypatch):
    monkeypatch.delenv("AUTOCUE_POOL_SIZE", raising=False)
    expected = max(1, min(8, os.cpu_count() or 1))
    assert conc.pool_size() == expected


def test_pool_size_default_empty_env(monkeypatch):
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "")
    expected = max(1, min(8, os.cpu_count() or 1))
    assert conc.pool_size() == expected


def test_pool_size_override(monkeypatch):
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "4")
    assert conc.pool_size() == 4


def test_pool_size_clamps_zero(monkeypatch):
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "0")
    assert conc.pool_size() == 1


def test_pool_size_clamps_negative(monkeypatch):
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "-5")
    assert conc.pool_size() == 1


def test_pool_size_invalid_raises_value_error(monkeypatch):
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "abc")
    with pytest.raises(ValueError, match="AUTOCUE_POOL_SIZE"):
        conc.pool_size()


def test_pool_size_invalid_float_raises_value_error(monkeypatch):
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "4.5")
    with pytest.raises(ValueError, match="AUTOCUE_POOL_SIZE"):
        conc.pool_size()


# ---------------------------------------------------------------------------
# TASK-001 — get_pool() singleton + shutdown
# ---------------------------------------------------------------------------

def test_get_pool_returns_singleton():
    p1 = conc.get_pool()
    p2 = conc.get_pool()
    assert p1 is p2


def test_get_pool_respects_size(monkeypatch):
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "3")
    pool = conc.get_pool()
    # ThreadPoolExecutor exposes _max_workers (internal but stable across stdlib).
    assert pool._max_workers == 3


def test_shutdown_pool_idempotent():
    conc.shutdown_pool()
    conc.shutdown_pool()  # no-op
    # New pool can still be created after shutdown.
    new_pool = conc.get_pool()
    assert new_pool is not None


def test_shutdown_pool_clears_singleton():
    p1 = conc.get_pool()
    conc.shutdown_pool()
    p2 = conc.get_pool()
    assert p1 is not p2, "shutdown should clear the singleton"


def test_thread_name_prefix_set():
    pool = conc.get_pool()
    captured: list[str] = []

    def _capture():
        captured.append(threading.current_thread().name)

    fut = pool.submit(_capture)
    fut.result(timeout=5)
    assert captured[0].startswith("autocue-pool")


# ---------------------------------------------------------------------------
# TASK-009 — exception isolation at the pool level
# ---------------------------------------------------------------------------

def test_exception_in_one_future_does_not_poison_pool():
    """A worker raising must not corrupt the pool for subsequent submissions."""
    pool = conc.get_pool()

    def _ok(x):
        return x * 2

    def _boom(_):
        raise ValueError("test boom")

    futures = [pool.submit(_boom if i == 7 else _ok, i) for i in range(20)]

    ok_results: list[int] = []
    errors: list[BaseException] = []
    for fut in as_completed(futures, timeout=10):
        exc = fut.exception()
        if exc is not None:
            errors.append(exc)
        else:
            ok_results.append(fut.result())

    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert sorted(ok_results) == sorted(i * 2 for i in range(20) if i != 7)

    # Pool is still usable.
    follow = pool.submit(_ok, 100).result(timeout=5)
    assert follow == 200


def test_many_submit_cycles_no_thread_leak(monkeypatch):
    """100 batches of submissions should not grow the live thread set.

    Caps the live autocue-pool threads at pool_size + epsilon.
    """
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "4")
    pool = conc.get_pool()

    def _noop(x):
        return x

    for batch in range(100):
        futs = [pool.submit(_noop, i) for i in range(20)]
        for f in as_completed(futs, timeout=10):
            f.result()

    # Allow a brief window for any transient threads to settle.
    time.sleep(0.05)
    live = [t for t in threading.enumerate() if t.name.startswith("autocue-pool")]
    # ThreadPoolExecutor keeps up to max_workers worker threads alive.
    assert len(live) <= 4, f"unexpected live worker count: {len(live)}"


def test_completion_order_independent_of_submission(monkeypatch):
    """as_completed yields results in completion order; faster work finishes first."""
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "4")
    pool = conc.get_pool()

    def _sleep_then_return(delay, value):
        time.sleep(delay)
        return value

    # Submit slow first, fast second.
    fut_slow = pool.submit(_sleep_then_return, 0.2, "slow")
    fut_fast = pool.submit(_sleep_then_return, 0.02, "fast")

    order: list[str] = []
    for fut in as_completed([fut_slow, fut_fast], timeout=5):
        order.append(fut.result())

    assert order == ["fast", "slow"]


# ---------------------------------------------------------------------------
# TASK-008 — pyrekordbox read_anlz_file thread-safety verification
# ---------------------------------------------------------------------------
#
# This stress test needs a real Rekordbox database with ANLZ files. It is
# gated behind RUN_ANLZ_STRESS=1 so it does not run in normal CI but can be
# triggered locally against a developer's real library to verify (or
# falsify) the thread-safety assumption.
#
# To run:
#   RUN_ANLZ_STRESS=1 AUTOCUE_DB_PATH=~/Library/Pioneer/rekordbox pytest \
#       tests/test_concurrency.py::test_anlz_read_concurrent -v
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("RUN_ANLZ_STRESS") != "1",
    reason="Set RUN_ANLZ_STRESS=1 to verify pyrekordbox thread-safety against a real DB.",
)
def test_anlz_read_concurrent():
    """Hammer read_anlz_file() from 16 threads against the same Rekordbox6Database.

    Asserts no exceptions and that concurrent tag counts match a serial reference.
    """
    from pyrekordbox.db6 import Rekordbox6Database

    db_path = os.environ.get("AUTOCUE_DB_PATH")
    db = Rekordbox6Database(db_path) if db_path else Rekordbox6Database()
    contents = [c for c in db.get_content() if c.AnalysisDataPath][:50]
    if not contents:
        pytest.skip("no tracks with ANLZ data in the configured library")

    def _serial_count(content):
        try:
            anlz = db.read_anlz_file(content)
            tags = list(anlz.tags) if anlz is not None else []
            return len(tags)
        except Exception:
            return -1

    serial_ref = {c.ID: _serial_count(c) for c in contents}

    # Now hammer with 16 threads.
    barrier = threading.Barrier(16)
    errors: list[BaseException] = []
    parallel_counts: dict[int, int] = {}
    counts_lock = threading.Lock()

    def _worker():
        try:
            barrier.wait(timeout=5)
            for _ in range(20):
                for c in contents:
                    try:
                        anlz = db.read_anlz_file(c)
                        n = len(list(anlz.tags)) if anlz is not None else 0
                    except Exception:
                        n = -1
                    with counts_lock:
                        parallel_counts[c.ID] = n
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_worker, name=f"stress-{i}") for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)

    assert not errors, f"thread exceptions: {errors!r}"
    assert parallel_counts == serial_ref, "parallel reads diverged from serial reference"
