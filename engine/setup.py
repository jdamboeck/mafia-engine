"""Generic engine setup helpers (NO game-specific code).

The Mafia-specific new-game setup (``new_game``, ``fnm``, vehicle/rank loaders)
now lives in the *game config* (``data/game_configs/mafia_1920s/setup.py``), so it
can be copied with the config (docs/design/product-and-scope.md and docs/design/config-and-content-contract.md). The engine keeps only the generic
Engine<->Config plumbing, which lives in :mod:`engine.config_loader`.

This module re-exports that generic plumbing for convenience; it holds no Mafia
formulas, constants, or setup flow.
"""

from __future__ import annotations

from engine.config_loader import ENGINE_API, load_config, load_game_config

__all__ = ["ENGINE_API", "load_config", "load_game_config"]
