"""
Mix-in / mix-out loop generation.

Generates at most TWO loops per track from phrase + beatgrid data:

- **Mix In Loop** — the last 4/8 bars of the intro phrase: loop it while
  bringing the track in, release into the body.
- **Mix Out Loop** — the first 4/8 bars of the outro phrase: loop it to
  extend the mix-out window.

Quality bar: a clicking loop is worse than none, so candidates are rejected
aggressively. When librosa is installed (``pip install 'autocue[loops]'``)
each candidate's seam is validated on the real audio: the audio right after
the loop START must sound like the audio right after the loop END — that is
exactly what plays across the jump when the loop wraps. Candidates whose
seam similarity falls below :data:`SEAM_THRESHOLD` are dropped. Without
librosa, loops are grid/phrase-derived only and carry ``confidence`` 0.5.

Verdicts are cached in the sidecar cache keyed by ``anlz_mtime`` (same
invalidation contract as the energy curve).
"""
from __future__ import annotations

from ..models import PhraseLabel

# Cosine similarity across the loop seam below which a candidate is dropped.
# 1-second windows of chroma+MFCC means; steady intros/outros in the genres
# this targets sit well above this, hard section changes fall well below.
SEAM_THRESHOLD = 0.80
_SEAM_WINDOW_S = 1.0
_SR = 22050


def _have_librosa() -> bool:
    try:
        import librosa  # noqa: F401
        return True
    except ImportError:
        return False


def _phrase_candidates(content, db, *, phrase_cues=None) -> list[dict]:
    """Derive raw loop candidates from phrase + beatgrid data (no audio)."""
    from ..analyzer import analyze_track

    if phrase_cues is None:
        phrase_cues = analyze_track(content, db)
    bpm = float(getattr(content, "BPM", 0) or 0) / 100
    if not phrase_cues or bpm <= 0:
        return []
    bar_ms = 60_000.0 / bpm * 4  # 4/4 assumed, same as the cue generator
    dur_ms = int(float(getattr(content, "Length", 0) or 0) * 1000)
    cues = sorted((c for c in phrase_cues if c.slot >= 0), key=lambda c: c.position_ms)
    if not cues:
        return []

    def _bars(n: int) -> int:
        """Loop length for a phrase of n bars: 8 if it fits, else 4, else 0."""
        return 8 if n >= 8 else 4 if n >= 4 else 0

    candidates: list[dict] = []

    # Mix In Loop: the track must OPEN with an intro phrase, and the loop sits
    # at its end so releasing the loop drops straight into the body.
    first = cues[0]
    if first.label is PhraseLabel.INTRO:
        nxt = next((c for c in cues[1:] if c.position_ms > first.position_ms), None)
        if nxt is not None:
            intro_bars = first.phrase_bars or round(
                (nxt.position_ms - first.position_ms) / bar_ms
            )
            n = _bars(intro_bars)
            if n:
                end = int(nxt.position_ms)
                start = int(end - n * bar_ms)
                if start >= first.position_ms - 5:  # ms tolerance for rounding
                    candidates.append({
                        "start_ms": max(start, 0), "end_ms": end,
                        "name": "Mix In Loop", "kind": "mix_in", "bars": n,
                    })

    # Mix Out Loop: starts exactly at the last outro phrase boundary.
    outros = [c for c in cues if c.label is PhraseLabel.OUTRO]
    if outros:
        outro = outros[-1]
        outro_bars = outro.phrase_bars or (
            round((dur_ms - outro.position_ms) / bar_ms) if dur_ms else 0
        )
        n = _bars(outro_bars)
        if n:
            start = int(outro.position_ms)
            end = int(start + n * bar_ms)
            if dur_ms == 0 or end <= dur_ms - 100:
                candidates.append({
                    "start_ms": start, "end_ms": end,
                    "name": "Mix Out Loop", "kind": "mix_out", "bars": n,
                })

    return candidates


def seam_similarity(path: str, start_ms: int, end_ms: int) -> float | None:
    """Cosine similarity between what plays after the loop START and what
    would naturally play after the loop END. Returns None when it cannot be
    computed (no librosa, unreadable file, loop at EOF)."""
    try:
        import librosa
        import numpy as np
    except ImportError:
        return None
    try:
        w = _SEAM_WINDOW_S
        y1, _ = librosa.load(path, sr=_SR, mono=True, offset=start_ms / 1000, duration=w)
        y2, _ = librosa.load(path, sr=_SR, mono=True, offset=end_ms / 1000, duration=w)
        n = min(len(y1), len(y2))
        if n < int(0.5 * _SR * w):  # ran off the end of the file
            return None
        y1, y2 = y1[:n], y2[:n]

        def feats(y):
            chroma = librosa.feature.chroma_stft(y=y, sr=_SR).mean(axis=1)
            mfcc = librosa.feature.mfcc(y=y, sr=_SR, n_mfcc=13).mean(axis=1)
            parts = []
            for v in (chroma, mfcc):
                norm = float(np.linalg.norm(v)) or 1.0
                parts.append(v / norm)
            return np.concatenate(parts)

        a, b = feats(y1), feats(y2)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
        return float(np.dot(a, b) / denom)
    except Exception:
        return None


def generate_loops(content, db, *, cache=None, audio_check=True, stats=None) -> list[dict]:
    """Return 0-2 validated loops for a track:
    ``{start_ms, end_ms, name, kind, bars, confidence}``.

    ``confidence``: seam similarity when audio-validated; 0.5 ONLY in
    grid/phrase-only mode (librosa not installed). When librosa is available
    but a seam cannot be analyzed (missing/streaming/unreadable file, loop at
    EOF), the candidate is REJECTED — "a clicking loop is worse than none"
    also applies to loops nobody could check.

    ``stats``: optional Counter-like; increments per-track outcome buckets
    (accepted / no_candidates / no_audio_file / seam_rejected /
    seam_unreadable / grid_only) for calibration visibility.
    Verdicts are cached in ``cache`` (a CacheStore) keyed by anlz_mtime.
    """
    import os

    def _count(key):
        if stats is not None:
            stats[key] = stats.get(key, 0) + 1

    mtime = None
    if cache is not None:
        from .anlz_path import get_anlz_mtime
        mtime = get_anlz_mtime(content, db)
        if mtime is not None:
            cached = cache.get_loop_verdicts(content.ID, expected_anlz_mtime=mtime)
            if cached is not None:
                _count("accepted" if cached else "no_candidates_cached")
                return cached

    candidates = _phrase_candidates(content, db)
    accepted: list[dict] = []
    if not candidates:
        _count("no_candidates")
    elif audio_check and _have_librosa():
        from ..writer import _resolve_file_path
        path = _resolve_file_path(content)
        if not path or not os.path.exists(path):
            # streaming rows (e.g. FolderPath "spotify:track:…") or moved
            # files: no audio to validate against -> reject everything.
            _count("no_audio_file")
        else:
            for cand in candidates:
                sim = seam_similarity(path, cand["start_ms"], cand["end_ms"])
                if sim is None:
                    _count("seam_unreadable")  # dropped: unverifiable seam
                elif sim >= SEAM_THRESHOLD:
                    cand["confidence"] = round(sim, 3)
                    accepted.append(cand)
                else:
                    _count("seam_rejected")
            if accepted:
                _count("accepted")
    else:
        for cand in candidates:
            cand["confidence"] = 0.5
            accepted.append(cand)
        _count("grid_only")

    if cache is not None and mtime is not None:
        cache.put_loop_verdicts(content.ID, accepted, anlz_mtime=mtime)
    return accepted
