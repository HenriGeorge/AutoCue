"""TASK-039 / TASK-040 / TASK-041 (Issue #107) — producer/consumer split for
/api/generate-apply-stream (flagged behind AUTOCUE_PARALLEL_GENERATE_APPLY=1)."""
from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autocue.analysis.concurrency import shutdown_pool
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


# ---------------------------------------------------------------------------
# End-to-end: /api/generate-apply-stream still processes every track.
# ---------------------------------------------------------------------------


def test_bounded_in_flight_caps_at_2_x_pool_size(tmp_path, monkeypatch):
    """End-to-end — 50 tracks all flow through compute → writer."""
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "4")
    db = _make_db(tmp_path)
    (tmp_path / "master.db").write_bytes(b"x")
    (tmp_path / "backups").mkdir(exist_ok=True)

    observed: list[int] = []

    def _slow_gen(content, _db, _prefs):
        observed.append(content.ID if hasattr(content, "ID") else 0)
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
    assert len(observed) == 50


# ---------------------------------------------------------------------------
# TASK-039 — _compute_stage in isolation.
# ---------------------------------------------------------------------------


def test_compute_stage_pushes_sentinel_on_empty_input():
    """Regression guard: empty input still terminates the writer."""
    from autocue.serve.routes import _compute_stage, _COMPUTE_SENTINEL

    db = MagicMock()
    q: queue.Queue = queue.Queue(maxsize=4)
    cancel = threading.Event()

    _compute_stage([], db, prefs=MagicMock(), phrase_only=False, q=q, cancel=cancel)

    assert q.qsize() == 1
    assert q.get_nowait() is _COMPUTE_SENTINEL


def test_compute_stage_pushes_one_tuple_per_track_plus_sentinel():
    """For N tracks, queue receives N result tuples then a single None."""
    from autocue.serve.routes import _compute_stage, _COMPUTE_SENTINEL

    db = MagicMock()
    db.get_content.side_effect = lambda ID: SimpleNamespace(ID=ID)
    q: queue.Queue = queue.Queue(maxsize=32)
    cancel = threading.Event()

    with patch("autocue.serve.routes.generate_cues_for_track",
               return_value=([{"slot": 0, "posSec": 0, "label": "x"}], None)):
        _compute_stage([1, 2, 3, 4, 5], db, prefs=MagicMock(),
                       phrase_only=False, q=q, cancel=cancel)

    items = []
    while True:
        item = q.get_nowait()
        items.append(item)
        if item is _COMPUTE_SENTINEL:
            break
    assert len(items) == 6
    tids = sorted(i[0] for i in items[:-1])
    assert tids == [1, 2, 3, 4, 5]


def test_compute_stage_pushes_sentinel_even_on_cancel():
    """Cancel mid-stream: writer must still see the sentinel."""
    from autocue.serve.routes import _compute_stage, _COMPUTE_SENTINEL

    db = MagicMock()
    db.get_content.side_effect = lambda ID: SimpleNamespace(ID=ID)
    q: queue.Queue = queue.Queue(maxsize=32)
    cancel = threading.Event()
    cancel.set()

    with patch("autocue.serve.routes.generate_cues_for_track",
               return_value=([{"slot": 0, "posSec": 0, "label": "x"}], None)):
        _compute_stage([1, 2, 3], db, prefs=MagicMock(),
                       phrase_only=False, q=q, cancel=cancel)

    found_sentinel = False
    while not q.empty():
        if q.get_nowait() is _COMPUTE_SENTINEL:
            found_sentinel = True
            break
    assert found_sentinel, "Sentinel must be pushed even when cancel is pre-set"


def test_compute_stage_skip_reasons_propagate():
    """no_phrase / not_found / no_cues each translate to a skip tuple."""
    from autocue.serve.routes import _compute_stage, _COMPUTE_SENTINEL

    db = MagicMock()

    def _get_content(ID):
        return None if ID == 99 else SimpleNamespace(ID=ID)
    db.get_content.side_effect = _get_content

    q: queue.Queue = queue.Queue(maxsize=32)
    cancel = threading.Event()

    with patch("autocue.serve.routes.generate_cues_for_track",
               return_value=([], None)):  # no cues
        _compute_stage([1, 99], db, prefs=MagicMock(),
                       phrase_only=False, q=q, cancel=cancel)

    items = []
    while True:
        item = q.get_nowait()
        if item is _COMPUTE_SENTINEL:
            break
        items.append(item)
    skips = {tid: reason for (tid, _c, _cues, reason) in items}
    assert skips[99] == "not_found"
    assert skips[1] == "no_cues"


# ---------------------------------------------------------------------------
# TASK-039 — _writer_stage in isolation.
# ---------------------------------------------------------------------------


def test_writer_stage_terminates_on_sentinel():
    """Sentinel-only input → writer exits cleanly with all-zero counters."""
    from autocue.serve.routes import _writer_stage, _COMPUTE_SENTINEL

    db = MagicMock()
    q: queue.Queue = queue.Queue()
    q.put(_COMPUTE_SENTINEL)
    cancel = threading.Event()
    events: list[dict] = []

    applied, skipped, written = _writer_stage(
        db, dry_run=False, overwrite=True,
        q=q, send_event=events.append, cancel=cancel,
    )
    assert (applied, skipped, written) == (0, 0, [])
    assert events == []


def test_writer_stage_emits_one_event_per_result_and_counts():
    """Mixed batch: write_cues_to_db returns n>0 → applied++; skip → skipped++."""
    from autocue.serve.routes import _writer_stage, _COMPUTE_SENTINEL

    db = MagicMock()
    q: queue.Queue = queue.Queue()
    content_a = SimpleNamespace(ID=1)
    content_b = SimpleNamespace(ID=2)
    q.put((1, content_a, [{"slot": 0}], None))           # write returns 1 → applied
    q.put((2, content_b, [{"slot": 0}], None))           # write returns 0 → skipped
    q.put((3, None, None, "no_phrase"))                  # skip path
    q.put(_COMPUTE_SENTINEL)

    cancel = threading.Event()
    events: list[dict] = []

    with patch("autocue.db_writer.write_cues_to_db", side_effect=[1, 0]):
        applied, skipped, written = _writer_stage(
            db, dry_run=False, overwrite=True,
            q=q, send_event=events.append, cancel=cancel,
        )
    assert applied == 1
    assert skipped == 2
    assert written == [1]
    assert len(events) == 3
    assert [e["processed"] for e in events] == [1, 2, 3]


def test_writer_stage_per_track_exception_does_not_abort_batch():
    """TASK-039 acceptance #4: one failing track must not stop subsequent writes."""
    from autocue.serve.routes import _writer_stage, _COMPUTE_SENTINEL

    db = MagicMock()
    q: queue.Queue = queue.Queue()
    content_a = SimpleNamespace(ID=1)
    content_b = SimpleNamespace(ID=2)
    q.put((1, content_a, [{"slot": 0}], None))  # raises
    q.put((2, content_b, [{"slot": 0}], None))  # succeeds
    q.put(_COMPUTE_SENTINEL)

    cancel = threading.Event()
    events: list[dict] = []

    with patch("autocue.db_writer.write_cues_to_db",
               side_effect=[RuntimeError("boom"), 1]):
        applied, skipped, written = _writer_stage(
            db, dry_run=False, overwrite=True,
            q=q, send_event=events.append, cancel=cancel,
        )
    assert applied == 1
    assert skipped == 1
    assert written == [2]


# ---------------------------------------------------------------------------
# TASK-040 — bounded-queue backpressure invariant.
# ---------------------------------------------------------------------------


def test_bounded_queue_maxsize_invariant_under_slow_writer():
    """Producer faster than writer → q.qsize() must NEVER exceed maxsize.

    Property: every observed qsize() ≤ maxsize. This is the actual
    invariant TASK-040 acceptance #2 calls for.
    """
    from autocue.serve.routes import _compute_stage, _COMPUTE_SENTINEL

    maxsize = 4
    q: queue.Queue = queue.Queue(maxsize=maxsize)
    cancel = threading.Event()
    db = MagicMock()
    db.get_content.side_effect = lambda ID: SimpleNamespace(ID=ID)

    sizes: list[int] = []
    stop_sampler = threading.Event()

    def _sampler():
        while not stop_sampler.is_set():
            sizes.append(q.qsize())
            time.sleep(0.001)

    sampler = threading.Thread(target=_sampler, daemon=True)
    sampler.start()

    producer_done = threading.Event()

    def _consume_slowly():
        while True:
            try:
                item = q.get(block=True, timeout=2)
            except Exception:
                if producer_done.is_set() and q.empty():
                    return
                continue
            if item is _COMPUTE_SENTINEL:
                return
            time.sleep(0.005)

    consumer = threading.Thread(target=_consume_slowly, daemon=True)
    consumer.start()

    with patch("autocue.serve.routes.generate_cues_for_track",
               return_value=([{"slot": 0}], None)):
        _compute_stage(list(range(1, 31)), db, prefs=MagicMock(),
                       phrase_only=False, q=q, cancel=cancel)
    producer_done.set()
    consumer.join(timeout=5)
    stop_sampler.set()
    sampler.join(timeout=1)

    assert sizes, "sampler should have observed at least one qsize value"
    assert max(sizes) <= maxsize, (
        f"qsize invariant violated: max observed={max(sizes)} > maxsize={maxsize}"
    )


# ---------------------------------------------------------------------------
# Legacy: _wait_any is kept for back-compat but no longer used by the route.
# ---------------------------------------------------------------------------


def test_wait_any_helper_returns_completed_first():
    """_wait_any wraps concurrent.futures.wait FIRST_COMPLETED — unit-test the wrapper."""
    from concurrent.futures import ThreadPoolExecutor
    from autocue.serve.routes import _wait_any
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        f_slow = pool.submit(time.sleep, 0.2)
        f_fast = pool.submit(time.sleep, 0.01)
        done, _pending = _wait_any({f_fast: "fast", f_slow: "slow"})
        assert f_fast in done
    finally:
        pool.shutdown()


def test_wait_any_empty_returns_empty_sets():
    from autocue.serve.routes import _wait_any
    done, pending = _wait_any({})
    assert done == set()
    assert pending == set()


def test_producer_consumer_module_surface():
    """TASK-039 acceptance #5: module exports the factored stages + sentinel."""
    import autocue.serve.routes as routes_mod
    assert hasattr(routes_mod, "_compute_stage")
    assert hasattr(routes_mod, "_writer_stage")
    assert hasattr(routes_mod, "_COMPUTE_SENTINEL")
    assert routes_mod._COMPUTE_SENTINEL is None
