"""
Reads Rekordbox phrase structure and beat grid from ANLZ analysis files,
returning a list of CuePoints ready to be written back to the library.
"""
from __future__ import annotations

from pyrekordbox import MasterDatabase
from pyrekordbox.masterdb.models import DjmdContent

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

    pssi = anlz_ext.get_tag("PSSI")
    pqtz = anlz_dat.get_tag("PQTZ")

    if pssi is None or pqtz is None:
        return []

    beat_entries = pqtz.content.entries
    phrases = pssi.content.entries
    mood = pssi.content.mood

    cues: list[CuePoint] = []
    slot = 1

    for entry in phrases:
        if slot > MAX_HOT_CUES:
            break

        ms = _beat_to_ms(beat_entries, entry.beat)
        if ms is None:
            continue

        label = phrase_label(mood, entry.kind)
        cues.append(CuePoint(position_ms=ms, label=label, slot=slot))
        slot += 1

    return cues


def analyze_by_title(title: str, db: MasterDatabase) -> tuple[DjmdContent, list[CuePoint]] | None:
    """Look up a track by title and return (content, cues). Returns None if not found."""
    results = db.get_content(Title=title)
    if results is None:
        return None
    # get_content with no ID returns a Query; with a match it returns the object directly
    content = results if isinstance(results, DjmdContent) else None
    if content is None:
        # Try as a query
        try:
            content = results.first()
        except AttributeError:
            return None
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
