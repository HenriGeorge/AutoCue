"""Integration test for delete_tracks against a real SQLite session.

The MagicMock-based unit tests in test_serve_routes.py never exercise an
actual database — the cascade list could (and did) drop entire tables
without any test reporting a failure. This file builds a fresh
in-memory SQLite, applies the full pyrekordbox Rekordbox6 schema, seeds
one row in EVERY ContentID-bearing child table, calls
:func:`delete_tracks`, and asserts that every child row is gone.

If pyrekordbox adds a new ContentID-bearing table in a future release,
``test_every_content_id_table_is_in_the_cascade`` introspects the schema
and fails — pinning the cascade list to the schema, not to our memory
of it.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pyrekordbox.db6 import tables as t


def _has_content_id(model_cls) -> bool:
    try:
        return "ContentID" in [c.name for c in model_cls.__table__.columns]
    except Exception:
        return False


def _content_id_tables():
    """Every pyrekordbox model with a ContentID column."""
    return [
        getattr(t, name)
        for name in dir(t)
        if isinstance(getattr(t, name, None), type)
        and hasattr(getattr(t, name), "__table__")
        and _has_content_id(getattr(t, name))
    ]


@pytest.fixture
def real_db():
    """An in-memory SQLite session with the Rekordbox6 schema applied."""
    engine = create_engine("sqlite:///:memory:")
    t.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # delete_tracks() expects a thin wrapper that mimics
    # Rekordbox6Database.get_content / .delete / .session.
    db = MagicMock()
    db.session = session

    def get_content(*, ID=None):
        if ID is None:
            return None
        return session.query(t.DjmdContent).filter(
            t.DjmdContent.ID == str(ID)
        ).first()

    db.get_content.side_effect = get_content
    db.delete.side_effect = lambda inst: session.delete(inst)

    yield db, session

    session.close()
    engine.dispose()


def _default_value(col) -> object:
    """Pick a sensible default for a NOT NULL column.

    pyrekordbox's DjmdContent has 78 NOT NULL columns — too many to
    hand-set per test. We fill by SQL type so the INSERT succeeds. The
    dedup feature never reads these — they're noise.

    Also handles pyrekordbox's `created_at` / `updated_at` columns,
    which have a custom `DateTime` type validator that rejects strings.
    """
    import datetime as _dt

    type_name = str(col.type).upper()
    # pyrekordbox uses a custom DateTime type on created_at/updated_at;
    # the python_type isn't a SQL string. The validator wants a datetime.
    if "DATETIME" in type_name or col.name in ("created_at", "updated_at"):
        return _dt.datetime.now(_dt.timezone.utc)
    if any(s in type_name for s in ("VARCHAR", "TEXT", "STRING")):
        return ""
    if "FLOAT" in type_name or "REAL" in type_name or "DOUBLE" in type_name:
        return 0.0
    return 0


def _construct(model_cls, **overrides):
    """Build a row with every NOT NULL column defaulted, then apply
    overrides on top. Skips relationship-side columns."""
    row = model_cls()
    for c in model_cls.__table__.columns:
        if not c.nullable and c.name not in overrides:
            setattr(row, c.name, _default_value(c))
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


def _seed_content_row(session, track_id: int, title: str = "Test"):
    """Insert one DjmdContent + one child row in EVERY ContentID-bearing
    table. Returns the content's ID.

    Defaults every NOT NULL column to empty/zero — the dedup feature
    never reads these and the test only cares about ContentID + the
    cascade itself.
    """
    content = _construct(t.DjmdContent, ID=str(track_id), Title=title)
    session.add(content)
    session.flush()

    for child in _content_id_tables():
        if child is t.DjmdContent:
            continue
        row = _construct(
            child,
            ID=f"{child.__tablename__}-{track_id}",
            ContentID=str(track_id),
        )
        session.add(row)
    session.flush()
    return content


class TestDeleteTracksCascade:
    def test_every_content_id_table_is_in_the_cascade(self, real_db):
        """Schema-pinned regression — if pyrekordbox adds a new
        ContentID table, this fails until db_writer.delete_tracks
        is updated to drop the child rows too."""
        from autocue.db_writer import delete_tracks

        db, session = real_db
        _seed_content_row(session, 1)
        session.commit()

        # Sanity — every ContentID table has 1 row before the delete.
        for child in _content_id_tables():
            if child is t.DjmdContent:
                continue
            n = session.query(child).filter(
                child.ContentID == "1"
            ).count()
            assert n == 1, f"seed failed for {child.__tablename__}"

        delete_tracks(db, [1], dry_run=False)

        # After the delete: every ContentID table is empty for this id.
        leaked = []
        for child in _content_id_tables():
            if child is t.DjmdContent:
                continue
            n = session.query(child).filter(
                child.ContentID == "1"
            ).count()
            if n != 0:
                leaked.append((child.__tablename__, n))
        assert leaked == [], (
            f"delete_tracks left orphan rows in {len(leaked)} tables: {leaked}. "
            f"Add them to the cascade in autocue/db_writer.py::delete_tracks."
        )

        # And the DjmdContent row itself is gone.
        assert session.query(t.DjmdContent).filter(
            t.DjmdContent.ID == "1"
        ).first() is None

    def test_unrelated_tracks_are_not_touched(self, real_db):
        """Deleting track 1 must leave track 2's child rows alone — the
        WHERE clause must be keyed on ContentID, not stripped."""
        from autocue.db_writer import delete_tracks

        db, session = real_db
        _seed_content_row(session, 1)
        _seed_content_row(session, 2)
        session.commit()

        delete_tracks(db, [1], dry_run=False)

        # Track 2's content row + every child row survive.
        assert session.query(t.DjmdContent).filter(
            t.DjmdContent.ID == "2"
        ).first() is not None
        for child in _content_id_tables():
            if child is t.DjmdContent:
                continue
            n = session.query(child).filter(
                child.ContentID == "2"
            ).count()
            assert n == 1, (
                f"delete_tracks(1) collateral-damaged {child.__tablename__} "
                f"for unrelated track 2"
            )

    def test_dry_run_leaves_everything_in_place(self, real_db):
        from autocue.db_writer import delete_tracks

        db, session = real_db
        _seed_content_row(session, 1)
        session.commit()

        result = delete_tracks(db, [1], dry_run=True)
        assert result["deleted"] == 1
        assert result["dry_run"] is True

        # No state changed.
        assert session.query(t.DjmdContent).filter(
            t.DjmdContent.ID == "1"
        ).first() is not None
        for child in _content_id_tables():
            if child is t.DjmdContent:
                continue
            n = session.query(child).filter(
                child.ContentID == "1"
            ).count()
            assert n == 1


class TestDeleteTracksCancellation:
    """Phase 3 WS4 — the cancel Event + progress_cb hooks on delete_tracks."""

    def test_cancel_event_stops_mid_batch(self, real_db):
        """Set the cancel Event from inside progress_cb after 2 rows —
        the remaining rows must survive, and the result must carry
        cancelled=True with the partial counts."""
        import threading
        from autocue.db_writer import delete_tracks

        db, session = real_db
        for tid in (1, 2, 3, 4, 5):
            _seed_content_row(session, tid)
        session.commit()

        cancel = threading.Event()

        def progress(processed, deleted, skipped):
            if processed >= 2:
                cancel.set()

        result = delete_tracks(
            db, [1, 2, 3, 4, 5], dry_run=False,
            cancel=cancel, progress_cb=progress,
        )

        assert result["cancelled"] is True
        assert result["deleted"] == 2
        # Rows 3-5 survive.
        for tid in ("3", "4", "5"):
            assert session.query(t.DjmdContent).filter(
                t.DjmdContent.ID == tid
            ).first() is not None, f"track {tid} should have survived the cancel"
        # Rows 1-2 are gone AND committed (partial progress durable).
        for tid in ("1", "2"):
            assert session.query(t.DjmdContent).filter(
                t.DjmdContent.ID == tid
            ).first() is None

    def test_progress_cb_called_per_row(self, real_db):
        from autocue.db_writer import delete_tracks

        db, session = real_db
        for tid in (1, 2, 3):
            _seed_content_row(session, tid)
        session.commit()

        calls = []
        delete_tracks(
            db, [1, 2, 3], dry_run=True,
            progress_cb=lambda p, d, s: calls.append((p, d, s)),
        )
        assert calls == [(1, 1, 0), (2, 2, 0), (3, 3, 0)]

    def test_cancel_before_first_row_deletes_nothing(self, real_db):
        import threading
        from autocue.db_writer import delete_tracks

        db, session = real_db
        _seed_content_row(session, 1)
        session.commit()

        cancel = threading.Event()
        cancel.set()  # already cancelled before the call

        result = delete_tracks(db, [1], dry_run=False, cancel=cancel)
        assert result["cancelled"] is True
        assert result["deleted"] == 0
        assert session.query(t.DjmdContent).filter(
            t.DjmdContent.ID == "1"
        ).first() is not None
