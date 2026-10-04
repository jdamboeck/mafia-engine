"""The map win flows — ports ``mf-prg.bas:2002-2003``, ``:2045-2046``, ``:23000-23030``
and ``:24000-24020``.

::

    2002 poke646,0:iftp(sp)=3thenpokebr+569,135:pokefr+569,6
    2003 iftp(sp)=5thenpokebr+861,130:pokefr+861,2
    2035 sysie:ifpeek(p)<>156goto2045
    2045 ifpo(sp)+x=569thenla=13:ln=1:gosub23000:goto2060
    2046 ifpo(sp)+x=861thenla=14:ln=1:gosub24000:goto2060
    2060 ms=ms-5:ifms>0goto2000

The two event cells are plain street on the map (code 156). ``:2002``/``:2003`` poke a
cell off the street code while the active player holds its tip, and only then does a
step onto it fall through ``:2035`` to ``:2045``/``:2046``. So the cell is armed by the
tip alone, derived each time and never stored: each special cell in
``content/map/city.yaml`` carries its ``armed`` guard (``tip = 3`` on 569, ``tip = 5``
on 861), which :func:`special_cell` evaluates and the terminal client's map reads to
draw the armed cell for the active player. An unarmed cell is the street it is.

The engine turn runner asks :func:`special_cell` (its special-cell hook,
:data:`~engine.turns.SPECIAL_CELL_HOOK_KEY`) before every move onto an event cell. Armed,
it runs the cell's flow and returns truthy: the player does not step onto the cell
(``:2040 po(sp)=po(sp)+x`` is the street branch only), and the runner then charges the
door's 5 points (``:2060``), whatever the flow did. Unarmed, it returns falsy and the
move is an ordinary street step (the roadblock may stop it).

**The cash transport** (``:23000-23030``, cell 569, tip 3): the heading; a gang of
fewer than 3 (``:23010``) loses the tip and goes; otherwise the escort's warning, then
the fight against 10 ``eskorte`` (``content/encounters/win_escort.yaml``). Won, +4 score
and the win flag ``x5`` (``:23030``), then the bank's payout (``goto20050``,
:func:`~.ban.heist_payout`): 4000..6999 $, and the held tip 3 matches ``(x=3andla=13)``
so it is cleared and 3000 more paid (7000..9999 $), and +4 score again. The tip is gone,
so the cell is no longer armed; a later tip 3 arms it again and the flow can be won
again (the flag stays set).

**The mayor hit** (``:24000-24020``, cell 861, tip 5): no heading and no gang check; two
fights on "ks", the 5 bodyguards (``win_mayor_1.yaml``), then the mayor
(``win_mayor_2.yaml``). The source keeps the gang's energy in its stats between them; the
port's first fight writes its energy loss back as effects buffered into this handler,
which ``ctx.state`` does not show, so the second fight's gang is the first one's
closing energies (:func:`~.setup.roster_after`). A lost first fight skips the second.
Won, the reward screen, 7000 $, a passport (``ag(sp)or1``), the tip cleared and the win
flag ``x6``; no score.

A lost fight in either flow is ``goto26020``, the arrest (:func:`~.police.caught`),
with ``p`` 0 (the port's fight does not report the ``p`` it leaves, see
:mod:`.police`). A sentence clears the tip; a bribe or an escape keeps it, so the cell
stays armed and the player can step onto it again with the points left.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from engine.conditions import build_context, evaluate
from engine.effects import MoneyChange
from engine.interactions import Acknowledge
from engine.locations import register
from engine.turns import SPECIAL_CELL_HOOK_KEY

from ..effects import MarkSet, TipClear, WinFlagSet
from ..setup import load_encounter, roster_after, run_encounter, score_and_rank
from ..state import tip_target
from .ban import heist_payout
from .police import Arrest, caught

__all__ = [
    "WIN_FLOWS_SCREEN",
    "armed_cells",
    "cash_transport",
    "mayor_hit",
    "special_cell",
]

_CONFIG_DIR = Path(__file__).resolve().parents[1]
_ENCOUNTERS = _CONFIG_DIR / "content" / "encounters"

#: The escort (``:23025``) and the mayor's two fights (``:24005``, ``:24010``).
_ESCORT = load_encounter(_ENCOUNTERS / "win_escort.yaml")
_GUARDS = load_encounter(_ENCOUNTERS / "win_mayor_1.yaml")
_MAYOR = load_encounter(_ENCOUNTERS / "win_mayor_2.yaml")

#: Acknowledge: one of the flows' own screens, closed by ``:1100``'s key. ``params``:
#: ``lines``, ``(key, params)`` pairs.
WIN_FLOWS_SCREEN = "win_flows.screen"


def _special_cells() -> dict[int, dict]:
    """The city's event cells (``content/map/city.yaml``), keyed by cell."""
    raw = yaml.safe_load((_CONFIG_DIR / "content" / "map" / "city.yaml").read_text("utf-8"))
    return {spec["cell"]: spec for spec in raw.get("special_cells", [])}


_CELLS = _special_cells()


def armed_cells(state) -> set[int]:
    """The event cells armed for the active player (``:2002``/``:2003``)."""
    context = build_context(state)
    return {cell for cell, spec in _CELLS.items() if evaluate(spec.get("armed"), context)}


def cash_transport(ctx):
    """The cash-transport hold-up — ``:23000-23030``, then ``:20050-20060``."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    # :23001 ``syslh,"gtp-pic"`` is the picture; :23005 the heading.
    title = ("win_flows.transport_title", {})
    # :23010 ``ifgz(sp)<3thenprint"{down}du hast zuwenig gangster!":tp(sp)=0:goto1100``
    if len(active.roster) < params["win_transport_min_gang"]:
        ctx.apply(TipClear())
        yield Acknowledge(WIN_FLOWS_SCREEN, {"lines": [title, ("win_flows.transport_too_few", {})]})
        return
    # :23015-23020 the escort, then ``gosub1100``.
    yield Acknowledge(WIN_FLOWS_SCREEN, {"lines": [title, ("win_flows.transport_escort", {})]})
    # :23025 ``bn$(0)="eskorte":gz(0)=10:e=50:w=7:kf$="kgtp":gosub5000:ifs=2goto26020``
    result = yield from run_encounter(ctx, _ESCORT)
    if result.winner == 2:
        yield from caught(ctx, Arrest(p=0))
        return
    # :23030 ``x=4:gosub1160:x5%(sp)=1:goto20050``
    ctx.apply(score_and_rank(params["win_transport_score"], params))
    ctx.apply(WinFlagSet("x5"))
    # :20051 ``(x=3andla=13)``: x is the held tip, and la=13 is this flow.
    yield from heist_payout(ctx, tip_bonus=tip_target(active) == params["win_transport_tip"])


def mayor_hit(ctx):
    """The mayor hit — ``:24000-24020``."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    # :24005 ``bn$(0)="leibwaechter":gz(0)=5:e=20:w=7:kf$="ks":gosub5000:ifs=2goto26020``
    first = yield from run_encounter(ctx, _GUARDS)
    if first.winner == 2:
        yield from caught(ctx, Arrest(p=0))
        return
    # :24010 ``bn$(0)="buergermeister":gz(0)=1:e=30:w=1:kf$="ks":gosub5000:ifs=2goto26020``
    # -- with the energy the bodyguards left (see the module docstring).
    second = yield from run_encounter(ctx, _MAYOR, roster=roster_after(active.roster, first))
    if second.winner == 2:
        yield from caught(ctx, Arrest(p=0))
        return
    # :24015-24017 the reward, then :24020's ``goto1100``.
    yield Acknowledge(
        WIN_FLOWS_SCREEN,
        {"lines": [("win_flows.mayor_title", {}), ("win_flows.mayor_reward", {})]},
    )
    # :24020 ``ka(sp)=ka(sp)+7000:ag(sp)=ag(sp)or1:tp(sp)=0:x6%(sp)=1``
    ctx.apply(MoneyChange(params["win_mayor_reward"]))
    ctx.apply(MarkSet(fake_papers=True))
    ctx.apply(TipClear())
    ctx.apply(WinFlagSet("x6"))


#: The flow each event cell's ``flow`` names (``content/map/city.yaml``).
_FLOWS = {"cash_transport": cash_transport, "mayor_hit": mayor_hit}


@register(SPECIAL_CELL_HOOK_KEY)
def special_cell(ctx, *, cell, la):
    """``:2035``/``:2045``/``:2046``: run the cell's flow if it is armed; see the module
    docstring. Returns whether it was armed (the runner then charges ``:2060``)."""
    if cell not in armed_cells(ctx.state):
        return False
    yield from _FLOWS[_CELLS[cell]["flow"]](ctx)
    return True
