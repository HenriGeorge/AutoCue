-- Discover v2 initial schema (v0.5 — staging columns, NULLABLE artist/title,
-- per-scan_id boot recovery, unknown_styles on scans).
-- See PRD §6.7 for the contract.

CREATE TABLE schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT    NOT NULL
);

-- State tables ──────────────────────────────────────────────────────────────
-- release_key is the source of truth for curation lookups.
-- release_key_version captures the normalize_release_key() version that
-- produced the key; future re-normalization runs as a migration.
-- artist/title are NULLABLE — store CRUD coerces empty/null to
-- "Unknown Artist"/"Unknown Title" at insert time (S4-3). The coerced value
-- is for display only; never feeds back into release_key.

CREATE TABLE saved (
    release_key         TEXT    PRIMARY KEY,
    release_key_version INTEGER NOT NULL DEFAULT 1,
    release_id          INTEGER NOT NULL,
    artist              TEXT,
    title               TEXT,
    label               TEXT,
    saved_at            TEXT    NOT NULL
);

CREATE TABLE dismissed (
    release_key         TEXT    PRIMARY KEY,
    release_key_version INTEGER NOT NULL DEFAULT 1,
    release_id          INTEGER,
    artist              TEXT,
    title               TEXT,
    dismissed_at        TEXT    NOT NULL,
    reason              TEXT
);

CREATE TABLE snoozed (
    release_key         TEXT    PRIMARY KEY,
    release_key_version INTEGER NOT NULL DEFAULT 1,
    release_id          INTEGER,
    artist              TEXT,
    title               TEXT,
    snoozed_at          TEXT    NOT NULL,
    until_date          TEXT    NOT NULL
);

CREATE TABLE downloaded (
    release_key         TEXT    PRIMARY KEY,
    release_key_version INTEGER NOT NULL DEFAULT 1,
    release_id          INTEGER,
    artist              TEXT,
    title               TEXT,
    downloaded_at       TEXT    NOT NULL,
    -- file_paths is ALWAYS JSON-encoded list (even for single files);
    -- record_download() wraps; future re-normalization merges safely.
    file_paths          TEXT
);

-- Block-lists ───────────────────────────────────────────────────────────────

CREATE TABLE blocked_artists (
    discogs_artist_id INTEGER PRIMARY KEY,
    name              TEXT,
    blocked_at        TEXT
);

CREATE TABLE blocked_labels (
    discogs_label_id INTEGER PRIMARY KEY,
    name             TEXT,
    blocked_at       TEXT
);

-- Watch tables with staging columns ─────────────────────────────────────────
-- last_scanned_at        — committed value; TTL gate reads this
-- last_scanned_at_pending — in-flight scan writes here
-- pending_scan_id        — scan_id that wrote the pending value
-- Boot recovery clears (pending, scan_id) when pending_scan_id refers to a
-- crashed scan. Per-scan_id semantics — interleaved successful scans on the
-- same entity never get rolled back (S4-2).

CREATE TABLE followed_labels (
    label_id                INTEGER PRIMARY KEY,
    name                    TEXT    NOT NULL,
    added_at                TEXT    NOT NULL,
    last_scanned_at         TEXT,
    last_scanned_at_pending TEXT,
    pending_scan_id         INTEGER,
    health                  TEXT,
    consecutive_errors      INTEGER DEFAULT 0,
    current_name_check_at   TEXT
);

CREATE TABLE followed_shops (
    shop_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name            TEXT    NOT NULL,
    source_type             TEXT    NOT NULL,  -- 'discogs' | 'rss' | 'bandcamp' | 'manual'
    source_url              TEXT    NOT NULL,
    added_at                TEXT    NOT NULL,
    last_scanned_at         TEXT,
    last_scanned_at_pending TEXT,
    pending_scan_id         INTEGER,
    health                  TEXT,
    consecutive_errors      INTEGER DEFAULT 0
);

-- Telemetry ────────────────────────────────────────────────────────────────

CREATE TABLE scans (
    scan_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at             TEXT    NOT NULL,
    finished_at            TEXT,                                    -- NULL while running
    status                 TEXT    NOT NULL DEFAULT 'running',      -- running | ok | cancelled | rate_limited | crashed
    feeders                TEXT,
    novelty_strategy       TEXT,                                    -- style | label | artist
    novelty_status         TEXT,                                    -- ok | sparse_adjacency | partial
    unknown_styles         TEXT,                                    -- JSON list
    duration_ms            INTEGER,
    requests_used          INTEGER,
    releases_seen          INTEGER,
    releases_after_dedup   INTEGER,
    releases_surfaced      INTEGER
);

-- Caches ────────────────────────────────────────────────────────────────────

CREATE TABLE release_details (
    release_id   INTEGER PRIMARY KEY,
    payload_json TEXT,
    fetched_at   TEXT,
    expires_at   TEXT
);

CREATE TABLE youtube_results (
    release_key  TEXT,
    track_index  INTEGER,
    results_json TEXT,
    fetched_at   TEXT,
    PRIMARY KEY (release_key, track_index)
);

-- Tier 3 reserved ───────────────────────────────────────────────────────────

CREATE TABLE friends (
    discogs_username TEXT PRIMARY KEY,
    alias            TEXT,
    added_at         TEXT
);
