"""
Duplicate-track detector.

Groups tracks by case-insensitive whitespace-normalized ``(artist, title)``
and surfaces buckets with two or more entries. Pure stdlib, no DB writes,
no ANLZ parsing — operates on whatever the caller hands it.

The library scan endpoint (``GET /api/duplicates``) and the CLI
(``autocue --find-duplicates``, future) both build their input by calling
``DjmdContent`` and projecting only the small subset of fields the
keeper-heuristic needs.

Phase 1 is **read-only**: this module never mutates anything. The future
"delete non-keepers" path will live in a separate ``apply_duplicates``
helper alongside ``db_writer.delete_track`` with the standard backup +
``rekordbox_is_running()`` guards.
"""
from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Iterable


_WS_RE = re.compile(r"\s+")


def normalize_key(artist: str | None, title: str | None) -> str:
    """Build the dedup key for a track.

    Lowercase + trim + collapse internal whitespace so trailing spaces and
    capitalisation don't fool the bucket. The format is
    ``"<artist>|||<title>"`` — the triple-pipe separator is chosen because
    it does not legally appear in Rekordbox metadata.

    Returns ``""`` when both fields are empty (caller should skip the row).
    """
    a = (artist or "").lower().strip()
    t = (title or "").lower().strip()
    a = _WS_RE.sub(" ", a)
    t = _WS_RE.sub(" ", t)
    if not a and not t:
        return ""
    return f"{a}|||{t}"


@dataclass
class TrackProjection:
    """The minimal per-track payload the dedup + keeper-heuristic needs.

    Kept small so the SSE stream stays under MTU and the JSON ingest cost
    of a 707-group / 1,570-track scan on a 3,775-track library is trivial.
    """

    track_id: int
    title: str = ""
    artist: str = ""
    bpm: float = 0.0
    key: str = ""
    existing_hot_cues: int = 0
    play_count: int = 0
    last_played: str | None = None  # ISO date string or None
    source: str = "file"             # "file" | "streaming" | "unknown"


@dataclass
class DuplicateGroup:
    artist: str            # echoed back un-normalised from the first row in the bucket
    title: str
    copies: list[TrackProjection]
    keeper_id: int = 0     # track_id picked by the keeper heuristic below

    def to_dict(self) -> dict:
        return {
            "artist": self.artist,
            "title": self.title,
            "copies": [
                {
                    "track_id": c.track_id,
                    "bpm": c.bpm,
                    "key": c.key,
                    "existing_hot_cues": c.existing_hot_cues,
                    "play_count": c.play_count,
                    "last_played": c.last_played,
                    "source": c.source,
                    "is_keeper": c.track_id == self.keeper_id,
                }
                for c in self.copies
            ],
        }


def pick_keeper(copies: Iterable[TrackProjection]) -> int:
    """Choose the row most likely to be the user's "real" copy.

    Heuristic (largest tuple wins via ``max``):
      1. highest play_count — reflects actual DJ use
      2. most existing hot cues — preserves cue-prep work
      3. most recent last_played — newer ISO-date string wins; missing
         (``None``) becomes ``""`` which loses against any real date
      4. smallest track_id — deterministic tie-break (negated so larger
         ``-track_id`` = smaller ``track_id`` wins ties)
    """
    return max(
        copies,
        key=lambda c: (
            c.play_count or 0,
            c.existing_hot_cues or 0,
            c.last_played or "",
            -c.track_id,
        ),
    ).track_id


def find_duplicate_groups(
    tracks: Iterable[TrackProjection],
) -> list[DuplicateGroup]:
    """Group tracks by ``normalize_key`` and return only buckets with ≥2 entries.

    Buckets are sorted by ``(-copy_count, artist, title)`` so the worst
    offenders surface first and ties break alphabetically — deterministic
    output for the SSE stream.

    Empty-metadata tracks (``normalize_key`` returns ``""``) are excluded
    — they form a bucket of their own that would mix unrelated streaming
    references and isn't a legitimate dedup target.
    """
    buckets: dict[str, list[TrackProjection]] = collections.defaultdict(list)
    for t in tracks:
        key = normalize_key(t.artist, t.title)
        if not key:
            continue
        buckets[key].append(t)

    groups: list[DuplicateGroup] = []
    for key, copies in buckets.items():
        if len(copies) < 2:
            continue
        artist, title = key.split("|||", 1)
        groups.append(
            DuplicateGroup(
                # Use the first copy's UNnormalised metadata so the UI
                # shows the user-recognisable spelling, not the lowercase
                # bucket key.
                artist=copies[0].artist or artist,
                title=copies[0].title or title,
                copies=copies,
                keeper_id=pick_keeper(copies),
            )
        )

    groups.sort(key=lambda g: (-len(g.copies), g.artist.lower(), g.title.lower()))
    return groups
