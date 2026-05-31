"""
Tests for autocue/models.py

Adversarial notes before writing:
- phrase_label tests pin exact enum values, not just membership, to catch wrong label bugs.
- position_sec uses a non-round number to catch integer-truncation bugs.
- slot_name verifies boundary at slot=7 ("H") since chr(65+7)=72 could be miscomputed.
- Dataclass tests use value equality (not identity) to confirm @dataclass behavior.
- UNKNOWN value is "?" not "Unknown" — easy to get wrong.
"""
import dataclasses

import pytest

from autocue.models import CuePoint, DJ_NAMES, PhraseLabel, phrase_label


# ---------------------------------------------------------------------------
# PhraseLabel enum
# ---------------------------------------------------------------------------

class TestPhraseLabelValues:
    def test_intro_value(self):
        assert PhraseLabel.INTRO.value == "Intro"

    def test_unknown_value(self):
        # "?" not "Unknown" — guard against a common copy-paste mistake
        assert PhraseLabel.UNKNOWN.value == "?"

    def test_all_members_present(self):
        names = {m.name for m in PhraseLabel}
        assert names == {"INTRO", "VERSE", "BRIDGE", "CHORUS", "OUTRO", "UP", "DOWN", "UNKNOWN"}


# ---------------------------------------------------------------------------
# phrase_label()
# ---------------------------------------------------------------------------

class TestPhraseLabel:
    # mood=3 (Low)
    def test_mood3_kind1_intro(self):
        assert phrase_label(3, 1) is PhraseLabel.INTRO

    def test_mood3_kind9_chorus(self):
        assert phrase_label(3, 9) is PhraseLabel.CHORUS

    def test_mood3_kind10_outro(self):
        assert phrase_label(3, 10) is PhraseLabel.OUTRO

    def test_mood3_kind2_verse(self):
        assert phrase_label(3, 2) is PhraseLabel.VERSE

    def test_mood3_kind8_bridge(self):
        assert phrase_label(3, 8) is PhraseLabel.BRIDGE

    # mood=2 (Mid) — same structure as mood=3
    def test_mood2_kind1_intro(self):
        assert phrase_label(2, 1) is PhraseLabel.INTRO

    def test_mood2_kind8_bridge(self):
        assert phrase_label(2, 8) is PhraseLabel.BRIDGE

    def test_mood2_kind9_chorus(self):
        assert phrase_label(2, 9) is PhraseLabel.CHORUS

    def test_mood2_kind10_outro(self):
        assert phrase_label(2, 10) is PhraseLabel.OUTRO

    # mood=1 (High) — different kind mapping
    def test_mood1_kind2_up(self):
        assert phrase_label(1, 2) is PhraseLabel.UP

    def test_mood1_kind3_down(self):
        assert phrase_label(1, 3) is PhraseLabel.DOWN

    def test_mood1_kind5_chorus(self):
        assert phrase_label(1, 5) is PhraseLabel.CHORUS

    def test_mood1_kind1_intro(self):
        assert phrase_label(1, 1) is PhraseLabel.INTRO

    # Unknown combinations
    def test_unknown_mood(self):
        assert phrase_label(99, 1) is PhraseLabel.UNKNOWN

    def test_unknown_kind_in_valid_mood(self):
        # mood=1 has no kind=99
        assert phrase_label(1, 99) is PhraseLabel.UNKNOWN

    def test_zero_mood(self):
        assert phrase_label(0, 1) is PhraseLabel.UNKNOWN

    def test_zero_kind_in_valid_mood(self):
        assert phrase_label(3, 0) is PhraseLabel.UNKNOWN

    def test_negative_mood(self):
        assert phrase_label(-1, 1) is PhraseLabel.UNKNOWN

    def test_returns_phrase_label_instance(self):
        result = phrase_label(3, 1)
        assert isinstance(result, PhraseLabel)


# ---------------------------------------------------------------------------
# CuePoint dataclass
# ---------------------------------------------------------------------------

class TestCuePointDataclass:
    def test_is_dataclass(self):
        assert dataclasses.is_dataclass(CuePoint)

    def test_expected_fields(self):
        field_names = {f.name for f in dataclasses.fields(CuePoint)}
        assert field_names == {"position_ms", "label", "slot", "name", "color_id"}

    def test_value_equality(self):
        a = CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0)
        b = CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0)
        assert a == b

    def test_inequality(self):
        a = CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0)
        b = CuePoint(position_ms=2000, label=PhraseLabel.INTRO, slot=0)
        assert a != b

    def test_field_access(self):
        cue = CuePoint(position_ms=5000, label=PhraseLabel.CHORUS, slot=3)
        assert cue.position_ms == 5000
        assert cue.label is PhraseLabel.CHORUS
        assert cue.slot == 3

    def test_name_defaults_to_empty_string(self):
        cue = CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)
        assert cue.name == ""

    def test_name_can_be_set(self):
        cue = CuePoint(position_ms=0, label=PhraseLabel.CHORUS, slot=0, name="Drop")
        assert cue.name == "Drop"


# ---------------------------------------------------------------------------
# DJ_NAMES mapping
# ---------------------------------------------------------------------------

class TestDJNames:
    def test_chorus_maps_to_drop(self):
        assert DJ_NAMES[PhraseLabel.CHORUS] == "Drop"

    def test_up_maps_to_build(self):
        assert DJ_NAMES[PhraseLabel.UP] == "Build"

    def test_down_maps_to_break(self):
        assert DJ_NAMES[PhraseLabel.DOWN] == "Break"

    def test_intro_unchanged(self):
        assert DJ_NAMES[PhraseLabel.INTRO] == "Intro"

    def test_verse_unchanged(self):
        assert DJ_NAMES[PhraseLabel.VERSE] == "Verse"

    def test_outro_unchanged(self):
        assert DJ_NAMES[PhraseLabel.OUTRO] == "Outro"

    def test_bridge_unchanged(self):
        assert DJ_NAMES[PhraseLabel.BRIDGE] == "Bridge"

    def test_unknown_is_empty_string(self):
        assert DJ_NAMES[PhraseLabel.UNKNOWN] == ""

    def test_all_labels_covered(self):
        assert set(DJ_NAMES.keys()) == set(PhraseLabel)


# ---------------------------------------------------------------------------
# CuePoint.position_sec
# ---------------------------------------------------------------------------

class TestPositionSec:
    def test_round_number(self):
        cue = CuePoint(position_ms=50_000, label=PhraseLabel.INTRO, slot=0)
        assert cue.position_sec == 50.0

    def test_non_round_number(self):
        # 1234 ms → 1.234 s; tests float division not integer truncation
        cue = CuePoint(position_ms=1234, label=PhraseLabel.INTRO, slot=0)
        assert cue.position_sec == pytest.approx(1.234)

    def test_zero(self):
        cue = CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)
        assert cue.position_sec == 0.0

    def test_large_value(self):
        # 3_600_000 ms = 3600.0 s (1 hour)
        cue = CuePoint(position_ms=3_600_000, label=PhraseLabel.OUTRO, slot=7)
        assert cue.position_sec == 3600.0

    def test_returns_float(self):
        cue = CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0)
        assert isinstance(cue.position_sec, float)


# ---------------------------------------------------------------------------
# CuePoint.slot_name
# ---------------------------------------------------------------------------

class TestSlotName:
    def test_slot_0_is_A(self):
        cue = CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)
        assert cue.slot_name == "A"

    def test_slot_1_is_B(self):
        cue = CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=1)
        assert cue.slot_name == "B"

    def test_slot_7_is_H(self):
        # Boundary: 7 is the last valid hot cue slot
        cue = CuePoint(position_ms=0, label=PhraseLabel.OUTRO, slot=7)
        assert cue.slot_name == "H"

    def test_slot_minus1_is_Mem(self):
        cue = CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=-1)
        assert cue.slot_name == "Mem"

    def test_slot_mid_range(self):
        # slot=4 → 'E'
        cue = CuePoint(position_ms=0, label=PhraseLabel.CHORUS, slot=4)
        assert cue.slot_name == "E"
