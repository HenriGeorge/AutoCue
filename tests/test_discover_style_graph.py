"""Tests for ``autocue.analysis.discover.style_graph``.

Covers all T-002 pass criteria:

- STYLE_ALIAS_MAP has ≥60 keys
- style_adjacency.default.json validates against schema
- Every anchor style has ≥3 edges
- Required-anchor styles (Drum & Bass, Footwork, Jungle, Garage UK, IDM) are anchors
- S3-8 fallback test: malformed user JSON → load returns default + warning set
- S3-9 unknown-style test: adjacent('nonexistent_style') returns []
"""

from __future__ import annotations

import json
import pytest

from autocue.analysis.discover.style_graph import (
    BUNDLED_DEFAULT_PATH,
    STYLE_ALIAS_MAP,
    StyleAdjacency,
    load_style_adjacency,
    normalize_style,
)


# --------------------------------------------------------------------------- #
# STYLE_ALIAS_MAP
# --------------------------------------------------------------------------- #

def test_alias_map_has_at_least_60_keys():
    assert len(STYLE_ALIAS_MAP) >= 60, (
        f"STYLE_ALIAS_MAP must have ≥60 entries (currently {len(STYLE_ALIAS_MAP)}) "
        f"per T-002 pass criteria"
    )


def test_alias_map_keys_are_pre_stripped_form():
    """Every key must be lowercase a-z0-9 only — i.e., what comes out of the
    normalize_style strip step. If a key contains spaces or hyphens, the lookup
    after stripping will miss it."""
    bad = [k for k in STYLE_ALIAS_MAP if not k.replace("_", "x").isalnum() or k != k.lower()]
    # Allow underscore-free; we strip non-alphanumerics including underscores.
    bad = [k for k in STYLE_ALIAS_MAP if any(not c.isalnum() for c in k)]
    assert not bad, f"alias-map keys must be pre-stripped (alnum only); offenders: {bad}"


def test_alias_map_values_match_canonical_keys():
    """Each canonical value must exist in the bundled default adjacency JSON.
    Otherwise the alias resolves to a key the adjacency graph doesn't know
    about and downstream lookups silently degrade."""
    payload = json.loads(BUNDLED_DEFAULT_PATH.read_text())
    canonical_keys = set(payload["styles"].keys())
    orphan_values = {v for v in STYLE_ALIAS_MAP.values() if v not in canonical_keys}
    assert not orphan_values, (
        f"alias-map values must reference canonical styles in style_adjacency.default.json; "
        f"orphans: {sorted(orphan_values)}"
    )


# --------------------------------------------------------------------------- #
# normalize_style
# --------------------------------------------------------------------------- #

class TestNormalizeStyle:
    def test_canonical_collapsing(self):
        # The headline T-002 pass-criterion case.
        assert normalize_style("Deep House") == "deep_house"
        assert normalize_style("deep-house") == "deep_house"
        assert normalize_style("deephouse") == "deep_house"
        assert normalize_style("DEEPHOUSE") == "deep_house"

    def test_drum_and_bass_aliases(self):
        for variant in ("Drum & Bass", "drum n bass", "DnB", "drumandbass", "DRUM-N-BASS"):
            assert normalize_style(variant) == "drum_and_bass", variant

    def test_garage_uk_aliases(self):
        for variant in ("UK Garage", "UKG", "garage UK", "Garage UK"):
            assert normalize_style(variant) == "garage_uk", variant

    def test_unknown_style_returns_stripped_form(self):
        """Unknown styles are NOT in the alias map. The function returns the
        stripped form so callers can still pass it to the adjacency graph,
        where it'll fall through to the unknown-style branch."""
        assert normalize_style("Vaporsynth") == "vaporsynth"

    @pytest.mark.parametrize("falsy", [None, "", "   ", "!!!"])
    def test_falsy_or_pure_punctuation_returns_none(self, falsy):
        assert normalize_style(falsy) is None

    def test_idempotent(self):
        # Running normalize twice on the same input gives the same result.
        for raw in ("Deep House", "TECH HOUSE", "Drum & Bass"):
            once = normalize_style(raw)
            twice = normalize_style(once)
            assert once == twice, f"{raw!r}: {once!r} → {twice!r}"


# --------------------------------------------------------------------------- #
# Bundled default JSON
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def default_adjacency() -> StyleAdjacency:
    result = load_style_adjacency()  # no user dir — pure-bundled mode
    return result.adjacency


class TestBundledDefault:
    def test_load_returns_default_source(self):
        result = load_style_adjacency()
        assert result.source == "default"
        assert result.warning is None

    def test_anchor_styles_have_at_least_three_edges(self, default_adjacency):
        anchors = {k for k in default_adjacency.known_styles() if default_adjacency.is_anchor(k)}
        assert anchors, "bundled default must contain at least one anchor style"
        offenders = {k: default_adjacency.adjacent(k) for k in anchors
                     if len(default_adjacency.adjacent(k)) < 3}
        assert not offenders, (
            f"anchor styles must have ≥3 edges per T-002 pass criteria; "
            f"offenders: {offenders}"
        )

    def test_terminal_styles_have_zero_to_two_edges(self, default_adjacency):
        terminals = {k for k in default_adjacency.known_styles()
                     if default_adjacency.is_terminal(k) and k in default_adjacency.styles}
        offenders = {k: default_adjacency.adjacent(k) for k in terminals
                     if len(default_adjacency.adjacent(k)) > 2}
        assert not offenders, f"terminal styles must have ≤2 edges; offenders: {offenders}"

    @pytest.mark.parametrize("required_anchor", [
        "drum_and_bass", "footwork", "jungle", "garage_uk", "idm",
    ])
    def test_required_anchors_present(self, default_adjacency, required_anchor):
        """T-002 pass-criterion call-outs: these styles MUST be anchors."""
        assert default_adjacency.is_anchor(required_anchor), (
            f"{required_anchor!r} must be an anchor style per T-002 pass criteria"
        )

    def test_no_dangling_edges(self, default_adjacency):
        """Every edge target must exist as a key in the same map."""
        known = default_adjacency.known_styles()
        dangling = {
            k: [e for e in default_adjacency.adjacent(k) if e not in known]
            for k in known
        }
        dangling = {k: refs for k, refs in dangling.items() if refs}
        assert not dangling, f"dangling edge references: {dangling}"


# --------------------------------------------------------------------------- #
# StyleAdjacency.adjacent — unknown-style handling (S3-9)
# --------------------------------------------------------------------------- #

class TestUnknownStyleHandling:
    def test_unknown_style_returns_empty_list(self, default_adjacency):
        """The PRD-locked contract: unknown styles never KeyError; they degrade
        gracefully to an empty adjacency list so the novelty feeder can decide
        it's sparse-adjacency for this user."""
        assert default_adjacency.adjacent("nonexistent_style") == []

    def test_unknown_style_is_terminal(self, default_adjacency):
        """Treating absence as terminal mirrors the design: a style we don't
        know about by definition can't contribute novelty."""
        assert default_adjacency.is_terminal("nonexistent_style") is True

    def test_unknown_style_is_not_anchor(self, default_adjacency):
        assert default_adjacency.is_anchor("nonexistent_style") is False


# --------------------------------------------------------------------------- #
# Fallback behavior (S3-8) — malformed user JSON
# --------------------------------------------------------------------------- #

class TestFirstRunPath:
    def test_first_run_copies_default_to_user_dir(self, tmp_path):
        """When neither the user file nor a .bak exists, the loader silently
        materializes the default at the user-editable path. No warning."""
        result = load_style_adjacency(user_data_dir=tmp_path)
        assert result.source == "first_run_copy"
        assert result.warning is None
        assert (tmp_path / "style_adjacency.json").exists()
        # Content matches bundled default.
        assert json.loads((tmp_path / "style_adjacency.json").read_text()) == \
               json.loads(BUNDLED_DEFAULT_PATH.read_text())


class TestMalformedFallback:
    def test_invalid_json_falls_back_with_warning_and_bak(self, tmp_path):
        """T-002 S3-8: malformed JSON → default + warning + user file preserved as .bak."""
        bad = tmp_path / "style_adjacency.json"
        bad.write_text("{ not valid json")
        result = load_style_adjacency(user_data_dir=tmp_path)
        assert result.source == "default"
        assert result.warning is not None
        assert "not valid JSON" in result.warning
        assert (tmp_path / "style_adjacency.json.bak").exists()
        assert not bad.exists(), "bad file must be moved to .bak, not left in place"

    def test_schema_violation_falls_back_with_warning_and_bak(self, tmp_path):
        """Schema violation (dangling edge here) → default + warning + .bak."""
        bad_payload = {
            "schema_version": 1,
            "styles": {
                "fake_style": {"edges": ["does_not_exist"], "terminal": False},
            },
        }
        (tmp_path / "style_adjacency.json").write_text(json.dumps(bad_payload))
        result = load_style_adjacency(user_data_dir=tmp_path)
        assert result.source == "default"
        assert result.warning is not None
        assert "schema validation" in result.warning
        assert (tmp_path / "style_adjacency.json.bak").exists()

    def test_missing_required_field_falls_back(self, tmp_path):
        bad_payload = {"styles": {"deep_house": {"edges": [], "terminal": True}}}
        (tmp_path / "style_adjacency.json").write_text(json.dumps(bad_payload))
        result = load_style_adjacency(user_data_dir=tmp_path)
        assert result.source == "default"
        assert "missing required top-level keys" in (result.warning or "")

    def test_server_does_not_crash_on_any_failure(self, tmp_path):
        """The PRD contract: 'server never crashes on bad JSON; Discover always
        works at minimum default-quality.' Verify by trying every failure shape
        the loader handles."""
        for shape in (
            "",                    # empty file
            "null",                # not an object
            "[]",                  # array, not object
            '{"foo": "bar"}',      # missing required fields
            '{"schema_version": "1", "styles": {}}',  # wrong type for schema_version
        ):
            (tmp_path / "style_adjacency.json").write_text(shape)
            # Should not raise.
            result = load_style_adjacency(user_data_dir=tmp_path)
            assert result.source == "default", f"shape {shape!r} did not fall back cleanly"
            # Restore the .bak rename so the next iteration hits the same path.
            bak = tmp_path / "style_adjacency.json.bak"
            if bak.exists():
                bak.unlink()


# --------------------------------------------------------------------------- #
# Coverage requirement: bundled default must have enough styles
# --------------------------------------------------------------------------- #

def test_bundled_default_has_at_least_60_styles():
    """Per the PRD: 'Tier 1 ships ~60 styles in the default JSON (~30 anchor + ~30 terminal).'"""
    payload = json.loads(BUNDLED_DEFAULT_PATH.read_text())
    assert len(payload["styles"]) >= 60, (
        f"bundled default must ship ≥60 styles for Tier 1 "
        f"(currently {len(payload['styles'])})"
    )
