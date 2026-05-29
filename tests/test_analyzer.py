"""
Tests for autocue/analyzer.py

Adversarial notes before writing:
- _beat_to_ms is internal (underscore prefix) — import directly to avoid "always passes" mocking.
- MagicMock entry objects need explicit .time and .beat attributes set to real ints, otherwise
  arithmetic on them returns MagicMock objects instead of raising — silent wrong answers.
- Two-pass algorithm test must assert on specific labels present (INTRO, CHORUS, OUTRO),
  not just len(result) <= 8 (which passes even if result is []).
- The mock chain db.read_anlz_file → get_tag → .content.entries / .content.mood must
  be wired carefully; MagicMock auto-creates attributes which can mask missing setup.
- Slot assignment depends on sort order: verify slot=0 has the smallest position_ms.
- The VERSE-fills-remaining assertion requires enough VERSE phrases so that pass 2
  actually fires — use 7 VERSE entries so unique labels are INTRO/CHORUS/OUTRO
  (3 slots in pass 1) and 4 VERSE entries populate pass 2 up to 8 total.
- analyze_track top-level import of pyrekordbox must be mocked at module level or the
  import itself will fail if pyrekordbox can't connect to a DB at import time.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# _beat_to_ms is private but we import it directly to test its contract.
from autocue.analyzer import _beat_to_ms, analyze_track
from autocue.models import PhraseLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_beat_entry(time_ms: int) -> SimpleNamespace:
    """Return a minimal beat-grid entry with a .time attribute."""
    return SimpleNamespace(time=time_ms)


def _make_phrase_entry(beat: int, kind: int) -> SimpleNamespace:
    """Return a minimal phrase entry with .beat and .kind attributes."""
    return SimpleNamespace(beat=beat, kind=kind)


def _make_fake_beat_entries(count: int, ms_per_beat: int = 500) -> list:
    """Return `count` beat entries spaced `ms_per_beat` apart starting at 0ms."""
    return [_make_beat_entry(i * ms_per_beat) for i in range(count)]


# ---------------------------------------------------------------------------
# _beat_to_ms
# ---------------------------------------------------------------------------

class TestBeatToMs:
    def test_valid_beat_number(self):
        entries = [_make_beat_entry(0), _make_beat_entry(500), _make_beat_entry(1000)]
        assert _beat_to_ms(entries, 2) == 500

    def test_first_beat(self):
        entries = [_make_beat_entry(0), _make_beat_entry(500)]
        assert _beat_to_ms(entries, 1) == 0

    def test_last_beat(self):
        entries = [_make_beat_entry(0), _make_beat_entry(500), _make_beat_entry(1000)]
        assert _beat_to_ms(entries, 3) == 1000

    def test_beat_number_zero_returns_none(self):
        entries = [_make_beat_entry(0), _make_beat_entry(500)]
        assert _beat_to_ms(entries, 0) is None

    def test_negative_beat_number_returns_none(self):
        entries = [_make_beat_entry(0), _make_beat_entry(500)]
        assert _beat_to_ms(entries, -1) is None

    def test_beat_exceeds_length_returns_none(self):
        entries = [_make_beat_entry(0), _make_beat_entry(500)]
        assert _beat_to_ms(entries, 3) is None

    def test_empty_entries_returns_none(self):
        assert _beat_to_ms([], 1) is None

    def test_empty_entries_beat_zero_returns_none(self):
        assert _beat_to_ms([], 0) is None

    def test_single_entry_beat_1(self):
        entries = [_make_beat_entry(250)]
        assert _beat_to_ms(entries, 1) == 250

    def test_single_entry_beat_2_returns_none(self):
        entries = [_make_beat_entry(250)]
        assert _beat_to_ms(entries, 2) is None

    def test_returns_int_time_value(self):
        entries = [_make_beat_entry(333)]
        result = _beat_to_ms(entries, 1)
        assert result == 333


# ---------------------------------------------------------------------------
# analyze_track — two-pass phrase algorithm
# ---------------------------------------------------------------------------
#
# Phrase layout (mood=3, Low):
#   beat=1   kind=1  → INTRO
#   beat=33  kind=2  → VERSE   \
#   beat=65  kind=2  → VERSE    |
#   beat=97  kind=2  → VERSE    | repeating verses
#   beat=129 kind=2  → VERSE    |
#   beat=161 kind=2  → VERSE    |
#   beat=193 kind=2  → VERSE   /
#   beat=225 kind=9  → CHORUS
#   beat=257 kind=10 → OUTRO
#
# Beat grid: 300 entries at 500ms/beat → entry[N].time = N * 500
#
# Pass 1 unique labels: INTRO (beat=1), VERSE (beat=33), CHORUS (beat=225), OUTRO (beat=257) = 4 unique
# Pass 2 fills: VERSE at beat=65, beat=97, beat=129, beat=161 → 4 more → total = 8 cues

def _build_fake_analyze_inputs():
    """Return (pssi_mock, pqtz_mock) wired for the scenario above."""
    beat_entries = _make_fake_beat_entries(300, ms_per_beat=500)

    phrase_entries = [
        _make_phrase_entry(1, 1),    # INTRO
        _make_phrase_entry(33, 2),   # VERSE (first, taken by pass 1)
        _make_phrase_entry(65, 2),   # VERSE
        _make_phrase_entry(97, 2),   # VERSE
        _make_phrase_entry(129, 2),  # VERSE
        _make_phrase_entry(161, 2),  # VERSE
        _make_phrase_entry(193, 2),  # VERSE
        _make_phrase_entry(225, 9),  # CHORUS
        _make_phrase_entry(257, 10), # OUTRO
    ]

    pssi_content = SimpleNamespace(entries=phrase_entries, mood=3)
    pssi = SimpleNamespace(content=pssi_content)

    pqtz_content = SimpleNamespace(entries=beat_entries)
    pqtz = SimpleNamespace(content=pqtz_content)

    return pssi, pqtz


def _make_mock_db(pssi, pqtz):
    """Return a mock MasterDatabase whose read_anlz_file returns tagged objects."""

    def fake_read_anlz(content, suffix):
        if suffix == "EXT":
            anlz_ext = MagicMock()
            anlz_ext.get_tag.side_effect = lambda tag: pssi if tag == "PSSI" else None
            return anlz_ext
        if suffix == "DAT":
            anlz_dat = MagicMock()
            anlz_dat.get_tag.side_effect = lambda tag: pqtz if tag == "PQTZ" else None
            return anlz_dat
        return None

    db = MagicMock()
    db.read_anlz_file.side_effect = fake_read_anlz
    return db


class TestAnalyzeTrackTwoPass:
    def setup_method(self):
        self.pssi, self.pqtz = _build_fake_analyze_inputs()
        self.db = _make_mock_db(self.pssi, self.pqtz)
        self.content = MagicMock()
        self.result = analyze_track(self.content, self.db)

    def test_at_most_8_cues(self):
        assert len(self.result) <= 8

    def test_exactly_8_cues(self):
        # 4 unique labels + 4 VERSE repeats = exactly 8
        assert len(self.result) == 8

    def test_intro_present(self):
        labels = [c.label for c in self.result]
        assert PhraseLabel.INTRO in labels

    def test_chorus_present(self):
        labels = [c.label for c in self.result]
        assert PhraseLabel.CHORUS in labels

    def test_outro_present(self):
        labels = [c.label for c in self.result]
        assert PhraseLabel.OUTRO in labels

    def test_verse_fills_remaining_slots(self):
        verse_cues = [c for c in self.result if c.label is PhraseLabel.VERSE]
        assert len(verse_cues) >= 1  # at minimum the first unique VERSE
        # With 6 VERSE phrases and 4 remaining slots after unique labels, expect 4 total verse cues
        # (1 from pass 1 + 4 from pass 2 = 5, but we cap at 8 and have 3 other unique = 5 verse)
        assert len(verse_cues) == 5  # 1 unique + 4 pass-2 fills

    def test_sorted_by_position_ms_ascending(self):
        positions = [c.position_ms for c in self.result]
        assert positions == sorted(positions)

    def test_slots_are_zero_indexed(self):
        slots = [c.slot for c in self.result]
        assert slots == list(range(len(self.result)))

    def test_first_slot_is_intro(self):
        # INTRO is at beat=1 (time=0ms), so it should be the first cue
        assert self.result[0].label is PhraseLabel.INTRO
        assert self.result[0].slot == 0

    def test_last_slot_is_outro(self):
        # OUTRO is at beat=257 (time=257*500=128000ms) — last chronologically
        assert self.result[-1].label is PhraseLabel.OUTRO
        assert self.result[-1].slot == 7

    def test_cue_positions_match_beat_times(self):
        # INTRO at beat=1 → entry index 0 → time=0ms
        intro = next(c for c in self.result if c.label is PhraseLabel.INTRO)
        assert intro.position_ms == 0

        # CHORUS at beat=225 → entry index 224 → time=224*500=112000ms
        chorus = next(c for c in self.result if c.label is PhraseLabel.CHORUS)
        assert chorus.position_ms == 224 * 500

    def test_returns_list_of_cuepoints(self):
        from autocue.models import CuePoint
        assert all(isinstance(c, CuePoint) for c in self.result)


class TestAnalyzeTrackMissingData:
    def test_returns_empty_list_when_anlz_ext_is_none(self):
        db = MagicMock()
        db.read_anlz_file.return_value = None
        content = MagicMock()
        result = analyze_track(content, db)
        assert result == []

    def test_returns_empty_list_when_pssi_missing(self):
        db = MagicMock()
        anlz_ext = MagicMock()
        anlz_ext.get_tag.return_value = None  # PSSI not found
        anlz_dat = MagicMock()

        def fake_read(content, suffix):
            return anlz_ext if suffix == "EXT" else anlz_dat

        db.read_anlz_file.side_effect = fake_read
        content = MagicMock()
        result = analyze_track(content, db)
        assert result == []

    def test_returns_empty_list_when_pqtz_missing(self):
        pssi = MagicMock()
        db = MagicMock()

        def fake_read(content, suffix):
            if suffix == "EXT":
                anlz_ext = MagicMock()
                anlz_ext.get_tag.side_effect = lambda tag: pssi if tag == "PSSI" else None
                return anlz_ext
            if suffix == "DAT":
                anlz_dat = MagicMock()
                anlz_dat.get_tag.return_value = None  # PQTZ not found
                return anlz_dat
            return None

        db.read_anlz_file.side_effect = fake_read
        content = MagicMock()
        result = analyze_track(content, db)
        assert result == []


class TestAnalyzeTrackSmallInput:
    """Edge case: only one phrase entry — should yield exactly one cue."""

    def test_single_phrase_yields_one_cue(self):
        beat_entries = _make_fake_beat_entries(10, ms_per_beat=1000)
        phrase_entries = [_make_phrase_entry(1, 1)]  # INTRO only

        pssi = SimpleNamespace(
            content=SimpleNamespace(entries=phrase_entries, mood=3)
        )
        pqtz = SimpleNamespace(content=SimpleNamespace(entries=beat_entries))
        db = _make_mock_db(pssi, pqtz)
        content = MagicMock()

        result = analyze_track(content, db)
        assert len(result) == 1
        assert result[0].label is PhraseLabel.INTRO
        assert result[0].slot == 0
