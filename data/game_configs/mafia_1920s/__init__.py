"""The ``mafia_1920s`` game config package — the config's entry point.

This is the top-level package the engine loads BY PATH (via
:func:`engine.config_loader.load_game_config`). Importing it:

* imports the handlers package (firing each handler's ``@register`` decorator, so
  :data:`engine.locations.HANDLERS` is populated), and
* exposes the config's ``new_game`` and ``fnm`` entry points so the engine loader
  and tests can reach them.

A new game in this genre is a *copy of this directory* — its data (``config.yaml``,
``content/``, ``entities/``, ``themes/``), its Python handlers, and this setup code
travel together; the engine is untouched (PLAN.md §1, §6a).
"""

from __future__ import annotations

from . import handlers  # noqa: F401 — registers the config's handlers on import
from .setup import fnm, new_game

__all__ = ["new_game", "fnm", "handlers"]
