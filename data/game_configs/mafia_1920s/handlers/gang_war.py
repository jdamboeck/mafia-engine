"""The gang war — turn menu option 3, ports ``mf-prg.bas:27000-27045``.

::

    1021 print"{down}2 - durch die stadt gehen":print"{down}3 - bandenkrieg"
    1035 onxgosub1200,2000,27000
    27000 ifsz=1thenprint"{clr}{down}bei solo-spiel nicht moeglich!":goto1100
    27001 ifja<1925+4/12thenprint"{clr}{down}erst ab 4/1925 moeglich!":goto1100
    27010 fori=1tosz:ifi=spgoto27013
    27016 us=val(x$):ifus=0thenreturn
    27017 ifus<1orus>szorus=spgoto27015
    27018 ifgs(us)goto27100
    27020 ks(1)=us:ks(2)=sp:kf$="ks":gosub30000:a=ks(s):b=ks(1-(s=1))
    27025 p=int(rnd(1)*ka(b)/6)+int(ka(b)/4)
    27028 ...:iftm(b)=0goto27035
    27031 print"{down}mitnehmen (j/n) ?":gosub1115:ifx$="j"thentm(a)=tm(b):tm(b)=0
    27035 ka(a)=ka(a)+p:ka(b)=ka(b)-p:ag(a)=ag(a)or(ag(b)and1):ag(b)=ag(b)and254
    27040 x=tk(tm(a))-ta(a):ifx>ta(b)thenx=ta(b)
    27041 ta(a)=ta(a)+x:ta(b)=ta(b)-x:x=3:gosub1160:y=sp:sp=b:x=-1:gosub1160:sp=y
    27045 ms=ms-10:goto1100

**The gates.** The menu always offers the option (``:1021``); the handler refuses a
solo game (``:27000``) and a date before the round ``ja`` reaches ``1925+4/12``
(``:27001``). In VICE the fourth addition of ``1/12`` compares ``>=`` that value, so the
gate opens on the round the header shows as ``1925-5`` (0-based ``Clock.month`` 4), while
the message says ``4/1925``. A refusal costs nothing and the menu comes back.

**The opponent.** Every other player is listed (a jailed one too), numbered as the
seats are; ``0`` leaves (``:27016``), and a digit above the players or the attacker's own
is read again (``:27017``). The key is read as a number: a letter, which ``val()`` reads
as 0 and so leaves on the C64, is asked again here, and RETURN leaves (the
catalogue's header lists this departure).

**A jailed opponent** (``:27018 ifgs(us)goto27100``) gets the prison brawl,
``:27100-27150``, which is not ported yet (U36): until it is, picking a jailed player
goes back to the menu with nothing done and nothing spent (:func:`_prison_brawl`).

**The duel** (``:27020``): the defender's gang is side 1 and moves first, the attacker's
is side 2, on ``ks``; each player moves his own side (:func:`~..setup.run_gang_fight`),
and each gang's energy loss stays with its player. ``a`` is the winner, ``b`` the loser
(``1-(s=1)``, C64 true is -1): either player may win.

**The consequences**, in the source's order and all from the values before the fight
(the fight changes no cash, vehicle, mark or barrel):

* ``:27025`` the plunder ``p``, a quarter to five twelfths of the loser's cash;
* ``:27026-27031`` the winner's screen, and the vehicle question when the loser has one,
  answered by the winner: "j" takes it (the winner's own is gone), the loser walks; no
  movement points change (``ms`` is not recomputed);
* ``:27035`` the cash, and the passport goes over to the winner (the counterfeit mark
  stays);
* ``:27040-27041`` the barrels: the free room in the winner's (new) tank, at most the
  loser's stock. A winner over his tank's capacity has negative room, so the barrels go
  to the loser (catalogued, faithful-only);
* ``:27041`` the score: +3 to ``sp``, the attacker, whoever won (switch
  ``gang_war_score_to_the_attacker``; intent: +3 to the winner), then -1 to the loser.
  Each goes through ``:1160``'s clamp on its own;
* ``:27045`` the duel costs 10 movement points; at 10 or fewer the turn ends.

No one is jailed, killed or dropped from a gang, and a job is untouched.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import MoneyChange, MsChange
from engine.interactions import Acknowledge, Confirm, PromptInt, ShowMessage
from engine.locations import register

from ..effects import BarrelChange, MarkSet, VehicleSet
from ..house_rules import intent
from ..setup import load_vehicles, run_gang_fight, score_and_rank
from ..state import contraband, gang_name, wanted

__all__ = ["GANG_WAR_SCREEN", "SCORE_TO_THE_ATTACKER", "gang_war", "plunder"]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: Acknowledge: one of the gang war's screens, closed by ``:1100``'s key. ``params``:
#: ``lines``, ``(key, params)`` pairs.
GANG_WAR_SCREEN = "gang_war.screen"

#: The house rule for ``:27041``'s ``x=3:gosub1160`` (``content/house_rules.yaml``).
SCORE_TO_THE_ATTACKER = "gang_war_score_to_the_attacker"


def plunder(rng, cash: int, params: dict) -> int:
    """``:27025`` ``p=int(rnd(1)*ka(b)/6)+int(ka(b)/4)``: the loser's ``cash`` plundered.

    ``int(rnd(1)*n/6)`` is ``range(n)//6`` for a whole ``n > 0``
    (``int(int(r*n)/6) == int(r*n/6)``), so each value has the source's share (the
    last one less likely when 6 does not divide ``n``). ``int`` floors, as ``//`` does.
    Cash of 0 plunders 0. Below 0 (no path is known to reach it) the roll floors
    below 0, ``-(range(-n)//6+1)``.
    """
    spread = params["gang_war_plunder_spread"]
    base = cash // params["gang_war_plunder_base"]
    if cash > 0:
        return rng.range(cash) // spread + base
    if cash < 0:
        return -(rng.range(-cash) // spread + 1) + base
    return base


def _too_early(clock, params: dict) -> bool:
    """``:27001`` ``ifja<1925+4/12``: before the round the gate opens on."""
    return (clock.year, clock.month) < (params["gang_war_from_year"], params["gang_war_from_month"])


def _prison_brawl(ctx, defender: int):
    """``:27100-27150`` the prison brawl -- not ported yet (U36): nothing happens."""
    yield from ()


@register("turn.gang_war")
def gang_war(ctx):
    """``:27000-27045`` the gang war; see the module docstring. Returns nothing: the
    turn menu comes back while movement points remain (``:1045``)."""
    state = ctx.state
    params = state.config.formula_params
    clock = state.clock
    attacker = clock.active_player
    if clock.player_count == 1:  # :27000
        yield Acknowledge(GANG_WAR_SCREEN, {"lines": [("gang_war.solo", {})]})
        return None
    if _too_early(clock, params):  # :27001
        yield Acknowledge(GANG_WAR_SCREEN, {"lines": [("gang_war.too_early", {})]})
        return None

    # :27005-27013 the heading, then every other player: number, gang, cash, gang size.
    yield ShowMessage("gang_war.title")
    for seat, player in enumerate(state.players):
        if seat == attacker:  # :27010 ifi=spgoto27013
            continue
        yield ShowMessage(
            "gang_war.opponent",
            {
                "number": seat + 1,
                "gang_name": gang_name(player),
                "cash": player.ka,
                "bar": "",
                "gang_size": len(player.roster),
            },
        )
    # :27015-27017 getx$ / us=val(x$):ifus=0thenreturn / ifus<1orus>szorus=spgoto27015
    while True:
        us = yield PromptInt("gang_war.opponent_prompt", min=0, max=clock.player_count, blank=0)
        if us == 0:
            return None
        if us - 1 != attacker:
            break
    defender = us - 1

    if wanted(state.players[defender]).jail_months:  # :27018 ifgs(us)goto27100
        yield from _prison_brawl(ctx, defender)
        return None

    # :27020 ks(1)=us:ks(2)=sp:kf$="ks":gosub30000:a=ks(s):b=ks(1-(s=1))
    result = yield from run_gang_fight(
        ctx, defender=defender, attacker=attacker, grid=params["gang_war_grid"]
    )
    a, b = (defender, attacker) if result.winner == 1 else (attacker, defender)
    winner, loser = state.players[a], state.players[b]

    p = plunder(ctx.rng, loser.ka, params)  # :27025
    # :27026-27028 the winner's screen.
    lines = [("gang_war.plunder", {"name": winner.name, "amount": p})]
    winner_vehicle, loser_vehicle = winner.vehicle, loser.vehicle
    vehicles = load_vehicles(_CONFIG_DIR / "entities" / "vehicles.yaml")
    if loser_vehicle:  # :27028 iftm(b)=0goto27035
        offer = ("gang_war.vehicle_offer", {"vehicle": vehicles[loser_vehicle]["name"]})
        # :27030-27031 the winner answers (:1115 j/n).
        for key, line_params in lines:
            yield ShowMessage(key, line_params, player=a)
        yield ShowMessage(*offer, player=a)
        lines += [offer, ("gang_war.vehicle_confirm", {})]
        if (yield Confirm("gang_war.vehicle_confirm", player=a)):
            # tm(a)=tm(b):tm(b)=0 -- the winner's own vehicle is gone.
            winner_vehicle, loser_vehicle = loser_vehicle, 0
            ctx.apply(VehicleSet(winner_vehicle, player=a))
            ctx.apply(VehicleSet(loser_vehicle, player=b))
    yield Acknowledge(GANG_WAR_SCREEN, {"lines": lines}, player=a)  # :27045 goto1100

    # :27035 ka(a)=ka(a)+p:ka(b)=ka(b)-p:ag(a)=ag(a)or(ag(b)and1):ag(b)=ag(b)and254
    ctx.apply(MoneyChange(p, player=a))
    ctx.apply(MoneyChange(-p, player=b))
    if contraband(loser).fake_papers:
        ctx.apply(MarkSet(fake_papers=True, player=a))
    ctx.apply(MarkSet(fake_papers=False, player=b))

    # :27040 x=tk(tm(a))-ta(a):ifx>ta(b)thenx=ta(b)
    winner_barrels = contraband(winner).alcohol_barrels
    loser_barrels = contraband(loser).alcohol_barrels
    x = min(vehicles[winner_vehicle]["tank"] - winner_barrels, loser_barrels)
    # :27041 ta(a)=ta(a)+x:ta(b)=ta(b)-x
    ctx.apply(BarrelChange(x, player=a))
    ctx.apply(BarrelChange(-x, player=b))

    # :27041 x=3:gosub1160 -- on sp, the attacker -- then y=sp:sp=b:x=-1:gosub1160:sp=y
    scored = a if intent(state, SCORE_TO_THE_ATTACKER) else attacker
    ctx.apply(score_and_rank(params["gang_war_winner_score"], params, player=scored))
    ctx.apply(score_and_rank(params["gang_war_loser_score"], params, player=b))

    ctx.apply(MsChange(-params["gang_war_cost"]))  # :27045 ms=ms-10
    return None
