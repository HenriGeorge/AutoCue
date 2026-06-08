"""YouTube audio download — wraps yt-dlp + ffmpeg behind a job queue.

yt-dlp (and ffmpeg, for audio extraction / loudness normalization) are an
OPTIONAL dependency. Install with ``pip install -e ".[download]"`` and make
sure ``ffmpeg`` is on PATH. Everything here imports yt-dlp lazily so the
core CLI/server runs without it.

Architecture (PRD v1.0, .agent/prd/DOWNLOAD_PRD.md):
- ``download_audio()`` is the per-job worker: yt-dlp fetch → optional
  ffmpeg loudness normalize → optional metadata embed.
- ``DownloadQueue`` is a process-singleton: single FIFO, configurable
  concurrency via ``AUTOCUE_DOWNLOAD_CONCURRENCY`` env (default 1, max 4).
  Decouples HTTP request lifetimes from worker lifetimes so cancel via
  ``POST /api/download/cancel/{job_id}`` can target a job before its first
  SSE event lands.
- ``classify_download_error()`` maps yt-dlp / ffmpeg / OS exceptions to
  user-facing error codes for the frontend renderer.

LEGAL NOTE: downloading copyrighted audio from YouTube may violate
YouTube's Terms of Service and copyright law. This wrapper is provided
for downloading content the user is authorised to download (their own
uploads, Creative-Commons or licensed material). Lawful use is the user's
responsibility.
"""
from __future__ import annotations

import errno
import logging
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

logger = logging.getLogger(__name__)

# Public format keys exposed via the API (DownloadRequest.audio_format Literal).
AudioFormat = Literal["wav", "mp3_320", "original"]
ALLOWED_FORMATS: tuple[str, ...] = ("wav", "mp3_320", "original")

# Legacy values silently coerced (removed in v0.3.0). See CHANGELOG + PRD §6.2.
_LEGACY_FORMAT_MAP: dict[str, str] = {
    "mp3": "mp3_320",
    "m4a": "original",
    "aac": "original",
    "opus": "original",
    "flac": "wav",
    "alac": "wav",
    "vorbis": "wav",
    "wav": "wav",  # case-normalize
}

# Set of legacy values already log-warned in this process — second hit drops to DEBUG.
_legacy_warned: set[str] = set()
_legacy_warned_lock = threading.Lock()


class DownloadCancelled(Exception):
    """Raised inside yt-dlp's progress hook OR ffmpeg watcher when a caller-
    supplied cancel_event is set (e.g. an SSE client clicked Cancel or
    disconnected). Bubbles out so the worker thread can clean up partial
    files and emit a final ``status='cancelled'`` SSE event."""


# --------------------------------------------------------------------------- #
# Capability probes
# --------------------------------------------------------------------------- #

def ytdlp_available() -> bool:
    """True if yt-dlp can be imported in this environment."""
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False


def ffmpeg_available() -> bool:
    """True if an ffmpeg binary is discoverable on PATH."""
    return shutil.which("ffmpeg") is not None


def default_download_dir() -> str:
    """Resolve the default download directory.

    Order: ``AUTOCUE_DOWNLOAD_DIR`` env var, else ``~/Music/AutoCue``.
    """
    env = os.environ.get("AUTOCUE_DOWNLOAD_DIR")
    if env:
        return env
    return str(Path.home() / "Music" / "AutoCue")


# --------------------------------------------------------------------------- #
# Format-key handling (PRD §6.2)
# --------------------------------------------------------------------------- #

def normalize_audio_format(value: str | None) -> str:
    """Map any incoming ``audio_format`` value to an allowed key.

    Returns one of ``ALLOWED_FORMATS``. Raises ValueError on unknown input.
    Legacy values (``"mp3"``, ``"m4a"`` …) are coerced + log-warned (once per
    process per legacy value, then DEBUG). See PRD §6.2.
    """
    if value is None or value == "":
        return "mp3_320"
    if value in ALLOWED_FORMATS:
        return value
    key = value.lower().strip()
    if key in _LEGACY_FORMAT_MAP:
        mapped = _LEGACY_FORMAT_MAP[key]
        with _legacy_warned_lock:
            first = key not in _legacy_warned
            if first:
                _legacy_warned.add(key)
        msg = f"audio_format={value!r} is deprecated; coerced to {mapped!r}. Removed in 0.3.0."
        (logger.warning if first else logger.debug)(msg)
        return mapped
    raise ValueError(
        f"Unknown audio_format {value!r}. Pick one of {', '.join(ALLOWED_FORMATS)}."
    )


def _ytdlp_format_opts(audio_format: str) -> dict[str, Any]:
    """Return yt-dlp opts fragment for the given normalized format key.

    Caller merges this into the full yt-dlp opts dict. ``'original'`` returns
    an empty fragment — yt-dlp's ``bestaudio/best`` keeps the source
    container (m4a / webm) and no re-encode happens.
    """
    if audio_format == "wav":
        return {"postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}]}
    if audio_format == "mp3_320":
        return {"postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }]}
    if audio_format == "original":
        return {}
    raise ValueError(f"Unknown audio_format {audio_format!r}")


def _metadata_postprocessors() -> list[dict[str, Any]]:
    """yt-dlp postprocessors that write Artist/Title tags from the video title.

    Falls through cleanly when the "Artist - Title" regex doesn't match.

    The ``action`` MUST be an enum value from
    ``yt_dlp.postprocessor.MetadataParserPP.Actions``, NOT a string. Passing
    a string raises ``AssertionError`` inside ``MetadataParserPP.__init__``
    with an empty message — surfacing as a useless 'AssertionError' to the
    user. Importing lazily so the module still loads when yt-dlp is missing.
    """
    try:
        from yt_dlp.postprocessor import MetadataParserPP
        interpretter = MetadataParserPP.interpretter
    except Exception:
        # If yt-dlp is missing the MetadataParser postprocessor is unusable;
        # return only FFmpegMetadata which writes the raw video title as Title.
        return [{"key": "FFmpegMetadata", "add_metadata": True}]
    return [
        {"key": "FFmpegMetadata", "add_metadata": True},
        {
            "key": "MetadataParser",
            "actions": [(
                interpretter,
                "title",
                r"(?P<artist>.+?) ?[-–—] ?(?P<title>.+)",
            )],
        },
    ]


# --------------------------------------------------------------------------- #
# YouTube search + playlist preflight
# --------------------------------------------------------------------------- #

def search_youtube(query: str, max_results: int = 5) -> list[dict]:
    """Search YouTube and return lightweight metadata for the top results."""
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


def expand_playlist(url: str) -> list[dict]:
    """Return ``[{query, title}]`` for each entry in a YouTube playlist URL.

    Empty list if not a playlist URL or if extract fails. ``query`` is the
    webpage URL of the entry, suitable for feeding back into ``download_audio()``.
    """
    if not ytdlp_available():
        return []
    import yt_dlp

    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        logger.warning("playlist expand failed for %r: %s", url, exc)
        return []

    entries = (info or {}).get("entries") or []
    out: list[dict] = []
    for e in entries:
        if not e:
            continue
        vid = e.get("id") or ""
        webpage = e.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else None)
        if not webpage:
            continue
        out.append({"query": webpage, "title": e.get("title") or ""})
    return out


def _build_query(url_or_query: str) -> str:
    """Pass through real URLs; wrap bare search terms as a single yt-dlp search."""
    s = (url_or_query or "").strip()
    if not s:
        raise ValueError("empty download query")
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return f"ytsearch1:{s}"


# --------------------------------------------------------------------------- #
# ffmpeg loudness normalization (two-pass loudnorm)
# --------------------------------------------------------------------------- #

_LOUDNORM_RE = re.compile(
    r'"input_i"\s*:\s*"(?P<i>-?\d+\.\d+)".*?'
    r'"input_tp"\s*:\s*"(?P<tp>-?\d+\.\d+)".*?'
    r'"input_lra"\s*:\s*"(?P<lra>-?\d+\.\d+)".*?'
    r'"input_thresh"\s*:\s*"(?P<thresh>-?\d+\.\d+)".*?'
    r'"target_offset"\s*:\s*"(?P<offset>-?\d+\.\d+)"',
    re.DOTALL,
)


def _parse_loudnorm_pass1(stderr: str) -> dict[str, str] | None:
    """Extract input_i / input_tp / input_lra / input_thresh / target_offset
    from ffmpeg loudnorm pass-1 stderr. Returns None on parse failure."""
    m = _LOUDNORM_RE.search(stderr)
    if not m:
        return None
    return m.groupdict()


def _parse_ffmpeg_progress(line: str, total_ms: int | None) -> float | None:
    """Parse ``out_time_ms=NNN`` from ffmpeg ``-progress pipe:1`` output.

    ffmpeg's ``out_time_ms`` is actually MICROseconds — the name lies. We
    divide by 1000 to get milliseconds for the (got / total) ratio.

    Returns 0-100 percent when both ``out_time_ms`` and ``total_ms`` are valid;
    None for lines without time data or when total is unknown.
    """
    if not line.startswith("out_time_ms="):
        return None
    if not total_ms or total_ms <= 0:
        return None
    try:
        out_us = int(line.split("=", 1)[1].strip())
    except (ValueError, IndexError):
        return None
    pct = (out_us / 1000.0) / total_ms * 100.0
    return max(0.0, min(100.0, round(pct, 1)))


def _spawn_ffmpeg(
    args: list[str],
    cancel_event: threading.Event | None = None,
    capture_stderr: bool = True,
    on_pulse: Callable[[], None] | None = None,
) -> tuple[int, str]:
    """Run ffmpeg with cancel-watcher + pulse sidecar; collect stderr.

    Returns ``(returncode, stderr_text)``. Raises ``DownloadCancelled`` if
    cancel_event fires; ``RuntimeError`` on non-zero exit when not cancelled.
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostats", *args]
    logger.debug("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture_stderr else subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )
    cancel_fired = {"flag": False}
    stop = threading.Event()

    def _pulse() -> None:
        while not stop.is_set():
            if proc.poll() is not None:
                return
            if on_pulse:
                try: on_pulse()
                except Exception: pass
            stop.wait(5.0)

    def _watch() -> None:
        while not stop.is_set():
            if cancel_event is not None and cancel_event.is_set():
                cancel_fired["flag"] = True
                proc.terminate()
                try: proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired: proc.kill()
                return
            if proc.poll() is not None:
                return
            stop.wait(0.1)

    if on_pulse:
        threading.Thread(target=_pulse, daemon=True).start()
    if cancel_event is not None:
        threading.Thread(target=_watch, daemon=True).start()

    stderr_text = (proc.stderr.read() if proc.stderr else "") or ""
    proc.wait()
    stop.set()

    if cancel_fired["flag"]:
        raise DownloadCancelled("client disconnected during ffmpeg")
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg exited with code {proc.returncode}: "
            f"{(stderr_text.splitlines() or ['(no stderr)'])[-1]}"
        )
    return proc.returncode, stderr_text


def normalize_loudness_to_lufs(
    src_path: str,
    target_lufs: float = -14.0,
    dest_path: str | None = None,
    audio_format: str = "wav",
    cancel_event: threading.Event | None = None,
    progress_cb: Callable[[float | None, str], None] | None = None,
    on_pulse: Callable[[], None] | None = None,
) -> str:
    """Two-pass ffmpeg loudnorm. Returns final path.

    Pass 1 measures input statistics (loudness, peak, range, threshold).
    Pass 2 applies the filter with measured values + writes the result.

    ``progress_cb(percent, phase)`` is called with phase ``"normalizing_pass1"``
    (indeterminate — pass-1 emits no progress) and ``"normalizing_pass2"`` with
    real 0-100 percent parsed from ``-progress pipe:1``.

    ``on_pulse()`` is called every ~5 s while ffmpeg is alive — used by the
    DownloadQueue watchdog to prove worker liveness during silent stretches.
    """
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH — required for loudness normalization.")

    src = Path(src_path)
    if not src.exists():
        raise RuntimeError(f"normalize source not found: {src_path}")

    if audio_format == "wav":
        codec_args = ["-c:a", "pcm_s16le"]
        ext = ".wav"
    elif audio_format == "mp3_320":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "320k"]
        ext = ".mp3"
    else:
        raise ValueError(
            "normalize_loudness_to_lufs: audio_format must be 'wav' or 'mp3_320', "
            f"got {audio_format!r}. (Normalization is unsupported for 'original'.)"
        )

    if dest_path is None:
        dest_path = str(src.with_suffix("")) + ".norm" + ext

    # PASS 1: measure
    if progress_cb:
        progress_cb(None, "normalizing_pass1")
    pass1_args = [
        "-i", str(src),
        "-af", f"loudnorm=I={target_lufs}:LRA=11:TP=-1:print_format=json",
        "-f", "null", "-",
    ]
    _, stderr1 = _spawn_ffmpeg(
        pass1_args, cancel_event=cancel_event, capture_stderr=True, on_pulse=on_pulse,
    )

    measured = _parse_loudnorm_pass1(stderr1)
    if not measured:
        logger.warning("loudnorm pass-1 parse failed; falling back to single-pass.")
        single_args = [
            "-i", str(src),
            "-af", f"loudnorm=I={target_lufs}:LRA=11:TP=-1",
            *codec_args,
            "-y", dest_path,
        ]
        _spawn_ffmpeg(single_args, cancel_event=cancel_event, on_pulse=on_pulse)
        return dest_path

    # Probe duration for percent
    total_ms: int | None = None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            total_ms = int(float(out.stdout.strip()) * 1000)
    except Exception:
        pass

    af = (
        f"loudnorm=I={target_lufs}:LRA=11:TP=-1"
        f":measured_I={measured['i']}:measured_TP={measured['tp']}"
        f":measured_LRA={measured['lra']}:measured_thresh={measured['thresh']}"
        f":offset={measured['offset']}:linear=true:print_format=summary"
    )
    pass2_args = [
        "-i", str(src),
        "-af", af,
        *codec_args,
        "-progress", "pipe:1",
        "-y", dest_path,
    ]
    cmd = ["ffmpeg", "-hide_banner", "-nostats", *pass2_args]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
    )

    stop = threading.Event()
    cancel_fired = {"flag": False}

    def _pulse() -> None:
        while not stop.is_set():
            if proc.poll() is not None:
                return
            if on_pulse:
                try: on_pulse()
                except Exception: pass
            stop.wait(5.0)

    def _watch() -> None:
        while not stop.is_set():
            if cancel_event is not None and cancel_event.is_set():
                cancel_fired["flag"] = True
                proc.terminate()
                try: proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired: proc.kill()
                return
            if proc.poll() is not None:
                return
            stop.wait(0.1)

    if on_pulse:
        threading.Thread(target=_pulse, daemon=True).start()
    if cancel_event is not None:
        threading.Thread(target=_watch, daemon=True).start()

    if proc.stdout:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            pct = _parse_ffmpeg_progress(line, total_ms)
            if pct is not None and progress_cb:
                progress_cb(pct, "normalizing_pass2")
    stderr2 = (proc.stderr.read() if proc.stderr else "") or ""
    proc.wait()
    stop.set()

    if cancel_fired["flag"]:
        raise DownloadCancelled("client disconnected during loudnorm pass 2")
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg loudnorm pass-2 failed with code {proc.returncode}: "
            f"{(stderr2.splitlines() or ['(no stderr)'])[-1]}"
        )

    if progress_cb:
        progress_cb(100.0, "normalizing_pass2")
    return dest_path


# --------------------------------------------------------------------------- #
# Single-track download (PRD §6 + §7.1)
# --------------------------------------------------------------------------- #

def download_audio(
    url_or_query: str,
    dest_dir: str | None = None,
    audio_format: str = "mp3_320",
    allow_playlist: bool = False,
    embed_metadata: bool = True,
    normalize_lufs: float | None = None,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    cancel_event: threading.Event | None = None,
    output_artifacts: set[Path] | None = None,
    on_pulse: Callable[[], None] | None = None,
) -> str:
    """Download best audio + run optional postprocessing. Returns absolute path.

    Raises:
        RuntimeError on missing yt-dlp/ffmpeg.
        DownloadCancelled if ``cancel_event`` fires mid-fetch or mid-normalize.
        Other exceptions propagate from yt-dlp / ffmpeg.

    ``audio_format`` must be one of ``ALLOWED_FORMATS`` (caller is responsible
    for coercing legacy values via ``normalize_audio_format``).

    ``output_artifacts`` is an optional set the caller populates for cleanup
    on cancel/error (see DownloadQueue worker). The worker tracks every file
    yt-dlp + ffmpeg create during this job; the finally block in the queue
    worker removes anything in the set that doesn't match the returned path
    AND whose mtime ≥ job.started_at.
    """
    if not ytdlp_available():
        raise RuntimeError(
            'yt-dlp is not installed. Install with: pip install -e ".[download]"'
        )
    audio_format = normalize_audio_format(audio_format)
    needs_ffmpeg = audio_format != "original" or normalize_lufs is not None or embed_metadata
    if needs_ffmpeg and not ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH — required to extract / process audio.")
    import yt_dlp

    dest = Path(dest_dir or default_download_dir())
    dest.mkdir(parents=True, exist_ok=True)

    written: dict[str, str] = {}
    artifacts = output_artifacts if output_artifacts is not None else set()

    def _hook(d: dict[str, Any]) -> None:
        # Check cancellation FIRST so the next yt-dlp tick aborts cleanly.
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("client disconnected")
        if on_pulse:
            try: on_pulse()
            except Exception: pass
        if progress_cb:
            try: progress_cb(d)
            except Exception: pass
        fn = d.get("filename")
        if fn:
            try:
                artifacts.add(Path(fn).resolve())
            except Exception:
                pass
        if d.get("status") == "finished" and fn:
            written["raw"] = fn

    base_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": str(dest / "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": not allow_playlist,
        "progress_hooks": [_hook],
    }
    fmt_opts = _ytdlp_format_opts(audio_format)
    postprocessors: list[dict[str, Any]] = list(fmt_opts.get("postprocessors", []))
    if embed_metadata:
        postprocessors.extend(_metadata_postprocessors())
    if postprocessors:
        base_opts["postprocessors"] = postprocessors

    target = _build_query(url_or_query) if not allow_playlist else (url_or_query.strip() or _build_query(url_or_query))

    with yt_dlp.YoutubeDL(base_opts) as ydl:
        info = ydl.extract_info(target, download=True)

    if info and "entries" in info:
        entries = [e for e in info.get("entries", []) if e]
        info = entries[0] if entries else info

    final: str | None = None
    if info:
        try:
            final = info.get("requested_downloads", [{}])[0].get("filepath")
        except (IndexError, AttributeError, TypeError):
            final = None
    if not final and written.get("raw"):
        raw = Path(written["raw"])
        if audio_format == "original":
            final = str(raw)
        else:
            ext = ".mp3" if audio_format == "mp3_320" else ".wav"
            final = str(raw.with_suffix(ext))
    if not final and info:
        title = info.get("title", "audio")
        ext = ".mp3" if audio_format == "mp3_320" else (".wav" if audio_format == "wav" else ".m4a")
        final = str(dest / f"{title}{ext}")

    if not final:
        raise RuntimeError("yt-dlp did not return a downloadable file path.")
    final_path = str(Path(final).resolve())
    artifacts.add(Path(final_path))

    if normalize_lufs is not None and audio_format != "original":
        if progress_cb:
            progress_cb({"status": "normalizing"})
        norm_path = normalize_loudness_to_lufs(
            final_path,
            target_lufs=normalize_lufs,
            audio_format=audio_format,
            cancel_event=cancel_event,
            progress_cb=lambda pct, phase: (progress_cb({"status": phase, "percent": pct})
                                              if progress_cb else None),
            on_pulse=on_pulse,
        )
        artifacts.add(Path(norm_path))
        try:
            os.replace(norm_path, final_path)
        except OSError:
            return norm_path

    return final_path


# --------------------------------------------------------------------------- #
# Error classification (PRD §6.7)
# --------------------------------------------------------------------------- #

_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
    # ORDER MATTERS: region_blocked must precede unavailable_video, because
    # YouTube's "This video is not available in your country" matches both
    # patterns and the more-specific region message is the better signal.
    ("region_blocked",
     re.compile(r"not available in your country|not available in this region|geo restricted", re.I),
     "Not available in your region.",
     "Try a different upload of the same track."),
    ("unavailable_video",
     re.compile(r"video unavailable|this video has been removed|video is no longer available", re.I),
     "This video isn't available anymore.",
     "Try another search result."),
    ("age_gated",
     re.compile(r"sign in to confirm your age|age[- ]restricted", re.I),
     "YouTube requires sign-in for this video.",
     "Use a different source."),
    ("private_video",
     re.compile(r"this video is private|private video", re.I),
     "This video is private.",
     "Pick another candidate."),
    ("network_timeout",
     re.compile(r"timed? out|timeout|read timed out", re.I),
     "YouTube isn't responding.",
     "Try again in a moment."),
)


def classify_download_error(exc: BaseException) -> dict[str, str]:
    """Map an exception to a user-facing error dict.

    Returns ``{code, user_message, hint, raw}``. ``raw`` is ``str(exc)`` for
    the ``<details>Show technical details</details>`` disclosure.
    """
    raw = str(exc) or exc.__class__.__name__
    lower = raw.lower()

    if isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC:
            return {"code": "disk_full", "user_message": "No space left on disk.",
                    "hint": "Free up space and retry.", "raw": raw}
        if exc.errno in (errno.ENETUNREACH, errno.ENETDOWN, errno.EHOSTUNREACH):
            return {"code": "network_offline", "user_message": "No internet connection.",
                    "hint": "Reconnect and retry.", "raw": raw}

    if isinstance(exc, socket.timeout):
        return {"code": "network_timeout", "user_message": "YouTube isn't responding.",
                "hint": "Try again in a moment.", "raw": raw}

    if "ffmpeg not found" in lower or "ffmpeg is not installed" in lower:
        return {"code": "ffmpeg_missing", "user_message": "ffmpeg isn't installed on the server.",
                "hint": "Install it and restart the server.", "raw": raw}

    if "ffmpeg" in lower and ("exit" in lower or "conversion failed" in lower or "loudnorm" in lower):
        return {"code": "ffmpeg_conversion", "user_message": "Couldn't convert the audio.",
                "hint": "Try a different format (Original).", "raw": raw}

    for code, pat, msg, hint in _ERROR_PATTERNS:
        if pat.search(raw):
            return {"code": code, "user_message": msg, "hint": hint, "raw": raw}

    # If raw is just the class name (str(exc) was empty — common for
    # AssertionError and TypeError raised without a message), include the
    # class name explicitly in user_message so the user can file a useful
    # bug report. Also include the traceback's last frame in `raw` if one
    # is currently set on the exception, so Show technical details isn't
    # also empty.
    tb_tail = ""
    if exc.__traceback__ is not None:
        import traceback as _tb
        frames = _tb.extract_tb(exc.__traceback__)
        if frames:
            f = frames[-1]
            tb_tail = f"\n  at {f.filename}:{f.lineno} in {f.name}\n    {f.line or ''}"
    raw_with_tb = raw + tb_tail if tb_tail else raw
    return {
        "code": "unknown",
        "user_message": f"Something went wrong ({exc.__class__.__name__}).",
        "hint": "Try again, or open Show details for the raw error.",
        "raw": raw_with_tb,
    }


# --------------------------------------------------------------------------- #
# DownloadQueue (PRD §6.12) — process singleton
# --------------------------------------------------------------------------- #

@dataclass
class _Job:
    id: str
    kind: Literal["single", "album"]
    payload: dict
    enqueued_at: float
    started_at: float | None = None
    last_event_at: float | None = None
    worker_pulse_at: float | None = None
    phase: str | None = None
    title: str | None = None
    percent: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    event_bus: queue.Queue = field(default_factory=queue.Queue)
    final: dict | None = None
    final_at: float | None = None
    consumed: bool = False
    artifacts: set[Path] = field(default_factory=set)


def _ytdlp_pct(d: dict) -> float | None:
    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    got = d.get("downloaded_bytes")
    if total and got is not None:
        try: return round(min(100.0, got / total * 100.0), 1)
        except (TypeError, ZeroDivisionError): return None
    return None


class DownloadQueue:
    """Single in-process FIFO with bounded concurrency.

    Set concurrency via ``AUTOCUE_DOWNLOAD_CONCURRENCY`` env (default 1,
    clamped to ``[1, 4]``). All download endpoints route through this queue.
    """

    CACHE_TTL_S = 60.0  # Cached final-status TTL after `done` event

    def __init__(self, max_concurrency: int | None = None) -> None:
        if max_concurrency is None:
            try:
                env = int(os.environ.get("AUTOCUE_DOWNLOAD_CONCURRENCY", "1"))
            except ValueError:
                env = 1
            max_concurrency = env
        self.max_concurrency = max(1, min(4, max_concurrency))
        logger.info("DownloadQueue starting with max_concurrency=%d", self.max_concurrency)

        self._pending: queue.Queue[_Job] = queue.Queue()
        self._active: dict[str, _Job] = {}
        self._completed: dict[str, _Job] = {}
        self._cancel_pending: set[str] = set()
        self._lock = threading.Lock()
        self._shutdown = threading.Event()
        self._workers: list[threading.Thread] = []
        for i in range(self.max_concurrency):
            t = threading.Thread(target=self._worker_loop, name=f"DownloadWorker-{i}", daemon=True)
            t.start()
            self._workers.append(t)
        self._watchdog = threading.Thread(target=self._watchdog_loop, name="DownloadWatchdog", daemon=True)
        self._watchdog.start()

    # ---- public API ----

    def enqueue(self, kind: Literal["single", "album"], payload: dict) -> tuple[str, int]:
        """Enqueue a new job; return (job_id, position-from-tail)."""
        job_id = uuid.uuid4().hex[:12]
        job = _Job(id=job_id, kind=kind, payload=payload, enqueued_at=time.monotonic())
        with self._lock:
            self._pending.put(job)
            position = self._pending.qsize()
            self._completed_gc_locked()
        return job_id, position

    def cancel(self, job_id: str) -> bool:
        """Sync cancel. Returns True if the job was active or pending."""
        with self._lock:
            if job_id in self._active:
                self._active[job_id].cancel_event.set()
                return True
            if job_id in self._completed:
                return False
            self._cancel_pending.add(job_id)
        return True

    def stream(self, job_id: str) -> Iterator[dict]:
        """Yield events for a job until done. Synthetic ``done`` from cache for
        late connectors. Raises KeyError if job_id unknown AND not cached.
        Marks cache consumed on first open."""
        with self._lock:
            job = self._active.get(job_id)
            if job is None:
                cached = self._completed.get(job_id)
                if cached is None:
                    raise KeyError(job_id)
                if cached.consumed:
                    yield {
                        "type": "done", "status": "already_consumed", "job_id": job_id,
                        "path": (cached.final or {}).get("path"),
                        "cached_status": (cached.final or {}).get("status"),
                        "http_status": 410,
                    }
                    return
                cached.consumed = True
                ev = dict(cached.final or {})
                ev.setdefault("type", "done")
                ev.setdefault("job_id", job_id)
                yield ev
                return
            bus = job.event_bus
        while True:
            try:
                ev = bus.get(timeout=15.0)
            except queue.Empty:
                yield {"type": "_keepalive"}
                continue
            yield ev
            if ev.get("type") == "done":
                with self._lock:
                    if job_id in self._completed:
                        self._completed[job_id].consumed = True
                return

    def status(self) -> dict:
        """Snapshot for ``GET /api/download/queue``."""
        now = time.monotonic()
        with self._lock:
            active = []
            for j in self._active.values():
                active.append({
                    "id": j.id, "title": j.title, "percent": j.percent, "phase": j.phase,
                    "started_at": j.started_at, "last_event_at": j.last_event_at,
                })
            queued_count = self._pending.qsize()
        return {
            "active": active,
            "queued_count": queued_count,
            "max_concurrency": self.max_concurrency,
            "now": now,
        }

    # ---- worker ----

    def _worker_loop(self) -> None:
        while not self._shutdown.is_set():
            try:
                job = self._pending.get(timeout=1.0)
            except queue.Empty:
                continue
            with self._lock:
                if job.id in self._cancel_pending:
                    self._cancel_pending.discard(job.id)
                    self._emit_done(job, {"status": "cancelled", "path": None})
                    continue
                self._active[job.id] = job
                job.started_at = time.monotonic()
                job.worker_pulse_at = job.started_at
                job.last_event_at = job.started_at
                job.phase = "fetching"
            try:
                self._run_job(job)
            except Exception:
                logger.exception("Download worker crashed for job %s", job.id)
                self._emit_done(job, {
                    "status": "error",
                    "error_code": "worker_crash",
                    "error_message": "Internal worker crash.",
                    "error_hint": "Check the server log.",
                })
            finally:
                self._cleanup_artifacts(job)
                with self._lock:
                    self._active.pop(job.id, None)

    def _run_job(self, job: _Job) -> None:
        payload = job.payload
        kind = job.kind

        def pulse() -> None:
            job.worker_pulse_at = time.monotonic()

        def emit(ev: dict) -> None:
            job.last_event_at = time.monotonic()
            ev = {"job_id": job.id, **ev}
            if "type" not in ev:
                ev["type"] = "progress"
            job.event_bus.put(ev)

        if kind == "single":
            query = payload["query"]
            job.title = query
            dest_dir = payload.get("dest_dir")
            try:
                audio_format = normalize_audio_format(payload.get("audio_format"))
            except ValueError as exc:
                self._emit_done(job, {
                    "status": "error", "error_code": "unknown_format",
                    "error_message": str(exc),
                    "error_hint": "Pick one of WAV, MP3 320, or Original.",
                })
                return
            allow_playlist = bool(payload.get("allow_playlist"))
            normalize = bool(payload.get("normalize"))
            embed_meta = bool(payload.get("embed_metadata", True))

            if normalize and audio_format == "original":
                self._emit_done(job, {
                    "status": "error", "error_code": "normalize_unsupported_for_original",
                    "error_message": "Normalization isn't available for Original format. "
                                     "Pick WAV or MP3 320 to normalize.",
                    "error_hint": "auto_switch_to_mp3_320",
                })
                return

            normalize_lufs: float | None = -14.0 if normalize else None

            def yt_progress(d: dict) -> None:
                phase = "fetching"
                pct = _ytdlp_pct(d)
                if d.get("status") == "finished":
                    phase = "converting" if audio_format != "original" else "tagging"
                job.phase = phase
                if pct is not None:
                    job.percent = pct
                emit({"phase": phase, "percent": pct, "processed": 0, "total": 1,
                      "current_query": query})

            try:
                path = download_audio(
                    query, dest_dir=dest_dir, audio_format=audio_format,
                    allow_playlist=allow_playlist, embed_metadata=embed_meta,
                    normalize_lufs=normalize_lufs, cancel_event=job.cancel_event,
                    progress_cb=yt_progress, output_artifacts=job.artifacts, on_pulse=pulse,
                )
            except DownloadCancelled:
                self._emit_done(job, {"status": "cancelled", "path": None})
                return
            except Exception as exc:
                cl = classify_download_error(exc)
                self._emit_done(job, {
                    "status": "error", "error_code": cl["code"],
                    "error_message": cl["user_message"], "error_hint": cl["hint"],
                    "error_raw": cl["raw"],
                })
                return
            self._emit_done(job, {"status": "success", "path": path, "downloaded": 1})
            return

        if kind == "album":
            tracks = payload.get("tracks") or []
            total = len(tracks)
            dest_dir = payload.get("dest_dir")
            try:
                audio_format = normalize_audio_format(payload.get("audio_format"))
            except ValueError as exc:
                self._emit_done(job, {
                    "status": "error", "error_code": "unknown_format",
                    "error_message": str(exc),
                    "error_hint": "Pick one of WAV, MP3 320, or Original.",
                })
                return
            normalize = bool(payload.get("normalize"))
            embed_meta = bool(payload.get("embed_metadata", True))
            normalize_lufs = -14.0 if normalize else None
            downloaded = 0
            failed = 0

            for i, spec in enumerate(tracks):
                if job.cancel_event.is_set():
                    self._emit_done(job, {"status": "cancelled",
                                          "downloaded": downloaded, "failed": failed, "total": total})
                    return
                spec_query = spec.get("query") or ""
                label = spec.get("title") or spec_query
                job.title = label
                emit({"phase": "fetching", "percent": 0.0, "processed": i,
                      "total": total, "current_title": label, "current_query": spec_query})
                try:
                    path = download_audio(
                        spec_query, dest_dir=dest_dir, audio_format=audio_format,
                        allow_playlist=False, embed_metadata=embed_meta,
                        normalize_lufs=normalize_lufs, cancel_event=job.cancel_event,
                        progress_cb=lambda d: emit({
                            "phase": "fetching", "percent": _ytdlp_pct(d),
                            "processed": i, "total": total,
                            "current_title": label, "current_query": spec_query,
                        }),
                        output_artifacts=job.artifacts, on_pulse=pulse,
                    )
                    downloaded += 1
                    emit({"phase": "tagging", "percent": 100.0, "processed": i + 1,
                          "total": total, "current_title": label,
                          "current_query": spec_query, "path": path, "downloaded": downloaded})
                except DownloadCancelled:
                    self._emit_done(job, {"status": "cancelled",
                                          "downloaded": downloaded, "failed": failed, "total": total})
                    return
                except Exception as exc:
                    failed += 1
                    cl = classify_download_error(exc)
                    emit({"phase": "fetching", "processed": i + 1,
                          "total": total, "current_title": label,
                          "current_query": spec_query,
                          "error_code": cl["code"], "error_message": cl["user_message"],
                          "downloaded": downloaded, "failed": failed})
            self._emit_done(job, {"status": "success", "downloaded": downloaded,
                                  "failed": failed, "total": total})
            return

    def _emit_done(self, job: _Job, body: dict) -> None:
        ev = {"type": "done", "job_id": job.id, **body}
        job.event_bus.put(ev)
        job.final = body
        job.final_at = time.monotonic()
        with self._lock:
            self._completed[job.id] = job

    def _cleanup_artifacts(self, job: _Job) -> None:
        artifacts = job.artifacts
        success_path = (job.final or {}).get("path") if job.final else None
        success_resolved = None
        if success_path:
            try:
                success_resolved = Path(success_path).resolve()
            except Exception:
                success_resolved = None
        # Convert monotonic-based started_at to a wall-clock floor for mtime.
        started_at_wall: float | None = None
        if job.started_at is not None:
            started_at_wall = time.time() - (time.monotonic() - job.started_at)
        for p in artifacts:
            try:
                resolved = p.resolve()
            except Exception:
                continue
            if success_resolved and resolved == success_resolved:
                continue
            if not resolved.exists():
                continue
            try:
                mt = resolved.stat().st_mtime
            except OSError:
                continue
            if started_at_wall is not None and mt < started_at_wall - 1.0:
                continue
            try:
                resolved.unlink()
                logger.debug("cleaned partial artifact: %s", resolved)
            except OSError:
                pass

    def _watchdog_loop(self) -> None:
        while not self._shutdown.is_set():
            time.sleep(30.0)
            now = time.monotonic()
            with self._lock:
                stale_active: list[_Job] = []
                for j in list(self._active.values()):
                    pulse = j.worker_pulse_at or j.started_at or now
                    if now - pulse > 60.0:
                        stale_active.append(j)
                    elif j.phase in {"fetching", "normalizing_pass1", "normalizing_pass2"} \
                            and j.last_event_at and now - j.last_event_at > 1800.0:
                        stale_active.append(j)
                self._completed_gc_locked()
            for j in stale_active:
                logger.warning("watchdog killing job %s (stale)", j.id)
                j.cancel_event.set()
                self._emit_done(j, {
                    "status": "error", "error_code": "worker_crash",
                    "error_message": "Download stalled.",
                    "error_hint": "Try again — the worker stopped responding.",
                })

    def _completed_gc_locked(self) -> None:
        now = time.monotonic()
        expired = [jid for jid, j in self._completed.items()
                   if j.final_at and now - j.final_at > self.CACHE_TTL_S]
        for jid in expired:
            self._completed.pop(jid, None)


# --------------------------------------------------------------------------- #
# Process-singleton accessor
# --------------------------------------------------------------------------- #

_queue_singleton: DownloadQueue | None = None
_queue_singleton_lock = threading.Lock()


def get_download_queue() -> DownloadQueue:
    """Return the process-wide DownloadQueue, instantiating on first call."""
    global _queue_singleton
    if _queue_singleton is None:
        with _queue_singleton_lock:
            if _queue_singleton is None:
                _queue_singleton = DownloadQueue()
    return _queue_singleton


def reset_download_queue_for_tests() -> None:
    """Test helper: dispose the singleton so a fresh queue starts on next call."""
    global _queue_singleton
    with _queue_singleton_lock:
        if _queue_singleton is not None:
            _queue_singleton._shutdown.set()
        _queue_singleton = None


# --------------------------------------------------------------------------- #
# Platform-aware reveal probe (PRD §6.10)
# --------------------------------------------------------------------------- #

def reveal_supported() -> bool:
    """True if the host platform has a binary to reveal a file in its file manager."""
    if sys.platform == "darwin":
        return shutil.which("open") is not None
    if sys.platform == "win32":
        return shutil.which("explorer.exe") is not None
    if sys.platform.startswith("linux"):
        return shutil.which("xdg-open") is not None
    return False


def reveal_path(path: str) -> None:
    """Spawn the platform-specific reveal command. Raises RuntimeError on
    unsupported platform or non-zero exit. Caller is responsible for
    validating ``path`` against the allow-list before calling."""
    if sys.platform == "darwin":
        subprocess.run(["open", "-R", path], check=True)
        return
    if sys.platform == "win32":
        subprocess.run(["explorer.exe", "/select,", path])
        return
    if sys.platform.startswith("linux"):
        subprocess.run(["xdg-open", os.path.dirname(path) or "."], check=True)
        return
    raise RuntimeError(f"reveal_path unsupported on {sys.platform!r}")
