from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


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
                   # Must be non-zero. Cues that would shift to < 0 ms are skipped.

    @field_validator("delta_ms")
    @classmethod
    def _nonzero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("delta_ms must not be zero")
        return v


class CueDeleteOrphanParams(BaseModel):
    keep_slots: int  # 1–8; delete hot cues whose Kind > keep_slots (i.e. slots beyond this count)


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
    dry_run: bool
    backup_path: str | None = None


# ---------------------------------------------------------------------------
# Set Builder
# ---------------------------------------------------------------------------

class PlaylistSuggestRequest(BaseModel):
    category: str  # "warmup" | "build" | "peak" | "after_hours" | "closing"
    count: int = 20
    exclude_ids: list[int] = []
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


class SetBuilderTrackItem(BaseModel):
    track_id: int
    title: str
    artist: str
    bpm: float
    key: str
    category: str
    transition_score: float | None = None


class SetBuilderResponse(BaseModel):
    tracks: list[SetBuilderTrackItem]
    total_tracks: int
    estimated_duration_minutes: float
