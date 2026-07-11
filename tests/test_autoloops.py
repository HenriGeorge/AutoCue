"""TDD unit tests for AUTOLOOPS increment 1 (Serato-first).

This is the implementer's TDD file (crew build). The verifier owns a DISJOINT
golden/behavioural file — this file exercises the *logic* of each unit:

  1. Keystone       — CuePoint loop fields + is_loop        (models.py)
  2. Loop policy    — plan_loops() pure generation           (analyzer.py)
  3. Serato LOOP    — build_markers2 / parse_markers2 / preserve (serato_writer.py)
  4. Mirror read    — read_hot_cues carries OutMsec           (db_writer.py)
  5. CLI flag       — --loops is wired                         (cli.py)

Design: crew/DESIGN.md (§1-§4, F1/F6) · byte layout: crew/researcher.md §1.
"""
from __future__ import annotations

from autocue.models import CuePoint, PhraseLabel


def _ph(pos_ms, label, bars):
    """(position_ms, PhraseLabel, phrase_bars) tuple for plan_loops."""
    return (pos_ms, label, bars)


# ---------------------------------------------------------------------------
# Unit 1 — Keystone: CuePoint gains loop_end_ms / loop_beats / is_loop
# ---------------------------------------------------------------------------

class TestCuePointLoopFields:
    def test_non_loop_cue_defaults_are_regression_safe(self):
        # A plain cue built the old way behaves exactly as before.
        cue = CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0, name="Intro")
        assert cue.loop_end_ms is None
        assert cue.loop_beats is None
        assert cue.is_loop is False

    def test_loop_end_ms_makes_it_a_loop(self):
        cue = CuePoint(
            position_ms=10_000, label=PhraseLabel.OUTRO, slot=-1, name="Outro",
            loop_end_ms=18_000, loop_beats=32,
        )
        assert cue.is_loop is True
        assert cue.loop_end_ms == 18_000
        assert cue.loop_beats == 32

    def test_is_loop_only_keys_on_loop_end_ms(self):
        # loop_beats set but no end -> still NOT a loop (end is the discriminator).
        cue = CuePoint(position_ms=0, label=PhraseLabel.DOWN, slot=-1, loop_beats=16)
        assert cue.is_loop is False

    def test_position_sec_unchanged(self):
        cue = CuePoint(position_ms=2500, label=PhraseLabel.INTRO, slot=0)
        assert cue.position_sec == 2.5


# ---------------------------------------------------------------------------
# Unit 2 — Loop-generation policy (GRILLED §2): plan_loops()
# ---------------------------------------------------------------------------

# bar_ms = 500 ms/beat * 4 = 2000 ms/bar (i.e. 120 BPM). Easy round numbers.
BAR_MS = 2000.0


class TestPlanLoopsLabelRestriction:
    def test_only_intro_outro_break_by_default(self):
        from autocue.analyzer import plan_loops
        phrases = [
            _ph(0, PhraseLabel.INTRO, 16),
            _ph(60_000, PhraseLabel.VERSE, 16),
            _ph(120_000, PhraseLabel.CHORUS, 16),
            _ph(180_000, PhraseLabel.BRIDGE, 16),
            _ph(240_000, PhraseLabel.DOWN, 8),
            _ph(300_000, PhraseLabel.UP, 8),
            _ph(360_000, PhraseLabel.OUTRO, 16),
        ]
        loops = plan_loops(phrases, BAR_MS)
        names = {c.name for c in loops}
        assert names == {"Intro", "Outro", "Break"}  # no Verse/Drop/Bridge/Build

    def test_never_loops_verse_chorus_bridge(self):
        from autocue.analyzer import plan_loops
        phrases = [
            _ph(0, PhraseLabel.VERSE, 16),
            _ph(60_000, PhraseLabel.CHORUS, 16),
            _ph(120_000, PhraseLabel.BRIDGE, 16),
        ]
        assert plan_loops(phrases, BAR_MS, include_build=True) == []

    def test_build_only_with_flag(self):
        from autocue.analyzer import plan_loops
        phrases = [_ph(0, PhraseLabel.UP, 8)]
        assert plan_loops(phrases, BAR_MS) == []  # off by default
        loops = plan_loops(phrases, BAR_MS, include_build=True)
        assert [c.name for c in loops] == ["Build"]


class TestPlanLoopsLength:
    def test_power_of_two_round_down(self):
        from autocue.analyzer import plan_loops
        # 10-bar intro -> largest power-of-2 <= 10 (capped 16) = 8 bars
        loops = plan_loops([_ph(0, PhraseLabel.INTRO, 10)], BAR_MS)
        assert len(loops) == 1
        assert loops[0].loop_beats == 8 * 4  # 8 bars
        assert loops[0].loop_end_ms == int(round(0 + 8 * BAR_MS))

    def test_intro_capped_at_16_bars(self):
        from autocue.analyzer import plan_loops
        loops = plan_loops([_ph(5000, PhraseLabel.OUTRO, 40)], BAR_MS)
        assert loops[0].loop_beats == 16 * 4  # capped at 16
        assert loops[0].loop_end_ms == int(round(5000 + 16 * BAR_MS))

    def test_break_capped_at_8_bars(self):
        from autocue.analyzer import plan_loops
        # a 32-bar Break phrase still caps at 8 bars (Break/Build max)
        loops = plan_loops([_ph(0, PhraseLabel.DOWN, 32)], BAR_MS)
        assert loops[0].loop_beats == 8 * 4

    def test_exact_four_bars(self):
        from autocue.analyzer import plan_loops
        loops = plan_loops([_ph(0, PhraseLabel.INTRO, 4)], BAR_MS)
        assert loops[0].loop_beats == 4 * 4

    def test_six_bar_phrase_rounds_to_four(self):
        from autocue.analyzer import plan_loops
        loops = plan_loops([_ph(0, PhraseLabel.INTRO, 6)], BAR_MS)
        assert loops[0].loop_beats == 4 * 4


class TestPlanLoopsGuards:
    def test_phrase_shorter_than_four_bars_skipped(self):
        from autocue.analyzer import plan_loops
        assert plan_loops([_ph(0, PhraseLabel.INTRO, 3)], BAR_MS) == []
        assert plan_loops([_ph(0, PhraseLabel.INTRO, 0)], BAR_MS) == []

    def test_zero_or_negative_bar_ms_skips_all(self):
        from autocue.analyzer import plan_loops
        phrases = [_ph(0, PhraseLabel.INTRO, 16)]
        assert plan_loops(phrases, 0) == []       # no beat grid / BPM=0
        assert plan_loops(phrases, -5) == []

    def test_clamp_end_before_track_end_shrinks(self):
        from autocue.analyzer import plan_loops
        # Intro at 0 with 16 bars, but track ends at 20_000 ms.
        # 16 bars * 2000 = 32_000 overruns; 8 bars = 16_000 fits.
        loops = plan_loops([_ph(0, PhraseLabel.INTRO, 16)], BAR_MS, total_ms=20_000)
        assert loops[0].loop_beats == 8 * 4
        assert loops[0].loop_end_ms <= 20_000

    def test_clamp_skips_when_even_four_bars_overruns(self):
        from autocue.analyzer import plan_loops
        # 4 bars * 2000 = 8000; track ends at 5000 -> cannot fit -> skip.
        loops = plan_loops([_ph(0, PhraseLabel.OUTRO, 16)], BAR_MS, total_ms=5000)
        assert loops == []


class TestPlanLoopsPriorityAndCap:
    def test_one_loop_per_section(self):
        from autocue.analyzer import plan_loops
        # Two Break phrases -> only ONE "Break" loop (first qualifying).
        phrases = [
            _ph(100_000, PhraseLabel.DOWN, 8),
            _ph(200_000, PhraseLabel.DOWN, 8),
        ]
        loops = plan_loops(phrases, BAR_MS)
        assert [c.name for c in loops] == ["Break"]
        assert loops[0].position_ms == 100_000  # earliest

    def test_cap_three_by_default_four_with_build(self):
        from autocue.analyzer import plan_loops
        phrases = [
            _ph(0, PhraseLabel.INTRO, 16),
            _ph(60_000, PhraseLabel.UP, 8),
            _ph(120_000, PhraseLabel.DOWN, 8),
            _ph(360_000, PhraseLabel.OUTRO, 16),
        ]
        assert len(plan_loops(phrases, BAR_MS)) == 3          # Intro/Outro/Break
        assert len(plan_loops(phrases, BAR_MS, include_build=True)) == 4

    def test_returned_sorted_by_position(self):
        from autocue.analyzer import plan_loops
        phrases = [
            _ph(360_000, PhraseLabel.OUTRO, 16),
            _ph(0, PhraseLabel.INTRO, 16),
            _ph(120_000, PhraseLabel.DOWN, 8),
        ]
        loops = plan_loops(phrases, BAR_MS)
        assert [c.position_ms for c in loops] == [0, 120_000, 360_000]


class TestPlanLoopsCuePointShape:
    def test_loops_are_memory_loop_cuepoints(self):
        from autocue.analyzer import plan_loops
        loops = plan_loops([_ph(4000, PhraseLabel.OUTRO, 16)], BAR_MS)
        cue = loops[0]
        assert isinstance(cue, CuePoint)
        assert cue.is_loop is True
        assert cue.slot == -1            # memory loop (Kind=0), no hot-slot contention
        assert cue.name == "Outro"
        assert cue.label is PhraseLabel.OUTRO
        assert cue.position_ms == 4000
        assert cue.loop_end_ms == int(round(4000 + 16 * BAR_MS))
        assert cue.loop_beats == 16 * 4


# ---------------------------------------------------------------------------
# Unit 3 — Serato LOOP entry (write / decode / preserve), crew/researcher.md §1
# ---------------------------------------------------------------------------

def _loop_cue(pos=10_000, end=18_000, name="Outro", label=PhraseLabel.OUTRO, slot=-1):
    return CuePoint(
        position_ms=pos, label=label, slot=slot, name=name,
        loop_end_ms=end, loop_beats=(end - pos) // 500,
    )


class TestSeratoLoopWrite:
    def test_loop_entry_is_emitted(self):
        from autocue.serato_writer import build_markers2, parse_markers2, wrap_outer
        payload = build_markers2([_loop_cue()])
        assert b"LOOP\x00" in payload
        entries = parse_markers2(wrap_outer(payload))
        loops = [e for e in entries if e["type"] == "LOOP"]
        assert len(loops) == 1

    def test_memory_loop_slot_minus1_still_emitted(self):
        # Unlike a memory CUE (slot<0 dropped), a memory LOOP IS written —
        # loops carry their own Serato loop-slot index, not the cue slot.
        from autocue.serato_writer import build_markers2
        payload = build_markers2([_loop_cue(slot=-1)])
        assert b"LOOP\x00" in payload

    def test_loop_byte_layout(self):
        # Fixed portion = 20 bytes; name at offset 0x14 (researcher §1).
        from autocue.serato_writer import build_markers2
        payload = build_markers2([_loop_cue(pos=10_000, end=18_000, name="Outro")])
        idx = payload.index(b"LOOP\x00")
        length = int.from_bytes(payload[idx + 5:idx + 9], "big")
        data = payload[idx + 9:idx + 9 + length]
        assert data[0] == 0x00                                  # reserved
        assert data[1] == 0x00                                  # index (first loop)
        assert int.from_bytes(data[2:6], "big") == 10_000       # start ms, u32 BE
        assert int.from_bytes(data[6:10], "big") == 18_000      # end ms, u32 BE
        assert data[10:14] == b"\xff\xff\xff\xff"               # field5 reserved (opt-b)
        assert data[19] == 0x00                                 # locked = unlocked
        assert data[20:].split(b"\x00", 1)[0] == b"Outro"       # name at 0x14

    def test_roundtrip_fields(self):
        from autocue.serato_writer import build_markers2, parse_markers2, wrap_outer
        loop = _loop_cue(pos=12_345, end=54_321, name="Break", label=PhraseLabel.DOWN)
        entries = parse_markers2(wrap_outer(build_markers2([loop])))
        e = next(x for x in entries if x["type"] == "LOOP")
        assert e["start_ms"] == 12_345
        assert e["end_ms"] == 54_321
        assert e["name"] == "Break"
        assert e["index"] == 0
        assert e["locked"] is False

    def test_loop_index_independent_of_cue_slots(self):
        from autocue.serato_writer import build_markers2, parse_markers2, wrap_outer
        cues = [
            CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0, name="A"),
            CuePoint(position_ms=2000, label=PhraseLabel.OUTRO, slot=3, name="D"),
            _loop_cue(pos=5000, end=13_000, name="Intro", label=PhraseLabel.INTRO),
            _loop_cue(pos=90_000, end=98_000, name="Outro", label=PhraseLabel.OUTRO),
        ]
        entries = parse_markers2(wrap_outer(build_markers2(cues)))
        cue_idx = [e["index"] for e in entries if e["type"] == "CUE"]
        loop_idx = [e["index"] for e in entries if e["type"] == "LOOP"]
        assert cue_idx == [0, 3]        # cue slots preserved
        assert loop_idx == [0, 1]       # loops get their own 0-based sequence

    def test_non_loop_cue_still_serialized_as_cue(self):
        # Regression: a plain cue with no loop end is still a CUE entry.
        from autocue.serato_writer import build_markers2, parse_markers2, wrap_outer
        cue = CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0, name="Intro")
        entries = parse_markers2(wrap_outer(build_markers2([cue])))
        assert [e["type"] for e in entries] == ["CUE"]


class TestSeratoLoopDecode:
    def test_parse_decodes_loop_previously_dropped(self):
        # Before this unit parse_markers2 left LOOP opaque ({"type":"LOOP"}).
        from autocue.serato_writer import build_markers2, parse_markers2, wrap_outer
        entries = parse_markers2(wrap_outer(build_markers2([_loop_cue()])))
        e = next(x for x in entries if x["type"] == "LOOP")
        assert "start_ms" in e and "end_ms" in e and "name" in e

    def test_entries_carry_raw_framed_bytes(self):
        from autocue.serato_writer import build_markers2, parse_markers2, wrap_outer
        payload = build_markers2([_loop_cue()])
        entries = parse_markers2(wrap_outer(payload))
        e = next(x for x in entries if x["type"] == "LOOP")
        assert e["raw"].startswith(b"LOOP\x00")
        assert e["raw"] in payload         # raw is the exact framed slice


class TestSeratoLoopPreserve:
    def test_byte_for_byte_preserve_via_build(self):
        # F1: a foreign LOOP entry re-emitted verbatim survives a rebuild.
        from autocue.serato_writer import build_markers2, parse_markers2, wrap_outer
        original = build_markers2([_loop_cue(pos=7000, end=15_000, name="DJLoop")])
        raw = next(
            e["raw"] for e in parse_markers2(wrap_outer(original)) if e["type"] == "LOOP"
        )
        # Rebuild with NO loop cues but preserving the foreign raw entry.
        rebuilt = build_markers2([], preserve=[raw])
        assert raw in rebuilt                    # byte-for-byte identical
        entries = parse_markers2(wrap_outer(rebuilt))
        e = next(x for x in entries if x["type"] == "LOOP")
        assert e["name"] == "DJLoop" and e["start_ms"] == 7000 and e["end_ms"] == 15_000

    def test_existing_serato_loop_survives_file_rewrite(self, tmp_path):
        # F1 end-to-end: a DJ's saved Serato loop must NOT be wiped when we
        # rewrite the Markers2 tag with only cues.
        import pytest
        pytest.importorskip("mutagen")
        from autocue.serato_writer import parse_markers2, write_serato_tags, _read_existing, wrap_outer

        mp3 = tmp_path / "t.mp3"
        mp3.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 413)
        # 1) DJ made a loop in Serato (write it as the file's existing tag).
        write_serato_tags(mp3, [_loop_cue(pos=3000, end=11_000, name="MyLoop")])
        # 2) AutoCue rewrites with only a hot cue (no loop cues passed).
        cue = CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0, name="Intro")
        write_serato_tags(mp3, [cue])
        # 3) The DJ's loop must still be present.
        from autocue.serato_writer import GEOB_V2
        raw = _read_existing(mp3)[GEOB_V2]
        entries = parse_markers2(raw)
        names = {e.get("name") for e in entries}
        assert "MyLoop" in names               # loop preserved
        assert "Intro" in names                # new cue also written


# ---------------------------------------------------------------------------
# Unit 4 — read_hot_cues carries OutMsec -> loop_end_ms (§4, F6 mirror-first)
# ---------------------------------------------------------------------------

def _cue_row(kind=1, in_ms=1000, out_ms=-1, beat_loop=0, comment="Intro", color=5):
    from types import SimpleNamespace
    return SimpleNamespace(
        Kind=kind, InMsec=in_ms, OutMsec=out_ms, BeatLoopSize=beat_loop,
        Comment=comment, ColorTableIndex=color,
    )


def _db_with_rows(rows):
    from unittest.mock import MagicMock
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = rows
    return db


class TestReadHotCuesOutMsec:
    def test_point_cue_out_msec_sentinel_stays_point(self):
        # OutMsec=-1 sentinel (write_cues_to_db's non-loop value) -> point cue.
        from autocue.db_writer import read_hot_cues
        from types import SimpleNamespace
        cues = read_hot_cues(SimpleNamespace(ID=1), _db_with_rows([_cue_row(out_ms=-1)]))
        assert len(cues) == 1
        assert cues[0].loop_end_ms is None
        assert cues[0].is_loop is False

    def test_existing_loop_carries_out_msec(self):
        # A hot-slot loop (OutMsec > InMsec) mirrors back as a loop CuePoint.
        from autocue.db_writer import read_hot_cues
        from types import SimpleNamespace
        row = _cue_row(kind=1, in_ms=5000, out_ms=13_000, beat_loop=16, comment="Outro")
        cues = read_hot_cues(SimpleNamespace(ID=1), _db_with_rows([row]))
        assert cues[0].is_loop is True
        assert cues[0].position_ms == 5000
        assert cues[0].loop_end_ms == 13_000
        assert cues[0].loop_beats == 16          # BeatLoopSize carried through
        assert cues[0].name == "Outro"

    def test_out_msec_not_greater_than_in_is_point(self):
        from autocue.db_writer import read_hot_cues
        from types import SimpleNamespace
        # OutMsec == InMsec or < InMsec -> not a valid loop region.
        for out in (0, 1000, 500):
            cues = read_hot_cues(
                SimpleNamespace(ID=1), _db_with_rows([_cue_row(in_ms=1000, out_ms=out)])
            )
            assert cues[0].loop_end_ms is None, f"out={out}"

    def test_out_msec_none_is_point(self):
        from autocue.db_writer import read_hot_cues
        from types import SimpleNamespace
        cues = read_hot_cues(SimpleNamespace(ID=1), _db_with_rows([_cue_row(out_ms=None)]))
        assert cues[0].loop_end_ms is None

    def test_loop_with_zero_beat_loop_size_has_none_beats(self):
        from autocue.db_writer import read_hot_cues
        from types import SimpleNamespace
        row = _cue_row(in_ms=1000, out_ms=9000, beat_loop=0)
        cues = read_hot_cues(SimpleNamespace(ID=1), _db_with_rows([row]))
        assert cues[0].is_loop is True
        assert cues[0].loop_beats is None        # unknown length, but still a loop


# ---------------------------------------------------------------------------
# Unit 5 — CLI wiring: the --loops flag + loop-merge helper
# ---------------------------------------------------------------------------

class TestLoopsFlag:
    def test_loops_flag_parses_true(self):
        from autocue.cli import _build_parser
        args = _build_parser().parse_args(["--track", "Song", "--serato", "--loops"])
        assert args.loops is True

    def test_loops_flag_defaults_false(self):
        from autocue.cli import _build_parser
        args = _build_parser().parse_args(["--track", "Song", "--serato"])
        assert args.loops is False


class TestMergeLoops:
    def test_appends_non_colliding_loops(self):
        from autocue.cli import _merge_loops
        cues = [CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0, name="Intro")]
        loops = [_loop_cue(pos=30_000, end=38_000, name="Outro")]
        merged = _merge_loops(cues, loops)
        assert len(merged) == 2
        assert merged[-1].is_loop and merged[-1].name == "Outro"

    def test_drops_loop_colliding_with_existing_start(self):
        from autocue.cli import _merge_loops
        # A generated loop at the same start as an existing (mirrored) entry
        # is dropped — the existing one wins (mirror-first).
        cues = [CuePoint(position_ms=5000, label=PhraseLabel.OUTRO, slot=1, name="Kept")]
        loops = [_loop_cue(pos=5000, end=13_000, name="Generated")]
        merged = _merge_loops(cues, loops)
        assert len(merged) == 1
        assert merged[0].name == "Kept"

    def test_empty_loops_returns_cues_unchanged(self):
        from autocue.cli import _merge_loops
        cues = [CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0)]
        assert _merge_loops(cues, []) == cues


# ---------------------------------------------------------------------------
# Increment 2 — Rekordbox XML loop marks (writer.py §5, researcher §2)
# ---------------------------------------------------------------------------
# Rekordbox XML POSITION_MARK Type is NUMERIC: 0=cue, 4=loop. Start/End are in
# SECONDS. add_mark(Type="loop", End=<sec>) serializes to Type="4" + End attr.

def _xml_content(title="Test Track", artist="Test Artist", folder="/Music", filename="t.mp3"):
    from unittest.mock import MagicMock
    c = MagicMock()
    c.Title, c.ArtistName = title, artist
    c.FolderPath, c.FileNameL, c.FileNameS = folder, filename, filename
    return c


def _marks(xml_path):
    import xml.etree.ElementTree as ET
    root = ET.parse(str(xml_path)).getroot()
    return [e.attrib for e in root.iter("POSITION_MARK")]


class TestXmlLoopMark:
    def test_loop_cue_yields_loop_position_mark(self, tmp_path):
        from autocue.writer import write_xml
        loop = CuePoint(
            position_ms=10_000, label=PhraseLabel.OUTRO, slot=-1, name="Outro",
            loop_end_ms=18_000, loop_beats=32,
        )
        out = write_xml([(_xml_content(), [loop])], tmp_path / "loop.xml")
        marks = _marks(out)
        assert len(marks) == 1
        m = marks[0]
        assert m["Type"] == "4"                 # 4 = loop (not "0" = cue)
        assert m["Name"] == "Outro"
        assert float(m["Start"]) == 10.0        # position_ms -> seconds
        assert float(m["End"]) == 18.0          # loop_end_ms  -> seconds
        assert m["Num"] == "-1"                 # memory loop

    def test_end_is_seconds_not_milliseconds(self, tmp_path):
        # Guard the unit bug: End must be 18.0 s, never 18000 (ms).
        from autocue.writer import write_xml
        loop = CuePoint(
            position_ms=12_500, label=PhraseLabel.DOWN, slot=-1, name="Break",
            loop_end_ms=20_500,
        )
        out = write_xml([(_xml_content(), [loop])], tmp_path / "break.xml")
        m = _marks(out)[0]
        assert float(m["End"]) == 20.5
        assert float(m["Start"]) == 12.5
        assert float(m["End"]) < 1000           # sanity: seconds, not ms

    def test_non_loop_cue_unchanged_no_end(self, tmp_path):
        # Regression: a plain cue is still Type="0" (cue) with NO End attribute.
        from autocue.writer import write_xml
        cue = CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0, name="Intro")
        out = write_xml([(_xml_content(), [cue])], tmp_path / "cue.xml")
        m = _marks(out)[0]
        assert m["Type"] == "0"                 # cue, not loop
        assert "End" not in m                   # no End on a point cue
        assert float(m["Start"]) == 1.0
        assert m["Num"] == "0"

    def test_mixed_cue_and_loop_in_one_track(self, tmp_path):
        from autocue.writer import write_xml
        cue = CuePoint(position_ms=2000, label=PhraseLabel.INTRO, slot=0, name="Intro")
        loop = CuePoint(
            position_ms=90_000, label=PhraseLabel.OUTRO, slot=-1, name="Outro",
            loop_end_ms=98_000,
        )
        out = write_xml([(_xml_content(), [cue, loop])], tmp_path / "mix.xml")
        marks = _marks(out)
        by_type = {m["Type"]: m for m in marks}
        assert set(by_type) == {"0", "4"}
        assert "End" not in by_type["0"]        # cue: no End
        assert float(by_type["4"]["End"]) == 98.0
        assert by_type["4"]["Name"] == "Outro"

    def test_loop_name_falls_back_to_label(self, tmp_path):
        # Name empty -> falls back to label.value (same rule as cues).
        from autocue.writer import write_xml
        loop = CuePoint(
            position_ms=0, label=PhraseLabel.OUTRO, slot=-1, name="",
            loop_end_ms=8000,
        )
        out = write_xml([(_xml_content(), [loop])], tmp_path / "fallback.xml")
        m = _marks(out)[0]
        assert m["Name"] == PhraseLabel.OUTRO.value


# ===========================================================================
# P4-FIX — consolidated VERIFY-loop fixes (auditor #1/#2/N1/N2 · verifier P-10/C-3)
# ===========================================================================

def _fake_anlz(monkeypatch, phrases, *, mood=1, n_beats=400, beat_ms=500):
    """Patch analyze_loops' ANLZ source with a synthetic PSSI+PQTZ.

    ``phrases`` = list of ``(beat, kind)`` (1-indexed downbeat + PSSI kind).
    mood=1 (High) maps kind 1→INTRO, 2→UP(Build), 3→DOWN(Break), 5→CHORUS, 6→OUTRO.
    beat_ms=500 ⇒ bar_ms=2000 (120 BPM). Returns nothing; sets up the patch.
    """
    from types import SimpleNamespace
    import autocue.analyzer as az
    beats = [SimpleNamespace(time=i * beat_ms, beat=i + 1) for i in range(n_beats)]
    pqtz = SimpleNamespace(entries=beats)
    pssi = SimpleNamespace(
        entries=[SimpleNamespace(beat=b, kind=k) for b, k in phrases], mood=mood,
    )
    monkeypatch.setattr(az, "_get_pssi_and_pqtz", lambda *a, **k: (pssi, pqtz))


class TestAnalyzeLoopsTerminalPhrase:
    """Auditor #1 — the Outro is (almost always) the LAST phrase, so its bar
    length must come from the track end, else it computes to 0 bars and is
    silently dropped. This path had ZERO coverage — that is why the bug shipped."""

    def test_terminal_outro_produces_outro_loop(self, monkeypatch):
        from types import SimpleNamespace
        from autocue.analyzer import analyze_loops
        # Intro@0, Chorus@16s, Outro@32s (LAST). 50s track.
        _fake_anlz(monkeypatch, [(1, 1), (33, 5), (65, 6)])
        content = SimpleNamespace(Length=50)  # seconds -> total_ms 50_000
        loops = analyze_loops(content, object())
        names = [c.name for c in loops]
        assert "Outro" in names, "terminal Outro must produce a loop (bar length from track end)"
        outro = next(c for c in loops if c.name == "Outro")
        assert outro.is_loop and outro.slot == -1
        assert 32_000 < outro.loop_end_ms <= 50_000   # bounded by track end, no run into silence

    def test_terminal_phrase_without_duration_still_safe(self, monkeypatch):
        # No Length -> total_ms None -> terminal phrase can't be measured -> no
        # Outro, but MUST NOT crash (graceful).
        from types import SimpleNamespace
        from autocue.analyzer import analyze_loops
        _fake_anlz(monkeypatch, [(1, 1), (65, 6)])
        loops = analyze_loops(SimpleNamespace(), object())  # no Length attr
        assert isinstance(loops, list)  # Intro may qualify off the gap to Outro; never raises
