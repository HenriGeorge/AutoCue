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

# Importance order for non-mix-in slots (lower = earlier slot).
# Slot A is always the mix-in point (first non-Intro phrase). Slots B+ use this priority.
_SMART_PRIORITY: dict[PhraseLabel, int] = {
    PhraseLabel.CHORUS:  0,  # Drop
    PhraseLabel.UP:      1,  # Build
    PhraseLabel.OUTRO:   2,  # Outro
    PhraseLabel.VERSE:   3,  # Verse
    PhraseLabel.DOWN:    4,  # Break
    PhraseLabel.BRIDGE:  5,  # Bridge
    PhraseLabel.INTRO:   6,  # Intro — least useful as a hot cue
    PhraseLabel.UNKNOWN: 7,
}


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
    add_memory_cue: bool = False  # legacy alias for memory_cue_mode="load_only"
    memory_cue_mode: Literal["none", "load_only", "all"] = "none"
    add_fill_cues: bool = False
    # "smart": A=mix-in (first non-Intro), B=Drop, C=Build, … last=Intro
    # "sequential": slots assigned in chronological order (legacy behaviour)
    slot_priority: Literal["smart", "sequential"] = "smart"


def _resolve_memory_cue_mode(prefs: GenerationPrefs) -> Literal["none", "load_only", "all"]:
    """memory_cue_mode takes precedence; add_memory_cue=True is legacy alias for load_only."""
    if prefs.memory_cue_mode != "none":
        return prefs.memory_cue_mode
    if prefs.add_memory_cue:
        return "load_only"
    return "none"


def _apply_smart_slot_order(cues: list[CuePoint]) -> None:
    """Reassign hot cue slot numbers: A = mix-in point, B+ by musical importance.

    Slot A is always the first non-Intro phrase chronologically — the point a DJ
    presses to start the track during a transition. Remaining slots are ordered by
    _SMART_PRIORITY (Drop first, Intro last), with chronological tiebreaking.

    Mutates slots in-place; memory cues (slot=-1) and list order are untouched.
    """
    hot = [c for c in cues if c.slot >= 0]
    if not hot:
        return

    # Slot A = first non-Intro phrase in time (the mix-in point)
    chronological = sorted(hot, key=lambda c: c.position_ms)
    mix_in = next((c for c in chronological if c.label != PhraseLabel.INTRO), None)

    if mix_in is None:
        # All Intros — fall back to chronological order
        for i, c in enumerate(chronological):
            c.slot = i
        return

    mix_in.slot = 0
    # When a non-Intro phrase is the mix-in point, mark it so DJs know A = entry, not mid-track re-cue
    if mix_in.label != PhraseLabel.INTRO:
        base = mix_in.name or ""
        if base and "(Mix In)" not in base:
            mix_in.name = f"{base} (Mix In)"
        elif not base:
            mix_in.name = "Mix In"

    # Remaining slots sorted by importance, chronological within each tier
    remaining = [c for c in hot if c is not mix_in]
    remaining.sort(key=lambda c: (_SMART_PRIORITY.get(c.label, 7), c.position_ms))
    for i, c in enumerate(remaining):
        c.slot = i + 1


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
                             name=f"Bar {bar_number}", color_id=SLOT_COLORS[slot],
                             confidence=0.6))
        slot += 1
    return cues, "bar"


def _heuristic_strategy(content, db, prefs: GenerationPrefs) -> tuple[list[CuePoint], str]:
    """Every 30 s when no BPM or phrase data is available."""
    dur_ms = int(float(getattr(content, "Length", 300) or 300) * 1000)
    step = 30_000
    cues = [
        CuePoint(position_ms=i * step, label=PhraseLabel.UNKNOWN, slot=i,
                 name=f"{(i * 30) // 60}:{(i * 30) % 60:02d}",
                 color_id=SLOT_COLORS[i] if i < len(SLOT_COLORS) else 0,
                 confidence=0.3)
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
                # confidence=1.0 is the CuePoint default; phrase+beat data is fully reliable
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

    if prefs.add_fill_cues and mode_used == "phrase" and len(cues) < prefs.max_cues:
        from .analyzer import analyze_fills
        fills = analyze_fills(content, db)
        existing_ms = {c.position_ms for c in cues}
        for fill in fills:
            if len(cues) >= prefs.max_cues:
                break
            if all(abs(fill.position_ms - e) > 500 for e in existing_ms):
                fill.slot = len([c for c in cues if c.slot >= 0])
                # color_id intentionally left as 0 here; set from final slot after smart ordering below
                cues.append(fill)
                existing_ms.add(fill.position_ms)
        cues = sorted(cues, key=lambda c: c.position_ms)
        slot_idx = 0
        for c in cues:
            if c.slot >= 0:
                c.slot = slot_idx
                slot_idx += 1

    # Smart slot ordering: A=mix-in point, B=Drop, C=Build, … last=Intro (phrase mode only).
    # Applied after fill cues so all slots are numbered before reordering.
    if mode_used == "phrase" and prefs.slot_priority == "smart":
        _apply_smart_slot_order(cues)

    # Set fill cue colors using their final (post-smart-ordering) slot numbers.
    # Must run after _apply_smart_slot_order so slot values are stable.
    if prefs.add_fill_cues and mode_used == "phrase":
        for c in cues:
            if c.label == PhraseLabel.UNKNOWN and c.slot >= 0:
                c.color_id = SLOT_COLORS[c.slot % len(SLOT_COLORS)]

    effective_mcm = _resolve_memory_cue_mode(prefs)
    if effective_mcm != "none" and cues:
        mem_confidence = {"phrase": 1.0, "bar": 0.6, "heuristic": 0.3}.get(mode_used, 1.0)
        hot = [c for c in cues if c.slot >= 0]
        mem_cues: list[CuePoint] = []

        # Load Point: always added — at first phrase (phrase mode) or inizio_ms (bar/heuristic)
        load_pos = min(c.position_ms for c in hot) if hot else 0
        if mode_used != "phrase":
            load_pos = max(0, prefs.inizio_ms)
        mem_cues.append(CuePoint(
            position_ms=load_pos, label=PhraseLabel.UNKNOWN, slot=-1,
            name="Load Point", color_id=0, confidence=mem_confidence,
        ))

        if effective_mcm == "all" and mode_used == "phrase":
            # Mix-In: slot-0 hot cue (the mix-in point after smart ordering)
            mix_in_cue = next((c for c in cues if c.slot == 0), None)
            if mix_in_cue and abs(mix_in_cue.position_ms - load_pos) > 500:
                mem_cues.append(CuePoint(
                    position_ms=mix_in_cue.position_ms, label=PhraseLabel.UNKNOWN, slot=-1,
                    name="Mix In", color_id=5, confidence=mem_confidence,  # Green
                ))

            # Mix-Out: last OUTRO phrase
            outros = [c for c in hot if c.label == PhraseLabel.OUTRO]
            if outros:
                outro_pos = max(c.position_ms for c in outros)
                mem_cues.append(CuePoint(
                    position_ms=outro_pos, label=PhraseLabel.UNKNOWN, slot=-1,
                    name="Mix Out", color_id=3, confidence=mem_confidence,  # Orange
                ))

            # Warning: 16 bars before track end, only when outro is short (< 8 bars) or absent
            bpm_raw = float(getattr(content, "BPM", 0) or 0) / 100
            track_end_ms = int(float(getattr(content, "Length", 0) or 0) * 1000)
            if bpm_raw > 0 and track_end_ms > 0:
                bar_ms = 60000.0 / bpm_raw * 4
                outro_bars_approx = 0
                if outros:
                    outro_start_ms = max(c.position_ms for c in outros)
                    outro_bars_approx = max(0, round((track_end_ms - outro_start_ms) / bar_ms))
                if (not outros) or (outro_bars_approx < 8):
                    warning_pos = int(track_end_ms - 16 * bar_ms)
                    if warning_pos > 0:
                        existing_ms = {c.position_ms for c in mem_cues}
                        if all(abs(warning_pos - e) > 500 for e in existing_ms):
                            mem_cues.append(CuePoint(
                                position_ms=warning_pos, label=PhraseLabel.UNKNOWN, slot=-1,
                                name="Warning", color_id=2, confidence=mem_confidence,  # Red
                            ))

        # Sort by position — CDJ orders memory cues by insertion order, so position order = display order
        mem_cues.sort(key=lambda c: c.position_ms)
        cues = mem_cues + cues

    return cues, mode_used
