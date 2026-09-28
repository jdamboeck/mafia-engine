"""The turn hooks — this game's rules at the engine turn runner's fixed keys.

The engine turn runner (:mod:`engine.turns`) owns the ORDER of a turn
(``mf-prg.bas:1010-1013``); every rule in it is a handler registered here under one of
its hook keys, in the SAME :data:`engine.locations.HANDLERS` registry location handlers
use:

* :data:`~engine.turns.EARLY_WIN_HOOK_KEY` — ``:1011``'s turn-start check
  ``ifra(sp)=10andx5%(sp)>0andx6%(sp)>0thensyslh,"sieg-pic":goto40000``. A no-op: the
  two win flags come from the cash-transport and mayor flows (``la=13``/``14``), which
  are not built, so the check can never pass yet.
* :data:`~engine.turns.MOVEMENT_POINTS_HOOK_KEY` — ``:1012`` ``ms=tr(tm(sp))``: the
  active player's vehicle's ``tr``. Returns the value; the runner writes it.
* :data:`~engine.turns.JOB_HOOK_KEY` — ``:1012`` ``ifjo(sp)thengosub25000:goto1010``:
  whether the active player holds a job (the runner then runs ``job.shift``).
* :data:`~engine.turns.SCORE_TRUNCATION_HOOK_KEY` — ``:1013``
  ``gf(sp)=int(gf(sp)*100)/100``.
* :data:`~engine.turns.JAIL_HOOK_KEY` — ``:1013`` ``ifgs(sp)thengosub1500:goto1010``.
  A no-op: nothing can jail a player yet, so no turn is spent in jail.

Handler-API conformance: touches only ``ctx.state`` (read-only), ``ctx.apply(<Effect>)``
and this config's own helpers. None of them draws from ``ctx.rng`` or asks anything.
"""

from __future__ import annotations

import math
from pathlib import Path

from engine.effects import SetScore
from engine.locations import register
from engine.turns import (
    EARLY_WIN_HOOK_KEY,
    JAIL_HOOK_KEY,
    JOB_HOOK_KEY,
    MOVEMENT_POINTS_HOOK_KEY,
    SCORE_TRUNCATION_HOOK_KEY,
)

from ..setup import load_vehicles
from ..state import job

__all__ = [
    "early_win",
    "movement_points",
    "has_job",
    "score_truncation",
    "jail",
    "truncated_score",
]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: Decimal places ``gf * 100`` is rounded to before :func:`truncated_score` floors it.
#: A representation guard, not a rule: IEEE doubles store most whole-cent scores a hair
#: off (``0.29 * 100`` is ``28.999999999999996``), and a plain floor would take a cent
#: off such a score every turn. Rounding to 1e-6 of a cent (5e-9 in ``gf``) absorbs
#: that drift -- at ``gf <= 100`` it is thousands of times the double's own error --
#: while staying at or below the C64's float resolution there (a 32-bit mantissa is
#: about 7e-9 at ``gf`` = 25), so no difference the original could hold is erased.
_SCORE_SNAP_DECIMALS = 6


def truncated_score(gf: float) -> float:
    """``:1013`` ``gf(sp)=int(gf(sp)*100)/100``: the score cut to two decimals.

    BASIC ``int`` is floor, so a negative score goes toward -inf (-0.125 becomes
    -0.13). ``gf * 100`` is rounded to :data:`_SCORE_SNAP_DECIMALS` places first, so
    every whole-cent score is a fixed point and a second truncation changes nothing.
    """
    return math.floor(round(gf * 100, _SCORE_SNAP_DECIMALS)) / 100


@register(EARLY_WIN_HOOK_KEY)
def early_win(ctx):
    """``:1011``'s early-win check. Never passes yet (the win flows are not built)."""
    yield from ()
    return False


@register(MOVEMENT_POINTS_HOOK_KEY)
def movement_points(ctx):
    """``:1012`` ``ms=tr(tm(sp))``: the active player's vehicle's movement points."""
    yield from ()
    active = ctx.state.players[ctx.state.clock.active_player]
    return load_vehicles(_CONFIG_DIR / "entities" / "vehicles.yaml")[active.vehicle]["tr"]


@register(JOB_HOOK_KEY)
def has_job(ctx):
    """``:1012`` ``ifjo(sp)``: an employed player works a shift instead of the free turn."""
    yield from ()
    active = ctx.state.players[ctx.state.clock.active_player]
    return bool(job(active).type)


@register(SCORE_TRUNCATION_HOOK_KEY)
def score_truncation(ctx):
    """``:1013`` ``gf(sp)=int(gf(sp)*100)/100``, once per free turn, after upkeep.

    It runs only on the path to a free turn: after upkeep (``:1011``), so the upkeep
    screens show the score before truncation; after ``:1012``'s job dispatch, so an
    employed player's turn never reaches it; and after ``:1010``'s year-end jump, so the
    final scoring sees each score as it stood when that player's last turn ended.
    """
    yield from ()
    active = ctx.state.players[ctx.state.clock.active_player]
    ctx.apply(SetScore(truncated_score(active.gf)))
    return []


@register(JAIL_HOOK_KEY)
def jail(ctx):
    """``:1013`` ``ifgs(sp)``: a jailed player's turn. Never, yet (no jail is built)."""
    yield from ()
    return False
