"""Tests for the ble shell + classic-theme strings (``mf-prg.bas:22000-22120``).

The checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, the keys the handlers emit in their behavioural runs
(``tests/test_ble.py``'s exits) resolve in the classic theme with the params they carry
and reach every ble string, and the text is verbatim from the research corpus
(location-dialogue.yaml, ble).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine.config_loader import load_game_config
from engine.locations import HANDLERS, load_location
from engine.strings import Resolver
from tests.helpers import (
    StubRng,
    assert_captured_keys_resolve,
    location_strings,
    run_pure,
    scripted,
)
from tests.test_ble import _EXITS, _state

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

_CONFIG = load_game_config(_CONFIG_DIR)
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
    assert by_id["passport"].handler is _CONFIG.handlers["ble.passport"]
    assert by_id["counterfeit"].handler is _CONFIG.handlers["ble.counterfeit"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_ble_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.ble.entry_prompt")
    for opt_id in ("passport", "counterfeit", "leave"):
        assert _get(data, f"locations.ble.menu.{opt_id}")


#: The behavioural runs whose captured keys are resolved: every exit of
#: ``tests/test_ble.py`` (id, option, state kwargs, answers, rng draws), and an offer
#: to a gang of three (:22011's plural).
_RUNS = [case[:5] for case in _EXITS] + [
    ("22011-for-many", "ble.passport", {"gang": 3}, (True,), ()),
]


def _captured(key, kwargs, answers, draws) -> list:
    src = scripted(*answers)
    run_pure(HANDLERS[key], src, state=_state(**kwargs), rng=StubRng(*draws))
    return src.seen


@pytest.mark.parametrize(
    ("key", "kwargs", "answers", "draws"),
    [run[1:] for run in _RUNS],
    ids=[run[0] for run in _RUNS],
)
def test_the_keys_each_ble_run_emits_resolve_in_the_classic_theme(key, kwargs, answers, draws):
    assert_captured_keys_resolve(_captured(key, kwargs, answers, draws))


def test_the_ble_runs_reach_every_ble_string():
    reached = set().union(*(assert_captured_keys_resolve(_captured(*r[1:])) for r in _RUNS))
    assert location_strings("ble") - reached == set()


def test_the_classic_theme_prints_each_ble_line_as_the_source():
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
