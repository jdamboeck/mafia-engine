"""Shared test fixtures / helpers.

The ``mafia_1920s`` game config is loaded BY PATH (it is deliberately not a
pip-installed package — see :mod:`engine.config_loader`), so tests reach its
``new_game``/``fnm``/entity loaders through the engine's loader rather than a
static ``import data.game_configs...``.

One config is live at a time (:mod:`engine.config_owner`), and the module-level
Mafia handles the test files hold are checked against it. A test that loads another
config and leaves it live would make every later test's Mafia handle stale, so the
autouse :func:`_mafia_stays_live` puts the registries back and loads Mafia again
after such a test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from engine.conditions import GUARD_VARIABLES
from engine.config_loader import load_game_config
from engine.config_owner import ConfigOwner, active_owner
from engine.effects import CONSEQUENCES, EFFECTS, STAT_NAMES
from engine.locations import HANDLERS
from engine.substates import SUBSTATES

CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"
_MAFIA = ConfigOwner.for_dir(CONFIG_DIR)
_REGISTRIES: tuple[dict[str, Any], ...] = (
    HANDLERS,
    EFFECTS,
    CONSEQUENCES,
    SUBSTATES,
    GUARD_VARIABLES,
)


@pytest.fixture(autouse=True)
def _mafia_stays_live():
    """After a test that left another config live, restore the registries and reload Mafia.

    The registries are put back as the test found them first, so nothing the other
    config registered under a name Mafia does not use survives it; the reload then
    makes Mafia the live config again.
    """
    before = [dict(registry) for registry in _REGISTRIES]
    stat_names = set(STAT_NAMES)
    yield
    active = active_owner()
    if active is None or active.token == _MAFIA.token:
        return
    for registry, saved in zip(_REGISTRIES, before):
        registry.clear()
        registry.update(saved)
    STAT_NAMES.clear()
    STAT_NAMES.update(stat_names)
    load_game_config(CONFIG_DIR)


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
    """Pin color support to TRUECOLOR for a session built by ``play()``/``main()``.

    A session reads its color support from the env once, when it starts
    (``Colors.detect``), so the pin goes through env detection:
    ``COLORTERM=truecolor`` on top of the hostile env, so the pin (not the host) is
    what makes truecolor come out.
    """
    monkeypatch.setenv("COLORTERM", "truecolor")
