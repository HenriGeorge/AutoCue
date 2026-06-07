"""Focused tests for autocue.analysis.discogs cache behavior.

The Discogs module is also exercised indirectly via tests/test_discovery.py
and tests/test_serve_routes.py, but those mock at a higher level. These tests
mock urllib.request.urlopen directly to assert cache semantics.
"""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch

from autocue.analysis import discogs


def _mock_urlopen(payload: dict):
    """Return a context-manager-style mock for urllib.request.urlopen."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(payload).encode()
    cm = MagicMock()
    cm.__enter__.return_value = mock_resp
    cm.__exit__.return_value = False
    return cm


class TestSearchStylesCache:
    def setup_method(self):
        discogs._cache.clear()
        discogs._releases_cache.clear()

    def test_non_empty_result_is_cached(self):
        payload = {"results": [{"style": ["Tech House", "Disco"]}]}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)) as m:
            r1 = discogs.search_styles("Daft Punk", "Around the World", "TOKEN")
            r2 = discogs.search_styles("Daft Punk", "Around the World", "TOKEN")
        assert r1 == ["Tech House", "Disco"]
        assert r2 == ["Tech House", "Disco"]
        # Second call hits the cache — only one network round-trip
        assert m.call_count == 1

    def test_empty_result_is_NOT_cached(self):
        """Bug fix: empty 200 OK responses must not poison the cache."""
        payload = {"results": []}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)) as m:
            r1 = discogs.search_styles("Unknown Artist", "Unknown Title", "TOKEN")
            r2 = discogs.search_styles("Unknown Artist", "Unknown Title", "TOKEN")
        assert r1 == []
        assert r2 == []
        # Second call retried — caller can fix typo or wait for Discogs to catalogue
        assert m.call_count == 2

    def test_results_with_no_styles_NOT_cached(self):
        """Results array present, but no style fields → empty deduped list → don't cache."""
        payload = {"results": [{"title": "A — B", "style": []}]}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)) as m:
            r1 = discogs.search_styles("X", "Y", "TOKEN")
            r2 = discogs.search_styles("X", "Y", "TOKEN")
        assert r1 == []
        assert r2 == []
        assert m.call_count == 2


class TestSearchArtistReleasesCache:
    def setup_method(self):
        discogs._cache.clear()
        discogs._releases_cache.clear()

    def test_non_empty_releases_cached(self):
        payload = {
            "results": [
                {"title": "Daft Punk - Discovery", "year": 2001, "style": [], "format": ["CD"]},
            ]
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)) as m:
            r1 = discogs.search_artist_releases("Daft Punk", "TOKEN")
            r2 = discogs.search_artist_releases("Daft Punk", "TOKEN")
        assert len(r1) == 1
        assert len(r2) == 1
        assert m.call_count == 1

    def test_empty_releases_NOT_cached(self):
        payload = {"results": []}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)) as m:
            r1 = discogs.search_artist_releases("Unknown Artist", "TOKEN")
            r2 = discogs.search_artist_releases("Unknown Artist", "TOKEN")
        assert r1 == []
        assert r2 == []
        assert m.call_count == 2

    def test_all_results_filtered_out_by_year_NOT_cached(self):
        """Results present but all year < year_from → empty after filter → don't cache."""
        payload = {
            "results": [
                {"title": "Old Album", "year": 1995, "style": [], "format": []},
            ]
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)) as m:
            r1 = discogs.search_artist_releases("X", "TOKEN", year_from=2020)
            r2 = discogs.search_artist_releases("X", "TOKEN", year_from=2020)
        assert r1 == []
        assert r2 == []
        assert m.call_count == 2
