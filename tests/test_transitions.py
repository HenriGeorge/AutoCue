"""Tests for autocue/analysis/transitions.py"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis.transitions import (
    _bpm_score,
    _camelot_distance,
    _key_score,
    _energy_score,
    _window_avg,
    score_transition,
)

MODULE = "autocue.analysis.transitions"


# ---------------------------------------------------------------------------
# _bpm_score
# ---------------------------------------------------------------------------

class TestBpmScore:
    def test_identical_bpm_is_100(self):
        assert _bpm_score(120, 120) == 100.0

    def test_within_3pct_is_100(self):
        # 120 * 1.025 = 123 → delta=0.025 < 0.03
        assert _bpm_score(120, 123) == 100.0

    def test_at_10pct_is_zero(self):
        # delta = 0.1 exactly
        assert _bpm_score(100, 110) == 0.0

    def test_above_10pct_is_zero(self):
        assert _bpm_score(100, 115) == 0.0

    def test_between_3_and_10pct_is_partial(self):
        # delta = 0.065 → between 0
        score = _bpm_score(100, 106.5)
        assert 0 < score < 100

    def test_zero_bpm_a_returns_neutral(self):
        assert _bpm_score(0, 120) == 50.0

    def test_zero_bpm_b_returns_neutral(self):
        assert _bpm_score(120, 0) == 50.0

    def test_half_time_returns_50(self):
        # 120 → 60 (ratio 0.5)
        assert _bpm_score(120, 60) == 50.0

    def test_double_time_returns_50(self):
        # 60 → 120 (ratio 2.0)
        assert _bpm_score(60, 120) == 50.0

    def test_near_double_time_returns_50(self):
        # 140 → 70.5 ≈ double-time
        assert _bpm_score(140, 71.4) == 50.0

    def test_near_half_time_boundary_no_cliff(self):
        # 130 → 62 BPM: ratio = 0.477 — previously fell outside [0.48,0.52] and scored 0
        # With the widened gate [0.46, 0.54] this should return 50 (not 0)
        assert _bpm_score(130, 62) == 50.0

    def test_just_outside_widened_half_time_gate(self):
        # 130 → 57 BPM: ratio = 0.438 — outside widened gate → delta-based → 0
        assert _bpm_score(130, 57) == 0.0

    def test_directional_asymmetry_is_small(self):
        # delta divides by bpm_a, so A→B ≠ B→A for different BPMs — acceptable
        s1 = _bpm_score(120, 125)
        s2 = _bpm_score(125, 120)
        # Both should be non-zero and reasonably close (within 5 points)
        assert s1 > 0 and s2 > 0
        assert abs(s1 - s2) < 5.0


# ---------------------------------------------------------------------------
# _camelot_distance
# ---------------------------------------------------------------------------

class TestCamelotDistance:
    def test_same_number(self):
        assert _camelot_distance(8, 8) == 0

    def test_adjacent(self):
        assert _camelot_distance(8, 9) == 1
        assert _camelot_distance(8, 7) == 1

    def test_wrap_12_to_1(self):
        # 12 and 1 are adjacent on the wheel
        assert _camelot_distance(12, 1) == 1

    def test_wrap_1_to_12(self):
        assert _camelot_distance(1, 12) == 1

    def test_opposite(self):
        # 1 and 7 are max distance (6 steps)
        assert _camelot_distance(1, 7) == 6

    def test_distance_2(self):
        assert _camelot_distance(8, 10) == 2


# ---------------------------------------------------------------------------
# _key_score
# ---------------------------------------------------------------------------

class TestKeyScore:
    def test_same_key_is_100(self):
        assert _key_score("8A", "8A") == 100.0

    def test_same_number_diff_letter_is_75(self):
        assert _key_score("8A", "8B") == 75.0

    def test_adjacent_same_letter_is_80(self):
        assert _key_score("8A", "9A") == 80.0
        assert _key_score("8A", "7A") == 80.0

    def test_wrap_12_to_1_adjacent(self):
        # 12A → 1A is adjacent on wheel → 80
        assert _key_score("12A", "1A") == 80.0

    def test_incompatible_key_is_zero(self):
        # 1A → 7B: dist=6 (opposite side)
        assert _key_score("1A", "7B") == 0.0

    def test_no_artificial_floor(self):
        # dist=6 should be 0 (no floor at 20)
        assert _key_score("1A", "7A") == 0.0

    def test_unknown_key_is_neutral(self):
        assert _key_score("", "8A") == 50.0
        assert _key_score("8A", "") == 50.0

    def test_dist_2_is_50(self):
        assert _key_score("8A", "10A") == 50.0

    def test_dist_3_is_25(self):
        assert _key_score("8A", "11A") == 25.0


# ---------------------------------------------------------------------------
# _window_avg
# ---------------------------------------------------------------------------

class TestWindowAvg:
    def test_empty_returns_half(self):
        assert _window_avg([], 5, from_end=False) == 0.5

    def test_from_start(self):
        assert _window_avg([0.1, 0.2, 0.3, 0.8, 0.9], 3, from_end=False) == pytest.approx(0.2)

    def test_from_end(self):
        assert _window_avg([0.1, 0.2, 0.3, 0.8, 0.9], 3, from_end=True) == pytest.approx(2.0/3)

    def test_window_larger_than_curve(self):
        avg = _window_avg([0.5, 0.5], 10, from_end=False)
        assert avg == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# _energy_score
# ---------------------------------------------------------------------------

class TestEnergyScore:
    def test_identical_energy_is_100(self):
        curve = [0.5] * 20
        assert _energy_score(curve, curve) == 100.0

    def test_very_close_energy_is_100(self):
        a = [0.5] * 20
        b = [0.52] * 20
        # delta < 0.05
        assert _energy_score(a, b) == 100.0

    def test_large_delta_is_zero(self):
        a = [0.0] * 20
        b = [1.0] * 20
        assert _energy_score(a, b) == 0.0

    def test_none_curves_return_neutral(self):
        # Both None → unknown energy → neutral 50, not perfect 100
        assert _energy_score(None, None) == 50.0

    def test_partial_none_returns_partial(self):
        # One None → capped at 75; other [0.9] → delta=0.4 → ~22.2, capped at 75
        score = _energy_score(None, [0.9] * 20)
        assert 0 < score <= 75.0

    def test_score_decreases_with_delta(self):
        base = [0.5] * 20
        close = [0.6] * 20   # delta 0.1
        far = [0.8] * 20     # delta 0.3
        assert _energy_score(base, close) > _energy_score(base, far)


# ---------------------------------------------------------------------------
# score_transition
# ---------------------------------------------------------------------------

def _make_content(track_id=1, bpm_int=12000, key="8A"):
    c = MagicMock()
    c.ID = str(track_id)
    c.BPM = bpm_int
    key_obj = MagicMock()
    key_obj.ScaleName = key
    c.Key = key_obj
    return c


class TestScoreTransition:
    def _run(self, bpm_a=12000, bpm_b=12000, key_a="8A", key_b="8A",
             curve_a=None, curve_b=None):
        ca = _make_content(1, bpm_a, key_a)
        cb = _make_content(2, bpm_b, key_b)
        db = MagicMock()

        def fake_energy(content, db_):
            if content.ID == "1":
                return curve_a
            return curve_b

        with patch(f"{MODULE}.get_energy_curve", side_effect=fake_energy):
            return score_transition(ca, cb, db)

    def test_returns_required_keys(self):
        result = self._run()
        for key in ("overall", "bpm", "key", "energy", "bpm_a", "bpm_b", "key_a", "key_b", "explanation"):
            assert key in result

    def test_explanation_is_list_of_3_strings(self):
        result = self._run()
        assert isinstance(result["explanation"], list)
        assert len(result["explanation"]) == 3
        assert all(isinstance(s, str) for s in result["explanation"])

    def test_perfect_transition_scores_high(self):
        # Same BPM, same key, same energy
        curve = [0.5] * 20
        result = self._run(bpm_a=12000, bpm_b=12000, key_a="8A", key_b="8A",
                           curve_a=curve, curve_b=curve)
        assert result["overall"] == 100.0

    def test_bpm_divided_by_100(self):
        result = self._run(bpm_a=12000, bpm_b=12500)
        assert result["bpm_a"] == pytest.approx(120.0)
        assert result["bpm_b"] == pytest.approx(125.0)

    def test_incompatible_key_scores_low(self):
        result = self._run(bpm_a=12000, bpm_b=12000, key_a="1A", key_b="7B")
        assert result["key"] == 0.0
        # BPM=100, key=0, energy=100 → overall = 0.4*100 + 0.35*0 + 0.25*100 = 65
        assert result["overall"] < 70.0

    def test_large_bpm_diff_scores_low(self):
        result = self._run(bpm_a=12000, bpm_b=14000)
        assert result["bpm"] == 0.0

    def test_overall_weighted_correctly(self):
        # bpm=100, key=100, energy=100 → overall=100
        curve = [0.5] * 20
        result = self._run(curve_a=curve, curve_b=curve)
        assert result["overall"] == pytest.approx(100.0, abs=0.5)

    def test_end_energy_a_reported(self):
        curve_a = [0.8] * 20
        result = self._run(curve_a=curve_a, curve_b=[0.3] * 20)
        assert result["end_energy_a"] == pytest.approx(0.8, abs=0.01)

    def test_start_energy_b_reported(self):
        curve_b = [0.3] * 20
        result = self._run(curve_a=[0.8] * 20, curve_b=curve_b)
        assert result["start_energy_b"] == pytest.approx(0.3, abs=0.01)

    def test_no_energy_data_still_returns_result(self):
        result = self._run(curve_a=None, curve_b=None)
        assert result is not None
        assert result["end_energy_a"] is None
        assert result["start_energy_b"] is None
