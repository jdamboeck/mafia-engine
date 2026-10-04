"""The ble (Blueten-Eddie) handlers — ports ``mf-prg.bas:22000-22120``.

``:22005 onwgoto22010,22100``: two playable options behind a guardless shell
(``content/locations/ble.yaml``); every refusal happens here, after the pick.

- ``ble.passport`` (``:22010-22020``) — a passport for the whole gang at
  ``1000*gz(sp)``; the confirm comes before the cash check. Buying sets the passport
  mark (``ag(sp) or 1``) and scores 1. The source never looks at ``ag(sp)`` first, so a
  holder pays again for nothing (house rule ``passport_rebuy_charged``).
- ``ble.counterfeit`` (``:22100-22120``) — the player stakes ``q`` real dollars and
  gets ``p=int(rnd(1)*q/2)+q+100`` in counterfeit bills, so the cash rises by
  ``p-q``. The roll comes before the confirm. Buying sets the counterfeit mark
  (``ag(sp) or 2``) and scores 1.

The marks do nothing here; the roadblock reads them, and upkeep fades them
(see ``handlers/upkeep.py``, ``:4055-4056``).

The stake prompt (``:22105-22106``)
-----------------------------------
``input"{down}willst du anlegen (0-5000)";q:ifq<=0thenreturn`` then
``ifq>5000orq>ka(sp)thenprint"{up}{up}";:goto22105``:

- a stake at or below 0 leaves quietly. That includes a negative: C64 ``INPUT``
  takes "-5" as -5 (checked in VICE), and ``q<=0`` returns. So the prompt's floor lets
  any negative through to that return instead of asking again;
- a stake above 5000 or above the cash is asked again. That is the prompt's ceiling:
  the driver asks again for an answer above it, as ``:22106`` does.

Two answers the C64 takes are not ported, because the port's prompt reads whole
numbers only (the engine's ``PromptInt``); both were checked in VICE:

- a fraction (".5", "2.7") is a stake on the C64. ``q`` cancels out of
  ``ka(sp)-q+p`` (the gain is ``100+int(rnd(1)*q/2)``), so cash stays whole but for a
  float residue of a few millionths, and the offer shows a fraction ("100.5").
  Here the prompt asks again;
- an empty answer leaves ``q`` as it was, and ``q`` is a scratch variable shared with
  the pub (``:12025``), the arms dealer (``:13070``), the car dealer (``:14045``) and
  the fight AI (``:30490``). The C64 then deals on whatever ``q`` last held, if it
  passes ``:22106``. The port keeps no such variable; its prompt asks again.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

from engine.effects import MoneyChange
from engine.interactions import Confirm, PromptInt, ShowMessage
from engine.locations import register

from ..effects import MarkSet
from ..setup import score_and_rank

__all__ = ["ble_counterfeit", "ble_passport"]

#: The stake prompt's floor: any negative whole number reaches ``:22105``'s
#: ``ifq<=0thenreturn`` rather than being asked again.
_ANY_NEGATIVE = -(2**31)


@register("ble.passport")
def ble_passport(ctx):
    """Buy a passport for the gang — ports ``mf-prg.bas:22010-22020``."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params

    count = len(active.roster)  # :22010 ``x=gz(sp)`` — the boss is roster[0]
    price = params["ble_passport_price"] * count  # :22010 ``p=1000*x``
    if count == 1:  # :22011 ``ifx=1thenprint"fuer einen pass"``
        yield ShowMessage("locations.ble.passport_for_one")
    else:  # :22012 ``print"fuer"x"paesse"``
        yield ShowMessage("locations.ble.passport_for_many", {"count": count})
    yield ShowMessage("locations.ble.passport_price", {"price": price})  # :22013
    if not (yield Confirm("locations.ble.confirm")):  # :22013 ``ifx$="n"thenreturn``
        return []

    if active.ka < price:  # :22014 ``ifka(sp)<pgoto1125``
        yield ShowMessage("system.not_enough_money")
        return []

    yield ShowMessage("locations.ble.passport_done")  # :22015
    # :22020 ``ka(sp)=ka(sp)-p:ag(sp)=ag(sp)or1:x=1:gosub1160``
    ctx.apply(MoneyChange(-price))
    ctx.apply(MarkSet(fake_papers=True))
    ctx.apply(score_and_rank(params["ble_passport_score"], params))
    return []


@register("ble.counterfeit")
def ble_counterfeit(ctx):
    """Buy counterfeit money — ports ``mf-prg.bas:22100-22120``."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params

    yield ShowMessage("locations.ble.counterfeit_reluctant")  # :22100
    # :22105-22106 — see the module docstring for the prompt's bounds.
    ceiling = max(min(params["ble_counterfeit_max"], active.ka), 0)
    q = yield PromptInt("locations.ble.counterfeit_prompt", min=_ANY_NEGATIVE, max=ceiling)
    if q <= 0:  # :22105 ``ifq<=0thenreturn``
        return []

    # :22110 ``p=int(rnd(1)*q/2)+q+100``. ``int(rnd(1)*q/2)`` is drawn as
    # ``range(q)//2``: ``int(int(r*q)/2) == int(r*q/2)`` for every r, so each value and
    # its share (the last one half as likely when q is odd) are the source's.
    p = ctx.rng.range(q) // params["ble_counterfeit_spread"] + q + params["ble_counterfeit_bonus"]
    yield ShowMessage("locations.ble.counterfeit_offer", {"amount": p})
    if not (yield Confirm("locations.ble.confirm")):  # :22115 ``ifx$="n"thenreturn``
        return []

    # :22120 ``ka(sp)=ka(sp)-q+p:ag(sp)=ag(sp)or2:x=1:gosub1160``
    ctx.apply(MoneyChange(p - q))
    ctx.apply(MarkSet(counterfeit=True))
    ctx.apply(score_and_rank(params["ble_counterfeit_score"], params))
    return []
