"""Delete the sidecar analysis cache (and its WAL/SHM siblings) before serve startup.

Implements ``autocue serve --reset-cache`` (TASK-020). Resolves the
Rekordbox directory the same way ``serve`` does and removes only the
three known cache files — never anything else in the directory.
"""
from __future__ import annotations

import logging
import os

from .cache import CACHE_FILENAME

logger = logging.getLogger(__name__)

_SUFFIXES = ("", "-wal", "-shm")


def reset_sidecar_cache(db_path: str | None) -> list[str]:
    """Delete sidecar cache files if present.

    ``db_path`` is the path passed via ``--db-path`` (may be ``None`` when
    the default Rekordbox location is used). Returns the list of files
    that were actually removed (for logging / test assertions).
    """
    rekordbox_dir = _resolve_rekordbox_dir(db_path)
    if rekordbox_dir is None:
        logger.warning(
            "Could not resolve Rekordbox directory for --reset-cache; skipping"
        )
        return []

    removed: list[str] = []
    for suffix in _SUFFIXES:
        path = os.path.join(rekordbox_dir, CACHE_FILENAME + suffix)
        if os.path.exists(path):
            try:
                os.remove(path)
                removed.append(path)
            except OSError as exc:  # pragma: no cover — race during cleanup
                logger.warning("Could not remove cache file %s: %s", path, exc)
    if removed:
        logger.info("Removed sidecar cache files: %s", removed)
    return removed


def _resolve_rekordbox_dir(db_path: str | None) -> str | None:
    """Return the directory that contains ``master.db``.

    Mirrors the resolution order used by ``Rekordbox6Database`` — if the
    user passed ``--db-path``, we treat it as the rekordbox directory
    (or the parent of master.db); otherwise we ask pyrekordbox where the
    default install lives, falling back to ``None`` on any error.
    """
    if db_path:
        # Accept either a path to master.db or to the directory itself.
        if os.path.basename(db_path).lower() == "master.db":
            return os.path.dirname(db_path) or "."
        return db_path

    try:
        # pyrekordbox >= 0.2 exposes the default install dir; older
        # versions raise / return None. Either way: if we cannot resolve
        # it, the caller logs a warning and we no-op.
        from pyrekordbox.config import get_pioneer_install_dir
        install_dir = get_pioneer_install_dir()
        if install_dir is None:
            return None
        return str(install_dir)
    except Exception:
        return None
