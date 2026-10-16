"""Tests for the sph shell + classic-theme strings (U5, the declarative layer).

The sph handler's game menu lives INSIDE the handler (a PromptChoice), so the shell
is a single guardless "play" entry routing to the "sph" handler plus a guardless
"leave". This unit checks the shell loads and resolves its handler id, and that the
keys sph.py emits in its behavioural runs (``tests/test_sph.py``) resolve in the classic
theme with the params they carry and reach every sph string.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from engine.config_loader import load_game_config
from engine.interactions import CANCEL
from engine.locations import load_location
from tests.helpers import StubRng, assert_captured_keys_resolve, location_strings
from tests.test_sph import _sph_exit
from tests.text_fold import fixture_value

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"

# Load the config by path so "sph" registers before the shell resolves its handler id.
_CONFIG = load_game_config(_CONFIG_DIR)
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
    assert play.handler is _CONFIG.handlers["sph"]


#: The behavioural runs whose captured keys are resolved: each exit of
#: ``tests/test_sph.py``'s ``_sph_exit`` (id, answers, rng draws) -- nothing chosen
#: (:16015), no stake (:16020), a stake above the cash (:16025), a lost and a won hand
#: (:16035, :16040) -- and a hand of each other game.
_RUNS = [
    ("16015-nothing", (CANCEL,), ()),
    ("16020-no-stake", (0, 0), ()),
    ("16025-too-poor", (0, 6000), ()),
    ("16035-lost", (0, 100), (1,)),
    ("16040-won", (0, 100), (0,)),
    ("16040-blackjack", (1, 100), (0,)),
    ("16040-roulette", (2, 100), (0,)),
]


def _captured(answers, draws) -> list:
    src, _ = _sph_exit(answers, StubRng(*draws))
    return src.seen


@pytest.mark.parametrize(
    ("answers", "draws"), [run[1:] for run in _RUNS], ids=[run[0] for run in _RUNS]
)
def test_the_keys_each_sph_run_emits_resolve_in_the_classic_theme(answers, draws):
    assert_captured_keys_resolve(_captured(answers, draws))


def test_the_sph_runs_reach_every_sph_string():
    reached = set().union(*(assert_captured_keys_resolve(_captured(*r[1:])) for r in _RUNS))
    assert location_strings("sph") - reached == set()


def test_sph_shell_menu_strings_present():
    data = yaml.safe_load(_SPH_STRINGS.read_text(encoding="utf-8"))
    assert _get(data, "locations.sph.entry_prompt")
    assert _get(data, "locations.sph.menu.play")
    assert _get(data, "locations.sph.menu.leave")


def test_sph_strings_verbatim():
    assert fixture_value("locations.sph.game_menu") == "waehle, mein freund:"
    assert fixture_value("locations.sph.poker") == "1 - poker"
    assert fixture_value("locations.sph.blackjack") == "2 - black jack"
    assert fixture_value("locations.sph.roulette") == "3 - roulette"
    assert fixture_value("locations.sph.at_the_table") == "du begibst dich an den spieltisch..."
    assert fixture_value("locations.sph.won") == "du hast{amount}$ gewonnen!"
    assert fixture_value("locations.sph.lost") == "leider verloren!"
