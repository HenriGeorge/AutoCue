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
_SENTINEL_END = "*/"
_CATEGORY_LABELS = {
    "warmup":     "Warm Up",
    "build":      "Build",
    "peak":       "Peak",
    "after_hours": "After Hours",
    "closing":    "Closing",
}

# Rekordbox CDJ comment field renders cleanly up to ~256 characters. Beyond
# that, the on-screen text gets truncated mid-word. Postgres/MySQL replicas
# of master.db can have stricter caps. The store itself is SQL Text (no
# fixed cap), so this is a soft UX guard rather than a database constraint.
# When the final string exceeds this, AutoCue progressively drops the less
# essential parts of its own contribution (intro_info → category → energy)
# so the user-authored text is never trimmed.
MAX_COMMENT_LEN = 256


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


def _build_parts(content, db) -> list[str]:
    """Return the enrichment parts in display order (key+energy, category, intro).

    Returned in priority order: index 0 is essential (key+energy), trailing
    parts can be dropped first when truncating to fit MAX_COMMENT_LEN.
    """
    cls = get_classification(content, db)
    key = _camelot_key(content)
    energy_mean = cls.get("energy_mean")
    level = _energy_level(energy_mean)
    category = cls.get("primary", "")
    cat_label = _CATEGORY_LABELS.get(category, "")

    parts: list[str] = []

    # Key + energy block (MIK-compatible prefix) — most essential
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

    return parts


def build_comment_string(content, db) -> str:
    """Build the enrichment comment string for a track.

    Returns a string like '8A - Energy 7 | Peak | 4 bar intro', omitting
    components for which no data is available.
    """
    return " | ".join(_build_parts(content, db))


def _fit_within(user_text: str, parts: list[str], overwrite: bool, max_len: int) -> str | None:
    """Build the final comment string within ``max_len``, preserving user text.

    Drops AutoCue parts from the end (intro → category) until the result
    fits. Returns None when even the minimal sentinel can't fit (user text
    alone is already over the cap and overwrite is False).
    """
    if overwrite or not user_text:
        # Pure AutoCue write — truncate AutoCue parts to fit.
        for cutoff in range(len(parts), 0, -1):
            candidate = " | ".join(parts[:cutoff])
            if len(candidate) <= max_len:
                if cutoff < len(parts):
                    logger.warning(
                        "comment enrichment: truncated to %d/%d parts to fit %d chars",
                        cutoff, len(parts), max_len,
                    )
                return candidate
        # Last resort: a hard slice of the most essential part
        return parts[0][:max_len] if parts else None

    # Append mode — never trim user_text. Drop AutoCue parts as needed.
    overhead = len(user_text) + 1 + len(_SENTINEL) + 1 + 1 + len(_SENTINEL_END)
    budget = max_len - overhead
    if budget <= 0:
        logger.warning(
            "comment enrichment: user text length %d already over cap %d — skipping enrichment",
            len(user_text), max_len,
        )
        return None
    for cutoff in range(len(parts), 0, -1):
        body = " | ".join(parts[:cutoff])
        if len(body) <= budget:
            if cutoff < len(parts):
                logger.warning(
                    "comment enrichment: truncated to %d/%d parts to fit %d chars (user text uses %d)",
                    cutoff, len(parts), max_len, len(user_text),
                )
            return f"{user_text} {_SENTINEL} {body} {_SENTINEL_END}"
    return None


def enrich_comment(content, db, *, overwrite: bool = False, dry_run: bool = False) -> str | None:
    """Write enrichment data to DjmdContent.Comment.

    - If comment is empty: writes the full string directly.
    - If comment already contains our sentinel: replaces only the sentinel block.
    - Otherwise: appends '/* AutoCue: ... */' to the existing comment.
    - overwrite=True: always replaces the entire comment field.

    The result is constrained to MAX_COMMENT_LEN characters so the CDJ UI
    can render it cleanly. AutoCue parts (intro → category) drop in that
    order to fit; user-authored text is never trimmed.

    Returns the new comment string, or None if nothing changed.
    Does NOT commit — caller must commit the session.
    """
    existing = str(getattr(content, "Commnt", "") or "").strip()
    parts = _build_parts(content, db)

    if not parts:
        return None

    # When the existing comment already carries our sentinel, treat the
    # text BEFORE the sentinel as the user-authored portion to preserve.
    if not overwrite and _SENTINEL in existing:
        user_text = existing[:existing.index(_SENTINEL)].rstrip()
    elif overwrite:
        user_text = ""
    else:
        user_text = existing

    new_comment = _fit_within(user_text, parts, overwrite, MAX_COMMENT_LEN)
    if new_comment is None or new_comment == existing:
        return None

    if not dry_run:
        content.Commnt = new_comment

    return new_comment


def enrich_comments_batch(
    track_ids: list[int],
    db,
    *,
    overwrite: bool = False,
    dry_run: bool = False,
) -> dict:
    """Enrich comments for multiple tracks.

    Returns ``{'enriched', 'skipped', 'errors', 'backup_path', 'undo_data'}``.
    ``undo_data`` is a list of ``{'content_id', 'previous': str}`` rows, one
    per track AutoCue actually modified — pass it back to ``restore_comments``
    to roll the change back without touching other DB state. Makes a backup
    before writing (unless ``dry_run``).
    """
    enriched = 0
    skipped = 0
    errors = 0
    backup_path = None
    undo_rows: list[dict] = []

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
            previous = str(getattr(content, "Commnt", "") or "")
            result = enrich_comment(content, db, overwrite=overwrite, dry_run=dry_run)
            if result is None:
                skipped += 1
            else:
                enriched += 1
                if not dry_run:
                    undo_rows.append({"content_id": str(tid), "previous": previous})
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
        "undo_data": {"modified": undo_rows},
    }


def restore_comments(db, undo_data: dict) -> dict:
    """Reverse a previous ``enrich_comments_batch`` run.

    Writes ``previous`` back onto every modified track's ``DjmdContent.Commnt``.
    Tracks that were deleted since the original run are silently skipped.
    Commits the session on success.

    Mirrors ``auto_tag.undo_tag_run`` so the UI can offer symmetric "undo
    last enrichment" / "undo last tagging" controls without a full DB restore.
    """
    if not undo_data:
        return {"restored": 0, "skipped": 0, "errors": 0}
    rows = undo_data.get("modified") if isinstance(undo_data, dict) else None
    if not rows:
        return {"restored": 0, "skipped": 0, "errors": 0}

    restored = 0
    skipped = 0
    errors = 0
    for row in rows:
        try:
            cid = row.get("content_id")
            previous = row.get("previous", "")
            if cid is None:
                skipped += 1
                continue
            content = db.get_content(ID=int(cid) if str(cid).isdigit() else cid)
            if content is None:
                skipped += 1
                continue
            content.Commnt = previous
            restored += 1
        except Exception as exc:
            logger.warning("restore_comments: failed for %r: %s", row, exc)
            errors += 1

    if restored > 0:
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            raise RuntimeError(f"restore_comments: commit failed: {exc}") from exc

    return {"restored": restored, "skipped": skipped, "errors": errors}
