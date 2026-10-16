"""Tests for the bhf shell + classic-theme strings (``mf-prg.bas:19000-19050``).

The checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, the keys the handlers emit in their behavioural runs
(``tests/test_bhf.py``) resolve in the classic theme and reach every bhf string,
and the text is verbatim from the research corpus (location-dialogue.yaml, bhf; the
corpus misses :19015 and :19016, which are the listing's).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine.config_loader import load_game_config
from engine.locations import load_location
from engine.strings import Resolver
from tests.helpers import assert_captured_keys_resolve, location_strings
from tests.test_bhf import _EXITS, _run, _state, _won_fight
from tests.text_fold import fixture_value

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

_CONFIG = load_game_config(_CONFIG_DIR)
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
    assert by_id["pub"].handler is _CONFIG.handlers["bhf.pub"]
    assert by_id["pickpocket"].handler is _CONFIG.handlers["bhf.pickpocket"]
    assert by_id["mail_train"].handler is _CONFIG.handlers["bhf.mail_train"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_bhf_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.bhf.entry_prompt")
    for opt_id in ("pub", "pickpocket", "mail_train", "leave"):
        assert _get(data, f"locations.bhf.menu.{opt_id}")


#: The behavioural runs whose captured keys are resolved: every exit of
#: ``tests/test_bhf.py`` (id, option, state fields, answers, rng draws, whether the
#: guards' fight is a stand-in win), a won mail train paying out on the bank's screen
#: (:19040 goto20050) and the station's handbag (:19050 w=1 into :18046).
_RUNS = [(*case[:5], False) for case in _EXITS] + [
    ("19040-loot", "mail_train", {}, (), (1234,), True),
    ("19050-handbag", "pickpocket", {}, (1,), (0, 10, 0), False),
]

#: The screens bhf borrows: :19040's is the bank's, :19050's the subway's.
_BORROWED = {"locations.ban.loot", "locations.sub.thief_prompt", "locations.sub.loot_handbag"}


def _captured(monkeypatch, key, fields, answers, draws, won) -> list:
    with monkeypatch.context() as patch:
        if won:
            _won_fight(patch)
        return _run(key, _state(**fields), answers=answers, draws=draws)[1].seen


@pytest.mark.parametrize(
    ("key", "fields", "answers", "draws", "won"),
    [run[1:] for run in _RUNS],
    ids=[run[0] for run in _RUNS],
)
def test_the_keys_each_bhf_run_emits_resolve_in_the_classic_theme(
    monkeypatch, key, fields, answers, draws, won
):
    assert_captured_keys_resolve(_captured(monkeypatch, key, fields, answers, draws, won))


def test_the_bhf_runs_reach_every_bhf_string_and_the_borrowed_screens(monkeypatch):
    reached = set().union(
        *(assert_captured_keys_resolve(_captured(monkeypatch, *r[1:])) for r in _RUNS)
    )
    assert (location_strings("bhf") | _BORROWED) - reached == set()


def test_the_classic_theme_prints_each_bhf_line_as_the_source():
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
        # :19040 goto20050: the payout's screen is the bank's.
        "locations.ban.loot": (
            {"p": 8234},
            "du hast es geschafft! deine beute\nbetraegt 8234 $!",
        ),
        # :19050 jumps into the subway's body: its screens are the subway's.
        "locations.sub.thief_prompt": ({}, "welchen spieler setzt du als dieb ein:"),
        "locations.sub.loot_handbag": ({}, "...eine handtasche - nichts wertvolles!"),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_bhf_strings_verbatim():
    """The C64-text fixture's menu and entry text, byte for byte from
    location-dialogue.yaml (bhf)."""
    assert fixture_value("locations.bhf.entry_prompt") == "DU BIST IM BAHNHOF VON CHICKAGO."
    assert fixture_value("locations.bhf.menu.pub") == "BAHNHOFSKNEIPE AUFSUCHEN"
    assert fixture_value("locations.bhf.menu.pickpocket") == "TASCHENDIEBSTAHL"
    assert fixture_value("locations.bhf.menu.mail_train") == "POSTZUG UEBERFALLEN"
    assert fixture_value("locations.bhf.menu.leave") == "NICHTS WIE WEG"


def test_bhf_screen_text_in_today_s_spelling():
    """The live classic theme's bhf title, entry and menu text: the disk file's
    words in today's spelling, the wrap padding one space (the fold check ties
    them to the C64-text fixture above)."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.bhf.title") == "Railway-Station (Bahnhof)"
    assert _get(data, "locations.bhf.entry_prompt") == "Du bist im Bahnhof von Chickago."
    assert _get(data, "locations.bhf.menu.pub") == "Bahnhofskneipe aufsuchen"
    assert _get(data, "locations.bhf.menu.pickpocket") == "Taschendiebstahl"
    assert _get(data, "locations.bhf.menu.mail_train") == "Postzug überfallen"
    assert _get(data, "locations.bhf.menu.leave") == "Nichts wie weg"


def test_the_bhf_door_opens_the_bhf_shell():
    """Cell 68 is the station's one door (``la=9``, ``ln=1``)."""
    city = yaml.safe_load(_CITY.read_text(encoding="utf-8"))
    doors = [d for d in city["doors"] if d.get("location") == "bhf"]
    assert [(d["cell"], d["la"], d["ln"]) for d in doors] == [(68, 9, 1)]
