"""Tests for autocue.analysis.duplicates — phase 1 (read-only scan)."""
from __future__ import annotations

import pytest

from autocue.analysis.duplicates import (
    DuplicateGroup,
    TrackProjection,
    find_duplicate_groups,
    normalize_key,
    pick_keeper,
)


# ============================================================ normalize_key

class TestNormalizeKey:
    def test_lowercases_and_strips(self):
        assert normalize_key(" Ed Longo  ", " New Life  ") == "ed longo|||new life"

    def test_collapses_internal_whitespace(self):
        assert (
            normalize_key("Gia    Margaret", "Apathy  (Original Mix)")
            == "gia margaret|||apathy (original mix)"
        )

    def test_both_empty_returns_empty_string(self):
        assert normalize_key("", "") == ""
        assert normalize_key(None, None) == ""

    def test_one_side_empty_still_returns_key(self):
        # Classical / various-artist tracks with empty artist must still
        # group on title alone.
        assert normalize_key("", "Symphony No. 9") == "|||symphony no. 9"
        assert normalize_key("Daft Punk", "") == "daft punk|||"

    def test_case_insensitive(self):
        assert normalize_key("DAFT PUNK", "GET LUCKY") == normalize_key(
            "Daft Punk", "Get Lucky"
        )


# ============================================================ pick_keeper

def _track(track_id, *, plays=0, hot_cues=0, last_played=None):
    return TrackProjection(
        track_id=track_id,
        title="X", artist="Y",
        play_count=plays,
        existing_hot_cues=hot_cues,
        last_played=last_played,
    )


def _full(track_id, artist, title, *, plays=0, hot_cues=0, last_played=None, source="file"):
    return TrackProjection(
        track_id=track_id,
        artist=artist,
        title=title,
        play_count=plays,
        existing_hot_cues=hot_cues,
        last_played=last_played,
        source=source,
    )


class TestPickKeeper:
    def test_highest_play_count_wins(self):
        assert pick_keeper([_track(1, plays=2), _track(2, plays=5)]) == 2

    def test_falls_back_to_hot_cues_when_plays_tied(self):
        assert (
            pick_keeper([
                _track(1, plays=3, hot_cues=2),
                _track(2, plays=3, hot_cues=8),
            ])
            == 2
        )

    def test_falls_back_to_last_played_when_plays_and_cues_tied(self):
        assert (
            pick_keeper([
                _track(1, plays=3, hot_cues=4, last_played="2024-01-01 00:00:00"),
                _track(2, plays=3, hot_cues=4, last_played="2026-06-10 00:00:00"),
            ])
            == 2
        )

    def test_track_id_breaks_ties(self):
        # Two truly identical copies — the lower track_id wins so a re-scan
        # always returns the same keeper.
        assert pick_keeper([_track(7), _track(3), _track(5)]) == 3

    def test_missing_last_played_loses_against_a_date(self):
        # A row with a date beats a row without one (other fields equal).
        assert (
            pick_keeper([
                _track(1, plays=0, hot_cues=0, last_played=None),
                _track(2, plays=0, hot_cues=0, last_played="2025-01-01 00:00:00"),
            ])
            == 2
        )


# ============================================================ find_duplicate_groups

class TestFindDuplicateGroups:
    def test_returns_empty_list_when_no_duplicates(self):
        tracks = [
            _full(1, "A", "X"),
            _full(2, "B", "Y"),
            _full(3, "C", "Z"),
        ]
        assert find_duplicate_groups(tracks) == []

    def test_finds_a_simple_duplicate_pair(self):
        tracks = [
            _full(1, "Ed Longo", "New Life", plays=5),
            _full(2, "Ed Longo", "New Life", plays=2),
            _full(3, "Other", "Track", plays=0),
        ]
        groups = find_duplicate_groups(tracks)
        assert len(groups) == 1
        assert groups[0].artist == "Ed Longo"
        assert groups[0].title == "New Life"
        assert len(groups[0].copies) == 2
        assert groups[0].keeper_id == 1  # higher play count wins

    def test_case_and_whitespace_insensitive_match(self):
        tracks = [
            _full(1, "Ed Longo ", " New Life"),
            _full(2, "ed   longo", "new   life"),
        ]
        assert len(find_duplicate_groups(tracks)) == 1

    def test_skips_empty_metadata_tracks(self):
        # The 10 streaming tracks with empty artist+title should NOT form
        # one giant fake bucket.
        tracks = [
            _full(1, "", "", source="streaming"),
            _full(2, "", "", source="streaming"),
            _full(3, "", "", source="streaming"),
        ]
        assert find_duplicate_groups(tracks) == []

    def test_groups_sort_by_count_desc_then_alpha(self):
        # 2 in "B", 4 in "A". Worst offender first.
        tracks = (
            [_full(100 + i, "Alpha", "Same", plays=i) for i in range(4)]
            + [_full(200 + i, "Beta", "Same", plays=i) for i in range(2)]
        )
        groups = find_duplicate_groups(tracks)
        assert [len(g.copies) for g in groups] == [4, 2]
        assert groups[0].artist.lower() == "alpha"

    def test_keeper_marked_in_to_dict_output(self):
        tracks = [
            _full(1, "X", "Y", plays=10),
            _full(2, "X", "Y", plays=1),
        ]
        d = find_duplicate_groups(tracks)[0].to_dict()
        keepers = [c for c in d["copies"] if c["is_keeper"]]
        assert len(keepers) == 1
        assert keepers[0]["track_id"] == 1

    def test_artist_or_title_alone_still_groups(self):
        # Classical / various-artists pattern — empty artist, distinct titles.
        tracks = [
            _full(1, "", "Symphony No. 9"),
            _full(2, "", "Symphony No. 9"),
            _full(3, "", "Symphony No. 5"),
        ]
        groups = find_duplicate_groups(tracks)
        assert len(groups) == 1
        assert {c.track_id for c in groups[0].copies} == {1, 2}

    def test_artist_unnormalised_echoed_back(self):
        # The bucket key is normalised but we show the user the original
        # capitalisation + spacing from the first copy.
        tracks = [
            _full(1, "Ed Longo", "New Life"),
            _full(2, "ED LONGO", "NEW LIFE"),
        ]
        group = find_duplicate_groups(tracks)[0]
        assert group.artist == "Ed Longo"
        assert group.title == "New Life"
