"""Property / invariant tests for pure scoring functions.

Uses Hypothesis for generative cases plus hand-written breakpoint tests.
Functions under test live in autocue.analysis.classify and
autocue.analysis.transitions — pure math, no I/O, no DB required.
"""
from __future__ import annotations

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from autocue.analysis.classify import _trap
from autocue.analysis.transitions import (
    _bpm_score,
    _camelot_distance,
    _key_score,
    _energy_score,
)

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

_finite_floats = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
_positive_bpm = st.floats(min_value=0.01, max_value=300.0, allow_nan=False, allow_infinity=False)
_camelot_num = st.integers(min_value=1, max_value=12)
_energy_val = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_energy_curve = st.lists(_energy_val, min_size=0, max_size=50)


# ===========================================================================
# 1.  _trap  (autocue.analysis.classify)
# ===========================================================================

class TestTrapProperties:
    """Property tests for the trapezoidal membership function."""

    # -----------------------------------------------------------------------
    # Generative: output always in [0.0, 1.0]
    # -----------------------------------------------------------------------

    @given(
        value=_finite_floats,
        lo_zero=_finite_floats,
        width=st.floats(min_value=0.0, max_value=1e4, allow_nan=False, allow_infinity=False),
        plateau=st.floats(min_value=0.0, max_value=1e4, allow_nan=False, allow_infinity=False),
        tail=st.floats(min_value=0.0, max_value=1e4, allow_nan=False, allow_infinity=False),
    )
    def test_output_in_unit_interval(self, value, lo_zero, width, plateau, tail):
        lo_full = lo_zero + width
        hi_full = lo_full + plateau
        hi_zero = hi_full + tail
        result = _trap(value, lo_zero, lo_full, hi_full, hi_zero)
        assert 0.0 <= result <= 1.0

    # -----------------------------------------------------------------------
    # Generative: exactly 1.0 on plateau
    # -----------------------------------------------------------------------

    @given(
        lo_zero=_finite_floats,
        ramp=st.floats(min_value=1e-3, max_value=1e4, allow_nan=False, allow_infinity=False),
        plateau=st.floats(min_value=1e-3, max_value=1e4, allow_nan=False, allow_infinity=False),
        tail=st.floats(min_value=1e-3, max_value=1e4, allow_nan=False, allow_infinity=False),
        frac=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    def test_plateau_is_exactly_one(self, lo_zero, ramp, plateau, tail, frac):
        lo_full = lo_zero + ramp
        hi_full = lo_full + plateau
        hi_zero = hi_full + tail
        value = lo_full + frac * plateau   # guaranteed in [lo_full, hi_full]
        assert _trap(value, lo_zero, lo_full, hi_full, hi_zero) == 1.0

    # -----------------------------------------------------------------------
    # Generative: non-decreasing on rising ramp
    # -----------------------------------------------------------------------

    @given(
        lo_zero=_finite_floats,
        ramp=st.floats(min_value=1e-3, max_value=1e4, allow_nan=False, allow_infinity=False),
        plateau=st.floats(min_value=1e-3, max_value=1e4, allow_nan=False, allow_infinity=False),
        tail=st.floats(min_value=1e-3, max_value=1e4, allow_nan=False, allow_infinity=False),
        f1=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        f2=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    def test_rising_ramp_nondecreasing(self, lo_zero, ramp, plateau, tail, f1, f2):
        lo_full = lo_zero + ramp
        hi_full = lo_full + plateau
        hi_zero = hi_full + tail
        v1 = lo_zero + f1 * ramp
        v2 = lo_zero + f2 * ramp
        r1 = _trap(v1, lo_zero, lo_full, hi_full, hi_zero)
        r2 = _trap(v2, lo_zero, lo_full, hi_full, hi_zero)
        if v1 <= v2:
            assert r1 <= r2 + 1e-12

    # -----------------------------------------------------------------------
    # Generative: non-increasing on falling ramp
    # -----------------------------------------------------------------------

    @given(
        lo_zero=_finite_floats,
        ramp=st.floats(min_value=1e-3, max_value=1e4, allow_nan=False, allow_infinity=False),
        plateau=st.floats(min_value=1e-3, max_value=1e4, allow_nan=False, allow_infinity=False),
        tail=st.floats(min_value=1e-3, max_value=1e4, allow_nan=False, allow_infinity=False),
        f1=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        f2=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    def test_falling_ramp_nonincreasing(self, lo_zero, ramp, plateau, tail, f1, f2):
        lo_full = lo_zero + ramp
        hi_full = lo_full + plateau
        hi_zero = hi_full + tail
        v1 = hi_full + f1 * tail
        v2 = hi_full + f2 * tail
        r1 = _trap(v1, lo_zero, lo_full, hi_full, hi_zero)
        r2 = _trap(v2, lo_zero, lo_full, hi_full, hi_zero)
        if v1 <= v2:
            assert r1 >= r2 - 1e-12

    # -----------------------------------------------------------------------
    # Breakpoints: continuity — no jump at the four corner points
    # -----------------------------------------------------------------------

    def test_continuous_at_lo_full(self):
        # _trap(lo_full - ε) approaches _trap(lo_full) = 1.0 from below
        lo_zero, lo_full, hi_full, hi_zero = 0.0, 40.0, 60.0, 100.0
        eps = 1e-7
        just_below = _trap(lo_full - eps, lo_zero, lo_full, hi_full, hi_zero)
        at_lo_full = _trap(lo_full, lo_zero, lo_full, hi_full, hi_zero)
        assert abs(just_below - at_lo_full) < 1e-4, (
            f"Jump at lo_full: {just_below} vs {at_lo_full}"
        )

    def test_continuous_at_hi_full(self):
        lo_zero, lo_full, hi_full, hi_zero = 0.0, 40.0, 60.0, 100.0
        eps = 1e-7
        at_hi_full = _trap(hi_full, lo_zero, lo_full, hi_full, hi_zero)
        just_above = _trap(hi_full + eps, lo_zero, lo_full, hi_full, hi_zero)
        assert abs(at_hi_full - just_above) < 1e-4, (
            f"Jump at hi_full: {at_hi_full} vs {just_above}"
        )

    def test_continuous_at_lo_zero(self):
        lo_zero, lo_full, hi_full, hi_zero = 0.0, 40.0, 60.0, 100.0
        eps = 1e-7
        # value just at lo_zero → 0; value just above → near 0
        at_lo_zero = _trap(lo_zero, lo_zero, lo_full, hi_full, hi_zero)
        just_above = _trap(lo_zero + eps, lo_zero, lo_full, hi_full, hi_zero)
        assert at_lo_zero == 0.0
        assert just_above >= 0.0
        assert abs(at_lo_zero - just_above) < 1e-4

    def test_continuous_at_hi_zero(self):
        lo_zero, lo_full, hi_full, hi_zero = 0.0, 40.0, 60.0, 100.0
        eps = 1e-7
        at_hi_zero = _trap(hi_zero, lo_zero, lo_full, hi_full, hi_zero)
        just_below = _trap(hi_zero - eps, lo_zero, lo_full, hi_full, hi_zero)
        assert at_hi_zero == 0.0
        assert just_below >= 0.0
        assert abs(at_hi_zero - just_below) < 1e-4

    # -----------------------------------------------------------------------
    # Breakpoints: concrete plateau/ramp values
    # -----------------------------------------------------------------------

    def test_lo_zero_exactly_zero(self):
        assert _trap(0.0, 0.0, 40.0, 60.0, 100.0) == 0.0

    def test_hi_zero_exactly_zero(self):
        assert _trap(100.0, 0.0, 40.0, 60.0, 100.0) == 0.0

    def test_below_lo_zero_is_zero(self):
        assert _trap(-1.0, 0.0, 40.0, 60.0, 100.0) == 0.0

    def test_above_hi_zero_is_zero(self):
        assert _trap(101.0, 0.0, 40.0, 60.0, 100.0) == 0.0

    def test_midpoint_rising_ramp_is_half(self):
        # lo_zero=0, lo_full=40 → midpoint 20 → 0.5
        assert abs(_trap(20.0, 0.0, 40.0, 60.0, 100.0) - 0.5) < 1e-9

    def test_midpoint_falling_ramp_is_half(self):
        # hi_full=60, hi_zero=100 → midpoint 80 → 0.5
        assert abs(_trap(80.0, 0.0, 40.0, 60.0, 100.0) - 0.5) < 1e-9


# ===========================================================================
# 2.  _bpm_score  (autocue.analysis.transitions)
# ===========================================================================

class TestBpmScoreProperties:
    """Property tests for the BPM compatibility scorer."""

    # -----------------------------------------------------------------------
    # Generative: output always in [0.0, 100.0]
    # -----------------------------------------------------------------------

    @given(
        bpm_a=st.floats(min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False),
        bpm_b=st.floats(min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False),
    )
    def test_output_in_valid_range(self, bpm_a, bpm_b):
        result = _bpm_score(bpm_a, bpm_b)
        assert 0.0 <= result <= 100.0

    # -----------------------------------------------------------------------
    # Generative: identical BPM → 100.0
    # -----------------------------------------------------------------------

    @given(bpm=_positive_bpm)
    def test_identical_bpm_is_100(self, bpm):
        assert _bpm_score(bpm, bpm) == 100.0

    # -----------------------------------------------------------------------
    # Generative: monotone non-increasing in |delta| outside half/double windows
    # -----------------------------------------------------------------------

    @given(
        bpm_a=_positive_bpm,
        d1=st.floats(min_value=0.0, max_value=0.09, allow_nan=False, allow_infinity=False),
        d2=st.floats(min_value=0.0, max_value=0.09, allow_nan=False, allow_infinity=False),
    )
    def test_monotone_nonincreasing_in_delta(self, bpm_a, d1, d2):
        """Smaller delta should yield score >= larger delta, outside special zones."""
        bpm_b1 = bpm_a * (1.0 + d1)
        bpm_b2 = bpm_a * (1.0 + d2)
        r1 = bpm_a / bpm_b1 if bpm_b1 > 0 else 0
        r2 = bpm_a / bpm_b2 if bpm_b2 > 0 else 0
        # Skip if either ratio falls in half/double time window (ratio from bpm_b/bpm_a)
        ratio1 = bpm_b1 / bpm_a
        ratio2 = bpm_b2 / bpm_a
        in_half_or_double = lambda r: (0.46 <= r <= 0.54) or (1.85 <= r <= 2.15)
        assume(not in_half_or_double(ratio1) and not in_half_or_double(ratio2))

        s1 = _bpm_score(bpm_a, bpm_b1)
        s2 = _bpm_score(bpm_a, bpm_b2)
        if d1 <= d2:
            assert s1 >= s2 - 1e-9

    # -----------------------------------------------------------------------
    # Breakpoints: zero/negative BPM → 50.0
    # -----------------------------------------------------------------------

    def test_zero_bpm_a_is_neutral(self):
        assert _bpm_score(0.0, 120.0) == 50.0

    def test_zero_bpm_b_is_neutral(self):
        assert _bpm_score(120.0, 0.0) == 50.0

    def test_negative_bpm_a_is_neutral(self):
        assert _bpm_score(-10.0, 120.0) == 50.0

    def test_negative_bpm_b_is_neutral(self):
        assert _bpm_score(120.0, -5.0) == 50.0

    # -----------------------------------------------------------------------
    # Breakpoints: half-time window boundary — ratio 0.46 and 0.54
    # -----------------------------------------------------------------------

    def test_half_time_exact_ratio_050(self):
        # 120 → 60, ratio = 0.50
        assert _bpm_score(120.0, 60.0) == 50.0

    def test_half_time_lower_boundary_046(self):
        # ratio = 0.46 (inclusive)
        assert _bpm_score(100.0, 46.0) == 50.0

    def test_half_time_upper_boundary_054(self):
        # ratio = 0.54 (inclusive)
        assert _bpm_score(100.0, 54.0) == 50.0

    def test_half_time_just_below_046_not_50(self):
        # ratio = 0.459 — outside window
        result = _bpm_score(1000.0, 459.0)
        assert result != 50.0, f"ratio 0.459 should be outside half-time window, got {result}"

    def test_half_time_just_inside_046_is_50(self):
        # ratio = 0.461 — inside window
        assert _bpm_score(1000.0, 461.0) == 50.0

    def test_half_time_just_inside_054_is_50(self):
        # ratio = 0.539 — inside window
        assert _bpm_score(1000.0, 539.0) == 50.0

    def test_half_time_just_above_054_not_50(self):
        # ratio = 0.541 — outside window
        result = _bpm_score(1000.0, 541.0)
        assert result != 50.0, f"ratio 0.541 should be outside half-time window, got {result}"

    # -----------------------------------------------------------------------
    # Breakpoints: double-time window boundary — ratio 1.85 and 2.15
    # -----------------------------------------------------------------------

    def test_double_time_exact_ratio_200(self):
        # 60 → 120, ratio = 2.0
        assert _bpm_score(60.0, 120.0) == 50.0

    def test_double_time_lower_boundary_185(self):
        assert _bpm_score(100.0, 185.0) == 50.0

    def test_double_time_upper_boundary_215(self):
        assert _bpm_score(100.0, 215.0) == 50.0

    # -----------------------------------------------------------------------
    # Breakpoints: delta thresholds
    # -----------------------------------------------------------------------

    def test_delta_exactly_003_is_100(self):
        # delta = 0.03 exactly: bpm_a=100, bpm_b=103
        assert _bpm_score(100.0, 103.0) == 100.0

    def test_delta_exactly_010_is_0(self):
        # delta = 0.10 exactly: bpm_a=100, bpm_b=110
        assert _bpm_score(100.0, 110.0) == 0.0

    def test_delta_greater_than_010_is_0(self):
        assert _bpm_score(100.0, 115.0) == 0.0

    def test_delta_between_003_and_010_is_partial(self):
        # delta = 0.065
        score = _bpm_score(100.0, 106.5)
        assert 0.0 < score < 100.0


# ===========================================================================
# 3.  _camelot_distance  (autocue.analysis.transitions)
# ===========================================================================

class TestCamelotDistanceProperties:
    """Property tests for the circular Camelot-wheel distance function."""

    # -----------------------------------------------------------------------
    # Generative: symmetric
    # -----------------------------------------------------------------------

    @given(a=_camelot_num, b=_camelot_num)
    def test_symmetric(self, a, b):
        assert _camelot_distance(a, b) == _camelot_distance(b, a)

    # -----------------------------------------------------------------------
    # Generative: d(a, a) == 0
    # -----------------------------------------------------------------------

    @given(a=_camelot_num)
    def test_identity_is_zero(self, a):
        assert _camelot_distance(a, a) == 0

    # -----------------------------------------------------------------------
    # Generative: max distance ≤ 6
    # -----------------------------------------------------------------------

    @given(a=_camelot_num, b=_camelot_num)
    def test_max_distance_is_6(self, a, b):
        assert 0 <= _camelot_distance(a, b) <= 6

    # -----------------------------------------------------------------------
    # Breakpoints: wrap-around values
    # -----------------------------------------------------------------------

    def test_wrap_12_to_1_is_1(self):
        assert _camelot_distance(12, 1) == 1

    def test_wrap_11_to_1_is_2(self):
        assert _camelot_distance(11, 1) == 2

    def test_antipodal_7_to_1_is_6(self):
        assert _camelot_distance(7, 1) == 6

    def test_adjacent_clockwise(self):
        assert _camelot_distance(1, 2) == 1

    def test_adjacent_counterclockwise(self):
        assert _camelot_distance(2, 1) == 1


# ===========================================================================
# 4.  _key_score  (autocue.analysis.transitions)
# ===========================================================================

class TestKeyScoreProperties:
    """Property tests for the Camelot key compatibility scorer."""

    # -----------------------------------------------------------------------
    # Generative: valid Camelot keys → output in [0.0, 100.0]
    # -----------------------------------------------------------------------

    @given(
        num_a=_camelot_num,
        letter_a=st.sampled_from(["A", "B"]),
        num_b=_camelot_num,
        letter_b=st.sampled_from(["A", "B"]),
    )
    def test_output_in_valid_range(self, num_a, letter_a, num_b, letter_b):
        key_a = f"{num_a}{letter_a}"
        key_b = f"{num_b}{letter_b}"
        result = _key_score(key_a, key_b)
        assert 0.0 <= result <= 100.0

    # -----------------------------------------------------------------------
    # Breakpoints: identical key → 100.0
    # -----------------------------------------------------------------------

    def test_identical_key_is_100(self):
        assert _key_score("8A", "8A") == 100.0

    def test_identical_key_b_ring_is_100(self):
        assert _key_score("5B", "5B") == 100.0

    # -----------------------------------------------------------------------
    # Breakpoints: same number, different ring (parallel) → 75.0
    # -----------------------------------------------------------------------

    def test_parallel_key_is_75(self):
        assert _key_score("8A", "8B") == 75.0

    def test_parallel_key_reversed_is_75(self):
        assert _key_score("8B", "8A") == 75.0

    # -----------------------------------------------------------------------
    # Breakpoints: adjacent same ring → 80.0
    # -----------------------------------------------------------------------

    def test_adjacent_same_ring_forward_is_80(self):
        assert _key_score("8A", "9A") == 80.0

    def test_adjacent_same_ring_backward_is_80(self):
        assert _key_score("8A", "7A") == 80.0

    # -----------------------------------------------------------------------
    # Breakpoints: unknown / empty key → 50.0 (neutral)
    # -----------------------------------------------------------------------

    def test_empty_key_a_is_neutral(self):
        assert _key_score("", "8A") == 50.0

    def test_empty_key_b_is_neutral(self):
        assert _key_score("8A", "") == 50.0

    def test_both_empty_is_neutral(self):
        assert _key_score("", "") == 50.0

    def test_invalid_key_string_is_neutral(self):
        assert _key_score("X_UNKNOWN", "8A") == 50.0


# ===========================================================================
# 5.  _energy_score  (autocue.analysis.transitions)
# ===========================================================================

class TestEnergyScoreProperties:
    """Property tests for the energy-handoff scorer."""

    # -----------------------------------------------------------------------
    # Generative: output always in [0.0, 100.0]
    # -----------------------------------------------------------------------

    @given(curve_a=_energy_curve, curve_b=_energy_curve)
    def test_output_in_valid_range(self, curve_a, curve_b):
        result = _energy_score(curve_a, curve_b)
        assert 0.0 <= result <= 100.0

    @given(curve_a=_energy_curve, curve_b=_energy_curve)
    def test_output_in_valid_range_with_none(self, curve_a, curve_b):
        # Also valid when one or both curves are None
        for ca, cb in [(None, curve_b), (curve_a, None), (None, None)]:
            result = _energy_score(ca, cb)
            assert 0.0 <= result <= 100.0

    # -----------------------------------------------------------------------
    # Generative: monotone non-increasing in delta (0.05 to 0.5)
    # -----------------------------------------------------------------------

    @given(
        base=st.floats(min_value=0.0, max_value=0.45, allow_nan=False, allow_infinity=False),
        d1=st.floats(min_value=0.05, max_value=0.5, allow_nan=False, allow_infinity=False),
        d2=st.floats(min_value=0.05, max_value=0.5, allow_nan=False, allow_infinity=False),
    )
    def test_monotone_nonincreasing_in_delta(self, base, d1, d2):
        """Larger energy delta → equal-or-lower score."""
        end_a1 = base
        start_b1 = min(base + d1, 1.0)
        end_a2 = base
        start_b2 = min(base + d2, 1.0)
        # Use single-element lists to control the exact delta
        s1 = _energy_score([end_a1], [start_b1])
        s2 = _energy_score([end_a2], [start_b2])
        actual_d1 = abs(start_b1 - end_a1)
        actual_d2 = abs(start_b2 - end_a2)
        if actual_d1 <= actual_d2:
            assert s1 >= s2 - 1e-9

    # -----------------------------------------------------------------------
    # Breakpoints: delta ≤ 0.05 → 100.0
    # -----------------------------------------------------------------------

    def test_identical_curves_is_100(self):
        curve = [0.5, 0.6, 0.7, 0.6, 0.5]
        assert _energy_score(curve, curve) == 100.0

    def test_delta_at_005_is_100(self):
        # end_a = 0.5, start_b = 0.55 → delta = 0.05
        assert _energy_score([0.5], [0.55]) == 100.0

    def test_delta_below_005_is_100(self):
        assert _energy_score([0.5], [0.52]) == 100.0

    # -----------------------------------------------------------------------
    # Breakpoints: delta ≥ 0.5 → 0.0
    # -----------------------------------------------------------------------

    def test_delta_at_05_is_0(self):
        # end_a = 0.0, start_b = 0.5 → delta = 0.5
        assert _energy_score([0.0], [0.5]) == 0.0

    def test_delta_above_05_is_0(self):
        # end_a = 0.0, start_b = 1.0 → delta = 1.0
        assert _energy_score([0.0], [1.0]) == 0.0

    # -----------------------------------------------------------------------
    # Breakpoints: partial score between thresholds
    # -----------------------------------------------------------------------

    def test_delta_midpoint_is_partial(self):
        # delta = 0.275 (midpoint between 0.05 and 0.5) → should be ~50
        assert _energy_score([0.0], [0.275]) > 0.0
        assert _energy_score([0.0], [0.275]) < 100.0

    # -----------------------------------------------------------------------
    # Breakpoints: None curves treated as neutral (0.5)
    # -----------------------------------------------------------------------

    def test_none_curves_gives_100(self):
        # Both None → end_a = start_b = 0.5 → delta = 0 → 100.0
        assert _energy_score(None, None) == 100.0

    def test_none_curve_a_and_flat_05_b_is_100(self):
        # None curve_a → end_a = 0.5; curve_b = [0.5] → start_b = 0.5 → delta = 0
        assert _energy_score(None, [0.5]) == 100.0
