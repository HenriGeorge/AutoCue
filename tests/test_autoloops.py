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
