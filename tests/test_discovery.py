"""Tests for autocue/analysis/discovery.py — new-release suggestions via Discogs."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from autocue.analysis import discovery


def _content(artist="", album=""):
    return SimpleNamespace(ArtistName=artist, AlbumName=album)


def _db(contents):
    db = MagicMock()
    q = MagicMock()
    q.all.return_value = contents
    db.get_content.return_value = q
    return db


def _release(album, year=2025, artist="Artist"):
    return {
        "title": f"{artist} - {album}",
        "artist": artist,
        "album": album,
        "year": year,
        "thumb": "", "cover": "", "genres": [], "styles": [],
        "url": "", "id": 1, "formats": [],
    }


# ---------------------------------------------------------------------------
# library_artists
# ---------------------------------------------------------------------------

class TestLibraryArtists:
    def test_orders_by_frequency(self):
        db = _db([_content("A"), _content("B"), _content("A"), _content("A"), _content("B")])
        assert discovery.library_artists(db) == ["A", "B"]

    def test_skips_blank_artists(self):
        db = _db([_content(""), _content("  "), _content("Real")])
        assert discovery.library_artists(db) == ["Real"]

    def test_respects_top_n(self):
        db = _db([_content("A"), _content("B"), _content("C")])
        assert len(discovery.library_artists(db, top_n=2)) == 2

    def test_empty_library(self):
        assert discovery.library_artists(_db([])) == []

    def test_read_error_returns_empty(self):
        db = MagicMock()
        db.get_content.side_effect = RuntimeError("boom")
        assert discovery.library_artists(db) == []


# ---------------------------------------------------------------------------
# library_album_set
# ---------------------------------------------------------------------------

class TestLibraryAlbumSet:
    def test_normalizes_albums(self):
        db = _db([_content("A", "  Deep   House  "), _content("B", "TECHNO")])
        owned = discovery.library_album_set(db)
        assert "deep house" in owned
        assert "techno" in owned

    def test_ignores_blank_albums(self):
        db = _db([_content("A", ""), _content("B", "Real Album")])
        assert discovery.library_album_set(db) == {"real album"}


# ---------------------------------------------------------------------------
# iter_new_releases
# ---------------------------------------------------------------------------

class TestIterNewReleases:
    def test_yields_unowned_releases(self):
        db = _db([_content("Artist", "Old Album")])
        with patch.object(discovery, "search_artist_releases",
                           return_value=[_release("New Album", 2025)]):
            out = list(discovery.iter_new_releases(db, token="tok", since_year=2024))
        suggestions = [s for _, _, s in out if s]
        assert len(suggestions) == 1
        assert suggestions[0]["album"] == "New Album"
        assert suggestions[0]["query"] == "Artist New Album"

    def test_filters_already_owned(self):
        db = _db([_content("Artist", "Owned Album")])
        with patch.object(discovery, "search_artist_releases",
                           return_value=[_release("Owned Album", 2025)]):
            out = list(discovery.iter_new_releases(db, token="tok", since_year=2024))
        assert [s for _, _, s in out if s] == []

    def test_dedupes_across_artists(self):
        db = _db([_content("A"), _content("B")])
        def fake(artist, token, year_from=None, **kw):
            return [_release("Shared Comp", 2025, artist=artist)]
        with patch.object(discovery, "search_artist_releases", side_effect=fake):
            out = list(discovery.iter_new_releases(db, token="tok", since_year=2024))
        assert len([s for _, _, s in out if s]) == 1

    def test_per_artist_cap(self):
        db = _db([_content("Artist")])
        rels = [_release(f"Album {i}", 2025) for i in range(10)]
        with patch.object(discovery, "search_artist_releases", return_value=rels):
            out = list(discovery.iter_new_releases(db, token="tok", since_year=2024, per_artist=3))
        assert len([s for _, _, s in out if s]) == 3

    def test_progress_tick_when_no_releases(self):
        db = _db([_content("Artist")])
        with patch.object(discovery, "search_artist_releases", return_value=[]):
            out = list(discovery.iter_new_releases(db, token="tok", since_year=2024))
        # one (processed, total, None) tick is emitted for the artist
        assert out == [(1, 1, None)]

    def test_default_since_year_is_last_year(self):
        db = _db([_content("Artist")])
        captured = {}
        def fake(artist, token, year_from=None, **kw):
            captured["year_from"] = year_from
            return []
        with patch.object(discovery, "search_artist_releases", side_effect=fake):
            list(discovery.iter_new_releases(db, token="tok"))
        from datetime import datetime
        assert captured["year_from"] == datetime.now().year - 1

    def test_discogs_failure_is_swallowed(self):
        db = _db([_content("Artist")])
        with patch.object(discovery, "search_artist_releases", side_effect=RuntimeError("api down")):
            out = list(discovery.iter_new_releases(db, token="tok", since_year=2024))
        # artist produced nothing but the scan still completes
        assert out == [(1, 1, None)]


class TestSuggestNewReleases:
    def test_returns_flat_list(self):
        db = _db([_content("Artist")])
        with patch.object(discovery, "search_artist_releases",
                           return_value=[_release("New", 2025)]):
            result = discovery.suggest_new_releases(db, token="tok", since_year=2024)
        assert isinstance(result, list)
        assert result[0]["album"] == "New"
