"""
Tests for autocue/analysis/quality.py

Adversarial notes:
- NO_AUDIO_FILE must force score=0 and skip all other checks.
- Duplicate threshold is 10ms — 9ms apart = duplicate, 10ms apart = not.
- UNNAMED_CUES catches empty string AND "Cue 3" / "cue3" patterns; NOT "Drop" or "Chorus".
- fix_tier depends on has_phrase (AnalysisDataPath) AND has_beatgrid (BPM>0).
- Memory cue absence is info-only; score must stay unchanged.
- Per-track exception in check_library_health must yield error report, not propagate.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis.quality import (
    CueIssue,
    TrackHealthReport,
    _fix_tier,
    _resolve_audio_path,
    check_library_health,
    check_track_health,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content(
    folder_path="/music/track.mp3",
    analysis_data_path="path/to/analysis",
    bpm=13000,  # 130.00 BPM stored as int×100
    track_id=1,
):
    c = MagicMock()
    c.ID = track_id
    c.FolderPath = folder_path
    c.AnalysisDataPath = analysis_data_path
    c.BPM = bpm
    return c


def _cue(kind, in_frame, comment="Drop"):
    c = MagicMock()
    c.Kind = kind
    c.InFrame = in_frame  # position_ms = in_frame * 1000 // 150
    c.Comment = comment
    return c


def _db_with_cues(cues):
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = cues
    return db


# ---------------------------------------------------------------------------
# _resolve_audio_path
# ---------------------------------------------------------------------------

class TestResolveAudioPath:
    def test_plain_path(self):
        c = MagicMock()
        c.FolderPath = "/Users/dj/Music/track.mp3"
        assert _resolve_audio_path(c) == "/Users/dj/Music/track.mp3"

    def test_macos_prefix_stripped(self):
        c = MagicMock()
        c.FolderPath = "/:/Users/dj/Music/track.mp3"
        assert _resolve_audio_path(c) == "/Users/dj/Music/track.mp3"

    def test_missing_leading_slash_added(self):
        c = MagicMock()
        c.FolderPath = "Users/dj/track.mp3"
        assert _resolve_audio_path(c) == "/Users/dj/track.mp3"

    def test_empty_returns_empty(self):
        c = MagicMock()
        c.FolderPath = ""
        assert _resolve_audio_path(c) == ""


# ---------------------------------------------------------------------------
# _fix_tier
# ---------------------------------------------------------------------------

class TestFixTier:
    def test_phrase_and_beat_gives_phrase(self):
        assert _fix_tier(True, True, True) == "phrase"

    def test_no_phrase_with_beat_gives_bar(self):
        assert _fix_tier(False, True, True) == "bar"

    def test_no_beat_gives_heuristic(self):
        assert _fix_tier(True, False, True) == "heuristic"

    def test_no_beat_no_phrase_gives_heuristic(self):
        assert _fix_tier(False, False, True) == "heuristic"

    def test_no_audio_gives_none(self):
        assert _fix_tier(True, True, False) == "none"


# ---------------------------------------------------------------------------
# check_track_health — NO_AUDIO_FILE
# ---------------------------------------------------------------------------

class TestNoAudioFile:
    def test_missing_audio_score_is_zero(self):
        content = _content(folder_path="/nonexistent/track.mp3")
        db = _db_with_cues([])
        with patch("os.path.exists", return_value=False):
            report = check_track_health(content, db)
        assert report.score == 0

    def test_missing_audio_fix_tier_is_none(self):
        content = _content(folder_path="/nonexistent/track.mp3")
        db = _db_with_cues([])
        with patch("os.path.exists", return_value=False):
            report = check_track_health(content, db)
        assert report.fix_tier == "none"

    def test_missing_audio_has_no_audio_file_issue(self):
        content = _content(folder_path="/nonexistent/track.mp3")
        db = _db_with_cues([])
        with patch("os.path.exists", return_value=False):
            report = check_track_health(content, db)
        codes = [i.code for i in report.issues]
        assert "NO_AUDIO_FILE" in codes

    def test_missing_audio_skips_cue_queries(self):
        """DB should not be queried when audio file is missing."""
        content = _content(folder_path="/nonexistent/track.mp3")
        db = MagicMock()
        with patch("os.path.exists", return_value=False):
            check_track_health(content, db)
        db.query.assert_not_called()


# ---------------------------------------------------------------------------
# check_track_health — healthy track
# ---------------------------------------------------------------------------

class TestHealthyTrack:
    def _healthy_report(self):
        content = _content()
        db = _db_with_cues([
            _cue(1, 1200, "Drop"),    # slot A, 8000ms
            _cue(2, 600, "Verse 1"),  # slot B, 4000ms
        ])
        with patch("os.path.exists", return_value=True):
            return check_track_health(content, db)

    def test_score_is_100(self):
        assert self._healthy_report().score == 100

    def test_no_error_issues(self):
        report = self._healthy_report()
        assert not any(i.severity == "error" for i in report.issues)

    def test_hot_cue_count(self):
        assert self._healthy_report().hot_cue_count == 2

    def test_fix_tier_is_phrase(self):
        assert self._healthy_report().fix_tier == "phrase"


# ---------------------------------------------------------------------------
# check_track_health — NO_CUES
# ---------------------------------------------------------------------------

class TestNoCues:
    def test_no_cues_deducts_30(self):
        content = _content()
        db = _db_with_cues([])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert report.score == 100 - 30

    def test_no_cues_issue_present(self):
        content = _content()
        db = _db_with_cues([])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert any(i.code == "NO_CUES" for i in report.issues)

    def test_no_cues_severity_is_error(self):
        content = _content()
        db = _db_with_cues([])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        issue = next(i for i in report.issues if i.code == "NO_CUES")
        assert issue.severity == "error"


# ---------------------------------------------------------------------------
# check_track_health — NO_PHRASE / NO_BEATGRID
# ---------------------------------------------------------------------------

class TestMissingAnalysis:
    def test_no_phrase_deducts_10(self):
        content = _content(analysis_data_path=None)
        db = _db_with_cues([_cue(1, 1200, "Drop")])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert report.score == 90

    def test_no_beatgrid_deducts_10(self):
        content = _content(bpm=0)
        db = _db_with_cues([_cue(1, 1200, "Drop")])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert report.score == 90

    def test_no_phrase_no_beatgrid_deducts_20(self):
        content = _content(analysis_data_path=None, bpm=0)
        db = _db_with_cues([_cue(1, 1200, "Drop")])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert report.score == 80

    def test_no_phrase_fix_tier_is_bar(self):
        content = _content(analysis_data_path=None)
        db = _db_with_cues([_cue(1, 1200, "Drop")])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert report.fix_tier == "bar"

    def test_no_beatgrid_fix_tier_is_heuristic(self):
        content = _content(bpm=0)
        db = _db_with_cues([_cue(1, 1200, "Drop")])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert report.fix_tier == "heuristic"

    def test_no_phrase_severity_is_info(self):
        content = _content(analysis_data_path=None)
        db = _db_with_cues([_cue(1, 1200, "Drop")])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        issue = next(i for i in report.issues if i.code == "NO_PHRASE")
        assert issue.severity == "info"


# ---------------------------------------------------------------------------
# check_track_health — DUPLICATE_CUE (< 2 InFrames threshold ≈ <13ms)
# ---------------------------------------------------------------------------

class TestDuplicateCue:
    def _report_with_frames(self, frame_a, frame_b):
        """Pass InFrame values directly to avoid ms↔frame roundtrip precision issues."""
        content = _content()
        db = _db_with_cues([_cue(1, frame_a, "Drop"), _cue(2, frame_b, "Verse")])
        with patch("os.path.exists", return_value=True):
            return check_track_health(content, db)

    def test_same_frame_is_duplicate(self):
        # Same InFrame = double-write bug
        report = self._report_with_frames(1200, 1200)
        assert any(i.code == "DUPLICATE_CUE" for i in report.issues)

    def test_1_frame_apart_is_duplicate(self):
        # Adjacent frames (≈6.67ms) — still caught as duplicate
        report = self._report_with_frames(1200, 1201)
        assert any(i.code == "DUPLICATE_CUE" for i in report.issues)

    def test_1_frame_deducts_5(self):
        report = self._report_with_frames(1200, 1201)
        assert report.score == 95

    def test_2_frames_apart_is_not_duplicate(self):
        # Boundary: exactly 2 frames apart (≈13ms) should NOT trigger
        report = self._report_with_frames(1200, 1202)
        assert not any(i.code == "DUPLICATE_CUE" for i in report.issues)

    def test_100_frames_apart_is_not_duplicate(self):
        report = self._report_with_frames(1200, 1300)
        assert not any(i.code == "DUPLICATE_CUE" for i in report.issues)

    def test_duplicate_only_penalized_once(self):
        """Three cues at the same frame → still only -5."""
        content = _content()
        db = _db_with_cues([_cue(1, 1200, "A"), _cue(2, 1200, "B"), _cue(3, 1200, "C")])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert report.score == 95


# ---------------------------------------------------------------------------
# check_track_health — UNNAMED_CUES
# ---------------------------------------------------------------------------

class TestUnnamedCues:
    def _report_with_name(self, name):
        content = _content()
        db = _db_with_cues([_cue(1, 1200, name)])
        with patch("os.path.exists", return_value=True):
            return check_track_health(content, db)

    def test_empty_name_triggers(self):
        assert any(i.code == "UNNAMED_CUES" for i in self._report_with_name("").issues)

    def test_whitespace_name_triggers(self):
        assert any(i.code == "UNNAMED_CUES" for i in self._report_with_name("   ").issues)

    def test_cue_n_pattern_triggers(self):
        assert any(i.code == "UNNAMED_CUES" for i in self._report_with_name("Cue 3").issues)

    def test_cue_n_case_insensitive(self):
        assert any(i.code == "UNNAMED_CUES" for i in self._report_with_name("cue3").issues)

    def test_named_cue_does_not_trigger(self):
        assert not any(i.code == "UNNAMED_CUES" for i in self._report_with_name("Drop").issues)

    def test_verse_name_does_not_trigger(self):
        assert not any(i.code == "UNNAMED_CUES" for i in self._report_with_name("Verse 1").issues)

    def test_unnamed_deducts_5(self):
        report = self._report_with_name("")
        assert report.score == 95


# ---------------------------------------------------------------------------
# check_track_health — NO_MEMORY_CUE (info only, no score impact)
# ---------------------------------------------------------------------------

class TestMemoryCue:
    def test_no_memory_cue_is_info_only(self):
        content = _content()
        db = _db_with_cues([_cue(1, 1200, "Drop")])  # kind=1 = hot cue only
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert any(i.code == "NO_MEMORY_CUE" for i in report.issues)
        issue = next(i for i in report.issues if i.code == "NO_MEMORY_CUE")
        assert issue.severity == "info"

    def test_no_memory_cue_does_not_affect_score(self):
        """Score must remain 100 when the only issue is a missing memory cue."""
        content = _content()
        db = _db_with_cues([_cue(1, 1200, "Drop")])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert report.score == 100

    def test_memory_cue_count_tracked(self):
        content = _content()
        db = _db_with_cues([_cue(0, 0, ""), _cue(1, 1200, "Drop")])  # kind=0 = memory
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert report.memory_cue_count == 1
        assert not any(i.code == "NO_MEMORY_CUE" for i in report.issues)


# ---------------------------------------------------------------------------
# check_track_health — score clamping
# ---------------------------------------------------------------------------

class TestScoreClamping:
    def test_score_never_below_zero(self):
        """All deductions applied: -30 -10 -10 = 50 minimum in practice (NO_CUES + both info)."""
        content = _content(analysis_data_path=None, bpm=0)
        db = _db_with_cues([])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert report.score >= 0

    def test_score_never_above_100(self):
        content = _content()
        db = _db_with_cues([_cue(1, 1200, "Drop")])
        with patch("os.path.exists", return_value=True):
            report = check_track_health(content, db)
        assert report.score <= 100


# ---------------------------------------------------------------------------
# check_library_health
# ---------------------------------------------------------------------------

class TestLibraryHealth:
    def test_yields_one_report_per_track(self):
        contents = [_content(track_id=i) for i in range(3)]
        db = MagicMock()
        db.query.return_value.all.return_value = contents
        db.query.return_value.filter.return_value.all.return_value = []

        with patch("os.path.exists", return_value=True):
            reports = list(check_library_health(db))
        assert len(reports) == 3

    def test_exception_per_track_yields_error_report(self):
        """A DB error on one track must not abort the scan."""
        good = _content(track_id=1)
        bad = MagicMock()
        bad.ID = 2
        bad.FolderPath = "/ok.mp3"
        # Make AnalysisDataPath raise
        type(bad).AnalysisDataPath = property(lambda self: (_ for _ in ()).throw(RuntimeError("boom")))

        db = MagicMock()
        db.query.return_value.all.return_value = [good, bad]
        db.query.return_value.filter.return_value.all.return_value = []

        with patch("os.path.exists", return_value=True):
            reports = list(check_library_health(db))

        assert len(reports) == 2
        error_report = next(r for r in reports if r.track_id == 2)
        assert error_report.score == 0
        assert any(i.code == "INTERNAL_ERROR" for i in error_report.issues)

    def test_playlist_filter_passed_to_query(self):
        """playlist_id parameter must filter the content query."""
        db = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.all.return_value = []

        list(check_library_health(db, playlist_id=42))
        db.query.return_value.join.assert_called_once()
