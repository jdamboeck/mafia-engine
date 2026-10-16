"""Tests for the sub shell + classic-theme strings (``mf-prg.bas:18000-18052``).

The checks every location shell gets (``tests/test_kdh_shell.py``): the handlers
resolve, the menu strings exist, the keys the handlers emit in their behavioural runs
(``tests/test_sub.py``) resolve in the classic theme and reach every sub string,
and the text is verbatim from the research corpus (location-dialogue.yaml, sub).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine.config_loader import load_config, load_game_config
from engine.locations import load_location
from engine.strings import Resolver
from tests.helpers import assert_captured_keys_resolve, location_strings
from tests.test_sub import _EXITS, _FREE, _NO_MANUAL, _run, _state

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

_CONFIG = load_game_config(_CONFIG_DIR)
_SHELL = _CONFIG_DIR / "content" / "locations" / "sub.yaml"
_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "sub.yaml"
_CITY = _CONFIG_DIR / "content" / "map" / "city.yaml"


def _get(data, dotted):
    node = data
    for part in dotted.split("."):
        node = node[part]
    return node


def _shell():
    return load_location(yaml.safe_load(_SHELL.read_text(encoding="utf-8")))


def test_sub_shell_resolves_both_handlers_and_a_guardless_leave():
    loc = _shell()
    assert loc.key == "sub"
    assert [o.id for o in loc.options] == ["platform", "train", "leave"]
    by_id = {o.id: o for o in loc.options}
    assert by_id["platform"].handler is _CONFIG.handlers["sub.platform"]
    assert by_id["train"].handler is _CONFIG.handlers["sub.train"]
    assert by_id["leave"].handler is None
    for opt in loc.options:
        assert opt.guard is None, f"{opt.id} carries a shell guard"


def test_sub_menu_strings_present():
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.sub.entry_prompt")
    for opt_id in ("platform", "train", "leave"):
        assert _get(data, f"locations.sub.menu.{opt_id}")


#: The behavioural runs whose captured keys are resolved: every exit of
#: ``tests/test_sub.py`` (id, option, state fields, answers, rng draws), and the loot
#: lines the exits do not reach (:18046, :18048-18050; the handbag is the station's).
_RUNS = [case[:5] for case in _EXITS] + [
    ("18046-handbag", "sub.platform", {"last_la": 9}, (1,), (_NO_MANUAL, _FREE, 0)),
    ("18048-pearls", "sub.platform", {}, (1,), (_NO_MANUAL, _FREE, 1)),
    ("18049-watch", "sub.platform", {}, (1,), (_NO_MANUAL, _FREE, 2)),
    ("18050-wallet", "sub.platform", {}, (1,), (_NO_MANUAL, _FREE, 3)),
]


def _captured(key, fields, answers, draws) -> list:
    return _run(key, _state(**fields), answers=answers, draws=draws)[1].seen


@pytest.mark.parametrize(
    ("key", "fields", "answers", "draws"),
    [run[1:] for run in _RUNS],
    ids=[run[0] for run in _RUNS],
)
def test_the_keys_each_sub_run_emits_resolve_in_the_classic_theme(key, fields, answers, draws):
    assert_captured_keys_resolve(_captured(key, fields, answers, draws))


def test_the_sub_runs_reach_every_sub_string():
    reached = set().union(*(assert_captured_keys_resolve(_captured(*r[1:])) for r in _RUNS))
    assert location_strings("sub") - reached == set()


def test_the_classic_theme_prints_each_sub_line_as_the_source():
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    cases = {
        "locations.sub.ticket": ({"price": 50}, "ein u-bahn-ticket kostet dich\n50 $."),
        "locations.sub.confirm": ({}, "ok (j/n)?"),
        "system.not_enough_money": ({}, "du hast zu wenig kies!"),
        "locations.sub.thief_prompt": ({}, "welchen spieler setzt du als dieb ein:"),
        "locations.sub.stealing": ({}, "du stiehlst..."),
        "locations.sub.caught": ({}, "...nichts! denn du wirst erwischt!"),
        "locations.sub.loot_handbag": ({}, "...eine handtasche - nichts wertvolles!"),
        "locations.sub.loot_camera": ({}, "...'nen fotoapparat! er bringt 50 $."),
        "locations.sub.loot_pearls": ({}, "...eine falsche perlenkette!"),
        "locations.sub.loot_watch": ({}, "...eine armbanduhr fuer 100 $!"),
        "locations.sub.loot_wallet": ({}, "...'ne brieftasche mit 500 $!"),
        "locations.sub.loot_diamond": ({}, "...einen diamanten! der ist 800 $ wert!"),
        "locations.sub.loot_manual": ({}, "...eine anleitung -der safeknacker- ??!"),
        "turn.picker.prompt": ({}, "nummer:"),
    }
    for key, (params, text) in cases.items():
        assert resolver.resolve(key, params) == text, key


def test_every_loot_item_in_the_config_has_its_string():
    """The loot table (``formula_params.sub_loot``) names the key the handler emits."""
    resolver = Resolver.from_config(_CONFIG_DIR, theme="classic")
    loot = load_config(_CONFIG_DIR / "config.yaml")["formula_params"]["sub_loot"]
    assert [cash for _, cash in loot] == [0, 50, 0, 100, 500, 800]  # :18046-18051
    for item, _ in loot:
        assert resolver.resolve(f"locations.sub.loot_{item}", {}).startswith("...")


def test_sub_strings_verbatim():
    """Menu and entry text, byte for byte from location-dialogue.yaml (sub), the
    40-column wrap padding included."""
    data = yaml.safe_load(_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.sub.entry_prompt") == (
        "DU BIST IN EINEM GROSSEN U-BAHNHOF. 'NE MENGE LEUTE TREIBEN SICH HIER 'RUM..."
    )
    assert _get(data, "locations.sub.menu.platform") == (
        "LEUTEN AUF DEM BAHNSTEIG DIE TASCHEN    ERLEICHTERN"
    )
    assert _get(data, "locations.sub.menu.train") == "IN DER BAHN DIE TASCHENINHALTE PRUEFEN"
    assert _get(data, "locations.sub.menu.leave") == "ZURUECK ANS TAGESLICHT"


def test_the_sub_doors_open_the_sub_shell_one_per_tile():
    """Cells 149, 167 and 511 are sub's doors (``la=8``, ``ln`` 1..3)."""
    city = yaml.safe_load(_CITY.read_text(encoding="utf-8"))
    doors = [d for d in city["doors"] if d.get("location") == "sub"]
    assert [(d["cell"], d["la"], d["ln"]) for d in doors] == [
        (149, 8, 1),
        (167, 8, 2),
        (511, 8, 3),
    ]
