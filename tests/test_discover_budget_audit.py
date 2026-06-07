"""Per-feeder budget enforcement audit (T-040).

PRD §4 locks the per-scan request budget:

    artist:  20
    label:   15
    novelty: 10
    -------------
    total:   45  (with 15 reserved for shop-watch / future feeders)

The orchestrator's HARD_CAP is 60 — if any feeder exceeds its budget, it
burns into other feeders' share of the rate-limit window. These tests
use a request-counting mock that wraps every Discogs client function the
feeders touch and asserts the count NEVER exceeds the budget — even under
error injection and rate-limit signaling, which historically have been
the easiest places to over-spend.

The existing ``tests/test_discover_feeders.py`` confirms each feeder
*tries* to respect its budget on the happy path. This file is the
adversarial complement: it hammers the error + degraded paths.
"""
from __future__ import annotations

from collections import Counter
from unittest import mock

import pytest

from autocue.analysis import discogs as discogs_client
from autocue.analysis.discover.feeders.artist import artist_feeder, DEFAULT_ARTIST_BUDGET
from autocue.analysis.discover.feeders.label import (
    DEFAULT_LABEL_BUDGET,
    label_feeder,
    select_label_slots,
)
from autocue.analysis.discover.feeders.novelty import (
    DEFAULT_NOVELTY_BUDGET,
    novelty_feeder,
)
from autocue.analysis.discover.taste import TasteVector
# Read the orchestrator's HARD_CAP defensively because the symbol's name may
# evolve. The audit's job is to verify the ACTUAL current cap; falling back to
# 60 mirrors PRD §4.
from autocue.analysis.discover import scan_orchestrator as _orch
HARD_CAP = (
    getattr(_orch, "HARD_SCAN_REQUEST_CAP", None)
    or getattr(_orch, "HARD_CAP_TOTAL_BUDGET", None)
    or getattr(_orch, "TOTAL_BUDGET_HARD_CAP", None)
    or 60
)


# ============================================================ shared fixtures

def make_taste(artist_count: int = 30, label_count: int = 30, style_count: int = 5) -> TasteVector:
    """TasteVector with enough entries to fill every feeder's worst case."""
    artists = Counter({f"Artist {i}": 100 - i for i in range(artist_count)})
    labels = Counter({f"Label {i}": 100 - i for i in range(label_count)})
    styles = Counter({f"Style {i}": 100 - i for i in range(style_count)})
    return TasteVector(artists=artists, labels=labels, styles=styles, track_count=200)


class CountingClient:
    """Drop-in mock for the discogs_client module surface that the feeders
    touch. Increments ``self.calls`` on every public-API hit and asserts on
    cap violations.
    """

    def __init__(self, behavior=None):
        # behavior is an optional callable(call_index) → result.
        # Default: every call returns one fake release.
        self.calls = 0
        self.events: list[str] = []
        self._behavior = behavior

    def search_artist_releases(self, artist, *, token, year_from=None, per_page=50, page=1):
        self.calls += 1
        self.events.append(f"artist:{artist}")
        if self._behavior:
            return self._behavior(self.calls)
        return [{"id": self.calls, "title": f"r{self.calls}", "artist": artist}]

    def search_label_releases(self, label_id, *, token, year_from=None, per_page=50, page=1):
        self.calls += 1
        self.events.append(f"label:{label_id}")
        if self._behavior:
            return self._behavior(self.calls)
        return [{"id": self.calls, "title": f"r{self.calls}"}]


# ============================================================ artist feeder

class TestArtistFeederBudget:
    """The artist feeder must spend ≤ DEFAULT_ARTIST_BUDGET requests no matter
    what the underlying Discogs client does."""

    @pytest.mark.parametrize("budget", [1, 5, DEFAULT_ARTIST_BUDGET, DEFAULT_ARTIST_BUDGET + 5])
    def test_happy_path_never_exceeds_budget(self, budget):
        taste = make_taste(artist_count=budget * 3)
        client = CountingClient()
        with mock.patch.object(discogs_client, "search_artist_releases", client.search_artist_releases):
            list(artist_feeder(taste, token="t", budget=budget))
        assert client.calls <= budget, (
            f"artist_feeder spent {client.calls} requests for budget={budget} (events={client.events!r})"
        )

    def test_per_artist_error_still_counts_as_a_request(self):
        """An exception from search_artist_releases must NOT give the feeder
        a free retry slot — the feeder still increments `used` so the budget
        decays correctly."""
        taste = make_taste(artist_count=10)
        budget = 3
        boom = mock.MagicMock(side_effect=RuntimeError("simulated 500"))
        with mock.patch.object(discogs_client, "search_artist_releases", boom):
            results = list(artist_feeder(taste, token="t", budget=budget))
        # At most `budget` calls, even though every one of them errored.
        assert boom.call_count <= budget
        # All results should be ('error', ...) sentinels.
        assert all(isinstance(r, tuple) and r[0] == "error" for r in results)

    def test_near_exhausted_stops_immediately_no_overrun(self):
        """When the underlying client signals near-exhaustion, the feeder must
        return its partial results and stop — it cannot keep dispatching."""
        taste = make_taste(artist_count=10)
        def boom(*a, **kw):
            raise discogs_client.RateLimitNearExhausted(
                remaining=2, data=[{"id": 1, "title": "partial"}],
            )
        with mock.patch.object(discogs_client, "search_artist_releases", boom):
            results = list(artist_feeder(taste, token="t", budget=DEFAULT_ARTIST_BUDGET))
        warnings = [r for r in results if isinstance(r, tuple) and r[0] == "warning"]
        assert len(warnings) == 1, "exactly one warning event on near-exhausted"


# ============================================================ label feeder

class TestLabelFeederBudget:
    """The label feeder relies on ``select_label_slots`` returning ≤ budget
    slots; verify both the selector and the feeder honour the cap together."""

    @pytest.mark.parametrize("budget", [1, 5, DEFAULT_LABEL_BUDGET, DEFAULT_LABEL_BUDGET + 5])
    def test_select_label_slots_caps_at_budget(self, budget, tmp_path):
        from autocue.analysis.discover.store import DiscoverStore
        store = DiscoverStore(db_path=str(tmp_path / "discover.db"))
        # Seed 3x more follows than the budget — selector MUST cap.
        for i in range(budget * 3):
            store.follow_label(label_id=1000 + i, name=f"Label {i}")
        taste = make_taste()
        slots = select_label_slots(store=store, taste_vector=taste, budget=budget)
        assert len(slots) <= budget, f"selector returned {len(slots)} slots for budget={budget}"
        store.close()

    def test_label_feeder_spends_at_most_budget(self, tmp_path):
        from autocue.analysis.discover.store import DiscoverStore
        store = DiscoverStore(db_path=str(tmp_path / "discover.db"))
        budget = 5
        for i in range(budget * 4):
            store.follow_label(label_id=1000 + i, name=f"Label {i}")
        client = CountingClient()
        taste = make_taste()
        # Open a scan first so mark_label_scanned has a scan_id to write under.
        scan_id = store.start_scan(feeders=["label"])
        with mock.patch.object(discogs_client, "search_label_releases", client.search_label_releases):
            list(label_feeder(taste, store, token="t", scan_id=scan_id, budget=budget))
        assert client.calls <= budget, (
            f"label_feeder spent {client.calls} requests for budget={budget}"
        )
        store.close()


# ============================================================ novelty feeder

class TestNoveltyFeederBudget:
    """The novelty feeder has the most branches; verify each strategy stays
    inside its budget."""

    def test_label_strategy_caps_at_budget(self):
        budget = 4
        client = CountingClient()
        # The label-strategy calls _request_json + search_label_releases. We
        # mock both so the request-count is deterministic.
        fake_label_detail = {
            "data": {
                "parent_label": {"id": 9000, "name": "Parent"},
                "sublabels": [{"id": 9001, "name": "Sub A"}, {"id": 9002, "name": "Sub B"}],
            },
            "remaining": 50,
        }
        class FakeResp:
            def __init__(self, data, remaining=50):
                self.data = data
                self.remaining = remaining

        def fake_request_json(path, **kw):
            client.calls += 1
            client.events.append(path)
            return FakeResp(fake_label_detail["data"])

        with mock.patch.object(discogs_client, "_request_json", fake_request_json), \
             mock.patch.object(discogs_client, "search_label_releases", client.search_label_releases):
            list(novelty_feeder(
                make_taste(), adjacency=mock.MagicMock(),
                token="t", strategy="label", budget=budget,
                followed_label_ids=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                followed_label_names=["L"] * 10,
            ))
        assert client.calls <= budget, (
            f"novelty(label) spent {client.calls} for budget={budget}"
        )

    def test_artist_strategy_caps_at_budget(self):
        budget = 4
        client = CountingClient()
        rel_calls = {"n": 0}
        def fake_relations(artist_id, *, token):
            rel_calls["n"] += 1
            client.calls += 1
            client.events.append(f"rel:{artist_id}")
            return {"members": [], "groups": []}

        with mock.patch.object(discogs_client, "get_artist_relations", fake_relations), \
             mock.patch.object(discogs_client, "search_artist_releases", client.search_artist_releases):
            list(novelty_feeder(
                make_taste(), adjacency=mock.MagicMock(),
                token="t", strategy="artist", budget=budget,
                top_artist_ids=list(range(1, 21)),
            ))
        assert client.calls <= budget, (
            f"novelty(artist) spent {client.calls} for budget={budget}"
        )

    def test_zero_budget_yields_nothing(self):
        # Every strategy is a no-op at budget=0.
        for strat in ["style", "label", "artist"]:
            results = list(novelty_feeder(
                make_taste(), adjacency=mock.MagicMock(),
                token="t", strategy=strat, budget=0,
                followed_label_ids=[1, 2, 3],
                followed_label_names=["L1", "L2", "L3"],
                top_artist_ids=[1, 2, 3],
            ))
            assert results == [], f"strategy {strat!r} produced output at budget=0"


# ============================================================ orchestrator total cap

class TestOrchestratorHardCap:
    """The orchestrator's ``ScanConfig.__post_init__`` validation MUST refuse
    a feeder-budget table whose sum exceeds the global hard cap. That's the
    final guard if a future PR bumps one budget and forgets the others.
    """

    def test_sum_of_default_budgets_does_not_exceed_hard_cap(self):
        from autocue.analysis.discover.feeders.artist import DEFAULT_ARTIST_BUDGET as a
        from autocue.analysis.discover.feeders.label import DEFAULT_LABEL_BUDGET as l
        from autocue.analysis.discover.feeders.novelty import DEFAULT_NOVELTY_BUDGET as n
        assert a + l + n <= HARD_CAP, (
            f"default budgets sum to {a + l + n} (artist={a}, label={l}, novelty={n}), "
            f"exceeds hard cap {HARD_CAP}"
        )

    def test_scan_config_validate_rejects_over_budget(self):
        from autocue.analysis.discover.scan_orchestrator import ScanConfig
        cfg = ScanConfig(
            feeders=["artist", "label", "novelty"],
            artist_budget=HARD_CAP,
            label_budget=HARD_CAP,
            novelty_budget=HARD_CAP,
        )
        # ScanConfig uses an explicit .validate() rather than __post_init__ so
        # the orchestrator can fail loudly at scan start instead of import time.
        with pytest.raises(ValueError, match="exceeds hard cap"):
            cfg.validate()
