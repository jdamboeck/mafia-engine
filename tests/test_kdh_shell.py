"""Tests for the kdh shell + classic-theme strings (U11 declarative layer).

kdh has SIX menu options: five handler-backed (borrow/repay/trade/capital/collect,
all guardless at the shell level — every refusal lives inside the handler body, per
``pub.drink``/``pub.tip``/``pub.job``'s established shape) plus a guardless leave.
This checks the shell loads and resolves every handler id, and that the keys
``handlers/kdh.py`` emits in its behavioural runs (``tests/test_kdh.py``) resolve in the
classic theme with the params they carry and reach every kdh string.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from data.game_configs.mafia_1920s.gangster import Gangster
from data.game_configs.mafia_1920s.state import Business
from engine.combat import STEP_RIGHT
from engine.config_loader import load_game_config
from engine.locations import HANDLERS, load_location
from engine.rng import Rng
from tests.helpers import (
    StubRng,
    assert_captured_keys_resolve,
    location_strings,
    run_pure,
    scripted,
)
from tests.test_kdh import _EXITS, _OWN, _player, _state
from tests.text_fold import fixture_value

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

# Load the config by path so "kdh.*" handlers register before the shell resolves them.
_CONFIG = load_game_config(_CONFIG_DIR)
_KDH_SHELL = _CONFIG_DIR / "content" / "locations" / "kdh.yaml"
_KDH_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "kdh.yaml"


def _get(data, dotted):
    if dotted in data:
        return data[dotted]
    node = data
    for part in dotted.split("."):
        node = node[part]
    return node


def test_kdh_shell_loads_and_resolves_all_five_handlers():
    loc = load_location(yaml.safe_load(_KDH_SHELL.read_text(encoding="utf-8")))
    assert loc.key == "kdh"
    ids = [o.id for o in loc.options]
    assert ids == ["borrow", "repay", "trade", "capital", "collect", "leave"]

    expected_handlers = {
        "borrow": "kdh.borrow",
        "repay": "kdh.repay",
        "trade": "kdh.trade",
        "capital": "kdh.capital",
        "collect": "kdh.collect",
    }
    for opt_id, handler_id in expected_handlers.items():
        opt = next(o for o in loc.options if o.id == opt_id)
        assert opt.handler is _CONFIG.handlers[handler_id]

    leave = next(o for o in loc.options if o.id == "leave")
    assert leave.handler is None  # guardless leave via resolve/consequences, no handler


def test_kdh_options_carry_no_shell_guard():
    """Every refusal (no existing debt, bounds, rival scan, own-this-tile) lives
    INSIDE the handler body — none of the five playable options has a shell-level
    guard (matches pub.drink/pub.tip/pub.job's shape, KTD-8)."""
    loc = load_location(yaml.safe_load(_KDH_SHELL.read_text(encoding="utf-8")))
    for opt in loc.options:
        if opt.id == "leave":
            continue
        assert opt.guard is None, f"{opt.id} unexpectedly carries a shell guard"


def test_kdh_shell_menu_strings_present():
    data = yaml.safe_load(_KDH_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.kdh.entry_prompt")
    for opt_id in ("borrow", "repay", "trade", "capital", "collect", "leave"):
        assert _get(data, f"locations.kdh.menu.{opt_id}")


_STRONG = (Gangster(name="p0", energie=100, kraft=50, brutalitaet=50, weapon=8),)

#: The behavioural runs whose captured keys are resolved: every exit of
#: ``tests/test_kdh.py`` (id, option, each player's kwargs, rng draws or ``None`` for
#: the seeded engine RNG, answers), and the paths no exit ends on: a rival's shop
#: (:15107), a purchase into the capital screen (:15120-15125) and the ambush lost and
#: won (:15312-15321; seed 0 reaches the win with the aimed shots).
_RUNS = [(case[0], case[1], (case[2],), case[3], case[4]) for case in _EXITS] + [
    (
        "15107-rival-owner",
        "kdh.trade",
        ({"last_location": 1}, {"name": "p1", "business": Business(shop_tile=1)}),
        (),
        (),
    ),
    ("15125-bought", "kdh.trade", ({"ka": 100000, "last_location": 1},), (0,), (True, 0)),
    ("15315-ambush-lost", "kdh.collect", ({"ka": 1000, **_OWN},), (1,), ("surrender",)),
    (
        "15321-ambush-won",
        "kdh.collect",
        ({"ka": 1000, "roster": _STRONG, **_OWN},),
        None,
        (*[("shoot", STEP_RIGHT)] * 40, "surrender"),
    ),
]


def _captured(handler, players, draws, answers) -> list:
    src = scripted(*answers)
    rng = Rng(0) if draws is None else StubRng(*draws)
    run_pure(HANDLERS[handler], src, state=_state([_player(**p) for p in players]), rng=rng)
    return src.seen


@pytest.mark.parametrize(
    ("handler", "players", "draws", "answers"),
    [run[1:] for run in _RUNS],
    ids=[run[0] for run in _RUNS],
)
def test_the_keys_each_kdh_run_emits_resolve_in_the_classic_theme(handler, players, draws, answers):
    assert_captured_keys_resolve(_captured(handler, players, draws, answers))


def test_the_kdh_runs_reach_every_kdh_string():
    reached = set().union(*(assert_captured_keys_resolve(_captured(*r[1:])) for r in _RUNS))
    assert location_strings("kdh") - reached == set()


def test_kdh_strings_verbatim():
    """The C64-text fixture's text, as :15010-15306 print it: no quote marks around the
    narration."""
    assert (
        fixture_value("locations.kdh.pay_old_debts_first")
        == "zahle erstmal deine alten schulden ab!"
    )
    assert fixture_value("locations.kdh.borrow_grace_notice") == (
        "du hast 6 monate zeit, die schulden zurueck zu zahlen!"
    )
    assert fixture_value("locations.kdh.repay_full") == "du hast die schulden zurueckgezahlt!"
    assert fixture_value("locations.kdh.already_own_a_shop") == "du hast schon ein kreditgeschaeft!"
    assert (
        fixture_value("locations.kdh.pay_own_debts_first")
        == "zahle erstmal deine eigenen schulden!"
    )
    assert fixture_value("locations.kdh.bought") == "der laden gehoert nun dir!"
    assert fixture_value("locations.kdh.not_your_shop") == "dieser laden gehoert dir nicht!"
    assert (
        fixture_value("locations.kdh.debts_paid_on_time")
        == "alle schuldner haben puenktlich gezahlt!"
    )


def test_kdh_screen_text_in_today_s_spelling():
    """The live classic theme's kdh title, entry and menu text: the disk file's
    words in today's spelling, the wrap padding one space (the fold check ties
    them to the C64-text fixture above)."""
    data = yaml.safe_load(_KDH_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.kdh.title") == "Kredit-Hai"
    assert _get(data, "locations.kdh.entry_prompt") == (
        "'Ein wunderschönen guten Tag, kann ich Ihnen helfen?'"
    )
    assert _get(data, "locations.kdh.menu.borrow") == "'Ja. Ich brauche ein bisschen Knete!'"
    assert _get(data, "locations.kdh.menu.repay") == (
        "'Nein, nein! Ich will nur meine Schulden abzahlen!'"
    )
    assert _get(data, "locations.kdh.menu.trade") == "Laden aufkaufen bzw. verkaufen"
    assert _get(data, "locations.kdh.menu.capital") == (
        "Kapital im Laden ändern (bei eigenem Kreditgeschäft)"
    )
    assert _get(data, "locations.kdh.menu.collect") == (
        "Überfällige Schulden eintreiben (bei eigenem Geschäft)"
    )
    assert _get(data, "locations.kdh.menu.leave") == "'Ich helfe mir selbst.'"


def test_kdh_upkeep_shop_income_strings_present():
    """The upkeep shop-income slot (U11) reuses the shared upkeep.yaml strings file,
    not kdh's own — checked here since it is this unit's income slot, not U3/U8's."""
    upkeep_strings = _CONFIG_DIR / "themes" / "classic" / "strings" / "upkeep.yaml"
    data = yaml.safe_load(upkeep_strings.read_text(encoding="utf-8"))
    assert _get(data, "upkeep.shop_income_quiet") == "in deinem kreditinstitut herrscht flaute."
    assert "{amount}" in _get(data, "upkeep.shop_income_earned")


def test_kdh_doors_reach_both_map_cells():
    """The kdh doors at cells 221 and 753 (U1 audit finding) both resolve to kdh."""
    city_raw = yaml.safe_load(
        (_CONFIG_DIR / "content" / "map" / "city.yaml").read_text(encoding="utf-8")
    )
    kdh_doors = {d["cell"]: d for d in city_raw["doors"] if d.get("location") == "kdh"}
    assert 221 in kdh_doors
    assert 753 in kdh_doors
