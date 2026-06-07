"""Tests for the T-012 ``DiscoverStore`` extensions (is_*/list_*, block-list
CRUD, release_details + youtube_results caches, saves_correlated_to_scan).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from autocue.analysis.discover.store import DiscoverStore


@pytest.fixture
def store(tmp_path):
    s = DiscoverStore(db_path=tmp_path / "discover.db")
    yield s
    s.close()


# ── Predicates + listings ─────────────────────────────────────────────────

class TestStatePredicates:
    def test_is_saved_and_unsave(self, store):
        store.save(release_key="k1", release_id=1, artist="A", title="T")
        assert store.is_saved("k1") is True
        store.unsave("k1")
        assert store.is_saved("k1") is False

    def test_is_dismissed_round_trip(self, store):
        store.dismiss(release_key="k1", artist="A", title="T")
        assert store.is_dismissed("k1") is True
        store.undismiss("k1")
        assert store.is_dismissed("k1") is False

    def test_is_snoozed_respects_until_date(self, store):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        store.snooze(release_key="future", until_date=future, artist="A", title="T")
        store.snooze(release_key="past", until_date=past, artist="A", title="T")

        assert store.is_snoozed("future") is True
        assert store.is_snoozed("past") is False  # expired → not snoozed for feed purposes

    def test_is_downloaded(self, store):
        store.record_download(release_key="k1", file_paths=["/p/x.flac"])
        assert store.is_downloaded("k1") is True

    def test_list_saved_returns_rows_newest_first(self, store):
        store.save(release_key="old", release_id=1, artist="A", title="T",
                   saved_at="2026-01-01T00:00:00+00:00")
        store.save(release_key="new", release_id=2, artist="A", title="T",
                   saved_at="2026-06-01T00:00:00+00:00")
        rows = store.list_saved()
        assert [r["release_key"] for r in rows] == ["new", "old"]

    def test_list_downloaded_decodes_file_paths_json(self, store):
        store.record_download(release_key="k", file_paths=["/a.flac", "/b.flac"])
        rows = store.list_downloaded()
        assert rows[0]["file_paths"] == ["/a.flac", "/b.flac"]

    def test_list_snoozed_filters_expired_by_default(self, store):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        store.snooze(release_key="future", until_date=future, artist="A", title="T")
        store.snooze(release_key="past", until_date=past, artist="A", title="T")

        active = store.list_snoozed()
        assert {r["release_key"] for r in active} == {"future"}

        with_resurfaced = store.list_snoozed(include_resurfaced=True)
        assert {r["release_key"] for r in with_resurfaced} == {"future", "past"}


# ── Block-list CRUD ───────────────────────────────────────────────────────

class TestBlockLists:
    def test_block_artist_round_trip(self, store):
        store.block_artist(42, "Anjunabeats")
        rows = store.list_blocked_artists()
        assert rows[0]["discogs_artist_id"] == 42
        assert rows[0]["name"] == "Anjunabeats"
        assert store.blocked_artist_names() == {"Anjunabeats"}
        store.unblock_artist(42)
        assert store.list_blocked_artists() == []

    def test_block_label_round_trip(self, store):
        store.block_label(99, "BoringLabel")
        assert store.blocked_label_names() == {"BoringLabel"}
        store.unblock_label(99)
        assert store.list_blocked_labels() == []


# ── Caches ────────────────────────────────────────────────────────────────

class TestCaches:
    def test_release_details_cache_round_trip(self, store):
        payload = {"id": 1, "title": "X", "master_id": 99, "tracklist": []}
        store.record_release_detail(1, payload)
        cached = store.get_release_detail(1)
        assert cached == payload

    def test_release_details_cache_expires(self, store):
        # Insert with a fetched_at deep in the past so expiry < now.
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        store.record_release_detail(1, {"id": 1}, fetched_at=old)
        assert store.get_release_detail(1) is None

    def test_youtube_results_round_trip(self, store):
        results = [{"videoId": "abc"}, {"videoId": "def"}]
        store.record_youtube_results("rk1", 0, results)
        cached = store.get_youtube_results("rk1", 0)
        assert cached == results
        # Missing track index returns None.
        assert store.get_youtube_results("rk1", 5) is None


# ── Saves correlation ─────────────────────────────────────────────────────

class TestSavesCorrelation:
    def test_save_within_window_counted(self, store):
        scan_id = store.start_scan(feeders=["artist"], started_at="2026-06-07T10:00:00+00:00")
        store.finish_scan(scan_id, status="ok", finished_at="2026-06-07T10:01:00+00:00")
        # User saves 5 minutes after scan finish — within the 30-minute tail.
        store.save(release_key="k", release_id=1, artist="A", title="T",
                   saved_at="2026-06-07T10:06:00+00:00")
        assert store.saves_correlated_to_scan(scan_id) == 1

    def test_save_after_tail_not_counted(self, store):
        scan_id = store.start_scan(feeders=["artist"], started_at="2026-06-07T10:00:00+00:00")
        store.finish_scan(scan_id, status="ok", finished_at="2026-06-07T10:01:00+00:00")
        # 45 minutes after scan finish — past the 30-minute tail.
        store.save(release_key="k", release_id=1, artist="A", title="T",
                   saved_at="2026-06-07T10:46:00+00:00")
        assert store.saves_correlated_to_scan(scan_id) == 0

    def test_later_scan_steals_attribution(self, store):
        """When a later scan opens before the save lands, the LATER scan
        gets the attribution per PRD §13 tiebreaker."""
        scan_a = store.start_scan(feeders=["artist"], started_at="2026-06-07T10:00:00+00:00")
        store.finish_scan(scan_a, status="ok", finished_at="2026-06-07T10:01:00+00:00")
        scan_b = store.start_scan(feeders=["artist"], started_at="2026-06-07T10:05:00+00:00")
        store.finish_scan(scan_b, status="ok", finished_at="2026-06-07T10:06:00+00:00")
        # Save lands 10 minutes after scan_a, within both windows. scan_b
        # claims it (most recent started_at preceding the save).
        store.save(release_key="k", release_id=1, artist="A", title="T",
                   saved_at="2026-06-07T10:10:00+00:00")
        assert store.saves_correlated_to_scan(scan_a) == 0
        assert store.saves_correlated_to_scan(scan_b) == 1

    def test_unfinished_scan_returns_zero(self, store):
        scan_id = store.start_scan(feeders=["artist"])
        store.save(release_key="k", release_id=1, artist="A", title="T")
        # No finish_scan yet → returns 0 (we can't compute the window).
        assert store.saves_correlated_to_scan(scan_id) == 0
