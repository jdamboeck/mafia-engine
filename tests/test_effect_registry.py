"""U3 — effect and state registries.

The engine applies, saves and replays effects and state it does not name
(docs/design/engine-architecture.md § Events vs effects, § Save/replay, § GameState
overview). An effect carries its own ``apply`` and joins the engine by registering
under a tag; a config declares its value maps' names, types and defaults. Save
loading and replay take the loaded config's registries.

The config under test is ``tests/fixtures/configs/counter_game``: it registers one
game effect, ``CounterBump``, over the ``counter`` key of its declared per-player map.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

from engine import persistence
from engine.config_loader import STATE_SCHEMA_FILE, load_game_config, load_state_schema
from engine.effects import EFFECTS, MoneyChange, commit, register_effect
from engine.state import Clock, GameState, Player, StateSchema
from tests.helpers import _shape, run_pure, scripted

COUNTER_DIR = Path(__file__).resolve().parent / "fixtures" / "configs" / "counter_game"


@pytest.fixture
def restore_effects():
    """Leave the global effect registry as the test found it."""
    before = dict(EFFECTS)
    yield
    EFFECTS.clear()
    EFFECTS.update(before)


@pytest.fixture
def counter(restore_effects):
    """The loaded ``counter_game`` config (registers ``CounterBump`` on load)."""
    return load_game_config(COUNTER_DIR)


def _save(path: Path, config, state: GameState, effects: list | None = None) -> None:
    persistence.save_game(
        path, state, registries=config.registries, effect_log=effects or [], rng_log=[], seed=1
    )


def _rewrite_snapshot(path: Path, edit) -> None:
    """Apply ``edit`` to the save header's snapshot dict, in place on disk."""
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    edit(header["snapshot"])
    lines[0] = json.dumps(header)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- #
# Effects                                                                     #
# --------------------------------------------------------------------------- #
def test_a_config_effect_applies_saves_and_reloads_to_an_equal_state(counter, tmp_path):
    state = counter.new_game()
    bump = counter.module.CounterBump(amount=3)
    live = commit(state, [bump]).state
    assert live.players[0].values["counter"] == 3

    path = tmp_path / "game.jsonl"
    _save(path, counter, state, [bump])
    loaded = persistence.load_game(path, counter.registries)

    assert loaded.state == state
    assert loaded.effect_log == [bump]
    assert persistence.replay(loaded, counter.registries) == live


def test_an_unregistered_tag_in_a_save_is_refused_naming_the_tag(counter, tmp_path):
    path = tmp_path / "game.jsonl"
    _save(path, counter, counter.new_game(), [MoneyChange(amount=5)])
    header, effect_line = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(effect_line)
    record["effect"]["_type"] = "NoSuchEffect"
    path.write_text(f"{header}\n{json.dumps(record)}\n", encoding="utf-8")

    with pytest.raises(persistence.UnknownEffectError, match="NoSuchEffect"):
        persistence.load_game(path, counter.registries)


def test_re_registering_a_tag_replaces_the_old_entry(restore_effects):
    @register_effect("ReplaceMe")
    @dataclass(frozen=True)
    class First:
        def apply(self, state: GameState) -> GameState:
            return state

    @register_effect("ReplaceMe")
    @dataclass(frozen=True)
    class Second:
        def apply(self, state: GameState) -> GameState:
            return state

    assert EFFECTS["ReplaceMe"] is Second


def test_loading_a_save_without_the_registries_is_a_type_error(counter, tmp_path):
    """The registries are a required parameter: pyright flags the call below (hence the
    named ignore), and at runtime it is a ``TypeError`` at the call site."""
    path = tmp_path / "game.jsonl"
    _save(path, counter, counter.new_game())
    with pytest.raises(TypeError, match="registries"):
        persistence.load_game(path)  # pyright: ignore[reportCallIssue]  # the point of the test
    loaded = persistence.load_game(path, counter.registries)
    with pytest.raises(TypeError, match="registries"):
        persistence.replay(loaded)  # pyright: ignore[reportCallIssue]  # the point of the test


def test_a_config_loaded_twice_loads_a_save_from_its_first_load(tmp_path, restore_effects):
    """``load_game_config`` re-executes the config package, so the second load's
    ``CounterBump`` is a different class. A save written under the first load still
    loads and replays under the second, to an equal state."""
    first = load_game_config(COUNTER_DIR)
    state = first.new_game()
    bump = first.module.CounterBump(amount=4)
    live = commit(state, [bump]).state
    path = tmp_path / "game.jsonl"
    _save(path, first, state, [bump])

    second = load_game_config(COUNTER_DIR)
    assert second.module.CounterBump is not first.module.CounterBump
    loaded = persistence.load_game(path, second.registries)

    assert type(loaded.effect_log[0]) is second.module.CounterBump
    assert loaded.state == state
    assert persistence.replay(loaded, second.registries) == live


# --------------------------------------------------------------------------- #
# Value maps                                                                  #
# --------------------------------------------------------------------------- #
def test_a_value_map_freezes_on_construction():
    player = Player(values={"counter": 1, "history": [1, 2]})
    state = GameState(players=(player,), values={"round_bonus": 0.5})

    assert isinstance(player.values, MappingProxyType)
    assert player.values["history"] == (1, 2)
    assert isinstance(state.values, MappingProxyType)


def test_mutating_a_value_map_in_place_raises():
    player = Player(values={"counter": 1})
    state = GameState(players=(player,), values={"round_bonus": 0.5})
    with pytest.raises(TypeError):
        player.values["counter"] = 2  # pyright: ignore[reportIndexIssue]  # the point of the test
    with pytest.raises(TypeError):
        state.values["round_bonus"] = 1.0  # pyright: ignore[reportIndexIssue]  # the point of the test


def test_shape_sees_a_type_change_inside_the_value_map():
    as_int = GameState(players=(Player(values={"counter": 1}),))
    as_float = GameState(players=(Player(values={"counter": 1.0}),))
    assert as_int == as_float  # value equality cannot see it ...
    assert _shape(as_int) != _shape(as_float)  # ... the fingerprint can


def test_run_pure_catches_a_type_change_inside_the_value_map():
    def drifting_handler(ctx):
        player = ctx.state.players[0]
        object.__setattr__(player, "values", MappingProxyType({"counter": 1.0}))
        return []
        yield  # pragma: no cover - make this a generator

    state = GameState(players=(Player(values={"counter": 1}),), clock=Clock(player_count=1))
    with pytest.raises(AssertionError, match="changed the TYPE"):
        run_pure(drifting_handler, scripted(), state=state)


def test_a_save_missing_a_declared_key_loads_with_the_declared_default(counter, tmp_path):
    state = commit(counter.new_game(), [counter.module.CounterBump(amount=7)]).state
    path = tmp_path / "game.jsonl"
    _save(path, counter, state)

    def drop(snapshot):
        del snapshot["players"][0]["values"]["counter"]
        del snapshot["values"]["round_bonus"]

    _rewrite_snapshot(path, drop)
    loaded = persistence.load_game(path, counter.registries).state

    assert loaded.players[0].values == {"counter": 0, "label": "boss"}
    assert loaded.values == {"round_bonus": 0.0}
    assert isinstance(loaded.players[0].values, MappingProxyType)


@pytest.mark.parametrize("where", ["player", "global"])
def test_a_save_with_an_unknown_key_is_refused_naming_it(counter, tmp_path, where):
    path = tmp_path / "game.jsonl"
    _save(path, counter, counter.new_game())

    def add(snapshot):
        target = snapshot["players"][0] if where == "player" else snapshot
        target["values"]["smuggled_key"] = 1

    _rewrite_snapshot(path, add)
    with pytest.raises(ValueError, match="smuggled_key"):
        persistence.load_game(path, counter.registries)


def test_a_save_with_a_wrongly_typed_value_is_refused_naming_it(counter, tmp_path):
    path = tmp_path / "game.jsonl"
    _save(path, counter, counter.new_game())

    def retype(snapshot):
        snapshot["players"][0]["values"]["counter"] = "three"

    _rewrite_snapshot(path, retype)
    with pytest.raises(ValueError, match="counter"):
        persistence.load_game(path, counter.registries)


def test_the_config_loader_reads_the_declared_schema(counter):
    schema = counter.state_schema
    assert dict(schema.player_defaults()) == {"counter": 0, "label": "none"}
    assert dict(schema.global_defaults()) == {"round_bonus": 0.0}


@pytest.mark.parametrize(
    ("raw", "named"),
    [
        ({"player": {"x": {"type": "decimal", "default": 0}}}, "decimal"),
        ({"player": {"x": {"type": "int", "default": "zero"}}}, "x"),
        ({"player": {"x": {"type": "int"}}}, "default"),
        ({"players": {}}, "players"),
    ],
)
def test_a_malformed_schema_is_refused(raw, named):
    with pytest.raises(ValueError, match=named):
        StateSchema.from_dict(raw)


def test_a_config_without_a_state_schema_file_declares_empty_maps(tmp_path):
    schema = load_state_schema(tmp_path / STATE_SCHEMA_FILE)
    assert dict(schema.player_defaults()) == {}
    assert dict(schema.global_defaults()) == {}


def test_a_state_section_in_config_yaml_is_refused_naming_the_schema_file(tmp_path):
    """One way to declare the value maps: ``state_schema.yaml``, never ``config.yaml``."""
    import shutil

    config_dir = tmp_path / "counter_game"
    shutil.copytree(COUNTER_DIR, config_dir, ignore=shutil.ignore_patterns("__pycache__"))
    with (config_dir / "config.yaml").open("a", encoding="utf-8") as fh:
        fh.write("state:\n  player: {}\n")
    with pytest.raises(ValueError, match=STATE_SCHEMA_FILE):
        load_game_config(config_dir)


def test_a_malformed_schema_file_is_refused_naming_the_file(tmp_path):
    path = tmp_path / STATE_SCHEMA_FILE
    path.write_text("player:\n  x: {type: decimal, default: 0}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=STATE_SCHEMA_FILE):
        load_state_schema(path)


def test_value_map_helpers_leave_the_input_state_untouched():
    from engine.effects import set_global_value, set_player_value

    state = GameState(players=(Player(values={"counter": 1}),), values={"round_bonus": 0.0})
    snapshot = dataclasses.replace(state)
    after = set_global_value(set_player_value(state, "counter", 2), "round_bonus", 1.5)

    assert after.players[0].values == {"counter": 2}
    assert after.values == {"round_bonus": 1.5}
    assert state == snapshot and state.players[0].values == {"counter": 1}
    assert isinstance(after.players[0].values, MappingProxyType)
