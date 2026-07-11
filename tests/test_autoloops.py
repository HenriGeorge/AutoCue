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
