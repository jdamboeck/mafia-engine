"""Tests for the aut shell + classic-theme strings (``mf-prg.bas:14000-14131``).

The four checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, every key the handlers emit resolves with its params,
and the text is verbatim from the research corpus (location-dialogue.yaml, aut).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.locations import HANDLERS, load_location
from engine.strings import Resolver

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

load_game_config(_CONFIG_DIR)
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
    assert by_id["buy"].handler is HANDLERS["aut.buy"]
    assert by_id["steal"].handler is HANDLERS["aut.steal"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_aut_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.aut.entry_prompt")
    for opt_id in ("buy", "steal", "leave"):
        assert _get(data, f"locations.aut.menu.{opt_id}")


def test_every_key_the_handlers_emit_resolves_with_its_params():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cases = {
        "locations.aut.showroom": (
            {},
            "der verkaeufer zeigt dir eine auswahl\nder modelle. zum kauf die entsprechende\n"
            "nummerntaste (0=ende) druecken!",
        ),
        "locations.aut.model": (
            {"number": 4, "name": "auburn mod.120", "price": 6000},
            "4 auburn mod.120\n 6000 $",
        ),
        "locations.aut.model_prompt": ({}, "nummerntaste (0=ende) druecken!"),
        "system.not_enough_money": ({}, "du hast zu wenig kies!"),
        "locations.aut.trade_in_offer": (
            {"amount": 2000},
            "man bietet dir 2000 $ fuer deine alte\nschaukel.",
        ),
        "locations.aut.confirm": ({}, "ok (j/n)?"),
        "locations.aut.sold": ({}, "der verkaeufer reicht dir schluessel\nund papiere."),
        "locations.aut.crowded": ({}, "es sind zuviele leute hier!"),
        "locations.aut.steal_prompt": ({}, "wer soll den wagen aufbrechen:"),
        "locations.aut.stolen": (
            {},
            "yeah! die karre ist offen! du machst\ndich damit aus dem staub.",
        ),
        "locations.aut.stolen_old_car_left": (
            {},
            "yeah! die karre ist offen! du machst\ndich damit aus dem staub und musst lei-\n"
            "der deinen alten wagen stehen lassen.",
        ),
        "locations.aut.caught": ({}, "leider erwischt dich der besitzer der\nkarre!"),
        "locations.aut.owner_killed": (
            {},
            "nach dem mord an dem besitzer musst du\nverschwinden und den wagen stehen\nlassen!",
        ),
        "turn.picker.gangster": (
            {
                "index": 2,
                "name": "luigi",
                "energie": 9,
                "kraft": 12,
                "intelligenz": 30,
                "brutalitaet": 5,
                "weapon": "revolver",
            },
            "2 luigi\ne09 k12 i30 b05\nw:revolver",
        ),
        "turn.picker.prompt": ({}, "nummer:"),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_aut_strings_verbatim():
    """Menu and entry text, byte for byte from location-dialogue.yaml (aut), the
    40-column wrap padding included."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.aut.entry_prompt") == (
        "EIN UMFANGREICHES SORTIMENT DER         FLOTTESTEN SCHLITTEN."
    )
    assert _get(data, "locations.aut.menu.buy") == "WAGEN KAUFEN"
    assert _get(data, "locations.aut.menu.steal") == "AUTO 'ORGANISIEREN'"
    assert _get(data, "locations.aut.menu.leave") == "WIEDER GEHEN"


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
