"""Label-watch feeder — Discover v2 Feeder 2 (the headline new surface).

Pulls recent releases on the labels the user has either followed explicitly OR
that bubbled to the top of the taste-vector's plays-weighted label scores.
Per PRD §6.2 Feeder 2, the priority order is locked:

  1. **Explicit follows take precedence** — every label in ``followed_labels``
     that's over its 24h TTL gets a slot first.
  2. **Taste-vector implicit follows fill the rest** — when the user has fewer
     than ``budget`` explicit follows, the top labels in the taste vector
     (excluding any already in the explicit list) take the remaining slots.
  3. **Round-robin fairness when explicit follows exceed budget** — labels are
     ordered ``last_scanned_at ASC NULLS FIRST`` so the longest-unscanned
     explicit follow always gets a slot first. Across N scans every explicit
     follow is guaranteed to be scanned at least once per ``ceil(N_explicit / budget)``.

The feeder writes per-label completion via :meth:`DiscoverStore.mark_label_scanned`
to the **staging columns** (``last_scanned_at_pending`` + ``pending_scan_id``).
The orchestrator promotes these to ``last_scanned_at`` atomically at scan-finish
via :meth:`DiscoverStore.commit_pending_scan`. That decoupling is what makes a
crashed mid-scan reset cleanly via boot recovery without leaking partial state
into the next scan's TTL gate.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from autocue.analysis import discogs as discogs_client
from autocue.analysis.discover.store import DiscoverStore
from autocue.analysis.discover.taste import TasteVector

logger = logging.getLogger(__name__)


DEFAULT_LABEL_BUDGET = 15
DEFAULT_TTL_HOURS = 24


def _is_ttl_fresh(last_scanned_at: Optional[str], ttl_hours: int, now: datetime) -> bool:
    """True iff a previously-scanned label is still within its TTL window."""
    if not last_scanned_at:
        return False
    try:
        ts = datetime.fromisoformat(last_scanned_at)
    except (ValueError, TypeError):
        # Corrupt timestamp — treat as never-scanned so we re-fetch.
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts) < timedelta(hours=ttl_hours)


def _label_id_for_taste_vector_entry(label_name: str) -> Optional[int]:
    """Map a taste-vector label name to a Discogs numeric label_id.

    Tier 1 scope: the taste-vector ``labels`` Counter is keyed by Rekordbox
    label-name strings, NOT by Discogs IDs. Without a resolver we can't issue
    ``GET /labels/{id}/releases`` for implicit follows. The resolver belongs in
    a future task (or could call :func:`discogs.search_labels` per name, but
    that would burn the per-scan budget on lookups).

    For now we return ``None`` for implicit follows. Explicit follows already
    carry a real ``label_id`` from ``DiscoverStore.list_followed_labels``, so
    they're the only labels the feeder dispatches against. This is the
    correct Tier 1 behavior — implicit follows only matter when the user has
    fewer than ``budget`` explicit follows, which is the first-run state we
    address with the onboarding banner per PRD Flow A.
    """
    # NOTE: see the docstring — this stub returns None on purpose. Tier 2 wires
    # in a name → Discogs label_id cache so implicit follows can fill empty
    # budget slots. The function exists as a single hook the cache can land on.
    return None


def select_label_slots(
    *,
    store: DiscoverStore,
    taste_vector: TasteVector,
    budget: int,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Pick which labels to scan this round.

    Returns up to ``budget`` candidates as a list of dicts:
    ``{"label_id": int, "name": str, "source": "explicit" | "implicit"}``.

    Selection algorithm (PRD §6.2 Feeder 2):

    1. List explicit follows, ordered ``last_scanned_at ASC NULLS FIRST``
       (the store-side query already does this).
    2. Filter out labels that are within their ``ttl_hours`` TTL window. Those
       were scanned recently enough; re-scanning would burn budget for no
       new information.
    3. Take up to ``budget`` slots from this list.
    4. If we still have budget left, fill from the taste-vector implicit
       follows. The implicit-name → label_id resolver currently returns
       ``None`` (Tier 1 limitation, see :func:`_label_id_for_taste_vector_entry`)
       so this branch is effectively a no-op until Tier 2.

    The returned order is the order the feeder dispatches in — explicit
    follows first (longest-unscanned at the front).
    """
    if budget <= 0:
        return []
    now = now or datetime.now(timezone.utc)

    explicit_rows = store.list_followed_labels()
    explicit_ids: set[int] = set()
    slots: list[dict] = []
    for row in explicit_rows:
        if len(slots) >= budget:
            break
        last = row.get("last_scanned_at")
        if _is_ttl_fresh(last, ttl_hours, now):
            continue
        slots.append({
            "label_id": int(row["label_id"]),
            "name": str(row["name"]),
            "source": "explicit",
        })
        explicit_ids.add(int(row["label_id"]))

    remaining = budget - len(slots)
    if remaining <= 0:
        return slots

    for name in taste_vector.top_labels(remaining * 2):  # over-fetch — many will lack a resolved ID
        if len(slots) >= budget:
            break
        label_id = _label_id_for_taste_vector_entry(name)
        if label_id is None or label_id in explicit_ids:
            continue
        slots.append({
            "label_id": label_id,
            "name": name,
            "source": "implicit",
        })
        explicit_ids.add(label_id)

    return slots


def label_feeder(
    taste_vector: TasteVector,
    store: DiscoverStore,
    token: str,
    *,
    scan_id: int,
    budget: int = DEFAULT_LABEL_BUDGET,
    year_from: int | None = None,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    now: Optional[datetime] = None,
) -> Iterator[dict]:
    """Yield release dicts from the labels selected by :func:`select_label_slots`.

    Writes per-label completion via :meth:`DiscoverStore.mark_label_scanned`
    to the staging columns; the orchestrator commits them on success.

    Args:
        taste_vector: built by :func:`build_taste_vector`. Used for the
            implicit-fill branch (a no-op until the Tier 2 name-resolver lands).
        store: the active :class:`DiscoverStore`. Used to read the followed
            list and write staging-column updates.
        token: Discogs personal access token.
        scan_id: the active scan's ID (from :meth:`DiscoverStore.start_scan`).
            Passed through to :meth:`mark_label_scanned` so boot recovery can
            unwind a crashed scan's pending writes per-scan_id.
        budget: maximum number of Discogs requests this feeder may issue.
        year_from: drop releases older than this year.
        ttl_hours: how recently a label must have been scanned to be skipped
            this round. Defaults to 24 hours per PRD §8.
        now: override for tests. Production callers omit this.

    Yields:
        Per surfaced release: ``{"source": "label", "label_id": int,
        "label_name": str, "release": {...}}``. The ``release`` shape matches
        :func:`discogs.search_label_releases`.

        Rate-limit signaling mirrors :func:`artist_feeder` — ``Discogs429``
        re-raises (orchestrator aborts), ``RateLimitNearExhausted`` yields the
        partial results + a ``("warning", ...)`` sentinel + stops the feeder.
        Per-label errors yield an ``("error", ...)`` sentinel and continue.
    """
    if budget <= 0 or not token:
        return

    slots = select_label_slots(
        store=store,
        taste_vector=taste_vector,
        budget=budget,
        ttl_hours=ttl_hours,
        now=now,
    )

    for slot in slots:
        label_id = slot["label_id"]
        label_name = slot["name"]

        try:
            releases = discogs_client.search_label_releases(
                label_id,
                token=token,
                year_from=year_from,
                per_page=50,
            )
        except discogs_client.Discogs429:
            raise
        except discogs_client.RateLimitNearExhausted as exc:
            for release in (exc.data or []):
                yield {
                    "source": "label",
                    "label_id": label_id,
                    "label_name": label_name,
                    "release": release,
                }
            # Even though we hit near-exhausted, we DID fetch this label
            # successfully — record the staging-column write so the
            # orchestrator's commit step can promote it.
            store.mark_label_scanned(label_id, scan_id)
            yield ("warning", {"feeder": "label", "remaining": exc.remaining})
            return
        except Exception as exc:
            logger.warning("label_feeder: error on %r (%s): %s", label_name, label_id, exc)
            yield ("error", {
                "feeder": "label",
                "label_id": label_id,
                "label_name": label_name,
                "exc": str(exc),
            })
            continue

        store.mark_label_scanned(label_id, scan_id)
        for release in releases:
            yield {
                "source": "label",
                "label_id": label_id,
                "label_name": label_name,
                "release": release,
            }
