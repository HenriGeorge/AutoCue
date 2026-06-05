"""New-release discovery — suggests recent albums from artists already in the library.

Strategy: take the most-represented artists in the Rekordbox library, ask Discogs
for each artist's newest releases, drop anything the user already owns, and surface
the rest as suggestions. Reuses ``discogs.py`` (client + token-bucket rate limiter).

The heavy lifting (one Discogs call per artist) is exposed as a generator
(``iter_new_releases``) so the server can stream progress over SSE.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime

from .discogs import search_artist_releases

_log = logging.getLogger(__name__)


def _norm(s: str) -> str:
    """Lowercase + collapse whitespace for loose title/album matching."""
    return " ".join(str(s or "").lower().split())


def library_artists(db, top_n: int = 25) -> list[str]:
    """Return the most frequent artist names in the library, most common first.

    Frequency is used as a proxy for "artists the DJ cares about" so a large
    library does not blow past the Discogs rate limit — only ``top_n`` artists
    are queried.
    """
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


def library_album_set(db) -> set[str]:
    """Return the set of normalized album titles already in the library.

    Used to filter out releases the DJ already owns. Albums are keyed loosely
    (lowercased, whitespace-collapsed) since Discogs titles rarely match exactly.
    """
    owned: set[str] = set()
    try:
        for c in db.get_content().all():
            album = _norm(getattr(c, "AlbumName", "") or "")
            if album:
                owned.add(album)
    except Exception as exc:
        _log.warning("library_album_set: could not read content: %s", exc)
    return owned


def iter_new_releases(
    db,
    token: str,
    since_year: int | None = None,
    max_artists: int = 25,
    per_artist: int = 5,
):
    """Yield ``(processed, total, suggestion | None)`` tuples for SSE streaming.

    ``processed``/``total`` count *artists* scanned (one Discogs call each), so a
    client can render a progress bar. ``suggestion`` is None for artists that
    produced no new releases. A suggestion dict has the shape returned by
    :func:`discogs.search_artist_releases` plus the originating ``query`` string.

    Releases already present in the library (by album title) and duplicate
    albums across artists are skipped.
    """
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


def suggest_new_releases(
    db,
    token: str,
    since_year: int | None = None,
    max_artists: int = 25,
    per_artist: int = 5,
) -> list[dict]:
    """Eager wrapper around :func:`iter_new_releases` — returns the suggestion list."""
    return [s for _, _, s in iter_new_releases(
        db, token, since_year=since_year,
        max_artists=max_artists, per_artist=per_artist,
    ) if s is not None]
