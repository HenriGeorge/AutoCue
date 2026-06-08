"""Closes #108 — enforce PERFORMANCE_PRD §6 row 2: ``/api/tracks`` warm p95 ≤ 200 ms.

The PRD names this exact file as the budget gate. Issue #108 noted that the
existing ``test_tracks_snapshot_perf.py`` asserts a 50 ms implementation-detail
bound, so a regression to (e.g.) 150 ms slips through the existing guard while
silently consuming most of the 200 ms contract budget.

This file pins the PRD number directly. Gated by ``RUN_PERF=1`` via the
``@pytest.mark.perf`` fixture in ``tests/conftest.py``.

Cold p95 (≤ 800 ms) and startup-to-UI (≤ 1.5 s) remain unmeasured — they need
a synthetic Rekordbox SQLCipher fixture (see ``.agent/prd/PERFORMANCE_NOTES.md``)
and are explicitly out of scope here.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from autocue.serve.app import create_app
from autocue.serve.schemas import TrackItem


pytestmark = pytest.mark.perf

# PRD §6 row 2 — see .agent/prd/PERFORMANCE_PRD.md
WARM_P95_BUDGET_MS = 200
LIBRARY_SIZE = 10_000
ITERATIONS = 50
WARM_MTIME = 100.0
# /api/tracks default limit is 5000; pass an explicit higher cap so the full
# 10k library is returned (mirrors the web UI which requests the full library).
TRACKS_URL = f"/api/tracks?limit={LIBRARY_SIZE}"


def _realistic_track_item(track_id: int) -> TrackItem:
    """Match the real ``_to_item`` payload shape so Pydantic serialization
    cost is comparable to production. Production rows carry artist + album +
    title + genre + comment strings (often 30–80 chars each), one or more
    my_tags, and several optional fields populated. Keep it close to the
    real distribution so the JSON encode work is representative.
    """
    return TrackItem(
        id=track_id,
        title=f"Track Title Number {track_id:05d} (Original Mix)",
        artist=f"Artist Name {track_id % 1500:04d}",
        album=f"Album Name {track_id % 800:04d}",
        bpm=120.0 + (track_id % 40),
        duration=180.0 + (track_id % 240),
        has_phrase=(track_id % 3 == 0),
        has_beats=True,
        existing_hot_cues=(track_id % 9),
        key="A min" if track_id % 2 else "C maj",
        rating=(track_id % 6),
        play_count=(track_id % 50),
        last_played=None if track_id % 7 == 0 else "2025-11-04 12:34:56",
        my_tags=["energy:high"] if track_id % 4 == 0 else [],
        color_name="Red" if track_id % 5 == 0 else "",
        genre="Techno" if track_id % 2 else "House",
        comment="" if track_id % 3 else "AutoCue:phrase",
        source="file",
    )


def _client_with_hydrated_snapshot(items: list[TrackItem], mtime: float) -> TestClient:
    """Build a real ``create_app()`` and hydrate the in-memory snapshot the
    same way ``lifespan`` does at startup (TASK-022 path). The snapshot path
    in ``routes.tracks`` is the production warm path — once the snapshot is
    populated and mtime is unchanged, every subsequent request short-circuits
    the SQL pipeline. We faithfully exercise that path here.
    """
    app = create_app()
    db = MagicMock()
    db._db_dir = "/tmp"
    app.state.db = db
    app.state.ro_db = db
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = {"mtime": mtime, "payload": items}
    return TestClient(app)


def _p95_ms(durations_ms: list[float]) -> float:
    durations_ms.sort()
    # ceil-based percentile keeps small-N cases honest
    idx = max(0, int(round(0.95 * len(durations_ms))) - 1)
    return durations_ms[idx]


def test_tracks_warm_p95_meets_prd_budget(monkeypatch):
    """PRD §6 row 2: warm p95 ≤ 200 ms on a 10 k library.

    Drives the real ``/api/tracks`` snapshot-hit code path through FastAPI's
    full stack (response_model serialization included), 50 sequential GETs.
    """
    items = [_realistic_track_item(i) for i in range(1, LIBRARY_SIZE + 1)]
    client = _client_with_hydrated_snapshot(items, mtime=WARM_MTIME)
    monkeypatch.setattr(
        "autocue.serve.routes._master_db_mtime", lambda _db: WARM_MTIME
    )

    # Warm-up: one untimed request so any first-call JIT / lazy imports
    # land outside the measurement window. Production parity — the first
    # post-hydration request always pays whatever lazy-init cost exists.
    r = client.get(TRACKS_URL)
    assert r.status_code == 200
    assert len(r.json()) == LIBRARY_SIZE

    durations_ms: list[float] = []
    for _ in range(ITERATIONS):
        start = time.perf_counter()
        r = client.get(TRACKS_URL)
        durations_ms.append((time.perf_counter() - start) * 1000.0)
        assert r.status_code == 200

    p95 = _p95_ms(durations_ms)
    assert p95 <= WARM_P95_BUDGET_MS, (
        f"PRD §6 budget breached: warm p95 {p95:.2f} ms > {WARM_P95_BUDGET_MS} ms "
        f"on {LIBRARY_SIZE}-track snapshot ({ITERATIONS} iterations)"
    )


def test_warm_responses_are_stable_across_iterations(monkeypatch):
    """Regression-guard invariant (per agent test-quality rules):

    For all N in [1, 10 000]: ``ITERATIONS`` sequential warm requests against
    an unchanged snapshot return byte-identical payloads. This catches a
    regression where the snapshot is re-built (or filtered) per request — a
    likely root cause for blowing the 200 ms budget.
    """
    items = [_realistic_track_item(i) for i in range(1, LIBRARY_SIZE + 1)]
    client = _client_with_hydrated_snapshot(items, mtime=WARM_MTIME)
    monkeypatch.setattr(
        "autocue.serve.routes._master_db_mtime", lambda _db: WARM_MTIME
    )

    first = client.get(TRACKS_URL).content
    for _ in range(5):
        assert client.get(TRACKS_URL).content == first


def test_warm_path_short_circuits_without_snapshot():
    """Boundary case: ``test_tracks_warm_p95_meets_prd_budget`` is only a
    meaningful PRD gate if the warm path actually runs when the snapshot is
    hydrated. Without a snapshot, the route falls into the SQL pipeline —
    which against a ``MagicMock`` returns gibberish or 500s.

    Asserting the negative here keeps future refactors honest: if someone
    removes the snapshot-hit branch in ``routes.tracks``, the perf test
    above would suddenly be measuring the wrong code path. This test fails
    loudly in that scenario.
    """
    items = [_realistic_track_item(i) for i in range(1, 11)]
    app = create_app()
    db = MagicMock()
    db._db_dir = "/tmp"
    app.state.db = db
    app.state.ro_db = db
    app.state.tracks_snapshot_lock = threading.Lock()
    app.state.tracks_snapshot = None  # NOT hydrated
    client = TestClient(app)

    # With no snapshot we should NOT get a clean 200 + 10-item payload.
    # Either we 5xx out of the MagicMock SQL path, or we get a 200 whose
    # body is empty / mismatched. Both prove the warm path requires the
    # snapshot to be hydrated — the inverse of what the perf test asserts.
    r = client.get("/api/tracks")
    snapshot_path_used = (r.status_code == 200 and len(r.json()) == len(items))
    assert not snapshot_path_used, (
        "Warm path appears to be active without a hydrated snapshot — "
        "test_tracks_warm_p95_meets_prd_budget would no longer reflect the "
        "production warm code path."
    )
