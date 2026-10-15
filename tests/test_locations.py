"""Tests for the YAML location-shell loader + HANDLERS registry (U6, PART 2).

Proof-first: written and observed RED (module missing) before
``engine/locations.py`` existed.

The loader is the declarative shell layer (docs/design/config-and-content-contract.md): it parses menu
structure, resolves handler ids against a registry, evaluates guards, and owns
denial (KTD-8) — a denied option is never entered and its ``on_denied`` key is
returned instead. It holds no display text (KTD-5).
"""

import pytest
import yaml

from engine.locations import (
    HANDLERS,
    Location,
    Option,
    available_options,
    load_location,
    register,
)
from engine.state import Clock, GameState, Player
from data.game_configs.mafia_1920s.gangster import Gangster


def _state(*, rank=1, roster=0, active=0, players=1):
    plist = [Player() for _ in range(players)]
    plist[active] = Player(rank=rank, roster=tuple(Gangster() for _ in range(roster)))
    return GameState(
        players=tuple(plist),
        clock=Clock(active_player=active, player_count=players),
    )


# A dummy handler registered under a test id to prove resolution.
@register("test.dummy")
def _dummy_handler(ctx):  # pragma: no cover - not invoked, only resolved
    yield


SHELL = """
key: pub
options:
  - id: recruit
    guard: {and: [{var: rank, op: '>', value: 4}, {var: gang_size, op: '<', value: 10}]}
    on_denied: "system.pub.rank_too_low"
    handler: "test.dummy"
  - id: leave
    resolve: {consequences: [{type: ms_change, amount: -5}]}
"""


def test_shell_parses_into_structured_objects():
    loc = load_location(yaml.safe_load(SHELL))
    assert isinstance(loc, Location)
    assert loc.key == "pub"
    assert len(loc.options) == 2

    recruit = loc.options[0]
    assert isinstance(recruit, Option)
    assert recruit.id == "recruit"
    assert recruit.guard is not None
    assert recruit.on_denied == "system.pub.rank_too_low"
    assert callable(recruit.handler)
    assert recruit.consequences is None

    leave = loc.options[1]
    assert leave.id == "leave"
    assert leave.handler is None
    assert leave.consequences == [{"type": "ms_change", "amount": -5}]


def test_handler_resolves_from_registry():
    loc = load_location(yaml.safe_load(SHELL))
    assert loc.options[0].handler is HANDLERS["test.dummy"]


def test_unregistered_handler_raises():
    bad = {"key": "x", "options": [{"id": "o", "handler": "does.not.exist"}]}
    with pytest.raises(ValueError):
        load_location(bad)


def test_option_with_neither_handler_nor_consequences_raises():
    bad = {"key": "x", "options": [{"id": "o"}]}
    with pytest.raises(ValueError):
        load_location(bad)


def test_option_with_both_handler_and_consequences_raises():
    bad = {
        "key": "x",
        "options": [
            {
                "id": "o",
                "handler": "test.dummy",
                "resolve": {"consequences": []},
            }
        ],
    }
    with pytest.raises(ValueError):
        load_location(bad)


def test_available_options_filters_by_guard_and_exposes_denial():
    loc = load_location(yaml.safe_load(SHELL))

    # rank 3 -> recruit guard denied; leave (no guard) always available
    denied_state = _state(rank=3, roster=3)
    avail = available_options(loc, denied_state, ln=None)
    ids = [o.id for o in avail]
    assert "recruit" not in ids
    assert "leave" in ids
    # denial key retrievable from the excluded option
    assert loc.options[0].on_denied == "system.pub.rank_too_low"

    # rank 5, room in gang -> recruit available
    ok_state = _state(rank=5, roster=3)
    avail_ok = available_options(loc, ok_state, ln=None)
    assert "recruit" in [o.id for o in avail_ok]


def test_flat_consequences_option_has_no_handler():
    loc = load_location(yaml.safe_load(SHELL))
    leave = next(o for o in loc.options if o.id == "leave")
    assert leave.handler is None
    assert leave.consequences == [{"type": "ms_change", "amount": -5}]


# --------------------------------------------------------------------------- #
# The one-key pick reaches 9 options: a positional menu caps there (KTD-10)   #
# --------------------------------------------------------------------------- #
def _shell(n: int, *, keyed: bool = False) -> dict:
    """A shell of ``n`` handler options; ``keyed`` gives each its own ``key``."""
    options = []
    for i in range(n):
        option = {"id": f"opt{i}", "handler": "test.dummy"}
        if keyed:
            option["key"] = f"k{i}"
        options.append(option)
    return {"key": "big", "options": options}


def test_a_positional_shell_of_10_options_is_refused_at_load():
    # A location menu is picked by one key, "1".."9": a tenth option is unreachable.
    assert len(load_location(_shell(9)).options) == 9
    with pytest.raises(ValueError, match=r"10 options"):
        load_location(_shell(10))


def test_a_keyed_menu_of_10_options_loads():
    # A menu that names its keys (the turn menu) is not picked by position.
    assert len(load_location(_shell(10, keyed=True)).options) == 10


def test_every_mafia_shell_and_menu_loads():
    from pathlib import Path

    from engine.config_loader import load_game_config

    config_dir = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
    config = load_game_config(config_dir)
    shell_files = {p.stem for p in (config_dir / "content" / "locations").glob("*.yaml")}
    assert set(config.shells) == shell_files and len(shell_files) == 12
    assert "turn" in config.menus
