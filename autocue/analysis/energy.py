"""
PWAV waveform energy curve extraction.

Reads Rekordbox's waveform preview (PWAV tag, .DAT analysis files).
Each PWAV entry is a single byte: amplitude = byte & 0x1F (0–31), color = (byte & 0xE0) >> 5.
"""
from __future__ import annotations

from typing import Any, Literal

# Session-level in-memory cache: (track_id, n_points) → list[float] | None
_cache: dict[tuple, list[float] | None] = {}


def _read_pwav_amplitudes(anlz_dat) -> list[int] | None:
    """Return raw PWAV amplitude values (each 0–31) or None if unavailable."""
    try:
        tag = anlz_dat.get_tag("PWAV")
        if tag is None:
            return None
        entries = tag.content.entries
        if not entries:
            return None
        return [int(e) & 0x1F for e in entries]
    except Exception:
        return None


def _smooth_3(values: list[float]) -> list[float]:
    """3-sample symmetric rolling average (preserves length)."""
    n = len(values)
    if n < 3:
        return list(values)
    out = [values[0]]
    for i in range(1, n - 1):
        out.append((values[i - 1] + values[i] + values[i + 1]) / 3.0)
    out.append(values[-1])
    return out


def _downsample_avg(values: list[float], n: int) -> list[float]:
    """Chunk-average a list of floats to exactly n values."""
    total = len(values)
    if total == 0:
        return []
    if total <= n:
        return list(values)
    result: list[float] = []
    for i in range(n):
        start = int(i * total / n)
        end = int((i + 1) * total / n)
        end = max(end, start + 1)
        chunk = values[start:end]
        result.append(sum(chunk) / len(chunk))
    return result


def classify_energy_profile(
    curve: list[float],
) -> Literal["flat", "build", "drop-then-flat", "wave"]:
    """
    Classify an energy curve into one of four profiles.

    flat          — low variance throughout
    build         — energy rises in the second half
    drop-then-flat — early peak, lower energy in second half
    wave          — two or more distinct energy crests
    """
    if len(curve) < 4:
        return "flat"
    n = len(curve)
    mean = sum(curve) / n
    variance = sum((v - mean) ** 2 for v in curve) / n
    if variance < 0.05:
        return "flat"

    # Count local maxima (strict peaks)
    peaks = [
        i for i in range(1, n - 1)
        if curve[i] > curve[i - 1] and curve[i] > curve[i + 1]
    ]
    if len(peaks) >= 2:
        return "wave"

    first_mean = sum(curve[: n // 2]) / (n // 2)
    second_mean = sum(curve[n // 2 :]) / (n - n // 2)

    if second_mean > first_mean + 0.05:
        return "build"

    return "drop-then-flat"


# L2 cache: optional sidecar CacheStore. set via set_cache_store() from the
# server lifespan; left None in CLI / test contexts so the existing L1 + compute
# path is unchanged. See TASK-013 in .agent/prd/PERFORMANCE_PRD.md.
_cache_store = None  # type: Any | None


def set_cache_store(store) -> None:
    """Wire the sidecar CacheStore so L2 hits short-circuit compute.

    Call once from serve/deps.py lifespan after :func:`CacheStore.open_for`.
    Pass ``None`` to detach (used by tests).
    """
    global _cache_store
    _cache_store = store


def get_energy_curve(content, db, n_points: int = 50) -> list[float] | None:
    """
    Return a normalized energy curve of length n_points (values 0.0–1.0).
    Returns None if PWAV data is unavailable for this track.

    Cache layers (in order):
      L1 (in-process dict)  → L2 (CacheStore sidecar, if wired) → compute.
    Results are populated into L1 always; into L2 only when wired.
    L2 lookups are keyed by ``(content.ID, anlz_mtime)``.
    """
    from ..perf import perf_span

    cache_key = (content.ID, n_points)
    if cache_key in _cache:
        with perf_span("energy.L1.hit"):
            return _cache[cache_key]

    # L2 lookup (sidecar).
    if _cache_store is not None and n_points == 50:
        # Sidecar stores at default n_points only; non-default callers
        # fall through to compute + L1 cache (rare path).
        from .anlz_path import get_anlz_mtime, MISSING_MTIME
        from ..cache import MISSING
        with perf_span("energy.L2.lookup"):
            try:
                mtime = get_anlz_mtime(content, db)
                l2 = _cache_store.get_energy_curve(
                    content.ID, expected_anlz_mtime=mtime
                )
            except Exception:
                l2 = None
        if l2 is MISSING:
            _cache[cache_key] = None
            return None
        if l2 is not None:
            _cache[cache_key] = l2
            return l2

    curve: list[float] | None = None
    with perf_span("energy.compute"):
        try:
            anlz_dat = db.read_anlz_file(content, "DAT")
            if anlz_dat is not None:
                raw = _read_pwav_amplitudes(anlz_dat)
                if raw and len(raw) >= 2:
                    normalized = [v / 31.0 for v in raw]
                    smoothed = _smooth_3(normalized)
                    curve = _downsample_avg(smoothed, n_points)
        except Exception:
            pass

    _cache[cache_key] = curve

    # Write-through to L2.
    if _cache_store is not None and n_points == 50:
        try:
            from .anlz_path import get_anlz_mtime, MISSING_MTIME
            mtime = get_anlz_mtime(content, db)
            if mtime is None:
                _cache_store.put_energy_curve(
                    content.ID, [], anlz_mtime=MISSING_MTIME
                )
            elif curve is not None:
                _cache_store.put_energy_curve(content.ID, curve, anlz_mtime=mtime)
        except Exception:
            pass

    return curve


def clear_cache() -> None:
    """Clear the energy cache (e.g., after a DB restore)."""
    _cache.clear()
