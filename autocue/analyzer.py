"""
Reads Rekordbox phrase structure and beat grid from ANLZ analysis files,
returning a list of CuePoints ready to be written back to the library.
"""
from __future__ import annotations

from pyrekordbox import Rekordbox6Database as MasterDatabase
from pyrekordbox.db6 import DjmdContent

from .models import CuePoint, PhraseLabel, phrase_label

# Maximum hot cue slots Rekordbox supports (A–H)
MAX_HOT_CUES = 8


def _beat_to_ms(entries: list, beat_number: int) -> int | None:
    """Convert a 1-indexed beat number to milliseconds using the beat grid entries."""
    idx = beat_number - 1
    if idx < 0 or idx >= len(entries):
        return None
    return entries[idx].time  # already in ms


def analyze_track(content: DjmdContent, db: MasterDatabase) -> list[CuePoint]:
    """
    Return up to MAX_HOT_CUES CuePoints derived from Rekordbox's own phrase
    analysis for the given track. Returns an empty list if analysis data is
    unavailable.
    """
    anlz_ext = db.read_anlz_file(content, "EXT")
    anlz_dat = db.read_anlz_file(content, "DAT")

    if anlz_ext is None or anlz_dat is None:
        return []

    try:
        pssi = anlz_ext.get_tag("PSSI")
        pqtz = anlz_dat.get_tag("PQTZ")
    except (IndexError, KeyError):
        return []

    if pssi is None or pqtz is None:
        return []

    beat_entries = pqtz.content.entries
    phrases = pssi.content.entries
    mood = pssi.content.mood

    # Two-pass algorithm to ensure rare labels (Intro, Chorus, Outro) always get a slot
    # even when the track is dominated by repeating "Up"/"Down" phrases.

    # Pass 1: first occurrence of each unique PhraseLabel
    seen_labels: set[PhraseLabel] = set()
    taken_indices: set[int] = set()
    pass1: list[tuple[int, PhraseLabel]] = []  # (position_ms, label)

    for idx, entry in enumerate(phrases):
        ms = _beat_to_ms(beat_entries, entry.beat)
        if ms is None:
            continue
        lbl = phrase_label(mood, entry.kind)
        if lbl not in seen_labels:
            seen_labels.add(lbl)
            taken_indices.add(idx)
            pass1.append((ms, lbl))

    # Pass 2: fill remaining slots with phrases not already taken, in order
    pass2: list[tuple[int, PhraseLabel]] = []
    for idx, entry in enumerate(phrases):
        if len(pass1) + len(pass2) >= MAX_HOT_CUES:
            break
        if idx in taken_indices:
            continue
        ms = _beat_to_ms(beat_entries, entry.beat)
        if ms is None:
            continue
        lbl = phrase_label(mood, entry.kind)
        pass2.append((ms, lbl))

    # Combine, sort by position ascending, assign 0-indexed slots
    combined = sorted(pass1 + pass2, key=lambda x: x[0])
    cues: list[CuePoint] = [
        CuePoint(position_ms=ms, label=lbl, slot=slot)
        for slot, (ms, lbl) in enumerate(combined)
    ]
    return cues


def analyze_by_title(title: str, db: MasterDatabase) -> tuple[DjmdContent, list[CuePoint]] | None:
    """Look up a track by title and return (content, cues). Returns None if not found."""
    matches = db.get_content(Title=title).all()
    if len(matches) == 0:
        return None
    if len(matches) > 1:
        import sys
        print(
            f"Error: {len(matches)} tracks share the title {title!r}. "
            "Use --track-id instead:",
            file=sys.stderr,
        )
        for c in matches:
            path = (c.FolderPath or "") + (c.FileNameL or c.FileNameS or "")
            print(f"  ID={c.ID}  {path}", file=sys.stderr)
        return None
    content = matches[0]
    return content, analyze_track(content, db)


def analyze_by_id(track_id: int, db: MasterDatabase) -> tuple[DjmdContent, list[CuePoint]] | None:
    """Look up a track by its Rekordbox ID and return (content, cues). Returns None if not found."""
    content = db.get_content(ID=track_id)
    if content is None:
        return None
    return content, analyze_track(content, db)


def analyze_all(db: MasterDatabase) -> list[tuple[DjmdContent, list[CuePoint]]]:
    """Return (content, cues) for every track in the library that has phrase data."""
    results = []
    for content in db.get_content().all():
        cues = analyze_track(content, db)
        if cues:
            results.append((content, cues))
    return results
