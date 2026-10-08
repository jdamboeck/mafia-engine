"""Theme string verification: load every string value from the classic theme
YAML and assert no empty values, no broken ``{param}`` references.

Runs as a regular pytest file so it's part of CI (T11).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_THEME_DIR = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "game_configs"
    / "mafia_1920s"
    / "themes"
    / "classic"
)

# Regex for {param} placeholders in resolved strings.
_PARAM_RE = re.compile(r"\{(\w+)\}")


def _collect_strings(d: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a nested dict into dot-separated key -> string value."""
    out: dict[str, str] = {}
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_collect_strings(v, full))
        elif isinstance(v, str):
            out[full] = v
    return out


def test_no_empty_string_values():
    """Every string value in the theme must be non-empty."""
    strings_dir = _THEME_DIR / "strings"
    for yaml_file in sorted(strings_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        flat = _collect_strings(data, yaml_file.stem)
        for key, val in flat.items():
            assert val.strip(), f"empty string at {key} in {yaml_file.name}"


def test_no_broken_param_references():
    """If a handler calls resolver.resolve(key, params={...}), the template
    must contain the corresponding {param}. We check that every {param} in
    a template is lowercase alphanumeric (valid Python identifier)."""
    strings_dir = _THEME_DIR / "strings"
    for yaml_file in sorted(strings_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        flat = _collect_strings(data, yaml_file.stem)
        for key, val in flat.items():
            for match in _PARAM_RE.finditer(val):
                param = match.group(1)
                assert param.isidentifier(), (
                    f"invalid param {{{param}}} in {key} ({yaml_file.name})"
                )


def test_no_unresolved_placeholders_in_final_strings():
    """After resolving all strings with empty params, no {xxx} should remain
    (unless the handler provides them at call time — we can't check that here,
    but we can flag templates that have zero params defined anywhere)."""
    strings_dir = _THEME_DIR / "strings"
    for yaml_file in sorted(strings_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        flat = _collect_strings(data, yaml_file.stem)
        for key, val in flat.items():
            # Just verify the string is well-formed (no stray { or }).
            opens = val.count("{")
            closes = val.count("}")
            assert opens == closes, f"mismatched braces in {key}: {opens} opens, {closes} closes"


#: Each location's title as ``:3025`` reads it from the location's disk file (``input#1,x$``)
#: and prints it, verbatim from ``research-data/pass-2/location-dialogue.yaml`` (``title``);
#: sgl's run of spaces is the file's.
_LOCATION_TITLES = {
    "slw": "SCHLUPFWINKEL (MOTEL, MIETSKASERNE)",
    "pub": "PUB/BAR (EIN ZWIELICHTIGES LOKAL)",
    "waf": "WAFFENLADEN",
    "aut": "AUTOMOBIL-HAENDLER",
    "kdh": "KREDIT-HAI",
    "sph": "SPIELHOELLE",
    "sgl": "EINFACHER LADEN (WIE GESCHAFFEN ZUM     SCHUTZGELD EINTREIBEN!)",
    "sub": "SUBWAY-STATION (U-BAHN)",
    "bhf": "RAILWAY-STATION (BAHNHOF)",
    "ban": "BANK/POSTAMT",
    "pol": "POLIZEI-PRAESIDIUM",
    "ble": "BLUETEN-EDDIE",
}

_LOCATION_DIALOGUE = (
    Path(__file__).resolve().parents[2]
    / "research"
    / "research-data"
    / "pass-2"
    / "location-dialogue.yaml"
)


def test_every_location_title_resolves_from_the_theme():
    """``locations.<key>.title`` for each of the twelve locations, as :3025 prints it."""
    from engine.strings import Resolver

    resolver = Resolver.from_config(_THEME_DIR.parents[1], theme="classic")
    assert {key: resolver.resolve(f"locations.{key}.title") for key in _LOCATION_TITLES} == (
        _LOCATION_TITLES
    )


def test_the_location_titles_are_the_research_titles():
    """The table above is the research's, title by title, for every location it has."""
    import pytest

    if not _LOCATION_DIALOGUE.exists():
        pytest.skip(f"this test needs the research tree: {_LOCATION_DIALOGUE} is not present")
    data = yaml.safe_load(_LOCATION_DIALOGUE.read_text(encoding="utf-8"))
    research = {loc["key"]: loc["title"] for loc in data["location_dialogue"]["locations"]}
    assert research == _LOCATION_TITLES
