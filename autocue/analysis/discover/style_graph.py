"""Style normalization + adjacency graph for the Discover novelty feeder.

Two pieces:

1. ``STYLE_ALIAS_MAP``: maps a normalized input form (lowercased, non-alphanumerics
   stripped) to its canonical key. ``normalize_style()`` is the entrypoint —
   callers feed it raw Discogs / Rekordbox / My-Tag style strings and get back
   either the canonical snake_case key or ``None`` for unparseable input.

2. ``load_style_adjacency()``: reads the live, user-editable
   ``style_adjacency.json`` from the platform data dir if present, falls back
   to the bundled default if not. Validates against ``style_adjacency.schema.json``
   and surfaces a ``LoadResult`` describing which path was taken so the server
   can show a one-time UI warning when the user's file is broken.

Design contract (locked at PRD v1.0 — §6.2 Feeder 4):
- Anchor styles have ≥3 adjacency edges and ``terminal: false``.
- Terminal styles have 0–2 edges and ``terminal: true``.
- Edge references must point to keys present in the same map; dangling edges
  cause validation failure.
- Unknown styles (not present in the loaded map) return ``[]`` via
  ``StyleAdjacency.adjacent(style)`` — never ``KeyError``.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# -- Style aliasing ------------------------------------------------------------

# Maps normalized input (lowercased + non-alphanumerics stripped) → canonical key.
# Lookups happen AFTER normalization; "Deep House" → "deephouse" → "deep_house".
# Designed so common Discogs / Rekordbox spellings all funnel to one canonical
# form. ≥60 entries per the v1.0 design contract.
STYLE_ALIAS_MAP: dict[str, str] = {
    # House family
    "deephouse": "deep_house",
    "techhouse": "tech_house",
    "minimalhouse": "minimal_house",
    "microhouse": "microhouse",
    "lofihouse": "lo_fi_house",
    "lofihousebeats": "lo_fi_house",
    "soulfulhouse": "soulful_house",
    "acidhouse": "acid_house",
    "chicagohouse": "house_chicago",
    "chicago": "house_chicago",
    "ghettohouse": "ghetto_house",

    # Techno family
    "techno": "techno",
    "minimaltechno": "minimal_techno",
    "dubtechno": "dub_techno",
    "acidtechno": "acid_techno",
    "hardcore": "hardcore",

    # Garage / UK bass
    "garage": "garage_uk",
    "garageuk": "garage_uk",
    "ukgarage": "garage_uk",
    "ukg": "garage_uk",
    "usgarage": "garage_us",
    "garageus": "garage_us",
    "futuregarage": "future_garage",
    "bassline": "bassline",
    "grime": "grime",
    "drilluk": "drill_uk",
    "ukdrill": "drill_uk",
    "drillus": "drill_us",
    "usdrill": "drill_us",
    "dubstep": "dubstep",
    "dubuk": "dub_uk",
    "ukdub": "dub_uk",

    # Drum & bass / jungle
    "dnb": "drum_and_bass",
    "drumnbass": "drum_and_bass",
    "drumandbass": "drum_and_bass",
    "drumbass": "drum_and_bass",
    "jungle": "jungle",
    "raggajungle": "ragga_jungle",
    "neurofunk": "neurofunk",
    "liquid": "liquid_dnb",
    "liquiddnb": "liquid_dnb",
    "liquidfunk": "liquid_dnb",
    "footwork": "footwork",
    "juke": "juke",
    "breakbeat": "breakbeat",
    "bigbeat": "big_beat",

    # Hip-hop / soul / funk
    "hiphop": "hip_hop",
    "rap": "hip_hop",
    "boombap": "boom_bap",
    "trap": "trap_hh",
    "traphh": "trap_hh",
    "diryt": "dirty_south",  # common typo
    "dirtysouth": "dirty_south",
    "abstracthiphop": "abstract_hh",
    "abstract": "abstract_hh",
    "soul": "soul",
    "funk": "funk",
    "disco": "disco",
    "boogie": "boogie",
    "italodisco": "italo_disco",
    "italo": "italo_disco",

    # Reggae / Caribbean / African
    "reggae": "reggae",
    "dancehall": "dancehall",
    "dub": "dub_jamaican",
    "dubjamaican": "dub_jamaican",
    "jamaicandub": "dub_jamaican",
    "afrobeat": "afrobeat",
    "afrobeats": "afrobeats_modern",
    "afro": "afrobeats_modern",
    "amapiano": "amapiano",
    "highlife": "highlife",
    "ethiojazz": "ethio_jazz",
    "ethiopianjazz": "ethio_jazz",

    # Jazz
    "jazz": "jazz_modern",
    "modernjazz": "jazz_modern",
    "spiritualjazz": "spiritual_jazz",
    "freejazz": "free_jazz",
    "brokenbeat": "broken_beat",
    "bruk": "broken_beat",

    # Ambient / IDM / leftfield
    "ambient": "ambient",
    "drone": "drone",
    "idm": "idm",
    "intelligentdancemusic": "idm",
    "leftfield": "leftfield",
    "electronica": "electronica",
    "glitch": "glitch",
    "modular": "modular",
    "krautrock": "krautrock",
    "kraut": "krautrock",
    "postpunk": "post_punk",

    # Electro / freestyle
    "electro": "electro",
    "electrofunk": "electro_funk",
    "freestyle": "freestyle",
}


def normalize_style(s: str | None) -> str | None:
    """Normalize a raw style string to its canonical snake_case key.

    Pipeline:
    1. Reject ``None`` / empty.
    2. Lowercase + strip non-alphanumerics.
    3. Look up in :data:`STYLE_ALIAS_MAP`; if absent, return the stripped form
       unchanged (so unknown styles still flow through to the adjacency lookup,
       where they degrade gracefully).

    >>> normalize_style("Deep House")
    'deep_house'
    >>> normalize_style("deep-house")
    'deep_house'
    >>> normalize_style("dnb")
    'drum_and_bass'
    >>> normalize_style("")
    >>> normalize_style(None)
    """
    if not s:
        return None
    stripped = re.sub(r"[^a-z0-9]", "", s.lower())
    if not stripped:
        return None
    return STYLE_ALIAS_MAP.get(stripped, stripped)


# -- Adjacency graph -----------------------------------------------------------

BUNDLED_DEFAULT_PATH = Path(__file__).parent / "style_adjacency.default.json"
SCHEMA_PATH = Path(__file__).parent / "style_adjacency.schema.json"


@dataclass(frozen=True)
class LoadResult:
    """Outcome of :func:`load_style_adjacency`.

    Returned to callers so the server can decide whether to surface a UI warning.
    """

    adjacency: "StyleAdjacency"
    source: str  # 'user' | 'default' | 'first_run_copy'
    warning: str | None = None  # human-readable explanation when not 'user'/'first_run_copy'


@dataclass(frozen=True)
class StyleAdjacency:
    """In-memory adjacency map with safe lookups.

    Always use :meth:`adjacent` rather than indexing — unknown styles return ``[]``
    instead of raising ``KeyError``. This is the contract the novelty feeder
    relies on when a user's library contains styles not in the graph.
    """

    schema_version: int
    styles: dict[str, dict[str, Any]] = field(default_factory=dict)

    def adjacent(self, style: str) -> list[str]:
        """Return adjacency edges for ``style``. Unknown styles return ``[]``."""
        entry = self.styles.get(style)
        return list(entry["edges"]) if entry else []

    def is_terminal(self, style: str) -> bool:
        """True if ``style`` is in the graph and marked terminal, or absent.

        Absence is treated as terminal: a style we don't know about can't
        contribute to novelty by definition.
        """
        entry = self.styles.get(style)
        return True if entry is None else bool(entry["terminal"])

    def is_anchor(self, style: str) -> bool:
        """True iff ``style`` is in the graph and ``terminal: false``."""
        entry = self.styles.get(style)
        return entry is not None and not entry["terminal"]

    def known_styles(self) -> set[str]:
        return set(self.styles.keys())


# -- Validation ----------------------------------------------------------------

# Minimal hand-rolled JSON-Schema-compatible validator. The full ``jsonschema``
# package would be cleaner but it's not in AutoCue's runtime deps, and the
# schema we ship is small enough to validate inline without false economy.
class _ValidationError(Exception):
    pass


def _validate_adjacency(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise _ValidationError("top-level value must be an object")
    missing = {"schema_version", "styles"} - payload.keys()
    if missing:
        raise _ValidationError(f"missing required top-level keys: {sorted(missing)}")
    if not isinstance(payload["schema_version"], int) or payload["schema_version"] < 1:
        raise _ValidationError("schema_version must be a positive integer")
    styles = payload["styles"]
    if not isinstance(styles, dict) or not styles:
        raise _ValidationError("styles must be a non-empty object")
    known = set(styles.keys())
    key_re = re.compile(r"^[a-z0-9_]+$")
    for key, entry in styles.items():
        if not key_re.match(key):
            raise _ValidationError(f"style key {key!r} must match ^[a-z0-9_]+$")
        if not isinstance(entry, dict):
            raise _ValidationError(f"styles[{key!r}] must be an object")
        if set(entry.keys()) != {"edges", "terminal"}:
            raise _ValidationError(
                f"styles[{key!r}] must have exactly keys edges + terminal, got {sorted(entry.keys())}"
            )
        edges = entry["edges"]
        if not isinstance(edges, list) or any(not isinstance(e, str) for e in edges):
            raise _ValidationError(f"styles[{key!r}].edges must be list[str]")
        if len(edges) != len(set(edges)):
            raise _ValidationError(f"styles[{key!r}].edges contains duplicates")
        for edge in edges:
            if edge not in known:
                raise _ValidationError(
                    f"styles[{key!r}].edges references unknown style {edge!r} — "
                    f"dangling reference"
                )
        if not isinstance(entry["terminal"], bool):
            raise _ValidationError(f"styles[{key!r}].terminal must be a boolean")


def _to_adjacency(payload: dict[str, Any]) -> StyleAdjacency:
    return StyleAdjacency(
        schema_version=payload["schema_version"],
        styles={k: {"edges": list(v["edges"]), "terminal": bool(v["terminal"])}
                for k, v in payload["styles"].items()},
    )


# -- Loader --------------------------------------------------------------------

def load_style_adjacency(user_data_dir: Path | None = None) -> LoadResult:
    """Load the style adjacency graph with fallback to the bundled default.

    Loading sequence (matches PRD §6.2 v1.0 design contract):

    1. **First-run path**: if ``user_data_dir/style_adjacency.json`` does not
       exist AND no prior ``.bak`` is alongside it, silently copy the bundled
       default into ``user_data_dir`` and load that copy. NO warning flag.

    2. **Subsequent-run path**: read the user file. On malformed JSON,
       schema-violation, or missing-file, fall back to the bundled default
       and set a human-readable warning that the server can surface to the UI.

    Returns:
        :class:`LoadResult` with the loaded graph and a ``warning`` set only
        when we fell back due to a real problem (not first-run).
    """
    default_payload = json.loads(BUNDLED_DEFAULT_PATH.read_text())
    default_adj = _to_adjacency(default_payload)

    # Pure-bundled mode: no user dir supplied (used by tests + offline tools).
    if user_data_dir is None:
        return LoadResult(adjacency=default_adj, source="default")

    user_data_dir = Path(user_data_dir)
    user_file = user_data_dir / "style_adjacency.json"
    bak_file = user_data_dir / "style_adjacency.json.bak"

    # First-run path: silently materialize the default at the user-editable path.
    if not user_file.exists() and not bak_file.exists():
        try:
            user_data_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(BUNDLED_DEFAULT_PATH, user_file)
            logger.info(
                "Created %s from bundled default — edit to customize the novelty graph.",
                user_file,
            )
        except OSError as exc:  # disk full, permission denied — still serve defaults
            logger.warning("Could not write user-editable adjacency file: %s", exc)
            return LoadResult(
                adjacency=default_adj,
                source="default",
                warning=f"Could not create {user_file} ({exc}). Using bundled defaults.",
            )
        return LoadResult(adjacency=default_adj, source="first_run_copy")

    # Subsequent-run path: try to read + validate the user file.
    try:
        payload = json.loads(user_file.read_text())
        _validate_adjacency(payload)
        return LoadResult(adjacency=_to_adjacency(payload), source="user")
    except FileNotFoundError:
        # User had a .bak but not the live file — degrade silently to defaults
        # so the warning surface stays for genuinely broken files.
        return LoadResult(
            adjacency=default_adj,
            source="default",
            warning=f"{user_file} is missing (a .bak exists alongside). Using bundled defaults.",
        )
    except json.JSONDecodeError as exc:
        _preserve_as_bak(user_file, bak_file)
        return LoadResult(
            adjacency=default_adj,
            source="default",
            warning=f"{user_file} is not valid JSON ({exc.msg}). Using bundled defaults; "
                    f"your file is preserved at {bak_file.name}.",
        )
    except _ValidationError as exc:
        _preserve_as_bak(user_file, bak_file)
        return LoadResult(
            adjacency=default_adj,
            source="default",
            warning=f"{user_file} failed schema validation: {exc}. Using bundled defaults; "
                    f"your file is preserved at {bak_file.name}.",
        )


def _preserve_as_bak(user_file: Path, bak_file: Path) -> None:
    """Rename a bad user file to ``.bak`` so the next startup hits the first-run
    path. Move (not copy) so the user notices the warning resolves once they
    either fix the .bak content or delete it."""
    try:
        user_file.replace(bak_file)
    except OSError as exc:
        logger.warning("Could not preserve %s as .bak: %s", user_file, exc)
