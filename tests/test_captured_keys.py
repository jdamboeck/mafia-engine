"""Self-tests of the captured-key resolver (``tests/helpers.py``).

The location shell tests resolve the keys their behavioural runs capture through
:func:`tests.helpers.assert_captured_keys_resolve`. These pin that it fails, naming the
key, where the theme lacks a key or a template asks for a param the emission does not
carry, and which keys each interaction type hands it.
"""

from __future__ import annotations

import pytest

from engine.interactions import (
    Acknowledge,
    CombatScreen,
    Confirm,
    Heading,
    LoadSubState,
    LocationMenu,
    PromptChoice,
    PromptInt,
    PromptText,
    RollFrame,
    ShowMessage,
    StartCombat,
    TurnMenu,
)
from engine.turns import KEY_WAIT_SCREEN, STANDINGS_SCREEN, UPKEEP_SCREEN
from tests.helpers import assert_captured_keys_resolve, location_strings, theme_emissions


def test_a_key_the_theme_lacks_fails_naming_the_key():
    with pytest.raises(pytest.fail.Exception, match="locations.ble.missing_price"):
        assert_captured_keys_resolve([ShowMessage("locations.ble.missing_price", {"price": 1})])


def test_a_key_missing_a_template_param_fails_naming_the_key():
    # The template is "macht das{price}$." -- the emission carries no price.
    with pytest.raises(pytest.fail.Exception, match="locations.ble.passport_price"):
        assert_captured_keys_resolve([ShowMessage("locations.ble.passport_price", {})])


def test_a_missing_param_in_an_acknowledged_line_fails_naming_the_line_key():
    screen = Acknowledge("gang_war.screen", {"lines": [("locations.ble.passport_price", {})]})
    with pytest.raises(pytest.fail.Exception, match="locations.ble.passport_price"):
        assert_captured_keys_resolve([screen])


def test_resolved_keys_are_returned():
    captured = [
        ShowMessage("locations.ble.passport_price", {"price": 1000}),
        ("system.not_enough_money", {}),
    ]
    assert assert_captured_keys_resolve(captured) == {
        "locations.ble.passport_price",
        "system.not_enough_money",
    }


def test_each_interaction_type_hands_over_the_keys_a_client_resolves():
    captured = [
        ShowMessage("a.message", {"n": 1}),
        PromptInt("a.int", 0, 5, params={"cash": 2}),
        PromptText("a.text", {"number": 3}),
        RollFrame("a.roll", {"value": 4}),
        Confirm("a.confirm"),
        PromptChoice("a.menu", options=["a.one", 2, "a name"], prompt="a.prompt"),
        Acknowledge("a.screen", {"lines": [("a.line", {"p": 5})]}),
        Acknowledge("a.own_screen", {"name": "x"}),
        Acknowledge(KEY_WAIT_SCREEN),
        Acknowledge(STANDINGS_SCREEN, {"lines": [("a.row", {})]}),
        Heading(UPKEEP_SCREEN),
        Heading("a.heading"),
        CombatScreen(message="begins"),
        CombatScreen(message={"hit": True, "damage": 3}),
        LocationMenu("ble", ("passport", "leave"), ln=1),
        TurnMenu(options=("walk",), keys=("1",)),
        StartCombat(),
        LoadSubState("kind", {"p": 1}),
    ]
    assert theme_emissions(captured) == [
        ("a.message", {"n": 1}),
        ("a.int", {"cash": 2}),
        ("a.text", {"number": 3}),
        ("a.roll", {"value": 4}),
        ("a.confirm", {}),
        ("a.menu", {}),
        ("a.one", {}),
        ("a.prompt", {}),
        ("a.line", {"p": 5}),
        ("a.own_screen", {"name": "x"}),
        ("a.row", {}),
        ("a.heading", {}),
        ("combat.begins", {}),
        ("locations.ble.title", {}),
        ("locations.ble.entry_prompt", {}),
        ("locations.ble.menu.passport", {}),
        ("locations.ble.menu.leave", {}),
        ("turn.menu.option.walk", {}),
    ]


def test_location_strings_leave_out_the_shells_own():
    strings = location_strings("ble")
    assert "locations.ble.passport_price" in strings
    assert not any(k.endswith((".title", ".entry_prompt")) or ".menu." in k for k in strings)
