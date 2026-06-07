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
