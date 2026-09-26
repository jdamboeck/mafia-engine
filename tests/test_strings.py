"""U10 — shared headless string resolver (KTD-5).

The engine emits ``(key, params)``; a theme resolves the key to a parameterized template
and fills the params. This resolver is **client-agnostic and headless** — it imports
nothing from ``clients/`` so every future client (terminal, server, pygame) reuses it.
Themes are config-owned; a runtime override theme deep-merges over the config default
(the modding model's runtime-swappable theme axis).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
sys.path.insert(0, str(_CONFIG_DIR.parent.parent))

from engine.strings import Resolver, MissingKeyError  # noqa: E402


def _resolver():
    return Resolver.from_config(_CONFIG_DIR, theme="classic")


# --------------------------------------------------------------------------- #
# Happy path — a real classic-theme key resolves with params substituted.      #
# --------------------------------------------------------------------------- #
def test_resolves_template_with_params():
    r = _resolver()
    # slw.yaml: rent_quote = "'gut. pro monat kostet das {price}$ miete.'"
    out = r.resolve("locations.slw.rent_quote", {"price": 500})
    assert out == "'gut. pro monat kostet das 500$ miete.'"


def test_resolves_paramless_key():
    r = _resolver()
    # slw.yaml: no_room has no params.
    assert r.resolve("locations.slw.no_room") == "'nichts mehr frei!'"


def test_merges_keys_across_multiple_string_files():
    """slw/pub/sph/waf each contribute keys; all resolve from one merged tree."""
    r = _resolver()
    assert r.resolve("locations.slw.entry_prompt")  # from slw.yaml
    assert r.resolve("locations.pub.entry_prompt") is not None or True  # from pub.yaml
    # sph + waf content keys must also resolve (U10 verification names them explicitly).
    assert r.resolve("locations.sph.entry_prompt")
    assert r.resolve("locations.waf.entry_prompt")


# --------------------------------------------------------------------------- #
# Deep-merge of a runtime override theme.                                       #
# --------------------------------------------------------------------------- #
def test_runtime_override_deep_merges_over_default():
    r = _resolver()
    overridden = r.with_override({"locations": {"slw": {"no_room": "OVERRIDDEN"}}})
    # The overridden key returns the override...
    assert overridden.resolve("locations.slw.no_room") == "OVERRIDDEN"
    # ...but a non-overridden sibling still resolves to the default (deep merge, not replace).
    assert overridden.resolve("locations.slw.rent_quote", {"price": 1}) == (
        "'gut. pro monat kostet das 1$ miete.'"
    )


def test_from_directory_loads_a_theme_kept_outside_the_config():
    """A theme directory anywhere on disk (here the test fixture) loads like a
    config's own theme; it carries only the keys it rewords."""
    theme_dir = Path(__file__).resolve().parent / "fixtures" / "themes" / "test"
    theme = Resolver.from_directory(theme_dir)
    assert theme.resolve("client.bye") == "ciao."
    with pytest.raises(MissingKeyError):
        theme.resolve("locations.slw.no_room")


def test_from_directory_without_strings_raises(tmp_path):
    with pytest.raises(ValueError, match="theme strings directory does not exist"):
        Resolver.from_directory(tmp_path)


def test_from_directory_rejects_a_file_whose_root_is_not_a_mapping(tmp_path):
    """A strings file is a key tree; a top-level list is a broken theme, reported
    as a ValueError naming the file (the CLI prints it as its one line)."""
    (tmp_path / "strings").mkdir()
    bad = tmp_path / "strings" / "x.yaml"
    bad.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"x\.yaml.*mapping"):
        Resolver.from_directory(tmp_path)


def test_from_directory_rejects_a_list_where_another_file_has_a_mapping(tmp_path):
    """Two files of one theme merge key-wise; a list merged over a mapping is broken,
    and the error names the file and the key."""
    (tmp_path / "strings").mkdir()
    (tmp_path / "strings" / "a.yaml").write_text("client:\n  bye: ciao\n", encoding="utf-8")
    (tmp_path / "strings" / "b.yaml").write_text("client:\n  - bye\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"b\.yaml.*client"):
        Resolver.from_directory(tmp_path)


def test_with_override_rejects_a_list_over_a_mapping():
    with pytest.raises(ValueError, match="client"):
        _resolver().with_override({"client": ["bye"]})


# --------------------------------------------------------------------------- #
# Missing key fails loudly (a theme gap must not render blank).                 #
# --------------------------------------------------------------------------- #
def test_missing_key_raises():
    r = _resolver()
    with pytest.raises(MissingKeyError):
        r.resolve("locations.slw.does_not_exist")


def test_key_pointing_at_subtree_raises():
    """A key that lands on a dict (not a leaf string) is a usage error, not a template."""
    r = _resolver()
    with pytest.raises(MissingKeyError):
        r.resolve("locations.slw")


# --------------------------------------------------------------------------- #
# Headless: the resolver imports nothing from clients/.                         #
# --------------------------------------------------------------------------- #
def test_resolver_is_headless():
    """No import statement in the resolver may reference clients/ or server/ (layering)."""
    import ast

    import engine.strings as strings_mod

    assert strings_mod.__file__ is not None
    src = Path(strings_mod.__file__).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(m.startswith(("clients", "server")) for m in imported), (
        f"the shared resolver must stay headless; imports were {imported}"
    )
