"""Single-writer guard: a running `autocue serve` must block the CLI's DB write.

The `--loops` DB write only checks that REKORDBOX is closed. But `autocue serve`
holds its own **read-write** handle on master.db, and `rekordbox_is_running()`
cannot see it. A CLI write concurrent with a server write violates the
single-writer rule for master.db (.claude/project/db-constraints.md).

Detecting it is harder than it looks — two traps:

  (a) FALSE POSITIVES. A naive "'serve' in cmdline and 'autocue' somewhere" match
      fires on `grep serve autocue/cli.py` or `pytest -k serve autocue`, refusing a
      perfectly legal write. The token BEFORE `serve` must end with `autocue`.
  (b) PORT COVERAGE. serve() defaults to 7432 but AUTO-FALLS-BACK through the next
      ports and honours `--port` — a server on :3004 must still be caught. So the
      process table is scanned too, not just a port.

FAIL-SAFE: an unresolvable probe returns True (REFUSE the write). On the one path
that mutates the user's library, never fail open.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest


def _fake_procs(monkeypatch, cmdlines, *, pids=None):
    import psutil
    procs = [
        SimpleNamespace(pid=(pids[i] if pids else 10_000 + i), info={"cmdline": c})
        for i, c in enumerate(cmdlines)
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: procs)


class TestServeDetection:
    def test_serve_on_an_arbitrary_port_is_detected(self, monkeypatch):
        """★ `autocue serve --port 3004` — no port in the scan range is listening,
        so ONLY the process probe can catch it."""
        import autocue.db_writer as dbw
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: False)
        _fake_procs(monkeypatch, [["autocue", "serve", "--port", "3004"]])
        assert dbw.autocue_serve_is_running() is True

    def test_serve_on_a_fallback_port_is_detected(self, monkeypatch):
        # 7432 busy -> serve() auto-switched to 7437.
        import autocue.db_writer as dbw
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: p == 7437)
        _fake_procs(monkeypatch, [])
        assert dbw.autocue_serve_is_running() is True

    def test_the_whole_fallback_range_is_scanned(self, monkeypatch):
        import autocue.db_writer as dbw
        seen = []
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: seen.append(p) or False)
        _fake_procs(monkeypatch, [])
        dbw.autocue_serve_is_running()
        assert set(seen) == set(range(7432, 7442)), f"scanned {seen}"

    @pytest.mark.parametrize("cmdline", [
        ["autocue", "serve"],
        ["autocue", "serve", "--port", "3004"],
        ["python", "-m", "autocue", "serve"],
        ["/usr/local/bin/autocue", "serve", "--no-browser"],
        # Windows launcher — a plain endswith("autocue") MISSES this. A false
        # NEGATIVE is the dangerous direction: the guard exists to stop two
        # writers on the library, so a missed server means a corrupted DB.
        ["autocue.exe", "serve"],
        ["C:\\Program Files\\AutoCue\\autocue.exe", "serve", "--port", "3004"],
    ])
    def test_real_serve_invocations_are_detected(self, monkeypatch, cmdline):
        import autocue.db_writer as dbw
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: False)
        _fake_procs(monkeypatch, [cmdline])
        assert dbw.autocue_serve_is_running() is True, f"missed a real serve: {cmdline}"


class TestNoFalsePositives:
    @pytest.mark.parametrize("cmdline", [
        ["grep", "serve", "autocue/cli.py"],      # a dev grepping the source
        ["pytest", "-k", "serve", "autocue"],     # a test run
        ["vim", "autocue/serve/app.py", "serve"],
        ["serve", "--port", "8080"],              # some unrelated `serve` binary
        ["autocue", "--library", "--loops"],      # the CLI's own sibling process
    ])
    def test_unrelated_processes_do_not_trip_the_guard(self, monkeypatch, cmdline):
        import autocue.db_writer as dbw
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: False)
        _fake_procs(monkeypatch, [cmdline])
        assert dbw.autocue_serve_is_running() is False, f"false positive on {cmdline}"

    def test_the_current_process_is_never_self_detected(self, monkeypatch):
        import os
        import autocue.db_writer as dbw
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: False)
        # Even if OUR pid somehow looks like a serve, we must not detect ourselves.
        _fake_procs(monkeypatch, [["autocue", "serve"]], pids=[os.getpid()])
        assert dbw.autocue_serve_is_running() is False

    def test_nothing_running_is_false(self, monkeypatch):
        import autocue.db_writer as dbw
        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: False)
        _fake_procs(monkeypatch, [["python", "-m", "pytest"], ["Google Chrome"]])
        assert dbw.autocue_serve_is_running() is False


class TestFailSafe:
    def test_an_unresolvable_probe_refuses_the_write(self, monkeypatch, caplog):
        """Never fail OPEN on the path that mutates the user's library."""
        import psutil
        import autocue.db_writer as dbw

        def _boom(*a, **k):
            raise RuntimeError("procfs unavailable")

        monkeypatch.setattr(dbw, "_port_is_listening", lambda p: False)
        monkeypatch.setattr(psutil, "process_iter", _boom)
        with caplog.at_level(logging.WARNING):
            assert dbw.autocue_serve_is_running() is True, (
                "an unresolvable serve probe must REFUSE the write, not allow it"
            )
        assert any("fail-safe" in r.getMessage().lower() or "refus" in r.getMessage().lower()
                   for r in caplog.records)


def _cli_stub(monkeypatch, tmp_path, *, serve=False, rb=False):
    """Stub the --loops write path, recording the ORDER of the guard calls."""
    import sys
    import autocue.cli as cli
    import autocue.db_writer as dbw
    import autocue.analysis.loops as loopmod
    from autocue.models import CuePoint, PhraseLabel

    (tmp_path / "master.db").write_bytes(b"fake")
    content = SimpleNamespace(Title="Fixture", ArtistName="A", FolderPath="/M",
                              FileNameL="f.mp3", FileNameS="f.mp3", ID="1")
    calls = {"order": [], "backups": [], "written": []}

    def _open_db(*a, **k):
        calls["order"].append("open_db")
        return SimpleNamespace(_db_dir=None)

    def _serve_probe(*a, **k):
        calls["order"].append("serve_guard")
        return serve

    def _rb_probe(*a, **k):
        calls["order"].append("rb_guard")
        return rb

    monkeypatch.setattr(cli, "MasterDatabase", _open_db)
    monkeypatch.setattr(cli, "analyze_by_title", lambda *a, **k: (content, None))
    monkeypatch.setattr(cli, "generate_cues_for_track", lambda *a, **k: (
        [CuePoint(position_ms=1000, label=PhraseLabel.INTRO, slot=0, name="I")], "phrase"))
    monkeypatch.setattr(loopmod, "_have_librosa", lambda: True)
    monkeypatch.setattr(loopmod, "generate_loops", lambda *a, **k: [
        {"start_ms": 30_000, "end_ms": 45_000, "name": "Mix In Loop",
         "bars": 8, "confidence": 0.9}])
    monkeypatch.setattr(dbw, "autocue_serve_is_running", _serve_probe)
    monkeypatch.setattr(dbw, "rekordbox_is_running", _rb_probe)
    monkeypatch.setattr(dbw, "backup_database",
                        lambda p, **k: calls["backups"].append(str(p)) or (tmp_path / "b.db"))
    monkeypatch.setattr(dbw, "write_memory_loops",
                        lambda *a, **k: calls["written"].append(1) or 1)
    monkeypatch.setattr(sys, "argv", [
        "autocue", "--track", "x", "--loops", "--db-path", str(tmp_path / "master.db")])
    return calls


class TestCliRefusesTheWrite:
    def test_loops_db_write_is_refused_while_a_serve_is_running(
            self, monkeypatch, tmp_path, capsys):
        """End-to-end: ABORT — no backup, no write, and the DB is never even opened."""
        from autocue.cli import main
        calls = _cli_stub(monkeypatch, tmp_path, serve=True)

        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        err = capsys.readouterr().err.lower()
        assert "serve" in err, "the user must be told to stop the SERVER"
        assert calls["backups"] == [] and calls["written"] == [], \
            "nothing may be written while a server holds the database"
        # The guard lives in the PRE-OPEN preflight, so we refuse before we even
        # take our own handle on the DB — and long before any backup.
        assert "open_db" not in calls["order"]

    def test_serve_is_asked_BEFORE_rekordbox_so_the_message_is_honest(
            self, monkeypatch, tmp_path, capsys):
        """★ THE FOLD'S KEY PROPERTY. A running `autocue serve` ALSO holds the DB
        file, so it trips the file-lock probe inside rekordbox_is_running(). If
        Rekordbox were asked first, the user would be told to close Rekordbox when
        the real culprit is our own server. Ask the specific question first."""
        from autocue.cli import main
        calls = _cli_stub(monkeypatch, tmp_path, serve=True, rb=True)  # both would fire

        with pytest.raises(SystemExit) as e:
            main()
        assert e.value.code == 1
        err = capsys.readouterr().err.lower()
        assert "serve" in err
        assert "rekordbox is running" not in err, "misattributed to Rekordbox"
        # serve is asked first and short-circuits — the lock probe is never reached.
        assert calls["order"].index("serve_guard") == 0
        assert "rb_guard" not in calls["order"]

    def test_both_guards_run_before_the_db_is_opened(self, monkeypatch, tmp_path):
        """Neither guard may be reachable only after MasterDatabase() — that is the
        self-lock this PR fixes — and neither after a backup is taken."""
        from autocue.cli import main
        calls = _cli_stub(monkeypatch, tmp_path)   # both clear → the write proceeds
        main()
        order = calls["order"]
        assert order.index("serve_guard") < order.index("open_db")
        assert order.index("rb_guard") < order.index("open_db")
        assert calls["written"], "the write should proceed when both guards are clear"
