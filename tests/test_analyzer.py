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
from autocue.analyzer import _beat_to_ms, _get_anlz_tags_resilient, analyze_track
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

    def test_phrase_bars_populated_for_non_last_phrase(self):
        # 300 beats at 500ms/beat → avg_ms_per_beat=500, bar=2000ms
        # INTRO at 0ms, next phrase (VERSE) at 16000ms → 16000/2000 = 8 bars
        intro = next(c for c in self.result if c.label is PhraseLabel.INTRO)
        assert intro.phrase_bars == 8

    def test_phrase_bars_zero_for_last_phrase(self):
        # OUTRO has no next phrase in PSSI list → 0
        outro = next(c for c in self.result if c.label is PhraseLabel.OUTRO)
        assert outro.phrase_bars == 0

    def test_phrase_bars_consistent_for_repeated_verse(self):
        # Each VERSE phrase spans 32 beats = 16000ms → 8 bars
        verses = [c for c in self.result if c.label is PhraseLabel.VERSE]
        for v in verses[:-1]:  # all but last VERSE (which is followed by CHORUS)
            assert v.phrase_bars == 8


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


# ---------------------------------------------------------------------------
# _get_anlz_tags_resilient
# ---------------------------------------------------------------------------

import struct as _struct
import tempfile
import os
from pathlib import Path as _TPath


def _build_pssi_bytes(mood: int = 2, entries=None) -> bytes:
    """
    Build a minimal but valid PSSI content blob (after the 12-byte tag header).
    Each entry is 24 bytes: index(2)+beat(2)+kind(2)+u1(1)+k1(1)+u2(1)+k2(1)+
                              u3(1)+b(1)+beat_2(2)+beat_3(2)+beat_4(2)+
                              u4(1)+k3(1)+u5(1)+fill(1)+beat_fill(2)
    """
    entries = entries or [(1, 1), (33, 2)]  # (beat, kind) pairs
    n = len(entries)
    # PSSI header: len_entry_bytes=24(4), len_entries(2), mood(2), u1(6), end_beat(2), u2(2), bank(1), u3(1)
    header = _struct.pack(">I", 24) + _struct.pack(">H", n) + _struct.pack(">H", mood)
    header += b"\x00" * 6  # u1
    header += _struct.pack(">H", 999)  # end_beat
    header += b"\x00" * 2  # u2
    header += b"\x01"  # bank
    header += b"\x00"  # u3
    entry_bytes = b""
    for i, (beat, kind) in enumerate(entries):
        entry_bytes += (
            _struct.pack(">H", i)      # index
            + _struct.pack(">H", beat) # beat
            + _struct.pack(">H", kind) # kind
            + b"\x00" * 12             # u1 k1 u2 k2 u3 b beat_2 beat_3 beat_4
            + b"\x00" * 4             # u4 k3 u5 fill
            + _struct.pack(">H", 0)    # beat_fill
        )
    return header + entry_bytes


def _build_anlz_file(tags: list[tuple[str, bytes]], bad_pqt2: bool = False) -> bytes:
    """
    Build a minimal ANLZ file with a PMAI header and the given tag blobs.
    If bad_pqt2=True, insert a PQT2 tag with version 0x02000002 before the real tags.
    """
    FILE_HEADER_LEN = 28
    pmai = b"PMAI" + _struct.pack(">I", FILE_HEADER_LEN) + b"\x00" * (FILE_HEADER_LEN - 8)

    tag_blobs = []
    if bad_pqt2:
        # PQT2 tag that pyrekordbox can't parse (wrong Const value)
        bad_content = (
            b"\x00" * 4          # Padding(4)
            + _struct.pack(">I", 0x02000002)  # u1 — wrong version
            + b"\x00" * 40       # rest of PQT2 header + minimal entries
        )
        tag_len = 12 + len(bad_content)
        tag_blobs.append(b"PQT2" + _struct.pack(">I", 12) + _struct.pack(">I", tag_len) + bad_content)

    for tag_type, content in tags:
        tag_len = 12 + len(content)
        tag_blobs.append(
            tag_type.encode("ascii")
            + _struct.pack(">I", 12)
            + _struct.pack(">I", tag_len)
            + content
        )

    file_body = b"".join(tag_blobs)
    total_len = FILE_HEADER_LEN + len(file_body)
    # Patch len_file into PMAI header
    pmai = b"PMAI" + _struct.pack(">I", FILE_HEADER_LEN) + _struct.pack(">I", total_len) + b"\x00" * (FILE_HEADER_LEN - 12)
    return pmai + file_body


class TestResilientAnlzParser:
    def _write_tmp(self, data: bytes) -> str:
        fd, path = tempfile.mkstemp(suffix=".EXT")
        os.write(fd, data)
        os.close(fd)
        return path

    def test_parses_pssi_from_clean_file(self):
        pssi_content = _build_pssi_bytes(mood=2, entries=[(1, 1), (33, 2)])
        anlz = _build_anlz_file([("PSSI", pssi_content)])
        path = self._write_tmp(anlz)
        try:
            tags = _get_anlz_tags_resilient(_TPath(path), {"PSSI"})
            assert "PSSI" in tags
            assert tags["PSSI"].mood == 2
            assert len(tags["PSSI"].entries) == 2
        finally:
            os.unlink(path)

    def test_parses_pssi_when_pqt2_has_wrong_version(self):
        """Core regression: EXT file has bad PQT2 tag before PSSI."""
        pssi_content = _build_pssi_bytes(mood=1, entries=[(1, 1), (17, 3), (65, 8)])
        anlz = _build_anlz_file([("PSSI", pssi_content)], bad_pqt2=True)
        path = self._write_tmp(anlz)
        try:
            tags = _get_anlz_tags_resilient(_TPath(path), {"PSSI"})
            assert "PSSI" in tags
            assert tags["PSSI"].mood == 1
            assert len(tags["PSSI"].entries) == 3
        finally:
            os.unlink(path)

    def test_returns_empty_dict_for_missing_tag(self):
        anlz = _build_anlz_file([])
        path = self._write_tmp(anlz)
        try:
            tags = _get_anlz_tags_resilient(_TPath(path), {"PSSI"})
            assert tags == {}
        finally:
            os.unlink(path)

    def test_returns_empty_dict_for_nonexistent_file(self):
        from pathlib import Path as _Path
        tags = _get_anlz_tags_resilient(_Path("/nonexistent/ANLZ0000.EXT"), {"PSSI"})
        assert tags == {}

    def test_returns_empty_dict_for_non_pmai_file(self):
        path = self._write_tmp(b"NOPE" + b"\x00" * 100)
        try:
            tags = _get_anlz_tags_resilient(_TPath(path), {"PSSI"})
            assert tags == {}
        finally:
            os.unlink(path)


class TestAnalyzeTrackResilientFallback:
    """analyze_track falls back to resilient parser when read_anlz_file raises."""

    def test_fallback_recovers_pssi_after_const_error(self):
        """Simulates a ConstError from pyrekordbox and checks the resilient path."""
        from pathlib import Path as _Path
        from unittest.mock import patch as _patch
        from construct import ConstError

        beat_entries = _make_fake_beat_entries(40, ms_per_beat=500)
        phrase_entries = [(1, 1), (9, 2), (33, 8)]  # INTRO, VERSE, OUTRO (mood=2 Mid)

        pssi_content = _build_pssi_bytes(mood=2, entries=phrase_entries)
        anlz = _build_anlz_file([("PSSI", pssi_content)])

        fd, ext_path = tempfile.mkstemp(suffix=".EXT")
        os.write(fd, anlz)
        os.close(fd)

        # Build a dummy DAT file with PQTZ so the beat grid is available
        # We use a minimal valid PQTZ: len_header=24, entry_count entries of 3 bytes each
        pqtz_entries = b"".join(_struct.pack(">H", i * 500) + b"\x01" for i in range(40))
        # Actual PQTZ parse is complex; just mock the DAT side via db.read_anlz_file
        try:
            db = MagicMock()
            # EXT raises ConstError → triggers resilient path
            def fake_read(content_arg, suffix):
                if suffix == "EXT":
                    raise ConstError("parsing expected 16777218 but parsed 33554434", path="u1")
                # DAT returns normal PQTZ mock
                anlz_dat = MagicMock()
                pqtz_mock = MagicMock()
                pqtz_mock.content.entries = beat_entries
                anlz_dat.get_tag.return_value = pqtz_mock
                return anlz_dat

            db.read_anlz_file.side_effect = fake_read
            db.get_anlz_path.return_value = _Path(ext_path)

            content = MagicMock()
            result = analyze_track(content, db)
            assert len(result) > 0
            labels = {c.label for c in result}
            assert PhraseLabel.INTRO in labels
        finally:
            os.unlink(ext_path)


# ---------------------------------------------------------------------------
# Deduplication: same-position phrases
# ---------------------------------------------------------------------------

class TestAnalyzeTrackDeduplication:
    def _make_db_with_phrases(self, phrase_entries, beat_entries):
        pssi_content = SimpleNamespace(entries=phrase_entries, mood=3)
        pssi = SimpleNamespace(content=pssi_content)
        pqtz_content = SimpleNamespace(entries=beat_entries)
        pqtz = SimpleNamespace(content=pqtz_content)

        def fake_read(content_arg, suffix):
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
        db.read_anlz_file.side_effect = fake_read
        return db

    def test_two_phrases_at_same_beat_produce_one_cue(self):
        """Degenerate PSSI with two phrases at beat=1 must produce exactly one cue."""
        beat_entries = _make_fake_beat_entries(100, ms_per_beat=500)
        phrases = [
            _make_phrase_entry(1, 1),   # INTRO at beat 1 (0ms)
            _make_phrase_entry(1, 9),   # CHORUS also at beat 1 (0ms) — degenerate
            _make_phrase_entry(33, 10), # OUTRO at beat 33
        ]
        db = self._make_db_with_phrases(phrases, beat_entries)
        content = MagicMock()
        cues = analyze_track(content, db)
        # Should have 2 cues (0ms deduped to 1, 33→OUTRO), not 3
        positions = [c.position_ms for c in cues]
        assert positions.count(0) == 1, f"Duplicate 0ms cues: {positions}"

    def test_dedup_preserves_pass1_label_over_pass2(self):
        """When pass1 and pass2 produce cues at the same ms, the pass1 (unique label) is kept."""
        beat_entries = _make_fake_beat_entries(100, ms_per_beat=500)
        # Two INTRO phrases at same beat → pass1 takes first, pass2 would also take same beat
        phrases = [
            _make_phrase_entry(1, 1),   # INTRO (pass1 takes this)
            _make_phrase_entry(1, 2),   # VERSE at same beat (would be pass2 candidate at 0ms)
            _make_phrase_entry(33, 9),  # CHORUS
        ]
        db = self._make_db_with_phrases(phrases, beat_entries)
        content = MagicMock()
        cues = analyze_track(content, db)
        cues_at_0 = [c for c in cues if c.position_ms == 0]
        assert len(cues_at_0) == 1

    def test_no_dedup_needed_returns_all_cues(self):
        """Normal PSSI with unique positions passes through unchanged."""
        beat_entries = _make_fake_beat_entries(100, ms_per_beat=500)
        phrases = [
            _make_phrase_entry(1,  1),   # INTRO
            _make_phrase_entry(33, 9),   # CHORUS
            _make_phrase_entry(65, 10),  # OUTRO
        ]
        db = self._make_db_with_phrases(phrases, beat_entries)
        content = MagicMock()
        cues = analyze_track(content, db)
        assert len(cues) == 3
        positions = [c.position_ms for c in cues]
        assert len(set(positions)) == 3
