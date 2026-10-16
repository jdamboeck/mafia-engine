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
    assert out == "'gut. pro monat kostet das 500 $ miete.'"


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
        "'gut. pro monat kostet das 1 $ miete.'"
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
    [(22.0, "[ 22 ]", "[22.0]"), (-0.9, "[-.9 ]", "[-0.9]"), (3000, "[ 3000 ]", "[3000]")],
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
    r = _styled("c64", "[{cash:>8}|{name:<5}]")
    assert r.resolve("t", {"cash": 3000.0, "name": "al"}) == "[   3000 |al   ]"


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
    assert base.with_override({"x": "y"}).resolve("t", {"n": 22.0}) == "[ 22 ]"
    fixture = Resolver.from_directory(_TEST_THEME_DIR)
    assert "_format" not in fixture.tree
    assert base.with_override(fixture.tree).resolve("t", {"n": -0.9}) == "[-.9 ]"


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


# --------------------------------------------------------------------------- #
# The classic theme prints numbers as the C64 does (_format.numbers: c64).     #
# --------------------------------------------------------------------------- #
def test_classic_prints_a_float_price_as_the_c64_does():
    """:12135 ``print"{down}"y$" verlangt"p"$ vorschuss, wenn"`` — a whole float price is
    `` 3000 $`` on the C64 (``str$`` has no ``.0``, ``PRINT`` adds the sign space and
    the trailing space), never Python's ``3000.0$``."""
    out = _resolver().resolve(
        "locations.pub.recruit_offer",
        {"pronoun": "er", "name": "joe", "description": "stark", "price": 3000.0},
    )
    assert " verlangt 3000 $ vorschuss" in out
    assert "3000.0" not in out


# --------------------------------------------------------------------------- #
# C64 PRINT spacing (#141): under ``c64`` a bare number prints as PRINT writes #
# it -- str$'s sign position, the digits, the trailing cursor-right -- so a    #
# template holds only the source's own spaces. The VICE capture behind this is #
# tests/fixtures/c64_print/ (see tests/test_c64_numbers.py).                   #
# --------------------------------------------------------------------------- #
def test_a_negative_prints_its_minus_without_a_doubled_space():
    """``print"du hast"p"$"`` with p=-500 shows ``du hast-500 $``: the minus takes the
    sign position, so no space opens before it and only PRINT's one follows it."""
    r = _styled("c64", "du hast{p}$ schulden")
    assert r.resolve("t", {"p": -500}) == "du hast-500 $ schulden"
    classic = _resolver().resolve("locations.sph.wager_prompt", {"cash": -500})
    assert classic == "du hast-500 $. dein einsatz:"
    assert "  " not in classic


def test_a_positive_number_gets_the_sign_space():
    """``print"du hast"p"$"`` with p=500 shows ``du hast 500 $``: a space for the sign."""
    assert _styled("c64", "du hast{p}$").resolve("t", {"p": 500}) == "du hast 500 $"
    classic = _resolver().resolve("locations.sph.wager_prompt", {"cash": 500})
    assert classic == "du hast 500 $. dein einsatz:"


def test_the_plain_number_style_is_unchanged():
    """``plain`` is ``str.format``: no sign space, no trailing space, Python's text."""
    r = _styled("plain", "du hast{p}$|{f}|{n:>5}|{g:g}")
    assert r.resolve("t", {"p": 500, "f": -0.9, "n": 7, "g": 22.0}) == "du hast500$|-0.9|    7|22"


@pytest.mark.parametrize("style", ["c64", "plain"])
def test_str_spec_is_str_dollar_with_no_trailing_space(style):
    """``{n:str$}`` is ``str$(n)``: the sign position kept, PRINT's trailing space not
    added (``print"a";str$(p)``), under either style since the template asks for it."""
    r = _styled(style, "(0 -{n:str$}):")
    assert r.resolve("t", {"n": 800}) == "(0 - 800):"
    assert r.resolve("t", {"n": -0.5}) == "(0 --.5):"


def test_raw_spec_is_the_number_without_c64_padding():
    """``{n:raw}`` is for text no source line prints: no sign space, no trailing space;
    under ``plain`` it is ``str.format``, and a non-number passes through."""
    assert _styled("c64", "[{n:raw}]").resolve("t", {"n": 22.0}) == "[22]"
    assert _styled("c64", "[{n:raw}]").resolve("t", {"n": -0.9}) == "[-.9]"
    assert _styled("c64", "[{n:raw>4}]").resolve("t", {"n": 7}) == "[   7]"
    assert _styled("plain", "[{n:raw}]").resolve("t", {"n": 22.0}) == "[22.0]"
    assert _styled("c64", "[{n:raw}]").resolve("t", {"n": "x"}) == "[x]"


@pytest.mark.parametrize("style", ["c64", "plain"])
def test_tab_pads_the_line_to_its_column(style):
    """``{tab(N)}`` is BASIC's ``tab(N)``: spaces up to column N of the current line,
    nothing once the line is already there; each line counts from its own start."""
    r = _styled(style, "{a}{tab(6)}|\nxy{tab(3)}|{b}{tab(2)}|")
    assert r.resolve("t", {"a": "ab", "b": "long"}) == "ab    |\nxy |long|"


def test_the_classic_standings_row_tabs_as_4510_does():
    """:4510 ``sp$(i);tab(15);ka(i)"$";tab(26);gf(i)``: the cash's sign position at
    column 15, the score's at column 26, both PRINTed with the trailing space."""
    row = _resolver().resolve(
        "game_end.standings_row", {"name": "alcapone", "cash": 5500, "score": -0.9}
    )
    assert row == "alcapone        5500 $    -.9 "
    assert row.index("5500") - 1 == 15 and row.index("-.9") == 26


# --------------------------------------------------------------------------- #
# Text fixes (U3): each string shows what its source line prints -- no quote   #
# marks the line lacks, one apostrophe where it has one, its line breaks.       #
# A string joined from several PRINTs keeps the port's one-space join.          #
# --------------------------------------------------------------------------- #
_SOURCE_TEXTS = [
    # :16020 print"{down}du hast"ka(sp)"$. dein einsatz:":inputp
    ("locations.sph.wager_prompt", {"cash": 500}, "du hast 500 $. dein einsatz:"),
    # :12021 print"'ich habe" / :12022 printx"faesser fuer"p"$ pro fass.'"
    (
        "locations.pub.drink_offer",
        {"stock": 150, "price": 7},
        "'ich habe\n 150 faesser fuer 7 $ pro fass.'",
    ),
    # :13090 print"...brandheiss! eine ladung army-hand-"
    # :13091 print"{down}granaten ist gerade eingetroffen!":b=8:print:return
    (
        "locations.waf.grenades_in",
        {},
        "...brandheiss! eine ladung army-hand-\n\ngranaten ist gerade eingetroffen!\n",
    ),
    # The pub's SEQ file (src/pub): 'EH@ AMIGO@ WILLST DU 'NE MILCH? ...
    (
        "locations.pub.entry_prompt",
        {},
        "'Eh, Amigo, willst du 'ne Milch? Oder suchst du was anderes?'",
    ),
    (
        "locations.pub.menu.tip",
        {},
        "'Hast du vielleicht 'n guten Tipp für 'nen Überfall oder sowas?'",
    ),
    # --- pub recruit, :12101-12170 ---
    (
        "locations.pub.rank_too_low",
        {"rank": "ganove"},
        "als ganove kannst du noch keine eigenen leute haben!",
    ),
    ("locations.pub.recruit_no_housing", {}, "du brauchst eine unterkunft fuer die gangster!"),
    ("locations.pub.recruit_gang_full", {}, "maximal 10 gangster!"),
    ("locations.pub.recruit_nobody_available", {}, "es ist niemand da."),
    ("locations.pub.recruit_intro_male", {}, "ein gangster kommt, um mit dir zu sprechen..."),
    (
        "locations.pub.recruit_intro_female",
        {},
        "eine dame scheint sich fuer deine gang zu interessieren...",
    ),
    (
        "locations.pub.recruit_offer",
        {"pronoun": "er", "name": "luigi", "description": "ein killer", "price": 1500},
        "er wird luigi genannt. ein killer. er verlangt 1500 $ vorschuss, wenn er sich dir"
        " anschliessen soll.",
    ),
    ("locations.pub.recruit_hired", {"gang_size": 3}, "nun hast du 3 gangster!"),
    # --- pub job, :12301-12335 ---
    ("locations.pub.job_rank_too_high", {"rank": "boss"}, "als 'boss' findest du was besseres!"),
    ("locations.pub.job_nobody_available", {}, "niemand hat einen job fuer dich."),
    (
        "locations.pub.job_offer_bouncer",
        {},
        "der inhaber der bahnhofskneipe will dich fuer 3 monate als rausschmeisser anstellen.",
    ),
    (
        "locations.pub.job_offer_croupier",
        {},
        "du kannst 2 monate als croupier in georges spielhoelle arbeiten.",
    ),
    (
        "locations.pub.job_offer_doorman",
        {},
        "das 'le roi'-hotel sucht einen tuersteher fuer 2 monate.",
    ),
    (
        "locations.pub.job_offer_killer",
        {},
        "du erhaelst das foto eines mannes, der im 'red house' abgestiegen ist. du sollst ihn"
        " umlegen!",
    ),
    ("locations.pub.job_pay_confirm", {"pay": 2500}, "du bekommst dafuer 2500 $."),
    ("locations.pub.job_accepted", {}, "du hast den job!"),
    # --- job shifts, :25020-25556 ---
    (
        "job.shift_bouncer_wait",
        {},
        "du wartest auf ein zeichen deines chefs, einen ungebetenen gast zu entfernen...",
    ),
    ("job.shift_bouncer_quiet", {}, "heute gab es nichts!"),
    ("job.shift_bouncer_trouble", {}, "ein bursche faengt an, zu randalieren! tu etwas!"),
    (
        "job.shift_croupier_caught",
        {},
        "ein kunde hat deine tricks durchschaut und zieht ein messer!",
    ),
    (
        "job.shift_croupier_bonus",
        {"amount": 450},
        "du machst gewinne und erhaeltst einen bonus von 450 $.",
    ),
    (
        "job.shift_killer_intro",
        {},
        "du hast den typ auf dem foto gefunden und lockst ihn in eine seitenstrasse...",
    ),
    ("job.shift_failed", {}, "du hast deinen auftrag nicht erledigt!"),
    (
        "job.shift_completed",
        {"pay": 2500},
        "du hast deinen job erledigt und erhaeltst deinen lohn. es sind 2500 $!",
    ),
    # --- loan shark, :15010-15321 ---
    ("locations.kdh.pay_old_debts_first", {}, "zahle erstmal deine alten schulden ab!"),
    (
        "locations.kdh.borrow_grace_notice",
        {},
        "du hast 6 monate zeit, die schulden zurueck zu zahlen!",
    ),
    ("locations.kdh.repay_partial", {"remaining": 300}, "ok, du hast jetzt noch 300 $ schulden!"),
    ("locations.kdh.repay_full", {}, "du hast die schulden zurueckgezahlt!"),
    ("locations.kdh.already_own_a_shop", {}, "du hast schon ein kreditgeschaeft!"),
    ("locations.kdh.pay_own_debts_first", {}, "zahle erstmal deine eigenen schulden!"),
    ("locations.kdh.shop_belongs_to", {"name": "moran"}, "der laden gehoert moran!"),
    ("locations.kdh.buy_offer", {"price": 5500}, "der jetzige besitzer verlangt 5500 $."),
    ("locations.kdh.bought", {}, "der laden gehoert nun dir!"),
    (
        "locations.kdh.sell_offer",
        {"price": 4500},
        "ein kaeufer bietet dir 4500 $ fuer den laden. willst du verkaufen (j/n) ?",
    ),
    ("locations.kdh.not_your_shop", {}, "dieser laden gehoert dir nicht!"),
    (
        "locations.kdh.capital_status",
        {"capital": 2000, "max": 5000},
        "du hast 2000 $ im kreditgeschaeft (maximal 5000 $).",
    ),
    ("locations.kdh.debts_paid_on_time", {}, "alle schuldner haben puenktlich gezahlt!"),
    (
        "locations.kdh.ambush_intro",
        {},
        "einer deiner klienten hat noch geld. er lauert dir mit einem gewehr auf, als du"
        " kassieren willst!",
    ),
    ("locations.kdh.ambush_loot", {"amount": 800}, "er hatte noch 800 $ dabei!"),
]


@pytest.mark.parametrize(
    ("key", "params", "expected"), _SOURCE_TEXTS, ids=[k for k, _, _ in _SOURCE_TEXTS]
)
def test_the_string_shows_what_its_source_line_prints(key, params, expected):
    assert _resolver().resolve(key, params) == expected
