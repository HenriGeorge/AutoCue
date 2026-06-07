"""
Track classification: deterministic category scoring using BPM + PWAV energy + PSSI vocals.

Each track gets a score (0.0–1.0) for five DJ set categories. Multi-label: the highest
score is the primary category; tracks close to a boundary will score well on two.

BPM is stored in DjmdContent as integer × 100 (e.g., 14000 = 140.00 BPM).
"""
from __future__ import annotations

from typing import Any

from .energy import get_energy_curve
from .score import get_mixability

CATEGORIES = ("warmup", "build", "peak", "after_hours", "closing")

_class_cache: dict = {}  # content.ID → classification dict
_cache_store = None  # type: Any | None — L2 sidecar, wired from serve/deps.py lifespan

_CATEGORY_LABELS = {
    "warmup":      "Warm-up",
    "build":       "Build",
    "peak":        "Peak",
    "after_hours": "After-hours",
    "closing":     "Closing",
}

_CATEGORY_COLORS = {
    "warmup":      "#6cc",
    "build":       "#fa0",
    "peak":        "#f44",
    "after_hours": "#a6f",
    "closing":     "#4af",
}


def _trap(value: float, lo_zero: float, lo_full: float, hi_full: float, hi_zero: float) -> float:
    """Trapezoidal membership: 1.0 in [lo_full, hi_full], 0.0 outside [lo_zero, hi_zero]."""
    if value <= lo_zero or value >= hi_zero:
        return 0.0
    if lo_full <= value <= hi_full:
        return 1.0
    if value < lo_full:
        return (value - lo_zero) / max(lo_full - lo_zero, 1e-9)
    return (hi_zero - value) / max(hi_zero - hi_full, 1e-9)


def _score_category(
    bpm: float,
    energy_mean: float | None,
    energy_peak: float | None,
    vocal_proxy: bool,
    category: str,
) -> float:
    """Return a 0.0–1.0 score for how well a track fits the given category.

    When energy_mean is None (no ANLZ data), eng_s defaults to 0.5, capping
    the score at ~0.70 — tracks are scored on BPM alone with uncertainty.
    """
    if category == "warmup":
        bpm_s = _trap(bpm, 75, 100, 100, 130)
        eng_s = _trap(energy_mean, -0.1, 0.12, 0.12, 0.55) if energy_mean is not None else 0.5
        return bpm_s * (eng_s * 0.60 + 0.40)

    if category == "build":
        bpm_s   = _trap(bpm, 108, 123, 123, 140)
        eng_s   = _trap(energy_mean, 0.1, 0.45, 0.45, 0.72) if energy_mean is not None else 0.5
        vocal_f = 0.85 if vocal_proxy else 1.0
        return bpm_s * (eng_s * 0.60 + 0.40) * vocal_f

    if category == "peak":
        epeak  = energy_peak
        eng_s  = _trap(epeak, 0.40, 0.60, 1.0, 1.01) if epeak is not None else 0.5
        bpm_s  = _trap(bpm, 116, 136, 136, 158)
        vocal_f = 0.80 if vocal_proxy else 1.0
        return bpm_s * (eng_s * 0.60 + 0.40) * vocal_f

    if category == "after_hours":
        bpm_s = _trap(bpm, 88, 107, 107, 132)
        eng_s = _trap(energy_mean, 0.05, 0.32, 0.32, 0.62) if energy_mean is not None else 0.5
        return bpm_s * (eng_s * 0.60 + 0.40)

    if category == "closing":
        bpm_s = _trap(bpm, 55, 88, 88, 118)
        eng_s = _trap(energy_mean, -0.1, 0.12, 0.12, 0.55) if energy_mean is not None else 0.5
        return bpm_s * (eng_s * 0.60 + 0.40)

    return 0.0


def set_cache_store(store) -> None:
    """Wire the L2 sidecar (CacheStore) — see TASK-014."""
    global _cache_store
    _cache_store = store


def get_classification(content, db) -> dict:
    """
    Return classification scores for all five categories plus the primary category.

    Always returns a result (never None) — tracks with missing data score neutrally.
    Keys: primary, label, color, scores {warmup, build, peak, after_hours, closing}.

    Cache layers (in order): L1 in-process dict → L2 sidecar CacheStore (if wired) →
    compute. L2 lookups are keyed by ``(content.ID, anlz_mtime)``.
    """
    tid = content.ID
    if tid in _class_cache:
        return _class_cache[tid]

    # L2 lookup — sidecar stores the full classification dict as JSON in
    # the schema's ``scores_json`` slot (we keep ``primary_cat`` typed
    # separately for query-by-primary later if needed).
    if _cache_store is not None:
        from .anlz_path import get_anlz_mtime
        from ..cache import MISSING
        try:
            mtime = get_anlz_mtime(content, db)
            l2 = _cache_store.get_classification(tid, expected_anlz_mtime=mtime)
        except Exception:
            l2 = None
        if l2 is MISSING:
            # ANLZ missing — caller still wants a neutral-default dict.
            # Don't cache this in L1; let compute run once below and produce
            # the actual neutral result.
            pass
        elif l2 is not None:
            # Stored shape: l2 == {"primary": str, "scores": dict, "bpm": float, "energy_mean": float}
            # plus any extra fields packed into "scores" via the round-tripped json.
            scores_payload = l2.get("scores", {}) or {}
            # ``scores_payload`` may carry the full result merged in (see
            # write-through below); if so, reuse it. Otherwise rebuild a
            # minimal valid result from the typed columns.
            if isinstance(scores_payload, dict) and "scores" in scores_payload:
                result = scores_payload
            else:
                primary = l2["primary"]
                result = {
                    "primary": primary,
                    "label": _CATEGORY_LABELS.get(primary, primary),
                    "color": _CATEGORY_COLORS.get(primary, "#888"),
                    "confidence": round(max(scores_payload.values()) if scores_payload else 0.0, 3),
                    "scores": scores_payload,
                    "bpm": l2.get("bpm"),
                    "energy_mean": l2.get("energy_mean"),
                    "energy_peak": None,
                    "vocal_proxy": False,
                }
            _class_cache[tid] = result
            return result

    raw_bpm = getattr(content, "BPM", 0) or 0
    bpm = float(raw_bpm) / 100.0

    energy_mean: float | None = None
    energy_peak: float | None = None
    curve = get_energy_curve(content, db)
    if curve:
        energy_mean = sum(curve) / len(curve)
        energy_peak = max(curve)

    vocal_proxy = False
    mix = get_mixability(content, db)
    if mix:
        vocal_proxy = mix["vocal_proxy"]

    scores: dict[str, float] = {}
    for cat in CATEGORIES:
        scores[cat] = round(_score_category(bpm, energy_mean, energy_peak, vocal_proxy, cat), 3)

    primary = max(scores, key=lambda k: scores[k]) if any(scores.values()) else "unknown"
    top_score = scores.get(primary, 0.0)

    result = {
        "primary": primary,
        "label": _CATEGORY_LABELS.get(primary, primary),
        "color": _CATEGORY_COLORS.get(primary, "#888"),
        "confidence": round(top_score, 3),
        "scores": scores,
        "bpm": round(bpm, 2),
        "energy_mean": round(energy_mean, 3) if energy_mean is not None else None,
        "energy_peak": round(energy_peak, 3) if energy_peak is not None else None,
        "vocal_proxy": vocal_proxy,
    }
    _class_cache[tid] = result

    # Write-through to L2. Pack the FULL result into scores_json so the
    # reader can reuse it without recomputing energy_peak / vocal_proxy.
    if _cache_store is not None:
        from .anlz_path import get_anlz_mtime, MISSING_MTIME
        try:
            mtime = get_anlz_mtime(content, db)
            if mtime is None:
                _cache_store.put_classification(
                    tid, "unknown", {}, None, None, anlz_mtime=MISSING_MTIME
                )
            else:
                _cache_store.put_classification(
                    tid,
                    primary_cat=primary,
                    scores=result,  # store the full dict; reader reuses on hit
                    bpm=result["bpm"],
                    energy_mean=result["energy_mean"],
                    anlz_mtime=mtime,
                )
        except Exception:
            pass

    return result
