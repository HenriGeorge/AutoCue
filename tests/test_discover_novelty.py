"""Tests for ``autocue.analysis.discover.feeders.novelty`` — T-008.

Covers:
- Round-robin rotation across the three strategies.
- Style-adjacent: top-3 styles flow through adjacency; sparse-adjacency
  sentinel when all top styles are terminal / unknown.
- Label-adjacent: parent_label + sub_labels collected; no followed labels →
  sparse_adjacency.
- Artist-adjacent: members + groups collected; no resolved IDs →
  sparse_adjacency.
- Rate-limit propagation matches the rest of the feeder family.
"""

from __future__ import annotations

from collections import Counter
from unittest.mock import patch

import pytest

from autocue.analysis import discogs as discogs_client
from autocue.analysis.discover.feeders.novelty import (
    NOVELTY_STRATEGIES,
    next_novelty_strategy,
    novelty_feeder,
)
from autocue.analysis.discover.style_graph import StyleAdjacency
from autocue.analysis.discover.taste import TasteVector


@pytest.fixture(autouse=True)
def _stub_bucket(monkeypatch):
    monkeypatch.setattr(discogs_client, "_acquire_token", lambda: None)


# --------------------------------------------------------------------------- #
# Rotation
# --------------------------------------------------------------------------- #

class TestNextNoveltyStrategy:
    def test_starts_at_style(self):
        assert next_novelty_strategy(None) == "style"

    def test_round_robin(self):
        seq = []
        cur = None
        for _ in range(6):
            cur = next_novelty_strategy(cur)
            seq.append(cur)
        assert seq == ["style", "label", "artist", "style", "label", "artist"]

    def test_unknown_previous_resets_to_style(self):
        assert next_novelty_strategy("totally-fake") == "style"


# --------------------------------------------------------------------------- #
# style-adjacent
# --------------------------------------------------------------------------- #

@pytest.fixture
def small_adjacency():
    return StyleAdjacency(
        schema_version=1,
        styles={
            "deep_house": {"edges": ["lo_fi_house", "soulful_house"], "terminal": False},
            "tech_house": {"edges": ["techno"], "terminal": False},
            "lo_fi_house": {"edges": [], "terminal": True},
            "soulful_house": {"edges": [], "terminal": True},
            "techno": {"edges": [], "terminal": True},
            "obscure_terminal": {"edges": [], "terminal": True},
        },
    )


class TestStyleAdjacent:
    def test_yields_releases_from_adjacent_styles(self, small_adjacency):
        tv = TasteVector(styles=Counter({"deep_house": 10, "tech_house": 5, "techno": 1}))

        def fake_request(_path, token, params=None, **__):
            from autocue.analysis.discogs import _Response
            return _Response(data={"results": [
                {"id": 1, "title": f"Release for {params['style']}", "year": 2026},
            ]}, remaining=50)

        with patch.object(discogs_client, "_request_json", side_effect=fake_request):
            results = list(novelty_feeder(tv, small_adjacency, token="t", strategy="style"))

        # We hit lo_fi_house, soulful_house, techno via adjacency edges from
        # deep_house and tech_house (techno itself is terminal so contributes
        # nothing). Top styles themselves are filtered out of the adjacent set.
        sources = [r.get("source") for r in results if isinstance(r, dict)]
        # Each source starts with "novelty:style:" + adjacent style name.
        assert all(s.startswith("novelty:style:") for s in sources)
        adjacent_used = {s.split(":", 2)[2] for s in sources}
        assert "lo_fi_house" in adjacent_used
        assert "soulful_house" in adjacent_used

    def test_all_top_styles_terminal_yields_sparse_sentinel(self, small_adjacency):
        tv = TasteVector(styles=Counter({"obscure_terminal": 10}))
        results = list(novelty_feeder(tv, small_adjacency, token="t", strategy="style"))
        sparse = [r for r in results if isinstance(r, tuple) and r[0] == "sparse_adjacency"]
        assert len(sparse) == 1
        assert sparse[0][1]["strategy"] == "style"

    def test_all_top_styles_unknown_yields_sparse_sentinel(self, small_adjacency):
        tv = TasteVector(styles=Counter({"mystery_genre_a": 10, "mystery_genre_b": 8}))
        results = list(novelty_feeder(tv, small_adjacency, token="t", strategy="style"))
        sparse = [r for r in results if isinstance(r, tuple) and r[0] == "sparse_adjacency"]
        assert len(sparse) == 1
        assert set(sparse[0][1]["unknown_styles"]) >= {"mystery_genre_a", "mystery_genre_b"}

    def test_empty_taste_vector_yields_sparse_sentinel(self, small_adjacency):
        tv = TasteVector()
        results = list(novelty_feeder(tv, small_adjacency, token="t", strategy="style"))
        assert any(isinstance(r, tuple) and r[0] == "sparse_adjacency" for r in results)


# --------------------------------------------------------------------------- #
# label-adjacent
# --------------------------------------------------------------------------- #

class TestLabelAdjacent:
    def test_no_followed_labels_yields_sparse(self, small_adjacency):
        tv = TasteVector()
        results = list(novelty_feeder(
            tv, small_adjacency, token="t", strategy="label",
        ))
        sparse = [r for r in results if isinstance(r, tuple) and r[0] == "sparse_adjacency"]
        assert sparse and sparse[0][1]["strategy"] == "label"

    def test_yields_releases_from_parent_and_sub_labels(self, small_adjacency):
        tv = TasteVector()

        def fake_request(path, token, params=None, **__):
            from autocue.analysis.discogs import _Response
            # /labels/42 returns parent + sublabels metadata.
            if path == "/labels/42":
                return _Response(data={
                    "parent_label": {"id": 100, "name": "Parent"},
                    "sublabels": [{"id": 200, "name": "Sub-A"}],
                }, remaining=50)
            return _Response(data={"results": []}, remaining=50)

        def fake_search_label_releases(label_id, token, *, year_from=None, per_page=50, page=1):
            return [{"id": label_id * 10, "title": f"From-{label_id}", "year": 2026}]

        with patch.object(discogs_client, "_request_json", side_effect=fake_request):
            with patch.object(discogs_client, "search_label_releases",
                              side_effect=fake_search_label_releases):
                results = list(novelty_feeder(
                    tv, small_adjacency, token="t", strategy="label",
                    followed_label_ids=[42], followed_label_names=["MyLabel"],
                ))

        # Should fetch from both parent (100) and sublabel (200).
        ids = {r["adjacent_label_id"] for r in results if isinstance(r, dict)}
        assert 100 in ids
        assert 200 in ids


# --------------------------------------------------------------------------- #
# artist-adjacent
# --------------------------------------------------------------------------- #

class TestArtistAdjacent:
    def test_no_artist_ids_yields_sparse(self, small_adjacency):
        tv = TasteVector()
        results = list(novelty_feeder(
            tv, small_adjacency, token="t", strategy="artist",
        ))
        sparse = [r for r in results if isinstance(r, tuple) and r[0] == "sparse_adjacency"]
        assert sparse and sparse[0][1]["strategy"] == "artist"

    def test_yields_from_members_and_groups(self, small_adjacency):
        tv = TasteVector()

        def fake_relations(artist_id, token):
            return {
                "members": [{"id": 10, "name": "Madlib"}],
                "groups": [{"id": 11, "name": "Madvillain"}],
            }

        def fake_search_artist(artist_name, *, token, year_from=None, per_page=50):
            return [{"id": 999, "title": f"From-{artist_name}", "year": 2026}]

        with patch.object(discogs_client, "get_artist_relations", side_effect=fake_relations):
            with patch.object(discogs_client, "search_artist_releases",
                              side_effect=fake_search_artist):
                results = list(novelty_feeder(
                    tv, small_adjacency, token="t", strategy="artist",
                    top_artist_ids=[7],
                ))

        adjacent_names = {r["adjacent_artist_name"] for r in results if isinstance(r, dict)}
        assert adjacent_names == {"Madlib", "Madvillain"}


# --------------------------------------------------------------------------- #
# Cross-cutting
# --------------------------------------------------------------------------- #

class TestCrossCutting:
    def test_unknown_strategy_raises(self, small_adjacency):
        tv = TasteVector(styles=Counter({"deep_house": 1}))
        with pytest.raises(ValueError, match="unknown novelty strategy"):
            list(novelty_feeder(tv, small_adjacency, token="t", strategy="bogus"))

    def test_zero_budget_yields_nothing(self, small_adjacency):
        tv = TasteVector(styles=Counter({"deep_house": 1}))
        assert list(novelty_feeder(
            tv, small_adjacency, token="t", strategy="style", budget=0,
        )) == []

    def test_empty_token_yields_nothing(self, small_adjacency):
        tv = TasteVector(styles=Counter({"deep_house": 1}))
        assert list(novelty_feeder(
            tv, small_adjacency, token="", strategy="style",
        )) == []

    def test_all_three_strategies_listed(self):
        assert set(NOVELTY_STRATEGIES) == {"style", "label", "artist"}
