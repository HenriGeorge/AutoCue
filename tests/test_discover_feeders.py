"""Tests for ``autocue.analysis.discover.feeders``.

Covers all T-007 pass criteria:

- Each feeder produces release dicts from mocked Discogs responses.
- 24h TTL: a label scanned <24h ago is skipped (gated by the feeder's
  selection step, not the Discogs client).
- One-feeder-failure-doesn't-abort-scan: a per-label exception yields an
  ('error', …) sentinel; subsequent labels still get fetched.
- Round-robin: with N explicit follows > budget, the longest-unscanned
  ``budget`` labels run; over multiple scans every label is eventually
  scanned.
- Staging-column writes: each scanned label gets ``last_scanned_at_pending``
  + ``pending_scan_id`` written to the followed_labels row (committed by
  orchestrator, not the feeder).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from autocue.analysis import discogs as discogs_client
from autocue.analysis.discover.feeders import (
    DEFAULT_LABEL_BUDGET,
    artist_feeder,
    label_feeder,
    select_label_slots,
)
from autocue.analysis.discover.store import DiscoverStore
from autocue.analysis.discover.taste import TasteVector


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def store(tmp_path):
    s = DiscoverStore(db_path=tmp_path / "discover.db")
    yield s
    s.close()


@pytest.fixture
def basic_taste():
    """A taste vector with three artists + three labels — enough variety for
    budget / ordering tests without bloat."""
    return TasteVector(
        artists=Counter({"Madvillain": 5.0, "Larry Heard": 3.0, "Goldie": 1.0}),
        labels=Counter({"Stones Throw": 10.0, "Alleviated": 5.0, "Metalheadz": 3.0}),
    )


@pytest.fixture(autouse=True)
def _stub_token_bucket(monkeypatch):
    """Don't sleep in the rate-limit token bucket during tests."""
    monkeypatch.setattr(discogs_client, "_acquire_token", lambda: None)


# --------------------------------------------------------------------------- #
# artist_feeder
# --------------------------------------------------------------------------- #

class TestArtistFeeder:
    def test_yields_releases_per_artist(self, basic_taste):
        def fake_search(artist_name, *_, **__):
            return [{"id": 1, "title": f"{artist_name} - Album", "artist": artist_name,
                     "album": "Album", "year": 2026}]

        with patch.object(discogs_client, "search_artist_releases", side_effect=fake_search):
            results = list(artist_feeder(basic_taste, token="t", budget=3))

        # 3 artists × 1 release each = 3 entries.
        sources = [r["source"] for r in results if isinstance(r, dict)]
        assert sources == ["artist", "artist", "artist"]
        # Order respects top_artists() — Madvillain (5.0) first.
        assert results[0]["artist_name"] == "Madvillain"
        assert results[1]["artist_name"] == "Larry Heard"

    def test_respects_budget(self, basic_taste):
        """A budget of 2 should produce calls for 2 artists, even though the
        taste vector has 3."""
        calls: list[str] = []

        def fake_search(artist_name, *_, **__):
            calls.append(artist_name)
            return []

        with patch.object(discogs_client, "search_artist_releases", side_effect=fake_search):
            list(artist_feeder(basic_taste, token="t", budget=2))

        assert calls == ["Madvillain", "Larry Heard"]

    def test_per_artist_error_yields_sentinel_and_continues(self, basic_taste):
        def fake_search(artist_name, *_, **__):
            if artist_name == "Larry Heard":
                raise RuntimeError("flake")
            return [{"id": 1, "title": f"{artist_name} - Album", "artist": artist_name,
                     "album": "Album", "year": 2026}]

        with patch.object(discogs_client, "search_artist_releases", side_effect=fake_search):
            results = list(artist_feeder(basic_taste, token="t", budget=3))

        # Madvillain release + ('error', ...) + Goldie release.
        kinds = [r[0] if isinstance(r, tuple) else "release" for r in results]
        assert "error" in kinds
        # Goldie still ran AFTER Larry Heard's error.
        artist_names = [r["artist_name"] for r in results if isinstance(r, dict)]
        assert "Goldie" in artist_names

    def test_discogs_429_aborts_scan_by_propagating(self, basic_taste):
        """A 429 is a scan-abort signal — the orchestrator catches it and
        marks status='rate_limited'. The feeder must NOT swallow it."""
        def fake_search(artist_name, *_, **__):
            raise discogs_client.Discogs429(retry_after=42)

        with patch.object(discogs_client, "search_artist_releases", side_effect=fake_search):
            gen = artist_feeder(basic_taste, token="t", budget=3)
            with pytest.raises(discogs_client.Discogs429) as exc_info:
                list(gen)
        assert exc_info.value.retry_after == 42

    def test_near_exhausted_yields_partial_then_warning_then_stops(self, basic_taste):
        """When the rate-limit bucket nearly drains, the feeder surfaces what
        it got + a warning so the orchestrator can back off."""
        def fake_search(artist_name, *_, **__):
            if artist_name == "Madvillain":
                raise discogs_client.RateLimitNearExhausted(
                    remaining=3,
                    data=[{"id": 99, "title": "OnTheCliff", "artist": "Madvillain",
                           "album": "OnTheCliff", "year": 2026}],
                )
            return [{"id": 1, "title": "should-not-appear"}]

        with patch.object(discogs_client, "search_artist_releases", side_effect=fake_search):
            results = list(artist_feeder(basic_taste, token="t", budget=3))

        # Got the partial Madvillain payload + a warning sentinel; Larry Heard
        # and Goldie were NEVER fetched.
        release_titles = [r["release"]["title"] for r in results if isinstance(r, dict)]
        assert release_titles == ["OnTheCliff"]
        warnings = [r for r in results if isinstance(r, tuple) and r[0] == "warning"]
        assert warnings == [("warning", {"feeder": "artist", "remaining": 3})]

    def test_empty_token_yields_nothing(self, basic_taste):
        assert list(artist_feeder(basic_taste, token="", budget=3)) == []

    def test_zero_budget_yields_nothing(self, basic_taste):
        assert list(artist_feeder(basic_taste, token="t", budget=0)) == []


# --------------------------------------------------------------------------- #
# select_label_slots — the prioritization unit
# --------------------------------------------------------------------------- #

class TestSelectLabelSlots:
    def test_explicit_follows_take_precedence(self, store, basic_taste):
        store.follow_label(1, "Stones Throw")
        slots = select_label_slots(store=store, taste_vector=basic_taste, budget=5)
        assert [s["source"] for s in slots] == ["explicit"]
        assert slots[0]["label_id"] == 1

    def test_implicit_resolver_returns_none_in_tier1_no_slots_filled(self, store, basic_taste):
        """The Tier-1 stub _label_id_for_taste_vector_entry returns None.
        With no explicit follows, the budget stays empty."""
        slots = select_label_slots(store=store, taste_vector=basic_taste, budget=5)
        assert slots == []

    def test_ttl_skip(self, store, basic_taste):
        """A label scanned <ttl_hours ago must NOT be re-scanned this round."""
        store.follow_label(1, "Stones Throw")

        now = datetime.now(timezone.utc)
        # Mark it as scanned 12h ago — within the 24h TTL.
        store.conn.execute(
            "UPDATE followed_labels SET last_scanned_at = ? WHERE label_id = ?",
            ((now - timedelta(hours=12)).isoformat(), 1),
        )
        store.conn.commit()

        slots = select_label_slots(
            store=store, taste_vector=basic_taste, budget=5, ttl_hours=24, now=now,
        )
        assert slots == [], "TTL-fresh label must be skipped"

    def test_ttl_expired_is_picked_again(self, store, basic_taste):
        store.follow_label(1, "Stones Throw")
        now = datetime.now(timezone.utc)
        # Scanned 30h ago — past the 24h TTL.
        store.conn.execute(
            "UPDATE followed_labels SET last_scanned_at = ? WHERE label_id = ?",
            ((now - timedelta(hours=30)).isoformat(), 1),
        )
        store.conn.commit()

        slots = select_label_slots(
            store=store, taste_vector=basic_taste, budget=5, ttl_hours=24, now=now,
        )
        assert len(slots) == 1
        assert slots[0]["label_id"] == 1

    def test_round_robin_picks_longest_unscanned_first(self, store, basic_taste):
        """With more follows than budget allows, the budget-many labels picked
        should be the ones with the OLDEST last_scanned_at (NULLs first)."""
        # 5 explicit follows, budget = 2. We expect the two least-recently-scanned.
        now = datetime.now(timezone.utc)
        store.follow_label(1, "OldestScanned")
        store.follow_label(2, "MidScanned")
        store.follow_label(3, "NewestScanned")
        store.follow_label(4, "NeverScanned_A")
        store.follow_label(5, "NeverScanned_B")
        for lid, hours_ago in ((1, 48), (2, 36), (3, 25)):
            store.conn.execute(
                "UPDATE followed_labels SET last_scanned_at = ? WHERE label_id = ?",
                ((now - timedelta(hours=hours_ago)).isoformat(), lid),
            )
        store.conn.commit()

        slots = select_label_slots(
            store=store, taste_vector=basic_taste, budget=2, ttl_hours=24, now=now,
        )
        # Both NeverScanned labels come first (NULL ORDER), then within TTL-expired.
        # We can only pick 2 — and the budget MUST go to the NULL pair (4 + 5)
        # because they're "least recently scanned" by definition.
        picked = {s["label_id"] for s in slots}
        assert picked == {4, 5}

    def test_fairness_across_two_scans_eventually_covers_all(self, store, basic_taste):
        """With 3 explicit follows + budget=2, every label is scanned at least
        once across two scans (PRD §6.2 round-robin invariant)."""
        store.follow_label(1, "A")
        store.follow_label(2, "B")
        store.follow_label(3, "C")
        now_1 = datetime.now(timezone.utc)

        # Scan 1: picks 2 of 3 (which 2? all NULL → SQLite picks by rowid).
        slots_1 = select_label_slots(
            store=store, taste_vector=basic_taste, budget=2, ttl_hours=24, now=now_1,
        )
        for s in slots_1:
            store.conn.execute(
                "UPDATE followed_labels SET last_scanned_at = ? WHERE label_id = ?",
                (now_1.isoformat(), s["label_id"]),
            )
        store.conn.commit()

        # Scan 2 happens 30h later — the previously-scanned pair is now TTL-fresh
        # (within 24h of the scan_1 time? No — 30h > 24h, so they're stale again).
        # The picked pair from scan_1 + the third label all become eligible.
        # Let's pick a time inside scan_1's TTL window so only the unscanned
        # third label is eligible.
        now_2 = now_1 + timedelta(hours=12)
        slots_2 = select_label_slots(
            store=store, taste_vector=basic_taste, budget=2, ttl_hours=24, now=now_2,
        )
        picked_2 = {s["label_id"] for s in slots_2}
        # The two from scan_1 are TTL-fresh (12h ago < 24h), so picked_2 must be
        # only the third (NeverScanned) label.
        picked_1 = {s["label_id"] for s in slots_1}
        third_label = {1, 2, 3} - picked_1
        assert picked_2 == third_label


# --------------------------------------------------------------------------- #
# label_feeder end-to-end
# --------------------------------------------------------------------------- #

class TestLabelFeeder:
    def test_yields_releases_and_writes_staging_columns(self, store, basic_taste):
        store.follow_label(42, "Stones Throw")
        scan_id = store.start_scan(feeders=["label"])

        def fake_search(label_id, *_, **__):
            return [{"id": 1, "title": "Madvillainy", "artist": "Madvillain",
                     "year": 2004, "format": "2xLP",
                     "thumb": "/t.jpg", "resource_url": "u"}]

        with patch.object(discogs_client, "search_label_releases", side_effect=fake_search):
            results = list(label_feeder(
                basic_taste, store, token="t", scan_id=scan_id,
                budget=DEFAULT_LABEL_BUDGET,
            ))

        assert len(results) == 1
        assert results[0]["source"] == "label"
        assert results[0]["label_id"] == 42
        assert results[0]["release"]["title"] == "Madvillainy"

        # The staging-column write was performed — but committed value is still NULL.
        row = store.list_followed_labels()[0]
        assert row["last_scanned_at"] is None
        assert row["last_scanned_at_pending"] is not None
        assert row["pending_scan_id"] == scan_id

    def test_ttl_skip_means_no_discogs_call(self, store, basic_taste):
        """If select_label_slots skips a label due to TTL freshness, the
        feeder must NOT issue a Discogs request for it."""
        store.follow_label(42, "Stones Throw")
        now = datetime.now(timezone.utc)
        store.conn.execute(
            "UPDATE followed_labels SET last_scanned_at = ? WHERE label_id = ?",
            ((now - timedelta(hours=1)).isoformat(), 42),
        )
        store.conn.commit()
        scan_id = store.start_scan(feeders=["label"])

        with patch.object(discogs_client, "search_label_releases") as m:
            results = list(label_feeder(
                basic_taste, store, token="t", scan_id=scan_id,
                budget=DEFAULT_LABEL_BUDGET, now=now,
            ))
        m.assert_not_called()
        assert results == []

    def test_one_label_error_yields_sentinel_and_continues(self, store, basic_taste):
        store.follow_label(1, "A")
        store.follow_label(2, "B")
        store.follow_label(3, "C")
        scan_id = store.start_scan(feeders=["label"])

        def fake_search(label_id, *_, **__):
            if label_id == 2:
                raise RuntimeError("flake")
            return [{"id": 99, "title": f"label-{label_id}-release", "artist": "X"}]

        with patch.object(discogs_client, "search_label_releases", side_effect=fake_search):
            results = list(label_feeder(
                basic_taste, store, token="t", scan_id=scan_id,
                budget=DEFAULT_LABEL_BUDGET,
            ))

        kinds = [r[0] if isinstance(r, tuple) else "release" for r in results]
        assert "error" in kinds
        # Labels 1 and 3 still yielded releases.
        release_titles = [r["release"]["title"] for r in results if isinstance(r, dict)]
        assert "label-1-release" in release_titles
        assert "label-3-release" in release_titles

        # Label 2 did NOT get a staging-column write — the error path returns
        # before mark_label_scanned. Labels 1 and 3 did.
        rows = {r["label_id"]: r for r in store.list_followed_labels()}
        assert rows[1]["pending_scan_id"] == scan_id
        assert rows[2]["pending_scan_id"] is None
        assert rows[3]["pending_scan_id"] == scan_id

    def test_near_exhausted_yields_partial_marks_scanned_and_warns(self, store, basic_taste):
        """Hitting near-exhaustion on label 1 must still RECORD label 1 as
        scanned (staging-column write) — we got valid data for it — and then
        stop before touching labels 2 / 3."""
        store.follow_label(1, "A")
        store.follow_label(2, "B")
        scan_id = store.start_scan(feeders=["label"])

        def fake_search(label_id, *_, **__):
            if label_id == 1:
                raise discogs_client.RateLimitNearExhausted(
                    remaining=2,
                    data=[{"id": 99, "title": "OnTheCliff", "artist": "Madvillain"}],
                )
            raise AssertionError("label 2 should not be reached")

        with patch.object(discogs_client, "search_label_releases", side_effect=fake_search):
            results = list(label_feeder(
                basic_taste, store, token="t", scan_id=scan_id,
                budget=DEFAULT_LABEL_BUDGET,
            ))

        release_titles = [r["release"]["title"] for r in results if isinstance(r, dict)]
        assert release_titles == ["OnTheCliff"]
        warnings = [r for r in results if isinstance(r, tuple) and r[0] == "warning"]
        assert warnings == [("warning", {"feeder": "label", "remaining": 2})]

        rows = {r["label_id"]: r for r in store.list_followed_labels()}
        assert rows[1]["pending_scan_id"] == scan_id
        assert rows[2]["pending_scan_id"] is None

    def test_discogs_429_propagates(self, store, basic_taste):
        store.follow_label(1, "A")
        scan_id = store.start_scan(feeders=["label"])

        def fake_search(label_id, *_, **__):
            raise discogs_client.Discogs429(retry_after=99)

        with patch.object(discogs_client, "search_label_releases", side_effect=fake_search):
            gen = label_feeder(
                basic_taste, store, token="t", scan_id=scan_id,
                budget=DEFAULT_LABEL_BUDGET,
            )
            with pytest.raises(discogs_client.Discogs429) as exc_info:
                list(gen)
        assert exc_info.value.retry_after == 99

    def test_empty_token_yields_nothing(self, store, basic_taste):
        store.follow_label(1, "A")
        scan_id = store.start_scan(feeders=["label"])
        assert list(label_feeder(
            basic_taste, store, token="", scan_id=scan_id,
        )) == []

    def test_full_lifecycle_with_orchestrator_commit(self, store, basic_taste):
        """Sanity check: scan starts → feeder writes pending → orchestrator
        commits → finish_scan → re-running within TTL skips the label."""
        store.follow_label(42, "Stones Throw")
        scan_id = store.start_scan(feeders=["label"])

        with patch.object(discogs_client, "search_label_releases",
                          return_value=[{"id": 1, "title": "X"}]):
            list(label_feeder(
                basic_taste, store, token="t", scan_id=scan_id,
                budget=DEFAULT_LABEL_BUDGET,
            ))

        # Orchestrator promotes + closes.
        store.commit_pending_scan(scan_id)
        store.finish_scan(scan_id, status="ok")

        # A second scan starting immediately should pick zero labels (TTL fresh).
        scan2_id = store.start_scan(feeders=["label"])
        with patch.object(discogs_client, "search_label_releases") as m:
            results = list(label_feeder(
                basic_taste, store, token="t", scan_id=scan2_id,
                budget=DEFAULT_LABEL_BUDGET,
            ))
        m.assert_not_called()
        assert results == []
