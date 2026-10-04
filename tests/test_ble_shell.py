"""Tests for the ble shell + classic-theme strings (``mf-prg.bas:22000-22120``).

The four checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, every key the handlers emit resolves with its params,
and the text is verbatim from the research corpus (location-dialogue.yaml, ble).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.locations import HANDLERS, load_location
from engine.strings import Resolver

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

load_game_config(_CONFIG_DIR)
_SHELL = _CONFIG_DIR / "content" / "locations" / "ble.yaml"
_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "ble.yaml"
_CITY = _CONFIG_DIR / "content" / "map" / "city.yaml"


def _get(data, dotted):
    node = data
    for part in dotted.split("."):
        node = node[part]
    return node


def _shell():
    return load_location(yaml.safe_load(_SHELL.read_text(encoding="utf-8")))


def test_ble_shell_resolves_both_handlers_and_a_guardless_leave():
    loc = _shell()
    assert loc.key == "ble"
    assert [o.id for o in loc.options] == ["passport", "counterfeit", "leave"]
    by_id = {o.id: o for o in loc.options}
    assert by_id["passport"].handler is HANDLERS["ble.passport"]
    assert by_id["counterfeit"].handler is HANDLERS["ble.counterfeit"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_ble_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.ble.entry_prompt")
    for opt_id in ("passport", "counterfeit", "leave"):
        assert _get(data, f"locations.ble.menu.{opt_id}")


def test_every_key_the_handlers_emit_resolves_with_its_params():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cases = {
        "locations.ble.passport_for_one": ({}, "gut, mein sohn. fuer einen pass"),
        "locations.ble.passport_for_many": ({"count": 3}, "gut, mein sohn. fuer 3 paesse"),
        "locations.ble.passport_price": ({"price": 3000}, "macht das 3000 $."),
        "locations.ble.confirm": ({}, "ok (j/n)?"),
        "locations.ble.passport_done": ({}, "hier, noch druckfrisch, he, he!"),
        "locations.ble.counterfeit_reluctant": ({}, "nng...hoechst ungern! wieviel dollar"),
        "locations.ble.counterfeit_prompt": ({}, "willst du anlegen (0-5000)"),
        "locations.ble.counterfeit_offer": (
            {"amount": 1234},
            "ich gebe dir 1234 $ blueten dafuer.",
        ),
        "system.not_enough_money": ({}, "du hast zu wenig kies!"),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_ble_strings_verbatim():
    """Menu and entry text, byte for byte from location-dialogue.yaml (ble), the
    40-column wrap padding included."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.ble.entry_prompt") == (
        "'NA, MEIN JUNGE, WO DRUECKT DER SCHUH?  KANN ICH DIR HELFEN?'"
    )
    assert _get(data, "locations.ble.menu.passport") == (
        "'YEAH, KANNST DU MIR EINEN NEUEN PASS   MACHEN? ER MUSS GUT SEIN, KLAR?'"
    )
    assert _get(data, "locations.ble.menu.counterfeit") == (
        "'DRUCKST DU NOCH BLUETEN? WIE WAERS,    WENN DU MIR EIN PAAR VERKAUFST?'"
    )
    assert _get(data, "locations.ble.menu.leave") == (
        "'DANKE, EDDIE, VIELLEICHT BEIM NAECHS-  TEN MAL. BYE!'"
    )


def test_the_ble_door_opens_the_ble_shell():
    """Cell 371 is ble's one door (``la=12, ln=1``)."""
    city = yaml.safe_load(_CITY.read_text(encoding="utf-8"))
    doors = [d for d in city["doors"] if d.get("location") == "ble"]
    assert [(d["cell"], d["la"], d["ln"]) for d in doors] == [(371, 12, 1)]
