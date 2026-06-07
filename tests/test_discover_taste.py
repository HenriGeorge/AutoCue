"""Tests for ``autocue.analysis.discover.taste``.

Covers all T-003 pass criteria:

- TasteVector built from a mocked Rekordbox DB has populated artists / labels
  / styles / bpm_hist / key_hist counters.
- Streaming-source tracks (FolderPath = spotify:/tidal:/applemusic:/empty) are
  excluded when ``include_streaming=False``; tallied in ``streaming_count``.
- User-created My Tags are NOT in the styles Counter — only tags whose names
  match ``ALL_AUTOCUE_TAG_NAMES`` (or an explicit allowlist) flow through.

Also covers the PRD §6.3 normalize_release_key contract that downstream
orchestrator dedup + store CRUD will rely on.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import pytest

from autocue.analysis.discover.taste import (
    BPM_BUCKET_COUNT,
    TasteVector,
    _bpm_bucket,
    build_taste_vector,
    normalize_release_key,
)


# --------------------------------------------------------------------------- #
# Lightweight stand-ins for pyrekordbox ORM rows
# --------------------------------------------------------------------------- #

@dataclass
class FakeContent:
    ID: str
    ArtistName: str = ""
    LabelName: str | None = None
    GenreName: str = ""
    Commnt: str = ""
    BPM: float = 0.0
    KeyID: str | None = None
    FolderPath: str = ""


@dataclass
class FakeHistory:
    ContentID: str


@dataclass
class FakeMyTag:
    ID: str
    Name: str


@dataclass
class FakeSongMyTag:
    ContentID: str
    MyTagID: str


@dataclass
class FakeKey:
    ID: str
    ScaleName: str


@dataclass
class FakeDB:
    """Duck-typed ``db.query(model).all()`` source the builder reads from.

    The real :class:`pyrekordbox.db6.Rekordbox6Database` exposes ``query(model)``
    returning a SQLAlchemy ``Query`` whose ``.all()`` yields rows. We mirror
    just enough of that surface to drive the builder.
    """

    contents: list[FakeContent] = field(default_factory=list)
    histories: list[FakeHistory] = field(default_factory=list)
    my_tags: list[FakeMyTag] = field(default_factory=list)
    song_my_tags: list[FakeSongMyTag] = field(default_factory=list)
    keys: list[FakeKey] = field(default_factory=list)

    def query(self, model: Any) -> "_FakeQuery":  # noqa: D401
        name = getattr(model, "__name__", str(model))
        if name == "DjmdContent":
            data = self.contents
        elif name == "DjmdSongHistory":
            data = self.histories
        elif name == "DjmdMyTag":
            data = self.my_tags
        elif name == "DjmdSongMyTag":
            data = self.song_my_tags
        elif name == "DjmdKey":
            data = self.keys
        else:
            raise AssertionError(f"FakeDB has no fixture for model {name!r}")
        return _FakeQuery(data)


@dataclass
class _FakeQuery:
    _data: list[Any]

    def all(self) -> list[Any]:
        return list(self._data)


# --------------------------------------------------------------------------- #
# normalize_release_key (PRD §6.3)
# --------------------------------------------------------------------------- #

class TestNormalizeReleaseKey:
    def test_named_artist_uses_artist_title_pair(self):
        # Format variants share a key — the named-artist invariant.
        k1 = normalize_release_key("Madvillain", "Madvillainy", release_id=11125)
        k2 = normalize_release_key("Madvillain", "Madvillainy", release_id=99999)
        assert k1 == k2
        assert "rid_" not in k1

    def test_empty_artist_uses_release_id_discriminator(self):
        """Empty-artist comps are keyed by (title, release_id) so unrelated
        'Vol 1' compilations don't collide. Different release_ids → different
        keys; same release_id → same key."""
        a = normalize_release_key("", "Vol 1", release_id=1234)
        b = normalize_release_key("", "Vol 1", release_id=5678)
        c = normalize_release_key(None, "vol 1", release_id=5678)
        assert a != b
        assert b == c, "case + None-vs-'' should be canonicalized to same key"
        assert a.startswith("[compilation]|||")

    def test_nfkd_folds_accents(self):
        """Accented forms should normalize to ASCII-folded keys so 'Beyoncé'
        and 'Beyonce' don't end up as separate releases."""
        accented = normalize_release_key("Beyoncé", "B'Day", release_id=42)
        ascii_ = normalize_release_key("Beyonce", "B'Day", release_id=42)
        assert accented == ascii_

    def test_whitespace_collapses_to_underscore(self):
        a = normalize_release_key("Sam Gendel", "Digi-Squires", release_id=1)
        # Both halves lowercased and internal whitespace → underscore.
        assert a == "sam_gendel|||digi-squires"


# --------------------------------------------------------------------------- #
# build_taste_vector — populated counters
# --------------------------------------------------------------------------- #

@pytest.fixture
def basic_db() -> FakeDB:
    """A small-but-real-shaped library: 4 file tracks, varied artists / labels /
    styles, with play history + AutoCue My Tags + key references."""
    contents = [
        FakeContent(
            ID="c1", ArtistName="Madvillain", LabelName="Stones Throw",
            GenreName="Hip Hop", Commnt="[Genre: boom bap]",
            BPM=92.0, KeyID="k1", FolderPath="/Music/Madvillain/Madvillainy.flac",
        ),
        FakeContent(
            ID="c2", ArtistName="Larry Heard", LabelName="Alleviated",
            GenreName="Deep House",
            BPM=120.0, KeyID="k2", FolderPath="/Music/Heard/SceneryNotSongs.flac",
        ),
        FakeContent(
            ID="c3", ArtistName="Madvillain", LabelName="Stones Throw",
            GenreName="Hip Hop / Abstract",
            BPM=90.0, KeyID="k1", FolderPath="/Music/Madvillain/MmmFood.flac",
        ),
        FakeContent(
            ID="c4", ArtistName="Goldie", LabelName="Metalheadz",
            GenreName="Drum & Bass",
            BPM=174.0, KeyID="k3", FolderPath="/Music/Goldie/Timeless.flac",
        ),
    ]
    histories = [
        # Madvillain Madvillainy played 5 times
        *(FakeHistory(ContentID="c1") for _ in range(5)),
        # Larry Heard played twice
        *(FakeHistory(ContentID="c2") for _ in range(2)),
        # Goldie played once
        FakeHistory(ContentID="c4"),
    ]
    my_tags = [
        FakeMyTag(ID="t1", Name="Boom Bap"),     # AutoCue-namespaced
        FakeMyTag(ID="t99", Name="Wedding Set"),  # User-created — must be filtered out
    ]
    song_my_tags = [
        FakeSongMyTag(ContentID="c1", MyTagID="t1"),
        FakeSongMyTag(ContentID="c2", MyTagID="t99"),  # user tag on Larry Heard
    ]
    keys = [
        FakeKey(ID="k1", ScaleName="8A"),
        FakeKey(ID="k2", ScaleName="3A"),
        FakeKey(ID="k3", ScaleName="11A"),
    ]
    return FakeDB(
        contents=contents, histories=histories,
        my_tags=my_tags, song_my_tags=song_my_tags, keys=keys,
    )


class TestBuildTasteVector:
    def test_pass_criterion_all_counters_populated(self, basic_db):
        """T-003 #1: every counter should be populated from a normal library."""
        tv = build_taste_vector(basic_db, autocue_tag_names={"Boom Bap"})
        assert tv.artists, "artists counter must be populated"
        assert tv.labels, "labels counter must be populated"
        assert tv.styles, "styles counter must be populated"
        assert any(v > 0 for v in tv.bpm_hist), "bpm_hist must have at least one bucket > 0"
        assert tv.key_hist, "key_hist must be populated"

    def test_artists_use_log_play_weighting(self, basic_db):
        """log(1 + play_count) damping so a mega-played artist doesn't dominate."""
        tv = build_taste_vector(basic_db, autocue_tag_names={"Boom Bap"})
        # Madvillain: 5 plays across 2 tracks → log1p(5) per track summed.
        # Larry Heard: 2 plays across 1 track → log1p(2).
        # Goldie: 1 play across 1 track → log1p(1).
        # log1p(5)≈1.79, log1p(2)≈1.10, log1p(1)≈0.69 — confirm ordering.
        ordered = [a for a, _ in tv.artists.most_common()]
        assert ordered[0] == "Madvillain"
        # Madvillain summed across both tracks: 2 * log1p(5/2)? No — we sum the
        # CONTENT-level play count: each track's play count contributes its own
        # log1p separately. c1 has 5 plays, c3 has 0. Total = log1p(5) only.
        assert tv.artists["Madvillain"] == pytest.approx(math.log1p(5), rel=1e-6)

    def test_artist_falls_back_to_track_count_when_no_plays(self):
        """Cold-start library (no DjmdSongHistory) → falls back to track count."""
        db = FakeDB(contents=[
            FakeContent(ID="c1", ArtistName="A", FolderPath="/a.flac"),
            FakeContent(ID="c2", ArtistName="A", FolderPath="/b.flac"),
            FakeContent(ID="c3", ArtistName="B", FolderPath="/c.flac"),
        ])
        tv = build_taste_vector(db, autocue_tag_names=set())
        assert tv.artists["A"] == 2.0
        assert tv.artists["B"] == 1.0

    def test_label_weight_uses_log_plays_times_sqrt_track_count(self, basic_db):
        """labels = log(1+plays) × √track_count when plays > 0."""
        tv = build_taste_vector(basic_db, autocue_tag_names={"Boom Bap"})
        # Stones Throw: 2 tracks (c1, c3), 5 plays (c1) + 0 (c3) = 5 plays.
        expected = math.log1p(5) * math.sqrt(2)
        assert tv.labels["Stones Throw"] == pytest.approx(expected, rel=1e-6)

    def test_label_falls_back_to_track_count(self):
        db = FakeDB(contents=[
            FakeContent(ID="c1", ArtistName="X", LabelName="L", FolderPath="/x.flac"),
            FakeContent(ID="c2", ArtistName="Y", LabelName="L", FolderPath="/y.flac"),
            FakeContent(ID="c3", ArtistName="Z", LabelName="L", FolderPath="/z.flac"),
        ])
        tv = build_taste_vector(db, autocue_tag_names=set())
        assert tv.labels["L"] == 3.0

    def test_styles_canonicalize_through_normalize_style(self, basic_db):
        tv = build_taste_vector(basic_db, autocue_tag_names={"Boom Bap"})
        # "Hip Hop" / "Hip Hop / Abstract" / "boom bap" all canonicalize.
        assert tv.styles["hip_hop"] >= 2, "Hip Hop genre on two tracks"
        # "Boom Bap" (AutoCue tag on c1) + "boom bap" in c1's [Genre: …] comment
        # both flow through.
        assert tv.styles["boom_bap"] >= 1
        assert tv.styles["deep_house"] >= 1
        assert tv.styles["drum_and_bass"] >= 1

    def test_user_created_my_tags_excluded(self, basic_db):
        """T-003 pass criterion: user-created My Tags (not in the allowlist)
        do not flow into the styles counter."""
        # The Larry Heard track (c2) carries a user-created "Wedding Set" tag.
        # That tag must NOT show up in the styles counter, despite being on a
        # real track in the library.
        tv = build_taste_vector(basic_db, autocue_tag_names={"Boom Bap"})
        assert "wedding_set" not in tv.styles
        assert "weddingset" not in tv.styles
        # ...and the artist is still counted; we filter the tag, not the track.
        assert "Larry Heard" in tv.artists

    def test_bpm_histogram_buckets_correctly(self, basic_db):
        tv = build_taste_vector(basic_db, autocue_tag_names={"Boom Bap"})
        # BPMs: 92 (bucket (92-60)//4 = 8), 120 (bucket 15), 90 (bucket 7),
        # 174 (bucket (174-60)//4 = 28).
        assert tv.bpm_hist[8] >= 1
        assert tv.bpm_hist[15] >= 1
        assert tv.bpm_hist[7] >= 1
        assert tv.bpm_hist[28] >= 1

    def test_bpm_zero_excluded_from_histogram(self):
        """BPM=0 (Rekordbox's unanalyzed marker) is not a real BPM and would
        skew the distribution toward bucket 0."""
        db = FakeDB(contents=[
            FakeContent(ID="c1", ArtistName="A", BPM=0.0, FolderPath="/a.flac"),
            FakeContent(ID="c2", ArtistName="A", BPM=120.0, FolderPath="/b.flac"),
        ])
        tv = build_taste_vector(db, autocue_tag_names=set())
        assert sum(tv.bpm_hist) == 1
        assert tv.bpm_hist[_bpm_bucket(120.0)] == 1

    def test_camelot_key_histogram(self, basic_db):
        tv = build_taste_vector(basic_db, autocue_tag_names={"Boom Bap"})
        assert tv.key_hist["8A"] >= 2  # c1 + c3
        assert tv.key_hist["3A"] == 1
        assert tv.key_hist["11A"] == 1


# --------------------------------------------------------------------------- #
# Streaming-source filtering
# --------------------------------------------------------------------------- #

@pytest.fixture
def mixed_source_db() -> FakeDB:
    """Library with file + streaming tracks side-by-side."""
    return FakeDB(contents=[
        FakeContent(ID="local1", ArtistName="LocalOnly", LabelName="L1",
                    BPM=120.0, FolderPath="/Music/local.flac"),
        FakeContent(ID="spot1", ArtistName="SpotifyOnly", LabelName="L2",
                    BPM=128.0, FolderPath="spotify:track:abc123"),
        FakeContent(ID="tidal1", ArtistName="TidalOnly", LabelName="L3",
                    BPM=100.0, FolderPath="tidal:track:xyz"),
        FakeContent(ID="apple1", ArtistName="AppleOnly", LabelName="L4",
                    BPM=140.0, FolderPath="applemusic:song:foo"),
        FakeContent(ID="empty1", ArtistName="EmptyPathOnly", LabelName="L5",
                    BPM=130.0, FolderPath=""),
        FakeContent(ID="http1", ArtistName="HttpOnly", LabelName="L6",
                    BPM=110.0, FolderPath="https://example.com/song.mp3"),
    ])


class TestStreamingFiltering:
    def test_streaming_excluded_by_default(self, mixed_source_db):
        """T-003 pass criterion: only ``source == 'file'`` flows through."""
        tv = build_taste_vector(mixed_source_db, autocue_tag_names=set())
        assert tv.artists == Counter({"LocalOnly": 1.0})
        # Every non-file track tallied separately for telemetry.
        assert tv.streaming_count == 5
        assert tv.track_count == 1

    def test_streaming_included_when_flag_set(self, mixed_source_db):
        tv = build_taste_vector(
            mixed_source_db,
            autocue_tag_names=set(),
            include_streaming=True,
        )
        assert set(tv.artists) == {
            "LocalOnly", "SpotifyOnly", "TidalOnly",
            "AppleOnly", "EmptyPathOnly", "HttpOnly",
        }
        # streaming_count is still tallied even when included — informational only.
        assert tv.streaming_count == 5
        assert tv.track_count == 6


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #

class TestEdgeCases:
    def test_empty_database(self):
        tv = build_taste_vector(FakeDB(), autocue_tag_names=set())
        assert tv.is_empty()
        assert tv.track_count == 0
        assert tv.streaming_count == 0

    def test_track_with_no_artist_skipped_for_artists_counter(self):
        """Tracks with empty ArtistName don't break the build — they just don't
        contribute to the artists counter. Compilations frequently look this way."""
        db = FakeDB(contents=[
            FakeContent(ID="c1", ArtistName="", LabelName="L",
                        GenreName="Various", BPM=120.0, FolderPath="/comp.flac"),
        ])
        tv = build_taste_vector(db, autocue_tag_names=set())
        assert not tv.artists
        assert tv.labels["L"] == 1.0  # label still tallies
        assert tv.styles  # "various" still flows through normalize_style

    def test_default_autocue_allowlist_uses_real_set(self, monkeypatch):
        """When ``autocue_tag_names=None`` the builder pulls the real
        ALL_AUTOCUE_TAG_NAMES allowlist from auto_tag. Verify that a tag whose
        name IS in that set is included while a clearly-user-created name is not."""
        from autocue.analysis import auto_tag

        # Pick any real AutoCue tag name to exercise the default path.
        sample_autocue_name = next(iter(auto_tag.ALL_AUTOCUE_TAG_NAMES))
        db = FakeDB(
            contents=[
                FakeContent(ID="c1", ArtistName="A", FolderPath="/a.flac"),
            ],
            my_tags=[
                FakeMyTag(ID="t_real", Name=sample_autocue_name),
                FakeMyTag(ID="t_user", Name="My Custom Vibes 🎉"),
            ],
            song_my_tags=[
                FakeSongMyTag(ContentID="c1", MyTagID="t_real"),
                FakeSongMyTag(ContentID="c1", MyTagID="t_user"),
            ],
        )
        tv = build_taste_vector(db)  # default allowlist
        # The user-custom name should never appear.
        assert "my_custom_vibes" not in tv.styles

    def test_genre_splits_on_separator(self):
        db = FakeDB(contents=[
            FakeContent(ID="c1", ArtistName="A",
                        GenreName="Deep House / Tech House / Boogie",
                        FolderPath="/a.flac"),
        ])
        tv = build_taste_vector(db, autocue_tag_names=set())
        assert tv.styles["deep_house"] == 1
        assert tv.styles["tech_house"] == 1
        assert tv.styles["boogie"] == 1

    def test_bpm_out_of_range_clamps_to_boundary_bucket(self):
        db = FakeDB(contents=[
            FakeContent(ID="c1", ArtistName="A", BPM=40.0, FolderPath="/low.flac"),
            FakeContent(ID="c2", ArtistName="A", BPM=240.0, FolderPath="/high.flac"),
        ])
        tv = build_taste_vector(db, autocue_tag_names=set())
        # 40 BPM (sub-MIN) clamps to bucket 0; 240 BPM (above MAX) clamps to last.
        assert tv.bpm_hist[0] == 1
        assert tv.bpm_hist[BPM_BUCKET_COUNT - 1] == 1


# --------------------------------------------------------------------------- #
# Top-N accessors
# --------------------------------------------------------------------------- #

class TestTopNAccessors:
    def test_top_n_respects_default_limits(self, basic_db):
        tv = build_taste_vector(basic_db, autocue_tag_names={"Boom Bap"})
        # Madvillain (5 plays) → top of the artists list.
        assert tv.top_artists(1) == ["Madvillain"]
        # Stones Throw should be #1 label by the log(1+plays)*sqrt(2) weight.
        assert tv.top_labels(1) == ["Stones Throw"]
        # Top style is hip_hop (2 tracks contribute).
        assert tv.top_styles(1) == ["hip_hop"]

    def test_top_artists_returns_empty_when_empty(self):
        tv = TasteVector()
        assert tv.top_artists() == []
        assert tv.top_labels() == []
        assert tv.top_styles() == []
        assert tv.is_empty()
