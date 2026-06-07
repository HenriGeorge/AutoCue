"""Discogs API client.

Two layers of functionality live here:

1. **Legacy auto-tag client** (``search_styles`` / ``search_artist_releases``)
   used by the existing genre-tagging and Discover v1 paths. These swallow all
   network/API errors into empty results so callers don't have to handle them.

2. **Discover v2 client** (``search_label_releases``, ``search_seller_inventory``,
   ``get_release_details``, ``search_labels``, ``get_artist_relations``,
   ``validate_token``) used by the new feeders and orchestrator. These raise
   on rate-limit conditions so the orchestrator can back off / abort cleanly:

   - :class:`Discogs429` on HTTP 429 (carries ``retry_after`` seconds)
   - :class:`RateLimitNearExhausted` when ``x-discogs-ratelimit-remaining`` < 5
     (carries the response ``data`` so the caller can still use the result while
     also choosing to sleep before the next call)

Both layers share the per-process token-bucket (60 req/min ceiling).
"""
from __future__ import annotations

import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import json
from dataclasses import dataclass
from threading import Lock
from typing import Any

_log = logging.getLogger(__name__)

_DISCOGS_API = "https://api.discogs.com/database/search"
_DISCOGS_BASE = "https://api.discogs.com"
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


# =============================================================================
# Discover v2 client (T-005)
# =============================================================================
#
# This block extends the existing client with the endpoints the new feeders +
# orchestrator need. It REUSES the legacy token-bucket (_acquire_token) and
# adds explicit signaling for rate-limit conditions so the orchestrator can
# back off mid-scan or abort cleanly.
#
# Endpoints called:
#   GET /labels/{id}/releases          → label-watch feeder
#   GET /users/{seller}/inventory      → shop-watch (Discogs-seller subtype)
#   GET /releases/{id}                 → tracklist + master_id for detail panel
#   GET /database/search?type=label    → "Add label by name" autocomplete
#   GET /artists/{id}                  → members / groups for novelty
#   GET /oauth/identity                → token validator (UI banner)

NEAR_EXHAUSTED_THRESHOLD = 5  # x-discogs-ratelimit-remaining < this → raise


# --- Exceptions ---------------------------------------------------------------

class Discogs429(Exception):
    """Raised when Discogs returns HTTP 429. Carries ``retry_after`` seconds.

    The orchestrator catches this to mark the scan ``status='rate_limited'``
    and surface a clear UI message. The legacy auto-tag callers don't see this
    — they catch their own exceptions inline.
    """

    def __init__(self, retry_after: int, message: str | None = None) -> None:
        super().__init__(message or f"Discogs rate-limited; retry after {retry_after}s")
        self.retry_after = retry_after


class RateLimitNearExhausted(Exception):
    """Raised AFTER a successful response when the remaining bucket count
    dropped below :data:`NEAR_EXHAUSTED_THRESHOLD`.

    The response :attr:`data` is still attached — callers can use it normally
    and then choose to sleep before issuing the next request. The orchestrator
    typically does ``time.sleep(5)`` before the next call. Less-strict callers
    (e.g. the detail-panel endpoint) can ignore the exception and use ``data``.

    Pattern::

        try:
            data = search_label_releases(label_id, token)
        except RateLimitNearExhausted as exc:
            data = exc.data  # still good
            time.sleep(5)
    """

    def __init__(self, remaining: int, data: Any) -> None:
        super().__init__(f"Discogs ratelimit nearly exhausted ({remaining} remaining)")
        self.remaining = remaining
        self.data = data


# --- Rate-limit observability ------------------------------------------------

# Last seen ``x-discogs-ratelimit-remaining`` value. ``None`` means we haven't
# made a request yet OR the last response didn't include the header. The
# orchestrator can poll this to decide whether to continue dispatching feeder
# requests without burning a request just to check.
_last_remaining: int | None = None
_last_remaining_lock = Lock()


def get_last_remaining() -> int | None:
    """Return the most recently observed ``x-discogs-ratelimit-remaining``.

    Cheap to call — does not issue any HTTP. Returns ``None`` if no request
    has been made yet (or the last response was missing the header).
    """
    with _last_remaining_lock:
        return _last_remaining


def _set_last_remaining(value: int | None) -> None:
    with _last_remaining_lock:
        global _last_remaining
        _last_remaining = value


def reset_rate_limit_state() -> None:
    """Forget the last-seen remaining count. Used by tests + by the orchestrator
    when starting a fresh scan after a known sleep window."""
    _set_last_remaining(None)


# --- HTTP wrapper ------------------------------------------------------------

@dataclass(frozen=True)
class _Response:
    data: Any
    remaining: int | None


def _request_json(
    path_or_url: str,
    token: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> _Response:
    """Issue an authenticated Discogs request and parse the JSON response.

    - Acquires a token-bucket slot before issuing (60 req/min ceiling).
    - On HTTP 429: raises :class:`Discogs429` with retry-after.
    - Records ``x-discogs-ratelimit-remaining`` in module state via
      :func:`_set_last_remaining` so callers can poll without an extra request.

    Returns a :class:`_Response` with parsed JSON ``data`` and the ``remaining``
    count. The caller (one of the public functions below) decides whether to
    raise :class:`RateLimitNearExhausted` based on the response — the wrapper
    itself doesn't, so callers can opt out (e.g. ``validate_token`` doesn't
    care about near-exhaustion).
    """
    if path_or_url.startswith("http"):
        url = path_or_url
    else:
        url = f"{_DISCOGS_BASE}{path_or_url}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    _acquire_token()
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Discogs token={token}",
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            headers = resp.headers
            data = json.loads(raw.decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            retry = _coerce_int(exc.headers.get("Retry-After")) or 60
            raise Discogs429(retry_after=retry) from exc
        raise

    remaining = _coerce_int(headers.get("x-discogs-ratelimit-remaining"))
    _set_last_remaining(remaining)
    return _Response(data=data, remaining=remaining)


def _coerce_int(raw: Any) -> int | None:
    """Best-effort integer coercion for header values."""
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return None


def _check_near_exhausted(response: _Response) -> None:
    """If the response left the bucket under the threshold, raise so the caller
    is forced to acknowledge it (and probably back off). The response data is
    attached to the exception so callers can still use it."""
    if response.remaining is not None and response.remaining < NEAR_EXHAUSTED_THRESHOLD:
        raise RateLimitNearExhausted(
            remaining=response.remaining,
            data=response.data,
        )


# --- Public surface ----------------------------------------------------------

def search_label_releases(
    label_id: int,
    token: str,
    year_from: int | None = None,
    per_page: int = 50,
    page: int = 1,
) -> list[dict]:
    """Return recent releases on a label.

    Used by the label-watch feeder (T-007). Page-1-only at scan time per the
    PRD-locked depth tradeoff; the optional ``page`` argument is for the Tier 2
    background paginator.

    Args:
        label_id: numeric Discogs label ID (resolved via :func:`search_labels`).
        token: Discogs personal access token.
        year_from: drop releases older than this year. ``None`` keeps all.
        per_page / page: standard Discogs pagination.

    Returns:
        list of release dicts with keys: ``id``, ``title``, ``artist``, ``year``,
        ``thumb``, ``format``, ``resource_url``.

    Raises:
        :class:`Discogs429`: on HTTP 429.
        :class:`RateLimitNearExhausted`: when the response left ``remaining < 5``.
            The ``.data`` attribute on the exception still holds the parsed
            release list, so the caller can use it and then back off.
    """
    if not token or not label_id:
        return []
    resp = _request_json(
        f"/labels/{int(label_id)}/releases",
        token=token,
        params={
            "page": int(page),
            "per_page": int(per_page),
            "sort": "year",
            "sort_order": "desc",
        },
    )
    releases = _extract_label_releases(resp.data, year_from=year_from)
    # Attach the cleaned list onto the response data for the near-exhausted path
    # so the caller can pull from exc.data without re-extracting.
    _check_near_exhausted(_Response(data=releases, remaining=resp.remaining))
    return releases


def _extract_label_releases(payload: dict, year_from: int | None) -> list[dict]:
    out: list[dict] = []
    for r in payload.get("releases", []) or []:
        year = _parse_year(r.get("year"))
        if year_from is not None and (year is None or year < year_from):
            continue
        out.append({
            "id": r.get("id"),
            "title": str(r.get("title") or ""),
            "artist": str(r.get("artist") or ""),
            "year": year,
            "thumb": r.get("thumb") or "",
            "format": str(r.get("format") or ""),
            "resource_url": r.get("resource_url") or "",
        })
    return out


def search_seller_inventory(
    seller: str,
    token: str,
    since_date: str | None = None,
    per_page: int = 50,
    page: int = 1,
) -> list[dict]:
    """Return a Discogs seller's marketplace inventory (newest-listed first).

    Used by the shop-watch feeder's ``DiscogsSeller`` source-type (Tier 1.5).
    The page-1-only policy applies during scans; deeper pagination is the Tier 2
    background path.

    Args:
        seller: Discogs seller username (e.g. ``"hardwax"``).
        token: Discogs personal access token.
        since_date: ISO-8601 date (e.g. ``"2026-05-01"``) — discard listings
            older than this. Server-side filtering isn't supported, so we
            walk the page and drop client-side. ``None`` keeps all.

    Raises: same as :func:`search_label_releases`.
    """
    if not token or not seller:
        return []
    resp = _request_json(
        f"/users/{urllib.parse.quote(seller, safe='')}/inventory",
        token=token,
        params={
            "page": int(page),
            "per_page": int(per_page),
            "sort": "listed",
            "sort_order": "desc",
        },
    )
    listings = _extract_seller_listings(resp.data, since_date=since_date)
    _check_near_exhausted(_Response(data=listings, remaining=resp.remaining))
    return listings


def _extract_seller_listings(payload: dict, since_date: str | None) -> list[dict]:
    out: list[dict] = []
    cutoff = since_date.strip() if since_date else None
    for item in payload.get("listings", []) or []:
        posted = (item.get("posted") or "").strip()
        if cutoff and posted and posted[:10] < cutoff:
            continue
        release = item.get("release") or {}
        out.append({
            "listing_id": item.get("id"),
            "release_id": release.get("id"),
            "title": str(release.get("title") or release.get("description") or ""),
            "artist": str(release.get("artist") or ""),
            "format": str(release.get("format") or ""),
            "thumb": release.get("thumbnail") or "",
            "posted": posted,
            "price": (item.get("price") or {}).get("value"),
            "currency": (item.get("price") or {}).get("currency"),
            "uri": item.get("uri") or "",
        })
    return out


def get_release_details(release_id: int, token: str) -> dict:
    """Fetch full release details including tracklist + ``master_id``.

    Used by the detail panel (lazy fetch on card open) and by the Tier 2
    background paginator that enriches empty-artist releases with ``master_id``
    for compilation-reissue dedup.

    Returns a normalized dict with keys: ``id``, ``master_id``, ``title``,
    ``artist``, ``label``, ``year``, ``country``, ``formats``, ``genres``,
    ``styles``, ``tracklist`` (list of ``{position, title, duration}``),
    ``videos`` (list of YouTube URLs from ``videos[*].uri``), ``notes``,
    ``thumb``, ``cover``.

    Raises: same as :func:`search_label_releases`.
    """
    if not token or not release_id:
        return {}
    resp = _request_json(f"/releases/{int(release_id)}", token=token)
    details = _extract_release_details(resp.data)
    _check_near_exhausted(_Response(data=details, remaining=resp.remaining))
    return details


def _extract_release_details(payload: dict) -> dict:
    artists = payload.get("artists") or []
    artist_name = " & ".join(a.get("name", "").strip() for a in artists if a.get("name"))
    labels = payload.get("labels") or []
    label_name = labels[0].get("name", "").strip() if labels else ""
    return {
        "id": payload.get("id"),
        "master_id": payload.get("master_id"),
        "title": str(payload.get("title") or ""),
        "artist": artist_name,
        "label": label_name,
        "year": _parse_year(payload.get("year")),
        "country": str(payload.get("country") or ""),
        "formats": [str(f.get("name") or "") for f in (payload.get("formats") or [])],
        "genres": list(payload.get("genres", []) or []),
        "styles": list(payload.get("styles", []) or []),
        "tracklist": [
            {
                "position": str(t.get("position") or ""),
                "title": str(t.get("title") or ""),
                "duration": str(t.get("duration") or ""),
            }
            for t in (payload.get("tracklist", []) or [])
            if (t.get("type_") or "track") == "track"
        ],
        "videos": [v.get("uri") for v in (payload.get("videos", []) or []) if v.get("uri")],
        "notes": str(payload.get("notes") or ""),
        "thumb": payload.get("thumb") or "",
        "cover": payload.get("cover_image") or "",
    }


def search_labels(query: str, token: str, per_page: int = 20) -> list[dict]:
    """Autocomplete labels by name. Used by the "Add label to watch" UI.

    Returns: list of ``{id, name, thumb, resource_url}``.

    Raises: same as :func:`search_label_releases`.
    """
    if not token or not query.strip():
        return []
    resp = _request_json(
        "/database/search",
        token=token,
        params={"type": "label", "q": query.strip(), "per_page": int(per_page)},
    )
    out = [
        {
            "id": r.get("id"),
            "name": str(r.get("title") or ""),  # label search returns name in 'title'
            "thumb": r.get("thumb") or "",
            "resource_url": r.get("resource_url") or "",
        }
        for r in (resp.data.get("results") or [])
    ]
    _check_near_exhausted(_Response(data=out, remaining=resp.remaining))
    return out


def get_artist_relations(artist_id: int, token: str) -> dict:
    """Return Discogs ``members`` (for groups) and ``groups`` (for individuals)
    for an artist. Used by the artist-adjacent novelty strategy.

    Returns: ``{"members": [{"id", "name"}, …], "groups": [{"id", "name"}, …]}``.

    Raises: same as :func:`search_label_releases`.
    """
    if not token or not artist_id:
        return {"members": [], "groups": []}
    resp = _request_json(f"/artists/{int(artist_id)}", token=token)
    members = [
        {"id": m.get("id"), "name": str(m.get("name") or "")}
        for m in (resp.data.get("members") or [])
        if m.get("id")
    ]
    groups = [
        {"id": g.get("id"), "name": str(g.get("name") or "")}
        for g in (resp.data.get("groups") or [])
        if g.get("id")
    ]
    out = {"members": members, "groups": groups}
    _check_near_exhausted(_Response(data=out, remaining=resp.remaining))
    return out


def validate_token(token: str) -> bool:
    """Check whether a Discogs personal access token is currently valid.

    Calls ``GET /oauth/identity`` — a cheap endpoint that returns the
    authenticated user's identity. Used at server startup + on every fresh
    Discover open to surface a "Re-enter token" banner before the user wastes a
    full scan on an expired token.

    - Returns ``True`` on HTTP 200.
    - Returns ``False`` on HTTP 401.
    - Returns ``False`` on missing/empty token (defensive — saves an HTTP call).
    - Re-raises other transport errors (timeout, 5xx) so the caller can tell
      "token bad" apart from "Discogs unreachable" and surface a different UI
      message.

    Does NOT raise :class:`RateLimitNearExhausted` — this is a startup check,
    not a scan step.
    """
    if not token:
        return False
    try:
        resp = _request_json("/oauth/identity", token=token)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return False
        raise
    return bool(resp.data.get("id") or resp.data.get("username"))
