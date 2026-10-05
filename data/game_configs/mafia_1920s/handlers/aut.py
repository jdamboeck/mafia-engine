"""The aut (Automobil-Haendler, car dealer) handlers — ports ``mf-prg.bas:14000-14131``.

``:14005 onwgoto14006,14100``: two playable options behind a guardless shell
(``content/locations/aut.yaml``); every refusal happens here, after the pick.

- ``aut.buy`` (``:14006-14052``) — the showroom: three models, four on tile 2
  (``:14010``), priced ``3000+1000*(y-1)``. The cash check (``:14035``) asks for the
  full price before the trade-in is known, and a refused trade-in goes back to the
  showroom (``:14047``), so a car is never bought without trading the old one in. A
  purchase moves the movement points at once (``:14050``
  ``ms=tr(y)-tr(tm(sp))+ms``), so the map goes on with the new car's range.
- ``aut.steal`` (``:14100-14131``) — too crowded 2 times in 3 on every tile but 4
  (``:14100``); otherwise the picked gangster tries the lock. ``:14110``
  ``ifint(rnd(1)*(in/40+kr/30))=0goto14120``: a roll of 0 means the owner catches
  him, and the owner fights (``:14125``, one ``wagenbesitzer`` on ``ks``). Lost, the
  police take the player (``:26020``, a ``goto``: the handler ends there); won, the
  player flees without the car (``:14130``). Any other roll gives the citroen
  (``:14118`` ``tm(sp)=5``) with the movement points as they were.

Neither option touches the alcohol barrels, so a car with a smaller tank keeps them
all (only the pub buy, ``:12025``, and the roadblock read the tank). There is no rank
check anywhere in ``:14000-14131``.

The steal roll
--------------
``int(rnd(1)*(in/40+kr/30))`` is 0 exactly when ``rnd(1)*(in/40+kr/30) < 1``; times
120 (the least common multiple of 40 and 30) that is ``rnd(1)*(3*in+4*kr) < 120``, so
the port draws ``range(3*in+4*kr)`` and the owner catches the thief below 120. That is
exact, since the stats are whole numbers. A gangster with ``3*in+4*kr`` of 120 or less
is always caught, as ``k<=1`` is in the source.

After a lost owner fight the capture's ``p`` is 0: the source's ``p`` there is
whatever the fight left in it, which the port's fight does not report (the catalogue's
header lists this departure, as for the police fight).

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

import math
from pathlib import Path

from engine.effects import MoneyChange, MsChange
from engine.interactions import Confirm, PromptInt, ShowMessage
from engine.locations import register

from ..effects import VehicleSet
from ..setup import KEY_WAIT, load_encounter, load_vehicles, pick_gangster, run_encounter
from .police import Arrest, caught

__all__ = ["aut_buy", "aut_steal"]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: The owner who catches a thief (``:14125``).
_OWNER = load_encounter(_CONFIG_DIR / "content" / "encounters" / "aut_owner.yaml")


def _vehicles() -> list[dict]:
    return load_vehicles(_CONFIG_DIR / "entities" / "vehicles.yaml")


@register("aut.buy")
def aut_buy(ctx):
    """Buy a car — ports ``mf-prg.bas:14006-14052``."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    vehicles = _vehicles()
    ln = active.last_location

    # :14010 ``x=2:ifln=2thenx=x+1``: the models on show, 1..x+1.
    models = params["aut_models"] + (1 if ln == params["aut_extra_model_tile"] else 0)
    while True:  # :14006 — a refused sale and a refused trade-in come back here
        yield ShowMessage("locations.aut.showroom")  # :14006-14008
        yield KEY_WAIT  # :14008 ...:gosub1100:print"{clr}";
        for number in range(1, models + 1):  # :14011-14015
            yield ShowMessage(
                "locations.aut.model",
                {
                    "number": number,
                    "name": vehicles[number]["name"],
                    "price": _price(number, params),
                },
            )
        # :14020-14030 ``getx$`` / ``y=val(x$):ify=0thenpokev+21,0:return`` /
        # ``if(y-1)>xgoto14020``: a digit above the models is read again; 0 leaves,
        # and so does RETURN (``val`` of it is 0).
        y = yield PromptInt("locations.aut.model_prompt", min=0, max=models, blank=0)
        if y == 0:
            return []  # :14025 ...ify=0thenpokev+21,0:return -- no key wait

        # :14035 ``p=3000+1000*(y-1):ifka(sp)<pthengosub1125:goto14006`` — the full
        # price in cash, before any trade-in is known.
        price = _price(y, params)
        if active.ka < price:
            yield ShowMessage("system.not_enough_money")
            yield KEY_WAIT  # :14035 gosub1125 (-> :1100), then goto14006
            continue

        old = active.vehicle
        trade_in = 0  # :14040 ``iftm(sp)=0thenq=0:goto14050``
        if old != 0:
            # :14045 ``q=1000+1000*tm(sp):iftm(sp)=5thenq=1000``
            if old == params["aut_stolen_vehicle"]:
                trade_in = params["aut_trade_in_stolen"]
            else:
                trade_in = params["aut_trade_in_base"] + params["aut_trade_in_step"] * old
            yield ShowMessage("locations.aut.trade_in_offer", {"amount": trade_in})  # :14046
            if not (yield Confirm("locations.aut.confirm")):  # :14047 ``ifx$="n"goto14006``
                continue

        # :14050 ``ka(sp)=ka(sp)-p+q:ms=tr(y)-tr(tm(sp))+ms:tm(sp)=y``
        ctx.apply(MoneyChange(trade_in - price))
        ctx.apply(MsChange(vehicles[y]["tr"] - vehicles[old]["tr"]))
        ctx.apply(VehicleSet(y))
        yield ShowMessage("locations.aut.sold")  # :14051-14052
        yield KEY_WAIT  # :14052 ...:goto1100
        return []


def _price(model: int, params) -> int:
    """``p=3000+1000*(y-1)`` (``:14035``), the price ``:14014`` prints for model ``y``."""
    return params["aut_price_base"] + params["aut_price_step"] * (model - 1)


@register("aut.steal")
def aut_steal(ctx):
    """Steal a car — ports ``mf-prg.bas:14100-14131``."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params

    # :14100 ``ifln<>4andint(rnd(1)*3)<>0thenprint"es sind zuviele leute hier!"``: the
    # roll is drawn on every tile (AND does not short-circuit).
    crowd = ctx.rng.range(params["aut_crowd_roll"])
    if active.last_location != params["aut_no_crowd_tile"] and crowd != 0:
        yield ShowMessage("locations.aut.crowded")
        yield KEY_WAIT  # :14100 ...:goto1100
        return []

    # :14101 ``print"wer soll den wagen aufbrechen:":gosub1130:ify=0thenreturn``
    yield ShowMessage("locations.aut.steal_prompt")
    y = yield from pick_gangster(ctx, cancellable=True)
    if y is None:
        return []

    # :14110 ``ifint(rnd(1)*(in/40+kr/30))=0goto14120`` — see the module docstring.
    thief = active.roster[y]
    in_div, kr_div = params["aut_steal_in_divisor"], params["aut_steal_kr_divisor"]
    scale = math.lcm(in_div, kr_div)
    bound = thief.attrs["intelligenz"] * (scale // in_div) + thief.attrs["kraft"] * (
        scale // kr_div
    )
    if ctx.rng.range(max(bound, 1)) >= scale:
        # :14115-14118 — away with the car; an old one stays behind (:14117).
        if active.vehicle == 0:
            yield ShowMessage("locations.aut.stolen")
        else:
            yield ShowMessage("locations.aut.stolen_old_car_left")
        ctx.apply(VehicleSet(params["aut_stolen_vehicle"]))  # :14118 ``tm(sp)=5``
        yield KEY_WAIT  # :14118 ...:goto1100
        return []

    # :14120 caught; :14125 the owner fights, ``ifs=2goto26020``.
    yield ShowMessage("locations.aut.caught")
    yield KEY_WAIT  # :14120 ...:gosub1100, before the fight
    # The fight's outcome screen ends in its own :30520 key wait (run_encounter).
    result = yield from run_encounter(ctx, _OWNER)
    if result.winner == 2:
        # :26020 the capture, which ends in its own key waits (handlers/police.py).
        yield from caught(ctx, Arrest(p=0))
        return []
    yield ShowMessage("locations.aut.owner_killed")  # :14130-14131
    yield KEY_WAIT  # :14131 ...:goto1100
    return []
