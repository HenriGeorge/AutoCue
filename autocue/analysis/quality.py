"""
Cue quality health checker.

Pure DB reads — no ANLZ parsing. All data comes from DjmdCue, DjmdContent,
and fast file-existence checks already available on TrackItem fields.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Generator, Literal

# Two hot cues within this InFrame threshold = duplicate (targets double-write bugs only).
# Frame resolution is 1000/150 ≈ 6.67ms, so < 2 frames ≈ <13ms — catches same-position
# double-writes without false-positiving on intentional adjacent cues (always >2 frames apart).
_DUPLICATE_FRAMES = 2

# Default Rekordbox cue name pattern — empty names are also caught by the `not name` check.
_UNNAMED_RE = re.compile(r"^Cue\s*\d+$", re.IGNORECASE)

Severity = Literal["error", "warning", "info"]
FixTier = Literal["phrase", "bar", "heuristic", "none"]


@dataclass
class CueIssue:
    code: str
    severity: Severity
    message: str


@dataclass
class TrackHealthReport:
    track_id: int
    score: int  # 0–100; forced to 0 when NO_AUDIO_FILE
    issues: list[CueIssue] = field(default_factory=list)
    fix_tier: FixTier = "none"
    hot_cue_count: int = 0
    memory_cue_count: int = 0


def _resolve_audio_path(content) -> str:
    """Return the absolute audio file path stored in FolderPath, normalised for macOS."""
    raw = getattr(content, "FolderPath", None) or ""
    # Rekordbox on macOS sometimes prefixes with /: — strip the leading slash+colon
    if raw.startswith("/:"):
        raw = raw[2:]
    if raw and not raw.startswith("/"):
        raw = "/" + raw
    return raw


def _fix_tier(has_phrase: bool, has_beatgrid: bool, audio_exists: bool) -> FixTier:
    if not audio_exists:
        return "none"
    if has_phrase and has_beatgrid:
        return "phrase"
    if has_beatgrid:
        return "bar"
    return "heuristic"


def check_track_health(content, db) -> TrackHealthReport:
    """Compute health score and issues for a single track. Pure DB reads."""
    from pyrekordbox.db6 import DjmdCue

    track_id = content.ID
    issues: list[CueIssue] = []

    # --- Audio file existence (forced score=0 if missing) ---
    audio_path = _resolve_audio_path(content)
    audio_exists = bool(audio_path) and os.path.exists(audio_path)
    if not audio_exists:
        issues.append(CueIssue("NO_AUDIO_FILE", "error", "Audio file not found on disk"))
        return TrackHealthReport(track_id=track_id, score=0, issues=issues, fix_tier="none")

    # --- Phrase and beat analysis availability ---
    # AnalysisDataPath being set means Rekordbox ran analysis; the .EXT file
    # could theoretically have been deleted since, but checking file existence
    # here would require ANLZ path resolution and is too slow for bulk scans.
    has_phrase = bool(getattr(content, "AnalysisDataPath", None))
    bpm_raw = getattr(content, "BPM", None)
    has_beatgrid = bool(bpm_raw and float(bpm_raw) > 0)

    # --- Cue data ---
    all_cues = db.query(DjmdCue).filter(DjmdCue.ContentID == track_id).all()
    hot_cues = [c for c in all_cues if 1 <= int(c.Kind or 0) <= 8]
    memory_cues = [c for c in all_cues if int(c.Kind or 0) == 0]

    # --- Score calculation ---
    score = 100

    if not has_phrase:
        issues.append(CueIssue("NO_PHRASE", "info", "No phrase analysis — re-analyze in Rekordbox"))
        score -= 10
    if not has_beatgrid:
        issues.append(CueIssue("NO_BEATGRID", "info", "No beat grid — re-analyze in Rekordbox"))
        score -= 10

    if not hot_cues:
        issues.append(CueIssue("NO_CUES", "error", "No hot cues"))
        score -= 30
    else:
        # Duplicate detection: any two hot cues within _DUPLICATE_FRAMES InFrames of each other.
        # Comparing in frame space avoids ms↔frame roundtrip imprecision.
        in_frames = sorted(int(c.InFrame or 0) for c in hot_cues)
        for i in range(len(in_frames) - 1):
            if in_frames[i + 1] - in_frames[i] < _DUPLICATE_FRAMES:
                issues.append(CueIssue("DUPLICATE_CUE", "warning",
                                       "Duplicate cue positions (within ~13ms)"))
                score -= 5
                break  # one penalty regardless of how many duplicates

        # Unnamed cues: empty Comment or matches default "Cue N" pattern
        names = [getattr(c, "Comment", None) or "" for c in hot_cues]
        if any(not n.strip() or _UNNAMED_RE.match(n.strip()) for n in names):
            issues.append(CueIssue("UNNAMED_CUES", "info",
                                   "One or more cues have default or empty names"))
            score -= 5

    # Memory cue — info only, zero score impact
    if not memory_cues:
        issues.append(CueIssue("NO_MEMORY_CUE", "info",
                                "No memory cue — CDJ Auto Cue won't load at a cue point"))

    score = max(0, min(100, score))
    return TrackHealthReport(
        track_id=track_id,
        score=score,
        issues=issues,
        fix_tier=_fix_tier(has_phrase, has_beatgrid, audio_exists),
        hot_cue_count=len(hot_cues),
        memory_cue_count=len(memory_cues),
    )


def check_library_health(
    db,
    *,
    playlist_id: int | None = None,
) -> Generator[TrackHealthReport, None, None]:
    """Yield one TrackHealthReport per track. Filters to playlist if given.

    Designed for SSE streaming — caller yields each report as a JSON event.
    Per-track exceptions are caught and emitted as score=0 / NO_AUDIO_FILE stand-ins
    so one bad row never aborts a 10K-track scan.
    """
    from pyrekordbox.db6 import DjmdContent, DjmdSongPlaylist

    if playlist_id is not None:
        # DjmdSongPlaylist.PlaylistID is VARCHAR(255) — coerce to str to match.
        contents = (
            db.query(DjmdContent)
            .join(DjmdSongPlaylist, DjmdSongPlaylist.ContentID == DjmdContent.ID)
            .filter(DjmdSongPlaylist.PlaylistID == str(playlist_id))
            .all()
        )
    else:
        contents = db.query(DjmdContent).all()

    for content in contents:
        try:
            yield check_track_health(content, db)
        except Exception as exc:
            yield TrackHealthReport(
                track_id=getattr(content, "ID", -1),
                score=0,
                issues=[CueIssue("INTERNAL_ERROR", "error", str(exc))],
                fix_tier="none",
            )
