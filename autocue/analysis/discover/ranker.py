"""Ranker — Discover v2 §6.3.

Two stages:

**Stage 1** — :func:`score_release` returns a base score in ``[0, 100]`` using
the 7-term formula locked at PRD v1.0::

    base(release) =
       0.28 * artist_match
     + 0.22 * label_match
     + 0.18 * style_match
     + 0.08 * bpm_fit              (0.5 when neither side has data)
     + 0.10 * recency              (linear decay over 90 days)
     + 0.05 * source_diversity
     + 0.09 * cohort_freshness
     - hard_block if artist ∈ blocked_artists OR label ∈ blocked_labels

**Stage 2** — :func:`assemble_feed` enforces a 25% novelty reservation when
the novelty pool has enough candidates. When it's thin, partial reservation
is recorded. When empty (sparse adjacency), the feed degrades to pure
retrieval — never backfilled from random data, per PRD §6.3.

Both functions take fully-realized in-memory lists; the orchestrator does the
dedup + streaming upstream. Keeps the ranker pure (and Hypothesis-friendly).
"""

from __future__ import annotations

import math
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional

from autocue.analysis.discover.taste import TasteVector

# --------------------------------------------------------------------------- #
# Term weights — locked at PRD v1.0 §6.3.
# Sum is 1.00; tests assert this so a future tweak to one weight doesn't
# silently push the score out of [0, 100].
# --------------------------------------------------------------------------- #

WEIGHT_ARTIST = 0.28
WEIGHT_LABEL = 0.22
WEIGHT_STYLE = 0.18
WEIGHT_BPM = 0.08
WEIGHT_RECENCY = 0.10
WEIGHT_SOURCE_DIVERSITY = 0.05
WEIGHT_COHORT_FRESHNESS = 0.09

NOVELTY_FRACTION = 0.25
RECENCY_WINDOW_DAYS = 90

# A release without any "match" terms still floats above the floor because
# recency / bpm_fit can contribute. Hard-blocked releases score 0 exactly.
HARD_BLOCK_SCORE = 0.0


# --------------------------------------------------------------------------- #
# score_release
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FeedContext:
    """Per-scan context the ranker needs in addition to the taste vector.

    Pulled out so :func:`score_release` is pure and trivially testable —
    no DB lookups inside.
    """

    blocked_artists: set[str] = field(default_factory=set)
    blocked_labels: set[str] = field(default_factory=set)
    today: Optional[date] = None

    # Cohort counters for the diversity terms. Built incrementally by the
    # orchestrator as it streams ranked items; passed back in for the next
    # batch. Tier-1 simplification: the orchestrator can leave these empty
    # and the diversity terms degrade to 0.5 neutrals.
    seen_artists: Counter[str] = field(default_factory=Counter)
    seen_labels: Counter[str] = field(default_factory=Counter)
    seen_sources: Counter[str] = field(default_factory=Counter)


def score_release(
    release: dict,
    taste_vector: TasteVector,
    context: Optional[FeedContext] = None,
) -> float:
    """Compute the base score in ``[0, 100]`` for one release dict.

    Args:
        release: a normalized release dict — see :func:`extract_for_ranker`
            for the expected keys. Missing keys degrade to neutral terms.
        taste_vector: the user's taste profile.
        context: cohort + block-list context. Defaults to an empty context.

    Returns:
        Float in ``[0.0, 100.0]``.

        Hard-blocked releases (artist ∈ blocked_artists OR label ∈
        blocked_labels) return exactly :data:`HARD_BLOCK_SCORE` (0.0) so
        the orchestrator can filter them with one comparison.
    """
    context = context or FeedContext()
    artist = (release.get("artist") or "").strip()
    label = (release.get("label") or "").strip()

    # Hard block — shortest path, no other math.
    if artist and artist in context.blocked_artists:
        return HARD_BLOCK_SCORE
    if label and label in context.blocked_labels:
        return HARD_BLOCK_SCORE

    # Each term lives in [0, 1].
    a = _artist_match(artist, release.get("artists"), taste_vector)
    b = _label_match(label, taste_vector)
    s = _style_match(release.get("styles") or [], taste_vector)
    bpm = _bpm_fit(release.get("bpm"), taste_vector)
    r = _recency(release.get("year"), context.today)
    sd = _source_diversity(release.get("source") or "", context)
    cf = _cohort_freshness(artist, label, context)

    composite = (
        WEIGHT_ARTIST * a
        + WEIGHT_LABEL * b
        + WEIGHT_STYLE * s
        + WEIGHT_BPM * bpm
        + WEIGHT_RECENCY * r
        + WEIGHT_SOURCE_DIVERSITY * sd
        + WEIGHT_COHORT_FRESHNESS * cf
    )
    # Clamp defensively — float math rounding shouldn't leave us at 100.0000001.
    return max(0.0, min(100.0, composite * 100.0))


# Term implementations ─────────────────────────────────────────────────────

def _nfkd_lower(s: str) -> str:
    folded = unicodedata.normalize("NFKD", s.lower())
    return "".join(ch for ch in folded if not unicodedata.combining(ch)).strip()


def _artist_match(primary_artist: str, all_artists: Iterable[str] | None,
                  tv: TasteVector) -> float:
    """Set-overlap with taste-vector artists weighted by their counter values.

    Real cosine-similarity is overkill here — most releases name one or two
    artists, and the taste vector's top-N already encodes the user's
    preference shape via play-weighted scores. We compute the
    sum-of-weights of matched-artists divided by the sum-of-weights of the
    user's top-30 artists — a normalized intensity in ``[0, 1]``.
    """
    if not tv.artists:
        return 0.0
    names = []
    if primary_artist:
        names.append(primary_artist)
    if all_artists:
        names.extend(all_artists)
    matched_weight = 0.0
    for n in names:
        n_clean = (n or "").strip()
        if n_clean and n_clean in tv.artists:
            matched_weight += tv.artists[n_clean]
    if matched_weight == 0:
        # Try NFKD-fold fallback to catch accented spelling variations.
        folded_taste = {_nfkd_lower(k): v for k, v in tv.artists.items()}
        for n in names:
            folded = _nfkd_lower(n or "")
            if folded and folded in folded_taste:
                matched_weight += folded_taste[folded]
    total = sum(w for _, w in tv.artists.most_common(30))
    if total == 0:
        return 0.0
    return min(1.0, matched_weight / total)


def _label_match(label: str, tv: TasteVector) -> float:
    if not label or not tv.labels:
        return 0.0
    if label in tv.labels:
        ratio = tv.labels[label] / max(tv.labels.values())
        return min(1.0, ratio)
    # NFKD-fold fallback.
    folded_taste = {_nfkd_lower(k): v for k, v in tv.labels.items()}
    folded = _nfkd_lower(label)
    if folded in folded_taste:
        ratio = folded_taste[folded] / max(tv.labels.values())
        return min(1.0, ratio)
    return 0.0


def _style_match(release_styles: Iterable[str], tv: TasteVector) -> float:
    if not tv.styles:
        return 0.0
    # Apply alias normalization to release.styles so 'Deep House' from Discogs
    # canonicalizes to the same key as the taste vector's 'deep_house'.
    from autocue.analysis.discover.style_graph import normalize_style
    rs = {normalize_style(s) for s in release_styles if s}
    rs.discard(None)
    if not rs:
        return 0.0
    overlap = rs & set(tv.styles.keys())
    if not overlap:
        return 0.0
    # Jaccard overlap weighted by taste-vector counts on the matched styles.
    matched_weight = sum(tv.styles[s] for s in overlap)
    total_taste = sum(tv.styles.values())
    if total_taste == 0:
        return 0.0
    return min(1.0, matched_weight / total_taste)


def _bpm_fit(release_bpm: Optional[float], tv: TasteVector) -> float:
    """1.0 when release BPM is in a heavily-weighted bucket of the taste
    histogram; 0.0 when it's a bucket the user never plays; 0.5 when one or
    both sides have no BPM info (no-data neutral, per PRD §6.3)."""
    if release_bpm is None or release_bpm <= 0:
        return 0.5
    if not any(tv.bpm_hist):
        return 0.5
    from autocue.analysis.discover.taste import _bpm_bucket
    try:
        bpm_f = float(release_bpm)
    except (TypeError, ValueError):
        return 0.5
    bucket = _bpm_bucket(bpm_f)
    bucket_count = tv.bpm_hist[bucket]
    max_count = max(tv.bpm_hist)
    if max_count == 0:
        return 0.5
    return bucket_count / max_count


def _recency(year: Optional[int], today: Optional[date]) -> float:
    """Linear decay over :data:`RECENCY_WINDOW_DAYS`. Returns 0 for unknown
    year (compilation reissues often lack year — that's fine, they just don't
    get the recency boost)."""
    if year is None or year <= 0:
        return 0.0
    today = today or date.today()
    # Approximate "today" against year by treating year as Jan 1 of that year —
    # more precise than nothing, less precise than a real release-date field
    # which Discogs doesn't reliably expose on the listing endpoints.
    age_days = (today - date(int(year), 1, 1)).days
    if age_days < 0:
        return 1.0  # released in the future (Discogs occasionally has these)
    if age_days >= RECENCY_WINDOW_DAYS:
        return 0.0
    return 1.0 - (age_days / RECENCY_WINDOW_DAYS)


def _source_diversity(source: str, context: FeedContext) -> float:
    """Bonus for releases coming from a feeder type that's under-represented
    in the cohort built so far. Empty cohort returns neutral 0.5."""
    if not context.seen_sources:
        return 0.5
    family = source.split(":", 1)[0] if source else "unknown"
    total = sum(context.seen_sources.values())
    if total == 0:
        return 0.5
    share = context.seen_sources.get(family, 0) / total
    # Inverted share — lower share gives a higher bonus.
    return 1.0 - share


def _cohort_freshness(artist: str, label: str, context: FeedContext) -> float:
    """Bonus for releases whose artist/label aren't already heavily repeated
    in the current feed cohort. Empty cohort returns neutral 0.5.
    """
    if not context.seen_artists and not context.seen_labels:
        return 0.5
    a_seen = context.seen_artists.get(artist, 0)
    l_seen = context.seen_labels.get(label, 0)
    # Both heavily seen → 0. Both unseen → 1. One seen each → 0.5.
    a_term = 1.0 if a_seen == 0 else 1.0 / (1.0 + a_seen)
    l_term = 1.0 if l_seen == 0 else 1.0 / (1.0 + l_seen)
    return (a_term + l_term) / 2.0


# --------------------------------------------------------------------------- #
# assemble_feed — Stage 2
# --------------------------------------------------------------------------- #

@dataclass
class FeedAssemblyResult:
    """The output of :func:`assemble_feed`. Carries enough metadata for the
    orchestrator to populate the ``scans`` telemetry row."""

    feed: list[dict]
    novelty_status: str  # 'ok' | 'partial' | 'sparse_adjacency'
    novelty_partial: Optional[int] = None  # filled only when novelty_status='partial'


def assemble_feed(
    scored: list[tuple[float, dict]],
    *,
    novelty_fraction: float = NOVELTY_FRACTION,
    top_n: int = 50,
    novelty_pool_size_hint: Optional[int] = None,
) -> FeedAssemblyResult:
    """Apply Stage 2 — the 25% novelty reservation — to a list of
    ``(score, release)`` tuples.

    Args:
        scored: every release the orchestrator dedup'd, with its base score.
            Includes both retrieval AND novelty entries — we separate them
            here by ``release.source.startswith('novelty:')``.
        novelty_fraction: fraction of top_n reserved for novelty when the pool
            has enough candidates. Default 0.25.
        top_n: feed size.
        novelty_pool_size_hint: when caller already knows the pool is empty
            (e.g. the feeder returned ``("sparse_adjacency", …)``), it can pass
            ``0`` here to force the sparse-adjacency status without us having
            to derive it from the data.

    Returns:
        :class:`FeedAssemblyResult` with the assembled feed + telemetry.
    """
    # Sort once, descending by score, stable so deterministic for tests.
    scored = sorted(scored, key=lambda t: (-t[0], _release_sort_key(t[1])))

    novelty_pool: list[tuple[float, dict]] = []
    retrieval_pool: list[tuple[float, dict]] = []
    for score, release in scored:
        if (release.get("source") or "").startswith("novelty:"):
            novelty_pool.append((score, release))
        else:
            retrieval_pool.append((score, release))

    # If the caller knows the pool is empty, prefer that signal over data inference.
    if novelty_pool_size_hint == 0:
        novelty_pool = []

    quota = max(0, int(top_n * novelty_fraction))

    if len(novelty_pool) >= quota and quota > 0:
        feed = [r for _, r in retrieval_pool[: top_n - quota]] + \
               [r for _, r in novelty_pool[:quota]]
        return FeedAssemblyResult(feed=feed, novelty_status="ok")

    if novelty_pool:
        # Partial pool — surface what we have, fill the rest from retrieval.
        partial_count = len(novelty_pool)
        feed = [r for _, r in retrieval_pool[: top_n - partial_count]] + \
               [r for _, r in novelty_pool]
        return FeedAssemblyResult(feed=feed, novelty_status="partial",
                                  novelty_partial=partial_count)

    # Empty pool — pure retrieval. No garbage backfill.
    feed = [r for _, r in retrieval_pool[:top_n]]
    return FeedAssemblyResult(feed=feed, novelty_status="sparse_adjacency")


def _release_sort_key(release: dict) -> tuple:
    """Deterministic secondary sort: titles ascending so equal-scored releases
    sort the same across runs (helps tests + reduces UI shuffle on re-sort)."""
    return (
        (release.get("artist") or "").lower(),
        (release.get("title") or "").lower(),
        int(release.get("id") or 0),
    )
