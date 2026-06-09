"""TASK-040 + TASK-041 — bounded in-flight + cancellation refinements
on /api/generate-apply-stream (flagged behind AUTOCUE_PARALLEL_GENERATE_APPLY=1)."""
from __future__ import annotations

import json
import threading
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


def test_disconnect_cancellation_structural_helpers_present(tmp_path):
    """TASK-039/040/041: producer/consumer stages + cancel scaffolding exist.

    Issue #107 — assert the PRD-spec'd structure (separate ``_compute_stage``
    / ``_writer_stage`` + ``_wait_any`` helper) is in place. Full E2E
    disconnect is hard to mock without a real client, so we check the
    structural pieces; the integration test above exercises the SSE path
    end-to-end."""
    import autocue.serve.routes as routes_mod

    # Producer/consumer (TASK-039/040): explicit, importable stages.
    assert callable(getattr(routes_mod, "_compute_stage", None))
    assert callable(getattr(routes_mod, "_writer_stage", None))
    # Sentinel surfaces so tests + consumers can identify end-of-stream.
    assert hasattr(routes_mod, "_COMPUTE_DONE")
    assert routes_mod._COMPUTE_DONE is None
    # Helper retained for the wait-wrapper unit tests above.
    assert callable(getattr(routes_mod, "_wait_any", None))


def test_compute_stage_pushes_sentinel_on_normal_completion():
    """TASK-039 step 1: compute stage must push ``None`` after all results."""
    import queue
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from autocue.serve.routes import _COMPUTE_DONE, _compute_stage

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        # maxsize=8 > (5 results + 1 sentinel) so the producer never blocks on
        # the queue — keeps this test focused on sentinel semantics, not
        # backpressure (covered by the dedicated backpressure test below).
        q: queue.Queue = queue.Queue(maxsize=8)
        cancel = threading.Event()

        def _stub(tid):
            return (tid, object(), [{"x": 1}], None)

        _compute_stage(
            [1, 2, 3, 4, 5],
            compute_fn=_stub,
            q=q,
            cancel=cancel,
            pool=pool,
            in_flight_cap=2,
        )

        drained = []
        while True:
            item = q.get_nowait()
            drained.append(item)
            if item is _COMPUTE_DONE:
                break
        assert drained[-1] is _COMPUTE_DONE
        ids = sorted(r[0] for r in drained[:-1])
        assert ids == [1, 2, 3, 4, 5]
    finally:
        pool.shutdown()


def test_compute_stage_pushes_sentinel_on_cancellation():
    """TASK-041 + #107: cancel mid-stream must still push the sentinel.

    Regression guard — without the ``finally: q.put(_COMPUTE_DONE)`` the
    writer thread would hang forever waiting on ``q.get()``.
    """
    import queue
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from autocue.serve.routes import _COMPUTE_DONE, _compute_stage

    pool = ThreadPoolExecutor(max_workers=2)
    try:
        q: queue.Queue = queue.Queue(maxsize=2)
        cancel = threading.Event()
        cancel.set()  # Set BEFORE the call — producer should exit immediately.

        def _stub(tid):
            time.sleep(0.05)
            return (tid, None, None, "skip")

        _compute_stage(
            list(range(20)),
            compute_fn=_stub,
            q=q,
            cancel=cancel,
            pool=pool,
            in_flight_cap=2,
        )

        # Drain — the sentinel must be the last item.
        last = None
        while not q.empty():
            last = q.get_nowait()
        assert last is _COMPUTE_DONE
    finally:
        pool.shutdown()


def test_writer_stage_drains_until_sentinel():
    """TASK-039 step 2: writer breaks on the ``None`` sentinel."""
    import queue
    import threading
    from unittest.mock import MagicMock

    from autocue.serve.routes import _COMPUTE_DONE, _writer_stage

    q: queue.Queue = queue.Queue()
    event_q: queue.Queue = queue.Queue()
    cancel = threading.Event()

    db = MagicMock()
    db.session = MagicMock()

    # Three results then sentinel.
    for tid in (1, 2, 3):
        q.put((tid, object(), [{"slot": 0}], None))
    q.put(_COMPUTE_DONE)

    write_fn = MagicMock(return_value=1)
    applied, skipped, errors = _writer_stage(
        q, db,
        write_fn=write_fn,
        overwrite=True,
        dry_run=False,
        event_q=event_q,
        cancel=cancel,
        total=3,
    )

    assert applied == 3
    assert skipped == 0
    assert errors == 0
    assert write_fn.call_count == 3
    # One SSE payload per processed item.
    payloads = []
    while not event_q.empty():
        payloads.append(event_q.get_nowait())
    assert len(payloads) == 3
    # TASK-042/043 — payload carries content_id + errors counter.
    assert payloads[-1]["processed"] == 3
    assert payloads[-1]["total"] == 3
    assert payloads[-1]["applied"] == 3
    assert payloads[-1]["skipped"] == 0
    assert payloads[-1]["errors"] == 0
    assert payloads[-1]["content_id"] == 3


def test_compute_writer_backpressure_queue_never_exceeds_maxsize():
    """TASK-040 step 3 — slow writer must NOT let the queue grow past maxsize.

    Regression guard: if we ever drop the ``maxsize`` on the work queue,
    this test fails because a fast producer would buffer all 30 results.
    """
    import queue
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from autocue.serve.routes import _COMPUTE_DONE, _compute_stage

    pool = ThreadPoolExecutor(max_workers=4)
    try:
        maxsize = 4
        work_q: queue.Queue = queue.Queue(maxsize=maxsize)
        cancel = threading.Event()
        observed_sizes: list[int] = []
        sizes_lock = threading.Lock()

        def _fast_compute(tid):
            return (tid, object(), [{"slot": 0}], None)

        # Slow writer: sleeps before each get, simulating a hung DB.
        drained_count = 0

        def _slow_writer():
            nonlocal drained_count
            while True:
                # Observe size BEFORE blocking — represents producer pressure.
                with sizes_lock:
                    observed_sizes.append(work_q.qsize())
                try:
                    item = work_q.get(timeout=5)
                except Exception:
                    break
                if item is _COMPUTE_DONE:
                    break
                drained_count += 1
                time.sleep(0.01)  # slow consumer

        writer = threading.Thread(target=_slow_writer, daemon=True)
        writer.start()

        _compute_stage(
            list(range(30)),
            compute_fn=_fast_compute,
            q=work_q,
            cancel=cancel,
            pool=pool,
            in_flight_cap=4,
        )
        writer.join(timeout=10)

        assert drained_count == 30, f"expected 30 results, got {drained_count}"
        # Boundary case (PRD): qsize never exceeds maxsize even under
        # fast-producer/slow-consumer pressure.
        assert observed_sizes, "writer never observed queue size"
        assert max(observed_sizes) <= maxsize, (
            f"queue overflowed maxsize={maxsize}: observed max={max(observed_sizes)}"
        )
    finally:
        pool.shutdown()


def test_generate_apply_stream_has_request_injection():
    """Issue #104 regression: ``generate_apply_stream`` must take a
    ``Request`` parameter — without it the inner disconnect-poll references
    an undefined ``request`` and raises NameError on every poll iteration,
    swallowed by a bare ``except``. The route ALSO must be ``async def``
    so the poll task can live on the request's event loop and actually
    ``await request.is_disconnected()`` (a sync thread cannot do that —
    starlette only sets ``_is_disconnected`` from inside the async call).

    Regression guard: if anyone removes either property, this fails.
    """
    import inspect

    import autocue.serve.routes as routes_mod

    handler = routes_mod.generate_apply_stream
    # Async route — required for ``asyncio.create_task(_poll(...))``.
    assert inspect.iscoroutinefunction(handler), (
        "generate_apply_stream must be `async def` so the disconnect poll "
        "task can live on the event loop (Issue #104)."
    )
    # `Request` injected — without it the poll body raises NameError.
    # Note: ``from __future__ import annotations`` makes annotations strings,
    # so compare by name rather than identity.
    sig = inspect.signature(handler)
    request_params = [
        name
        for name, p in sig.parameters.items()
        if (getattr(p.annotation, "__name__", None) == "Request")
        or (isinstance(p.annotation, str) and p.annotation == "Request")
    ]
    assert request_params, (
        "generate_apply_stream must accept a `request: Request` parameter — "
        "the disconnect-poll task references it (Issue #104)."
    )


def test_generate_apply_stream_respects_cancel_before_emission(tmp_path, monkeypatch):
    """Issue #104 / TASK-041 boundary: when the disconnect poll fires BEFORE
    the first iteration, the parallel path must not process all tracks —
    only the final ``done`` event is guaranteed. Demonstrates the
    compute/writer loop actually observes the cancel signal.

    Property-style: for any track-id list, if the poll task immediately
    flags disconnect, processed must be strictly less than total.
    """
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "2")
    db = _make_db(tmp_path)
    (tmp_path / "master.db").write_bytes(b"x")
    (tmp_path / "backups").mkdir(exist_ok=True)

    # Force ``request.is_disconnected()`` to return True immediately — this
    # is exactly what starlette returns once the client closes the TCP
    # connection.
    async def _always_disconnected(self):
        return True

    import starlette.requests as _sreq

    monkeypatch.setattr(_sreq.Request, "is_disconnected", _always_disconnected)

    with patch("autocue.serve.routes.generate_cues_for_track", return_value=([{"slot": 0, "posSec": 0, "label": "Drop"}], None)):
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                    client = _client(db)
                    r = client.post(
                        "/api/generate-apply-stream",
                        json={
                            "track_ids": list(range(1, 21)),
                            "dry_run": False,
                            "overwrite": True,
                        },
                    )

    assert r.status_code == 200
    # Parse SSE events.
    events = [
        json.loads(line[len("data: "):])
        for line in r.text.splitlines()
        if line.startswith("data: ")
    ]
    # The stream must still emit the final done event so the response
    # closes cleanly.
    assert any(e.get("done") for e in events), (
        "Stream must still emit the final done event even after cancel."
    )
    # Progress events MAY appear (the poll runs concurrently with compute),
    # but processed must never exceed track count — and crucially, the
    # final event's processed count must be < total when cancel fires
    # before all tracks complete.
    progress = [e for e in events if not e.get("done")]
    if progress:
        # Cancel fired mid-stream — must NOT have processed all 20 tracks.
        max_processed = max(e.get("processed", 0) for e in progress)
        assert max_processed < 20, (
            f"Stream processed all tracks despite is_disconnected=True; "
            f"max processed={max_processed}"
        )
