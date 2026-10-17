"""Tests for the ban shell + classic-theme strings (``mf-prg.bas:20000-20060``).

The checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, the keys the handlers emit in their behavioural runs
(``tests/test_ban.py``) resolve in the classic theme and reach every ban string,
and the text is verbatim from the research corpus (location-dialogue.yaml, ban, with
its 40-column wrap padding; the corpus misses :20003 and :20009, which are the
listing's).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine.config_loader import load_game_config
from engine.locations import load_location
from engine.strings import Resolver
from tests.helpers import assert_captured_keys_resolve, location_strings
from tests.test_ban import _EXITS, _LA, _SURRENDER, _SURRENDER_FIGHT, _run, _state
from tests.text_fold import fixture_value

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

_CONFIG = load_game_config(_CONFIG_DIR)
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
    assert by_id["holdup"].handler is _CONFIG.handlers["ban.holdup"]
    assert by_id["safe"].handler is _CONFIG.handlers["ban.safe"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_ban_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.ban.entry_prompt")
    for opt_id in ("holdup", "safe", "leave"):
        assert _get(data, f"locations.ban.menu.{opt_id}")


#: The behavioural runs whose captured keys are resolved: every exit of
#: ``tests/test_ban.py`` (id, option, state fields, answers, rng draws), and a revisit
#: of the same bank tile, which :20004 sends into the shop's police trap.
_RUNS = [case[:5] for case in _EXITS] + [
    (
        "20004-revisit",
        "holdup",
        {"ln": 2, "previous": (_LA, 2)},
        (_SURRENDER_FIGHT, _SURRENDER),
        (0, 0, 0),
    ),
]

#: The screen ban borrows: :20004 jumps into the shop's trap.
_BORROWED = {"locations.sgl.police_waiting"}


def _captured(key, fields, answers, draws) -> list:
    return _run(key, _state(**fields), answers=answers, draws=draws)[1].seen


@pytest.mark.parametrize(
    ("key", "fields", "answers", "draws"),
    [run[1:] for run in _RUNS],
    ids=[run[0] for run in _RUNS],
)
def test_the_keys_each_ban_run_emits_resolve_in_the_classic_theme(key, fields, answers, draws):
    assert_captured_keys_resolve(_captured(key, fields, answers, draws))


def test_the_ban_runs_reach_every_ban_string_and_the_borrowed_screen():
    reached = set().union(*(assert_captured_keys_resolve(_captured(*r[1:])) for r in _RUNS))
    assert (location_strings("ban") | _BORROWED) - reached == set()


def test_the_classic_theme_prints_each_ban_line_as_the_source():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cases = {
        "locations.ban.rank_too_low": ({"rank": "Kleiner Fisch"}, "Werde erst 'Kleiner Fisch'!"),
        "locations.ban.alone": ({}, "Du brauchst einen Begleiter!"),
        "locations.ban.guards": ({}, "Leider hast du die drei Wachmänner am Eingang übersehen..."),
        "locations.ban.loot": ({"p": 5234}, "Du hast es geschafft! Deine Beute beträgt 5234 $!"),
        "locations.ban.safe_untrained": ({}, "Du musst noch trainieren!"),
        "locations.ban.safe_who": ({}, "Wer soll den Kasten knacken:"),
        "locations.ban.safe_stethoscope": (
            {},
            "'Hhm. Mal sehen. Ich versuch's mit dem Stethoskop...' (Drehen der Coderäder mit"
            " F1, F3 und F5!)",
        ),
        "locations.ban.safe_dials": ({"d1": 1, "d2": 2, "d3": 3}, "1  2  3"),
        "locations.ban.safe_turn": ({}, "F1, F3, F5:"),
        "locations.ban.safe_f1": ({}, "F1"),
        "locations.ban.safe_f3": ({}, "F3"),
        "locations.ban.safe_f5": ({}, "F5"),
        "locations.ban.safe_click": ({"d1": 1, "d2": 2, "d3": 4}, "1  2  4   *klick*"),
        "locations.ban.safe_slip": ({"d1": 0, "d2": 2, "d3": 4}, "0  2  4"),
        "locations.ban.safe_failed": (
            {},
            "'Teufel...! Da ist was schiefgegangen! Es kommt jemand!'",
        ),
        # :20004 jumps into the shop's trap: its screen is the shop's.
        "locations.sgl.police_waiting": ({}, "Vor dem Laden erwartet dich die Polizei!"),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_ban_strings_verbatim():
    """The C64-text fixture\'s menu and entry text, byte for byte from
    location-dialogue.yaml (ban), the 40-column wrap padding included."""
    assert fixture_value("locations.ban.entry_prompt") == (
        "'GUTEN TAG, MEIN HERR! WAS IST IHR      BEGEHR?'"
    )
    assert fixture_value("locations.ban.menu.holdup") == (
        "'QUATSCH NICH' UND LANG DEN ZASTER      'RUEBER, ABER SCHNELL!'"
    )
    assert fixture_value("locations.ban.menu.safe") == (
        "UNAUFFAELLIG NACH ALARMANLAGEN UM-      SEHEN UND NACHTS EINBRECHEN"
    )
    assert fixture_value("locations.ban.menu.leave") == "WIEDER GEHEN"


def test_ban_screen_text_in_today_s_spelling():
    """The live classic theme's ban title, entry and menu text: the disk file's
    words in today's spelling, the wrap padding one space (the fold check ties
    them to the C64-text fixture above)."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.ban.title") == "Bank/Postamt"
    assert _get(data, "locations.ban.entry_prompt") == (
        "'Guten Tag, mein Herr! Was ist Ihr Begehr?'"
    )
    assert _get(data, "locations.ban.menu.holdup") == (
        "'Quatsch nich' und lang den Zaster 'rüber, aber schnell!'"
    )
    assert _get(data, "locations.ban.menu.safe") == (
        "Unauffällig nach Alarmanlagen umsehen und nachts einbrechen"
    )
    assert _get(data, "locations.ban.menu.leave") == "Wieder gehen"


def test_the_ban_doors_open_the_ban_shell_one_per_tile():
    """Cells 95, 437, 442, 657 and 865 are the bank's doors (``la=10``, ``ln`` 1..5)."""
    city = yaml.safe_load(_CITY.read_text(encoding="utf-8"))
    doors = [d for d in city["doors"] if d.get("location") == "ban"]
    cells = (95, 437, 442, 657, 865)
    assert [(d["cell"], d["la"], d["ln"]) for d in doors] == [
        (cell, 10, ln) for ln, cell in enumerate(cells, start=1)
    ]
