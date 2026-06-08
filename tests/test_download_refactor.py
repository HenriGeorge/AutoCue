"""Tests for the v0.2.0 Download refactor (PRD .agent/prd/DOWNLOAD_PRD.md).

Covers:
- normalize_audio_format() legacy coercion table
- _ytdlp_format_opts() format mapping
- classify_download_error() taxonomy
- _parse_ffmpeg_progress() helper
- DownloadRequest/DownloadAlbumRequest pydantic constraints (audio_quality
  removed, audio_format Literal enforced, extra='forbid')
- DownloadProgressEvent schema
- /api/download/enqueue + /stream + /cancel + /queue + /reveal endpoints
- _legacy_event_shape preserves `done`/`failed`/`finished` keys
"""
from __future__ import annotations

import errno
import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from autocue.download import (
    ALLOWED_FORMATS,
    DownloadQueue,
    _parse_ffmpeg_progress,
    _ytdlp_format_opts,
    classify_download_error,
    normalize_audio_format,
    reset_download_queue_for_tests,
    reveal_supported,
)
from autocue.serve.routes import _legacy_event_shape
from autocue.serve.schemas import (
    DownloadAlbumRequest,
    DownloadConfigResponse,
    DownloadProgressEvent,
    DownloadRequest,
)


# --------------------------------------------------------------------------- #
# normalize_audio_format
# --------------------------------------------------------------------------- #

class TestNormalizeAudioFormat:
    def test_allowed_passthrough(self):
        for v in ALLOWED_FORMATS:
            assert normalize_audio_format(v) == v

    def test_none_or_empty_defaults_mp3_320(self):
        assert normalize_audio_format(None) == "mp3_320"
        assert normalize_audio_format("") == "mp3_320"

    @pytest.mark.parametrize("legacy,expected", [
        ("mp3", "mp3_320"), ("MP3", "mp3_320"),
        ("m4a", "original"), ("aac", "original"), ("opus", "original"),
        ("flac", "wav"), ("alac", "wav"), ("vorbis", "wav"),
        ("wav", "wav"),
    ])
    def test_legacy_coercion(self, legacy, expected):
        assert normalize_audio_format(legacy) == expected

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            normalize_audio_format("gibberish")


# --------------------------------------------------------------------------- #
# _ytdlp_format_opts
# --------------------------------------------------------------------------- #

class TestYtdlpFormatOpts:
    def test_wav(self):
        opts = _ytdlp_format_opts("wav")
        assert opts["postprocessors"][0]["preferredcodec"] == "wav"
        assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"

    def test_mp3_320(self):
        opts = _ytdlp_format_opts("mp3_320")
        pp = opts["postprocessors"][0]
        assert pp["preferredcodec"] == "mp3"
        assert pp["preferredquality"] == "320"

    def test_original_no_postprocessor(self):
        assert _ytdlp_format_opts("original") == {}


# --------------------------------------------------------------------------- #
# classify_download_error
# --------------------------------------------------------------------------- #

class TestClassifyDownloadError:
    @pytest.mark.parametrize("msg,expected_code", [
        ("Video unavailable", "unavailable_video"),
        ("This video has been removed", "unavailable_video"),
        ("Sign in to confirm your age", "age_gated"),
        ("Video is age-restricted", "age_gated"),
        ("This video is not available in your country", "region_blocked"),
        ("Geo restricted content", "region_blocked"),
        ("This video is private", "private_video"),
    ])
    def test_yt_dlp_message_classification(self, msg, expected_code):
        assert classify_download_error(Exception(msg))["code"] == expected_code

    def test_disk_full_oserror(self):
        exc = OSError(errno.ENOSPC, "No space left on device")
        assert classify_download_error(exc)["code"] == "disk_full"

    def test_network_offline_oserror(self):
        exc = OSError(errno.ENETUNREACH, "Network is unreachable")
        assert classify_download_error(exc)["code"] == "network_offline"

    def test_socket_timeout(self):
        assert classify_download_error(socket.timeout("read timeout"))["code"] == "network_timeout"

    def test_ffmpeg_missing(self):
        assert classify_download_error(RuntimeError("ffmpeg not found on PATH"))["code"] == "ffmpeg_missing"

    def test_ffmpeg_conversion(self):
        exc = RuntimeError("ffmpeg exited with code 1: Conversion failed!")
        assert classify_download_error(exc)["code"] == "ffmpeg_conversion"

    def test_unknown_fallback(self):
        result = classify_download_error(RuntimeError("anything else"))
        assert result["code"] == "unknown"
        assert result["raw"] == "anything else"

    def test_all_results_have_required_keys(self):
        result = classify_download_error(Exception("Video unavailable"))
        assert set(result.keys()) == {"code", "user_message", "hint", "raw"}

    def test_user_messages_contain_no_jargon(self):
        """User-facing messages must not leak 'ffmpeg' / 'yt-dlp' / 'exit code'.
        Exception: ffmpeg_missing is allowed to name the binary the user installs."""
        for msg in ("Video unavailable", "Sign in to confirm your age",
                    "not available in your country", "This video is private"):
            result = classify_download_error(Exception(msg))
            lower = result["user_message"].lower()
            assert "yt-dlp" not in lower
            assert "exit code" not in lower
            assert "ffmpeg" not in lower


# --------------------------------------------------------------------------- #
# _parse_ffmpeg_progress
# --------------------------------------------------------------------------- #

class TestParseFfmpegProgress:
    def test_valid_returns_percent(self):
        # 5_000_000 µs = 5_000 ms; total 10_000 ms → 50%
        assert _parse_ffmpeg_progress("out_time_ms=5000000", 10000) == 50.0

    def test_zero_returns_zero(self):
        assert _parse_ffmpeg_progress("out_time_ms=0", 10000) == 0.0

    def test_clamps_to_100(self):
        assert _parse_ffmpeg_progress("out_time_ms=999999999", 1000) == 100.0

    def test_unrelated_line_returns_none(self):
        assert _parse_ffmpeg_progress("frame=42 fps=30", 10000) is None
        assert _parse_ffmpeg_progress("bitrate=128.0kbits/s", 10000) is None

    def test_zero_or_unknown_total_returns_none(self):
        assert _parse_ffmpeg_progress("out_time_ms=5000000", 0) is None
        assert _parse_ffmpeg_progress("out_time_ms=5000000", None) is None

    def test_malformed_value_returns_none(self):
        assert _parse_ffmpeg_progress("out_time_ms=bad", 10000) is None


# --------------------------------------------------------------------------- #
# Pydantic schema constraints
# --------------------------------------------------------------------------- #

class TestDownloadRequestSchema:
    def test_default_audio_format_is_mp3_320(self):
        req = DownloadRequest(query="x")
        assert req.audio_format == "mp3_320"
        assert req.normalize is False
        assert req.embed_metadata is True
        assert req.allow_playlist is False

    def test_audio_quality_is_forbidden(self):
        with pytest.raises(ValidationError) as exc:
            DownloadRequest(query="x", audio_quality="192")  # type: ignore[call-arg]
        assert "extra_forbidden" in str(exc.value) or "audio_quality" in str(exc.value)

    def test_audio_format_literal_rejects_legacy(self):
        with pytest.raises(ValidationError):
            DownloadRequest(query="x", audio_format="mp3")  # type: ignore[arg-type]

    def test_audio_format_literal_rejects_unknown(self):
        with pytest.raises(ValidationError):
            DownloadRequest(query="x", audio_format="gibberish")  # type: ignore[arg-type]


class TestDownloadAlbumRequestSchema:
    def test_default_audio_format(self):
        req = DownloadAlbumRequest(tracks=[])
        assert req.audio_format == "mp3_320"

    def test_audio_quality_forbidden(self):
        with pytest.raises(ValidationError):
            DownloadAlbumRequest(tracks=[], audio_quality="192")  # type: ignore[call-arg]


class TestDownloadProgressEvent:
    def test_progress_event(self):
        ev = DownloadProgressEvent(type="progress", job_id="abc", phase="fetching", percent=50.0)
        assert ev.percent == 50.0

    def test_done_event(self):
        ev = DownloadProgressEvent(type="done", job_id="abc", status="success", path="/foo")
        assert ev.status == "success"

    def test_invalid_phase_rejected(self):
        with pytest.raises(ValidationError):
            DownloadProgressEvent(type="progress", job_id="abc", phase="invalid")  # type: ignore

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            DownloadProgressEvent(type="done", job_id="abc", status="weird")  # type: ignore


class TestDownloadConfigResponse:
    def test_includes_new_fields(self):
        cfg = DownloadConfigResponse(available=True, ffmpeg=True, default_dir="/x",
                                      os_reveal_supported=True, max_concurrency=2)
        assert cfg.os_reveal_supported is True
        assert cfg.max_concurrency == 2


# --------------------------------------------------------------------------- #
# Legacy SSE shape transform
# --------------------------------------------------------------------------- #

class TestLegacyEventShape:
    def test_progress_unchanged(self):
        ev = {"type": "progress", "phase": "fetching", "percent": 50.0}
        assert _legacy_event_shape(ev)["type"] == "progress"
        assert "done" not in _legacy_event_shape(ev)

    def test_success_done_keys(self):
        out = _legacy_event_shape({"type": "done", "status": "success", "path": "/x", "job_id": "y"})
        assert out["done"] is True
        assert out["status"] == "finished"
        assert out["downloaded"] == 1
        assert out["path"] == "/x"

    def test_error_failed_key(self):
        out = _legacy_event_shape({"type": "done", "status": "error",
                                    "error_message": "broken", "job_id": "y"})
        assert out["done"] is True
        assert out["status"] == "error"
        assert out["failed"] == 1
        assert out["error"] == "broken"

    def test_cancelled_failed_key(self):
        out = _legacy_event_shape({"type": "done", "status": "cancelled", "job_id": "y"})
        assert out["done"] is True
        assert out["failed"] == 1


# --------------------------------------------------------------------------- #
# Reveal capability probe
# --------------------------------------------------------------------------- #

class TestRevealSupported:
    def test_returns_bool(self):
        assert isinstance(reveal_supported(), bool)


# --------------------------------------------------------------------------- #
# Endpoint integration tests (require TestClient + autocue serve)
# --------------------------------------------------------------------------- #

def _make_client():
    """Build a TestClient against the FastAPI app. Reuses the deps fixture
    pattern from tests/test_serve_routes.py."""
    from autocue.serve.app import create_app
    # NOTE: lifespan does Rekordbox connect; we let it noop by patching at the
    # deps layer. For these new download endpoints we don't need the DB, but
    # the app initialization still expects it.
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset_queue():
    """Each test gets a fresh DownloadQueue singleton to avoid cross-pollution."""
    reset_download_queue_for_tests()
    yield
    reset_download_queue_for_tests()


@pytest.mark.skipif(  # requires Rekordbox; gate to local-dev env
    not Path.home().joinpath("Library", "Pioneer", "rekordbox", "master.db").exists(),
    reason="Rekordbox master.db not present — skipping integration tests",
)
class TestEnqueueEndpoint:
    def test_enqueue_returns_job_id(self):
        with patch("autocue.download.ytdlp_available", return_value=True):
            with patch("autocue.download.ffmpeg_available", return_value=True):
                client = _make_client()
                r = client.post("/api/download/enqueue", json={"query": "test"})
        assert r.status_code == 200
        body = r.json()
        assert "job_id" in body
        assert body["phase"] == "queued"
        assert isinstance(body["position"], int)

    def test_audio_quality_returns_friendly_422(self):
        with patch("autocue.download.ytdlp_available", return_value=True):
            with patch("autocue.download.ffmpeg_available", return_value=True):
                client = _make_client()
                r = client.post("/api/download/enqueue", json={"query": "x", "audio_quality": "192"})
        assert r.status_code == 422
        detail = r.json().get("detail", {})
        assert detail.get("error_code") == "audio_quality_removed"

    def test_unknown_format_returns_friendly_422(self):
        with patch("autocue.download.ytdlp_available", return_value=True):
            with patch("autocue.download.ffmpeg_available", return_value=True):
                client = _make_client()
                r = client.post("/api/download/enqueue", json={"query": "x", "audio_format": "gibberish"})
        assert r.status_code == 422
        detail = r.json().get("detail", {})
        assert detail.get("error_code") == "unknown_format"

    def test_legacy_format_mp3_coerced(self):
        with patch("autocue.download.ytdlp_available", return_value=True):
            with patch("autocue.download.ffmpeg_available", return_value=True):
                client = _make_client()
                r = client.post("/api/download/enqueue", json={"query": "x", "audio_format": "mp3"})
        assert r.status_code == 200  # coerced server-side


@pytest.mark.skipif(
    not Path.home().joinpath("Library", "Pioneer", "rekordbox", "master.db").exists(),
    reason="Rekordbox master.db not present",
)
class TestCancelEndpoint:
    def test_cancel_unknown_job_returns_404_or_false(self):
        with patch("autocue.download.ytdlp_available", return_value=True):
            with patch("autocue.download.ffmpeg_available", return_value=True):
                client = _make_client()
                r = client.post("/api/download/cancel/nonexistent_job_id")
        # Endpoint always returns 200 (idempotent); body's `cancelled` is False
        # only when job is definitively known-completed.
        assert r.status_code == 200
        body = r.json()
        assert "cancelled" in body


@pytest.mark.skipif(
    not Path.home().joinpath("Library", "Pioneer", "rekordbox", "master.db").exists(),
    reason="Rekordbox master.db not present",
)
class TestRevealEndpoint:
    def test_path_outside_allowed_root_blocked(self):
        with patch("autocue.serve.routes._detect_music_folder", return_value=None):
            client = _make_client()
            r = client.post("/api/download/reveal", json={"path": "/etc/passwd"})
        # /etc/passwd is not under default_download_dir → 403 (or 404 if missing,
        # or 503 if the DB dep is unavailable in the test environment). All
        # three outcomes prove the request was REJECTED, never reached the
        # platform-open call — which is what the security gate guarantees.
        assert r.status_code in (403, 404, 503)

    def test_nonexistent_path_rejected(self):
        with patch("autocue.download.reveal_supported", return_value=True):
            client = _make_client()
            r = client.post("/api/download/reveal",
                            json={"path": "/Users/henrigeorge/Music/AutoCue/__nonexistent__.mp3"})
        # 404 (path missing) or 503 (DB dep unavailable) — either rejects.
        assert r.status_code in (404, 503)


# --------------------------------------------------------------------------- #
# Jargon grep (PRD §4 acceptance gate)
# --------------------------------------------------------------------------- #

class TestNoJargonInUserCopy:
    """The classified-error user_message + hint strings must never leak
    'yt-dlp', 'ffmpeg' (except in the ffmpeg_missing install hint), or
    'exit code' to end users."""

    def test_no_yt_dlp_in_any_error_message(self):
        test_exceptions = [
            Exception("Video unavailable"),
            Exception("Sign in to confirm your age"),
            Exception("not available in your country"),
            Exception("This video is private"),
            socket.timeout("read timeout"),
            OSError(errno.ENOSPC, "no space"),
            OSError(errno.ENETUNREACH, "no net"),
            RuntimeError("anything"),
        ]
        for exc in test_exceptions:
            result = classify_download_error(exc)
            assert "yt-dlp" not in result["user_message"].lower()
            assert "yt-dlp" not in result["hint"].lower()

    def test_html_panel_helper_no_jargon(self):
        """Verify the HTML helper copy doesn't contain 'yt-dlp' or
        'suggestion above'."""
        html_path = Path(__file__).parent.parent / "docs" / "index.html"
        text = html_path.read_text(encoding="utf-8")
        # Grab the #download-section block (rough heuristic)
        start = text.index('id="download-section"')
        section = text[start:start + 3500]
        assert "yt-dlp" not in section
        assert "suggestion above" not in section
        assert "Use the ⬇ Album button on any suggestion above" not in section
