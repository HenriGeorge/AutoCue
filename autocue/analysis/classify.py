"""
Track classification: deterministic category scoring using BPM + PWAV energy + PSSI vocals.

Each track gets a score (0.0–1.0) for five DJ set categories. Multi-label: the highest
score is the primary category; tracks close to a boundary will score well on two.

BPM is stored in DjmdContent as integer × 100 (e.g., 14000 = 140.00 BPM).
"""
from __future__ import annotations

from .energy import get_energy_curve
from .score import get_mixability

CATEGORIES = ("warmup", "build", "peak", "after_hours", "closing")

_class_cache: dict = {}  # content.ID → classification dict

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
    """Return a 0.0–1.0 score for how well a track fits the given category."""
    neutral_energy = 0.5  # fallback when PWAV unavailable

    # All formulas: bpm_s acts as a gate (bpm_s=0 → score=0).
    # Energy/vocals only modulate within a valid BPM range.
    if category == "warmup":
        bpm_s = _trap(bpm, 75, 90, 120, 130)
        eng_s = _trap(energy_mean if energy_mean is not None else neutral_energy,
                      -0.1, 0.0, 0.35, 0.55)
        return bpm_s * (eng_s * 0.60 + 0.40)

    if category == "build":
        bpm_s   = _trap(bpm, 108, 118, 128, 140)
        eng_s   = _trap(energy_mean if energy_mean is not None else neutral_energy,
                        0.1, 0.3, 0.60, 0.75)
        vocal_f = 0.85 if vocal_proxy else 1.0
        return bpm_s * (eng_s * 0.60 + 0.40) * vocal_f

    if category == "peak":
        # Use peak energy (max of curve) — tracks with big drops classify correctly
        epeak  = energy_peak if energy_peak is not None else neutral_energy
        bpm_s  = _trap(bpm, 116, 126, 145, 158)
        eng_s  = _trap(epeak, 0.40, 0.60, 1.0, 1.01)
        vocal_f = 0.80 if vocal_proxy else 1.0
        return bpm_s * (eng_s * 0.60 + 0.40) * vocal_f

    if category == "after_hours":
        bpm_s   = _trap(bpm, 88, 100, 122, 134)
        eng_s   = _trap(energy_mean if energy_mean is not None else neutral_energy,
                        0.05, 0.2, 0.50, 0.65)
        vocal_f = 1.05 if vocal_proxy else 1.0  # slight boost — vocals fit after-hours
        return min(bpm_s * (eng_s * 0.60 + 0.40) * vocal_f, 1.0)

    if category == "closing":
        bpm_s = _trap(bpm, 55, 70, 105, 118)
        eng_s = _trap(energy_mean if energy_mean is not None else neutral_energy,
                      -0.1, 0.0, 0.35, 0.55)
        return bpm_s * (eng_s * 0.60 + 0.40)

    return 0.0


def get_classification(content, db) -> dict:
    """
    Return classification scores for all five categories plus the primary category.

    Always returns a result (never None) — tracks with missing data score neutrally.
    Keys: primary, label, color, scores {warmup, build, peak, after_hours, closing}.
    """
    tid = content.ID
    if tid in _class_cache:
        return _class_cache[tid]

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
    return result
