"""`--loops` DB-write guard: the self-lock, the backup contract, the exit code.

🔴 THE SELF-LOCK (why `--loops` could never write)
`rekordbox_is_running()` probes an EXCLUSIVE FILE LOCK on master.db. But the CLI
had already opened the DB (`MasterDatabase(...)`) and the analysis queries leave
SQLAlchemy's autobegin transaction holding a SQLite lock. So the guard detected
**AutoCue's own handle**, reported a false "Rekordbox is running", and exited 1 —
on every run, with Rekordbox closed. Characterization on a real DB:

    nothing open ............... lock = False
    DB open, no query .......... lock = False
    DB open, AFTER a query ..... lock = TRUE   ← the CLI's state at the guard
    after session.rollback() ... lock = False

⚠️ EVERY unit test MOCKS `rekordbox_is_running`, and a mock can never reveal a
self-lock. So the load-bearing test here does NOT assert the guard's return value
— it asserts the CALL ORDER (`rb_guard` before `open_db`), which no amount of
mocking can hide.

These tests never touch the live master.db.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from autocue.models import CuePoint, PhraseLabel


def _loop(start=30_000, end=45_000, name="Mix In Loop"):
    return {"start_ms": start, "end_ms": end, "name": name,
            "bars": 8, "confidence": 0.9}


def _stub(monkeypatch, tmp_path, *, rb=False, backup_raises=False,
          write_raises=False, loops=None, db_file="master.db"):
    """Run main() DB-free, recording the ORDER of the load-bearing calls."""
    import autocue.cli as cli
    import autocue.db_writer as dbw
    import autocue.analysis.loops as loopmod

    (tmp_path / db_file).write_bytes(b"fake-master-db")

    content = SimpleNamespace(
        Title="Fixture", ArtistName="A", FolderPath="/Music",
        FileNameL="f.mp3", FileNameS="f.mp3", ID="1", Length=200,
    )
    calls = {"order": [], "rb_paths": [], "backups": [], "written": []}

    def _open_db(*a, **k):
        calls["order"].append("open_db")
        return SimpleNamespace(_db_dir=None)   # _db_dir only feeds the cache

    def _rb(path=None, *a, **k):
        calls["order"].append("rb_guard")
        calls["rb_paths"].append(str(path))
        return rb

    def _backup(path, **k):
        calls["order"].append("backup")
        if backup_raises:
            raise RuntimeError("disk full")
        calls["backups"].append(str(path))
        return tmp_path / "master_20260711T000000.db"

    def _write(content_, found, db_, **k):
        calls["order"].append("write")
        if write_raises:
            raise RuntimeError("db exploded")
        calls["written"].append(list(found))
        return len(found)

    monkeypatch.setattr(cli, "MasterDatabase", _open_db)
    monkeypatch.setattr(cli, "analyze_by_title", lambda *a, **k: (content, None))
    monkeypatch.setattr(
        cli, "generate_cues_for_track",
        lambda *a, **k: ([CuePoint(position_ms=1000, label=PhraseLabel.INTRO,
                                   slot=0, name="Intro")], "phrase"))
    monkeypatch.setattr(cli, "_serato_running", lambda: False)
    monkeypatch.setattr(loopmod, "_have_librosa", lambda: True)
    monkeypatch.setattr(
        loopmod, "generate_loops",
        lambda content_, db_, **k: list(loops if loops is not None else [_loop()]))
    monkeypatch.setattr(dbw, "rekordbox_is_running", _rb)
    monkeypatch.setattr(dbw, "backup_database", _backup)
    monkeypatch.setattr(dbw, "write_memory_loops", _write)
    return calls


def _argv(monkeypatch, tmp_path, *extra, db_file="master.db"):
    monkeypatch.setattr(sys, "argv", [
        "autocue", "--track", "x", "--db-path", str(tmp_path / db_file), *extra,
    ])


# ===========================================================================
# ★ THE ANTI-MOCK ORDERING TEST — this is the one that would have caught it
# ===========================================================================

class TestGuardRunsBeforeTheDbIsOpened:
    def test_rekordbox_guard_runs_BEFORE_MasterDatabase_is_constructed(
            self, monkeypatch, tmp_path):
        """The guard takes an EXCLUSIVE FILE LOCK. If it runs after we have opened
        the DB, it self-detects AutoCue's own handle and aborts every run. Asserting
        the guard's RETURN VALUE (what every existing test does) can never reveal
        that — only the ORDER can."""
        from autocue.cli import main
        calls = _stub(monkeypatch, tmp_path)
        _argv(monkeypatch, tmp_path, "--loops")
        main()
        order = calls["order"]
        assert "rb_guard" in order and "open_db" in order
        assert order.index("rb_guard") < order.index("open_db"), (
            "SELF-LOCK: the Rekordbox guard must run BEFORE MasterDatabase is "
            f"opened, or it detects our own DB handle. order={order}"
        )
        # and the backup must still precede the write
        assert order.index("backup") < order.index("write")

    def test_loop_write_actually_happens(self, monkeypatch, tmp_path):
        # The headline regression: --loops must WRITE, not abort.
        from autocue.cli import main
        calls = _stub(monkeypatch, tmp_path)
        _argv(monkeypatch, tmp_path, "--loops")
        main()
        assert calls["written"], "--loops wrote nothing (the shipped bug)"

    def test_guard_and_backup_target_the_db_path_flag(self, monkeypatch, tmp_path):
        """--db-path must be the file guarded AND backed up — not a reconstructed
        `_db_dir/'master.db'`, which would back up a different file than the one
        being written and void the 'your only undo' promise."""
        from autocue.cli import main
        calls = _stub(monkeypatch, tmp_path, db_file="copy.db")
        _argv(monkeypatch, tmp_path, "--loops", db_file="copy.db")
        main()
        target = str(tmp_path / "copy.db")
        assert calls["rb_paths"] == [target]
        assert calls["backups"] == [target]

    def test_aborts_before_opening_the_db_when_rekordbox_runs(
            self, monkeypatch, tmp_path, capsys):
        from autocue.cli import main
        calls = _stub(monkeypatch, tmp_path, rb=True)
        _argv(monkeypatch, tmp_path, "--loops")
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        assert "rekordbox" in capsys.readouterr().err.lower()
        assert "open_db" not in calls["order"]      # never even opened the DB
        assert calls["backups"] == [] and calls["written"] == []

    def test_non_loop_paths_unchanged(self, monkeypatch, tmp_path):
        """Without --loops there is no DB write, so no pre-open guard runs and the
        XML/Serato paths behave exactly as before."""
        from autocue.cli import main
        calls = _stub(monkeypatch, tmp_path)
        _argv(monkeypatch, tmp_path, "--output", str(tmp_path / "o.xml"))
        main()
        assert "rb_guard" not in calls["order"]
        assert calls["written"] == []


# ===========================================================================
# The backup contract — never write without a successful backup
# ===========================================================================

class TestBackupContract:
    def test_backup_failure_aborts_with_nothing_written(
            self, monkeypatch, tmp_path, capsys):
        """main took a backup but never wrapped it — a failing backup let the write
        proceed anyway, leaving the user with NO undo."""
        from autocue.cli import main
        calls = _stub(monkeypatch, tmp_path, backup_raises=True)
        _argv(monkeypatch, tmp_path, "--loops")
        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        assert "backup" in capsys.readouterr().err.lower()
        assert calls["written"] == [], "wrote loops despite a FAILED backup"

    def test_backup_path_is_printed(self, monkeypatch, tmp_path, capsys):
        from autocue.cli import main
        _stub(monkeypatch, tmp_path)
        _argv(monkeypatch, tmp_path, "--loops")
        main()
        assert "master_20260711T000000.db" in capsys.readouterr().out


# ===========================================================================
# Exit codes — a partial DB write must not look like success
# ===========================================================================

class TestExitCodes:
    def test_partial_write_exits_non_zero(self, monkeypatch, tmp_path, capsys):
        from autocue.cli import main
        _stub(monkeypatch, tmp_path, write_raises=True)
        _argv(monkeypatch, tmp_path, "--loops")
        with pytest.raises(SystemExit) as e:
            main()                       # not a raw traceback…
        assert e.value.code == 1         # …and not a silent success
        out = capsys.readouterr()
        text = (out.out + out.err).lower()
        assert "fixture" in text         # names the failing track
        assert "backup" in text          # reminds where the undo lives

    def test_full_success_exits_zero(self, monkeypatch, tmp_path):
        from autocue.cli import main
        _stub(monkeypatch, tmp_path)
        _argv(monkeypatch, tmp_path, "--loops")
        main()                           # no SystemExit


# ===========================================================================
# Dry run
# ===========================================================================

class TestDryRun:
    def test_dry_run_previews_loops_and_writes_nothing(
            self, monkeypatch, tmp_path, capsys):
        from autocue.cli import main
        calls = _stub(monkeypatch, tmp_path)
        _argv(monkeypatch, tmp_path, "--loops", "--dry-run")
        main()
        out = capsys.readouterr().out
        assert "Dry run — no files written." in out
        assert "Mix In Loop" in out                 # previewed
        assert calls["backups"] == [] and calls["written"] == []
