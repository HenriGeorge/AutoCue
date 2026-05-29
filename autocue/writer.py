"""
Writes CuePoints to a Rekordbox XML file for import via
File > Import Library in Rekordbox.

Color is not supported at the per-cue level in the XML format — it is a
track-level attribute only. For per-cue colors, use direct DB write mode
(see db_writer.py, planned).
"""
from __future__ import annotations

import os
from pathlib import Path

from pyrekordbox.db6 import DjmdContent
from pyrekordbox.rbxml import RekordboxXml

from .models import CuePoint


def write_xml(
    tracks: list[tuple[DjmdContent, list[CuePoint]]],
    output_path: str | Path,
) -> Path:
    """
    Write cue points for one or more tracks to a Rekordbox XML file.

    Import the resulting file in Rekordbox via:
        File > Import Library > select the XML file

    Returns the resolved output path.
    """
    output_path = Path(output_path).resolve()
    xml = RekordboxXml()

    for content, cues in tracks:
        file_path = _resolve_file_path(content)
        track = xml.add_track(Location=file_path)

        if content.Title:
            track.set("Name", content.Title)
        if content.ArtistName:
            track.set("Artist", content.ArtistName)

        for cue in cues:
            # Num: -1 = memory cue, 0–7 = Hot Cues A–H (slot already matches wire format)
            track.add_mark(
                Name=cue.label.value,
                Type="cue",
                Start=cue.position_sec,
                Num=cue.slot,
            )

    xml.save(str(output_path))
    return output_path


def _resolve_file_path(content: DjmdContent) -> str:
    """Build the absolute file path for a track from its DjmdContent record."""
    folder = content.FolderPath or ""
    filename = content.FileNameL or content.FileNameS or ""
    return os.path.join(folder, filename)
