"""Tests for autocue/analysis/auto_tag.py"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

MODULE = "autocue.analysis.auto_tag"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(existing_tags=None, existing_song_tags=None):
    """Return a mock DB with configurable My Tag state."""
    db = MagicMock()
    db.session = MagicMock()

    # get_my_tag().all() → existing DjmdMyTag rows
    existing_tags = existing_tags or []
    tag_q = MagicMock()
    tag_q.all.return_value = existing_tags
    db.get_my_tag.return_value = tag_q

    # get_my_tag_songs(ContentID=...).all() → existing DjmdSongMyTag rows
    existing_song_tags = existing_song_tags or []
    song_tag_q = MagicMock()
    song_tag_q.all.return_value = existing_song_tags
    db.get_my_tag_songs.return_value = song_tag_q

    # generate_unused_id returns incrementing ints
    _counter = [100]
    def _gen_id(table):
        v = _counter[0]
        _counter[0] += 1
        return v
    db.generate_unused_id.side_effect = _gen_id

    return db


def _make_content(track_id=1, bpm_str="128.0"):
    content = MagicMock()
    content.ID = track_id
    content.BPM = bpm_str
    return content


def _make_my_tag(id_val, name):
    t = MagicMock()
    t.ID = id_val
    t.Name = name
    return t


# ---------------------------------------------------------------------------
# ensure_category_tags
# ---------------------------------------------------------------------------

class TestEnsureCategoryTags:
    def test_creates_all_five_when_none_exist(self):
        from autocue.analysis.auto_tag import ensure_category_tags, _CATEGORIES
        db = _make_db()
        result = ensure_category_tags(db)

        assert set(result.keys()) == set(_CATEGORIES.keys())
        assert db.session.add.call_count == 5
        assert db.session.flush.call_count == 5

    def test_reuses_existing_tags(self):
        from autocue.analysis.auto_tag import ensure_category_tags
        existing = [
            _make_my_tag(42, "Peak"),
            _make_my_tag(43, "Build"),
        ]
        db = _make_db(existing_tags=existing)
        result = ensure_category_tags(db)

        assert result["peak"] == "42"
        assert result["build"] == "43"
        # Only creates missing ones (3 new, not 5)
        assert db.session.add.call_count == 3

    def test_idempotent_all_existing(self):
        from autocue.analysis.auto_tag import ensure_category_tags
        existing = [
            _make_my_tag(1, "Warmup"),
            _make_my_tag(2, "Build"),
            _make_my_tag(3, "Peak"),
            _make_my_tag(4, "After Hours"),
            _make_my_tag(5, "Closing"),
        ]
        db = _make_db(existing_tags=existing)
        result = ensure_category_tags(db)

        assert db.session.add.call_count == 0
        assert result["warmup"] == "1"
        assert result["after_hours"] == "4"

    def test_returns_string_ids(self):
        from autocue.analysis.auto_tag import ensure_category_tags
        db = _make_db()
        result = ensure_category_tags(db)
        for v in result.values():
            assert isinstance(v, str)

    def test_handles_get_my_tag_exception(self):
        from autocue.analysis.auto_tag import ensure_category_tags, _CATEGORIES
        db = _make_db()
        db.get_my_tag.side_effect = Exception("DB error")
        # Should not raise; creates all 5 fresh
        result = ensure_category_tags(db)
        assert set(result.keys()) == set(_CATEGORIES.keys())

    def test_reuses_existing_tag_with_different_casing(self):
        """Manually-created lowercase 'warmup' should NOT cause a duplicate."""
        from autocue.analysis.auto_tag import ensure_category_tags
        existing = [
            _make_my_tag(99, "warmup"),       # lowercase
            _make_my_tag(100, "  Peak  "),    # whitespace-padded
            _make_my_tag(101, "BUILD"),       # all caps
        ]
        db = _make_db(existing_tags=existing)
        result = ensure_category_tags(db)

        # All three existing tags reused — no duplicates created for them
        assert result["warmup"] == "99"
        assert result["peak"] == "100"
        assert result["build"] == "101"
        # Only the missing two (after_hours, closing) get created
        assert db.session.add.call_count == 2


class TestEnsureTagByName:
    def test_reuses_tag_with_different_casing(self):
        """Bug fix: "Vocal" and "vocal" should resolve to the same row."""
        from autocue.analysis.auto_tag import ensure_tag_by_name
        existing = [_make_my_tag(77, "Vocal")]
        db = _make_db(existing_tags=existing)

        same_id = ensure_tag_by_name(db, "vocal")
        assert same_id == "77"
        assert db.session.add.call_count == 0

    def test_reuses_tag_with_whitespace(self):
        from autocue.analysis.auto_tag import ensure_tag_by_name
        existing = [_make_my_tag(88, "Tech House")]
        db = _make_db(existing_tags=existing)

        same_id = ensure_tag_by_name(db, "  tech house  ")
        assert same_id == "88"
        assert db.session.add.call_count == 0

    def test_creates_new_tag_preserves_original_casing(self):
        from autocue.analysis.auto_tag import ensure_tag_by_name
        db = _make_db()
        ensure_tag_by_name(db, "Disco")
        # The newly-added tag preserves the user's casing
        added_tag = db.session.add.call_args[0][0]
        assert added_tag.Name == "Disco"


# ---------------------------------------------------------------------------
# apply_classification_tags
# ---------------------------------------------------------------------------

class TestApplyClassificationTags:
    def _base_db(self):
        return _make_db()

    def _content(self, tid=1):
        return _make_content(tid)

    def test_tags_track_with_high_score(self):
        from autocue.analysis.auto_tag import apply_classification_tags
        db = self._base_db()
        content = self._content(1)
        db.get_content.return_value = content

        clf = {"primary": "peak", "scores": {"peak": 0.88}}
        with patch(f"{MODULE}.get_energy_curve", return_value=[0.5] * 50):
            with patch(f"{MODULE}.get_classification", return_value=clf):
                with patch(f"{MODULE}.ensure_category_tags", return_value={"peak": "10"}):
                    result = apply_classification_tags(db, [1])

        assert result["tagged"] == 1
        assert result["skipped_no_anlz"] == 0
        assert result["skipped_low_score"] == 0
        assert result["errors"] == 0
        assert result["undo_data"] is not None

    def test_skips_track_below_min_score(self):
        from autocue.analysis.auto_tag import apply_classification_tags, MIN_SCORE
        db = self._base_db()
        content = self._content(1)
        db.get_content.return_value = content

        clf = {"primary": "build", "scores": {"build": MIN_SCORE - 0.01}}
        with patch(f"{MODULE}.get_energy_curve", return_value=[0.5] * 50):
            with patch(f"{MODULE}.get_classification", return_value=clf):
                with patch(f"{MODULE}.ensure_category_tags", return_value={"build": "10"}):
                    result = apply_classification_tags(db, [1])

        assert result["tagged"] == 0
        assert result["skipped_low_score"] == 1

    def test_skips_track_with_no_energy_curve(self):
        from autocue.analysis.auto_tag import apply_classification_tags
        db = self._base_db()
        db.get_content.return_value = self._content(1)

        with patch(f"{MODULE}.get_energy_curve", return_value=[]):
            with patch(f"{MODULE}.ensure_category_tags", return_value={}):
                result = apply_classification_tags(db, [1])

        assert result["skipped_no_anlz"] == 1
        assert result["tagged"] == 0

    def test_skips_track_with_none_classification(self):
        from autocue.analysis.auto_tag import apply_classification_tags
        db = self._base_db()
        db.get_content.return_value = self._content(1)

        with patch(f"{MODULE}.get_energy_curve", return_value=[0.5] * 10):
            with patch(f"{MODULE}.get_classification", return_value=None):
                with patch(f"{MODULE}.ensure_category_tags", return_value={}):
                    result = apply_classification_tags(db, [1])

        assert result["skipped_no_anlz"] == 1

    def test_dry_run_does_not_write(self):
        from autocue.analysis.auto_tag import apply_classification_tags
        db = self._base_db()
        db.get_content.return_value = self._content(1)

        clf = {"primary": "peak", "scores": {"peak": 0.90}}
        with patch(f"{MODULE}.get_energy_curve", return_value=[0.5] * 50):
            with patch(f"{MODULE}.get_classification", return_value=clf):
                with patch(f"{MODULE}.ensure_category_tags", return_value={"peak": "10"}):
                    result = apply_classification_tags(db, [1], dry_run=True)

        assert result["tagged"] == 1
        assert result["dry_run"] is True
        assert result["undo_data"] is None
        db.session.add.assert_not_called()
        db.session.flush.assert_not_called()

    def test_overwrite_removes_existing_autocue_tags(self):
        from autocue.analysis.auto_tag import apply_classification_tags

        # Set up a pre-existing song-tag assignment for our track
        old_song_tag = MagicMock()
        old_song_tag.ID = "99"
        old_song_tag.MyTagID = "10"   # same as our tag_id_map value → will be removed
        old_song_tag.ContentID = "1"
        old_song_tag.TrackNo = 0
        old_song_tag.UUID = "old-uuid"

        db = self._base_db()
        db.get_content.return_value = self._content(1)
        # Song tag query for this content
        q = MagicMock()
        q.all.return_value = [old_song_tag]
        db.get_my_tag_songs.return_value = q

        clf = {"primary": "peak", "scores": {"peak": 0.85}}
        tag_map = {"peak": "10"}  # autocue_tag_ids = {"10"}
        with patch(f"{MODULE}.get_energy_curve", return_value=[0.5] * 50):
            with patch(f"{MODULE}.get_classification", return_value=clf):
                with patch(f"{MODULE}.ensure_category_tags", return_value=tag_map):
                    result = apply_classification_tags(db, [1], overwrite=True)

        db.session.delete.assert_called_once_with(old_song_tag)
        assert len(result["undo_data"]["removed"]) == 1

    def test_no_overwrite_keeps_existing_tags(self):
        from autocue.analysis.auto_tag import apply_classification_tags

        old_song_tag = MagicMock()
        old_song_tag.MyTagID = "10"
        db = self._base_db()
        db.get_content.return_value = self._content(1)

        clf = {"primary": "peak", "scores": {"peak": 0.85}}
        with patch(f"{MODULE}.get_energy_curve", return_value=[0.5] * 50):
            with patch(f"{MODULE}.get_classification", return_value=clf):
                with patch(f"{MODULE}.ensure_category_tags", return_value={"peak": "10"}):
                    result = apply_classification_tags(db, [1], overwrite=False)

        db.session.delete.assert_not_called()

    def test_skips_missing_content(self):
        from autocue.analysis.auto_tag import apply_classification_tags
        db = self._base_db()
        db.get_content.return_value = None
        with patch(f"{MODULE}.ensure_category_tags", return_value={}):
            result = apply_classification_tags(db, [999])
        assert result["tagged"] == 0

    def test_error_counter_increments(self):
        from autocue.analysis.auto_tag import apply_classification_tags
        db = self._base_db()
        db.get_content.side_effect = Exception("boom")
        with patch(f"{MODULE}.ensure_category_tags", return_value={}):
            result = apply_classification_tags(db, [1, 2])
        assert result["errors"] == 2

    def test_flushes_after_writes(self):
        from autocue.analysis.auto_tag import apply_classification_tags
        db = self._base_db()
        db.get_content.return_value = self._content(1)

        clf = {"primary": "peak", "scores": {"peak": 0.90}}
        with patch(f"{MODULE}.get_energy_curve", return_value=[0.5] * 50):
            with patch(f"{MODULE}.get_classification", return_value=clf):
                with patch(f"{MODULE}.ensure_category_tags", return_value={"peak": "10"}):
                    apply_classification_tags(db, [1])

        db.session.flush.assert_called()

    def test_multiple_tracks_tagged(self):
        from autocue.analysis.auto_tag import apply_classification_tags
        db = self._base_db()

        def _get_content(**kw):
            return _make_content(kw["ID"])

        db.get_content.side_effect = _get_content

        clf = {"primary": "build", "scores": {"build": 0.80}}
        with patch(f"{MODULE}.get_energy_curve", return_value=[0.4] * 30):
            with patch(f"{MODULE}.get_classification", return_value=clf):
                with patch(f"{MODULE}.ensure_category_tags", return_value={"build": "20"}):
                    result = apply_classification_tags(db, [1, 2, 3])

        assert result["tagged"] == 3
        assert len(result["undo_data"]["added"]) == 3

    def test_undo_data_added_contains_string_ids(self):
        from autocue.analysis.auto_tag import apply_classification_tags
        db = self._base_db()
        db.get_content.return_value = self._content(1)
        clf = {"primary": "closing", "scores": {"closing": 0.75}}
        with patch(f"{MODULE}.get_energy_curve", return_value=[0.2] * 20):
            with patch(f"{MODULE}.get_classification", return_value=clf):
                with patch(f"{MODULE}.ensure_category_tags", return_value={"closing": "55"}):
                    result = apply_classification_tags(db, [1])
        for added_id in result["undo_data"]["added"]:
            assert isinstance(added_id, str)


# ---------------------------------------------------------------------------
# undo_tag_run
# ---------------------------------------------------------------------------

class TestUndoTagRun:
    def test_removes_added_tags(self):
        from autocue.analysis.auto_tag import undo_tag_run
        db = MagicMock()
        existing_row = MagicMock()
        db.get_my_tag_songs.return_value = existing_row

        undo_data = {"added": ["101", "102"], "removed": []}
        result = undo_tag_run(db, undo_data)

        assert result["removed"] == 2
        assert db.session.delete.call_count == 2
        db.session.flush.assert_called_once()

    def test_restores_removed_tags(self):
        from autocue.analysis.auto_tag import undo_tag_run
        db = MagicMock()

        removed_items = [
            {"ID": "50", "MyTagID": "10", "ContentID": "1", "TrackNo": 0, "UUID": "uuid-a"},
            {"ID": "51", "MyTagID": "20", "ContentID": "2", "TrackNo": 0, "UUID": None},
        ]
        result = undo_tag_run(db, {"added": [], "removed": removed_items})

        assert result["restored"] == 2
        assert db.session.add.call_count == 2

    def test_handles_missing_row_gracefully(self):
        from autocue.analysis.auto_tag import undo_tag_run
        db = MagicMock()
        db.get_my_tag_songs.return_value = None  # already deleted

        result = undo_tag_run(db, {"added": ["999"], "removed": []})
        # row is None → count stays 0, no error
        assert result["removed"] == 0

    def test_handles_empty_undo_data(self):
        from autocue.analysis.auto_tag import undo_tag_run
        db = MagicMock()
        result = undo_tag_run(db, {"added": [], "removed": []})
        assert result == {"removed": 0, "restored": 0}

    def test_flushes_at_end(self):
        from autocue.analysis.auto_tag import undo_tag_run
        db = MagicMock()
        db.get_my_tag_songs.return_value = MagicMock()
        undo_tag_run(db, {"added": ["1"], "removed": []})
        db.session.flush.assert_called_once()


# ---------------------------------------------------------------------------
# ensure_tags
# ---------------------------------------------------------------------------

class TestEnsureTags:
    def test_creates_tags_for_requested_types(self):
        from autocue.analysis.auto_tag import ensure_tags
        db = _make_db()
        result = ensure_tags(db, ["vocal"])
        assert "Vocal" in result
        assert "Instrumental" in result
        assert db.session.add.call_count == 2

    def test_does_not_create_unrelated_types(self):
        from autocue.analysis.auto_tag import ensure_tags
        db = _make_db()
        result = ensure_tags(db, ["energy_level"])
        assert "High Energy" in result
        assert "Vocal" not in result

    def test_reuses_existing_tags(self):
        from autocue.analysis.auto_tag import ensure_tags
        existing = [_make_my_tag(99, "Vocal"), _make_my_tag(100, "Instrumental")]
        db = _make_db(existing_tags=existing)
        result = ensure_tags(db, ["vocal"])
        assert result["Vocal"] == "99"
        assert result["Instrumental"] == "100"
        db.session.add.assert_not_called()

    def test_multiple_types(self):
        from autocue.analysis.auto_tag import ensure_tags, _TAG_GROUPS
        db = _make_db()
        result = ensure_tags(db, ["vocal", "energy_level"])
        expected_names = (
            {c["name"] for c in _TAG_GROUPS["vocal"].values()} |
            {c["name"] for c in _TAG_GROUPS["energy_level"].values()}
        )
        assert set(result.keys()) == expected_names


# ---------------------------------------------------------------------------
# apply_tags — unified tagger
# ---------------------------------------------------------------------------

class TestApplyTags:
    def _base_db(self):
        return _make_db()

    def test_vocal_tag_applied(self):
        from autocue.analysis.auto_tag import apply_tags
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        mix = {"vocal_proxy": True, "phrase_count": 4, "intro_bars": 8, "outro_bars": 8}
        with patch(f"{MODULE}.get_mixability", return_value=mix):
            with patch(f"{MODULE}.ensure_tags", return_value={"Vocal": "10", "Instrumental": "11"}):
                result = apply_tags(db, [1], tag_types=["vocal"])
        assert result["tagged"] == 1
        assert result["skipped_no_data"] == 0

    def test_instrumental_tag_applied(self):
        from autocue.analysis.auto_tag import apply_tags
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        mix = {"vocal_proxy": False, "phrase_count": 4, "intro_bars": 8, "outro_bars": 8}
        with patch(f"{MODULE}.get_mixability", return_value=mix):
            with patch(f"{MODULE}.ensure_tags", return_value={"Vocal": "10", "Instrumental": "11"}):
                result = apply_tags(db, [1], tag_types=["vocal"])
        assert result["tagged"] == 1

    def test_energy_level_high(self):
        from autocue.analysis.auto_tag import apply_tags
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        high_curve = [0.8] * 50
        with patch(f"{MODULE}.get_energy_curve", return_value=high_curve):
            with patch(f"{MODULE}.ensure_tags", return_value={"High Energy": "20", "Mid Energy": "21", "Low Energy": "22"}):
                result = apply_tags(db, [1], tag_types=["energy_level"])
        assert result["tagged"] == 1

    def test_energy_level_skips_no_curve(self):
        from autocue.analysis.auto_tag import apply_tags
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.ensure_tags", return_value={"High Energy": "20"}):
                result = apply_tags(db, [1], tag_types=["energy_level"])
        assert result["skipped_no_data"] == 1

    def test_energy_profile_build(self):
        from autocue.analysis.auto_tag import apply_tags
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        curve = [0.2] * 25 + [0.8] * 25
        with patch(f"{MODULE}.get_energy_curve", return_value=curve):
            with patch(f"{MODULE}.ensure_tags", return_value={"Build Track": "30", "Wave Track": "31", "Flat Track": "32", "Drop Track": "33"}):
                result = apply_tags(db, [1], tag_types=["energy_profile"])
        assert result["tagged"] == 1

    def test_intro_outro_long_intro(self):
        from autocue.analysis.auto_tag import apply_tags, LONG_INTRO_BARS
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        mix = {"vocal_proxy": False, "phrase_count": 5, "intro_bars": LONG_INTRO_BARS, "outro_bars": 8}
        with patch(f"{MODULE}.get_mixability", return_value=mix):
            with patch(f"{MODULE}.ensure_tags", return_value={"Long Intro": "40"}):
                result = apply_tags(db, [1], tag_types=["intro_outro"])
        assert result["tagged"] == 1

    def test_intro_outro_skips_no_phrase_data(self):
        from autocue.analysis.auto_tag import apply_tags
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        with patch(f"{MODULE}.get_mixability", return_value=None):
            with patch(f"{MODULE}.ensure_tags", return_value={}):
                result = apply_tags(db, [1], tag_types=["intro_outro"])
        assert result["skipped_no_data"] == 1

    def test_multiple_tag_types_combined(self):
        from autocue.analysis.auto_tag import apply_tags
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        mix = {"vocal_proxy": True, "phrase_count": 4, "intro_bars": 8, "outro_bars": 8}
        curve = [0.5] * 50
        tag_map = {"Vocal": "10", "Instrumental": "11", "Mid Energy": "21"}
        with patch(f"{MODULE}.get_mixability", return_value=mix):
            with patch(f"{MODULE}.get_energy_curve", return_value=curve):
                with patch(f"{MODULE}.ensure_tags", return_value=tag_map):
                    result = apply_tags(db, [1], tag_types=["vocal", "energy_level"])
        assert result["tagged"] == 1

    def test_dry_run_does_not_write(self):
        from autocue.analysis.auto_tag import apply_tags
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        mix = {"vocal_proxy": True, "phrase_count": 3, "intro_bars": 8, "outro_bars": 8}
        with patch(f"{MODULE}.get_mixability", return_value=mix):
            with patch(f"{MODULE}.ensure_tags", return_value={"Vocal": "10", "Instrumental": "11"}):
                result = apply_tags(db, [1], tag_types=["vocal"], dry_run=True)
        assert result["tagged"] == 1
        assert result["dry_run"] is True
        assert result["undo_data"] is None
        db.session.add.assert_not_called()

    def test_skips_missing_content(self):
        from autocue.analysis.auto_tag import apply_tags
        db = self._base_db()
        db.get_content.return_value = None
        with patch(f"{MODULE}.ensure_tags", return_value={}):
            result = apply_tags(db, [999], tag_types=["vocal"])
        assert result["tagged"] == 0


# ---------------------------------------------------------------------------
# Issue #118 — skip_reasons breakdown
#
# These tests pin the per-bucket diagnostic so a user staring at the API
# response can tell "ANLZ missing" apart from "low classification confidence"
# apart from "no detector fired".  Run in serial mode for deterministic
# detector ordering.
# ---------------------------------------------------------------------------

class TestSkipReasons:
    def _base_db(self):
        return _make_db()

    def test_no_anlz_energy_bucket_when_category_lacks_curve(self, monkeypatch):
        """Regression for #118 — would FAIL before the fix because the only
        signal was the opaque ``skipped_no_data`` counter."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_AUTO_TAG", "0")
        from autocue.analysis.auto_tag import apply_tags
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.ensure_tags", return_value={"Peak": "10"}):
                result = apply_tags(db, [1], tag_types=["category"])
        assert result["skipped_no_data"] == 1
        assert result["skip_reasons"]["no_anlz_energy"] == 1
        assert result["skip_reasons"]["low_classification"] == 0
        assert result["skip_reasons"]["no_detector_match"] == 0

    def test_low_classification_bucket_at_boundary(self, monkeypatch):
        """Boundary case: a track with full energy data but classification
        score JUST below MIN_SCORE lands in ``low_classification``, not
        ``no_anlz_energy``."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_AUTO_TAG", "0")
        from autocue.analysis.auto_tag import apply_tags, MIN_SCORE
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        clf = {"primary": "peak", "scores": {"peak": MIN_SCORE - 0.001}}
        with patch(f"{MODULE}.get_energy_curve", return_value=[0.5] * 50):
            with patch(f"{MODULE}.get_classification", return_value=clf):
                with patch(f"{MODULE}.ensure_tags", return_value={"Peak": "10"}):
                    result = apply_tags(db, [1], tag_types=["category"])
        assert result["tagged"] == 0
        assert result["skipped_no_data"] == 1
        assert result["skip_reasons"]["low_classification"] == 1
        assert result["skip_reasons"]["no_anlz_energy"] == 0

    def test_boundary_at_min_score_tags_the_track(self, monkeypatch):
        """Complement of the boundary case — at exactly MIN_SCORE the track
        IS tagged (the ``< MIN_SCORE`` predicate is strict)."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_AUTO_TAG", "0")
        from autocue.analysis.auto_tag import apply_tags, MIN_SCORE
        db = self._base_db()
        db.get_content.return_value = _make_content(1)
        clf = {"primary": "peak", "scores": {"peak": MIN_SCORE}}
        with patch(f"{MODULE}.get_energy_curve", return_value=[0.5] * 50):
            with patch(f"{MODULE}.get_classification", return_value=clf):
                with patch(f"{MODULE}.ensure_tags", return_value={"Peak": "10"}):
                    result = apply_tags(db, [1], tag_types=["category"])
        assert result["tagged"] == 1
        assert sum(result["skip_reasons"].values()) == 0

    def test_no_detector_match_when_decade_unknown(self, monkeypatch):
        """A non-ANLZ-dependent tag_type (decade) with no ReleaseYear lands
        in ``no_detector_match`` — NOT ``no_anlz_energy``."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_AUTO_TAG", "0")
        from autocue.analysis.auto_tag import apply_tags
        db = self._base_db()
        content = _make_content(1)
        content.ReleaseYear = None
        db.get_content.return_value = content
        with patch(f"{MODULE}.ensure_tags", return_value={}):
            result = apply_tags(db, [1], tag_types=["decade"])
        assert result["skipped_no_data"] == 1
        assert result["skip_reasons"]["no_detector_match"] == 1
        assert result["skip_reasons"]["no_anlz_energy"] == 0

    def test_skip_reasons_sum_equals_skipped_no_data_invariant(self, monkeypatch):
        """Invariant: for any mix of skip outcomes, the buckets sum to the
        total. Three tracks: one ANLZ-less, one low-score, one no-decade."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_AUTO_TAG", "0")
        from autocue.analysis.auto_tag import apply_tags, MIN_SCORE

        db = self._base_db()
        # Three distinct contents, dispatched by track_id via side_effect.
        c_no_anlz = _make_content(1)
        c_low     = _make_content(2)
        c_no_dec  = _make_content(3)
        c_no_dec.ReleaseYear = None
        contents = {1: c_no_anlz, 2: c_low, 3: c_no_dec}
        db.get_content.side_effect = lambda ID: contents[ID]

        def _curve(content, db_=None, n_points=50):
            return None if content.ID == 1 else [0.5] * 50

        def _clf(content, db_=None):
            if content.ID == 2:
                return {"primary": "peak", "scores": {"peak": MIN_SCORE - 0.05}}
            return {"primary": "peak", "scores": {"peak": 0.9}}

        # All three requested tag_types so we exercise no_detector_match too.
        with patch(f"{MODULE}.get_energy_curve", side_effect=_curve):
            with patch(f"{MODULE}.get_classification", side_effect=_clf):
                with patch(f"{MODULE}.ensure_tags", return_value={"Peak": "10"}):
                    result = apply_tags(
                        db, [1, 2, 3], tag_types=["category", "decade"]
                    )
        # Invariant — not specific-value: the buckets always sum to the total.
        assert sum(result["skip_reasons"].values()) == result["skipped_no_data"]
        assert result["skipped_no_data"] + result["tagged"] == 3
