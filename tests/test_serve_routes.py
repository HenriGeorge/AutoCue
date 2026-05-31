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
        from autocue.serve.deps import get_db
        app.dependency_overrides[get_db] = lambda: db
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
        # DjmdSongPlaylist returns one entry
        sp_q = MagicMock()
        sp_q.filter_by.return_value = [SimpleNamespace(ContentID="1")]
        db.query.side_effect = lambda cls: pl_q if "Playlist" in cls.__name__ else sp_q
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

    def test_has_phrase_true_when_anlz_ext_exists(self):
        db = _make_db()
        track = SimpleNamespace(
            ID=1, Title="T", ArtistName="A", AlbumName="", BPM=12800, Length=300, KeyID=None,
        )
        q = self._make_content_q(db, [track])
        key_q = MagicMock()
        key_q.all.return_value = []
        db.query.return_value = key_q
        db.get_anlz_path.return_value = "/some/path/ANLZ0000.EXT"
        with patch("autocue.serve.routes.has_existing_hot_cues", return_value=0):
            client = _make_client(db)
            r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["has_phrase"] is True

    def test_has_phrase_false_when_anlz_ext_missing(self):
        db = _make_db()
        track = SimpleNamespace(
            ID=1, Title="T", ArtistName="A", AlbumName="", BPM=12800, Length=300, KeyID=None,
        )
        q = self._make_content_q(db, [track])
        key_q = MagicMock()
        key_q.all.return_value = []
        db.query.return_value = key_q
        db.get_anlz_path.return_value = ""
        with patch("autocue.serve.routes.has_existing_hot_cues", return_value=0):
            client = _make_client(db)
            r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["has_phrase"] is False

    def test_has_phrase_false_on_anlz_exception(self):
        db = _make_db()
        track = SimpleNamespace(
            ID=1, Title="T", ArtistName="A", AlbumName="", BPM=12800, Length=300, KeyID=None,
        )
        q = self._make_content_q(db, [track])
        key_q = MagicMock()
        key_q.all.return_value = []
        db.query.return_value = key_q
        db.get_anlz_path.side_effect = Exception("ANLZ error")
        with patch("autocue.serve.routes.has_existing_hot_cues", return_value=0):
            client = _make_client(db)
            r = client.get("/api/tracks")
        assert r.status_code == 200
        assert r.json()[0]["has_phrase"] is False


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
