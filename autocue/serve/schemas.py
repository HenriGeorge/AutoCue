from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class StatusResponse(BaseModel):
    connected: bool
    rekordbox_version: str | None = None
    track_count: int


class PlaylistItem(BaseModel):
    id: int
    name: str
    track_count: int


class TrackItem(BaseModel):
    id: int
    title: str
    artist: str
    album: str
    bpm: float
    duration: float
    has_phrase: bool
    has_beats: bool
    existing_hot_cues: int
    key: str = ""
    rating: int = 0
    play_count: int = 0
    last_played: str | None = None
    my_tags: list[str] = []
    color_name: str = ""
    genre: str = ""
    comment: str = ""


class GenerateRequest(BaseModel):
    track_ids: list[int]
    mode: Literal["phrase", "bar", "auto"] = "auto"
    bars_interval: int = 16
    start_bar: int = 1
    max_cues: int = 8
    add_memory_cue: bool = False  # legacy alias — memory_cue_mode takes precedence
    memory_cue_mode: Literal["none", "load_only", "all"] = "none"
    add_fill_cues: bool = False


class CueItem(BaseModel):
    slot: int
    label: str
    position_ms: int
    is_phrase: bool = False
    name: str = ""
    color_id: int = 0
    confidence: float = 1.0
    phrase_bars: int = 0


class TrackResult(BaseModel):
    id: int
    title: str
    cues: list[CueItem]
    mode_used: str
    skipped: bool


class GenerateResponse(BaseModel):
    tracks: list[TrackResult]


class ApplyRequest(BaseModel):
    tracks: list[TrackResult]
    overwrite: bool = False
    dry_run: bool = False


class GenerateAndApplyRequest(BaseModel):
    track_ids: list[int]
    mode: Literal["phrase", "bar", "auto"] = "auto"
    bars_interval: int = 16
    start_bar: int = 1
    max_cues: int = 8
    add_memory_cue: bool = False  # legacy alias — memory_cue_mode takes precedence
    memory_cue_mode: Literal["none", "load_only", "all"] = "none"
    add_fill_cues: bool = False
    overwrite: bool = False
    dry_run: bool = False
    phrase_only: bool = False


class BackupItem(BaseModel):
    path: str
    filename: str
    size_mb: float
    created_at: str


class RestoreRequest(BaseModel):
    filename: str


class RestoreResponse(BaseModel):
    restored: bool
    message: str


class ApplyResponse(BaseModel):
    applied: int
    skipped: int
    dry_run: bool
    backup_path: str | None


class DeleteRequest(BaseModel):
    track_ids: list[int]
    dry_run: bool = False


class DeleteResponse(BaseModel):
    deleted: int
    tracks_affected: int
    dry_run: bool
    backup_path: str | None


class ColorTracksRequest(BaseModel):
    track_ids: list[int]
    dry_run: bool = False
    skip_colored: bool = False


class ColorTracksResponse(BaseModel):
    colored: int
    skipped: int
    dry_run: bool
    backup_path: str | None


# ---------------------------------------------------------------------------
# Cue Quality Checker
# ---------------------------------------------------------------------------

class EnergyResponse(BaseModel):
    track_id: int
    energy: list[float] | None = None  # None = PWAV tag unavailable
    n_points: int = 0
    energy_profile: str | None = None  # "flat" | "build" | "drop-then-flat" | "wave"


class MixabilityComponents(BaseModel):
    intro: int
    outro: int
    energy: int
    vocals: int
    structure: int


class MixabilityResponse(BaseModel):
    track_id: int
    score: int | None = None          # None = no phrase data
    intro_bars: int = 0
    outro_bars: int = 0
    phrase_count: int = 0
    vocal_proxy: bool = False
    energy_variance: float | None = None
    outro_length_unknown: bool = False
    components: MixabilityComponents | None = None


class ClassificationResponse(BaseModel):
    track_id: int
    primary: str          # "warmup" | "build" | "peak" | "after_hours" | "closing" | "unknown"
    label: str            # human-friendly display name
    color: str            # hex colour for UI chip
    confidence: float     # score of the primary category
    scores: dict[str, float]
    bpm: float
    energy_mean: float | None = None
    energy_peak: float | None = None
    vocal_proxy: bool = False


class SimilarTrackItem(BaseModel):
    track_id: int
    score: float          # cosine similarity 0.0–1.0
    bpm_diff: float       # |target_bpm - candidate_bpm|


class SimilarTracksResponse(BaseModel):
    track_id: int
    results: list[SimilarTrackItem]


class TransitionRequest(BaseModel):
    track_a_id: int
    track_b_id: int


class TransitionResponse(BaseModel):
    track_a_id: int
    track_b_id: int
    overall: float
    bpm: float
    key: float
    energy: float
    bpm_a: float
    bpm_b: float
    key_a: str
    key_b: str
    end_energy_a: float | None = None
    start_energy_b: float | None = None
    explanation: list[str] = []


# ---------------------------------------------------------------------------
# Cue Quality Checker
# ---------------------------------------------------------------------------

class CueIssueSchema(BaseModel):
    code: str
    severity: str  # "error" | "warning" | "info"
    message: str


class TrackHealthReport(BaseModel):
    track_id: int
    score: int  # 0–100; 0 means NO_AUDIO_FILE
    issues: list[CueIssueSchema] = []
    fix_tier: str  # "phrase" | "bar" | "heuristic" | "none"
    hot_cue_count: int = 0
    memory_cue_count: int = 0


class LibraryHealthSummary(BaseModel):
    total: int
    excluded_missing_audio: int
    library_score: float  # mean of non-missing-audio tracks, 0.0 if none
    no_cues: int
    no_phrase: int
    no_beatgrid: int
    duplicate_cues: int
    unnamed_cues: int
    no_memory_cue: int
    fix_tier_counts: dict[str, int]  # {"phrase": N, "bar": N, "heuristic": N, "none": N}


# ---------------------------------------------------------------------------
# Cue Library Tools
# ---------------------------------------------------------------------------

class CueRenameParams(BaseModel):
    from_name: str   # exact, case-sensitive match against DjmdCue.Comment
    to_name: str


class CueRecolorParams(BaseModel):
    # Maps slot index string ("0"–"7") to ColorTableIndex (0=none,1=Pink,2=Red,
    # 3=Orange,4=Yellow,5=Green,6=Aqua,7=Blue,8=Purple). Slots absent from the
    # mapping are left unchanged.
    slot_colors: dict[str, int]


class CueShiftParams(BaseModel):
    delta_ms: int  # positive = shift later, negative = shift earlier
    negative_policy: Literal["skip", "clamp_to_zero", "abort_track"] = "abort_track"
    # abort_track (default): if ANY cue on a track would go negative, leave the whole
    # track untouched — preserves internal cue-set consistency.
    # skip: silently drop cues that would go negative, shift the rest.
    # clamp_to_zero: place cues that would go negative at 0 ms instead.

    @field_validator("delta_ms")
    @classmethod
    def _nonzero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("delta_ms must not be zero")
        return v


class CueDeleteOrphanParams(BaseModel):
    keep_slots: int = Field(..., ge=1, le=8)  # delete hot cues whose Kind > keep_slots


class CueToolsRequest(BaseModel):
    operation: Literal["rename", "recolor", "shift", "delete_orphan"]
    track_ids: list[int]
    dry_run: bool = False
    rename: CueRenameParams | None = None
    recolor: CueRecolorParams | None = None
    shift: CueShiftParams | None = None
    delete_orphan: CueDeleteOrphanParams | None = None

    @model_validator(mode="after")
    def _params_present(self):
        required = {"rename": self.rename, "recolor": self.recolor,
                    "shift": self.shift, "delete_orphan": self.delete_orphan}
        if required[self.operation] is None:
            raise ValueError(f"params for '{self.operation}' must be provided")
        return self


class CueToolsSummary(BaseModel):
    operation: str
    tracks_processed: int
    tracks_affected: int
    cues_changed: int
    cues_skipped: int
    skip_reasons: dict[str, int] = {}
    # Stable reason keys: "would_be_negative" (shift/skip policy), "no_match" (rename/recolor),
    # "track_aborted" (shift/abort_track policy), "beyond_keep_slots" (delete_orphan)
    dry_run: bool
    backup_path: str | None = None


# ---------------------------------------------------------------------------
# Set Builder
# ---------------------------------------------------------------------------

class PlaylistSuggestRequest(BaseModel):
    category: str  # "warmup" | "build" | "peak" | "after_hours" | "closing"
    count: int = 20
    exclude_ids: list[int] = []
    seed_track_ids: list[int] = []  # pre-included tracks; bypass exclude_ids
    playlist_id: int | None = None  # scope to a specific playlist, or None = full library


class PlaylistSuggestItem(BaseModel):
    track_id: int
    score: float  # category score 0.0–1.0


class PlaylistSuggestResponse(BaseModel):
    category: str
    results: list[PlaylistSuggestItem]


# ---------------------------------------------------------------------------
# Set Builder
# ---------------------------------------------------------------------------

class SetBuilderRequest(BaseModel):
    start_bpm: float = 110.0
    end_bpm: float = 135.0
    duration_minutes: float = 60.0
    energy_mode: Literal["build", "flat", "drop"] = "build"
    bpm_step_max: float = 0.08
    seed_track_id: int | None = None
    anchor_track_ids: list[int] = []  # must-include tracks, merged at BPM-sorted positions


class SetBuilderTrackItem(BaseModel):
    track_id: int
    title: str
    artist: str
    bpm: float
    key: str
    category: str
    transition_score: float | None = None
    mix_advice: str | None = None
    relaxed: bool = False  # True if this track was placed via relaxed constraints


class SetBuilderResponse(BaseModel):
    tracks: list[SetBuilderTrackItem]
    total_tracks: int
    estimated_duration_minutes: float
    terminated_reason: Literal[
        "target_duration_reached",
        "no_candidates_passed_thresholds",
        "safety_cap_hit",
    ] = "target_duration_reached"


class AutoTagRequest(BaseModel):
    track_ids: list[int]
    tag_types: list[str] = ["category"]  # subset of: category, vocal, energy_level, energy_profile, intro_outro
    overwrite: bool = True
    dry_run: bool = False


class AutoTagUndoData(BaseModel):
    removed: list[dict] = []
    added: list[str] = []


class AutoTagResponse(BaseModel):
    tagged: int
    skipped_no_data: int = 0
    errors: int
    dry_run: bool
    undo_data: AutoTagUndoData | None = None


class AutoTagUndoRequest(BaseModel):
    undo_data: AutoTagUndoData


class AutoTagUndoResponse(BaseModel):
    removed: int
    restored: int


class DiscogsTagRequest(BaseModel):
    track_ids: list[int]
    token: str  # Discogs personal access token
    dry_run: bool = False
    skip_existing: bool = False  # skip tracks that already have non-AutoCue My Tags (Discogs styles)


class DiscogsTagEvent(BaseModel):
    processed: int
    total: int
    track_id: int | None = None
    artist: str | None = None
    title: str | None = None
    styles: list[str] = []
    error: str | None = None
    done: bool = False
    tagged: int = 0
    skipped: int = 0
    errors: int = 0


# ---------------------------------------------------------------------------
# Set Builder — playlist export + alternatives
# ---------------------------------------------------------------------------

class CreatePlaylistRequest(BaseModel):
    name: str
    track_ids: list[int]


class CreatePlaylistResponse(BaseModel):
    playlist_id: int
    name: str
    track_count: int


class SetAlternativeItem(BaseModel):
    track_id: int
    title: str
    artist: str
    bpm: float
    key: str
    score: float          # combined fit score
    from_prev: float | None = None  # transition score from previous track
    to_next: float | None = None    # transition score to next track
    genre: str = ""
    genre_match: bool | None = None  # True = matches reference genre, False = mismatch, None = unknown


class SetAlternativesResponse(BaseModel):
    alternatives: list[SetAlternativeItem]


# ---------------------------------------------------------------------------
# Comment enrichment
# ---------------------------------------------------------------------------

class EnrichCommentsRequest(BaseModel):
    track_ids: list[int]
    overwrite: bool = False
    dry_run: bool = False


class EnrichCommentsResponse(BaseModel):
    enriched: int
    skipped: int
    errors: int
    dry_run: bool
    backup_path: str | None = None


class CommentPreviewRequest(BaseModel):
    track_id: int


class CommentPreviewResponse(BaseModel):
    track_id: int
    current_comment: str
    preview: str  # what the comment would become after enrichment


# ---------------------------------------------------------------------------
# Discovery — new releases from library artists (Discogs)
# ---------------------------------------------------------------------------

class DiscoverItem(BaseModel):
    """One suggested new release (also the SSE event payload)."""
    processed: int
    total: int
    artist: str | None = None
    album: str | None = None
    title: str | None = None
    year: int | None = None
    thumb: str | None = None
    cover: str | None = None
    genres: list[str] = []
    styles: list[str] = []
    formats: list[str] = []        # Discogs format tags e.g. ["Vinyl","LP","Album"]
    url: str | None = None
    query: str | None = None       # ready-made "artist album" download query
    done: bool = False
    suggested: int = 0


# ---------------------------------------------------------------------------
# Download — YouTube audio via yt-dlp (optional dependency)
# ---------------------------------------------------------------------------

class DownloadConfigResponse(BaseModel):
    available: bool          # yt-dlp importable
    ffmpeg: bool             # ffmpeg on PATH (needed for audio extraction)
    default_dir: str
    music_folder: str | None = None  # detected Rekordbox music root (common ancestor of FolderPath)


class DownloadRequest(BaseModel):
    query: str               # a YouTube URL or a search term ("artist - title")
    dest_dir: str | None = None
    audio_format: str = "mp3"


class DownloadTrackSpec(BaseModel):
    query: str
    title: str | None = None


class DownloadAlbumRequest(BaseModel):
    tracks: list[DownloadTrackSpec]
    dest_dir: str | None = None
    audio_format: str = "mp3"


class DownloadEvent(BaseModel):
    """SSE event for a download in progress."""
    processed: int = 0
    total: int = 1
    query: str | None = None
    title: str | None = None
    percent: float | None = None
    status: str | None = None     # "downloading" | "extracting" | "finished" | "error"
    path: str | None = None
    error: str | None = None
    done: bool = False
    downloaded: int = 0
    failed: int = 0
