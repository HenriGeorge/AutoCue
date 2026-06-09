"""Sidecar analysis cache — plain SQLite at ``<rekordbox_dir>/autocue_cache.sqlite``.

Memoizes per-track analysis results across ``autocue serve`` runs so cold
starts don't re-parse every ANLZ file. Contains no audio, no credentials,
no Discogs tokens — safe to ship plaintext (no SQLCipher).

See .agent/prd/PERFORMANCE_PRD.md §7 (schema) and TASK-010 / TASK-011 /
TASK-012 for design.

Invariants:
  - Every per-track row carries ``anlz_mtime``. Readers MUST compare against
    the current ANLZ file's mtime; mismatch → recompute + replace.
  - Tracks with no ANLZ store ``anlz_mtime = -1`` (sentinel) so we don't
    retry on every call until the file appears.
  - WAL mode + per-row commits → readers never block on writers.
  - Schema bumps drop + recreate; no migrations (cache is regenerable).
"""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
import struct
import threading
from typing import Any

__all__ = ["CacheStore", "CACHE_FILENAME", "SCHEMA_VERSION", "MISSING"]

CACHE_FILENAME = "autocue_cache.sqlite"
SCHEMA_VERSION = 2  # v2 bump: TrackItem snapshot now includes existing_cue_details

# Sentinel returned when a track has been recorded with anlz_mtime = -1
# (no ANLZ file present at compute time). Callers MUST treat this distinctly
# from None (cache miss) — None means "compute now", MISSING means "compute
# would just fail again, skip".
MISSING = object()

SCHEMA_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS meta (
      key   TEXT PRIMARY KEY,
      value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS energy_curve (
      content_id INTEGER PRIMARY KEY,
      anlz_mtime REAL NOT NULL,
      n_points   INTEGER NOT NULL,
      curve      BLOB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS classification (
      content_id   INTEGER PRIMARY KEY,
      anlz_mtime   REAL NOT NULL,
      primary_cat  TEXT NOT NULL,
      scores_json  TEXT NOT NULL,
      bpm          REAL,
      energy_mean  REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS similarity_vector (
      content_id INTEGER PRIMARY KEY,
      anlz_mtime REAL NOT NULL,
      vector     BLOB NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mixability (
      content_id      INTEGER PRIMARY KEY,
      anlz_mtime      REAL NOT NULL,
      score           REAL NOT NULL,
      components_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tracks_snapshot (
      master_db_mtime REAL PRIMARY KEY,
      payload         BLOB NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_energy_mtime ON energy_curve(anlz_mtime)",
    "CREATE INDEX IF NOT EXISTS idx_class_mtime  ON classification(anlz_mtime)",
)

# Per-track tables — used by invalidate_track() and bulk-clears.
_PER_TRACK_TABLES: tuple[str, ...] = (
    "energy_curve",
    "classification",
    "similarity_vector",
    "mixability",
)


class CacheStore:
    """SQLite-backed sidecar cache.

    Open with :meth:`open_for` to resolve the canonical path in a Rekordbox
    directory; construct directly with ``CacheStore(path)`` for tests using
    ``:memory:`` databases.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------

    @classmethod
    def open_for(cls, rekordbox_dir: str) -> "CacheStore":
        """Open the sidecar at ``<rekordbox_dir>/autocue_cache.sqlite``."""
        path = os.path.join(rekordbox_dir, CACHE_FILENAME)
        return cls._open(path)

    @classmethod
    def open_memory(cls) -> "CacheStore":
        """Open an in-memory cache. Test-only."""
        return cls._open(":memory:")

    @classmethod
    def _open(cls, path: str) -> "CacheStore":
        store = cls(path)
        store._conn = sqlite3.connect(
            path,
            check_same_thread=False,
            isolation_level=None,  # autocommit; explicit BEGIN/COMMIT in batches.
        )
        # WAL + NORMAL synchronous: durable but lets readers overlap writers.
        # `:memory:` doesn't support WAL; PRAGMA fails silently and we continue.
        try:
            store._conn.execute("PRAGMA journal_mode=WAL")
            store._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass
        store._conn.execute("PRAGMA foreign_keys=ON")
        store._init_schema()
        return store

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.commit()
                finally:
                    self._conn.close()
                    self._conn = None

    # -- schema --------------------------------------------------------------

    def _init_schema(self) -> None:
        assert self._conn is not None
        with self._lock:
            cur = self._conn.cursor()
            # Loop at most twice: first pass either accepts existing schema or
            # drops it; second pass writes a clean slate. No recursion (the
            # lock is held for the whole sequence).
            for _ in range(2):
                try:
                    cur.execute("BEGIN")
                    for ddl in SCHEMA_DDL:
                        cur.execute(ddl)
                    cur.execute(
                        "SELECT value FROM meta WHERE key='schema_version'"
                    )
                    row = cur.fetchone()
                    if row is None:
                        cur.execute(
                            "INSERT INTO meta(key, value) VALUES "
                            "('schema_version', ?)",
                            (str(SCHEMA_VERSION),),
                        )
                        self._conn.commit()
                        return
                    if int(row[0]) == SCHEMA_VERSION:
                        self._conn.commit()
                        return
                    # Schema mismatch — drop everything and retry.
                    for table in (*_PER_TRACK_TABLES, "tracks_snapshot", "meta"):
                        cur.execute(f"DROP TABLE IF EXISTS {table}")
                    self._conn.commit()
                    continue
                except Exception:
                    self._conn.rollback()
                    raise
            # Two iterations should always converge; if we got here, the
            # second pass somehow saw a third version — bail loudly.
            raise RuntimeError("CacheStore schema init did not converge")

    def dump_schema(self) -> str:
        """Return the live CREATE statements (for diffing / debugging)."""
        assert self._conn is not None
        cur = self._conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type IN ('table','index') AND sql IS NOT NULL "
            "ORDER BY type, name"
        )
        return "\n".join(row[0] for row in cur.fetchall())

    # -- energy_curve --------------------------------------------------------

    def put_energy_curve(
        self, content_id: int, curve: list[float], anlz_mtime: float
    ) -> None:
        blob = struct.pack(f"{len(curve)}f", *curve) if curve else b""
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                "INSERT OR REPLACE INTO energy_curve "
                "(content_id, anlz_mtime, n_points, curve) VALUES (?, ?, ?, ?)",
                (content_id, anlz_mtime, len(curve), blob),
            )

    def get_energy_curve(
        self, content_id: int, expected_anlz_mtime: float | None
    ) -> Any:
        """Return ``list[float]`` on hit, ``MISSING`` on -1 sentinel, ``None`` on miss/mismatch."""
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT anlz_mtime, n_points, curve FROM energy_curve WHERE content_id=?",
                (content_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        anlz_mtime, n_points, blob = row
        if anlz_mtime < 0:
            return MISSING
        if expected_anlz_mtime is None or anlz_mtime != expected_anlz_mtime:
            return None
        if not blob or n_points == 0:
            return []
        return list(struct.unpack(f"{n_points}f", blob))

    # -- classification ------------------------------------------------------

    def put_classification(
        self,
        content_id: int,
        primary_cat: str,
        scores: dict[str, float],
        bpm: float | None,
        energy_mean: float | None,
        anlz_mtime: float,
    ) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                "INSERT OR REPLACE INTO classification "
                "(content_id, anlz_mtime, primary_cat, scores_json, bpm, energy_mean) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (content_id, anlz_mtime, primary_cat, json.dumps(scores), bpm, energy_mean),
            )

    def get_classification(
        self, content_id: int, expected_anlz_mtime: float | None
    ) -> Any:
        """Return ``{primary, scores, bpm, energy_mean}`` or ``MISSING`` or ``None``."""
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT anlz_mtime, primary_cat, scores_json, bpm, energy_mean "
                "FROM classification WHERE content_id=?",
                (content_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        anlz_mtime, primary_cat, scores_json, bpm, energy_mean = row
        if anlz_mtime < 0:
            return MISSING
        if expected_anlz_mtime is None or anlz_mtime != expected_anlz_mtime:
            return None
        return {
            "primary": primary_cat,
            "scores": json.loads(scores_json),
            "bpm": bpm,
            "energy_mean": energy_mean,
        }

    # -- similarity_vector ---------------------------------------------------

    def put_similarity_vector(
        self, content_id: int, vector: tuple[float, ...], anlz_mtime: float
    ) -> None:
        if len(vector) != 6:
            raise ValueError(f"similarity_vector must be 6 floats, got {len(vector)}")
        blob = struct.pack("6f", *vector)
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                "INSERT OR REPLACE INTO similarity_vector "
                "(content_id, anlz_mtime, vector) VALUES (?, ?, ?)",
                (content_id, anlz_mtime, blob),
            )

    def get_similarity_vector(
        self, content_id: int, expected_anlz_mtime: float | None
    ) -> Any:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT anlz_mtime, vector FROM similarity_vector WHERE content_id=?",
                (content_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        anlz_mtime, blob = row
        if anlz_mtime < 0:
            return MISSING
        if expected_anlz_mtime is None or anlz_mtime != expected_anlz_mtime:
            return None
        return struct.unpack("6f", blob)

    # -- mixability ----------------------------------------------------------

    def put_mixability(
        self,
        content_id: int,
        score: float,
        components: dict[str, Any],
        anlz_mtime: float,
    ) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                "INSERT OR REPLACE INTO mixability "
                "(content_id, anlz_mtime, score, components_json) VALUES (?, ?, ?, ?)",
                (content_id, anlz_mtime, score, json.dumps(components)),
            )

    def get_mixability(
        self, content_id: int, expected_anlz_mtime: float | None
    ) -> Any:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT anlz_mtime, score, components_json FROM mixability WHERE content_id=?",
                (content_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        anlz_mtime, score, components_json = row
        if anlz_mtime < 0:
            return MISSING
        if expected_anlz_mtime is None or anlz_mtime != expected_anlz_mtime:
            return None
        return {"score": score, "components": json.loads(components_json)}

    # -- tracks_snapshot -----------------------------------------------------

    def put_tracks_snapshot(self, master_db_mtime: float, payload: bytes) -> None:
        """Replace the (single-row) snapshot with a fresh gzipped JSON payload."""
        with self._lock:
            assert self._conn is not None
            self._conn.execute("DELETE FROM tracks_snapshot")
            self._conn.execute(
                "INSERT INTO tracks_snapshot(master_db_mtime, payload) VALUES (?, ?)",
                (master_db_mtime, payload),
            )

    def get_tracks_snapshot(self, expected_master_db_mtime: float) -> bytes | None:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT master_db_mtime, payload FROM tracks_snapshot LIMIT 1"
            )
            row = cur.fetchone()
        if row is None or row[0] != expected_master_db_mtime:
            return None
        return row[1]

    # -- invalidation --------------------------------------------------------

    def invalidate_all(self) -> None:
        with self._lock:
            assert self._conn is not None
            for table in (*_PER_TRACK_TABLES, "tracks_snapshot"):
                self._conn.execute(f"DELETE FROM {table}")

    def invalidate_track(self, content_id: int) -> None:
        with self._lock:
            assert self._conn is not None
            for table in _PER_TRACK_TABLES:
                self._conn.execute(
                    f"DELETE FROM {table} WHERE content_id=?", (content_id,)
                )

    def invalidate_mixability(self, content_id: int) -> None:
        """Used by /api/apply since cue edits change intro/outro detection."""
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                "DELETE FROM mixability WHERE content_id=?", (content_id,)
            )

    # -- warm-up -------------------------------------------------------------

    def find_missing(self, content_ids: list[int]) -> list[int]:
        """Return the subset of content_ids that are missing from energy_curve.

        Used by warm_up() — energy is the canonical seed; once energy is
        present the other per-track tables fill on first read through the
        wired analysis modules.
        """
        if not content_ids:
            return []
        with self._lock:
            assert self._conn is not None
            placeholders = ",".join("?" * len(content_ids))
            cur = self._conn.execute(
                f"SELECT content_id FROM energy_curve WHERE content_id IN ({placeholders})",
                content_ids,
            )
            present = {row[0] for row in cur.fetchall()}
        return [cid for cid in content_ids if cid not in present]

    def warm_up(
        self,
        db: Any,
        content_ids: list[int],
        pool: Any,
        progress_cb: Any | None = None,
        cancel_event: Any | None = None,
        batch_size: int = 50,
    ) -> int:
        """Hydrate missing per-track rows in parallel via the shared pool.

        Each missing content_id has its full analysis pipeline triggered
        via the wired set_cache_store hooks (energy → classification →
        similarity vector → mixability) — the L2 writes are side-effects.

        ``progress_cb(done, total)`` is invoked every ``batch_size`` tracks.
        ``cancel_event.is_set()`` is checked between batches; cancellation
        leaves the partially-warmed cache intact (per-row commits are
        atomic).

        Returns the count of tracks actually processed (less than total
        when cancelled or when content rows could not be resolved).
        """
        from .analysis import energy as _energy
        from .analysis import classify as _classify
        from .analysis import similar as _similar

        missing = self.find_missing(content_ids)
        total = len(missing)
        if total == 0:
            if progress_cb:
                progress_cb(0, 0)
            return 0

        def _warm_one(content_id: int) -> bool:
            if cancel_event is not None and cancel_event.is_set():
                return False
            try:
                content = db.get_content(ID=content_id)
            except Exception:
                return False
            if content is None:
                return False
            try:
                _energy.get_energy_curve(content, db)
                _classify.get_classification(content, db)
                _similar._index_track(content, db)
            except Exception:
                return False
            return True

        done = 0
        # Batch through the pool so cancellation lands quickly. We iterate
        # futures in submission order — `.result()` blocks until done so
        # the batch's wall-clock is bounded by the slowest worker. Avoids
        # ``concurrent.futures.as_completed`` which doesn't work with
        # stubbed pools in tests and gains us only marginal parallelism
        # at our batch sizes.
        for offset in range(0, total, batch_size):
            if cancel_event is not None and cancel_event.is_set():
                break
            batch = missing[offset:offset + batch_size]
            futures = [pool.submit(_warm_one, cid) for cid in batch]
            for fut in futures:
                try:
                    fut.result()
                except Exception:
                    pass
                done += 1
            if progress_cb:
                progress_cb(done, total)
        return done

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def gzip_json(payload: list[Any] | dict[str, Any]) -> bytes:
        return gzip.compress(json.dumps(payload).encode("utf-8"))

    @staticmethod
    def ungzip_json(blob: bytes) -> Any:
        return json.loads(gzip.decompress(blob).decode("utf-8"))
