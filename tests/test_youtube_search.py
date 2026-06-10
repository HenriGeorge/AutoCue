"""Unit + route tests for /api/youtube/search candidate-quality filter.

The filter mirrors the frontend `_ytLikelyMismatch` heuristic and is gated
on the caller passing both `artist` and `album` query params — without
them, behavior is unchanged (every candidate returned, `mismatch=False`).

When both are provided, the route:
1. Tags each candidate with `mismatch` based on a 4+ char token overlap
   between (title, channel) and (artist, album).
2. If ≥1 candidate is a real match, mismatches are DROPPED.
3. If ALL candidates mismatch, ALL are returned (with `mismatch=true`)
   so the caller can warn the user instead of showing nothing.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from autocue.serve.app import create_app
from autocue.serve.routes import _youtube_token_mismatch


# ────────────────────────── unit: token-mismatch ──────────────────────────

class TestYoutubeTokenMismatch:
    def test_returns_false_when_no_artist_and_no_album(self):
        assert _youtube_token_mismatch("Some Title", "Some Channel", "", "") is False

    def test_returns_false_when_haystack_is_empty(self):
        assert _youtube_token_mismatch("", "", "Drexciya", "Neptunes Lair") is False

    def test_returns_false_when_artist_token_matches_title(self):
        assert (
            _youtube_token_mismatch(
                "Drexciya - Neptune's Lair (1999)",
                "Random Channel",
                "Drexciya",
                "Neptunes Lair",
            )
            is False
        )

    def test_album_only_match_is_still_a_mismatch(self):
        # Album appears in title but artist does NOT — too risky to call
        # this a match because place names and generic terms ("Songs",
        # "Vénissieux", "30") cause Discogs→YouTube false positives. Caller
        # has to put artist in title or channel to count as legit.
        assert (
            _youtube_token_mismatch(
                "Selected Ambient Works compilation 1990s",
                "Generic Music Channel",
                "Aphex Twin",
                "Selected Ambient Works",
            )
            is True
        )

    def test_artist_in_title_OR_album_only_in_title_is_a_match(self):
        # Same album term as the mismatch case above, but now the title
        # also names the artist — that's the discriminator.
        assert (
            _youtube_token_mismatch(
                "Aphex Twin - Selected Ambient Works full album",
                "Random",
                "Aphex Twin",
                "Selected Ambient Works",
            )
            is False
        )

    def test_returns_false_when_artist_token_matches_channel_only(self):
        # Channel often carries the artist name in the official upload case.
        assert (
            _youtube_token_mismatch(
                "Full Album - HQ",
                "Drexciya Official",
                "Drexciya",
                "Neptunes Lair",
            )
            is False
        )

    def test_returns_true_for_clear_mismatch(self):
        # The original Vénissieux bug — a corporate-services video with
        # the location name in the title but nothing about the album.
        assert (
            _youtube_token_mismatch(
                "ZFU du Grand Lyon : 6e sens Global Services à Vénissieux",
                "Lyon Business",
                "Some Indie Artist",
                "Vénissieux Album",
            )
            is True
        )

    def test_returns_true_for_wrong_album_with_same_term(self):
        # Philip Glass - Songs case: search returned Schubert Winterreise.
        assert (
            _youtube_token_mismatch(
                "Winterreise, D 911: Das Wirtshaus",
                "Classical Music",
                "Philip Glass",
                "Songs",
            )
            is True
        )

    def test_is_case_insensitive(self):
        assert (
            _youtube_token_mismatch(
                "DREXCIYA - NEPTUNE'S LAIR",
                "random",
                "drexciya",
                "neptunes lair",
            )
            is False
        )

    def test_ignores_short_tokens(self):
        # "The" and "of" appear everywhere — shouldn't anchor a match.
        assert (
            _youtube_token_mismatch(
                "The Wheels of the Bus",
                "Nursery Rhymes",
                "The Beatles",
                "The of",
            )
            is True
        )


# ──────────────────────────── route integration ────────────────────────────

@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture
def mock_yt():
    """Patch the inner search + ytdlp_available probes so no network call."""
    with patch("autocue.download.ytdlp_available", return_value=True), \
         patch("autocue.download.search_youtube") as mock_search:
        yield mock_search


class TestYoutubeSearchRoute:
    def test_no_filter_params_returns_all_candidates(self, client, mock_yt):
        mock_yt.return_value = [
            {"url": "https://youtu.be/a", "title": "First", "uploader": "Ch1"},
            {"url": "https://youtu.be/b", "title": "Second", "uploader": "Ch2"},
        ]
        r = client.get("/api/youtube/search", params={"q": "anything"})
        assert r.status_code == 200
        body = r.json()
        assert len(body["candidates"]) == 2
        # Without artist/album, mismatch defaults to False on all.
        assert all(c["mismatch"] is False for c in body["candidates"])

    def test_artist_album_drops_mismatches_when_at_least_one_match(
        self, client, mock_yt,
    ):
        mock_yt.return_value = [
            {
                "url": "https://youtu.be/wrong",
                "title": "ZFU du Grand Lyon corporate services",
                "uploader": "Lyon Business",
            },
            {
                "url": "https://youtu.be/right",
                "title": "Drexciya - Neptune's Lair full album",
                "uploader": "Tresor",
            },
        ]
        r = client.get(
            "/api/youtube/search",
            params={"q": "Drexciya Neptunes Lair", "artist": "Drexciya", "album": "Neptunes Lair"},
        )
        assert r.status_code == 200
        body = r.json()
        # Mismatch dropped; only the real match survives.
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["url"].endswith("right")
        assert body["candidates"][0]["mismatch"] is False

    def test_all_mismatches_kept_so_caller_can_warn(self, client, mock_yt):
        # Worst-case Vénissieux scenario — every candidate is wrong but the
        # caller still gets results so they can show a "no clean match" warning.
        mock_yt.return_value = [
            {"url": "https://youtu.be/a", "title": "Wrong One", "uploader": "Random"},
            {"url": "https://youtu.be/b", "title": "Also Wrong", "uploader": "Other"},
        ]
        r = client.get(
            "/api/youtube/search",
            params={
                "q": "Mystery Artist Mystery Album",
                "artist": "Mystery Artist",
                "album": "Mystery Album",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["candidates"]) == 2
        assert all(c["mismatch"] is True for c in body["candidates"])

    def test_only_artist_no_album_does_not_filter(self, client, mock_yt):
        """Filter requires BOTH params — passing just one is treated as
        'caller doesn't know enough to judge', so no filtering."""
        mock_yt.return_value = [
            {"url": "https://youtu.be/a", "title": "Random unrelated", "uploader": "x"},
        ]
        r = client.get(
            "/api/youtube/search",
            params={"q": "anything", "artist": "Drexciya"},  # no album
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["candidates"]) == 1
        # mismatch flag also unset (the filter doesn't run unless both arrive).
        assert body["candidates"][0]["mismatch"] is False
