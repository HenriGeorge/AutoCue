"""Tests for autocue/analysis/score.py"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis.score import get_mixability, _INTRO_OUTRO_REFERENCE_BARS, _mixability_cache
from autocue.models import PhraseLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_beat_entries(n: int, ms_per_beat: float = 500.0) -> list:
    entries = []
    for i in range(n):
        e = MagicMock()
        e.time = int(i * ms_per_beat)
        entries.append(e)
    return entries


def _make_phrase_entry(kind: int, beat: int) -> MagicMock:
    e = MagicMock()
    e.kind = kind
    e.beat = beat
    return e


def _make_pssi(mood: int, phrase_kinds_beats: list[tuple[int, int]]):
    pssi = MagicMock()
    pssi.mood = mood
    pssi.entries = [_make_phrase_entry(k, b) for k, b in phrase_kinds_beats]
    return pssi


def _make_pqtz(beat_entries: list) -> MagicMock:
    pqtz = MagicMock()
    pqtz.entries = beat_entries
    return pqtz


def _make_content(track_id: int, length_s: float = 300.0) -> MagicMock:
    c = MagicMock()
    c.ID = track_id
    c.Length = length_s
    return c


def _make_db_for_mixability(pssi, pqtz):
    """Build a DB whose ANLZ calls return the given pssi/pqtz via _get_pssi_and_pqtz mock."""
    db = MagicMock()
    return db


# ---------------------------------------------------------------------------
# Core score tests (patch _get_pssi_and_pqtz directly)
# ---------------------------------------------------------------------------

MODULE = "autocue.analysis.score"


class TestGetMixabilityNoPhraseData:
    def test_returns_none_when_pssi_missing(self):
        with patch(f"{MODULE}._get_pssi_and_pqtz", return_value=(None, None)):
            with patch(f"{MODULE}.get_energy_curve", return_value=None):
                result = get_mixability(_make_content(1), MagicMock())
        assert result is None

    def test_returns_none_when_pqtz_missing(self):
        with patch(f"{MODULE}._get_pssi_and_pqtz", return_value=(MagicMock(), None)):
            with patch(f"{MODULE}.get_energy_curve", return_value=None):
                result = get_mixability(_make_content(1), MagicMock())
        assert result is None

    def test_returns_none_when_phrases_empty(self):
        pssi = MagicMock(); pssi.entries = []; pssi.mood = 1
        pqtz = _make_pqtz([])
        with patch(f"{MODULE}._get_pssi_and_pqtz", return_value=(pssi, pqtz)):
            with patch(f"{MODULE}.get_energy_curve", return_value=None):
                result = get_mixability(_make_content(1), MagicMock())
        assert result is None


class TestGetMixabilityScore:
    def _run(self, pssi, pqtz, energy_curve=None, length_s=300.0, track_id=99):
        content = _make_content(track_id, length_s=length_s)
        with patch(f"{MODULE}._get_pssi_and_pqtz", return_value=(pssi, pqtz)):
            with patch(f"{MODULE}.get_energy_curve", return_value=energy_curve):
                return get_mixability(content, MagicMock())

    def test_returns_dict_with_required_keys(self):
        beats = _make_beat_entries(200)
        # mood=1 High: kind 1=Intro, 4=Chorus, 7=Outro
        pssi = _make_pssi(1, [(1, 1), (4, 33), (7, 161)])
        pqtz = _make_pqtz(beats)
        result = self._run(pssi, pqtz)
        assert result is not None
        assert "score" in result
        assert "intro_bars" in result
        assert "outro_bars" in result
        assert "components" in result

    def test_score_in_range(self):
        beats = _make_beat_entries(200)
        pssi = _make_pssi(1, [(1, 1), (4, 33), (7, 161)])
        result = self._run(pssi, _make_pqtz(beats))
        assert 0 <= result["score"] <= 100

    def test_no_energy_uses_neutral_fallback(self):
        beats = _make_beat_entries(200)
        pssi = _make_pssi(1, [(1, 1), (4, 33), (7, 161)])
        result = self._run(pssi, _make_pqtz(beats), energy_curve=None)
        assert result["components"]["energy"] == 50

    def test_flat_energy_scores_high(self):
        beats = _make_beat_entries(200)
        pssi = _make_pssi(1, [(1, 1), (4, 33), (7, 161)])
        flat_curve = [0.5] * 50  # zero variance
        result = self._run(pssi, _make_pqtz(beats), energy_curve=flat_curve)
        assert result["components"]["energy"] == 100

    def test_vocal_proxy_true_penalises(self):
        beats = _make_beat_entries(200)
        # mood 3 = Low: kind 2 = VERSE (vocal); mood 1 = High: kind 2 = UP (no VERSE)
        # Both use INTRO(kind 1) at beat 1 and OUTRO at beat 161 (mood3→kind10, mood1→kind6)
        pssi_vocal = _make_pssi(3, [(1, 1), (2, 33), (10, 161)])  # INTRO, VERSE, OUTRO
        pssi_instr = _make_pssi(1, [(1, 1), (2, 33), (6, 161)])   # INTRO, UP, OUTRO
        r_vocal = self._run(pssi_vocal, _make_pqtz(beats), track_id=98)
        r_instr = self._run(pssi_instr, _make_pqtz(beats), track_id=97)
        assert r_vocal["score"] < r_instr["score"]
        assert r_vocal["vocal_proxy"] is True
        assert r_instr["vocal_proxy"] is False

    def test_vocal_score_floor_is_30(self):
        beats = _make_beat_entries(200)
        # mood=3 Low: kind 2 = VERSE
        pssi = _make_pssi(3, [(2, 1), (2, 33)])  # all verse
        result = self._run(pssi, _make_pqtz(beats))
        assert result["components"]["vocals"] == 30

    def test_intro_bars_computed_correctly(self):
        # 500ms/beat, 4 beats/bar = 2000ms/bar
        # Intro at beat 1, Chorus at beat 33 → 32 beats = 8 bars
        beats = _make_beat_entries(200, ms_per_beat=500.0)
        pssi = _make_pssi(1, [(1, 1), (4, 33)])
        result = self._run(pssi, _make_pqtz(beats))
        assert result["intro_bars"] == 8

    def test_outro_bars_computed_from_track_end(self):
        # 500ms/beat, 2000ms/bar, Outro at beat 161, track=300s=300000ms
        # outro start = 160*500=80000ms, end=300000ms → (300000-80000)/2000 = 110 bars
        # mood=1 High Energy: kind 6 = OUTRO
        beats = _make_beat_entries(200, ms_per_beat=500.0)
        pssi = _make_pssi(1, [(1, 1), (2, 33), (6, 161)])  # INTRO, UP, OUTRO
        result = self._run(pssi, _make_pqtz(beats), length_s=300.0)
        assert result["outro_bars"] > 0

    def test_intro_score_capped_at_reference_bars(self):
        # Intro = 32 bars = reference (32) → score = 100
        beats = _make_beat_entries(200, ms_per_beat=500.0)
        # 32 bars × 4 beats = 128 beats → Chorus starts at beat 129
        pssi = _make_pssi(1, [(1, 1), (4, 129)])
        result = self._run(pssi, _make_pqtz(beats))
        assert result["components"]["intro"] == 100

    def test_outro_length_unknown_gives_neutral_score(self):
        beats = _make_beat_entries(200)
        # mood=1 High: kind 1=Intro, kind 6=Outro
        pssi = _make_pssi(1, [(1, 1), (6, 161)])
        result = self._run(pssi, _make_pqtz(beats), length_s=0.0)
        assert result["outro_length_unknown"] is True
        assert result["components"]["outro"] == 50

    def test_outro_length_known_computes_normally(self):
        beats = _make_beat_entries(200, ms_per_beat=500.0)
        # Outro at beat 161 (ms=80000), track end=300s=300000ms → 110 bars → capped at 100
        pssi = _make_pssi(1, [(1, 1), (6, 161)])
        result = self._run(pssi, _make_pqtz(beats), length_s=300.0)
        assert result["outro_length_unknown"] is False
        assert result["outro_bars"] > 0
        assert result["components"]["outro"] == 100

    def test_cache_returns_same_result_second_call(self):
        beats = _make_beat_entries(200)
        pssi = _make_pssi(1, [(1, 1), (4, 33), (7, 161)])
        pqtz = _make_pqtz(beats)
        content = _make_content(42)
        with patch(f"{MODULE}._get_pssi_and_pqtz", return_value=(pssi, pqtz)):
            with patch(f"{MODULE}.get_energy_curve", return_value=None):
                r1 = get_mixability(content, MagicMock())
        with patch(f"{MODULE}._get_pssi_and_pqtz", return_value=(None, None)):
            with patch(f"{MODULE}.get_energy_curve", return_value=None):
                r2 = get_mixability(content, MagicMock())
        assert r2 is r1  # second call returns cached result, not None

    def test_no_outro_phrase_gives_zero_outro_bars(self):
        beats = _make_beat_entries(100)
        pssi = _make_pssi(1, [(1, 1), (4, 33)])  # no Outro
        result = self._run(pssi, _make_pqtz(beats))
        assert result["outro_bars"] == 0

    def test_phrase_count_returned(self):
        beats = _make_beat_entries(100)
        pssi = _make_pssi(1, [(1, 1), (4, 33), (4, 65), (7, 81)])
        result = self._run(pssi, _make_pqtz(beats))
        assert result["phrase_count"] == 4

    def test_six_or_more_phrases_maxes_structure_score(self):
        beats = _make_beat_entries(200)
        pssi = _make_pssi(1, [(1, 1), (2, 17), (4, 33), (5, 49), (2, 65), (7, 81)])
        result = self._run(pssi, _make_pqtz(beats))
        assert result["components"]["structure"] == 100

    def test_single_phrase_low_structure_score(self):
        beats = _make_beat_entries(100)
        pssi = _make_pssi(1, [(4, 1)])
        result = self._run(pssi, _make_pqtz(beats))
        assert result["components"]["structure"] <= 20
