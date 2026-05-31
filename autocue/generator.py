"""
Per-track cue generation with automatic strategy fallback.

Priority order (mode="auto"):
  1. Phrase analysis (ANLZ data)  → "phrase"
  2. Bar-interval from BPM        → "bar"
  3. 30-second heuristic          → "heuristic"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .analyzer import analyze_track
from .models import CuePoint, LABEL_COLORS, PhraseLabel, SLOT_COLORS

MAX_HOT_CUES = 8


@dataclass
class TrackCapability:
    has_phrase: bool
    has_beats: bool
    bpm: float | None
    duration_ms: int | None


@dataclass
class GenerationPrefs:
    mode: Literal["phrase", "bar", "auto"] = "auto"
    bars_interval: int = 16
    start_bar: int = 1
    max_cues: int = MAX_HOT_CUES
    inizio_ms: int = 0  # first-beat offset in milliseconds
    add_memory_cue: bool = False


def detect_capability(
    content, db, *, phrase_cues: list | None = None
) -> TrackCapability:
    """Inspect a track and report which data sources are available.

    Pass phrase_cues to reuse already-computed ANLZ results and skip a second parse.
    """
    if phrase_cues is None:
        phrase_cues = analyze_track(content, db)
    bpm_raw = getattr(content, "BPM", None)
    dur_raw = getattr(content, "Length", None)
    return TrackCapability(
        has_phrase=len(phrase_cues) > 0,
        has_beats=bool(bpm_raw),
        # DB stores BPM as int×100 (e.g. 14000 = 140.00 BPM)
        bpm=float(bpm_raw) / 100 if bpm_raw else None,
        duration_ms=int(float(dur_raw) * 1000) if dur_raw else None,
    )


def _bar_strategy(content, db, prefs: GenerationPrefs) -> tuple[list[CuePoint], str]:
    bpm = float(content.BPM) / 100  # DB stores BPM as int×100
    if bpm <= 0:
        return [], "bar"
    bar_ms = int((60_000.0 / bpm) * 4)  # 4/4 assumed
    dur_ms = int(float(getattr(content, "Length", 0) or 0) * 1000)
    cues: list[CuePoint] = []
    slot = 0
    for i in range(prefs.max_cues + 64):  # extra headroom for negative inizio skips
        if slot >= prefs.max_cues:
            break
        pos = prefs.inizio_ms + (prefs.start_bar - 1 + i * prefs.bars_interval) * bar_ms
        if pos < 0:
            continue
        if dur_ms > 0 and pos >= dur_ms:
            break
        bar_number = prefs.start_bar + i * prefs.bars_interval
        cues.append(CuePoint(position_ms=pos, label=PhraseLabel.UNKNOWN, slot=slot,
                             name=f"Bar {bar_number}", color_id=SLOT_COLORS[slot]))
        slot += 1
    return cues, "bar"


def _heuristic_strategy(content, db, prefs: GenerationPrefs) -> tuple[list[CuePoint], str]:
    """Every 30 s when no BPM or phrase data is available."""
    dur_ms = int(float(getattr(content, "Length", 300) or 300) * 1000)
    step = 30_000
    cues = [
        CuePoint(position_ms=i * step, label=PhraseLabel.UNKNOWN, slot=i,
                 name=f"{(i * 30) // 60}:{(i * 30) % 60:02d}",
                 color_id=SLOT_COLORS[i] if i < len(SLOT_COLORS) else 0)
        for i in range(prefs.max_cues)
        if i * step < dur_ms
    ]
    return cues, "heuristic"


def generate_cues_for_track(
    content,
    db,
    prefs: GenerationPrefs | None = None,
) -> tuple[list[CuePoint], str]:
    """
    Return (cues, mode_used) for a track using the best available strategy.

    mode="auto"   → phrase → bar → heuristic (first strategy that yields cues)
    mode="phrase" → phrase only  (returns [] if no ANLZ data, no fallback)
    mode="bar"    → bar only     (returns [] if no BPM)

    If prefs.add_memory_cue is True, a Kind=0 memory cue (slot=-1) is prepended
    to the result whenever hot cues were found.
    """
    if prefs is None:
        prefs = GenerationPrefs()

    cues: list[CuePoint] = []
    mode_used = "bar"

    if prefs.mode in ("phrase", "auto"):
        phrase_cues = analyze_track(content, db)
        if phrase_cues:
            for c in phrase_cues:
                c.color_id = LABEL_COLORS.get(c.label.value, 0)
            cues, mode_used = phrase_cues, "phrase"
        elif prefs.mode == "phrase":
            # Explicit phrase mode with no ANLZ data — return empty, skip memory cue too
            return [], "phrase"

    if not cues:
        bpm_ok = float(getattr(content, "BPM", 0) or 0) / 100 > 0
        if prefs.mode == "bar":
            if bpm_ok:
                cues, mode_used = _bar_strategy(content, db, prefs)
            else:
                return [], "bar"
        elif prefs.mode == "auto":
            if bpm_ok:
                cues, mode_used = _bar_strategy(content, db, prefs)
            else:
                cues, mode_used = _heuristic_strategy(content, db, prefs)

    if prefs.add_memory_cue and cues:
        mem_pos = cues[0].position_ms if mode_used == "phrase" else max(0, prefs.inizio_ms)
        cues = [CuePoint(
            position_ms=mem_pos,
            label=PhraseLabel.UNKNOWN,
            slot=-1,
            name="Auto Cue",
            color_id=0,
        )] + cues

    return cues, mode_used
