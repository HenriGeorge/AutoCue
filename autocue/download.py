"""YouTube audio download — thin wrapper around yt-dlp.

yt-dlp (and ffmpeg, for audio extraction) are an OPTIONAL dependency. Install
with ``pip install -e ".[download]"`` and make sure ``ffmpeg`` is on PATH.
Everything here imports yt-dlp lazily so the core CLI/server runs without it.

LEGAL NOTE: downloading copyrighted audio from YouTube may violate YouTube's
Terms of Service and copyright law. This wrapper is provided for downloading
content the user is authorised to download (their own uploads, Creative-Commons
or otherwise licensed material). Lawful use is the user's responsibility.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class DownloadCancelled(Exception):
    """Raised inside yt-dlp's progress hook when a caller-supplied cancel_event
    is set (e.g. an SSE client disconnected). yt-dlp propagates the exception
    out of ``extract_info`` and the partial download is left in place — see the
    "Orphaned files" section of docs/reference/youtube-download.md."""


def ytdlp_available() -> bool:
    """True if yt-dlp can be imported in this environment."""
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False


def ffmpeg_available() -> bool:
    """True if an ffmpeg binary is discoverable on PATH (needed for audio extraction)."""
    import shutil
    return shutil.which("ffmpeg") is not None


def default_download_dir() -> str:
    """Resolve the default download directory.

    Order: ``AUTOCUE_DOWNLOAD_DIR`` env var, else ``~/Music/AutoCue``.
    """
    env = os.environ.get("AUTOCUE_DOWNLOAD_DIR")
    if env:
        return env
    return str(Path.home() / "Music" / "AutoCue")


def _build_query(url_or_query: str) -> str:
    """Pass through real URLs; wrap bare search terms as a single yt-dlp search."""
    s = (url_or_query or "").strip()
    if not s:
        raise ValueError("empty download query")
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return f"ytsearch1:{s}"


def search_youtube(query: str, max_results: int = 5) -> list[dict]:
    """Search YouTube and return lightweight metadata for the top results.

    Each dict: {url, title, duration, uploader, id}. Returns [] if yt-dlp is
    unavailable or the search fails.
    """
    if not ytdlp_available():
        return []
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
    except Exception as exc:
        logger.warning("YouTube search failed for %r: %s", query, exc)
        return []

    results = []
    for entry in (info or {}).get("entries", []) or []:
        if not entry:
            continue
        vid = entry.get("id", "")
        results.append({
            "url": entry.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else ""),
            "title": entry.get("title", ""),
            "duration": entry.get("duration"),
            "uploader": entry.get("uploader") or entry.get("channel", ""),
            "id": vid,
        })
    return results


def download_audio(
    url_or_query: str,
    dest_dir: str | None = None,
    audio_format: str = "mp3",
    audio_quality: str = "192",
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> str:
    """Download the best audio for a URL or search term and extract it to ``audio_format``.

    Returns the absolute path of the written audio file. Raises RuntimeError if
    yt-dlp or ffmpeg are unavailable, and propagates download errors.

    ``progress_cb`` (optional) receives yt-dlp progress dicts
    ({'status', 'downloaded_bytes', 'total_bytes', ...}) for streaming UIs.

    ``cancel_event`` (optional) is checked on every yt-dlp progress tick. When
    set, ``DownloadCancelled`` is raised inside the hook, which yt-dlp will
    propagate out of ``extract_info`` — callers should catch it. SSE handlers
    set this when the client disconnects so the daemon worker thread does not
    keep yt-dlp running indefinitely.
    """
    if not ytdlp_available():
        raise RuntimeError(
            "yt-dlp is not installed. Install with: pip install -e \".[download]\""
        )
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH — required to extract audio.")
    import yt_dlp

    dest = Path(dest_dir or default_download_dir())
    dest.mkdir(parents=True, exist_ok=True)

    written: dict[str, str] = {}

    def _hook(d: dict[str, Any]) -> None:
        # Check cancellation FIRST — even before progress_cb — so a long
        # download stops at the next tick when the client disconnects.
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("client disconnected")
        if progress_cb:
            try:
                progress_cb(d)
            except Exception:
                pass
        if d.get("status") == "finished":
            fn = d.get("filename")
            if fn:
                written["raw"] = fn

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(dest / "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "progress_hooks": [_hook],
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_format,
            "preferredquality": audio_quality,
        }],
    }

    target = _build_query(url_or_query)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(target, download=True)

    # ytsearch / playlist results nest the real entry under "entries"
    if info and "entries" in info:
        entries = [e for e in info.get("entries", []) if e]
        info = entries[0] if entries else info

    # Prefer yt-dlp's reported final path; fall back to the post-extraction name.
    final = None
    if info:
        try:
            final = info.get("requested_downloads", [{}])[0].get("filepath")
        except (IndexError, AttributeError, TypeError):
            final = None
    if not final and written.get("raw"):
        raw = Path(written["raw"])
        final = str(raw.with_suffix(f".{audio_format}"))
    if not final and info:
        title = info.get("title", "audio")
        final = str(dest / f"{title}.{audio_format}")

    return str(Path(final).resolve()) if final else ""
