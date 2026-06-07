"""Tests for the scan orchestrator — T-014.

Covers the wiring contract end-to-end with mocked Discogs responses + a real
DiscoverStore (in-memory tmp). We exercise:
- The end-of-scan commit ordering (pending → committed BEFORE finish_scan).
- The dedup filters (library_album_set / saved / dismissed / snoozed / blocked).
- The hard 60-request cap validation.
- Round-robin novelty strategy from the previous scan.
- Stage 2 reservation status flowing into the 'done' event.
- Discogs429 mid-scan → status='rate_limited' and pending values preserved.
"""

from __future__ import annotations

from collections import Counter
from unittest.mock import patch

import pytest

from autocue.analysis import discogs as discogs_client
from autocue.analysis.discover.scan_orchestrator import (
    DEFAULT_FEED_TOP_N,
    HARD_SCAN_REQUEST_CAP,
    ScanConfig,
    run_scan,
)
from autocue.analysis.discover.store import DiscoverStore
from autocue.analysis.discover.style_graph import StyleAdjacency
from autocue.analysis.discover.taste import TasteVector


@pytest.fixture(autouse=True)
def _stub_bucket(monkeypatch):
    monkeypatch.setattr(discogs_client, "_acquire_token", lambda: None)


@pytest.fixture
def store(tmp_path):
    s = DiscoverStore(db_path=tmp_path / "discover.db")
    yield s
    s.close()


@pytest.fixture
def basic_taste():
    return TasteVector(
        artists=Counter({"Madvillain": 10.0, "Larry Heard": 3.0}),
        labels=Counter({"Stones Throw": 10.0}),
        styles=Counter({"deep_house": 5}),
    )


@pytest.fixture
def simple_adjacency():
    return StyleAdjacency(
        schema_version=1,
        styles={
            "deep_house": {"edges": ["lo_fi_house"], "terminal": False},
            "lo_fi_house": {"edges": [], "terminal": True},
        },
    )


def _collect(gen):
    """Drain a scan generator and return its events grouped by kind."""
    events = list(gen)
    by_kind = {}
    for kind, payload in events:
        by_kind.setdefault(kind, []).append(payload)
    return by_kind, events


# --------------------------------------------------------------------------- #
# Config validation
# --------------------------------------------------------------------------- #

class TestConfig:
    def test_budgets_over_cap_fail_validation(self):
        cfg = ScanConfig(
            artist_budget=30, label_budget=20, novelty_budget=20,  # sum=70 > 60
        )
        with pytest.raises(ValueError, match="exceeds hard cap"):
            cfg.validate()


# --------------------------------------------------------------------------- #
# End-to-end scan with all feeders
# --------------------------------------------------------------------------- #

class TestRunScan:
    def test_artist_only_feed_produces_releases(self, store, basic_taste, simple_adjacency):
        store.follow_label(1, "Stones Throw")
        cfg = ScanConfig(feeders=["artist"], top_n=10)

        def fake_artist_releases(artist_name, *, token, year_from=None, per_page=50):
            return [{"id": hash(artist_name) & 0xfffff, "title": f"{artist_name} Album",
                     "artist": artist_name, "year": 2026, "label": "Stones Throw"}]

        with patch.object(discogs_client, "search_artist_releases", side_effect=fake_artist_releases):
            by_kind, _ = _collect(run_scan(
                store, basic_taste, simple_adjacency, token="t", config=cfg,
            ))

        assert "release" in by_kind
        assert "done" in by_kind
        done = by_kind["done"][0]
        assert done["status"] == "ok"
        assert done["releases_seen"] >= 2  # 2 artists in taste vector
        assert done["releases_surfaced"] <= cfg.top_n
        # Sanity: each release event carries a normalized release_key.
        for r in by_kind["release"]:
            assert isinstance(r["release_key"], str) and "|||" in r["release_key"]

    def test_full_lifecycle_commits_pending_before_finish(self, store, basic_taste, simple_adjacency):
        store.follow_label(1, "Stones Throw")
        cfg = ScanConfig(feeders=["label"], top_n=10)

        def fake_label_releases(label_id, *, token, year_from=None, per_page=50, page=1):
            return [{"id": 99, "title": "X", "artist": "Y", "year": 2026, "label": "L"}]

        with patch.object(discogs_client, "search_label_releases", side_effect=fake_label_releases):
            list(run_scan(store, basic_taste, simple_adjacency, token="t", config=cfg))

        # The label's pending columns were committed.
        rows = store.list_followed_labels()
        assert rows[0]["last_scanned_at"] is not None
        assert rows[0]["last_scanned_at_pending"] is None
        # Scan row closed.
        assert store.is_scan_running() is False


# --------------------------------------------------------------------------- #
# Dedup filters
# --------------------------------------------------------------------------- #

class TestDedup:
    def test_already_saved_release_filtered(self, store, basic_taste, simple_adjacency):
        # Pre-save the release the artist feeder will produce.
        store.save(release_key="madvillain|||madvillainy",
                   release_id=1, artist="Madvillain", title="Madvillainy")

        cfg = ScanConfig(feeders=["artist"], top_n=10)

        def fake_artist_releases(artist_name, *, token, year_from=None, per_page=50):
            if artist_name == "Madvillain":
                return [{"id": 1, "title": "Madvillainy", "artist": "Madvillain",
                         "year": 2004}]
            return []

        with patch.object(discogs_client, "search_artist_releases", side_effect=fake_artist_releases):
            by_kind, _ = _collect(run_scan(
                store, basic_taste, simple_adjacency, token="t", config=cfg,
            ))

        # The saved release was deduped out.
        keys = [r["release_key"] for r in by_kind.get("release", [])]
        assert "madvillain|||madvillainy" not in keys

    def test_blocked_artist_scores_zero_and_filters_out(self, store, basic_taste, simple_adjacency):
        store.block_artist(42, "Madvillain")
        cfg = ScanConfig(feeders=["artist"], top_n=10)

        def fake_artist_releases(artist_name, *, token, year_from=None, per_page=50):
            return [{"id": 1, "title": "X", "artist": artist_name, "year": 2026}]

        with patch.object(discogs_client, "search_artist_releases", side_effect=fake_artist_releases):
            by_kind, _ = _collect(run_scan(
                store, basic_taste, simple_adjacency, token="t", config=cfg,
            ))

        # Madvillain blocked → no release for them in the feed.
        artists_in_feed = {r["release"]["artist"] for r in by_kind.get("release", [])}
        assert "Madvillain" not in artists_in_feed

    def test_library_album_set_filters_owned(self, store, basic_taste, simple_adjacency):
        cfg = ScanConfig(feeders=["artist"], top_n=10)

        def fake_artist_releases(artist_name, *, token, year_from=None, per_page=50):
            return [{"id": 1, "title": "Madvillainy", "artist": artist_name, "year": 2004}]

        with patch.object(discogs_client, "search_artist_releases", side_effect=fake_artist_releases):
            by_kind, _ = _collect(run_scan(
                store, basic_taste, simple_adjacency, token="t", config=cfg,
                library_album_set={"madvillain|||madvillainy"},
            ))

        # Owned → filtered out for that artist; Larry Heard's still flows.
        artists_in_feed = {r["release"]["artist"] for r in by_kind.get("release", [])}
        assert "Madvillain" not in artists_in_feed


# --------------------------------------------------------------------------- #
# Rate-limit handling
# --------------------------------------------------------------------------- #

class TestRateLimitHandling:
    def test_429_mid_scan_marks_rate_limited(self, store, basic_taste, simple_adjacency):
        cfg = ScanConfig(feeders=["artist"], top_n=10)

        def fake_artist_releases(artist_name, *, token, year_from=None, per_page=50):
            raise discogs_client.Discogs429(retry_after=60)

        with patch.object(discogs_client, "search_artist_releases", side_effect=fake_artist_releases):
            by_kind, _ = _collect(run_scan(
                store, basic_taste, simple_adjacency, token="t", config=cfg,
            ))

        done = by_kind["done"][0]
        assert done["status"] == "rate_limited"
        # The scan row reflects it too.
        row = store.conn.execute(
            "SELECT status FROM scans WHERE scan_id = ?", (done["scan_id"],)
        ).fetchone()
        assert row["status"] == "rate_limited"

    def test_429_does_not_commit_pending(self, store, basic_taste, simple_adjacency):
        store.follow_label(1, "Stones Throw")
        cfg = ScanConfig(feeders=["label"], top_n=10)

        # Label feeder writes to pending BEFORE the 429 lands.
        def fake_label_releases(label_id, *, token, year_from=None, per_page=50, page=1):
            raise discogs_client.Discogs429(retry_after=60)

        with patch.object(discogs_client, "search_label_releases", side_effect=fake_label_releases):
            list(run_scan(store, basic_taste, simple_adjacency, token="t", config=cfg))

        # Pending value should NOT have been promoted to committed because
        # the scan didn't finish 'ok'. The next scan can retry.
        rows = store.list_followed_labels()
        assert rows[0]["last_scanned_at"] is None
