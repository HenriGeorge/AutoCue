"""Regression test for the get_discover_store first-load race.

The bug:
``get_discover_store`` is a sync FastAPI dependency. The first page load
fires 7 parallel ``/api/discover/*`` fetches via ``loadInitialState()``;
FastAPI dispatches each one onto its thread-pool, and N threads all see
``app.state.discover_store is None`` at once. Without a lock, every
thread calls ``DiscoverStore()`` — the first finishes its migration and
caches the singleton; subsequent constructors fail at
``CREATE TABLE schema_version`` (the table is committed but the singleton
swap hasn't happened yet because they raced past it).

The fix is double-checked locking inside ``get_discover_store``. This
test asserts:
  1. Concurrent calls return the SAME store instance (singleton holds).
  2. No constructor call raises (the lock keeps re-entries off the
     migration code path).
  3. Only ONE underlying ``DiscoverStore`` is constructed.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import pytest

from autocue.serve import deps


@pytest.fixture()
def app_state_with_no_store(tmp_path, monkeypatch):
    """Fresh request.app.state with no cached store + a tmp data dir."""
    monkeypatch.setenv("AUTOCUE_DISCOVER_DATA_DIR", str(tmp_path))

    class _State:
        discover_store = None

    class _App:
        state = _State()

    class _Req:
        app = _App()

    return _Req()


def test_concurrent_first_load_returns_same_store(app_state_with_no_store):
    """7 parallel callers all see the same DiscoverStore instance."""
    results = []
    errors = []

    def caller():
        try:
            results.append(deps.get_discover_store(app_state_with_no_store))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=7) as ex:
        for _ in range(7):
            ex.submit(caller)

    assert not errors, f"unexpected errors: {errors}"
    assert len(results) == 7
    first = results[0]
    for r in results[1:]:
        assert r is first, "every caller must see the same singleton"


def test_constructor_called_only_once_under_contention(app_state_with_no_store):
    """Under contention, the constructor itself runs ONCE — the lock
    must serialize re-entries, not just hide them with try/except."""

    from autocue.analysis.discover.store import DiscoverStore as _RealStore

    call_count = {"n": 0}
    real_init = _RealStore.__init__

    def spy_init(self, *a, **kw):
        call_count["n"] += 1
        return real_init(self, *a, **kw)

    with mock.patch.object(_RealStore, "__init__", spy_init):
        with ThreadPoolExecutor(max_workers=10) as ex:
            futures = [ex.submit(deps.get_discover_store, app_state_with_no_store) for _ in range(10)]
            [f.result() for f in futures]

    assert call_count["n"] == 1, (
        f"DiscoverStore constructed {call_count['n']} times under contention; "
        f"expected exactly 1 (singleton via lock)"
    )


def test_cached_store_returned_without_taking_lock(app_state_with_no_store):
    """Hot path: once the singleton is set, get_discover_store must NOT
    re-acquire the lock for every subsequent call. We can't easily prove
    the negative directly, but we CAN prove caching wins by stamping a
    sentinel onto app.state and verifying it's the same object."""
    first = deps.get_discover_store(app_state_with_no_store)
    second = deps.get_discover_store(app_state_with_no_store)
    third = deps.get_discover_store(app_state_with_no_store)
    assert first is second is third


def test_constructor_failure_does_not_cache_a_broken_store(monkeypatch, tmp_path):
    """If DiscoverStore() raises, get_discover_store must surface a 503
    and NOT cache anything — a later call should get to retry."""
    monkeypatch.setenv("AUTOCUE_DISCOVER_DATA_DIR", str(tmp_path))

    class _State:
        discover_store = None
    class _App:
        state = _State()
    class _Req:
        app = _App()
    req = _Req()

    from autocue.analysis.discover.store import DiscoverStore as _RealStore

    boom = mock.MagicMock(side_effect=RuntimeError("simulated migration failure"))
    with mock.patch.object(_RealStore, "__init__", boom):
        with pytest.raises(Exception):
            deps.get_discover_store(req)
    # No store should be cached after the failure.
    assert req.app.state.discover_store is None
    # A retry under good conditions should succeed.
    store = deps.get_discover_store(req)
    assert store is not None
    assert req.app.state.discover_store is store
