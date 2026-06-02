"""Tests for autocue/analysis/classify.py"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis.classify import (
    CATEGORIES,
    _trap,
    _score_category,
    get_classification,
)

MODULE = "autocue.analysis.classify"


# ---------------------------------------------------------------------------
# _trap
# ---------------------------------------------------------------------------

class TestTrap:
    def test_full_inside_range(self):
        assert _trap(50, 0, 40, 60, 100) == 1.0

    def test_zero_below_lo_zero(self):
        assert _trap(-1, 0, 40, 60, 100) == 0.0

    def test_zero_at_hi_zero(self):
        assert _trap(100, 0, 40, 60, 100) == 0.0

    def test_ramp_up(self):
        val = _trap(20, 0, 40, 60, 100)
        assert 0 < val < 1

    def test_ramp_down(self):
        val = _trap(80, 0, 40, 60, 100)
        assert 0 < val < 1

    def test_midpoint_ramp_up_is_half(self):
        # lo_zero=0, lo_full=40 → midpoint 20 → 0.5
        assert abs(_trap(20, 0, 40, 60, 100) - 0.5) < 1e-9

    def test_midpoint_ramp_down_is_half(self):
        # hi_full=60, hi_zero=100 → midpoint 80 → 0.5
        assert abs(_trap(80, 0, 40, 60, 100) - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# _score_category
# ---------------------------------------------------------------------------

class TestScoreCategory:
    # Peak should use energy_peak, not energy_mean
    def test_peak_uses_peak_energy(self):
        # EDM build track: mean energy low but peak high
        low_mean = 0.35
        high_peak = 0.85
        score_with_peak   = _score_category(135, low_mean, high_peak, False, "peak")
        score_mean_only   = _score_category(135, low_mean, low_mean, False, "peak")
        assert score_with_peak > score_mean_only

    def test_peak_vocal_penalty(self):
        s_instr = _score_category(135, 0.7, 0.9, False, "peak")
        s_vocal = _score_category(135, 0.7, 0.9, True, "peak")
        assert s_instr > s_vocal

    def test_build_vocal_penalty(self):
        s_instr = _score_category(123, 0.45, 0.6, False, "build")
        s_vocal = _score_category(123, 0.45, 0.6, True, "build")
        assert s_instr > s_vocal

    def test_after_hours_vocal_boost(self):
        s_instr = _score_category(110, 0.35, 0.5, False, "after_hours")
        s_vocal = _score_category(110, 0.35, 0.5, True, "after_hours")
        assert s_vocal >= s_instr

    def test_warmup_low_bpm_scores_high(self):
        score = _score_category(95, 0.2, 0.3, False, "warmup")
        assert score > 0.5

    def test_peak_low_bpm_scores_zero(self):
        score = _score_category(90, 0.9, 0.9, False, "peak")
        assert score == 0.0

    def test_closing_high_bpm_scores_low(self):
        score = _score_category(150, 0.2, 0.2, False, "closing")
        assert score < 0.1

    def test_closing_low_bpm_scores_high(self):
        score = _score_category(85, 0.2, 0.2, False, "closing")
        assert score > 0.4

    def test_bpm_zero_gives_zero(self):
        # BPM=0 (unanalyzed) should not score in any active category
        for cat in ("warmup", "build", "peak", "after_hours"):
            assert _score_category(0, 0.5, 0.5, False, cat) == 0.0

    def test_neutral_energy_fallback(self):
        # energy=None should still produce a score (0.5 fallback, not error)
        score = _score_category(120, None, None, False, "build")
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# get_classification
# ---------------------------------------------------------------------------

_next_id = iter(range(1000, 9000))  # unique content IDs across all tests


class TestGetClassification:
    def _make_content(self, bpm_int=12000):
        c = MagicMock()
        c.ID = next(_next_id)  # unique per call — prevents accidental cache hits
        c.BPM = bpm_int  # stored as integer × 100
        return c

    def _run(self, bpm_int=12000, energy_curve=None, mix_data=None):
        content = self._make_content(bpm_int)
        with patch(f"{MODULE}.get_energy_curve", return_value=energy_curve):
            with patch(f"{MODULE}.get_mixability", return_value=mix_data):
                return get_classification(content, MagicMock())

    def test_bpm_divided_by_100(self):
        result = self._run(bpm_int=12000)
        assert result["bpm"] == 120.0

    def test_returns_all_required_keys(self):
        result = self._run()
        for key in ("primary", "label", "color", "confidence", "scores", "bpm"):
            assert key in result

    def test_scores_all_five_categories(self):
        result = self._run()
        for cat in CATEGORIES:
            assert cat in result["scores"]

    def test_primary_is_highest_score(self):
        result = self._run(bpm_int=13500, energy_curve=[0.8] * 50)
        primary = result["primary"]
        max_score = max(result["scores"].values())
        assert result["scores"][primary] == max_score

    def test_peak_bpm_and_high_energy_classifies_peak(self):
        result = self._run(bpm_int=13500, energy_curve=[0.85] * 50)
        assert result["primary"] == "peak"

    def test_warmup_bpm_and_low_energy_classifies_warmup(self):
        result = self._run(bpm_int=9500, energy_curve=[0.2] * 50)
        assert result["primary"] == "warmup"

    def test_closing_bpm_classifies_closing(self):
        # 80 BPM: fully inside closing range, partially inside warmup ramp → closing wins
        result = self._run(bpm_int=8000, energy_curve=[0.15] * 50)
        assert result["primary"] == "closing"

    def test_no_energy_data_still_returns_result(self):
        result = self._run(energy_curve=None)
        assert result is not None
        assert result["energy_mean"] is None

    def test_vocal_proxy_from_mixability(self):
        mix = {"vocal_proxy": True}
        result = self._run(bpm_int=13500, energy_curve=[0.85] * 50, mix_data=mix)
        assert result["vocal_proxy"] is True

    def test_no_mixability_defaults_vocal_false(self):
        result = self._run(bpm_int=13500, energy_curve=[0.85] * 50, mix_data=None)
        assert result["vocal_proxy"] is False

    def test_confidence_equals_primary_score(self):
        result = self._run(bpm_int=13500, energy_curve=[0.85] * 50)
        assert result["confidence"] == result["scores"][result["primary"]]

    def test_scores_sum_to_reasonable_total(self):
        result = self._run(bpm_int=12500, energy_curve=[0.5] * 50)
        for s in result["scores"].values():
            assert 0.0 <= s <= 1.0
