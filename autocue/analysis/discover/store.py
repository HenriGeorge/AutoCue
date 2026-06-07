"""DiscoverStore — schema, migration runner, boot-time scan-lock recovery.

See PRD §6.7 for the schema contract and v0.5 invariants this implements.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from autocue.serve.deps import discover_data_dir

# Display fallbacks for NULL / empty artist & title columns (S4-3).
# These strings are used ONLY for display. They never feed back into
# release_key — that keeps re-normalization migrations correct.
UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_TITLE = "Unknown Title"

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp; consistent with other autocue serialisers."""
    return datetime.now(timezone.utc).isoformat()


def _coerce_display(value: Optional[str], fallback: str) -> str:
    """Map NULL / empty / whitespace-only strings to the fallback (S4-3)."""
    if value is None:
        return fallback
    stripped = value.strip()
    return stripped if stripped else fallback


def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply every migration file whose version > schema_version.MAX(version).

    PRD §6.7 contract. Migration files are `<int>_*.sql` under migrations/;
    sorted lexically (zero-padded numbers keep order monotonic). Each
    migration runs in one executescript() call and bumps schema_version.
    """
    has_version = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    current = 0
    if has_version:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        current = (row[0] or 0) if row else 0

    for path in sorted(_MIGRATIONS_DIR.glob("[0-9]*.sql")):
        version = int(path.name.split("_", 1)[0])
        if version <= current:
            continue
        conn.executescript(path.read_text())
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, _utc_now_iso()),
        )
    conn.commit()


class DiscoverStore:
    """SQLite-backed persistence for Discover v2 state.

    Use as a long-lived singleton per process. Connection is single-threaded —
    callers serialize access (the orchestrator already does this via the
    concurrent-scan lock). For test isolation pass an explicit ``db_path``.
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        if db_path is None:
            db_path = discover_data_dir() / "discover.db"
        self.db_path = Path(db_path)
        # PRD §6.7: parent dirs created with mkdir(parents=True, exist_ok=True).
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # check_same_thread=False is required because FastAPI may dispatch the
        # connection across the async event loop's executor pool. We compensate
        # with the orchestrator's scan lock at the call site.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL gives us readers-during-writes — useful for the SSE feed reading
        # while a scan is still committing. Foreign keys are off by default in
        # SQLite; we keep them off because the schema doesn't declare any.
        self.conn.execute("PRAGMA journal_mode = WAL")

        run_migrations(self.conn)
        self._boot_recovery()

    # ── Boot recovery ──────────────────────────────────────────────────────

    def _boot_recovery(self) -> None:
        """Clear pending values from crashed scans and close their rows.

        PRD §6.7. Per-scan_id semantics: only entities whose
        ``pending_scan_id`` belongs to a crashed scan (finished_at IS NULL at
        boot) get cleared. Successful scans on the same entities are
        unaffected (S4-2 — no cascading rollback across interleaved scans).
        """
        now = _utc_now_iso()
        with self.conn:
            # Step 1: discard pending values from crashed scans, per-scan_id.
            self.conn.execute(
                """
                UPDATE followed_labels
                   SET last_scanned_at_pending = NULL,
                       pending_scan_id = NULL
                 WHERE pending_scan_id IN (
                     SELECT scan_id FROM scans WHERE finished_at IS NULL
                 )
                """
            )
            self.conn.execute(
                """
                UPDATE followed_shops
                   SET last_scanned_at_pending = NULL,
                       pending_scan_id = NULL
                 WHERE pending_scan_id IN (
                     SELECT scan_id FROM scans WHERE finished_at IS NULL
                 )
                """
            )
            # Step 2: close the open scan rows so the next startup is clean.
            self.conn.execute(
                "UPDATE scans SET finished_at = ?, status = 'crashed' "
                "WHERE finished_at IS NULL",
                (now,),
            )

    # ── State-table CRUD (S4-3: coerce empty/null artist/title to display) ──

    def save(
        self,
        *,
        release_key: str,
        release_id: int,
        artist: Optional[str],
        title: Optional[str],
        label: Optional[str] = None,
        saved_at: Optional[str] = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO saved "
                "(release_key, release_key_version, release_id, artist, title, label, saved_at) "
                "VALUES (?, 1, ?, ?, ?, ?, ?)",
                (
                    release_key,
                    release_id,
                    _coerce_display(artist, UNKNOWN_ARTIST),
                    _coerce_display(title, UNKNOWN_TITLE),
                    label,
                    saved_at or _utc_now_iso(),
                ),
            )

    def dismiss(
        self,
        *,
        release_key: str,
        release_id: Optional[int] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        reason: Optional[str] = None,
        dismissed_at: Optional[str] = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO dismissed "
                "(release_key, release_key_version, release_id, artist, title, dismissed_at, reason) "
                "VALUES (?, 1, ?, ?, ?, ?, ?)",
                (
                    release_key,
                    release_id,
                    _coerce_display(artist, UNKNOWN_ARTIST),
                    _coerce_display(title, UNKNOWN_TITLE),
                    dismissed_at or _utc_now_iso(),
                    reason,
                ),
            )

    def snooze(
        self,
        *,
        release_key: str,
        until_date: str,
        release_id: Optional[int] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        snoozed_at: Optional[str] = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO snoozed "
                "(release_key, release_key_version, release_id, artist, title, snoozed_at, until_date) "
                "VALUES (?, 1, ?, ?, ?, ?, ?)",
                (
                    release_key,
                    release_id,
                    _coerce_display(artist, UNKNOWN_ARTIST),
                    _coerce_display(title, UNKNOWN_TITLE),
                    snoozed_at or _utc_now_iso(),
                    until_date,
                ),
            )

    def record_download(
        self,
        *,
        release_key: str,
        file_paths: Iterable[str],
        release_id: Optional[int] = None,
        artist: Optional[str] = None,
        title: Optional[str] = None,
        downloaded_at: Optional[str] = None,
    ) -> None:
        # file_paths is ALWAYS stored as a JSON-encoded list — even single files.
        # Tier 2 re-normalization migrations rely on this being safe to json.loads().
        paths = list(file_paths)
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO downloaded "
                "(release_key, release_key_version, release_id, artist, title, downloaded_at, file_paths) "
                "VALUES (?, 1, ?, ?, ?, ?, ?)",
                (
                    release_key,
                    release_id,
                    _coerce_display(artist, UNKNOWN_ARTIST),
                    _coerce_display(title, UNKNOWN_TITLE),
                    downloaded_at or _utc_now_iso(),
                    json.dumps(paths),
                ),
            )

    # ── Scan lifecycle (T-007 / T-014) ─────────────────────────────────────

    def start_scan(
        self,
        feeders: Iterable[str],
        *,
        novelty_strategy: Optional[str] = None,
        started_at: Optional[str] = None,
    ) -> int:
        """Insert a new ``scans`` row with ``finished_at = NULL`` and return its ID.

        The orchestrator (T-014) calls this once per scan attempt. While
        ``finished_at`` is NULL the row holds the concurrent-scan lock — the
        SSE feed endpoint refuses to dispatch another scan until it flips.
        Boot recovery (``_boot_recovery``) closes any row left open by a crash.

        Args:
            feeders: iterable of feeder names that this scan plans to run.
                Stored as a comma-list for telemetry — order matters for
                debugging "which feeder hung the scan?".
            novelty_strategy: round-robin pointer for novelty feeder rotation
                (``'style'`` / ``'label'`` / ``'artist'``). ``None`` when no
                novelty strategy is scheduled for this scan.
            started_at: override for tests. Production callers omit this so
                ``_utc_now_iso()`` is used.

        Returns:
            The new scan_id (SQLite ``last_insert_rowid``).
        """
        feeders_list = ",".join(str(f) for f in feeders)
        ts = started_at or _utc_now_iso()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO scans (started_at, status, feeders, novelty_strategy) "
                "VALUES (?, 'running', ?, ?)",
                (ts, feeders_list, novelty_strategy),
            )
        return int(cur.lastrowid)

    def finish_scan(
        self,
        scan_id: int,
        *,
        status: str,
        finished_at: Optional[str] = None,
        novelty_status: Optional[str] = None,
        unknown_styles: Optional[Iterable[str]] = None,
        duration_ms: Optional[int] = None,
        requests_used: Optional[int] = None,
        releases_seen: Optional[int] = None,
        releases_after_dedup: Optional[int] = None,
        releases_surfaced: Optional[int] = None,
    ) -> None:
        """Close a scan row with terminal status.

        ``status`` is one of ``'ok'`` / ``'cancelled'`` / ``'rate_limited'``.
        (``'crashed'`` is reserved for boot recovery — callers must not pass it.)
        On status='ok' the orchestrator typically calls
        :meth:`commit_pending_scan` FIRST so any staging-column writes promote
        atomically before the lock releases.

        ``unknown_styles`` is stored as a JSON list — empty/None reads as
        SQL NULL so queries can filter on ``IS NULL`` cleanly.
        """
        if status == "crashed":
            raise ValueError("'crashed' is reserved for boot recovery, not direct finish_scan")
        ts = finished_at or _utc_now_iso()
        styles_json = (
            json.dumps(list(unknown_styles)) if unknown_styles else None
        )
        with self.conn:
            self.conn.execute(
                """
                UPDATE scans
                   SET finished_at = ?,
                       status = ?,
                       novelty_status = ?,
                       unknown_styles = ?,
                       duration_ms = ?,
                       requests_used = ?,
                       releases_seen = ?,
                       releases_after_dedup = ?,
                       releases_surfaced = ?
                 WHERE scan_id = ?
                """,
                (
                    ts, status, novelty_status, styles_json,
                    duration_ms, requests_used,
                    releases_seen, releases_after_dedup, releases_surfaced,
                    scan_id,
                ),
            )

    def commit_pending_scan(self, scan_id: int) -> None:
        """Atomically promote ``last_scanned_at_pending`` → ``last_scanned_at``
        for every entity whose ``pending_scan_id`` matches.

        Called by the orchestrator on successful scan finish, BEFORE
        :meth:`finish_scan` so the TTL gate sees the new committed values
        immediately. One transaction across both watch tables.
        """
        with self.conn:
            self.conn.execute(
                """
                UPDATE followed_labels
                   SET last_scanned_at = last_scanned_at_pending,
                       last_scanned_at_pending = NULL,
                       pending_scan_id = NULL
                 WHERE pending_scan_id = ?
                   AND last_scanned_at_pending IS NOT NULL
                """,
                (scan_id,),
            )
            self.conn.execute(
                """
                UPDATE followed_shops
                   SET last_scanned_at = last_scanned_at_pending,
                       last_scanned_at_pending = NULL,
                       pending_scan_id = NULL
                 WHERE pending_scan_id = ?
                   AND last_scanned_at_pending IS NOT NULL
                """,
                (scan_id,),
            )

    def is_scan_running(self) -> bool:
        """Cheap check used by the SSE feed endpoint to refuse a 2nd scan
        (returns 409 in that case)."""
        row = self.conn.execute(
            "SELECT 1 FROM scans WHERE finished_at IS NULL LIMIT 1"
        ).fetchone()
        return row is not None

    # ── Per-feeder staging-column writes (T-007) ───────────────────────────

    def mark_label_scanned(
        self,
        label_id: int,
        scan_id: int,
        *,
        scanned_at: Optional[str] = None,
    ) -> None:
        """Record that the label feeder fetched releases for this label
        during the given scan.

        Writes to the staging columns only — ``last_scanned_at`` doesn't move
        until :meth:`commit_pending_scan` runs. That keeps the TTL gate honest
        across a crashed mid-scan: the TTL still reflects the last successful
        scan, so the next attempt re-scans the same label.
        """
        ts = scanned_at or _utc_now_iso()
        with self.conn:
            self.conn.execute(
                """
                UPDATE followed_labels
                   SET last_scanned_at_pending = ?,
                       pending_scan_id = ?
                 WHERE label_id = ?
                """,
                (ts, scan_id, label_id),
            )

    # ── Follow / unfollow / list (T-007) ───────────────────────────────────

    def follow_label(
        self,
        label_id: int,
        name: str,
        *,
        added_at: Optional[str] = None,
    ) -> None:
        """Add a label to the user's watch list, or refresh its display name
        if it's already followed.

        Idempotent — INSERT OR REPLACE updates ``name`` without disturbing
        ``last_scanned_at`` or the staging columns, so re-following a label
        doesn't unnecessarily reset the TTL gate.
        """
        ts = added_at or _utc_now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO followed_labels (label_id, name, added_at)
                VALUES (?, ?, ?)
                ON CONFLICT(label_id) DO UPDATE SET name = excluded.name
                """,
                (label_id, name, ts),
            )

    def unfollow_label(self, label_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM followed_labels WHERE label_id = ?",
                (label_id,),
            )

    def list_followed_labels(self) -> list[dict]:
        """Return every followed label with TTL + health metadata.

        Ordered ``last_scanned_at ASC NULLS FIRST`` so the label feeder's
        round-robin fairness logic (PRD §6.2 Feeder 2) gets the longest-
        unscanned label first. Newly-followed labels (``last_scanned_at IS NULL``)
        come ahead of any previously-scanned label — they need a first fetch
        before the TTL clock can mean anything.
        """
        rows = self.conn.execute(
            """
            SELECT label_id, name, added_at, last_scanned_at,
                   last_scanned_at_pending, pending_scan_id,
                   health, consecutive_errors, current_name_check_at
              FROM followed_labels
             ORDER BY (last_scanned_at IS NULL) DESC, last_scanned_at ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Test / introspection helpers ───────────────────────────────────────

    def schema_version(self) -> int:
        """Highest applied schema version, or 0 on a blank DB."""
        row = self.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return (row[0] or 0) if row else 0

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "DiscoverStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
