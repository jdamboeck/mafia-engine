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

from tests.text_fold import fixture_text, flat, tree_text  # noqa: E402

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


#: The prompts whose source line prints nothing of its own: the key a client reads is
#: a bare ``getx$``, so its text is empty by design (``<file stem>.<key>`` -> reason).
#: Every entry must exist and be exactly empty; no other string may be.
_SILENT_PROMPTS = {
    "aut.locations.aut.model_prompt": (
        ':14020 `getx$:ifx$=""goto14020` prints nothing; :14008 printed the line'
    ),
}


def test_no_empty_string_values():
    """Every string value in the theme must be non-empty, but a silent prompt's."""
    strings_dir = _THEME_DIR / "strings"
    silent: dict[str, str] = {}
    for yaml_file in sorted(strings_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        flat = _collect_strings(data, yaml_file.stem)
        for key, val in flat.items():
            if key in _SILENT_PROMPTS:
                silent[key] = val
                continue
            assert val.strip(), f"empty string at {key} in {yaml_file.name}"
    assert silent == dict.fromkeys(_SILENT_PROMPTS, ""), "a silent prompt is missing or has text"


def test_no_string_doubles_an_apostrophe():
    """A double-quoted YAML string takes ``''`` as two apostrophes (only a single-quoted
    one escapes it), and no source line prints two in a row."""
    strings_dir = _THEME_DIR / "strings"
    doubled = []
    for yaml_file in sorted(strings_dir.glob("*.yaml")):
        data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        flat = _collect_strings(data, yaml_file.stem)
        doubled.extend(key for key, val in flat.items() if "''" in val)
    assert doubled == []


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
#: and prints it: the words of ``research-data/pass-2/location-dialogue.yaml``'s ``title`` in
#: today's spelling, sgl's wrap padding one space (the fold check ties them to the source).
_LOCATION_TITLES = {
    "slw": "Schlupfwinkel (Motel, Mietskaserne)",
    "pub": "Pub/Bar (ein zwielichtiges Lokal)",
    "waf": "Waffenladen",
    "aut": "Automobil-Händler",
    "kdh": "Kredit-Hai",
    "sph": "Spielhölle",
    "sgl": "Einfacher Laden (wie geschaffen zum Schutzgeld eintreiben!)",
    "sub": "Subway-Station (U-Bahn)",
    "bhf": "Railway-Station (Bahnhof)",
    "ban": "Bank/Postamt",
    "pol": "Polizei-Präsidium",
    "ble": "Blüten-Eddie",
}


def test_every_location_title_resolves_from_the_theme():
    """``locations.<key>.title`` for each of the twelve locations, as :3025 prints it."""
    from engine.strings import Resolver

    resolver = Resolver.from_config(_THEME_DIR.parents[1], theme="classic")
    assert {key: resolver.resolve(f"locations.{key}.title") for key in _LOCATION_TITLES} == (
        _LOCATION_TITLES
    )


def test_the_location_titles_are_the_research_titles():
    """The C64-text fixture's titles (``tests/fixtures/c64_text``) are the research's,
    title by title, for every location it has."""
    from tests.helpers import LOCATION_DIALOGUE, read_research
    from tests.text_fold import fixture_value

    data = yaml.safe_load(read_research(LOCATION_DIALOGUE))
    research = {loc["key"]: loc["title"] for loc in data["location_dialogue"]["locations"]}
    assert research == {key: fixture_value(f"locations.{key}.title") for key in research}
    assert set(research) == set(_LOCATION_TITLES)


def _location_screen_text() -> dict[str, str]:
    """``{key: text}`` of every location's ``title``, ``entry_prompt`` and ``menu.*`` in
    the live classic theme: the texts ``:3025``-``:3030`` read from the disk files."""
    out: dict[str, str] = {}
    for key in _LOCATION_TITLES:
        data = yaml.safe_load((_THEME_DIR / "strings" / f"{key}.yaml").read_text("utf-8"))
        location = data["locations"][key]
        out[f"locations.{key}.title"] = location["title"]
        out[f"locations.{key}.entry_prompt"] = location["entry_prompt"]
        for option, text in location["menu"].items():
            out[f"locations.{key}.menu.{option}"] = text
    return out


def test_no_location_screen_text_has_a_run_of_spaces():
    """The C64's 40-column wrap padding is one space in every location's title, entry
    prompt and menu option (R4), the pub's, sgl's and pol's menus among them."""
    texts = _location_screen_text()
    assert len(texts) == 12 * 2 + 44
    assert {"locations.pub.menu.recruit", "locations.sgl.menu.fake_police"} <= set(texts)
    assert {"locations.pol.menu.free"} <= set(texts)
    assert [key for key, text in texts.items() if "  " in text] == []


def test_the_pub_and_waf_screens_read_in_today_s_spelling():
    """The two locations without a shell test of their own: the pub's and the weapon
    shop's menu and entry text, the disk files' words in today's spelling (AE1, AE3)."""
    texts = _location_screen_text()
    assert {key: text for key, text in texts.items() if key.split(".")[1] in ("pub", "waf")} == {
        "locations.pub.title": "Pub/Bar (ein zwielichtiges Lokal)",
        "locations.pub.entry_prompt": (
            "'Eh, Amigo, willst du 'ne Milch? Oder suchst du was anderes?'"
        ),
        "locations.pub.menu.drink": "'Nein, keine Milch! Hast du was Alkoholisches da?'",
        "locations.pub.menu.recruit": (
            "'Ich suche ein paar Jungs für meine Gang. Mal sehen, wer da ist...'"
        ),
        "locations.pub.menu.tip": "'Hast du vielleicht 'n guten Tipp für 'nen Überfall oder sowas?'",
        "locations.pub.menu.job": (
            "'Ich brauche dringend einen Job, einen Auftrag! Du verstehst schon...'"
        ),
        "locations.pub.menu.leave": (
            "'Scheint nicht der richtige Laden zu sein. Ich verschwinde wieder...'"
        ),
        "locations.waf.title": "Waffenladen",
        "locations.waf.entry_prompt": (
            "'Tag, Mister. Ein paar gute Handböller gefällig? Sind im Hinterzimmer...'"
        ),
        "locations.waf.menu.buy": "'Yeah, mal sehen, was da ist.'",
        "locations.waf.menu.train": (
            "'Kannst du meine Jungs im Schießen ausbilden? Sind einfach zu schlapp!'"
        ),
        "locations.waf.menu.leave": "'Danke, danke! Kein Bedarf. Bye!'",
    }


def _location_print_text() -> tuple[dict[str, str], dict[str, str]]:
    """``({key: text}, {key: C64 text})`` of every key of the twelve location string
    files other than the screen texts above (``title``, ``entry_prompt``, ``menu.*``):
    the BASIC print strings, live and as the C64-text fixture froze them."""
    current, fixture = flat(tree_text()), flat(fixture_text())
    screen = set(_location_screen_text())
    keys = [
        key
        for name, texts in fixture_text().items()
        if name in {f"strings/{loc}.yaml" for loc in _LOCATION_TITLES}
        for key in texts
        if key not in screen
    ]
    return {key: current[key] for key in keys}, {key: fixture[key] for key in keys}


def test_ae2_the_print_strings_restore_umlauts_only_where_german_spells_them():
    """``faesser`` is ``Fässer`` (``{stock}`` before it, as :12022 prints it), and every
    ``eue``/``aue`` of the C64 text (``neue``, ``bereuen``, ``lauert``) keeps its ``eu``
    and ``au``: no ``ue`` there was an umlaut (AE2)."""
    current, fixture = _location_print_text()
    assert len(current) > 150
    assert "{stock}Fässer für{price}$ pro Fass." in current["locations.pub.drink_offer"]
    kept = [
        (key, word)
        for key, text in fixture.items()
        for word in re.findall(r"\w*[ea]ue\w*", text.lower())
    ]
    assert {word for _, word in kept} >= {"neue", "bereuen", "lauert", "blauer"}
    assert [(key, word) for key, word in kept if word not in current[key].lower()] == []
    assert [key for key, text in current.items() if re.search("[ae]ü", text.lower())] == []


#: ``{placeholder}`` with its field spec, as the resolver reads it.
_FIELD_RE = re.compile(r"\{([^{}]*)\}")


def test_the_print_strings_keep_every_placeholder_in_its_place():
    """Each key's ``{placeholders}`` are its C64 text's, in order and with their specs;
    the one new name is the recruit offer's sentence-start ``{Pronoun}`` (:12131,
    :12135), where the C64 text had ``{pronoun}``."""
    current, fixture = _location_print_text()
    changed = {
        key: (_FIELD_RE.findall(text), _FIELD_RE.findall(fixture[key]))
        for key, text in current.items()
        if _FIELD_RE.findall(text) != _FIELD_RE.findall(fixture[key])
    }
    assert changed == {
        "locations.pub.recruit_offer": (
            ["Pronoun", "name", "description", "Pronoun", "price", "pronoun"],
            ["pronoun", "name", "description", "pronoun", "price", "pronoun"],
        )
    }


#: The game-flow string files (U4 of the string normalization): the turn, upkeep,
#: setup, ending, combat, police, gang war, roadblock, win-flow, job, house-rule and
#: session strings, and the client's German line.
_GAME_FLOW_FILES = {
    f"strings/{name}.yaml"
    for name in (
        "turn",
        "upkeep",
        "setup",
        "game_end",
        "combat",
        "police",
        "gang_war",
        "roadblock",
        "win_flows",
        "jobs",
        "house_rules",
        "session",
        "_format",
        "client",
    )
}


def _game_flow_text() -> tuple[dict[str, str], dict[str, str]]:
    """``({key: text}, {key: C64 text})`` of every key of the game-flow string files."""
    current, fixture = flat(tree_text()), flat(fixture_text())
    keys = [
        key for name, texts in fixture_text().items() if name in _GAME_FLOW_FILES for key in texts
    ]
    return {key: current[key] for key in keys}, {key: fixture[key] for key in keys}


def test_ae4_the_standings_header_keeps_its_columns():
    """:4505 ``print"{down}spieler:        kapital: punkte:"``: the header reads in
    today's spelling with each label at the column the C64 text puts it (AE4), so the
    :4510 rows' ``tab(15)`` and ``tab(26)`` still line up under it."""
    from engine.strings import Resolver

    header = Resolver.from_directory(_THEME_DIR).resolve(
        "game_end.standings_header", {"year": 1925, "month": 1}
    )
    rows = header.split("\n")
    assert rows == ["Spielstand 1925-1", "", "Spieler:        Kapital: Punkte:"]
    c64_row = flat(fixture_text())["game_end.standings_header"].split("\n")[2]
    assert c64_row == "spieler:        kapital: punkte:"
    for label in ("Spieler:", "Kapital:", "Punkte:"):
        assert rows[2].index(label) == c64_row.index(label.lower()), label
    assert len(rows[2]) == len(c64_row)


def test_the_game_flow_strings_keep_every_placeholder_in_its_place():
    """Each game-flow key's ``{placeholders}`` are its C64 text's, in order and with
    their specs (``{tab(39)}``, ``{amount:str$.1}``, ``{year:mid$}``...)."""
    current, fixture = _game_flow_text()
    assert len(current) > 150
    assert {
        key: (_FIELD_RE.findall(text), _FIELD_RE.findall(fixture[key]))
        for key, text in current.items()
        if _FIELD_RE.findall(text) != _FIELD_RE.findall(fixture[key])
    } == {}


def test_the_game_flow_strings_break_a_row_only_for_a_layout():
    """A row break inside running text became a space; the breaks left lay out a screen:
    the rent seizure's C64 rows and its column-39 sign (:4610-4620), the standings header
    (:4500-4505), the gangster panel's three rows (:1300-1320), the turn banner's two
    centred rows (:4005-4006, and the whose-turn line worded as it), the promotion
    poster (:4200-4220), the victory stand-in, the croupier's trick list (:25110-25111),
    the job header's blank row (:25000), the police capture's two unpunctuated rows
    (:26042-26043), and the client's own turn-over summary."""
    current, _fixture = _game_flow_text()
    assert {key for key, text in current.items() if "\n" in text} == {
        "upkeep.rent_late",
        "game_end.standings_header",
        "turn.overview.gangster",
        "turn.picker.gangster",
        "upkeep.turn_banner",
        "session.whose_turn",
        "upkeep.rank_promotion",
        "turn.victory",
        "job.shift_croupier_tricks",
        "job.shift_header",
        "client.turn_over.summary",
    }
