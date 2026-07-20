"""Shared test fixtures / helpers.

The ``mafia_1920s`` game config is loaded BY PATH (it is deliberately not a
pip-installed package — see :mod:`engine.config_loader`), so tests reach its
``new_game``/``fnm``/entity loaders through the engine's loader rather than a
static ``import data.game_configs...``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.config_loader import load_game_config

CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"


@pytest.fixture(scope="session")
def mafia_config():
    """The loaded ``mafia_1920s`` config handle (registers its handlers on load)."""
    return load_game_config(CONFIG_DIR)


@pytest.fixture(scope="session")
def mafia_module(mafia_config):
    """The imported ``mafia_1920s`` config package module (exposes new_game, fnm)."""
    return mafia_config.module
