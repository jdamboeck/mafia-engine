"""The ``mafia_1920s`` handler package.

Importing this package imports every handler module, which fires each handler's
``@register`` decorator so the engine's :data:`engine.locations.HANDLERS` registry
is populated. The config package (``mafia_1920s/__init__.py``) imports this at
config-load time via :func:`engine.config_loader.load_game_config`.
"""

from __future__ import annotations

from . import pub  # noqa: F401 — imported for its @register("pub.recruit") side effect
from . import slw  # noqa: F401 — imported for its @register("slw.rent") side effect
from . import sph  # noqa: F401 — imported for its @register("sph") side effect
from . import waf  # noqa: F401 — registers waf.buy / waf.train + the weapon_spec sub-state

__all__ = ["pub", "slw", "sph", "waf"]
