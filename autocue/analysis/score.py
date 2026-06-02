"""
Mixability score: deterministic 0-100 score derived from PSSI phrase structure + PWAV energy.
Returns None for tracks without phrase analysis data.
"""
from __future__ import annotations

from ..analyzer import _get_pssi_and_pqtz, _beat_to_ms
from ..models import PhraseLabel, phrase_label
from .energy import get_energy_curve

# 32 bars = "perfect" intro/outro length (plan spec).
# Most house/techno tracks have 16-bar intros — those score 50%, 32+ scores 100%.
_INTRO_OUTRO_REFERENCE_BARS = 32

# Vocal penalty: 0.3 (vocals reduce mixability; instrumental = 1.0).
# Intentionally stricter than the midpoint to reflect DJ mixing reality.
_VOCAL_SCORE_WITH_VOCALS = 0.3

# Module-level cache: content.ID → result dict. Cleared in conftest.py autouse fixture.
_mixability_cache: dict[int, dict] = {}


def get_mixability(content, db) -> dict | None:
    """
    Return a mixability dict or None if phrase data is unavailable.

    Keys: score (0-100), intro_bars, outro_bars, phrase_count, vocal_proxy,
          energy_variance, outro_length_unknown, components {intro, outro, energy, vocals, structure}.
    """
    track_id = getattr(content, 'ID', None)
    if track_id is not None and int(track_id) in _mixability_cache:
        return _mixability_cache[int(track_id)]

    pssi_content, pqtz_content = _get_pssi_and_pqtz(content, db)
    if pssi_content is None or pqtz_content is None:
        return None

    phrases = pssi_content.entries
    if not phrases:
        return None

    mood = pssi_content.mood
    beat_entries = pqtz_content.entries

    avg_ms_per_beat: float | None = None
    if len(beat_entries) >= 2:
        span = beat_entries[-1].time - beat_entries[0].time
        if span > 0:
            avg_ms_per_beat = span / (len(beat_entries) - 1)

    phrase_ms_list = [_beat_to_ms(beat_entries, e.beat) for e in phrases]
    phrase_labels = [phrase_label(mood, e.kind) for e in phrases]

    def _bars_between(ms_a: int | None, ms_b: int | None) -> int:
        if ms_a is None or ms_b is None or avg_ms_per_beat is None or avg_ms_per_beat <= 0:
            return 0
        bar_ms = avg_ms_per_beat * 4
        return max(0, round((ms_b - ms_a) / bar_ms))

    # intro_bars: total bars covered by leading Intro phrase(s)
    intro_bars = 0
    for i, lbl in enumerate(phrase_labels):
        if lbl != PhraseLabel.INTRO:
            break
        next_ms = next((phrase_ms_list[j] for j in range(i + 1, len(phrase_ms_list))
                        if phrase_ms_list[j] is not None), None)
        intro_bars += _bars_between(phrase_ms_list[i], next_ms)

    # outro_bars: bars from start of last Outro phrase to track end.
    # When content.Length is missing/0, the outro length is unmeasurable — use neutral fallback
    # rather than penalising with 0 (metadata absence ≠ missing outro).
    outro_bars = 0
    track_end_ms = int((content.Length or 0) * 1000)
    outro_length_unknown = track_end_ms <= 0
    for i in range(len(phrase_labels) - 1, -1, -1):
        if phrase_labels[i] == PhraseLabel.OUTRO:
            if not outro_length_unknown:
                outro_bars = _bars_between(phrase_ms_list[i], track_end_ms)
            break

    phrase_count = len(phrases)
    vocal_proxy = any(lbl == PhraseLabel.VERSE for lbl in phrase_labels)

    # Energy variance from PWAV (neutral fallback when unavailable)
    energy_curve = get_energy_curve(content, db)
    if energy_curve and len(energy_curve) >= 2:
        mean = sum(energy_curve) / len(energy_curve)
        variance: float | None = sum((v - mean) ** 2 for v in energy_curve) / len(energy_curve)
    else:
        variance = None

    ref = _INTRO_OUTRO_REFERENCE_BARS
    intro_score = min(intro_bars / ref, 1.0)
    # Use neutral 0.5 when outro length is unmeasurable (Length missing from DB)
    outro_score = 0.5 if outro_length_unknown else min(outro_bars / ref, 1.0)
    energy_score = (1.0 - min(variance * 5, 1.0)) if variance is not None else 0.5
    vocal_score = _VOCAL_SCORE_WITH_VOCALS if vocal_proxy else 1.0
    phrase_score = min(phrase_count / 6.0, 1.0)

    score = max(0.0, min(100.0, (
        intro_score  * 0.25
        + outro_score  * 0.25
        + energy_score * 0.20
        + vocal_score  * 0.15
        + phrase_score * 0.15
    ) * 100))

    result = {
        "score": round(score),
        "intro_bars": intro_bars,
        "outro_bars": outro_bars,
        "phrase_count": phrase_count,
        "vocal_proxy": vocal_proxy,
        "energy_variance": round(variance, 4) if variance is not None else None,
        "outro_length_unknown": outro_length_unknown,
        "components": {
            "intro": round(intro_score * 100),
            "outro": round(outro_score * 100),
            "energy": round(energy_score * 100),
            "vocals": round(vocal_score * 100),
            "structure": round(phrase_score * 100),
        },
    }
    if track_id is not None:
        _mixability_cache[int(track_id)] = result
    return result
