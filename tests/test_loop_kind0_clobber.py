"""Kind=0 clobber safety — memory CUES and memory LOOPS must not be conflated.

⚠️ THE INVARIANT
Memory cues and memory loops SHARE the DjmdCue ``Kind=0`` space. The ONLY
discriminator is ``OutMsec``:

    point cue :  OutMsec is NULL / -1 / <= InMsec
    loop      :  OutMsec > InMsec

Any blanket ``Kind == 0`` DELETE or COUNT therefore conflates two different
object classes, and the consequences are destructive:

  A. ``write_memory_loops(overwrite=True)`` blanket-deleted Kind=0 → wiped the
     DJ's hand-placed memory CUES library-wide on `--library --loops --overwrite`.
  B. ``write_cues_to_db(overwrite=True)`` blanket-deleted Kind=0 → wiped the
     loops AutoCue itself had just generated (any Apply with memory_cue_mode).
  C. ``has_existing_memory_cues()`` blanket-counted Kind=0 → silently SUPPRESSED
     writes in BOTH directions (a track with only loops stopped getting memory
     cues; a track with only memory cues never got its loops).

These tests run against a SCRATCH in-memory SQLite with the real pyrekordbox
schema — NEVER the live master.db.
"""
from __future__ import annotations

import datetime as _dt

import pytest
from unittest.mock import MagicMock

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from pyrekordbox.db6 import tables as t

from autocue.models import CuePoint, PhraseLabel


# ---------------------------------------------------------------------------
# Scratch DB (tests/test_duplicates_integration.py fixture pattern)
# ---------------------------------------------------------------------------

def _default_value(col):
    name = str(col.type).upper()
    if "DATETIME" in name or col.name in ("created_at", "updated_at"):
        return _dt.datetime.now(_dt.timezone.utc)
    if any(s in name for s in ("VARCHAR", "TEXT", "STRING")):
        return ""
    if any(s in name for s in ("FLOAT", "REAL", "DOUBLE")):
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


class _Scratch:
    def __init__(self, db, session, engine, statements):
        self.db, self.session, self.engine = db, session, engine
        self.statements = statements


@pytest.fixture
def scratch():
    engine = create_engine("sqlite:///:memory:")

    # pyrekordbox marks these NOT NULL with no default, but the shipped writers
    # omit them (the real master.db tolerates it). Relax for the scratch DDL only,
    # then restore — every column a writer MUST set stays NOT NULL, so an omission
    # still fails loudly.
    relaxed = [
        t.DjmdCue.__table__.columns[n]
        for n in ("InPointSeekInfo", "OutPointSeekInfo", "usn", "rb_local_usn")
    ]
    for c in relaxed:
        c.nullable = True
    try:
        t.Base.metadata.create_all(engine)
    finally:
        for c in relaxed:
            c.nullable = False

    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    session = sessionmaker(bind=engine)()

    db = MagicMock()
    db.session = session
    # has_existing_* call db.query (NOT db.session.query) — a bare MagicMock makes
    # `.count() == 0` silently False and the write gates untestable.
    db.query.side_effect = session.query
    counter = {"n": 7000}

    def _gen(_model):
        counter["n"] += 1
        return counter["n"]

    # ⚠️ Without this the writers insert ID=<MagicMock> and every assertion lies.
    db.generate_unused_id.side_effect = _gen

    yield _Scratch(db, session, engine, statements)
    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

CID = "1"


def _track(session):
    session.add(_construct(t.DjmdContent, ID=CID, Title="Track", UUID="content-uuid"))
    session.commit()
    return session.query(t.DjmdContent).filter(t.DjmdContent.ID == CID).first()


def _point_cue_row(session, row_id, in_ms, comment):
    """A DJ hand-placed memory CUE: Kind=0 with the OutMsec=-1 point sentinel."""
    session.add(_construct(
        t.DjmdCue, ID=row_id, ContentID=CID, UUID=f"u-{row_id}", Kind=0,
        InMsec=in_ms, InFrame=int(in_ms * 0.15), OutMsec=-1, OutFrame=0,
        ActiveLoop=0, BeatLoopSize=0, Comment=comment,
    ))
    session.commit()


def _loop_row(session, row_id, in_ms, out_ms, comment):
    """A saved memory LOOP: Kind=0 with a real out-point (OutMsec > InMsec)."""
    session.add(_construct(
        t.DjmdCue, ID=row_id, ContentID=CID, UUID=f"u-{row_id}", Kind=0,
        InMsec=in_ms, InFrame=int(in_ms * 0.15), OutMsec=out_ms,
        OutFrame=int(out_ms * 0.15), ActiveLoop=0, BeatLoopSize=0, Comment=comment,
    ))
    session.commit()


def _kind0(session):
    return (
        session.query(t.DjmdCue)
        .filter(t.DjmdCue.ContentID == CID, t.DjmdCue.Kind == 0)
        .order_by(t.DjmdCue.InMsec)
        .all()
    )


def _snap(r):
    return (r.ID, r.UUID, r.Kind, r.InMsec, r.OutMsec, r.OutFrame, r.Comment)


def _is_loop(r):
    return r.OutMsec is not None and r.OutMsec > r.InMsec


def _mem_cue(pos, name="Load Point"):
    return CuePoint(position_ms=pos, label=PhraseLabel.UNKNOWN, slot=-1, name=name)


def _loop(start, end, name="Mix In Loop"):
    return {"start_ms": start, "end_ms": end, "name": name}


# ===========================================================================
# BUG A — the DJ's memory CUES must survive a LOOP rewrite
# ===========================================================================

class TestMemoryCuesSurviveLoopOverwrite:
    def test_dj_memory_cues_survive_write_memory_loops_overwrite(self, scratch):
        """★ `autocue --library --loops --overwrite` must NOT wipe the DJ's
        hand-placed memory cues. write_memory_loops(overwrite=True) may replace
        LOOP rows — never point cues."""
        from autocue.db_writer import write_memory_loops
        s = scratch.session
        content = _track(s)
        _point_cue_row(s, "900", 5_000, "DJ Load Point")
        _point_cue_row(s, "901", 90_000, "DJ Mix Out")
        _loop_row(s, "902", 30_000, 45_000, "Old Loop")
        before = {r.ID: _snap(r) for r in _kind0(s) if not _is_loop(r)}
        assert len(before) == 2

        write_memory_loops(content, [_loop(60_000, 75_000, "New Loop")],
                           scratch.db, overwrite=True)

        after = {r.ID: _snap(r) for r in _kind0(s)}
        for rid, snap in before.items():
            assert rid in after, f"DJ memory cue {rid} was DELETED — clobber!"
            assert after[rid] == snap, f"DJ memory cue {rid} was MUTATED"
        # the stale LOOP was replaced (that IS what overwrite means)
        comments = {r.Comment for r in _kind0(s)}
        assert "New Loop" in comments and "Old Loop" not in comments


# ===========================================================================
# BUG B — generated memory LOOPS must survive a memory-CUE rewrite
# ===========================================================================

class TestLoopsSurviveCueOverwrite:
    def test_generated_loops_survive_write_cues_to_db_overwrite(self, scratch):
        """★ An Apply / generate-apply with memory_cue_mode + overwrite must NOT
        wipe the loops AutoCue itself just generated."""
        from autocue.db_writer import write_cues_to_db
        s = scratch.session
        content = _track(s)
        _loop_row(s, "910", 30_000, 45_000, "Mix In Loop")
        _loop_row(s, "911", 200_000, 215_000, "Mix Out Loop")
        _point_cue_row(s, "912", 1_000, "Old Memory Cue")
        before = {r.ID: _snap(r) for r in _kind0(s) if _is_loop(r)}
        assert len(before) == 2

        write_cues_to_db(content, [_mem_cue(2_000, "New Memory Cue")],
                         scratch.db, overwrite=True)

        after = {r.ID: _snap(r) for r in _kind0(s)}
        for rid, snap in before.items():
            assert rid in after, f"generated loop {rid} was DELETED — clobber!"
            assert after[rid] == snap, f"generated loop {rid} was MUTATED"
        # the memory POINT cue set was rewritten (that IS what overwrite means)
        comments = {r.Comment for r in _kind0(s)}
        assert "New Memory Cue" in comments and "Old Memory Cue" not in comments


# ===========================================================================
# BUG C — the conflated COUNT silently suppressed writes in BOTH directions
# ===========================================================================

class TestNoSilentSuppression:
    def test_track_with_only_loops_still_gets_memory_cues(self, scratch):
        """A track whose only Kind=0 rows are LOOPS has NO memory cues — the
        memory-cue write must proceed (it was silently skipped)."""
        from autocue.db_writer import write_cues_to_db
        s = scratch.session
        content = _track(s)
        _loop_row(s, "920", 30_000, 45_000, "Mix In Loop")

        n = write_cues_to_db(content, [_mem_cue(1_000, "Load Point")],
                             scratch.db, overwrite=False)

        assert n == 1, "the memory cue was silently suppressed by the loop"
        comments = {r.Comment for r in _kind0(s)}
        assert "Load Point" in comments      # written
        assert "Mix In Loop" in comments     # and the loop survived

    def test_track_with_only_memory_cues_still_gets_loops(self, scratch):
        """A track whose only Kind=0 rows are POINT CUES has NO loops — the loop
        write must proceed (it was silently skipped), and must not touch the cues."""
        from autocue.db_writer import write_memory_loops
        s = scratch.session
        content = _track(s)
        _point_cue_row(s, "930", 5_000, "DJ Load Point")

        n = write_memory_loops(content, [_loop(30_000, 45_000, "Mix In Loop")],
                               scratch.db, overwrite=False)

        assert n == 1, "the loop was silently suppressed by the DJ's memory cue"
        rows = _kind0(s)
        assert any(r.Comment == "Mix In Loop" and _is_loop(r) for r in rows)
        assert any(r.Comment == "DJ Load Point" and not _is_loop(r) for r in rows)


class TestIntendedProtectionPreserved:
    """The conflation fix must NOT weaken the real protection each gate exists for."""

    def test_existing_loops_still_block_a_loop_write_without_overwrite(self, scratch):
        from autocue.db_writer import write_memory_loops
        s = scratch.session
        content = _track(s)
        _loop_row(s, "940", 30_000, 45_000, "DJ's Own Loop")
        n = write_memory_loops(content, [_loop(60_000, 75_000)], scratch.db,
                               overwrite=False)
        assert n == 0                                     # skipped, not clobbered
        assert [r.Comment for r in _kind0(s)] == ["DJ's Own Loop"]

    def test_existing_memory_cues_still_block_a_cue_write_without_overwrite(self, scratch):
        from autocue.db_writer import write_cues_to_db
        s = scratch.session
        content = _track(s)
        _point_cue_row(s, "950", 5_000, "DJ Load Point")
        write_cues_to_db(content, [_mem_cue(1_000, "Generated")], scratch.db,
                         overwrite=False)
        comments = {r.Comment for r in _kind0(s)}
        assert "DJ Load Point" in comments                # never destroyed
        assert "Generated" not in comments                # and not silently added


# ===========================================================================
# The discriminator itself
# ===========================================================================

class TestCounters:
    def test_memory_cue_count_ignores_loops(self, scratch):
        from autocue.db_writer import has_existing_memory_cues
        s = scratch.session
        content = _track(s)
        _loop_row(s, "960", 30_000, 45_000, "Loop")
        assert has_existing_memory_cues(content, scratch.db) == 0
        _point_cue_row(s, "961", 5_000, "Cue")
        assert has_existing_memory_cues(content, scratch.db) == 1

    def test_memory_loop_count_ignores_point_cues(self, scratch):
        from autocue.db_writer import has_existing_memory_loops
        s = scratch.session
        content = _track(s)
        _point_cue_row(s, "970", 5_000, "Cue")
        assert has_existing_memory_loops(content, scratch.db) == 0
        _loop_row(s, "971", 30_000, 45_000, "Loop")
        assert has_existing_memory_loops(content, scratch.db) == 1


# ===========================================================================
# The structural guarantee — stronger than any row assertion
# ===========================================================================

class TestNoBlanketKind0Delete:
    def test_no_delete_filters_kind0_without_the_outmsec_discriminator(self, scratch):
        """Catches a blanket Kind=0 DELETE even when it happens to match 0 rows in
        the fixture (which would still clobber in the wild). Every DELETE on
        djmdCue that constrains Kind == 0 MUST also constrain OutMsec."""
        from autocue.db_writer import write_cues_to_db, write_memory_loops
        s = scratch.session
        content = _track(s)
        _point_cue_row(s, "980", 5_000, "DJ Cue")
        _loop_row(s, "981", 30_000, 45_000, "DJ Loop")

        scratch.statements.clear()
        write_memory_loops(content, [_loop(60_000, 75_000)], scratch.db, overwrite=True)
        write_cues_to_db(content, [_mem_cue(1_000)], scratch.db, overwrite=True)

        deletes = [st for st in scratch.statements
                   if st.lstrip().upper().startswith("DELETE") and "djmdCue" in st]
        assert deletes, "anti-vacuous: the writers must have issued DELETEs at all"
        for st in deletes:
            if '"Kind" = ' in st:                       # a Kind=0 equality delete
                assert "OutMsec" in st, (
                    f"BLANKET Kind=0 DELETE — conflates memory cues and loops:\n{st}"
                )
