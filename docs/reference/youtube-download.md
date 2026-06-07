# YouTube Download

Optional audio acquisition pipeline that lets the AutoCue UI fetch a track from
YouTube on behalf of the user — typically as the follow-on action to a
suggestion produced by the [Discover tab](./discogs-and-discovery.md).

The download stack is built around `yt-dlp` for fetching and `ffmpeg` for audio
extraction. Both are **optional** runtime dependencies; the core CLI and server
work fine without them, and the UI surfaces a clear "feature unavailable" path
when they are absent.

---

## 1. Overview

Three things make this feature exist:

1. The [Discover tab](./discogs-and-discovery.md) surfaces new releases by the
   library's most-played artists (via Discogs). Once a DJ sees an album they
   want, they need to actually get the audio file onto disk.
2. yt-dlp is a robust, well-maintained CLI/library for downloading audio and
   video from YouTube (and ~1000 other sites).
3. ffmpeg can re-encode the downloaded audio stream into a stable container
   (mp3, m4a, opus, …) that Rekordbox happily ingests.

A `DiscoverItem` includes a pre-baked `query` field (e.g. `"Daft Punk Discovery"`)
which the UI passes straight to `POST /api/download` as the `query`. The server
runs yt-dlp in a worker thread, streams progress events over SSE, and reports
the final file path.

Because YouTube downloads can fail (network errors, region locks, deleted
videos, copyright takedowns), every operation streams structured progress and
clear error events instead of a single blocking response.

> **This feature is optional.** A vanilla `pip install -e .` does **not** pull
> yt-dlp. Without the `[download]` extra installed (and `ffmpeg` on PATH), the
> Download buttons in the UI become disabled and the API endpoints return
> `503`. Nothing else in AutoCue is affected.

Reference files:

- `autocue/download.py` — the yt-dlp wrapper
- `autocue/serve/routes.py:1903–2042` — the `/api/download/*` endpoints
- `autocue/serve/schemas.py:509–550` — request/response models
- `tests/test_download.py` — 15 tests with yt-dlp mocked

---

## 2. Installation

### Install the optional Python extra

```bash
pip install -e ".[download]"
```

This is declared in `pyproject.toml` as an `[project.optional-dependencies]`
entry that pulls `yt-dlp`. The core install (`pip install -e .`) deliberately
omits it.

### Install ffmpeg on PATH

yt-dlp downloads the best available audio stream (often `.webm` Opus or `.m4a`
AAC). It then shells out to `ffmpeg` via the `FFmpegExtractAudio` post-processor
to produce a final `.mp3` (or other configured format). Without ffmpeg the
download will fail at the extraction stage — so the server checks for it up
front and refuses to start a download until it is present.

**macOS (Homebrew):**

```bash
brew install ffmpeg
```

**Debian / Ubuntu:**

```bash
sudo apt-get install -y ffmpeg
```

**Windows:** download a static build from <https://www.gyan.dev/ffmpeg/builds/>
and add the `bin/` directory to your `PATH`.

After installation, confirm it works:

```bash
ffmpeg -version
which ffmpeg
```

### Verify the install

Start the local server and hit `/api/download/config`:

```bash
curl http://localhost:7432/api/download/config
```

A healthy install responds with:

```json
{
  "available": true,
  "ffmpeg": true,
  "default_dir": "/Users/you/Music/AutoCue",
  "music_folder": "/Users/you/Music/Rekordbox"
}
```

If `available` is `false`, `pip install -e ".[download]"` did not run in the
same Python environment serving the API. If `ffmpeg` is `false`, ffmpeg is not
on the server process's PATH (note: shells started after a `brew install` may
need a new terminal to see the updated PATH).

---

## 3. Lazy imports

`yt-dlp` is **never** imported at module load time. Every import of `yt_dlp`
lives inside a function body, so importing `autocue.download` is safe even
when yt-dlp is not installed.

```python
# autocue/download.py:22
def ytdlp_available() -> bool:
    """True if yt-dlp can be imported in this environment."""
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False
```

The same pattern is used inside `search_youtube()` (`download.py:66`) and
`download_audio()` (`download.py:112`). Importing the module never imports
yt-dlp; the import only happens once a function that needs it is actually
called.

This matters because:

- The core CLI (`autocue --library`) and the server's analysis surface
  (energy, mixability, transitions, set builder, …) work without yt-dlp.
  Failing at module import would break unrelated workflows.
- Tests can run on CI without installing yt-dlp by mocking
  `sys.modules["yt_dlp"]` (see [§19](#19-testing)).
- The `/api/download/config` probe is fast: it does not load yt-dlp, it just
  attempts the import inside a `try`.

---

## 4. `ytdlp_available()` — runtime probe

```python
# autocue/download.py:22
def ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False
```

A simple boolean probe. Returns `True` when yt-dlp can be imported in the
current Python process. Used by the `/api/download/config` endpoint and as a
gate at the top of `search_youtube()` and `download_audio()`.

The `except Exception` is intentionally broad: aside from `ImportError`, some
yt-dlp transitively-imported modules have been known to raise other exceptions
on broken installs.

---

## 5. `ffmpeg_available()` — ffmpeg probe

```python
# autocue/download.py:31
def ffmpeg_available() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None
```

Checks whether `ffmpeg` is discoverable on the current process's `PATH`. Used
both by `/api/download/config` (to populate `ffmpeg: bool`) and by
`download_audio()` (to refuse to start a download that will inevitably fail
during audio extraction).

`shutil.which` resolves PATH the same way the shell does, so the check matches
what yt-dlp itself will see when it spawns `ffmpeg` as a subprocess.

---

## 6. `default_download_dir()`

```python
# autocue/download.py:37
def default_download_dir() -> str:
    env = os.environ.get("AUTOCUE_DOWNLOAD_DIR")
    if env:
        return env
    return str(Path.home() / "Music" / "AutoCue")
```

Resolution order:

1. `AUTOCUE_DOWNLOAD_DIR` environment variable (if set, used verbatim).
2. Fallback: `~/Music/AutoCue`.

The directory is created lazily inside `download_audio()`
(`download.py:115`: `dest.mkdir(parents=True, exist_ok=True)`) — not on probe.
This keeps `default_download_dir()` a pure resolver with no side effects, so
the `/api/download/config` endpoint never accidentally creates a folder just by
being polled.

The UI uses this value to pre-fill the destination folder input on the
download dialog, but the user may override it on a per-download basis via the
`dest_dir` request field.

---

## 7. Music folder detection

`_detect_music_folder()` is a small heuristic in `serve/routes.py` that tries
to find the directory the DJ already uses for their tracked audio files. The
UI uses this as a smarter default than `~/Music/AutoCue`: if the DJ keeps
everything under `~/Music/Rekordbox`, AutoCue should suggest downloading new
tracks into the same root.

```python
# autocue/serve/routes.py:1907
def _detect_music_folder(db) -> str | None:
    """Find the common ancestor directory of tracked audio files."""
    import os
    from pathlib import Path
    paths: list[str] = []
    try:
        from pyrekordbox.db6 import DjmdContent
        for row in db.query(DjmdContent).limit(30).all():
            raw = getattr(row, "FolderPath", None) or ""
            if raw and os.path.isabs(raw):
                paths.append(str(Path(raw).parent))
    except Exception:
        return None
    if not paths:
        return None
    try:
        return os.path.commonpath(paths)
    except (ValueError, TypeError):
        return None
```

Algorithm:

1. Sample up to 30 `DjmdContent` rows (the first 30 — order is undefined but
   consistent across calls within one DB).
2. Collect `FolderPath` values that are absolute paths.
3. Reduce each to its parent directory (we want a folder, not a file).
4. Compute `os.path.commonpath()` across all of them — the deepest directory
   that contains every sampled track.

The 30-row cap exists so a 50k-track library does not pay a full scan on every
`/api/download/config` poll. A 30-track sample is plenty: as long as the DJ
keeps tracks in a single rooted tree (the common case), even one sample would
work; 30 makes the sampler robust to the occasional one-off path elsewhere on
disk.

Failure modes that all silently return `None`:

- No tracks in the DB (`paths` empty).
- All `FolderPath` values are relative or missing.
- `os.path.commonpath()` raises (`ValueError` on cross-drive paths on Windows;
  `TypeError` on degenerate inputs).
- Any unexpected exception from pyrekordbox.

The UI gracefully falls back to `default_dir` when `music_folder` is `null`.

---

## 8. `GET /api/download/config`

```http
GET /api/download/config
```

Reports the runtime state of the download feature without touching the
network. Returned as `DownloadConfigResponse`:

```python
# autocue/serve/schemas.py:513
class DownloadConfigResponse(BaseModel):
    available: bool          # yt-dlp importable
    ffmpeg: bool             # ffmpeg on PATH
    default_dir: str
    music_folder: str | None = None  # detected Rekordbox music root
```

| Field | Meaning |
| --- | --- |
| `available` | `ytdlp_available()` — yt-dlp can be imported. |
| `ffmpeg` | `ffmpeg_available()` — ffmpeg binary on PATH. |
| `default_dir` | `default_download_dir()` — env override or `~/Music/AutoCue`. |
| `music_folder` | `_detect_music_folder(db)` — common ancestor of `DjmdContent.FolderPath`, or `null`. |

Implementation (`routes.py:1933`):

```python
@router.get("/download/config", response_model=DownloadConfigResponse)
def download_config(db=Depends(get_ro_db)):
    from .. import download as dl
    return DownloadConfigResponse(
        available=dl.ytdlp_available(),
        ffmpeg=dl.ffmpeg_available(),
        default_dir=dl.default_download_dir(),
        music_folder=_detect_music_folder(db),
    )
```

The UI polls this on Discover-tab activation. If `available` or `ffmpeg` is
`false`, Download buttons are disabled with a tooltip explaining what to
install.

---

## 9. `download_audio(url_or_query, dest_dir, audio_format, progress_cb)`

The core download primitive. Both `/api/download` and `/api/download/album`
ultimately call this.

Signature:

```python
# autocue/download.py:91
def download_audio(
    url_or_query: str,
    dest_dir: str | None = None,
    audio_format: str = "mp3",
    audio_quality: str = "192",
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
) -> str:
```

### URL detection vs. search-term wrap

```python
# autocue/download.py:48
def _build_query(url_or_query: str) -> str:
    s = (url_or_query or "").strip()
    if not s:
        raise ValueError("empty download query")
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return f"ytsearch1:{s}"
```

- A real `http://` or `https://` URL is **passed through** to yt-dlp as-is.
  This means a Discogs/YouTube/SoundCloud/Bandcamp/etc. link will all be
  resolved by yt-dlp's site-specific extractors.
- Anything else is wrapped as `ytsearch1:<terms>` — a yt-dlp shorthand for
  "do a YouTube search and return the single best match". This is what makes
  bare strings like `"Daft Punk Discovery"` Just Work.
- An empty / whitespace-only input raises `ValueError`.

### Behaviour

1. Validate `yt-dlp` and `ffmpeg` are present (raises `RuntimeError` otherwise).
2. Ensure `dest_dir` exists (`mkdir(parents=True, exist_ok=True)`).
3. Build yt-dlp options:
   - `format: "bestaudio/best"` — request best audio-only stream, falling
     back to the best combined stream.
   - `outtmpl: <dest>/%(title)s.%(ext)s` — write to `<dest>/<video title>.<ext>`.
   - `noplaylist: True` — if a URL points at a video that is also part of a
     playlist, only the single video is downloaded.
   - `quiet: True, no_warnings: True` — yt-dlp's stdout is silenced; progress
     is reported via the hook instead.
   - `progress_hooks: [_hook]` — internal wrapper around `progress_cb`.
   - `postprocessors: [{key: "FFmpegExtractAudio", preferredcodec: <fmt>,
     preferredquality: <q>}]` — re-encode to the target format/quality.
4. Run the synchronous `yt_dlp.YoutubeDL(opts).extract_info(target, download=True)`.
5. Unwrap search results: `ytsearch1:` returns `{"entries": [...]}`. The first
   non-null entry is taken.
6. Resolve the final file path:
   - First choice: `info["requested_downloads"][0]["filepath"]` — yt-dlp's
     authoritative post-extraction filepath.
   - Fallback: swap the in-progress filename's extension to `audio_format`
     (`raw.with_suffix(...)`).
   - Last resort: `<dest>/<title>.<audio_format>`.
7. Return `str(Path(final).resolve())`.

### `progress_cb`

The optional `progress_cb` is invoked with every yt-dlp progress dict, which
looks roughly like:

```python
{"status": "downloading", "downloaded_bytes": 1024000, "total_bytes": 4096000, "filename": "..."}
{"status": "finished",    "filename": "/.../My Song.webm"}
```

`status` cycles through `"downloading"` → `"finished"` (post-extraction is
typically reported via separate hook callbacks; the server only watches the
download stage for progress). Exceptions raised inside `progress_cb` are
caught and ignored — a buggy callback never aborts a download.

### Audio format / quality

- `audio_format` defaults to `"mp3"`. Any format ffmpeg supports works
  (e.g. `"m4a"`, `"opus"`, `"flac"`).
- `audio_quality` defaults to `"192"` (kbps for lossy, ignored for lossless).

Both are routed straight into the `FFmpegExtractAudio` postprocessor's
`preferredcodec` / `preferredquality`.

---

## 10. `search_youtube(query, max_results=5)`

A read-only search that returns lightweight metadata without downloading
anything.

```python
# autocue/download.py:58
def search_youtube(query: str, max_results: int = 5) -> list[dict]:
    if not ytdlp_available():
        return []
    import yt_dlp
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True}
    ...
```

Each result dict:

```python
{
  "url": "https://www.youtube.com/watch?v=<id>",
  "title": "Song A",
  "duration": 200,                 # seconds, or None
  "uploader": "Channel Name",      # falls back to .channel
  "id": "vid1",
}
```

Behaviour:

- Returns `[]` if yt-dlp is unavailable.
- Returns `[]` if the underlying `extract_info` raises (network error, blocked
  query, etc.) — logged at `WARNING` level.
- `extract_flat: True` skips per-video metadata fetches, making the search
  faster (no per-result HTTP roundtrip).
- Null entries in `info["entries"]` (which yt-dlp may include for unavailable
  results) are filtered out.

The server does not currently expose `search_youtube` as an HTTP endpoint —
it is a Python-level utility intended for richer "let me pick a candidate"
UIs that have not yet been built. The current Discover flow goes straight
from `DiscoverItem.query` → `ytsearch1:` inside `download_audio`.

---

## 11. `POST /api/download` (SSE)

Single-track download with streaming progress.

### Request

```python
# autocue/serve/schemas.py:520
class DownloadRequest(BaseModel):
    query: str               # a YouTube URL or a search term ("artist - title")
    dest_dir: str | None = None
    audio_format: str = "mp3"
```

### Pre-flight 503s

```python
# autocue/serve/routes.py:1966
if not dl.ytdlp_available():
    raise HTTPException(503, "yt-dlp is not installed. Install with: pip install -e \".[download]\"")
if not dl.ffmpeg_available():
    raise HTTPException(503, "ffmpeg not found on PATH — required to extract audio.")
```

Both probes run before the response starts streaming. If either fails the
client gets a clean `503 Service Unavailable` with an actionable detail
message instead of a partial SSE stream that eventually 500s.

### Streaming model

The route does **not** call yt-dlp on the request thread. yt-dlp's
`extract_info(..., download=True)` is fully synchronous and blocking; running
it on the event loop would block FastAPI/uvicorn from serving any other
request for the entire download.

Instead, the route spawns a daemon worker thread, hands yt-dlp a progress
callback that pushes events to a `queue.Queue`, and the SSE generator drains
the queue. This is the pattern documented in [§14](#14-threading-model).

### Wire format

Each SSE line is a JSON object:

```json
data: {"processed": 0, "total": 1, "query": "Daft Punk Discovery", "status": "downloading", "percent": 42.0}
```

Final event:

```json
data: {"done": true, "status": "finished", "path": "/Users/.../Discovery.mp3", "downloaded": 1}
```

Or on failure:

```json
data: {"done": true, "status": "error", "error": "HTTP Error 410: Gone", "failed": 1}
```

---

## 12. `POST /api/download/album` (SSE)

Sequential download of multiple tracks (e.g. an entire album).

### Request

```python
# autocue/serve/schemas.py:526
class DownloadTrackSpec(BaseModel):
    query: str
    title: str | None = None

class DownloadAlbumRequest(BaseModel):
    tracks: list[DownloadTrackSpec]
    dest_dir: str | None = None
    audio_format: str = "mp3"
```

### Pre-flight

Same 503 pattern as `/api/download` — yt-dlp and ffmpeg are both required.

### Per-track event

```json
data: {"processed": 3, "total": 12, "title": "One More Time", "query": "Daft Punk One More Time",
       "status": "finished", "path": "/.../One More Time.mp3", "downloaded": 3}
```

On per-track failure (`processed` still advances; `failed` increments):

```json
data: {"processed": 4, "total": 12, "title": "X", "query": "X", "status": "error",
       "error": "Video unavailable", "failed": 1}
```

Final event:

```json
data: {"done": true, "downloaded": 11, "failed": 1, "total": 12}
```

### Differences from `/api/download`

- **Sequential, not parallel.** Tracks are downloaded one after another.
  Network bandwidth is rarely the limit; YouTube rate-limits aggressive
  parallel grabs. Serial downloads are also easier to reason about and to
  abort mid-way (the user simply closes the SSE stream).
- **No per-track progress %.** Per-track progress callbacks would require
  one worker thread per track in flight; the album endpoint trades that
  complexity for clarity, surfacing only the per-track finished/error event.
- One track failing does **not** abort the album. Failures are counted and
  reported alongside the final summary.

---

## 13. `DownloadEvent` schema

The Pydantic shape every SSE event conforms to (informationally — the
endpoints emit raw `data: <json>` lines so the structure is documented here
rather than enforced at serialisation time).

```python
# autocue/serve/schemas.py:537
class DownloadEvent(BaseModel):
    """SSE event for a download in progress."""
    processed: int = 0
    total: int = 1
    query: str | None = None
    title: str | None = None
    percent: float | None = None
    status: str | None = None     # "downloading" | "extracting" | "finished" | "error"
    path: str | None = None
    error: str | None = None
    done: bool = False
    downloaded: int = 0
    failed: int = 0
```

| Field | When set |
| --- | --- |
| `processed`, `total` | Always (`/api/download/album` increments per track; `/api/download` stays at `0..1`). |
| `query` | The original search term or URL. |
| `title` | Album endpoint only — the human-readable track title. |
| `percent` | `/api/download` only, when yt-dlp reports `total_bytes`. |
| `status` | `"downloading"` (mid-stream), `"finished"` (terminal success), `"error"` (terminal failure). |
| `path` | Terminal success — the absolute path of the written audio file. |
| `error` | Terminal failure — the exception message. |
| `done` | The final event of any stream is `done: true`. |
| `downloaded`, `failed` | Running counters in album mode. |

The `percent` derivation lives in `_percent_from_hook()`
(`routes.py:1945`): `min(100, downloaded_bytes / (total_bytes or total_bytes_estimate) * 100)`.

---

## 14. Threading model

yt-dlp is synchronous, blocks for the entire duration of a download, and
expects to receive a progress callback that it calls inline as bytes arrive.
FastAPI's response generators run on the event loop. Running yt-dlp directly
inside the SSE generator would freeze the event loop until the download
finishes — every other request to the server would stall.

The solution is the classic worker-thread-plus-queue pattern:

```python
# autocue/serve/routes.py:1971
def event_stream():
    events: "queue.Queue[dict]" = queue.Queue()

    def progress(d: dict) -> None:
        events.put({
            "status": d.get("status"),
            "percent": _percent_from_hook(d),
        })

    result: dict = {}

    def worker() -> None:
        try:
            path = dl.download_audio(
                req.query, dest_dir=req.dest_dir,
                audio_format=req.audio_format, progress_cb=progress,
            )
            result["path"] = path
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            events.put({"_end": True})

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    while True:
        ev = events.get()
        if ev.get("_end"):
            break
        yield f"data: {_json.dumps({'processed': 0, 'total': 1, 'query': req.query, **ev})}\n\n"

    if "error" in result:
        yield f"data: {_json.dumps({'done': True, 'status': 'error', 'error': result['error'], 'failed': 1})}\n\n"
    else:
        yield f"data: {_json.dumps({'done': True, 'status': 'finished', 'path': result.get('path'), 'downloaded': 1})}\n\n"
```

Key invariants:

- The worker thread is a `daemon` thread. If the server shuts down mid-download,
  the worker is killed with it — no clean shutdown logic required.
- The worker pushes a sentinel `{"_end": True}` event in a `finally`, so the
  generator always terminates even when yt-dlp raises.
- The result dict is captured by closure; the generator reads it after the
  worker is done, so there is no read-while-write race.
- `events.get()` blocks the generator (in async terms, it parks the coroutine)
  until the worker has an event to publish. There is no busy-wait.

The album endpoint **does not** use the worker-thread pattern because it does
not stream per-byte progress. Each iteration of the album loop calls
`dl.download_audio()` synchronously inside the generator — which is acceptable
in practice because Starlette runs the generator on its `iterate_in_threadpool`
machinery (the SSE generator is itself thread-pooled), so the event loop is
not blocked even though one album request occupies one thread-pool worker for
the duration.

---

## 15. Audio format and ffmpeg postprocessor

Default container: **mp3** (`audio_format="mp3"`, `audio_quality="192"`).

The relevant yt-dlp option block:

```python
# autocue/download.py:130
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
```

Why mp3 by default:

- Universal Rekordbox compatibility. CDJs (and all Rekordbox versions) play
  mp3 natively without any sidecar setup.
- 192 kbps is a reasonable compromise between size and quality for DJ use.
  It also matches the YouTube source quality — most YouTube audio is around
  128–256 kbps lossy already, so re-encoding higher buys nothing.

The DJ can override via the request body's `audio_format` field
(`/api/download` and `/api/download/album`). Common alternatives:

- `"m4a"` — same codec YouTube usually serves (AAC), so the postprocessor can
  remux without re-encoding when the stream is already AAC.
- `"opus"` — what YouTube's modern audio streams are. Smallest files, best
  quality-per-byte, but not all DJ hardware plays it.
- `"flac"` — lossless container, but transcoding a lossy YouTube source to
  FLAC just bloats the file; only useful for lawful FLAC sources fetched via
  yt-dlp from non-YouTube sites.

`outtmpl` puts the file at `<dest>/<video title>.<ext>`. yt-dlp sanitises the
title for filesystem use, so `Daft Punk - One More Time / 12" Mix` becomes
something like `Daft Punk - One More Time ⧸ 12'' Mix.mp3`.

---

## 16. UI surface

The download flow lives in `docs/index.html` on the **Discover** tab.

1. The user opens the Discover tab. The page polls `/api/discover` (SSE) which
   streams `DiscoverItem` cards rendered by `_renderSuggestion()`. Each card
   has a **Download** button.
2. Clicking Download opens a small dialog with:
   - **Destination folder** input, pre-filled with `music_folder` if present,
     else `default_dir` (both from `/api/download/config`).
   - **Audio format** selector (mp3 default).
   - A disclaimer about copyright — see [§17](#17-legal-note).
3. On confirm, the UI `POST`s `{query, dest_dir, audio_format}` to
   `/api/download` and reads the SSE stream via the shared `_consumeSSE()`
   helper. Progress events update a progress bar; the final `done: true`
   event flips the card to a "Downloaded" state with the file path.
4. Album downloads use the same dialog from the Discover album cards but hit
   `/api/download/album` instead, which streams one event per track and
   updates a multi-bar progress UI.

`_consumeSSE()` is the same helper used by the Discover SSE stream — see
[discogs-and-discovery.md](./discogs-and-discovery.md) — so adding a new
SSE-driven endpoint should reuse it rather than re-inlining a
`fetch` + `ReadableStream` reader loop.

All user-supplied strings rendered into Discover/Download cards pass through
`_esc()` first to prevent HTML injection from Discogs/YouTube responses.

---

## 17. Legal note

> **Downloading copyrighted audio from YouTube may violate YouTube's Terms of
> Service and copyright law in your jurisdiction.** AutoCue ships this
> wrapper as a convenience for downloading content the user is *authorised*
> to download — their own uploads, Creative-Commons material, public-domain
> works, content the user has acquired a download license for, and other
> lawful uses.
>
> **Lawful use is entirely the user's responsibility.** The AutoCue project
> does not condone, endorse, or assist with copyright infringement.

The UI surfaces this disclaimer in the download dialog. The same notice is
duplicated at the top of `autocue/download.py` so a developer auditing the
wrapper module sees it immediately. The optional-extra installation
discipline (`pip install -e ".[download]"`) is also a deliberate friction
point — users who do not want the feature never have to install yt-dlp.

---

## 18. Edge cases

| Case | Behaviour |
| --- | --- |
| yt-dlp not installed | `/api/download/config` returns `available: false`. Buttons disabled in UI. Direct API call returns `503` with install hint. |
| ffmpeg not on PATH | `/api/download/config` returns `ffmpeg: false`. `503` from download endpoints. `download_audio()` raises `RuntimeError`. |
| Network failure mid-download | yt-dlp raises; worker thread catches; event stream yields `{"done": true, "status": "error", "error": "<message>", "failed": 1}`. |
| Video not found / region locked | Same as network failure — yt-dlp raises `DownloadError`; surfaced via `error` field. |
| `ytsearch1:` returns no results | yt-dlp raises; same error path. |
| `query` is empty / whitespace | `_build_query` raises `ValueError("empty download query")` before any network call. Worker thread surfaces it as `error`. |
| `dest_dir` doesn't exist | `download_audio()` creates it (`mkdir(parents=True, exist_ok=True)`). |
| `dest_dir` not writable | OS-level exception bubbles up through yt-dlp; surfaced via `error`. |
| Same title downloaded twice | yt-dlp's default `outtmpl` will overwrite; no dedupe logic here. Up to the DJ. |
| Server restart mid-download | The daemon worker thread dies with the server. Partial file may remain on disk. |
| Browser closes mid-stream | SSE connection drops; the worker thread continues to completion (file is still written). |
| Album mode, one track fails | Per-track event yields `status: "error"`; `failed` counter increments; the loop continues to the next track. Final event has `downloaded` + `failed` totals. |
| `_detect_music_folder` finds nothing useful | Returns `None`; UI falls back to `default_dir`. |
| Cross-drive `FolderPath` on Windows | `os.path.commonpath` raises `ValueError`; `_detect_music_folder` returns `None`. |

---

## 19. Testing

`tests/test_download.py` (15 tests) exercises every code path in
`autocue/download.py` **without requiring yt-dlp to be installed.** The trick
is `sys.modules` patching combined with `MagicMock`:

```python
# tests/test_download.py:18
def test_ytdlp_available_true_when_importable(self):
    with patch.dict(sys.modules, {"yt_dlp": MagicMock()}):
        assert dl.ytdlp_available() is True

def test_ytdlp_available_false_when_missing(self):
    with patch("builtins.__import__", side_effect=ImportError):
        assert dl.ytdlp_available() is False
```

For end-to-end download tests, the test injects a fake `yt_dlp` module that
exposes a `YoutubeDL` mock whose context manager returns an object with a
controllable `extract_info`:

```python
# tests/test_download.py:113
info = {"title": "My Song", "requested_downloads": [{"filepath": str(tmp_path / "My Song.mp3")}]}
ydl = MagicMock()
ydl.__enter__.return_value.extract_info.return_value = info
fake_module = SimpleNamespace(YoutubeDL=MagicMock(return_value=ydl))
with patch.object(dl, "ytdlp_available", return_value=True):
    with patch.object(dl, "ffmpeg_available", return_value=True):
        with patch.dict(sys.modules, {"yt_dlp": fake_module}):
            path = dl.download_audio("My Song", dest_dir=str(tmp_path))
assert path.endswith("My Song.mp3")
```

`ffmpeg` is mocked via `shutil.which`:

```python
# tests/test_download.py:26
with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
    assert dl.ffmpeg_available() is True
with patch("shutil.which", return_value=None):
    assert dl.ffmpeg_available() is False
```

The progress-hook contract is verified by capturing the hook yt-dlp would be
called with, invoking it ourselves with synthetic progress dicts, and
asserting the user-supplied `progress_cb` saw them
(`test_progress_callback_invoked`, `test_download.py:134`).

Because of this approach:

- `pyproject.toml` does **not** list `yt-dlp` in `[dev]` — CI installs only
  the test deps and the download tests still pass on every machine.
- A developer can run `pytest tests/test_download.py` without `pip install
  -e ".[download]"`.
- The integration boundary (yt-dlp → ffmpeg) is genuinely untested by these
  unit tests. End-to-end testing is left to manual verification through the
  UI; CI verifies only that AutoCue's wrapper logic is correct.

The `/api/download` and `/api/download/album` endpoints are exercised by
`tests/test_serve_routes.py` (search for `test_download_`) using the same
`yt_dlp` mocking strategy.

---

## 20. Related

- [Discogs & Discovery](./discogs-and-discovery.md) — produces the
  `DiscoverItem.query` strings that feed `/api/download`. The Discover tab is
  the primary consumer of this feature.
- [REST API reference](./rest-api.md) — full request/response schemas for
  `/api/download/config`, `/api/download`, `/api/download/album`.
- [CLI usage](./cli-usage.md) — the download feature is **server-only**. There
  is currently no `autocue download <query>` CLI command; it would be a small
  wrapper around `download_audio()` if needed.
