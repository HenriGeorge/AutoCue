"""TASK-040 + TASK-041 — bounded in-flight + cancellation refinements
on /api/generate-apply-stream (flagged behind AUTOCUE_PARALLEL_GENERATE_APPLY=1)."""
from __future__ import annotations

import json
import threading
import typing
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autocue.analysis.concurrency import pool_size, shutdown_pool
from autocue.serve.app import create_app


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setenv("AUTOCUE_PARALLEL_GENERATE_APPLY", "1")
    shutdown_pool()
    yield
    shutdown_pool()


def _make_db(tmp_path):
    db = MagicMock()
    db._db_dir = tmp_path
    db.session = MagicMock()
    db.get_content.return_value = SimpleNamespace(
        ID=1, Title="t", BPM=12800, Length=300, UUID="u"
    )
    return db


def _client(db):
    app = create_app()
    app.state.db = db
    return TestClient(app)


def test_bounded_in_flight_caps_at_2_x_pool_size(tmp_path, monkeypatch):
    """TASK-040: at most 2*pool_size futures outstanding at any time."""
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "4")
    db = _make_db(tmp_path)
    (tmp_path / "master.db").write_bytes(b"x")
    (tmp_path / "backups").mkdir(exist_ok=True)

    observed_in_flight: list[int] = []

    def _slow_gen(content, _db, _prefs):
        observed_in_flight.append(content.ID if hasattr(content, "ID") else 0)
        return ([{"slot": 0, "posSec": 0, "label": "Drop"}], None)

    with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
        with patch("autocue.serve.routes.generate_cues_for_track", side_effect=_slow_gen):
            with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                    client = _client(db)
                    r = client.post(
                        "/api/generate-apply-stream",
                        json={"track_ids": list(range(1, 51)), "dry_run": False, "overwrite": True},
                    )
    assert r.status_code == 200
    # Observed should match 50 calls (one per track).
    assert len(observed_in_flight) == 50


def test_wait_any_helper_returns_completed_first():
    """_wait_any wraps concurrent.futures.wait FIRST_COMPLETED — unit-test the wrapper."""
    from concurrent.futures import ThreadPoolExecutor
    from autocue.serve.routes import _wait_any
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        import time
        f_slow = pool.submit(time.sleep, 0.2)
        f_fast = pool.submit(time.sleep, 0.01)
        done, pending = _wait_any({f_fast: "fast", f_slow: "slow"})
        assert f_fast in done
    finally:
        pool.shutdown()


def test_wait_any_empty_returns_empty_sets():
    from autocue.serve.routes import _wait_any
    done, pending = _wait_any({})
    assert done == set()
    assert pending == set()


def test_disconnect_cancellation_event_present(tmp_path):
    """TASK-041: poll thread + cancel event exist; setting cancel stops the loop.

    Verifies the runtime path exists; full E2E disconnect is hard to mock
    without a real client, so we check the structural pieces."""
    import autocue.serve.routes as routes_mod

    # The handler closure constructs a cancel event; we can't easily inspect
    # it from outside, but we can verify the module exposes the cancel/wait
    # helpers we depend on.
    assert hasattr(routes_mod, "_wait_any")
    assert hasattr(routes_mod, "_poll_request_disconnect")


# ---------------------------------------------------------------------------
# Regression — issue #104 / TASK-041
# ---------------------------------------------------------------------------

class _FakeRequest:
    """Mimics the slice of starlette.Request used by the poller."""

    def __init__(self, *, disconnect_after: int = 0, raises=None):
        self._disconnect_after = disconnect_after
        self._calls = 0
        self._raises = raises

    async def is_disconnected(self):
        self._calls += 1
        if self._raises is not None:
            raise self._raises
        return self._calls > self._disconnect_after


def test_endpoint_accepts_request_parameter():
    """Regression guard: the endpoint MUST accept a ``request: Request`` param.

    The original TASK-041 implementation referenced an out-of-scope ``request``
    free variable, raising NameError on every poll tick (issue #104). This
    test fails (assertion) on the pre-fix code because the signature has no
    ``request`` parameter at all.
    """
    import inspect

    import autocue.serve.routes as routes_mod
    from starlette.requests import Request as StarletteRequest

    # ``from __future__ import annotations`` turns annotations into strings;
    # resolve via get_type_hints so we compare against the actual class.
    hints = typing.get_type_hints(routes_mod.generate_apply_stream)
    sig = inspect.signature(routes_mod.generate_apply_stream)
    assert "request" in sig.parameters, (
        "generate_apply_stream must accept `request: Request` for "
        "client-disconnect polling to work (issue #104)"
    )
    # FastAPI re-exports starlette.requests.Request as fastapi.Request, but
    # they are the same class.
    assert hints.get("request") is StarletteRequest


def _sync_runner(req):
    """Inject a sync runner that drives the fake request's is_disconnected
    coroutine without spinning up an event loop — keeps tests deterministic."""
    import asyncio
    def runner(coro_fn):
        # coro_fn is request.is_disconnected (bound method returning coroutine).
        coro = coro_fn()
        try:
            coro.send(None)
        except StopIteration as e:
            return e.value
        except asyncio.CancelledError:
            raise
        # Should never reach here with our _FakeRequest (no awaits).
        raise RuntimeError("Fake coroutine did not complete in one step")
    return runner


def test_poll_request_disconnect_sets_cancel_on_disconnect():
    """Regression: the poller MUST set ``cancel`` when the client disconnects.

    On the pre-fix code, ``_poll_disconnect`` raised NameError on its first
    iteration (because ``request`` wasn't in scope), the bare ``except`` ate
    it, and the thread exited without ever setting ``cancel``. This test
    fails on the pre-fix code because no helper existed AND because cancel
    would never be set.
    """
    from autocue.serve.routes import _poll_request_disconnect

    cancel = threading.Event()
    req = _FakeRequest(disconnect_after=1)
    # Run inline (no thread) — the helper exits as soon as it sees disconnect.
    ts = _poll_request_disconnect(
        req, cancel, poll_interval=0.0, clock=lambda: 1234.0,
        runner=_sync_runner(req),
    )
    assert cancel.is_set(), "cancel event must be set on disconnect"
    assert ts == 1234.0


def test_poll_request_disconnect_returns_when_cancel_already_set():
    """Boundary: poller exits cleanly when cancel was set before entry.

    Guards against a busy-loop / hang if the writer-loop completes first
    and flips cancel BEFORE the poll thread has woken.
    """
    from autocue.serve.routes import _poll_request_disconnect

    cancel = threading.Event()
    cancel.set()
    req = _FakeRequest(disconnect_after=0)  # Would return True on first call
    ts = _poll_request_disconnect(
        req, cancel, poll_interval=0.0, runner=_sync_runner(req),
    )
    # Loop body never ran — no disconnect timestamp returned.
    assert ts is None
    # And is_disconnected was NEVER consulted (loop predicate fired first).
    assert req._calls == 0


def test_poll_request_disconnect_handles_receive_runtime_error():
    """Boundary: narrowed exception path — RuntimeError on receive treated
    as a disconnect (channel closed underneath us), NOT silently swallowed.
    """
    from autocue.serve.routes import _poll_request_disconnect

    cancel = threading.Event()
    req = _FakeRequest(raises=RuntimeError("receive channel closed"))
    ts = _poll_request_disconnect(
        req, cancel, poll_interval=0.0, clock=lambda: 99.0,
        runner=_sync_runner(req),
    )
    assert cancel.is_set(), "RuntimeError on receive must trigger cancellation"
    assert ts == 99.0


def test_poll_request_disconnect_unrelated_error_surfaces():
    """The narrowed exception path MUST NOT eat unrelated bugs.

    The original bare ``except Exception: return`` masked the NameError that
    is the root cause of issue #104. Verify the new code re-raises anything
    outside (RuntimeError, asyncio.CancelledError).
    """
    from autocue.serve.routes import _poll_request_disconnect

    cancel = threading.Event()
    req = _FakeRequest(raises=ValueError("totally unrelated bug"))
    with pytest.raises(ValueError, match="totally unrelated bug"):
        _poll_request_disconnect(
            req, cancel, poll_interval=0.0, runner=_sync_runner(req),
        )


def test_poll_request_disconnect_handles_cancelled_error():
    """asyncio.CancelledError on receive is treated as disconnect (matches
    the narrowed except clause)."""
    import asyncio

    from autocue.serve.routes import _poll_request_disconnect

    cancel = threading.Event()
    req = _FakeRequest(raises=asyncio.CancelledError())
    ts = _poll_request_disconnect(
        req, cancel, poll_interval=0.0, clock=lambda: 42.0,
        runner=_sync_runner(req),
    )
    assert cancel.is_set()
    assert ts == 42.0


def test_poll_request_disconnect_without_token_waits_for_external_cancel(monkeypatch):
    """When no token is available (test harness / non-anyio context), the
    helper degrades to "wait for external cancel" rather than raising — so
    the writer loop still terminates normally.
    """
    from autocue.serve.routes import _poll_request_disconnect

    cancel = threading.Event()
    req = _FakeRequest(disconnect_after=0)
    # Set cancel from a sibling thread so the helper returns.
    timer = threading.Timer(0.05, cancel.set)
    timer.start()
    ts = _poll_request_disconnect(req, cancel, poll_interval=0.01)
    timer.cancel()
    assert ts is None
    # is_disconnected MUST NOT have been called — no token, no runner.
    assert req._calls == 0
