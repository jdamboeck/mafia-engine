"""U32 — this game's state lives in declared value maps, its guard vocabulary in a registry.

``engine/state`` holds no game entity or game field: the mafia config declares its
per-player and global state in ``state_schema.yaml``, and ``state.py`` wraps the maps in
typed accessors. The guard variables its shells use (``rank``, ``tenancy``, ...) are
registered by the config; the engine resolves none by name
(docs/design/engine-architecture.md § Engine/config seam).

The round-trip and shape tests read the schema, so a key a later unit declares is
covered without a new test.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest
import yaml

from engine import conditions, persistence
from engine.conditions import GUARD_VARIABLES, build_context, evaluate
from engine.config_loader import load_game_config
from engine.locations import available_options, load_location
from engine.state import GameState, ValueSpec
from engine.effects import commit
from data.game_configs.mafia_1920s.effects import SetTenancy
from tests.helpers import run_pure, scripted, with_player
import data.game_configs.mafia_1920s.state as game

_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_DIR = _ROOT / "data" / "game_configs" / "mafia_1920s"
_ENGINE_STATE = _ROOT / "engine" / "state" / "__init__.py"
_PUB_SHELL = _CONFIG_DIR / "content" / "locations" / "pub.yaml"

_LOADED = load_game_config(_CONFIG_DIR)
_SCHEMA = _LOADED.state_schema

_PLAYER_KEYS = sorted(_SCHEMA.player)
_GLOBAL_KEYS = sorted(_SCHEMA.global_)


def _new_game() -> GameState:
    return _LOADED.new_game(
        seed=7, end_year=1930, score_weight=1.0, players=[("al", "outfit"), ("bugs", "north")]
    )


def _other_value(spec: ValueSpec) -> Any:
    """A value of ``spec``'s declared type that differs from its default."""
    if spec.type is bool:
        return not spec.default
    if spec.type is str:
        return spec.default + "-changed"
    return spec.default + spec.type(7)


def _same_value_other_type(spec: ValueSpec) -> Any:
    """A value Python's ``==`` equates with the default, of a different type."""
    if spec.type is int:
        return float(spec.default)
    if spec.type is float:
        return int(spec.default)
    return int(spec.default)  # bool: False == 0


# --------------------------------------------------------------------------- #
# Save round-trip, one case per declared key                                  #
# --------------------------------------------------------------------------- #
def test_the_schema_declares_keys_to_cover():
    """Guards the parametrized tests below against going vacuous."""
    assert len(_PLAYER_KEYS) >= 19 and len(_GLOBAL_KEYS) >= 39
    # The loader and the config's accessors read the one schema file.
    assert dict(_SCHEMA.player) == dict(game.SCHEMA.player)
    assert dict(_SCHEMA.global_) == dict(game.SCHEMA.global_)


@pytest.mark.parametrize("key", _PLAYER_KEYS)
def test_every_player_key_survives_a_save_round_trip(key, tmp_path):
    spec = _SCHEMA.player[key]
    state = _new_game()
    value = _other_value(spec)
    state = with_player(state, 1, values={**state.players[1].values, key: value})

    path = tmp_path / "game.jsonl"
    persistence.save_game(
        path, state, registries=_LOADED.registries, effect_log=[], rng_log=[], seed=7
    )
    loaded = persistence.load_game(path, _LOADED.registries).state

    assert type(loaded.players[1].values[key]) is spec.type
    assert loaded.players[1].values[key] == value
    assert loaded == state


@pytest.mark.parametrize("key", _GLOBAL_KEYS)
def test_every_global_key_survives_a_save_round_trip(key, tmp_path):
    spec = _SCHEMA.global_[key]
    state = _new_game()
    value = _other_value(spec)
    state = dataclasses.replace(state, values={**state.values, key: value})

    path = tmp_path / "game.jsonl"
    persistence.save_game(
        path, state, registries=_LOADED.registries, effect_log=[], rng_log=[], seed=7
    )
    loaded = persistence.load_game(path, _LOADED.registries).state

    assert type(loaded.values[key]) is spec.type
    assert loaded.values[key] == value
    assert loaded == state


# --------------------------------------------------------------------------- #
# run_pure's type fingerprint sees drift inside a value map, key by key       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", [k for k in _PLAYER_KEYS if _SCHEMA.player[k].type is not str])
def test_run_pure_sees_a_type_change_of_a_declared_player_key(key):
    spec = _SCHEMA.player[key]
    drifted = _same_value_other_type(spec)
    assert drifted == spec.default  # value equality alone cannot see the drift

    def drifting_handler(ctx):
        player = ctx.state.players[0]
        object.__setattr__(player, "values", MappingProxyType({**player.values, key: drifted}))
        return []
        yield  # pragma: no cover - make this a generator

    with pytest.raises(AssertionError, match="changed the TYPE"):
        run_pure(drifting_handler, scripted(), state=_new_game())


# --------------------------------------------------------------------------- #
# Guard variables are the config's                                            #
# --------------------------------------------------------------------------- #
def _pub():
    """The pub shell with its recruit option guarded ``rank > 4 and gang_size < 10``.

    This config's shells guard no option (the source refuses inside the handlers,
    #122), so the guard-variable registry is exercised on this probe copy.
    """
    raw = yaml.safe_load(_PUB_SHELL.read_text(encoding="utf-8"))
    for option in raw["options"]:
        if option["id"] == "recruit":
            option["guard"] = {
                "and": [
                    {"var": "rank", "op": ">", "value": 4},
                    {"var": "gang_size", "op": "<", "value": 10},
                ]
            }
    return load_location(raw)


def _option_ids(state: GameState) -> list[str]:
    return [o.id for o in available_options(_pub(), state, ln=1)]


def test_a_config_registered_guard_variable_filters_a_menu_option():
    """A recruit guard (``rank > 4``, ``gang_size < 10``) reads the config's ``rank``."""
    rank_4 = with_player(_new_game(), 0, rank=4)
    rank_5 = with_player(_new_game(), 0, rank=5)
    assert "recruit" not in _option_ids(rank_4)
    assert "recruit" in _option_ids(rank_5)


def test_the_guard_reads_whatever_the_config_registered(monkeypatch):
    """The engine resolves ``rank`` only through the registry: swap the resolver, and
    the same shell guard filters differently."""
    rank_4 = with_player(_new_game(), 0, rank=4)
    monkeypatch.setitem(GUARD_VARIABLES, "rank", lambda state, ln: 9)
    assert "recruit" in _option_ids(rank_4)


def test_the_engine_resolves_no_guard_variable_on_its_own(monkeypatch):
    """With the config's registrations gone, a guard naming ``rank`` is an unknown
    variable: nothing in ``engine/`` backs it."""
    monkeypatch.setattr(conditions, "GUARD_VARIABLES", {})
    with pytest.raises(ValueError, match="unknown guard variable 'rank'"):
        _option_ids(_new_game())


def test_an_unregistered_guard_variable_in_a_shell_is_refused_naming_it():
    shell = load_location(
        {
            "key": "probe",
            "options": [
                {
                    "id": "x",
                    "guard": {"var": "no_such_var", "op": "=", "value": 0},
                    "handler": "slw.rent",
                }
            ],
        }
    )
    with pytest.raises(ValueError, match="no_such_var"):
        available_options(shell, _new_game(), ln=1)


def test_the_config_registers_every_guard_variable_its_shells_use():
    paths = [
        *(_CONFIG_DIR / "content" / "locations").glob("*.yaml"),
        *(_CONFIG_DIR / "content" / "menus").glob("*.yaml"),
    ]
    assert paths, "no shell found: the scan is vacuous"
    used = set()
    for path in paths:
        used |= set(re.findall(r"var: (\w+)", path.read_text(encoding="utf-8")))
    assert used <= set(_LOADED.guard_variables)


def test_no_shell_of_this_config_guards_an_option():
    """The source's menus are fixed: every option is always shown and refuses inside
    its handler (``:3030`` prints the file's ``aw`` options with no precondition), so a
    guard -- which hides an option and renumbers the ones after it -- has no source
    counterpart here (#122)."""
    shells = [*_LOADED.shells.values(), *_LOADED.menus.values()]
    assert shells
    assert [(s.key, o.id) for s in shells for o in s.options if o.guard is not None] == []


def test_the_tenancy_guard_reads_the_global_value_map():
    """``tenancy`` is the tenant's 0-based index, -1 for a vacant room (#146): a vacant
    room and player 0's room read apart, as ``uk(ln)=0`` and ``uk(ln)=1`` do in the
    1-based source (:10010 ``ifuk(ln)<>0``)."""
    state = _new_game()
    ctx = build_context(state, ln=2)
    assert evaluate({"var": "tenancy", "op": "=", "value": -1}, ctx) is True  # vacant
    assert evaluate({"var": "tenancy", "op": "=", "value": 0}, ctx) is False
    own = commit(state, [SetTenancy(ln=2)]).state  # :10040 uk(ln)=sp, sp = player 0
    assert game.tenant(own, 2) == 0
    ctx = build_context(own, ln=2)
    assert evaluate({"var": "tenancy", "op": "=", "value": 0}, ctx) is True
    assert evaluate({"var": "tenancy", "op": "=", "value": -1}, ctx) is False
    rented = dataclasses.replace(state, values={**state.values, "tenancy.2": 1})
    ctx = build_context(rented, ln=2)
    assert evaluate({"var": "tenancy", "op": "=", "value": 1}, ctx) is True


# --------------------------------------------------------------------------- #
# The engine names no game state                                              #
# --------------------------------------------------------------------------- #
#: The game entities and fields that moved out of ``engine/state`` (U2's table).
_MOVED_NAMES = (
    "Job",
    "Debt",
    "Business",
    "Contraband",
    "Wanted",
    "MapState",
    "Flags",
    "gang_name",
    "nr",
    "tip_target",
    "safe_skill",
    "rented_months",
    "score_mult",
    "action_costs",
    "speed",
    "hired_gangsters",
    "graphics_mode",
)


def _schema_words() -> set[str]:
    """Every declared key, and each dotted key's entity prefix (``debt`` of ``debt.amount``)."""
    keys = set(_SCHEMA.player) | set(_SCHEMA.global_)
    return keys | {k.split(".", 1)[0] for k in keys}


@pytest.mark.parametrize("name", sorted(set(_MOVED_NAMES) | _schema_words()))
def test_engine_state_names_no_game_entity_or_field(name):
    source = _ENGINE_STATE.read_text(encoding="utf-8")
    assert not re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", source), (
        f"engine/state/__init__.py names {name!r}, which is this game's state"
    )


def test_no_engine_module_spells_a_declared_key():
    keys = set(_SCHEMA.player) | set(_SCHEMA.global_)
    offenders = [
        f"{path.relative_to(_ROOT)}: {key}"
        for path in sorted((_ROOT / "engine").rglob("*.py"))
        for key in keys
        if "." in key and key in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# --------------------------------------------------------------------------- #
# The schema agrees with the data its indexed keys mirror                     #
# --------------------------------------------------------------------------- #
def test_one_hired_flag_per_recruit_candidate():
    candidates = yaml.safe_load(
        (_CONFIG_DIR / "entities" / "gangsters.yaml").read_text(encoding="utf-8")
    )["gangsters"]
    hired = {k for k in _SCHEMA.global_ if k.startswith("hired.")}
    assert hired == {f"hired.{i}" for i in range(len(candidates))}


def test_one_tenancy_key_per_tile_index():
    tenancy = {k for k in _SCHEMA.global_ if k.startswith("tenancy.")}
    assert tenancy == {f"tenancy.{ln}" for ln in range(1, 10)}  # ln is 1..9
