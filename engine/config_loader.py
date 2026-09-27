"""Generic game-config loader — the engine's Engine<->Config entry point.

A game config is a **directory** (e.g. ``data/game_configs/mafia_1920s``) that the
engine loads **by filesystem path** — NOT a pip-installed package. This is what keeps
a config *copyable*: a new game is a copy of a config dir (docs/design/product-and-scope.md), and a
copied/third-party config loads without editing ``pyproject.toml`` or reinstalling.

:func:`load_game_config` reads ``config.yaml``, validates it against the type
contracts in :mod:`engine.types`, then imports the config's package **by path**
(via :func:`importlib.util.spec_from_file_location`). Importing the package fires
the config's handler ``@register`` and effect ``@register_effect`` decorators
(populating :data:`engine.locations.HANDLERS` and :data:`engine.effects.EFFECTS`) and
exposes the config's ``new_game`` callable; a config's guard variables register the
same way (:data:`engine.conditions.GUARD_VARIABLES`). ``state_schema.yaml`` beside
``config.yaml`` declares the config's value maps (:class:`~engine.state.StateSchema`).

Layering: this module imports the config *dynamically, by path* — the ``engine/``
package never statically imports anything under ``data/``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from engine.conditions import GUARD_VARIABLES
from engine.effects import EFFECTS, STAT_NAMES
from engine.locations import HANDLERS
from engine.persistence import Registries
from engine.state import StateSchema
from engine.types import validate_config

__all__ = [
    "ENGINE_API",
    "LoadedConfig",
    "load_config",
    "load_game_config",
    "load_state_schema",
    "STATE_SCHEMA_FILE",
]

#: The file, beside ``config.yaml``, that declares a config's value maps.
STATE_SCHEMA_FILE = "state_schema.yaml"

#: The Engine<->Config API version this engine speaks (docs/design/config-and-content-contract.md).
ENGINE_API = 1


def load_config(path: str | Path) -> dict:
    """Load and validate a game ``config.yaml`` (generic, no game specifics).

    Enforces the Engine<->Config contract (docs/design/config-and-content-contract.md) via
    :func:`engine.types.validate_config`: the config MUST declare ``engine_api: 1``
    and carry every required top-level key. Any violation raises ``ValueError``
    (a :class:`~engine.types.ConfigValidationError`).
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    try:
        return validate_config(data)
    except ValueError as exc:  # add the file path to the clear error
        raise type(exc)(f"{path}: {exc}") from None


def load_state_schema(path: str | Path) -> StateSchema:
    """Read a config's ``state_schema.yaml``; a missing file declares two empty maps.

    The one way a config declares its value maps: the loader reads it for save loading,
    and a config reads the same file for its own defaults. A malformed schema raises
    :class:`~engine.state.StateSchemaError` (a ``ValueError``) naming the file.
    """
    path = Path(path)
    if not path.is_file():
        return StateSchema()
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    try:
        return StateSchema.from_dict(raw)
    except ValueError as exc:
        raise type(exc)(f"{path}: {exc}") from None


@dataclass
class LoadedConfig:
    """A handle over a loaded game config.

    Attributes
    ----------
    config_dir:
        The config's directory (its ``config.yaml`` lives here).
    config:
        The parsed, validated ``config.yaml`` dict.
    new_game:
        The config's ``new_game`` callable (its ported new-game setup).
    module:
        The imported config package module object.
    handlers:
        The engine's :data:`~engine.locations.HANDLERS` registry, now populated by
        the config's ``@register`` decorators (returned for convenient assertion).
    effects:
        The engine's :data:`~engine.effects.EFFECTS` registry: the generic engine
        effects plus the config's own, registered by its ``@register_effect``
        decorators on import.
    state_schema:
        The config's declared value maps (its ``state_schema.yaml``); empty maps when
        the config declares none.
    guard_variables:
        The engine's :data:`~engine.conditions.GUARD_VARIABLES` registry, filled by the
        config's ``@register_guard_variable`` resolvers on import.
    stat_names:
        The engine's :data:`~engine.effects.STAT_NAMES`: the stat names the config
        declared with :func:`~engine.effects.declare_stat_names` on import, which
        ``StatChange`` validation reads.
    """

    config_dir: Path
    config: dict
    new_game: Callable[..., Any]
    module: Any
    handlers: dict
    effects: dict
    state_schema: StateSchema
    guard_variables: dict
    stat_names: set[str]

    @property
    def registries(self) -> Registries:
        """What save loading and replay take (:func:`engine.persistence.load_game`)."""
        return Registries(effects=self.effects, state_schema=self.state_schema)


def _config_module_name(config_dir: Path) -> str:
    """A ``sys.modules`` name UNIQUE to this config's resolved PATH.

    Keying on the directory *basename* alone would collide when two configs share
    a name at different paths — exactly the "copy the directory" case this loader
    exists to support (``/a/mafia_1920s`` vs ``/b/mafia_1920s``): the second load
    would silently reuse the first's cached submodules and return the wrong config.
    A path-derived digest makes each distinct config a distinct package.
    """
    digest = hashlib.sha1(str(config_dir).encode()).hexdigest()[:8]
    return f"_game_config_{config_dir.name}_{digest}"


def _import_config_package(config_dir: Path) -> Any:
    """Import the config package at ``config_dir`` BY PATH and return its module.

    Uses ``spec_from_file_location`` on ``config_dir/__init__.py`` so a config dir
    anywhere on disk loads without being on ``sys.path`` or pip-installed. The
    module is registered under a **path-unique** name (see :func:`_config_module_name`)
    so its relative imports (``from .setup import ...``, ``from ..setup import fnm``)
    resolve and two same-named configs never collide.
    """
    init = config_dir / "__init__.py"
    if not init.is_file():
        raise ValueError(
            f"{config_dir}: not a config package (no __init__.py); a game config "
            f"must be an importable directory."
        )
    module_name = _config_module_name(config_dir)

    # Drop any prior load of this package (and its cached submodules) so a reload
    # reads fresh from disk instead of binding stale, previously-execed submodules.
    for name in [n for n in sys.modules if n == module_name or n.startswith(module_name + ".")]:
        del sys.modules[name]

    spec = importlib.util.spec_from_file_location(
        module_name, init, submodule_search_locations=[str(config_dir)]
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ValueError(f"{config_dir}: could not build an import spec for the config.")
    module = importlib.util.module_from_spec(spec)
    # Register before exec so intra-package relative imports resolve.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # On failure, remove the package and any submodules it managed to import.
        for name in [n for n in sys.modules if n == module_name or n.startswith(module_name + ".")]:
            del sys.modules[name]
        raise
    return module


def load_game_config(config_dir: str | Path) -> LoadedConfig:
    """Load a game config from its directory ``config_dir`` and return a handle.

    Steps:

    1. Read ``config_dir/config.yaml`` and validate it (``engine_api == 1`` plus the
       :class:`~engine.types.GameConfigSchema` required keys) — reject with a clear
       ``ValueError`` on any violation.
    2. Import the config's package **by path** (``config_dir/__init__.py``). This
       fires the config's handler ``@register`` decorators and gives access to the
       config's ``new_game`` callable.
    3. Return a :class:`LoadedConfig` exposing the parsed config, the ``new_game``
       callable, the imported module, the populated ``HANDLERS`` and ``EFFECTS``
       registries, the declared state schema (``state_schema.yaml``), the guard
       variable registry and the declared stat names.
    """
    config_dir = Path(config_dir).resolve()
    if not config_dir.is_dir():
        raise ValueError(f"{config_dir}: config directory does not exist.")

    config = load_config(config_dir / "config.yaml")
    if "state" in config:
        raise ValueError(
            f"{config_dir / 'config.yaml'}: a 'state' section is not read; declare the "
            f"value maps in {STATE_SCHEMA_FILE} beside config.yaml"
        )
    state_schema = load_state_schema(config_dir / STATE_SCHEMA_FILE)
    module = _import_config_package(config_dir)

    new_game = getattr(module, "new_game", None)
    if not callable(new_game):
        raise ValueError(
            f"{config_dir}: config package exposes no callable 'new_game' entry point."
        )

    return LoadedConfig(
        config_dir=config_dir,
        config=config,
        new_game=new_game,
        module=module,
        handlers=HANDLERS,
        effects=EFFECTS,
        state_schema=state_schema,
        guard_variables=GUARD_VARIABLES,
        stat_names=STAT_NAMES,
    )
