"""Tests for autocue/generator.py"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autocue.generator import (
    GenerationPrefs,
    TrackCapability,
    _bar_strategy,
    _heuristic_strategy,
    detect_capability,
    generate_cues_for_track,
)
from autocue.models import PhraseLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content(bpm=None, length=None):
    ns = SimpleNamespace()
    if bpm is not None:
        ns.BPM = bpm
    # else: no BPM attribute at all
    if length is not None:
        ns.Length = length
    return ns


def _make_cue(pos=0, slot=0):
    from autocue.models import CuePoint
    return CuePoint(position_ms=pos, label=PhraseLabel.UNKNOWN, slot=slot)


def _make_fake_cues():
    """Return fresh CuePoint objects each call — avoids in-place slot mutation across tests."""
    return [_make_cue(0, 0), _make_cue(30_000, 1)]


FAKE_CUES = _make_fake_cues()  # kept for tests that do NOT exercise smart ordering


# ---------------------------------------------------------------------------
# detect_capability
# ---------------------------------------------------------------------------

class TestDetectCapability:
    def test_has_phrase_true_when_analyze_returns_cues(self):
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=FAKE_CUES):
            cap = detect_capability(content, db)
        assert cap.has_phrase is True

    def test_has_phrase_false_when_analyze_returns_empty(self):
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=[]):
            cap = detect_capability(content, db)
        assert cap.has_phrase is False

    def test_has_beats_true_when_bpm_set(self):
        content = _content(bpm=12800, length=300)  # 12800 = 128.00 BPM (DB stores int×100)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=[]):
            cap = detect_capability(content, db)
        assert cap.has_beats is True
        assert cap.bpm == pytest.approx(128.0)

    def test_has_beats_false_when_bpm_missing(self):
        content = _content(length=300)  # no BPM
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=[]):
            cap = detect_capability(content, db)
        assert cap.has_beats is False
        assert cap.bpm is None

    def test_duration_ms_computed(self):
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=[]):
            cap = detect_capability(content, db)
        assert cap.duration_ms == 300_000

    def test_duration_ms_none_when_no_length(self):
        content = _content(bpm=12800)  # no Length
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=[]):
            cap = detect_capability(content, db)
        assert cap.duration_ms is None

    def test_phrase_cues_kwarg_skips_analyze_call(self):
        """Passing phrase_cues= must not trigger analyze_track."""
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track") as mock_analyze:
            cap = detect_capability(content, db, phrase_cues=FAKE_CUES)
        mock_analyze.assert_not_called()
        assert cap.has_phrase is True

    def test_phrase_cues_empty_kwarg_skips_analyze_call(self):
        """Passing phrase_cues=[] must not trigger analyze_track."""
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track") as mock_analyze:
            cap = detect_capability(content, db, phrase_cues=[])
        mock_analyze.assert_not_called()
        assert cap.has_phrase is False


# ---------------------------------------------------------------------------
# _bar_strategy
# ---------------------------------------------------------------------------

class TestBarStrategy:
    def test_positions_at_128bpm_16bar_intervals(self):
        # 128 BPM → bar_ms = (60000/128)*4 = 1875 ms
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", bars_interval=16, start_bar=1, max_cues=4)
        cues, mode = _bar_strategy(content, db, prefs)
        assert mode == "bar"
        bar_ms = int((60_000.0 / 128.0) * 4)  # 1875
        expected = [0, 16 * bar_ms, 32 * bar_ms, 48 * bar_ms]
        assert [c.position_ms for c in cues] == expected

    def test_slots_are_sequential(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", bars_interval=16, start_bar=1, max_cues=4)
        cues, _ = _bar_strategy(content, db, prefs)
        assert [c.slot for c in cues] == [0, 1, 2, 3]

    def test_negative_position_skipped_with_negative_inizio(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        # start_bar=1, inizio_ms=-5000 → first cue pos = -5000 → skipped
        prefs = GenerationPrefs(mode="bar", bars_interval=16, start_bar=1,
                                max_cues=4, inizio_ms=-5000)
        cues, _ = _bar_strategy(content, db, prefs)
        for c in cues:
            assert c.position_ms >= 0

    def test_stops_at_duration(self):
        content = _content(bpm=12800, length=30)  # 30 s = 30000 ms
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", bars_interval=16, start_bar=1, max_cues=8)
        cues, _ = _bar_strategy(content, db, prefs)
        for c in cues:
            assert c.position_ms < 30_000

    def test_max_cues_respected(self):
        content = _content(bpm=12800, length=3600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", bars_interval=1, start_bar=1, max_cues=3)
        cues, _ = _bar_strategy(content, db, prefs)
        assert len(cues) <= 3

    def test_inizio_offset_applied(self):
        content = _content(bpm=12000, length=600)
        db = MagicMock()
        bar_ms = int((60_000.0 / 120.0) * 4)  # 2000
        prefs = GenerationPrefs(mode="bar", bars_interval=1, start_bar=1,
                                max_cues=2, inizio_ms=500)
        cues, _ = _bar_strategy(content, db, prefs)
        assert cues[0].position_ms == 500
        assert cues[1].position_ms == 500 + bar_ms

    def test_labels_are_unknown(self):
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        cues, _ = _bar_strategy(content, db, GenerationPrefs())
        assert all(c.label == PhraseLabel.UNKNOWN for c in cues)

    def test_first_cue_name_bar_1(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", bars_interval=16, start_bar=1, max_cues=4)
        cues, _ = _bar_strategy(content, db, prefs)
        assert cues[0].name == "Bar 1"

    def test_second_cue_name_bar_17(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", bars_interval=16, start_bar=1, max_cues=4)
        cues, _ = _bar_strategy(content, db, prefs)
        assert cues[1].name == "Bar 17"

    def test_start_bar_offset_in_name(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", bars_interval=16, start_bar=5, max_cues=2)
        cues, _ = _bar_strategy(content, db, prefs)
        assert cues[0].name == "Bar 5"
        assert cues[1].name == "Bar 21"

    def test_negative_inizio_produces_no_slot_gaps(self):
        """When inizio_ms is negative, skipped iterations must not create slot gaps."""
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        # bar_ms = 1875; inizio=-5000 → first few bars at negative pos, skip them
        prefs = GenerationPrefs(mode="bar", bars_interval=1, start_bar=1,
                                max_cues=4, inizio_ms=-5000)
        cues, _ = _bar_strategy(content, db, prefs)
        assert len(cues) > 0
        for idx, c in enumerate(cues):
            assert c.slot == idx, f"Slot gap: cue[{idx}] has slot={c.slot}"
            assert c.position_ms >= 0

    def test_bar_names_preserved_with_negative_inizio(self):
        """Bar names should reflect the actual bar number even with negative inizio."""
        content = _content(bpm=12000, length=600)  # 120 BPM → bar_ms=2000
        db = MagicMock()
        # inizio=-1000; bar 1 pos = -1000 (skipped), bar 2 pos = 1000 (first kept)
        prefs = GenerationPrefs(mode="bar", bars_interval=1, start_bar=1,
                                max_cues=2, inizio_ms=-1000)
        cues, _ = _bar_strategy(content, db, prefs)
        assert len(cues) == 2
        # The name must say "Bar 2" (bar index 1, first non-negative)
        assert cues[0].name.startswith("Bar ")
        bar_num = int(cues[0].name.split()[1])
        assert bar_num >= 2  # bar 1 was negative, so first valid is bar 2 or later


# ---------------------------------------------------------------------------
# _heuristic_strategy
# ---------------------------------------------------------------------------

class TestHeuristicStrategy:
    def test_every_30s(self):
        content = _content(length=300)  # 300 s = 5 min
        db = MagicMock()
        cues, mode = _heuristic_strategy(content, db, GenerationPrefs())
        assert mode == "heuristic"
        positions = [c.position_ms for c in cues]
        assert positions == [0, 30_000, 60_000, 90_000, 120_000, 150_000, 180_000, 210_000]

    def test_stops_before_duration(self):
        content = _content(length=100)  # 100 s
        db = MagicMock()
        cues, _ = _heuristic_strategy(content, db, GenerationPrefs())
        for c in cues:
            assert c.position_ms < 100_000

    def test_max_cues_respected(self):
        content = _content(length=3600)
        db = MagicMock()
        cues, _ = _heuristic_strategy(content, db, GenerationPrefs(max_cues=3))
        assert len(cues) == 3

    def test_labels_are_unknown(self):
        content = _content(length=300)
        db = MagicMock()
        cues, _ = _heuristic_strategy(content, db, GenerationPrefs())
        assert all(c.label == PhraseLabel.UNKNOWN for c in cues)

    def test_short_track_fewer_cues(self):
        content = _content(length=50)  # 50 s → cues at 0 and 30s only
        db = MagicMock()
        cues, _ = _heuristic_strategy(content, db, GenerationPrefs())
        assert len(cues) == 2

    def test_first_cue_name_0_00(self):
        content = _content(length=300)
        db = MagicMock()
        cues, _ = _heuristic_strategy(content, db, GenerationPrefs())
        assert cues[0].name == "0:00"

    def test_second_cue_name_0_30(self):
        content = _content(length=300)
        db = MagicMock()
        cues, _ = _heuristic_strategy(content, db, GenerationPrefs())
        assert cues[1].name == "0:30"

    def test_minute_boundary_1_00(self):
        content = _content(length=300)
        db = MagicMock()
        cues, _ = _heuristic_strategy(content, db, GenerationPrefs())
        assert cues[2].name == "1:00"

    def test_zero_padding_1_30(self):
        content = _content(length=300)
        db = MagicMock()
        cues, _ = _heuristic_strategy(content, db, GenerationPrefs())
        assert cues[3].name == "1:30"


# ---------------------------------------------------------------------------
# generate_cues_for_track — routing logic
# ---------------------------------------------------------------------------

class TestGenerateCuesForTrack:
    def test_mode_phrase_returns_phrase_cues(self):
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase")
        with patch("autocue.generator.analyze_track", return_value=FAKE_CUES):
            cues, mode = generate_cues_for_track(content, db, prefs)
        assert mode == "phrase"
        assert cues == FAKE_CUES

    def test_mode_phrase_returns_empty_when_no_anlz(self):
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase")
        with patch("autocue.generator.analyze_track", return_value=[]):
            cues, mode = generate_cues_for_track(content, db, prefs)
        assert mode == "phrase"
        assert cues == []

    def test_mode_bar_uses_bar_strategy(self):
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar")
        cues, mode = generate_cues_for_track(content, db, prefs)
        assert mode == "bar"
        assert len(cues) > 0

    def test_mode_bar_returns_empty_without_bpm(self):
        content = _content(length=300)  # no BPM
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar")
        # No BPM → explicit ([], "bar") — must not fall through to heuristic
        cues, mode = generate_cues_for_track(content, db, prefs)
        assert cues == []
        assert mode == "bar"

    def test_auto_uses_phrase_when_available(self):
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="auto")
        with patch("autocue.generator.analyze_track", return_value=FAKE_CUES):
            cues, mode = generate_cues_for_track(content, db, prefs)
        assert mode == "phrase"

    def test_auto_falls_to_bar_when_no_phrase(self):
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="auto")
        with patch("autocue.generator.analyze_track", return_value=[]):
            cues, mode = generate_cues_for_track(content, db, prefs)
        assert mode == "bar"
        assert len(cues) > 0

    def test_auto_falls_to_heuristic_when_no_phrase_no_bpm(self):
        content = _content(length=300)  # no BPM
        db = MagicMock()
        prefs = GenerationPrefs(mode="auto")
        with patch("autocue.generator.analyze_track", return_value=[]):
            cues, mode = generate_cues_for_track(content, db, prefs)
        assert mode == "heuristic"
        assert len(cues) > 0

    def test_default_prefs_used_when_none(self):
        content = _content(length=300)  # no BPM, triggers heuristic
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=[]):
            cues, mode = generate_cues_for_track(content, db, None)
        assert mode == "heuristic"

    def test_mode_bar_zero_bpm_string_returns_empty(self):
        """BPM="0.0" is truthy as a string but must not reach division by zero."""
        content = _content(bpm="0.0", length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar")
        cues, mode = generate_cues_for_track(content, db, prefs)
        assert cues == []
        assert mode == "bar"

    def test_auto_zero_bpm_string_falls_to_heuristic(self):
        """BPM="0.0" in auto mode must fall through to heuristic, not crash."""
        content = _content(bpm="0.0", length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="auto")
        with patch("autocue.generator.analyze_track", return_value=[]):
            cues, mode = generate_cues_for_track(content, db, prefs)
        assert mode == "heuristic"
        assert len(cues) > 0

    def test_phrase_cues_have_correct_labels(self):
        from autocue.models import CuePoint
        phrase_cues = [
            CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0),
            CuePoint(position_ms=30_000, label=PhraseLabel.CHORUS, slot=1),
        ]
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, mode = generate_cues_for_track(content, db, GenerationPrefs(mode="phrase"))
        labels = {c.label for c in cues}
        assert PhraseLabel.INTRO in labels
        assert PhraseLabel.CHORUS in labels

    def test_phrase_cues_sequential_order_preserved(self):
        """sequential slot_priority keeps chronological order."""
        from autocue.models import CuePoint
        phrase_cues = [
            CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0),
            CuePoint(position_ms=30_000, label=PhraseLabel.CHORUS, slot=1),
        ]
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", slot_priority="sequential")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, mode = generate_cues_for_track(content, db, prefs)
        assert cues[0].label == PhraseLabel.INTRO
        assert cues[1].label == PhraseLabel.CHORUS


# ---------------------------------------------------------------------------
# Memory cue (add_memory_cue=True)
# ---------------------------------------------------------------------------

class TestMemoryCue:
    def test_prepends_slot_minus1_in_bar_mode(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", add_memory_cue=True)
        cues, _ = generate_cues_for_track(content, db, prefs)
        assert cues[0].slot == -1
        assert cues[0].name == "Load Point"

    def test_memory_cue_position_is_inizio_for_bar_mode(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", add_memory_cue=True, inizio_ms=500)
        cues, _ = generate_cues_for_track(content, db, prefs)
        assert cues[0].slot == -1
        assert cues[0].position_ms == 500

    def test_memory_cue_clamps_negative_inizio_to_zero(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", add_memory_cue=True, inizio_ms=-5000)
        cues, _ = generate_cues_for_track(content, db, prefs)
        assert cues[0].slot == -1
        assert cues[0].position_ms == 0

    def test_memory_cue_anchors_to_first_phrase_start(self):
        from autocue.models import CuePoint
        phrase_cues = [
            CuePoint(position_ms=8000, label=PhraseLabel.INTRO, slot=0),
            CuePoint(position_ms=32000, label=PhraseLabel.CHORUS, slot=1),
        ]
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", add_memory_cue=True)
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, mode = generate_cues_for_track(content, db, prefs)
        assert cues[0].slot == -1
        assert cues[0].position_ms == 8000  # anchored to first phrase
        assert mode == "phrase"

    def test_no_memory_cue_when_add_memory_cue_false(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        cues, _ = generate_cues_for_track(content, db, GenerationPrefs(mode="bar"))
        assert all(c.slot >= 0 for c in cues)

    def test_no_memory_cue_when_phrase_mode_no_anlz(self):
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", add_memory_cue=True)
        with patch("autocue.generator.analyze_track", return_value=[]):
            cues, mode = generate_cues_for_track(content, db, prefs)
        assert cues == []
        assert mode == "phrase"

    def test_hot_cue_slots_unaffected_by_memory_cue(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", bars_interval=16, max_cues=4, add_memory_cue=True)
        cues, _ = generate_cues_for_track(content, db, prefs)
        hot_cues = [c for c in cues if c.slot >= 0]
        assert [c.slot for c in hot_cues] == [0, 1, 2, 3]

    def test_memory_cue_anchors_to_min_position_with_smart_order(self):
        """Smart ordering reorders slots but memory cue must still be at earliest phrase."""
        from autocue.models import CuePoint
        phrase_cues = [
            CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0),
            CuePoint(position_ms=32_000, label=PhraseLabel.CHORUS, slot=1),
        ]
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", add_memory_cue=True, slot_priority="smart")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        mem = next(c for c in cues if c.slot == -1)
        assert mem.position_ms == 0  # earliest phrase (Intro), not the smart slot-0 (Chorus)


# ---------------------------------------------------------------------------
# memory_cue_mode ("load_only" / "all") tests
# ---------------------------------------------------------------------------

class TestMemoryCueMode:
    def _phrase_cues_with_outro(self):
        from autocue.models import CuePoint
        return [
            CuePoint(position_ms=0,      label=PhraseLabel.INTRO,  slot=0),
            CuePoint(position_ms=32_000, label=PhraseLabel.CHORUS, slot=1),
            CuePoint(position_ms=96_000, label=PhraseLabel.OUTRO,  slot=2),
        ]

    def test_load_only_mode_generates_one_memory_cue(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", memory_cue_mode="load_only")
        cues, _ = generate_cues_for_track(content, db, prefs)
        mem = [c for c in cues if c.slot == -1]
        assert len(mem) == 1
        assert mem[0].name == "Load Point"

    def test_add_memory_cue_bool_is_load_only_alias(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs_bool = GenerationPrefs(mode="bar", add_memory_cue=True)
        prefs_mode = GenerationPrefs(mode="bar", memory_cue_mode="load_only")
        cues_bool, _ = generate_cues_for_track(content, db, prefs_bool)
        cues_mode, _ = generate_cues_for_track(content, db, prefs_mode)
        mem_bool = [c for c in cues_bool if c.slot == -1]
        mem_mode = [c for c in cues_mode if c.slot == -1]
        assert len(mem_bool) == len(mem_mode) == 1
        assert mem_bool[0].name == mem_mode[0].name == "Load Point"

    def test_none_mode_no_memory_cues(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", memory_cue_mode="none", add_memory_cue=False)
        cues, _ = generate_cues_for_track(content, db, prefs)
        assert all(c.slot >= 0 for c in cues)

    def test_all_mode_phrase_generates_load_and_mixout(self):
        phrase_cues = self._phrase_cues_with_outro()
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", memory_cue_mode="all")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        mem = [c for c in cues if c.slot == -1]
        names = {c.name for c in mem}
        assert "Load Point" in names
        assert "Mix Out" in names

    def test_all_mode_load_point_color_is_zero(self):
        phrase_cues = self._phrase_cues_with_outro()
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", memory_cue_mode="all")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        load = next(c for c in cues if c.slot == -1 and c.name == "Load Point")
        assert load.color_id == 0

    def test_all_mode_mix_out_color_is_orange(self):
        phrase_cues = self._phrase_cues_with_outro()
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", memory_cue_mode="all")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        mix_out = next(c for c in cues if c.slot == -1 and c.name == "Mix Out")
        assert mix_out.color_id == 3  # Orange

    def test_all_mode_warning_added_when_outro_short(self):
        from autocue.models import CuePoint
        # Outro at 280s, track ends at 290s → 10s outro. At 128BPM: bar_ms=1875ms → outro_bars≈5 (<8)
        phrase_cues = [
            CuePoint(position_ms=0,       label=PhraseLabel.INTRO,  slot=0),
            CuePoint(position_ms=32_000,  label=PhraseLabel.CHORUS, slot=1),
            CuePoint(position_ms=280_000, label=PhraseLabel.OUTRO,  slot=2),
        ]
        content = _content(bpm=12800, length=290)  # BPM 128.0, 290s track
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", memory_cue_mode="all")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        mem = [c for c in cues if c.slot == -1]
        names = {c.name for c in mem}
        assert "Warning" in names
        warning = next(c for c in mem if c.name == "Warning")
        assert warning.color_id == 2  # Red

    def test_all_mode_no_warning_when_outro_long(self):
        from autocue.models import CuePoint
        # Outro at 100s, track ends at 300s → 200s outro. At 128BPM ≈ 100+ bars (>>8)
        phrase_cues = [
            CuePoint(position_ms=0,       label=PhraseLabel.INTRO,  slot=0),
            CuePoint(position_ms=32_000,  label=PhraseLabel.CHORUS, slot=1),
            CuePoint(position_ms=100_000, label=PhraseLabel.OUTRO,  slot=2),
        ]
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", memory_cue_mode="all")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        mem = [c for c in cues if c.slot == -1]
        names = {c.name for c in mem}
        assert "Warning" not in names

    def test_all_mode_no_mix_out_without_outro_phrase(self):
        from autocue.models import CuePoint
        phrase_cues = [
            CuePoint(position_ms=0,      label=PhraseLabel.INTRO,  slot=0),
            CuePoint(position_ms=32_000, label=PhraseLabel.CHORUS, slot=1),
        ]
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", memory_cue_mode="all")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        mem = [c for c in cues if c.slot == -1]
        names = {c.name for c in mem}
        assert "Mix Out" not in names
        assert "Warning" in names  # no outro → warning added

    def test_all_mode_bar_mode_only_load_point(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", memory_cue_mode="all")
        cues, _ = generate_cues_for_track(content, db, prefs)
        mem = [c for c in cues if c.slot == -1]
        assert len(mem) == 1
        assert mem[0].name == "Load Point"

    def test_memory_cues_sorted_by_position_before_hot_cues(self):
        from autocue.models import CuePoint
        phrase_cues = [
            CuePoint(position_ms=0,       label=PhraseLabel.INTRO,  slot=0),
            CuePoint(position_ms=32_000,  label=PhraseLabel.CHORUS, slot=1),
            CuePoint(position_ms=280_000, label=PhraseLabel.OUTRO,  slot=2),
        ]
        content = _content(bpm=12800, length=290)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", memory_cue_mode="all")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        mem = [c for c in cues if c.slot == -1]
        assert mem == sorted(mem, key=lambda c: c.position_ms)

    def test_warning_skipped_when_bpm_zero(self):
        from autocue.models import CuePoint
        phrase_cues = [
            CuePoint(position_ms=0,      label=PhraseLabel.INTRO,  slot=0),
            CuePoint(position_ms=32_000, label=PhraseLabel.CHORUS, slot=1),
        ]
        content = _content(bpm=0, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", memory_cue_mode="all")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        mem = [c for c in cues if c.slot == -1]
        assert all(c.name != "Warning" for c in mem)


# ---------------------------------------------------------------------------
# Smart slot ordering
# ---------------------------------------------------------------------------

class TestSmartSlotOrder:
    def _phrase_cues(self, labels_ms):
        from autocue.models import CuePoint
        return [
            CuePoint(position_ms=ms, label=lbl, slot=i)
            for i, (lbl, ms) in enumerate(labels_ms)
        ]

    def test_first_non_intro_is_mix_in_slot_a(self):
        """Slot A = first non-Intro phrase (mix-in point), regardless of type."""
        phrase_cues = self._phrase_cues([
            (PhraseLabel.INTRO,  0),
            (PhraseLabel.CHORUS, 32_000),
            (PhraseLabel.OUTRO,  96_000),
        ])
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, GenerationPrefs(mode="phrase"))
        by_slot = {c.slot: c for c in cues}
        # CHORUS is first non-Intro → gets slot A as mix-in (not because it's a Drop)
        assert by_slot[0].label == PhraseLabel.CHORUS

    def test_verse_before_chorus_verse_gets_slot_a(self):
        """When VERSE precedes CHORUS, VERSE is the mix-in (A), CHORUS is slot B."""
        phrase_cues = self._phrase_cues([
            (PhraseLabel.INTRO,  0),
            (PhraseLabel.VERSE,  8_000),   # first non-Intro → mix-in
            (PhraseLabel.CHORUS, 32_000),  # most important but not first
        ])
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, GenerationPrefs(mode="phrase"))
        by_slot = {c.slot: c for c in cues}
        assert by_slot[0].label == PhraseLabel.VERSE    # mix-in → A
        assert by_slot[1].label == PhraseLabel.CHORUS   # Drop → B
        assert by_slot[2].label == PhraseLabel.INTRO    # Intro → last

    def test_intro_gets_last_slot(self):
        phrase_cues = self._phrase_cues([
            (PhraseLabel.INTRO,  0),
            (PhraseLabel.UP,     16_000),
            (PhraseLabel.CHORUS, 32_000),
        ])
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, GenerationPrefs(mode="phrase"))
        by_slot = {c.slot: c for c in cues}
        assert by_slot[2].label == PhraseLabel.INTRO

    def test_two_choruses_in_slot_order(self):
        """Multiple Chorus phrases get consecutive low slots, chronologically ordered."""
        phrase_cues = self._phrase_cues([
            (PhraseLabel.CHORUS, 32_000),
            (PhraseLabel.CHORUS, 80_000),
            (PhraseLabel.OUTRO,  120_000),
        ])
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, GenerationPrefs(mode="phrase"))
        by_slot = {c.slot: c for c in cues}
        assert by_slot[0].position_ms == 32_000  # first Chorus
        assert by_slot[1].position_ms == 80_000  # second Chorus
        assert by_slot[2].label == PhraseLabel.OUTRO

    def test_sequential_mode_preserves_chronological_order(self):
        phrase_cues = self._phrase_cues([
            (PhraseLabel.INTRO,  0),
            (PhraseLabel.CHORUS, 32_000),
            (PhraseLabel.OUTRO,  96_000),
        ])
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", slot_priority="sequential")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        # Chronological: Intro=slot0, Chorus=slot1, Outro=slot2
        by_slot = {c.slot: c for c in cues}
        assert by_slot[0].label == PhraseLabel.INTRO
        assert by_slot[1].label == PhraseLabel.CHORUS

    def test_smart_ordering_not_applied_to_bar_cues(self):
        """Bar cues always use sequential (chronological) slot order."""
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", bars_interval=16, max_cues=4)
        cues, mode = generate_cues_for_track(content, db, prefs)
        assert mode == "bar"
        assert [c.slot for c in cues] == [0, 1, 2, 3]

    def test_smart_list_order_unchanged(self):
        """Smart ordering changes slot numbers but NOT the list order (stays chronological)."""
        phrase_cues = self._phrase_cues([
            (PhraseLabel.INTRO,  0),
            (PhraseLabel.CHORUS, 32_000),
        ])
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, GenerationPrefs(mode="phrase"))
        # List still in position order (Intro first, Chorus second)
        assert cues[0].position_ms == 0        # Intro at index 0
        assert cues[1].position_ms == 32_000   # Chorus at index 1
        # Chorus is first non-Intro → mix-in = slot A; Intro pushed to slot B
        assert cues[0].slot == 1  # Intro → slot 1
        assert cues[1].slot == 0  # Chorus → slot 0 (mix-in)


# ---------------------------------------------------------------------------
# Confidence values
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_phrase_cues_have_confidence_1(self):
        from autocue.models import CuePoint
        phrase_cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, GenerationPrefs(mode="phrase"))
        assert all(c.confidence == 1.0 for c in cues if c.slot >= 0)

    def test_bar_cues_have_confidence_0_6(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        cues, mode = generate_cues_for_track(content, db, GenerationPrefs(mode="bar"))
        assert mode == "bar"
        assert all(c.confidence == pytest.approx(0.6) for c in cues)

    def test_heuristic_cues_have_confidence_0_3(self):
        content = _content(length=300)  # no BPM → heuristic
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=[]):
            cues, mode = generate_cues_for_track(content, db, GenerationPrefs(mode="auto"))
        assert mode == "heuristic"
        assert all(c.confidence == pytest.approx(0.3) for c in cues)

    def test_memory_cue_bar_mode_confidence_0_6(self):
        content = _content(bpm=12800, length=600)
        db = MagicMock()
        prefs = GenerationPrefs(mode="bar", add_memory_cue=True)
        cues, _ = generate_cues_for_track(content, db, prefs)
        mem = next(c for c in cues if c.slot == -1)
        assert mem.confidence == pytest.approx(0.6)

    def test_memory_cue_heuristic_mode_confidence_0_3(self):
        content = _content(length=300)  # no BPM → heuristic
        db = MagicMock()
        prefs = GenerationPrefs(mode="auto", add_memory_cue=True)
        with patch("autocue.generator.analyze_track", return_value=[]):
            cues, mode = generate_cues_for_track(content, db, prefs)
        assert mode == "heuristic"
        mem = next(c for c in cues if c.slot == -1)
        assert mem.confidence == pytest.approx(0.3)

    def test_memory_cue_phrase_mode_confidence_1_0(self):
        from autocue.models import CuePoint
        phrase_cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", add_memory_cue=True)
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        mem = next(c for c in cues if c.slot == -1)
        assert mem.confidence == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Slot A "Mix In" naming
# ---------------------------------------------------------------------------

class TestSlotAMixInNaming:
    def _phrase_cues_named(self, labels_names_ms):
        from autocue.models import CuePoint
        return [
            CuePoint(position_ms=ms, label=lbl, slot=i, name=name)
            for i, (lbl, name, ms) in enumerate(labels_names_ms)
        ]

    def test_non_intro_slot_a_with_name_gets_mix_in_suffix(self):
        """Slot A that is a Chorus (DJ name 'Drop') gets '(Mix In)' appended."""
        phrase_cues = self._phrase_cues_named([
            (PhraseLabel.INTRO,  "Intro", 0),
            (PhraseLabel.CHORUS, "Drop",  32_000),
        ])
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, GenerationPrefs(mode="phrase"))
        by_slot = {c.slot: c for c in cues}
        assert by_slot[0].name == "Drop (Mix In)"

    def test_non_intro_slot_a_without_name_becomes_mix_in(self):
        """Slot A with no name gets renamed to 'Mix In'."""
        phrase_cues = self._phrase_cues_named([
            (PhraseLabel.INTRO,  "Intro", 0),
            (PhraseLabel.CHORUS, "",      32_000),
        ])
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, GenerationPrefs(mode="phrase"))
        by_slot = {c.slot: c for c in cues}
        assert by_slot[0].name == "Mix In"

    def test_intro_slot_a_name_unchanged(self):
        """When all phrases are Intro (degenerate), slot A keeps its name."""
        phrase_cues = self._phrase_cues_named([
            (PhraseLabel.INTRO, "Intro", 0),
            (PhraseLabel.INTRO, "Intro 2", 16_000),
        ])
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, GenerationPrefs(mode="phrase"))
        by_slot = {c.slot: c for c in cues}
        # All Intros → fall back to chronological; slot A = first Intro, no Mix In suffix
        assert "(Mix In)" not in by_slot[0].name

    def test_mix_in_suffix_not_duplicated(self):
        """Re-running smart ordering on already-suffixed names does not double-append."""
        phrase_cues = self._phrase_cues_named([
            (PhraseLabel.INTRO,  "Intro",         0),
            (PhraseLabel.CHORUS, "Drop (Mix In)", 32_000),
        ])
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, GenerationPrefs(mode="phrase"))
        by_slot = {c.slot: c for c in cues}
        assert by_slot[0].name == "Drop (Mix In)"
        assert "(Mix In) (Mix In)" not in by_slot[0].name

    def test_sequential_mode_no_mix_in_suffix(self):
        """Sequential slot priority does not add Mix In suffix."""
        phrase_cues = self._phrase_cues_named([
            (PhraseLabel.INTRO,  "Intro", 0),
            (PhraseLabel.CHORUS, "Drop",  32_000),
        ])
        content = _content(bpm=12800, length=300)
        db = MagicMock()
        prefs = GenerationPrefs(mode="phrase", slot_priority="sequential")
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            cues, _ = generate_cues_for_track(content, db, prefs)
        for c in cues:
            assert "(Mix In)" not in c.name
