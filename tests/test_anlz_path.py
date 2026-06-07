"""Tests for autocue.analysis.anlz_path — TASK-012 helper."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autocue.analysis.anlz_path import MISSING_MTIME, get_anlz_mtime


def test_returns_none_when_no_analysis_data_path():
    content = SimpleNamespace(AnalysisDataPath="")
    db = MagicMock()
    assert get_anlz_mtime(content, db) is None


def test_returns_none_when_analysis_data_path_is_none():
    content = SimpleNamespace(AnalysisDataPath=None)
    db = MagicMock()
    assert get_anlz_mtime(content, db) is None


def test_returns_none_when_file_does_not_exist(tmp_path):
    content = SimpleNamespace(AnalysisDataPath=str(tmp_path / "does-not-exist.DAT"))
    db = MagicMock()
    # The fake db.get_anlz_path returns a non-existent path too.
    db.get_anlz_path.return_value = str(tmp_path / "does-not-exist.DAT")
    assert get_anlz_mtime(content, db) is None


def test_returns_mtime_when_file_exists(tmp_path):
    anlz_dat = tmp_path / "track.DAT"
    anlz_dat.write_bytes(b"fake anlz")
    content = SimpleNamespace(AnalysisDataPath="track")
    db = MagicMock()
    db.get_anlz_path.side_effect = lambda c, ext: str(anlz_dat) if ext == "DAT" else ""

    got = get_anlz_mtime(content, db)
    assert got is not None
    assert got == pytest.approx(os.path.getmtime(anlz_dat), abs=0.001)


def test_falls_back_to_ext_when_dat_unavailable(tmp_path):
    anlz_ext = tmp_path / "track.EXT"
    anlz_ext.write_bytes(b"fake ext")
    content = SimpleNamespace(AnalysisDataPath="track")
    db = MagicMock()
    db.get_anlz_path.side_effect = lambda c, ext: "" if ext == "DAT" else str(anlz_ext)

    got = get_anlz_mtime(content, db)
    assert got is not None
    assert got == pytest.approx(os.path.getmtime(anlz_ext), abs=0.001)


def test_uses_analysis_data_path_when_db_helper_missing(tmp_path):
    """When db doesn't expose get_anlz_path (mocked test fixture), fall back."""
    anlz = tmp_path / "track.DAT"
    anlz.write_bytes(b"fake")
    content = SimpleNamespace(AnalysisDataPath=str(anlz))
    db = SimpleNamespace()  # no get_anlz_path attribute
    got = get_anlz_mtime(content, db)
    assert got is not None
    assert got == pytest.approx(os.path.getmtime(anlz), abs=0.001)


def test_swallows_get_anlz_path_exceptions(tmp_path):
    anlz = tmp_path / "fallback.DAT"
    anlz.write_bytes(b"x")
    content = SimpleNamespace(AnalysisDataPath=str(anlz))
    db = MagicMock()
    db.get_anlz_path.side_effect = RuntimeError("boom")
    # Falls back to AnalysisDataPath, which is the real path.
    got = get_anlz_mtime(content, db)
    assert got is not None


def test_missing_mtime_sentinel_is_negative():
    """The sentinel must be negative so it can't collide with real mtimes."""
    assert MISSING_MTIME < 0
