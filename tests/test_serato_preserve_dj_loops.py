"""`--serato` must not destroy the DJ's Serato-native loops.

THE BUG (main)
``write_serato`` reads the old tag only for the fingerprint skip and the JSONL
backup — it is never parsed for LOOP entries and never fed back into the payload.
``write_serato_tags`` then does a FULL tag replacement, rebuilding the payload
exclusively from ``cues`` (read_hot_cues) + ``loops`` (read_loops = the Rekordbox
DB). Any LOOP entry that exists in the FILE but not in the DB — i.e. a loop the DJ
made **in Serato** — is simply never re-emitted, and is dropped on every run.
(The prior raw tag IS appended to autocue_serato_backup.jsonl, so it is
recoverable by hand-restoring the ENTIRE previous Markers2 tag — which also
reverts AutoCue's cues. No per-loop restore. Real data loss; not unrecoverable.)

🔑 DEDUP IS MANDATORY, NOT POLITENESS
Preserving *every* file LOOP entry would double-count AutoCue's own loops: run 1
writes DB loop X into the file; run 2 would preserve the file's X **and** re-emit X
from read_loops() → X twice, growing every rewrite until the 8-slot cap. So only
FOREIGN loops are preserved. Discriminator = exact ``start_ms`` equality (safe:
read_loops takes it from DjmdCue.InMsec (int) and build_markers2 writes/parses it
as an exact u32be round-trip).
    * file loop matching a DB loop's start → ours → regenerate (DB is AUTHORITATIVE,
      so a re-tuned loop end actually updates)
    * file loop with no DB match         → FOREIGN → preserve raw bytes verbatim

Throwaway files only — never a real library audio file.
"""
from __future__ import annotations

import base64
import logging

import pytest

pytest.importorskip("mutagen")

from autocue.models import CuePoint, PhraseLabel
from autocue.serato_writer import GEOB_V2, wrap_outer, write_serato_tags

_LOOP_COLOR4 = bytes.fromhex("0027AAE1")


# ---------------------------------------------------------------------------
# An INDEPENDENT byte-walker — deliberately not parse_markers2, so these tests
# cannot be satisfied by a bug in the very parser they are meant to police.
# ---------------------------------------------------------------------------

def _decode_payload(outer: bytes) -> bytes:
    b64 = outer[2:].split(b"\x00", 1)[0].replace(b"\n", b"").decode("ascii", "ignore")
    if len(b64) % 4 == 1:
        b64 += "A"
    b64 += "=" * (-len(b64) % 4)
    return base64.b64decode(b64)


def _entries(outer: bytes) -> list[dict]:
    payload = _decode_payload(outer)
    out: list[dict] = []
    i = 2
    while i < len(payload) and payload[i] != 0:
        end = payload.index(b"\x00", i)
        etype = payload[i:end].decode("ascii", "ignore")
        length = int.from_bytes(payload[end + 1:end + 5], "big")
        data = payload[end + 5:end + 5 + length]
        e: dict = {
            "type": etype,
            "raw": payload[i:end + 5 + length],
            "index": data[1] if length > 1 else None,
        }
        if etype == "LOOP" and length >= 21:
            e.update(
                start_ms=int.from_bytes(data[2:6], "big"),
                end_ms=int.from_bytes(data[6:10], "big"),
                locked=bool(data[19]),                       # 0x13
                name=data[20:].split(b"\x00", 1)[0].decode("utf-8", "replace"),
            )
        elif etype == "CUE" and length >= 13:
            e.update(
                position_ms=int.from_bytes(data[2:6], "big"),
                name=data[12:].split(b"\x00", 1)[0].decode("utf-8", "replace"),
            )
        out.append(e)
        i = end + 5 + length
    return out


def _loop_entry_bytes(index, start, end, name, locked=False) -> bytes:
    """A LOOP entry exactly as Serato/main lay it out — used to plant a loop at a
    chosen slot index, which build_markers2 alone cannot do."""
    data = (
        b"\x00"
        + bytes([index])
        + int(start).to_bytes(4, "big")
        + int(end).to_bytes(4, "big")
        + b"\xff\xff\xff\xff"
        + _LOOP_COLOR4
        + b"\x00"
        + (b"\x01" if locked else b"\x00")
        + name.encode("utf-8")
        + b"\x00"
    )
    return b"LOOP\x00" + len(data).to_bytes(4, "big") + data


# ---------------------------------------------------------------------------
# Throwaway file helpers
# ---------------------------------------------------------------------------

def _mp3(tmp_path, name="t.mp3"):
    p = tmp_path / name
    p.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 413)   # one silent MPEG frame
    return p


def _outer(path) -> bytes:
    from mutagen.id3 import ID3
    return bytes(ID3(str(path))[GEOB_V2].data)


def _put_raw_tag(path, payload: bytes) -> None:
    """Embed a hand-built payload as the file's Markers2 tag (simulates Serato)."""
    from mutagen.id3 import GEOB, ID3
    from mutagen.id3._util import ID3NoHeaderError
    try:
        id3 = ID3(str(path))
    except ID3NoHeaderError:
        id3 = ID3()
    id3.setall(GEOB_V2, [GEOB(encoding=0, mime="application/octet-stream", filename="",
                              desc="Serato Markers2", data=wrap_outer(payload))])
    id3.save(str(path), v2_version=4)


def _cue(pos=1000, slot=0, name="Intro"):
    return CuePoint(position_ms=pos, label=PhraseLabel.INTRO, slot=slot, name=name)


def _loops(path):
    return [e for e in _entries(_outer(path)) if e["type"] == "LOOP"]


def _db_loop(start, end, name, locked=False):
    return {"start_ms": start, "end_ms": end, "name": name, "locked": locked}


# ===========================================================================
# ★ THE BUG — a loop the DJ made IN SERATO must survive a --serato rewrite
# ===========================================================================

class TestForeignLoopSurvives:
    def test_dj_serato_native_loop_survives_byte_identical(self, tmp_path):
        mp3 = _mp3(tmp_path)
        # The DJ saved this loop in Serato: custom name, off-policy length, LOCKED.
        dj = _db_loop(111_000, 123_456, "DJ's Weird Loop", locked=True)
        write_serato_tags(mp3, [_cue()], loops=[dj])
        before = _loops(mp3)
        assert len(before) == 1 and before[0]["locked"] is True
        dj_raw = before[0]["raw"]

        # AutoCue rewrites the tag from the DB — which knows nothing of that loop.
        write_serato_tags(mp3, [_cue()], loops=[_db_loop(30_000, 45_000, "Mix In Loop")])

        after = _loops(mp3)
        names = {e["name"] for e in after}
        assert "DJ's Weird Loop" in names, "the DJ's Serato-native loop was DROPPED"
        survivor = next(e for e in after if e["name"] == "DJ's Weird Loop")
        assert survivor["raw"] == dj_raw, "preserved loop was not byte-identical"
        assert survivor["locked"] is True                 # locked flag kept
        assert "Mix In Loop" in names                     # ...and ours was written

    def test_foreign_loop_survives_in_flac(self, tmp_path):
        flac = tmp_path / "t.flac"
        streaminfo = (
            (4096).to_bytes(2, "big") + (4096).to_bytes(2, "big")
            + (0).to_bytes(3, "big") + (0).to_bytes(3, "big")
            + ((44100 << 44) | (0 << 41) | (15 << 36)).to_bytes(8, "big") + b"\x00" * 16
        )
        flac.write_bytes(b"fLaC" + bytes([0x80]) + (34).to_bytes(3, "big") + streaminfo)
        write_serato_tags(flac, [_cue()], loops=[_db_loop(111_000, 123_456, "DJ Loop")])
        write_serato_tags(flac, [_cue()], loops=[_db_loop(30_000, 45_000, "Mix In Loop")])

        from autocue.serato_writer import FLAC_V2, _envelope_payload
        from mutagen.flac import FLAC
        raw = FLAC(str(flac))[FLAC_V2][0].encode("ascii", "ignore")
        names = {e["name"] for e in _entries(_envelope_payload(raw)) if e["type"] == "LOOP"}
        assert "DJ Loop" in names, "the DJ's loop was dropped from the FLAC tag"
        assert "Mix In Loop" in names


# ===========================================================================
# 🔑 DEDUP — the fix must not double-count AutoCue's OWN loops
# (green before AND after: it is the guard against a naive preserve-everything)
# ===========================================================================

class TestNoDoubleCount:
    def test_autocue_loops_not_duplicated_across_repeated_rewrites(self, tmp_path):
        mp3 = _mp3(tmp_path)
        db = [_db_loop(30_000, 45_000, "Mix In Loop"),
              _db_loop(190_000, 200_000, "Mix Out Loop")]
        for _ in range(3):
            write_serato_tags(mp3, [_cue()], loops=db)
        loops = _loops(mp3)
        assert len(loops) == 2, (
            f"AutoCue's own loops were duplicated across rewrites: "
            f"{[l['name'] for l in loops]}"
        )

    def test_db_is_authoritative_a_retuned_loop_end_updates(self, tmp_path):
        # The file already has our loop; the DB's end moved. The file must FOLLOW —
        # a preserved stale file-loop must never shadow the DB.
        mp3 = _mp3(tmp_path)
        write_serato_tags(mp3, [_cue()], loops=[_db_loop(30_000, 45_000, "Mix In Loop")])
        write_serato_tags(mp3, [_cue()], loops=[_db_loop(30_000, 60_000, "Mix In Loop")])
        loops = _loops(mp3)
        assert len(loops) == 1
        assert loops[0]["end_ms"] == 60_000, "the stale file loop shadowed the DB"


# ===========================================================================
# The 8-slot cap — the DJ wins
# ===========================================================================

class TestEightSlotCap:
    def test_dj_loops_win_and_surplus_generated_are_dropped_with_a_breadcrumb(
            self, tmp_path, caplog):
        mp3 = _mp3(tmp_path)
        dj = [_db_loop(100_000 + i * 1000, 100_500 + i * 1000, f"DJ {i}", locked=True)
              for i in range(6)]
        write_serato_tags(mp3, [_cue()], loops=dj)          # 6 DJ loops now in the file

        gen = [_db_loop(i * 1000, i * 1000 + 500, f"Gen {i}") for i in range(5)]
        with caplog.at_level(logging.WARNING):
            write_serato_tags(mp3, [_cue()], loops=gen)     # 6 preserved + 5 generated > 8

        loops = _loops(mp3)
        names = {e["name"] for e in loops}
        assert len(loops) == 8                              # Serato's hard cap
        for i in range(6):
            assert f"DJ {i}" in names, "a DJ loop was dropped to make room for a generated one"
        assert len([n for n in names if n.startswith("Gen")]) == 2   # only the free slots
        assert any("drop" in r.getMessage().lower() for r in caplog.records), \
            "surplus generated loops must be dropped with a breadcrumb, never silently"

    def test_preserved_loop_at_index_7_never_pushes_a_generated_loop_past_7(self, tmp_path):
        # Slot indices are a single byte and Serato only has slots 0-7. A naive
        # `max(preserved)+1` would emit index 8 here.
        mp3 = _mp3(tmp_path)
        payload = (b"\x01\x01"
                   + _loop_entry_bytes(7, 111_000, 123_456, "DJ At Seven", locked=True)
                   + b"\x00")
        _put_raw_tag(mp3, payload)

        write_serato_tags(mp3, [_cue()], loops=[_db_loop(30_000, 45_000, "Mix In Loop")])

        loops = _loops(mp3)
        idx = {e["index"] for e in loops}
        assert idx <= set(range(8)), f"loop slot index out of Serato's 0-7 range: {idx}"
        assert {e["name"] for e in loops} == {"DJ At Seven", "Mix In Loop"}
        assert next(e for e in loops if e["name"] == "DJ At Seven")["index"] == 7

    def test_non_contiguous_preserved_indices(self, tmp_path):
        mp3 = _mp3(tmp_path)
        payload = (b"\x01\x01"
                   + _loop_entry_bytes(0, 111_000, 112_000, "DJ Zero")
                   + _loop_entry_bytes(5, 211_000, 212_000, "DJ Five")
                   + b"\x00")
        _put_raw_tag(mp3, payload)
        write_serato_tags(mp3, [_cue()], loops=[_db_loop(30_000, 45_000, "Gen")])
        loops = _loops(mp3)
        by_name = {e["name"]: e["index"] for e in loops}
        assert by_name["DJ Zero"] == 0 and by_name["DJ Five"] == 5
        assert by_name["Gen"] not in (0, 5) and by_name["Gen"] < 8   # lowest free slot


# ===========================================================================
# Regressions + the silent-failure lens
# ===========================================================================

class TestRegressionAndFailures:
    def test_cue_entries_are_byte_identical_with_a_preserved_loop_alongside(self, tmp_path):
        mp3 = _mp3(tmp_path)
        cues = [_cue(1000, 0, "Intro"), _cue(60_000, 3, "Drop")]
        write_serato_tags(mp3, cues)                        # cues only, no loops
        cue_raws = [e["raw"] for e in _entries(_outer(mp3)) if e["type"] == "CUE"]
        assert len(cue_raws) == 2

        write_serato_tags(mp3, cues, loops=[_db_loop(30_000, 45_000, "L")])
        after = [e["raw"] for e in _entries(_outer(mp3)) if e["type"] == "CUE"]
        assert after == cue_raws, "CUE bytes changed when a loop rode alongside"

    def test_undecodable_v2_tag_warns_and_the_write_still_succeeds(self, tmp_path, caplog):
        # Silent-failure lens: if a present v2 tag can't be decoded we cannot
        # preserve its loops — say so, don't drop them silently.
        mp3 = _mp3(tmp_path)
        from mutagen.id3 import GEOB, ID3
        id3 = ID3()
        id3.setall(GEOB_V2, [GEOB(encoding=0, mime="application/octet-stream", filename="",
                                  desc="Serato Markers2", data=b"\x01\x01!!!not-base64!!!")])
        id3.save(str(mp3), v2_version=4)

        with caplog.at_level(logging.WARNING):
            write_serato_tags(mp3, [_cue()], loops=[_db_loop(30_000, 45_000, "Mix In Loop")])

        assert {e["name"] for e in _loops(mp3)} == {"Mix In Loop"}   # write succeeded
        assert any("preserv" in r.getMessage().lower() or "decode" in r.getMessage().lower()
                   for r in caplog.records), "an undecodable v2 tag must warn"

    def test_no_existing_tag_is_a_clean_write(self, tmp_path):
        mp3 = _mp3(tmp_path)
        write_serato_tags(mp3, [_cue()], loops=[_db_loop(30_000, 45_000, "Mix In Loop")])
        assert {e["name"] for e in _loops(mp3)} == {"Mix In Loop"}


class TestFingerprintSkipInteraction:
    """The incremental fingerprint covers DB cues/loops/comment — NOT foreign
    loops. That is safe *because the skip path performs no write at all*: a
    skipped track's file is never rewritten, so the DJ's loops cannot be harmed
    while skipped. (Folding foreign loops into the fingerprint would instead force
    a pointless rewrite every time the DJ touched a loop in Serato.)"""

    def test_a_skipped_export_leaves_the_djs_loop_untouched_and_undoubled(
            self, tmp_path, monkeypatch):
        from types import SimpleNamespace
        import autocue.serato_writer as sw
        from autocue.serato_writer import write_serato

        mp3 = _mp3(tmp_path, "song.mp3")
        monkeypatch.setattr(sw, "_resolve_file_path", lambda c: str(mp3), raising=False)
        monkeypatch.setattr("autocue.writer._resolve_file_path", lambda c: str(mp3))

        content = SimpleNamespace(ID=1, Title="Song", FileNameL="song.mp3", Commnt="")
        db_loops = [_db_loop(30_000, 45_000, "Mix In Loop")]
        state = tmp_path / "state.json"
        backup = tmp_path / "backup.jsonl"

        # Run 1 — writes cues + our DB loop, records the fingerprint.
        s1 = write_serato([(content, [_cue()], db_loops)],
                          state_path=state, backup_path=backup)
        assert s1.written == 1

        # The DJ then saves their own loop in Serato (planted straight into the file).
        dj = _loop_entry_bytes(5, 111_000, 123_456, "DJ Loop", locked=True)
        existing = b"".join(e["raw"] for e in _entries(_outer(mp3)))
        _put_raw_tag(mp3, b"\x01\x01" + existing + dj + b"\x00")
        assert {e["name"] for e in _loops(mp3)} == {"Mix In Loop", "DJ Loop"}

        # Run 2 — nothing changed in the DB → fingerprint matches → SKIP (no write).
        s2 = write_serato([(content, [_cue()], db_loops)],
                          state_path=state, backup_path=backup)
        assert s2.unchanged == 1 and s2.written == 0

        loops = _loops(mp3)
        names = [e["name"] for e in loops]
        assert "DJ Loop" in names                    # survived the skip
        assert names.count("Mix In Loop") == 1       # and ours was not doubled
