"""
Track comment enrichment — writes DJ-useful metadata to DjmdContent.Comment.

Format (empty comment):   8A - Energy 7 | Peak | 4 bar intro
Append format (existing): existing comment /* AutoCue: 8A / Peak / 4 bar intro */

The append sentinel /* AutoCue: ... */ matches the convention Rekordbox itself uses
when "Add My Tag to Comments" is enabled, making the enrichment block identifiable
and re-writable without touching user-authored text.
"""
from __future__ import annotations

import logging
import math

from .classify import get_classification
from .energy import get_energy_curve

logger = logging.getLogger(__name__)

_SENTINEL = "/* AutoCue:"
_CATEGORY_LABELS = {
    "warmup":     "Warm Up",
    "build":      "Build",
    "peak":       "Peak",
    "after_hours": "After Hours",
    "closing":    "Closing",
}


def _camelot_key(content) -> str:
    """Return Camelot key string (e.g. '8A') from DjmdContent, or '' if unavailable."""
    try:
        k = getattr(content, "Key", None)
        if k:
            return str(getattr(k, "ScaleName", "") or "").strip()
    except Exception:
        pass
    return ""


def _energy_level(energy_mean: float | None) -> int | None:
    """Convert 0–1 energy_mean to 1–10 MIK-compatible energy level, or None."""
    if energy_mean is None:
        return None
    return max(1, min(10, round(energy_mean * 9) + 1))


def _intro_bars(content, db) -> int | None:
    """
    Return intro length in bars (rounded to nearest 4), or None if unavailable.

    Uses phrase analysis — reads the ANLZ file to find where the intro ends.
    Falls back to None rather than guessing.
    """
    try:
        from ..analyzer import analyze_track
        from ..models import PhraseLabel
        phrases = analyze_track(content, db)
        if not phrases:
            return None
        # First non-INTRO phrase start = intro end
        non_intro = [p for p in phrases if p.label != PhraseLabel.INTRO]
        if not non_intro:
            return None
        intro_end_ms = min(p.position_ms for p in non_intro)
        if intro_end_ms <= 0:
            return None
        raw_bpm = getattr(content, "BPM", 0) or 0
        bpm = float(raw_bpm) / 100.0
        if bpm <= 0:
            return None
        ms_per_bar = (60_000.0 / bpm) * 4
        bars_raw = intro_end_ms / ms_per_bar
        # Round to nearest 4 bars (standard phrase grid)
        bars = max(4, round(bars_raw / 4) * 4)
        return bars
    except Exception:
        return None


def build_comment_string(content, db) -> str:
    """
    Build the enrichment comment string for a track.

    Returns a string like '8A - Energy 7 | Peak | 4 bar intro', omitting
    components for which no data is available.
    """
    cls = get_classification(content, db)
    key = _camelot_key(content)
    energy_mean = cls.get("energy_mean")
    level = _energy_level(energy_mean)
    category = cls.get("primary", "")
    cat_label = _CATEGORY_LABELS.get(category, "")

    parts: list[str] = []

    # Key + energy block (MIK-compatible prefix)
    if key and level is not None:
        parts.append(f"{key} - Energy {level}")
    elif key:
        parts.append(key)
    elif level is not None:
        parts.append(f"Energy {level}")

    if cat_label:
        parts.append(cat_label)

    intro = _intro_bars(content, db)
    if intro:
        parts.append(f"{intro} bar intro")

    return " | ".join(parts)


def enrich_comment(content, db, *, overwrite: bool = False, dry_run: bool = False) -> str | None:
    """
    Write enrichment data to DjmdContent.Comment.

    - If comment is empty: writes the full string directly.
    - If comment already contains our sentinel: replaces only the sentinel block.
    - Otherwise: appends '/* AutoCue: ... */' to the existing comment.
    - overwrite=True: always replaces the entire comment field.

    Returns the new comment string, or None if nothing changed.
    Does NOT commit — caller must commit the session.
    """
    existing = str(getattr(content, "Comment", "") or "").strip()
    enrichment = build_comment_string(content, db)

    if not enrichment:
        return None

    if overwrite or not existing:
        new_comment = enrichment
    elif _SENTINEL in existing:
        # Replace the existing AutoCue sentinel block
        sentinel_start = existing.index(_SENTINEL)
        base = existing[:sentinel_start].rstrip()
        new_comment = f"{base} {_SENTINEL} {enrichment} */" if base else f"{_SENTINEL} {enrichment} */"
    else:
        new_comment = f"{existing} {_SENTINEL} {enrichment} */"

    if new_comment == existing:
        return None

    if not dry_run:
        content.Comment = new_comment

    return new_comment


def enrich_comments_batch(
    track_ids: list[int],
    db,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    Enrich comments for multiple tracks.

    Returns {'enriched': int, 'skipped': int, 'errors': int, 'backup_path': str|None}.
    Makes a backup before writing (unless dry_run).
    """
    enriched = 0
    skipped = 0
    errors = 0
    backup_path = None

    if not dry_run and track_ids:
        from pathlib import Path as _Path
        from ..db_writer import backup_database as _backup
        try:
            db_dir = getattr(db, "_db_dir", None)
            if db_dir:
                backup_path = str(_backup(_Path(db_dir) / "master.db"))
        except Exception as e:
            logger.warning("Comment enrichment: backup failed: %s", e)

    for tid in track_ids:
        try:
            content = db.get_content(ID=tid)
            if content is None:
                skipped += 1
                continue
            result = enrich_comment(content, db, overwrite=overwrite, dry_run=dry_run)
            if result is None:
                skipped += 1
            else:
                enriched += 1
        except Exception as e:
            logger.warning("Comment enrichment failed for track %s: %s", tid, e)
            errors += 1

    if not dry_run and enriched > 0:
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            raise RuntimeError(f"Failed to commit comment enrichment: {e}") from e

    return {
        "enriched": enriched,
        "skipped": skipped,
        "errors": errors,
        "backup_path": backup_path,
    }
