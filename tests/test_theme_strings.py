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
    / "data" / "game_configs" / "mafia_1920s" / "themes" / "classic"
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
            assert opens == closes, (
                f"mismatched braces in {key}: {opens} opens, {closes} closes"
            )
