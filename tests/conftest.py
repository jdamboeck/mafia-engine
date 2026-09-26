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


@pytest.fixture
def hostile_color_env(monkeypatch):
    """A host whose env detection would NOT pick truecolor.

    ``COLORTERM`` unset + ``TERM=xterm-256color`` sends ``term_color_support()``
    down its 256-color branch. Tests asserting truecolor sequences run under this
    so they prove they don't lean on the developer's terminal.
    """
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


@pytest.fixture
def truecolor(hostile_color_env, monkeypatch):
    """Pin color support to TRUECOLOR for renderers that don't take ``support``.

    Renderers call ``fg()``/``bg()`` without a ``support`` argument, so the pin
    goes through env detection: ``COLORTERM=truecolor`` on top of the hostile env,
    so the pin (not the host) is what makes truecolor come out.
    """
    monkeypatch.setenv("COLORTERM", "truecolor")
