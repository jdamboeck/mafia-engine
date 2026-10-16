"""Tests for the pol shell + classic-theme strings (``mf-prg.bas:21000-21255``).

The checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, the keys the handlers emit in their behavioural runs
(``tests/test_pol.py``) resolve in the classic theme and reach every pol string,
and the text is verbatim from the research (the SEQ menu src/pol, via
location-dialogue.yaml; the handler lines from the listing).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine.config_loader import load_config, load_game_config
from engine.locations import load_location
from engine.strings import Resolver
from tests.helpers import assert_captured_keys_resolve, location_strings
from tests.test_pol import _EXITS, _run, _state
from tests.text_fold import fixture_value

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

_CONFIG = load_game_config(_CONFIG_DIR)
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
    assert by_id["surrender"].handler is _CONFIG.handlers["pol.surrender"]
    assert by_id["bribe"].handler is _CONFIG.handlers["pol.bribe"]
    assert by_id["free"].handler is _CONFIG.handlers["pol.free"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_pol_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.pol.entry_prompt")
    for opt_id in ("surrender", "bribe", "free", "leave"):
        assert _get(data, f"locations.pol.menu.{opt_id}")


#: The behavioural runs whose captured keys are resolved: every exit of
#: ``tests/test_pol.py`` (id, option, other players, active fields, answers, rng draws).
_RUNS = [case[:6] for case in _EXITS]


def _captured(key, others, fields, answers, draws) -> list:
    state = _state(*(dict(o) for o in others), **fields)
    return _run(key, state, answers=answers, draws=draws)[1].seen


@pytest.mark.parametrize(
    ("key", "others", "fields", "answers", "draws"),
    [run[1:] for run in _RUNS],
    ids=[run[0] for run in _RUNS],
)
def test_the_keys_each_pol_run_emits_resolve_in_the_classic_theme(
    key, others, fields, answers, draws
):
    assert_captured_keys_resolve(_captured(key, others, fields, answers, draws))


def test_the_pol_runs_reach_every_pol_string():
    reached = set().union(*(assert_captured_keys_resolve(_captured(*r[1:])) for r in _RUNS))
    assert location_strings("pol") - reached == set()


def test_the_classic_theme_prints_each_pol_line_as_the_source():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cases = {
        "locations.pol.chief_offer": ({}, "Aha! Für einen Monat Untätigkeit"),
        "locations.pol.chief_prompt": ({}, "nehme ich 1000 $. Wie viele Monate:"),
        "locations.pol.chief_broke": ({}, "Sie sind leider nich flüssig!"),
        "locations.pol.chief_done": ({}, "Danke sehr! Nehmen Sie den Hinterausgang!"),
        "locations.pol.nobody_jailed": ({}, "Es ist niemand inhaftiert."),
        "locations.pol.free_prompt": ({}, "Wen willst du befreien:"),
        "locations.pol.free_entry": ({"index": 2, "name": "moran"}, "2 moran"),
        "locations.pol.free_entry_phantom": ({"index": 1}, "1 irgendjemanden"),
        "locations.pol.free_price": (
            {"price": 3500},
            "Du brauchst 3500 $, um die Wärter zu bestechen.",
        ),
        "locations.pol.confirm": ({}, "OK (j/n)?"),
        "locations.pol.freed": ({}, "Du konntest den Gefangenen befreien!"),
        "locations.pol.phantom_leaves": ({}, "Er bedankt sich und verschwindet..."),
        "locations.pol.thank_you": (
            {"name": "moran", "rescuer": "alcapone", "cash": 800},
            "moran! Du bist von alcapone aus dem Knast befreit worden. Wie viel zahlst du ihm"
            " zum Dank (0 - 800):",
        ),
        "locations.pol.thank_you_prompt": ({}, "?"),
        "system.not_enough_money": ({}, "Du hast zu wenig Kies!"),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_pol_strings_verbatim():
    """The C64-text fixture's menu, byte for byte from the SEQ file src/pol
    (location-dialogue.yaml, pol), the 40-column wrap padding included, "@" read as ","."""
    assert fixture_value("locations.pol.entry_prompt") == (
        "'WAS HABEN SIE HIER ZU SUCHEN? MACHEN   SIE, DASS SIE RAUS KOMMEN!'"
    )
    assert fixture_value("locations.pol.menu.surrender") == (
        "'ICH BIN EIN GESUCHTER GANGSTER! ICH    MOECHTE MICH STELLEN.'"
    )
    assert fixture_value("locations.pol.menu.bribe") == "POLIZEICHEF BESTECHEN"
    assert fixture_value("locations.pol.menu.free") == (
        "VERSUCHEN, INHAFTIERTEN GANGSTER ZU     BEFREIEN"
    )
    assert fixture_value("locations.pol.menu.leave") == "LIEBER GAR NICHTS TUN"


def test_pol_screen_text_in_today_s_spelling():
    """The live classic theme's pol title, entry and menu text: the disk file's
    words in today's spelling, the wrap padding one space (the fold check ties
    them to the C64-text fixture above)."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.pol.title") == "Polizei-Präsidium"
    assert _get(data, "locations.pol.entry_prompt") == (
        "'Was haben Sie hier zu suchen? Machen Sie, dass Sie raus kommen!'"
    )
    assert _get(data, "locations.pol.menu.surrender") == (
        "'Ich bin ein gesuchter Gangster! Ich möchte mich stellen.'"
    )
    assert _get(data, "locations.pol.menu.bribe") == "Polizeichef bestechen"
    assert _get(data, "locations.pol.menu.free") == "Versuchen, inhaftierten Gangster zu befreien"
    assert _get(data, "locations.pol.menu.leave") == "Lieber gar nichts tun"


def test_the_pol_door_opens_the_pol_shell_and_is_the_configured_door_cell():
    """Cell 910 is pol's one door (``la=11, ln=1``); the empty month answer's step is
    measured to it (``formula_params.pol_door_cell``)."""
    city = yaml.safe_load(_CITY.read_text(encoding="utf-8"))
    doors = [d for d in city["doors"] if d.get("location") == "pol"]
    assert [(d["cell"], d["la"], d["ln"]) for d in doors] == [(910, 11, 1)]
    params = load_config(_CONFIG_DIR / "config.yaml")["formula_params"]
    assert params["pol_door_cell"] == 910
