"""The ``mafia_1920s`` handler package.

Importing this package imports every handler module, which fires each handler's
``@register`` decorator so the engine's :data:`engine.locations.HANDLERS` registry
is populated. The config package (``mafia_1920s/__init__.py``) imports this at
config-load time via :func:`engine.config_loader.load_game_config`.
"""

from __future__ import annotations

from . import ble  # noqa: F401 — registers ble.passport / ble.counterfeit
from . import game_end  # noqa: F401 — registers game_end.standings / game_end.year_end
from . import jobs  # noqa: F401 — registers "job.shift" (the employed-turn flow)
from . import kdh  # noqa: F401 — registers kdh.borrow/repay/trade/capital/collect
from . import police  # noqa: F401 — the police capture module the callers share (no handler)
from . import roadblock  # noqa: F401 — registers the roadblock hook (turn.roadblock)
from . import pub  # noqa: F401 — imported for its @register("pub.recruit") side effect
from . import slw  # noqa: F401 — imported for its @register("slw.rent") side effect
from . import sph  # noqa: F401 — imported for its @register("sph") side effect
from . import turn  # noqa: F401 — registers the engine turn runner's hooks (engine.turns)
from . import upkeep  # noqa: F401 — registers "upkeep.turn_start" (engine.upkeep.run_upkeep)
from . import waf  # noqa: F401 — registers waf.buy / waf.train + the weapon_spec sub-state

__all__ = [
    "ble",
    "game_end",
    "jobs",
    "kdh",
    "police",
    "pub",
    "roadblock",
    "slw",
    "sph",
    "turn",
    "upkeep",
    "waf",
]
