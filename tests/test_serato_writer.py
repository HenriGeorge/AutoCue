"""Tests for autocue/serato_writer.py"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("mutagen")

from autocue.models import CuePoint, PhraseLabel
from autocue.serato_writer import (
    FLAC_V2,
    GEOB_V1,
    GEOB_V2,
    SeratoSummary,
    build_envelope,
    build_markers2,
    has_serato_cues,
    parse_markers2,
    read_comment,
    wrap_outer,
    write_comment,
    write_serato,
    write_serato_tags,
)

# Researcher-verified worked example: CUE idx 0, 1000 ms, #CC0000, "Intro"
GOLDEN_PAYLOAD = bytes.fromhex(
    "010143554500000000120000000003e800cc00000000496e74726f0000"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cue(pos=1000, slot=0, name="Intro", color_id=2, label=PhraseLabel.INTRO):
    return CuePoint(position_ms=pos, label=label, slot=slot, name=name, color_id=color_id)


def _eight_cues():
    labels = [
        PhraseLabel.INTRO, PhraseLabel.VERSE, PhraseLabel.UP, PhraseLabel.CHORUS,
        PhraseLabel.DOWN, PhraseLabel.BRIDGE, PhraseLabel.CHORUS, PhraseLabel.OUTRO,
    ]
    return [
        _make_cue(pos=(i + 1) * 15000, slot=i, name=f"Cue {i}", color_id=(i % 8) + 1,
                  label=labels[i])
        for i in range(8)
    ]


def _minimal_mp3(path: Path) -> Path:
    """One MPEG-1 Layer III frame of silence — enough for mutagen ID3."""
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 413)
    return path


def _minimal_flac(path: Path) -> Path:
    """fLaC magic + a single (last) STREAMINFO block — parses in mutagen."""
    streaminfo = (
        (4096).to_bytes(2, "big")
        + (4096).to_bytes(2, "big")
        + (0).to_bytes(3, "big")
        + (0).to_bytes(3, "big")
        + ((44100 << 44) | (0 << 41) | (15 << 36)).to_bytes(8, "big")
        + b"\x00" * 16
    )
    path.write_bytes(b"fLaC" + bytes([0x80]) + (34).to_bytes(3, "big") + streaminfo)
    return path


def _make_content(path: Path, title="Test Track"):
    return SimpleNamespace(
        FolderPath=str(path.parent) + "/", FileNameL=path.name, Title=title
    )


# ---------------------------------------------------------------------------
# build_markers2 — serialization
# ---------------------------------------------------------------------------

class TestBuildMarkers2:
    def test_golden_bytes_single_cue(self):
        payload = build_markers2([_make_cue()])
        assert payload == GOLDEN_PAYLOAD

    def test_memory_cues_dropped(self):
        payload = build_markers2([_make_cue(slot=-1)])
        assert payload == b"\x01\x01\x00"  # header + terminator only

    def test_slots_out_of_range_dropped(self):
        payload = build_markers2([_make_cue(slot=8)])
        assert payload == b"\x01\x01\x00"

    def test_entries_sorted_by_slot(self):
        cues = [_make_cue(slot=3, name="D"), _make_cue(slot=0, name="A")]
        entries = parse_markers2(wrap_outer(build_markers2(cues)))
        assert [e["index"] for e in entries] == [0, 3]

    def test_name_falls_back_to_label(self):
        payload = build_markers2([_make_cue(name="", label=PhraseLabel.CHORUS)])
        assert b"Chorus\x00" in payload

    def test_color_id_zero_uses_label_color(self):
        # CHORUS -> LABEL_COLORS "Chorus" = 2 (Red) -> CC0000
        entries = parse_markers2(
            wrap_outer(build_markers2([_make_cue(color_id=0, label=PhraseLabel.CHORUS)]))
        )
        assert entries[0]["color"] == "cc0000"


# ---------------------------------------------------------------------------
# wrap_outer — outer tag structure
# ---------------------------------------------------------------------------

class TestWrapOuter:
    def test_starts_with_raw_header(self):
        assert wrap_outer(GOLDEN_PAYLOAD).startswith(b"\x01\x01")

    def test_padded_to_min_470(self):
        out = wrap_outer(GOLDEN_PAYLOAD)
        assert len(out) >= 470
        assert out.endswith(b"\x00")

    def test_base64_roundtrip(self):
        out = wrap_outer(GOLDEN_PAYLOAD)
        b64 = out[2:].split(b"\x00", 1)[0].replace(b"\n", b"")
        decoded = base64.b64decode(b64 + b"=" * (-len(b64) % 4))
        # '=' padding is replaced with 'A' (Serato's dialect), which decodes
        # to harmless trailing NUL-ish bytes past the payload terminator.
        assert decoded.startswith(GOLDEN_PAYLOAD)

    def test_linefeed_every_72_chars(self):
        payload = build_markers2(_eight_cues())
        out = wrap_outer(payload)
        body = out[2:].split(b"\x00", 1)[0]
        lines = body.split(b"\n")
        assert len(lines) > 1
        assert all(len(line) <= 72 for line in lines)
        assert all(len(line) == 72 for line in lines[:-1])


# ---------------------------------------------------------------------------
# parse_markers2 — decode + quirk tolerance
# ---------------------------------------------------------------------------

class TestParseMarkers2:
    def test_roundtrip_eight_cues(self):
        cues = _eight_cues()
        entries = parse_markers2(wrap_outer(build_markers2(cues)))
        assert len(entries) == 8
        for cue, entry in zip(cues, entries):
            assert entry["type"] == "CUE"
            assert entry["index"] == cue.slot
            assert entry["position_ms"] == cue.position_ms
            assert entry["name"] == cue.name

    def test_rejects_bad_header(self):
        assert parse_markers2(b"\x02\x02whatever") == []

    def test_rejects_garbage_base64(self):
        assert parse_markers2(b"\x01\x01!!!not-base64!!!\x00") == []

    def test_tolerates_len_mod_4_of_1(self):
        # Serato sometimes emits a base64 string 1 char longer than a multiple
        # of 4; parser appends "A" + padding.
        b64 = base64.b64encode(GOLDEN_PAYLOAD).decode().rstrip("=")  # 39 chars
        quirky = b64[:37]  # 37 % 4 == 1
        entries = parse_markers2(b"\x01\x01" + quirky.encode())
        assert len(entries) == 1
        assert entries[0]["index"] == 0
        assert entries[0]["position_ms"] == 1000

    def test_ignores_trailing_nul_padding(self):
        out = wrap_outer(GOLDEN_PAYLOAD)
        assert len(out) == 470  # short payload -> padded
        entries = parse_markers2(out)
        assert len(entries) == 1
        assert entries[0]["name"] == "Intro"


# ---------------------------------------------------------------------------
# build_envelope — FLAC / MP4 wrapper
# ---------------------------------------------------------------------------

class TestBuildEnvelope:
    def test_envelope_contents(self):
        env = build_envelope(GOLDEN_PAYLOAD)
        b64 = env.replace(b"\n", b"")
        raw = base64.b64decode(b64 + b"=" * (-len(b64) % 4))
        assert raw.startswith(b"application/octet-stream\x00\x00Serato Markers2\x00")
        inner = raw[len(b"application/octet-stream\x00\x00Serato Markers2\x00"):]
        entries = parse_markers2(inner)
        assert len(entries) == 1

    def test_no_padding_chars(self):
        assert b"=" not in build_envelope(GOLDEN_PAYLOAD)


# ---------------------------------------------------------------------------
# File embedding — MP3
# ---------------------------------------------------------------------------

class TestMp3Embedding:
    def test_write_and_read_back(self, tmp_path):
        from mutagen.id3 import ID3

        mp3 = _minimal_mp3(tmp_path / "track.mp3")
        cues = _eight_cues()
        write_serato_tags(mp3, cues)

        id3 = ID3(str(mp3))
        assert GEOB_V2 in id3
        frame = id3[GEOB_V2]
        assert frame.mime == "application/octet-stream"
        entries = parse_markers2(bytes(frame.data))
        assert [e["index"] for e in entries] == list(range(8))

    def test_legacy_markers_deleted(self, tmp_path):
        from mutagen.id3 import GEOB, ID3

        mp3 = _minimal_mp3(tmp_path / "track.mp3")
        id3 = ID3()
        id3.add(GEOB(encoding=0, mime="application/octet-stream", filename="",
                     desc="Serato Markers_", data=b"\x02\x05legacy"))
        id3.save(str(mp3))
        assert GEOB_V1 in ID3(str(mp3))

        write_serato_tags(mp3, [_make_cue()])

        id3 = ID3(str(mp3))
        assert GEOB_V1 not in id3
        assert GEOB_V2 in id3

    def test_has_serato_cues(self, tmp_path):
        mp3 = _minimal_mp3(tmp_path / "track.mp3")
        assert not has_serato_cues(mp3)
        write_serato_tags(mp3, [_make_cue()])
        assert has_serato_cues(mp3)


# ---------------------------------------------------------------------------
# File embedding — FLAC
# ---------------------------------------------------------------------------

class TestFlacEmbedding:
    def test_write_and_read_back(self, tmp_path):
        from mutagen.flac import FLAC

        flac = _minimal_flac(tmp_path / "track.flac")
        write_serato_tags(flac, [_make_cue()])

        f = FLAC(str(flac))
        assert FLAC_V2 in f
        b64 = f[FLAC_V2][0].replace("\n", "")
        raw = base64.b64decode(b64 + "=" * (-len(b64) % 4))
        prefix = b"application/octet-stream\x00\x00Serato Markers2\x00"
        assert raw.startswith(prefix)
        entries = parse_markers2(raw[len(prefix):])
        assert len(entries) == 1
        assert entries[0]["name"] == "Intro"

    def test_has_serato_cues(self, tmp_path):
        flac = _minimal_flac(tmp_path / "track.flac")
        assert not has_serato_cues(flac)
        write_serato_tags(flac, [_make_cue()])
        assert has_serato_cues(flac)


# ---------------------------------------------------------------------------
# write_serato — orchestration
# ---------------------------------------------------------------------------

class TestWriteSerato:
    def test_writes_fresh_file(self, tmp_path):
        mp3 = _minimal_mp3(tmp_path / "fresh.mp3")
        summary = write_serato(
            [(_make_content(mp3), [_make_cue()])],
            backup_path=tmp_path / "backup.jsonl",
            state_path=tmp_path / "state.json",
        )
        assert summary.written == 1
        assert summary.unchanged == 0
        assert not (tmp_path / "backup.jsonl").exists()  # nothing replaced
        assert has_serato_cues(mp3)

    def test_existing_tags_unknown_state_rewritten_with_backup(self, tmp_path):
        # Incremental semantics: with no state entry, existing tags are
        # REWRITTEN (backed up first) — skip now requires a fingerprint match.
        mp3 = _minimal_mp3(tmp_path / "cued.mp3")
        write_serato_tags(mp3, [_make_cue(name="Old")])
        summary = write_serato(
            [(_make_content(mp3), [_make_cue(name="New")])],
            backup_path=tmp_path / "backup.jsonl",
            state_path=tmp_path / "state.json",
        )
        assert summary.written == 1
        assert summary.unchanged == 0
        assert (tmp_path / "backup.jsonl").exists()

    def test_overwrite_writes_and_backs_up(self, tmp_path):
        from mutagen.id3 import ID3

        mp3 = _minimal_mp3(tmp_path / "cued.mp3")
        write_serato_tags(mp3, [_make_cue(name="Old")])
        original = bytes(ID3(str(mp3))[GEOB_V2].data)

        backup = tmp_path / "backup.jsonl"
        summary = write_serato(
            [(_make_content(mp3), [_make_cue(name="New")])],
            overwrite=True,
            backup_path=backup,
            state_path=tmp_path / "state.json",
        )
        assert summary.written == 1

        lines = [json.loads(l) for l in backup.read_text().splitlines()]
        assert any(
            base64.b64decode(rec["payload_b64"]) == original for rec in lines
        )
        entries = parse_markers2(bytes(ID3(str(mp3))[GEOB_V2].data))
        assert entries[0]["name"] == "New"

    def test_missing_file_counted(self, tmp_path):
        gone = tmp_path / "gone.mp3"
        summary = write_serato(
            [(_make_content(gone), [_make_cue()])],
            backup_path=tmp_path / "backup.jsonl",
            state_path=tmp_path / "state.json",
        )
        assert summary.missing == 1
        assert summary.written == 0

    def test_unsupported_suffix_counted(self, tmp_path):
        wav = tmp_path / "track.wav"
        wav.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
        summary = write_serato(
            [(_make_content(wav), [_make_cue()])],
            backup_path=tmp_path / "backup.jsonl",
            state_path=tmp_path / "state.json",
        )
        assert summary.unsupported == 1
        assert summary.written == 0

    def test_summary_dataclass_defaults(self):
        s = SeratoSummary()
        assert (s.written, s.unchanged, s.unsupported, s.missing) == (0, 0, 0, 0)
        assert s.errors == []


# ---------------------------------------------------------------------------
# read_hot_cues (db_writer) — mirror-first source for Serato export
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock  # noqa: E402

from autocue.db_writer import read_hot_cues  # noqa: E402


def _row(kind, in_msec=0, comment=None, color_table_index=None, out_msec=None):
    return SimpleNamespace(
        Kind=kind, InMsec=in_msec, Comment=comment, ColorTableIndex=color_table_index,
        OutMsec=out_msec,
    )


def _db_with_rows(rows):
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.all.return_value = rows
    db.query.return_value = q
    return db


def _content(id=1, title="Track"):
    return SimpleNamespace(ID=id, Title=title)


class TestReadHotCues:
    def test_kind_maps_to_slot_and_sorted(self):
        db = _db_with_rows([
            _row(3, 30_000, "Drop", 2),
            _row(1, 10_000, "Intro", 5),
            _row(2, 20_000, "Verse", 7),
        ])
        cues = read_hot_cues(_content(), db)
        assert [(c.slot, c.position_ms, c.name, c.color_id) for c in cues] == [
            (0, 10_000, "Intro", 5),
            (1, 20_000, "Verse", 7),
            (2, 30_000, "Drop", 2),
        ]

    def test_comment_falls_back_to_slot_letter(self):
        cues = read_hot_cues(_content(), _db_with_rows([_row(1), _row(4)]))
        assert [c.name for c in cues] == ["A", "D"]

    def test_none_color_and_msec_default_to_zero(self):
        cues = read_hot_cues(
            _content(), _db_with_rows([_row(2, in_msec=None, color_table_index=None)])
        )
        assert cues[0].position_ms == 0
        assert cues[0].color_id == 0

    def test_invalid_kind_rows_skipped(self):
        cues = read_hot_cues(
            _content(), _db_with_rows([_row(None), _row(9), _row(1, 5000)])
        )
        assert len(cues) == 1
        assert cues[0].slot == 0

    def test_label_is_unknown(self):
        cues = read_hot_cues(_content(), _db_with_rows([_row(1)]))
        assert cues[0].label is PhraseLabel.UNKNOWN

    def test_empty_library_row_set(self):
        assert read_hot_cues(_content(), _db_with_rows([])) == []

    def test_roundtrip_into_markers2(self):
        """Mirrored Rekordbox cues serialize into a valid Serato payload."""
        db = _db_with_rows([_row(1, 15_000, "Intro", 5), _row(2, 60_000, "Drop", 2)])
        cues = read_hot_cues(_content(), db)
        entries = parse_markers2(wrap_outer(build_markers2(cues)))
        assert [(e["index"], e["position_ms"], e["name"]) for e in entries] == [
            (0, 15_000, "Intro"),
            (1, 60_000, "Drop"),
        ]


class TestSeratoBase64Dialect:
    """Serato's parser silently rejects '=' padding — encoder must never emit it."""

    def test_wrap_outer_never_contains_padding_char(self):
        # try payload lengths across all three base64 padding cases
        for extra in range(3):
            cues = [
                CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0,
                         name="x" * (5 + extra), color_id=2),
            ]
            out = wrap_outer(build_markers2(cues))
            assert b"=" not in out, f"padding char leaked for name length {5 + extra}"

    def test_padding_replaced_with_A_still_roundtrips(self):
        cues = [CuePoint(position_ms=15000, label=PhraseLabel.INTRO, slot=0,
                         name="Intro", color_id=5),
                CuePoint(position_ms=60500, label=PhraseLabel.VERSE, slot=1,
                         name="Verse", color_id=7)]
        entries = parse_markers2(wrap_outer(build_markers2(cues)))
        cue_entries = [e for e in entries if e["type"] == "CUE"]
        assert [e["name"] for e in cue_entries] == ["Intro", "Verse"]
        assert [e["position_ms"] for e in cue_entries] == [15000, 60500]
# comment mirroring
# ---------------------------------------------------------------------------

def _content_with_comment(path, comment, title="Test Track"):
    c = _make_content(path, title=title)
    c.Commnt = comment
    return c


class TestCommentMirroring:
    def test_mp3_comment_written_with_cues(self, tmp_path):
        from mutagen.id3 import ID3

        mp3 = _minimal_mp3(tmp_path / "t.mp3")
        summary = write_serato(
            [(_content_with_comment(mp3, "8A - Energy 2 | Warm Up"), [_make_cue()])],
            backup_path=tmp_path / "backup.jsonl",
            state_path=tmp_path / "state.json",
        )
        assert summary.written == 1
        assert summary.comments_updated == 1
        frames = ID3(str(mp3)).getall("COMM::eng")
        assert str(frames[0].text[0]) == "8A - Energy 2 | Warm Up"

    def test_flac_comment_written_with_cues(self, tmp_path):
        from mutagen.flac import FLAC

        flac = _minimal_flac(tmp_path / "t.flac")
        summary = write_serato(
            [(_content_with_comment(flac, "11B - Energy 3 | Peak"), [_make_cue()])],
            backup_path=tmp_path / "backup.jsonl",
            state_path=tmp_path / "state.json",
        )
        assert summary.written == 1
        assert summary.comments_updated == 1
        assert FLAC(str(flac))["COMMENT"] == ["11B - Energy 3 | Peak"]

    def test_none_comment_leaves_existing_untouched(self, tmp_path):
        from mutagen.id3 import ID3

        mp3 = _minimal_mp3(tmp_path / "t.mp3")
        write_comment(mp3, "hand-written note")
        summary = write_serato(
            [(_make_content(mp3), [_make_cue()])],  # no Commnt attribute
            backup_path=tmp_path / "backup.jsonl",
            state_path=tmp_path / "state.json",
        )
        assert summary.written == 1
        assert summary.comments_updated == 0
        frames = ID3(str(mp3)).getall("COMM::eng")
        assert str(frames[0].text[0]) == "hand-written note"

    def test_changed_comment_rewrites_and_backs_up_both(self, tmp_path):
        from mutagen.id3 import ID3

        mp3 = _minimal_mp3(tmp_path / "cued.mp3")
        write_serato_tags(mp3, [_make_cue(name="Old")], comment="old comment")
        backup = tmp_path / "backup.jsonl"
        summary = write_serato(
            [(_content_with_comment(mp3, "new comment"), [_make_cue(name="New")])],
            backup_path=backup,
            state_path=tmp_path / "state.json",
        )
        assert summary.written == 1
        assert summary.comments_updated == 1
        # full rewrite: cues AND comment updated; both originals backed up
        entries = parse_markers2(bytes(ID3(str(mp3))[GEOB_V2].data))
        assert [e["name"] for e in entries if e["type"] == "CUE"] == ["New"]
        frames = ID3(str(mp3)).getall("COMM::eng")
        assert str(frames[0].text[0]) == "new comment"
        lines = [json.loads(l) for l in backup.read_text().splitlines()]
        tags = {rec["tag"] for rec in lines}
        assert GEOB_V2 in tags
        assert any(rec.get("previous") == "old comment" for rec in lines)

    def test_identical_comment_not_counted_on_rewrite(self, tmp_path):
        mp3 = _minimal_mp3(tmp_path / "cued.mp3")
        write_serato_tags(mp3, [_make_cue(name="Old")], comment="same")
        backup = tmp_path / "backup.jsonl"
        summary = write_serato(
            [(_content_with_comment(mp3, "same"), [_make_cue(name="New")])],
            backup_path=backup,
            state_path=tmp_path / "state.json",
        )
        # cue change forces a rewrite, but the unchanged comment is not counted
        assert summary.written == 1
        assert summary.comments_updated == 0

    def test_read_comment_roundtrip_flac(self, tmp_path):
        flac = _minimal_flac(tmp_path / "t.flac")
        assert read_comment(flac) is None
        write_comment(flac, "hello")
        assert read_comment(flac) == "hello"


# ---------------------------------------------------------------------------
# Loop export — Rekordbox saved loops -> Serato LOOP entries
# ---------------------------------------------------------------------------

from autocue.db_writer import read_loops  # noqa: E402
from autocue.serato_writer import _LOOP_COLOR4  # noqa: E402

_ST_VENV = (
    "/private/tmp/claude-501/-Users-henrigeorge-Projects-ddj-sx-rekordbox-bridge/"
    "a3dce9d4-839c-497b-aaf7-af41ef19bffe/scratchpad/st-venv/lib/python3.13/site-packages"
)


def _loop(start, end, name="", locked=False):
    return {"start_ms": start, "end_ms": end, "name": name, "locked": locked}


class TestReadLoops:
    def test_loop_rows_selected_and_sorted(self):
        db = _db_with_rows([
            _row(0, in_msec=60_000, out_msec=68_000, comment="Late"),
            _row(1, in_msec=10_000, out_msec=18_000, comment="Early"),
            _row(2, in_msec=30_000, out_msec=-1),          # plain cue, not a loop
            _row(3, in_msec=40_000, out_msec=None),        # no out point
            _row(4, in_msec=50_000, out_msec=50_000),      # zero-length: skipped
        ])
        loops = read_loops(_content(), db)
        assert [(l["start_ms"], l["end_ms"], l["name"]) for l in loops] == [
            (10_000, 18_000, "Early"),
            (60_000, 68_000, "Late"),
        ]

    def test_capped_at_eight(self):
        rows = [_row(0, in_msec=i * 1000, out_msec=i * 1000 + 500) for i in range(12)]
        assert len(read_loops(_content(), _db_with_rows(rows))) == 8


class TestLoopSerialization:
    def test_loop_entry_matches_reference_implementation(self):
        import sys
        if not Path(_ST_VENV).is_dir():
            pytest.skip("serato-tools reference venv not present")
        sys.path.insert(0, _ST_VENV)
        try:
            from serato_tools.track_cues_v2 import TrackCuesV2
        finally:
            sys.path.remove(_ST_VENV)
        ref = TrackCuesV2.LoopEntry(
            field1=b"\x00", index=0, startposition=5000, endposition=9000,
            field5=b"\xff\xff\xff\xff", field6=_LOOP_COLOR4,
            color=0, locked=False, name="Loop 1",
        ).dump()
        payload = build_markers2([], [_loop(5000, 9000, "Loop 1")])
        # payload = 0101 + "LOOP\0" + len(4) + data + terminator 00
        data = payload[2 + 5 + 4:-1]
        assert data == ref

    def test_cues_and_loops_roundtrip(self):
        cues = [CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0,
                         name="Intro", color_id=5)]
        loops = [_loop(5000, 9000, "Main Loop"), _loop(20_000, 28_000, "Outro Loop")]
        entries = parse_markers2(wrap_outer(build_markers2(cues, loops)))
        loop_entries = [e for e in entries if e["type"] == "LOOP"]
        assert [(e["index"], e["start_ms"], e["end_ms"], e["name"]) for e in loop_entries] == [
            (0, 5000, 9000, "Main Loop"),
            (1, 20_000, 28_000, "Outro Loop"),
        ]

    def test_loops_capped_at_eight_in_payload(self):
        loops = [_loop(i * 1000, i * 1000 + 500) for i in range(10)]
        entries = parse_markers2(wrap_outer(build_markers2([], loops)))
        assert sum(1 for e in entries if e["type"] == "LOOP") == 8


class TestLoopFileEmbed:
    def test_mp3_write_and_readback_with_loops(self, tmp_path):
        path = _minimal_mp3(tmp_path / "loops.mp3")
        cues = [CuePoint(position_ms=1500, label=PhraseLabel.CHORUS, slot=0,
                         name="Drop", color_id=2)]
        write_serato_tags(path, cues, loops=[_loop(4000, 8000, "L1", locked=True)])
        from mutagen.id3 import ID3
        entries = parse_markers2(bytes(ID3(str(path))[GEOB_V2].data))
        assert [e["type"] for e in entries] == ["CUE", "LOOP"]
        loop = entries[1]
        assert (loop["start_ms"], loop["end_ms"], loop["name"]) == (4000, 8000, "L1")


# ---------------------------------------------------------------------------
# incremental export — fingerprint + state store
# ---------------------------------------------------------------------------

from autocue.serato_writer import fingerprint  # noqa: E402


class TestFingerprint:
    def test_stable_for_same_data(self):
        cues = [_make_cue(name="Intro"), _make_cue(slot=1, pos=2000, name="Drop")]
        loops = [{"start_ms": 100, "end_ms": 900, "name": "L", "locked": False}]
        assert fingerprint(cues, loops, "c") == fingerprint(list(cues), list(loops), "c")

    def test_any_field_change_changes_hash(self):
        base_cues = [_make_cue(name="Intro")]
        base_loops = [{"start_ms": 100, "end_ms": 900, "name": "L", "locked": False}]
        base = fingerprint(base_cues, base_loops, "c")
        assert fingerprint([_make_cue(name="Other")], base_loops, "c") != base
        assert fingerprint([_make_cue(pos=1001)], base_loops, "c") != base
        assert fingerprint(base_cues, [{**base_loops[0], "end_ms": 901}], "c") != base
        assert fingerprint(base_cues, base_loops, "different") != base
        assert fingerprint(base_cues, [], "c") != base

    def test_memory_cues_ignored(self):
        mem = _make_cue(slot=-1)
        assert fingerprint([_make_cue(), mem], [], None) == fingerprint([_make_cue()], [], None)


class TestIncrementalState:
    def _run(self, tmp_path, mp3, cues, comment=None, **kw):
        content = (_content_with_comment(mp3, comment) if comment
                   else _make_content(mp3))
        return write_serato(
            [(content, cues)],
            backup_path=tmp_path / "backup.jsonl",
            state_path=tmp_path / "state.json",
            **kw,
        )

    def test_first_run_writes_and_creates_state(self, tmp_path):
        mp3 = _minimal_mp3(tmp_path / "t.mp3")
        summary = self._run(tmp_path, mp3, [_make_cue()])
        assert summary.written == 1
        state = json.loads((tmp_path / "state.json").read_text())
        assert len(state) == 1

    def test_second_run_unchanged_touches_nothing(self, tmp_path):
        mp3 = _minimal_mp3(tmp_path / "t.mp3")
        self._run(tmp_path, mp3, [_make_cue()])
        mtime = mp3.stat().st_mtime_ns
        summary = self._run(tmp_path, mp3, [_make_cue()])
        assert summary.written == 0
        assert summary.unchanged == 1
        assert mp3.stat().st_mtime_ns == mtime  # file untouched

    def test_cue_change_rewrites_only_that_file(self, tmp_path):
        from mutagen.id3 import ID3
        a = _minimal_mp3(tmp_path / "a.mp3")
        b = _minimal_mp3(tmp_path / "b.mp3")
        pairs = lambda name_a: [
            (_make_content(a), [_make_cue(name=name_a)]),
            (_make_content(b), [_make_cue(name="B")]),
        ]
        write_serato(pairs("A1"), backup_path=tmp_path / "backup.jsonl",
                     state_path=tmp_path / "state.json")
        b_mtime = b.stat().st_mtime_ns
        summary = write_serato(pairs("A2"), backup_path=tmp_path / "backup.jsonl",
                               state_path=tmp_path / "state.json")
        assert (summary.written, summary.unchanged) == (1, 1)
        assert b.stat().st_mtime_ns == b_mtime
        entries = parse_markers2(bytes(ID3(str(a))[GEOB_V2].data))
        assert entries[0]["name"] == "A2"
        assert (tmp_path / "backup.jsonl").exists()  # replaced payload backed up

    def test_corrupt_state_graceful_full_pass(self, tmp_path):
        mp3 = _minimal_mp3(tmp_path / "t.mp3")
        self._run(tmp_path, mp3, [_make_cue()])
        (tmp_path / "state.json").write_text("{ not json !!")
        summary = self._run(tmp_path, mp3, [_make_cue()])
        assert summary.written == 1  # falls back to rewriting
        json.loads((tmp_path / "state.json").read_text())  # state healed

    def test_overwrite_ignores_state(self, tmp_path):
        mp3 = _minimal_mp3(tmp_path / "t.mp3")
        self._run(tmp_path, mp3, [_make_cue()])
        summary = self._run(tmp_path, mp3, [_make_cue()], overwrite=True)
        assert summary.written == 1
        assert summary.unchanged == 0
