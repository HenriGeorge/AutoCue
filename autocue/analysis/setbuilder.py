"""
Set Builder — builds a DJ set by greedy beam search over the track library.

Key design decisions:
- No full graph construction (O(n²)). Per step, uses find_similar() to get
  ~20 BPM-gated candidates, then scores only those with score_transition().
- Deduplication: each beam maintains a visited set — no track appears twice.
- Fallback: relaxes constraints progressively if no candidates pass hard filters.
- Energy constraint: soft penalty on energy slope, not a hard gate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .classify import get_classification
from . import similar as _similar_mod
from .similar import find_similar, _build_index
from .transitions import score_transition, _bpm_score


_log = logging.getLogger(__name__)

_BEAM_WIDTH = 5
_CANDIDATES_PER_STEP = 20
_MIN_TRANSITION_SCORE = 40.0  # initial threshold — relaxed if no candidates
_CATEGORY_SCORE_THRESHOLD = 0.3  # track must score ≥ this for target category


@dataclass
class SetTrack:
    track_id: int
    title: str
    artist: str
    bpm: float
    key: str
    category: str
    transition_score: float | None  # None for first track
    relaxed: bool = False           # True if placed via relaxed constraint pass


@dataclass
class _Beam:
    tracks: list[SetTrack] = field(default_factory=list)
    total_duration: float = 0.0  # seconds
    cumulative_score: float = 0.0
    visited: set[int] = field(default_factory=set)


def _category_order(prefs: dict) -> list[str]:
    """Return ordered list of categories given start/end BPM prefs."""
    start_bpm = prefs.get("start_bpm", 100.0)
    end_bpm = prefs.get("end_bpm", 140.0)
    if end_bpm >= start_bpm:
        # Ascending energy: warmup → build → peak
        return ["warmup", "build", "peak"]
    else:
        # Descending: peak → after_hours → closing
        return ["peak", "after_hours", "closing"]


def _target_category(step: int, total_steps: int, category_sequence: list[str]) -> str:
    """Map the current set position to a target category."""
    if total_steps <= 1:
        return category_sequence[0]
    idx = min(int(step / total_steps * len(category_sequence)), len(category_sequence) - 1)
    return category_sequence[idx]


def _energy_penalty(end_prev: float | None, start_next: float | None,
                    energy_mode: str) -> float:
    """Return 0–15 penalty for energy direction mismatch.

    Takes pre-computed scalar energy values (avg last/first window) rather than
    full curves — callers can reuse values already computed in score_transition.
    """
    if end_prev is None or start_next is None:
        return 0.0
    if energy_mode == "build" and start_next < end_prev - 0.15:
        return 15.0
    if energy_mode == "drop" and start_next > end_prev + 0.15:
        return 15.0
    if energy_mode == "flat" and abs(start_next - end_prev) > 0.15:
        return 15.0
    return 0.0


def _get_track_info(content) -> tuple[str, str, float, float]:
    """Return (title, artist, bpm, duration)."""
    title = str(getattr(content, "Title", None) or "")
    artist = str(getattr(content, "ArtistName", None) or "")
    raw_bpm = getattr(content, "BPM", 0) or 0
    bpm = float(raw_bpm) / 100.0
    duration = float(getattr(content, "Length", 0) or 0)
    return title, artist, bpm, duration


def build_set(
    db,
    start_bpm: float = 110.0,
    end_bpm: float = 135.0,
    duration_minutes: float = 60.0,
    energy_mode: str = "build",     # "build" | "flat" | "drop"
    bpm_step_max: float = 0.08,     # max BPM increase per step (8%)
    seed_track_id: int | None = None,
) -> dict:
    """
    Build a DJ set using beam search.

    Returns a dict: {
        "tracks": list[dict],           # track_id, title, artist, bpm, key, category,
                                        #   transition_score, relaxed
        "terminated_reason": str,       # "target_duration_reached" |
                                        #   "no_candidates_passed_thresholds" |
                                        #   "safety_cap_hit"
    }
    """
    if not _similar_mod._INDEX_BUILT:
        _log.info("setbuilder: building similarity index…")
        _build_index(db)

    target_duration_s = duration_minutes * 60.0
    cat_sequence = _category_order({"start_bpm": start_bpm, "end_bpm": end_bpm})

    # Estimate number of tracks needed (average 6 minutes per track)
    est_tracks = max(3, int(target_duration_s / 360))

    # --- SEED TRACK ---
    if seed_track_id is not None:
        seed_content = db.get_content(ID=seed_track_id)
    else:
        seed_content = _find_seed(db, start_bpm, cat_sequence[0])

    if seed_content is None:
        return {"tracks": [], "terminated_reason": "no_candidates_passed_thresholds"}

    seed_title, seed_artist, seed_bpm, seed_dur = _get_track_info(seed_content)
    seed_key = ""
    try:
        k = getattr(seed_content, "Key", None)
        if k:
            seed_key = str(getattr(k, "ScaleName", "") or "")
    except Exception:
        pass

    seed_class = get_classification(seed_content, db)

    initial_track = SetTrack(
        track_id=int(seed_content.ID),
        title=seed_title,
        artist=seed_artist,
        bpm=seed_bpm,
        key=seed_key,
        category=seed_class.get("primary", "warmup"),
        transition_score=None,
    )

    beams = [_Beam(
        tracks=[initial_track],
        total_duration=seed_dur,
        cumulative_score=0.0,
        visited={int(seed_content.ID)},
    )]

    step = 0
    terminated_reason = "target_duration_reached"

    while True:
        # Check if all beams have enough duration
        if all(b.total_duration >= target_duration_s for b in beams):
            terminated_reason = "target_duration_reached"
            break
        if step >= est_tracks * 3:  # safety cap — never infinite loop
            terminated_reason = "safety_cap_hit"
            break

        step += 1
        target_cat = _target_category(
            min(step, est_tracks - 1), est_tracks, cat_sequence
        )

        new_beams: list[_Beam] = []
        for beam in beams:
            current_track = beam.tracks[-1]
            current_content = db.get_content(ID=current_track.track_id)

            # --- Relaxation ladder ---
            # Each tier progressively loosens constraints; tracks placed via
            # a relaxed tier are flagged relaxed=True.
            # Candidate lists are cached by (category, category_min, bpm_step_max)
            # to avoid redundant find_similar calls across tiers that share inputs.
            scored: list[tuple[float, _Beam]] = []
            used_relaxed = False
            _cand_cache: dict[tuple, list] = {}

            for tier in _relaxation_tiers(bpm_step_max, target_cat):
                cache_key = (tier["category"], tier["category_min"], tier["bpm_step_max"])
                if cache_key not in _cand_cache:
                    _cand_cache[cache_key] = _get_candidates(
                        current_track.track_id, current_track.bpm, tier["category"],
                        beam.visited, db, tier["bpm_step_max"], start_bpm, end_bpm,
                        category_min=tier["category_min"],
                    )
                candidates = _cand_cache[cache_key]
                if not candidates:
                    continue

                for cand_id, cand_bpm, cand_key in candidates:
                    try:
                        cand_content = db.get_content(ID=cand_id)
                        if cand_content is None:
                            continue
                        ts = score_transition(current_content, cand_content, db)
                        overall = ts["overall"]
                        if overall < tier["transition_min"]:
                            continue

                        ep = _energy_penalty(ts["end_energy_a"], ts["start_energy_b"], energy_mode)
                        adjusted = overall - ep
                        cand_title, cand_artist, _, cand_dur = _get_track_info(cand_content)
                        cand_class = get_classification(cand_content, db)

                        new_track = SetTrack(
                            track_id=cand_id,
                            title=cand_title,
                            artist=cand_artist,
                            bpm=cand_bpm,
                            key=cand_key,
                            category=cand_class.get("primary", target_cat),
                            transition_score=round(overall, 1),
                            relaxed=used_relaxed,
                        )
                        new_beam = _Beam(
                            tracks=beam.tracks + [new_track],
                            total_duration=beam.total_duration + cand_dur,
                            cumulative_score=beam.cumulative_score + adjusted,
                            visited=beam.visited | {cand_id},
                        )
                        scored.append((adjusted, new_beam))
                    except Exception as exc:
                        _log.debug("setbuilder: candidate %s failed: %s", cand_id, exc)

                if scored:
                    break  # found candidates on this tier — don't relax further
                used_relaxed = True  # next tier is relaxed

            if not scored:
                new_beams.append(beam)  # all tiers exhausted — keep beam as-is
                continue

            scored.sort(key=lambda x: -x[0])
            for _, nb in scored[:_BEAM_WIDTH]:
                new_beams.append(nb)

        # Keep best _BEAM_WIDTH beams
        new_beams.sort(key=lambda b: -b.cumulative_score)
        beams = new_beams[:_BEAM_WIDTH]

        if not beams:
            terminated_reason = "no_candidates_passed_thresholds"
            break

    # Return best beam
    if not beams:
        return {"tracks": [], "terminated_reason": "no_candidates_passed_thresholds"}

    best = beams[0]
    return {
        "tracks": [
            {
                "track_id": t.track_id,
                "title": t.title,
                "artist": t.artist,
                "bpm": round(t.bpm, 2),
                "key": t.key,
                "category": t.category,
                "transition_score": t.transition_score,
                "relaxed": t.relaxed,
            }
            for t in best.tracks
        ],
        "terminated_reason": terminated_reason,
    }


def _relaxation_tiers(bpm_step_max: float, target_cat: str) -> list[dict]:
    """Return constraint tiers from strict to relaxed.

    Tier 0 — normal constraints.
    Tier 1 — loosen category_min (0.3 → 0.2).
    Tier 2 — loosen transition_min (40 → 30).
    Tier 3 — widen BPM window (+2%).
    Tier 4 — drop category filter entirely (None).
    """
    return [
        {"category": target_cat, "category_min": 0.3,  "transition_min": 40.0, "bpm_step_max": bpm_step_max},
        {"category": target_cat, "category_min": 0.2,  "transition_min": 40.0, "bpm_step_max": bpm_step_max},
        {"category": target_cat, "category_min": 0.2,  "transition_min": 30.0, "bpm_step_max": bpm_step_max},
        {"category": target_cat, "category_min": 0.2,  "transition_min": 30.0, "bpm_step_max": bpm_step_max + 0.02},
        {"category": None,       "category_min": None, "transition_min": 30.0, "bpm_step_max": bpm_step_max + 0.02},
    ]


def _find_seed(db, start_bpm: float, category: str):
    """Find a seed track close to start_bpm in the target category."""
    try:
        contents = list(db.get_content())
    except Exception:
        return None

    best_content = None
    best_score = -1.0
    for c in contents:
        try:
            raw_bpm = getattr(c, "BPM", 0) or 0
            bpm = float(raw_bpm) / 100.0
            if bpm <= 0:
                continue
            bpm_s = _bpm_score(start_bpm, bpm) / 100.0
            cls = get_classification(c, db)
            cat_s = cls.get("scores", {}).get(category, 0.0)
            score = bpm_s * 0.5 + cat_s * 0.5
            if score > best_score:
                best_score = score
                best_content = c
        except Exception:
            pass
    return best_content


def _get_candidates(
    track_id: int,
    current_bpm: float,
    target_cat: str | None,
    visited: set[int],
    db,
    bpm_step_max: float,
    start_bpm: float,
    end_bpm: float,
    category_min: float | None = _CATEGORY_SCORE_THRESHOLD,
) -> list[tuple[int, float, str]]:
    """Return list of (track_id, bpm, key) candidate tuples.

    target_cat=None and category_min=None skips the category filter entirely
    (used by the final relaxation tier).
    """
    # BPM gate: allow up to bpm_step_max increase, or small decrease
    bpm_lo = current_bpm * (1.0 - 0.03)
    bpm_hi = current_bpm * (1.0 + bpm_step_max)
    # Ensure we stay within overall start_bpm–end_bpm range
    bpm_gate = max(abs(bpm_hi - current_bpm), abs(current_bpm - bpm_lo), 8.0)

    similar = find_similar(track_id, db, n=_CANDIDATES_PER_STEP, bpm_gate=bpm_gate)
    results = []
    for item in similar:
        cid = item["track_id"]
        if cid in visited:
            continue
        try:
            content = db.get_content(ID=cid)
            if content is None:
                continue
            raw_bpm = getattr(content, "BPM", 0) or 0
            bpm = float(raw_bpm) / 100.0
            if bpm < bpm_lo or bpm > bpm_hi:
                continue
            # Category filter — skip if target_cat or category_min is None
            if target_cat is not None and category_min is not None:
                cls = get_classification(content, db)
                if cls.get("scores", {}).get(target_cat, 0.0) < category_min:
                    continue
            key = ""
            try:
                k = getattr(content, "Key", None)
                if k:
                    key = str(getattr(k, "ScaleName", "") or "")
            except Exception:
                pass
            results.append((cid, bpm, key))
        except Exception:
            pass
    return results
