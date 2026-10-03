"""Tests for the ban shell + classic-theme strings (``mf-prg.bas:20000-20060``).

The four checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, every key the handlers emit resolves with its params,
and the text is verbatim from the research corpus (location-dialogue.yaml, ban, with
its 40-column wrap padding; the corpus misses :20003 and :20009, which are the
listing's).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.locations import HANDLERS, load_location
from engine.strings import Resolver

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

load_game_config(_CONFIG_DIR)
_SHELL = _CONFIG_DIR / "content" / "locations" / "ban.yaml"
_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "ban.yaml"
_CITY = _CONFIG_DIR / "content" / "map" / "city.yaml"


def _get(data, dotted):
    node = data
    for part in dotted.split("."):
        node = node[part]
    return node


def _shell():
    return load_location(yaml.safe_load(_SHELL.read_text(encoding="utf-8")))


def test_ban_shell_resolves_its_handlers_and_a_guardless_leave():
    loc = _shell()
    assert loc.key == "ban"
    assert [o.id for o in loc.options] == ["holdup", "safe", "leave"]
    by_id = {o.id: o for o in loc.options}
    assert by_id["holdup"].handler is HANDLERS["ban.holdup"]
    assert by_id["safe"].handler is HANDLERS["ban.safe"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_ban_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.ban.entry_prompt")
    for opt_id in ("holdup", "safe", "leave"):
        assert _get(data, f"locations.ban.menu.{opt_id}")


def test_every_key_the_handlers_emit_resolves_with_its_params():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cases = {
        "locations.ban.rank_too_low": ({"rank": "kleiner fisch"}, "werde erst 'kleiner fisch'!"),
        "locations.ban.alone": ({}, "du brauchst einen begleiter!"),
        "locations.ban.guards": (
            {},
            "leider hast du die drei wachmaenner am\neingang uebersehen...",
        ),
        "locations.ban.loot": (
            {"p": 5234},
            "du hast es geschafft! deine beute\nbetraegt 5234$!",
        ),
        # :20004 jumps into the shop's trap: its screen is the shop's.
        "locations.sgl.police_waiting": ({}, "vor dem laden erwartet dich die\npoliyei!"),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_ban_strings_verbatim():
    """Menu and entry text, byte for byte from location-dialogue.yaml (ban), the
    40-column wrap padding included."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.ban.entry_prompt") == (
        "'GUTEN TAG, MEIN HERR! WAS IST IHR      BEGEHR?'"
    )
    assert _get(data, "locations.ban.menu.holdup") == (
        "'QUATSCH NICH' UND LANG DEN ZASTER      'RUEBER, ABER SCHNELL!'"
    )
    assert _get(data, "locations.ban.menu.safe") == (
        "UNAUFFAELLIG NACH ALARMANLAGEN UM-      SEHEN UND NACHTS EINBRECHEN"
    )
    assert _get(data, "locations.ban.menu.leave") == "WIEDER GEHEN"


def test_the_ban_doors_open_the_ban_shell_one_per_tile():
    """Cells 95, 437, 442, 657 and 865 are the bank's doors (``la=10``, ``ln`` 1..5)."""
    city = yaml.safe_load(_CITY.read_text(encoding="utf-8"))
    doors = [d for d in city["doors"] if d.get("location") == "ban"]
    cells = (95, 437, 442, 657, 865)
    assert [(d["cell"], d["la"], d["ln"]) for d in doors] == [
        (cell, 10, ln) for ln, cell in enumerate(cells, start=1)
    ]
