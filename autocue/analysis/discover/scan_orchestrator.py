"""Scan orchestrator — wires feeders, dedup, ranker, and store into one scan.

Lifecycle locked at PRD §6.2 / §6.3 / §8:

1. ``start_scan`` (in :class:`DiscoverStore`) writes a 'running' row.
   This row is the concurrent-scan lock — `/api/discover/feed` returns 409
   while it's open.
2. Build the taste vector (cached for the session).
3. Dispatch each enabled feeder; stream their yields through the dedup
   pipeline. Per-scan request budget caps prevent any single feeder from
   running away with the 60-request total.
4. Each yielded release gets normalized into a ``release_key`` (PRD §6.3),
   deduped against:
   - ``library_album_set()`` (already-owned filter)
   - ``saved`` / ``dismissed`` / ``snoozed`` (within ``until_date``) /
     ``downloaded`` (no point re-surfacing what the user already actioned)
   - ``blocked_artists`` / ``blocked_labels`` (hard-block by name)
5. Pass each survivor through the ranker (Stage 1).
6. Assemble the final feed via Stage 2 (25% novelty reservation).
7. On clean finish, ``commit_pending_scan`` promotes the feeders'
   ``last_scanned_at_pending`` writes atomically, THEN ``finish_scan``
   flips the row to status='ok'. The order matters — TTL gate must see
   fresh values immediately, not next request.
8. On ``Discogs429`` mid-scan, finish_scan with status='rate_limited'.
   Pending values stay; the next scan will commit them or boot recovery
   will roll them back.

The orchestrator's output is a streaming generator of SSE-friendly events
that the API layer wraps. Events:

- ``("progress", {"feeder": str, "scanned": int, "total": int|None})``
- ``("release", {"release_key": str, "score": float, "release": dict, "source": str})``
- ``("warning", {"feeder": str, "remaining": int})``  — rate-limit near-exhausted
- ``("error", {"feeder": str, "key": str|None, "exc": str})``  — non-fatal
- ``("sparse_adjacency", {"strategy": str, "reason": str, "unknown_styles": list})``
- ``("done", {"scan_id": int, "duration_ms": int, "stats": {...}})``

Bonus: the orchestrator is a pure-Python generator (no FastAPI / SSE coupling
here) so it's easy to unit-test without a running server.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Optional

from autocue.analysis import discogs as discogs_client
from autocue.analysis.discover import (
    DiscoverStore,
    FeedAssemblyResult,
    FeedContext,
    TasteVector,
    assemble_feed,
    normalize_release_key,
    score_release,
)
from autocue.analysis.discover.feeders import (
    DEFAULT_ARTIST_BUDGET,
    DEFAULT_LABEL_BUDGET,
    DEFAULT_NOVELTY_BUDGET,
    artist_feeder,
    label_feeder,
    next_novelty_strategy,
    novelty_feeder,
)
from autocue.analysis.discover.style_graph import StyleAdjacency

logger = logging.getLogger(__name__)


# Hard global cap. Sum of feeder budgets MUST not exceed this. The
# rate-limit signaling (Discogs429 / RateLimitNearExhausted) is the second
# line of defense if a future budget table goes out of sync.
HARD_SCAN_REQUEST_CAP = 60

DEFAULT_FEED_TOP_N = 50


@dataclass
class ScanConfig:
    """Per-scan tuning the API layer can pass through from query params."""

    feeders: list[str] = field(default_factory=lambda: ["artist", "label", "novelty"])
    top_n: int = DEFAULT_FEED_TOP_N
    year_from: Optional[int] = None
    artist_budget: int = DEFAULT_ARTIST_BUDGET
    label_budget: int = DEFAULT_LABEL_BUDGET
    novelty_budget: int = DEFAULT_NOVELTY_BUDGET

    def validate(self) -> None:
        total = self.artist_budget + self.label_budget + self.novelty_budget
        if total > HARD_SCAN_REQUEST_CAP:
            raise ValueError(
                f"feeder budgets sum to {total}, exceeds hard cap "
                f"{HARD_SCAN_REQUEST_CAP}"
            )


@dataclass
class ScanResult:
    scan_id: int
    feed: list[dict]
    novelty_status: str
    novelty_partial: Optional[int]
    releases_seen: int
    releases_after_dedup: int
    releases_surfaced: int
    requests_used: Optional[int]
    duration_ms: int
    status: str  # 'ok' | 'rate_limited' | 'cancelled'


def run_scan(
    store: DiscoverStore,
    taste_vector: TasteVector,
    adjacency: StyleAdjacency,
    token: str,
    *,
    config: Optional[ScanConfig] = None,
    library_album_set: Optional[set[str]] = None,
    today: Optional[Any] = None,
    followed_label_ids_for_novelty: Optional[list[int]] = None,
    followed_label_names_for_novelty: Optional[list[str]] = None,
    top_artist_ids_for_novelty: Optional[list[int]] = None,
    previous_novelty_strategy: Optional[str] = None,
) -> Iterator[tuple[str, dict]]:
    """Run one scan as a generator of SSE-friendly events.

    The caller (the SSE endpoint in T-015) wraps each yielded tuple as
    ``event: <type>\\ndata: <json>``.

    Args:
        store: live :class:`DiscoverStore`.
        taste_vector: pre-built per session; passed in so the orchestrator
            doesn't re-walk the Rekordbox DB on every scan.
        adjacency: loaded style graph (for novelty).
        token: Discogs personal access token.
        config: per-scan tuning.
        library_album_set: lowercased ``{artist|||title}`` set of releases
            the user already owns (from CLAUDE.md's library_album_set()).
            Releases matching this set are filtered out.
        today: override for the ranker's recency math (mostly for tests).
        followed_label_ids_for_novelty / followed_label_names_for_novelty:
            populated from ``store.list_followed_labels()`` by the caller
            (passed in so the orchestrator stays generator-friendly).
        top_artist_ids_for_novelty: Tier 2 feature; populated by the caller
            from a future name → Discogs-ID resolver. ``None`` triggers the
            artist-strategy sparse-adjacency sentinel cleanly.
        previous_novelty_strategy: last scan's strategy (from the prior
            scan row's ``novelty_strategy`` column) — used to advance the
            round-robin.
    """
    config = config or ScanConfig()
    config.validate()

    novelty_strategy = next_novelty_strategy(previous_novelty_strategy) if "novelty" in config.feeders else None

    scan_id = store.start_scan(
        feeders=config.feeders,
        novelty_strategy=novelty_strategy,
    )
    discogs_client.reset_rate_limit_state()
    started = time.monotonic()

    blocked_artists = store.blocked_artist_names()
    blocked_labels = store.blocked_label_names()
    library_album_set = library_album_set or set()
    feed_ctx = FeedContext(
        blocked_artists=blocked_artists,
        blocked_labels=blocked_labels,
        today=today,
    )

    # Scoring is incremental — we keep the per-source counters live so the
    # source_diversity and cohort_freshness terms react to what we've already
    # added to the feed. The final assemble_feed call re-sorts, but the
    # in-loop counters drive WHICH novelty/retrieval items make the cut.
    scored: list[tuple[float, dict]] = []
    seen_keys: set[str] = set()
    novelty_sparse_status: Optional[str] = None
    releases_seen = 0
    releases_after_dedup = 0
    final_status = "ok"

    try:
        for feeder_name in config.feeders:
            yield ("progress", {"feeder": feeder_name, "scanned": 0, "total": None})
            stream = _dispatch_feeder(
                feeder_name=feeder_name,
                token=token,
                taste_vector=taste_vector,
                adjacency=adjacency,
                strategy=novelty_strategy,
                config=config,
                followed_label_ids=followed_label_ids_for_novelty,
                followed_label_names=followed_label_names_for_novelty,
                top_artist_ids=top_artist_ids_for_novelty,
                store=store,
                scan_id=scan_id,
            )
            for event in stream:
                if isinstance(event, tuple):
                    # ('error', …), ('warning', …), ('sparse_adjacency', …)
                    kind = event[0]
                    if kind == "sparse_adjacency":
                        novelty_sparse_status = "sparse_adjacency"
                    yield event
                    continue

                release = event["release"]
                source = event["source"]
                releases_seen += 1

                # Dedup against owned + previously-actioned + blocked.
                artist = (release.get("artist") or "").strip()
                title = (release.get("title") or "").strip()
                release_id = release.get("id") or 0
                if not title and not artist:
                    continue
                key = normalize_release_key(artist, title, release_id)
                # library_album_set() (autocue.analysis.discovery) returns the
                # set of LOWERCASED + WHITESPACE-COLLAPSED album names — no
                # artist prefix, no `|||` separator. The old f"{artist}|||{title}"
                # key never matched ANY library entry, so every owned album
                # showed up in Discover. Match on the album token instead:
                # Discogs releases carry both `album` (already split from the
                # "Artist - Album" title) and `title` (the full string), so
                # prefer album when set and fall back to title with the leading
                # "Artist - " stripped.
                album = (release.get("album") or "").strip().lower()
                if not album:
                    # title looks like "Artist - Album"; strip the prefix once.
                    album = title.lower().split(" - ", 1)[-1].strip()
                lib_key = " ".join(album.split())
                if key in seen_keys:
                    continue
                if lib_key and lib_key in library_album_set:
                    seen_keys.add(key)
                    continue
                if store.is_saved(key) or store.is_downloaded(key):
                    seen_keys.add(key)
                    continue
                if store.is_dismissed(key) or store.is_snoozed(key):
                    seen_keys.add(key)
                    continue
                seen_keys.add(key)
                releases_after_dedup += 1

                payload = {**release, "source": source}
                s = score_release(payload, taste_vector, feed_ctx)
                if s <= 0:
                    continue  # hard-blocked
                scored.append((s, payload))

                # Update cohort counters so future scores react.
                feed_ctx.seen_artists[artist] += 1
                if release.get("label"):
                    feed_ctx.seen_labels[str(release["label"])] += 1
                family = source.split(":", 1)[0] if source else "unknown"
                feed_ctx.seen_sources[family] += 1

                yield ("release", {
                    "release_key": key,
                    "score": s,
                    "release": payload,
                    "source": source,
                })

    except discogs_client.Discogs429 as exc:
        final_status = "rate_limited"
        yield ("error", {"feeder": "scan", "key": None,
                         "exc": f"rate-limited (retry after {exc.retry_after}s)"})
    except Exception as exc:  # noqa: BLE001 — concurrent-scan lock leak is worse than swallowing
        # Any unexpected crash inside the feeder loop (e.g. a TypeError from
        # an unsanitised Discogs response, a SQL hiccup) used to skip the
        # finish_scan call below and leave the scan row's finished_at = NULL,
        # wedging the concurrent-scan lock so every subsequent /feed POST
        # returned 409 until the server restarted. We now log + surface as
        # a structured error event, then fall through so the row is closed.
        logger.exception("scan_orchestrator: feeder loop crashed")
        final_status = "error"
        yield ("error", {"feeder": "orchestrator", "key": None, "exc": str(exc)})

    # Stage 2 — assemble final feed with novelty reservation. Even on a
    # crash we still attempt this so the partial `scored` list isn't wasted.
    try:
        novelty_pool_hint = 0 if novelty_sparse_status else None
        result: FeedAssemblyResult = assemble_feed(
            scored, top_n=config.top_n, novelty_pool_size_hint=novelty_pool_hint,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("scan_orchestrator: assemble_feed crashed; using empty feed")
        if final_status == "ok":
            final_status = "error"
        from types import SimpleNamespace
        result = SimpleNamespace(feed=[], novelty_status="error", novelty_partial=None)

    duration_ms = int((time.monotonic() - started) * 1000)
    requests_used = discogs_client.get_last_remaining()

    # finish_scan MUST run on every code path or the scan row stays open and
    # the concurrent-scan lock leaks. Wrap commit in try/finally so even a
    # commit_pending_scan crash still releases the lock.
    try:
        if final_status == "ok":
            store.commit_pending_scan(scan_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("scan_orchestrator: commit_pending_scan crashed")
        final_status = "error"
    finally:
        store.finish_scan(
            scan_id,
            status=final_status,
            novelty_status=result.novelty_status,
            duration_ms=duration_ms,
            requests_used=requests_used,
            releases_seen=releases_seen,
            releases_after_dedup=releases_after_dedup,
            releases_surfaced=len(result.feed),
        )

    yield ("done", {
        "scan_id": scan_id,
        "status": final_status,
        "duration_ms": duration_ms,
        "novelty_status": result.novelty_status,
        "novelty_partial": result.novelty_partial,
        "releases_seen": releases_seen,
        "releases_after_dedup": releases_after_dedup,
        "releases_surfaced": len(result.feed),
        "feed": result.feed,
    })


def _dispatch_feeder(
    *,
    feeder_name: str,
    token: str,
    taste_vector: TasteVector,
    adjacency: StyleAdjacency,
    strategy: Optional[str],
    config: ScanConfig,
    followed_label_ids: Optional[list[int]],
    followed_label_names: Optional[list[str]],
    top_artist_ids: Optional[list[int]],
    store: DiscoverStore,
    scan_id: int,
) -> Iterable[Any]:
    """Route to the right feeder generator. Keeps run_scan readable."""
    if feeder_name == "artist":
        return artist_feeder(
            taste_vector, token=token,
            budget=config.artist_budget, year_from=config.year_from,
        )
    if feeder_name == "label":
        return label_feeder(
            taste_vector, store, token=token, scan_id=scan_id,
            budget=config.label_budget, year_from=config.year_from,
        )
    if feeder_name == "novelty":
        if strategy is None:
            return iter([("sparse_adjacency", {
                "strategy": "none",
                "reason": "novelty not enabled for this scan",
                "unknown_styles": [],
            })])
        return novelty_feeder(
            taste_vector, adjacency, token=token, strategy=strategy,
            budget=config.novelty_budget,
            followed_label_ids=followed_label_ids,
            followed_label_names=followed_label_names,
            top_artist_ids=top_artist_ids,
        )
    return iter([("error", {"feeder": feeder_name, "key": None,
                            "exc": f"unknown feeder: {feeder_name!r}"})])
