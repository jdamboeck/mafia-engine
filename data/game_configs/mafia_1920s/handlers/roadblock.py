"""The police roadblock on the map — ports ``mf-prg.bas:2041`` and ``:6000-6036``.

::

    2030 p=br+po(sp)+x:ifp<brorp>br+999goto2010
    2040 po(sp)=po(sp)+x:ms=ms-1
    2041 ifms/20=int(ms/20)andint(rnd(1)*5)=0andra(sp)>3thengosub6000:goto2060
    2060 ms=ms-5:ifms>0goto2000
    6015 fort=1to2000:next:ifint(rnd(1)*3)=0goto6025
    6016 if(ag(sp)and2)<>0goto6030
    6017 ifta(sp)goto6035
    6018 if(ag(sp)and1)<>0goto6025
    6020 print"{down}er hat einen steckbrief von dir!":gosub1100:goto26020
    6025 print"{down}er hat nichts zu beanstanden.":goto1100
    6030 print"{down}dein blueten-schwindel ist aufgeflogen!":gosub1100:goto26020
    6036 print"{down}deckt! es ist aus...":ta(sp)=0:gosub1100:goto26020

The engine turn runner asks :func:`roadblock` (its roadblock hook,
:data:`~engine.turns.ROADBLOCK_HOOK_KEY`) after every street step, with the step and
its point already committed. Only a street step reaches it: a door, a wall and an
armed event cell (``:2045``/``:2046``, poked off the street code) never do, and an
unarmed event cell is a plain street.

**The gate** (``:2041``, :func:`roadblock_would_fire`): the points left after the
step are a multiple of 20 (0 included, so the step that spends the last point can be
stopped), a 1-in-5 roll, and a rank above 3. The source's ``and`` evaluates all three
terms, so it draws the roll on every street step; the port draws it only when the
other two terms hold. The roll is independent of them, so the chance of a stop is the
source's (the fidelity bar is behavioural; the RNG draw order may differ).

**The stop** (``:6000-6036``), in the source's order:

1. a clean pass one time in three (``:6015``), before anything is looked at;
2. counterfeit money (``:6016``): caught, whatever else the player carries;
3. alcohol (``:6017``): the barrels are taken (``ta(sp)=0``), then caught;
4. a passport (``:6018``): the officer finds nothing;
5. otherwise a wanted poster (``:6020``): caught.

The screen (:data:`ROADBLOCK_SCREEN`) shows the heading, the demand for papers and
the finding, and waits for a key (``:1100``). A capture then enters the police
module at ``:26020`` (:func:`~.police.caught`) with the ``p`` the map step left:
``br+po(sp)``, the screen address of the cell the player stands on (``:2030``, with
``:110``'s ``br=52224``). The marks stay as they were: only the barrels are taken.

Every stop, a pass or a capture, returns to ``:2060 ms=ms-5``, so the hook returns
truthy whenever the gate fired and the runner charges the 5 points once. A sentence
or an acquittal has already set ``ms=0`` (the turn ends); a bribe, an escape or a
pass goes on with the points that remain.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

from engine.interactions import Acknowledge
from engine.locations import register
from engine.turns import ROADBLOCK_HOOK_KEY

from ..effects import BarrelChange
from ..state import contraband
from .police import Arrest, caught

__all__ = ["ROADBLOCK_SCREEN", "roadblock", "roadblock_would_fire"]

#: Acknowledge: the roadblock (``:6000-6036``). ``params``: ``lines``, ``(key, params)``
#: pairs -- the heading, the demand for papers, the finding.
ROADBLOCK_SCREEN = "roadblock.screen"

_HEAD = [("roadblock.title", {}), ("roadblock.papers", {})]  # :6000, :6010


def roadblock_would_fire(state, rng) -> bool:
    """``:2041`` ``ifms/20=int(ms/20)andint(rnd(1)*5)=0andra(sp)>3``, for the active player.

    Reads the points the step left (``ms`` after ``:2040``'s ``ms=ms-1``). Draws
    ``range(5)`` (``int(rnd(1)*5)``, 0 is the hit) only when the rank and the points
    let the stop happen; see the module docstring.
    """
    params = state.config.formula_params
    active = state.players[state.clock.active_player]
    if active.rank <= params["roadblock_min_rank"]:  # ra(sp)>3
        return False
    if active.ms % params["roadblock_every"] != 0:  # ms/20=int(ms/20)
        return False
    return rng.range(params["roadblock_roll"]) == 0  # int(rnd(1)*5)=0


@register(ROADBLOCK_HOOK_KEY)
def roadblock(ctx):
    """``:2041`` then ``gosub6000``: the gate, and the stop if it fires.

    Returns truthy when the police stopped the player (the step then ends with the
    ``:2060`` charge), falsy when the gate did not fire.
    """
    if not roadblock_would_fire(ctx.state, ctx.rng):
        return False
    params = ctx.state.config.formula_params
    active = ctx.state.players[ctx.state.clock.active_player]
    held = contraband(active)

    # :6015 ``ifint(rnd(1)*3)=0goto6025``
    if ctx.rng.range(params["roadblock_pass_roll"]) == 0:
        finding = None
    elif held.counterfeit:  # :6016 ``if(ag(sp)and2)<>0goto6030``
        finding = "roadblock.counterfeit"
    elif held.alcohol_barrels != 0:  # :6017 ``ifta(sp)goto6035``
        finding = "roadblock.alcohol"
    elif held.fake_papers:  # :6018 ``if(ag(sp)and1)<>0goto6025``
        finding = None
    else:  # :6020
        finding = "roadblock.wanted_poster"

    if finding is None:
        # :6025 ``print"{down}er hat nichts zu beanstanden.":goto1100``
        yield Acknowledge(ROADBLOCK_SCREEN, {"lines": [*_HEAD, ("roadblock.nothing", {})]})
        return True
    if finding == "roadblock.alcohol":
        ctx.apply(BarrelChange(-held.alcohol_barrels))  # :6036 ``ta(sp)=0``
    # :6020/:6030/:6036 ``gosub1100:goto26020``
    yield Acknowledge(ROADBLOCK_SCREEN, {"lines": [*_HEAD, (finding, {})]})
    # :2030 ``p=br+po(sp)+x``: the cell the step reached, on the screen at ``br``.
    yield from caught(ctx, Arrest(p=params["map_screen_base"] + active.po))
    return True
