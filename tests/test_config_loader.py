"""Tests for the generic Engine<->Config boundary (engine.config_loader).

Proves the refactored boundary works end-to-end:

* ``load_game_config(mafia_1920s_dir)`` loads the config BY PATH, validates it, and
  registers the config's ``slw.rent`` handler, which the handle's shells resolve.
* A malformed config (bad/missing ``engine_api``, or a missing GameConfigSchema
  required key) is rejected with a clear error.
* The loaded handle exposes the config's ``new_game`` callable.

This is the NEW test the structural refactor adds; it does not change any existing
handler/setup game logic.
"""

from __future__ import annotations

import ast
import dataclasses
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from engine import persistence
from engine.actions import HandlerResult
from engine.config_loader import StaleConfigError, load_game_config
from engine.config_owner import active_owner
from engine.effects import MoneyChange, commit
from engine.interactions import Confirm, LoadSubState, run, step
from engine.locations import HANDLERS
from engine.substates import SUBSTATES
from engine.turns import TurnRunner
from engine.types import ConfigValidationError, validate_config, validate_vehicle

CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"


def test_load_game_config_registers_handler_and_exposes_new_game():
    loaded = load_game_config(CONFIG_DIR)

    # The config's handler registered via its @register decorator (fired by the
    # by-path package import); the handle's shells resolve it through the handle.
    assert "slw.rent" in loaded.handlers
    rent = next(o for o in loaded.shells["slw"].options if o.id == "rent")
    assert rent.handler is loaded.handlers["slw.rent"]

    # The handle exposes the config's new_game entry point + parsed config.
    assert callable(loaded.new_game)
    assert loaded.config["engine_api"] == 2
    assert loaded.config_dir == CONFIG_DIR.resolve()


def test_loaded_config_exposes_the_configs_declared_stat_names():
    # StatChange validation reads these; the config declares them (the engine names none).
    from data.game_configs.mafia_1920s.gangster import GANGSTER_ATTR_NAMES

    loaded = load_game_config(CONFIG_DIR)
    assert set(loaded.stat_names) == set(GANGSTER_ATTR_NAMES)


def test_load_game_config_rejects_missing_dir(tmp_path):
    with pytest.raises(ValueError):
        load_game_config(tmp_path / "does_not_exist")


def test_load_game_config_rejects_bad_engine_api(tmp_path):
    # A config dir with engine_api != 2 is rejected at load (before importing it).
    (tmp_path / "config.yaml").write_text(
        "engine_api: 1\nname: x\ncontent_version: 1\nentities: {}\nformula_params: {}\nsetup: {}\ninput_ranges: {}\n"
    )
    (tmp_path / "__init__.py").write_text("")
    with pytest.raises(ValueError):
        load_game_config(tmp_path)


def test_load_game_config_rejects_missing_required_key(tmp_path):
    # engine_api ok but a GameConfigSchema-required key ('setup') is missing.
    (tmp_path / "config.yaml").write_text(
        "engine_api: 2\nname: x\ncontent_version: 1\nentities: {}\nformula_params: {}\ninput_ranges: {}\n"
    )
    (tmp_path / "__init__.py").write_text("")
    with pytest.raises(ValueError) as exc:
        load_game_config(tmp_path)
    assert "setup" in str(exc.value)


def test_load_game_config_rejects_missing_init(tmp_path):
    # Valid config.yaml but no __init__.py => not an importable config package.
    (tmp_path / "config.yaml").write_text(
        "engine_api: 2\nname: x\ncontent_version: 1\nentities: {}\nformula_params: {}\nsetup: {}\ninput_ranges: {}\n"
    )
    with pytest.raises(ValueError):
        load_game_config(tmp_path)


def _write_minimal_config(pkg_dir: Path, marker: int) -> None:
    """Write a minimal, self-contained config package that exposes new_game().

    ``new_game()`` returns ``marker`` so a test can tell WHICH config's code ran —
    the point being that two configs sharing a directory basename must not alias.
    """
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "config.yaml").write_text(
        "engine_api: 2\nname: x\ncontent_version: 1\nentities: {}\nformula_params: {}\nsetup: {}\ninput_ranges: {}\n"
    )
    (pkg_dir / "__init__.py").write_text(f"def new_game(*args, **kwargs):\n    return {marker}\n")


def test_same_basename_configs_at_different_paths_do_not_collide(tmp_path):
    # The "copy the directory" scenario: two configs share the basename
    # "mygame" but live at different paths. Each must load its OWN code, not
    # silently alias to whichever was loaded first (the sys.modules-name bug).
    a = tmp_path / "a" / "mygame"
    b = tmp_path / "b" / "mygame"
    _write_minimal_config(a, marker=111)
    _write_minimal_config(b, marker=222)

    loaded_a = load_game_config(a)
    loaded_b = load_game_config(b)

    assert loaded_a.new_game() == 111
    assert loaded_b.new_game() == 222  # would be 111 if the dir-name collision bit
    # Re-loading A after B still yields A's code (fresh, not stale-cached).
    assert load_game_config(a).new_game() == 111


# --- direct contract-validator checks (engine.types is load-bearing) -------


def test_validate_config_rejects_wrong_typed_key():
    with pytest.raises(ConfigValidationError):
        validate_config(
            {
                "engine_api": 2,
                "name": "x",
                "content_version": 1,
                "entities": [],  # wrong type: must be a dict
                "formula_params": {},
                "setup": {},
                "input_ranges": {},
            }
        )


def test_validate_vehicle_rejects_missing_field():
    with pytest.raises(ConfigValidationError):
        validate_vehicle({"name": "x", "tank": 50}, index=0)  # missing 'tr'


def test_validate_vehicle_accepts_valid_entry():
    entry = {"name": "fuesse", "tank": 50, "tr": 25}
    assert validate_vehicle(entry, index=0) is entry


# --- one live config: a replaced handle fails loudly (R1, R2, R22) ----------
COUNTER_DIR = Path(__file__).resolve().parent / "fixtures" / "configs" / "counter_game"


def _counter_copy(parent: Path, *, factor: int = 1) -> Path:
    """A copy of ``counter_game`` at ``parent``; its ``CounterBump`` adds ``amount * factor``."""
    cfg_dir = parent / "counter_game"
    shutil.copytree(COUNTER_DIR, cfg_dir, ignore=shutil.ignore_patterns("__pycache__"))
    if factor != 1:
        init = cfg_dir / "__init__.py"
        source = init.read_text(encoding="utf-8")
        changed = source.replace("current + self.amount,", f"current + self.amount * {factor},")
        assert changed != source
        init.write_text(changed, encoding="utf-8")
    return cfg_dir


def _save(path: Path, cfg, state, effects: list) -> None:
    persistence.save_game(
        path, state, registries=cfg.registries, effect_log=effects, rng_log=[], seed=1
    )


def test_a_replaced_handle_refuses_to_save_load_or_replay_naming_both_configs(tmp_path):
    """AE1: config A, then a copy B whose ``CounterBump`` adds a hundredfold. A's
    ``+3`` through A's handle raises naming A and B, instead of replaying B's code
    (``300``)."""
    a_dir = _counter_copy(tmp_path / "a")
    b_dir = _counter_copy(tmp_path / "b", factor=100)
    a = load_game_config(a_dir)
    state = a.new_game()
    path = tmp_path / "game.jsonl"
    _save(path, a, state, [a.module.CounterBump(amount=3)])
    saved = persistence.load_game(path, a.registries)

    b = load_game_config(b_dir)

    for use in (
        lambda: persistence.replay(persistence.load_game(path, a.registries), a.registries),
        lambda: persistence.replay(saved, a.registries),
        lambda: _save(tmp_path / "other.jsonl", a, state, []),
    ):
        with pytest.raises(StaleConfigError) as raised:
            use()
        assert str(a_dir) in str(raised.value)
        assert str(b_dir) in str(raised.value)
    # B's own handle is live and runs B's code.
    replayed = persistence.replay(persistence.load_game(path, b.registries), b.registries)
    assert replayed.players[0].values["counter"] == 300


def test_a_replaced_handle_refuses_a_turn_runner_and_an_owned_step(tmp_path):
    a = load_game_config(_counter_copy(tmp_path / "a"))
    b = load_game_config(_counter_copy(tmp_path / "b"))
    called: list[str] = []

    def handler(ctx):
        called.append("handler")
        yield from ()

    with pytest.raises(StaleConfigError):
        TurnRunner(a.new_game(), registries=a.registries)
    with pytest.raises(StaleConfigError):
        next(step(handler, a.new_game(), owner=a.owner))
    assert called == []  # refused before the handler was even called
    # The live handle builds a runner and runs an owned step.
    TurnRunner(b.new_game(), registries=b.registries)
    assert run(handler, lambda _: None, state=b.new_game()).status == "completed"
    with pytest.raises(StopIteration):
        next(step(handler, b.new_game(), owner=b.owner))


def test_a_step_with_no_owner_is_not_checked(tmp_path, monkeypatch):
    """Engine code run with no config (a test-only sub-state) carries no owner, so it
    runs even while the config a handle names is not the live one."""
    load_game_config(_counter_copy(tmp_path / "a"))
    load_game_config(_counter_copy(tmp_path / "b"))

    def child(ctx, params):
        answer = yield Confirm("probe.confirm")
        return answer

    def parent(ctx):
        return (yield LoadSubState("probe.no_owner"))

    monkeypatch.setitem(SUBSTATES, "probe.no_owner", child)
    result = run(parent, lambda _: True)
    assert result.status == "completed"
    assert isinstance(result.payload, HandlerResult)
    assert result.payload.returned is True


def _mafia_game(cfg):
    return cfg.new_game(
        seed=1, end_year=1930, score_weight=1.0, players=[("alcapone", "the outfit")]
    )


def test_a_save_from_before_a_same_config_reload_replays_identically(tmp_path):
    """AE2: Mafia is loaded, saved and reloaded; the save loads through the new handle
    and replays identically, and the old handle is live again too (a path, not a
    load counter, names the config)."""
    first = load_game_config(CONFIG_DIR)
    state = _mafia_game(first)
    effects = [MoneyChange(amount=-100), first.module.effects.SetTenancy(ln=2)]
    live = commit(state, effects).state
    path = tmp_path / "game.jsonl"
    _save(path, first, state, effects)

    second = load_game_config(CONFIG_DIR)
    assert second.module is not first.module

    loaded = persistence.load_game(path, second.registries)
    assert loaded.effect_log == effects
    assert persistence.replay(loaded, second.registries) == live
    old = persistence.load_game(path, first.registries)
    assert persistence.replay(old, first.registries) == live
    TurnRunner(state, registries=first.registries)


def _registry_contents() -> list:
    from engine.conditions import GUARD_VARIABLES
    from engine.effects import CONSEQUENCES, EFFECTS, STAT_NAMES

    return [
        dict(HANDLERS),
        dict(EFFECTS),
        dict(CONSEQUENCES),
        dict(SUBSTATES),
        dict(GUARD_VARIABLES),
        set(STAT_NAMES),
    ]


def test_a_copy_refused_at_load_leaves_the_live_config_in_place(tmp_path):
    """A copy whose weapon table is refused registered its handlers and effects before
    its weapon check ran; the refusal puts back every registry (the same objects) and
    Mafia stays the live config."""
    mafia = load_game_config(CONFIG_DIR)
    before = _registry_contents()
    cfg_dir = tmp_path / "mafia_1920s"
    shutil.copytree(CONFIG_DIR, cfg_dir, ignore=shutil.ignore_patterns("__pycache__"))
    weapons_yaml = cfg_dir / "entities" / "weapons.yaml"
    table = yaml.safe_load(weapons_yaml.read_text(encoding="utf-8"))
    table["weapons"][5]["requires"] = {"charisma": 5}
    weapons_yaml.write_text(yaml.safe_dump(table), encoding="utf-8")

    with pytest.raises(ConfigValidationError, match="charisma"):
        load_game_config(cfg_dir)

    after = _registry_contents()
    for kept, restored in zip(before, after):
        assert restored == kept
    for kept, restored in zip(before[:-1], after[:-1]):
        assert all(restored[key] is value for key, value in kept.items())
    assert active_owner() == mafia.owner
    _save(tmp_path / "game.jsonl", mafia, _mafia_game(mafia), [])


def test_a_refused_reload_of_the_live_config_leaves_it_live(tmp_path):
    cfg_dir = _counter_copy(tmp_path)
    live = load_game_config(cfg_dir)
    bump = live.module.CounterBump
    module_name = live.module.__name__
    init = cfg_dir / "__init__.py"
    init.write_text(
        init.read_text(encoding="utf-8") + '\nraise ValueError("refused on purpose")\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="refused on purpose"):
        load_game_config(cfg_dir)

    assert active_owner() == live.owner
    assert live.effects["CounterBump"] is bump
    assert sys.modules[module_name] is live.module
    path = tmp_path / "game.jsonl"
    _save(path, live, live.new_game(), [bump(amount=2)])
    replayed = persistence.replay(persistence.load_game(path, live.registries), live.registries)
    assert replayed.players[0].values["counter"] == 2


def test_a_replaced_copy_of_a_handle_keeps_its_owner(tmp_path):
    mafia = load_game_config(CONFIG_DIR)
    copy = dataclasses.replace(mafia, shells={})
    assert copy.owner == mafia.owner
    state = _mafia_game(mafia)
    _save(tmp_path / "game.jsonl", copy, state, [])

    load_game_config(_counter_copy(tmp_path / "other"))
    with pytest.raises(StaleConfigError):
        _save(tmp_path / "game.jsonl", copy, state, [])


def test_the_terminal_passes_its_handles_handlers_and_registries_to_the_turn_runner():
    """R3: the session builds its turn runner from its loaded handle, not the global
    default, so the runner's actions carry the handle's owner."""
    import clients.terminal.session as session

    tree = ast.parse(Path(session.__file__).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "TurnRunner"
    ]
    assert len(calls) == 1
    keywords = {k.arg: ast.unparse(k.value) for k in calls[0].keywords}
    assert keywords["handlers"] == "self.cfg.handlers"
    assert keywords["registries"] == "self.cfg.registries"
