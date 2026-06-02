"""
Transition Assistant — scores how well track A transitions into track B.

Three components (no PSSI required):
  bpm_score   (0–100) — tempo compatibility, with half-time/double-time bonus
  key_score   (0–100) — Camelot wheel distance; circular; floor=0
  energy_score(0–100) — end-of-A energy vs start-of-B energy (averaged windows)

Overall = 0.40 × bpm + 0.35 × key + 0.25 × energy

Phrase alignment (optional bonus info) is omitted from MVP — requires PSSI
timestamps not available from get_mixability counts.
"""
from __future__ import annotations

import re

from .energy import get_energy_curve

_CAMELOT_RE = re.compile(r"^(\d{1,2})([AB])$", re.IGNORECASE)
_ENERGY_WINDOW = 5  # points to average for end/start energy


# ---------------------------------------------------------------------------
# BPM score
# ---------------------------------------------------------------------------

def _bpm_score(bpm_a: float, bpm_b: float) -> float:
    """0–100. Guards against zero BPM. Rewards half-time / double-time at 50."""
    if bpm_a <= 0 or bpm_b <= 0:
        return 50.0  # no BPM data — neutral

    # Half-time / double-time: ±4% tolerance around exact 2:1 to avoid a hard cliff
    # at the boundary (e.g. ratio 0.479 would otherwise score 0 while 0.481 scores 50)
    ratio = bpm_b / bpm_a
    if 0.46 <= ratio <= 0.54 or 1.85 <= ratio <= 2.15:
        return 50.0

    delta = abs(bpm_b - bpm_a) / bpm_a

    # Smooth decay: full score within ±3%, linear decay to 0 at ±10%
    if delta <= 0.03:
        return 100.0
    if delta >= 0.10:
        return 0.0
    # Linear from 100 at 3% to 0 at 10%
    return round(100.0 * (0.10 - delta) / 0.07, 1)


# ---------------------------------------------------------------------------
# Camelot key score
# ---------------------------------------------------------------------------

def _parse_camelot(key_str: str) -> tuple[int, str] | None:
    """Parse "8A" → (8, 'A'). Returns None if unparseable."""
    if not key_str:
        return None
    m = _CAMELOT_RE.match(key_str.strip())
    if not m:
        return None
    return int(m.group(1)), m.group(2).upper()


def _camelot_distance(num_a: int, num_b: int) -> int:
    """Circular distance on the 12-position wheel (0–6)."""
    diff = abs(num_a - num_b)
    return min(diff, 12 - diff)


def _key_score(key_a: str, key_b: str) -> float:
    """0–100. Floor = 0 (no artificial minimum for incompatible keys)."""
    pa = _parse_camelot(key_a)
    pb = _parse_camelot(key_b)
    if pa is None or pb is None:
        return 50.0  # unknown key — neutral

    num_a, letter_a = pa
    num_b, letter_b = pb

    same_number = (num_a == num_b)
    same_letter = (letter_a == letter_b)
    dist = _camelot_distance(num_a, num_b)

    if same_number and same_letter:
        return 100.0  # identical
    if dist == 0 and not same_letter:
        return 75.0   # e.g. 8A → 8B (same number, different ring)
    if dist == 1 and same_letter:
        return 80.0   # e.g. 8A → 7A or 9A
    if dist == 1 and not same_letter:
        return 60.0   # e.g. 8A → 7B
    if dist == 2:
        return 50.0
    if dist == 3:
        return 25.0
    return 0.0        # dist ≥ 4 — genuinely incompatible


# ---------------------------------------------------------------------------
# Energy score
# ---------------------------------------------------------------------------

def _window_avg(curve: list[float], window: int, from_end: bool) -> float:
    """Average the first or last `window` points of an energy curve."""
    if not curve:
        return 0.5
    n = min(window, len(curve))
    if from_end:
        return sum(curve[-n:]) / n
    return sum(curve[:n]) / n


def _energy_score(curve_a: list[float] | None, curve_b: list[float] | None) -> float:
    """0–100. Measures smoothness of energy handoff from end-of-A to start-of-B."""
    end_a = _window_avg(curve_a, _ENERGY_WINDOW, from_end=True) if curve_a else 0.5
    start_b = _window_avg(curve_b, _ENERGY_WINDOW, from_end=False) if curve_b else 0.5
    delta = abs(end_a - start_b)
    # Full score when delta ≤ 0.05; linear decay to 0 at delta = 0.5
    if delta <= 0.05:
        return 100.0
    if delta >= 0.5:
        return 0.0
    return round(100.0 * (0.5 - delta) / 0.45, 1)


# ---------------------------------------------------------------------------
# Overall transition score
# ---------------------------------------------------------------------------

def _bpm_explanation(bpm_a: float, bpm_b: float, score: float) -> str:
    if bpm_a <= 0 or bpm_b <= 0:
        return "BPM unknown — neutral"
    diff = abs(bpm_b - bpm_a)
    ratio = bpm_b / bpm_a
    if 0.46 <= ratio <= 0.54:
        return f"Half-time ({bpm_a:.0f}→{bpm_b:.0f} BPM)"
    if 1.85 <= ratio <= 2.15:
        return f"Double-time ({bpm_a:.0f}→{bpm_b:.0f} BPM)"
    if score == 100.0:
        return f"{diff:.1f} BPM difference — perfect"
    if score >= 60.0:
        return f"{diff:.1f} BPM difference — good"
    if score > 0:
        return f"{diff:.1f} BPM difference — marginal"
    return f"{diff:.1f} BPM difference — incompatible"


def _key_explanation(key_a: str, key_b: str, score: float) -> str:
    if not key_a or not key_b:
        return "Key unknown — neutral"
    if score == 100.0:
        return f"Same key ({key_a})"
    if score == 80.0:
        return f"{key_a}→{key_b} — adjacent (±1)"
    if score == 75.0:
        return f"{key_a}→{key_b} — parallel (same number)"
    if score == 60.0:
        return f"{key_a}→{key_b} — compatible"
    if score >= 50.0:
        return f"{key_a}→{key_b} — risky"
    if score >= 25.0:
        return f"{key_a}→{key_b} — clash"
    return f"{key_a}→{key_b} — incompatible"


def _energy_explanation(end_a: float | None, start_b: float | None, score: float) -> str:
    if end_a is None or start_b is None:
        return "Energy data unavailable"
    if score == 100.0:
        return "Smooth energy handoff"
    delta = abs(end_a - start_b)
    direction = "drops" if start_b < end_a else "jumps"
    if score >= 60.0:
        return f"Energy {direction} slightly ({delta:.0%})"
    if score > 0:
        return f"Energy {direction} ({delta:.0%}) — noticeable"
    return f"Energy {direction} sharply ({delta:.0%})"


def score_transition(content_a, content_b, db) -> dict:
    """
    Return a transition score dict for A → B.

    Keys: overall, bpm, key, energy, bpm_a, bpm_b, key_a, key_b,
          end_energy_a, start_energy_b, explanation.
    """
    # BPM
    raw_bpm_a = getattr(content_a, "BPM", 0) or 0
    raw_bpm_b = getattr(content_b, "BPM", 0) or 0
    bpm_a = float(raw_bpm_a) / 100.0
    bpm_b = float(raw_bpm_b) / 100.0
    bpm_s = _bpm_score(bpm_a, bpm_b)

    # Key
    key_a, key_b = "", ""
    try:
        k = getattr(content_a, "Key", None)
        if k:
            key_a = str(getattr(k, "ScaleName", "") or "")
    except Exception:
        pass
    try:
        k = getattr(content_b, "Key", None)
        if k:
            key_b = str(getattr(k, "ScaleName", "") or "")
    except Exception:
        pass
    key_s = _key_score(key_a, key_b)

    # Energy
    curve_a = get_energy_curve(content_a, db)
    curve_b = get_energy_curve(content_b, db)
    energy_s = _energy_score(curve_a, curve_b)

    end_energy_a = round(_window_avg(curve_a, _ENERGY_WINDOW, from_end=True), 3) if curve_a else None
    start_energy_b = round(_window_avg(curve_b, _ENERGY_WINDOW, from_end=False), 3) if curve_b else None

    # Weights: BPM 40%, key 35%, energy 25%
    overall = round(0.40 * bpm_s + 0.35 * key_s + 0.25 * energy_s, 1)

    explanation = [
        _bpm_explanation(bpm_a, bpm_b, bpm_s),
        _key_explanation(key_a, key_b, key_s),
        _energy_explanation(end_energy_a, start_energy_b, energy_s),
    ]

    return {
        "overall": overall,
        "bpm": round(bpm_s, 1),
        "key": round(key_s, 1),
        "energy": round(energy_s, 1),
        "bpm_a": round(bpm_a, 2),
        "bpm_b": round(bpm_b, 2),
        "key_a": key_a,
        "key_b": key_b,
        "end_energy_a": end_energy_a,
        "start_energy_b": start_energy_b,
        "explanation": explanation,
    }
