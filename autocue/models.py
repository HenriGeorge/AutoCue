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


@dataclass
class CuePoint:
    position_ms: int
    label: PhraseLabel
    # hot cue slot: 0–7 (A–H), or -1 for memory cue — matches Rekordbox XML Num field directly
    slot: int

    @property
    def position_sec(self) -> float:
        return self.position_ms / 1000.0

    @property
    def slot_name(self) -> str:
        return chr(ord("A") + self.slot) if self.slot >= 0 else "Mem"
