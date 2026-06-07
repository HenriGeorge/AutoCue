"""Crash-safety regressions for run_scan (T-040+ follow-ups).

Two production incidents surfaced on the first live scan of the v2 stack:

1.  Ranker's ``_recency`` did ``year <= 0`` directly, but Discogs sometimes
    returns ``year`` as a string. That raised ``TypeError`` that aborted
    the whole scan mid-flight.
2.  The orchestrator only caught ``Discogs429`` in its big try/except; any
    other exception bypassed ``store.finish_scan(...)`` and left the scan
    row with ``finished_at = NULL`` — wedging the concurrent-scan lock so
    every subsequent ``/api/discover/feed`` POST returned 409 until the
    server was restarted.

These tests assert that any exception in the feeder loop, the
assemble_feed stage, or commit_pending_scan still closes the scan row.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from autocue.analysis import discogs as discogs_client
from autocue.analysis.discover.ranker import _recency
from autocue.analysis.discover.scan_orchestrator import ScanConfig, run_scan


# ============================================================ year coerce

class TestRecencyYearCoercion:
    def test_string_year_does_not_raise(self):
        """The original crash signature: ``'<=' not supported between str and int``."""
        # No today provided → uses date.today(); for "2024" we just want non-zero.
        out = _recency("2024", today=None)
        assert isinstance(out, float)

    def test_non_numeric_string_is_treated_as_unknown(self):
        assert _recency("not-a-year", today=None) == 0.0
        assert _recency("", today=None) == 0.0

    def test_none_is_unknown(self):
        assert _recency(None, today=None) == 0.0

    def test_zero_or_negative_is_unknown(self):
        assert _recency(0, today=None) == 0.0
        assert _recency(-1, today=None) == 0.0
        assert _recency("0", today=None) == 0.0


# ============================================================ scan-lock leak

def _collect(stream):
    by_kind = {}
    last_payload = None
    for kind, payload in stream:
        by_kind.setdefault(kind, []).append(payload)
        last_payload = payload
    return by_kind, last_payload


@pytest.fixture()
def store(tmp_path):
    from autocue.analysis.discover.store import DiscoverStore
    s = DiscoverStore(db_path=str(tmp_path / "discover.db"))
    yield s
    s.close()


@pytest.fixture()
def basic_taste():
    from collections import Counter
    from autocue.analysis.discover.taste import TasteVector
    return TasteVector(
        artists=Counter({"Madvillain": 5, "Larry Heard": 3}),
        labels=Counter({"Stones Throw": 4}),
        styles=Counter({"Hip Hop": 7, "House": 5}),
        track_count=10,
    )


@pytest.fixture()
def simple_adjacency():
    from autocue.analysis.discover.style_graph import StyleAdjacency
    return StyleAdjacency(
        schema_version=1,
        styles={
            "deep_house": {"edges": ["lo_fi_house"], "terminal": False},
            "lo_fi_house": {"edges": [], "terminal": True},
        },
    )


class TestScanLockNeverLeaks:
    """Whatever crashes inside run_scan, the scan row's finished_at MUST be
    stamped so the concurrent-scan lock never wedges."""

    def test_feeder_loop_crash_still_closes_scan(self, store, basic_taste, simple_adjacency):
        """A TypeError thrown from the ranker (or anywhere in the feeder loop)
        used to skip finish_scan. With the fix, finish_scan still runs."""
        cfg = ScanConfig(feeders=["artist"], top_n=10)

        def crashing_releases(artist_name, *, token, year_from=None, per_page=50):
            # Return ONE release; the orchestrator will try to score it.
            return [{"id": 1, "title": "Madvillainy", "artist": artist_name, "year": 2004}]

        with patch.object(discogs_client, "search_artist_releases", side_effect=crashing_releases), \
             patch("autocue.analysis.discover.scan_orchestrator.score_release",
                   side_effect=RuntimeError("simulated ranker crash")):
            by_kind, _ = _collect(run_scan(
                store, basic_taste, simple_adjacency, token="t", config=cfg,
            ))

        # An ('error', ...) event was emitted.
        assert "error" in by_kind, "orchestrator must yield ('error', ...) on crash"
        # The scan row's finished_at is NOT NULL — lock released.
        row = store.conn.execute(
            "SELECT finished_at, status FROM scans ORDER BY scan_id DESC LIMIT 1"
        ).fetchone()
        assert row["finished_at"] is not None, "scan-lock leak: finished_at still NULL"
        assert row["status"] == "error"

    def test_assemble_feed_crash_still_closes_scan(self, store, basic_taste, simple_adjacency):
        cfg = ScanConfig(feeders=["artist"], top_n=10)

        def releases(artist_name, *, token, year_from=None, per_page=50):
            return [{"id": 1, "title": "X", "artist": artist_name, "year": 2024}]

        with patch.object(discogs_client, "search_artist_releases", side_effect=releases), \
             patch("autocue.analysis.discover.scan_orchestrator.assemble_feed",
                   side_effect=RuntimeError("simulated assemble crash")):
            by_kind, _ = _collect(run_scan(
                store, basic_taste, simple_adjacency, token="t", config=cfg,
            ))

        # done event still fires (we fall through to the yield), and the row is closed.
        row = store.conn.execute(
            "SELECT finished_at, status FROM scans ORDER BY scan_id DESC LIMIT 1"
        ).fetchone()
        assert row["finished_at"] is not None
        assert row["status"] == "error"

    def test_concurrent_scan_lock_releases_after_crash(self, store, basic_taste, simple_adjacency):
        """The 'A Discover scan is already running' 409 was a follow-on of the
        leak: scan_1 crashed, scan_2 hit the lock and was rejected. With the
        fix, scan_2 can start cleanly."""
        cfg = ScanConfig(feeders=["artist"], top_n=10)

        def releases(artist_name, *, token, year_from=None, per_page=50):
            return [{"id": 1, "title": "X", "artist": artist_name, "year": 2024}]

        # First scan crashes mid-flight.
        with patch.object(discogs_client, "search_artist_releases", side_effect=releases), \
             patch("autocue.analysis.discover.scan_orchestrator.score_release",
                   side_effect=RuntimeError("boom")):
            list(run_scan(store, basic_taste, simple_adjacency, token="t", config=cfg))

        # Now we should be able to start a fresh scan.
        assert not store.is_scan_running(), "scan-lock should be released after crashed scan"


# ============================================================ library dedup

class TestLibraryAlbumDedup:
    """Regression: the orchestrator used to compute lib_key as
    f"{artist}|||{title}" but library_album_set returns plain album names
    (no separator, no artist prefix). The check never matched, so every
    owned album leaked into Discover."""

    def test_owned_album_is_filtered_out(self, store, basic_taste, simple_adjacency):
        cfg = ScanConfig(feeders=["artist"], top_n=10)

        def releases(artist_name, *, token, year_from=None, per_page=50):
            return [{
                "id": 1, "title": f"{artist_name} - Madvillainy",
                "artist": artist_name, "album": "Madvillainy", "year": 2004,
            }]

        with patch.object(discogs_client, "search_artist_releases", side_effect=releases):
            by_kind, _ = _collect(run_scan(
                store, basic_taste, simple_adjacency, token="t", config=cfg,
                library_album_set={"madvillainy"},  # the REAL shape
            ))

        # The release should have been filtered as already-owned.
        feed_titles = {r["release"].get("album") for r in by_kind.get("release", [])}
        assert "Madvillainy" not in feed_titles

    def test_dedup_falls_back_to_title_when_album_missing(self, store, basic_taste, simple_adjacency):
        cfg = ScanConfig(feeders=["artist"], top_n=10)

        def releases(artist_name, *, token, year_from=None, per_page=50):
            # No `album` key — orchestrator must derive it from `title`.
            return [{"id": 1, "title": f"{artist_name} - Donuts", "artist": artist_name, "year": 2006}]

        with patch.object(discogs_client, "search_artist_releases", side_effect=releases):
            by_kind, _ = _collect(run_scan(
                store, basic_taste, simple_adjacency, token="t", config=cfg,
                library_album_set={"donuts"},
            ))

        feed_titles = {r["release"].get("title") for r in by_kind.get("release", [])}
        assert not any("Donuts" in t for t in feed_titles), \
            "title-fallback dedup should have caught 'Donuts'"
