# Discogs Integration & New Release Discovery

Technical reference for AutoCue's two Discogs-powered features and the shared
API client that backs them.

## Table of Contents

- [1. Overview](#1-overview)
- [2. Discogs API client (`autocue/analysis/discogs.py`)](#2-discogs-api-client-autocueanalysisdiscogspy)
- [3. Authentication](#3-authentication)
  - [3.1 Where the token comes from](#31-where-the-token-comes-from)
  - [3.2 `/api/config` — pre-filling the UI](#32-apiconfig--pre-filling-the-ui)
  - [3.3 `/api/auto-tag/discogs/test` — token validation](#33-apiauto-tagdiscogstest--token-validation)
- [4. Rate limiting (token bucket)](#4-rate-limiting-token-bucket)
  - [4.1 Bucket math](#41-bucket-math)
  - [4.2 Shared by both functions](#42-shared-by-both-functions)
  - [4.3 Why a token bucket and not a leaky bucket](#43-why-a-token-bucket-and-not-a-leaky-bucket)
  - [4.4 Multi-process caveat](#44-multi-process-caveat)
- [5. Caches](#5-caches)
  - [5.1 `discogs._cache` — Styles](#51-discogs_cache--styles)
  - [5.2 `discogs._releases_cache` — Releases](#52-discogs_releases_cache--releases)
  - [5.3 Test isolation](#53-test-isolation)
  - [5.4 Cache rationale](#54-cache-rationale)
- [6. `search_styles(artist, title, token) -> list[str]`](#6-search_stylesartist-title-token---liststr)
  - [6.1 Behaviour](#61-behaviour)
  - [6.2 Why union across the top 5 results, not just the first](#62-why-union-across-the-top-5-results-not-just-the-first)
  - [6.3 Genre vs Style](#63-genre-vs-style)
  - [6.4 Failure modes](#64-failure-modes)
- [7. `search_artist_releases(artist, token, year_from) -> list[dict]`](#7-search_artist_releasesartist-token-year_from---listdict)
  - [7.1 Behaviour](#71-behaviour)
  - [7.2 URL construction](#72-url-construction)
  - [7.3 `per_page=25` default](#73-per_page25-default)
- [8. Discovery feature (`autocue/analysis/discovery.py`)](#8-discovery-feature-autocueanalysisdiscoverypy)
  - [8.1 `library_artists(db, top_n=25) -> list[str]`](#81-library_artistsdb-top_n25---liststr)
  - [8.2 `library_album_set(db) -> set[str]`](#82-library_album_setdb---setstr)
  - [8.3 `iter_new_releases(db, token, since_year, max_artists, per_artist)`](#83-iter_new_releasesdb-token-since_year-max_artists-per_artist)
  - [8.4 `suggest_new_releases(...)` — non-streaming wrapper](#84-suggest_new_releases--non-streaming-wrapper)
- [9. `/api/discover` SSE endpoint](#9-apidiscover-sse-endpoint)
  - [9.1 Request](#91-request)
  - [9.2 Token resolution](#92-token-resolution)
  - [9.3 Read-only DB](#93-read-only-db)
  - [9.4 Streamed events](#94-streamed-events)
- [10. `DiscoverItem` schema](#10-discoveritem-schema)
- [11. Discogs auto-tag flow](#11-discogs-auto-tag-flow)
  - [11.1 Flow](#111-flow)
  - [11.2 Per-track commit semantics](#112-per-track-commit-semantics)
  - [11.3 Token-bucket interaction](#113-token-bucket-interaction)
- [12. UI surface — Discover tab](#12-ui-surface--discover-tab)
- [13. UI surface — Auto-Tag panel](#13-ui-surface--auto-tag-panel)
- [14. Performance characteristics](#14-performance-characteristics)
- [15. Examples](#15-examples)
- [16. Edge cases](#16-edge-cases)
- [17. Testing](#17-testing)
- [18. Network failures and error handling](#18-network-failures-and-error-handling)
- [19. Cache strategy and persistence](#19-cache-strategy-and-persistence)
- [20. Related documentation](#20-related-documentation)
- [Appendix A: File reference index](#appendix-a-file-reference-index)

---

## 1. Overview

AutoCue uses the [Discogs database](https://www.discogs.com/developers) for two
distinct features that share a single in-process HTTP client and rate limiter:

1. **Discogs Style auto-tagging** — given a track's artist + title, look up the
   release on Discogs and persist the result's *Styles* (e.g. "Tech House",
   "Detroit Techno") as Rekordbox **My Tags**. This is the "fill in the genre
   metadata my library is missing" use case.
2. **New Release Discovery** — given the artists that already dominate the
   library, ask Discogs for their *recent* releases, drop anything the DJ
   already owns, and surface the rest as a stream of suggestion cards. This is
   the "what should I be downloading this week?" use case, which feeds directly
   into the YouTube download flow (see `youtube-download.md`).

Both features rely on:

- The same Discogs personal-access-token authentication
  (`autocue/analysis/discogs.py:73`).
- The same in-process token-bucket rate limiter that keeps AutoCue under the
  authenticated 60 req/min ceiling (`autocue/analysis/discogs.py:27-47`).
- Per-process caches so a re-run inside the same `autocue serve` process never
  re-hits Discogs for the same artist/title (`discogs.py:24`, `discogs.py:97`).

The features are surfaced in three places:

| Surface | Discogs Styles | New Release Discovery |
|---|---|---|
| REST API | `POST /api/auto-tag/discogs` (SSE) | `GET /api/discover` (SSE) |
| Web UI | Auto-Tag panel | Discover tab |
| Library code | `analysis.discogs.search_styles` | `analysis.discovery.iter_new_releases` |

Both features are **local-server-only** — they require `autocue serve` to be
running because they read the Rekordbox database (artist/album/play counts)
and, for tagging, write [`DjmdMyTag`](./GLOSSARY.md#djmdmytag--djmdsongmytag) / [`DjmdSongMyTag`](./GLOSSARY.md#djmdmytag--djmdsongmytag) rows. The static GitHub
Pages build of `docs/index.html` cannot reach Discogs.

---

## 2. Discogs API client (`autocue/analysis/discogs.py`)

The client is intentionally tiny: ~180 lines of `urllib` + a module-level
token bucket. There is no third-party HTTP library, no async, no retry
machinery. The two public functions are:

| Function | Endpoint | Cache | Returns |
|---|---|---|---|
| `search_styles(artist, title, token)` | `database/search?type=release&q=…` | `_cache` | `list[str]` of Style names |
| `search_artist_releases(artist, token, year_from)` | `database/search?artist=…&sort=year&sort_order=desc` | `_releases_cache` | `list[dict]` of release records |

Both functions are synchronous, block on `urllib.request.urlopen` with a
10-second timeout, and **return an empty result on any failure** rather than
raising. This is a deliberate "best effort" contract — callers (the auto-tag
loop, the discovery generator) treat absence of data as "skip this track" not
"abort the run".

The base URL and User-Agent are module constants
(`discogs.py:20-21`):

```python
_DISCOGS_API = "https://api.discogs.com/database/search"
_USER_AGENT  = "AutoCue/1.0 +https://github.com/henrigeorge/AutoCue"
```

Discogs requires a meaningful User-Agent for authenticated traffic; the value
above identifies AutoCue clearly so Discogs can contact us if a particular
install is misbehaving.

---

## 3. Authentication

Discogs requires a **personal access token** (PAT) for the 60 req/min
authenticated rate. Unauthenticated requests are capped at 25 req/min and
quickly return 429s on a real library scan, so AutoCue treats the token as
mandatory.

### 3.1 Where the token comes from

The token is resolved in priority order:

1. **Explicit request parameter** — `DiscogsTagRequest.token` (for tagging,
   `schemas.py:407`) or the `?token=` query string (for discovery,
   `routes.py:1842`). Whatever the UI sends wins.
2. **`DISCOGS_TOKEN` environment variable** — read at request time, useful for
   server processes started from a shell with the env already set.
3. **`.env` file at the project root** — a single-line `DISCOGS_TOKEN=...`
   parsed by `_resolve_discogs_token()` (`routes.py:1885-1900`) and
   `get_config()` (`routes.py:1577-1596`). The `.env` lookup walks three
   directories up from `serve/routes.py` to land at the repo root.

The actual environment variable **takes precedence** over the `.env` value
in `get_config()` (`routes.py:1595`):

```python
# Also check actual environment (takes precedence)
token = _os.environ.get("DISCOGS_TOKEN", token)
return {"discogs_token": token}
```

This means a user can override the on-disk `.env` with a one-off
`DISCOGS_TOKEN=xxx autocue serve` invocation without editing the file.

### 3.2 `/api/config` — pre-filling the UI

`GET /api/config` (`routes.py:1577`) returns `{"discogs_token": "..."}`
so the web UI's Discogs panel can pre-fill the input field on page load. The
client never has to *ask* the user to paste the token if it's already on
disk. This endpoint is local-server-only; the GitHub Pages build of the UI
gets a 404 and silently leaves the field blank.

### 3.3 `/api/auto-tag/discogs/test` — token validation

Before kicking off a long tagging run, the UI calls
`POST /api/auto-tag/discogs/test` with `{token}` to validate it
(`routes.py:1599-1616`). The endpoint hits Discogs' identity endpoint:

```python
request = _urlreq.Request(
    "https://api.discogs.com/oauth/identity",
    headers={"Authorization": f"Discogs token={token}", "User-Agent": "AutoCue/1.0"},
)
```

On success it returns `{"ok": True, "username": "..."}`; on any failure the
endpoint raises `HTTPException(400)` carrying the underlying error string. The
UI uses the username to show a confirmation toast ("Connected as your-handle").

Note that this validation request **bypasses the token bucket**. It hits a
different endpoint (`/oauth/identity` rather than `/database/search`) and uses
its own ad-hoc `urllib` call rather than going through `_acquire_token()`. A
single test ping is cheap and one extra request will never push a real session
over 60 req/min.

---

## 4. Rate limiting (token bucket)

Discogs enforces 60 req/min for authenticated traffic. AutoCue stays under
that ceiling with a single shared token bucket
(`autocue/analysis/discogs.py:26-47`):

```python
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
```

### 4.1 Bucket math

- **Capacity**: 60 tokens (`_MAX_TOKENS = 60.0`).
- **Refill**: 1 token per second (`_FILL_RATE = 1.0`), refilled lazily on every
  acquire — there is no background thread.
- **Burst behaviour**: a fresh process starts with 60 tokens. The first 60
  back-to-back calls succeed instantly; from there the bucket is empty and
  each subsequent call sleeps for the time it takes for one token to refill
  (~1 second steady state).
- **Steady-state throughput**: 60 req/min, matching the Discogs ceiling
  exactly.

The bucket is a single module-level instance guarded by `_rate_lock`, so
*every caller in the same process shares it* — `search_styles` and
`search_artist_releases` cannot starve each other but they also cannot
combine to exceed 60 req/min between them.

### 4.2 Shared by both functions

Both `search_styles` (`discogs.py:67`) and `search_artist_releases`
(`discogs.py:128`) call `_acquire_token()` immediately before
`urllib.request.urlopen`. The identity-validation endpoint
(`/api/auto-tag/discogs/test`) does **not** call it — see [§3.3 `/api/auto-tag/discogs/test` — token validation](#33-apiauto-tagdiscogstest--token-validation) above.

### 4.3 Why a token bucket and not a leaky bucket

A leaky bucket would enforce a strict 1 req/sec spacing; the token bucket lets
small bursts (the first 60 calls) go through at full speed, which is what
happens when the UI clicks "Tag" on a small selection. Discogs explicitly
allows bursts under the per-minute average, so the token bucket squeezes out
more useful throughput.

### 4.4 Multi-process caveat

The bucket is per-process. If the user runs two `autocue serve` instances in
parallel pointing at the same Discogs token, each will think it has its own
60 req/min budget and Discogs will start returning 429s. AutoCue assumes a
single-server local install, which is the documented usage.

---

## 5. Caches

Both client functions are wrapped by **per-process, never-evicted** caches
keyed by the request inputs. A second call with the same key never hits
Discogs.

### 5.1 `discogs._cache` — Styles

```python
_cache: dict[str, list[str]] = {}        # discogs.py:24
```

- **Key**: `f"{artist.lower().strip()}|||{title.lower().strip()}"` (a single
  string, not a tuple — easier to inspect at the REPL).
- **Value**: the deduplicated list of Styles for that artist/title.
- **Population**: written at the very end of `search_styles` after the API
  response is parsed (`discogs.py:92`).
- **Eviction**: none. Restart the server to clear it.

### 5.2 `discogs._releases_cache` — Releases

```python
_releases_cache: dict[str, list[dict]] = {}    # discogs.py:97
```

- **Key**: `f"{artist.lower().strip()}|||{year_from or 0}"` so different
  `year_from` filters get separate entries (a 2024-onward query and a
  2020-onward query of the same artist do not collide).
- **Value**: the list of release dicts (see [§7 `search_artist_releases`](#7-search_artist_releasesartist-token-year_from---listdict) for the shape).
- **Population**: written at the end of `search_artist_releases`
  (`discogs.py:166`).
- **Eviction**: none.

### 5.3 Test isolation

These caches are module globals, so tests that exercise the client need to
clear them. The conftest fixture (`tests/conftest.py`) wipes the analysis
caches between tests, and `test_discovery.py` sidesteps the issue entirely
by patching `discovery.search_artist_releases` rather than calling through to
the real `discogs.py` module (see [§17 Testing](#17-testing)).

### 5.4 Cache rationale

Discogs responses are extremely stable — an artist's Style metadata for a
given track does not change minute to minute. Within a single `autocue serve`
session a DJ will frequently re-tag the same playlist, scroll back and forth,
or rerun discovery. Caching makes those interactions instant. The lack of
eviction is intentional: even a 5000-track library tops out at ~5MB of cached
strings, which is cheaper than a single duplicate API call.

---

## 6. `search_styles(artist, title, token) -> list[str]`

`autocue/analysis/discogs.py:50`

```python
def search_styles(artist: str, title: str, token: str) -> list[str]:
```

### 6.1 Behaviour

1. **Empty-token guard** — returns `[]` immediately if `token` is falsy
   (`discogs.py:56`).
2. **Cache lookup** — composes `cache_key = "{artist}|||{title}"` (both
   lowercased and stripped) and returns the cached value if present
   (`discogs.py:59-61`).
3. **Query construction** — combines artist and title into the `q` parameter
   and asks for `type=release` with `per_page=5` (`discogs.py:63-65`).
   Searching by `q` rather than separate `artist`+`track` fields gives the
   widest match (Discogs' full-text index covers all release fields).
4. **Token-bucket acquire** — `_acquire_token()` blocks if the bucket is
   empty (`discogs.py:67`).
5. **HTTP request** — `urllib.request.urlopen` with the
   `Authorization: Discogs token=...` header and a 10s timeout
   (`discogs.py:70-78`). On any exception the function logs a warning and
   returns `[]`.
6. **Style extraction** — iterates *every* result in the top-5 page, unions
   each result's `style` list, and preserves first-seen order
   (`discogs.py:84-90`).

### 6.2 Why union across the top 5 results, not just the first

Discogs has many versions of the same release (Vinyl A-side, Vinyl B-side,
12" remix, digital, compilation). Each release row carries its own Style
metadata, and a curator may have tagged "Deep House" on one pressing and
"Tech House" on another. Taking the union of the top 5 gives more useful
tag coverage than locking onto the first result. Order is preserved so the
most-frequent Style on the highest-ranked result remains first.

### 6.3 Genre vs Style

Discogs has both a `genre` field (broad — "Electronic", "Rock") and a `style`
field (specific — "Tech House", "Deep House"). `search_styles` only returns
**style**. Style is what DJs actually want as a My Tag; genre is too coarse
to be useful for set planning.

> Note: the docstring header for this section says "styles + genres union",
> but the current implementation (`discogs.py:84-90`) reads `result.get("style", [])`
> only. The function name `search_styles` reflects what actually happens.

### 6.4 Failure modes

| Cause | Result |
|---|---|
| No token | `[]` (silent) |
| Network error / timeout | `[]` + WARNING log |
| Invalid token (401) | `[]` + WARNING log |
| Artist/title not found | `[]` (no exception — Discogs returns an empty `results` list) |
| Malformed JSON | `[]` + WARNING log |

---

## 7. `search_artist_releases(artist, token, year_from) -> list[dict]`

`autocue/analysis/discogs.py:100`

```python
def search_artist_releases(
    artist: str,
    token: str,
    year_from: int | None = None,
    per_page: int = 25,
) -> list[dict]:
```

### 7.1 Behaviour

1. **Empty-input guard** — returns `[]` if `token` or `artist` is empty
   (`discogs.py:112`).
2. **Cache lookup** — `cache_key = f"{artist}|||{year_from or 0}"`
   (`discogs.py:115-117`).
3. **Query construction** — uses Discogs' dedicated `artist=` filter rather
   than free-text search, sorts by year descending, and requests 25 results
   (`discogs.py:119-126`):

   ```
   ?artist=Daft+Punk&type=release&sort=year&sort_order=desc&per_page=25
   ```

4. **Token-bucket acquire + HTTP request** — same pattern as
   `search_styles` (`discogs.py:128-142`).
5. **Year filter** — releases with `year < year_from` (or missing year)
   are skipped (`discogs.py:147-148`). `_parse_year()` coerces strings like
   `"2024"` or `"2024-05"` to an int (`discogs.py:170-177`).
6. **Title splitting** — Discogs returns titles in the form
   `"Artist - Album"`; the function splits on `" - "` to get a clean album
   name (`discogs.py:151`).
7. **Result shape** — each dict carries:

   ```python
   {
     "title":   "Daft Punk - Discovery",   # raw Discogs title
     "artist":  "Daft Punk",                # the query artist (echoed back)
     "album":   "Discovery",                # parsed from title
     "year":    2001,
     "thumb":   "https://i.discogs.com/...thumb.jpg",
     "cover":   "https://i.discogs.com/...cover.jpg",
     "genres":  ["Electronic"],
     "styles":  ["House", "Disco"],
     "url":     "https://www.discogs.com/release/12345-...",
     "id":      12345,
     "formats": ["Vinyl", "LP", "Album"],
   }
   ```

### 7.2 URL construction

Discogs returns a relative `uri` (e.g. `/release/12345-Daft-Punk-Discovery`);
`search_artist_releases` prefixes `https://www.discogs.com` to form a full
URL (`discogs.py:161`). If `uri` is missing it falls back to the API's
`resource_url`.

### 7.3 `per_page=25` default

Per-page defaults to 25 because it's the largest single-page response that
still fits one Discogs request and is more than enough headroom — discovery
caps each artist's surfaced suggestions at `per_artist=5` upstream
(see [§8.3 `iter_new_releases`](#83-iter_new_releasesdb-token-since_year-max_artists-per_artist)), so 25 leaves plenty of slack after the year filter and the
owned-album filter prune the list.

---

## 8. Discovery feature (`autocue/analysis/discovery.py`)

Discovery turns the Discogs client into a "what should I buy this week?"
recommender. The entire module is ~125 lines and contains three functions
plus a generator.

### 8.1 `library_artists(db, top_n=25) -> list[str]`

`autocue/analysis/discovery.py:26`

```python
def library_artists(db, top_n: int = 25) -> list[str]:
    counts: Counter[str] = Counter()
    try:
        for c in db.get_content().all():
            name = str(getattr(c, "ArtistName", "") or "").strip()
            if name:
                counts[name] += 1
    except Exception as exc:
        _log.warning("library_artists: could not read content: %s", exc)
        return []
    return [name for name, _ in counts.most_common(top_n)]
```

- **Frequency proxy for "DJ cares about"** — artists that appear most often
  in the library are presumed to be the ones the DJ most wants to track for
  new releases. This is a simple proxy that avoids any need for play-history
  weighting.
- **Why top_n matters** — every artist costs one Discogs API call. At 60
  req/min and a 25-artist default, a full scan takes ~25 seconds steady
  state (less when the cache is warm). Increasing `top_n` to 100 (the API
  ceiling, `routes.py:1840`) makes the scan ~1.5 minutes and uses ~100 of
  the 60-token initial burst.
- **Blank-artist guard** — empty or whitespace-only `ArtistName` values are
  skipped (`discovery.py:36-38`). Rekordbox often has these on
  user-imported tracks with missing tags.
- **Failure mode** — any DB read error returns `[]` and logs a warning;
  the caller (`iter_new_releases`) treats this as "no work to do".

### 8.2 `library_album_set(db) -> set[str]`

`autocue/analysis/discovery.py:45`

```python
def library_album_set(db) -> set[str]:
    owned: set[str] = set()
    try:
        for c in db.get_content().all():
            album = _norm(getattr(c, "AlbumName", "") or "")
            if album:
                owned.add(album)
    except Exception as exc:
        _log.warning("library_album_set: could not read content: %s", exc)
    return owned
```

- **Normalization** — `_norm(s)` lowercases the string and collapses runs of
  whitespace to a single space (`discovery.py:21-23`). This makes
  `"  Deep   House  "` and `"deep house"` match.
- **Why a `set`** — `iter_new_releases` does O(n) membership tests against
  this set as it walks Discogs results; a set keeps that O(1).
- **Not a strong match** — title similarity isn't fuzzy. A reissue titled
  `"Discovery (Remastered)"` will *not* match an owned `"Discovery"`. This
  is intentional — the DJ may genuinely want the remaster.

### 8.3 `iter_new_releases(db, token, since_year, max_artists, per_artist)`

`autocue/analysis/discovery.py:62`

A **generator** that yields `(processed: int, total: int, suggestion: dict | None)`
tuples so the SSE endpoint can stream progress as it goes:

```python
def iter_new_releases(
    db,
    token: str,
    since_year: int | None = None,
    max_artists: int = 25,
    per_artist: int = 5,
):
    if since_year is None:
        since_year = datetime.now().year - 1

    artists = library_artists(db, top_n=max_artists)
    owned = library_album_set(db)
    seen: set[str] = set()
    total = len(artists)

    for i, artist in enumerate(artists):
        emitted = 0
        try:
            releases = search_artist_releases(artist, token, year_from=since_year)
        except Exception as exc:
            _log.warning("iter_new_releases: Discogs failed for %r: %s", artist, exc)
            releases = []

        for rel in releases:
            if emitted >= per_artist:
                break
            album_key = _norm(rel.get("album", ""))
            if not album_key or album_key in owned or album_key in seen:
                continue
            seen.add(album_key)
            emitted += 1
            suggestion = dict(rel)
            suggestion["query"] = f"{artist} {rel.get('album', '')}".strip()
            yield (i + 1, total, suggestion)

        if emitted == 0:
            # Still report progress for artists with no new releases.
            yield (i + 1, total, None)
```

Key behaviours:

- **Default `since_year` is *last year*** — `datetime.now().year - 1`. So
  "new releases" really means "anything from the last year and a half" in
  practice, which catches Q4-of-previous-year drops the DJ might have missed.
- **Per-artist cap** — only the first `per_artist` (default 5) unowned,
  un-deduped releases per artist are emitted. This stops a prolific artist
  with 30 reissues from monopolising the suggestion stream.
- **Two-axis dedupe**:
  - `owned` (set of normalized album titles already in the Rekordbox
    library) prevents suggesting what the DJ already has.
  - `seen` (running set across all artists in this scan) prevents the same
    compilation appearing once per featured artist.
- **Progress-only ticks** — for an artist that produced zero unowned
  releases, a single `(processed, total, None)` tuple is yielded
  (`discovery.py:107-109`). The UI uses this to advance the progress bar
  even when no card is rendered.
- **Discogs failures are swallowed per-artist** — a network error on one
  artist becomes "no releases for that artist", not an aborted scan
  (`discovery.py:91-93`). This is tested explicitly in
  `TestIterNewReleases.test_discogs_failure_is_swallowed`.
- **`query` field** — every suggestion dict gets an extra `query` key
  containing `"{artist} {album}"`, ready to be passed to the YouTube
  downloader's search (`discovery.py:104`).

### 8.4 `suggest_new_releases(...)` — non-streaming wrapper

`autocue/analysis/discovery.py:112`

```python
def suggest_new_releases(db, token, since_year=None, max_artists=25, per_artist=5) -> list[dict]:
    return [s for _, _, s in iter_new_releases(
        db, token, since_year=since_year,
        max_artists=max_artists, per_artist=per_artist,
    ) if s is not None]
```

An eager wrapper that materializes the generator and filters out
progress-only ticks. Used by callers that want the full list at once
(unit tests, future CLI integrations). The HTTP endpoint uses
`iter_new_releases` directly so it can stream.

---

## 9. `/api/discover` SSE endpoint

`autocue/serve/routes.py:1837`

```python
@router.get("/discover")
def discover_new_releases(
    since_year: int | None = Query(None),
    max_artists: int = Query(25, ge=1, le=100),
    per_artist: int = Query(5, ge=1, le=20),
    token: str = Query(""),
    db=Depends(get_ro_db),
):
```

### 9.1 Request

| Param | Type | Default | Bounds | Notes |
|---|---|---|---|---|
| `since_year` | `int?` | `None` → last year | — | Year filter passed to `search_artist_releases` |
| `max_artists` | `int` | 25 | 1–100 | Top-N artists to scan |
| `per_artist` | `int` | 5 | 1–20 | Suggestion cap per artist |
| `token` | `str` | `""` | — | Optional Discogs PAT (falls back to env/.env) |

`GET` is used (not POST) because the request is idempotent and small, and
because it lets the browser's native `EventSource` consume the stream if a
client ever wants to. The current UI uses `fetch` + `ReadableStream`
(`_consumeSSE`) for consistency with the POST-based SSE endpoints.

### 9.2 Token resolution

`tok = (token or "").strip() or _resolve_discogs_token()`
(`routes.py:1854`). If neither the query param nor the env/.env yields a
token, the endpoint returns `HTTPException(400)` immediately — the scan
would just return `[]` from every Discogs call anyway.

### 9.3 Read-only DB

Uses `get_ro_db` (read-only) rather than `get_db`. Discovery never writes —
it only counts artists and reads album names. Using the RO dependency
avoids contending for the write lock with a parallel tagging run.

### 9.4 Streamed events

The endpoint streams two event shapes:

**Progress-only tick** (artist produced no card):

```json
{"processed": 4, "total": 25, "suggested": 6}
```

**Suggestion event**:

```json
{
  "processed": 7,
  "total": 25,
  "suggested": 7,
  "artist": "KH",
  "album": "Looking At Your Pager",
  "title": "KH - Looking At Your Pager",
  "year": 2024,
  "thumb": "https://i.discogs.com/...",
  "cover": "https://i.discogs.com/...",
  "genres": ["Electronic"],
  "styles": ["Breakbeat", "UK Garage"],
  "formats": ["Vinyl", "12\""],
  "url": "https://www.discogs.com/release/...",
  "query": "KH Looking At Your Pager"
}
```

**Final event**:

```json
{"done": true, "total": 25, "suggested": 12}
```

**Error event** (one-shot, before the `done`):

```json
{"error": "..."}
```

A try/except around the generator loop (`routes.py:1877-1879`) ensures any
unexpected failure inside `iter_new_releases` produces an event the client
can render, rather than dropping the connection.

---

## 10. `DiscoverItem` schema

`autocue/serve/schemas.py:490`

```python
class DiscoverItem(BaseModel):
    """One suggested new release (also the SSE event payload)."""
    processed: int
    total: int
    artist: str | None = None
    album: str | None = None
    title: str | None = None
    year: int | None = None
    thumb: str | None = None
    cover: str | None = None
    genres: list[str] = []
    styles: list[str] = []
    formats: list[str] = []        # Discogs format tags e.g. ["Vinyl","LP","Album"]
    url: str | None = None
    query: str | None = None       # ready-made "artist album" download query
    done: bool = False
    suggested: int = 0
```

A few fields deserve callouts:

- **`title` vs `album`** — `title` is the raw Discogs title (usually
  `"Artist - Album"`); `album` is the parsed album-only string. The UI
  renders `album` as the card heading.
- **`thumb` vs `cover`** — `thumb` is a small JPEG (~150px) used in the
  card grid; `cover` is the full-resolution image used on hover/expand.
- **`formats`** — Discogs format tags (e.g. `["Vinyl", "LP", "Album"]`,
  `["CD", "Mixed"]`, `["File", "MP3", "320 kbps"]`). The UI renders these
  as small chips so a DJ can tell at a glance whether the release is a
  vinyl-only thing they need to buy or a digital release they can grab now.
- **`query`** — the ready-made `"artist album"` string that gets fed to
  `/api/download` if the user clicks the YouTube button. This is the
  contract that ties discovery to the download feature.
- **`done`** / **`suggested`** — the `done` field is the marker on the
  final event; `suggested` is a running counter for the UI progress bar.

Note that `DiscoverItem` is the schema for *both* suggestion events and
progress-only ticks — for the latter, all the optional fields are absent
and only `processed`/`total`/`suggested` carry meaning.

---

## 11. Discogs auto-tag flow

(See `auto-tag.md` for the broader auto-tagging architecture; this section
covers only the Discogs-specific path.)

The endpoint is `POST /api/auto-tag/discogs` (SSE, `routes.py:1619`). It
expects `DiscogsTagRequest`:

```python
class DiscogsTagRequest(BaseModel):
    track_ids: list[int]
    token: str
    dry_run: bool = False
    skip_existing: bool = False
```

### 11.1 Flow

1. **Pre-flight**: if Rekordbox is running and this is not a dry-run, return
   409 (`routes.py:1632-1633`).
2. **Pre-build tag-name map** (only if `skip_existing`): one query to
   `DjmdMyTag` builds `{tag_id: name}` for the whole library so each
   per-track check is in-memory only (`routes.py:1644-1647`).
3. **For each track**:
   1. Fetch the [`DjmdContent`](./GLOSSARY.md#djmdcontent) row (`routes.py:1651`).
   2. **If `skip_existing`**: check `DjmdSongMyTag` for tags whose name is
      not in `auto_tag.ALL_AUTOCUE_TAG_NAMES`. The assumption is that
      non-AutoCue tags are pre-existing Discogs styles (or manually
      curated genre tags) and the user doesn't want them duplicated
      (`routes.py:1657-1670`).
   3. Call `search_styles(artist, title, token)`.
   4. **If empty styles**: skip + emit a progress event.
   5. **Otherwise**: for each style, `ensure_tag_by_name(db, style)`
      idempotently creates (or reuses) a `DjmdMyTag` row, then inserts a
      `DjmdSongMyTag` linking the track to the tag (skipping if the link
      already exists). Commit per track (`routes.py:1681-1697`).
   6. Emit a per-track event with the styles and a running `tagged` counter.
4. **Final event**: `{"done": true, "tagged": T, "skipped": S, "errors": E}`.

### 11.2 Per-track commit semantics

Each track's writes are committed individually (`routes.py:1697`). A failure
on one track triggers a `db.session.rollback()` (`routes.py:1706`) but does
not abort the loop — the next track gets a fresh transaction. This mirrors
the per-track commit pattern used by `/api/enrich-comments/stream`.

### 11.3 Token-bucket interaction

`search_styles` blocks on `_acquire_token()`. In a 60-track tagging run,
the first 60 calls finish in seconds and the next ones pace out at
~1 req/sec. The UI's progress bar reflects this — the first sixty cards
flash by, then the rate visibly settles.

---

## 12. UI surface — Discover tab

In `docs/index.html`, the Discover tab (one of three top-level tabs alongside
Cues and Library) hosts the new-release feature. Key pieces:

- **Discogs token field** — pre-filled from `/api/config` on page load.
- **"Find new releases" button** — opens an SSE connection to `/api/discover`
  via the shared `_consumeSSE(response, onEvent)` reader. Reusing
  `_consumeSSE` means progress event handling, error event handling, and
  the `done` flag all work the same way they do for the Download flow.
- **`_renderSuggestion(item)`** — turns one `DiscoverItem` into a card:
  cover thumb, artist + album header, year, format chips (one chip per
  `formats[]` entry), genre/style pills, an "Open on Discogs" link, and a
  YouTube download button that POSTs `{query: item.query}` to
  `/api/download`. The renderer passes every string through `_esc()` so
  user-supplied Discogs text cannot inject HTML.
- **Format chips** are rendered with a slightly different visual treatment
  from style pills so the DJ can quickly distinguish "Vinyl only" from
  "released on streaming".

The renderer and `_esc` helper have parallel Vitest tests in
`tests/web/ui-logic.test.js` that copy the functions verbatim — keep those
in sync.

---

## 13. UI surface — Auto-Tag panel

The Discogs styles flow lives inside the broader Auto-Tag panel. It exposes:

- **Discogs token input** (also pre-filled from `/api/config`).
- **"Test connection" button** → `POST /api/auto-tag/discogs/test`. Shows a
  toast with the connected username or the error message.
- **"Skip tracks that already have non-AutoCue tags" checkbox** → sets
  `DiscogsTagRequest.skip_existing`. Documented behaviour: skips tracks
  whose `DjmdSongMyTag` rows include any tag *not* in
  `auto_tag.ALL_AUTOCUE_TAG_NAMES`.
- **Dry-run toggle** → `DiscogsTagRequest.dry_run`. With dry-run set,
  Rekordbox is allowed to be running and no DB writes happen — the
  endpoint still does the Discogs lookups and returns the style lists,
  so the user can preview what would be applied.
- **"Tag with Discogs styles" button** → opens an SSE stream to
  `POST /api/auto-tag/discogs`. Progress events update a counter; the
  final event drives a summary toast ("Tagged 47, skipped 12, errors 1").

---

## 14. Performance characteristics

| Scenario | Discogs calls | Wall-clock (cold cache) | Wall-clock (warm cache) |
|---|---|---|---|
| Tag a 30-track playlist with `search_styles` | 30 | ~30s (burst) | ~0s |
| Tag a 200-track playlist with `search_styles` | 200 | 60 in ~1s + 140 at 1 req/s ≈ 2m20s | ~0s |
| Discover with `max_artists=25` | 25 | ~25s (within burst) | ~0s |
| Discover with `max_artists=100` | 100 | 60 in ~1s + 40 at 1 req/s ≈ 41s | ~0s |

The "warm cache" column assumes the same artist/title (or artist/year)
combos as before. The cache is per-process so an `autocue serve` restart
loses all of it.

**Library scale**: `library_artists` walks every `DjmdContent` row to build
the frequency Counter. For a 5000-track library this is a single SQL fetch
plus a 5000-element `Counter()` increment loop — well under 100ms on a
local SQLCipher DB. Discovery's bottleneck is always Discogs, not the
local DB.

---

## 15. Examples

### 15.1 `search_styles` — concrete invocation

```python
from autocue.analysis.discogs import search_styles

styles = search_styles("Daft Punk", "Around the World", token="abc123...")
# Typical result (order preserved, deduplicated):
# ["House", "Disco", "Tech House"]
```

A cold call sleeps zero or one seconds (token bucket usually has tokens
available at the start of a session), makes one HTTPS call, returns in
~300ms. A warm call returns the cached list in microseconds.

### 15.2 Sample `DiscoverItem` JSON

A representative suggestion event from `/api/discover`:

```json
{
  "processed": 7,
  "total": 25,
  "suggested": 4,
  "artist": "Floating Points",
  "album": "Cascade",
  "title": "Floating Points - Cascade",
  "year": 2024,
  "thumb": "https://i.discogs.com/abc/thumb.jpg",
  "cover": "https://i.discogs.com/abc/cover.jpg",
  "genres": ["Electronic"],
  "styles": ["IDM", "Leftfield", "Deep House"],
  "formats": ["Vinyl", "LP", "Album"],
  "url": "https://www.discogs.com/release/30000000-Floating-Points-Cascade",
  "query": "Floating Points Cascade"
}
```

The progress-only tick for the *next* artist (no releases found) looks like:

```json
{"processed": 8, "total": 25, "suggested": 4}
```

And the terminal event:

```json
{"done": true, "total": 25, "suggested": 12}
```

---

## 16. Edge cases

| Case | Behaviour |
|---|---|
| **Empty token** in `search_styles` | Returns `[]` immediately, no HTTP call (`discogs.py:56`). |
| **Empty token** at `/api/discover` | `HTTPException(400, "Discogs token required ...")` (`routes.py:1856`). |
| **Invalid token (401)** | `search_styles` catches the urllib error and returns `[]` + WARNING log. The tag run records the track as "skipped". `/api/auto-tag/discogs/test` raises `HTTPException(400)` so the UI can show the validation failure. |
| **Artist/title not found** | Discogs returns `{"results": []}`; `search_styles` returns `[]`. |
| **`year_from` filters out all results** | `search_artist_releases` returns `[]`; discovery emits a progress-only tick for that artist. |
| **API timeout (>10s)** | `urllib` raises; both functions catch and return `[]` + WARNING log. The scan continues with the next artist. |
| **Library has fewer artists than `top_n`** | `Counter.most_common(top_n)` returns however many it has; `iter_new_releases` reports `total = len(artists)` so the UI's progress denominator is correct. |
| **Empty library** | `library_artists` returns `[]`; `iter_new_releases` yields nothing and the SSE stream goes straight to `{"done": true, "total": 0, "suggested": 0}`. |
| **DB read failure** | `library_artists` / `library_album_set` catch and log; both return empty containers. The discovery scan completes immediately with zero suggestions. |
| **Discogs returns malformed JSON** | `json.loads` raises; caught in the same `except Exception` and treated as a network error. |
| **`AlbumName` exact-match miss (remasters)** | Reissues with a parenthetical suffix are NOT filtered out by `owned`; they appear as suggestions. This is intentional — the DJ may want the remaster. |
| **Same compilation appears under multiple library artists** | The `seen` set in `iter_new_releases` is normalized by album title and shared across artists, so a "Various — Best of 2024" comp is yielded at most once per scan. |
| **Token bucket starved by previous burst** | `_acquire_token` blocks; this is observable as the scan slowing to ~1 suggestion/sec after the first 60 calls. The UI still gets progress events so it doesn't look frozen. |
| **Rekordbox running during tagging** | `/api/auto-tag/discogs` returns 409 unless `dry_run=True` (`routes.py:1632-1633`). |

---

## 17. Testing

### 17.1 `tests/test_discovery.py` — 17 tests

The discovery module is covered by `tests/test_discovery.py:1`. The test
file patches `discovery.search_artist_releases` rather than hitting Discogs,
so tests run offline and never consume the rate budget.

Coverage breakdown:

| Test class | What it covers |
|---|---|
| `TestLibraryArtists` (5 tests) | Frequency ordering, blank-name skipping, `top_n` truncation, empty library, DB read failure → `[]`. |
| `TestLibraryAlbumSet` (2 tests) | Whitespace + case normalization, blank-album skipping. |
| `TestIterNewReleases` (7 tests) | Unowned releases pass through; owned albums are filtered; cross-artist dedupe via `seen`; `per_artist` cap; progress-only ticks for empty results; default `since_year` is last year; Discogs failures are swallowed per-artist. |
| `TestSuggestNewReleases` (1 test) | Wrapper returns a flat list of suggestion dicts. |
| Module-level helpers (`_content`, `_db`, `_release`) | Build SimpleNamespace stand-ins for the pyrekordbox content rows so tests don't need a real DB. |

Critical test patterns from this file:

- **`patch.object(discovery, "search_artist_releases", ...)`** instead of
  patching `discogs.search_artist_releases`. Discovery imports the function
  by name at the top of the module (`discovery.py:16`), so the patch must
  target `discovery`'s namespace.
- **Generator materialization** — tests call `list(iter_new_releases(...))`
  to materialize the generator and inspect every tuple, then filter for
  `s is not None` to count actual suggestions.
- **Default-year assertion** — `test_default_since_year_is_last_year`
  captures the `year_from` kwarg passed into the patched
  `search_artist_releases` and asserts it equals `datetime.now().year - 1`.
  This guards against accidental drift if someone "fixes" the default to
  the current year.

### 17.2 Why the tests do not hit Discogs

Two reasons:

1. **Determinism** — Discogs results change as curators edit the database.
   Tests that assert a specific Style list would flake.
2. **Rate budget** — running the test suite repeatedly in CI would burn
   through the token bucket of whoever's PAT was used.

The Discogs client itself (`discogs.py`) is not directly unit-tested
because its surface is small and stable, and because the only meaningful
test would mock `urllib.request.urlopen`, which is just retesting Python's
stdlib. Coverage of the integration is exercised end-to-end via the
discovery tests and the auto-tag route tests in `test_serve_routes.py`.

---

## 18. Network failures and error handling

Both `search_styles` (`autocue/analysis/discogs.py:50`) and
`search_artist_releases` (`autocue/analysis/discogs.py:100`) follow the same
contract: **return `[]` on any failure**, log a warning, and let the caller
decide what "no data" means. The HTTP layer is `urllib.request.urlopen` with a
hard 10-second timeout. There is **no retry** — a single failed request becomes
a single empty result.

The blanket `except Exception` at `discogs.py:79` and `discogs.py:140` is
deliberate: callers (the auto-tag SSE loop, the discovery generator) are
designed to skip individual tracks/artists rather than abort the entire run.
This means a network blip mid-scan degrades the result set but never crashes
the server.

### 18.1 Invalid token (401)

| Surface | Exception path | User-visible behaviour | Cache poisoned? | Aborts batch? |
|---|---|---|---|---|
| `search_styles` / `search_artist_releases` | `urllib.error.HTTPError` (subclass of `URLError`) caught by the generic `except Exception` (`discogs.py:79`, `discogs.py:140`) | Returns `[]` + `WARNING` log line `"Discogs API error for ... : HTTP Error 401: Unauthorized"`. The track/artist is silently skipped. | **No** — the result is never written to `_cache` / `_releases_cache` because the `_cache[...] = styles` assignment is past the `return []` short-circuit. | **No** — the loop continues with the next item. |
| `/api/auto-tag/discogs/test` | Calls Discogs' `/oauth/identity` endpoint via its own ad-hoc `urllib` call (bypasses the token bucket). On 401 the route's `try` catches and re-raises as `HTTPException(400)` carrying the underlying error string (`routes.py:1599-1616`). | UI shows the validation error in a toast ("Connected as ..." replaced with the error text). The user is prompted to re-enter the token before kicking off a batch. | n/a (does not touch `_cache` / `_releases_cache`). | n/a (validation runs before the batch). |

The UI **recommends** the user click "Test connection" first so a bad token is
caught up front, but nothing forces it — a batch with a bad token will run to
completion writing zero tags and emitting one `WARNING` per track.

### 18.2 Upstream 429 (rate limit hit)

The token bucket ([§4](#4-rate-limiting-token-bucket)) is sized to match
Discogs' 60 req/min ceiling, so a single `autocue serve` process should never
trigger a 429 in normal use. The cases that can still produce one:

- Two `autocue serve` processes sharing a token (see [§4.4](#44-multi-process-caveat)).
- An unrelated script using the same PAT in parallel.
- Discogs returning 429 spuriously under their own load.

When a 429 does happen, `urllib.error.HTTPError` is raised mid-request and
caught by the same generic handler. The result: `[]` + a `WARNING` log line,
the affected track/artist is skipped, and the **next** request continues
normally. There is no exponential backoff and no awareness that we're being
rate-limited — the next `_acquire_token()` will hand out a token immediately
because the local bucket is happy. This can produce a cascade of 429s if the
upstream throttle persists, but each one independently fails-soft.

The cache is **not** poisoned (same short-circuit reason as 18.1). On a retry
in the same session, the affected items will be re-queried.

### 18.3 Service unavailable (503)

Same path as 401/429: `urllib.error.HTTPError` → generic handler → `[]` +
`WARNING`. There is no retry, no service-health awareness, no fallback. If
Discogs is having a bad day, the auto-tag run completes with most/all tracks
emitting empty style lists and the discovery scan completes with most/all
artists producing zero suggestions. Both endpoints still emit a clean `done`
event so the UI doesn't hang.

### 18.4 Timeout / network drop

`urllib.request.urlopen(req, timeout=10)` raises `socket.timeout`
(`TimeoutError` on Python 3.10+) after 10s. DNS failures and TCP resets raise
`urllib.error.URLError`. Both are subclasses of `OSError` and are caught by
the generic `except Exception`. Result: `[]` + `WARNING` log line, no retry,
caller skips.

The 10-second timeout is intentional. Longer would let one stuck request stall
a per-track commit loop; shorter would false-positive against a slow
intercontinental TLS handshake.

### 18.5 Empty / malformed response

Two flavours:

- **HTTP 200 with empty `results`** — `data.get("results", [])` yields `[]`,
  the style/release loop has nothing to do, the function returns `[]`
  **and writes `[]` into the cache**. This is the one case where the cache
  *is* populated with an empty result: a 200 OK with zero hits is treated as
  authoritative ("Discogs has nothing for this artist/title") rather than a
  transient failure. A second call with the same key will return `[]` from
  the cache without hitting the network. To re-query, the user must restart
  `autocue serve` (or clear `discogs._cache` / `discogs._releases_cache`
  manually from a REPL).
- **HTTP 200 with malformed JSON** — `json.loads` raises `json.JSONDecodeError`,
  caught by the generic handler, treated identically to a network error
  (`[]` + `WARNING`, no cache write). Reaching this state implies a
  Discogs-side bug or a middlebox rewriting the response — neither has been
  observed in practice.

### 18.6 Summary

| Failure | Python exception | UI surface | Cache poisoned? | Aborts batch? |
|---|---|---|---|---|
| Invalid token (401) | `urllib.error.HTTPError` | Silent skip + `WARNING` log; validation route returns 400 | No | No |
| Rate limit (429) | `urllib.error.HTTPError` | Silent skip + `WARNING` log | No | No |
| Service unavailable (503) | `urllib.error.HTTPError` | Silent skip + `WARNING` log | No | No |
| Timeout (>10s) | `TimeoutError` / `socket.timeout` | Silent skip + `WARNING` log | No | No |
| DNS / TCP reset | `urllib.error.URLError` | Silent skip + `WARNING` log | No | No |
| Empty `results` (200 OK) | None | Silent skip (no error) | **Yes** — `[]` written to cache | No |
| Malformed JSON | `json.JSONDecodeError` | Silent skip + `WARNING` log | No | No |

The recurring theme: **the Discogs layer never crashes the run**. Every error
path is squeezed into "no data for this item" and the loop moves on.

---

## 19. Cache strategy and persistence

AutoCue's Discogs caches are minimal by design. The full picture, in one place:

### 19.1 What's cached

- `discogs._cache` (`autocue/analysis/discogs.py:24`) — `dict[str, list[str]]`,
  keyed by `"{artist}|||{title}"` lowercased and stripped.
- `discogs._releases_cache` (`autocue/analysis/discogs.py:97`) —
  `dict[str, list[dict]]`, keyed by `"{artist}|||{year_from or 0}"` lowercased
  and stripped.

Both are written at the end of a successful API call ([§5.1](#51-discogs_cache--styles),
[§5.2](#52-discogs_releases_cache--releases)) and consulted at the top of every
subsequent call.

### 19.2 Lifetime — in-process only

- The caches live on module globals. They are populated lazily and **cleared
  only when the Python process exits**.
- Restarting `autocue serve` wipes both caches. The first scan after a restart
  re-queries Discogs for every key, paying the full token-bucket cost.
- There is no inter-process sharing. If a developer runs the CLI and the
  server back-to-back, each starts with a cold cache.

### 19.3 TTL — there isn't one

- Entries never expire. Once `discogs._cache["daft punk|||one more time"]` is
  populated, every subsequent call in the same process returns the same list
  regardless of how stale it is.
- This is acceptable in practice because Discogs Style metadata for a given
  release is extremely stable — curators rarely re-tag old releases. New
  pressings of the same artist/title would land under a different release ID
  but Discogs' top-5 search ranking is unlikely to demote the cached match.
- For discovery, the lack of TTL matters more — a release added to Discogs
  *after* the artist was first scanned will not appear until the server
  restarts. In practice the user starts a fresh scan once a day or week, so
  process restarts happen frequently enough to keep results fresh.

### 19.4 Disk persistence — there isn't one

- Nothing is written to disk. Both caches live entirely in RAM.
- There is no warm-start mechanism. A cold restart pays the full Discogs cost
  every time.
- Memory footprint is trivial — even a 5000-track library tops out at ~5MB
  of strings (see [§5.4](#54-cache-rationale)), well below any concern.

### 19.5 Test fixtures

`tests/conftest.py` clears the analysis caches between tests (see
[§5.3](#53-test-isolation)), and `tests/test_discovery.py` sidesteps the
Discogs layer entirely by patching `discovery.search_artist_releases`. The
Discogs client itself is not directly unit-tested, so `discogs._cache` and
`discogs._releases_cache` are exercised only via the discovery and route
integration tests.

### 19.6 Possible future enhancements

The current design is deliberately minimal — the project shipped without
sophisticated caching because it didn't need any. Concrete improvements when
the constraints change:

- **Disk-backed cache with TTL** — persist `discogs._cache` /
  `discogs._releases_cache` to a SQLite or JSON file under
  `~/.autocue/` so restarts don't re-burn the Discogs budget. A TTL of 30
  days for Styles and 7 days for releases would balance freshness against
  rate-limit cost.
- **Per-call cache bypass** — add an explicit `force_refresh=False` kwarg to
  `search_styles` / `search_artist_releases`, and surface a "Refresh from
  Discogs" button in the UI that bypasses the cache for a single track or a
  single discovery scan.
- **Cache size cap with LRU eviction** — not currently needed (memory budget
  is trivial), but a long-running server scanning many libraries would
  eventually want bounded memory.
- **Shared cache across `autocue serve` and the CLI** — sharing requires
  disk persistence first.

None of these are scheduled. They are documented here so the design
intent of "in-memory only, no eviction" is explicit rather than implicit.

---

## 20. Related documentation

- **`auto-tag.md`** — Broader auto-tag architecture. `search_styles` is one
  of several detector inputs; the My Tags / `DjmdMyTag` write path is shared.
- **`youtube-download.md`** — The download endpoint consumes
  `DiscoverItem.query` as its search term. The Discover tab's "download"
  button wires the two together.
- **`rest-api.md`** — Full REST API reference. `/api/discover`,
  `/api/auto-tag/discogs`, `/api/auto-tag/discogs/test`, and `/api/config`
  are all in scope here.
- **CLAUDE.md** — Inline reference notes on Discogs and Discovery
  invariants (rate limit, cache keys, token resolution, `_resolve_discogs_token`,
  `iter_new_releases` generator contract, `DiscoverItem.formats`).
- **`docs/FEATURES.md`** — End-user copy for both features.

---

## Appendix A: File reference index

| Path | Purpose |
|---|---|
| `autocue/analysis/discogs.py` | HTTP client, token bucket, caches |
| `autocue/analysis/discovery.py` | Library scan + generator |
| `autocue/serve/routes.py:1577` | `GET /api/config` |
| `autocue/serve/routes.py:1599` | `POST /api/auto-tag/discogs/test` |
| `autocue/serve/routes.py:1619` | `POST /api/auto-tag/discogs` (SSE) |
| `autocue/serve/routes.py:1837` | `GET /api/discover` (SSE) |
| `autocue/serve/routes.py:1885` | `_resolve_discogs_token()` |
| `autocue/serve/schemas.py:405` | `DiscogsTagRequest` |
| `autocue/serve/schemas.py:412` | `DiscogsTagEvent` |
| `autocue/serve/schemas.py:490` | `DiscoverItem` |
| `tests/test_discovery.py` | 17 unit tests |
| `docs/index.html` | `_renderSuggestion`, `_esc`, `_consumeSSE`, Discover tab, Auto-Tag panel |
