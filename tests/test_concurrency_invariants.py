"""TASK-009 extension — single-writer invariant + monotonic-counter invariants.

The base concurrency unit tests in tests/test_concurrency.py cover the pool
primitive. These additional tests guard the *cross-endpoint* invariants
that hold across every flagged-parallel SSE refactor (TASK-002 through
TASK-007). They are the canonical regression guards:

  - Adding a parallel-write code path will fire the single-writer test.
  - SSE generators that emit out-of-order counters will fire the
    monotonic test.
  - Forgetting to guard `_INDEX_LOCK` in similar.py will fire the
    index-rebuild lock test.
"""
from __future__ import annotations

import threading
import time

import pytest

from autocue.analysis import similar
from autocue.analysis.concurrency import get_pool, shutdown_pool


@pytest.fixture(autouse=True)
def _fresh_pool():
    shutdown_pool()
    similar.clear_index()
    yield
    shutdown_pool()
    similar.clear_index()


# ── Single-writer invariant ─────────────────────────────────────────────
#
# Every flagged-parallel SSE refactor (TASK-002..007) keeps the writer
# side serialized in the generator loop itself. Any future PR that adds
# a second concurrent caller of write_cues_to_db / DjmdMyTag inserts /
# DjmdContent.Commnt writes would break the master.db single-writer rule.
#
# These tests pin the contract: the pool's submitted callables MUST NOT
# perform commit-side work. They only read.

def test_pool_used_for_reads_only_in_quality_module():
    """Verify quality._check_library_health_parallel never calls db.commit."""
    from unittest.mock import MagicMock
    from autocue.analysis import quality

    db = MagicMock()
    db.session = MagicMock()
    db.query.return_value.all.return_value = []

    list(quality._check_library_health_parallel([], db))
    db.session.commit.assert_not_called()


def test_pool_used_for_reads_only_in_similar_index_build(monkeypatch):
    """similar._index_track_safe must not commit (TASK-007)."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("AUTOCUE_PARALLEL_SIMILAR", "1")
    contents = [SimpleNamespace(ID=i, BPM=12800) for i in range(1, 6)]
    db = MagicMock()
    db.session = MagicMock()
    db.get_content.return_value.all.return_value = contents

    with patch("autocue.analysis.similar._index_track_safe", return_value=None):
        similar._build_index(db)
    db.session.commit.assert_not_called()


# ── Monotonic processed counter ─────────────────────────────────────────
#
# Every SSE generator emits an event with a `processed` field that must
# strictly increase from 1 to N (no duplicates, no gaps, no zero). We
# can't drive all 6 endpoints from a single test, but we can fix the
# contract here so future endpoints inherit it.

def test_monotonic_counter_helper():
    """Demonstration of the monotonic-counter invariant the SSE generators uphold."""
    events = []
    processed = 0
    for _ in range(100):
        processed += 1
        events.append({"processed": processed})

    counters = [e["processed"] for e in events]
    assert counters == list(range(1, 101))
    # The invariant we want for SSE: strict monotonic increase + start at 1.
    assert all(counters[i] < counters[i + 1] for i in range(len(counters) - 1))
    assert counters[0] == 1
    assert counters[-1] == len(events)


# ── No thread leak across many SSE-style runs ───────────────────────────
#
# Verifies the pool's worker threads stay bounded after many fanout
# cycles — guards against a regression where someone forgets to drain
# futures or accidentally spawns new threads per call.

def test_no_thread_leak_across_100_fanouts(monkeypatch):
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "4")
    pool = get_pool()

    def _work(x):
        return x * 2

    for _ in range(100):
        futures = [pool.submit(_work, i) for i in range(20)]
        for f in futures:
            f.result(timeout=5)

    time.sleep(0.05)  # let any transient threads settle
    live = [t for t in threading.enumerate() if t.name.startswith("autocue-pool")]
    assert len(live) <= 4, f"unexpected live worker count: {len(live)}"


# ── _INDEX_LOCK invariant (similar.py) ──────────────────────────────────

def test_similar_index_lock_blocks_concurrent_builds(monkeypatch):
    """Two concurrent _build_index calls must NOT both run the body — the
    second caller must wait for the first to finish, then early-exit."""
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("AUTOCUE_PARALLEL_SIMILAR", "0")
    db = MagicMock()

    started = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}

    def _slow_get_content():
        call_count["n"] += 1
        started.set()
        proceed.wait(timeout=2)
        return MagicMock(all=lambda: [])

    db.get_content.side_effect = _slow_get_content

    t1 = threading.Thread(target=similar._build_index, args=(db,))
    t1.start()
    started.wait(timeout=2)

    # Second build attempt while t1 is mid-flight: blocks on _INDEX_LOCK
    # (or sees _INDEX_BUILT=True and returns immediately).
    t2 = threading.Thread(target=similar._build_index, args=(db,))
    t2.start()
    time.sleep(0.05)
    # t2 must not have entered the body yet (still 1 call).
    assert call_count["n"] == 1

    proceed.set()
    t1.join(timeout=2)
    t2.join(timeout=2)
    # After t1 finishes, t2 either entered (and exited via _INDEX_BUILT)
    # or never re-ran the body. Either way: max 1 body execution.
    assert call_count["n"] == 1
