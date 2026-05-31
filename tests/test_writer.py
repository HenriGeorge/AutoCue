"""
Tests for autocue/writer.py

Adversarial notes before writing:
- RekordboxXml writes Start as a raw float string (e.g. "10.5", not "10.500").
  Parse to float for comparison rather than doing string equality.
- Num and Start are stored as XML attribute strings — always convert before comparing.
- MagicMock for DjmdContent must have explicit string values for FolderPath/FileNameL/etc.;
  otherwise MagicMock auto-attributes become MagicMock objects that os.path.join silently
  concatenates into nonsense paths with no error.
- Location in the TRACK element is prefixed with "file://localhost/" by RekordboxXml.
  Tests should check the path ends with the expected filename rather than equality.
- write_xml returns a resolved Path — verify the file actually exists on disk.
- Memory cues (slot=-1) must appear with Num="-1" in the XML.
- Multiple tracks must each produce a separate TRACK element with their own POSITION_MARKs.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autocue.models import CuePoint, PhraseLabel
from autocue.writer import _resolve_file_path, write_xml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_content(
    title: str = "Test Track",
    artist: str = "Test Artist",
    folder: str = "/Music",
    filename: str = "test.mp3",
) -> MagicMock:
    """Return a MagicMock DjmdContent with explicit string attributes."""
    content = MagicMock()
    content.Title = title
    content.ArtistName = artist
    content.FolderPath = folder
    content.FileNameL = filename
    content.FileNameS = filename
    return content


def _parse_position_marks(xml_path: Path) -> list[dict]:
    """Return a list of attribute dicts for every POSITION_MARK in the XML file."""
    tree = ET.parse(str(xml_path))
    root = tree.getroot()
    return [elem.attrib for elem in root.iter("POSITION_MARK")]


def _parse_tracks(xml_path: Path) -> list[ET.Element]:
    """Return all TRACK elements from the XML file."""
    tree = ET.parse(str(xml_path))
    root = tree.getroot()
    return list(root.iter("TRACK"))


# ---------------------------------------------------------------------------
# Basic file creation
# ---------------------------------------------------------------------------

class TestWriteXmlCreatesFile:
    def test_returns_path(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        result = write_xml([(content, cues)], tmp_path / "out.xml")
        assert isinstance(result, Path)

    def test_file_exists_after_write(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        assert out.exists()

    def test_file_is_valid_xml(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=5000, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        # Parsing succeeds → valid XML
        ET.parse(str(out))

    def test_returned_path_resolves_to_correct_location(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        result = write_xml([(content, cues)], out)
        assert result.resolve() == out.resolve()


# ---------------------------------------------------------------------------
# POSITION_MARK attributes
# ---------------------------------------------------------------------------

class TestPositionMarkAttributes:
    def test_start_value_matches_position_sec(self, tmp_path):
        content = _make_content()
        # 50000ms → 50.0s
        cues = [CuePoint(position_ms=50_000, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert len(marks) == 1
        assert float(marks[0]["Start"]) == pytest.approx(50.0)

    def test_start_value_non_round(self, tmp_path):
        content = _make_content()
        # 1234ms → 1.234s
        cues = [CuePoint(position_ms=1234, label=PhraseLabel.CHORUS, slot=1)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert float(marks[0]["Start"]) == pytest.approx(1.234)

    def test_num_attribute_matches_slot(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=3)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert int(marks[0]["Num"]) == 3

    def test_num_attribute_slot_zero(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert int(marks[0]["Num"]) == 0

    def test_memory_cue_num_is_minus_one(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=5000, label=PhraseLabel.VERSE, slot=-1)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert int(marks[0]["Num"]) == -1

    def test_name_attribute_is_label_value(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert marks[0]["Name"] == "Intro"

    def test_name_attribute_chorus(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=0, label=PhraseLabel.CHORUS, slot=2)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert marks[0]["Name"] == "Chorus"

    def test_name_attribute_unknown(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=0, label=PhraseLabel.UNKNOWN, slot=1)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert marks[0]["Name"] == "?"

    def test_name_field_overrides_label_value(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=0, label=PhraseLabel.CHORUS, slot=0, name="Drop")]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert marks[0]["Name"] == "Drop"

    def test_empty_name_falls_back_to_label_value(self, tmp_path):
        content = _make_content()
        cues = [CuePoint(position_ms=0, label=PhraseLabel.CHORUS, slot=0, name="")]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert marks[0]["Name"] == "Chorus"


# ---------------------------------------------------------------------------
# Multiple cues per track
# ---------------------------------------------------------------------------

class TestMultipleCuesPerTrack:
    def test_correct_cue_count(self, tmp_path):
        content = _make_content()
        cues = [
            CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0),
            CuePoint(position_ms=30_000, label=PhraseLabel.VERSE, slot=1),
            CuePoint(position_ms=60_000, label=PhraseLabel.CHORUS, slot=2),
        ]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert len(marks) == 3

    def test_start_values_match_positions(self, tmp_path):
        content = _make_content()
        cues = [
            CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0),
            CuePoint(position_ms=30_000, label=PhraseLabel.VERSE, slot=1),
            CuePoint(position_ms=60_000, label=PhraseLabel.CHORUS, slot=2),
        ]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        expected_starts = [0.0, 30.0, 60.0]
        for mark, expected in zip(marks, expected_starts):
            assert float(mark["Start"]) == pytest.approx(expected)

    def test_num_attributes_match_slots(self, tmp_path):
        content = _make_content()
        cues = [
            CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0),
            CuePoint(position_ms=1000, label=PhraseLabel.CHORUS, slot=4),
            CuePoint(position_ms=2000, label=PhraseLabel.OUTRO, slot=7),
        ]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert [int(m["Num"]) for m in marks] == [0, 4, 7]

    def test_mixed_hot_and_memory_cues(self, tmp_path):
        content = _make_content()
        cues = [
            CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0),
            CuePoint(position_ms=500, label=PhraseLabel.VERSE, slot=-1),
        ]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        nums = {int(m["Num"]) for m in marks}
        assert 0 in nums
        assert -1 in nums


# ---------------------------------------------------------------------------
# Multiple tracks
# ---------------------------------------------------------------------------

class TestMultipleTracks:
    def test_two_tracks_produce_two_track_elements(self, tmp_path):
        c1 = _make_content(title="Track One", folder="/Music", filename="one.mp3")
        c2 = _make_content(title="Track Two", folder="/Music", filename="two.mp3")
        cues1 = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        cues2 = [CuePoint(position_ms=1000, label=PhraseLabel.CHORUS, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(c1, cues1), (c2, cues2)], out)
        tracks = _parse_tracks(out)
        assert len(tracks) == 2

    def test_two_tracks_produce_correct_cue_count(self, tmp_path):
        c1 = _make_content(title="Track One", folder="/Music", filename="one.mp3")
        c2 = _make_content(title="Track Two", folder="/Music", filename="two.mp3")
        cues1 = [
            CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0),
            CuePoint(position_ms=10_000, label=PhraseLabel.VERSE, slot=1),
        ]
        cues2 = [CuePoint(position_ms=5000, label=PhraseLabel.CHORUS, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(c1, cues1), (c2, cues2)], out)
        marks = _parse_position_marks(out)
        assert len(marks) == 3

    def test_track_names_written(self, tmp_path):
        c1 = _make_content(title="Alpha", folder="/Music", filename="alpha.mp3")
        c2 = _make_content(title="Beta", folder="/Music", filename="beta.mp3")
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(c1, cues), (c2, cues)], out)
        tracks = _parse_tracks(out)
        names = {t.get("Name") for t in tracks}
        assert "Alpha" in names
        assert "Beta" in names

    def test_empty_tracks_list_produces_valid_xml(self, tmp_path):
        out = tmp_path / "out.xml"
        write_xml([], out)
        ET.parse(str(out))  # valid XML without error


# ---------------------------------------------------------------------------
# File path / Location attribute
# ---------------------------------------------------------------------------

class TestTrackLocation:
    def test_location_contains_filename(self, tmp_path):
        content = _make_content(folder="/Music", filename="my_track.mp3")
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        tracks = _parse_tracks(out)
        assert len(tracks) == 1
        location = tracks[0].get("Location", "")
        assert "my_track.mp3" in location

    def test_location_contains_folder(self, tmp_path):
        content = _make_content(folder="/Library/Music", filename="song.mp3")
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        tracks = _parse_tracks(out)
        location = tracks[0].get("Location", "")
        assert "Library" in location or "Music" in location


# ---------------------------------------------------------------------------
# _resolve_file_path
# ---------------------------------------------------------------------------

class TestResolveFilePath:
    def _content(self, folder=None, file_l=None, file_s=None):
        c = MagicMock()
        c.FolderPath = folder
        c.FileNameL = file_l
        c.FileNameS = file_s
        return c

    def test_normal_path_joined(self):
        c = self._content(folder="/Music", file_l="track.mp3")
        assert _resolve_file_path(c) == "/Music/track.mp3"

    def test_folder_path_none_uses_empty_string(self):
        c = self._content(folder=None, file_l="track.mp3")
        result = _resolve_file_path(c)
        assert result.endswith("track.mp3")
        assert result != ""

    def test_file_name_l_none_falls_back_to_file_name_s(self):
        c = self._content(folder="/Music", file_l=None, file_s="short.mp3")
        result = _resolve_file_path(c)
        assert "short.mp3" in result

    def test_both_filenames_none_produces_folder_only(self):
        # os.path.join("/Music", "") returns "/Music/" — no filename component.
        # Rekordbox silently ignores entries with invalid paths on import.
        c = self._content(folder="/Music", file_l=None, file_s=None)
        result = _resolve_file_path(c)
        assert "Music" in result
        assert result == "/Music/"

    def test_all_fields_none_returns_empty_string(self):
        c = self._content(folder=None, file_l=None, file_s=None)
        assert _resolve_file_path(c) == ""

    def test_empty_string_fields(self):
        c = self._content(folder="", file_l="", file_s="")
        assert _resolve_file_path(c) == ""

    def test_trailing_slash_on_folder_no_double_slash(self):
        c = self._content(folder="/Music/", file_l="song.mp3")
        result = _resolve_file_path(c)
        assert "//" not in result
        assert result.endswith("song.mp3")


# ---------------------------------------------------------------------------
# write_xml with None Title / ArtistName
# ---------------------------------------------------------------------------

class TestWriteXmlNoneFields:
    def test_none_title_omits_name_attribute(self, tmp_path):
        content = _make_content(title="Placeholder")
        content.Title = None
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        tracks = _parse_tracks(out)
        assert tracks[0].get("Name") is None

    def test_empty_string_title_omits_name_attribute(self, tmp_path):
        content = _make_content(title="Placeholder")
        content.Title = ""
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        tracks = _parse_tracks(out)
        assert tracks[0].get("Name") is None

    def test_none_artist_omits_artist_attribute(self, tmp_path):
        content = _make_content()
        content.ArtistName = None
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        tracks = _parse_tracks(out)
        assert tracks[0].get("Artist") is None

    def test_none_title_and_artist_track_still_written(self, tmp_path):
        content = _make_content()
        content.Title = None
        content.ArtistName = None
        cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        tracks = _parse_tracks(out)
        assert len(tracks) == 1


# ---------------------------------------------------------------------------
# write_xml with empty cues
# ---------------------------------------------------------------------------

class TestWriteXmlEmptyCues:
    def test_track_with_no_cues_produces_no_position_marks(self, tmp_path):
        content = _make_content()
        out = tmp_path / "out.xml"
        write_xml([(content, [])], out)
        marks = _parse_position_marks(out)
        assert marks == []

    def test_track_with_no_cues_still_produces_track_element(self, tmp_path):
        content = _make_content(title="Silent Track")
        out = tmp_path / "out.xml"
        write_xml([(content, [])], out)
        tracks = _parse_tracks(out)
        assert len(tracks) == 1

    def test_mix_of_cued_and_uncued_tracks(self, tmp_path):
        c1 = _make_content(title="With Cues", folder="/Music", filename="a.mp3")
        c2 = _make_content(title="No Cues", folder="/Music", filename="b.mp3")
        cues = [
            CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0),
            CuePoint(position_ms=30_000, label=PhraseLabel.CHORUS, slot=1),
        ]
        out = tmp_path / "out.xml"
        write_xml([(c1, cues), (c2, [])], out)
        marks = _parse_position_marks(out)
        assert len(marks) == 2


# ---------------------------------------------------------------------------
# All 8 slots
# ---------------------------------------------------------------------------

class TestWriteXmlAllSlots:
    def test_all_eight_slots_written(self, tmp_path):
        from autocue.models import PhraseLabel
        labels = [
            PhraseLabel.INTRO, PhraseLabel.VERSE, PhraseLabel.BRIDGE,
            PhraseLabel.CHORUS, PhraseLabel.OUTRO, PhraseLabel.UP,
            PhraseLabel.DOWN, PhraseLabel.UNKNOWN,
        ]
        content = _make_content()
        cues = [CuePoint(position_ms=i * 10_000, label=labels[i], slot=i) for i in range(8)]
        out = tmp_path / "out.xml"
        write_xml([(content, cues)], out)
        marks = _parse_position_marks(out)
        assert len(marks) == 8
        nums = sorted(int(m["Num"]) for m in marks)
        assert nums == list(range(8))
