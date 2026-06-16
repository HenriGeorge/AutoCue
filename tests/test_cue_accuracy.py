"""Unit tests for the cue-accuracy benchmark's pure logic (no DB needed)."""

import math

import pytest

from autocue.bench.cue_accuracy import (
    aggregate_results,
    band_tolerances_ms,
    beats_to_ms,
    evaluate_track,
    load_mik_xml,
    match_cues,
    score_cues,
    summarize_by_mode,
)


class TestBeatsToMs:
    def test_128bpm_one_beat(self):
        assert beats_to_ms(1, 128) == pytest.approx(468.75, abs=0.01)

    def test_one_bar_is_four_beats(self):
        assert beats_to_ms(4, 120) == pytest.approx(2000.0)  # 120bpm → 500ms/beat

    def test_zero_bpm_returns_zero(self):
        assert beats_to_ms(4, 0) == 0.0
        assert beats_to_ms(4, -5) == 0.0


class TestMatchCues:
    def test_exact_match(self):
        m = match_cues([1000, 2000], [1000, 2000], tol_ms=100)
        assert len(m) == 2
        assert all(off == 0 for *_, off in m)

    def test_within_tolerance(self):
        m = match_cues([1000], [1080], tol_ms=100)
        assert len(m) == 1
        assert m[0][2] == 1000 - 1080  # signed offset = ref - pred = -80

    def test_outside_tolerance_no_match(self):
        assert match_cues([1000], [1200], tol_ms=100) == []

    def test_one_to_one_no_double_match(self):
        # Two refs near a single pred: only one ref can claim it.
        m = match_cues([1000, 1050], [1020], tol_ms=100)
        assert len(m) == 1
        # The nearer ref (1050, |off|=30 wait: 1000→20, 1050→30) → 1000 wins.
        assert m[0][0] == 0  # ref index 0 (1000) is closer

    def test_greedy_picks_nearest_first(self):
        # pred at 1000; refs at 990 (off 10) and 1005 (off 5) → 1005 should win.
        m = match_cues([990, 1005], [1000], tol_ms=100)
        assert len(m) == 1
        assert m[0][0] == 1  # ref 1005 (index 1) is nearer

    def test_negative_tolerance_clamped(self):
        assert match_cues([1000], [1000], tol_ms=-5) == [(0, 0, 0)]


class TestScoreCues:
    def test_perfect(self):
        s = score_cues([1000, 2000, 3000], [1000, 2000, 3000], tol_ms=50)
        assert s["precision"] == 1.0
        assert s["recall"] == 1.0
        assert s["f1"] == 1.0
        assert s["n_exact"] == 3
        assert s["median_abs_offset_ms"] == 0

    def test_half_recall_full_precision(self):
        # 2 refs, 1 pred that matches one ref.
        s = score_cues([1000, 5000], [1000], tol_ms=50)
        assert s["recall"] == 0.5
        assert s["precision"] == 1.0
        assert s["f1"] == pytest.approx(2 * 0.5 * 1.0 / 1.5)

    def test_overprediction_lowers_precision(self):
        # 1 ref, 3 preds, one matches.
        s = score_cues([1000], [1000, 9000, 12000], tol_ms=50)
        assert s["recall"] == 1.0
        assert s["precision"] == pytest.approx(1 / 3)

    def test_empty_sides(self):
        assert score_cues([], [], 50)["f1"] == 0.0
        assert score_cues([1000], [], 50)["recall"] == 0.0
        assert score_cues([], [1000], 50)["precision"] == 0.0

    def test_offset_stats(self):
        s = score_cues([1000, 2000], [1100, 1900], tol_ms=200)
        assert s["mean_abs_offset_ms"] == pytest.approx(100.0)
        assert s["median_abs_offset_ms"] == pytest.approx(100.0)
        assert s["n_exact"] == 0


class TestBandTolerances:
    def test_uses_bpm_when_present(self):
        t = band_tolerances_ms(120)
        assert t["<=1 beat"] == pytest.approx(500.0)
        assert t["<=1 bar"] == pytest.approx(2000.0)
        assert t["<=2 bars"] == pytest.approx(4000.0)

    def test_falls_back_when_no_bpm(self):
        t = band_tolerances_ms(0)
        assert t == {"<=1 beat": 500.0, "<=1 bar": 2000.0, "<=2 bars": 4000.0}


class TestEvaluateAndAggregate:
    def _mk(self, ref, pred, bpm=120, mode="phrase", tid="1"):
        return evaluate_track(tid, "T", "A", bpm, mode, ref, pred)

    def test_evaluate_track_bands(self):
        tr = self._mk([1000, 2000], [1000, 2000])
        assert tr.bpm_known is True
        assert tr.bands["<=1 bar"]["recall"] == 1.0
        assert set(tr.bands) == {"<=1 beat", "<=1 bar", "<=2 bars"}

    def test_aggregate_macro_average(self):
        a = self._mk([1000], [1000], tid="1")       # f1 1.0
        b = self._mk([1000, 5000], [1000], tid="2")  # recall .5 precision 1
        agg = aggregate_results([a, b])
        assert agg["<=1 bar"]["recall"] == pytest.approx((1.0 + 0.5) / 2)
        assert agg["<=1 bar"]["precision"] == pytest.approx(1.0)

    def test_exact_match_share_flags_contamination(self):
        # All matches exact → share 1.0 (the contamination signal).
        a = self._mk([1000, 2000], [1000, 2000])
        agg = aggregate_results([a])
        assert agg["_exact_match_share"] == 1.0

    def test_by_mode_breakdown(self):
        a = self._mk([1000], [1000], mode="phrase", tid="1")
        b = self._mk([1000], [1000], mode="bar", tid="2")
        bm = summarize_by_mode([a, b])
        assert bm["phrase"]["n"] == 1
        assert bm["bar"]["n"] == 1


class TestLoadMikXml:
    def test_parses_position_marks(self, tmp_path):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <COLLECTION Entries="2">
    <TRACK TrackID="1" Name="Song One" Artist="Artist A">
      <POSITION_MARK Name="Intro" Type="0" Start="1.500" Num="0"/>
      <POSITION_MARK Name="Drop"  Type="0" Start="60.250" Num="1"/>
      <POSITION_MARK Name="Mem"   Type="0" Start="0.000" Num="-1"/>
    </TRACK>
    <TRACK TrackID="2" Name="Song Two" Artist="Artist B"/>
  </COLLECTION>
</DJ_PLAYLISTS>"""
        f = tmp_path / "mik.xml"
        f.write_text(xml)
        out = load_mik_xml(str(f))
        # Song One: two hot cues (memory Num=-1 dropped), seconds → ms.
        assert out[("artist a", "song one")] == [1500.0, 60250.0]
        # Song Two has no marks → absent.
        assert ("artist b", "song two") not in out

    def test_normalizes_whitespace_and_case(self, tmp_path):
        xml = """<?xml version="1.0"?>
<DJ_PLAYLISTS><COLLECTION>
  <TRACK Name="  Mixed   CASE  " Artist="  DJ   X ">
    <POSITION_MARK Name="c" Type="0" Start="2.0" Num="0"/>
  </TRACK>
</COLLECTION></DJ_PLAYLISTS>"""
        f = tmp_path / "m.xml"
        f.write_text(xml)
        out = load_mik_xml(str(f))
        assert ("dj x", "mixed case") in out
