"""Discover feeders — surface candidate releases from various sources.

Currently exposes:
- artist_feeder (T-007)
- label_feeder, select_label_slots (T-007)

Reserved for later tasks:
- novelty_feeder (T-008)
- shop_feeder (T-047, Tier 1.5)
"""

from autocue.analysis.discover.feeders.artist import (
    DEFAULT_ARTIST_BUDGET,
    artist_feeder,
)
from autocue.analysis.discover.feeders.label import (
    DEFAULT_LABEL_BUDGET,
    DEFAULT_TTL_HOURS,
    label_feeder,
    select_label_slots,
)

__all__ = [
    "DEFAULT_ARTIST_BUDGET",
    "DEFAULT_LABEL_BUDGET",
    "DEFAULT_TTL_HOURS",
    "artist_feeder",
    "label_feeder",
    "select_label_slots",
]
