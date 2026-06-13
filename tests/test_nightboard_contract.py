"""P4 Nightboard — backend schema contract pin.

Nightboard adds NO backend endpoint in v1; it renders entirely off the existing
setbuilder / transitions / alternatives / energy REST shapes. These tests pin
the specific fields the canvas, joints, popover, swap and tray read, so a future
backend refactor that drops one (e.g. `explanation`, `from_prev`/`to_next`,
`transition_score`) fails loudly here instead of silently breaking the canvas.

Style mirrors tests/test_duplicates_integration.py's schema pin.
"""
from autocue.serve import schemas


def _fields(model):
    return set(model.model_fields.keys())


def test_setbuilder_track_item_fields():
    # tiles + initial joint scores read these (no /api/transitions/score on paint)
    required = {"track_id", "title", "artist", "bpm", "key", "category", "transition_score", "relaxed"}
    missing = required - _fields(schemas.SetBuilderTrackItem)
    assert not missing, f"SetBuilderTrackItem dropped {missing} — Nightboard tiles/joints depend on them"


def test_setbuilder_response_fields():
    required = {"tracks", "total_tracks", "estimated_duration_minutes", "terminated_reason"}
    missing = required - _fields(schemas.SetBuilderResponse)
    assert not missing, f"SetBuilderResponse dropped {missing} — stats strip / R3 notice depend on them"


def test_transition_response_keeps_explanation():
    # the joint popover shows the three explanation strings verbatim
    required = {"track_a_id", "track_b_id", "overall", "explanation"}
    missing = required - _fields(schemas.TransitionResponse)
    assert not missing, f"TransitionResponse dropped {missing} — joint popover reasons + re-score depend on them"


def test_set_alternative_item_keeps_neighbour_scores():
    # swap alternatives + gravity tray candidates
    required = {"track_id", "title", "artist", "bpm", "key", "score", "from_prev", "to_next", "genre_match"}
    missing = required - _fields(schemas.SetAlternativeItem)
    assert not missing, f"SetAlternativeItem dropped {missing} — swap/tray depend on them"


def test_set_alternatives_response_carries_alternatives():
    assert "alternatives" in _fields(schemas.SetAlternativesResponse)


def test_energy_response_carries_curve():
    # the set-wide arc + tile sparklines read EnergyResponse.energy
    assert "energy" in _fields(schemas.EnergyResponse)


def test_create_playlist_response_shape():
    # export delegates to POST /api/playlists and reads name + track_count
    fields = _fields(schemas.CreatePlaylistResponse)
    assert {"name", "track_count"} <= fields, f"CreatePlaylistResponse missing name/track_count: {fields}"
