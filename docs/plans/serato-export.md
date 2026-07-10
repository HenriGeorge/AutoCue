# Serato export — design (GATE 1)

Write AutoCue's generated hot cues into audio files as Serato DJ Pro tags, so the
same cue prep serves both Rekordbox and Serato. Approved shape (user, 2026-07-10):
"one new writer module plus a CLI flag, testable against copied files".

## CLI interface

```
autocue --library --serato              # write Serato cue tags into the audio files
autocue --track "Title" --serato
autocue --library --serato --dry-run    # preview only, nothing written
autocue --library --serato --overwrite  # replace files' existing Serato cues
```

- `--serato` switches the output backend: Serato tags are written **into the
  audio files** instead of producing the Rekordbox XML. All existing targeting
  flags (`--track`, `--track-id`, `--library`, `--playlist`, `--dry-run`,
  `--overwrite`) compose unchanged.
- Without `--overwrite`, files that already carry Serato cue tags are skipped
  (mirrors the Rekordbox skip-if-cued behaviour).
- Serato DJ must be closed: refuse to write if a `Serato DJ` process is running
  (psutil, mirrors the `_rb_running` guard for Rekordbox).

## Safety model

- Writes touch **metadata tags only** (mutagen); the audio stream is untouched.
- Before replacing any existing Serato tag, its original payload is appended to
  `autocue_serato_backup.jsonl` next to the working directory:
  `{path, tag, base64_payload, ts}` — enough to hand-restore any file.
- Unsupported containers (WAV, OGG in v1) are skipped with a per-file notice
  and counted in the summary.

## Module

`autocue/serato_writer.py`
- pure serializer: `build_markers2(cues: list[CuePoint]) -> bytes` (+ inverse
  `parse_markers2` used by tests and skip-detection)
- embedding: `write_serato_tags(path, payload)` dispatching on suffix —
  MP3/AIFF → ID3 GEOB `Serato Markers2`; FLAC → vorbis `SERATO_MARKERS_V2`;
  M4A → freeform atom
- orchestration: `write_serato(tracks, *, overwrite, backup_path) -> SeratoSummary`
- Dependency `mutagen` as optional extra `autocue[serato]`; import guarded with
  an actionable install hint.

## Cue mapping

- slots 0–7 → Serato cue indexes 0–7 (A–H); memory cues (slot -1) are dropped
  (Serato has no equivalent).
- names pass through; colors: DjmdColor id → nearest Serato palette RGB
  (Green→#28E214-family per researched palette table).

## Out of scope (v1)

Beatgrids, loops, waveform/overview tags, Serato database/crates (tags-only is
sufficient for Serato to show cues), WAV/OGG containers, server/UI surface.
