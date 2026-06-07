"""Resolve the on-disk ANLZ mtime for a Rekordbox content row.

Centralizes the mtime lookup so every cache call site agrees on the
single source of truth for "is this row still valid?" — see CacheStore
(autocue/cache.py) and TASK-012.
"""
from __future__ import annotations

import os
from typing import Any

__all__ = ["get_anlz_mtime", "MISSING_MTIME"]

# Sentinel value stored in CacheStore rows when no ANLZ file exists for a
# given track. Negative so it can never match a real os.path.getmtime()
# result (mtimes are non-negative seconds since epoch).
MISSING_MTIME: float = -1.0


def get_anlz_mtime(content: Any, db: Any) -> float | None:
    """Return the mtime (seconds since epoch) of the track's ANLZ file.

    Returns ``None`` when:
      - the content row has no ``AnalysisDataPath`` (track was never
        analyzed by Rekordbox);
      - the ANLZ path resolves but the file is missing on disk;
      - any other ``OSError`` while stat-ing the file.

    Per CLAUDE.md, ``AnalysisDataPath`` is truthy when an ``.EXT`` exists.
    The mtime check is what tells us whether the cache row is still valid.
    """
    analysis_data_path = getattr(content, "AnalysisDataPath", None)
    if not analysis_data_path:
        return None

    # Prefer ``db.get_anlz_path()`` when available — it knows the prefix /
    # extension conventions. Fall back to the raw path if the helper is
    # absent (e.g., in fakes / mocks).
    path: str | None = None
    if hasattr(db, "get_anlz_path"):
        try:
            path = db.get_anlz_path(content, "DAT") or db.get_anlz_path(content, "EXT")
        except Exception:
            path = None
    if not path:
        path = analysis_data_path

    try:
        return os.path.getmtime(path)
    except OSError:
        return None
