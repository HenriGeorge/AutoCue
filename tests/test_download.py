"""Tests for autocue/download.py — yt-dlp wrapper (yt-dlp itself is mocked)."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from autocue import download as dl


# ---------------------------------------------------------------------------
# availability / config helpers
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_ytdlp_available_true_when_importable(self):
        with patch.dict(sys.modules, {"yt_dlp": MagicMock()}):
            assert dl.ytdlp_available() is True

    def test_ytdlp_available_false_when_missing(self):
        with patch("builtins.__import__", side_effect=ImportError):
            assert dl.ytdlp_available() is False

    def test_ffmpeg_available_uses_which(self):
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            assert dl.ffmpeg_available() is True
        with patch("shutil.which", return_value=None):
            assert dl.ffmpeg_available() is False

    def test_default_dir_env_override(self, monkeypatch):
        monkeypatch.setenv("AUTOCUE_DOWNLOAD_DIR", "/custom/dl")
        assert dl.default_download_dir() == "/custom/dl"

    def test_default_dir_fallback(self, monkeypatch):
        monkeypatch.delenv("AUTOCUE_DOWNLOAD_DIR", raising=False)
        assert dl.default_download_dir().endswith("Music/AutoCue")


# ---------------------------------------------------------------------------
# _build_query
# ---------------------------------------------------------------------------

class TestBuildQuery:
    def test_passes_through_https_url(self):
        url = "https://www.youtube.com/watch?v=abc"
        assert dl._build_query(url) == url

    def test_passes_through_http_url(self):
        assert dl._build_query("http://youtu.be/x").startswith("http://")

    def test_wraps_search_terms(self):
        assert dl._build_query("daft punk one more time") == "ytsearch1:daft punk one more time"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            dl._build_query("   ")


# ---------------------------------------------------------------------------
# search_youtube
# ---------------------------------------------------------------------------

class TestSearchYoutube:
    def test_returns_empty_when_unavailable(self):
        with patch.object(dl, "ytdlp_available", return_value=False):
            assert dl.search_youtube("anything") == []

    def test_maps_entries(self):
        fake_info = {"entries": [
            {"id": "vid1", "title": "Song A", "duration": 200, "uploader": "Chan"},
            {"id": "vid2", "title": "Song B", "duration": 180, "channel": "Chan2"},
            None,
        ]}
        ydl = MagicMock()
        ydl.__enter__.return_value.extract_info.return_value = fake_info
        fake_module = SimpleNamespace(YoutubeDL=MagicMock(return_value=ydl))
        with patch.object(dl, "ytdlp_available", return_value=True):
            with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                results = dl.search_youtube("query", max_results=2)
        assert len(results) == 2
        assert results[0]["title"] == "Song A"
        assert results[0]["url"].endswith("vid1")
        assert results[1]["uploader"] == "Chan2"

    def test_search_failure_returns_empty(self):
        ydl = MagicMock()
        ydl.__enter__.return_value.extract_info.side_effect = RuntimeError("net")
        fake_module = SimpleNamespace(YoutubeDL=MagicMock(return_value=ydl))
        with patch.object(dl, "ytdlp_available", return_value=True):
            with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                assert dl.search_youtube("query") == []


# ---------------------------------------------------------------------------
# download_audio
# ---------------------------------------------------------------------------

class TestDownloadAudio:
    def test_raises_without_ytdlp(self):
        with patch.object(dl, "ytdlp_available", return_value=False):
            with pytest.raises(RuntimeError, match="yt-dlp is not installed"):
                dl.download_audio("query")

    def test_raises_without_ffmpeg(self):
        with patch.object(dl, "ytdlp_available", return_value=True):
            with patch.object(dl, "ffmpeg_available", return_value=False):
                with pytest.raises(RuntimeError, match="ffmpeg"):
                    dl.download_audio("query")

    def test_returns_final_path_from_requested_downloads(self, tmp_path):
        info = {"title": "My Song", "requested_downloads": [{"filepath": str(tmp_path / "My Song.mp3")}]}
        ydl = MagicMock()
        ydl.__enter__.return_value.extract_info.return_value = info
        fake_module = SimpleNamespace(YoutubeDL=MagicMock(return_value=ydl))
        with patch.object(dl, "ytdlp_available", return_value=True):
            with patch.object(dl, "ffmpeg_available", return_value=True):
                with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                    path = dl.download_audio("My Song", dest_dir=str(tmp_path))
        assert path.endswith("My Song.mp3")

    def test_unwraps_search_entries(self, tmp_path):
        info = {"entries": [{"title": "Found", "requested_downloads": [{"filepath": str(tmp_path / "Found.mp3")}]}]}
        ydl = MagicMock()
        ydl.__enter__.return_value.extract_info.return_value = info
        fake_module = SimpleNamespace(YoutubeDL=MagicMock(return_value=ydl))
        with patch.object(dl, "ytdlp_available", return_value=True):
            with patch.object(dl, "ffmpeg_available", return_value=True):
                with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                    path = dl.download_audio("search me", dest_dir=str(tmp_path))
        assert path.endswith("Found.mp3")

    def test_progress_callback_invoked(self, tmp_path):
        captured = []

        def run_extract(target, download=False):
            # simulate yt-dlp invoking the progress hook
            hook = ydl.__enter__.return_value._hook
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
            hook({"status": "finished", "filename": str(tmp_path / "X.webm")})
            return {"title": "X", "requested_downloads": [{"filepath": str(tmp_path / "X.mp3")}]}

        ydl = MagicMock()

        def ydl_ctor(opts):
            # stash the progress hook so run_extract can call it
            ydl.__enter__.return_value._hook = opts["progress_hooks"][0]
            return ydl
        ydl.__enter__.return_value.extract_info.side_effect = run_extract
        fake_module = SimpleNamespace(YoutubeDL=MagicMock(side_effect=ydl_ctor))
        with patch.object(dl, "ytdlp_available", return_value=True):
            with patch.object(dl, "ffmpeg_available", return_value=True):
                with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                    dl.download_audio("X", dest_dir=str(tmp_path), progress_cb=captured.append)
        statuses = [c["status"] for c in captured]
        assert "downloading" in statuses and "finished" in statuses


class TestCancelEvent:
    """Bug fix: client disconnect must stop the download worker."""

    def _make_mock_ydl(self):
        """Build a MagicMock yt-dlp that exposes the progress hook for direct calls."""
        ydl = MagicMock()

        def ydl_ctor(opts):
            ydl.__enter__.return_value._hook = opts["progress_hooks"][0]
            return ydl
        fake_module = SimpleNamespace(YoutubeDL=MagicMock(side_effect=ydl_ctor))
        return ydl, fake_module

    def test_cancel_event_set_before_call_aborts_immediately(self, tmp_path):
        import threading
        ydl, fake_module = self._make_mock_ydl()

        def run_extract(target, download=False):
            hook = ydl.__enter__.return_value._hook
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100})
            return {"title": "X"}
        ydl.__enter__.return_value.extract_info.side_effect = run_extract

        cancel = threading.Event()
        cancel.set()  # already cancelled

        with patch.object(dl, "ytdlp_available", return_value=True):
            with patch.object(dl, "ffmpeg_available", return_value=True):
                with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                    with pytest.raises(dl.DownloadCancelled):
                        dl.download_audio("X", dest_dir=str(tmp_path), cancel_event=cancel)

    def test_cancel_event_set_mid_download_raises_in_hook(self, tmp_path):
        import threading
        ydl, fake_module = self._make_mock_ydl()
        cancel = threading.Event()

        def run_extract(target, download=False):
            hook = ydl.__enter__.return_value._hook
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100})
            cancel.set()
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
            return {"title": "X"}
        ydl.__enter__.return_value.extract_info.side_effect = run_extract

        with patch.object(dl, "ytdlp_available", return_value=True):
            with patch.object(dl, "ffmpeg_available", return_value=True):
                with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                    with pytest.raises(dl.DownloadCancelled):
                        dl.download_audio("X", dest_dir=str(tmp_path), cancel_event=cancel)

    def test_no_cancel_event_runs_normally(self, tmp_path):
        ydl, fake_module = self._make_mock_ydl()

        def run_extract(target, download=False):
            hook = ydl.__enter__.return_value._hook
            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 100})
            hook({"status": "finished", "filename": str(tmp_path / "X.webm")})
            return {"title": "X", "requested_downloads": [{"filepath": str(tmp_path / "X.mp3")}]}
        ydl.__enter__.return_value.extract_info.side_effect = run_extract

        with patch.object(dl, "ytdlp_available", return_value=True):
            with patch.object(dl, "ffmpeg_available", return_value=True):
                with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                    path = dl.download_audio("X", dest_dir=str(tmp_path))
        assert path.endswith("X.mp3")

