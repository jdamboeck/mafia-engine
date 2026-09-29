"""Tests for the bhf shell + classic-theme strings (``mf-prg.bas:19000-19050``).

The four checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, every key the handlers emit resolves with its params,
and the text is verbatim from the research corpus (location-dialogue.yaml, bhf; the
corpus misses :19015 and :19016, which are the listing's).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.locations import HANDLERS, load_location
from engine.strings import Resolver

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

load_game_config(_CONFIG_DIR)
_SHELL = _CONFIG_DIR / "content" / "locations" / "bhf.yaml"
_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "bhf.yaml"
_CITY = _CONFIG_DIR / "content" / "map" / "city.yaml"


def _get(data, dotted):
    node = data
    for part in dotted.split("."):
        node = node[part]
    return node


def _shell():
    return load_location(yaml.safe_load(_SHELL.read_text(encoding="utf-8")))


def test_bhf_shell_resolves_its_handlers_and_a_guardless_leave():
    loc = _shell()
    assert loc.key == "bhf"
    assert [o.id for o in loc.options] == ["pub", "pickpocket", "mail_train", "leave"]
    by_id = {o.id: o for o in loc.options}
    assert by_id["pub"].handler is HANDLERS["bhf.pub"]
    assert by_id["pickpocket"].handler is HANDLERS["bhf.pickpocket"]
    assert by_id["mail_train"].handler is HANDLERS["bhf.mail_train"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_bhf_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.bhf.entry_prompt")
    for opt_id in ("pub", "pickpocket", "mail_train", "leave"):
        assert _get(data, f"locations.bhf.menu.{opt_id}")


def test_every_key_the_handlers_emit_resolves_with_its_params():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cases = {
        "locations.bhf.no_train": ({}, "kein postzug zu sehen..."),
        "locations.bhf.too_few": ({}, "du hast zu wenig gangster!"),
        "locations.bhf.storm": (
            {},
            "du stuermst in den panzerwaggon und\n"
            "beginnst gerade einzusacken, als du auf\n"
            "drei nette herren aufmerksam wirst...",
        ),
        "locations.bhf.loot": (
            {"p": 8234},
            "du hast es geschafft! deine beute\nbetraegt 8234$!",
        ),
        # :19050 jumps into the subway's body: its screens are the subway's.
        "locations.sub.thief_prompt": ({}, "welchen spieler setzt du als dieb ein:"),
        "locations.sub.loot_handbag": ({}, "...eine handtasche - nichts wertvolles!"),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_bhf_strings_verbatim():
    """Menu and entry text, byte for byte from location-dialogue.yaml (bhf)."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.bhf.entry_prompt") == "DU BIST IM BAHNHOF VON CHICKAGO."
    assert _get(data, "locations.bhf.menu.pub") == "BAHNHOFSKNEIPE AUFSUCHEN"
    assert _get(data, "locations.bhf.menu.pickpocket") == "TASCHENDIEBSTAHL"
    assert _get(data, "locations.bhf.menu.mail_train") == "POSTZUG UEBERFALLEN"
    assert _get(data, "locations.bhf.menu.leave") == "NICHTS WIE WEG"


def test_the_bhf_door_opens_the_bhf_shell():
    """Cell 68 is the station's one door (``la=9``, ``ln=1``)."""
    city = yaml.safe_load(_CITY.read_text(encoding="utf-8"))
    doors = [d for d in city["doors"] if d.get("location") == "bhf"]
    assert [(d["cell"], d["la"], d["ln"]) for d in doors] == [(68, 9, 1)]
