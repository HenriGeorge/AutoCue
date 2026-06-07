"""Novelty feeder — Discover v2 Feeder 4.

Surfaces releases that are *adjacent but not literal matches* to the taste
vector — the difference between "discovery" and "retrieval". To fit the
locked 10-request per-scan budget, only **one of three strategies runs per
scan**, rotated round-robin via ``scans.novelty_strategy``:

- **style-adjacent**: for each top-3 taste-vector style, fetch from the
  adjacency edges defined in ``style_adjacency.json``. Skipped when the
  user's top-3 styles are all terminal OR all absent from the graph
  (sparse-adjacency case) — feeder yields a ``("sparse_adjacency", …)``
  sentinel that the orchestrator records as the scan's ``novelty_status``.

- **label-adjacent**: for top-5 followed labels, resolve ``parent_label``
  and ``sub_labels`` via Discogs, then fetch recent releases from up to
  5 adjacent labels.

- **artist-adjacent**: for top-5 taste-vector artists, fetch ``members``
  and ``groups`` via Discogs, then recent releases from up to 5 unique
  adjacent artists.

Tags every release with ``source = "novelty:{strategy}"`` so the ranker's
Stage 2 reservation can identify the novelty pool.
"""

from __future__ import annotations

import logging
from typing import Iterator

from autocue.analysis import discogs as discogs_client
from autocue.analysis.discover.style_graph import StyleAdjacency
from autocue.analysis.discover.taste import TasteVector

logger = logging.getLogger(__name__)


DEFAULT_NOVELTY_BUDGET = 10
DEFAULT_TOP_STYLES = 3
DEFAULT_TOP_LABELS = 5
DEFAULT_TOP_ARTISTS = 5
DEFAULT_EDGES_PER_STYLE = 3
DEFAULT_RELEASES_PER_ADJACENT = 10

NOVELTY_STRATEGIES = ("style", "label", "artist")


def next_novelty_strategy(previous: str | None) -> str:
    """Round-robin next strategy after the one used on the prior scan.

    The orchestrator passes the prior scan's ``novelty_strategy`` and uses the
    returned value for the next scan. ``None`` (first scan) starts at 'style'
    — the most common case (every library has some styles) so the first scan
    feels alive even with sparse adjacency.
    """
    if previous not in NOVELTY_STRATEGIES:
        return NOVELTY_STRATEGIES[0]
    idx = NOVELTY_STRATEGIES.index(previous)
    return NOVELTY_STRATEGIES[(idx + 1) % len(NOVELTY_STRATEGIES)]


def novelty_feeder(
    taste_vector: TasteVector,
    adjacency: StyleAdjacency,
    token: str,
    strategy: str,
    *,
    budget: int = DEFAULT_NOVELTY_BUDGET,
    followed_label_ids: list[int] | None = None,
    followed_label_names: list[str] | None = None,
    top_artist_ids: list[int] | None = None,
) -> Iterator[dict | tuple]:
    """Yield novelty release dicts for the given strategy.

    Args:
        taste_vector: the user's taste profile.
        adjacency: the loaded style graph (from :func:`load_style_adjacency`).
        token: Discogs personal access token.
        strategy: which rotation slot to run this scan.
        budget: max Discogs requests this feeder may issue.
        followed_label_ids / followed_label_names: parallel lists used by the
            label-adjacent strategy. Required only for that strategy.
        top_artist_ids: Discogs artist IDs for the artist-adjacent strategy.
            None means "no resolved IDs yet" — feeder yields the sparse-data
            sentinel so the orchestrator can record novelty_status.

    Yields:
        Release dicts: ``{"source": "novelty:{strategy}:{sub}", "release": {...}}``
        plus sentinel tuples on edge cases:
        - ``("sparse_adjacency", {"strategy": str, "reason": str, "unknown_styles": list})``
          when the strategy can't produce candidates from this taste vector.
        - ``("error", {…})`` for per-entity exceptions.
        - ``("warning", {…})`` on near-exhausted bucket — feeder stops.
        - ``Discogs429`` re-raised — orchestrator handles.
    """
    if budget <= 0 or not token:
        return
    if strategy not in NOVELTY_STRATEGIES:
        raise ValueError(f"unknown novelty strategy: {strategy!r}")

    if strategy == "style":
        yield from _style_adjacent(taste_vector, adjacency, token, budget)
    elif strategy == "label":
        yield from _label_adjacent(token, budget, followed_label_ids, followed_label_names)
    else:  # artist
        yield from _artist_adjacent(token, budget, top_artist_ids)


# --------------------------------------------------------------------------- #
# Strategy: style-adjacent
# --------------------------------------------------------------------------- #

def _style_adjacent(
    taste_vector: TasteVector,
    adjacency: StyleAdjacency,
    token: str,
    budget: int,
) -> Iterator[dict | tuple]:
    top_styles = taste_vector.top_styles(DEFAULT_TOP_STYLES)
    if not top_styles:
        yield ("sparse_adjacency", {
            "strategy": "style",
            "reason": "no top styles in taste vector",
            "unknown_styles": [],
        })
        return

    unknown = [s for s in top_styles if not adjacency.styles.get(s)]
    if len(unknown) == len(top_styles):
        # Every top style is absent from the graph → sparse adjacency.
        yield ("sparse_adjacency", {
            "strategy": "style",
            "reason": "all top-3 styles missing from adjacency graph",
            "unknown_styles": unknown,
        })
        return

    # Collect adjacent styles, deduped across top-N origins.
    adjacent_styles: list[str] = []
    seen: set[str] = set()
    for style in top_styles:
        if adjacency.is_terminal(style):
            continue  # terminal styles contribute no novelty edges
        for edge in adjacency.adjacent(style)[:DEFAULT_EDGES_PER_STYLE]:
            if edge not in seen and edge not in top_styles:
                seen.add(edge)
                adjacent_styles.append(edge)

    if not adjacent_styles:
        yield ("sparse_adjacency", {
            "strategy": "style",
            "reason": "all top-3 styles are terminal in adjacency graph",
            "unknown_styles": unknown,
        })
        return

    used = 0
    for adj_style in adjacent_styles:
        if used >= budget:
            break
        try:
            # We reuse the legacy search via search_artist_releases-style call
            # would be wrong here — we want to search the database for releases
            # tagged with this style. The simplest tier-1 approach: Discogs
            # full-text search with q={style} type=release.
            results = _discogs_search_style(token, adj_style)
        except discogs_client.Discogs429:
            raise
        except discogs_client.RateLimitNearExhausted as exc:
            for release in (exc.data or []):
                yield {
                    "source": f"novelty:style:{adj_style}",
                    "adjacent_style": adj_style,
                    "release": release,
                }
            yield ("warning", {"feeder": "novelty", "remaining": exc.remaining})
            return
        except Exception as exc:
            logger.warning("novelty(style): error on %r: %s", adj_style, exc)
            yield ("error", {"feeder": "novelty", "strategy": "style",
                             "key": adj_style, "exc": str(exc)})
            used += 1
            continue
        used += 1
        for release in results[:DEFAULT_RELEASES_PER_ADJACENT]:
            yield {
                "source": f"novelty:style:{adj_style}",
                "adjacent_style": adj_style,
                "release": release,
            }


def _discogs_search_style(token: str, style: str) -> list[dict]:
    """Thin wrapper over the legacy ``search_artist_releases``-style call we
    reuse for style queries. Separate to make mocking cleaner in tests."""
    # Discogs search by style: q=<style> type=release. We use the v1 client's
    # underlying search but with a style query rather than an artist name.
    # search_artist_releases already calls /database/search?type=release; with
    # an empty artist + a styled query it'd be wrong. So we go direct.
    import urllib.parse
    import urllib.request
    import json
    # Use the v2 wrapper indirectly so we still get rate-limit signaling.
    resp = discogs_client._request_json(
        "/database/search",
        token=token,
        params={
            "type": "release",
            "style": style,
            "per_page": 25,
            "sort": "year",
            "sort_order": "desc",
        },
    )
    out = []
    for r in resp.data.get("results", []) or []:
        out.append({
            "id": r.get("id"),
            "title": str(r.get("title") or ""),
            "year": r.get("year"),
            "thumb": r.get("thumb") or "",
            "format": ",".join(r.get("format", []) or []),
            "resource_url": r.get("resource_url") or "",
            "styles": list(r.get("style", []) or []),
        })
    # Mirror the near-exhausted check from the typed client functions.
    if resp.remaining is not None and resp.remaining < discogs_client.NEAR_EXHAUSTED_THRESHOLD:
        raise discogs_client.RateLimitNearExhausted(remaining=resp.remaining, data=out)
    return out


# --------------------------------------------------------------------------- #
# Strategy: label-adjacent
# --------------------------------------------------------------------------- #

def _label_adjacent(
    token: str,
    budget: int,
    followed_label_ids: list[int] | None,
    followed_label_names: list[str] | None,
) -> Iterator[dict | tuple]:
    if not followed_label_ids:
        yield ("sparse_adjacency", {
            "strategy": "label",
            "reason": "no followed labels with Discogs IDs",
            "unknown_styles": [],
        })
        return

    followed_label_names = followed_label_names or []
    ids = followed_label_ids[:DEFAULT_TOP_LABELS]
    names = followed_label_names[:DEFAULT_TOP_LABELS]
    used = 0

    # Step 1: resolve parent + sub-labels for each followed label.
    adjacent_label_ids: list[tuple[int, str]] = []  # (id, friendly_name)
    seen_adjacents: set[int] = set()
    for i, label_id in enumerate(ids):
        if used >= budget:
            break
        try:
            from autocue.analysis.discogs import _request_json
            resp = _request_json(f"/labels/{int(label_id)}", token=token)
        except discogs_client.Discogs429:
            raise
        except Exception as exc:
            logger.warning("novelty(label): label-detail error on %r: %s", label_id, exc)
            yield ("error", {"feeder": "novelty", "strategy": "label",
                             "key": str(label_id), "exc": str(exc)})
            used += 1
            continue
        used += 1
        parent = resp.data.get("parent_label") or {}
        if parent.get("id") and parent["id"] not in seen_adjacents:
            seen_adjacents.add(parent["id"])
            adjacent_label_ids.append((parent["id"], str(parent.get("name") or "")))
        for sub in resp.data.get("sublabels") or []:
            sid = sub.get("id")
            if sid and sid not in seen_adjacents and sid not in ids:
                seen_adjacents.add(sid)
                adjacent_label_ids.append((sid, str(sub.get("name") or "")))

    if not adjacent_label_ids:
        yield ("sparse_adjacency", {
            "strategy": "label",
            "reason": "followed labels have no parent or sub-labels on Discogs",
            "unknown_styles": [],
        })
        return

    # Step 2: fetch recent releases from each adjacent label until budget runs out.
    for label_id, label_name in adjacent_label_ids[:DEFAULT_TOP_LABELS]:
        if used >= budget:
            break
        try:
            releases = discogs_client.search_label_releases(label_id, token=token, per_page=25)
        except discogs_client.Discogs429:
            raise
        except discogs_client.RateLimitNearExhausted as exc:
            for release in (exc.data or []):
                yield {
                    "source": f"novelty:label:{label_id}",
                    "adjacent_label_id": label_id,
                    "adjacent_label_name": label_name,
                    "release": release,
                }
            yield ("warning", {"feeder": "novelty", "remaining": exc.remaining})
            return
        except Exception as exc:
            logger.warning("novelty(label): release-list error on %r: %s", label_id, exc)
            yield ("error", {"feeder": "novelty", "strategy": "label",
                             "key": str(label_id), "exc": str(exc)})
            used += 1
            continue
        used += 1
        for release in releases[:DEFAULT_RELEASES_PER_ADJACENT]:
            yield {
                "source": f"novelty:label:{label_id}",
                "adjacent_label_id": label_id,
                "adjacent_label_name": label_name,
                "release": release,
            }


# --------------------------------------------------------------------------- #
# Strategy: artist-adjacent
# --------------------------------------------------------------------------- #

def _artist_adjacent(
    token: str,
    budget: int,
    top_artist_ids: list[int] | None,
) -> Iterator[dict | tuple]:
    if not top_artist_ids:
        yield ("sparse_adjacency", {
            "strategy": "artist",
            "reason": "no resolved Discogs artist IDs for the top-N taste-vector artists",
            "unknown_styles": [],
        })
        return

    ids = top_artist_ids[:DEFAULT_TOP_ARTISTS]
    adjacent_artists: list[tuple[int, str]] = []
    seen_adj: set[int] = set()
    used = 0

    for artist_id in ids:
        if used >= budget:
            break
        try:
            relations = discogs_client.get_artist_relations(artist_id, token=token)
        except discogs_client.Discogs429:
            raise
        except discogs_client.RateLimitNearExhausted as exc:
            relations = exc.data
            yield ("warning", {"feeder": "novelty", "remaining": exc.remaining})
            for kind in ("members", "groups"):
                for adj in relations.get(kind, []):
                    if adj["id"] not in seen_adj and adj["id"] not in ids:
                        seen_adj.add(adj["id"])
                        adjacent_artists.append((adj["id"], adj["name"]))
            return  # near-exhausted: stop after one warning
        except Exception as exc:
            logger.warning("novelty(artist): relations error on %r: %s", artist_id, exc)
            yield ("error", {"feeder": "novelty", "strategy": "artist",
                             "key": str(artist_id), "exc": str(exc)})
            used += 1
            continue
        used += 1
        for kind in ("members", "groups"):
            for adj in relations.get(kind, []):
                if adj["id"] not in seen_adj and adj["id"] not in ids:
                    seen_adj.add(adj["id"])
                    adjacent_artists.append((adj["id"], adj["name"]))

    if not adjacent_artists:
        yield ("sparse_adjacency", {
            "strategy": "artist",
            "reason": "top-N artists have no member/group relations on Discogs",
            "unknown_styles": [],
        })
        return

    # Each adjacent artist costs 1 request via search_artist_releases.
    for artist_id, artist_name in adjacent_artists[:DEFAULT_TOP_ARTISTS]:
        if used >= budget:
            break
        try:
            releases = discogs_client.search_artist_releases(
                artist_name, token=token, per_page=25,
            )
        except discogs_client.Discogs429:
            raise
        except Exception as exc:
            logger.warning("novelty(artist): release-list error on %r: %s", artist_name, exc)
            yield ("error", {"feeder": "novelty", "strategy": "artist",
                             "key": artist_name, "exc": str(exc)})
            used += 1
            continue
        used += 1
        for release in releases[:DEFAULT_RELEASES_PER_ADJACENT]:
            yield {
                "source": f"novelty:artist:{artist_id}",
                "adjacent_artist_id": artist_id,
                "adjacent_artist_name": artist_name,
                "release": release,
            }
