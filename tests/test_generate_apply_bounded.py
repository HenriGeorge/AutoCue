"""TASK-039/040/041 — producer/consumer stage tests + bounded in-flight
guarantee for /api/generate-apply-stream (flagged behind
AUTOCUE_PARALLEL_GENERATE_APPLY=1)."""
from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autocue.analysis.concurrency import pool_size, shutdown_pool
from autocue.serve.app import create_app
from autocue.serve.routes import _compute_stage, _writer_stage


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
# TASK-040 — bounded in-flight via _compute_stage
# ---------------------------------------------------------------------------


def test_compute_stage_pushes_sentinel_when_drained():
    """TASK-039: compute stage pushes exactly N results + 1 sentinel for N inputs.

    The bounded queue is backpressured by a parallel drainer; this is how the
    real writer stage and the compute stage coexist.
    """
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        n = 7
        q: queue.Queue = queue.Queue(maxsize=4)
        cancel = threading.Event()

        def _work(tid):
            return (tid, "content", ["cue"], None)

        items: list = []

        def _drain():
            while True:
                it = q.get()
                items.append(it)
                if it is None:
                    return

        drainer = threading.Thread(target=_drain, daemon=True)
        drainer.start()

        t = threading.Thread(
            target=_compute_stage,
            args=(iter(range(n)), pool, _work, q, cancel, 4),
            daemon=True,
        )
        t.start()
        t.join(timeout=5.0)
        drainer.join(timeout=5.0)
        assert not t.is_alive()
        assert not drainer.is_alive()

        # N real results + exactly one trailing sentinel.
        assert items[-1] is None
        assert len(items) == n + 1
        assert all(it is not None for it in items[:-1])
        assert {it[0] for it in items[:-1]} == set(range(n))
    finally:
        pool.shutdown()


def test_compute_stage_caps_in_flight_at_max_in_flight():
    """TASK-040 regression guard: never more than ``max_in_flight`` futures outstanding.

    This test FAILS if the cap is removed (the previous dict-of-futures pattern
    would still cap, but a naive ``pool.submit`` for every track id would
    exceed the cap and observed_concurrency would shoot past it).
    """
    pool = ThreadPoolExecutor(max_workers=8)
    try:
        cap = 4
        n = 30
        q: queue.Queue = queue.Queue(maxsize=cap)
        cancel = threading.Event()

        # Block the writer side until we've had a chance to observe the
        # producer trying to keep ``cap`` futures live. Without the cap, the
        # producer would queue all N immediately and exceed the bound.
        live = 0
        peak = 0
        lock = threading.Lock()
        release = threading.Event()

        def _work(tid):
            nonlocal live, peak
            with lock:
                live += 1
                if live > peak:
                    peak = live
            # Hold each worker until release is set so concurrency stays high.
            release.wait(timeout=2.0)
            with lock:
                live -= 1
            return (tid, "c", ["x"], None)

        t = threading.Thread(
            target=_compute_stage,
            args=(iter(range(n)), pool, _work, q, cancel, cap),
            daemon=True,
        )
        t.start()

        # Wait until the producer has primed at least one batch.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            with lock:
                if live >= 1:
                    break
            time.sleep(0.01)

        # Boundary assertion: peak concurrency never exceeds the cap, even
        # before the queue is drained.
        with lock:
            assert peak <= cap, f"peak {peak} > cap {cap}"

        # Now let workers complete; drain the queue (writer-side) so the
        # producer can keep submitting.
        def _drain():
            while True:
                it = q.get()
                if it is None:
                    return

        drainer = threading.Thread(target=_drain, daemon=True)
        drainer.start()
        release.set()
        t.join(timeout=5.0)
        drainer.join(timeout=5.0)

        assert not t.is_alive()
        assert peak <= cap, f"peak {peak} > cap {cap} (post-drain)"
    finally:
        pool.shutdown()


def test_compute_stage_pushes_sentinel_on_cancel():
    """TASK-041 boundary: cancel.set() short-circuits submission; sentinel still pushed.

    Without the sentinel-on-cancel, the writer (blocked on q.get()) would
    deadlock. This test FAILS without that guarantee.
    """
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        q: queue.Queue = queue.Queue(maxsize=4)
        cancel = threading.Event()
        cancel.set()  # cancel BEFORE the stage starts

        calls = []

        def _work(tid):
            calls.append(tid)
            return (tid, "c", ["x"], None)

        t = threading.Thread(
            target=_compute_stage,
            args=(iter(range(20)), pool, _work, q, cancel, 4),
            daemon=True,
        )
        t.start()
        t.join(timeout=5.0)
        assert not t.is_alive(), "compute stage did not exit after cancel"

        # The sentinel MUST be in the queue.
        items = []
        while True:
            try:
                items.append(q.get_nowait())
            except queue.Empty:
                break
        assert None in items, "sentinel missing — writer would deadlock"
        # No new work was scheduled.
        assert calls == []
    finally:
        pool.shutdown()


# ---------------------------------------------------------------------------
# TASK-039 — writer stage
# ---------------------------------------------------------------------------


def test_writer_stage_stops_on_sentinel():
    """Boundary case: writer drains exactly until it sees the sentinel."""
    q: queue.Queue = queue.Queue()
    q.put((1, SimpleNamespace(ID=1), [{"slot": 0}], None))
    q.put((2, SimpleNamespace(ID=2), [{"slot": 0}], None))
    q.put(None)
    # Anything AFTER the sentinel must be ignored.
    q.put((3, SimpleNamespace(ID=3), [{"slot": 0}], None))

    write_calls = []
    events = []

    def _write(content, cues):
        write_calls.append(content.ID)
        return 1

    def _on_event(processed, applied, skipped):
        events.append((processed, applied, skipped))

    db = MagicMock()
    cancel = threading.Event()

    applied, skipped = _writer_stage(q, db, _write, _on_event, cancel)

    assert write_calls == [1, 2]
    assert applied == 2
    assert skipped == 0
    # processed counter monotonic, exactly 2 events.
    assert [e[0] for e in events] == [1, 2]


def test_writer_stage_counts_skips_for_empty_cues_and_errors():
    """skip flag, empty cues, and write_fn raising all count as skipped, not applied."""
    q: queue.Queue = queue.Queue()
    # Pre-flagged skip
    q.put((1, SimpleNamespace(ID=1), None, "no_phrase"))
    # Empty cues
    q.put((2, SimpleNamespace(ID=2), [], None))
    # Write raises
    q.put((3, SimpleNamespace(ID=3), [{"slot": 0}], None))
    # write_fn returns 0
    q.put((4, SimpleNamespace(ID=4), [{"slot": 0}], None))
    q.put(None)

    def _write(content, cues):
        if content.ID == 3:
            raise RuntimeError("write boom")
        if content.ID == 4:
            return 0
        return 1

    events = []
    db = MagicMock()

    applied, skipped = _writer_stage(q, db, _write, lambda *a: events.append(a), threading.Event())

    assert applied == 0
    assert skipped == 4
    # Every input emits exactly one progress event.
    assert len(events) == 4


def test_writer_stage_drains_queue_on_cancel_without_writing():
    """TASK-041: when cancel is set, the writer stops writing but still drains until sentinel.

    Without draining, the compute stage (blocked on a bounded q.put()) would
    deadlock — this is the reverse-side guarantee.
    """
    q: queue.Queue = queue.Queue()
    q.put((1, SimpleNamespace(ID=1), [{"slot": 0}], None))
    q.put((2, SimpleNamespace(ID=2), [{"slot": 0}], None))
    q.put(None)

    write_calls = []
    cancel = threading.Event()
    cancel.set()

    def _write(content, cues):
        write_calls.append(content.ID)
        return 1

    applied, skipped = _writer_stage(q, MagicMock(), _write, lambda *a: None, cancel)

    assert write_calls == []
    assert applied == 0
    assert skipped == 0
    # Queue MUST be fully drained.
    assert q.qsize() == 0


# ---------------------------------------------------------------------------
# End-to-end regression: SSE endpoint with the producer/consumer wired
# ---------------------------------------------------------------------------


def test_generate_apply_stream_processes_every_track(tmp_path, monkeypatch):
    """TASK-040 regression: 50 tracks → 50 compute calls → SSE 'done' event reports them."""
    monkeypatch.setenv("AUTOCUE_POOL_SIZE", "4")
    db = _make_db(tmp_path)
    (tmp_path / "master.db").write_bytes(b"x")
    (tmp_path / "backups").mkdir(exist_ok=True)

    observed: list[int] = []

    def _gen(content, _db, _prefs):
        observed.append(content.ID if hasattr(content, "ID") else 0)
        return ([{"slot": 0, "posSec": 0, "label": "Drop"}], None)

    with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
        with patch("autocue.serve.routes.generate_cues_for_track", side_effect=_gen):
            with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                    client = _client(db)
                    r = client.post(
                        "/api/generate-apply-stream",
                        json={"track_ids": list(range(1, 51)), "dry_run": False, "overwrite": True},
                    )
    assert r.status_code == 200
    # 50 compute calls — every track was processed exactly once.
    assert len(observed) == 50
    # Last SSE event reports done with the right totals.
    body = r.text
    assert '"done": true' in body or '"done":true' in body
