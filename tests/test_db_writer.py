"""Tests for autocue/db_writer.py"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from autocue.db_writer import (
    _bpm_to_color_sort_key,
    backup_database,
    color_tracks_by_bpm,
    delete_cues_from_db,
    has_existing_hot_cues,
    has_existing_memory_cues,
    rekordbox_is_running,
    write_cues_to_db,
)
from autocue.models import CuePoint, PhraseLabel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cue(pos=0, slot=0, label=PhraseLabel.UNKNOWN):
    return CuePoint(position_ms=pos, label=label, slot=slot)


def _make_content(id=1, title="Test Track"):
    return SimpleNamespace(ID=id, Title=title)


def _make_db(existing_hot_cues: int = 0):
    db = MagicMock()
    cue_q = MagicMock()
    cue_q.filter.return_value = cue_q
    cue_q.count.return_value = existing_hot_cues
    cue_q.delete.return_value = None
    db.query.return_value = cue_q
    db.session = MagicMock()
    savepoint = MagicMock()
    db.session.begin_nested.return_value = savepoint
    return db


# ---------------------------------------------------------------------------
# backup_database
# ---------------------------------------------------------------------------

class TestBackupDatabase:
    def test_creates_file_at_backup_dir(self, tmp_path):
        src = tmp_path / "master.db"
        src.write_bytes(b"fake db content")
        backup_dir = tmp_path / "backups"

        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            dest = backup_database(src)

        assert dest.exists()
        assert dest.read_bytes() == b"fake db content"
        assert dest.parent == backup_dir

    def test_filename_contains_timestamp(self, tmp_path):
        src = tmp_path / "master.db"
        src.write_bytes(b"x")
        backup_dir = tmp_path / "backups"

        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            dest = backup_database(src)

        assert dest.name.startswith("master_")
        assert dest.suffix == ".db"

    def test_returns_path_object(self, tmp_path):
        src = tmp_path / "master.db"
        src.write_bytes(b"x")
        backup_dir = tmp_path / "backups"

        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            result = backup_database(src)

        assert isinstance(result, Path)

    def test_copies_wal_and_shm_if_present(self, tmp_path):
        src = tmp_path / "master.db"
        src.write_bytes(b"db")
        (tmp_path / "master.db-wal").write_bytes(b"wal")
        (tmp_path / "master.db-shm").write_bytes(b"shm")
        backup_dir = tmp_path / "backups"

        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            dest = backup_database(src)

        assert dest.exists()
        assert Path(str(dest) + "-wal").exists()
        assert Path(str(dest) + "-shm").exists()
        assert Path(str(dest) + "-wal").read_bytes() == b"wal"
        assert Path(str(dest) + "-shm").read_bytes() == b"shm"

    def test_ok_when_wal_absent(self, tmp_path):
        src = tmp_path / "master.db"
        src.write_bytes(b"db")
        backup_dir = tmp_path / "backups"

        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            dest = backup_database(src)

        assert dest.exists()
        assert not Path(str(dest) + "-wal").exists()
        assert not Path(str(dest) + "-shm").exists()


# ---------------------------------------------------------------------------
# rekordbox_is_running
# ---------------------------------------------------------------------------

class TestRekordboxIsRunning:
    def test_returns_true_when_rekordbox_process_found(self):
        proc = SimpleNamespace(name=lambda: "rekordbox")
        with patch("psutil.process_iter", return_value=[proc]):
            assert rekordbox_is_running() is True

    def test_returns_false_when_no_rekordbox_process(self):
        proc = SimpleNamespace(name=lambda: "chrome")
        with patch("psutil.process_iter", return_value=[proc]):
            assert rekordbox_is_running() is False

    def test_returns_false_when_psutil_unavailable(self):
        import importlib
        import sys
        import autocue.db_writer as mod

        original_psutil = sys.modules.get("psutil")
        sys.modules["psutil"] = None  # type: ignore[assignment]
        try:
            reloaded = importlib.reload(mod)
            result = reloaded.rekordbox_is_running()
            assert result is False
        finally:
            if original_psutil is not None:
                sys.modules["psutil"] = original_psutil
            else:
                sys.modules.pop("psutil", None)
            importlib.reload(mod)  # restore original state


# ---------------------------------------------------------------------------
# has_existing_hot_cues
# ---------------------------------------------------------------------------

class TestHasExistingHotCues:
    def test_returns_count_from_db(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=3)
        assert has_existing_hot_cues(content, db) == 3

    def test_returns_zero_when_no_cues(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=0)
        assert has_existing_hot_cues(content, db) == 0


# ---------------------------------------------------------------------------
# write_cues_to_db
# ---------------------------------------------------------------------------

class TestWriteCuesToDb:
    def test_dry_run_returns_zero_no_db_calls(self):
        content = _make_content()
        db = _make_db()
        cues = [_make_cue()]
        n = write_cues_to_db(content, cues, db, dry_run=True)
        assert n == 0
        db.session.begin_nested.assert_not_called()
        db.session.commit.assert_not_called()

    def test_skip_when_existing_cues_no_overwrite(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=2)
        cues = [_make_cue()]
        n = write_cues_to_db(content, cues, db, overwrite=False)
        assert n == 0
        db.session.begin_nested.assert_not_called()

    def test_overwrite_true_proceeds_despite_existing_cues(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=2)
        cues = [_make_cue(pos=0, slot=0), _make_cue(pos=30_000, slot=1)]
        n = write_cues_to_db(content, cues, db, overwrite=True)
        assert n == 2
        db.session.commit.assert_called_once()

    def test_writes_correct_number_of_cues(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=0)
        cues = [_make_cue(pos=i * 1000, slot=i) for i in range(4)]
        n = write_cues_to_db(content, cues, db)
        assert n == 4
        assert db.session.add.call_count == 4

    def test_session_rollback_on_exception(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=0)
        db.session.commit.side_effect = RuntimeError("db error")
        cues = [_make_cue()]
        with pytest.raises(RuntimeError, match="db error"):
            write_cues_to_db(content, cues, db)
        db.session.rollback.assert_called_once()

    def test_zero_cues_list_writes_nothing(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=0)
        n = write_cues_to_db(content, [], db)
        assert n == 0
        db.session.add.assert_not_called()
        db.session.commit.assert_not_called()
        db.session.begin_nested.assert_not_called()

    def test_comment_uses_cue_name_when_set(self):
        from pyrekordbox.db6 import DjmdCue
        content = _make_content()
        db = _make_db(existing_hot_cues=0)
        cue = CuePoint(position_ms=0, label=PhraseLabel.CHORUS, slot=0, name="Drop")
        write_cues_to_db(content, [cue], db)
        added = db.session.add.call_args[0][0]
        assert added.Comment == "Drop"

    def test_comment_falls_back_to_label_value_when_name_empty(self):
        from pyrekordbox.db6 import DjmdCue
        content = _make_content()
        db = _make_db(existing_hot_cues=0)
        cue = CuePoint(position_ms=0, label=PhraseLabel.CHORUS, slot=0, name="")
        write_cues_to_db(content, [cue], db)
        added = db.session.add.call_args[0][0]
        assert added.Comment == "Chorus"

    def test_color_table_index_written_from_cue(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=0)
        cue = CuePoint(position_ms=0, label=PhraseLabel.CHORUS, slot=0, name="Drop", color_id=5)
        write_cues_to_db(content, [cue], db)
        added = db.session.add.call_args[0][0]
        assert added.ColorTableIndex == 5

    def test_color_table_index_zero_when_no_color(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=0)
        cue = CuePoint(position_ms=0, label=PhraseLabel.UNKNOWN, slot=0, color_id=0)
        write_cues_to_db(content, [cue], db)
        added = db.session.add.call_args[0][0]
        assert added.ColorTableIndex == 0


# ---------------------------------------------------------------------------
# delete_cues_from_db
# ---------------------------------------------------------------------------

class TestDeleteCuesFromDb:
    def test_dry_run_returns_count_without_modifying_db(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=4)
        n = delete_cues_from_db(content, db, dry_run=True)
        assert n == 4
        db.session.begin_nested.assert_not_called()
        db.session.commit.assert_not_called()

    def test_returns_zero_when_no_existing_cues(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=0)
        n = delete_cues_from_db(content, db)
        assert n == 0
        db.session.begin_nested.assert_not_called()

    def test_deletes_and_returns_count(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=5)
        n = delete_cues_from_db(content, db)
        assert n == 5
        db.session.commit.assert_called_once()

    def test_session_rollback_on_exception(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=3)
        db.session.commit.side_effect = RuntimeError("db error")
        with pytest.raises(RuntimeError, match="db error"):
            delete_cues_from_db(content, db)
        db.session.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# _bpm_to_color_sort_key
# ---------------------------------------------------------------------------

class TestBpmToColorSortKey:
    def test_zero_bpm_returns_0(self):
        assert _bpm_to_color_sort_key(0) == 0

    def test_negative_bpm_returns_0(self):
        assert _bpm_to_color_sort_key(-1) == 0

    def test_below_90_is_aqua(self):
        assert _bpm_to_color_sort_key(89.9) == 6

    def test_90_is_green(self):
        assert _bpm_to_color_sort_key(90.0) == 5

    def test_114_is_green(self):
        assert _bpm_to_color_sort_key(114.9) == 5

    def test_115_is_blue(self):
        assert _bpm_to_color_sort_key(115.0) == 7

    def test_124_is_blue(self):
        assert _bpm_to_color_sort_key(124.9) == 7

    def test_125_is_orange(self):
        assert _bpm_to_color_sort_key(125.0) == 3

    def test_134_is_orange(self):
        assert _bpm_to_color_sort_key(134.9) == 3

    def test_135_is_red(self):
        assert _bpm_to_color_sort_key(135.0) == 2

    def test_149_is_red(self):
        assert _bpm_to_color_sort_key(149.9) == 2

    def test_150_is_pink(self):
        assert _bpm_to_color_sort_key(150.0) == 1

    def test_very_fast_is_pink(self):
        assert _bpm_to_color_sort_key(200.0) == 1


# ---------------------------------------------------------------------------
# color_tracks_by_bpm
# ---------------------------------------------------------------------------

def _make_db_for_color(bpm_int=12800):
    """Return a mock DB with one content row and a full DjmdColor table."""
    db = MagicMock()
    content = SimpleNamespace(ID=1, Title="Track", BPM=bpm_int, ColorID=None)
    db.get_content.return_value = content

    # Mock DjmdColor rows: SortKey 1-8
    color_rows = [
        SimpleNamespace(SortKey=i, ID=f"color-uuid-{i}")
        for i in range(1, 9)
    ]
    color_q = MagicMock()
    color_q.all.return_value = color_rows
    db.query.return_value = color_q

    db.session = MagicMock()
    savepoint = MagicMock()
    db.session.begin_nested.return_value = savepoint
    return db, content


class TestColorTracksByBpm:
    def test_dry_run_returns_count_without_db_write(self):
        db, content = _make_db_for_color(bpm_int=12800)
        colored, skipped = color_tracks_by_bpm([1], db, dry_run=True)
        assert colored == 1
        assert skipped == 0
        db.session.begin_nested.assert_not_called()
        db.session.commit.assert_not_called()

    def test_applies_color_id_from_sort_key(self):
        # BPM=128 → sort_key=3 (Orange) → color-uuid-3
        db, content = _make_db_for_color(bpm_int=12800)
        colored, skipped = color_tracks_by_bpm([1], db)
        assert colored == 1
        # Verify raw SQL UPDATE was executed with the correct color ID
        call_args = db.session.execute.call_args
        assert call_args is not None
        params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1]
        assert params["cid"] == "color-uuid-3"
        assert params["tid"] == 1  # integer ID passed directly to raw SQL
        db.session.commit.assert_called_once()

    def test_skips_missing_tracks(self):
        db, _ = _make_db_for_color()
        db.get_content.return_value = None
        colored, skipped = color_tracks_by_bpm([9999], db)
        assert colored == 0
        assert skipped == 1
        db.session.begin_nested.assert_not_called()

    def test_session_rollback_on_exception(self):
        db, _ = _make_db_for_color(bpm_int=12800)
        db.session.commit.side_effect = RuntimeError("db error")
        with pytest.raises(RuntimeError, match="db error"):
            color_tracks_by_bpm([1], db)
        db.session.rollback.assert_called_once()

    def test_empty_track_ids_returns_zero(self):
        db, _ = _make_db_for_color()
        colored, skipped = color_tracks_by_bpm([], db)
        assert colored == 0
        assert skipped == 0
        db.session.begin_nested.assert_not_called()


# ---------------------------------------------------------------------------
# has_existing_memory_cues
# ---------------------------------------------------------------------------

class TestHasExistingMemoryCues:
    def test_returns_count_from_db(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=2)  # _make_db returns same count for any query
        assert has_existing_memory_cues(content, db) == 2

    def test_returns_zero_when_no_memory_cues(self):
        content = _make_content()
        db = _make_db(existing_hot_cues=0)
        assert has_existing_memory_cues(content, db) == 0


# ---------------------------------------------------------------------------
# Memory cue preservation in write_cues_to_db
# ---------------------------------------------------------------------------

class TestMemoryCuePreservation:
    def _make_mem_cue(self, pos=0):
        return CuePoint(position_ms=pos, label=PhraseLabel.UNKNOWN, slot=-1, name="Load Point")

    def _make_hot_cue(self, slot=0, pos=1000):
        return CuePoint(position_ms=pos, label=PhraseLabel.UNKNOWN, slot=slot)

    def test_existing_memory_cues_preserved_without_overwrite(self):
        content = _make_content()
        cues = [self._make_mem_cue(), self._make_hot_cue()]
        with patch("autocue.db_writer.has_existing_memory_cues", return_value=1):
            db = _make_db(existing_hot_cues=0)
            write_cues_to_db(content, cues, db, overwrite=False)
        # Kind=0 DELETE should NOT have been called (existing memory cues preserved)
        filter_calls = str(db.query.return_value.filter.call_args_list)
        assert "Kind == 0" not in filter_calls

    def test_existing_memory_cues_overwritten_with_overwrite_true(self):
        content = _make_content()
        cues = [self._make_mem_cue(), self._make_hot_cue()]
        with patch("autocue.db_writer.has_existing_memory_cues", return_value=1):
            db = _make_db(existing_hot_cues=0)
            count = write_cues_to_db(content, cues, db, overwrite=True)
        assert count == 2  # both hot and memory cue written

    def test_no_existing_memory_cues_allows_write(self):
        content = _make_content()
        cues = [self._make_mem_cue(), self._make_hot_cue()]
        with patch("autocue.db_writer.has_existing_memory_cues", return_value=0):
            db = _make_db(existing_hot_cues=0)
            count = write_cues_to_db(content, cues, db, overwrite=False)
        assert count == 2  # both written since no prior memory cues

    def test_hot_cue_delete_unaffected_by_memory_cue_logic(self):
        content = _make_content()
        cues = [self._make_hot_cue(slot=0), self._make_hot_cue(slot=1, pos=2000)]
        db = _make_db(existing_hot_cues=0)
        count = write_cues_to_db(content, cues, db, overwrite=False)
        assert count == 2

    def test_only_mem_cues_in_list_preserves_when_existing(self):
        content = _make_content()
        cues = [self._make_mem_cue()]
        with patch("autocue.db_writer.has_existing_memory_cues", return_value=2):
            db = _make_db(existing_hot_cues=0)
            count = write_cues_to_db(content, cues, db, overwrite=False)
        assert count == 0  # memory cue skipped, nothing written
