"""Tests for the pol shell + classic-theme strings (``mf-prg.bas:21000-21255``).

The four checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, every key the handlers emit resolves with its params,
and the text is verbatim from the research (the SEQ menu src/pol, via
location-dialogue.yaml; the handler lines from the listing).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_config, load_game_config
from engine.locations import HANDLERS, load_location
from engine.strings import Resolver

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

load_game_config(_CONFIG_DIR)
_SHELL = _CONFIG_DIR / "content" / "locations" / "pol.yaml"
_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "pol.yaml"
_CITY = _CONFIG_DIR / "content" / "map" / "city.yaml"


def _get(data, dotted):
    node = data
    for part in dotted.split("."):
        node = node[part]
    return node


def _shell():
    return load_location(yaml.safe_load(_SHELL.read_text(encoding="utf-8")))


def test_pol_shell_resolves_its_handlers_and_a_guardless_leave():
    loc = _shell()
    assert loc.key == "pol"
    assert [o.id for o in loc.options] == ["surrender", "bribe", "free", "leave"]
    by_id = {o.id: o for o in loc.options}
    assert by_id["surrender"].handler is HANDLERS["pol.surrender"]
    assert by_id["bribe"].handler is HANDLERS["pol.bribe"]
    assert by_id["free"].handler is HANDLERS["pol.free"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_pol_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.pol.entry_prompt")
    for opt_id in ("surrender", "bribe", "free", "leave"):
        assert _get(data, f"locations.pol.menu.{opt_id}")


def test_every_key_the_handlers_emit_resolves_with_its_params():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cases = {
        "locations.pol.chief_offer": ({}, "aha! fuer einen monat untaetigkeit"),
        "locations.pol.chief_prompt": ({}, "nehme ich 1000 $. wieviele monate:"),
        "locations.pol.chief_broke": ({}, "sie sind leider nich fluessig!"),
        "locations.pol.chief_done": ({}, "danke sehr! nehmen sie den hinter-\nausgang!"),
        "locations.pol.nobody_jailed": ({}, "es ist niemand inhaftiert."),
        "locations.pol.free_prompt": ({}, "wen willst du befreien:"),
        "locations.pol.free_entry": ({"index": 2, "name": "moran"}, "2 moran"),
        "locations.pol.free_entry_phantom": ({"index": 1}, "1 irgendjemanden"),
        "locations.pol.free_price": (
            {"price": 3500},
            "du brauchst 3500$, um die waerter zu\nbestechen.",
        ),
        "locations.pol.confirm": ({}, "ok (j/n)?"),
        "locations.pol.freed": ({}, "du konntest den gefangenen befreien!"),
        "locations.pol.phantom_leaves": ({}, "er bedankt sich und verschwindet..."),
        "locations.pol.thank_you": (
            {"name": "moran", "rescuer": "alcapone", "cash": 800},
            "moran!\ndu bist von alcapone aus dem\nknast befreit worden. wieviel zahlst du\n"
            "ihm zum dank (0 - 800):",
        ),
        "locations.pol.thank_you_prompt": ({}, "?"),
        "system.not_enough_money": ({}, "du hast zu wenig kies!"),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_pol_strings_verbatim():
    """The menu byte for byte from the SEQ file src/pol (location-dialogue.yaml, pol),
    the 40-column wrap padding included, "@" read as ","."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.pol.entry_prompt") == (
        "'WAS HABEN SIE HIER ZU SUCHEN? MACHEN   SIE, DASS SIE RAUS KOMMEN!'"
    )
    assert _get(data, "locations.pol.menu.surrender") == (
        "'ICH BIN EIN GESUCHTER GANGSTER! ICH    MOECHTE MICH STELLEN.'"
    )
    assert _get(data, "locations.pol.menu.bribe") == "POLIZEICHEF BESTECHEN"
    assert _get(data, "locations.pol.menu.free") == (
        "VERSUCHEN, INHAFTIERTEN GANGSTER ZU     BEFREIEN"
    )
    assert _get(data, "locations.pol.menu.leave") == "LIEBER GAR NICHTS TUN"


def test_the_pol_door_opens_the_pol_shell_and_is_the_configured_door_cell():
    """Cell 910 is pol's one door (``la=11, ln=1``); the empty month answer's step is
    measured to it (``formula_params.pol_door_cell``)."""
    city = yaml.safe_load(_CITY.read_text(encoding="utf-8"))
    doors = [d for d in city["doors"] if d.get("location") == "pol"]
    assert [(d["cell"], d["la"], d["ln"]) for d in doors] == [(910, 11, 1)]
    params = load_config(_CONFIG_DIR / "config.yaml")["formula_params"]
    assert params["pol_door_cell"] == 910
