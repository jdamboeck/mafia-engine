"""Tests for the generic Engine<->Config boundary (engine.config_loader).

Proves the refactored boundary works end-to-end:

* ``load_game_config(mafia_1920s_dir)`` loads the config BY PATH, validates it, and
  registers the config's ``slw.rent`` handler into ``engine.locations.HANDLERS``.
* A malformed config (bad/missing ``engine_api``, or a missing GameConfigSchema
  required key) is rejected with a clear error.
* The loaded handle exposes the config's ``new_game`` callable.

This is the NEW test the structural refactor adds; it does not change any existing
handler/setup game logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.config_loader import load_game_config
from engine.locations import HANDLERS
from engine.types import ConfigValidationError, validate_config, validate_vehicle

CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"


def test_load_game_config_registers_handler_and_exposes_new_game():
    loaded = load_game_config(CONFIG_DIR)

    # The config's handler registered via its @register decorator (fired by the
    # by-path package import), populating the engine's generic registry.
    assert "slw.rent" in HANDLERS
    assert loaded.handlers is HANDLERS
    assert "slw.rent" in loaded.handlers

    # The handle exposes the config's new_game entry point + parsed config.
    assert callable(loaded.new_game)
    assert loaded.config["engine_api"] == 1
    assert loaded.config_dir == CONFIG_DIR.resolve()


def test_load_game_config_rejects_missing_dir(tmp_path):
    with pytest.raises(ValueError):
        load_game_config(tmp_path / "does_not_exist")


def test_load_game_config_rejects_bad_engine_api(tmp_path):
    # A config dir with engine_api != 1 is rejected at load (before importing it).
    (tmp_path / "config.yaml").write_text(
        "engine_api: 2\nentities: {}\nformula_params: {}\nsetup: {}\ninput_ranges: {}\n"
    )
    (tmp_path / "__init__.py").write_text("")
    with pytest.raises(ValueError):
        load_game_config(tmp_path)


def test_load_game_config_rejects_missing_required_key(tmp_path):
    # engine_api ok but a GameConfigSchema-required key ('setup') is missing.
    (tmp_path / "config.yaml").write_text(
        "engine_api: 1\nentities: {}\nformula_params: {}\ninput_ranges: {}\n"
    )
    (tmp_path / "__init__.py").write_text("")
    with pytest.raises(ValueError) as exc:
        load_game_config(tmp_path)
    assert "setup" in str(exc.value)


def test_load_game_config_rejects_missing_init(tmp_path):
    # Valid config.yaml but no __init__.py => not an importable config package.
    (tmp_path / "config.yaml").write_text(
        "engine_api: 1\nentities: {}\nformula_params: {}\nsetup: {}\ninput_ranges: {}\n"
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
        "engine_api: 1\nentities: {}\nformula_params: {}\nsetup: {}\ninput_ranges: {}\n"
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
                "engine_api": 1,
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
