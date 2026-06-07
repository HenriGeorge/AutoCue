"""Tests for the scan-lifecycle / follow / staging-column methods added to
``DiscoverStore``.

These methods landed alongside T-007 because the label_feeder can't function
without them — T-011's PR only shipped the user-state CRUD half of the store.

Covers:

- ``start_scan`` / ``finish_scan`` / ``is_scan_running`` — concurrent-scan lock
- ``commit_pending_scan`` — atomic staging-column promotion
- ``mark_label_scanned`` — staging-column writes (never touches committed)
- ``follow_label`` / ``unfollow_label`` / ``list_followed_labels`` — watch CRUD
  with the ``last_scanned_at ASC NULLS FIRST`` ordering the feeder relies on
"""

from __future__ import annotations

import json

import pytest

from autocue.analysis.discover.store import DiscoverStore


@pytest.fixture
def store(tmp_path):
    s = DiscoverStore(db_path=tmp_path / "discover.db")
    yield s
    s.close()


# --------------------------------------------------------------------------- #
# start_scan / finish_scan / is_scan_running
# --------------------------------------------------------------------------- #

class TestStartFinishScan:
    def test_start_scan_returns_id_and_locks(self, store):
        scan_id = store.start_scan(feeders=["artist", "label"])
        assert isinstance(scan_id, int) and scan_id > 0
        # The "running" row holds the lock until finish_scan flips it.
        assert store.is_scan_running() is True

    def test_finish_scan_releases_lock(self, store):
        scan_id = store.start_scan(feeders=["artist"])
        store.finish_scan(scan_id, status="ok")
        assert store.is_scan_running() is False

    def test_concurrent_scans_show_as_running(self, store):
        """The orchestrator's concurrent-scan guard reads is_scan_running()
        before allowing a 2nd start_scan. Verify the lock is by-the-table-row,
        not by some hidden mutex."""
        s1 = store.start_scan(feeders=["artist"])
        s2 = store.start_scan(feeders=["label"])
        assert s1 != s2
        assert store.is_scan_running() is True

    def test_finish_scan_persists_telemetry_fields(self, store):
        scan_id = store.start_scan(feeders=["artist", "label", "novelty"], novelty_strategy="style")
        store.finish_scan(
            scan_id, status="ok",
            duration_ms=4500, requests_used=42,
            releases_seen=120, releases_after_dedup=85, releases_surfaced=50,
            novelty_status="ok",
            unknown_styles=["future_funkstep", "neuro_jazz"],
        )
        row = store.conn.execute(
            "SELECT * FROM scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        assert row["status"] == "ok"
        assert row["feeders"] == "artist,label,novelty"
        assert row["novelty_strategy"] == "style"
        assert row["duration_ms"] == 4500
        assert row["requests_used"] == 42
        assert row["releases_surfaced"] == 50
        assert json.loads(row["unknown_styles"]) == ["future_funkstep", "neuro_jazz"]

    def test_finish_scan_with_no_unknown_styles_stores_null(self, store):
        scan_id = store.start_scan(feeders=["artist"])
        store.finish_scan(scan_id, status="ok")
        row = store.conn.execute(
            "SELECT unknown_styles FROM scans WHERE scan_id = ?", (scan_id,)
        ).fetchone()
        assert row["unknown_styles"] is None

    def test_finish_scan_rejects_crashed_status(self, store):
        """'crashed' is reserved for boot recovery — callers shouldn't pass it
        because it would skew the telemetry 'how many scans crashed?' query."""
        scan_id = store.start_scan(feeders=["artist"])
        with pytest.raises(ValueError, match="crashed"):
            store.finish_scan(scan_id, status="crashed")


# --------------------------------------------------------------------------- #
# follow_label / unfollow_label / list_followed_labels
# --------------------------------------------------------------------------- #

class TestFollowLabel:
    def test_follow_inserts_row(self, store):
        store.follow_label(42, "Stones Throw")
        rows = store.list_followed_labels()
        assert len(rows) == 1
        assert rows[0]["label_id"] == 42
        assert rows[0]["name"] == "Stones Throw"
        assert rows[0]["last_scanned_at"] is None  # never scanned yet
        assert rows[0]["last_scanned_at_pending"] is None
        assert rows[0]["pending_scan_id"] is None

    def test_follow_is_idempotent_and_updates_name(self, store):
        """Re-following the same label must NOT reset last_scanned_at — that
        would burn the budget re-fetching a label we just scanned. ON CONFLICT
        only updates the display name."""
        store.follow_label(42, "Stones Throw")
        scan_id = store.start_scan(feeders=["label"])
        store.mark_label_scanned(42, scan_id)
        store.commit_pending_scan(scan_id)
        store.finish_scan(scan_id, status="ok")

        before = store.list_followed_labels()[0]
        assert before["last_scanned_at"] is not None
        original_ts = before["last_scanned_at"]

        # Re-follow with an updated display name.
        store.follow_label(42, "Stones Throw Records")

        after = store.list_followed_labels()[0]
        assert after["name"] == "Stones Throw Records"
        assert after["last_scanned_at"] == original_ts, \
            "re-following must not reset the TTL clock"

    def test_unfollow_removes_row(self, store):
        store.follow_label(42, "Stones Throw")
        store.follow_label(43, "Brainfeeder")
        store.unfollow_label(42)
        rows = store.list_followed_labels()
        assert [r["label_id"] for r in rows] == [43]

    def test_list_ordering_puts_unscanned_first_then_oldest(self, store):
        """The feeder relies on this ordering for round-robin fairness when
        the user has more explicit follows than the per-scan budget."""
        # Three labels, scanned at staggered times. Plus one never-scanned.
        store.follow_label(1, "OldScan")
        store.follow_label(2, "MidScan")
        store.follow_label(3, "NewScan")
        store.follow_label(4, "NeverScanned")

        # Promote scan times directly via SQL so we control the ordering.
        store.conn.execute(
            "UPDATE followed_labels SET last_scanned_at = ? WHERE label_id = ?",
            ("2026-06-01T00:00:00+00:00", 1),
        )
        store.conn.execute(
            "UPDATE followed_labels SET last_scanned_at = ? WHERE label_id = ?",
            ("2026-06-05T00:00:00+00:00", 2),
        )
        store.conn.execute(
            "UPDATE followed_labels SET last_scanned_at = ? WHERE label_id = ?",
            ("2026-06-07T00:00:00+00:00", 3),
        )
        store.conn.commit()

        rows = store.list_followed_labels()
        # NULL (NeverScanned) FIRST, then ascending by last_scanned_at.
        assert [r["label_id"] for r in rows] == [4, 1, 2, 3]


# --------------------------------------------------------------------------- #
# mark_label_scanned + commit_pending_scan
# --------------------------------------------------------------------------- #

class TestStagingColumnContract:
    def test_mark_writes_pending_not_committed(self, store):
        """The whole point of staging columns: a feeder can write a label's
        completion mid-scan, but the TTL gate (which reads last_scanned_at)
        sees the OLD value until commit_pending_scan promotes it."""
        store.follow_label(42, "Stones Throw")
        scan_id = store.start_scan(feeders=["label"])
        store.mark_label_scanned(42, scan_id, scanned_at="2026-06-07T12:00:00+00:00")

        row = store.list_followed_labels()[0]
        assert row["last_scanned_at"] is None  # not yet committed
        assert row["last_scanned_at_pending"] == "2026-06-07T12:00:00+00:00"
        assert row["pending_scan_id"] == scan_id

    def test_commit_promotes_pending_and_clears_staging(self, store):
        store.follow_label(42, "Stones Throw")
        scan_id = store.start_scan(feeders=["label"])
        store.mark_label_scanned(42, scan_id, scanned_at="2026-06-07T12:00:00+00:00")

        store.commit_pending_scan(scan_id)

        row = store.list_followed_labels()[0]
        assert row["last_scanned_at"] == "2026-06-07T12:00:00+00:00"
        assert row["last_scanned_at_pending"] is None
        assert row["pending_scan_id"] is None

    def test_commit_only_affects_matching_scan_id(self, store):
        """Two crashed scans left pending writes on different labels. Committing
        scan A must not promote scan B's pending value."""
        store.follow_label(1, "A")
        store.follow_label(2, "B")
        scan_a = store.start_scan(feeders=["label"])
        scan_b = store.start_scan(feeders=["label"])
        store.mark_label_scanned(1, scan_a)
        store.mark_label_scanned(2, scan_b)

        store.commit_pending_scan(scan_a)

        rows = {r["label_id"]: r for r in store.list_followed_labels()}
        assert rows[1]["last_scanned_at"] is not None
        assert rows[1]["last_scanned_at_pending"] is None
        # Label 2's pending values are untouched.
        assert rows[2]["last_scanned_at"] is None
        assert rows[2]["last_scanned_at_pending"] is not None
        assert rows[2]["pending_scan_id"] == scan_b

    def test_boot_recovery_clears_crashed_pending_via_existing_path(self, tmp_path):
        """Re-opening DiscoverStore after a crash (scans with finished_at NULL)
        must reset only those scans' pending writes. Re-uses the existing
        _boot_recovery path implemented in T-011 — this test guards against
        the new mark_label_scanned writes interacting weirdly with it."""
        first = DiscoverStore(db_path=tmp_path / "discover.db")
        first.follow_label(42, "Stones Throw")
        scan_id = first.start_scan(feeders=["label"])
        first.mark_label_scanned(42, scan_id)  # pending now
        # SIMULATE A CRASH: close without finish_scan.
        first.close()

        second = DiscoverStore(db_path=tmp_path / "discover.db")
        rows = second.list_followed_labels()
        try:
            assert rows[0]["last_scanned_at_pending"] is None
            assert rows[0]["pending_scan_id"] is None
            # The crashed scan row is closed with status='crashed'.
            crashed = second.conn.execute(
                "SELECT status FROM scans WHERE scan_id = ?", (scan_id,)
            ).fetchone()
            assert crashed["status"] == "crashed"
        finally:
            second.close()
