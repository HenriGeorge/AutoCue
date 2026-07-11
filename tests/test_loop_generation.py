"""Tests for mix-in/mix-out loop generation (autocue/analysis/loops.py)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from autocue.analysis.loops import (
    SEAM_THRESHOLD,
    _phrase_candidates,
    generate_loops,
    seam_similarity,
)
from autocue.cache import CacheStore
from autocue.models import CuePoint, PhraseLabel


def _content(bpm=12800, length=300.0, title="T"):
    c = MagicMock()
    c.ID = 42
    c.BPM = bpm  # DB stores int×100 → 128.00 BPM
    c.Length = length
    c.Title = title
    c.UUID = "u"
    return c


def _cue(pos_ms, label, bars=0, slot=0):
    return CuePoint(position_ms=pos_ms, label=label, slot=slot,
                    name=label.value, phrase_bars=bars)


# 128 BPM → bar = 1875 ms
BAR = 1875


class TestPhraseCandidates:
    def test_intro_and_outro_produce_two_loops(self):
        phrases = [
            _cue(0, PhraseLabel.INTRO, bars=16),
            _cue(16 * BAR, PhraseLabel.VERSE, bars=32, slot=1),
            _cue(120_000, PhraseLabel.OUTRO, bars=16, slot=2),
        ]
        with patch("autocue.analyzer.analyze_track", return_value=phrases):
            cands = _phrase_candidates(_content(length=180.0), MagicMock())
        assert [c["kind"] for c in cands] == ["mix_in", "mix_out"]
        mix_in, mix_out = cands
        # mix-in: 8 bars ending exactly at the first non-intro phrase
        assert mix_in["end_ms"] == 16 * BAR
        assert mix_in["start_ms"] == 16 * BAR - 8 * BAR
        assert mix_in["bars"] == 8
        # mix-out: 8 bars from the outro start
        assert mix_out["start_ms"] == 120_000
        assert mix_out["end_ms"] == 120_000 + 8 * BAR

    def test_short_intro_gets_4_bar_loop(self):
        phrases = [
            _cue(0, PhraseLabel.INTRO, bars=4),
            _cue(4 * BAR, PhraseLabel.VERSE, bars=32, slot=1),
        ]
        with patch("autocue.analyzer.analyze_track", return_value=phrases):
            cands = _phrase_candidates(_content(), MagicMock())
        assert len(cands) == 1 and cands[0]["bars"] == 4

    def test_tiny_intro_rejected(self):
        phrases = [
            _cue(0, PhraseLabel.INTRO, bars=2),
            _cue(2 * BAR, PhraseLabel.VERSE, bars=32, slot=1),
        ]
        with patch("autocue.analyzer.analyze_track", return_value=phrases):
            assert _phrase_candidates(_content(), MagicMock()) == []

    def test_track_not_opening_with_intro_gets_no_mix_in(self):
        phrases = [
            _cue(0, PhraseLabel.VERSE, bars=32),
            _cue(100_000, PhraseLabel.OUTRO, bars=16, slot=1),
        ]
        with patch("autocue.analyzer.analyze_track", return_value=phrases):
            cands = _phrase_candidates(_content(length=180.0), MagicMock())
        assert [c["kind"] for c in cands] == ["mix_out"]

    def test_outro_loop_running_past_track_end_rejected(self):
        phrases = [_cue(178_000, PhraseLabel.OUTRO, bars=8)]
        with patch("autocue.analyzer.analyze_track", return_value=phrases):
            assert _phrase_candidates(_content(length=180.0), MagicMock()) == []

    def test_no_bpm_or_no_phrases(self):
        with patch("autocue.analyzer.analyze_track", return_value=[]):
            assert _phrase_candidates(_content(), MagicMock()) == []
        phrases = [_cue(0, PhraseLabel.INTRO, bars=16)]
        with patch("autocue.analyzer.analyze_track", return_value=phrases):
            assert _phrase_candidates(_content(bpm=0), MagicMock()) == []


librosa = pytest.importorskip("librosa", reason="seam tests need autocue[loops]")
import numpy as np  # noqa: E402  (after importorskip on purpose)
import soundfile as sf  # noqa: E402


def _write_wav(path, y, sr=22050):
    sf.write(str(path), y, sr)


class TestSeamSimilarity:
    def test_loopable_signal_accepted(self, tmp_path):
        # A perfectly periodic signal: audio after loop-start == audio after
        # loop-end by construction.
        sr = 22050
        rng = np.random.default_rng(7)
        pattern = rng.standard_normal(sr // 2) * 0.1 + np.sin(
            2 * np.pi * 220 * np.arange(sr // 2) / sr
        )
        y = np.tile(pattern, 20).astype(np.float32)  # 10 s
        f = tmp_path / "loopable.wav"
        _write_wav(f, y, sr)
        sim = seam_similarity(str(f), start_ms=1000, end_ms=3000)  # both on-pattern
        assert sim is not None and sim >= SEAM_THRESHOLD

    def test_discontinuous_signal_rejected(self, tmp_path):
        # Tone into a completely different texture across the seam.
        sr = 22050
        tone = np.sin(2 * np.pi * 220 * np.arange(sr * 4) / sr)
        rng = np.random.default_rng(7)
        noise = (rng.standard_normal(sr * 4) * 0.5)
        y = np.concatenate([tone, noise]).astype(np.float32)  # 4 s tone, 4 s noise
        f = tmp_path / "jumpy.wav"
        _write_wav(f, y, sr)
        sim = seam_similarity(str(f), start_ms=1000, end_ms=5000)
        assert sim is not None and sim < SEAM_THRESHOLD

    def test_loop_at_eof_returns_none(self, tmp_path):
        sr = 22050
        y = np.zeros(sr, dtype=np.float32)  # 1 s file
        f = tmp_path / "short.wav"
        _write_wav(f, y, sr)
        assert seam_similarity(str(f), start_ms=0, end_ms=950) is None


class TestGenerateLoopsCache:
    def _phrases(self):
        return [
            _cue(0, PhraseLabel.INTRO, bars=16),
            _cue(16 * BAR, PhraseLabel.VERSE, bars=32, slot=1),
        ]

    def test_cache_roundtrip_and_stale_mtime(self):
        cache = CacheStore.open_memory()
        content = _content()
        with patch("autocue.analyzer.analyze_track", return_value=self._phrases()), \
             patch("autocue.analysis.loops._have_librosa", return_value=False), \
             patch("autocue.analysis.anlz_path.get_anlz_mtime", return_value=111.0):
            first = generate_loops(content, MagicMock(), cache=cache)
            assert len(first) == 1
        # second call must come from the cache — analyze_track not consulted
        with patch("autocue.analyzer.analyze_track",
                   side_effect=AssertionError("should hit cache")), \
             patch("autocue.analysis.anlz_path.get_anlz_mtime", return_value=111.0):
            assert generate_loops(content, MagicMock(), cache=cache) == first
        # stale mtime → recompute
        with patch("autocue.analyzer.analyze_track", return_value=[]), \
             patch("autocue.analysis.anlz_path.get_anlz_mtime", return_value=222.0):
            assert generate_loops(content, MagicMock(), cache=cache) == []
        cache.close()

    def test_existing_cache_gains_table_without_drop(self, tmp_path):
        # simulate a pre-existing cache: open (creates schema v-current), add
        # an energy row, reopen — loop_verdicts exists AND the row survived.
        d = str(tmp_path)
        c1 = CacheStore.open_for(d)
        c1.put_energy_curve(1, [0.5], anlz_mtime=1.0)
        c1.close()
        c2 = CacheStore.open_for(d)
        assert c2.get_energy_curve(1, expected_anlz_mtime=1.0) is not None
        c2.put_loop_verdicts(1, [{"start_ms": 0}], anlz_mtime=1.0)
        assert c2.get_loop_verdicts(1, expected_anlz_mtime=1.0) == [{"start_ms": 0}]
        c2.close()


class TestWriteMemoryLoops:
    def _db(self, existing_memory=0):
        db = MagicMock()
        db.query.return_value.filter.return_value.count.return_value = existing_memory
        db.generate_unused_id.return_value = 999
        return db

    def test_writes_rows_with_out_points(self):
        from autocue.db_writer import write_memory_loops
        db = self._db()
        loops = [{"start_ms": 30000, "end_ms": 45000, "name": "Mix In Loop"}]
        with patch("pyrekordbox.db6.DjmdCue") as cue_cls:
            n = write_memory_loops(_content(), loops, db)
        assert n == 1
        kwargs = cue_cls.call_args.kwargs
        assert kwargs["Kind"] == 0
        assert kwargs["InMsec"] == 30000 and kwargs["OutMsec"] == 45000
        assert kwargs["OutFrame"] == round(45000 * 150 / 1000)
        assert kwargs["Comment"] == "Mix In Loop"

    def test_preserves_manual_memory_data_without_overwrite(self):
        from autocue.db_writer import write_memory_loops
        db = self._db(existing_memory=2)
        n = write_memory_loops(_content(), [{"start_ms": 0, "end_ms": 1}], db)
        assert n == 0
        db.session.add.assert_not_called()

    def test_dry_run_writes_nothing(self):
        from autocue.db_writer import write_memory_loops
        db = self._db()
        n = write_memory_loops(_content(), [{"start_ms": 0, "end_ms": 1}], db,
                               dry_run=True)
        assert n == 0
        db.session.add.assert_not_called()


class TestSeratoComposition:
    def test_generated_loops_flow_into_markers2(self):
        from autocue.serato_writer import build_markers2, parse_markers2, wrap_outer
        loops = [
            {"start_ms": 30000, "end_ms": 45000, "name": "Mix In Loop"},
            {"start_ms": 200000, "end_ms": 215000, "name": "Mix Out Loop"},
        ]
        payload = build_markers2([], loops=loops)
        entries = parse_markers2(wrap_outer(payload))
        loop_entries = [e for e in entries if e["type"] == "LOOP"]
        assert [(e["start_ms"], e["end_ms"], e["name"]) for e in loop_entries] == [
            (30000, 45000, "Mix In Loop"),
            (200000, 215000, "Mix Out Loop"),
        ]


class TestCalibrationRegressions:
    """Regressions from the first real-library calibration run (2026-07-10):
    only 1/2900 tracks produced loops (CLI skip filter swallowed the prepped
    library) and unverifiable seams were kept at confidence 0.5 (streaming
    'spotify:track:…' rows have no local file)."""

    def _content(self, folder="spotify:track:abc123"):
        c = MagicMock()
        c.ID = 42
        c.BPM = 12000
        c.Length = 300.0
        c.FolderPath = folder
        c.FileNameL = ""
        c.FileNameS = ""
        return c

    def _patch_candidates(self):
        return patch(
            "autocue.analysis.loops._phrase_candidates",
            return_value=[{"start_ms": 8000, "end_ms": 16000,
                           "name": "Mix In Loop", "kind": "mix_in", "bars": 4}],
        )

    def test_streaming_track_without_file_is_rejected(self):
        stats = {}
        with self._patch_candidates(), \
             patch("autocue.analysis.loops._have_librosa", return_value=True):
            out = generate_loops(self._content(), MagicMock(), stats=stats)
        assert out == []
        assert stats.get("no_audio_file") == 1

    def test_unreadable_seam_is_rejected_not_kept(self, tmp_path):
        f = tmp_path / "t.wav"
        f.write_bytes(b"not audio")
        c = self._content(folder=str(tmp_path) + "/")
        c.FileNameL = "t.wav"
        stats = {}
        with self._patch_candidates(), \
             patch("autocue.analysis.loops._have_librosa", return_value=True):
            out = generate_loops(c, MagicMock(), stats=stats)
        assert out == []
        assert stats.get("seam_unreadable") == 1

    def test_grid_only_mode_still_keeps_half_confidence(self):
        stats = {}
        with self._patch_candidates(), \
             patch("autocue.analysis.loops._have_librosa", return_value=False):
            out = generate_loops(self._content(), MagicMock(), stats=stats)
        assert len(out) == 1 and out[0]["confidence"] == 0.5
        assert stats.get("grid_only") == 1

    def test_cli_skip_filter_exempts_loops_runs(self):
        # the library-mode skip-if-cued filter must not gate loop generation
        import inspect
        from autocue import cli
        src = inspect.getsource(cli.main)
        assert "loop_targets = list(tracks)" in src
        filter_idx = src.index("has_existing_hot_cues(content, db)")
        snapshot_idx = src.index("loop_targets = list(tracks)")
        assert snapshot_idx < filter_idx, "snapshot must happen before the filter"
