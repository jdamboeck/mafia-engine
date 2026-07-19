"""Tests for the kdh shell + classic-theme strings (U11 declarative layer).

kdh has SIX menu options: five handler-backed (borrow/repay/trade/capital/collect,
all guardless at the shell level — every refusal lives inside the handler body, per
``pub.drink``/``pub.tip``/``pub.job``'s established shape) plus a guardless leave.
This checks the shell loads and resolves every handler id, and that every string key
``handlers/kdh.py`` emits resolves in the classic theme.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.locations import HANDLERS, load_location

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

# Load the config by path so "kdh.*" handlers register before the shell resolves them.
load_game_config(_CONFIG_DIR)
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
        assert opt.handler is HANDLERS[handler_id]

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


def test_kdh_strings_resolve_for_every_key_the_handler_emits():
    data = yaml.safe_load(_KDH_STRINGS.read_text(encoding="utf-8"))
    for key in [
        "locations.kdh.pay_old_debts_first",
        "locations.kdh.borrow_prompt",
        "locations.kdh.borrow_grace_notice",
        "locations.kdh.repay_prompt",
        "locations.kdh.repay_partial",
        "locations.kdh.repay_full",
        "locations.kdh.already_own_a_shop",
        "locations.kdh.pay_own_debts_first",
        "locations.kdh.shop_belongs_to",
        "locations.kdh.buy_offer",
        "locations.kdh.buy_confirm",
        "locations.kdh.bought",
        "locations.kdh.sell_offer",
        "locations.kdh.sell_confirm",
        "locations.kdh.not_your_shop",
        "locations.kdh.capital_status",
        "locations.kdh.capital_prompt",
        "locations.kdh.debts_paid_on_time",
        "locations.kdh.ambush_intro",
        "locations.kdh.ambush_loot",
    ]:
        assert _get(data, key), f"missing string for {key}"
    # Template params present.
    assert "{remaining}" in _get(data, "locations.kdh.repay_partial")
    assert "{name}" in _get(data, "locations.kdh.shop_belongs_to")
    assert "{price}" in _get(data, "locations.kdh.buy_offer")
    assert "{price}" in _get(data, "locations.kdh.sell_offer")
    assert "{capital}" in _get(data, "locations.kdh.capital_status")
    assert "{max}" in _get(data, "locations.kdh.capital_status")
    assert "{amount}" in _get(data, "locations.kdh.ambush_loot")


def test_kdh_strings_verbatim():
    data = yaml.safe_load(_KDH_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.kdh.pay_old_debts_first") == "'zahle erstmal deine alten schulden ab!'"
    assert _get(data, "locations.kdh.borrow_grace_notice") == (
        "'du hast 6 monate zeit, die schulden zurueck zu zahlen!'"
    )
    assert _get(data, "locations.kdh.repay_full") == "'du hast die schulden zurueckgezahlt!'"
    assert _get(data, "locations.kdh.already_own_a_shop") == "'du hast schon ein kreditgeschaeft!'"
    assert _get(data, "locations.kdh.pay_own_debts_first") == "'zahle erstmal deine eigenen schulden!'"
    assert _get(data, "locations.kdh.bought") == "'der laden gehoert nun dir!'"
    assert _get(data, "locations.kdh.not_your_shop") == "'dieser laden gehoert dir nicht!'"
    assert _get(data, "locations.kdh.debts_paid_on_time") == "'alle schuldner haben puenktlich gezahlt!'"


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
