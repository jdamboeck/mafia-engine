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

**A jailed opponent** (``:27018 ifgs(us)goto27100``, any sentence, a last month too)
gets the prison brawl instead of the duel (:func:`_prison_brawl`, below).

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

**The prison brawl** (``:27100-27150``)::

    27100 print"{clr}{down}"sp$(us)" sitzt im knast. ein mit-"
    27110 print"{down}mischen'. er verlangt 3000 $. ";:gosub1110:ifx$="n"thenreturn
    27115 ifka(sp)<3000goto1125
    27120 ka(sp)=ka(sp)-3000:print"{down}verteidige dich, "sp$(us)"!":gosub1100
    27125 bn$(0)="mr.bonebreaker":gz(0)=1:gw(0,1)=3:ec(1)=50:ks(2)=0
    27130 z1=gw(us,1):z2=gz(us):gw(us,1)=0:gz(us)=1:ks(1)=us:kf$="kg":gosub30000
    27135 gw(us,1)=z1:gz(us)=z2:ifs=2goto27146
    27140 x=int(rnd(1)*2)+1:print"{clr}{down}"sp$(us)"! deine strafe wird wegen"
    27145 gs(us)=gs(us)+x:goto27150
    27146 a=sp:b=1:gosub1350:en=0:gosub1365
    27150 ms=ms-10:x=2:gosub1160:goto1100

* ``:27100-27110`` the attacker is offered a fellow inmate for 3000 $ and answers j/n;
  "n" leaves at no cost. Only then is the cash checked: under 3000 $ ``:1125`` refuses,
  and the menu comes back with nothing spent (``:27115`` ``goto1125`` skips ``:27150``);
* ``:27120`` the 3000 $ are paid, and the jailed player is told to defend himself;
* ``:27125-27130`` the fight, on ``kg``: side 1 is the jailed player's boss alone and
  unarmed, owned and moved by the jailed player; side 2 is ``mr.bonebreaker``, one CPU
  fighter (``ks(2)=0``) with a schlagkette and 50 energy
  (``content/encounters/prison_inmate.yaml``). The boss alone and unarmed is a per-fight
  roster view (:func:`_boss_alone_unarmed`): the real roster is never changed, which is
  ``:27135``'s restore. The boss's energy loss stays with him;
* ``:27140-27145`` the boss wins: the sentence grows by 1 or 2 months;
* ``:27146`` mr.bonebreaker wins: ``a=sp:b=1`` zeroes the energy of gangster 1 of
  ``sp``, the ATTACKER's boss, not the beaten one (switch
  ``prison_brawl_zeroes_the_attackers_boss``; intent: the jailed boss's);
* ``:27150`` either way the attacker scores +2 and the brawl costs 10 movement points.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from engine.effects import EnergyChange, MoneyChange, MsChange
from engine.interactions import Acknowledge, Confirm, PromptInt, ShowMessage
from engine.locations import register

from ..effects import BarrelChange, Jail, MarkSet, VehicleSet
from ..house_rules import intent
from ..setup import (
    load_encounter,
    load_vehicles,
    run_encounter,
    run_gang_fight,
    score_and_rank,
)
from ..state import contraband, gang_name, wanted

__all__ = [
    "GANG_WAR_SCREEN",
    "SCORE_TO_THE_ATTACKER",
    "ZEROES_THE_ATTACKERS_BOSS",
    "gang_war",
    "plunder",
]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: Acknowledge: one of the gang war's screens, closed by ``:1100``'s key. ``params``:
#: ``lines``, ``(key, params)`` pairs.
GANG_WAR_SCREEN = "gang_war.screen"

#: The house rule for ``:27041``'s ``x=3:gosub1160`` (``content/house_rules.yaml``).
SCORE_TO_THE_ATTACKER = "gang_war_score_to_the_attacker"

#: The house rule for ``:27146``'s ``a=sp:b=1:gosub1350:en=0:gosub1365``.
ZEROES_THE_ATTACKERS_BOSS = "prison_brawl_zeroes_the_attackers_boss"

#: ``:27125`` mr.bonebreaker, the fellow inmate of the prison brawl.
_INMATE = load_encounter(_CONFIG_DIR / "content" / "encounters" / "prison_inmate.yaml")


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


def _boss_alone_unarmed(roster, params: dict) -> tuple:
    """``:27130`` ``gw(us,1)=0:gz(us)=1``: the fight's view of the jailed gang, the boss
    (gangster 1, ``roster[0]``) alone and unarmed. A new tuple: the roster it is taken
    from keeps its weapons and its size (``:27135``'s restore), and the fight writes
    only the boss's energy back, to slot 0."""
    return tuple(replace(boss, weapon=params["prison_brawl_boss_weapon"]) for boss in roster[:1])


def _zero_boss_energy(roster, player: int):
    """``:27146`` ``en=0`` between ``:1350``/``:1365``: gangster 1 of ``player``'s
    ``roster`` at 0 energy, as an :class:`EnergyChange` of minus its energy before the
    fight. A fight only lowers energy, and its own write-back is buffered first, so
    the floor at 0 takes a boss the fight hurt to 0 too."""
    vitality = roster[0].vitality
    return EnergyChange(amount=-vitality, cap=vitality, gangster=0, player=player)


def _prison_brawl(ctx, defender: int):
    """``:27100-27150`` the prison brawl; see the module docstring."""
    state = ctx.state
    params = state.config.formula_params
    attacker = state.clock.active_player
    jailed = state.players[defender]
    price = params["prison_brawl_price"]

    # :27100-27110 the offer, then :1110 ok (j/n)?; "n" returns.
    yield ShowMessage("gang_war.inmate_offer", {"name": jailed.name, "price": price})
    if not (yield Confirm("gang_war.inmate_confirm")):
        return None
    if state.players[attacker].ka < price:  # :27115 ifka(sp)<3000goto1125
        yield Acknowledge(GANG_WAR_SCREEN, {"lines": [("system.not_enough_money", {})]})
        return None
    ctx.apply(MoneyChange(-price))  # :27120 ka(sp)=ka(sp)-3000
    yield Acknowledge(
        GANG_WAR_SCREEN,
        {"lines": [("gang_war.defend_yourself", {"name": jailed.name})]},
        player=defender,
    )

    # :27125-27130 the boss alone and unarmed (ks(1)=us) against the inmate on kg.
    result = yield from run_encounter(
        ctx, _INMATE, roster=_boss_alone_unarmed(jailed.roster, params), owner=defender
    )
    if result.winner == 1:  # :27135 ifs=2goto27146 is false: the boss won
        # :27140 x=int(rnd(1)*2)+1 ... :27145 gs(us)=gs(us)+x
        x = ctx.rng.range(params["prison_brawl_extension"]) + 1
        ctx.apply(Jail(months=wanted(jailed).jail_months + x, player=defender))
        yield Acknowledge(
            GANG_WAR_SCREEN,
            {"lines": [("gang_war.sentence_extended", {"name": jailed.name, "months": x})]},
            player=defender,
        )
    elif intent(state, ZEROES_THE_ATTACKERS_BOSS):
        # The beaten boss.
        ctx.apply(_zero_boss_energy(jailed.roster, defender))
    else:
        # :27146 a=sp:b=1:gosub1350:en=0:gosub1365 -- the attacker's own boss.
        ctx.apply(_zero_boss_energy(state.players[attacker].roster, attacker))

    # :27150 ms=ms-10:x=2:gosub1160 -- on sp, the attacker, in both outcomes.
    ctx.apply(score_and_rank(params["prison_brawl_score"], params))
    ctx.apply(MsChange(-params["prison_brawl_cost"]))
    return None


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
