"""
Similar Track Discovery — cosine similarity on a 5-dim feature vector.

Vector dimensions (L2-normalized):
  [0] key_cos  = cos(camelot_angle)   circular Camelot key
  [1] key_sin  = sin(camelot_angle)
  [2] energy_mean
  [3] energy_variance (min×10, 1.0)
  [4] vocal_proxy (0 or 1.0)

BPM gate: candidates must be within ±8 BPM of the target.
BPM is excluded from the vector to avoid double-counting.
Intro/outro bars are excluded — they require PSSI; absence would cluster
every non-analyzed track at the origin.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Optional

from .energy import get_energy_curve
from .score import get_mixability

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Camelot key → angle
# ---------------------------------------------------------------------------

# Camelot wheel: 1A…12A, 1B…12B (24 positions evenly around a circle)
_CAMELOT_RE = re.compile(r"^(\d{1,2})([AB])$", re.IGNORECASE)

def _camelot_angle(key_str: str) -> float:
    """Return angle in radians for a Camelot key string (e.g. '8A', '11B').

    A-keys and B-keys share the same number position on the wheel; adjacent
    numbers are ±30° apart. Missing / unparseable key → 0.0.
    """
    if not key_str:
        return 0.0
    m = _CAMELOT_RE.match(key_str.strip())
    if not m:
        return 0.0
    number = int(m.group(1))   # 1–12
    # Letter distinguishes inner (A) and outer (B) ring — treat as same position
    # for BPM-gated similarity; the key compatibility detail belongs in Transition Assistant.
    return 2 * math.pi * (number - 1) / 12.0


# ---------------------------------------------------------------------------
# Feature vector
# ---------------------------------------------------------------------------

def _build_vector(
    key_str: str,
    energy_mean: float,
    energy_variance: float,
    vocal_proxy: bool,
) -> list[float]:
    angle = _camelot_angle(key_str)
    v = [
        math.cos(angle),
        math.sin(angle),
        float(energy_mean),
        min(energy_variance * 10.0, 1.0),
        1.0 if vocal_proxy else 0.0,
    ]
    # L2-normalize
    mag = math.sqrt(sum(x * x for x in v))
    if mag < 1e-9:
        return [0.0] * len(v)
    return [x / mag for x in v]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# In-process index
# ---------------------------------------------------------------------------

# track_id → (bpm, vector)
_INDEX: dict[int, tuple[float, list[float]]] = {}
_INDEX_BUILT = False


def _build_index(db) -> None:
    global _INDEX, _INDEX_BUILT
    _INDEX = {}
    try:
        contents = db.get_content().all()
    except Exception as exc:
        _log.warning("similar index: get_content failed: %s", exc)
        _INDEX_BUILT = True
        return

    errors = 0
    for content in contents:
        try:
            _index_track(content, db)
        except Exception as exc:
            errors += 1
            if errors <= 3:
                _log.warning("similar index: _index_track %s failed: %s", getattr(content, 'ID', '?'), exc)
    _log.info("similar index built: %d tracks, %d errors", len(_INDEX), errors)
    _INDEX_BUILT = True


def _index_track(content, db) -> None:
    """Compute and store a feature vector for one track."""
    from .classify import get_classification

    raw_bpm = getattr(content, "BPM", 0) or 0
    bpm = float(raw_bpm) / 100.0

    key_str = ""
    try:
        key_obj = getattr(content, "Key", None)
        if key_obj is not None:
            key_str = str(getattr(key_obj, "ScaleName", "") or "")
    except Exception:
        pass

    energy_mean = 0.5
    energy_variance = 0.0
    vocal_proxy = False

    curve = get_energy_curve(content, db)
    if curve:
        energy_mean = sum(curve) / len(curve)
        mean = energy_mean
        energy_variance = sum((x - mean) ** 2 for x in curve) / len(curve)

    mix = get_mixability(content, db)
    if mix:
        vocal_proxy = mix["vocal_proxy"]
        if mix.get("energy_variance") is not None:
            energy_variance = mix["energy_variance"]

    vector = _build_vector(key_str, energy_mean, energy_variance, vocal_proxy)
    _INDEX[int(content.ID)] = (bpm, vector)

    # Pre-populate the classification cache so setbuilder beam search is O(1)
    try:
        get_classification(content, db)
    except Exception:
        pass


def clear_index() -> None:
    global _INDEX, _INDEX_BUILT
    _INDEX = {}
    _INDEX_BUILT = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_similar(
    track_id: int,
    db,
    n: int = 10,
    bpm_gate: float = 8.0,
) -> list[dict]:
    """Return up to *n* tracks most similar to *track_id*.

    Each result dict: {track_id, score (0.0–1.0), bpm_diff}.
    Returns [] if the target track is not in the index.
    """
    global _INDEX_BUILT
    if not _INDEX_BUILT:
        _build_index(db)

    target = _INDEX.get(track_id)
    if target is None:
        # Try to index this specific track on demand via direct lookup
        try:
            content = db.get_content(ID=track_id)
            if content is not None:
                _index_track(content, db)
        except Exception:
            pass
        target = _INDEX.get(track_id)
        if target is None:
            return []

    target_bpm, target_vec = target

    results: list[tuple[float, int, float]] = []  # (score, id, bpm_diff)
    for tid, (bpm, vec) in _INDEX.items():
        if tid == track_id:
            continue
        bpm_diff = abs(bpm - target_bpm)
        if bpm_diff > bpm_gate:
            continue
        score = _dot(target_vec, vec)  # both L2-normalized → cosine similarity
        score = max(0.0, min(1.0, score))
        results.append((score, tid, bpm_diff))

    results.sort(key=lambda x: -x[0])
    return [
        {"track_id": tid, "score": round(score, 3), "bpm_diff": round(bpm_diff, 2)}
        for score, tid, bpm_diff in results[:n]
    ]
