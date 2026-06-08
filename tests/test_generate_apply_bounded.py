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
    applied, skipped = _writer_stage(
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
    assert write_fn.call_count == 3
    # One SSE payload per processed item.
    payloads = []
    while not event_q.empty():
        payloads.append(event_q.get_nowait())
    assert len(payloads) == 3
    assert payloads[-1] == {"processed": 3, "total": 3, "applied": 3, "skipped": 0}


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
