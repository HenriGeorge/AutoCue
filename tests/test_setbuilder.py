"""Tests for autocue/analysis/setbuilder.py"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis.setbuilder import (
    _category_order,
    _target_category,
    _energy_penalty,
    _get_track_info,
    build_set,
)

MODULE = "autocue.analysis.setbuilder"


# ---------------------------------------------------------------------------
# _category_order
# ---------------------------------------------------------------------------

class TestCategoryOrder:
    def test_ascending_returns_warmup_build_peak(self):
        assert _category_order({"start_bpm": 110, "end_bpm": 135}) == ["warmup", "build", "peak"]

    def test_equal_bpm_returns_ascending(self):
        assert _category_order({"start_bpm": 120, "end_bpm": 120}) == ["warmup", "build", "peak"]

    def test_descending_returns_peak_after_hours_closing(self):
        assert _category_order({"start_bpm": 135, "end_bpm": 110}) == ["peak", "after_hours", "closing"]

    def test_defaults_when_no_bpm(self):
        result = _category_order({})
        assert result == ["warmup", "build", "peak"]


# ---------------------------------------------------------------------------
# _target_category
# ---------------------------------------------------------------------------

class TestTargetCategory:
    def test_first_step_is_first_category(self):
        cats = ["warmup", "build", "peak"]
        assert _target_category(0, 10, cats) == "warmup"

    def test_last_step_is_last_category(self):
        cats = ["warmup", "build", "peak"]
        assert _target_category(9, 10, cats) == "peak"

    def test_mid_step(self):
        cats = ["warmup", "build", "peak"]
        result = _target_category(5, 10, cats)
        assert result in cats

    def test_single_total_step(self):
        cats = ["warmup", "build", "peak"]
        assert _target_category(0, 1, cats) == "warmup"

    def test_step_beyond_total_clamped(self):
        cats = ["warmup", "build", "peak"]
        # step >= total_steps — should not raise IndexError
        result = _target_category(10, 10, cats)
        assert result == "peak"


# ---------------------------------------------------------------------------
# _energy_penalty
# ---------------------------------------------------------------------------

class TestEnergyPenalty:
    def test_none_values_returns_zero(self):
        assert _energy_penalty(None, None, "build") == 0.0

    def test_one_none_returns_zero(self):
        assert _energy_penalty(0.8, None, "build") == 0.0
        assert _energy_penalty(None, 0.6, "build") == 0.0

    def test_build_with_increasing_energy_no_penalty(self):
        assert _energy_penalty(0.3, 0.6, "build") == 0.0

    def test_build_with_drop_returns_penalty(self):
        assert _energy_penalty(0.8, 0.4, "build") == 15.0

    def test_drop_with_falling_energy_no_penalty(self):
        assert _energy_penalty(0.8, 0.4, "drop") == 0.0

    def test_drop_with_rising_energy_returns_penalty(self):
        assert _energy_penalty(0.3, 0.7, "drop") == 15.0

    def test_flat_mode_penalizes_large_swings(self):
        # flat mode: large energy change in either direction should be penalized
        assert _energy_penalty(0.9, 0.1, "flat") == 15.0  # big drop
        assert _energy_penalty(0.1, 0.9, "flat") == 15.0  # big rise

    def test_flat_mode_allows_small_swings(self):
        # delta < 0.15 — no penalty in flat mode
        assert _energy_penalty(0.5, 0.55, "flat") == 0.0

    def test_small_drop_in_build_no_penalty(self):
        # Only 0.1 delta (< 0.15 threshold)
        assert _energy_penalty(0.5, 0.4, "build") == 0.0


# ---------------------------------------------------------------------------
# _get_track_info
# ---------------------------------------------------------------------------

class TestGetTrackInfo:
    def _make_content(self, title="Test", artist="Artist", bpm_int=12000, length=300):
        c = MagicMock()
        c.Title = title
        c.ArtistName = artist
        c.BPM = bpm_int
        c.Length = length
        return c

    def test_basic_extraction(self):
        c = self._make_content("My Track", "DJ X", 13000, 360)
        title, artist, bpm, dur = _get_track_info(c)
        assert title == "My Track"
        assert artist == "DJ X"
        assert bpm == pytest.approx(130.0)
        assert dur == 360.0

    def test_bpm_division_by_100(self):
        c = self._make_content(bpm_int=14000)
        _, _, bpm, _ = _get_track_info(c)
        assert bpm == pytest.approx(140.0)

    def test_none_fields_default_to_empty_string(self):
        c = MagicMock()
        c.Title = None
        c.ArtistName = None
        c.BPM = 0
        c.Length = 0
        title, artist, bpm, dur = _get_track_info(c)
        assert title == ""
        assert artist == ""
        assert bpm == 0.0


# ---------------------------------------------------------------------------
# build_set — integration-style with mocked DB
# ---------------------------------------------------------------------------

def _make_db_content(track_id=1, bpm_int=12000, length=360, key="8A", title="Track", artist="DJ"):
    c = MagicMock()
    c.ID = str(track_id)
    c.Title = title
    c.ArtistName = artist
    c.BPM = bpm_int
    c.Length = length
    key_obj = MagicMock()
    key_obj.ScaleName = key
    c.Key = key_obj
    return c


class TestBuildSet:
    def _make_db_with_tracks(self, tracks: list) -> MagicMock:
        """Create a mock DB that returns the provided content objects."""
        db = MagicMock()
        content_map = {int(c.ID): c for c in tracks}

        def get_content(**kwargs):
            if "ID" in kwargs:
                return content_map.get(int(kwargs["ID"]))
            mock_q = MagicMock()
            mock_q.__iter__ = MagicMock(return_value=iter(tracks))
            return mock_q

        db.get_content.side_effect = get_content
        return db

    def test_returns_empty_when_no_seed(self):
        db = MagicMock()
        db.get_content.return_value = iter([])
        with patch("autocue.analysis.similar._INDEX_BUILT", False), \
             patch(f"{MODULE}._build_index"):
            result = build_set(db)
        assert result["tracks"] == []
        assert result["terminated_reason"] == "no_candidates_passed_thresholds"

    def test_returns_list_of_dicts(self):
        tracks = [_make_db_content(i, 12000 + i * 100, 360, "8A", f"Track{i}", "DJ")
                  for i in range(1, 6)]
        db = self._make_db_with_tracks(tracks)

        fake_similar = [{"track_id": int(tracks[j].ID), "score": 0.9, "bpm_diff": 1.0}
                        for j in range(1, 5)]
        fake_class = {"primary": "warmup", "scores": {"warmup": 0.8, "build": 0.4, "peak": 0.2}}
        fake_transition = {"overall": 75.0, "bpm": 80.0, "key": 70.0, "energy": 75.0,
                           "bpm_a": 120.0, "bpm_b": 121.0, "key_a": "8A", "key_b": "8A",
                           "end_energy_a": 0.5, "start_energy_b": 0.5}

        with patch("autocue.analysis.similar._INDEX_BUILT", True), \
             patch(f"{MODULE}.find_similar", return_value=fake_similar), \
             patch(f"{MODULE}.get_classification", return_value=fake_class), \
             patch(f"{MODULE}.score_transition", return_value=fake_transition):
            result = build_set(db, duration_minutes=0.1)

        tracks_out = result["tracks"]
        assert isinstance(tracks_out, list)
        assert len(tracks_out) >= 1
        assert all("track_id" in t for t in tracks_out)
        assert all("title" in t for t in tracks_out)
        assert all("bpm" in t for t in tracks_out)
        assert "terminated_reason" in result

    def test_no_duplicate_tracks(self):
        """Each track should appear at most once in the set."""
        tracks = [_make_db_content(i, 12000, 120, "8A", f"Track{i}", "DJ")
                  for i in range(1, 10)]
        db = self._make_db_with_tracks(tracks)

        # find_similar always returns all tracks except track 1
        fake_similar = [{"track_id": int(t.ID), "score": 0.9, "bpm_diff": 0.5}
                        for t in tracks[1:]]
        fake_class = {"primary": "warmup", "scores": {"warmup": 0.9}}
        fake_transition = {"overall": 75.0, "bpm": 90.0, "key": 70.0, "energy": 80.0,
                           "bpm_a": 120.0, "bpm_b": 120.0, "key_a": "8A", "key_b": "8A",
                           "end_energy_a": None, "start_energy_b": None}

        with patch("autocue.analysis.similar._INDEX_BUILT", True), \
             patch(f"{MODULE}.find_similar", return_value=fake_similar), \
             patch(f"{MODULE}.get_classification", return_value=fake_class), \
             patch(f"{MODULE}.score_transition", return_value=fake_transition):
            result = build_set(db, duration_minutes=10.0)

        track_ids = [t["track_id"] for t in result["tracks"]]
        assert len(track_ids) == len(set(track_ids)), "Duplicate track found in set"

    def test_seed_track_id_used_when_provided(self):
        tracks = [_make_db_content(i, 12000, 360, "8A", f"Track{i}", "DJ")
                  for i in range(1, 4)]
        db = self._make_db_with_tracks(tracks)

        fake_class = {"primary": "warmup", "scores": {"warmup": 0.9}}

        with patch("autocue.analysis.similar._INDEX_BUILT", True), \
             patch(f"{MODULE}.find_similar", return_value=[]), \
             patch(f"{MODULE}.get_classification", return_value=fake_class):
            result = build_set(db, seed_track_id=2, duration_minutes=0.01)

        assert len(result["tracks"]) >= 1
        assert result["tracks"][0]["track_id"] == 2

    def test_transition_score_in_output(self):
        tracks = [_make_db_content(i, 12000 + i * 50, 300) for i in range(1, 4)]
        db = self._make_db_with_tracks(tracks)

        fake_similar = [{"track_id": int(t.ID), "score": 0.9, "bpm_diff": 0.5}
                        for t in tracks[1:]]
        fake_class = {"primary": "warmup", "scores": {"warmup": 0.9}}
        fake_transition = {"overall": 82.5, "bpm": 90.0, "key": 80.0, "energy": 75.0,
                           "bpm_a": 120.0, "bpm_b": 120.5, "key_a": "8A", "key_b": "8A",
                           "end_energy_a": None, "start_energy_b": None}

        with patch("autocue.analysis.similar._INDEX_BUILT", True), \
             patch(f"{MODULE}.find_similar", return_value=fake_similar), \
             patch(f"{MODULE}.get_classification", return_value=fake_class), \
             patch(f"{MODULE}.score_transition", return_value=fake_transition):
            result = build_set(db, duration_minutes=0.1)

        tracks_out = result["tracks"]
        # First track has no transition score
        assert tracks_out[0]["transition_score"] is None
        # Subsequent tracks have transition scores
        if len(tracks_out) > 1:
            assert tracks_out[1]["transition_score"] == 82.5

    def test_low_transition_score_filtered(self):
        """Candidates scoring below _MIN_TRANSITION_SCORE should be skipped."""
        tracks = [_make_db_content(i, 12000, 120) for i in range(1, 4)]
        db = self._make_db_with_tracks(tracks)

        fake_similar = [{"track_id": int(t.ID), "score": 0.9, "bpm_diff": 0.5}
                        for t in tracks[1:]]
        fake_class = {"primary": "warmup", "scores": {"warmup": 0.9}}
        # Score below 40.0 threshold
        fake_transition = {"overall": 20.0, "bpm": 30.0, "key": 10.0, "energy": 20.0,
                           "bpm_a": 120.0, "bpm_b": 120.0, "key_a": "8A", "key_b": "8A",
                           "end_energy_a": None, "start_energy_b": None}

        with patch("autocue.analysis.similar._INDEX_BUILT", True), \
             patch(f"{MODULE}.find_similar", return_value=fake_similar), \
             patch(f"{MODULE}.get_classification", return_value=fake_class), \
             patch(f"{MODULE}.score_transition", return_value=fake_transition):
            result = build_set(db, duration_minutes=5.0)

        # Only seed track should be in result if all transitions fail threshold
        assert len(result["tracks"]) == 1


# ---------------------------------------------------------------------------
# Trajectory-deficit penalty (#116)
# ---------------------------------------------------------------------------

class TestTrajectoryDeficitPenalty:
    """Issue #116: small-BPM-span build sets (e.g. 120→128 / 30min) used to
    stall below start_bpm because the existing BPM-progress bonus was
    proportionally diluted on short climbs — a 1-BPM step was 12.5% of an
    8-BPM span (vs 4% of the 25-BPM range the algorithm was tuned on), so
    same-BPM transitions tied climbers and the beam picked same-BPM.

    The trajectory-deficit penalty subtracts points from candidates that
    sit significantly below where the set SHOULD be at the current elapsed
    duration (linear start→end interpolation). Same-BPM transitions in a
    build set get punished proportional to how far the climb has fallen
    behind, so climbers actually win even on short spans.
    """

    def _make_db_with_tracks(self, tracks: list) -> MagicMock:
        db = MagicMock()
        content_map = {int(c.ID): c for c in tracks}

        def get_content(**kwargs):
            if "ID" in kwargs:
                return content_map.get(int(kwargs["ID"]))
            mock_q = MagicMock()
            mock_q.__iter__ = MagicMock(return_value=iter(tracks))
            return mock_q

        db.get_content.side_effect = get_content
        return db

    @staticmethod
    def _transition_factory():
        """Return a side_effect that simulates score_transition's behaviour:
        BPM score drops linearly with diff, key/energy stay constant. Lets
        the beam-search realistically prefer same-BPM unless something else
        pushes it (the trajectory penalty)."""
        def fake(a, b, db):
            ba = float(getattr(a, "BPM", 0) or 0) / 100.0
            bb = float(getattr(b, "BPM", 0) or 0) / 100.0
            diff_pct = abs(bb - ba) / max(ba, 0.1)
            # 0% diff → 100; 15% diff → 0; linear in between.
            bpm = max(0.0, 100.0 - (diff_pct / 0.15) * 100.0)
            return {
                "overall": (0.40 * bpm + 0.35 * 80.0 + 0.25 * 70.0),
                "bpm": bpm, "key": 80.0, "energy": 70.0,
                "bpm_a": ba, "bpm_b": bb, "key_a": "8A", "key_b": "8A",
                "end_energy_a": 0.5, "start_energy_b": 0.5,
            }
        return fake

    def test_small_span_build_set_actually_climbs(self):
        """The exact QA probe scenario: start=120, end=128, 20min, build.

        Library has tracks at 120, 122, 124, 126, 128. Without the
        trajectory penalty, the seed lands near 120 and the beam stays
        there (same-BPM wins). WITH the penalty, the beam climbs because
        each slot's expected_bpm rises and below-line candidates lose
        points to mid-set."""
        bpms_int = [12000, 12200, 12400, 12600, 12800]  # 120, 122, 124, 126, 128
        tracks = [_make_db_content(i, bpms_int[i - 1], 240, "8A", f"Track{i}", f"DJ{i}")
                  for i in range(1, 6)]
        db = self._make_db_with_tracks(tracks)

        # find_similar returns ALL non-seed tracks so the beam has free choice.
        def fake_similar(track_id, db_, n, bpm_gate):
            return [{"track_id": int(t.ID), "score": 0.9,
                     "bpm_diff": abs(float(getattr(t, "BPM", 0) or 0) / 100.0
                                     - float(getattr(db_.get_content(ID=track_id), "BPM", 0) or 0) / 100.0)}
                    for t in tracks if int(t.ID) != track_id]

        fake_class = {"primary": "build", "scores": {"warmup": 0.8, "build": 0.8, "peak": 0.5}}

        with patch("autocue.analysis.similar._INDEX_BUILT", True), \
             patch(f"{MODULE}.find_similar", side_effect=fake_similar), \
             patch(f"{MODULE}.get_classification", return_value=fake_class), \
             patch(f"{MODULE}.score_transition", side_effect=self._transition_factory()):
            result = build_set(
                db, start_bpm=120.0, end_bpm=128.0,
                duration_minutes=20.0, energy_mode="build",
            )

        out = result["tracks"]
        assert len(out) >= 3, f"expected ≥3 tracks, got {len(out)}: {out}"
        first_bpm = out[0]["bpm"]
        last_bpm = out[-1]["bpm"]
        # The actual #116 symptom was last_bpm <= first_bpm (set stalled).
        # With the trajectory penalty, the last track must be strictly
        # above the first.
        assert last_bpm > first_bpm, (
            f"set did not climb — bpms={[t['bpm'] for t in out]} "
            f"(first={first_bpm}, last={last_bpm})"
        )

    def test_penalty_zero_on_same_start_and_end_bpm(self):
        """Flat sets (start == end) should not apply the trajectory penalty —
        the existing same-BPM behaviour is correct for flat mode."""
        tracks = [_make_db_content(i, 12000, 240, "8A", f"T{i}", f"D{i}") for i in range(1, 4)]
        db = self._make_db_with_tracks(tracks)

        def fake_similar(track_id, db_, n, bpm_gate):
            return [{"track_id": int(t.ID), "score": 0.9, "bpm_diff": 0.0}
                    for t in tracks if int(t.ID) != track_id]

        fake_class = {"primary": "warmup", "scores": {"warmup": 0.9}}

        with patch("autocue.analysis.similar._INDEX_BUILT", True), \
             patch(f"{MODULE}.find_similar", side_effect=fake_similar), \
             patch(f"{MODULE}.get_classification", return_value=fake_class), \
             patch(f"{MODULE}.score_transition", side_effect=self._transition_factory()):
            result = build_set(
                db, start_bpm=120.0, end_bpm=120.0,
                duration_minutes=5.0, energy_mode="flat",
            )

        # Flat set: every track at 120 — penalty must not fire (would
        # produce zero-score candidates that fail the relaxation tier
        # threshold and we'd get a single-track seed back).
        assert len(result["tracks"]) >= 2, (
            f"flat set unexpectedly short: {result['tracks']}"
        )

    def test_drop_mode_penalises_above_trajectory(self):
        """Drop (descending) mode: candidates ABOVE the expected trajectory
        get penalised. Mirror of the build case."""
        # Tracks at 128, 124, 120, 116, 112 — descending arc.
        bpms_int = [12800, 12400, 12000, 11600, 11200]
        tracks = [_make_db_content(i, bpms_int[i - 1], 240, "8A", f"T{i}", f"D{i}")
                  for i in range(1, 6)]
        db = self._make_db_with_tracks(tracks)

        def fake_similar(track_id, db_, n, bpm_gate):
            return [{"track_id": int(t.ID), "score": 0.9, "bpm_diff": 0.0}
                    for t in tracks if int(t.ID) != track_id]

        fake_class = {"primary": "closing", "scores": {"closing": 0.8, "after_hours": 0.8, "peak": 0.5}}

        with patch("autocue.analysis.similar._INDEX_BUILT", True), \
             patch(f"{MODULE}.find_similar", side_effect=fake_similar), \
             patch(f"{MODULE}.get_classification", return_value=fake_class), \
             patch(f"{MODULE}.score_transition", side_effect=self._transition_factory()):
            result = build_set(
                db, start_bpm=128.0, end_bpm=112.0,
                duration_minutes=20.0, energy_mode="drop",
            )

        out = result["tracks"]
        assert len(out) >= 3
        first_bpm, last_bpm = out[0]["bpm"], out[-1]["bpm"]
        assert last_bpm < first_bpm, (
            f"drop set did not descend — bpms={[t['bpm'] for t in out]} "
            f"(first={first_bpm}, last={last_bpm})"
        )
