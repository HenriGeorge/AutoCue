"""Discover & Watchlist v2 — personalized new-release feed.

Public surface is populated as downstream tasks land. Currently exposes:
- STYLE_ALIAS_MAP, normalize_style, load_style_adjacency (T-002)
- TasteVector, build_taste_vector, normalize_release_key (T-003)
- score_release, assemble_feed, FeedContext, FeedAssemblyResult (T-009)
- DiscoverStore (T-011/T-012)
"""

from autocue.analysis.discover.ranker import (
    FeedAssemblyResult,
    FeedContext,
    NOVELTY_FRACTION,
    assemble_feed,
    score_release,
)
from autocue.analysis.discover.store import DiscoverStore
from autocue.analysis.discover.style_graph import (
    STYLE_ALIAS_MAP,
    load_style_adjacency,
    normalize_style,
)
from autocue.analysis.discover.taste import (
    TasteVector,
    build_taste_vector,
    normalize_release_key,
)

__all__ = [
    "DiscoverStore",
    "FeedAssemblyResult",
    "FeedContext",
    "NOVELTY_FRACTION",
    "STYLE_ALIAS_MAP",
    "TasteVector",
    "assemble_feed",
    "build_taste_vector",
    "load_style_adjacency",
    "normalize_release_key",
    "normalize_style",
    "score_release",
]
