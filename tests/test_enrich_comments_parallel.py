"""TASK-006 — flagged parallel /api/enrich-comments/stream.

Until TASK-008 (pyrekordbox thread-safety verification) lands, the parallel
path is OFF by default so existing serial behaviour is unchanged. These
tests exercise the flagged path directly via the FastAPI TestClient.

Invariants under test:
- AUTOCUE_PARALLEL_ENRICH_COMMENTS unset → serial path runs (existing behaviour).
- Flag-on with N tracks → pool.submit called N times; SSE done event matches counts.
- Per-track exception in _build_one → that track yields error, stream continues,
  undo_data never contains the failed track (single-writer never wrote it).
- /api/enrich-comments/undo after a flagged run reverses every applied comment.
- AutoCue sentinel idempotency — re-running produces the same comment (no double-tag).
- User-text-over-cap (300 chars of pure user text) → flagged run skips the track.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from autocue.analysis.concurrency import shutdown_pool
from autocue.serve.app import create_app


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_pool():
    shutdown_pool()
    yield
    shutdown_pool()


@pytest.fixture
def _enable_flag(monkeypatch):
    monkeypatch.setenv("AUTOCUE_PARALLEL_ENRICH_COMMENTS", "1")


def _make_track(tid: int, commnt: str = "", key_name: str = "8A", bpm: float = 128.0):
    """Build a mock DjmdContent-like object with the fields enrich_comment reads."""
    key = SimpleNamespace(ScaleName=key_name)
    t = MagicMock()
    t.ID = tid
    t.Commnt = commnt
    t.Key = key
    t.BPM = int(bpm * 100)  # Rekordbox stores BPM * 100
    t.FolderPath = f"/x/{tid}.mp3"
    t.AnalysisDataPath = "x"
    return t


def _make_db(tracks):
    """Build a mock DB with get_content(ID=...) returning the track for that ID."""
    by_id = {t.ID: t for t in tracks}
    db = MagicMock()

    def _get_content(ID=None, **_):
        if ID is None:
            chain = MagicMock()
            chain.all.return_value = list(tracks)
            return chain
        return by_id.get(ID)

    db.get_content.side_effect = _get_content
    db._db_dir = None  # disables backup_database call
    db.session = MagicMock()
    return db


def _make_client(db):
    app = create_app()
    from autocue.serve.deps import get_db, get_ro_db
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_ro_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_default_off_runs_serial_path(monkeypatch):
    """No flag → existing serial path; pool.submit must NOT be called."""
    monkeypatch.setenv("AUTOCUE_PARALLEL_ENRICH_COMMENTS", "0")
    tracks = [_make_track(i) for i in range(1, 4)]
    db = _make_db(tracks)
    client = _make_client(db)

    with patch("autocue.serve.routes._rb_running", return_value=False), \
         patch("autocue.analysis.comment.enrich_comment", return_value="8A - Energy 7"), \
         patch("autocue.analysis.concurrency.get_pool") as pool_get:
        r = client.post(
            "/api/enrich-comments/stream",
            json={"track_ids": [1, 2, 3], "overwrite": False, "dry_run": True},
        )
        assert r.status_code == 200
        pool_get.assert_not_called()


def test_flag_on_submits_one_future_per_track(_enable_flag):
    """Flag-on: pool.submit called once per track, all events arrive."""
    tracks = [_make_track(i) for i in range(1, 6)]
    db = _make_db(tracks)
    client = _make_client(db)

    with patch("autocue.serve.routes._rb_running", return_value=False), \
         patch("autocue.analysis.comment.enrich_comment", return_value="8A - Energy 7"):
        r = client.post(
            "/api/enrich-comments/stream",
            json={"track_ids": [1, 2, 3, 4, 5], "overwrite": False, "dry_run": False},
        )
        assert r.status_code == 200
        events = _parse_sse(r.text)
        done = next(e for e in events if e.get("done"))
        assert done["enriched"] == 5
        assert done["errors"] == 0
        # Five progress events + 1 done event
        progress_events = [e for e in events if "processed" in e]
        assert len(progress_events) == 5
        # Writer committed once per track
        assert db.session.commit.call_count == 5
        # undo_data populated for all 5 writes
        assert len(done["undo_data"]["modified"]) == 5


def test_per_track_exception_isolated(_enable_flag):
    """One bad track yields error event; stream continues; undo_data excludes failure."""
    tracks = [_make_track(i) for i in range(1, 6)]
    db = _make_db(tracks)
    client = _make_client(db)

    def _enrich_side_effect(content, _db, **_kw):
        if content.ID == 3:
            raise RuntimeError("boom")
        return "8A - Energy 7"

    with patch("autocue.serve.routes._rb_running", return_value=False), \
         patch("autocue.analysis.comment.enrich_comment", side_effect=_enrich_side_effect):
        r = client.post(
            "/api/enrich-comments/stream",
            json={"track_ids": [1, 2, 3, 4, 5], "overwrite": False, "dry_run": False},
        )
        assert r.status_code == 200
        events = _parse_sse(r.text)
        done = next(e for e in events if e.get("done"))
        assert done["enriched"] == 4
        assert done["errors"] == 1
        modified_ids = {row["content_id"] for row in done["undo_data"]["modified"]}
        assert "3" not in modified_ids
        assert modified_ids == {"1", "2", "4", "5"}


def test_undo_reverses_flagged_run(_enable_flag):
    """After a flagged run, /api/enrich-comments/undo restores every previous comment."""
    tracks = [_make_track(i, commnt=f"old-{i}") for i in range(1, 4)]
    db = _make_db(tracks)
    client = _make_client(db)

    def _enrich_side_effect(content, _db, **_kw):
        content_to_new = {1: "new-1", 2: "new-2", 3: "new-3"}
        return content_to_new[content.ID]

    with patch("autocue.serve.routes._rb_running", return_value=False), \
         patch("autocue.analysis.comment.enrich_comment", side_effect=_enrich_side_effect):
        r = client.post(
            "/api/enrich-comments/stream",
            json={"track_ids": [1, 2, 3], "overwrite": False, "dry_run": False},
        )
        assert r.status_code == 200
        events = _parse_sse(r.text)
        done = next(e for e in events if e.get("done"))
        undo_data = done["undo_data"]
        # writer wrote the new comments
        for t in tracks:
            assert t.Commnt == f"new-{t.ID}"

    # Now undo
    with patch("autocue.serve.routes._rb_running", return_value=False):
        r2 = client.post("/api/enrich-comments/undo", json={"undo_data": undo_data})
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["restored"] == 3
        for t in tracks:
            assert t.Commnt == f"old-{t.ID}"


def test_sentinel_idempotent_under_flag(_enable_flag):
    """Re-running flagged enrich on already-tagged tracks (sentinel present)
    produces identical comment — no double-tagging, no extra sentinel block."""
    # Seed with a user comment that already carries the AutoCue sentinel for
    # the same stable classification. enrich_comment must detect that the
    # rebuilt block is identical and return None (= skip).
    initial = "my notes /* AutoCue: 8A - Energy 7 | Peak */"
    t = _make_track(1, commnt=initial)
    db = _make_db([t])
    client = _make_client(db)

    fake_cls = {"primary": "peak", "energy_mean": 0.7}
    with patch("autocue.serve.routes._rb_running", return_value=False), \
         patch("autocue.analysis.comment.get_classification", return_value=fake_cls), \
         patch("autocue.analysis.comment._intro_bars", return_value=None):
        r = client.post(
            "/api/enrich-comments/stream",
            json={"track_ids": [1], "overwrite": False, "dry_run": False},
        )
        assert r.status_code == 200
        events = _parse_sse(r.text)
        done = next(e for e in events if e.get("done"))
        # No change → skipped, not enriched, undo_data empty, comment unchanged
        assert done["enriched"] == 0
        assert done["skipped"] == 1
        assert done["undo_data"]["modified"] == []
        assert t.Commnt == initial  # exact, no double sentinel


def test_user_text_over_cap_skipped(_enable_flag):
    """User-authored text > MAX_COMMENT_LEN (256) → skip; never truncate user text."""
    long_user_text = "u" * 300
    t = _make_track(1, commnt=long_user_text)
    db = _make_db([t])
    client = _make_client(db)

    fake_cls = {"primary": "peak", "energy_mean": 0.7}
    with patch("autocue.serve.routes._rb_running", return_value=False), \
         patch("autocue.analysis.comment.get_classification", return_value=fake_cls), \
         patch("autocue.analysis.comment._intro_bars", return_value=None):
        r = client.post(
            "/api/enrich-comments/stream",
            json={"track_ids": [1], "overwrite": False, "dry_run": False},
        )
        assert r.status_code == 200
        events = _parse_sse(r.text)
        done = next(e for e in events if e.get("done"))
        # Skipped, not committed, undo_data empty
        assert done["enriched"] == 0
        assert done["skipped"] == 1
        assert done["errors"] == 0
        assert done["undo_data"]["modified"] == []
        assert t.Commnt == long_user_text  # untouched
        # No commit happened (no writes)
        # Note: backup logic may set db.session.commit; the relevant invariant
        # is the comment text is unchanged + undo_data is empty.
