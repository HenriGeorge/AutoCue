"""Tests for autocue/analysis/similar.py"""
from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis.similar import (
    _camelot_angle,
    _build_vector,
    _dot,
    find_similar,
    clear_index,
)

MODULE = "autocue.analysis.similar"


# ---------------------------------------------------------------------------
# _camelot_angle
# ---------------------------------------------------------------------------

class TestCamelotAngle:
    def test_1a_is_zero(self):
        assert _camelot_angle("1A") == pytest.approx(0.0)

    def test_1b_is_offset_by_pi_over_12(self):
        # B-ring keys are offset by +π/12 (15°) from the A-ring base position
        assert _camelot_angle("1B") == pytest.approx(math.pi / 12.0)

    def test_a_and_b_same_number_differ(self):
        # 8A and 8B must no longer share the same angle
        assert _camelot_angle("8A") != pytest.approx(_camelot_angle("8B"))
        assert _camelot_angle("8B") - _camelot_angle("8A") == pytest.approx(math.pi / 12.0)

    def test_7_is_half_circle(self):
        # 7A: (7-1)/12 * 2π = π
        assert _camelot_angle("7A") == pytest.approx(math.pi)

    def test_12a_and_1a_are_adjacent(self):
        # cos distance between 12A and 1A should be small (adjacent)
        a12 = _camelot_angle("12A")
        a1 = _camelot_angle("1A")
        cos_dist = abs(math.cos(a12) - math.cos(a1)) ** 2 + abs(math.sin(a12) - math.sin(a1)) ** 2
        # Should be close to each other on the circle
        assert cos_dist < 0.4  # ~30° apart

    def test_empty_string_returns_zero(self):
        assert _camelot_angle("") == pytest.approx(0.0)

    def test_invalid_returns_zero(self):
        assert _camelot_angle("bad") == pytest.approx(0.0)

    def test_lowercase_accepted(self):
        assert _camelot_angle("8a") == _camelot_angle("8A")


# ---------------------------------------------------------------------------
# _build_vector
# ---------------------------------------------------------------------------

class TestBuildVector:
    def test_output_length(self):
        v = _build_vector("8A", 0.5, 0.1, False, 120.0)
        assert len(v) == 6

    def test_unit_length(self):
        v = _build_vector("8A", 0.5, 0.1, False, 120.0)
        mag = math.sqrt(sum(x * x for x in v))
        assert mag == pytest.approx(1.0)

    def test_vocal_proxy_changes_vector(self):
        v1 = _build_vector("8A", 0.5, 0.1, False, 120.0)
        v2 = _build_vector("8A", 0.5, 0.1, True, 120.0)
        assert v1 != v2

    def test_same_inputs_same_vector(self):
        v1 = _build_vector("8A", 0.6, 0.2, True, 120.0)
        v2 = _build_vector("8A", 0.6, 0.2, True, 120.0)
        assert v1 == v2

    def test_zero_vector_safe(self):
        # All-zero raw vector shouldn't crash (returns zeros)
        v = _build_vector("", 0.0, 0.0, False, 0.0)
        assert isinstance(v, list)
        assert len(v) == 6


# ---------------------------------------------------------------------------
# find_similar
# ---------------------------------------------------------------------------

def _make_content(track_id=1, bpm_int=12000, key="8A"):
    c = MagicMock()
    c.ID = track_id
    c.BPM = bpm_int
    key_obj = MagicMock()
    key_obj.ScaleName = key
    c.Key = key_obj
    return c


def _make_db(contents):
    db = MagicMock()
    # _build_index calls db.get_content().all(); on-demand uses db.get_content(ID=x)
    all_mock = MagicMock()
    all_mock.all.return_value = contents
    # keyword-arg calls (on-demand single lookup) return first matching content or None
    def _get_content_side_effect(**kwargs):
        if "ID" in kwargs:
            tid = int(kwargs["ID"])
            return next((c for c in contents if int(c.ID) == tid), None)
        return all_mock
    db.get_content.side_effect = _get_content_side_effect
    return db


class TestFindSimilar:
    def setup_method(self):
        clear_index()

    def test_empty_library_returns_empty(self):
        db = _make_db([])
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                result = find_similar(1, db)
        assert result == []

    def test_single_track_returns_empty(self):
        c = _make_content(track_id=1, bpm_int=12000)
        db = _make_db([c])
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                result = find_similar(1, db)
        assert result == []

    def test_bpm_gate_excludes_distant_tracks(self):
        c1 = _make_content(track_id=1, bpm_int=12000)   # 120 BPM
        c2 = _make_content(track_id=2, bpm_int=14000)   # 140 BPM — 20 BPM away
        db = _make_db([c1, c2])
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                result = find_similar(1, db, bpm_gate=8.0)
        assert all(r["track_id"] != 2 for r in result)

    def test_bpm_gate_includes_nearby_tracks(self):
        c1 = _make_content(track_id=1, bpm_int=12000)   # 120 BPM
        c2 = _make_content(track_id=2, bpm_int=12500)   # 125 BPM — 5 BPM away
        db = _make_db([c1, c2])
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                result = find_similar(1, db, bpm_gate=8.0)
        assert any(r["track_id"] == 2 for r in result)

    def test_identical_tracks_score_close_to_one(self):
        c1 = _make_content(track_id=1, bpm_int=12000, key="8A")
        c2 = _make_content(track_id=2, bpm_int=12000, key="8A")
        db = _make_db([c1, c2])
        energy = [0.6] * 20
        with patch(f"{MODULE}.get_energy_curve", return_value=energy):
            with patch(f"{MODULE}.get_mixability", return_value={"vocal_proxy": False, "energy_variance": 0.05}):
                result = find_similar(1, db)
        assert result[0]["score"] > 0.99

    def test_different_keys_score_lower(self):
        c1 = _make_content(track_id=1, bpm_int=12000, key="1A")   # angle 0
        c2 = _make_content(track_id=2, bpm_int=12000, key="7A")   # angle π (opposite)
        c3 = _make_content(track_id=3, bpm_int=12000, key="1A")   # same key as c1
        db = _make_db([c1, c2, c3])
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                result = find_similar(1, db)
        same_key_score = next(r["score"] for r in result if r["track_id"] == 3)
        opp_key_score  = next(r["score"] for r in result if r["track_id"] == 2)
        assert same_key_score > opp_key_score

    def test_results_sorted_by_score_descending(self):
        contents = [_make_content(track_id=i, bpm_int=12000) for i in range(1, 6)]
        db = _make_db(contents)
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                result = find_similar(1, db)
        scores = [r["score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_n_limits_results(self):
        contents = [_make_content(track_id=i, bpm_int=12000) for i in range(1, 20)]
        db = _make_db(contents)
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                result = find_similar(1, db, n=5)
        assert len(result) <= 5

    def test_unknown_track_returns_empty(self):
        c = _make_content(track_id=1, bpm_int=12000)
        db = _make_db([c])
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                result = find_similar(999, db)
        assert result == []

    def test_bpm_diff_in_results(self):
        c1 = _make_content(track_id=1, bpm_int=12000)
        c2 = _make_content(track_id=2, bpm_int=12500)
        db = _make_db([c1, c2])
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                result = find_similar(1, db)
        if result:
            assert "bpm_diff" in result[0]
            assert result[0]["bpm_diff"] == pytest.approx(5.0, abs=0.1)

    def test_score_is_between_zero_and_one(self):
        contents = [_make_content(track_id=i, bpm_int=12000, key=f"{(i%12)+1}A") for i in range(1, 8)]
        db = _make_db(contents)
        with patch(f"{MODULE}.get_energy_curve", return_value=[0.3] * 20):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                result = find_similar(1, db)
        for r in result:
            assert 0.0 <= r["score"] <= 1.0

    def test_clear_index_resets_state(self):
        c1 = _make_content(track_id=1, bpm_int=12000)
        c2 = _make_content(track_id=2, bpm_int=12000)
        db = _make_db([c1, c2])
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                find_similar(1, db)  # builds index
        clear_index()
        # After clearing, index is rebuilt on next call
        with patch(f"{MODULE}.get_energy_curve", return_value=None):
            with patch(f"{MODULE}.get_mixability", return_value=None):
                result = find_similar(1, db)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# TestDataQualityCap
# ---------------------------------------------------------------------------

class TestDataQualityCap:
    """Score is capped based on whether tracks have real energy data."""

    def _make_index(self, tracks):
        """tracks: list of (id, bpm, has_energy, key_str)"""
        from autocue.analysis.similar import _build_vector
        idx = {}
        for tid, bpm, has_e, key in tracks:
            vec = _build_vector(key, 0.0 if not has_e else 0.5, 0.0, False, bpm)
            idx[tid] = (bpm, vec, has_e)
        return idx

    def test_both_no_energy_capped_at_65(self):
        with patch(f"{MODULE}._INDEX", self._make_index([(1, 120.0, False, ""), (2, 120.0, False, "")])), \
             patch(f"{MODULE}._INDEX_BUILT", True):
            results = find_similar(1, MagicMock(), n=5)
        assert all(r["score"] <= 0.65 for r in results)

    def test_one_has_energy_capped_at_82(self):
        with patch(f"{MODULE}._INDEX", self._make_index([(1, 120.0, True, "8A"), (2, 120.0, False, "")])), \
             patch(f"{MODULE}._INDEX_BUILT", True):
            results = find_similar(1, MagicMock(), n=5)
        assert all(r["score"] <= 0.82 for r in results)

    def test_both_have_energy_can_exceed_82(self):
        with patch(f"{MODULE}._INDEX", self._make_index([(1, 120.0, True, "8A"), (2, 120.0, True, "8A")])), \
             patch(f"{MODULE}._INDEX_BUILT", True):
            results = find_similar(1, MagicMock(), n=5)
        # Should be close to 1.0 for identical key/energy
        assert any(r["score"] > 0.82 for r in results)
