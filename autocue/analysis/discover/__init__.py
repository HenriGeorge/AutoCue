"""Discover & Watchlist v2 — personalized new-release feed.

Public surface is populated as downstream tasks land. Currently exposes:
- STYLE_ALIAS_MAP, normalize_style, load_style_adjacency (T-002)

Reserved for later tasks:
- TasteVector, build_taste_vector (T-003)
- score_release, assemble_feed (T-009)
- DiscoverStore (T-011/T-012)
"""

from autocue.analysis.discover.style_graph import (
    STYLE_ALIAS_MAP,
    load_style_adjacency,
    normalize_style,
)

__all__ = [
    "STYLE_ALIAS_MAP",
    "load_style_adjacency",
    "normalize_style",
]
