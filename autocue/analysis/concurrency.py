"""Shared ThreadPoolExecutor primitive for AutoCue's analysis fanouts.

Used by /api/generate-apply-stream, /api/health, /api/classify, /api/auto-tag,
/api/enrich-comments/stream, and similar.py index build. Centralizes pool
size so operators have one knob (AUTOCUE_POOL_SIZE).

See .agent/prd/PERFORMANCE_PRD.md TASK-001 / TASK-002..007 / TASK-008.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

__all__ = ["pool_size", "get_pool", "shutdown_pool"]

_DEFAULT_MAX_WORKERS = 8

_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def pool_size() -> int:
    """Return the configured pool size.

    Defaults to ``min(8, os.cpu_count() or 1)``. Override with
    ``AUTOCUE_POOL_SIZE`` env var. Values < 1 clamp to 1; non-integer
    overrides raise ``ValueError``.
    """
    raw = os.environ.get("AUTOCUE_POOL_SIZE")
    if raw is None or raw == "":
        cpu = os.cpu_count() or 1
        return max(1, min(_DEFAULT_MAX_WORKERS, cpu))
    try:
        n = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"AUTOCUE_POOL_SIZE must be an integer, got {raw!r}"
        ) from exc
    return max(1, n)


def get_pool() -> ThreadPoolExecutor:
    """Return the process-singleton ThreadPoolExecutor (lazy-init)."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=pool_size(),
                thread_name_prefix="autocue-pool",
            )
        return _pool


def shutdown_pool() -> None:
    """Cleanly shut down the singleton pool. Idempotent."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            # cancel_futures=True drops queued-but-not-started work so a
            # backlog (or a stuck worker on a slow ANLZ read) can't hang the
            # FastAPI lifespan teardown indefinitely. Running futures still
            # complete because of wait=True.
            _pool.shutdown(wait=True, cancel_futures=True)
            _pool = None
