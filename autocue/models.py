from dataclasses import dataclass
from enum import Enum


class PhraseLabel(Enum):
    INTRO = "Intro"
    VERSE = "Verse"
    BRIDGE = "Bridge"
    CHORUS = "Chorus"
    OUTRO = "Outro"
    UP = "Up"
    DOWN = "Down"
    UNKNOWN = "?"


# kind integer → PhraseLabel for each Rekordbox mood value
_KIND_MAP: dict[int, dict[int, PhraseLabel]] = {
    # mood 3 = Low
    3: {
        1: PhraseLabel.INTRO,
        2: PhraseLabel.VERSE,
        3: PhraseLabel.VERSE,
        4: PhraseLabel.VERSE,
        5: PhraseLabel.VERSE,
        6: PhraseLabel.VERSE,
        7: PhraseLabel.VERSE,
        8: PhraseLabel.BRIDGE,
        9: PhraseLabel.CHORUS,
        10: PhraseLabel.OUTRO,
    },
    # mood 2 = Mid
    2: {
        1: PhraseLabel.INTRO,
        2: PhraseLabel.VERSE,
        3: PhraseLabel.VERSE,
        4: PhraseLabel.VERSE,
        5: PhraseLabel.VERSE,
        6: PhraseLabel.VERSE,
        7: PhraseLabel.VERSE,
        8: PhraseLabel.BRIDGE,
        9: PhraseLabel.CHORUS,
        10: PhraseLabel.OUTRO,
    },
    # mood 1 = High
    1: {
        1: PhraseLabel.INTRO,
        2: PhraseLabel.UP,
        3: PhraseLabel.DOWN,
        5: PhraseLabel.CHORUS,
        6: PhraseLabel.OUTRO,
    },
}


def phrase_label(mood: int, kind: int) -> PhraseLabel:
    return _KIND_MAP.get(mood, {}).get(kind, PhraseLabel.UNKNOWN)


# DJ-friendly display names written into Rekordbox cue labels.
# CHORUS→Drop, UP→Build, DOWN→Break reflect EDM conventions where
# Rekordbox's PSSI "Chorus" is the drop and "Up"/"Down" are build/breakdown.
DJ_NAMES: dict[PhraseLabel, str] = {
    PhraseLabel.INTRO:   "Intro",
    PhraseLabel.VERSE:   "Verse",
    PhraseLabel.BRIDGE:  "Bridge",
    PhraseLabel.CHORUS:  "Drop",
    PhraseLabel.OUTRO:   "Outro",
    PhraseLabel.UP:      "Build",
    PhraseLabel.DOWN:    "Break",
    PhraseLabel.UNKNOWN: "",
}


# DjmdColor table IDs: 1=Pink 2=Red 3=Orange 4=Yellow 5=Green 6=Aqua 7=Blue 8=Purple
SLOT_COLORS: list[int] = [5, 7, 6, 3, 2, 2, 1, 8]  # slots A→H

LABEL_COLORS: dict[str, int] = {
    "Intro":   5,  # Green
    "Verse":   7,  # Blue
    "Bridge":  6,  # Aqua
    "Chorus":  2,  # Red  (Rekordbox "Chorus" maps to Drop in EDM)
    "Outro":   3,  # Orange
    "Up":      1,  # Pink (Build)
    "Down":    8,  # Purple (Break)
}


@dataclass
class CuePoint:
    position_ms: int
    label: PhraseLabel
    # hot cue slot: 0–7 (A–H), or -1 for memory cue — matches Rekordbox XML Num field directly
    slot: int
    # DJ-friendly text written to Rekordbox. Empty = fall back to label.value.
    name: str = ""
    # DjmdColor table ID: 0=none, 1=Pink, 2=Red, 3=Orange, 4=Yellow, 5=Green, 6=Aqua, 7=Blue, 8=Purple
    color_id: int = 0
    # Source reliability: 1.0=phrase+beat, 0.6=bar-interval, 0.3=heuristic
    confidence: float = 1.0
    # Number of bars in the phrase (PSSI-derived); 0 = unknown (bar/heuristic modes)
    phrase_bars: int = 0

    @property
    def position_sec(self) -> float:
        return self.position_ms / 1000.0

    @property
    def slot_name(self) -> str:
        return chr(ord("A") + self.slot) if self.slot >= 0 else "Mem"
