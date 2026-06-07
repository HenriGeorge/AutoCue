"""Artist-watch feeder — Discover v2 Feeder 1.

Walks the top-N artists in the user's taste vector and pulls each artist's
recent releases from Discogs. One request per artist; page-1-only at scan time
per the locked PRD §6.2 depth tradeoff. The optional ``page=`` argument on the
underlying Discogs client is reserved for Tier 2 background pagination.

Streaming-mode generator so the orchestrator can dedup + rank incrementally
without holding the whole result set in memory while requests are in flight.

Artist TTL persistence is NOT in Tier 1 — taste-vector top-20 changes slowly
enough that re-fetching each scan is acceptable (20 requests of a 60-request
budget). A ``scanned_artists`` table is mentioned in iteration-log.md for a
future Tier 2 cache; for now the orchestrator's per-scan dedup is the only
guard against issuing the same artist twice.
"""

from __future__ import annotations

import logging
from typing import Iterator

from autocue.analysis import discogs as discogs_client
from autocue.analysis.discover.taste import TasteVector

logger = logging.getLogger(__name__)


# Feeder budget cap — kept in lockstep with the PRD §4 budget table. Exceeding
# this would burn into other feeders' share of the 60 req/min scan budget.
DEFAULT_ARTIST_BUDGET = 20


def artist_feeder(
    taste_vector: TasteVector,
    token: str,
    *,
    budget: int = DEFAULT_ARTIST_BUDGET,
    year_from: int | None = None,
) -> Iterator[dict]:
    """Yield release dicts surfaced from the top-N taste-vector artists.

    Args:
        taste_vector: built by :func:`build_taste_vector`. The feeder pulls
            ``top_artists(budget)`` from it — fewer if the user's library is
            thin.
        token: Discogs personal access token.
        budget: maximum number of Discogs requests this feeder may issue in
            one scan. Defaults to :data:`DEFAULT_ARTIST_BUDGET`. The
            orchestrator overrides this when the per-scan budget is tighter.
        year_from: drop releases older than this year. ``None`` keeps all.

    Yields:
        ``{"source": "artist", "artist_name": str, "release": {...}}`` per
        surfaced Discogs release. The ``release`` payload matches the shape
        returned by :func:`discogs.search_artist_releases` (the legacy v1
        function we reuse — it already accepts an artist NAME rather than
        requiring us to resolve the artist's Discogs numeric ID first).

        On rate-limit issues:

        - :class:`discogs.RateLimitNearExhausted` is caught internally; the
          feeder yields a ``("warning", {...})`` tuple so the orchestrator
          knows to back off before dispatching the next feeder, then stops.
        - :class:`discogs.Discogs429` is re-raised — that's a scan-abort
          condition the orchestrator handles directly.
        - All other exceptions yield a ``("error", {...})`` sentinel so one
          flaky artist doesn't abort the whole scan.
    """
    if budget <= 0 or not token:
        return

    used = 0
    for artist_name in taste_vector.top_artists(budget):
        if used >= budget:
            break

        try:
            releases = discogs_client.search_artist_releases(
                artist_name,
                token=token,
                year_from=year_from,
                per_page=50,
            )
        except discogs_client.Discogs429:
            # Hard stop — the orchestrator marks the scan rate_limited.
            raise
        except discogs_client.RateLimitNearExhausted as exc:
            # The response was still good — surface what we got, then warn
            # so the orchestrator can back off before the next feeder.
            for release in (exc.data or []):
                yield {"source": "artist", "artist_name": artist_name, "release": release}
            yield ("warning", {"feeder": "artist", "remaining": exc.remaining})
            return
        except Exception as exc:  # network blip, JSON glitch, …
            logger.warning("artist_feeder: error on %r: %s", artist_name, exc)
            yield ("error", {"feeder": "artist", "artist_name": artist_name, "exc": str(exc)})
            used += 1   # we still spent a request token-bucket-wise
            continue

        used += 1
        for release in releases:
            yield {"source": "artist", "artist_name": artist_name, "release": release}
