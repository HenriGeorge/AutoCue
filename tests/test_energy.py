"""Tests for autocue/analysis/energy.py"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autocue.analysis import energy as energy_mod
from autocue.analysis.energy import (
    _downsample_avg,
    _read_pwav_amplitudes,
    _smooth_3,
    classify_energy_profile,
    get_energy_curve,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pwav_entries(values: list[int]):
    """Build a mock PWAV tag whose content.entries is the given list."""
    tag = MagicMock()
    tag.content.entries = values
    return tag


def _make_anlz_dat(pwav_entries: list[int] | None):
    """Build a mock DAT AnlzFile returning a PWAV tag (or None)."""
    anlz = MagicMock()
    if pwav_entries is None:
        anlz.get_tag.return_value = None
    else:
        anlz.get_tag.return_value = _make_pwav_entries(pwav_entries)
    return anlz


def _make_db(pwav_entries: list[int] | None):
    db = MagicMock()
    db.read_anlz_file.return_value = _make_anlz_dat(pwav_entries)
    return db


def _make_content(track_id: int):
    c = MagicMock()
    c.ID = track_id
    return c


# ---------------------------------------------------------------------------
# _downsample_avg
# ---------------------------------------------------------------------------

class TestDownsampleAvg:
    def test_exact_length_unchanged(self):
        vals = [0.1, 0.5, 0.9]
        assert _downsample_avg(vals, 3) == vals

    def test_shorter_than_n_unchanged(self):
        vals = [0.2, 0.8]
        assert _downsample_avg(vals, 10) == vals

    def test_downsamples_to_n(self):
        vals = list(range(100))
        result = _downsample_avg([float(v) for v in vals], 10)
        assert len(result) == 10

    def test_empty_returns_empty(self):
        assert _downsample_avg([], 10) == []

    def test_single_value(self):
        assert _downsample_avg([0.7], 1) == [0.7]

    def test_averages_are_within_range(self):
        vals = [float(i % 2) for i in range(100)]
        result = _downsample_avg(vals, 10)
        for v in result:
            assert 0.0 <= v <= 1.0


# ---------------------------------------------------------------------------
# _read_pwav_amplitudes
# ---------------------------------------------------------------------------

class TestReadPwavAmplitudes:
    def test_extracts_lower_5_bits(self):
        # byte 0xFF: lower 5 bits = 0x1F = 31
        anlz = _make_anlz_dat([0xFF, 0x00, 0x1F])
        result = _read_pwav_amplitudes(anlz)
        assert result == [31, 0, 31]

    def test_ignores_upper_3_bits(self):
        # 0xE0 = 224; lower 5 bits = 0
        anlz = _make_anlz_dat([0xE0])
        assert _read_pwav_amplitudes(anlz) == [0]

    def test_returns_none_when_tag_missing(self):
        anlz = _make_anlz_dat(None)
        assert _read_pwav_amplitudes(anlz) is None

    def test_returns_none_on_empty_entries(self):
        anlz = _make_anlz_dat([])
        assert _read_pwav_amplitudes(anlz) is None

    def test_returns_none_on_exception(self):
        anlz = MagicMock()
        anlz.get_tag.side_effect = Exception("parse error")
        assert _read_pwav_amplitudes(anlz) is None


# ---------------------------------------------------------------------------
# _smooth_3
# ---------------------------------------------------------------------------

class TestSmooth3:
    def test_length_preserved(self):
        vals = [0.1, 0.5, 0.3, 0.8, 0.2]
        assert len(_smooth_3(vals)) == len(vals)

    def test_first_and_last_unchanged(self):
        vals = [0.2, 0.5, 0.8, 0.3, 0.9]
        result = _smooth_3(vals)
        assert result[0] == vals[0]
        assert result[-1] == vals[-1]

    def test_interior_is_average_of_three(self):
        vals = [0.0, 0.6, 0.3, 0.9, 0.0]
        result = _smooth_3(vals)
        # Index 1: (0.0 + 0.6 + 0.3) / 3
        assert abs(result[1] - (0.0 + 0.6 + 0.3) / 3.0) < 1e-9
        # Index 2: (0.6 + 0.3 + 0.9) / 3
        assert abs(result[2] - (0.6 + 0.3 + 0.9) / 3.0) < 1e-9

    def test_short_list_returned_unchanged(self):
        vals = [0.1, 0.9]
        assert _smooth_3(vals) == vals

    def test_single_value_unchanged(self):
        assert _smooth_3([0.5]) == [0.5]

    def test_smoothing_reduces_variance(self):
        # Alternating 0/1 has high variance; after smoothing it should be lower
        vals = [float(i % 2) for i in range(20)]
        smoothed = _smooth_3(vals)
        mean = sum(smoothed) / len(smoothed)
        var_orig = sum((v - 0.5) ** 2 for v in vals) / len(vals)
        var_smooth = sum((v - mean) ** 2 for v in smoothed) / len(smoothed)
        assert var_smooth < var_orig


# ---------------------------------------------------------------------------
# classify_energy_profile
# ---------------------------------------------------------------------------

class TestClassifyEnergyProfile:
    def test_flat_low_variance(self):
        curve = [0.5] * 20
        assert classify_energy_profile(curve) == "flat"

    def test_flat_tiny_variance(self):
        curve = [0.5 + 0.01 * (i % 3 - 1) for i in range(20)]
        assert classify_energy_profile(curve) == "flat"

    def test_build_rising_energy(self):
        # Steadily rising energy — second half mean >> first half mean
        curve = [i / 20.0 for i in range(20)]
        assert classify_energy_profile(curve) == "build"

    def test_drop_then_flat(self):
        # Peak in first half, drops and stays low
        curve = [0.9, 0.8, 0.7, 0.6, 0.5, 0.2, 0.2, 0.2, 0.2, 0.2,
                 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2]
        result = classify_energy_profile(curve)
        assert result == "drop-then-flat"

    def test_wave_multiple_peaks(self):
        # Two clear peaks at 0.9 with 0.1 baseline — variance ≈ 0.058 > 0.05 threshold
        curve = [0.1, 0.9, 0.1, 0.1, 0.1, 0.9, 0.1, 0.1, 0.1, 0.1,
                 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
        assert classify_energy_profile(curve) == "wave"

    def test_short_curve_returns_flat(self):
        assert classify_energy_profile([0.5, 0.9]) == "flat"

    def test_empty_curve_returns_flat(self):
        assert classify_energy_profile([]) == "flat"


# ---------------------------------------------------------------------------
# get_energy_curve
# ---------------------------------------------------------------------------

class TestGetEnergyCurve:
    def setup_method(self):
        energy_mod.clear_cache()

    def test_returns_list_of_floats(self):
        # 100 entries all at max amplitude (31)
        db = _make_db([31] * 100)
        content = _make_content(999)
        result = get_energy_curve(content, db, n_points=10)
        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)

    def test_max_amplitude_normalizes_to_1(self):
        db = _make_db([31] * 100)
        content = _make_content(1)
        result = get_energy_curve(content, db, n_points=5)
        assert result is not None
        assert all(abs(v - 1.0) < 1e-6 for v in result)

    def test_zero_amplitude_normalizes_to_0(self):
        db = _make_db([0] * 100)
        content = _make_content(2)
        result = get_energy_curve(content, db, n_points=5)
        assert result is not None
        assert all(abs(v) < 1e-6 for v in result)

    def test_returns_n_points(self):
        db = _make_db([15] * 200)
        content = _make_content(3)
        result = get_energy_curve(content, db, n_points=50)
        assert result is not None
        assert len(result) == 50

    def test_returns_none_when_no_pwav(self):
        db = _make_db(None)
        content = _make_content(4)
        assert get_energy_curve(content, db) is None

    def test_returns_none_when_anlz_unavailable(self):
        db = MagicMock()
        db.read_anlz_file.return_value = None
        content = _make_content(5)
        assert get_energy_curve(content, db) is None

    def test_caches_result_avoids_second_parse(self):
        db = _make_db([20] * 100)
        content = _make_content(6)
        r1 = get_energy_curve(content, db, n_points=10)
        r2 = get_energy_curve(content, db, n_points=10)
        assert r1 == r2
        # Only one DAT file read — second call hits cache
        assert db.read_anlz_file.call_count == 1

    def test_different_n_points_not_served_from_cache(self):
        # Two calls with different n_points must each parse the file
        db = _make_db([15] * 200)
        content = _make_content(60)
        r1 = get_energy_curve(content, db, n_points=10)
        r2 = get_energy_curve(content, db, n_points=20)
        assert r1 is not None and r2 is not None
        assert len(r1) == 10
        assert len(r2) == 20
        # Both calls must have read the DAT file independently
        assert db.read_anlz_file.call_count == 2

    def test_caches_none_result(self):
        db = _make_db(None)
        content = _make_content(7)
        r1 = get_energy_curve(content, db)
        r2 = get_energy_curve(content, db)
        assert r1 is None and r2 is None
        assert db.read_anlz_file.call_count == 1

    def test_clear_cache_forces_reparse(self):
        db = _make_db([10] * 100)
        content = _make_content(8)
        get_energy_curve(content, db)
        energy_mod.clear_cache()
        get_energy_curve(content, db)
        assert db.read_anlz_file.call_count == 2

    def test_exception_in_anlz_read_returns_none(self):
        db = MagicMock()
        db.read_anlz_file.side_effect = Exception("disk error")
        content = _make_content(9)
        assert get_energy_curve(content, db) is None

    def test_smoothing_applied_to_output(self):
        # A perfectly alternating raw signal should be partially smoothed
        # Raw: [0, 31, 0, 31, ...] -> after smoothing interior values ≈ 10-21
        raw = [0, 31] * 50  # 100 entries
        db = _make_db(raw)
        content = _make_content(100)
        result = get_energy_curve(content, db, n_points=50)
        assert result is not None
        # All values should be between 0 and 1 (smoothing preserves range)
        assert all(0.0 <= v <= 1.0 for v in result)
        # Interior values should not be pure 0 or 1 (smoothing reduced peaks)
        interior = result[1:-1]
        assert not all(v == 0.0 or v == 1.0 for v in interior)
