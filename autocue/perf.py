"""Lightweight in-process performance instrumentation.

``perf_span(name)`` records start/stop wall-clock; results stream into a
fixed-size ring buffer. Zero overhead when ``AUTOCUE_PERF`` env var is
unset.

Used by analysis SSE endpoints to capture per-request latency for the
performance budgets in .agent/prd/PERFORMANCE_PRD.md §2.

See TASK-044.
"""
from __future__ import annotations

import os
import random
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Iterator

__all__ = [
    "perf_span",
    "is_enabled",
    "get_stats",
    "recent_spans",
    "clear",
]

_BUFFER_MAXLEN = 1000

_enabled: bool = os.environ.get("AUTOCUE_PERF", "0") == "1"
try:
    _sample_rate: float = float(os.environ.get("AUTOCUE_PERF_SAMPLE_RATE", "1.0"))
except (TypeError, ValueError):
    _sample_rate = 1.0

_buffer: deque[tuple[str, float, float]] = deque(maxlen=_BUFFER_MAXLEN)
_lock = threading.Lock()


def is_enabled() -> bool:
    """Return whether perf instrumentation is active."""
    return _enabled


@contextmanager
def perf_span(name: str) -> Iterator[None]:
    """Record wall-clock duration of the ``with`` block.

    No-op when ``AUTOCUE_PERF`` is unset. When enabled, samples per
    ``AUTOCUE_PERF_SAMPLE_RATE`` (default 1.0 = 100%).
    """
    if not _enabled or (_sample_rate < 1.0 and random.random() > _sample_rate):
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        dur_ms = (time.perf_counter() - start) * 1000.0
        with _lock:
            _buffer.append((name, start, dur_ms))


def recent_spans(limit: int = 100) -> list[tuple[str, float, float]]:
    """Return the most recent ``limit`` spans as ``(name, start_ts, duration_ms)``."""
    with _lock:
        if limit >= len(_buffer):
            return list(_buffer)
        return list(_buffer)[-limit:]


def get_stats(name: str) -> dict[str, float] | None:
    """Return ``{count, p50, p95, p99}`` for spans matching ``name``, or None."""
    with _lock:
        durations = [d for n, _start, d in _buffer if n == name]
    if not durations:
        return None
    durations.sort()
    n = len(durations)

    def _percentile(p: float) -> float:
        # Nearest-rank percentile — adequate for perf budget signal.
        idx = max(0, min(n - 1, int(round(p * n)) - 1))
        return durations[idx]

    return {
        "count": float(n),
        "p50": _percentile(0.50),
        "p95": _percentile(0.95),
        "p99": _percentile(0.99),
    }


def clear() -> None:
    """Drop all buffered spans. Test-only utility."""
    with _lock:
        _buffer.clear()
