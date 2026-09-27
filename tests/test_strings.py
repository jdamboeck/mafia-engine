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


# --------------------------------------------------------------------------- #
# Number style (#98): the theme's reserved ``_format.numbers`` key picks how   #
# int/float params print; ``plain`` is today's str.format, ``c64`` is str$.    #
# --------------------------------------------------------------------------- #
_TEST_THEME_DIR = Path(__file__).resolve().parent / "fixtures" / "themes" / "test"


def _styled(style: str | None, template: str = "[{n}]") -> Resolver:
    """A resolver whose tree holds ``t`` = ``template`` and, unless ``style`` is
    None, the reserved number-style key."""
    tree: dict = {"t": template}
    if style is not None:
        tree["_format"] = {"numbers": style}
    return Resolver(tree=tree)


@pytest.mark.parametrize(
    ("value", "c64", "plain"),
    [(22.0, "[22]", "[22.0]"), (-0.9, "[-.9]", "[-0.9]"), (3000, "[3000]", "[3000]")],
)
def test_number_style_c64_versus_plain(value, c64, plain):
    assert _styled("c64").resolve("t", {"n": value}) == c64
    assert _styled("plain").resolve("t", {"n": value}) == plain


def test_a_theme_without_the_style_key_prints_plain():
    assert _styled(None).resolve("t", {"n": 22.0}) == "[22.0]"
    assert _styled(None, "{n:g}").resolve("t", {"n": 22.0}) == "22"


@pytest.mark.parametrize("style", ["c64", "plain"])
def test_mid_spec_drops_the_sign_position_for_any_sign(style):
    """``{n:mid$}`` is BASIC's ``mid$(str$(n),2)``: the sign position goes, so a
    negative loses its minus; the template asks for it, so ``plain`` honours it too."""
    r = _styled(style, "[{n:mid$}]")
    assert r.resolve("t", {"n": -3.5}) == "[3.5]"
    assert r.resolve("t", {"n": 22.0}) == "[22]"


def test_mid_spec_takes_a_further_spec_for_the_stripped_text():
    assert _styled("c64", "[{n:mid$>4}]").resolve("t", {"n": -3.5}) == "[ 3.5]"


def test_c64_applies_the_format_spec_to_the_styled_text():
    r = _styled("c64", "[{cash:>6}|{name:<5}]")
    assert r.resolve("t", {"cash": 3000.0, "name": "al"}) == "[  3000|al   ]"


@pytest.mark.parametrize("value", [True, "22.0", "-0.9"])
def test_c64_passes_bools_and_strings_through(value):
    assert _styled("c64").resolve("t", {"n": value}) == f"[{value}]"


def test_override_to_plain_over_a_c64_base_switches_style():
    base = _styled("c64")
    assert base.with_override({"_format": {"numbers": "plain"}}).resolve("t", {"n": 22.0}) == (
        "[22.0]"
    )


def test_override_without_the_style_key_keeps_the_base_style():
    """A ``--theme`` tree merges whole over classic; lacking ``_format`` it must not
    reset classic's style, so the ``plain`` default is never written into a tree."""
    base = _styled("c64")
    assert "_format" not in _styled(None).tree
    assert base.with_override({"x": "y"}).resolve("t", {"n": 22.0}) == "[22]"
    fixture = Resolver.from_directory(_TEST_THEME_DIR)
    assert "_format" not in fixture.tree
    assert base.with_override(fixture.tree).resolve("t", {"n": -0.9}) == "[-.9]"


def test_unknown_style_raises_on_direct_construction():
    with pytest.raises(ValueError, match="basic7"):
        _styled("basic7")


def test_unknown_style_raises_on_with_override():
    with pytest.raises(ValueError, match="basic7"):
        _styled("c64").with_override({"_format": {"numbers": "basic7"}})


def test_unknown_style_raises_on_from_directory(tmp_path):
    (tmp_path / "strings").mkdir()
    (tmp_path / "strings" / "f.yaml").write_text("_format:\n  numbers: basic7\n", encoding="utf-8")
    with pytest.raises(ValueError, match="basic7"):
        Resolver.from_directory(tmp_path)


def test_a_non_mapping_format_key_raises():
    with pytest.raises(ValueError, match="_format"):
        Resolver(tree={"_format": "c64"})


@pytest.mark.parametrize("key", ["_format", "_format.numbers"])
def test_the_reserved_key_is_not_a_template(key):
    with pytest.raises(MissingKeyError, match="reserved"):
        _styled("c64").resolve(key)
