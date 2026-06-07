"""Build a per-user taste vector from the local Rekordbox library.

The taste vector is the substrate every Discover feeder + ranker scores against.
It captures *what this user actually plays from local audio*, normalized into
canonical shapes the rest of the package can compare against Discogs metadata.

Locked design contract (PRD v1.0 §6.1 + §6.3):

- **Artists** weighted by ``log(1 + play_count)`` so a single mega-played track
  doesn't dominate the signal. Falls back to track count when the play-history
  table is empty (cold-start library).
- **Labels** weighted by ``log(1 + total_label_plays) × √track_count`` —
  plays-weighted with a depth tiebreaker. Falls back to track count.
- **Styles** normalized through :func:`normalize_style` (lowercase + strip
  non-alphanumerics + alias map). Inputs: ``DjmdContent.GenreName``,
  AutoCue-namespaced My Tags only (filtered against ``ALL_AUTOCUE_TAG_NAMES``),
  and `[Genre: …]`-style markers in the track comment.
- **BPM histogram** in buckets of 4 BPM across 60–200 (= 35 buckets). Tracks
  with BPM=0 are excluded from the histogram.
- **Key histogram** of Camelot-format keys (e.g. "8A"); tracks with no key are
  skipped.
- **Source filter** keeps only ``source == 'file'`` tracks — streaming-source
  tracks (``spotify:``, ``tidal:``, ``applemusic:``, ``http(s)://``, empty path)
  bias the model toward stuff the user never actually plays from local audio.
  Toggleable via ``include_streaming=True`` for debugging.

Also exports :func:`normalize_release_key` — used by both the scan orchestrator
(dedup time) and the store CRUD layer (curation lookup). Designed in PRD §6.3
as the single source of truth for "this is the same release across format
variants" — including the empty-artist (compilation) handling with the
``release_id`` discriminator. **Never compute release_key from coerced
artist/title values** — always feed it the raw Discogs response strings.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from autocue.analysis.discover.style_graph import normalize_style

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# BPM histogram covers 60–200 in buckets of 4 (= 35 buckets). Anything outside
# this range maps to the nearest boundary bucket — Rekordbox occasionally
# reports BPMs in the 40s or 240s for half-time / double-time genres.
BPM_BUCKET_SIZE = 4
BPM_MIN = 60
BPM_MAX = 200
BPM_BUCKET_COUNT = (BPM_MAX - BPM_MIN) // BPM_BUCKET_SIZE  # 35

# Streaming-source URI prefixes (matches serve.routes._classify_source contract).
_STREAMING_PREFIXES = ("spotify:", "tidal:", "applemusic:", "http://", "https://")

# Match `[Genre: deep house, techno]`-style markers AutoCue's enrich_comment
# writes back into DjmdContent.Commnt. We pull styles out of these so the
# normalize-once-store-everywhere pipeline picks them up.
_COMMENT_GENRE_RE = re.compile(r"\[Genre:\s*([^\]]+)\]", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# TasteVector
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class TasteVector:
    """Per-user taste profile derived from the local Rekordbox library.

    All counters are keyed by their canonical normalized form. Top-N accessors
    return entries sorted by weight descending so feeders can slice off "what
    this user actually plays" without re-sorting.
    """

    artists: Counter[str] = field(default_factory=Counter)
    labels: Counter[str] = field(default_factory=Counter)
    styles: Counter[str] = field(default_factory=Counter)
    bpm_hist: list[int] = field(default_factory=lambda: [0] * BPM_BUCKET_COUNT)
    key_hist: Counter[str] = field(default_factory=Counter)

    # Bookkeeping — handy for telemetry + tests asserting on what was filtered.
    track_count: int = 0
    streaming_count: int = 0  # filtered out (informational; not in any counter)

    def top_artists(self, n: int = 30) -> list[str]:
        return [k for k, _ in self.artists.most_common(n)]

    def top_labels(self, n: int = 15) -> list[str]:
        return [k for k, _ in self.labels.most_common(n)]

    def top_styles(self, n: int = 3) -> list[str]:
        return [k for k, _ in self.styles.most_common(n)]

    def is_empty(self) -> bool:
        return not (self.artists or self.labels or self.styles)


# --------------------------------------------------------------------------- #
# normalize_release_key  (PRD §6.3 — locked contract)
# --------------------------------------------------------------------------- #

def normalize_release_key(
    raw_artist: Optional[str],
    raw_title: Optional[str],
    release_id: int,
) -> str:
    """Compute the canonical dedup key for a Discogs release.

    Two cases:

    - **Named-artist release**: ``"{artist_norm}|||{title_norm}"`` so format
      variants of the same album (2002 CD + 2024 vinyl) collapse to one card.
    - **Empty-artist release** (compilation / Various / no artist field):
      ``"[compilation]|||{title_norm}|||rid_{release_id}"``. The ``release_id``
      discriminator prevents two unrelated "Vol 1" comps from colliding. KNOWN
      LIMITATION (documented Tier 1 tradeoff): different release_ids of the
      same compilation reissue surface as separate cards. Tier 2 mitigation is
      ``master_id`` enrichment.

    Title normalization: lowercase + NFKD-fold accents + strip surrounding
    whitespace + collapse internal whitespace to single underscores. Same shape
    as ``library_album_set()`` so already-owned lookups dedup cleanly.

    Must be called with **raw** Discogs strings, never the coerced
    ``"Unknown Artist"`` / ``"Unknown Title"`` display values — those are for
    rendering only and would silently merge unrelated empty-artist releases.
    """
    title_norm = _nfkd_normalize(raw_title or "")
    if raw_artist:
        artist_norm = _nfkd_normalize(raw_artist)
        return f"{artist_norm}|||{title_norm}"
    return f"[compilation]|||{title_norm}|||rid_{release_id}"


def _nfkd_normalize(s: str) -> str:
    """Lowercase + NFKD-fold (accents drop to ASCII where possible) + collapse
    internal whitespace to underscores + strip ends. Idempotent.
    """
    if not s:
        return ""
    folded = unicodedata.normalize("NFKD", s.lower())
    # Drop the combining-mark codepoints NFKD broke characters into.
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", "_", folded.strip())


# --------------------------------------------------------------------------- #
# Source classification (mirrors serve.routes._classify_source)
# --------------------------------------------------------------------------- #

def _is_streaming(folder_path: Optional[str]) -> bool:
    """True iff the FolderPath looks like a streaming-service URI / empty path.

    Mirrors :func:`autocue.serve.routes._classify_source` so the two paths agree
    on what counts as streaming-vs-file. Duplicated here (not imported) to keep
    ``taste.py`` independent of the web layer.
    """
    if not folder_path:
        return True
    low = str(folder_path).strip().lower()
    if not low:
        return True
    return low.startswith(_STREAMING_PREFIXES)


# --------------------------------------------------------------------------- #
# build_taste_vector
# --------------------------------------------------------------------------- #

def build_taste_vector(
    db: Any,
    *,
    include_streaming: bool = False,
    autocue_tag_names: Optional[Iterable[str]] = None,
) -> TasteVector:
    """Walk the Rekordbox database and assemble a :class:`TasteVector`.

    Args:
        db: a ``pyrekordbox.db6.Rekordbox6Database`` (or any duck-typed object
            exposing ``query(model).all()`` for the DjmdContent / DjmdMyTag /
            DjmdSongMyTag / DjmdSongHistory models).
        include_streaming: when ``True``, keep streaming-source tracks in the
            taste vector. Default ``False`` per the PRD-locked invariant.
        autocue_tag_names: allowlist of My Tag names that should be admitted
            as styles. Defaults to ``auto_tag.ALL_AUTOCUE_TAG_NAMES`` — user-
            created tags are excluded so the styles counter isn't polluted by
            arbitrary user taxonomies. Passing an explicit set is mainly for
            tests + the rare caller that wants every tag in.

    Returns:
        :class:`TasteVector` with all five counters populated. Streaming-source
        tracks are tallied separately in ``streaming_count`` for telemetry.
    """
    from pyrekordbox.db6 import (  # local import — keeps taste.py importable in tooling without pyrekordbox
        DjmdContent,
        DjmdKey,
        DjmdMyTag,
        DjmdSongHistory,
        DjmdSongMyTag,
    )

    if autocue_tag_names is None:
        # Defer this import so taste.py stays loadable even when auto_tag's deps
        # aren't satisfied in some tooling context.
        from autocue.analysis.auto_tag import ALL_AUTOCUE_TAG_NAMES

        autocue_tag_names = ALL_AUTOCUE_TAG_NAMES
    autocue_tag_name_set = {n.casefold() for n in autocue_tag_names}

    # Step 1: build play-count + tag indexes once so the per-track loop stays cheap.
    play_counts: Counter[str] = Counter()  # content_id (str) → play count
    for row in db.query(DjmdSongHistory).all():
        cid = getattr(row, "ContentID", None)
        if cid is None:
            continue
        play_counts[str(cid)] += 1

    # Map My Tag IDs → name; we only keep AutoCue-namespaced names.
    autocue_tag_ids: dict[str, str] = {}
    for tag in db.query(DjmdMyTag).all():
        name = getattr(tag, "Name", None)
        if not name:
            continue
        if name.casefold() in autocue_tag_name_set:
            autocue_tag_ids[str(getattr(tag, "ID", ""))] = name

    # content_id → list of AutoCue-namespaced My Tag names
    tags_by_content: dict[str, list[str]] = {}
    for st in db.query(DjmdSongMyTag).all():
        cid = str(getattr(st, "ContentID", ""))
        tid = str(getattr(st, "MyTagID", ""))
        if tid in autocue_tag_ids:
            tags_by_content.setdefault(cid, []).append(autocue_tag_ids[tid])

    # Camelot key lookup
    key_names: dict[str, str] = {}
    for k in db.query(DjmdKey).all():
        kid = getattr(k, "ID", None)
        scale = getattr(k, "ScaleName", None)
        if kid is not None and scale:
            key_names[str(kid)] = str(scale)

    # Per-label intermediate buckets so we can compute log(1+plays)*sqrt(track_count)
    # after the main loop has summed everything.
    label_plays: Counter[str] = Counter()
    label_tracks: Counter[str] = Counter()

    artists_raw_plays: Counter[str] = Counter()
    artists_track_count: Counter[str] = Counter()

    styles = Counter()
    bpm_hist = [0] * BPM_BUCKET_COUNT
    key_hist: Counter[str] = Counter()

    track_count = 0
    streaming_count = 0

    # Step 2: main per-track scan.
    for content in db.query(DjmdContent).all():
        folder = getattr(content, "FolderPath", None)
        if _is_streaming(folder):
            streaming_count += 1
            if not include_streaming:
                continue

        cid = str(getattr(content, "ID", ""))
        plays = play_counts.get(cid, 0)
        track_count += 1

        # Artists — track count + raw play count; converted to log-weights below.
        artist_name = (getattr(content, "ArtistName", None) or "").strip()
        if artist_name:
            artists_track_count[artist_name] += 1
            artists_raw_plays[artist_name] += plays

        # Labels — same shape; uses log(1+plays)*sqrt(track_count) at the end.
        label_name = _label_for(content)
        if label_name:
            label_tracks[label_name] += 1
            label_plays[label_name] += plays

        # Styles — three sources funnel through normalize_style, deduped by canon.
        for raw_style in _collect_raw_styles(content, tags_by_content.get(cid, ())):
            canon = normalize_style(raw_style)
            if canon:
                styles[canon] += 1

        # BPM
        try:
            bpm = float(getattr(content, "BPM", 0) or 0)
        except (TypeError, ValueError):
            bpm = 0.0
        if bpm > 0:
            idx = _bpm_bucket(bpm)
            bpm_hist[idx] += 1

        # Camelot key
        key_id = getattr(content, "KeyID", None)
        if key_id is not None:
            scale = key_names.get(str(key_id))
            if scale:
                key_hist[scale] += 1

    # Step 3: collapse artists / labels into log-weighted counters.
    artists = Counter()
    for name, n_tracks in artists_track_count.items():
        plays = artists_raw_plays[name]
        if plays > 0:
            artists[name] = math.log1p(plays)
        else:
            # Cold-start library (no play history): fall back to track count.
            artists[name] = float(n_tracks)

    labels = Counter()
    for name, n_tracks in label_tracks.items():
        plays = label_plays[name]
        if plays > 0:
            labels[name] = math.log1p(plays) * math.sqrt(n_tracks)
        else:
            labels[name] = float(n_tracks)

    return TasteVector(
        artists=artists,
        labels=labels,
        styles=styles,
        bpm_hist=bpm_hist,
        key_hist=key_hist,
        track_count=track_count,
        streaming_count=streaming_count,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _bpm_bucket(bpm: float) -> int:
    """Map a BPM to its histogram bucket index.

    Out-of-range BPMs (Rekordbox's half-/double-time quirks) clamp to the
    boundary buckets rather than getting dropped.
    """
    if bpm < BPM_MIN:
        return 0
    if bpm >= BPM_MAX:
        return BPM_BUCKET_COUNT - 1
    return int((bpm - BPM_MIN) // BPM_BUCKET_SIZE)


def _label_for(content: Any) -> Optional[str]:
    """Best-effort label lookup. Rekordbox 7 doesn't always populate the same
    attribute across releases; we try the common candidates in order."""
    for attr in ("LabelName", "label_name"):
        v = getattr(content, attr, None)
        if v:
            return str(v).strip()
    label_obj = getattr(content, "Label", None)
    if label_obj is not None:
        name = getattr(label_obj, "Name", None)
        if name:
            return str(name).strip()
    return None


def _collect_raw_styles(content: Any, my_tag_names: Iterable[str]) -> Iterable[str]:
    """Yield raw style strings from every source before normalization.

    Three sources per PRD §6.1:
    - ``GenreName`` association proxy on DjmdContent
    - AutoCue-namespaced My Tag names attached to this track
    - ``[Genre: …]`` markers in the track comment (written back by enrich_comment)

    We yield raw strings — :func:`normalize_style` is applied once at the
    caller so the pipeline matches what the novelty feeder uses elsewhere.
    """
    genre = getattr(content, "GenreName", None)
    if genre:
        # GenreName may be "Deep House / Tech House" — split on common separators.
        for part in re.split(r"[/,;|]", str(genre)):
            part = part.strip()
            if part:
                yield part

    for name in my_tag_names:
        if name:
            yield str(name)

    comment = getattr(content, "Commnt", None)
    if comment:
        for match in _COMMENT_GENRE_RE.finditer(str(comment)):
            for part in re.split(r"[/,;|]", match.group(1)):
                part = part.strip()
                if part:
                    yield part
