"""Issue #108 — real /api/tracks warm + cold p95 against a 10k synthetic library.

Replaces the toy ``test_snapshot_hit_p95_under_50ms`` benchmark (kept alongside
as a micro-bench of the snapshot fast-path) with a benchmark that drives the
actual route end-to-end:

* ``/api/tracks`` is invoked via ``fastapi.testclient.TestClient``
* the route runs the real ``pyrekordbox.db6.Rekordbox6Database`` ORM query,
  the five pre-fetch queries, and the per-row ``_to_item`` mapping
* warm = snapshot hit (PRD §6 ≤ 200 ms p95)
* cold = snapshot cleared between each call (PRD §6 ≤ 800 ms p95)

Both tests are gated by ``@pytest.mark.perf`` (already RUN_PERF=1 in
``tests/conftest.py``) so they don't run on default CI.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.perf


# Process-scoped: building the 10k fixture takes ~400 ms and we want both
# tests in this module to share it.
@pytest.fixture(scope="module")
def synthetic_db_dir(tmp_path_factory):
    from tests.fixtures.synthetic_rb_db import build_synthetic_library

    db_dir = tmp_path_factory.mktemp("synthetic_rb")
    build_synthetic_library(db_dir, track_count=10_000)
    return db_dir


@pytest.fixture()
def app_with_synthetic_db(synthetic_db_dir):
    """Wire ``create_app()`` to two pyrekordbox handles against the fixture.

    Returns ``(app, client)``. ``app.state.tracks_snapshot`` is initialised
    to ``None`` — callers control whether the first request runs cold or
    warm-with-prewarm.
    """
    # Local imports so module collection doesn't pay the cost on RUN_PERF unset.
    from fastapi.testclient import TestClient

    from autocue.serve.app import create_app
    from autocue.serve.deps import get_db, get_ro_db
    from tests.fixtures.synthetic_rb_db import open_synthetic_db

    db = open_synthetic_db(synthetic_db_dir)
    ro = open_synthetic_db(synthetic_db_dir)

    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_ro_db] = lambda: ro

    app.state.db = db
    app.state.ro_db = ro
    app.state.tracks_snapshot = None
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.cache_store = None

    # TestClient triggers lifespan, which tries to open a real Rekordbox
    # library via the master config. Bypass it: route the import to the
    # synthetic DB. We undo the patch in the finally block.
    import pyrekordbox

    real_cls = pyrekordbox.Rekordbox6Database

    def _factory(*args, **kwargs):
        return open_synthetic_db(synthetic_db_dir)

    pyrekordbox.Rekordbox6Database = _factory
    try:
        client = TestClient(app)
        yield app, client
    finally:
        pyrekordbox.Rekordbox6Database = real_cls
        try:
            db.close()
        except Exception:
            pass
        try:
            ro.close()
        except Exception:
            pass


def _p95(samples: list[float]) -> float:
    samples = sorted(samples)
    return samples[int(0.95 * len(samples)) - 1]


def test_warm_p95_under_200ms_against_10k_library(app_with_synthetic_db):
    """PRD §6 warm budget — ≤ 200 ms p95 on a 10 k library.

    Snapshot is built by the first request and reused by the rest, which
    matches the real warm-path lifecycle: one cold hit per master.db mtime
    change, then snapshot service until the next mutation.
    """
    app, client = app_with_synthetic_db

    # Warmup — builds + caches the snapshot.
    r = client.get("/api/tracks?limit=10000")
    assert r.status_code == 200
    assert len(r.json()) == 10_000
    assert app.state.tracks_snapshot is not None, (
        "warm path requires the snapshot to be populated after the first request"
    )

    durations: list[float] = []
    for _ in range(50):
        t0 = time.perf_counter()
        r = client.get("/api/tracks?limit=10000")
        durations.append((time.perf_counter() - t0) * 1000.0)
        assert r.status_code == 200

    p95 = _p95(durations)
    # PRD §6 ceiling — the SLA budget the route exists to satisfy.
    assert p95 < 200.0, (
        f"warm p95 {p95:.1f}ms exceeded PRD 200ms budget "
        f"(p50={sorted(durations)[len(durations)//2]:.1f}ms, "
        f"max={max(durations):.1f}ms, n={len(durations)})"
    )


def test_cold_p95_under_800ms_against_10k_library(app_with_synthetic_db):
    """PRD §6 cold budget — ≤ 800 ms p95 on a 10 k library.

    Clears the snapshot between every request so each hit runs the full SQL
    pipeline: ORM ``get_content().order_by().all()`` + 5 prefetch queries +
    ``_to_item`` × 10 000.
    """
    app, client = app_with_synthetic_db

    # One warmup pass — pyrekordbox lazy-loads some metadata on first query;
    # we don't want that one-shot cost polluting the cold p95 sample.
    r = client.get("/api/tracks?limit=10000")
    assert r.status_code == 200

    durations: list[float] = []
    for _ in range(10):
        app.state.tracks_snapshot = None  # force the SQL pipeline
        t0 = time.perf_counter()
        r = client.get("/api/tracks?limit=10000")
        durations.append((time.perf_counter() - t0) * 1000.0)
        assert r.status_code == 200
        assert len(r.json()) == 10_000

    p95 = _p95(durations)
    # PRD §6 ceiling. The cold path has less margin than warm; this is the
    # regression guard that catches an _to_item or prefetch regression.
    assert p95 < 800.0, (
        f"cold p95 {p95:.1f}ms exceeded PRD 800ms budget "
        f"(p50={sorted(durations)[len(durations)//2]:.1f}ms, "
        f"max={max(durations):.1f}ms, n={len(durations)})"
    )


def test_synthetic_fixture_smoke():
    """Regression guard: fixture-builder API surface stays callable.

    Without this, a refactor that breaks ``build_synthetic_library`` would
    only surface when someone ran ``RUN_PERF=1`` — which is rare. This
    sanity check runs alongside the perf benches (also gated), at a smaller
    track count so it's cheap.
    """
    import tempfile

    from tests.fixtures.synthetic_rb_db import (
        build_synthetic_library,
        open_synthetic_db,
    )

    with tempfile.TemporaryDirectory() as td:
        db_path = build_synthetic_library(Path(td), track_count=50)
        assert db_path.exists()
        db = open_synthetic_db(Path(td))
        try:
            assert db.get_content().count() == 50
            # First row's ArtistName traverses the relationship — verifies
            # the foreign-key joins are walkable.
            first = db.get_content().first()
            assert first.ArtistName.startswith("Artist ")
        finally:
            db.close()
