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
        # Trailing `|||0` is the duration_bucket — 0 when duration is omitted.
        assert (
            normalize_key(" Ed Longo  ", " New Life  ")
            == "ed longo|||new life|||0"
        )

    def test_collapses_internal_whitespace(self):
        assert (
            normalize_key("Gia    Margaret", "Apathy  (Original Mix)")
            == "gia margaret|||apathy (original mix)|||0"
        )

    def test_both_empty_returns_empty_string(self):
        assert normalize_key("", "") == ""
        assert normalize_key(None, None) == ""

    def test_one_side_empty_still_returns_key(self):
        # Classical / various-artist tracks with empty artist must still
        # group on title alone.
        assert normalize_key("", "Symphony No. 9") == "|||symphony no. 9|||0"
        assert normalize_key("Daft Punk", "") == "daft punk||||||0"

    def test_case_insensitive(self):
        assert normalize_key("DAFT PUNK", "GET LUCKY") == normalize_key(
            "Daft Punk", "Get Lucky"
        )

    def test_duration_omitted_keeps_phase2_behaviour(self):
        # When the caller doesn't pass `duration`, the bucket falls to 0
        # and two same-artist+title tracks still collide. Phase 1/2
        # callers (and the JS frontend's vendored helper) never produced
        # a duration; they must keep grouping.
        a = normalize_key("X", "Y")
        b = normalize_key("X", "Y", duration=None)
        c = normalize_key("X", "Y", duration=0)
        assert a == b == c

    def test_duration_within_5s_groups(self):
        # Two ID3-tagged copies of the same song will often disagree on
        # length by ±1–2 s due to encoder rounding. They must still
        # bucket together.
        assert (
            normalize_key("X", "Y", duration=240.4)
            == normalize_key("X", "Y", duration=241.6)
        )

    def test_duration_separates_distinct_mixes(self):
        # Same (artist, title) but materially different durations →
        # different keys. Album mix 4:12 vs extended mix 6:48.
        assert (
            normalize_key("X", "Y", duration=252.0)  # 4:12
            != normalize_key("X", "Y", duration=408.0)  # 6:48
        )

    def test_duration_negative_buckets_as_unknown(self):
        # Defensive: a bogus negative duration shouldn't error and
        # shouldn't accidentally create its own bucket.
        assert (
            normalize_key("X", "Y", duration=-10.0)
            == normalize_key("X", "Y", duration=None)
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


def _full(track_id, artist, title, *, plays=0, hot_cues=0, last_played=None,
          source="file", duration=0.0, bitrate=0,
          folder_path="", file_name=""):
    return TrackProjection(
        track_id=track_id,
        artist=artist,
        title=title,
        play_count=plays,
        existing_hot_cues=hot_cues,
        last_played=last_played,
        source=source,
        duration=duration,
        bitrate=bitrate,
        folder_path=folder_path,
        file_name=file_name,
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

    def test_distinct_durations_do_not_collapse(self):
        # The 4:12 album cut and the 6:48 extended mix of the "same"
        # song must not merge — different audio, different intent.
        tracks = [
            _full(1, "X", "Y", duration=252.0),
            _full(2, "X", "Y", duration=408.0),
        ]
        assert find_duplicate_groups(tracks) == []

    def test_close_durations_still_collapse(self):
        # Two imports of the same song with encoder-rounded durations.
        tracks = [
            _full(1, "X", "Y", duration=240.4),
            _full(2, "X", "Y", duration=241.6),
        ]
        assert len(find_duplicate_groups(tracks)) == 1

    def test_to_dict_emits_same_path_chip(self):
        # Same FolderPath+FileNameL across two copies → same-path chip
        # true; deleting the non-keeper leaves no orphan file on disk.
        tracks = [
            _full(1, "X", "Y", duration=240.0,
                  folder_path="/lib/", file_name="song.mp3", plays=5),
            _full(2, "X", "Y", duration=240.0,
                  folder_path="/lib/", file_name="song.mp3", plays=1),
            _full(3, "X", "Y", duration=240.0,
                  folder_path="/other/", file_name="song.mp3", plays=0),
        ]
        d = find_duplicate_groups(tracks)[0].to_dict()
        chips = {c["track_id"]: c["same_path_as_keeper"] for c in d["copies"]}
        # Track 1 wins keeper (most plays) → it's the same-path baseline.
        assert chips[1] is True
        assert chips[2] is True  # also /lib/song.mp3
        assert chips[3] is False  # /other/song.mp3 — distinct file
