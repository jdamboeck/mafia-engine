"""The ban (Bank/Postamt) handlers — ports ``mf-prg.bas:20000-20060``.

``:20005 onwgoto20009,20100``: two options behind a guardless shell
(``content/locations/ban.yaml``), and a leave (``:3045``), which goes back to the map
before ``:20000`` is reached. Both options first run the same prologue, in the source's
order (``la=10``; ``ln`` is the tile, 1..5):

1. ``:20003 ifra(sp)<3thenprint"werde erst '"ra$(3)"'!":goto1100`` — a player below
   rank 3 is told the rank to reach (its name, "kleiner fisch").
2. ``:20004 ifll(sp)=20*la+lngoto17008`` — the revisit trap: the shop's
   (:func:`.sgl.revisit_trap`, ``:17008-17009``), text and police fight on "ks" included.

``ban.holdup`` (``:20009-20015``) — the daytime hold-up:

- ``:20009 ifgz(sp)=1thenprint"du brauchst einen begleiter!"`` — the boss alone is
  refused (the source tests exactly 1; the gang is never empty, see below).
- ``:20010 ifint(rnd(1)*3)=0goto20050`` — 1 time in 3 the guards miss it and the payout
  follows at once; 2 times in 3 ``:20011-20012`` "leider hast du die drei wachmaenner am
  eingang uebersehen...", and the guards fight: ``gz(0)=3-(ln=1)`` (C64 true is -1, so
  4 on tile 1, 3 elsewhere; the text says "drei" either way, house rule
  ``bank_guards_text_says_three``), ``:20015 bn$(0)="wachmaenner":e=30:w=6:kf$="kb"``
  (``content/encounters/ban_guards.yaml``).
- Lost, ``ifs=2goto26020``: the arrest, with no police fight. Nothing was taken before
  the fight, so capture sees the cash as it was. The ``p`` the fight leaves is not
  reported, so the arrest gets 0, as after a lost police fight (``handlers/police.py``).
- Won (or not fought), ``:20050``: :func:`heist_payout`, with ``+500`` on tile 1 and the
  pub's tip 2 ("die bank an der hauptstrasse", ``:12235``) paying only on tile 2.

``ban.safe`` (``:20100-20150``) — the night safe-crack, the next unit's work. Until then
it runs the prologue above (``:20003-20004`` come before ``:20005``'s dispatch, so they
are the source's for this option too) and then does nothing: no screen, no effect.

The source's gang is never empty (``gz(sp)`` only grows, and the eviction sets it to 1,
``:4651``); a port state with no gangster, where there is nobody to fight, leaves the
bank quietly with nothing changed once the prologue has passed.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import MoneyChange
from engine.interactions import ShowMessage
from engine.locations import register

from ..effects import TipClear
from ..setup import load_encounter, load_ranks, run_encounter, score_and_rank
from ..state import tip_target
from .police import Arrest, caught
from .sgl import revisit_trap

__all__ = ["ban_holdup", "ban_safe", "heist_payout"]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: The guards at the door (``:20012-20015``): variant 0 is 3 men, variant 1 (tile 1) 4.
_GUARDS = load_encounter(_CONFIG_DIR / "content" / "encounters" / "ban_guards.yaml")


def _prologue(ctx):
    """``:20003-20004``: the rank gate, then the revisit trap. Returns whether the visit
    is over."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    rank = params["ban_rank_min"]
    # :20003 ``ifra(sp)<3thenprint"werde erst '"ra$(3)"'!":goto1100``
    if active.rank < rank:
        ranks = load_ranks(_CONFIG_DIR / "entities" / "ranks.yaml")
        yield ShowMessage("locations.ban.rank_too_low", {"rank": ranks[rank - 1]})
        return True
    # :20004 ``ifll(sp)=20*la+lngoto17008``
    return (yield from revisit_trap(ctx))


@register("ban.holdup")
def ban_holdup(ctx):
    """The daytime hold-up — ports ``mf-prg.bas:20009-20015``, then ``:20050-20060``."""
    if (yield from _prologue(ctx)):
        return []
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    ln = active.last_location
    if not active.roster:  # see the module docstring: nobody to fight
        return []
    # :20009 ``ifgz(sp)=1thenprint"du brauchst einen begleiter!":goto1100``
    if len(active.roster) == params["ban_alone_gang"]:
        yield ShowMessage("locations.ban.alone")
        return []
    # :20010 ``ifint(rnd(1)*3)=0goto20050``
    if ctx.rng.range(params["ban_fight_roll"]) != 0:
        yield ShowMessage("locations.ban.guards")  # :20011-20012, then :1100's key
        # :20012 ``gz(0)=3-(ln=1)``; :20015 ``bn$(0)="wachmaenner":e=30:w=6:kf$="kb":
        # gosub5000:ifs=2goto26020``
        variant = 1 if ln == params["ban_main_tile"] else 0
        result = yield from run_encounter(ctx, _GUARDS, variant=variant)
        if result.winner == 2:
            yield from caught(ctx, Arrest(p=0))
            return []
    # :20050 ``-500*(la=10andln=1)``: C64 true is -1, so +500 on tile 1.
    # :20051 ``(x=2andla=10andln=2)``: the pub's tip 2 pays on tile 2 only.
    yield from heist_payout(
        ctx,
        tip_bonus=tip_target(active) == params["ban_tip"] and ln == params["ban_tip_tile"],
        adjust=params["ban_main_bonus"] if ln == params["ban_main_tile"] else 0,
    )
    return []


@register("ban.safe")
def ban_safe(ctx):
    """The night safe-crack, ``mf-prg.bas:20100-20150`` — only the shared prologue
    (``:20003-20004``) so far; see the module docstring."""
    yield from _prologue(ctx)
    return []


def heist_payout(ctx, *, tip_bonus: bool, adjust: int = 0):
    """The heist payout, ``mf-prg.bas:20050-20060``: the bank's, shared with the railway
    station's mail train (``:19040 goto20050``) and the cash transport.

    ``:20050 p=int(rnd(1)*3000)+4000-500*(la=10andln=1):x=tp(sp)`` — ``adjust`` is the
    caller's ``-500*(la=10andln=1)`` term (C64 true is -1, so it is +500 on the bank's
    tile 1, 0 elsewhere). ``:20051 if(x=1andla=9)or(x=2andla=10andln=2)or(x=3andla=13)
    thentp(sp)=0:p=p+3000`` — ``tip_bonus`` is whether the held tip matches the heist.
    ``:20055-20060`` the loot is shown, ``ka(sp)=ka(sp)+p:x=4:gosub1160``.
    """
    params = ctx.state.config.formula_params
    p = ctx.rng.range(params["heist_pay_spread"]) + params["heist_pay_min"] + adjust
    if tip_bonus:
        ctx.apply(TipClear())
        p += params["heist_tip_bonus"]
    yield ShowMessage("locations.ban.loot", {"p": p})
    ctx.apply(MoneyChange(p))
    ctx.apply(score_and_rank(params["heist_score"], params))
