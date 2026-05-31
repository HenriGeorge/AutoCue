from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


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


class GenerateRequest(BaseModel):
    track_ids: list[int]
    mode: Literal["phrase", "bar", "auto"] = "auto"
    bars_interval: int = 16
    start_bar: int = 1
    max_cues: int = 8
    add_memory_cue: bool = False


class CueItem(BaseModel):
    slot: int
    label: str
    position_ms: int
    is_phrase: bool = False
    name: str = ""
    color_id: int = 0


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
    add_memory_cue: bool = False
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


class ColorTracksResponse(BaseModel):
    colored: int
    skipped: int
    dry_run: bool
    backup_path: str | None
