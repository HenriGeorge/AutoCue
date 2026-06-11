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


def _duration_bucket(duration: float | None) -> int:
    """Bucket a track duration into a 5-second slot.

    Phase 3: stops "Album Mix 4:12" from grouping with "Extended Mix 6:48"
    just because they share an (artist, title). Within ±5 s (one full
    bucket span on either side) two tracks still collide — which is the
    realistic noise floor for ID3 vs container-vs-stream length
    rounding. Returns 0 when duration is unknown so phase 1 / phase 2
    callers (which never projected duration) keep the same bucket key
    and don't see a behaviour change.
    """
    if not duration or duration <= 0:
        return 0
    return int(round(float(duration) / 5.0))


def normalize_key(
    artist: str | None,
    title: str | None,
    duration: float | None = None,
) -> str:
    """Build the dedup key for a track.

    Lowercase + trim + collapse internal whitespace so trailing spaces and
    capitalisation don't fool the bucket. The format is
    ``"<artist>|||<title>|||<duration_bucket>"`` — the triple-pipe
    separator is chosen because it does not legally appear in Rekordbox
    metadata. The duration bucket is ``round(duration / 5)``; ``None``
    or non-positive durations bucket to ``0`` so existing callers that
    omit ``duration`` keep their original key shape.

    Returns ``""`` when artist + title are both empty (caller should
    skip the row — empty-metadata streaming tracks would otherwise mix
    into one fake bucket regardless of duration).
    """
    a = (artist or "").lower().strip()
    t = (title or "").lower().strip()
    a = _WS_RE.sub(" ", a)
    t = _WS_RE.sub(" ", t)
    if not a and not t:
        return ""
    return f"{a}|||{t}|||{_duration_bucket(duration)}"


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
    # Phase 3 — used by both the grouping key (duration_bucket) and the
    # keeper heuristic (bitrate as a tiebreaker before track_id).
    duration: float = 0.0            # seconds; 0 = unknown
    bitrate: int = 0                 # kbps; 0 = unknown
    # Path columns echoed back to the client so the UI can render the
    # same-path-as-keeper vs distinct-file chip per row without an
    # additional round-trip.
    folder_path: str = ""
    file_name: str = ""


@dataclass
class DuplicateGroup:
    artist: str            # echoed back un-normalised from the first row in the bucket
    title: str
    copies: list[TrackProjection]
    keeper_id: int = 0     # track_id picked by the keeper heuristic below

    def to_dict(self) -> dict:
        # The keeper's full file path drives the frontend's same-path vs
        # distinct-file chip per row. Compute it once here so the JSON
        # consumer doesn't have to scan the copies for is_keeper=True.
        keeper = next(
            (c for c in self.copies if c.track_id == self.keeper_id),
            self.copies[0] if self.copies else None,
        )
        keeper_path = (
            f"{keeper.folder_path}{keeper.file_name}" if keeper else ""
        )
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
                    "duration": c.duration,
                    "bitrate": c.bitrate,
                    # `same_path_as_keeper` flags rows whose audio file
                    # is shared with the keeper — deleting that row
                    # leaves the keeper's audio intact. Distinct-path
                    # rows surface an orphan audio file on disk.
                    "same_path_as_keeper": (
                        f"{c.folder_path}{c.file_name}" == keeper_path
                    ),
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
        key = normalize_key(t.artist, t.title, t.duration)
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
