"""
Reads Rekordbox phrase structure and beat grid from ANLZ analysis files,
returning a list of CuePoints ready to be written back to the library.
"""
from __future__ import annotations

import struct
from collections import Counter
from pathlib import Path

try:
    from pyrekordbox import MasterDatabase
except ImportError:
    from pyrekordbox import Rekordbox6Database as MasterDatabase  # type: ignore[no-redef]
from pyrekordbox.db6 import DjmdContent

from .models import CuePoint, DJ_NAMES, PhraseLabel, phrase_label

# Maximum hot cue slots Rekordbox supports (A–H)
MAX_HOT_CUES = 8

# XOR mask used by Rekordbox 6 to garble PSSI tags in exported files
_PSSI_XOR_MASK = bytearray.fromhex("CB E1 EE FA E5 EE AD EE E9 D2 E9 EB E1 E9 F3 E8 E9 F4 E1")


def _get_anlz_tags_resilient(path: Path, wanted: set[str]) -> dict:
    """
    Scan an ANLZ file tag-by-tag and return parsed structs for the requested tag
    types (e.g. {"PSSI", "PQTZ"}). Tags that fail to parse are silently skipped
    rather than aborting the whole file.

    Rekordbox 7 writes a PQT2 tag with version 0x02000002; pyrekordbox 0.4.x
    expects 0x01000002 and raises ConstError.  By iterating at the byte level we
    skip PQT2 and still recover the PSSI phrase data.
    """
    from pyrekordbox.anlz.structs import PSSI, PQTZ, AnlzTag
    from construct import ConstError

    try:
        data = path.read_bytes()
    except OSError:
        return {}

    if len(data) < 28 or data[:4] != b"PMAI":
        return {}

    file_len_header = struct.unpack_from(">I", data, 4)[0]
    file_len_file = struct.unpack_from(">I", data, 8)[0]
    file_len_file = min(file_len_file, len(data))

    _STRUCT_MAP = {"PSSI": PSSI, "PQTZ": PQTZ}
    results: dict = {}
    i = file_len_header

    while i < file_len_file:
        if i + 12 > file_len_file:
            break
        tag_type_bytes = data[i : i + 4]
        tag_len_tag = struct.unpack_from(">I", data, i + 8)[0]
        if tag_len_tag < 12:
            break

        tag_type = tag_type_bytes.decode("ascii", errors="replace")

        if tag_type in wanted:
            tag_data = data[i : i + tag_len_tag]

            if tag_type == "PSSI":
                # Unmask XOR garbling added by Rekordbox 6 export
                if len(tag_data) >= 20:
                    mood_raw = struct.unpack_from(">H", tag_data, 18)[0]
                    if not (1 <= mood_raw <= 3):
                        len_entries = struct.unpack_from(">H", tag_data, 16)[0]
                        mutable = bytearray(tag_data)
                        for x in range(len(mutable) - 18):
                            mask = _PSSI_XOR_MASK[x % len(_PSSI_XOR_MASK)] + len_entries
                            if mask > 255:
                                mask -= 256
                            mutable[18 + x] ^= mask
                        tag_data = bytes(mutable)

            try:
                content_struct = _STRUCT_MAP[tag_type].parse(tag_data[12:])
                results[tag_type] = content_struct
            except Exception:
                pass

        i += tag_len_tag

    return results


def _beat_to_ms(entries: list, beat_number: int) -> int | None:
    """Convert a 1-indexed beat number to milliseconds using the beat grid entries."""
    idx = beat_number - 1
    if idx < 0 or idx >= len(entries):
        return None
    return entries[idx].time  # already in ms


def _get_pssi_and_pqtz(content: DjmdContent, db: MasterDatabase):
    """Return (pssi_content, pqtz_content) or (None, None) on any failure."""
    pssi_content = pqtz_content = None

    # Try pyrekordbox's normal parse first; fall back to resilient scanner on failure
    try:
        anlz_ext = db.read_anlz_file(content, "EXT")
        if anlz_ext is not None:
            tag = anlz_ext.get_tag("PSSI")
            if tag is not None:
                pssi_content = tag.content
    except Exception:
        ext_path = None
        try:
            ext_path = db.get_anlz_path(content, "EXT")
        except Exception:
            pass
        if ext_path is not None:
            tags = _get_anlz_tags_resilient(Path(ext_path), {"PSSI"})
            pssi_content = tags.get("PSSI")

    try:
        anlz_dat = db.read_anlz_file(content, "DAT")
        if anlz_dat is not None:
            tag = anlz_dat.get_tag("PQTZ")
            if tag is not None:
                pqtz_content = tag.content
    except Exception:
        dat_path = None
        try:
            dat_path = db.get_anlz_path(content, "DAT")
        except Exception:
            pass
        if dat_path is not None:
            tags = _get_anlz_tags_resilient(Path(dat_path), {"PQTZ"})
            pqtz_content = tags.get("PQTZ")

    return pssi_content, pqtz_content


def analyze_track(content: DjmdContent, db: MasterDatabase) -> list[CuePoint]:
    """
    Return up to MAX_HOT_CUES CuePoints derived from Rekordbox's own phrase
    analysis for the given track. Returns an empty list if analysis data is
    unavailable.
    """
    pssi_content, pqtz_content = _get_pssi_and_pqtz(content, db)

    if pssi_content is None or pqtz_content is None:
        return []

    beat_entries = pqtz_content.entries
    phrases = pssi_content.entries
    mood = pssi_content.mood

    # Compute average ms-per-beat from the beat grid for bar-length calculation.
    # Uses global average which is accurate for constant-BPM tracks; for variable-BPM
    # tracks it's a reasonable approximation.
    avg_ms_per_beat: float | None = None
    if len(beat_entries) >= 2:
        span = beat_entries[-1].time - beat_entries[0].time
        if span > 0:
            avg_ms_per_beat = span / (len(beat_entries) - 1)

    # Two-pass algorithm to ensure rare labels (Intro, Chorus, Outro) always get a slot
    # even when the track is dominated by repeating "Up"/"Down" phrases.

    # Collect all phrase ms timestamps first for bar-count computation
    phrase_ms_list: list[int | None] = []
    for entry in phrases:
        phrase_ms_list.append(_beat_to_ms(beat_entries, entry.beat))

    # Pass 1: first occurrence of each unique PhraseLabel
    seen_labels: set[PhraseLabel] = set()
    taken_indices: set[int] = set()
    pass1: list[tuple[int, PhraseLabel, int]] = []  # (position_ms, label, phrase_idx)

    for idx, entry in enumerate(phrases):
        ms = phrase_ms_list[idx]
        if ms is None:
            continue
        lbl = phrase_label(mood, entry.kind)
        if lbl not in seen_labels:
            seen_labels.add(lbl)
            taken_indices.add(idx)
            pass1.append((ms, lbl, idx))

    # Pass 2: fill remaining slots with phrases not already taken, in order
    pass2: list[tuple[int, PhraseLabel, int]] = []
    for idx, entry in enumerate(phrases):
        if len(pass1) + len(pass2) >= MAX_HOT_CUES:
            break
        if idx in taken_indices:
            continue
        ms = phrase_ms_list[idx]
        if ms is None:
            continue
        lbl = phrase_label(mood, entry.kind)
        pass2.append((ms, lbl, idx))

    # Combine, sort by position ascending; deduplicate same-position phrases (degenerate PSSI)
    combined = sorted(pass1 + pass2, key=lambda x: x[0])
    seen_ms: set[int] = set()
    deduped: list[tuple[int, PhraseLabel, int]] = []
    for item in combined:
        if item[0] not in seen_ms:
            seen_ms.add(item[0])
            deduped.append(item)
    combined = deduped

    def _phrase_bars(phrase_idx: int) -> int:
        """Bars in this phrase, derived from ms-timestamp gap to the next phrase."""
        if avg_ms_per_beat is None or avg_ms_per_beat <= 0:
            return 0
        this_ms = phrase_ms_list[phrase_idx]
        # Find the next phrase entry in the full list (not just selected)
        next_ms: int | None = None
        for j in range(phrase_idx + 1, len(phrase_ms_list)):
            if phrase_ms_list[j] is not None:
                next_ms = phrase_ms_list[j]
                break
        if this_ms is None or next_ms is None:
            return 0
        bar_ms = avg_ms_per_beat * 4
        return max(0, round((next_ms - this_ms) / bar_ms))

    # Assign DJ-friendly names; number each label if it appears more than once
    label_counts = Counter(lbl for _, lbl, _ in combined)
    label_seen: dict[PhraseLabel, int] = {}
    cues: list[CuePoint] = []
    for slot, (ms, lbl, phrase_idx) in enumerate(combined):
        label_seen[lbl] = label_seen.get(lbl, 0) + 1
        base = DJ_NAMES[lbl]
        if not base:
            name = ""
        elif label_counts[lbl] == 1:
            name = base
        else:
            name = f"{base} {label_seen[lbl]}"
        cues.append(CuePoint(
            position_ms=ms, label=lbl, slot=slot, name=name,
            phrase_bars=_phrase_bars(phrase_idx),
        ))
    return cues


def analyze_fills(content: DjmdContent, db: MasterDatabase) -> list[CuePoint]:
    """Return CuePoints at fill beats from PSSI phrase data. Returns [] on any failure."""
    try:
        pssi_content, pqtz_content = _get_pssi_and_pqtz(content, db)
        if pssi_content is None or pqtz_content is None:
            return []
        beat_entries = pqtz_content.entries
        fills = []
        for entry in pssi_content.entries:
            fill_flag = getattr(entry, "fill", None)
            beat_fill = getattr(entry, "beat_fill", None)
            if not fill_flag or not beat_fill:
                continue
            fill_beat = entry.beat + int(beat_fill) - 1
            ms = _beat_to_ms(beat_entries, fill_beat)
            if ms is not None:
                fills.append(CuePoint(
                    position_ms=ms,
                    label=PhraseLabel.UNKNOWN,
                    slot=0,
                    name="Fill",
                    color_id=0,
                ))
        return fills
    except Exception:
        return []


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
