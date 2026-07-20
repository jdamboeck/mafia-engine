"""Abstract Engine<->Config contracts (docs/design/config-and-content-contract.md).

These are the *type contracts* the engine expects a game config to satisfy. The
engine is a **genre engine**, not a single-title clone: a new game is a *copy of a
config directory* supplying its own data, formulas, strings and Python handlers
(docs/design/product-and-scope.md). This module names what such a config must provide and gives the
engine a real, *exercised* validation layer for it — these are not
declared-and-unused: :mod:`engine.config_loader` calls every validator here at
config-load time.

Contracts defined:

* :class:`HandlerFunc` — the structural type of a handler generator factory.
* :class:`LocationDef` — the required shape of a loaded location shell (reuses
  :class:`engine.locations.Location`/:class:`engine.locations.Option`).
* :func:`validate_vehicle` / :func:`validate_rank` — entity field validators
  (:class:`VehicleInstance` / :class:`RankInstance` describe the required fields).
* :class:`GameConfigSchema` + :func:`validate_config` — the top-level ``config.yaml``
  contract, checked at load time.

Layering: ``engine/`` imports nothing from ``server``/``clients``/transport, and
imports nothing from any config under ``data/`` (configs are loaded by path at
runtime — see :mod:`engine.config_loader`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generator, Protocol, runtime_checkable

# Re-export the canonical location-shell dataclasses as the LocationDef contract.
# The loader/tests treat these as the required shape of a parsed location shell
# (a ``key: str`` plus an ordered ``options: list``).
from engine.locations import Location as LocationDef  # noqa: F401
from engine.locations import Option  # noqa: F401

if TYPE_CHECKING:  # pragma: no cover - typing only
    from engine.interactions import Ctx, Interaction

__all__ = [
    "HandlerFunc",
    "LocationDef",
    "Option",
    "VehicleInstance",
    "RankInstance",
    "WeaponInstance",
    "GameConfigSchema",
    "validate_vehicle",
    "validate_rank",
    "validate_weapon",
    "validate_config",
    "ConfigValidationError",
]


class ConfigValidationError(ValueError):
    """Raised when a game config does not satisfy an engine type contract."""


@runtime_checkable
class HandlerFunc(Protocol):
    """A handler generator *factory*: ``ctx -> Generator[Interaction, response, list]``.

    A game config registers callables of this shape under a handler-id in
    :data:`engine.locations.HANDLERS`. Structural (duck-typed): any callable
    returning a generator satisfies it. Used to document/annotate the registry.
    """

    def __call__(self, ctx: "Ctx") -> "Generator[Interaction, Any, list]":
        ...


# --- Entity contracts ------------------------------------------------------
#
# A config supplies entity tables (vehicles, ranks). These describe the required
# fields per entity; the validators below actually run at load time so a config
# whose entity data is missing a field is rejected with a clear error.


class VehicleInstance(Protocol):
    """Required fields of one vehicle entity the config supplies."""

    name: str
    tank: int
    tr: int


class RankInstance(Protocol):
    """Required shape of one rank entity — a bare name string.

    Ranks are supplied as a flat list of name strings (``ranks[i]`` == in-game
    rank ``i + 1``); the "instance" is the string itself.
    """


_VEHICLE_FIELDS: dict[str, type] = {"name": str, "tank": int, "tr": int}


def validate_vehicle(entry: Any, index: int | None = None) -> dict:
    """Validate one vehicle dict has ``name:str``, ``tank:int``, ``tr:int``.

    Raises :class:`ConfigValidationError` on a missing or wrong-typed field.
    Returns the entry unchanged on success.
    """
    where = f"vehicle[{index}]" if index is not None else "vehicle"
    if not isinstance(entry, dict):
        raise ConfigValidationError(f"{where} must be a mapping, got {type(entry).__name__}")
    for field_name, field_type in _VEHICLE_FIELDS.items():
        if field_name not in entry:
            raise ConfigValidationError(f"{where} missing required field {field_name!r}")
        if not isinstance(entry[field_name], field_type):
            raise ConfigValidationError(
                f"{where} field {field_name!r} must be {field_type.__name__}, "
                f"got {type(entry[field_name]).__name__}"
            )
    return entry


def validate_rank(entry: Any, index: int | None = None) -> str:
    """Validate one rank entry is a name string. Returns it on success."""
    where = f"rank[{index}]" if index is not None else "rank"
    if not isinstance(entry, str):
        raise ConfigValidationError(
            f"{where} must be a string name, got {type(entry).__name__}"
        )
    return entry


class WeaponInstance(Protocol):
    """Required fields of one weapon entity the config supplies.

    ``name/price/ts/tg/ws`` are the DATA-table fields (``mf-prg.bas:50100-50115``);
    ``range`` is the shot's travel distance in combat cells, DERIVED from the attack
    block (``30215-30216``); ``req_int/req_kraft/req_brut`` are the per-weapon stat
    minimums DERIVED from the buy-guard lines (``13050-13060``) that the ``waf``
    handler enforces at arm time.
    """

    name: str
    price: int
    ts: int
    tg: int
    range: int
    ws: int
    req_int: int
    req_kraft: int
    req_brut: int


_WEAPON_FIELDS: dict[str, type] = {
    "name": str,
    "price": int,
    "ts": int,
    "tg": int,
    "range": int,
    "ws": int,
    "req_int": int,
    "req_kraft": int,
    "req_brut": int,
}


def validate_weapon(entry: Any, index: int | None = None) -> dict:
    """Validate one weapon dict has every :class:`WeaponInstance` field with its type.

    Raises :class:`ConfigValidationError` on a missing or wrong-typed field.
    Returns the entry unchanged on success.
    """
    where = f"weapon[{index}]" if index is not None else "weapon"
    if not isinstance(entry, dict):
        raise ConfigValidationError(f"{where} must be a mapping, got {type(entry).__name__}")
    for field_name, field_type in _WEAPON_FIELDS.items():
        if field_name not in entry:
            raise ConfigValidationError(f"{where} missing required field {field_name!r}")
        if not isinstance(entry[field_name], field_type):
            raise ConfigValidationError(
                f"{where} field {field_name!r} must be {field_type.__name__}, "
                f"got {type(entry[field_name]).__name__}"
            )
    return entry


# --- Top-level config contract ---------------------------------------------


class GameConfigSchema:
    """The contract for a config's ``config.yaml`` top-level mapping.

    Names the keys the engine requires and their expected types. :func:`validate_config`
    checks a parsed config dict against this; the loader runs it at load time.
    """

    #: engine_api the engine speaks. A config MUST declare this exact value.
    ENGINE_API: int = 1

    #: Required top-level keys and their required Python types (after YAML parse).
    REQUIRED_KEYS: dict[str, type] = {
        "engine_api": int,
        "entities": dict,
        "formula_params": dict,
        "setup": dict,
        "input_ranges": dict,
    }


def validate_config(cfg: Any) -> dict:
    """Validate a parsed ``config.yaml`` dict against :class:`GameConfigSchema`.

    Checks ``engine_api == 1`` and that every required key is present and of the
    required type. Raises :class:`ConfigValidationError` (a ``ValueError``) with a
    clear message on any violation; returns the dict on success.
    """
    if not isinstance(cfg, dict):
        raise ConfigValidationError(
            f"config must be a mapping, got {type(cfg).__name__}"
        )

    api = cfg.get("engine_api")
    if api is None:
        raise ConfigValidationError(
            "config declares no 'engine_api'; this engine requires "
            f"engine_api == {GameConfigSchema.ENGINE_API}."
        )
    if api != GameConfigSchema.ENGINE_API:
        raise ConfigValidationError(
            f"unsupported engine_api {api!r}; this engine only accepts "
            f"engine_api == {GameConfigSchema.ENGINE_API}."
        )

    for key, key_type in GameConfigSchema.REQUIRED_KEYS.items():
        if key not in cfg:
            raise ConfigValidationError(
                f"config missing required key {key!r} (required by GameConfigSchema)."
            )
        if not isinstance(cfg[key], key_type):
            raise ConfigValidationError(
                f"config key {key!r} must be {key_type.__name__}, "
                f"got {type(cfg[key]).__name__}."
            )
    return cfg
