"""Tests for autocue.analysis.comment — length guard, undo helper, sentinel handling.

The comment enrichment is also exercised end-to-end via tests/test_serve_routes.py;
these tests focus on the pure logic.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from autocue.analysis import comment as _c


def _content(commnt: str = "", *, title: str = "Track", bpm_x100: int = 12800,
             key_name: str = "8A") -> SimpleNamespace:
    """Build a fake DjmdContent row sufficient for build_comment_string / enrich_comment."""
    return SimpleNamespace(
        ID=1, Title=title, BPM=bpm_x100,
        Key=SimpleNamespace(ScaleName=key_name),
        Commnt=commnt,
    )


def _patch_classification(primary="peak", energy_mean=0.7):
    cls = {"primary": primary, "energy_mean": energy_mean,
           "scores": {primary: 0.9}, "bpm": 128.0,
           "label": primary.title(), "color": "#fff"}
    return patch("autocue.analysis.comment.get_classification", return_value=cls)


def _patch_intro_bars(value):
    return patch("autocue.analysis.comment._intro_bars", return_value=value)


# ---------------------------------------------------------------------------
# Length guard
# ---------------------------------------------------------------------------

class TestLengthGuard:
    def test_short_user_text_fits_full_enrichment(self):
        with _patch_classification(), _patch_intro_bars(8):
            c = _content("short user text")
            new = _c.enrich_comment(c, db=MagicMock(), overwrite=False)
        assert new is not None
        assert "short user text" in new
        assert "/* AutoCue:" in new
        assert "Peak" in new
        assert "8 bar intro" in new
        assert len(new) <= _c.MAX_COMMENT_LEN

    def test_long_user_text_drops_intro_first(self):
        # 220-char user text: full enrichment would push to 269 (over 256);
        # dropping just the "8 bar intro" part brings it to ~255 — fits.
        padding = "x" * 220
        with _patch_classification(), _patch_intro_bars(8):
            c = _content(padding)
            new = _c.enrich_comment(c, db=MagicMock(), overwrite=False)
        assert new is not None
        assert padding in new                    # user text preserved
        assert "/* AutoCue:" in new
        assert "8 bar intro" not in new          # least-essential part dropped
        assert "Peak" in new                     # category kept
        assert len(new) <= _c.MAX_COMMENT_LEN

    def test_very_long_user_text_drops_category_too(self):
        # 225-char user text: dropping intro alone still over; category also drops.
        padding = "y" * 225
        with _patch_classification(), _patch_intro_bars(8):
            c = _content(padding)
            new = _c.enrich_comment(c, db=MagicMock(), overwrite=False)
        assert new is not None
        assert padding in new
        assert "8 bar intro" not in new
        assert "Peak" not in new                 # category also dropped
        assert "8A - Energy" in new              # key+energy retained
        assert len(new) <= _c.MAX_COMMENT_LEN

    def test_user_text_alone_over_cap_skips_enrichment(self):
        # Even the minimum AutoCue suffix can't fit — skip rather than truncate user text.
        padding = "z" * 250
        with _patch_classification(), _patch_intro_bars(8):
            c = _content(padding)
            new = _c.enrich_comment(c, db=MagicMock(), overwrite=False)
        assert new is None

    def test_overwrite_truncates_autocue_parts_to_fit(self):
        # In overwrite mode, just trim AutoCue parts (no user text to preserve)
        with _patch_classification(), _patch_intro_bars(8):
            c = _content("legacy text gets replaced")
            new = _c.enrich_comment(c, db=MagicMock(), overwrite=True)
        assert new is not None
        assert "legacy text" not in new
        assert "8A" in new
        assert len(new) <= _c.MAX_COMMENT_LEN


# ---------------------------------------------------------------------------
# Sentinel handling
# ---------------------------------------------------------------------------

class TestSentinelHandling:
    def test_existing_sentinel_replaced_not_duplicated(self):
        existing = "my notes /* AutoCue: 5A - Energy 5 | Build | 16 bar intro */"
        with _patch_classification(primary="peak", energy_mean=0.7), _patch_intro_bars(8):
            c = _content(existing)
            new = _c.enrich_comment(c, db=MagicMock(), overwrite=False)
        assert new is not None
        assert "my notes" in new
        assert new.count("/* AutoCue:") == 1     # one sentinel block, not two
        assert "Build" not in new                # old enrichment removed
        assert "Peak" in new                     # new enrichment present


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

class TestUndoData:
    def _batch_db(self, contents):
        """Mock pyrekordbox DB with a get_content lookup over the given list."""
        db = MagicMock()
        db.session = MagicMock()
        db._db_dir = None  # skip backup path resolution
        lookup = {int(c.ID): c for c in contents}
        db.get_content = lambda ID: lookup.get(int(ID))
        return db

    def test_undo_data_captures_previous_comments(self):
        c1 = _content("original note 1", title="A"); c1.ID = 11
        c2 = _content("", title="B"); c2.ID = 12
        db = self._batch_db([c1, c2])
        with _patch_classification(), _patch_intro_bars(8):
            result = _c.enrich_comments_batch([11, 12], db)
        # Two enriched → two undo rows with the original text
        modified = result["undo_data"]["modified"]
        assert len(modified) == 2
        previous_by_id = {r["content_id"]: r["previous"] for r in modified}
        assert previous_by_id["11"] == "original note 1"
        assert previous_by_id["12"] == ""

    def test_dry_run_yields_empty_undo_data(self):
        c = _content("anything"); c.ID = 7
        db = self._batch_db([c])
        with _patch_classification(), _patch_intro_bars(8):
            result = _c.enrich_comments_batch([7], db, dry_run=True)
        assert result["undo_data"]["modified"] == []

    def test_restore_comments_puts_previous_text_back(self):
        c = _content("ENRICHED VALUE"); c.ID = 42
        db = self._batch_db([c])
        undo = {"modified": [{"content_id": "42", "previous": "original verbatim"}]}
        result = _c.restore_comments(db, undo)
        assert result == {"restored": 1, "skipped": 0, "errors": 0}
        assert c.Commnt == "original verbatim"
        db.session.commit.assert_called_once()

    def test_restore_skips_deleted_content_id(self):
        db = self._batch_db([])  # empty lookup
        undo = {"modified": [{"content_id": "999", "previous": "anything"}]}
        result = _c.restore_comments(db, undo)
        assert result == {"restored": 0, "skipped": 1, "errors": 0}

    def test_restore_empty_undo_returns_zeros(self):
        assert _c.restore_comments(MagicMock(), {}) == {"restored": 0, "skipped": 0, "errors": 0}
        assert _c.restore_comments(MagicMock(), {"modified": []}) == {"restored": 0, "skipped": 0, "errors": 0}
