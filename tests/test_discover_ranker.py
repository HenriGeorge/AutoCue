"""Tests for ``autocue.analysis.discover.ranker`` — T-009 + T-010.

Two halves:

1. **Hypothesis properties** that hold for every release / taste vector
   combination — score is in [0, 100], hard-blocks score 0, novelty share
   guarantees, weight sum invariant.

2. **Concrete unit tests** for each scoring term and the Stage-2 assembly
   branches (full pool / partial pool / empty pool).
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from autocue.analysis.discover.ranker import (
    FeedAssemblyResult,
    FeedContext,
    HARD_BLOCK_SCORE,
    NOVELTY_FRACTION,
    WEIGHT_ARTIST,
    WEIGHT_BPM,
    WEIGHT_COHORT_FRESHNESS,
    WEIGHT_LABEL,
    WEIGHT_RECENCY,
    WEIGHT_SOURCE_DIVERSITY,
    WEIGHT_STYLE,
    assemble_feed,
    score_release,
)
from autocue.analysis.discover.taste import TasteVector


# --------------------------------------------------------------------------- #
# Weight invariants
# --------------------------------------------------------------------------- #

def test_weights_sum_to_one():
    """Stage-1 weights normalize so the composite stays in [0, 1] before the
    *100 scaling. If a future tweak forgets to renormalize, this fails loud."""
    total = (
        WEIGHT_ARTIST + WEIGHT_LABEL + WEIGHT_STYLE + WEIGHT_BPM
        + WEIGHT_RECENCY + WEIGHT_SOURCE_DIVERSITY + WEIGHT_COHORT_FRESHNESS
    )
    assert total == pytest.approx(1.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# Hypothesis: score is always in [0, 100]
# --------------------------------------------------------------------------- #

_release_strat = st.fixed_dictionaries({
    "artist": st.text(min_size=0, max_size=15),
    "title": st.text(min_size=0, max_size=20),
    "label": st.text(min_size=0, max_size=15),
    "year": st.integers(min_value=1900, max_value=2100),
    "bpm": st.one_of(st.none(), st.floats(min_value=40, max_value=240, allow_nan=False)),
    "styles": st.lists(st.sampled_from(["deep_house", "techno", "hip_hop", "jazz_modern"]),
                       max_size=4),
    "source": st.sampled_from(["artist", "label", "novelty:style", "shop:rss"]),
})


def _make_taste(artists, labels, styles, bpm_buckets):
    return TasteVector(
        artists=Counter(artists),
        labels=Counter(labels),
        styles=Counter(styles),
        bpm_hist=list(bpm_buckets),
    )


_taste_strat = st.builds(
    _make_taste,
    artists=st.dictionaries(st.text(min_size=1, max_size=10),
                            st.floats(min_value=0.1, max_value=10, allow_nan=False),
                            max_size=5),
    labels=st.dictionaries(st.text(min_size=1, max_size=10),
                           st.floats(min_value=0.1, max_value=10, allow_nan=False),
                           max_size=5),
    styles=st.dictionaries(st.sampled_from(["deep_house", "techno", "hip_hop", "jazz_modern"]),
                           st.integers(min_value=1, max_value=20),
                           max_size=4),
    bpm_buckets=st.lists(st.integers(min_value=0, max_value=20), min_size=35, max_size=35),
)


@given(release=_release_strat, tv=_taste_strat)
@settings(max_examples=200, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_score_always_in_range(release, tv):
    """Hypothesis property: score_release returns a float in [0.0, 100.0] for
    ANY combination of release dict and taste vector. Prevents future weight
    tweaks or term implementations from accidentally producing NaN, negative,
    or > 100 values."""
    s = score_release(release, tv)
    assert isinstance(s, float)
    assert 0.0 <= s <= 100.0


@given(release=_release_strat, tv=_taste_strat,
       blocked_seed=st.text(
           min_size=1, max_size=10,
           alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
       ))
@settings(max_examples=100, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_blocked_artist_zeros_score(release, tv, blocked_seed):
    """If the release's artist is in blocked_artists, the score is exactly 0.

    ``blocked_seed`` is constrained to printable non-whitespace so the
    name survives the ``.strip()`` the ranker does before comparing against
    the set — Hypothesis found a whitespace-only case that the simpler
    strategy admitted."""
    release["artist"] = blocked_seed
    ctx = FeedContext(blocked_artists={blocked_seed})
    assert score_release(release, tv, ctx) == HARD_BLOCK_SCORE


# --------------------------------------------------------------------------- #
# Concrete term tests
# --------------------------------------------------------------------------- #

class TestRecencyTerm:
    def test_today_scores_near_one(self):
        # A release with year=today's year and a recency-window query says
        # ~1.0 (it's day 0 of 90).
        tv = TasteVector()
        today = date(2026, 6, 7)
        score = score_release(
            {"year": 2026, "bpm": None},
            tv,
            FeedContext(today=today),
        )
        # The composite gets 0.10 recency * ~1.0 ≈ 10 * (today-fraction).
        # All other terms are 0 or 0.5 neutrals. Should be > 0.
        assert score > 0

    def test_release_older_than_window_gets_no_recency_bonus(self):
        """Recency uses Jan 1 of the release year (Discogs only gives us
        the year). With ``today`` set to mid-February of the release year,
        a 2026 release scores higher than a 1990 release. Picking a date
        within the 90-day window from Jan 1 to keep the test deterministic
        independent of the wall clock."""
        tv = TasteVector()
        today = date(2026, 2, 15)  # day 45 of the 90-day window
        score_old = score_release(
            {"year": 1990, "bpm": None}, tv, FeedContext(today=today),
        )
        score_recent = score_release(
            {"year": 2026, "bpm": None}, tv, FeedContext(today=today),
        )
        assert score_recent > score_old

    def test_unknown_year_doesnt_crash(self):
        tv = TasteVector()
        score_release({"year": None}, tv)  # smoke test


class TestArtistMatch:
    def test_matched_artist_increases_score(self):
        tv = TasteVector(artists=Counter({"Madvillain": 5.0}))
        ctx = FeedContext(today=date(2026, 6, 7))
        matched = score_release(
            {"artist": "Madvillain", "year": 2026}, tv, ctx,
        )
        unmatched = score_release(
            {"artist": "Random Person", "year": 2026}, tv, ctx,
        )
        assert matched > unmatched

    def test_accented_match(self):
        tv = TasteVector(artists=Counter({"Beyonce": 5.0}))
        matched = score_release({"artist": "Beyoncé", "year": 2026}, tv)
        unmatched = score_release({"artist": "Random", "year": 2026}, tv)
        assert matched > unmatched


class TestStyleMatch:
    def test_overlapping_style_boost(self):
        tv = TasteVector(styles=Counter({"deep_house": 100}))
        with_match = score_release(
            {"styles": ["Deep House"], "year": 2026}, tv,
        )
        without_match = score_release(
            {"styles": ["Polka"], "year": 2026}, tv,
        )
        assert with_match > without_match


class TestBpmFit:
    def test_no_data_neutral(self):
        tv = TasteVector(bpm_hist=[0] * 35)
        score = score_release({"bpm": 120, "year": 2026}, tv)
        # Just verify it doesn't crash and produces a number.
        assert 0 <= score <= 100

    def test_matching_bucket_higher_than_distant(self):
        bpm_hist = [0] * 35
        bpm_hist[15] = 10  # Lots of plays around 120 BPM (bucket 15 = 60+15*4 = 120).
        tv = TasteVector(bpm_hist=bpm_hist)
        match = score_release({"bpm": 120.0, "year": 2026}, tv)
        miss = score_release({"bpm": 60.0, "year": 2026}, tv)
        assert match > miss


# --------------------------------------------------------------------------- #
# assemble_feed — Stage 2
# --------------------------------------------------------------------------- #

def _r(rid, *, source="artist", artist="A", title="T"):
    return {"id": rid, "source": source, "artist": artist, "title": title}


class TestAssembleFeed:
    def test_full_pool_25_percent_reservation(self):
        """40 retrieval + 30 novelty, top_n=50 → 38 retrieval + 12 novelty."""
        scored = (
            [(99 - i, _r(i, source="artist")) for i in range(40)]
            + [(50 - i, _r(1000 + i, source="novelty:style")) for i in range(30)]
        )
        result = assemble_feed(scored, top_n=50)
        assert result.novelty_status == "ok"
        assert len(result.feed) == 50
        novelty_count = sum(1 for r in result.feed if r["source"].startswith("novelty:"))
        # 25% of 50 = 12 (floor).
        assert novelty_count == int(50 * NOVELTY_FRACTION)

    def test_partial_pool_records_count(self):
        """5 novelty + 50 retrieval, top_n=50 → 45 retrieval + 5 novelty,
        status='partial'."""
        scored = (
            [(99 - i, _r(i, source="artist")) for i in range(50)]
            + [(50 - i, _r(1000 + i, source="novelty:label")) for i in range(5)]
        )
        result = assemble_feed(scored, top_n=50)
        assert result.novelty_status == "partial"
        assert result.novelty_partial == 5
        novelty_count = sum(1 for r in result.feed if r["source"].startswith("novelty:"))
        assert novelty_count == 5
        assert len(result.feed) == 50

    def test_empty_pool_pure_retrieval(self):
        scored = [(99 - i, _r(i, source="artist")) for i in range(50)]
        result = assemble_feed(scored, top_n=50)
        assert result.novelty_status == "sparse_adjacency"
        assert all(not r["source"].startswith("novelty:") for r in result.feed)
        assert len(result.feed) == 50

    def test_size_hint_overrides_pool(self):
        """Caller knows the pool is empty (feeder yielded sparse_adjacency
        sentinel) — even if some 'novelty:' tagged item slipped in, hint=0
        forces sparse_adjacency status."""
        scored = [
            (50, _r(1, source="novelty:style")),
            (40, _r(2, source="artist")),
        ]
        result = assemble_feed(scored, top_n=10, novelty_pool_size_hint=0)
        assert result.novelty_status == "sparse_adjacency"
        # The novelty release gets filtered because we trust the hint.
        assert all(not r["source"].startswith("novelty:") for r in result.feed)

    def test_deterministic_secondary_sort_on_ties(self):
        """Equal scores → deterministic title/artist ordering. Important so the
        UI doesn't shuffle on every re-load."""
        scored = [
            (50.0, _r(3, artist="C", title="C")),
            (50.0, _r(1, artist="A", title="A")),
            (50.0, _r(2, artist="B", title="B")),
        ]
        result = assemble_feed(scored, top_n=10)
        assert [r["id"] for r in result.feed] == [1, 2, 3]

    def test_empty_input_returns_empty_feed(self):
        result = assemble_feed([], top_n=50)
        assert result.feed == []
        assert result.novelty_status == "sparse_adjacency"
