"""The ``mafia_1920s`` game config package — the config's entry point.

This is the top-level package the engine loads BY PATH (via
:func:`engine.config_loader.load_game_config`). Importing it:

* imports :mod:`.state` (firing each guard variable's ``@register_guard_variable``, so
  :data:`engine.conditions.GUARD_VARIABLES` holds this game's guard vocabulary; it
  also holds the typed accessors over the declared value maps),
* imports :mod:`.effects` (firing each game effect's ``@register_effect``, so
  :data:`engine.effects.EFFECTS` holds this game's effects),
* imports the handlers package (firing each handler's ``@register`` decorator, so
  :data:`engine.locations.HANDLERS` is populated),
* imports :mod:`.gangster` (declaring this game's stat names, which ``StatChange``
  validation reads — :data:`engine.effects.STAT_NAMES`),
* validates the weapon table, so a weapon requirement naming an undeclared stat is
  refused when the config loads rather than at the first shop visit, and
* exposes the config's ``new_game`` and ``fnm`` entry points so the engine loader
  and tests can reach them.

A new game in this genre is a *copy of this directory* — its data (``config.yaml``,
``content/``, ``entities/``, ``themes/``), its Python handlers, and this setup code
travel together; the engine is untouched (docs/design/product-and-scope.md and docs/design/config-and-content-contract.md).
"""

from __future__ import annotations

from pathlib import Path

from . import state  # noqa: F401 — registers the config's guard variables on import
from . import effects  # noqa: F401 — registers the config's own effects on import
from . import handlers  # noqa: F401 — registers the config's handlers on import
from .gangster import Gangster

from .combat_rules import build_rules, equipper
from .setup import fnm, load_ranks, load_vehicles, load_weapons, new_game

# Refuse a bad weapon table at config load (a ConfigValidationError out of the import).
load_weapons(Path(__file__).resolve().parent / "entities" / "weapons.yaml")

__all__ = [
    "new_game",
    "fnm",
    "effects",
    "handlers",
    "state",
    "load_ranks",
    "load_vehicles",
    "load_weapons",
    # This game's name for the engine's Combatant blueprint, plus its rules bundle.
    "Gangster",
    "build_rules",
    "equipper",
]
