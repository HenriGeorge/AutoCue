"""Tests for DiscoverStore — T-011 pass criteria.

Per PRD §6.7 + T-011 description:
1. First instantiation creates DB at canonical path; schema_version=1.
2. All v0.5 columns present (release_key_version + NULLABLE artist/title on state
   tables; status + unknown_styles on scans; last_scanned_at_pending +
   pending_scan_id on followed_labels/shops).
3. Migration bootstrap: blank DB applies 001 cleanly.
4. Idempotent: running migrations twice doesn't fail.
5. S4-2 acceptance: two crashed scans bracket one successful scan. Boot
   recovery clears pending values of the two crashed scans WITHOUT touching
   the successful scan's committed last_scanned_at on the same entities.
6. S4-3 acceptance: save() with artist='' or artist=None results in stored
   row with artist='Unknown Artist'; no insert failure.
"""

from __future__ import annotations

import sqlite3

import pytest

from autocue.analysis.discover.store import (
    UNKNOWN_ARTIST,
    UNKNOWN_TITLE,
    DiscoverStore,
    run_migrations,
)


# ── Pass criterion 1: instantiation creates DB; schema_version=1 ──────────


def test_first_instantiation_creates_db_at_path(tmp_path):
    db_path = tmp_path / "data" / "discover.db"
    assert not db_path.exists()
    assert not db_path.parent.exists()

    store = DiscoverStore(db_path=db_path)
    try:
        assert db_path.exists()
        assert store.schema_version() == 1
    finally:
        store.close()


# ── Pass criterion 2: all v0.5 columns present ────────────────────────────


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _column_notnull(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True iff the column is declared NOT NULL."""
    for row in conn.execute(f"PRAGMA table_info({table})"):
        if row[1] == column:
            return bool(row[3])
    raise AssertionError(f"{table}.{column} not found")


@pytest.fixture
def store(tmp_path):
    s = DiscoverStore(db_path=tmp_path / "discover.db")
    yield s
    s.close()


def test_state_tables_have_release_key_version_and_nullable_artist_title(store):
    for tbl in ("saved", "dismissed", "snoozed", "downloaded"):
        cols = _columns(store.conn, tbl)
        assert "release_key_version" in cols, f"{tbl} missing release_key_version"
        assert "artist" in cols and "title" in cols
        assert not _column_notnull(store.conn, tbl, "artist"), f"{tbl}.artist must be NULLABLE"
        assert not _column_notnull(store.conn, tbl, "title"), f"{tbl}.title must be NULLABLE"


def test_scans_has_status_and_unknown_styles(store):
    cols = _columns(store.conn, "scans")
    assert "status" in cols
    assert "unknown_styles" in cols


def test_followed_labels_and_shops_have_staging_columns(store):
    for tbl in ("followed_labels", "followed_shops"):
        cols = _columns(store.conn, tbl)
        assert "last_scanned_at" in cols
        assert "last_scanned_at_pending" in cols
        assert "pending_scan_id" in cols


# ── Pass criterion 3: migration bootstrap applies 001 cleanly ─────────────


def test_blank_db_applies_initial_migration(tmp_path):
    db_path = tmp_path / "discover.db"
    conn = sqlite3.connect(db_path)
    try:
        run_migrations(conn)
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        assert row[0] == 1
        # Sanity — one of the new tables must be queryable.
        conn.execute("SELECT COUNT(*) FROM saved").fetchone()
    finally:
        conn.close()


# ── Pass criterion 4: idempotent migration runner ────────────────────────


def test_running_migrations_twice_does_not_fail(tmp_path):
    db_path = tmp_path / "discover.db"
    store = DiscoverStore(db_path=db_path)
    try:
        first_version = store.schema_version()
        # Second call must be a no-op — re-applying the script would error on
        # CREATE TABLE saved (already exists).
        run_migrations(store.conn)
        assert store.schema_version() == first_version == 1
        # And a third for good measure.
        run_migrations(store.conn)
        assert store.schema_version() == 1
    finally:
        store.close()


# ── Pass criterion 5: S4-2 per-scan_id boot recovery ──────────────────────


def test_boot_recovery_per_scan_id_no_cascading_rollback(tmp_path):
    """Setup: two crashed scans bracket one successful scan, same label entity.

    Sequence over wall-clock time (label_id=42):
      1. Scan A starts. Writes last_scanned_at_pending='T1', pending_scan_id=A.
      2. Scan A crashes (finished_at left NULL).
      3. Scan B starts + finishes ok. Commits last_scanned_at='T2'.
         Clears pending columns (mimics what the orchestrator's commit step
         would do on success).
      4. Scan C starts. Writes pending='T3', pending_scan_id=C. Crashes.
      5. Process restart → DiscoverStore boot recovery runs.

    After recovery: label's last_scanned_at='T2' (Scan B's commit). The
    successful committed value MUST NOT be touched. Pending columns are
    NULL because both crashed scans had their pending values cleared.
    """
    db_path = tmp_path / "discover.db"
    store = DiscoverStore(db_path=db_path)
    try:
        c = store.conn
        # Seed the entity.
        c.execute(
            "INSERT INTO followed_labels (label_id, name, added_at) VALUES (?, ?, ?)",
            (42, "Test Label", "2026-01-01T00:00:00+00:00"),
        )
        # Scan A — crashed (no finished_at).
        c.execute(
            "INSERT INTO scans (scan_id, started_at, status) VALUES (?, ?, 'running')",
            (1, "2026-01-02T00:00:00+00:00"),
        )
        c.execute(
            "UPDATE followed_labels SET last_scanned_at_pending=?, pending_scan_id=? WHERE label_id=42",
            ("T1", 1),
        )
        # Scan B — succeeded. Orchestrator-on-success would clear pending and
        # commit to last_scanned_at. We simulate that here.
        c.execute(
            "INSERT INTO scans (scan_id, started_at, finished_at, status) "
            "VALUES (?, ?, ?, 'ok')",
            (2, "2026-01-03T00:00:00+00:00", "2026-01-03T00:01:00+00:00"),
        )
        c.execute(
            "UPDATE followed_labels SET last_scanned_at=?, "
            "last_scanned_at_pending=NULL, pending_scan_id=NULL WHERE label_id=42",
            ("T2",),
        )
        # Scan C — crashed mid-write.
        c.execute(
            "INSERT INTO scans (scan_id, started_at, status) VALUES (?, ?, 'running')",
            (3, "2026-01-04T00:00:00+00:00"),
        )
        c.execute(
            "UPDATE followed_labels SET last_scanned_at_pending=?, pending_scan_id=? WHERE label_id=42",
            ("T3", 3),
        )
        c.commit()
        store.close()
    except Exception:
        store.close()
        raise

    # Process restart → fresh DiscoverStore.
    store = DiscoverStore(db_path=db_path)
    try:
        row = store.conn.execute(
            "SELECT last_scanned_at, last_scanned_at_pending, pending_scan_id "
            "FROM followed_labels WHERE label_id=42"
        ).fetchone()
        # Successful scan's committed value MUST survive.
        assert row["last_scanned_at"] == "T2"
        # Crashed scan's pending values MUST be cleared.
        assert row["last_scanned_at_pending"] is None
        assert row["pending_scan_id"] is None

        # And both crashed scans are now closed with status='crashed'.
        crashed = store.conn.execute(
            "SELECT scan_id, status, finished_at FROM scans "
            "WHERE scan_id IN (1, 3) ORDER BY scan_id"
        ).fetchall()
        assert len(crashed) == 2
        for r in crashed:
            assert r["status"] == "crashed"
            assert r["finished_at"] is not None

        # Successful scan untouched.
        ok = store.conn.execute(
            "SELECT status, finished_at FROM scans WHERE scan_id=2"
        ).fetchone()
        assert ok["status"] == "ok"
        assert ok["finished_at"] == "2026-01-03T00:01:00+00:00"
    finally:
        store.close()


def test_boot_recovery_idempotent(tmp_path):
    """No crashed scans → boot recovery is a no-op."""
    db_path = tmp_path / "discover.db"
    store = DiscoverStore(db_path=db_path)
    try:
        store.conn.execute(
            "INSERT INTO scans (scan_id, started_at, finished_at, status) "
            "VALUES (?, ?, ?, 'ok')",
            (1, "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00"),
        )
        store.conn.commit()
        store.close()
    except Exception:
        store.close()
        raise

    # Re-open. Boot recovery must not flip the ok scan to 'crashed'.
    store = DiscoverStore(db_path=db_path)
    try:
        row = store.conn.execute(
            "SELECT status FROM scans WHERE scan_id=1"
        ).fetchone()
        assert row["status"] == "ok"
    finally:
        store.close()


# ── Pass criterion 6: S4-3 Unknown-coercion in CRUD ──────────────────────


def test_save_with_empty_artist_coerces_to_unknown(store):
    store.save(
        release_key="rk-empty-artist",
        release_id=100,
        artist="",
        title="Real Title",
    )
    row = store.conn.execute(
        "SELECT artist, title FROM saved WHERE release_key=?",
        ("rk-empty-artist",),
    ).fetchone()
    assert row["artist"] == UNKNOWN_ARTIST
    assert row["title"] == "Real Title"


def test_save_with_none_artist_coerces_to_unknown(store):
    store.save(
        release_key="rk-none-artist",
        release_id=101,
        artist=None,
        title=None,
    )
    row = store.conn.execute(
        "SELECT artist, title FROM saved WHERE release_key=?",
        ("rk-none-artist",),
    ).fetchone()
    assert row["artist"] == UNKNOWN_ARTIST
    assert row["title"] == UNKNOWN_TITLE


def test_save_whitespace_only_artist_coerces_to_unknown(store):
    store.save(
        release_key="rk-ws-artist",
        release_id=102,
        artist="   ",
        title="\t  \n",
    )
    row = store.conn.execute(
        "SELECT artist, title FROM saved WHERE release_key=?",
        ("rk-ws-artist",),
    ).fetchone()
    assert row["artist"] == UNKNOWN_ARTIST
    assert row["title"] == UNKNOWN_TITLE


def test_save_preserves_real_values(store):
    store.save(
        release_key="rk-real",
        release_id=103,
        artist="Aphex Twin",
        title="Selected Ambient Works 85-92",
        label="R&S",
    )
    row = store.conn.execute(
        "SELECT artist, title, label FROM saved WHERE release_key=?",
        ("rk-real",),
    ).fetchone()
    assert row["artist"] == "Aphex Twin"
    assert row["title"] == "Selected Ambient Works 85-92"
    assert row["label"] == "R&S"


def test_dismiss_snooze_download_all_coerce(store):
    # Each CRUD must apply the same coercion (S4-3 applies to every state table).
    store.dismiss(release_key="rk-d", artist="", title=None, reason="meh")
    store.snooze(release_key="rk-s", artist=None, title="", until_date="2026-02-01")
    store.record_download(release_key="rk-dl", artist="", title="", file_paths=["/x.flac"])

    for table, key in (("dismissed", "rk-d"), ("snoozed", "rk-s"), ("downloaded", "rk-dl")):
        row = store.conn.execute(
            f"SELECT artist, title FROM {table} WHERE release_key=?", (key,)
        ).fetchone()
        assert row["artist"] == UNKNOWN_ARTIST, table
        assert row["title"] == UNKNOWN_TITLE, table


def test_record_download_stores_file_paths_as_json_list(store):
    """Single-file downloads MUST still be JSON-encoded list per PRD §6.7."""
    import json as _json

    store.record_download(
        release_key="rk-single",
        artist="A",
        title="T",
        file_paths=["/music/track1.flac"],
    )
    row = store.conn.execute(
        "SELECT file_paths FROM downloaded WHERE release_key=?", ("rk-single",)
    ).fetchone()
    parsed = _json.loads(row["file_paths"])
    assert parsed == ["/music/track1.flac"]

    store.record_download(
        release_key="rk-multi",
        artist="A",
        title="T",
        file_paths=["/music/a.flac", "/music/b.flac"],
    )
    row = store.conn.execute(
        "SELECT file_paths FROM downloaded WHERE release_key=?", ("rk-multi",)
    ).fetchone()
    parsed = _json.loads(row["file_paths"])
    assert parsed == ["/music/a.flac", "/music/b.flac"]


# ── discover_data_dir() resolves to a sane platform-native path ──────────


def test_discover_data_dir_override(monkeypatch, tmp_path):
    from autocue.serve.deps import discover_data_dir

    custom = tmp_path / "custom-discover-dir"
    monkeypatch.setenv("AUTOCUE_DISCOVER_DATA_DIR", str(custom))
    assert discover_data_dir() == custom


def test_discover_data_dir_default_per_platform(monkeypatch):
    from autocue.serve.deps import discover_data_dir

    monkeypatch.delenv("AUTOCUE_DISCOVER_DATA_DIR", raising=False)
    p = discover_data_dir()
    # Whatever the platform, the dir name must end in 'autocue' or 'AutoCue'.
    assert p.name.lower() == "autocue"
