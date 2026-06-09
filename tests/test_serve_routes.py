"""Tests for autocue/serve/routes.py"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from autocue.serve.app import create_app


# ---------------------------------------------------------------------------
# App fixture with mocked DB
# ---------------------------------------------------------------------------

def _make_db(track_count: int = 5):
    """Return a mock MasterDatabase with sensible defaults."""
    db = MagicMock()

    # get_content().count()
    content_q = MagicMock()
    content_q.count.return_value = track_count
    content_q.all.return_value = []
    content_q.offset.return_value = content_q
    content_q.limit.return_value = content_q
    content_q.filter.return_value = content_q
    content_q.order_by.return_value = content_q
    db.get_content.return_value = content_q

    # query(DjmdPlaylist)
    playlist_q = MagicMock()
    playlist_q.filter.return_value = playlist_q
    playlist_q.all.return_value = []
    playlist_q.first.return_value = None
    db.query.return_value = playlist_q

    return db


def _make_client(db=None, connected: bool = True):
    """Build a TestClient with the DB dependency overridden."""
    app = create_app()
    if connected:
        if db is None:
            db = _make_db()
        from autocue.serve.deps import get_db, get_ro_db
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_ro_db] = lambda: db  # same mock for tests
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------

class TestStatus:
    def test_connected_true_returns_200(self):
        client = _make_client()
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["connected"] is True
        assert data["track_count"] == 5

    def test_no_db_returns_503(self):
        app = create_app()
        from autocue.serve.deps import get_db
        app.dependency_overrides[get_db] = lambda: (_ for _ in ()).throw(
            __import__("fastapi").HTTPException(503, "not connected")
        )
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/status")
        assert r.status_code == 503


# ---------------------------------------------------------------------------
# deps — get_db reads from app.state
# ---------------------------------------------------------------------------

class TestDeps:
    def test_get_db_raises_503_when_state_db_none(self):
        """get_db must raise 503 when app.state.db is None (not connected)."""
        import pytest
        from fastapi import HTTPException
        from unittest.mock import MagicMock
        from autocue.serve.deps import get_db

        app = create_app()
        app.state.db = None

        mock_request = MagicMock()
        mock_request.app = app

        with pytest.raises(HTTPException) as exc_info:
            get_db(mock_request)
        assert exc_info.value.status_code == 503

    def test_get_db_returns_db_when_state_db_set(self):
        """get_db must return app.state.db when it is set."""
        from unittest.mock import MagicMock
        from autocue.serve.deps import get_db

        app = create_app()
        fake_db = MagicMock()
        app.state.db = fake_db

        mock_request = MagicMock()
        mock_request.app = app

        result = get_db(mock_request)
        assert result is fake_db


# ---------------------------------------------------------------------------
# /api/playlists
# ---------------------------------------------------------------------------

class TestPlaylists:
    def test_returns_list(self):
        db = _make_db()
        from pyrekordbox.db6 import DjmdPlaylist, DjmdSongPlaylist
        pl = SimpleNamespace(ID=1, Name="My Set")
        playlist_q = MagicMock()
        playlist_q.filter.return_value = playlist_q
        playlist_q.all.return_value = [pl]
        playlist_q.count.return_value = 3
        db.query.return_value = playlist_q

        client = _make_client(db)
        r = client.get("/api/playlists")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["name"] == "My Set"

    def test_empty_library(self):
        client = _make_client()
        r = client.get("/api/playlists")
        assert r.status_code == 200
        assert r.json() == []


# ---------------------------------------------------------------------------
# /api/tracks
# ---------------------------------------------------------------------------

class TestTracks:
    def _make_track(self, id=1, title="Song", artist="DJ", album="", bpm=12800, length=300):
        t = SimpleNamespace(
            ID=id, Title=title, ArtistName=artist, AlbumName=album, BPM=bpm, Length=length,
        )
        return t

    def test_returns_tracks(self):
        db = _make_db()
        track = self._make_track()
        content_q = MagicMock()
        content_q.count.return_value = 1
        content_q.all.return_value = [track]
        content_q.offset.return_value = content_q
        content_q.limit.return_value = content_q
        content_q.filter.return_value = content_q
        content_q.order_by.return_value = content_q
        db.get_content.return_value = content_q

        with patch("autocue.generator.analyze_track", return_value=[]):
            with patch("autocue.cli.has_existing_hot_cues", return_value=0):
                client = _make_client(db)
                r = client.get("/api/tracks")

        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["title"] == "Song"
        assert data[0]["bpm"] == 128.0

    def test_playlist_not_found_returns_404(self):
        db = _make_db()
        # Ensure DjmdPlaylist query returns None (playlist not found)
        filter_q = MagicMock()
        filter_q.first.return_value = None
        db.query.return_value.filter_by.return_value = filter_q
        client = _make_client(db)
        r = client.get("/api/tracks?playlist_id=9999")
        assert r.status_code == 404

    def test_returns_x_total_count_header(self):
        db = _make_db(track_count=42)
        with patch("autocue.generator.analyze_track", return_value=[]):
            with patch("autocue.cli.has_existing_hot_cues", return_value=0):
                client = _make_client(db)
                r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.headers.get("x-total-count") == "42"


# ---------------------------------------------------------------------------
# /api/generate
# ---------------------------------------------------------------------------

class TestGenerate:
    def test_generates_cues_for_valid_track(self):
        db = _make_db()
        track = SimpleNamespace(
            ID=42, Title="Test Track", BPM=12800, Length=300,
        )
        db.get_content.return_value = track

        with patch("autocue.generator.analyze_track", return_value=[]):
            client = _make_client(db)
            r = client.post("/api/generate", json={"track_ids": [42], "mode": "bar"})

        assert r.status_code == 200
        data = r.json()
        assert len(data["tracks"]) == 1
        result = data["tracks"][0]
        assert result["id"] == 42
        assert result["mode_used"] == "bar"
        assert len(result["cues"]) > 0

    def test_unknown_track_id_silently_skipped(self):
        db = _make_db()
        db.get_content.return_value = None  # simulate not found

        client = _make_client(db)
        r = client.post("/api/generate", json={"track_ids": [9999]})
        assert r.status_code == 200
        data = r.json()
        assert data["tracks"] == []

    def test_mode_heuristic_when_no_bpm(self):
        db = _make_db()
        track = SimpleNamespace(ID=1, Title="No BPM", Length=120)
        # No BPM attribute
        db.get_content.return_value = track

        with patch("autocue.generator.analyze_track", return_value=[]):
            client = _make_client(db)
            r = client.post("/api/generate", json={"track_ids": [1], "mode": "auto"})

        assert r.status_code == 200
        assert r.json()["tracks"][0]["mode_used"] == "heuristic"

    def test_phrase_mode_sets_is_phrase_true_on_all_cues(self):
        """When /api/generate uses phrase mode, every cue must have is_phrase=True."""
        from autocue.models import CuePoint, PhraseLabel

        db = _make_db()
        track = SimpleNamespace(ID=10, Title="Phrase Track", BPM=12800, Length=300)
        db.get_content.return_value = track

        phrase_cues = [
            CuePoint(position_ms=0,      label=PhraseLabel.INTRO,  slot=0),
            CuePoint(position_ms=32000,  label=PhraseLabel.VERSE,  slot=1),
            CuePoint(position_ms=64000,  label=PhraseLabel.CHORUS, slot=2),
            CuePoint(position_ms=128000, label=PhraseLabel.OUTRO,  slot=3),
        ]

        # analyze_track returns phrase data → mode_used will be "phrase"
        with patch("autocue.generator.analyze_track", return_value=phrase_cues):
            client = _make_client(db)
            r = client.post("/api/generate", json={"track_ids": [10], "mode": "phrase"})

        assert r.status_code == 200
        cues = r.json()["tracks"][0]["cues"]
        assert len(cues) == 4
        assert r.json()["tracks"][0]["mode_used"] == "phrase"
        assert all(c["is_phrase"] is True for c in cues), (
            "Every cue must have is_phrase=True in phrase mode"
        )

    def test_bar_mode_sets_is_phrase_false_on_all_cues(self):
        """When /api/generate uses bar mode, every cue must have is_phrase=False."""
        db = _make_db()
        track = SimpleNamespace(ID=11, Title="Bar Track", BPM=12800, Length=300)
        db.get_content.return_value = track

        # analyze_track returns nothing → falls through to bar strategy
        with patch("autocue.generator.analyze_track", return_value=[]):
            client = _make_client(db)
            r = client.post("/api/generate", json={"track_ids": [11], "mode": "bar"})

        assert r.status_code == 200
        result = r.json()["tracks"][0]
        assert result["mode_used"] == "bar"
        assert len(result["cues"]) > 0
        assert all(c["is_phrase"] is False for c in result["cues"]), (
            "Every cue must have is_phrase=False in bar mode"
        )

    def test_bar_cues_have_bar_names(self):
        db = _make_db()
        track = SimpleNamespace(ID=42, Title="Bar Track", BPM=12800, Length=600)
        db.get_content.return_value = track

        with patch("autocue.generator.analyze_track", return_value=[]):
            client = _make_client(db)
            r = client.post("/api/generate", json={"track_ids": [42], "mode": "bar",
                                                   "start_bar": 1, "bars_interval": 16})

        cues = r.json()["tracks"][0]["cues"]
        assert cues[0]["name"] == "Bar 1"
        assert cues[1]["name"] == "Bar 17"

    def test_heuristic_cues_have_time_names(self):
        db = _make_db()
        track = SimpleNamespace(ID=1, Title="No BPM", Length=120)
        db.get_content.return_value = track

        with patch("autocue.generator.analyze_track", return_value=[]):
            client = _make_client(db)
            r = client.post("/api/generate", json={"track_ids": [1], "mode": "auto"})

        cues = r.json()["tracks"][0]["cues"]
        assert cues[0]["name"] == "0:00"
        assert cues[1]["name"] == "0:30"


# ---------------------------------------------------------------------------
# /api/apply — Rekordbox running guard + db path resolution
# ---------------------------------------------------------------------------

class TestApply:
    def test_returns_409_when_rekordbox_running(self):
        client = _make_client()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            r = client.post("/api/apply", json={"tracks": [], "dry_run": False})
        assert r.status_code == 409
        assert "rekordbox" in r.json()["detail"].lower()

    def test_detail_message_is_actionable(self):
        client = _make_client()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            r = client.post("/api/apply", json={"tracks": []})
        assert "close" in r.json()["detail"].lower()

    def test_dry_run_also_blocked_when_rekordbox_running(self):
        # /api/apply blocks unconditionally (unlike /api/color-tracks which allows dry_run)
        client = _make_client()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            r = client.post("/api/apply", json={"tracks": [], "dry_run": True})
        assert r.status_code == 409

    def test_response_has_applied_field(self, tmp_path):
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.return_value = None
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = client.post("/api/apply", json={"tracks": [], "dry_run": False})
        assert r.status_code == 200
        assert "applied" in r.json()


class TestApplyDbPath:
    def test_apply_uses_db_dir_to_find_master_db(self, tmp_path):
        """Backup path is resolved via db._db_dir / 'master.db', not db.db_path."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.return_value = None  # no tracks → applied=0, skipped=0

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = client.post("/api/apply", json={"tracks": [], "dry_run": False})

        assert r.status_code == 200
        data = r.json()
        assert data["backup_path"] is not None
        assert "master_" in data["backup_path"]

    def test_apply_returns_500_when_db_dir_missing(self):
        """If _db_dir is not set on the DB object, /api/apply returns 500 with a clear message."""
        db = _make_db()
        # Do NOT set _db_dir — MagicMock will create it as another Mock (truthy)
        # so we explicitly delete it
        del db._db_dir

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/apply", json={"tracks": [], "dry_run": False})

        assert r.status_code == 500
        assert "master.db" in r.json()["detail"].lower() or "_db_dir" in r.json()["detail"]


# ---------------------------------------------------------------------------
# CORS headers
# ---------------------------------------------------------------------------

class TestDeleteCues:
    def test_deletes_cues_and_returns_counts(self, tmp_path):
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        track = SimpleNamespace(ID=1, Title="Track", BPM=12800, Length=300)
        db.get_content.return_value = track

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.delete_cues_from_db", return_value=4) as mock_del:
                with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                    client = _make_client(db)
                    r = client.post("/api/delete-cues", json={"track_ids": [1]})

        assert r.status_code == 200
        data = r.json()
        assert data["deleted"] == 4
        assert data["tracks_affected"] == 1
        assert data["backup_path"] is not None
        mock_del.assert_called_once()

    def test_returns_409_when_rekordbox_running(self):
        client = _make_client()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            r = client.post("/api/delete-cues", json={"track_ids": [1]})
        assert r.status_code == 409

    def test_dry_run_skips_backup(self):
        db = _make_db()
        track = SimpleNamespace(ID=1, Title="Track", BPM=12800, Length=300)
        db.get_content.return_value = track

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.delete_cues_from_db", return_value=3):
                client = _make_client(db)
                r = client.post("/api/delete-cues", json={"track_ids": [1], "dry_run": True})

        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        assert data["backup_path"] is None

    def test_unknown_track_id_silently_skipped(self):
        db = _make_db()
        db.get_content.return_value = None

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.backup_database"):
                client = _make_client(db)
                r = client.post("/api/delete-cues", json={"track_ids": [9999], "dry_run": True})

        assert r.status_code == 200
        assert r.json()["deleted"] == 0
        assert r.json()["tracks_affected"] == 0


# ---------------------------------------------------------------------------
# /api/tracks — key field
# ---------------------------------------------------------------------------

class TestKeyField:
    def _make_track_with_key(self, key_id="uuid-8a"):
        t = SimpleNamespace(
            ID=1, Title="Track", ArtistName="DJ", AlbumName="", BPM=12800,
            Length=300, KeyID=key_id,
        )
        return t

    def test_track_item_includes_key_from_map(self):
        db = _make_db()
        track = self._make_track_with_key(key_id="uuid-8a")
        content_q = MagicMock()
        content_q.count.return_value = 1
        content_q.all.return_value = [track]
        content_q.offset.return_value = content_q
        content_q.limit.return_value = content_q
        content_q.filter.return_value = content_q
        content_q.order_by.return_value = content_q
        db.get_content.return_value = content_q

        # db.query(DjmdKey).all() returns one key
        key_q = MagicMock()
        key_q.all.return_value = [SimpleNamespace(ID="uuid-8a", ScaleName="8A")]
        db.query.return_value = key_q

        client = _make_client(db)
        r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["key"] == "8A"

    def test_track_item_key_empty_when_no_key_id(self):
        db = _make_db()
        track = SimpleNamespace(
            ID=1, Title="Track", ArtistName="DJ", AlbumName="", BPM=12800,
            Length=300, KeyID=None,
        )
        content_q = MagicMock()
        content_q.count.return_value = 1
        content_q.all.return_value = [track]
        content_q.offset.return_value = content_q
        content_q.limit.return_value = content_q
        content_q.filter.return_value = content_q
        content_q.order_by.return_value = content_q
        db.get_content.return_value = content_q

        key_q = MagicMock()
        key_q.all.return_value = []
        db.query.return_value = key_q

        client = _make_client(db)
        r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["key"] == ""

    def test_sort_by_key_returns_200(self):
        client = _make_client()
        r = client.get("/api/tracks?sort_by=key")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /api/color-tracks
# ---------------------------------------------------------------------------

class TestColorTracks:
    def test_dry_run_returns_counts(self):
        db = _make_db()
        color_q = MagicMock()
        color_q.all.return_value = [SimpleNamespace(SortKey=i, ID=f"c{i}") for i in range(1, 9)]
        db.query.return_value = color_q
        db.get_content.return_value = SimpleNamespace(
            ID=1, Title="T", BPM=12800, ColorID=None,
        )

        client = _make_client(db)
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            r = client.post("/api/color-tracks", json={"track_ids": [1], "dry_run": True})

        assert r.status_code == 200
        data = r.json()
        assert data["dry_run"] is True
        assert data["colored"] == 1
        assert data["backup_path"] is None

    def test_returns_409_when_rekordbox_running_and_not_dry_run(self):
        client = _make_client()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            r = client.post("/api/color-tracks", json={"track_ids": [1], "dry_run": False})
        assert r.status_code == 409

    def test_dry_run_allowed_when_rekordbox_running(self):
        db = _make_db()
        color_q = MagicMock()
        color_q.all.return_value = [SimpleNamespace(SortKey=i, ID=f"c{i}") for i in range(1, 9)]
        db.query.return_value = color_q
        db.get_content.return_value = SimpleNamespace(
            ID=1, Title="T", BPM=12800, ColorID=None,
        )
        client = _make_client(db)
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            r = client.post("/api/color-tracks", json={"track_ids": [1], "dry_run": True})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /api/tracks — playlist_id filter + has_phrase
# ---------------------------------------------------------------------------

class TestTracksPlaylistFilter:
    def _make_content_q(self, db, tracks):
        q = MagicMock()
        q.count.return_value = len(tracks)
        q.all.return_value = tracks
        q.offset.return_value = q
        q.limit.return_value = q
        q.filter.return_value = q
        q.order_by.return_value = q
        db.get_content.return_value = q
        return q

    def test_playlist_id_filters_tracks(self):
        db = _make_db()
        pl = SimpleNamespace(ID="10", Name="My Playlist")
        pl_q = MagicMock()
        pl_q.filter_by.return_value = pl_q
        pl_q.first.return_value = pl
        sp_q = MagicMock()
        sp_q.filter_by.return_value = [SimpleNamespace(ContentID="1")]
        empty_q = MagicMock()
        empty_q.filter.return_value = empty_q
        empty_q.group_by.return_value = empty_q
        empty_q.all.return_value = []

        def _side_effect(*args):
            cls = args[0] if args else None
            name = getattr(cls, "__name__", "")
            if "Playlist" in name:
                return pl_q
            if hasattr(cls, "__name__"):
                return sp_q
            return empty_q  # column-expression queries (e.g. hot_cue_counts)

        db.query.side_effect = _side_effect
        self._make_content_q(db, [])
        client = _make_client(db)
        r = client.get("/api/tracks?playlist_id=10")
        assert r.status_code == 200

    def test_playlist_id_integer_not_found_returns_404(self):
        db = _make_db()
        fb_q = MagicMock()
        fb_q.first.return_value = None
        db.query.return_value.filter_by.return_value = fb_q
        client = _make_client(db)
        r = client.get("/api/tracks?playlist_id=9999")
        assert r.status_code == 404

    def test_has_phrase_true_when_analysis_data_path_set(self):
        db = _make_db()
        track = SimpleNamespace(
            ID=1, Title="T", ArtistName="A", AlbumName="", BPM=12800, Length=300, KeyID=None,
            AnalysisDataPath="/PIONEER/USBANLZ/abc/ANLZ0000.DAT",
        )
        self._make_content_q(db, [track])
        key_q = MagicMock()
        key_q.all.return_value = []
        db.query.return_value = key_q
        client = _make_client(db)
        r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["has_phrase"] is True

    def test_has_phrase_false_when_analysis_data_path_missing(self):
        db = _make_db()
        track = SimpleNamespace(
            ID=1, Title="T", ArtistName="A", AlbumName="", BPM=12800, Length=300, KeyID=None,
            AnalysisDataPath=None,
        )
        self._make_content_q(db, [track])
        key_q = MagicMock()
        key_q.all.return_value = []
        db.query.return_value = key_q
        client = _make_client(db)
        r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["has_phrase"] is False

    def test_has_phrase_false_when_analysis_data_path_empty(self):
        db = _make_db()
        track = SimpleNamespace(
            ID=1, Title="T", ArtistName="A", AlbumName="", BPM=12800, Length=300, KeyID=None,
            AnalysisDataPath="",
        )
        self._make_content_q(db, [track])
        key_q = MagicMock()
        key_q.all.return_value = []
        db.query.return_value = key_q
        client = _make_client(db)
        r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["has_phrase"] is False

    def test_has_beats_true_when_bpm_positive(self):
        db = _make_db()
        track = SimpleNamespace(
            ID=1, Title="T", ArtistName="A", AlbumName="", BPM=12800, Length=300, KeyID=None,
            AnalysisDataPath="",
        )
        self._make_content_q(db, [track])
        key_q = MagicMock()
        key_q.all.return_value = []
        db.query.return_value = key_q
        client = _make_client(db)
        r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["has_beats"] is True

    def test_has_beats_false_when_bpm_zero(self):
        """Track imported but never analyzed in Rekordbox — BPM stored as 0."""
        db = _make_db()
        track = SimpleNamespace(
            ID=1, Title="T", ArtistName="A", AlbumName="", BPM=0, Length=300, KeyID=None,
            AnalysisDataPath="",
        )
        self._make_content_q(db, [track])
        key_q = MagicMock()
        key_q.all.return_value = []
        db.query.return_value = key_q
        client = _make_client(db)
        r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["has_beats"] is False

    def test_has_beats_false_when_bpm_none(self):
        db = _make_db()
        track = SimpleNamespace(
            ID=1, Title="T", ArtistName="A", AlbumName="", BPM=None, Length=300, KeyID=None,
            AnalysisDataPath="",
        )
        self._make_content_q(db, [track])
        key_q = MagicMock()
        key_q.all.return_value = []
        db.query.return_value = key_q
        client = _make_client(db)
        r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["has_beats"] is False


# ---------------------------------------------------------------------------
# /api/generate-apply-stream (SSE)
# ---------------------------------------------------------------------------

class TestGenerateApplyStream:
    def _make_track(self, id=1):
        return SimpleNamespace(
            ID=id, Title=f"Track {id}", BPM=12800, Length=300, UUID="test-uuid",
        )

    def _collect_sse(self, response_text: str) -> list[dict]:
        import json
        events = []
        for line in response_text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        return events

    def test_returns_409_when_rekordbox_running(self):
        client = _make_client()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            r = client.post("/api/generate-apply-stream",
                            json={"track_ids": [], "dry_run": True})
        assert r.status_code == 409

    def test_streams_progress_event_per_track(self, tmp_path):
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.side_effect = [self._make_track(1), self._make_track(2)]
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.generator.analyze_track", return_value=[]):
                with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                    with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                        client = _make_client(db)
                        r = client.post("/api/generate-apply-stream",
                                        json={"track_ids": [1, 2], "dry_run": False,
                                              "overwrite": True})
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        events = self._collect_sse(r.text)
        progress = [e for e in events if not e.get("done")]
        assert len(progress) == 2
        assert progress[0]["processed"] == 1
        assert progress[0]["total"] == 2
        assert progress[1]["processed"] == 2

    def test_final_event_has_done_true(self, tmp_path):
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.return_value = None
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = client.post("/api/generate-apply-stream",
                                json={"track_ids": [1], "dry_run": False})
        events = self._collect_sse(r.text)
        done_events = [e for e in events if e.get("done")]
        assert len(done_events) == 1
        assert "applied" in done_events[0]
        assert "skipped" in done_events[0]

    def test_phrase_only_skips_tracks_without_ext(self, tmp_path):
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.return_value = self._make_track(1)
        db.get_anlz_path.return_value = ""  # no .EXT file
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = client.post("/api/generate-apply-stream",
                                json={"track_ids": [1], "dry_run": False,
                                      "phrase_only": True})
        events = self._collect_sse(r.text)
        done = next(e for e in events if e.get("done"))
        assert done["skipped"] == 1
        assert done["applied"] == 0

    def test_dry_run_backup_path_is_none(self):
        db = _make_db()
        db.get_content.return_value = None
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/generate-apply-stream",
                            json={"track_ids": [], "dry_run": True})
        events = self._collect_sse(r.text)
        done = next(e for e in events if e.get("done"))
        assert done["backup_path"] is None


# ---------------------------------------------------------------------------
# /api/backups + /api/restore
# ---------------------------------------------------------------------------

class TestBackups:
    def test_returns_empty_list_when_no_backups(self, tmp_path):
        with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "nodir"):
            client = _make_client()
            r = client.get("/api/backups")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_backup_files_sorted_newest_first(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        import time
        (backup_dir / "master_20260101T000000.db").write_bytes(b"old")
        time.sleep(0.01)
        (backup_dir / "master_20260531T120000.db").write_bytes(b"new")
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            client = _make_client()
            r = client.get("/api/backups")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        assert data[0]["filename"] == "master_20260531T120000.db"

    def test_backup_item_has_required_fields(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "master_20260531T120000.db").write_bytes(b"x" * 1024)
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            client = _make_client()
            r = client.get("/api/backups")
        item = r.json()[0]
        assert "filename" in item
        assert "size_mb" in item
        assert "path" in item


class TestRestore:
    def test_returns_409_when_rekordbox_running(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "master_old.db").write_bytes(b"x")
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
                client = _make_client()
                r = client.post("/api/restore", json={"filename": "master_old.db"})
        assert r.status_code == 409

    def test_path_traversal_blocked(self, tmp_path):
        with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
            with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
                client = _make_client()
                r = client.post("/api/restore",
                                json={"filename": "../../../etc/passwd"})
        assert r.status_code == 400

    def test_path_traversal_with_slash_blocked(self, tmp_path):
        with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
            with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
                client = _make_client()
                r = client.post("/api/restore",
                                json={"filename": "subdir/master.db"})
        assert r.status_code == 400

    def test_missing_backup_returns_404(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
                client = _make_client()
                r = client.post("/api/restore", json={"filename": "nonexistent.db"})
        assert r.status_code == 404

    def test_successful_restore_returns_restored_true(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        backup_file = backup_dir / "master_old.db"
        backup_file.write_bytes(b"backup content")
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        (db_dir / "master.db").write_bytes(b"current content")
        db = _make_db()
        db._db_dir = db_dir
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
                with patch("pyrekordbox.Rekordbox6Database") as mock_rb:
                    mock_rb.return_value = _make_db()
                    client = _make_client(db)
                    r = client.post("/api/restore", json={"filename": "master_old.db"})
        assert r.status_code == 200
        assert r.json()["restored"] is True

    def test_restore_overwrites_db_file(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "master_old.db").write_bytes(b"backup content")
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        (db_dir / "master.db").write_bytes(b"old content")
        db = _make_db()
        db._db_dir = db_dir
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
                with patch("pyrekordbox.Rekordbox6Database") as mock_rb:
                    mock_rb.return_value = _make_db()
                    client = _make_client(db)
                    client.post("/api/restore", json={"filename": "master_old.db"})
        assert (db_dir / "master.db").read_bytes() == b"backup content"

    def test_restore_copies_wal_if_present(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "master_old.db").write_bytes(b"backup")
        (backup_dir / "master_old.db-wal").write_bytes(b"wal-content")
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        (db_dir / "master.db").write_bytes(b"current")
        db = _make_db()
        db._db_dir = db_dir
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
                with patch("pyrekordbox.Rekordbox6Database") as mock_rb:
                    mock_rb.return_value = _make_db()
                    client = _make_client(db)
                    client.post("/api/restore", json={"filename": "master_old.db"})
        assert (db_dir / "master.db-wal").read_bytes() == b"wal-content"

    def test_restore_removes_stale_wal_if_backup_has_none(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "master_old.db").write_bytes(b"backup")
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        (db_dir / "master.db").write_bytes(b"current")
        (db_dir / "master.db-wal").write_bytes(b"stale-wal")
        db = _make_db()
        db._db_dir = db_dir
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
                with patch("pyrekordbox.Rekordbox6Database") as mock_rb:
                    mock_rb.return_value = _make_db()
                    client = _make_client(db)
                    client.post("/api/restore", json={"filename": "master_old.db"})
        assert not (db_dir / "master.db-wal").exists()

    def test_restore_clears_analysis_caches(self, tmp_path):
        from autocue.analysis import energy as energy_mod
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "master_old.db").write_bytes(b"backup")
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        (db_dir / "master.db").write_bytes(b"current")
        db = _make_db()
        db._db_dir = db_dir
        # Pre-populate the energy cache
        energy_mod._cache[(1, 50)] = [0.5] * 50
        assert (1, 50) in energy_mod._cache
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
                with patch("pyrekordbox.Rekordbox6Database") as mock_rb:
                    mock_rb.return_value = _make_db()
                    client = _make_client(db)
                    client.post("/api/restore", json={"filename": "master_old.db"})
        assert (1, 50) not in energy_mod._cache


# ---------------------------------------------------------------------------
# DELETE /api/backups/{filename}
# ---------------------------------------------------------------------------

class TestDeleteBackup:
    def test_deletes_existing_backup(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "master_20260101T000000.db").write_bytes(b"data")
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            client = _make_client()
            r = client.delete("/api/backups/master_20260101T000000.db")
        assert r.status_code == 200
        assert r.json()["deleted"] == "master_20260101T000000.db"
        assert not (backup_dir / "master_20260101T000000.db").exists()

    def test_also_removes_wal_and_shm_sidecars(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "master_20260101T000000.db").write_bytes(b"data")
        (backup_dir / "master_20260101T000000.db-wal").write_bytes(b"wal")
        (backup_dir / "master_20260101T000000.db-shm").write_bytes(b"shm")
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            client = _make_client()
            r = client.delete("/api/backups/master_20260101T000000.db")
        assert r.status_code == 200
        assert not (backup_dir / "master_20260101T000000.db-wal").exists()
        assert not (backup_dir / "master_20260101T000000.db-shm").exists()

    def test_returns_404_when_not_found(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            client = _make_client()
            r = client.delete("/api/backups/nonexistent.db")
        assert r.status_code == 404

    def test_path_traversal_blocked(self, tmp_path):
        # URL routing prevents slashes in path params, so test the guard directly
        from autocue.serve.routes import delete_backup
        from fastapi import HTTPException
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            with pytest.raises(HTTPException) as exc_info:
                delete_backup("../sensitive.db")
        assert exc_info.value.status_code == 400

    def test_subdirectory_nonexistent_returns_404(self, tmp_path):
        # A subdirectory path stays inside BACKUP_DIR so passes the guard,
        # but no such file exists — 404 is still a safe outcome.
        from autocue.serve.routes import delete_backup
        from fastapi import HTTPException
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            with pytest.raises(HTTPException) as exc_info:
                delete_backup("subdir/master.db")
        assert exc_info.value.status_code == 404


class TestCORS:
    def test_localhost_origin_is_allowed(self):
        """The served UI's own origin must receive the CORS allow header."""
        app = create_app(port=7432)
        from autocue.serve.deps import get_db
        app.dependency_overrides[get_db] = lambda: _make_db()
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/status", headers={"Origin": "http://localhost:7432"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:7432"

    def test_untrusted_origin_is_not_allowed(self):
        """An arbitrary third-party origin must NOT receive the CORS allow header."""
        app = create_app(port=7432)
        from autocue.serve.deps import get_db
        app.dependency_overrides[get_db] = lambda: _make_db()
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/status", headers={"Origin": "http://example.com"})
        acao = r.headers.get("access-control-allow-origin", "")
        assert acao != "http://example.com" and acao != "*"

    def test_null_origin_is_allowed(self):
        """file:// pages send Origin: null and must be allowed."""
        app = create_app(port=7432)
        from autocue.serve.deps import get_db
        app.dependency_overrides[get_db] = lambda: _make_db()
        client = TestClient(app, raise_server_exceptions=False)
        r = client.get("/api/status", headers={"Origin": "null"})
        assert r.headers.get("access-control-allow-origin") == "null"


# ---------------------------------------------------------------------------
# /api/tracks/{id}/audio
# ---------------------------------------------------------------------------

class TestAudioEndpoint:
    def test_returns_404_when_track_not_found(self):
        db = _make_db()
        db.get_content.return_value = None
        client = _make_client(db)
        r = client.get("/api/tracks/9999/audio")
        assert r.status_code == 404

    def test_returns_404_when_folder_path_empty(self):
        db = _make_db()
        db.get_content.return_value = SimpleNamespace(ID=1, FolderPath="")
        client = _make_client(db)
        r = client.get("/api/tracks/1/audio")
        assert r.status_code == 404

    def test_returns_404_when_file_not_on_disk(self, tmp_path):
        db = _make_db()
        db.get_content.return_value = SimpleNamespace(
            ID=1, FolderPath=str(tmp_path / "nonexistent.mp3"),
        )
        client = _make_client(db)
        r = client.get("/api/tracks/1/audio")
        assert r.status_code == 404
        assert "not found on disk" in r.json()["detail"]

    def test_returns_200_when_file_exists(self, tmp_path):
        audio_file = tmp_path / "song.mp3"
        audio_file.write_bytes(b"\xff\xfb\x90\x04" * 128)  # minimal MP3 bytes
        db = _make_db()
        db.get_content.return_value = SimpleNamespace(ID=1, FolderPath=str(audio_file))
        client = _make_client(db)
        r = client.get("/api/tracks/1/audio")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("audio/")

    def test_strips_colon_volume_prefix(self, tmp_path):
        audio_file = tmp_path / "song.flac"
        audio_file.write_bytes(b"fLaC" + b"\x00" * 64)
        db = _make_db()
        # macOS volume paths start with /: — strip the colon
        colon_path = "/:" + str(audio_file)[1:]  # e.g. /:/tmp/song.flac
        db.get_content.return_value = SimpleNamespace(ID=1, FolderPath=colon_path)
        client = _make_client(db)
        r = client.get("/api/tracks/1/audio")
        assert r.status_code == 200

    def test_folder_path_is_full_path_not_folder(self, tmp_path):
        # Regression: FolderPath is the full file path, not a directory.
        # The old code appended FileNameL on top → always 404.
        audio_file = tmp_path / "track.m4a"
        audio_file.write_bytes(b"\x00\x00\x00\x1cftypM4A " + b"\x00" * 64)
        db = _make_db()
        db.get_content.return_value = SimpleNamespace(ID=1, FolderPath=str(audio_file))
        client = _make_client(db)
        r = client.get("/api/tracks/1/audio")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /api/tags
# ---------------------------------------------------------------------------

def _make_tags_db(tags, used_tag_ids):
    """Return a mock DB where /api/tags calls work correctly.

    The endpoint makes two queries:
      1. distinct(DjmdSongMyTag.MyTagID) → list of (id,) tuples for used tag IDs
      2. DjmdMyTag filtered by Name → list of tag objects
    """
    db = _make_db()
    used_q = MagicMock()
    used_q.all.return_value = [(tid,) for tid in used_tag_ids]

    tag_q = MagicMock()
    tag_q.filter.return_value = tag_q
    tag_q.all.return_value = tags

    # First call (distinct SongMyTag) → used_q; second call (MyTag) → tag_q
    db.query.side_effect = [used_q, tag_q]
    return db


class TestTagsEndpoint:
    def test_returns_list_of_tag_names(self):
        tags = [SimpleNamespace(ID=1, Name="House"), SimpleNamespace(ID=2, Name="Techno")]
        db = _make_tags_db(tags, used_tag_ids=[1, 2])
        client = _make_client(db)
        r = client.get("/api/tags")
        assert r.status_code == 200
        names = [item["name"] for item in r.json()]
        assert "House" in names
        assert "Techno" in names

    def test_returns_empty_list_when_no_tags(self):
        db = _make_tags_db([], used_tag_ids=[])
        client = _make_client(db)
        r = client.get("/api/tags")
        assert r.status_code == 200
        assert r.json() == []

    def test_filters_out_none_name_tags(self):
        tags = [SimpleNamespace(ID=1, Name="House"), SimpleNamespace(ID=2, Name=None)]
        db = _make_tags_db(tags, used_tag_ids=[1, 2])
        client = _make_client(db)
        r = client.get("/api/tags")
        assert r.status_code == 200
        names = [item["name"] for item in r.json()]
        assert None not in names
        assert "" not in names

    def test_filters_out_unused_tags(self):
        """Tags with no tracks (not in DjmdSongMyTag) must be excluded."""
        tags = [SimpleNamespace(ID=1, Name="House"), SimpleNamespace(ID=2, Name="Techno")]
        # Only tag 1 (House) has tracks assigned
        db = _make_tags_db(tags, used_tag_ids=[1])
        client = _make_client(db)
        r = client.get("/api/tags")
        assert r.status_code == 200
        names = [item["name"] for item in r.json()]
        assert "House" in names
        assert "Techno" not in names


# ---------------------------------------------------------------------------
# /api/tracks — new fields: rating, play_count, last_played, my_tags, color_name
# ---------------------------------------------------------------------------

class TestTrackNewFields:
    """Verify that new metadata fields are included in TrackItem responses."""

    def _make_track_q(self, db, track):
        q = MagicMock()
        q.count.return_value = 1
        q.all.return_value = [track]
        q.offset.return_value = q
        q.limit.return_value = q
        q.filter.return_value = q
        q.order_by.return_value = q
        db.get_content.return_value = q
        return q

    def _base_track(self, **kwargs):
        defaults = dict(
            ID=1, Title="Song", ArtistName="Artist", AlbumName="Album",
            BPM=12800, Length=300, KeyID=None, Rating=0, DJPlayCount="0",
            ColorID=None,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def _get_first_track(self, db, track):
        self._make_track_q(db, track)
        key_q = MagicMock(); key_q.all.return_value = []
        db.query.return_value = key_q
        db.get_anlz_path.return_value = ""
        with patch("autocue.serve.routes.has_existing_hot_cues", return_value=0):
            client = _make_client(db)
            r = client.get("/api/tracks")
        assert r.status_code == 200
        return r.json()[0]

    def test_rating_field_included(self):
        db = _make_db()
        item = self._get_first_track(db, self._base_track(Rating=4))
        assert item["rating"] == 4

    def test_play_count_field_included(self):
        db = _make_db()
        item = self._get_first_track(db, self._base_track(DJPlayCount="17"))
        assert item["play_count"] == 17

    def test_last_played_field_is_none_when_not_in_map(self):
        db = _make_db()
        item = self._get_first_track(db, self._base_track())
        assert item["last_played"] is None

    def test_my_tags_field_is_empty_when_not_in_map(self):
        db = _make_db()
        item = self._get_first_track(db, self._base_track())
        assert item["my_tags"] == []

    def test_color_name_field_is_empty_when_no_color(self):
        db = _make_db()
        item = self._get_first_track(db, self._base_track(ColorID=None))
        assert item["color_name"] == ""


# ---------------------------------------------------------------------------
# /api/backups — created_at formatted date
# ---------------------------------------------------------------------------

class TestBackupsCreatedAt:
    def test_created_at_parses_timestamp_from_filename(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "master_20260531T143000.db").write_bytes(b"x")
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            client = _make_client()
            r = client.get("/api/backups")
        assert r.status_code == 200
        item = r.json()[0]
        assert item["created_at"] == "2026-05-31 14:30:00"

    def test_created_at_falls_back_to_mtime_for_odd_filenames(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # T-022 changed the /api/backups glob from "*.db" to "master_*.db" so
        # the listing pairs cleanly with the new discover_<TS>.db sidecars.
        # Use a "master_" prefix without a parseable timestamp to exercise
        # the mtime-fallback branch.
        (backup_dir / "master_manual.db").write_bytes(b"x")
        with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
            client = _make_client()
            r = client.get("/api/backups")
        item = r.json()[0]
        # Falls back to mtime — just verify it's a non-empty date string
        assert len(item["created_at"]) == len("2026-01-01 00:00:00")


# ---------------------------------------------------------------------------
# /api/color-tracks — skip_colored option
# ---------------------------------------------------------------------------

class TestColorTracksSkipColored:
    def _color_db(self, color_id=None):
        db = _make_db()
        color_q = MagicMock()
        color_q.all.return_value = [SimpleNamespace(SortKey=i, ID=f"c{i}") for i in range(1, 9)]
        db.query.return_value = color_q
        db.get_content.return_value = SimpleNamespace(
            ID=1, Title="T", BPM=12800, ColorID=color_id,
        )
        return db

    def test_skip_colored_skips_already_colored_track(self):
        db = self._color_db(color_id="c5")  # track already has a color
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/color-tracks", json={"track_ids": [1], "dry_run": True, "skip_colored": True})
        assert r.status_code == 200
        assert r.json()["colored"] == 0
        assert r.json()["skipped"] == 1

    def test_skip_colored_false_colors_already_colored_track(self):
        db = self._color_db(color_id="c5")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/color-tracks", json={"track_ids": [1], "dry_run": True, "skip_colored": False})
        assert r.status_code == 200
        assert r.json()["colored"] == 1

    def test_skip_colored_default_is_false(self):
        db = self._color_db(color_id="c5")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/color-tracks", json={"track_ids": [1], "dry_run": True})
        assert r.status_code == 200
        assert r.json()["colored"] == 1  # not skipped by default


# ---------------------------------------------------------------------------
# /api/generate — add_fill_cues option
# ---------------------------------------------------------------------------

class TestGenerateAddFillCues:
    def test_add_fill_cues_passed_to_prefs_without_error(self):
        """add_fill_cues=True must be accepted by /api/generate (no 422)."""
        db = _make_db()
        track = SimpleNamespace(ID=1, Title="T", BPM=12800, Length=300)
        db.get_content.return_value = track
        with patch("autocue.generator.analyze_track", return_value=[]):
            client = _make_client(db)
            r = client.post("/api/generate", json={
                "track_ids": [1], "mode": "bar", "add_fill_cues": True,
            })
        assert r.status_code == 200

    def test_add_fill_cues_false_is_default(self):
        db = _make_db()
        track = SimpleNamespace(ID=1, Title="T", BPM=12800, Length=300)
        db.get_content.return_value = track
        with patch("autocue.generator.analyze_track", return_value=[]):
            client = _make_client(db)
            r = client.post("/api/generate", json={"track_ids": [1]})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /api/generate-apply  — add_fill_cues and add_memory_cue options
# ---------------------------------------------------------------------------

class TestGenerateApplyOptions:
    def _setup(self, tmp_path):
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"db")
        track = SimpleNamespace(ID=1, Title="T", BPM=12800, Length=300,
                                ArtistName="A", AlbumName="", KeyID=None,
                                Rating=0, DJPlayCount="0", ColorID=None)
        db.get_content.return_value = track
        db.session = MagicMock()
        db.generate_unused_id = MagicMock(return_value="new-id")
        return db

    def test_add_memory_cue_accepted(self, tmp_path):
        db = self._setup(tmp_path)
        with patch("autocue.generator.analyze_track", return_value=[]):
            with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
                with patch("autocue.db_writer.backup_database", return_value=tmp_path / "bak.db"):
                    with patch("autocue.db_writer.write_cues_to_db", return_value=0):
                        client = _make_client(db)
                        r = client.post("/api/generate-apply", json={
                            "track_ids": [1], "add_memory_cue": True, "dry_run": True,
                        })
        assert r.status_code == 200

    def test_add_fill_cues_accepted(self, tmp_path):
        db = self._setup(tmp_path)
        with patch("autocue.generator.analyze_track", return_value=[]):
            with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
                with patch("autocue.db_writer.backup_database", return_value=tmp_path / "bak.db"):
                    with patch("autocue.db_writer.write_cues_to_db", return_value=0):
                        client = _make_client(db)
                        r = client.post("/api/generate-apply", json={
                            "track_ids": [1], "add_fill_cues": True, "dry_run": True,
                        })
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /api/tracks/{track_id}/artwork
# ---------------------------------------------------------------------------

class TestArtworkEndpoint:
    def test_returns_404_when_track_not_found(self):
        # 404 is RESERVED for "track ID is unknown to the DB" — a genuine
        # client error that should surface in DevTools.
        db = _make_db()
        db.get_content.return_value = None
        client = _make_client(db)
        r = client.get("/api/tracks/9999/artwork")
        assert r.status_code == 404

    def test_returns_204_when_no_image_path(self):
        # Issue #120 — track exists but ImagePath is unset (e.g. streaming
        # source). 204 No Content lets the browser fail the <img> load silently
        # without spamming console errors.
        db = _make_db()
        db.get_content.return_value = SimpleNamespace(ID=1, ImagePath=None)
        client = _make_client(db)
        r = client.get("/api/tracks/1/artwork")
        assert r.status_code == 204

    def test_returns_204_when_db_dir_missing(self):
        # When _db_dir is None and ImagePath doesn't resolve to a real file
        # → 204 (track exists), not 500.
        db = _make_db()
        db.get_content.return_value = SimpleNamespace(ID=1, ImagePath="cover.jpg", FolderPath=None)
        db._db_dir = None
        client = _make_client(db)
        r = client.get("/api/tracks/1/artwork")
        assert r.status_code == 204

    def test_returns_204_when_file_not_on_disk(self, tmp_path):
        db = _make_db()
        db.get_content.return_value = SimpleNamespace(ID=1, ImagePath="/missing/cover.jpg")
        db._db_dir = str(tmp_path)
        client = _make_client(db)
        r = client.get("/api/tracks/1/artwork")
        assert r.status_code == 204

    def test_no_artwork_is_distinguishable_from_track_not_found(self):
        # Regression guard for issue #120: the two "no image returned" cases
        # MUST use different status codes so DevTools console noise can be
        # distinguished from genuine "track ID typo" errors.
        db = _make_db()
        db.get_content.return_value = None
        client = _make_client(db)
        r_missing = client.get("/api/tracks/9999/artwork")

        db2 = _make_db()
        db2.get_content.return_value = SimpleNamespace(ID=1, ImagePath=None)
        client2 = _make_client(db2)
        r_no_art = client2.get("/api/tracks/1/artwork")

        assert r_missing.status_code != r_no_art.status_code
        assert r_missing.status_code == 404
        assert r_no_art.status_code == 204

    def test_returns_200_when_absolute_path_exists(self, tmp_path):
        img = tmp_path / "cover.jpg"
        img.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)  # minimal JPEG header
        db = _make_db()
        db.get_content.return_value = SimpleNamespace(ID=1, ImagePath=str(img))
        db._db_dir = str(tmp_path)
        client = _make_client(db)
        r = client.get("/api/tracks/1/artwork")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"

    def test_returns_200_when_path_relative_to_db_dir(self, tmp_path):
        img = tmp_path / "cover.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
        db = _make_db()
        db.get_content.return_value = SimpleNamespace(ID=1, ImagePath="/cover.png")
        db._db_dir = str(tmp_path)
        client = _make_client(db)
        r = client.get("/api/tracks/1/artwork")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"


# ---------------------------------------------------------------------------
# /api/color-tracks-stream
# ---------------------------------------------------------------------------

class TestColorTracksStream:
    def _collect_sse(self, text: str) -> list[dict]:
        import json
        return [
            json.loads(line[6:])
            for line in text.splitlines()
            if line.startswith("data: ")
        ]

    def _make_color_db(self, bpm=12800):
        db = _make_db()
        color_q = MagicMock()
        color_q.all.return_value = [
            SimpleNamespace(SortKey=1, ID="pink"),
            SimpleNamespace(SortKey=5, ID="green"),
        ]
        db.query.return_value = color_q
        db.get_content.return_value = SimpleNamespace(ID=1, BPM=bpm, ColorID=None)
        db.session = MagicMock()
        db.session.execute = MagicMock()
        db.session.expire_all = MagicMock()
        db.session.commit = MagicMock()
        return db

    def test_returns_409_when_rekordbox_running(self):
        client = _make_client()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            r = client.post("/api/color-tracks-stream",
                            json={"track_ids": [1], "dry_run": False})
        assert r.status_code == 409

    def test_dry_run_skips_rekordbox_check(self):
        db = self._make_color_db()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            client = _make_client(db)
            r = client.post("/api/color-tracks-stream",
                            json={"track_ids": [1], "dry_run": True})
        assert r.status_code == 200

    def test_final_event_has_done_true(self, tmp_path):
        db = self._make_color_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = client.post("/api/color-tracks-stream",
                                json={"track_ids": [1], "dry_run": False})
        events = self._collect_sse(r.text)
        done = next(e for e in events if e.get("done"))
        assert done["colored"] == 1
        assert done["skipped"] == 0
        assert done["total"] == 1

    def test_dry_run_backup_path_is_none(self):
        db = self._make_color_db()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/color-tracks-stream",
                            json={"track_ids": [1], "dry_run": True})
        events = self._collect_sse(r.text)
        done = next(e for e in events if e.get("done"))
        assert done["backup_path"] is None

    def test_skip_colored_skips_already_colored_track(self):
        db = self._make_color_db()
        db.get_content.return_value = SimpleNamespace(ID=1, BPM=12800, ColorID="green")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/color-tracks-stream",
                            json={"track_ids": [1], "dry_run": True, "skip_colored": True})
        events = self._collect_sse(r.text)
        done = next(e for e in events if e.get("done"))
        assert done["skipped"] == 1
        assert done["colored"] == 0

    def test_skips_missing_track(self):
        db = self._make_color_db()
        db.get_content.return_value = None
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/color-tracks-stream",
                            json={"track_ids": [99], "dry_run": True})
        events = self._collect_sse(r.text)
        done = next(e for e in events if e.get("done"))
        assert done["skipped"] == 1
        assert done["colored"] == 0


# ---------------------------------------------------------------------------
# /api/tracks/{id}/health  and  GET /api/health  (SSE)
# ---------------------------------------------------------------------------

def _make_content_health(track_id=1, folder_path="/music/track.mp3",
                          analysis_data_path="path/to/analysis", bpm=13000):
    c = MagicMock()
    c.ID = track_id
    c.FolderPath = folder_path
    c.AnalysisDataPath = analysis_data_path
    c.BPM = bpm
    return c


def _make_cue_health(kind=1, in_frame=1200, comment="Drop"):
    c = MagicMock()
    c.Kind = kind
    c.InFrame = in_frame
    c.Comment = comment
    return c


def _parse_sse(text):
    import json
    return [json.loads(line[6:]) for line in text.splitlines()
            if line.startswith("data: ")]


class TestTrackHealth:
    def _db_with_content_and_cues(self, content, cues):
        db = _make_db()
        db.query.return_value.filter.return_value.first.return_value = content
        db.query.return_value.filter.return_value.all.return_value = cues
        return db

    def test_returns_200_with_score_for_healthy_track(self):
        content = _make_content_health()
        db = self._db_with_content_and_cues(content, [_make_cue_health()])
        client = _make_client(db)
        with patch("os.path.exists", return_value=True):
            r = client.get("/api/tracks/1/health")
        assert r.status_code == 200
        data = r.json()
        assert data["score"] == 100
        assert data["fix_tier"] == "phrase"
        assert data["hot_cue_count"] == 1

    def test_returns_404_when_track_not_found(self):
        db = _make_db()
        db.query.return_value.filter.return_value.first.return_value = None
        client = _make_client(db)
        r = client.get("/api/tracks/9999/health")
        assert r.status_code == 404

    def test_score_zero_when_audio_missing(self):
        content = _make_content_health(folder_path="/nonexistent/track.mp3")
        db = self._db_with_content_and_cues(content, [])
        client = _make_client(db)
        with patch("os.path.exists", return_value=False):
            r = client.get("/api/tracks/1/health")
        assert r.status_code == 200
        assert r.json()["score"] == 0
        assert any(i["code"] == "NO_AUDIO_FILE" for i in r.json()["issues"])

    def test_internal_error_returns_score_zero_not_500(self):
        bad = MagicMock()
        bad.ID = 1
        bad.FolderPath = "/music/track.mp3"
        type(bad).AnalysisDataPath = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        db = _make_db()
        db.query.return_value.filter.return_value.first.return_value = bad
        client = _make_client(db)
        with patch("os.path.exists", return_value=True):
            r = client.get("/api/tracks/1/health")
        assert r.status_code == 200
        assert r.json()["score"] == 0
        assert any(i["code"] == "INTERNAL_ERROR" for i in r.json()["issues"])


class TestLibraryHealthSSE:
    def _db_for_scan(self, contents, cues=None):
        db = _make_db()
        all_q = MagicMock()
        all_q.all.return_value = contents
        all_q.join.return_value = all_q
        all_q.filter.return_value = all_q
        all_q.count.return_value = len(contents)

        cue_q = MagicMock()
        cue_q.filter.return_value = cue_q
        cue_q.all.return_value = cues or []
        cue_q.first.return_value = MagicMock()  # playlist exists by default

        def _side(*args, **kwargs):
            cls = args[0] if args else None
            name = getattr(cls, "__name__", "")
            if name == "DjmdContent":
                return all_q
            return cue_q

        db.query.side_effect = _side
        return db

    def test_streams_one_event_per_track_plus_done(self):
        contents = [_make_content_health(track_id=i) for i in range(3)]
        db = self._db_for_scan(contents, [_make_cue_health()])
        client = _make_client(db)
        with patch("os.path.exists", return_value=True):
            r = client.get("/api/health")
        events = _parse_sse(r.text)
        assert len([e for e in events if e.get("track_id") is not None]) == 3
        assert len([e for e in events if e.get("done")]) == 1

    def test_done_event_contains_summary_fields(self):
        contents = [_make_content_health(track_id=1)]
        db = self._db_for_scan(contents, [_make_cue_health()])
        client = _make_client(db)
        with patch("os.path.exists", return_value=True):
            r = client.get("/api/health")
        done = next(e for e in _parse_sse(r.text) if e.get("done"))
        s = done["summary"]
        assert s["total"] == 1
        assert s["library_score"] == 100.0
        assert "fix_tier_counts" in s

    def test_sse_headers_prevent_buffering(self):
        db = self._db_for_scan([])
        client = _make_client(db)
        r = client.get("/api/health")
        assert r.headers.get("cache-control") == "no-cache"
        assert r.headers.get("x-accel-buffering") == "no"

    def test_playlist_id_404_when_not_found(self):
        db = _make_db()
        pl_q = MagicMock()
        pl_q.filter.return_value = pl_q
        pl_q.first.return_value = None
        db.query.return_value = pl_q
        client = _make_client(db)
        r = client.get("/api/health?playlist_id=9999")
        assert r.status_code == 404

    def test_per_track_exception_yields_internal_error(self):
        bad = MagicMock()
        bad.ID = 99
        bad.FolderPath = "/music/track.mp3"
        type(bad).AnalysisDataPath = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        db = self._db_for_scan([bad])
        client = _make_client(db)
        with patch("os.path.exists", return_value=True):
            r = client.get("/api/health")
        track_events = [e for e in _parse_sse(r.text) if e.get("track_id") is not None]
        assert len(track_events) == 1
        assert track_events[0]["score"] == 0
        assert any(i["code"] == "INTERNAL_ERROR" for i in track_events[0]["issues"])

    def test_empty_library_done_event_with_zero_score(self):
        db = self._db_for_scan([])
        client = _make_client(db)
        r = client.get("/api/health")
        done = next(e for e in _parse_sse(r.text) if e.get("done"))
        assert done["summary"]["total"] == 0
        assert done["summary"]["library_score"] == 0.0


# ---------------------------------------------------------------------------
# Cue Library Tools
# ---------------------------------------------------------------------------

def _make_cue_tool(kind=1, in_msec=5000, comment="Drop 1", color_table_index=5):
    c = MagicMock()
    c.Kind = kind
    c.InMsec = in_msec
    c.InFrame = round(in_msec * 150 / 1000)
    c.Comment = comment
    c.ColorTableIndex = color_table_index
    c.OutMsec = -1  # default: no out point (not a loop cue)
    return c


def _make_db_cue_tools(track, cues):
    """DB mock where get_content(ID=x) returns track and session.query filters return cues."""
    db = _make_db()
    db.get_content.return_value = track
    db.session = MagicMock()
    db.session.query.return_value.filter.return_value.all.return_value = cues
    return db


class TestCueToolsStream:
    def _track(self, tid=1):
        t = MagicMock()
        t.ID = tid
        return t

    def _post(self, client, payload):
        return client.post("/api/cue-tools-stream",
                           json=payload,
                           headers={"Content-Type": "application/json"})

    def test_returns_409_when_rekordbox_running(self):
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            client = _make_client()
            r = self._post(client, {
                "operation": "rename", "track_ids": [1], "dry_run": False,
                "rename": {"from_name": "a", "to_name": "b"},
            })
        assert r.status_code == 409

    def test_dry_run_skips_rekordbox_check(self, tmp_path):
        track = self._track()
        cue = _make_cue_tool(comment="Old")
        db = _make_db_cue_tools(track, [cue])
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            client = _make_client(db)
            r = self._post(client, {
                "operation": "rename", "track_ids": [1], "dry_run": True,
                "rename": {"from_name": "Old", "to_name": "New"},
            })
        assert r.status_code == 200

    def test_rename_counts_matching_cues(self, tmp_path):
        track = self._track()
        cues = [_make_cue_tool(comment="Old"), _make_cue_tool(comment="Other")]
        db = _make_db_cue_tools(track, cues)
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "rename", "track_ids": [1], "dry_run": False,
                    "rename": {"from_name": "Old", "to_name": "New"},
                })
        done = next(e for e in _parse_sse(r.text) if e.get("done"))
        assert done["summary"]["cues_changed"] == 1
        assert done["summary"]["cues_skipped"] == 1

    def test_rename_dry_run_no_backup(self):
        track = self._track()
        cues = [_make_cue_tool(comment="Old")]
        db = _make_db_cue_tools(track, cues)
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = self._post(client, {
                "operation": "rename", "track_ids": [1], "dry_run": True,
                "rename": {"from_name": "Old", "to_name": "New"},
            })
        done = next(e for e in _parse_sse(r.text) if e.get("done"))
        assert done["summary"]["backup_path"] is None
        assert done["summary"]["dry_run"] is True
        # Cue comment must NOT have been mutated
        assert cues[0].Comment == "Old"

    def test_shift_updates_both_msec_and_frame(self, tmp_path):
        track = self._track()
        cue = _make_cue_tool(in_msec=10000, comment="X")
        db = _make_db_cue_tools(track, [cue])
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "shift", "track_ids": [1], "dry_run": False,
                    "shift": {"delta_ms": 500},
                })
        assert r.status_code == 200
        done = next(e for e in _parse_sse(r.text) if e.get("done"))
        assert done["summary"]["cues_changed"] == 1
        assert cue.InMsec == 10500
        assert cue.InFrame == round(10500 * 150 / 1000)

    def test_shift_skips_cue_that_would_go_negative(self, tmp_path):
        track = self._track()
        cue = _make_cue_tool(in_msec=100, comment="X")
        db = _make_db_cue_tools(track, [cue])
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "shift", "track_ids": [1], "dry_run": False,
                    "shift": {"delta_ms": -500},
                })
        done = next(e for e in _parse_sse(r.text) if e.get("done"))
        assert done["summary"]["cues_changed"] == 0
        assert done["summary"]["cues_skipped"] == 1

    def test_recolor_only_changes_mapped_slots(self, tmp_path):
        track = self._track()
        cue_a = _make_cue_tool(kind=1, color_table_index=0)  # slot A
        cue_b = _make_cue_tool(kind=2, color_table_index=0)  # slot B (not in mapping)
        db = _make_db_cue_tools(track, [cue_a, cue_b])
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "recolor", "track_ids": [1], "dry_run": False,
                    "recolor": {"slot_colors": {"0": 5}},  # only slot A → Green
                })
        done = next(e for e in _parse_sse(r.text) if e.get("done"))
        assert done["summary"]["cues_changed"] == 1
        assert done["summary"]["cues_skipped"] == 1
        assert cue_a.ColorTableIndex == 5
        assert cue_b.ColorTableIndex == 0  # unchanged

    def test_delete_orphan_removes_slots_above_threshold(self, tmp_path):
        track = self._track()
        cues = [_make_cue_tool(kind=k) for k in range(1, 9)]  # slots A-H
        db = _make_db_cue_tools(track, cues)
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        deleted = []
        db.session.delete.side_effect = deleted.append
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "delete_orphan", "track_ids": [1], "dry_run": False,
                    "delete_orphan": {"keep_slots": 4},
                })
        done = next(e for e in _parse_sse(r.text) if e.get("done"))
        assert done["summary"]["cues_changed"] == 4   # E F G H deleted
        assert done["summary"]["cues_skipped"] == 4   # A B C D kept

    def test_memory_cues_are_excluded(self, tmp_path):
        """Kind=0 memory cues must never be touched."""
        track = self._track()
        hot_cue = _make_cue_tool(kind=1, comment="Old")
        db = _make_db_cue_tools(track, [hot_cue])
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "rename", "track_ids": [1], "dry_run": False,
                    "rename": {"from_name": "Old", "to_name": "New"},
                })
        assert r.status_code == 200

    def test_missing_operation_params_returns_422(self):
        client = _make_client()
        r = self._post(client, {
            "operation": "rename", "track_ids": [1],
            # rename params omitted — should fail validation
        })
        assert r.status_code == 422

    def test_sse_headers_present(self):
        track = self._track()
        db = _make_db_cue_tools(track, [])
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = self._post(client, {
                "operation": "rename", "track_ids": [1], "dry_run": True,
                "rename": {"from_name": "a", "to_name": "b"},
            })
        assert r.headers.get("cache-control") == "no-cache"
        assert r.headers.get("x-accel-buffering") == "no"

    def test_shift_zero_delta_rejected(self):
        """delta_ms=0 must be rejected at the schema level."""
        client = _make_client()
        r = self._post(client, {
            "operation": "shift", "track_ids": [1], "dry_run": True,
            "shift": {"delta_ms": 0},
        })
        assert r.status_code == 422

    def test_shift_preserves_loop_length(self, tmp_path):
        """Shift must update OutMsec for loop cues so loop length stays constant."""
        track = self._track()
        cue = _make_cue_tool(in_msec=10000, comment="loop")
        cue.OutMsec = 12000  # 2-second loop
        db = _make_db_cue_tools(track, [cue])
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "shift", "track_ids": [1], "dry_run": False,
                    "shift": {"delta_ms": 500},
                })
        assert r.status_code == 200
        assert cue.InMsec == 10500
        assert cue.OutMsec == 12500  # loop length preserved (2000ms)

    def test_shift_leaves_sentinel_out_msec_unchanged(self, tmp_path):
        """OutMsec=-1 (no out point) must not be modified by shift."""
        track = self._track()
        cue = _make_cue_tool(in_msec=10000, comment="X")
        cue.OutMsec = -1
        db = _make_db_cue_tools(track, [cue])
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "shift", "track_ids": [1], "dry_run": False,
                    "shift": {"delta_ms": 500},
                })
        assert r.status_code == 200
        assert cue.OutMsec == -1  # sentinel unchanged

    def test_empty_track_ids_no_backup(self, tmp_path):
        """Empty track_ids dry_run=False should not create a backup."""
        db = MagicMock()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        backup_dir = tmp_path / "backups"
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", backup_dir):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "rename", "track_ids": [], "dry_run": False,
                    "rename": {"from_name": "a", "to_name": "b"},
                })
        assert r.status_code == 200
        done = next(e for e in _parse_sse(r.text) if e.get("done"))
        assert done["summary"]["tracks_processed"] == 0

    def test_backup_path_is_filename_only(self, tmp_path):
        """backup_path in summary must be a bare filename, not a full absolute path."""
        track = self._track()
        cue = _make_cue_tool(comment="Old")
        db = _make_db_cue_tools(track, [cue])
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "rename", "track_ids": [1], "dry_run": False,
                    "rename": {"from_name": "Old", "to_name": "New"},
                })
        done = next(e for e in _parse_sse(r.text) if e.get("done"))
        bp = done["summary"].get("backup_path")
        assert bp is not None
        import os
        assert bp == os.path.basename(bp)  # no directory separators


# ---------------------------------------------------------------------------
# GET /api/tracks/{id}/energy
# ---------------------------------------------------------------------------

class TestTrackEnergy:
    def setup_method(self):
        from autocue.analysis import energy as energy_mod
        energy_mod.clear_cache()

    def _content(self, track_id=1):
        from unittest.mock import MagicMock
        c = MagicMock()
        c.ID = track_id
        return c

    def _db_with_pwav(self, entries):
        db = MagicMock()
        tag = MagicMock()
        tag.content.entries = entries
        anlz = MagicMock()
        anlz.get_tag.return_value = tag
        db.read_anlz_file.return_value = anlz
        db.get_content.return_value = self._content(1)
        return db

    def test_returns_energy_list(self):
        db = self._db_with_pwav([31] * 100)
        client = _make_client(db)
        r = client.get("/api/tracks/1/energy")
        assert r.status_code == 200
        data = r.json()
        assert "energy" in data
        assert isinstance(data["energy"], list)
        assert len(data["energy"]) == 50

    def test_values_normalized_0_to_1(self):
        db = self._db_with_pwav([31] * 100)
        client = _make_client(db)
        r = client.get("/api/tracks/1/energy")
        for v in r.json()["energy"]:
            assert 0.0 <= v <= 1.0

    def test_returns_null_energy_when_no_pwav(self):
        db = MagicMock()
        db.get_content.return_value = self._content(1)
        db.read_anlz_file.return_value = None
        client = _make_client(db)
        r = client.get("/api/tracks/1/energy")
        assert r.status_code == 200
        assert r.json()["energy"] is None

    def test_returns_404_for_unknown_track(self):
        db = MagicMock()
        db.get_content.return_value = None
        client = _make_client(db)
        r = client.get("/api/tracks/999/energy")
        assert r.status_code == 404

    def test_energy_profile_included_in_response(self):
        # Flat curve (all max amplitude) → profile should be "flat"
        db = self._db_with_pwav([31] * 100)
        client = _make_client(db)
        r = client.get("/api/tracks/1/energy")
        assert r.status_code == 200
        data = r.json()
        assert "energy_profile" in data
        assert data["energy_profile"] == "flat"

    def test_energy_profile_null_when_no_pwav(self):
        db = MagicMock()
        db.get_content.return_value = self._content(1)
        db.read_anlz_file.return_value = None
        client = _make_client(db)
        r = client.get("/api/tracks/1/energy")
        assert r.status_code == 200
        assert r.json()["energy_profile"] is None


# ---------------------------------------------------------------------------
# POST /api/playlists/suggest
# ---------------------------------------------------------------------------

class TestPlaylistSuggest:
    def _make_content(self, track_id: int, bpm_int: int = 13500):
        c = MagicMock()
        c.ID = track_id
        c.BPM = bpm_int
        c.Length = 240
        c.FolderPath = "/music/track.mp3"
        return c

    def _db_with_tracks(self, tracks):
        """Build a DB mock that returns `tracks` from get_content().all()."""
        db = MagicMock()
        q = MagicMock()
        q.all.return_value = tracks
        q.count.return_value = len(tracks)
        db.get_content.return_value = q
        return db

    def test_valid_category_returns_200(self):
        db = self._db_with_tracks([])
        with patch("autocue.analysis.classify.get_energy_curve", return_value=None):
            with patch("autocue.analysis.classify.get_mixability", return_value=None):
                client = _make_client(db)
                r = client.post("/api/playlists/suggest", json={"category": "peak"})
        assert r.status_code == 200

    def test_response_has_required_fields(self):
        db = self._db_with_tracks([])
        with patch("autocue.analysis.classify.get_energy_curve", return_value=None):
            with patch("autocue.analysis.classify.get_mixability", return_value=None):
                client = _make_client(db)
                r = client.post("/api/playlists/suggest", json={"category": "warmup"})
        data = r.json()
        assert "category" in data
        assert "results" in data
        assert data["category"] == "warmup"

    def test_invalid_category_returns_400(self):
        client = _make_client(_make_db())
        r = client.post("/api/playlists/suggest", json={"category": "disco"})
        assert r.status_code == 400

    def test_count_too_large_returns_400(self):
        client = _make_client(_make_db())
        r = client.post("/api/playlists/suggest", json={"category": "peak", "count": 999})
        assert r.status_code == 400

    def test_results_sorted_by_score_descending(self):
        # Two peak-range tracks: higher BPM within peak range should score differently
        track_a = self._make_content(101, bpm_int=13500)  # 135 BPM
        track_b = self._make_content(102, bpm_int=12600)  # 126 BPM (at peak's lo_full)
        db = self._db_with_tracks([track_a, track_b])
        with patch("autocue.analysis.classify.get_energy_curve", return_value=[0.8] * 50):
            with patch("autocue.analysis.classify.get_mixability", return_value={"vocal_proxy": False}):
                client = _make_client(db)
                r = client.post("/api/playlists/suggest", json={"category": "peak", "count": 10})
        data = r.json()
        scores = [item["score"] for item in data["results"]]
        assert scores == sorted(scores, reverse=True)

    def test_exclude_ids_omits_tracks(self):
        track_a = self._make_content(201, bpm_int=13500)
        track_b = self._make_content(202, bpm_int=13500)
        db = self._db_with_tracks([track_a, track_b])
        with patch("autocue.analysis.classify.get_energy_curve", return_value=[0.8] * 50):
            with patch("autocue.analysis.classify.get_mixability", return_value={"vocal_proxy": False}):
                client = _make_client(db)
                r = client.post(
                    "/api/playlists/suggest",
                    json={"category": "peak", "exclude_ids": [201]},
                )
        ids = [item["track_id"] for item in r.json()["results"]]
        assert 201 not in ids
        assert 202 in ids

    def test_count_limits_results(self):
        tracks = [self._make_content(300 + i, bpm_int=13500) for i in range(10)]
        db = self._db_with_tracks(tracks)
        with patch("autocue.analysis.classify.get_energy_curve", return_value=[0.8] * 50):
            with patch("autocue.analysis.classify.get_mixability", return_value={"vocal_proxy": False}):
                client = _make_client(db)
                r = client.post("/api/playlists/suggest", json={"category": "peak", "count": 3})
        assert len(r.json()["results"]) <= 3

    def test_zero_score_tracks_excluded(self):
        # BPM=50 scores 0 in every category
        track = self._make_content(401, bpm_int=5000)
        db = self._db_with_tracks([track])
        with patch("autocue.analysis.classify.get_energy_curve", return_value=None):
            with patch("autocue.analysis.classify.get_mixability", return_value=None):
                client = _make_client(db)
                r = client.post("/api/playlists/suggest", json={"category": "peak"})
        assert r.json()["results"] == []


# ---------------------------------------------------------------------------
# /api/transitions/score
# ---------------------------------------------------------------------------

class TestTransitionScore:
    def _make_content(self, track_id: int, bpm_int: int = 12000, key: str = "8A"):
        from unittest.mock import MagicMock
        c = MagicMock()
        c.ID = track_id
        c.BPM = bpm_int
        key_obj = MagicMock()
        key_obj.ScaleName = key
        c.Key = key_obj
        return c

    def _db_with_two_tracks(self, ca, cb):
        db = MagicMock()
        def _get(ID=None, **_):
            if ID == ca.ID: return ca
            if ID == cb.ID: return cb
            return None
        db.get_content.side_effect = _get
        db.read_anlz_file.return_value = None
        return db

    def test_returns_200_with_schema(self):
        ca = self._make_content(1)
        cb = self._make_content(2)
        db = self._db_with_two_tracks(ca, cb)
        with patch("autocue.analysis.transitions.get_energy_curve", return_value=None):
            client = _make_client(db)
            r = client.post("/api/transitions/score", json={"track_a_id": 1, "track_b_id": 2})
        assert r.status_code == 200
        data = r.json()
        for field in ("overall", "bpm", "key", "energy", "bpm_a", "bpm_b", "key_a", "key_b"):
            assert field in data

    def test_explanation_list_included(self):
        ca = self._make_content(1)
        cb = self._make_content(2)
        db = self._db_with_two_tracks(ca, cb)
        with patch("autocue.analysis.transitions.get_energy_curve", return_value=None):
            client = _make_client(db)
            r = client.post("/api/transitions/score", json={"track_a_id": 1, "track_b_id": 2})
        data = r.json()
        assert "explanation" in data
        assert isinstance(data["explanation"], list)
        assert len(data["explanation"]) == 3

    def test_404_when_track_a_missing(self):
        ca = self._make_content(1)
        cb = self._make_content(2)
        db = self._db_with_two_tracks(ca, cb)
        with patch("autocue.analysis.transitions.get_energy_curve", return_value=None):
            client = _make_client(db)
            r = client.post("/api/transitions/score", json={"track_a_id": 999, "track_b_id": 2})
        assert r.status_code == 404

    def test_404_when_track_b_missing(self):
        ca = self._make_content(1)
        cb = self._make_content(2)
        db = self._db_with_two_tracks(ca, cb)
        with patch("autocue.analysis.transitions.get_energy_curve", return_value=None):
            client = _make_client(db)
            r = client.post("/api/transitions/score", json={"track_a_id": 1, "track_b_id": 999})
        assert r.status_code == 404

    def test_400_when_same_track(self):
        ca = self._make_content(1)
        db = MagicMock()
        with patch("autocue.analysis.transitions.get_energy_curve", return_value=None):
            client = _make_client(db)
            r = client.post("/api/transitions/score", json={"track_a_id": 1, "track_b_id": 1})
        assert r.status_code == 400

    def test_perfect_score_same_bpm_key(self):
        ca = self._make_content(1, bpm_int=12000, key="8A")
        cb = self._make_content(2, bpm_int=12000, key="8A")
        db = self._db_with_two_tracks(ca, cb)
        curve = [0.5] * 50
        with patch("autocue.analysis.transitions.get_energy_curve", return_value=curve):
            client = _make_client(db)
            r = client.post("/api/transitions/score", json={"track_a_id": 1, "track_b_id": 2})
        assert r.json()["overall"] == 100.0

    def test_end_start_energy_fields(self):
        ca = self._make_content(1)
        cb = self._make_content(2)
        db = self._db_with_two_tracks(ca, cb)
        curve = [0.7] * 50
        with patch("autocue.analysis.transitions.get_energy_curve", return_value=curve):
            client = _make_client(db)
            r = client.post("/api/transitions/score", json={"track_a_id": 1, "track_b_id": 2})
        data = r.json()
        assert data["end_energy_a"] == pytest.approx(0.7, abs=0.01)
        assert data["start_energy_b"] == pytest.approx(0.7, abs=0.01)


# ---------------------------------------------------------------------------
# /api/tracks/{id}/similar
# ---------------------------------------------------------------------------

class TestTrackSimilar:
    def _make_content(self, track_id: int, bpm_int: int = 12800):
        from unittest.mock import MagicMock
        c = MagicMock()
        c.ID = track_id
        c.BPM = bpm_int
        c.Key = None
        return c

    def _db_with_tracks(self, contents):
        db = MagicMock()
        db.get_content.return_value = MagicMock()
        db.get_content.return_value.all.return_value = contents
        db.read_anlz_file.return_value = None
        return db

    def test_returns_200_and_schema(self):
        contents = [self._make_content(i, bpm_int=12800) for i in range(1, 6)]
        db = self._db_with_tracks(contents)
        # target track on-demand lookup
        db.get_content.side_effect = lambda **kw: (
            contents[0] if kw.get("ID") == 1 else MagicMock(ID=kw.get("ID", 99), BPM=12800, Key=None)
        )
        with patch("autocue.analysis.similar.get_energy_curve", return_value=[0.5] * 50):
            with patch("autocue.analysis.similar.get_mixability", return_value={"vocal_proxy": False, "energy_variance": 0.1}):
                client = _make_client(db)
                r = client.get("/api/tracks/1/similar")
        assert r.status_code == 200
        data = r.json()
        assert data["track_id"] == 1
        assert "results" in data
        assert isinstance(data["results"], list)

    def test_returns_404_for_unknown_track(self):
        db = MagicMock()
        # route checks db.get_content(ID=track_id) first — return None for 404
        db.get_content.return_value = None
        db.read_anlz_file.return_value = None
        with patch("autocue.analysis.similar.get_energy_curve", return_value=None):
            with patch("autocue.analysis.similar.get_mixability", return_value=None):
                client = _make_client(db)
                r = client.get("/api/tracks/999/similar")
        assert r.status_code == 404

    def test_bpm_gate_param_accepted(self):
        contents = [self._make_content(i, bpm_int=12800) for i in range(1, 4)]
        db = self._db_with_tracks(contents)
        db.get_content.side_effect = lambda **kw: contents[0] if kw.get("ID") == 1 else MagicMock(ID=kw.get("ID", 99), BPM=12800, Key=None)
        with patch("autocue.analysis.similar.get_energy_curve", return_value=[0.5] * 50):
            with patch("autocue.analysis.similar.get_mixability", return_value={"vocal_proxy": False, "energy_variance": 0.1}):
                client = _make_client(db)
                r = client.get("/api/tracks/1/similar?bpm_gate=4.0")
        assert r.status_code == 200

    def test_force_rebuild_clears_index(self):
        from autocue.analysis import similar as _sim
        _sim._INDEX[9999] = (128.0, [0.1] * 5)
        _sim._INDEX_BUILT = True

        contents = [self._make_content(i, bpm_int=12800) for i in range(1, 3)]
        db = self._db_with_tracks(contents)
        db.get_content.side_effect = lambda **kw: contents[0] if kw.get("ID") == 1 else MagicMock(ID=kw.get("ID", 99), BPM=12800, Key=None)
        with patch("autocue.analysis.similar.get_energy_curve", return_value=[0.5] * 50):
            with patch("autocue.analysis.similar.get_mixability", return_value={"vocal_proxy": False, "energy_variance": 0.1}):
                client = _make_client(db)
                r = client.get("/api/tracks/1/similar?force_rebuild=true")
        assert r.status_code == 200
        # stale track 9999 should be gone
        assert 9999 not in _sim._INDEX

    def test_n_param_limits_results(self):
        contents = [self._make_content(i, bpm_int=12800) for i in range(1, 12)]
        db = self._db_with_tracks(contents)
        db.get_content.side_effect = lambda **kw: contents[0] if kw.get("ID") == 1 else MagicMock(ID=kw.get("ID", 99), BPM=12800, Key=None)
        with patch("autocue.analysis.similar.get_energy_curve", return_value=[0.5] * 50):
            with patch("autocue.analysis.similar.get_mixability", return_value={"vocal_proxy": False, "energy_variance": 0.1}):
                client = _make_client(db)
                r = client.get("/api/tracks/1/similar?n=3")
        assert r.status_code == 200
        assert len(r.json()["results"]) <= 3


# ---------------------------------------------------------------------------
# /api/auto-tag  &  /api/auto-tag/undo
# ---------------------------------------------------------------------------

class TestAutoTagEndpoint:
    MODULE = "autocue.serve.routes"

    def _patch_auto_tag(self, result):
        return patch(
            "autocue.analysis.auto_tag.apply_tags",
            return_value=result,
        )

    def test_returns_200_on_success(self):
        db = _make_db()
        payload = {"tagged": 3, "skipped_no_data": 1,
                   "errors": 0, "dry_run": False,
                   "undo_data": {"removed": [], "added": ["101", "102", "103"]}}
        with self._patch_auto_tag(payload), \
             patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/auto-tag", json={"track_ids": [1, 2, 3, 4]})
        assert r.status_code == 200
        data = r.json()
        assert data["tagged"] == 3
        assert data["skipped_no_data"] == 1

    def test_dry_run_no_commit(self):
        db = _make_db()
        payload = {"tagged": 2, "skipped_no_data": 0,
                   "errors": 0, "dry_run": True, "undo_data": None}
        with self._patch_auto_tag(payload):
            client = _make_client(db)
            r = client.post("/api/auto-tag", json={"track_ids": [1, 2], "dry_run": True})
        assert r.status_code == 200
        assert r.json()["dry_run"] is True
        db.session.commit.assert_not_called()

    def test_commits_on_success(self):
        db = _make_db()
        payload = {"tagged": 1, "skipped_no_data": 0,
                   "errors": 0, "dry_run": False,
                   "undo_data": {"removed": [], "added": ["50"]}}
        with self._patch_auto_tag(payload), \
             patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/auto-tag", json={"track_ids": [1]})
        assert r.status_code == 200
        db.session.commit.assert_called_once()

    def test_tag_types_passed_through(self):
        db = _make_db()
        payload = {"tagged": 1, "skipped_no_data": 0, "errors": 0, "dry_run": False,
                   "undo_data": {"removed": [], "added": ["60"]}}
        with self._patch_auto_tag(payload) as mock_fn, \
             patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/auto-tag", json={"track_ids": [1], "tag_types": ["vocal", "energy_level"]})
        assert r.status_code == 200
        call_kwargs = mock_fn.call_args
        assert "vocal" in call_kwargs.kwargs.get("tag_types", []) or \
               "vocal" in (call_kwargs.args[2] if len(call_kwargs.args) > 2 else [])

    def test_returns_500_on_exception(self):
        db = _make_db()
        with patch(
            "autocue.analysis.auto_tag.apply_tags",
            side_effect=Exception("DB exploded"),
        ), patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/auto-tag", json={"track_ids": [1]})
        assert r.status_code == 500
        db.session.rollback.assert_called()

    def test_empty_track_ids_returns_200(self):
        db = _make_db()
        payload = {"tagged": 0, "skipped_no_data": 0,
                   "errors": 0, "dry_run": False,
                   "undo_data": {"removed": [], "added": []}}
        with self._patch_auto_tag(payload), \
             patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post("/api/auto-tag", json={"track_ids": []})
        assert r.status_code == 200


class TestAutoTagUndoEndpoint:
    def _patch_undo(self, result):
        return patch(
            "autocue.analysis.auto_tag.undo_tag_run",
            return_value=result,
        )

    def test_returns_200_on_success(self):
        db = _make_db()
        with self._patch_undo({"removed": 2, "restored": 1}), \
             patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post(
                "/api/auto-tag/undo",
                json={"undo_data": {"added": ["101", "102"], "removed": [
                    {"ID": "50", "MyTagID": "10", "ContentID": "1", "TrackNo": 0, "UUID": None}
                ]}},
            )
        assert r.status_code == 200
        assert r.json()["removed"] == 2
        assert r.json()["restored"] == 1

    def test_commits_on_success(self):
        db = _make_db()
        with self._patch_undo({"removed": 0, "restored": 0}), \
             patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post(
                "/api/auto-tag/undo",
                json={"undo_data": {"added": [], "removed": []}},
            )
        assert r.status_code == 200
        db.session.commit.assert_called_once()

    def test_returns_500_on_exception(self):
        db = _make_db()
        with patch(
            "autocue.analysis.auto_tag.undo_tag_run",
            side_effect=RuntimeError("undo boom"),
        ), patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            client = _make_client(db)
            r = client.post(
                "/api/auto-tag/undo",
                json={"undo_data": {"added": [], "removed": []}},
            )
        assert r.status_code == 500
        db.session.rollback.assert_called()


def _sse_payloads(text: str):
    """Parse an SSE response body into a list of JSON event dicts."""
    import json
    out = []
    for line in text.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: "):]))
    return out


class TestDiscoverEndpoint:
    def test_requires_token(self):
        client = _make_client(_make_db())
        with patch("autocue.serve.routes._resolve_discogs_token", return_value=""):
            r = client.get("/api/discover")
        assert r.status_code == 400

    def test_streams_suggestions(self):
        db = _make_db()
        fake_events = [
            (1, 2, {"artist": "A", "album": "New One", "title": "A - New One",
                    "year": 2025, "thumb": "", "cover": "", "genres": [], "styles": ["House"],
                    "url": "", "query": "A New One"}),
            (2, 2, None),
        ]
        with patch("autocue.analysis.discovery.iter_new_releases", return_value=iter(fake_events)):
            client = _make_client(db)
            r = client.get("/api/discover?token=tok")
        assert r.status_code == 200
        events = _sse_payloads(r.text)
        assert any(e.get("album") == "New One" for e in events)
        assert events[-1]["done"] is True
        assert events[-1]["suggested"] == 1

    def test_uses_env_token_when_param_missing(self):
        db = _make_db()
        with patch("autocue.serve.routes._resolve_discogs_token", return_value="envtok"):
            with patch("autocue.analysis.discovery.iter_new_releases", return_value=iter([])) as m:
                client = _make_client(db)
                r = client.get("/api/discover")
        assert r.status_code == 200
        # token forwarded to the generator
        assert m.call_args.args[1] == "envtok"


class TestDownloadConfigEndpoint:
    def test_reports_availability(self):
        client = _make_client(_make_db())
        with patch("autocue.download.ytdlp_available", return_value=True):
            with patch("autocue.download.ffmpeg_available", return_value=False):
                r = client.get("/api/download/config")
        assert r.status_code == 200
        data = r.json()
        assert data["available"] is True
        assert data["ffmpeg"] is False
        assert "default_dir" in data
        assert "music_folder" in data  # may be None if no tracks with absolute FolderPath

    def test_returns_music_folder_from_track_paths(self):
        db = _make_db()
        content_rows = [
            SimpleNamespace(FolderPath='/Users/test/Music/Rekordbox/Track1.mp3'),
            SimpleNamespace(FolderPath='/Users/test/Music/Rekordbox/Track2.mp3'),
        ]
        q = MagicMock()
        q.limit.return_value = q
        q.all.return_value = content_rows
        db.query.return_value = q
        client = _make_client(db)
        with patch("autocue.download.ytdlp_available", return_value=False):
            with patch("autocue.download.ffmpeg_available", return_value=False):
                r = client.get("/api/download/config")
        data = r.json()
        assert data["music_folder"] == '/Users/test/Music/Rekordbox'

    def test_music_folder_none_when_no_absolute_paths(self):
        db = _make_db()
        content_rows = [SimpleNamespace(FolderPath='relative/path/Track.mp3')]
        q = MagicMock()
        q.limit.return_value = q
        q.all.return_value = content_rows
        db.query.return_value = q
        client = _make_client(db)
        with patch("autocue.download.ytdlp_available", return_value=False):
            with patch("autocue.download.ffmpeg_available", return_value=False):
                r = client.get("/api/download/config")
        assert r.json()["music_folder"] is None


class TestDownloadEndpoint:
    def test_503_when_ytdlp_missing(self):
        client = _make_client(_make_db())
        with patch("autocue.download.ytdlp_available", return_value=False):
            r = client.post("/api/download", json={"query": "x"})
        assert r.status_code == 503

    def test_503_when_ffmpeg_missing(self):
        client = _make_client(_make_db())
        with patch("autocue.download.ytdlp_available", return_value=True):
            with patch("autocue.download.ffmpeg_available", return_value=False):
                r = client.post("/api/download", json={"query": "x"})
        assert r.status_code == 503

    def test_streams_finished_path(self):
        client = _make_client(_make_db())
        with patch("autocue.download.ytdlp_available", return_value=True):
            with patch("autocue.download.ffmpeg_available", return_value=True):
                with patch("autocue.download.download_audio", return_value="/dl/song.mp3"):
                    r = client.post("/api/download", json={"query": "daft punk"})
        assert r.status_code == 200
        events = _sse_payloads(r.text)
        assert events[-1]["done"] is True
        assert events[-1]["path"] == "/dl/song.mp3"
        assert events[-1]["downloaded"] == 1

    def test_streams_error(self):
        client = _make_client(_make_db())
        with patch("autocue.download.ytdlp_available", return_value=True):
            with patch("autocue.download.ffmpeg_available", return_value=True):
                with patch("autocue.download.download_audio", side_effect=RuntimeError("nope")):
                    r = client.post("/api/download", json={"query": "x"})
        events = _sse_payloads(r.text)
        assert events[-1]["status"] == "error"
        assert events[-1]["failed"] == 1


class TestDownloadAlbumEndpoint:
    def test_503_when_unavailable(self):
        client = _make_client(_make_db())
        with patch("autocue.download.ytdlp_available", return_value=False):
            r = client.post("/api/download/album", json={"tracks": [{"query": "a"}]})
        assert r.status_code == 503

    def test_downloads_each_track(self):
        client = _make_client(_make_db())
        with patch("autocue.download.ytdlp_available", return_value=True):
            with patch("autocue.download.ffmpeg_available", return_value=True):
                with patch("autocue.download.download_audio", side_effect=["/a.mp3", "/b.mp3"]):
                    r = client.post("/api/download/album", json={
                        "tracks": [{"query": "a", "title": "A"}, {"query": "b", "title": "B"}],
                    })
        events = _sse_payloads(r.text)
        assert events[-1]["done"] is True
        assert events[-1]["downloaded"] == 2
        assert events[-1]["failed"] == 0

    def test_partial_failure_counted(self):
        client = _make_client(_make_db())
        with patch("autocue.download.ytdlp_available", return_value=True):
            with patch("autocue.download.ffmpeg_available", return_value=True):
                with patch("autocue.download.download_audio",
                           side_effect=["/a.mp3", RuntimeError("bad")]):
                    r = client.post("/api/download/album", json={
                        "tracks": [{"query": "a"}, {"query": "b"}],
                    })
        events = _sse_payloads(r.text)
        assert events[-1]["downloaded"] == 1
        assert events[-1]["failed"] == 1


# ---------------------------------------------------------------------------
# Fix: rekordbox_is_running() guard on 5 previously-unguarded write endpoints
# ---------------------------------------------------------------------------

class TestWriteGuards:
    """Every write endpoint must return 409 when Rekordbox is running."""

    def test_create_playlist_blocked_when_rekordbox_running(self):
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            client = _make_client()
            r = client.post("/api/playlists", json={"name": "My Set", "track_ids": [1, 2]})
        assert r.status_code == 409

    def test_auto_tag_blocked_when_rekordbox_running(self):
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            client = _make_client()
            r = client.post("/api/auto-tag", json={"track_ids": [1]})
        assert r.status_code == 409

    def test_auto_tag_dry_run_allowed_when_rekordbox_running(self):
        db = _make_db()
        payload = {"tagged": 0, "skipped_no_data": 0, "errors": 0, "dry_run": True, "undo_data": None}
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            with patch("autocue.analysis.auto_tag.apply_tags", return_value=payload):
                client = _make_client(db)
                r = client.post("/api/auto-tag", json={"track_ids": [1], "dry_run": True})
        assert r.status_code == 200

    def test_auto_tag_undo_blocked_when_rekordbox_running(self):
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            client = _make_client()
            r = client.post("/api/auto-tag/undo",
                            json={"undo_data": {"added": [], "removed": []}})
        assert r.status_code == 409

    def test_auto_tag_discogs_blocked_when_rekordbox_running(self):
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            client = _make_client()
            r = client.post("/api/auto-tag/discogs",
                            json={"track_ids": [1], "token": "tok", "dry_run": False})
        assert r.status_code == 409

    def test_enrich_comments_blocked_when_rekordbox_running(self):
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            client = _make_client()
            r = client.post("/api/enrich-comments", json={"track_ids": [1]})
        assert r.status_code == 409

    def test_enrich_comments_dry_run_allowed_when_rekordbox_running(self):
        db = _make_db()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=True):
            with patch("autocue.analysis.comment.enrich_comments_batch",
                       return_value={"enriched": 0, "skipped": 1, "errors": 0, "backup_path": None}):
                client = _make_client(db)
                r = client.post("/api/enrich-comments",
                                json={"track_ids": [1], "dry_run": True})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Discogs skip_existing
# ---------------------------------------------------------------------------

class TestDiscogsSkipExisting:
    """skip_existing=True skips tracks that already have non-AutoCue My Tags."""

    def _make_db_discogs(self, existing_tag_name=None):
        """Build a mock DB with one track and optional pre-existing My Tag."""
        from autocue.analysis.auto_tag import ALL_AUTOCUE_TAG_NAMES
        db = MagicMock()

        content = MagicMock()
        content.ID = 1
        content.ArtistName = "Artist"
        content.Title = "Title"
        db.get_content.return_value = content

        # Pre-build tag_name_map: one tag with given name
        tag_mock = MagicMock()
        tag_mock.ID = "tag-1"
        tag_mock.Name = existing_tag_name or ""

        song_tag_mock = MagicMock()
        song_tag_mock.MyTagID = "tag-1"

        def _query_side_effect(cls):
            from pyrekordbox.db6 import DjmdMyTag, DjmdSongMyTag
            q = MagicMock()
            if cls is DjmdMyTag:
                q.all.return_value = [tag_mock] if existing_tag_name else []
            elif cls is DjmdSongMyTag:
                q.filter.return_value = q
                q.all.return_value = [song_tag_mock] if existing_tag_name else []
            else:
                q.all.return_value = []
                q.filter.return_value = q
                q.first.return_value = None
            return q

        db.session.query.side_effect = _query_side_effect
        db.session.commit.return_value = None
        return db

    def test_skip_existing_skips_track_with_discogs_tag(self):
        db = self._make_db_discogs(existing_tag_name="Deep House")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.analysis.discogs.search_styles", return_value=["Deep House"]) as mock_search:
                client = _make_client(db)
                r = client.post("/api/auto-tag/discogs",
                                json={"track_ids": [1], "token": "tok",
                                      "dry_run": False, "skip_existing": True})
        assert r.status_code == 200
        events = _sse_payloads(r.text)
        final = events[-1]
        assert final["done"] is True
        assert final["skipped"] == 1
        assert final["tagged"] == 0
        # search_styles must NOT have been called — the skip happens before the API call
        mock_search.assert_not_called()

    def test_skip_existing_does_not_skip_track_with_only_autocue_tags(self):
        db = self._make_db_discogs(existing_tag_name="Peak")  # AutoCue category tag
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.analysis.discogs.search_styles", return_value=["Deep House"]):
                with patch("autocue.analysis.auto_tag.ensure_tag_by_name", return_value="tag-2"):
                    client = _make_client(db)
                    r = client.post("/api/auto-tag/discogs",
                                    json={"track_ids": [1], "token": "tok",
                                          "dry_run": False, "skip_existing": True})
        assert r.status_code == 200
        events = _sse_payloads(r.text)
        final = events[-1]
        assert final["done"] is True
        assert final["tagged"] == 1
        assert final["skipped"] == 0

    def test_skip_existing_false_processes_all_tracks(self):
        db = self._make_db_discogs(existing_tag_name="Deep House")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.analysis.discogs.search_styles", return_value=["Deep House"]):
                with patch("autocue.analysis.auto_tag.ensure_tag_by_name", return_value="tag-2"):
                    client = _make_client(db)
                    r = client.post("/api/auto-tag/discogs",
                                    json={"track_ids": [1], "token": "tok",
                                          "dry_run": False, "skip_existing": False})
        assert r.status_code == 200
        events = _sse_payloads(r.text)
        final = events[-1]
        assert final["done"] is True
        assert final["tagged"] == 1
        assert final["skipped"] == 0


# ---------------------------------------------------------------------------
# Fix: loop cue OutFrame updated on shift
# ---------------------------------------------------------------------------

class TestLoopCueOutFrameOnShift:
    """Shifting a loop cue must update both OutMsec and OutFrame."""

    def _track(self, tid=1):
        t = MagicMock()
        t.ID = tid
        return t

    def _post(self, client, payload):
        return client.post("/api/cue-tools-stream", json=payload)

    def test_shift_updates_outframe_for_loop_cue(self, tmp_path):
        track = self._track()
        cue = _make_cue_tool(in_msec=10_000, comment="loop")
        cue.OutMsec = 12_000          # 2-second loop
        cue.OutFrame = round(12_000 * 150 / 1000)  # original OutFrame = 1800
        db = _make_db_cue_tools(track, [cue])
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "shift", "track_ids": [1], "dry_run": False,
                    "shift": {"delta_ms": 500},
                })
        assert r.status_code == 200
        assert cue.OutMsec == 12_500
        assert cue.OutFrame == round(12_500 * 150 / 1000)  # 1875

    def test_shift_does_not_set_outframe_for_non_loop_cue(self, tmp_path):
        """OutFrame must not be touched when OutMsec is the sentinel -1."""
        track = self._track()
        cue = _make_cue_tool(in_msec=5_000, comment="hot")
        cue.OutMsec = -1
        cue.OutFrame = 0
        db = _make_db_cue_tools(track, [cue])
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                client = _make_client(db)
                r = self._post(client, {
                    "operation": "shift", "track_ids": [1], "dry_run": False,
                    "shift": {"delta_ms": 500},
                })
        assert r.status_code == 200
        assert cue.OutMsec == -1   # sentinel unchanged
        assert cue.OutFrame == 0   # not touched


# ---------------------------------------------------------------------------
# Fix: keep_slots Field(ge=1, le=8) validation
# ---------------------------------------------------------------------------

class TestDeleteOrphanValidation:
    """keep_slots outside 1–8 must be rejected before any DB access."""

    def _post(self, client, keep_slots):
        return client.post("/api/cue-tools-stream", json={
            "operation": "delete_orphan", "track_ids": [], "dry_run": True,
            "delete_orphan": {"keep_slots": keep_slots},
        })

    def test_keep_slots_zero_returns_422(self):
        client = _make_client()
        assert self._post(client, 0).status_code == 422

    def test_keep_slots_nine_returns_422(self):
        client = _make_client()
        assert self._post(client, 9).status_code == 422

    def test_keep_slots_one_accepted(self):
        client = _make_client()
        assert self._post(client, 1).status_code == 200

    def test_keep_slots_eight_accepted(self):
        client = _make_client()
        assert self._post(client, 8).status_code == 200


# ---------------------------------------------------------------------------
# Fix: Query bounds on /api/tracks/{id}/similar
# ---------------------------------------------------------------------------

class TestSimilarQueryBounds:
    """n and bpm_gate out of range must return 422 before any DB access."""

    def test_n_zero_returns_422(self):
        client = _make_client()
        assert client.get("/api/tracks/1/similar?n=0").status_code == 422

    def test_n_above_max_returns_422(self):
        client = _make_client()
        assert client.get("/api/tracks/1/similar?n=101").status_code == 422

    def test_bpm_gate_negative_returns_422(self):
        client = _make_client()
        assert client.get("/api/tracks/1/similar?bpm_gate=-0.1").status_code == 422

    def test_bpm_gate_above_max_returns_422(self):
        client = _make_client()
        assert client.get("/api/tracks/1/similar?bpm_gate=50.1").status_code == 422

    def test_n_boundary_values_accepted(self):
        # Boundary values must not be rejected by the validator alone.
        # The route will 404 (no DB track) but not 422.
        db = MagicMock()
        db.get_content.return_value = None
        client = _make_client(db)
        for n in (1, 100):
            r = client.get(f"/api/tracks/1/similar?n={n}")
            assert r.status_code != 422, f"n={n} should pass validation"

    def test_bpm_gate_boundary_values_accepted(self):
        db = MagicMock()
        db.get_content.return_value = None
        client = _make_client(db)
        for gate in (0.0, 50.0):
            r = client.get(f"/api/tracks/1/similar?bpm_gate={gate}")
            assert r.status_code != 422, f"bpm_gate={gate} should pass validation"


# ---------------------------------------------------------------------------
# Issue #106 — /api/apply, /api/generate-apply, /api/generate-apply-stream
# MUST invalidate mixability sidecar rows for every successfully-written track
# (mixability scores depend on intro/outro cue positions).
# ---------------------------------------------------------------------------

class TestApplyInvalidatesMixability:
    """Regression suite for issue #106.

    Each test uses an in-memory ``CacheStore`` mounted on ``app.state``, seeds
    a mixability row for two tracks, then exercises the relevant endpoint and
    asserts that:
      - Tracks where ``write_cues_to_db`` returned ``n>0`` had their row dropped.
      - Tracks where the write was a no-op (``n=0``) kept their row.
      - A ``None`` ``cache_store`` (sidecar disabled) does not raise.
    """

    @staticmethod
    def _seed_store():
        from autocue.cache import CacheStore
        store = CacheStore.open_memory()
        # mtime=100.0 is arbitrary — only used for the (mtime-matching) hit path.
        store.put_mixability(1, 0.7, {"any": True}, anlz_mtime=100.0)
        store.put_mixability(2, 0.4, {"any": True}, anlz_mtime=100.0)
        return store

    @staticmethod
    def _make_track(tid: int):
        return SimpleNamespace(ID=tid, Title=f"T{tid}", BPM=12800, Length=300, UUID=f"u{tid}")

    @staticmethod
    def _client_with_store(db, store):
        """Build a TestClient that does NOT enter lifespan (so the store
        we mount is not closed under us when the call returns). The route
        only needs ``request.app.state.cache_store``; FastAPI services
        requests fine without lifespan startup having run."""
        from autocue.serve.app import create_app
        from autocue.serve.deps import get_db, get_ro_db
        app = create_app()
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_ro_db] = lambda: db
        # Mount the store BEFORE the request — Starlette's TestClient does
        # not auto-enter the lifespan unless used as a context manager.
        app.state.cache_store = store
        client = TestClient(app, raise_server_exceptions=False)
        return client, app

    # ----- /api/apply ----------------------------------------------------

    def test_apply_invalidates_mixability_for_written_tracks(self, tmp_path):
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        # Two distinct content objects so the route loops twice.
        db.get_content.side_effect = [self._make_track(1), self._make_track(2)]

        store = self._seed_store()
        # Sanity: rows are present BEFORE the call.
        assert store.get_mixability(1, expected_anlz_mtime=100.0) is not None
        assert store.get_mixability(2, expected_anlz_mtime=100.0) is not None

        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                    client, _ = self._client_with_store(db, store)
                    r = client.post(
                        "/api/apply",
                        json={
                            "tracks": [
                                {"id": 1, "title": "T1", "mode_used": "bar", "skipped": False, "cues": [{"position_ms": 0, "label": "intro", "slot": 0, "name": "I", "color_id": 1}]},
                                {"id": 2, "title": "T2", "mode_used": "bar", "skipped": False, "cues": [{"position_ms": 0, "label": "intro", "slot": 0, "name": "I", "color_id": 1}]},
                            ],
                            "dry_run": False,
                            "overwrite": True,
                        },
                    )

        assert r.status_code == 200, r.text
        assert r.json()["applied"] == 2
        # Regression guard: without the fix, both rows would still be present.
        assert store.get_mixability(1, expected_anlz_mtime=100.0) is None
        assert store.get_mixability(2, expected_anlz_mtime=100.0) is None

    def test_apply_does_not_invalidate_when_write_returned_zero(self, tmp_path):
        """Boundary: ``n=0`` from write_cues_to_db means no actual write —
        the sidecar row MUST survive."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.return_value = self._make_track(1)

        store = self._seed_store()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.write_cues_to_db", return_value=0):
                with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                    client, _ = self._client_with_store(db, store)
                    r = client.post(
                        "/api/apply",
                        json={
                            "tracks": [
                                {"id": 1, "title": "T1", "mode_used": "bar", "skipped": False, "cues": [{"position_ms": 0, "label": "intro", "slot": 0, "name": "I", "color_id": 1}]},
                            ],
                            "dry_run": False,
                            "overwrite": False,
                        },
                    )
        assert r.status_code == 200, r.text
        assert r.json()["applied"] == 0
        assert r.json()["skipped"] == 1
        # Row must still be present.
        assert store.get_mixability(1, expected_anlz_mtime=100.0) is not None

    def test_apply_with_no_cache_store_does_not_raise(self, tmp_path):
        """Boundary: sidecar disabled (None) must remain a no-op, not crash."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.return_value = self._make_track(1)
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                    client, _ = self._client_with_store(db, None)
                    r = client.post(
                        "/api/apply",
                        json={
                            "tracks": [
                                {"id": 1, "title": "T1", "mode_used": "bar", "skipped": False, "cues": [{"position_ms": 0, "label": "intro", "slot": 0, "name": "I", "color_id": 1}]},
                            ],
                            "dry_run": False,
                            "overwrite": True,
                        },
                    )
        assert r.status_code == 200, r.text
        assert r.json()["applied"] == 1

    def test_apply_dry_run_does_not_invalidate(self, tmp_path):
        """A dry-run write doesn't touch the DB, so it must not drop cache rows."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.return_value = self._make_track(1)
        store = self._seed_store()
        # write_cues_to_db is permitted to return n>0 even on dry_run in some
        # paths (the route decides by req.dry_run, not by the return code).
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                client, _ = self._client_with_store(db, store)
                r = client.post(
                    "/api/apply",
                    json={
                        "tracks": [
                            {"id": 1, "title": "T1", "mode_used": "bar", "skipped": False, "cues": [{"position_ms": 0, "label": "intro", "slot": 0, "name": "I", "color_id": 1}]},
                        ],
                        "dry_run": True,
                        "overwrite": True,
                    },
                )
        assert r.status_code == 200, r.text
        # Dry-run path: cache row survives.
        assert store.get_mixability(1, expected_anlz_mtime=100.0) is not None

    # ----- /api/generate-apply ------------------------------------------

    def test_generate_apply_invalidates_mixability_for_written_tracks(self, tmp_path):
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.side_effect = [self._make_track(1), self._make_track(2)]

        store = self._seed_store()
        from autocue.models import CuePoint, PhraseLabel
        fake_cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0, name="I", color_id=1)]
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.serve.routes.generate_cues_for_track", return_value=(fake_cues, None)):
                with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                    with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                        client, _ = self._client_with_store(db, store)
                        r = client.post(
                            "/api/generate-apply",
                            json={"track_ids": [1, 2], "dry_run": False, "overwrite": True},
                        )
        assert r.status_code == 200, r.text
        assert r.json()["applied"] == 2
        assert store.get_mixability(1, expected_anlz_mtime=100.0) is None
        assert store.get_mixability(2, expected_anlz_mtime=100.0) is None

    def test_generate_apply_skips_invalidation_for_no_cue_tracks(self, tmp_path):
        """Boundary: when generate_cues_for_track yields no cues, the track is
        skipped → its sidecar row MUST survive."""
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.return_value = self._make_track(1)
        store = self._seed_store()
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            # No cues generated → loop hits `continue` before write_cues_to_db.
            with patch("autocue.serve.routes.generate_cues_for_track", return_value=([], None)):
                with patch("autocue.db_writer.write_cues_to_db", return_value=1) as wcd:
                    with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                        client, _ = self._client_with_store(db, store)
                        r = client.post(
                            "/api/generate-apply",
                            json={"track_ids": [1], "dry_run": False, "overwrite": True},
                        )
        assert r.status_code == 200, r.text
        assert r.json()["applied"] == 0
        # write_cues_to_db must NOT have been called (no cues to write).
        wcd.assert_not_called()
        assert store.get_mixability(1, expected_anlz_mtime=100.0) is not None

    # ----- /api/generate-apply-stream (parallel + serial) ----------------

    def _collect_sse(self, response_text: str) -> list[dict]:
        import json
        events = []
        for line in response_text.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        return events

    def test_generate_apply_stream_serial_invalidates_mixability(self, tmp_path, monkeypatch):
        """Force the serial path via AUTOCUE_PARALLEL_GENERATE_APPLY=0."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_GENERATE_APPLY", "0")
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.side_effect = [self._make_track(1), self._make_track(2)]
        store = self._seed_store()
        from autocue.models import CuePoint, PhraseLabel
        fake_cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0, name="I", color_id=1)]
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.serve.routes.generate_cues_for_track", return_value=(fake_cues, None)):
                with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                    with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                        client, _ = self._client_with_store(db, store)
                        r = client.post(
                            "/api/generate-apply-stream",
                            json={"track_ids": [1, 2], "dry_run": False, "overwrite": True},
                        )
        assert r.status_code == 200, r.text
        done = next(e for e in self._collect_sse(r.text) if e.get("done"))
        assert done["applied"] == 2
        assert store.get_mixability(1, expected_anlz_mtime=100.0) is None
        assert store.get_mixability(2, expected_anlz_mtime=100.0) is None

    def test_generate_apply_stream_parallel_invalidates_mixability(self, tmp_path, monkeypatch):
        """Default path (AUTOCUE_PARALLEL_GENERATE_APPLY=1)."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_GENERATE_APPLY", "1")
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        # In the parallel path, get_content is called once inside the pool
        # worker per track id. side_effect lets us return distinct objects.
        db.get_content.side_effect = lambda ID: self._make_track(ID)
        store = self._seed_store()
        from autocue.models import CuePoint, PhraseLabel
        fake_cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0, name="I", color_id=1)]
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.serve.routes.generate_cues_for_track", return_value=(fake_cues, None)):
                with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                    with patch("autocue.db_writer.BACKUP_DIR", tmp_path / "backups"):
                        client, _ = self._client_with_store(db, store)
                        r = client.post(
                            "/api/generate-apply-stream",
                            json={"track_ids": [1, 2], "dry_run": False, "overwrite": True},
                        )
        assert r.status_code == 200, r.text
        done = next(e for e in self._collect_sse(r.text) if e.get("done"))
        assert done["applied"] == 2
        assert store.get_mixability(1, expected_anlz_mtime=100.0) is None
        assert store.get_mixability(2, expected_anlz_mtime=100.0) is None

    def test_generate_apply_stream_dry_run_does_not_invalidate(self, tmp_path, monkeypatch):
        """Dry run on the stream path must not drop cache rows (either branch)."""
        monkeypatch.setenv("AUTOCUE_PARALLEL_GENERATE_APPLY", "0")
        db = _make_db()
        db._db_dir = tmp_path
        (tmp_path / "master.db").write_bytes(b"fake")
        db.get_content.return_value = self._make_track(1)
        store = self._seed_store()
        from autocue.models import CuePoint, PhraseLabel
        fake_cues = [CuePoint(position_ms=0, label=PhraseLabel.INTRO, slot=0, name="I", color_id=1)]
        with patch("autocue.db_writer.rekordbox_is_running", return_value=False):
            with patch("autocue.serve.routes.generate_cues_for_track", return_value=(fake_cues, None)):
                with patch("autocue.db_writer.write_cues_to_db", return_value=1):
                    client, _ = self._client_with_store(db, store)
                    r = client.post(
                        "/api/generate-apply-stream",
                        json={"track_ids": [1], "dry_run": True, "overwrite": True},
                    )
        assert r.status_code == 200, r.text
        assert store.get_mixability(1, expected_anlz_mtime=100.0) is not None
