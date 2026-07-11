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
    def test_all_four_labels_eligible_by_default(self):
        # R-NC8: Intro/Outro/Break AND Build are all eligible by default
        # (Build lowest priority, opt-flag dropped). Never Verse/Drop/Bridge.
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
        assert names == {"Intro", "Outro", "Break", "Build"}  # never Verse/Drop/Bridge

    def test_never_loops_verse_chorus_bridge(self):
        from autocue.analyzer import plan_loops
        phrases = [
            _ph(0, PhraseLabel.VERSE, 16),
            _ph(60_000, PhraseLabel.CHORUS, 16),
            _ph(120_000, PhraseLabel.BRIDGE, 16),
        ]
        assert plan_loops(phrases, BAR_MS) == []

    def test_build_eligible_by_default_rnc8(self):
        # R-NC8: a lone Build(UP) phrase yields a "Build" loop with NO flag.
        from autocue.analyzer import plan_loops
        loops = plan_loops([_ph(0, PhraseLabel.UP, 8)], BAR_MS)
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

    def test_cap_four_with_build_default(self):
        # R-NC8: cap = 4, Build default-on → all four categories fill by default.
        from autocue.analyzer import plan_loops
        phrases = [
            _ph(0, PhraseLabel.INTRO, 16),
            _ph(60_000, PhraseLabel.UP, 8),
            _ph(120_000, PhraseLabel.DOWN, 8),
            _ph(360_000, PhraseLabel.OUTRO, 16),
        ]
        loops = plan_loops(phrases, BAR_MS)
        assert len(loops) == 4
        assert {c.name for c in loops} == {"Intro", "Outro", "Break", "Build"}

    def test_never_exceeds_cap_of_four(self):
        # Even with extra eligible phrases, one-per-section keeps it at ≤4.
        from autocue.analyzer import plan_loops
        phrases = [
            _ph(0, PhraseLabel.INTRO, 16),
            _ph(40_000, PhraseLabel.DOWN, 8),
            _ph(80_000, PhraseLabel.DOWN, 8),
            _ph(120_000, PhraseLabel.UP, 8),
            _ph(160_000, PhraseLabel.UP, 8),
            _ph(360_000, PhraseLabel.OUTRO, 16),
        ]
        assert len(plan_loops(phrases, BAR_MS)) <= 4

    def test_build_surfaces_when_few_higher_priority_qualify(self):
        # Build (lowest priority) still appears when < higher-priority qualify.
        from autocue.analyzer import plan_loops
        loops = plan_loops(
            [_ph(0, PhraseLabel.INTRO, 16), _ph(60_000, PhraseLabel.UP, 8)], BAR_MS,
        )
        assert {c.name for c in loops} == {"Intro", "Build"}

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

    def test_loop_coexists_with_point_cue_at_same_position(self):
        from autocue.cli import _merge_loops
        # ROOT-CAUSE FIX: a memory loop (Num=-1) and a hot/point cue (Num 0-7)
        # are DIFFERENT Rekordbox objects — they coexist even at the SAME
        # downbeat. Generated phrase cues and generated loops share downbeats,
        # so dropping on point-cue collision wiped every loop (XMLWIRE bug).
        cues = [CuePoint(position_ms=5000, label=PhraseLabel.OUTRO, slot=1, name="Cue")]
        loops = [_loop_cue(pos=5000, end=13_000, name="Outro")]
        merged = _merge_loops(cues, loops)
        assert len(merged) == 2
        assert {c.name for c in merged} == {"Cue", "Outro"}

    def test_drops_loop_colliding_with_existing_loop(self):
        from autocue.cli import _merge_loops
        # Mirror-first still holds: a generated loop at the same start as an
        # existing LOOP (a DJ's saved loop) is dropped — the DJ's loop wins.
        existing_loop = _loop_cue(pos=5000, end=20_000, name="DJLoop")
        loops = [_loop_cue(pos=5000, end=13_000, name="Generated")]
        merged = _merge_loops([existing_loop], loops)
        assert len(merged) == 1
        assert merged[0].name == "DJLoop"

    def test_two_generated_loops_same_start_deduped(self):
        from autocue.cli import _merge_loops
        merged = _merge_loops([], [
            _loop_cue(pos=5000, end=13_000, name="A"),
            _loop_cue(pos=5000, end=9000, name="B"),
        ])
        assert len(merged) == 1  # a newly-added loop start blocks a second at that spot

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


class TestAnalyzeLoopsBreadcrumb:
    """P-10 (verifier RED, DESIGN silent-failure lens) — a genuine ANLZ/grid
    failure must be DISTINGUISHABLE from the legit 'no eligible phrase' (silent)."""

    def test_missing_anlz_logs_breadcrumb(self, monkeypatch, caplog):
        import logging
        import autocue.analyzer as az
        monkeypatch.setattr(az, "_get_pssi_and_pqtz", lambda *a, **k: (None, None))
        with caplog.at_level(logging.WARNING):
            out = az.analyze_loops(object(), object())
        assert out == []
        assert any("grid" in r.getMessage().lower() for r in caplog.records), \
            "absent PSSI/PQTZ (a real parse/data failure) must log a breadcrumb"

    def test_no_eligible_phrase_is_silent(self, monkeypatch, caplog):
        # Valid grid + only VERSE phrases -> [] WITHOUT a warning (not a failure).
        import logging
        from types import SimpleNamespace
        _fake_anlz(monkeypatch, [(1, 2)], mood=1)  # kind 2 @ mood 1 -> UP... use VERSE
        import autocue.analyzer as az
        # mood 3 kind 2 -> VERSE (never looped); usable grid.
        _fake_anlz(monkeypatch, [(1, 2)], mood=3)
        with caplog.at_level(logging.WARNING):
            out = az.analyze_loops(SimpleNamespace(Length=200), object())
        assert out == []
        assert not any("grid" in r.getMessage().lower() for r in caplog.records), \
            "a valid grid with no eligible phrase must stay silent (no false alarm)"


def _cli_stub(monkeypatch, *, generated, loops, spy):
    """Run main() DB-free; spy records the cue lists handed to write_serato."""
    import sys
    from types import SimpleNamespace
    import autocue.cli as cli
    import autocue.db_writer as db_writer
    import autocue.analyzer as analyzer
    import autocue.serato_writer as serato_writer
    from autocue.serato_writer import SeratoSummary

    content = SimpleNamespace(
        Title="Fixture", ArtistName="Artist", FolderPath="/Music",
        FileNameL="fixture.mp3", FileNameS="fixture.mp3", ID=1, Length=200,
    )
    monkeypatch.setattr(cli, "MasterDatabase", lambda *a, **k: object())
    monkeypatch.setattr(cli, "analyze_by_title", lambda *a, **k: (content, None))
    monkeypatch.setattr(cli, "generate_cues_for_track", lambda *a, **k: (list(generated), "phrase"))
    monkeypatch.setattr(cli, "_serato_running", lambda: False)
    monkeypatch.setattr(db_writer, "read_hot_cues", lambda *a, **k: [])
    monkeypatch.setattr(analyzer, "analyze_loops", lambda *a, **k: list(loops), raising=False)
    monkeypatch.setattr(cli, "analyze_loops", lambda *a, **k: list(loops), raising=False)

    def _spy(pairs, **kw):
        spy.extend(list(cs) for _, cs in pairs)
        return SeratoSummary(written=len(pairs))

    monkeypatch.setattr(serato_writer, "write_serato", _spy)
    return content


class TestDryRunLoopPreview:
    """C-3 (verifier) — --loops --serato --dry-run must PREVIEW the loop
    placements (they were computed only inside the real write branch, after the
    dry-run early return) AND still write nothing."""

    def test_dry_run_lists_loops_and_writes_nothing(self, monkeypatch, capsys):
        import sys
        from autocue.cli import main
        spy = []
        loop = _loop_cue(pos=10_000, end=18_000, name="Outro")
        _cli_stub(monkeypatch, generated=[
            CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0, name="Intro")
        ], loops=[loop], spy=spy)
        monkeypatch.setattr(sys, "argv", ["autocue", "--track", "x", "--loops", "--serato", "--dry-run"])
        main()
        out = capsys.readouterr().out
        assert "Dry run — no files written." in out
        assert "Outro" in out and "loop" in out.lower()   # loop placement previewed
        assert spy == [], "dry-run must not call write_serato"

    def test_dry_run_without_loops_flag_no_loop_preview(self, monkeypatch, capsys):
        import sys
        from autocue.cli import main
        spy = []
        _cli_stub(monkeypatch, generated=[
            CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0, name="Intro")
        ], loops=[_loop_cue(name="Outro")], spy=spy)
        monkeypatch.setattr(sys, "argv", ["autocue", "--track", "x", "--serato", "--dry-run"])
        main()
        out = capsys.readouterr().out
        assert "Dry run — no files written." in out
        assert "loop [" not in out.lower()   # no loop preview without --loops


class TestCliXmlLoopWiring:
    """XMLWIRE — the end-to-end CLI → XML loop path had ZERO coverage (that is
    why `autocue --loops` shipped writing 0 loop marks). Drives real write_xml."""

    def _marks(self, path):
        import xml.etree.ElementTree as ET
        return [e.attrib for e in ET.parse(str(path)).getroot().iter("POSITION_MARK")]

    def test_xml_path_writes_loop_marks_with_loops(self, monkeypatch, tmp_path):
        import sys
        from autocue.cli import main
        out = tmp_path / "out.xml"
        loop = _loop_cue(pos=10_000, end=18_000, name="Outro")
        _cli_stub(monkeypatch, generated=[
            CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0, name="Intro"),
        ], loops=[loop], spy=[])
        monkeypatch.setattr(sys, "argv", ["autocue", "--track", "x", "--loops", "--output", str(out)])
        main()
        marks = self._marks(out)
        loop_marks = [m for m in marks if m.get("Type") == "4"]      # 4 = loop
        assert len(loop_marks) == 1, f"expected 1 loop POSITION_MARK, got {marks}"
        assert loop_marks[0]["Num"] == "-1"                          # memory loop
        assert float(loop_marks[0]["End"]) == 18.0                   # seconds
        assert loop_marks[0]["Name"] == "Outro"

    def test_xml_path_no_loop_marks_without_flag(self, monkeypatch, tmp_path):
        import sys
        from autocue.cli import main
        out = tmp_path / "out.xml"
        _cli_stub(monkeypatch, generated=[
            CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0, name="Intro"),
        ], loops=[_loop_cue(name="Outro")], spy=[])
        monkeypatch.setattr(sys, "argv", ["autocue", "--track", "x", "--output", str(out)])
        main()
        marks = self._marks(out)
        assert not any(m.get("Type") == "4" for m in marks)          # regression: no loops w/o --loops
        assert any(m.get("Type") == "0" for m in marks)              # the cue is still written

    def test_xml_loop_coexists_with_cue_at_same_downbeat(self, monkeypatch, tmp_path):
        # The real-world case: cue and loop share the phrase downbeat -> BOTH
        # must land in the XML (the bug dropped the loop).
        import sys
        from autocue.cli import main
        out = tmp_path / "out.xml"
        _cli_stub(monkeypatch, generated=[
            CuePoint(position_ms=10_000, label=PhraseLabel.OUTRO, slot=0, name="Outro"),
        ], loops=[_loop_cue(pos=10_000, end=18_000, name="Outro")], spy=[])
        monkeypatch.setattr(sys, "argv", ["autocue", "--track", "x", "--loops", "--output", str(out)])
        main()
        types = sorted(m.get("Type") for m in self._marks(out))
        assert "0" in types and "4" in types    # hot cue AND memory loop both present


class TestSeratoDecodeFailBreadcrumb:
    """N2 (auditor, conf 70) — if an existing v2 Markers2 tag can't be decoded,
    an --overwrite would silently drop the DJ's loops (preserve=[]). Warn."""

    def test_undecodable_v2_tag_warns(self, monkeypatch, caplog):
        import logging
        from pathlib import Path
        import autocue.serato_writer as sw
        # A v2 tag is present but decodes to zero entries (corrupt payload).
        monkeypatch.setattr(sw, "_read_existing", lambda p: {sw.GEOB_V2: b"\x01\x01!!!not-base64!!!"})
        with caplog.at_level(logging.WARNING):
            out = sw._existing_loop_entries(Path("x.mp3"))
        assert out == []
        assert any(
            "preserv" in r.getMessage().lower() or "decode" in r.getMessage().lower()
            for r in caplog.records
        ), "an undecodable existing v2 tag must warn (silent overwrite would lose DJ loops)"

    def test_valid_loop_tag_does_not_warn(self, monkeypatch, caplog):
        import logging
        from pathlib import Path
        import autocue.serato_writer as sw
        from autocue.serato_writer import build_markers2, wrap_outer
        payload = build_markers2([_loop_cue(pos=1000, end=9000, name="DJ")])
        monkeypatch.setattr(sw, "_read_existing", lambda p: {sw.GEOB_V2: wrap_outer(payload)})
        with caplog.at_level(logging.WARNING):
            out = sw._existing_loop_entries(Path("x.mp3"))
        assert len(out) == 1                       # the loop decoded fine
        assert not caplog.records, "a decodable tag must not warn"

    def test_no_existing_tag_is_silent(self, monkeypatch, caplog):
        import logging
        from pathlib import Path
        import autocue.serato_writer as sw
        monkeypatch.setattr(sw, "_read_existing", lambda p: {})
        with caplog.at_level(logging.WARNING):
            out = sw._existing_loop_entries(Path("x.mp3"))
        assert out == [] and not caplog.records    # no tag at all → nothing to warn about


class TestGeneratedLoopIndex:
    """N1 (auditor, conf 60) — index generated loops past max(existing index)+1,
    so a DJ loop in a non-contiguous high slot can't share an index."""

    def test_generated_index_past_max_existing_no_collision(self):
        from autocue.serato_writer import _loop_entry, build_markers2, parse_markers2, wrap_outer
        # DJ loops preserved at NON-contiguous indices 0 and 2 (len(preserve)=2).
        dj0 = _loop_entry(0, _loop_cue(pos=1000, end=9000, name="DJ0"))
        dj2 = _loop_entry(2, _loop_cue(pos=20_000, end=28_000, name="DJ2"))
        gen = _loop_cue(pos=50_000, end=58_000, name="Outro")
        entries = parse_markers2(wrap_outer(build_markers2([gen], preserve=[dj0, dj2])))
        idx = {e["name"]: e["index"] for e in entries if e["type"] == "LOOP"}
        assert idx["DJ0"] == 0 and idx["DJ2"] == 2      # preserved indices untouched
        assert idx["Outro"] == 3                        # past max(2)+1 (was len=2 → collided with DJ2)
        all_idx = [e["index"] for e in entries if e["type"] == "LOOP"]
        assert len(all_idx) == len(set(all_idx)), "loop indices must be unique"

    def test_no_preserve_indexes_from_zero(self):
        from autocue.serato_writer import build_markers2, parse_markers2, wrap_outer
        two = [_loop_cue(pos=1000, end=9000, name="A"), _loop_cue(pos=50_000, end=58_000, name="B")]
        entries = parse_markers2(wrap_outer(build_markers2(two)))
        assert sorted(e["index"] for e in entries if e["type"] == "LOOP") == [0, 1]


# ===========================================================================
# INCREMENT 3 — DB-DIRECT loop write (--write-db)
#
# 🚨 SAFETY IS THE FEATURE. These run against a SCRATCH in-memory SQLite with
# the real pyrekordbox schema — NEVER the live master.db.
# The load-bearing case is TestWriteLoopsNoClobber: write_loops_to_db must
# NEVER delete a Kind=0 row (memory CUES and memory LOOPS share Kind=0; the
# discriminator is OutMsec). write_cues_to_db is UNSAFE for loops on both
# branches and is deliberately NOT reused — pinned by the mirror-negative below.
# ===========================================================================

import pytest


@pytest.fixture
def real_db():
    """In-memory SQLite + the full pyrekordbox schema + a db shim.

    ⚠️ MUST stub ``generate_unused_id`` — a MagicMock db would otherwise
    silently insert ``ID=<MagicMock>`` and the row assertions would lie.
    """
    from unittest.mock import MagicMock
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from pyrekordbox.db6 import tables as t

    engine = create_engine("sqlite:///:memory:")

    # pyrekordbox's model marks these four NOT NULL with no default, but the REAL
    # master.db tolerates the SHIPPED write_cues_to_db insert, which omits them.
    # Relax ONLY those for the scratch DDL (then restore — no global side effect)
    # so the in-memory schema matches real-DB behaviour. Every column the writer
    # MUST set (Kind/InMsec/OutMsec/BeatLoopSize/…) stays NOT NULL, so omitting
    # one still fails loudly.
    _relaxed = [
        t.DjmdCue.__table__.columns[n]
        for n in ("InPointSeekInfo", "OutPointSeekInfo", "usn", "rb_local_usn")
    ]
    for c in _relaxed:
        c.nullable = True
    try:
        t.Base.metadata.create_all(engine)
    finally:
        for c in _relaxed:
            c.nullable = False

    session = sessionmaker(bind=engine)()

    db = MagicMock()
    db.session = session
    counter = {"n": 5000}

    def _gen(_model):
        counter["n"] += 1
        return counter["n"]

    db.generate_unused_id.side_effect = _gen

    yield db, session, t
    session.close()
    engine.dispose()


def _default_value(col):
    """A sensible value for a NOT NULL column (pyrekordbox has ~78 on DjmdContent).
    Mirrors tests/test_duplicates_integration.py's fill-by-SQL-type helper."""
    import datetime as _dt
    type_name = str(col.type).upper()
    if "DATETIME" in type_name or col.name in ("created_at", "updated_at"):
        return _dt.datetime.now(_dt.timezone.utc)
    if any(s in type_name for s in ("VARCHAR", "TEXT", "STRING")):
        return ""
    if any(s in type_name for s in ("FLOAT", "REAL", "DOUBLE")):
        return 0.0
    return 0


def _construct(model_cls, **overrides):
    row = model_cls()
    for c in model_cls.__table__.columns:
        if not c.nullable and c.name not in overrides:
            setattr(row, c.name, _default_value(c))
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


def _seed(session, t, cid="1"):
    """A track + 2 pre-existing DJ memory CUES (Kind=0, OutMsec=-1) + 1 hot cue."""
    session.add(_construct(t.DjmdContent, ID=cid, Title="Track", UUID="content-uuid"))
    session.add(_construct(
        t.DjmdCue, ID="900", ContentID=cid, UUID="mem-uuid-1", Kind=0,
        InMsec=5000, InFrame=750, OutMsec=-1, OutFrame=0, Comment="DJ Memory 1",
    ))
    session.add(_construct(
        t.DjmdCue, ID="901", ContentID=cid, UUID="mem-uuid-2", Kind=0,
        InMsec=60_000, InFrame=9000, OutMsec=-1, OutFrame=0, Comment="DJ Memory 2",
    ))
    session.add(_construct(
        t.DjmdCue, ID="902", ContentID=cid, UUID="hot-uuid-1", Kind=1,
        InMsec=1000, InFrame=150, OutMsec=-1, OutFrame=0, Comment="Intro",
    ))
    session.commit()
    return session.query(t.DjmdContent).filter(t.DjmdContent.ID == cid).first()


def _memory_rows(session, t, cid="1"):
    return (
        session.query(t.DjmdCue)
        .filter(t.DjmdCue.ContentID == cid, t.DjmdCue.Kind == 0)
        .order_by(t.DjmdCue.InMsec)
        .all()
    )


def _snapshot(row):
    return (row.ID, row.UUID, row.Kind, row.InMsec, row.OutMsec, row.Comment)


class TestWriteLoopsNoClobber:
    """★ THE LOAD-BEARING SAFETY CASE — a loop write must never delete Kind=0."""

    def test_existing_memory_cues_survive_byte_identical(self, real_db):
        from autocue.db_writer import write_loops_to_db
        db, session, t = real_db
        content = _seed(session, t)
        before = {r.ID: _snapshot(r) for r in _memory_rows(session, t)}
        assert len(before) == 2  # the DJ's 2 hand-placed memory cues

        loops = [
            _loop_cue(pos=10_000, end=18_000, name="Intro"),
            _loop_cue(pos=90_000, end=98_000, name="Outro"),
        ]
        n = write_loops_to_db(content, loops, db)
        assert n == 2

        after = {r.ID: _snapshot(r) for r in _memory_rows(session, t)}
        # (a) THE NO-CLOBBER ASSERTION — both originals still there, unchanged.
        for rid, snap in before.items():
            assert rid in after, f"memory cue {rid} was DELETED — clobber!"
            assert after[rid] == snap, f"memory cue {rid} was MUTATED"
        assert len(after) == 4  # 2 original cues + 2 new loops coexist

    def test_hot_cue_untouched(self, real_db):
        from autocue.db_writer import write_loops_to_db
        db, session, t = real_db
        content = _seed(session, t)
        write_loops_to_db(content, [_loop_cue(pos=10_000, end=18_000, name="Intro")], db)
        hot = session.query(t.DjmdCue).filter(
            t.DjmdCue.ContentID == "1", t.DjmdCue.Kind == 1).all()
        assert len(hot) == 1 and hot[0].Comment == "Intro"


class TestWriteLoopsColumns:
    """(b) The new loop rows carry the confirmed columns + UNITS."""

    def test_loop_row_columns_and_units(self, real_db):
        from autocue.db_writer import write_loops_to_db
        db, session, t = real_db
        content = _seed(session, t)
        loop = CuePoint(
            position_ms=10_000, label=PhraseLabel.OUTRO, slot=-1, name="Outro",
            loop_end_ms=18_000, loop_beats=32,   # 8 bars * 4 = 32 BEATS
        )
        write_loops_to_db(content, [loop], db)
        row = next(r for r in _memory_rows(session, t) if r.InMsec == 10_000)
        assert row.Kind == 0                       # memory (slot=-1)
        assert row.InMsec == 10_000                # ms
        assert row.InFrame == round(10_000 * 150 / 1000)     # 1500 — 150 sub-frames/s
        assert row.OutMsec == 18_000               # ms (loop end)
        assert row.OutFrame == round(18_000 * 150 / 1000)    # 2700
        assert row.OutMpegFrame == 0 and row.OutMpegAbs == 0
        assert row.ActiveLoop == 0                 # saved but UNARMED
        assert row.BeatLoopSize == 32              # BEATS = bars*4
        assert row.Comment == "Outro"              # the loop name
        assert row.ContentID == "1"
        assert str(row.ID).isdigit()               # generate_unused_id stubbed, not a MagicMock
        assert row.UUID and row.UUID != "mem-uuid-1"

    def test_only_memory_loops_written(self, real_db):
        # Hot-slot loops (slot>=0) and plain point cues are NOT this writer's job.
        from autocue.db_writer import write_loops_to_db
        db, session, t = real_db
        content = _seed(session, t)
        cues = [
            CuePoint(position_ms=2000, label=PhraseLabel.INTRO, slot=0, name="HotCue"),
            CuePoint(position_ms=3000, label=PhraseLabel.DOWN, slot=2, name="HotLoop",
                     loop_end_ms=9000, loop_beats=16),          # loop but slot>=0
            _loop_cue(pos=10_000, end=18_000, name="MemLoop"),  # slot=-1 → the only one
        ]
        assert write_loops_to_db(content, cues, db) == 1
        added = [r for r in _memory_rows(session, t) if r.InMsec == 10_000]
        assert len(added) == 1 and added[0].Comment == "MemLoop"


class TestWriteLoopsIdempotentAndCollision:
    def test_rerun_adds_zero_rows(self, real_db):
        # (c) idempotent — the collision-skip makes a re-run a no-op.
        from autocue.db_writer import write_loops_to_db
        db, session, t = real_db
        content = _seed(session, t)
        loops = [_loop_cue(pos=10_000, end=18_000, name="Intro")]
        assert write_loops_to_db(content, loops, db) == 1
        n_after_first = len(_memory_rows(session, t))
        assert write_loops_to_db(content, loops, db) == 0   # re-run: nothing new
        assert len(_memory_rows(session, t)) == n_after_first

    def test_loop_colliding_with_existing_memory_cue_skipped_and_logged(self, real_db, caplog):
        # (d) mirror-first: a DJ memory cue already starts at 5000 → skip + breadcrumb.
        import logging
        from autocue.db_writer import write_loops_to_db
        db, session, t = real_db
        content = _seed(session, t)
        with caplog.at_level(logging.INFO):
            n = write_loops_to_db(content, [_loop_cue(pos=5000, end=13_000, name="Clash")], db)
        assert n == 0
        assert not any(r.Comment == "Clash" for r in _memory_rows(session, t))
        assert any("5000" in r.getMessage() or "Clash" in r.getMessage()
                   for r in caplog.records), "a skipped-for-collision loop must log a breadcrumb"

    def test_dry_run_writes_nothing(self, real_db):
        from autocue.db_writer import write_loops_to_db
        db, session, t = real_db
        content = _seed(session, t)
        before = len(_memory_rows(session, t))
        assert write_loops_to_db(
            content, [_loop_cue(pos=10_000, end=18_000)], db, dry_run=True) == 0
        assert len(_memory_rows(session, t)) == before

    def test_no_loops_is_a_noop(self, real_db):
        from autocue.db_writer import write_loops_to_db
        db, session, t = real_db
        content = _seed(session, t)
        before = len(_memory_rows(session, t))
        assert write_loops_to_db(content, [], db) == 0
        assert len(_memory_rows(session, t)) == before


class TestWriteCuesToDbSparesMemoryLoops:
    """FIX-3 (auditor IMPORTANT 88) — the Kind=0 bulk delete in write_cues_to_db
    silently destroyed BOTH our INC-3 loops AND the DJ's hand-placed memory LOOPS
    on any overwrite=True apply (/api/apply, SSE, CLI --overwrite). Memory cues and
    memory loops share Kind=0; the discriminator is OutMsec (a point cue keeps the
    -1 sentinel, a loop has OutMsec > InMsec). The rewrite must delete POINT CUES
    ONLY and spare loops."""

    def _seed_loop(self, session, t):
        session.add(_construct(
            t.DjmdCue, ID="903", ContentID="1", UUID="loop-uuid-1", Kind=0,
            InMsec=10_000, InFrame=1500, OutMsec=18_000, OutFrame=2700,
            BeatLoopSize=16, ActiveLoop=0, Comment="Intro",
        ))
        session.commit()

    def test_memory_loop_survives_overwrite_while_point_cues_are_rewritten(self, real_db):
        from autocue.db_writer import write_cues_to_db
        db, session, t = real_db
        content = _seed(session, t)      # 2 memory POINT cues + 1 hot cue
        self._seed_loop(session, t)      # + a memory LOOP (OutMsec > InMsec)

        write_cues_to_db(
            content,
            [CuePoint(position_ms=7000, label=PhraseLabel.UNKNOWN, slot=-1, name="New Mem")],
            db, overwrite=True,
        )
        rows = _memory_rows(session, t)
        comments = {r.Comment for r in rows}

        # ★ the memory LOOP SURVIVES, byte-identical
        assert "Intro" in comments, "write_cues_to_db(overwrite=True) DESTROYED a memory LOOP"
        loop = next(r for r in rows if r.Comment == "Intro")
        assert (loop.ID, loop.UUID, loop.InMsec, loop.OutMsec, loop.BeatLoopSize) == \
               ("903", "loop-uuid-1", 10_000, 18_000, 16)

        # ...while memory POINT cues are still rewritten (memory_cue_mode intact)
        assert "New Mem" in comments
        assert not any(c.startswith("DJ Memory") for c in comments)

    def test_hot_cues_still_rewritten_slot_wise(self, real_db):
        # Regression: the hot-cue path is untouched by the loop-sparing filter.
        from autocue.db_writer import write_cues_to_db
        db, session, t = real_db
        content = _seed(session, t)
        self._seed_loop(session, t)
        write_cues_to_db(
            content,
            [CuePoint(position_ms=2000, label=PhraseLabel.INTRO, slot=0, name="NewHot")],
            db, overwrite=True,
        )
        hot = session.query(t.DjmdCue).filter(
            t.DjmdCue.ContentID == "1", t.DjmdCue.Kind == 1).all()
        assert [r.Comment for r in hot] == ["NewHot"]
        # and the loop is still there
        assert any(r.Comment == "Intro" for r in _memory_rows(session, t))


class TestMirrorNegativeWhyNotWriteCuesToDb:
    """Pins WHY write_loops_to_db exists: even after FIX-3 (which spares memory
    LOOPS), write_cues_to_db(overwrite=True) still DELETES memory POINT cues — so
    it can never be the loop writer. The no-reuse rationale stands."""

    def test_write_cues_to_db_overwrite_deletes_memory_point_cues(self, real_db):
        from autocue.db_writer import write_cues_to_db
        db, session, t = real_db
        content = _seed(session, t)
        assert len(_memory_rows(session, t)) == 2   # the DJ's memory cues

        write_cues_to_db(
            content,
            [CuePoint(position_ms=10_000, label=PhraseLabel.OUTRO, slot=-1, name="New")],
            db, overwrite=True,
        )
        rows = _memory_rows(session, t)
        # THE CLOBBER: both DJ memory cues are gone, replaced by the one we passed.
        assert [r.Comment for r in rows] == ["New"]
        assert not any(r.Comment.startswith("DJ Memory") for r in rows)


# ---------------------------------------------------------------------------
# INC-3 — the --write-db CLI branch (safety wiring)
# ---------------------------------------------------------------------------

def _writedb_stub(monkeypatch, tmp_path, *, loops=None, rb=False, serve=False,
                  backup_raises=False, write_raises=False, db_file="master.db"):
    """Stub the --write-db seams.

    Records the ORDER of (rb_guard, serve_guard, open_db, backup, write) and the
    PATHS handed to the guard/backup — the two things BL-1 and auditor#85 hinge on.
    """
    from types import SimpleNamespace
    import autocue.cli as cli
    import autocue.analyzer as analyzer
    import autocue.db_writer as dbw

    db_file_path = tmp_path / db_file
    db_file_path.write_bytes(b"fake")     # backup source must exist
    content = SimpleNamespace(
        Title="Fixture", ArtistName="A", FolderPath="/Music", FileNameL="f.mp3",
        FileNameS="f.mp3", ID="1", Length=200,
    )
    calls = {"written": [], "backups": [], "order": [], "rb_paths": [], "db_file": db_file_path}

    def _open_db(*a, **k):
        calls["order"].append("open_db")
        return SimpleNamespace(_db_dir=str(tmp_path))

    def _rb(path=None, *a, **k):
        calls["order"].append("rb_guard")
        calls["rb_paths"].append(str(path))
        return rb

    def _serve(*a, **k):
        calls["order"].append("serve_guard")
        return serve

    def _backup(path, **kw):
        calls["order"].append("backup")
        if backup_raises:
            raise RuntimeError("disk full")
        calls["backups"].append(str(path))
        return tmp_path / "master_20260711T000000.db"

    def _write(content_, cues, db_, **kw):
        calls["order"].append("write")
        if write_raises:
            raise RuntimeError("db exploded")
        calls["written"].append(list(cues))
        return len([c for c in cues if c.is_loop and c.slot == -1])

    monkeypatch.setattr(cli, "MasterDatabase", _open_db)
    monkeypatch.setattr(cli, "analyze_by_title", lambda *a, **k: (content, None))
    monkeypatch.setattr(cli, "generate_cues_for_track",
                        lambda *a, **k: ([CuePoint(position_ms=1000,
                                                   label=PhraseLabel.INTRO, slot=0,
                                                   name="Intro")], "phrase"))
    monkeypatch.setattr(analyzer, "analyze_loops",
                        lambda *a, **k: list(loops if loops is not None else []),
                        raising=False)
    monkeypatch.setattr(dbw, "rekordbox_is_running", _rb)
    monkeypatch.setattr(dbw, "autocue_serve_is_running", _serve)
    monkeypatch.setattr(dbw, "backup_database", _backup)
    monkeypatch.setattr(dbw, "write_loops_to_db", _write)
    return calls


def _argv(monkeypatch, tmp_path, *extra, db_file="master.db"):
    import sys
    monkeypatch.setattr(sys, "argv", [
        "autocue", "--track", "x", "--db-path", str(tmp_path / db_file), *extra,
    ])


class TestWriteDbCli:
    def test_flag_parses(self):
        from autocue.cli import _build_parser
        a = _build_parser().parse_args(["--track", "x", "--loops", "--write-db"])
        assert a.write_db is True
        b = _build_parser().parse_args(["--track", "x", "--loops"])
        assert b.write_db is False

    # ---- FIX-1 (BL-1 BLOCKER) — the ordering pin -------------------------------
    def test_rekordbox_guard_runs_BEFORE_the_db_is_opened(self, monkeypatch, tmp_path):
        """★ THE ANTI-MOCK PIN.

        BL-1: rekordbox_is_running() probes an EXCLUSIVE FILE LOCK. Once AutoCue
        has opened master.db and run a query, SQLAlchemy's autobegin txn holds a
        SQLite lock — so the guard detected OUR OWN handle and aborted 3/3 real
        runs with a false "Rekordbox is running". Every unit test MOCKS the guard,
        which is exactly why this shipped. This test pins the ORDERING, which a
        mock cannot hide: the guard must fire before MasterDatabase is constructed.
        """
        from autocue.cli import main
        calls = _writedb_stub(monkeypatch, tmp_path, loops=[_loop_cue()])
        _argv(monkeypatch, tmp_path, "--loops", "--write-db")
        main()
        order = calls["order"]
        assert "rb_guard" in order and "open_db" in order
        assert order.index("rb_guard") < order.index("open_db"), (
            f"BL-1: the Rekordbox guard must run BEFORE MasterDatabase is opened, "
            f"else it self-detects AutoCue's own DB lock. order={order}"
        )
        assert order.index("serve_guard") < order.index("open_db")
        # And the backup still precedes the write.
        assert order.index("backup") < order.index("write")

    # ---- FIX-4 (auditor 85) — one path for guard + backup + write ---------------
    def test_guard_and_backup_target_the_db_path_flag(self, monkeypatch, tmp_path):
        """--db-path /x/copy.db must be the file guarded AND backed up — not a
        reconstructed _db_dir/'master.db' (which would back up the WRONG file and
        void the printed 'your ONLY undo' promise)."""
        from autocue.cli import main
        calls = _writedb_stub(monkeypatch, tmp_path, loops=[_loop_cue()], db_file="copy.db")
        _argv(monkeypatch, tmp_path, "--loops", "--write-db", db_file="copy.db")
        main()
        target = str(tmp_path / "copy.db")
        assert calls["rb_paths"] == [target], "the guard probed the wrong file"
        assert calls["backups"] == [target], "the BACKUP targeted the wrong file"

    def test_write_db_requires_loops(self, monkeypatch, tmp_path, capsys):
        # Gates on --loops: writing CUES to the DB is a much bigger scope.
        from autocue.cli import main
        calls = _writedb_stub(monkeypatch, tmp_path, loops=[_loop_cue()])
        _argv(monkeypatch, tmp_path, "--write-db")
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        assert "--loops" in capsys.readouterr().err
        assert calls["written"] == [] and calls["backups"] == []

    def test_aborts_when_rekordbox_running(self, monkeypatch, tmp_path, capsys):
        from autocue.cli import main
        calls = _writedb_stub(monkeypatch, tmp_path, loops=[_loop_cue()], rb=True)
        _argv(monkeypatch, tmp_path, "--loops", "--write-db")
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        assert "rekordbox" in capsys.readouterr().err.lower()
        assert calls["written"] == [] and calls["backups"] == []   # nothing written, no backup
        assert "open_db" not in calls["order"]                     # aborted before opening the DB

    def test_aborts_when_autocue_serve_running(self, monkeypatch, tmp_path, capsys):
        # Single-writer: rekordbox_is_running does NOT see a running `autocue serve`.
        from autocue.cli import main
        calls = _writedb_stub(monkeypatch, tmp_path, loops=[_loop_cue()], serve=True)
        _argv(monkeypatch, tmp_path, "--loops", "--write-db")
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        assert "serve" in capsys.readouterr().err.lower()
        assert calls["written"] == [] and calls["backups"] == []

    def test_backup_failure_aborts_before_any_write(self, monkeypatch, tmp_path, capsys):
        # Never write without a successful backup (mirrors routes.py 995-997).
        from autocue.cli import main
        calls = _writedb_stub(monkeypatch, tmp_path, loops=[_loop_cue()],
                              backup_raises=True)
        _argv(monkeypatch, tmp_path, "--loops", "--write-db")
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        assert "backup" in capsys.readouterr().err.lower()
        assert calls["written"] == [], "NOTHING may be written when the backup fails"

    def test_happy_path_backs_up_prints_path_and_writes(self, monkeypatch, tmp_path, capsys):
        from autocue.cli import main
        loop = _loop_cue(pos=10_000, end=18_000, name="Outro")
        calls = _writedb_stub(monkeypatch, tmp_path, loops=[loop])
        _argv(monkeypatch, tmp_path, "--loops", "--write-db")
        main()
        out = capsys.readouterr().out
        assert len(calls["backups"]) == 1                    # backup taken BEFORE the write
        assert "master_20260711T000000.db" in out            # the user's only undo, printed
        assert len(calls["written"]) == 1
        assert calls["written"][0][0].name == "Outro"        # the loop reached the writer

    def test_dry_run_writes_nothing(self, monkeypatch, tmp_path, capsys):
        from autocue.cli import main
        calls = _writedb_stub(monkeypatch, tmp_path, loops=[_loop_cue(name="Outro")])
        _argv(monkeypatch, tmp_path, "--loops", "--write-db", "--dry-run")
        main()
        out = capsys.readouterr().out
        assert "Dry run — no files written." in out
        assert calls["written"] == [] and calls["backups"] == []  # no backup, no write

    # ---- FIX-5 (auditor N1) — per-track failure is reported, not a traceback ----
    def test_per_track_write_failure_is_reported_not_a_traceback(
            self, monkeypatch, tmp_path, capsys):
        from autocue.cli import main
        calls = _writedb_stub(monkeypatch, tmp_path, loops=[_loop_cue()], write_raises=True)
        _argv(monkeypatch, tmp_path, "--loops", "--write-db")
        main()   # must NOT propagate a raw traceback
        combined = capsys.readouterr()
        text = (combined.out + combined.err).lower()
        assert "backup" in text          # the user is reminded where their undo is
        assert "fixture" in text         # the failing track is named


# ---------------------------------------------------------------------------
# FIX-2 (BL-2 / auditor CRITICAL 95) — the serve single-writer probe
# ---------------------------------------------------------------------------

def _fake_procs(monkeypatch, cmdlines):
    """Patch psutil.process_iter with fake processes carrying these cmdlines."""
    import psutil
    from types import SimpleNamespace
    procs = [SimpleNamespace(pid=10_000 + i, info={"cmdline": c})
             for i, c in enumerate(cmdlines)]
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: procs)


class TestServeSingleWriterProbe:
    """The old probe hit ONLY port 7432 — but serve() auto-falls-back to 7433-7441
    and honours --port, so a serve on :3004 was invisible and the single-writer
    guard silently never fired."""

    def test_detects_serve_on_a_fallback_port(self, monkeypatch):
        import autocue.db_writer as dbw
        # Nothing on 7432, but serve auto-switched to 7437 (in serve()'s range).
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: p == 7437)
        _fake_procs(monkeypatch, [])
        assert dbw.autocue_serve_is_running() is True

    def test_detects_serve_process_on_an_arbitrary_port(self, monkeypatch):
        # ★ THE CRITICAL CASE: `autocue serve --port 3004` — no port in the scan
        # range is listening, so ONLY the process probe can catch it.
        import autocue.db_writer as dbw
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: False)
        _fake_procs(monkeypatch, [["autocue", "serve", "--port", "3004"]])
        assert dbw.autocue_serve_is_running() is True

    def test_detects_python_m_autocue_serve(self, monkeypatch):
        import autocue.db_writer as dbw
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: False)
        _fake_procs(monkeypatch, [["python", "-m", "autocue", "serve"]])
        assert dbw.autocue_serve_is_running() is True

    def test_false_when_nothing_running(self, monkeypatch):
        import autocue.db_writer as dbw
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: False)
        _fake_procs(monkeypatch, [["python", "-m", "pytest"], ["Google Chrome"]])
        assert dbw.autocue_serve_is_running() is False

    def test_no_false_positive_on_our_own_write_db_process(self, monkeypatch):
        # `autocue --loops --write-db` contains "autocue" but NOT "serve".
        import autocue.db_writer as dbw
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: False)
        _fake_procs(monkeypatch, [["autocue", "--loops", "--write-db"]])
        assert dbw.autocue_serve_is_running() is False

    def test_fail_safe_when_the_process_probe_raises(self, monkeypatch, caplog):
        # N2: an ambiguous probe must FAIL SAFE (refuse the write), never fail-open.
        import logging
        import psutil
        import autocue.db_writer as dbw

        def _boom(*a, **k):
            raise RuntimeError("procfs unavailable")

        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: False)
        monkeypatch.setattr(psutil, "process_iter", _boom)
        with caplog.at_level(logging.WARNING):
            assert dbw.autocue_serve_is_running() is True, (
                "an unresolvable serve probe must refuse the write, not allow it"
            )
        assert any("fail-safe" in r.getMessage().lower() or "refus" in r.getMessage().lower()
                   for r in caplog.records)

    def test_scans_the_whole_serve_fallback_range(self, monkeypatch):
        # serve() walks port..port+9 → the probe must cover 7432-7441.
        import autocue.db_writer as dbw
        seen = []
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: seen.append(p) or False)
        _fake_procs(monkeypatch, [])
        dbw.autocue_serve_is_running()
        assert set(seen) == set(range(7432, 7442)), f"scanned {seen}"
