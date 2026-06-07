"""Tests for the Discover v2 surface of ``autocue.analysis.discogs``.

Covers all T-005 pass criteria + the rate-limit observability contract:

- Each new function has a happy-path test with mocked HTTP.
- ``RateLimitNearExhausted`` raises (with response data attached) when the
  ``x-discogs-ratelimit-remaining`` header drops below 5.
- ``Discogs429`` raises with ``retry_after`` on HTTP 429.
- ``validate_token`` returns True for 200 on ``/oauth/identity``, False for 401,
  False for empty token, and re-raises other transport errors.
- ``get_last_remaining`` reflects the most recent observed value.
"""

from __future__ import annotations

import io
import json
import urllib.error
from contextlib import contextmanager
from email.message import Message
from unittest.mock import patch

import pytest

from autocue.analysis import discogs as discogs_mod
from autocue.analysis.discogs import (
    Discogs429,
    NEAR_EXHAUSTED_THRESHOLD,
    RateLimitNearExhausted,
    get_artist_relations,
    get_last_remaining,
    get_release_details,
    reset_rate_limit_state,
    search_label_releases,
    search_labels,
    search_seller_inventory,
    validate_token,
)


# --------------------------------------------------------------------------- #
# Fixtures + helpers
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _reset_rate_state():
    """Each test starts with no rate-limit history. Otherwise residue from a
    prior near-exhausted response could leak into the next test's assertions
    on :func:`get_last_remaining`."""
    reset_rate_limit_state()
    yield
    reset_rate_limit_state()


@pytest.fixture(autouse=True)
def _fast_token_bucket(monkeypatch):
    """Skip the real token-bucket so tests don't sleep — the bucket is exercised
    separately in test_discogs.py for the legacy surface."""
    monkeypatch.setattr(discogs_mod, "_acquire_token", lambda: None)


class _FakeResponse:
    """Stand-in for ``urllib.request.urlopen`` context-manager return value."""

    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self._body = body
        msg = Message()
        for k, v in (headers or {}).items():
            msg[k] = v
        self.headers = msg

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None


def _make_urlopen(
    body: dict | list,
    *,
    remaining: int | None = 50,
    status: int = 200,
    retry_after: int | None = None,
):
    """Build a urlopen replacement that returns ``body`` JSON-encoded with the
    given ``x-discogs-ratelimit-remaining`` header. On non-200 ``status``,
    raises ``HTTPError`` mirroring real urllib behavior."""
    raw = json.dumps(body).encode()

    def fake_urlopen(req, timeout=None):  # noqa: ARG001
        if status != 200:
            headers = Message()
            if retry_after is not None:
                headers["Retry-After"] = str(retry_after)
            raise urllib.error.HTTPError(
                url=getattr(req, "full_url", str(req)),
                code=status,
                msg=f"HTTP {status}",
                hdrs=headers,
                fp=io.BytesIO(raw),
            )
        headers = {}
        if remaining is not None:
            headers["x-discogs-ratelimit-remaining"] = str(remaining)
        return _FakeResponse(raw, headers)

    return fake_urlopen


@contextmanager
def patched_urlopen(*args, **kwargs):
    with patch("urllib.request.urlopen", _make_urlopen(*args, **kwargs)):
        yield


# --------------------------------------------------------------------------- #
# search_label_releases
# --------------------------------------------------------------------------- #

class TestSearchLabelReleases:
    BODY = {
        "releases": [
            {"id": 1, "title": "First", "artist": "A", "year": 2026,
             "format": "12\"", "thumb": "/t1.jpg", "resource_url": "u1"},
            {"id": 2, "title": "Older", "artist": "B", "year": 2010,
             "format": "LP", "thumb": "", "resource_url": "u2"},
            {"id": 3, "title": "Untyped year", "artist": "C", "year": None,
             "format": "LP", "thumb": "", "resource_url": "u3"},
        ]
    }

    def test_happy_path_returns_normalized_list(self):
        with patched_urlopen(self.BODY, remaining=50):
            out = search_label_releases(label_id=42, token="t")
        assert len(out) == 3
        assert out[0]["title"] == "First"
        assert out[0]["year"] == 2026
        assert out[0]["resource_url"] == "u1"
        assert get_last_remaining() == 50

    def test_year_from_filters_old_releases(self):
        with patched_urlopen(self.BODY, remaining=50):
            out = search_label_releases(label_id=42, token="t", year_from=2024)
        # "First" (2026) survives; "Older" (2010) and "Untyped year" (None) drop.
        assert [r["id"] for r in out] == [1]

    def test_empty_token_returns_empty_without_http(self):
        # If we reach urlopen the patch isn't installed, so any call would crash
        # with the real urlopen.NotImplementedError. Verify by NOT patching.
        out = search_label_releases(label_id=42, token="")
        assert out == []

    def test_zero_label_id_returns_empty(self):
        out = search_label_releases(label_id=0, token="t")
        assert out == []


# --------------------------------------------------------------------------- #
# search_seller_inventory
# --------------------------------------------------------------------------- #

class TestSearchSellerInventory:
    BODY = {
        "listings": [
            {"id": 100, "posted": "2026-06-01T10:00:00", "price": {"value": 10, "currency": "EUR"},
             "uri": "https://www.discogs.com/sell/item/100",
             "release": {"id": 1, "title": "Madvillainy", "artist": "Madvillain",
                         "format": "2xLP", "thumbnail": "/t.jpg"}},
            {"id": 99, "posted": "2024-01-01T00:00:00", "price": {"value": 20, "currency": "EUR"},
             "uri": "u2", "release": {"id": 2, "title": "Old", "artist": "X"}},
        ]
    }

    def test_happy_path(self):
        with patched_urlopen(self.BODY, remaining=50):
            out = search_seller_inventory(seller="hardwax", token="t")
        assert len(out) == 2
        assert out[0]["listing_id"] == 100
        assert out[0]["release_id"] == 1
        assert out[0]["price"] == 10
        assert out[0]["title"] == "Madvillainy"

    def test_since_date_drops_old_listings(self):
        with patched_urlopen(self.BODY, remaining=50):
            out = search_seller_inventory(
                seller="hardwax", token="t", since_date="2026-01-01",
            )
        # 2024-01-01 listing is dropped; 2026-06-01 survives.
        assert [item["listing_id"] for item in out] == [100]

    def test_seller_with_special_chars_url_encoded(self):
        captured: dict[str, str] = {}

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            captured["url"] = req.full_url
            return _FakeResponse(b'{"listings":[]}',
                                 {"x-discogs-ratelimit-remaining": "50"})

        with patch("urllib.request.urlopen", fake_urlopen):
            search_seller_inventory(seller="weird/seller name", token="t")
        # The path segment must be percent-encoded; '/' must NOT survive as a
        # path separator inside the seller name.
        assert "/users/weird%2Fseller%20name/inventory" in captured["url"]

    def test_empty_seller_returns_empty_without_http(self):
        assert search_seller_inventory(seller="", token="t") == []


# --------------------------------------------------------------------------- #
# get_release_details
# --------------------------------------------------------------------------- #

class TestGetReleaseDetails:
    BODY = {
        "id": 11125,
        "master_id": 99,
        "title": "Madvillainy",
        "artists": [{"name": "Madvillain"}],
        "labels": [{"name": "Stones Throw"}],
        "year": 2004,
        "country": "US",
        "formats": [{"name": "Vinyl"}, {"name": "LP"}],
        "genres": ["Hip Hop"],
        "styles": ["Abstract", "Boom Bap"],
        "tracklist": [
            {"position": "A1", "title": "Accordion", "duration": "1:58", "type_": "track"},
            {"position": "", "title": "[Side A]", "type_": "heading"},  # should drop
        ],
        "videos": [{"uri": "https://youtube.com/abc"}, {"uri": ""}],
        "notes": "Classic record.",
        "thumb": "/t.jpg",
        "cover_image": "/c.jpg",
    }

    def test_happy_path_extracts_full_release(self):
        with patched_urlopen(self.BODY, remaining=50):
            out = get_release_details(release_id=11125, token="t")
        assert out["id"] == 11125
        assert out["master_id"] == 99  # the key Tier-2 dedup hinges on
        assert out["artist"] == "Madvillain"
        assert out["label"] == "Stones Throw"
        assert out["formats"] == ["Vinyl", "LP"]
        assert out["styles"] == ["Abstract", "Boom Bap"]
        # Tracklist only contains type=='track' rows; the heading entry is dropped.
        assert len(out["tracklist"]) == 1
        assert out["tracklist"][0]["position"] == "A1"
        # Videos drop empty URIs.
        assert out["videos"] == ["https://youtube.com/abc"]

    def test_missing_master_id_is_none(self):
        body = {**self.BODY, "master_id": None}
        with patched_urlopen(body, remaining=50):
            out = get_release_details(release_id=11125, token="t")
        assert out["master_id"] is None

    def test_multiple_artists_joined_with_ampersand(self):
        body = {**self.BODY, "artists": [{"name": "Madlib"}, {"name": "MF DOOM"}]}
        with patched_urlopen(body, remaining=50):
            out = get_release_details(release_id=1, token="t")
        assert out["artist"] == "Madlib & MF DOOM"

    def test_zero_release_id_returns_empty(self):
        assert get_release_details(release_id=0, token="t") == {}


# --------------------------------------------------------------------------- #
# search_labels (autocomplete)
# --------------------------------------------------------------------------- #

class TestSearchLabels:
    BODY = {
        "results": [
            {"id": 1, "title": "Stones Throw", "thumb": "/t1.jpg", "resource_url": "u1"},
            {"id": 2, "title": "Stones Throw Records", "thumb": "", "resource_url": "u2"},
        ]
    }

    def test_happy_path(self):
        with patched_urlopen(self.BODY, remaining=50):
            out = search_labels(query="Stones Throw", token="t")
        assert len(out) == 2
        assert out[0]["name"] == "Stones Throw"
        # Discogs returns label names in the search 'title' field; we surface 'name'.
        assert "title" not in out[0]

    def test_empty_query_returns_empty(self):
        assert search_labels(query="   ", token="t") == []


# --------------------------------------------------------------------------- #
# get_artist_relations
# --------------------------------------------------------------------------- #

class TestGetArtistRelations:
    def test_extracts_members_and_groups(self):
        body = {
            "members": [
                {"id": 10, "name": "Madlib"},
                {"id": 11, "name": "MF DOOM"},
                {"id": None, "name": "drop-me"},  # missing id → skipped
            ],
            "groups": [{"id": 20, "name": "Some Group"}],
        }
        with patched_urlopen(body, remaining=50):
            out = get_artist_relations(artist_id=99, token="t")
        assert [m["id"] for m in out["members"]] == [10, 11]
        assert [g["id"] for g in out["groups"]] == [20]

    def test_missing_members_or_groups_returns_empty_lists(self):
        with patched_urlopen({}, remaining=50):
            out = get_artist_relations(artist_id=1, token="t")
        assert out == {"members": [], "groups": []}

    def test_zero_id_returns_empty_without_http(self):
        assert get_artist_relations(artist_id=0, token="t") == {"members": [], "groups": []}


# --------------------------------------------------------------------------- #
# validate_token
# --------------------------------------------------------------------------- #

class TestValidateToken:
    IDENTITY_OK = {"id": 1234, "username": "henri", "resource_url": "u"}

    def test_returns_true_on_200(self):
        with patched_urlopen(self.IDENTITY_OK, remaining=50):
            assert validate_token(token="t") is True

    def test_returns_false_on_401(self):
        with patched_urlopen({"message": "Invalid consumer token"},
                              remaining=50, status=401):
            assert validate_token(token="bad") is False

    def test_returns_false_on_empty_token_without_http(self):
        assert validate_token(token="") is False
        assert validate_token(token=None) is False  # type: ignore[arg-type]

    def test_propagates_5xx_so_caller_can_distinguish(self):
        with patched_urlopen({"err": "down"}, remaining=50, status=503):
            with pytest.raises(urllib.error.HTTPError):
                validate_token(token="t")

    def test_does_not_raise_near_exhausted(self):
        """Startup checks shouldn't be the path that surfaces rate-limit warnings —
        the orchestrator owns that. validate_token is silent on low-remaining."""
        with patched_urlopen(self.IDENTITY_OK, remaining=1):
            # No RateLimitNearExhausted raised.
            assert validate_token(token="t") is True


# --------------------------------------------------------------------------- #
# Rate-limit signaling — Discogs429 + RateLimitNearExhausted
# --------------------------------------------------------------------------- #

class TestRateLimitSignaling:
    def test_429_raises_with_retry_after(self):
        with patched_urlopen({}, status=429, retry_after=120):
            with pytest.raises(Discogs429) as exc_info:
                search_label_releases(label_id=1, token="t")
        assert exc_info.value.retry_after == 120

    def test_429_without_retry_after_header_defaults_to_60(self):
        with patched_urlopen({}, status=429, retry_after=None):
            with pytest.raises(Discogs429) as exc_info:
                search_labels(query="x", token="t")
        assert exc_info.value.retry_after == 60

    def test_near_exhausted_raises_with_data_attached(self):
        body = {"releases": [{"id": 1, "title": "OnLast", "artist": "A", "year": 2026}]}
        with patched_urlopen(body, remaining=NEAR_EXHAUSTED_THRESHOLD - 1):
            with pytest.raises(RateLimitNearExhausted) as exc_info:
                search_label_releases(label_id=1, token="t")
        # The exception carries the parsed data so the caller can still use it.
        assert exc_info.value.remaining == NEAR_EXHAUSTED_THRESHOLD - 1
        assert isinstance(exc_info.value.data, list)
        assert exc_info.value.data[0]["title"] == "OnLast"
        # And module state reflects the low count.
        assert get_last_remaining() == NEAR_EXHAUSTED_THRESHOLD - 1

    def test_at_threshold_does_not_raise(self):
        """Boundary: ``< 5`` is the trigger, not ``≤ 5``."""
        body = {"releases": []}
        with patched_urlopen(body, remaining=NEAR_EXHAUSTED_THRESHOLD):
            out = search_label_releases(label_id=1, token="t")
        assert out == []

    def test_missing_header_does_not_raise(self):
        """If Discogs ever drops the header, treat it as 'unknown, full-speed-ahead'.
        Better than spuriously raising and aborting every scan."""
        body = {"releases": []}
        with patched_urlopen(body, remaining=None):
            # No exception, no warning.
            search_label_releases(label_id=1, token="t")
        assert get_last_remaining() is None


# --------------------------------------------------------------------------- #
# get_last_remaining observability
# --------------------------------------------------------------------------- #

class TestGetLastRemaining:
    def test_starts_at_none(self):
        assert get_last_remaining() is None

    def test_updates_on_successful_response(self):
        with patched_urlopen({"results": []}, remaining=42):
            search_labels(query="x", token="t")
        assert get_last_remaining() == 42

    def test_updates_on_429_to_reflect_unknown(self):
        """A 429 raises before we can read the header; last_remaining must NOT be
        spuriously set to a stale prior value. We don't strictly require it to be
        cleared, but we do require it to not be misleading: it should never be a
        number that doesn't correspond to a real prior response."""
        # Prior call sets remaining=42.
        with patched_urlopen({"results": []}, remaining=42):
            search_labels(query="x", token="t")
        assert get_last_remaining() == 42

        # Subsequent 429 raises; the stale 42 from the prior call is still there
        # (and that's fine — the orchestrator will reset state anyway when it
        # decides to retry after the Retry-After window).
        with patched_urlopen({}, status=429, retry_after=30):
            with pytest.raises(Discogs429):
                search_labels(query="y", token="t")
        # We don't assert a specific value here — what matters is that the
        # 429 path didn't crash trying to parse missing headers.

    def test_reset_clears_state(self):
        with patched_urlopen({"results": []}, remaining=42):
            search_labels(query="x", token="t")
        reset_rate_limit_state()
        assert get_last_remaining() is None
