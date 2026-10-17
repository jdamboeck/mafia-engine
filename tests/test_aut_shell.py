"""Tests for the aut shell + classic-theme strings (``mf-prg.bas:14000-14131``).

The checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, the keys the handlers emit in their behavioural runs
(``tests/test_aut.py``) resolve in the classic theme and reach every aut string,
and the text is verbatim from the research corpus (location-dialogue.yaml, aut).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine.config_loader import load_game_config
from engine.locations import load_location
from engine.strings import Resolver
from tests.helpers import assert_captured_keys_resolve, location_strings
from tests.test_aut import _EXITS, _run, _state
from tests.text_fold import fixture_value

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

_CONFIG = load_game_config(_CONFIG_DIR)
_SHELL = _CONFIG_DIR / "content" / "locations" / "aut.yaml"
_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "aut.yaml"
_CITY = _CONFIG_DIR / "content" / "map" / "city.yaml"


def _get(data, dotted):
    node = data
    for part in dotted.split("."):
        node = node[part]
    return node


def _shell():
    return load_location(yaml.safe_load(_SHELL.read_text(encoding="utf-8")))


def test_aut_shell_resolves_both_handlers_and_a_guardless_leave():
    loc = _shell()
    assert loc.key == "aut"
    assert [o.id for o in loc.options] == ["buy", "steal", "leave"]
    by_id = {o.id: o for o in loc.options}
    assert by_id["buy"].handler is _CONFIG.handlers["aut.buy"]
    assert by_id["steal"].handler is _CONFIG.handlers["aut.steal"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_aut_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.aut.entry_prompt")
    for opt_id in ("buy", "steal", "leave"):
        assert _get(data, f"locations.aut.menu.{opt_id}")


#: The behavioural runs whose captured keys are resolved: every exit of
#: ``tests/test_aut.py`` (id, option, state fields, answers, rng draws), and a theft
#: that leaves the old car behind (:14117).
_RUNS = [case[:5] for case in _EXITS] + [
    ("14117-old-car-left", "aut.steal", {"vehicle": 4}, (1,), (0, 120)),
]


def _captured(key, fields, answers, draws) -> list:
    return _run(key, _state(**fields), answers=answers, draws=draws)[1].seen


@pytest.mark.parametrize(
    ("key", "fields", "answers", "draws"),
    [run[1:] for run in _RUNS],
    ids=[run[0] for run in _RUNS],
)
def test_the_keys_each_aut_run_emits_resolve_in_the_classic_theme(key, fields, answers, draws):
    assert_captured_keys_resolve(_captured(key, fields, answers, draws))


def test_the_aut_runs_reach_every_aut_string():
    reached = set().union(*(assert_captured_keys_resolve(_captured(*r[1:])) for r in _RUNS))
    assert location_strings("aut") - reached == set()


def test_the_classic_theme_prints_each_aut_line_as_the_source():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cases = {
        "locations.aut.showroom": (
            {},
            "Der Verkäufer zeigt dir eine Auswahl der Modelle. Zum Kauf die entsprechende"
            " Nummerntaste (0=Ende) drücken!",
        ),
        "locations.aut.model": (
            {"number": 4, "name": "Auburn Mod.120", "price": 6000},
            "4 Auburn Mod.120\n 6000 $",
        ),
        "locations.aut.model_prompt": ({}, ""),
        "system.not_enough_money": ({}, "Du hast zu wenig Kies!"),
        "locations.aut.trade_in_offer": (
            {"amount": 2000},
            "Man bietet dir 2000 $ für deine alte Schaukel.",
        ),
        "locations.aut.confirm": ({}, "OK (j/n)?"),
        "locations.aut.sold": ({}, "Der Verkäufer reicht dir Schlüssel und Papiere."),
        "locations.aut.crowded": ({}, "Es sind zu viele Leute hier!"),
        "locations.aut.steal_prompt": ({}, "Wer soll den Wagen aufbrechen:"),
        "locations.aut.stolen": (
            {},
            "Yeah! Die Karre ist offen! Du machst dich damit aus dem Staub.",
        ),
        "locations.aut.stolen_old_car_left": (
            {},
            "Yeah! Die Karre ist offen! Du machst dich damit aus dem Staub und musst leider"
            " deinen alten Wagen stehen lassen.",
        ),
        "locations.aut.caught": ({}, "Leider erwischt dich der Besitzer der Karre!"),
        "locations.aut.owner_killed": (
            {},
            "Nach dem Mord an dem Besitzer musst du verschwinden und den Wagen stehen lassen!",
        ),
        "turn.picker.gangster": (
            {
                "index": 2,
                "name": "luigi",
                "energie": 9,
                "kraft": 12,
                "intelligenz": 30,
                "brutalitaet": 5,
                "weapon": "Revolver",
            },
            "2 luigi\ne09 k12 i30 b05\nw:Revolver",
        ),
        "turn.picker.prompt": ({}, "Nummer:"),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_aut_strings_verbatim():
    """The C64-text fixture\'s menu and entry text, byte for byte from
    location-dialogue.yaml (aut), the 40-column wrap padding included."""
    assert fixture_value("locations.aut.entry_prompt") == (
        "EIN UMFANGREICHES SORTIMENT DER         FLOTTESTEN SCHLITTEN."
    )
    assert fixture_value("locations.aut.menu.buy") == "WAGEN KAUFEN"
    assert fixture_value("locations.aut.menu.steal") == "AUTO 'ORGANISIEREN'"
    assert fixture_value("locations.aut.menu.leave") == "WIEDER GEHEN"


def test_aut_screen_text_in_today_s_spelling():
    """The live classic theme's aut title, entry and menu text: the disk file's
    words in today's spelling, the wrap padding one space (the fold check ties
    them to the C64-text fixture above)."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.aut.title") == "Automobil-Händler"
    assert _get(data, "locations.aut.entry_prompt") == (
        "Ein umfangreiches Sortiment der flottesten Schlitten."
    )
    assert _get(data, "locations.aut.menu.buy") == "Wagen kaufen"
    assert _get(data, "locations.aut.menu.steal") == "Auto 'organisieren'"
    assert _get(data, "locations.aut.menu.leave") == "Wieder gehen"


def test_the_aut_doors_open_the_aut_shell_one_per_tile():
    """Cells 147, 278, 368 and 605 are aut's doors (``la=4``, ``ln`` 1..4)."""
    city = yaml.safe_load(_CITY.read_text(encoding="utf-8"))
    doors = [d for d in city["doors"] if d.get("location") == "aut"]
    assert [(d["cell"], d["la"], d["ln"]) for d in doors] == [
        (147, 4, 1),
        (278, 4, 2),
        (368, 4, 3),
        (605, 4, 4),
    ]
