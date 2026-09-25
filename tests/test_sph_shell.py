"""Tests for the sph shell + classic-theme strings (U5, the declarative layer).

The sph handler's game menu lives INSIDE the handler (a PromptChoice), so the shell
is a single guardless "play" entry routing to the "sph" handler plus a guardless
"leave". This unit checks the shell loads and resolves its handler id, and that every
string key sph.py emits resolves in the classic theme.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.config_loader import load_game_config
from engine.locations import HANDLERS, load_location

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

# Load the config by path so "sph" registers before the shell resolves its handler id.
load_game_config(_CONFIG_DIR)
_SPH_SHELL = _CONFIG_DIR / "content" / "locations" / "sph.yaml"
_SPH_STRINGS = _CONFIG_DIR / "themes" / "classic" / "strings" / "sph.yaml"


def _get(data, dotted):
    if dotted in data:
        return data[dotted]
    node = data
    for part in dotted.split("."):
        node = node[part]
    return node


def test_sph_shell_loads_and_resolves_handler():
    loc = load_location(yaml.safe_load(_SPH_SHELL.read_text(encoding="utf-8")))
    assert loc.key == "sph"
    ids = [o.id for o in loc.options]
    assert "play" in ids
    assert "leave" in ids
    play = next(o for o in loc.options if o.id == "play")
    # The shell resolved the "sph" handler to the registered callable.
    assert play.handler is HANDLERS["sph"]


def test_sph_strings_resolve_for_every_key_the_handler_emits():
    data = yaml.safe_load(_SPH_STRINGS.read_text(encoding="utf-8"))
    # Keys sph.py yields (game menu + option labels + wager/resolve lines).
    for key in [
        "locations.sph.game_menu",
        "locations.sph.poker",
        "locations.sph.blackjack",
        "locations.sph.roulette",
        "locations.sph.cash",
        "locations.sph.wager_prompt",
        "locations.sph.at_the_table",
        "locations.sph.won",
        "locations.sph.lost",
    ]:
        assert _get(data, key), f"missing string for {key}"
    # Template params present.
    assert "{cash}" in _get(data, "locations.sph.cash")
    assert "{amount}" in _get(data, "locations.sph.won")


def test_sph_shell_menu_strings_present():
    data = yaml.safe_load(_SPH_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.sph.entry_prompt")
    assert _get(data, "locations.sph.menu.play")
    assert _get(data, "locations.sph.menu.leave")


def test_sph_strings_verbatim():
    data = yaml.safe_load(_SPH_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.sph.game_menu") == "waehle, mein freund:"
    assert _get(data, "locations.sph.poker") == "1 - poker"
    assert _get(data, "locations.sph.blackjack") == "2 - black jack"
    assert _get(data, "locations.sph.roulette") == "3 - roulette"
    assert _get(data, "locations.sph.at_the_table") == "du begibst dich an den spieltisch..."
    assert _get(data, "locations.sph.won") == "du hast {amount}$ gewonnen!"
    assert _get(data, "locations.sph.lost") == "leider verloren!"
