"""Auto-classification tagging — writes DJ analysis results as Rekordbox My Tags."""
from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from .classify import get_classification
from .energy import get_energy_curve, classify_energy_profile
from .score import get_mixability

_log = logging.getLogger(__name__)

# Category → {name shown in RB, Attribute color hint}
# Attribute values mirror DjmdColor SortKey (1=Pink 2=Red 3=Orange 4=Yellow
# 5=Green 6=Aqua 7=Blue 8=Purple).  Rekordbox may or may not respect these
# for My Tags; the tags are functional regardless.
_CATEGORIES = {
    "warmup":      {"name": "Warmup",      "attribute": 7},  # Blue
    "build":       {"name": "Build",       "attribute": 3},  # Orange
    "peak":        {"name": "Peak",        "attribute": 2},  # Red
    "after_hours": {"name": "After Hours", "attribute": 8},  # Purple
    "closing":     {"name": "Closing",     "attribute": 5},  # Green
}

# Tag names AutoCue manages (used to identify & selectively remove its own tags)
AUTOCUE_TAG_NAMES = frozenset(v["name"] for v in _CATEGORIES.values())

MIN_SCORE = 0.70


# ---------------------------------------------------------------------------
# Ensure the 5 category My Tags exist in the DB
# ---------------------------------------------------------------------------

def ensure_category_tags(db) -> dict[str, str]:
    """Get or create the 5 AutoCue category My Tags.

    Returns {category_key → str(MyTag.ID)}.
    Idempotent: existing tags are reused and never recreated.
    """
    from pyrekordbox.db6.tables import DjmdMyTag

    # Case-insensitive + whitespace-trimmed lookup so a pre-existing user tag
    # like "warmup" reuses instead of creating a duplicate "Warmup".
    autocue_names_norm = {n.casefold() for n in AUTOCUE_TAG_NAMES}
    existing = {}
    try:
        for t in db.get_my_tag().all():
            if not t.Name:
                continue
            norm = t.Name.strip().casefold()
            if norm in autocue_names_norm:
                existing[norm] = str(t.ID)
    except Exception as exc:
        _log.warning("ensure_category_tags: could not read existing tags: %s", exc)

    result: dict[str, str] = {}
    for i, (cat_key, cfg) in enumerate(_CATEGORIES.items(), start=1):
        name = cfg["name"]
        key = name.casefold()
        if key in existing:
            result[cat_key] = existing[key]
            continue
        new_id = str(db.generate_unused_id(DjmdMyTag))
        tag = DjmdMyTag(
            ID=new_id,
            UUID=str(uuid4()),
            Name=name,
            Attribute=cfg["attribute"],
            Seq=i,
        )
        db.session.add(tag)
        db.session.flush()
        result[cat_key] = new_id
        _log.info("Created My Tag '%s' (ID=%s)", name, new_id)

    return result


# ---------------------------------------------------------------------------
# Main tagging logic
# ---------------------------------------------------------------------------

def apply_classification_tags(
    db,
    track_ids: list[int],
    overwrite: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Classify tracks and write the top category as a Rekordbox My Tag.

    Rules:
    - Top category only.
    - Only tags if score >= MIN_SCORE (0.70).
    - Skips tracks with no ANLZ energy data.
    - If overwrite=True, removes any existing AutoCue tags before adding new ones.

    Returns summary dict including undo_data for a future undo call.
    """
    from pyrekordbox.db6.tables import DjmdSongMyTag

    tag_id_map = ensure_category_tags(db)
    autocue_tag_ids = set(tag_id_map.values())

    tagged = 0
    skipped_no_anlz = 0
    skipped_low_score = 0
    errors = 0

    undo_data: dict[str, Any] = {
        "removed": [],  # list of {ID, MyTagID, ContentID, TrackNo, UUID}
        "added":   [],  # list of str IDs
    }

    for track_id in track_ids:
        try:
            content = db.get_content(ID=track_id)
            if content is None:
                continue

            curve = get_energy_curve(content, db)
            if not curve:
                skipped_no_anlz += 1
                continue

            clf = get_classification(content, db)
            if clf is None:
                skipped_no_anlz += 1
                continue

            top_cat = clf["primary"]
            top_score = clf["scores"].get(top_cat, 0.0)

            if top_score < MIN_SCORE:
                skipped_low_score += 1
                continue

            if dry_run:
                tagged += 1
                continue

            # Remove existing AutoCue category tags
            if overwrite:
                try:
                    for st in db.get_my_tag_songs(ContentID=str(track_id)).all():
                        if str(st.MyTagID) in autocue_tag_ids:
                            undo_data["removed"].append({
                                "ID": str(st.ID),
                                "MyTagID": str(st.MyTagID),
                                "ContentID": str(st.ContentID),
                                "TrackNo": st.TrackNo,
                                "UUID": str(st.UUID) if st.UUID else None,
                            })
                            db.session.delete(st)
                except Exception as exc:
                    _log.debug("auto_tag: delete old tags for %s: %s", track_id, exc)

            # Write new tag assignment
            new_id = str(db.generate_unused_id(DjmdSongMyTag))
            song_tag = DjmdSongMyTag(
                ID=new_id,
                UUID=str(uuid4()),
                MyTagID=tag_id_map[top_cat],
                ContentID=str(track_id),
                TrackNo=0,
            )
            db.session.add(song_tag)
            undo_data["added"].append(new_id)
            tagged += 1

        except Exception as exc:
            errors += 1
            if errors <= 3:
                _log.warning("auto_tag track %s: %s", track_id, exc)

    if not dry_run and (tagged or undo_data["removed"]):
        try:
            db.session.flush()
        except Exception as exc:
            _log.error("auto_tag flush failed: %s", exc)
            raise

    return {
        "tagged": tagged,
        "skipped_no_anlz": skipped_no_anlz,
        "skipped_low_score": skipped_low_score,
        "errors": errors,
        "dry_run": dry_run,
        "undo_data": undo_data if not dry_run else None,
    }


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

def undo_tag_run(db, undo_data: dict) -> dict[str, int]:
    """Reverse a previous apply_classification_tags run.

    Removes tags that were added, and re-inserts tags that were removed.
    """
    from pyrekordbox.db6.tables import DjmdSongMyTag

    removed = 0
    restored = 0

    for song_tag_id in undo_data.get("added", []):
        try:
            row = db.get_my_tag_songs(ID=song_tag_id)
            if row is not None:
                db.session.delete(row)
                removed += 1
        except Exception as exc:
            _log.warning("undo_tag_run: delete %s: %s", song_tag_id, exc)

    for item in undo_data.get("removed", []):
        try:
            song_tag = DjmdSongMyTag(
                ID=item["ID"],
                UUID=item.get("UUID") or str(uuid4()),
                MyTagID=item["MyTagID"],
                ContentID=item["ContentID"],
                TrackNo=item.get("TrackNo") or 0,
            )
            db.session.add(song_tag)
            restored += 1
        except Exception as exc:
            _log.warning("undo_tag_run: restore %s: %s", item.get("ID"), exc)

    db.session.flush()
    return {"removed": removed, "restored": restored}


# ---------------------------------------------------------------------------
# Extended tag type definitions
# ---------------------------------------------------------------------------

_TAG_GROUPS: dict[str, dict[str, dict]] = {
    "category": _CATEGORIES,
    "vocal": {
        "vocal":        {"name": "Vocal",        "attribute": 1},  # Pink
        "instrumental": {"name": "Instrumental", "attribute": 6},  # Aqua
    },
    "energy_level": {
        "high": {"name": "High Energy", "attribute": 2},  # Red
        "mid":  {"name": "Mid Energy",  "attribute": 4},  # Yellow
        "low":  {"name": "Low Energy",  "attribute": 7},  # Blue
    },
    "energy_profile": {
        "build_track": {"name": "Build Track", "attribute": 3},  # Orange
        "wave_track":  {"name": "Wave Track",  "attribute": 8},  # Purple
        "flat_track":  {"name": "Flat Track",  "attribute": 6},  # Aqua
        "drop_track":  {"name": "Drop Track",  "attribute": 1},  # Pink
    },
    "intro_outro": {
        "long_intro":  {"name": "Long Intro",  "attribute": 5},  # Green
        "short_intro": {"name": "Short Intro", "attribute": 4},  # Yellow
        "long_outro":  {"name": "Long Outro",  "attribute": 3},  # Orange
        "short_outro": {"name": "Short Outro", "attribute": 1},  # Pink
    },
    "decade": {
        "60s": {"name": "60s", "attribute": 1},
        "70s": {"name": "70s", "attribute": 8},
        "80s": {"name": "80s", "attribute": 7},
        "90s": {"name": "90s", "attribute": 6},
        "00s": {"name": "00s", "attribute": 5},
        "10s": {"name": "10s", "attribute": 4},
        "20s": {"name": "20s", "attribute": 3},
    },
    "bpm_tier": {
        "lt120":  {"name": "<120 BPM",     "attribute": 7},
        "t120":   {"name": "120–124 BPM",  "attribute": 6},
        "t125":   {"name": "125–128 BPM",  "attribute": 5},
        "t129":   {"name": "129–135 BPM",  "attribute": 4},
        "t136":   {"name": "136–144 BPM",  "attribute": 3},
        "gt144":  {"name": ">144 BPM",     "attribute": 2},
    },
    "play_history": {
        "never":      {"name": "Never Played",      "attribute": 7},
        "rarely":     {"name": "Rarely Played",     "attribute": 5},
        "frequently": {"name": "Frequently Played", "attribute": 2},
    },
}

# All tag names across every group AutoCue may write
ALL_AUTOCUE_TAG_NAMES = frozenset(
    cfg["name"]
    for group in _TAG_GROUPS.values()
    for cfg in group.values()
)

VALID_TAG_TYPES = frozenset(_TAG_GROUPS.keys())

LONG_INTRO_BARS  = 16
SHORT_INTRO_BARS = 4
LONG_OUTRO_BARS  = 16
SHORT_OUTRO_BARS = 4


# ---------------------------------------------------------------------------
# ensure_tags — create/fetch My Tag rows for any set of tag types
# ---------------------------------------------------------------------------

def ensure_tags(db, tag_types: list[str]) -> dict[str, str]:
    """Get or create My Tag rows for the requested tag groups.

    Returns {tag_name → str(MyTag.ID)}.
    """
    from pyrekordbox.db6.tables import DjmdMyTag

    needed: dict[str, dict] = {}  # name → cfg
    for ttype in tag_types:
        for cfg in _TAG_GROUPS.get(ttype, {}).values():
            needed[cfg["name"]] = cfg

    existing: dict[str, str] = {}
    try:
        for t in db.get_my_tag().all():
            if t.Name and t.Name in needed:
                existing[t.Name] = str(t.ID)
    except Exception as exc:
        _log.warning("ensure_tags: could not read existing tags: %s", exc)

    result: dict[str, str] = {}
    for seq, (name, cfg) in enumerate(needed.items(), start=1):
        if name in existing:
            result[name] = existing[name]
        else:
            new_id = str(db.generate_unused_id(DjmdMyTag))
            tag = DjmdMyTag(
                ID=new_id,
                UUID=str(uuid4()),
                Name=name,
                Attribute=cfg["attribute"],
                Seq=seq,
            )
            db.session.add(tag)
            db.session.flush()
            result[name] = new_id
            _log.info("Created My Tag '%s' (ID=%s)", name, new_id)

    return result


def ensure_tag_by_name(db, name: str, attribute: int = 1) -> str:
    """Get or create a single My Tag row by name (used for dynamic tags like Discogs styles).

    Returns the str(ID) of the My Tag row.
    attribute=1 (pink) is used as default color for Discogs style tags.

    Name lookup is case-insensitive and whitespace-trimmed so that running
    auto-tag with subtly different casing ("Vocal" vs "vocal" vs " Vocal ")
    reuses the same row instead of cluttering the Rekordbox sidebar with
    twin tags. Original casing is preserved on first create.
    """
    from pyrekordbox.db6.tables import DjmdMyTag

    normalized = name.strip().casefold()
    try:
        for t in db.get_my_tag().all():
            existing = (t.Name or "").strip().casefold()
            if existing == normalized:
                return str(t.ID)
    except Exception:
        pass

    new_id = str(db.generate_unused_id(DjmdMyTag))
    tag = DjmdMyTag(
        ID=new_id,
        UUID=str(uuid4()),
        Name=name.strip(),
        Attribute=attribute,
        Seq=0,
    )
    db.session.add(tag)
    db.session.flush()
    _log.info("Created My Tag (dynamic) '%s' (ID=%s)", name, new_id)
    return new_id


# ---------------------------------------------------------------------------
# Per-type detection functions — return list of tag *names* to apply
# ---------------------------------------------------------------------------

def _detect_category(content, db) -> list[str]:
    curve = get_energy_curve(content, db)
    if not curve:
        return []
    clf = get_classification(content, db)
    if clf is None:
        return []
    top_cat = clf["primary"]
    if clf["scores"].get(top_cat, 0.0) < MIN_SCORE:
        return []
    return [_CATEGORIES[top_cat]["name"]]


def _detect_vocal(content, db) -> list[str]:
    mix = get_mixability(content, db)
    if mix is None:
        return []
    return ["Vocal" if mix["vocal_proxy"] else "Instrumental"]


def _detect_energy_level(content, db) -> list[str]:
    curve = get_energy_curve(content, db)
    if not curve:
        return []
    mean = sum(curve) / len(curve)
    if mean >= 0.65:
        return ["High Energy"]
    if mean >= 0.35:
        return ["Mid Energy"]
    return ["Low Energy"]


def _detect_energy_profile(content, db) -> list[str]:
    curve = get_energy_curve(content, db)
    if not curve:
        return []
    profile = classify_energy_profile(curve)
    name_map = {
        "build":          "Build Track",
        "wave":           "Wave Track",
        "flat":           "Flat Track",
        "drop-then-flat": "Drop Track",
    }
    name = name_map.get(profile)
    return [name] if name else []


def _detect_intro_outro(content, db) -> list[str]:
    mix = get_mixability(content, db)
    if mix is None or mix.get("phrase_count", 0) == 0:
        return []
    tags = []
    intro = mix.get("intro_bars", 0)
    outro = mix.get("outro_bars", 0)
    if intro >= LONG_INTRO_BARS:
        tags.append("Long Intro")
    elif 0 < intro <= SHORT_INTRO_BARS:
        tags.append("Short Intro")
    if outro >= LONG_OUTRO_BARS:
        tags.append("Long Outro")
    elif 0 < outro <= SHORT_OUTRO_BARS:
        tags.append("Short Outro")
    return tags


def _detect_decade(content, db) -> list[str]:
    year = getattr(content, "ReleaseYear", None)
    if not year:
        return []
    try:
        year = int(year)
    except (ValueError, TypeError):
        return []
    if year <= 0:
        return []
    decade_start = (year // 10) * 10
    decade_map = {1960: "60s", 1970: "70s", 1980: "80s", 1990: "90s",
                  2000: "00s", 2010: "10s", 2020: "20s"}
    return [decade_map[decade_start]] if decade_start in decade_map else []


def _detect_bpm_tier(content, db) -> list[str]:
    bpm_raw = getattr(content, "BPM", None)
    if not bpm_raw:
        return []
    try:
        bpm = float(bpm_raw) / 100.0
    except (ValueError, TypeError):
        return []
    if bpm <= 0:
        return []
    if bpm < 120: return ["<120 BPM"]
    if bpm < 125: return ["120–124 BPM"]
    if bpm < 129: return ["125–128 BPM"]
    if bpm < 136: return ["129–135 BPM"]
    if bpm < 145: return ["136–144 BPM"]
    return [">144 BPM"]


def _detect_play_history(content, db) -> list[str]:
    count_raw = getattr(content, "DJPlayCount", None)
    try:
        count = int(str(count_raw or 0))
    except (ValueError, TypeError):
        count = 0
    if count == 0:
        return ["Never Played"]
    if count <= 5:
        return ["Rarely Played"]
    if count >= 25:
        return ["Frequently Played"]
    return []


_DETECTORS = {
    "category":       _detect_category,
    "vocal":          _detect_vocal,
    "energy_level":   _detect_energy_level,
    "energy_profile": _detect_energy_profile,
    "intro_outro":    _detect_intro_outro,
    "decade":         _detect_decade,
    "bpm_tier":       _detect_bpm_tier,
    "play_history":   _detect_play_history,
}


# ---------------------------------------------------------------------------
# apply_tags — unified multi-type tagger
# ---------------------------------------------------------------------------

def apply_tags(
    db,
    track_ids: list[int],
    tag_types: list[str] | None = None,
    overwrite: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Detect and write My Tags for the requested tag types.

    tag_types accepts any subset of: category, vocal, energy_level,
    energy_profile, intro_outro.  Defaults to all five.

    Returns summary dict with keys: tagged, skipped_no_data, errors,
    dry_run, undo_data.

    TASK-005 — when AUTOCUE_PARALLEL_AUTO_TAG=1, the *read-only* detector
    evaluation runs through the shared ThreadPoolExecutor. Writes (delete
    old AutoCue tags + insert new DjmdSongMyTag rows) still happen on a
    single writer thread (the caller's thread) to honor the single-writer
    rule for master.db. Default = existing serial behaviour.
    """
    import os as _os
    from pyrekordbox.db6.tables import DjmdSongMyTag

    if tag_types is None:
        tag_types = list(_DETECTORS.keys())

    active_types = [t for t in tag_types if t in _DETECTORS]
    tag_name_map = ensure_tags(db, active_types)  # {name → str(ID)}
    autocue_tag_ids = set(tag_name_map.values())

    tagged = 0
    skipped_no_data = 0
    errors = 0
    undo_data: dict[str, Any] = {"removed": [], "added": []}

    parallel = _os.environ.get("AUTOCUE_PARALLEL_AUTO_TAG") == "1"

    def _eval_one(track_id):
        """Pure-read worker: resolve content + run detectors. No writes."""
        try:
            content = db.get_content(ID=track_id)
            if content is None:
                return (track_id, None, [], None)
            names: list[str] = []
            for ttype in active_types:
                names.extend(_DETECTORS[ttype](content, db))
            return (track_id, content, names, None)
        except Exception as exc:
            return (track_id, None, [], exc)

    def _write_one(track_id, names_to_write):
        """Writer-thread side: delete old AutoCue tags + add new ones. Mutates
        tagged/undo_data via closure. Returns True if any write happened."""
        nonlocal tagged
        if overwrite:
            try:
                for st in db.get_my_tag_songs(ContentID=str(track_id)).all():
                    if str(st.MyTagID) in autocue_tag_ids:
                        undo_data["removed"].append({
                            "ID": str(st.ID),
                            "MyTagID": str(st.MyTagID),
                            "ContentID": str(st.ContentID),
                            "TrackNo": st.TrackNo,
                            "UUID": str(st.UUID) if st.UUID else None,
                        })
                        db.session.delete(st)
            except Exception as exc:
                _log.debug("apply_tags: delete old tags for %s: %s", track_id, exc)

        for name in names_to_write:
            if name not in tag_name_map:
                continue
            new_id = str(db.generate_unused_id(DjmdSongMyTag))
            song_tag = DjmdSongMyTag(
                ID=new_id,
                UUID=str(uuid4()),
                MyTagID=tag_name_map[name],
                ContentID=str(track_id),
                TrackNo=0,
            )
            db.session.add(song_tag)
            undo_data["added"].append(new_id)
        tagged += 1
        return True

    if parallel:
        from concurrent.futures import as_completed as _as_completed
        from .concurrency import get_pool as _get_pool

        pool = _get_pool()
        futures = [pool.submit(_eval_one, tid) for tid in track_ids]
        # Writer (this thread) drains completions one at a time.
        for fut in _as_completed(futures):
            try:
                track_id, content, names_to_write, exc = fut.result()
            except Exception as outer:
                errors += 1
                if errors <= 3:
                    _log.warning("apply_tags future failed: %s", outer)
                continue
            if exc is not None:
                errors += 1
                if errors <= 3:
                    _log.warning("apply_tags track %s: %s", track_id, exc)
                continue
            if content is None:
                # get_content returned None — silently skip (matches serial path).
                continue
            if not names_to_write:
                skipped_no_data += 1
                continue
            if dry_run:
                tagged += 1
                continue
            try:
                _write_one(track_id, names_to_write)
            except Exception as werr:
                errors += 1
                if errors <= 3:
                    _log.warning("apply_tags write track %s: %s", track_id, werr)
    else:
        for track_id in track_ids:
            try:
                content = db.get_content(ID=track_id)
                if content is None:
                    continue

                names_to_write: list[str] = []
                for ttype in active_types:
                    names_to_write.extend(_DETECTORS[ttype](content, db))

                if not names_to_write:
                    skipped_no_data += 1
                    continue

                if dry_run:
                    tagged += 1
                    continue

                _write_one(track_id, names_to_write)

            except Exception as exc:
                errors += 1
                if errors <= 3:
                    _log.warning("apply_tags track %s: %s", track_id, exc)

    if not dry_run and (tagged or undo_data["removed"]):
        try:
            db.session.flush()
        except Exception as exc:
            _log.error("apply_tags flush failed: %s", exc)
            raise

    return {
        "tagged": tagged,
        "skipped_no_data": skipped_no_data,
        "errors": errors,
        "dry_run": dry_run,
        "undo_data": undo_data if not dry_run else None,
    }
