"""Which loaded config is live, and the check a handle passes before it is used.

The product runs one game config at a time (docs/design/config-and-content-contract.md).
A config fills the engine's registries (handlers, effects, consequences, sub-states,
guard variables, stat names) when its package is imported, and those registries are
process-wide: loading another config replaces what the first one registered. A handle
to the replaced config (:class:`~engine.config_loader.LoadedConfig`, its
:class:`~engine.persistence.Registries`) would then save, load, replay or run turns
through the other config's code. So each handle carries the :class:`ConfigOwner` it
was loaded as, :func:`~engine.config_loader.load_game_config` records the active one
once a load succeeds, and the engine's entry points (``save_game``, ``load_game``,
``replay``, ``TurnRunner`` and an owned ``step``) call :func:`check_owner`, which
raises :class:`StaleConfigError` for a handle that is no longer the live config.

The owner is the config's resolved path (its digest is the token), not a load
counter: reloading the same directory makes every earlier handle to it live again.
An owner of ``None`` (engine code or a test run with no config) is never checked.

This module imports nothing from the rest of the engine, so every layer that checks
an owner can import it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ConfigOwner", "StaleConfigError", "active_owner", "check_owner"]


@dataclass(frozen=True)
class ConfigOwner:
    """The config a handle was loaded from: its resolved directory and that path's digest."""

    config_dir: Path
    token: str

    @classmethod
    def for_dir(cls, config_dir: Path) -> ConfigOwner:
        """The owner of the config at ``config_dir`` (already resolved)."""
        return cls(config_dir, hashlib.sha1(str(config_dir).encode()).hexdigest()[:8])


class StaleConfigError(RuntimeError):
    """Raised when a handle to a config is used after another config was loaded.

    ``handle`` is the owner the handle was loaded as; ``active`` the config loaded
    since (``None`` if none is).
    """

    def __init__(self, handle: ConfigOwner, active: ConfigOwner | None) -> None:
        self.handle = handle
        self.active = active
        now = active.config_dir if active is not None else "no config"
        super().__init__(
            f"this handle belongs to the config at {handle.config_dir}, but {now} "
            "has been loaded since; load the config again to get a live handle"
        )


#: The config whose package filled the registries last, set by a successful load.
_ACTIVE: ConfigOwner | None = None


def active_owner() -> ConfigOwner | None:
    """The live config's owner, or ``None`` before any config loaded."""
    return _ACTIVE


def _activate(owner: ConfigOwner | None) -> None:
    """Record ``owner`` as the live config (the loader's, after a clean load)."""
    global _ACTIVE
    _ACTIVE = owner


def check_owner(owner: ConfigOwner | None) -> None:
    """Raise :class:`StaleConfigError` unless ``owner`` is ``None`` or the live config."""
    if owner is None:
        return
    if _ACTIVE is None or owner.token != _ACTIVE.token:
        raise StaleConfigError(owner, _ACTIVE)
