"""TASK-005 — parallel /api/auto-tag detector evaluation (flagged behind
AUTOCUE_PARALLEL_AUTO_TAG=1).

Until TASK-008 verification lands, the parallel path is OFF by default so
the existing serial behaviour is unchanged for users. These tests exercise
the flagged path directly and verify:

  * default-off → serial branch (no pool.submit) — verbatim existing behaviour.
  * flag-on with 5 mocked tracks → pool.submit called 5 times; results stream.
  * per-track exception is isolated; undo_data does NOT include the failed track.
  * /api/auto-tag/undo round-trip after a flagged run still fully reverses it.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis import auto_tag as mod
from autocue.analysis.concurrency import get_pool, shutdown_pool


MODULE = "autocue.analysis.auto_tag"


# ---------------------------------------------------------------------------
# Helpers (mirror test_auto_tag.py shape)
# ---------------------------------------------------------------------------


def _make_db(existing_tags=None, existing_song_tags=None):
    db = MagicMock()
    db.session = MagicMock()
    existing_tags = existing_tags or []
    tag_q = MagicMock()
    tag_q.all.return_value = existing_tags
    db.get_my_tag.return_value = tag_q

    existing_song_tags = existing_song_tags or []
    song_tag_q = MagicMock()
    song_tag_q.all.return_value = existing_song_tags
    db.get_my_tag_songs.return_value = song_tag_q

    _counter = [100]
    def _gen_id(table):
        v = _counter[0]
        _counter[0] += 1
        return v
    db.generate_unused_id.side_effect = _gen_id
    return db


def _content(track_id):
    c = MagicMock()
    c.ID = track_id
    c.BPM = "128.0"
    return c


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _shutdown_pool_between_tests():
    shutdown_pool()
    yield
    shutdown_pool()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParallelDefaultOff:
    def test_flag_unset_runs_serial_path(self, monkeypatch, _shutdown_pool_between_tests):
        """Without AUTOCUE_PARALLEL_AUTO_TAG=1, pool.submit is NEVER called."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_AUTO_TAG", "0")
        db = _make_db()
        db.get_content.side_effect = lambda ID: _content(ID)
        mix = {"vocal_proxy": True, "phrase_count": 4, "intro_bars": 8, "outro_bars": 8}
        with patch(f"{MODULE}.get_mixability", return_value=mix):
            with patch(f"{MODULE}.ensure_tags", return_value={"Vocal": "10", "Instrumental": "11"}):
                pool = get_pool()
                with patch.object(pool, "submit", wraps=pool.submit) as submit_spy:
                    result = mod.apply_tags(db, [1, 2, 3], tag_types=["vocal"])
        assert submit_spy.call_count == 0
        assert result["tagged"] == 3


class TestParallelFlagOn:
    def test_pool_submit_called_per_track(self, monkeypatch, _shutdown_pool_between_tests):
        """Flag-on with 5 mocked tracks → pool.submit called exactly 5 times."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_AUTO_TAG", "1")
        db = _make_db()
        db.get_content.side_effect = lambda ID: _content(ID)
        mix = {"vocal_proxy": True, "phrase_count": 4, "intro_bars": 8, "outro_bars": 8}
        with patch(f"{MODULE}.get_mixability", return_value=mix):
            with patch(f"{MODULE}.ensure_tags", return_value={"Vocal": "10", "Instrumental": "11"}):
                pool = get_pool()
                with patch.object(pool, "submit", wraps=pool.submit) as submit_spy:
                    result = mod.apply_tags(db, [1, 2, 3, 4, 5], tag_types=["vocal"])
        assert submit_spy.call_count == 5
        assert result["tagged"] == 5
        assert result["errors"] == 0

    def test_completion_count_matches_inputs(self, monkeypatch, _shutdown_pool_between_tests):
        """Every input track yields exactly one outcome (tagged/skipped/error)."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_AUTO_TAG", "1")
        db = _make_db()
        db.get_content.side_effect = lambda ID: _content(ID)
        mix = {"vocal_proxy": True, "phrase_count": 4, "intro_bars": 8, "outro_bars": 8}
        with patch(f"{MODULE}.get_mixability", return_value=mix):
            with patch(f"{MODULE}.ensure_tags", return_value={"Vocal": "10", "Instrumental": "11"}):
                result = mod.apply_tags(db, list(range(1, 11)), tag_types=["vocal"])
        assert result["tagged"] + result["skipped_no_data"] + result["errors"] == 10


class TestParallelExceptionIsolation:
    def test_per_track_exception_skipped_undo_excludes_failed(self, monkeypatch, _shutdown_pool_between_tests):
        """One detector exception → that track is skipped; undo_data excludes it."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_AUTO_TAG", "1")
        db = _make_db()
        db.get_content.side_effect = lambda ID: _content(ID)

        def _flaky_mix(content, _db):
            if content.ID == 3:
                raise RuntimeError("boom")
            return {"vocal_proxy": True, "phrase_count": 4, "intro_bars": 8, "outro_bars": 8}

        with patch(f"{MODULE}.get_mixability", side_effect=_flaky_mix):
            with patch(f"{MODULE}.ensure_tags", return_value={"Vocal": "10", "Instrumental": "11"}):
                result = mod.apply_tags(db, [1, 2, 3, 4, 5], tag_types=["vocal"])

        # Track 3 hit the exception path → counted as an error, NOT tagged.
        assert result["errors"] >= 1
        assert result["tagged"] == 4
        # undo_data["added"] is the list of *written* DjmdSongMyTag IDs; the
        # failed track must contribute zero entries. With 4 vocal tags written
        # we expect exactly 4 added entries.
        assert len(result["undo_data"]["added"]) == 4


class TestParallelUndoRoundTrip:
    def test_undo_reverses_flagged_run(self, monkeypatch, _shutdown_pool_between_tests):
        """After a flagged auto-tag run, undo_tag_run deletes every added row."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_AUTO_TAG", "1")
        db = _make_db()
        db.get_content.side_effect = lambda ID: _content(ID)
        mix = {"vocal_proxy": True, "phrase_count": 4, "intro_bars": 8, "outro_bars": 8}
        with patch(f"{MODULE}.get_mixability", return_value=mix):
            with patch(f"{MODULE}.ensure_tags", return_value={"Vocal": "10", "Instrumental": "11"}):
                result = mod.apply_tags(db, [1, 2, 3], tag_types=["vocal"])

        undo_data = result["undo_data"]
        assert len(undo_data["added"]) == 3
        # Simulate undo: each added ID must reverse-delete cleanly.
        # get_my_tag_songs(ID=...) returns a row object the writer will delete.
        db.get_my_tag_songs.side_effect = lambda **kw: MagicMock()
        undo_result = mod.undo_tag_run(db, undo_data)
        assert undo_result["removed"] == 3
        assert undo_result["restored"] == 0
