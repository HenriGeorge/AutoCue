"""
Similar Track Discovery — cosine similarity on a 6-dim feature vector.

Vector dimensions (L2-normalized):
  [0] key_cos  = cos(camelot_angle)   circular Camelot key
  [1] key_sin  = sin(camelot_angle)
  [2] energy_mean (0.0 when no ANLZ data)
  [3] energy_variance (×10, capped 1.0)
  [4] vocal_proxy (0 or 1.0)
  [5] bpm_norm  = bpm / 200.0 (capped 1.0)

BPM gate: candidates must be within ±8 BPM of the target.
Intro/outro bars are excluded — they require PSSI; absence would cluster
every non-analyzed track at the origin.

A-ring and B-ring keys at the same number position are offset by π/12 (15°)
so that relative major/minor pairs are distinguished geometrically.
"""
from __future__ import annotations

import logging
import math
import re
import threading
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

    A-ring keys map to base positions (0, π/6, π/3, …).
    B-ring keys are offset by +π/12 (15°) — encodes relative major/minor
    proximity without adding a separate dimension.
    Missing / unparseable key → 0.0.
    """
    if not key_str:
        return 0.0
    m = _CAMELOT_RE.match(key_str.strip())
    if not m:
        return 0.0
    number = int(m.group(1))   # 1–12
    letter = m.group(2).upper()
    base_angle = 2 * math.pi * (number - 1) / 12.0
    ring_offset = math.pi / 12.0 if letter == 'B' else 0.0
    return base_angle + ring_offset


# ---------------------------------------------------------------------------
# Feature vector
# ---------------------------------------------------------------------------

def _build_vector(
    key_str: str,
    energy_mean: float,
    energy_variance: float,
    vocal_proxy: bool,
    bpm: float,
) -> list[float]:
    angle = _camelot_angle(key_str)
    v = [
        math.cos(angle),
        math.sin(angle),
        float(energy_mean),
        min(energy_variance * 10.0, 1.0),
        1.0 if vocal_proxy else 0.0,
        min(float(bpm) / 200.0, 1.0),
    ]
    mag = math.sqrt(sum(x * x for x in v))
    if mag < 1e-9:
        return [0.0] * len(v)
    return [x / mag for x in v]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# In-process index
# ---------------------------------------------------------------------------

# track_id → (bpm, vector, has_energy)
_INDEX: dict[int, tuple[float, list[float], bool]] = {}
_INDEX_BUILT = False
_INDEX_LOCK = threading.Lock()


def _build_index(db) -> None:
    global _INDEX, _INDEX_BUILT
    if not _INDEX_LOCK.acquire(blocking=False):
        with _INDEX_LOCK:  # wait for the in-progress build to finish, then return
            return
    try:
        if _INDEX_BUILT:
            return
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
    finally:
        _INDEX_LOCK.release()


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

    energy_mean = 0.0
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

    vector = _build_vector(key_str, energy_mean, energy_variance, vocal_proxy, bpm)
    _INDEX[int(content.ID)] = (bpm, vector, bool(curve))

    # Pre-populate the classification cache so setbuilder beam search is O(1)
    try:
        get_classification(content, db)
    except Exception:
        pass


def clear_index() -> None:
    global _INDEX, _INDEX_BUILT
    with _INDEX_LOCK:
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

    target_bpm, target_vec, target_has_e = target

    results: list[tuple[float, int, float]] = []  # (score, id, bpm_diff)
    for tid, (bpm, vec, has_e) in _INDEX.items():
        if tid == track_id:
            continue
        bpm_diff = abs(bpm - target_bpm)
        if bpm_diff > bpm_gate:
            continue
        score = _dot(target_vec, vec)
        score = max(0.0, min(1.0, score))
        # Data-quality cap: prevents same-BPM no-data tracks from all scoring 100%
        if not target_has_e and not has_e:
            score = min(score, 0.65)
        elif not target_has_e or not has_e:
            score = min(score, 0.82)
        # BPM distance penalty (capped at 15% to avoid double-penalising setbuilder)
        bpm_penalty = min(bpm_diff / 20.0, 0.15)
        score = round(score * (1.0 - bpm_penalty), 3)
        results.append((score, tid, bpm_diff))

    results.sort(key=lambda x: -x[0])
    return [
        {"track_id": tid, "score": round(score, 3), "bpm_diff": round(bpm_diff, 2)}
        for score, tid, bpm_diff in results[:n]
    ]
