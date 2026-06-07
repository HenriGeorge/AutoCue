"""Discover feeders — surface candidate releases from various sources.

Currently exposes:
- artist_feeder (T-007)
- label_feeder, select_label_slots (T-007)
- novelty_feeder, next_novelty_strategy, NOVELTY_STRATEGIES (T-008)

Reserved for later tasks:
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
from autocue.analysis.discover.feeders.novelty import (
    DEFAULT_NOVELTY_BUDGET,
    NOVELTY_STRATEGIES,
    next_novelty_strategy,
    novelty_feeder,
)

__all__ = [
    "DEFAULT_ARTIST_BUDGET",
    "DEFAULT_LABEL_BUDGET",
    "DEFAULT_NOVELTY_BUDGET",
    "DEFAULT_TTL_HOURS",
    "NOVELTY_STRATEGIES",
    "artist_feeder",
    "label_feeder",
    "next_novelty_strategy",
    "novelty_feeder",
    "select_label_slots",
]
