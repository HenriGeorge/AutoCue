"""Discogs API client for fetching track genre/style tags.

Provides `search_styles(artist, title, token)` which returns a list of
Discogs Style strings (e.g. ["House", "Deep House", "Minimal"]).

Rate-limit: 60 req/min for authenticated users. The module uses a
simple per-process token-bucket to stay under the limit.
"""
from __future__ import annotations

import logging
import time
import urllib.parse
import urllib.request
import json
from threading import Lock

_log = logging.getLogger(__name__)

_DISCOGS_API = "https://api.discogs.com/database/search"
_USER_AGENT  = "AutoCue/1.0 +https://github.com/henrigeorge/AutoCue"

# --- simple in-process cache (artist+title → styles list) ---
_cache: dict[str, list[str]] = {}

# --- token-bucket rate limiter (60 req/min authenticated) ---
_rate_lock      = Lock()
_tokens: float  = 60.0
_last_fill: float = time.monotonic()
_MAX_TOKENS     = 60.0
_FILL_RATE      = 1.0   # tokens per second


def _acquire_token() -> None:
    """Block until a request token is available (max 60 req/min)."""
    with _rate_lock:
        global _tokens, _last_fill
        now = time.monotonic()
        elapsed = now - _last_fill
        _tokens = min(_MAX_TOKENS, _tokens + elapsed * _FILL_RATE)
        _last_fill = now
        if _tokens < 1.0:
            wait = (1.0 - _tokens) / _FILL_RATE
            time.sleep(wait)
            _tokens = 0.0
        else:
            _tokens -= 1.0


def search_styles(artist: str, title: str, token: str) -> list[str]:
    """Return a deduplicated list of Discogs Styles for the given track.

    Returns [] on any network/API error — callers must handle the empty
    case gracefully (skip tagging rather than crash).
    """
    if not token:
        return []

    cache_key = f"{artist.lower().strip()}|||{title.lower().strip()}"
    if cache_key in _cache:
        return _cache[cache_key]

    query = f"{artist} {title}".strip()
    params = urllib.parse.urlencode({"q": query, "type": "release", "per_page": "5"})
    url = f"{_DISCOGS_API}?{params}"

    _acquire_token()

    try:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Discogs token={token}",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        _log.warning("Discogs API error for %r / %r: %s", artist, title, exc)
        return []

    # Collect all styles from the top results, deduplicated, order-preserving
    seen: set[str] = set()
    styles: list[str] = []
    for result in data.get("results", []):
        for s in result.get("style", []):
            if s not in seen:
                seen.add(s)
                styles.append(s)

    # Only cache non-empty responses. An empty result (no Discogs hit) is
    # often because the artist/title is misspelled or the release just
    # hasn't been catalogued yet — caching it would block retries until
    # the process restarts. Misses are cheap to re-issue against the
    # token-bucket-rate-limited API.
    if styles:
        _cache[cache_key] = styles
    return styles


# --- release discovery cache (artist + year_from → release list) ---
_releases_cache: dict[str, list[dict]] = {}


def search_artist_releases(
    artist: str,
    token: str,
    year_from: int | None = None,
    per_page: int = 25,
) -> list[dict]:
    """Return recent Discogs releases for an artist.

    Each dict: {title, artist, album, year, thumb, cover, genres, styles, url, id, formats}.
    Results are sorted newest-first and (when year_from is given) filtered to
    year >= year_from. Returns [] on any network/API error or missing token.
    """
    if not token or not artist.strip():
        return []

    cache_key = f"{artist.lower().strip()}|||{year_from or 0}"
    if cache_key in _releases_cache:
        return _releases_cache[cache_key]

    params = urllib.parse.urlencode({
        "artist": artist,
        "type": "release",
        "sort": "year",
        "sort_order": "desc",
        "per_page": str(per_page),
    })
    url = f"{_DISCOGS_API}?{params}"

    _acquire_token()

    try:
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Discogs token={token}",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        _log.warning("Discogs releases error for %r: %s", artist, exc)
        return []

    releases: list[dict] = []
    for r in data.get("results", []):
        year = _parse_year(r.get("year"))
        if year_from is not None and (year is None or year < year_from):
            continue
        raw_title = str(r.get("title") or "")
        # Discogs titles are usually "Artist - Album"; split off the album part
        album = raw_title.split(" - ", 1)[1].strip() if " - " in raw_title else raw_title.strip()
        releases.append({
            "title": raw_title,
            "artist": artist,
            "album": album,
            "year": year,
            "thumb": r.get("thumb") or "",
            "cover": r.get("cover_image") or "",
            "genres": list(r.get("genre", []) or []),
            "styles": list(r.get("style", []) or []),
            "url": "https://www.discogs.com" + str(r.get("uri", "")) if r.get("uri") else (r.get("resource_url") or ""),
            "id": r.get("id"),
            "formats": list(r.get("format", []) or []),
        })

    # Same rule as search_styles: don't cache empty responses so transient
    # "artist not found yet" results aren't sticky across the process lifetime.
    if releases:
        _releases_cache[cache_key] = releases
    return releases


def _parse_year(raw) -> int | None:
    """Coerce a Discogs year value ("2024", 2024, "2024-05") to an int, or None."""
    if raw is None:
        return None
    try:
        return int(str(raw).strip()[:4])
    except (ValueError, TypeError):
        return None
