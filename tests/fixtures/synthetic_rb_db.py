"""Synthetic Rekordbox-shaped SQLite library for perf benchmarks (issue #108).

Builds a plain-SQLite file (no SQLCipher) using pyrekordbox's declarative
schema and populates it with a realistic-shape library:

* ``DjmdContent`` × N (default 10 000)
* ``DjmdArtist`` × 500
* ``DjmdAlbum`` × 800
* ``DjmdKey`` × 24
* ``DjmdColor`` × 8
* ``DjmdGenre`` × 20

This gives the ``/api/tracks`` cold path a real ORM workload — the per-row
``ArtistName`` / ``AlbumName`` / ``GenreName`` accessors traverse the relevant
relationships, and the pre-fetch queries (history, mytag, color, hot-cue
counts) run against real tables (even if empty in the synthetic DB).

Open the result via ``pyrekordbox.db6.Rekordbox6Database(path=db_path,
unlock=False)`` — pyrekordbox's plain-SQLite mode skips SQLCipher entirely
which means tests do not need a master key.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import sqlalchemy as sa

DEFAULT_TRACK_COUNT = 10_000
DEFAULT_ARTIST_COUNT = 500
DEFAULT_ALBUM_COUNT = 800
DEFAULT_KEY_COUNT = 24
DEFAULT_COLOR_COUNT = 8
DEFAULT_GENRE_COUNT = 20


def _uid() -> str:
    return uuid.uuid4().hex[:32]


def _content_row(
    i: int,
    *,
    artist_id: str,
    album_id: str,
    key_id: str,
    color_id: str,
    genre_id: str,
) -> dict:
    """One ``djmdContent`` row with every NOT NULL column populated.

    Numeric ID is a stringified int so ``TrackItem.id`` (typed ``int``) can
    parse it without a custom adapter — matches how Rekordbox stores IDs.
    """
    return dict(
        ID=str(100_000 + i),
        FolderPath=f"/music/track_{i}.mp3" if i % 5 else "",
        FileNameL=f"track_{i}.mp3",
        FileNameS=f"track_{i}.mp3",
        Title=f"Track Title {i:05d}",
        ArtistID=artist_id,
        AlbumID=album_id,
        GenreID=genre_id,
        # BPM stored as int × 100 in Rekordbox (e.g. 128.00 → 12800)
        BPM=12000 + (i % 4000),
        Length=180 + (i % 240),
        TrackNo=(i % 20),
        BitRate=320,
        BitDepth=16,
        Commnt="",
        FileType=1,
        Rating=(i % 6),
        ReleaseYear=2024,
        RemixerID="",
        LabelID="",
        OrgArtistID="",
        KeyID=key_id,
        StockDate="",
        # Sparse color assignment — mirrors real libraries
        ColorID=color_id if i % 7 == 0 else "",
        DJPlayCount=str(i % 20),
        ImagePath="",
        MasterDBID="",
        MasterSongID="",
        AnalysisDataPath=f"/music/ANLZ{i:08d}.DAT" if i % 3 else "",
        SearchStr=f"track title {i}",
        FileSize=8_000_000,
        DiscNo=1,
        ComposerID="",
        Subtitle="",
        SampleRate=44100,
        DisableQuantize=0,
        Analysed=1,
        ReleaseDate="",
        DateCreated="",
        ContentLink=0,
        Tag="",
        ModifiedByRBM="",
        HotCueAutoLoad="",
        DeliveryControl="",
        DeliveryComment="",
        CueUpdated="",
        AnalysisUpdated="",
        TrackInfoUpdated="",
        Lyricist="",
        ISRC="",
        SamplerTrackInfo=0,
        SamplerPlayOffset=0,
        SamplerGain=0.0,
        VideoAssociate="",
        LyricStatus=0,
        ServiceID=0,
        OrgFolderPath="",
        Reserved1="",
        Reserved2="",
        Reserved3="",
        Reserved4="",
        ExtInfo="",
        rb_file_id="",
        DeviceID="",
        rb_LocalFolderPath="",
        SrcID="",
        SrcTitle="",
        SrcArtistName="",
        SrcAlbumName="",
        SrcLength=0,
        UUID=_uid(),
        usn=0,
        rb_local_usn=0,
    )


def build_synthetic_library(
    db_dir: Path,
    *,
    track_count: int = DEFAULT_TRACK_COUNT,
    artist_count: int = DEFAULT_ARTIST_COUNT,
    album_count: int = DEFAULT_ALBUM_COUNT,
    key_count: int = DEFAULT_KEY_COUNT,
    color_count: int = DEFAULT_COLOR_COUNT,
    genre_count: int = DEFAULT_GENRE_COUNT,
) -> Path:
    """Create ``<db_dir>/master.db`` populated with a synthetic Rekordbox library.

    Returns the master.db path. Also writes a stub ``masterPlaylists6.xml``
    so :class:`pyrekordbox.db6.Rekordbox6Database` opens without logging a
    warning during the test run.
    """
    # Import inside the function so module import doesn't drag in pyrekordbox
    # unless the fixture is actually used.
    from pyrekordbox.db6 import tables as t

    db_dir = Path(db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    (db_dir / "masterPlaylists6.xml").write_text("<DJ_PLAYLISTS/>")
    db_path = db_dir / "master.db"

    engine = sa.create_engine(f"sqlite:///{db_path}")
    t.Base.metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            t.DjmdKey.__table__.insert(),
            [
                dict(
                    ID=str(i + 1),
                    ScaleName=f"K{i}",
                    Seq=i,
                    UUID=_uid(),
                    usn=0,
                    rb_local_usn=0,
                )
                for i in range(key_count)
            ],
        )
        conn.execute(
            t.DjmdArtist.__table__.insert(),
            [
                dict(
                    ID=str(1000 + i),
                    Name=f"Artist {i}",
                    SearchStr=f"artist {i}",
                    UUID=_uid(),
                    usn=0,
                    rb_local_usn=0,
                )
                for i in range(artist_count)
            ],
        )
        conn.execute(
            t.DjmdAlbum.__table__.insert(),
            [
                dict(
                    ID=str(2000 + i),
                    Name=f"Album {i}",
                    AlbumArtistID="",
                    ImagePath="",
                    Compilation=0,
                    SearchStr=f"album {i}",
                    UUID=_uid(),
                    usn=0,
                    rb_local_usn=0,
                )
                for i in range(album_count)
            ],
        )
        conn.execute(
            t.DjmdColor.__table__.insert(),
            [
                dict(
                    ID=str(50 + i),
                    ColorCode=i,
                    SortKey=i,
                    Commnt=f"Color{i}",
                    UUID=_uid(),
                    usn=0,
                    rb_local_usn=0,
                )
                for i in range(color_count)
            ],
        )
        conn.execute(
            t.DjmdGenre.__table__.insert(),
            [
                dict(
                    ID=str(3000 + i),
                    Name=f"Genre {i}",
                    UUID=_uid(),
                    usn=0,
                    rb_local_usn=0,
                )
                for i in range(genre_count)
            ],
        )

        content_rows = [
            _content_row(
                i,
                artist_id=str(1000 + i % artist_count),
                album_id=str(2000 + i % album_count),
                key_id=str((i % key_count) + 1),
                color_id=str(50 + i % color_count),
                genre_id=str(3000 + i % genre_count),
            )
            for i in range(track_count)
        ]
        conn.execute(t.DjmdContent.__table__.insert(), content_rows)

    engine.dispose()
    return db_path


def open_synthetic_db(db_dir: Path):
    """Open the synthetic DB via pyrekordbox's plain-SQLite mode (``unlock=False``)."""
    from pyrekordbox.db6 import Rekordbox6Database

    return Rekordbox6Database(path=str(Path(db_dir) / "master.db"), unlock=False)
