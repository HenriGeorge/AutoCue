"""
Writes CuePoints into audio files as Serato DJ Pro "Serato Markers2" tags,
so Serato shows AutoCue's hot cues with names and colors.

Format per the reverse-engineered spec (Holzhaus/serato-tags, cross-checked
against Mixxx and triseratops):

- inner payload: b"\\x01\\x01" + entries + b"\\x00"; each entry is a
  null-terminated ASCII type ("CUE"), a uint32-BE data length, then data.
- CUE data: reserved(1) index(1) position-ms(uint32 BE) reserved(1)
  RGB(3) reserved(2) utf-8 name + NUL.
- outer structure (what goes into the tag): b"\\x01\\x01" + base64 of the
  payload with a linefeed every 72 chars, NUL-padded to >= 470 bytes.
- Serato prefers the legacy "Serato Markers_" tag over Markers2 when both
  exist, so the legacy tag is DELETED on every write.

Container mapping: MP3/AIFF -> ID3 GEOB "Serato Markers2" (ID3v2.4);
FLAC -> vorbis SERATO_MARKERS_V2; M4A -> ----:com.serato.dj:markersv2 atom
(both wrap "application/octet-stream\\0\\0Serato Markers2\\0" + the outer
structure in their own unpadded base64).

Requires mutagen: pip install 'autocue[serato]'
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .models import LABEL_COLORS, CuePoint

try:
    import mutagen  # noqa: F401
    _HAVE_MUTAGEN = True
except ImportError:
    _HAVE_MUTAGEN = False

GEOB_V2 = "GEOB:Serato Markers2"
GEOB_V1 = "GEOB:Serato Markers_"
MP4_V2 = "----:com.serato.dj:markersv2"
MP4_V1 = "----:com.serato.dj:markers"
FLAC_V2 = "SERATO_MARKERS_V2"
_MIN_TAG_LEN = 470
_ENVELOPE = b"application/octet-stream\x00\x00Serato Markers2\x00"

# DjmdColor id -> stored Serato hot-cue RGB (HOTCUE_COLORS_INTRO palette —
# Serato stores these bytes; the UI remaps them to its display palette).
_SERATO_RGB: dict[int, bytes] = {
    1: bytes.fromhex("CC0088"),  # Pink
    2: bytes.fromhex("CC0000"),  # Red
    3: bytes.fromhex("CC4400"),  # Orange
    4: bytes.fromhex("CCCC00"),  # Yellow
    5: bytes.fromhex("00CC00"),  # Green
    6: bytes.fromhex("00CCCC"),  # Aqua
    7: bytes.fromhex("0088CC"),  # Blue
    8: bytes.fromhex("8800CC"),  # Purple
}
_DEFAULT_RGB = bytes.fromhex("CC0000")
# Serato's observed default loop color (4-byte field, docs/serato-tools "field6").
_LOOP_COLOR4 = bytes.fromhex("0027AAE1")

SUPPORTED_SUFFIXES = {".mp3", ".aiff", ".aif", ".flac", ".m4a", ".mp4"}


def _require_mutagen() -> None:
    if not _HAVE_MUTAGEN:
        raise RuntimeError(
            "Serato export requires mutagen — install with: pip install 'autocue[serato]'"
        )


def _cue_rgb(cue: CuePoint) -> bytes:
    color_id = cue.color_id or LABEL_COLORS.get(cue.label.value, 0)
    return _SERATO_RGB.get(color_id, _DEFAULT_RGB)


# ---------------------------------------------------------------- serialization

def build_markers2(cues: list[CuePoint], loops: list[dict] | None = None) -> bytes:
    """Inner decoded payload: header + CUE entries + LOOP entries + terminator.

    `loops` items: {"start_ms", "end_ms", "name", "locked"} (see
    db_writer.read_loops). LOOP layout per the serato-tools reference
    (struct >cBII4s4sB? + NUL-terminated name): reserved, index, start,
    end, 0xFFFFFFFF, 4-byte color, pad, locked.
    """
    out = [b"\x01\x01"]
    for cue in sorted(cues, key=lambda c: c.slot):
        if cue.slot < 0 or cue.slot > 7:
            continue  # memory cues have no Serato equivalent
        name = (cue.name or cue.label.value).encode("utf-8")
        data = (
            b"\x00"
            + bytes([cue.slot])
            + cue.position_ms.to_bytes(4, "big")
            + b"\x00"
            + _cue_rgb(cue)
            + b"\x00\x00"
            + name
            + b"\x00"
        )
        out.append(b"CUE\x00" + len(data).to_bytes(4, "big") + data)
    for index, loop in enumerate((loops or [])[:8]):
        data = (
            b"\x00"
            + bytes([index])
            + int(loop["start_ms"]).to_bytes(4, "big")
            + int(loop["end_ms"]).to_bytes(4, "big")
            + b"\xff\xff\xff\xff"
            + _LOOP_COLOR4
            + b"\x00"
            + (b"\x01" if loop.get("locked") else b"\x00")
            + str(loop.get("name") or "").encode("utf-8")
            + b"\x00"
        )
        out.append(b"LOOP\x00" + len(data).to_bytes(4, "big") + data)
    out.append(b"\x00")
    return b"".join(out)


def wrap_outer(payload: bytes) -> bytes:
    """Outer tag structure: raw 0101 + linefed base64 + NUL pad to 470 bytes.

    Serato's base64 dialect NEVER emits '=' padding — its parser silently
    rejects tags that contain it. Standard '=' chars are replaced with 'A',
    matching what Serato itself and the reference implementations write.
    """
    b64 = base64.b64encode(payload).decode("ascii").replace("=", "A")
    lined = "\n".join(b64[i:i + 72] for i in range(0, len(b64), 72))
    out = b"\x01\x01" + lined.encode("ascii")
    if len(out) < _MIN_TAG_LEN:
        out += b"\x00" * (_MIN_TAG_LEN - len(out))
    return out


def build_envelope(payload: bytes) -> bytes:
    """FLAC/MP4 value: their own base64 (no padding, 72-char lines) of
    mime + description envelope + the outer structure."""
    raw = _ENVELOPE + wrap_outer(payload)
    b64 = base64.b64encode(raw).decode("ascii").rstrip("=")
    return "\n".join(b64[i:i + 72] for i in range(0, len(b64), 72)).encode("ascii")


def parse_markers2(outer: bytes) -> list[dict]:
    """Decode an outer tag structure back into a list of entry dicts.
    Tolerant of Serato's base64 quirks (stray length, NUL padding)."""
    if not outer.startswith(b"\x01\x01"):
        return []
    b64 = outer[2:].split(b"\x00", 1)[0].replace(b"\n", b"").decode("ascii", "ignore")
    if len(b64) % 4 == 1:
        b64 += "A"  # Serato sometimes emits length % 4 == 1
    b64 += "=" * (-len(b64) % 4)
    try:
        payload = base64.b64decode(b64)
    except Exception:
        return []
    if not payload.startswith(b"\x01\x01"):
        return []
    entries = []
    i = 2
    while i < len(payload) and payload[i] != 0:
        end = payload.index(b"\x00", i)
        etype = payload[i:end].decode("ascii", "ignore")
        length = int.from_bytes(payload[end + 1:end + 5], "big")
        data = payload[end + 5:end + 5 + length]
        entry: dict = {"type": etype}
        if etype == "CUE" and length >= 13:
            entry.update(
                index=data[1],
                position_ms=int.from_bytes(data[2:6], "big"),
                color=data[7:10].hex(),
                name=data[12:].split(b"\x00", 1)[0].decode("utf-8", "replace"),
            )
        elif etype == "LOOP" and length >= 21:
            entry.update(
                index=data[1],
                start_ms=int.from_bytes(data[2:6], "big"),
                end_ms=int.from_bytes(data[6:10], "big"),
                name=data[20:].split(b"\x00", 1)[0].decode("utf-8", "replace"),
            )
        entries.append(entry)
        i = end + 5 + length
    return entries


# ------------------------------------------------------------- file embedding

def _read_existing(path: Path) -> dict[str, bytes]:
    """Return {tag_name: raw_bytes} for any Serato marker tags in the file."""
    _require_mutagen()
    suffix = path.suffix.lower()
    found: dict[str, bytes] = {}
    if suffix in (".mp3", ".aiff", ".aif"):
        from mutagen.id3 import ID3
        from mutagen.id3._util import ID3NoHeaderError
        try:
            id3 = ID3(str(path))
        except ID3NoHeaderError:
            return {}
        for key in (GEOB_V2, GEOB_V1):
            if key in id3:
                found[key] = bytes(id3[key].data)
    elif suffix == ".flac":
        from mutagen.flac import FLAC
        f = FLAC(str(path))
        for key in (FLAC_V2, "SERATO_MARKERS"):
            if key in f:
                found[key] = f[key][0].encode("ascii", "ignore")
    elif suffix in (".m4a", ".mp4"):
        from mutagen.mp4 import MP4
        f = MP4(str(path))
        for key in (MP4_V2, MP4_V1):
            if f.tags and key in f.tags:
                found[key] = bytes(f.tags[key][0])
    return found


def has_serato_cues(path: Path) -> bool:
    """True if the file already carries any Serato marker tag (v1 or v2)."""
    return bool(_read_existing(path))


def read_comment(path: Path) -> str | None:
    """Return the file's standard comment tag, or None if absent."""
    _require_mutagen()
    suffix = path.suffix.lower()
    if suffix in (".mp3", ".aiff", ".aif"):
        from mutagen.id3 import ID3
        from mutagen.id3._util import ID3NoHeaderError
        try:
            id3 = ID3(str(path))
        except ID3NoHeaderError:
            return None
        frames = id3.getall("COMM::eng")
        return str(frames[0].text[0]) if frames and frames[0].text else None
    if suffix == ".flac":
        from mutagen.flac import FLAC
        vals = FLAC(str(path)).get("COMMENT")
        return vals[0] if vals else None
    if suffix in (".m4a", ".mp4"):
        from mutagen.mp4 import MP4
        f = MP4(str(path))
        vals = f.tags.get("\xa9cmt") if f.tags else None
        return vals[0] if vals else None
    return None


def write_comment(path: Path, comment: str) -> None:
    """Write the standard comment tag (COMM::eng / COMMENT / ©cmt)."""
    _require_mutagen()
    suffix = path.suffix.lower()
    if suffix in (".mp3", ".aiff", ".aif"):
        from mutagen.id3 import COMM, ID3
        from mutagen.id3._util import ID3NoHeaderError
        try:
            id3 = ID3(str(path))
        except ID3NoHeaderError:
            id3 = ID3()
        id3.setall("COMM::eng", [COMM(encoding=3, lang="eng", desc="", text=[comment])])
        id3.save(str(path), v2_version=4)
    elif suffix == ".flac":
        from mutagen.flac import FLAC
        f = FLAC(str(path))
        f["COMMENT"] = [comment]
        f.save()
    elif suffix in (".m4a", ".mp4"):
        from mutagen.mp4 import MP4
        f = MP4(str(path))
        if f.tags is None:
            f.add_tags()
        f.tags["\xa9cmt"] = [comment]
        f.save()
    else:
        raise ValueError(f"Unsupported container for Serato export: {suffix}")


def write_serato_tags(path: Path, cues: list[CuePoint], comment: str | None = None,
                      loops: list[dict] | None = None) -> None:
    """Write the Markers2 tag for `cues` (+ saved `loops`) into `path`,
    deleting legacy tags.

    When `comment` is a non-empty string, the file's standard comment tag is
    written in the same save; None/empty leaves the existing comment untouched.
    """
    _require_mutagen()
    payload = build_markers2(cues, loops)
    suffix = path.suffix.lower()

    if suffix in (".mp3", ".aiff", ".aif"):
        from mutagen.id3 import COMM, GEOB, ID3
        from mutagen.id3._util import ID3NoHeaderError
        try:
            id3 = ID3(str(path))
        except ID3NoHeaderError:
            id3 = ID3()
        id3.delall("GEOB:Serato Markers_")
        id3.setall(
            "GEOB:Serato Markers2",
            [GEOB(encoding=0, mime="application/octet-stream", filename="",
                  desc="Serato Markers2", data=wrap_outer(payload))],
        )
        if comment:
            id3.setall("COMM::eng", [COMM(encoding=3, lang="eng", desc="", text=[comment])])
        id3.save(str(path), v2_version=4)

    elif suffix == ".flac":
        from mutagen.flac import FLAC
        f = FLAC(str(path))
        f.pop("SERATO_MARKERS", None)
        f[FLAC_V2] = build_envelope(payload).decode("ascii")
        if comment:
            f["COMMENT"] = [comment]
        f.save()

    elif suffix in (".m4a", ".mp4"):
        from mutagen.mp4 import MP4, MP4FreeForm
        f = MP4(str(path))
        if f.tags is None:
            f.add_tags()
        f.tags.pop(MP4_V1, None)
        f.tags[MP4_V2] = [MP4FreeForm(build_envelope(payload))]
        if comment:
            f.tags["\xa9cmt"] = [comment]
        f.save()

    else:
        raise ValueError(f"Unsupported container for Serato export: {suffix}")


# -------------------------------------------------------------- orchestration

def fingerprint(cues: list[CuePoint], loops: list[dict] | None, comment: str | None) -> str:
    """Stable hash of everything the export writes for one track.

    Canonicalizes to the EFFECTIVE exported values (name fallback, resolved
    RGB) so any change that would alter the file changes the fingerprint,
    and nothing else does.
    """
    canon = {
        "cues": [
            [c.slot, c.position_ms, (c.name or c.label.value), _cue_rgb(c).hex()]
            for c in sorted(cues, key=lambda c: c.slot)
            if 0 <= c.slot <= 7
        ],
        "loops": [
            [int(l["start_ms"]), int(l["end_ms"]), str(l.get("name") or ""),
             bool(l.get("locked"))]
            for l in (loops or [])[:8]
        ],
        "comment": comment or "",
    }
    blob = json.dumps(canon, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _load_state(path: Path) -> dict:
    """Track-ID -> fingerprint map from the last run; {} when missing/corrupt."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=0), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class SeratoSummary:
    written: int = 0
    unchanged: int = 0
    unsupported: int = 0
    missing: int = 0
    comments_updated: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


def _backup_comment(backup_path: Path, path: Path, previous: str) -> None:
    with backup_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "path": str(path), "tag": "comment",
            "previous": previous, "ts": int(time.time()),
        }) + "\n")


def write_serato(
    tracks: list[tuple],
    *,
    overwrite: bool = False,
    backup_path: str | Path = "autocue_serato_backup.jsonl",
    state_path: str | Path = "autocue_serato_state.json",
) -> SeratoSummary:
    """Write Serato tags for (content, cues[, loops]) items into the audio files.

    INCREMENTAL by default: a per-track fingerprint of everything exported
    (cues, loops, comment) is kept in `state_path`; tracks whose fingerprint
    matches the last run — and whose file already carries Serato tags — are
    left completely untouched (no write, no mtime change). Anything new or
    changed is (re)written, with existing tag payloads and replaced comments
    appended to `backup_path` (JSONL) for hand-restore. `overwrite=True`
    ignores the state and rewrites everything (state still updated).
    """
    from .writer import _resolve_file_path

    summary = SeratoSummary()
    backup_path = Path(backup_path)
    state_path = Path(state_path)
    state = _load_state(state_path)

    for item in tracks:
        content, cues = item[0], item[1]
        loops = item[2] if len(item) > 2 else []
        path = Path(_resolve_file_path(content))
        title = content.Title or content.FileNameL or str(path)
        if not path.exists():
            summary.missing += 1
            print(f"  {title}: file not found — {path}")
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            summary.unsupported += 1
            print(f"  {title}: {path.suffix} not supported for Serato export, skipped")
            continue
        try:
            comment = (getattr(content, "Commnt", None) or "").strip() or None
            fp = fingerprint(cues, loops, comment)
            track_id = str(getattr(content, "ID", None) or path)

            existing = _read_existing(path)
            if not overwrite and existing and state.get(track_id) == fp:
                summary.unchanged += 1
                continue

            current_comment = read_comment(path) if comment else None
            comment_changed = comment is not None and comment != (current_comment or "")
            if existing:
                with backup_path.open("a", encoding="utf-8") as fh:
                    for tag, raw in existing.items():
                        fh.write(json.dumps({
                            "path": str(path), "tag": tag,
                            "payload_b64": base64.b64encode(raw).decode("ascii"),
                            "ts": int(time.time()),
                        }) + "\n")
            if comment_changed and current_comment:
                _backup_comment(backup_path, path, current_comment)
            write_serato_tags(path, cues, comment=comment if comment_changed else None,
                              loops=loops)
            summary.written += 1
            state[track_id] = fp
            if comment_changed:
                summary.comments_updated += 1
        except Exception as e:  # keep going; report at the end
            summary.errors.append((str(path), str(e)))
            print(f"  {title}: ERROR — {e}")

    _save_state(state_path, state)
    return summary
