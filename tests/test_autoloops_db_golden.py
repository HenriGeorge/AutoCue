"""
VERIFIER-owned INDEPENDENT SAFETY SUITE — AUTOLOOPS INCREMENT 3 (DB-direct loop write).

🚨 `--write-db` is the ONLY AutoCue CLI path that MUTATES the real Rekordbox library.
The failure mode is DESTRUCTION OF USER DATA: memory CUES and memory LOOPS share the
`Kind=0` space (discriminated only by `OutMsec`), so a loop write that DELETEs `Kind=0`
wipes the DJ's hand-placed memory cues. `write_cues_to_db(overwrite=True)` does exactly
that (`db_writer.py:632-637`) — which is precisely why INC-3 must NOT reuse it.

**Why this file exists (#99).** These assertions are authored from the DESIGN contract by
someone who did NOT write `write_loops_to_db`. An implementer who forgot the no-DELETE
rule would equally forget to test for it — the clobber test would be written to match the
code rather than the requirement. A safety suite the builder wrote is not independent
proof. This file is the independent proof.

Cases (crew/test-designer.md INC-3 map · crew/DESIGN.md "INCREMENT 3"):
  DB-1  NO-CLOBBER — every pre-existing Kind=0 row survives BYTE-IDENTICAL   ← the whole case
  DB-2  NO DELETE EVER ISSUED — structural, at the emitted-SQL level          ← stronger than DB-1
  DB-3  IDEMPOTENT — re-run inserts ZERO rows
  DB-4  COLLISION → skipped + breadcrumb; non-colliding loops still written
  DB-5  MIRROR-NEGATIVE — characterizes that write_cues_to_db DOES delete Kind=0
  DB-6  EXCEPTION → rollback, no partial write, error RAISED (not swallowed)
  DB-7  COLUMNS + UNITS (ms / beats / ActiveLoop=0 — the invisible-until-a-CDJ-misbehaves bugs)
  DB-8  Only memory LOOPS are written — a hot cue or point cue must NEVER leak into the DB
  DB-9..DB-13  CLI guard chain (Rekordbox running · backup-before-insert & backup-failure-aborts
               · `autocue serve` single-writer · --dry-run · --write-db without --loops)

────────────────────────────────────────────────────────────────────────────
🔒 TEST-HARNESS RULES (non-negotiable)
────────────────────────────────────────────────────────────────────────────
1. **NO test here may touch the live master.db.** Everything runs against an in-memory
   SQLite carrying the real pyrekordbox schema. `MasterDatabase` is never *called* — the
   CLI tests monkeypatch it away so a real library can never be opened.
2. **`db.generate_unused_id` MUST be stubbed.** Unstubbed, a MagicMock silently writes
   `ID=<MagicMock ...>` and a test can pass while producing garbage rows (researcher
   P0-DBWRITE). DB-7 asserts the ID is a real value, pinning the trap shut.
3. **Schema-pinned** — the loop columns are introspected from `DjmdCue.__table__`, so a
   future pyrekordbox rename/drop of OutMsec/BeatLoopSize/ActiveLoop FAILS loudly instead
   of silently writing nothing.

Contract note: the CLI `--write-db` seam (DB-9..DB-13) is not wired at authoring time —
those cases fail by design until the implementer lands it; reconcile the monkeypatch
targets at P4 (same pattern that worked for `analyze_loops` in INC-1).
"""
from __future__ import annotations

import datetime as _dt
import itertools
import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from pyrekordbox.db6 import tables as t

from autocue.models import CuePoint, PhraseLabel

# Columns excluded from the "byte-identical" comparison: ORM housekeeping stamps that
# are not part of the cue's meaning. EVERY other column is compared (a superset of the
# DESIGN-named set: ID, UUID, InMsec, InFrame, OutMsec, Kind, Comment, ColorTableIndex,
# ContentUUID).
_HOUSEKEEPING = {"created_at", "updated_at"}

CONTENT_ID = "4242"


# --------------------------------------------------------------------------- harness

def _default_value(col):
    """Fill a NOT NULL column by SQL type so the INSERT succeeds (pyrekordbox has
    dozens of NOT NULL columns the loop feature never reads)."""
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


# Columns pyrekordbox's ORM marks NOT NULL that the REAL djmdCue DDL declares
# `DEFAULT NULL` — verified this session against a COPY of the live master.db:
#   `InPointSeekInfo VARCHAR(255) DEFAULT NULL` … and 30761 of 30763 real rows are NULL.
# The SHIPPED write_cues_to_db (the /api/apply server path) omits them too. So a scratch
# DDL built from the ORM metadata is STRICTER than the user's real database and would
# produce a FALSE RED. Relax exactly these four (restored after create_all — no global
# side effect). Every column the writer MUST set (Kind/InMsec/OutMsec/BeatLoopSize/
# ActiveLoop/…) stays NOT NULL, so omitting one still fails loudly.
_ORM_STRICTER_THAN_REAL_DB = ("InPointSeekInfo", "OutPointSeekInfo", "usn", "rb_local_usn")


@pytest.fixture
def scratch():
    """In-memory SQLite + the REAL pyrekordbox schema. Never the live library."""
    engine = create_engine("sqlite:///:memory:")

    relaxed = [t.DjmdCue.__table__.columns[n] for n in _ORM_STRICTER_THAN_REAL_DB]
    for c in relaxed:
        c.nullable = True
    try:
        t.Base.metadata.create_all(engine)
    finally:
        for c in relaxed:
            c.nullable = False

    session = sessionmaker(bind=engine)()

    sql: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        sql.append(statement)

    db = MagicMock()
    db.session = session
    db.query = session.query          # has_existing_hot_cues / has_existing_memory_cues

    # ⚠ THE FALSE-GREEN TRAP: unstubbed, this returns a MagicMock and every row is
    # written with ID=<MagicMock ...> while the test still "passes".
    ids = itertools.count(9001)
    db.generate_unused_id = MagicMock(side_effect=lambda *a, **k: next(ids))

    yield SimpleNamespace(db=db, session=session, engine=engine, sql=sql)

    session.close()
    engine.dispose()


def _seed_content(session, title="Scratch Track"):
    content = _construct(t.DjmdContent, ID=CONTENT_ID, Title=title, UUID="content-uuid")
    session.add(content)
    session.flush()
    return content


def _seed_cue(session, *, row_id, in_msec, kind, comment, out_msec=-1, color=0):
    """A pre-existing DjmdCue row. kind=0 → memory cue/loop; kind>=1 → hot cue slot."""
    row = _construct(
        t.DjmdCue,
        ID=str(row_id),
        ContentID=CONTENT_ID,
        ContentUUID="content-uuid",
        UUID=f"uuid-{row_id}",
        InMsec=in_msec,
        InFrame=int(round(in_msec * 150.0 / 1000.0)),
        OutMsec=out_msec,
        Kind=kind,
        Comment=comment,
        ColorTableIndex=color,
    )
    session.add(row)
    session.flush()
    return row


def _cues(session, kind=None):
    q = session.query(t.DjmdCue).filter(t.DjmdCue.ContentID == CONTENT_ID)
    if kind is not None:
        q = q.filter(t.DjmdCue.Kind == kind)
    return q.all()


def _snapshot(session):
    """{row_id: {column: value}} for every cue row — the byte-identity baseline."""
    session.expire_all()   # force a fresh DB read; never trust the identity map
    return {
        r.ID: {
            c.name: getattr(r, c.name)
            for c in t.DjmdCue.__table__.columns
            if c.name not in _HOUSEKEEPING
        }
        for r in _cues(session)
    }


def _loop(pos, end, name="Outro", beats=32, label=PhraseLabel.OUTRO, slot=-1):
    return CuePoint(position_ms=pos, label=label, slot=slot, name=name,
                    loop_end_ms=end, loop_beats=beats)


def _point_cue(pos, name="Load Point", slot=-1):
    return CuePoint(position_ms=pos, label=PhraseLabel.UNKNOWN, slot=slot, name=name)


# ===========================================================================
# DB-1 — NO-CLOBBER. The whole safety case.
# ===========================================================================

class TestNoClobber:
    def test_existing_memory_cues_survive_byte_identical(self, scratch, caplog):
        from autocue.db_writer import write_loops_to_db

        content = _seed_content(scratch.session)
        # The DJ's hand-placed memory cues (Kind=0, OutMsec=-1 → point cues) + a hot cue.
        _seed_cue(scratch.session, row_id=1, in_msec=1_000, kind=0, comment="DJ Load", color=3)
        _seed_cue(scratch.session, row_id=2, in_msec=90_000, kind=0, comment="DJ Mix Out", color=5)
        _seed_cue(scratch.session, row_id=3, in_msec=30_000, kind=1, comment="Hot A", color=2)
        scratch.session.commit()

        before = _snapshot(scratch.session)
        assert len(before) == 3

        written = write_loops_to_db(
            content,
            [_loop(10_000, 18_000, "Intro"), _loop(200_000, 214_000, "Outro")],
            scratch.db,
        )

        after = _snapshot(scratch.session)

        # (a) THE NO-CLOBBER ASSERTION — every pre-existing row still exists, byte-identical.
        for row_id, cols in before.items():
            assert row_id in after, f"CLOBBER: pre-existing cue row {row_id} was DESTROYED"
            assert after[row_id] == cols, (
                f"CLOBBER: pre-existing cue row {row_id} was MUTATED — "
                f"changed columns: "
                f"{ {k: (cols[k], after[row_id][k]) for k in cols if cols[k] != after[row_id][k]} }"
            )

        # (b) …and the loops were ADDED alongside them.
        assert written == 2
        assert len(after) == 5                       # 3 original + 2 loops
        assert len(_cues(scratch.session, kind=0)) == 4   # 2 DJ memory cues + 2 loops
        assert len(_cues(scratch.session, kind=1)) == 1   # the hot cue is untouched


# ===========================================================================
# DB-2 — NO DELETE EVER ISSUED (structural — stronger than DB-1)
# ===========================================================================

class TestNoDeleteEverIssued:
    def test_no_delete_statement_is_emitted(self, scratch):
        """Catches a DELETE that happens to match 0 rows in THIS fixture but would
        clobber a real library. Asserts on the SQL actually sent to the driver."""
        from autocue.db_writer import write_loops_to_db

        content = _seed_content(scratch.session)
        _seed_cue(scratch.session, row_id=1, in_msec=1_000, kind=0, comment="DJ Load")
        scratch.session.commit()

        scratch.sql.clear()
        write_loops_to_db(content, [_loop(10_000, 18_000, "Intro")], scratch.db)

        # Match the DML *statement* form, not the substring: a plain SELECT mentions the
        # column `rb_local_deleted` (→ "…DELETED…"), which a naive `"DELETE" in sql`
        # check would flag as a false positive. A DELETE statement always starts with it.
        deletes = [s for s in scratch.sql if s.strip().upper().startswith("DELETE")]
        assert deletes == [], (
            "write_loops_to_db issued a DELETE — clobber is possible by construction:\n"
            + "\n".join(deletes)
        )
        # sanity: the listener really is capturing (else the assertion above is vacuous)
        assert any("INSERT" in s.upper() for s in scratch.sql), "SQL listener captured nothing"


# ===========================================================================
# DB-3 — IDEMPOTENT
# ===========================================================================

class TestIdempotency:
    def test_rerun_inserts_zero_rows(self, scratch):
        from autocue.db_writer import write_loops_to_db

        content = _seed_content(scratch.session)
        scratch.session.commit()
        loops = [_loop(10_000, 18_000, "Intro"), _loop(200_000, 214_000, "Outro")]

        first = write_loops_to_db(content, loops, scratch.db)
        count_after_first = len(_cues(scratch.session, kind=0))

        second = write_loops_to_db(content, loops, scratch.db)
        count_after_second = len(_cues(scratch.session, kind=0))

        assert first == 2
        assert second == 0, "re-run must insert ZERO rows (idempotent)"
        assert count_after_second == count_after_first == 2, "re-run duplicated loop rows"


# ===========================================================================
# DB-4 — COLLISION → skipped + breadcrumb (mirror-first: the DJ wins)
# ===========================================================================

class TestCollisionSkip:
    def test_colliding_loop_skipped_with_breadcrumb_others_still_written(self, scratch, caplog):
        from autocue.db_writer import write_loops_to_db

        content = _seed_content(scratch.session)
        # The DJ already has a memory cue exactly at 10_000 ms.
        _seed_cue(scratch.session, row_id=1, in_msec=10_000, kind=0, comment="DJ Cue")
        scratch.session.commit()

        colliding = _loop(10_000, 18_000, "Intro")     # same start → DJ wins
        free = _loop(200_000, 214_000, "Outro")        # no collision → must still be written

        with caplog.at_level(logging.INFO):
            written = write_loops_to_db(content, [colliding, free], scratch.db)

        assert written == 1, "one collision must not suppress the whole track"
        starts = {int(r.InMsec) for r in _cues(scratch.session, kind=0)}
        assert starts == {10_000, 200_000}
        # the DJ's row is still theirs, not overwritten by the loop
        dj = [r for r in _cues(scratch.session, kind=0) if int(r.InMsec) == 10_000][0]
        assert dj.Comment == "DJ Cue"
        assert int(dj.OutMsec) == -1, "the DJ's point cue was converted into a loop!"

        # silent-failure lens: the skip must leave a breadcrumb
        assert any(
            "10000" in r.getMessage() or "Intro" in r.getMessage()
            for r in caplog.records
        ), "a skipped loop must be logged, never silently dropped"


# ===========================================================================
# DB-5 — MIRROR-NEGATIVE: characterize WHY we do not reuse write_cues_to_db
# ===========================================================================

class TestMirrorNegativeWriteCuesIsUnsafe:
    """Characterization of the DANGEROUS function. If either test fails, someone
    changed write_cues_to_db's semantics and INC-3's rationale must be re-derived."""

    def test_overwrite_true_DELETES_existing_memory_cues(self, scratch):
        from autocue.db_writer import write_cues_to_db

        content = _seed_content(scratch.session)
        _seed_cue(scratch.session, row_id=1, in_msec=1_000, kind=0, comment="DJ Load")
        _seed_cue(scratch.session, row_id=2, in_msec=90_000, kind=0, comment="DJ Mix Out")
        scratch.session.commit()
        assert len(_cues(scratch.session, kind=0)) == 2

        write_cues_to_db(content, [_point_cue(5_000, "New Mem")], scratch.db, overwrite=True)

        survivors = {r.Comment for r in _cues(scratch.session, kind=0)}
        assert "DJ Load" not in survivors and "DJ Mix Out" not in survivors, (
            "write_cues_to_db(overwrite=True) NO LONGER deletes Kind=0 — the INC-3 "
            "no-reuse rationale has changed and must be re-derived"
        )
        assert survivors == {"New Mem"}   # ← THE CLOBBER, pinned in a test

    def test_overwrite_false_SILENTLY_drops_the_memory_cue(self, scratch):
        from autocue.db_writer import write_cues_to_db

        content = _seed_content(scratch.session)
        _seed_cue(scratch.session, row_id=1, in_msec=1_000, kind=0, comment="DJ Load")
        scratch.session.commit()

        write_cues_to_db(content, [_point_cue(5_000, "New Mem")], scratch.db, overwrite=False)

        comments = {r.Comment for r in _cues(scratch.session, kind=0)}
        assert comments == {"DJ Load"}, "expected the pre-existing memory cue to remain"
        assert "New Mem" not in comments, (
            "write_cues_to_db(overwrite=False) NO LONGER silently drops the memory cue — "
            "the INC-3 no-reuse rationale has changed"
        )


# ===========================================================================
# DB-6 — EXCEPTION → rollback, NO partial write, error RAISED
# ===========================================================================

class TestRollbackOnFailure:
    def test_midwrite_failure_rolls_back_everything_and_raises(self, scratch, caplog):
        from autocue.db_writer import write_loops_to_db

        content = _seed_content(scratch.session)
        _seed_cue(scratch.session, row_id=1, in_msec=1_000, kind=0, comment="DJ Load")
        scratch.session.commit()
        before = _snapshot(scratch.session)

        # Blow up while building the SECOND loop row.
        calls = {"n": 0}

        def _boom(*a, **k):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated DB failure")
            return 9000 + calls["n"]

        scratch.db.generate_unused_id = MagicMock(side_effect=_boom)

        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError):          # ← must PROPAGATE, not be swallowed
                write_loops_to_db(
                    content,
                    [_loop(10_000, 18_000, "Intro"), _loop(200_000, 214_000, "Outro")],
                    scratch.db,
                )

        after = _snapshot(scratch.session)
        # NOTHING partially written — not even the first loop.
        assert after == before, "partial write survived a failed loop write (no rollback)"
        assert len(_cues(scratch.session, kind=0)) == 1
        assert {r.Comment for r in _cues(scratch.session, kind=0)} == {"DJ Load"}
        assert any(r.levelno >= logging.ERROR for r in caplog.records), \
            "a failed write must leave an error breadcrumb"


# ===========================================================================
# DB-7 — COLUMNS + UNITS (the invisible-until-a-CDJ-misbehaves bugs)
# ===========================================================================

class TestLoopRowColumnsAndUnits:
    def test_schema_pin_loop_columns_exist(self):
        """If pyrekordbox renames/drops a loop column, fail LOUDLY here rather than
        silently writing nothing."""
        cols = {c.name for c in t.DjmdCue.__table__.columns}
        for required in ("Kind", "InMsec", "InFrame", "OutMsec", "OutFrame",
                         "OutMpegFrame", "OutMpegAbs", "ActiveLoop", "BeatLoopSize",
                         "Comment", "ContentID", "ContentUUID", "UUID", "ID"):
            assert required in cols, f"pyrekordbox schema no longer has DjmdCue.{required}"

    def test_loop_row_columns_and_units(self, scratch):
        from autocue.db_writer import write_loops_to_db

        content = _seed_content(scratch.session)
        scratch.session.commit()

        # An 8-BAR loop → 32 BEATS. 10.0 s → 18.0 s.
        loop = _loop(10_000, 18_000, name="Outro", beats=32)
        assert write_loops_to_db(content, [loop], scratch.db) == 1

        rows = _cues(scratch.session, kind=0)
        assert len(rows) == 1
        r = rows[0]

        assert int(r.Kind) == 0                                  # memory (slot=-1)
        assert int(r.InMsec) == 10_000
        assert int(r.InFrame) == round(10_000 * 150 / 1000)      # 1500
        # ── UNIT TRAPS ──────────────────────────────────────────────────────
        assert int(r.OutMsec) == 18_000, "OutMsec must be MILLISECONDS (18 → an 18 ms loop)"
        assert int(r.OutMsec) != -1, "OutMsec=-1 silently degrades the loop to a POINT CUE"
        assert int(r.OutMsec) > int(r.InMsec), "loop end must be after loop start"
        assert int(r.OutFrame) == round(18_000 * 150 / 1000)     # 2700
        assert int(r.BeatLoopSize) == 32, (
            "BeatLoopSize must be BEATS (bars×4) — an 8-bar loop is 32 beats, not 8"
        )
        assert int(r.ActiveLoop) == 0, (
            "ActiveLoop=1 AUTO-ARMS the loop — the track starts looping on the DJ (non-goal)"
        )
        # ────────────────────────────────────────────────────────────────────
        assert int(r.OutMpegFrame) == 0 and int(r.OutMpegAbs) == 0
        assert r.Comment == "Outro"                              # the loop NAME
        assert r.ContentID == content.ID
        assert r.UUID                                            # non-empty

        # the generate_unused_id stub trap: a MagicMock ID must never reach the DB
        assert "MagicMock" not in str(r.ID), (
            "row written with ID=<MagicMock> — db.generate_unused_id was not stubbed"
        )
        assert str(r.ID) == "9001"

    def test_uuids_are_unique_across_loops(self, scratch):
        from autocue.db_writer import write_loops_to_db

        content = _seed_content(scratch.session)
        scratch.session.commit()
        write_loops_to_db(
            content,
            [_loop(10_000, 18_000, "Intro"), _loop(200_000, 214_000, "Outro")],
            scratch.db,
        )
        rows = _cues(scratch.session, kind=0)
        assert len({r.UUID for r in rows}) == 2
        assert len({r.ID for r in rows}) == 2


# ===========================================================================
# DB-8 — ONLY memory LOOPS are written (scope containment)
# ===========================================================================

class TestOnlyMemoryLoopsAreWritten:
    def test_hot_cue_and_point_cue_never_reach_the_db(self, scratch):
        from autocue.db_writer import write_loops_to_db

        content = _seed_content(scratch.session)
        scratch.session.commit()

        mixed = [
            CuePoint(position_ms=1_000, label=PhraseLabel.INTRO, slot=0, name="Hot A"),  # hot cue
            _point_cue(2_000, "Load Point"),                    # memory POINT cue (no loop end)
            _loop(10_000, 18_000, "Outro"),                     # memory LOOP ← the only one
        ]
        written = write_loops_to_db(content, mixed, scratch.db)

        assert written == 1
        all_rows = _cues(scratch.session)
        assert len(all_rows) == 1, "a hot cue or point cue leaked into the DB"
        assert int(all_rows[0].Kind) == 0
        assert all_rows[0].Comment == "Outro"
        assert not [r for r in all_rows if int(r.Kind) >= 1], "a Kind>=1 hot row was written"

    def test_hot_slot_loop_is_not_written(self, scratch):
        """A loop carrying a hot slot (slot>=0) is out of scope — memory loops only."""
        from autocue.db_writer import write_loops_to_db

        content = _seed_content(scratch.session)
        scratch.session.commit()
        assert write_loops_to_db(content, [_loop(10_000, 18_000, "Outro", slot=0)], scratch.db) == 0
        assert _cues(scratch.session) == []

    def test_no_loops_is_a_clean_noop(self, scratch):
        from autocue.db_writer import write_loops_to_db

        content = _seed_content(scratch.session)
        scratch.session.commit()
        assert write_loops_to_db(content, [_point_cue(1_000)], scratch.db) == 0
        assert _cues(scratch.session) == []

    def test_dry_run_writes_nothing(self, scratch):
        from autocue.db_writer import write_loops_to_db

        content = _seed_content(scratch.session)
        scratch.session.commit()
        assert write_loops_to_db(
            content, [_loop(10_000, 18_000, "Outro")], scratch.db, dry_run=True
        ) == 0
        assert _cues(scratch.session) == [], "dry_run inserted rows"


# ===========================================================================
# DB-9..DB-13 — the CLI guard chain. EVERY abort path must leave the DB untouched.
# ===========================================================================

def _install_db_cli_stubs(monkeypatch, db_dir, *, loops, rb_running=False, serve_running=False,
                          backup_raises=False, order=None):
    """Patch the CLI's DB + analysis + guard seams so main() runs with NO real library.

    Returns a `calls` namespace recording what the CLI actually did.
    `order` (a list) records the *sequence* of backup vs write — DB-10a ordering proof.

    `db_dir` is a tmp dir standing in for the Rekordbox dir: the CLI derives
    `db_path = Path(db._db_dir) / "master.db"` and checks it exists, so the fake db
    must expose a real `_db_dir` (a bare MagicMock would blow up in `Path()`).
    """
    import autocue.cli as cli
    import autocue.analyzer as analyzer
    import autocue.db_writer as db_writer

    calls = SimpleNamespace(writes=[], backups=[], order=order if order is not None else [])
    content = SimpleNamespace(ID=CONTENT_ID, Title="Fixture", FileNameL="fixture.mp3")

    # A DECOY master.db in a tmp dir — the live library is never referenced.
    (db_dir / "master.db").write_bytes(b"decoy - not a real database")

    fake_db = MagicMock()
    fake_db._db_dir = str(db_dir)

    # NOTE: MasterDatabase is monkeypatched away — no real master.db can ever be opened.
    monkeypatch.setattr(cli, "MasterDatabase", lambda *a, **k: fake_db)
    monkeypatch.setattr(cli, "analyze_by_title", lambda *a, **k: (content, None))
    monkeypatch.setattr(cli, "analyze_by_id", lambda *a, **k: (content, None))
    monkeypatch.setattr(cli, "generate_cues_for_track",
                        lambda *a, **k: ([_point_cue(1_000, "Load")], "phrase"))
    monkeypatch.setattr(analyzer, "analyze_loops", lambda *a, **k: list(loops), raising=False)
    monkeypatch.setattr(cli, "analyze_loops", lambda *a, **k: list(loops), raising=False)

    monkeypatch.setattr(db_writer, "rekordbox_is_running", lambda *a, **k: rb_running)
    monkeypatch.setattr(cli, "rekordbox_is_running", lambda *a, **k: rb_running, raising=False)
    monkeypatch.setattr(db_writer, "autocue_serve_is_running", lambda *a, **k: serve_running)
    monkeypatch.setattr(cli, "autocue_serve_is_running", lambda *a, **k: serve_running,
                        raising=False)

    def _backup(*a, **k):
        calls.order.append("backup")
        if backup_raises:
            raise OSError("simulated backup failure (disk full)")
        p = "/tmp/fake-backups/master_20260711T000000.db"
        calls.backups.append(p)
        from pathlib import Path
        return Path(p)

    monkeypatch.setattr(db_writer, "backup_database", _backup)
    monkeypatch.setattr(cli, "backup_database", _backup, raising=False)

    def _write_loops(content_, cues_, db_, **kw):
        calls.order.append("write")
        n = len([c for c in cues_ if c.is_loop and c.slot == -1])
        calls.writes.append(n)
        return n

    monkeypatch.setattr(db_writer, "write_loops_to_db", _write_loops)
    monkeypatch.setattr(cli, "write_loops_to_db", _write_loops, raising=False)

    # never let the Serato/XML terminal branches do real work in these tests
    monkeypatch.setattr(cli, "write_xml", lambda pairs, out: out, raising=False)
    return calls


class TestCliWriteDbGuards:
    def test_db9_refuses_when_rekordbox_is_running(self, monkeypatch, capsys, tmp_path):
        """DB-9: Rekordbox open = SQLCipher lock. Refuse, exit non-zero, ZERO writes,
        and do NOT even take a backup."""
        from autocue.cli import main
        calls = _install_db_cli_stubs(monkeypatch, tmp_path, loops=[_loop(10_000, 18_000)], rb_running=True)
        monkeypatch.setattr(sys, "argv",
                            ["autocue", "--track-id", "1", "--loops", "--write-db"])

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code != 0
        assert calls.writes == [], "a write was attempted with Rekordbox running"
        assert calls.backups == [], "a backup was taken on a path that must abort first"
        err = capsys.readouterr().err.lower()
        assert "rekordbox" in err

    def test_db11_refuses_when_autocue_serve_holds_the_db(self, monkeypatch, capsys, tmp_path):
        """DB-11: single-writer rule. rekordbox_is_running does NOT see `autocue serve`,
        which holds its own read-write handle — a concurrent write corrupts the DB."""
        from autocue.cli import main
        calls = _install_db_cli_stubs(monkeypatch, tmp_path, loops=[_loop(10_000, 18_000)],
                                      rb_running=False, serve_running=True)
        monkeypatch.setattr(sys, "argv",
                            ["autocue", "--track-id", "1", "--loops", "--write-db"])

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code != 0
        assert calls.writes == [], "a write proceeded while `autocue serve` held the DB"
        out = capsys.readouterr()
        assert "serve" in (out.err + out.out).lower()

    def test_db10a_backup_is_taken_BEFORE_any_insert(self, monkeypatch, capsys, tmp_path):
        """DB-10a: the backup is the user's ONLY undo on this path — it must land first."""
        from autocue.cli import main
        order: list[str] = []
        calls = _install_db_cli_stubs(monkeypatch, tmp_path, loops=[_loop(10_000, 18_000)], order=order)
        monkeypatch.setattr(sys, "argv",
                            ["autocue", "--track-id", "1", "--loops", "--write-db"])

        main()
        assert "backup" in order and "write" in order, "expected both a backup and a write"
        assert order.index("backup") < order.index("write"), \
            "a row was written BEFORE the backup was taken"
        assert calls.writes == [1]

    def test_db10c_backup_path_is_printed(self, monkeypatch, capsys, tmp_path):
        """DB-10c: print the backup path — it is the user's only undo."""
        from autocue.cli import main
        _install_db_cli_stubs(monkeypatch, tmp_path, loops=[_loop(10_000, 18_000)])
        monkeypatch.setattr(sys, "argv",
                            ["autocue", "--track-id", "1", "--loops", "--write-db"])
        main()
        out = capsys.readouterr().out
        assert "master_20260711T000000.db" in out or "backup" in out.lower(), \
            "the backup path must be surfaced to the user (their only undo)"

    def test_db10b_backup_failure_ABORTS_with_zero_writes(self, monkeypatch, capsys, tmp_path):
        """DB-10b: never write without a successful backup. A swallowed backup error
        that lets the write proceed is the worst possible bug on this path."""
        from autocue.cli import main
        calls = _install_db_cli_stubs(monkeypatch, tmp_path, loops=[_loop(10_000, 18_000)],
                                      backup_raises=True)
        monkeypatch.setattr(sys, "argv",
                            ["autocue", "--track-id", "1", "--loops", "--write-db"])

        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code != 0, "a failed backup must abort with a non-zero exit"
        assert calls.writes == [], "WROTE TO THE DB AFTER THE BACKUP FAILED"
        err = capsys.readouterr().err.lower()
        assert "backup" in err

    def test_db12_dry_run_writes_nothing(self, monkeypatch, capsys, tmp_path):
        """DB-12: dry-run safety on a MUTATING path."""
        from autocue.cli import main
        calls = _install_db_cli_stubs(monkeypatch, tmp_path, loops=[_loop(10_000, 18_000)])
        monkeypatch.setattr(
            sys, "argv",
            ["autocue", "--track-id", "1", "--loops", "--write-db", "--dry-run"],
        )
        main()
        assert calls.writes == [], "--dry-run wrote to the database"
        assert calls.backups == [], "--dry-run took a backup (nothing is being written)"
        assert "Dry run" in capsys.readouterr().out

    def test_db13_write_db_without_loops_makes_no_db_write(self, monkeypatch, capsys, tmp_path):
        """DB-13: --write-db is loops-only scope. It must never become a backdoor to
        writing CUES to the DB (a much larger scope, explicitly NOT this increment).
        Rejected-with-exit OR documented no-op — either is fine; ZERO writes is not."""
        from autocue.cli import main
        calls = _install_db_cli_stubs(monkeypatch, tmp_path, loops=[_loop(10_000, 18_000)])
        monkeypatch.setattr(sys, "argv", ["autocue", "--track-id", "1", "--write-db"])

        try:
            main()
        except SystemExit as exc:
            assert exc.code != 0, "if --write-db without --loops exits, it must be non-zero"
        assert calls.writes == [], "--write-db without --loops wrote to the database"
