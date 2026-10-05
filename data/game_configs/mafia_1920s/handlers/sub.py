"""The sub (Subway-Station, U-Bahn) handlers — ports ``mf-prg.bas:18000-18052``.

``:18010 onwgoto18035,18015``: two playable options behind a guardless shell
(``content/locations/sub.yaml``); every refusal happens here, after the pick. ``w`` is
the menu option the player picked (``:3040``), and nothing here reads the tile ``ln``.

- ``sub.platform`` (``w=1``, ``:18035``) — pick pockets on the platform.
- ``sub.train`` (``w=2``, ``:18015-18030``) — the ticket first: its price and ok
  (j/n)? (``:18015-18020``), then the cash check (``:18025``), then the 50 $ are paid
  (``:18030``) and the train falls into the platform's body with ``w`` still 2.

The body (``:18035-18052``), :func:`pickpocket`
-----------------------------------------------
1. ``:18035`` the thief is picked; ``y=0`` returns. On the train the ticket is already
   paid and stays paid, so that picker is not cancellable (house rule
   ``subway_ticket_lost_on_cancel``).
2. ``:18039 x=1:gosub1160``: the score is paid before anything is known, so a caught
   thief keeps it (house rule ``pickpocket_scores_even_when_caught``).
3. ``:18040 ifint(rnd(1)*15)=10goto18052``: one time in 15 the loot is a safecracker's
   manual, ``s9(sp)=5`` (set, so a second manual does not add to the first). No catch
   roll is drawn.
4. ``:18041 ifint(rnd(1)*(in/10))goto18045``: a roll of 0 means the thief is caught.
   ``int(rnd(1)*(in/10))`` is 0 exactly when ``rnd(1)*in < 10``, that is when
   ``int(rnd(1)*in) < 10``, so the port draws ``range(in)`` and catches below 10: exact
   for a whole-number intelligence. A thief of intelligence 10 or less is always
   caught.
5. Caught, ``:18042`` goes to ``:26020``: the arrest, no fight. Capture is a ``goto``, so
   the handler ends there. The ``p`` it gets is the one the source holds: the map
   step's ``:2030`` ``p=br+po(sp)+x``, 52224 plus the door cell, since nothing between
   the door entry and here sets it.
6. ``:18045 onint(rnd(1)*4)-(w=2)-(la<>9)goto18047,18048,18049,18050,18051``: C64 true
   is -1, so the loot index is ``int(rnd(1)*4)`` plus 1 on the train and plus 1 away
   from the railway station (``la=9``, whose pickpocketing ``:19050`` jumps into this
   body with ``w=1``). Index 0 falls through to ``:18046``. In the subway the platform
   gives items 1-4 and the train 2-5.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

from engine.effects import MoneyChange
from engine.interactions import Confirm, ShowMessage
from engine.locations import register

from ..effects import SafeSkillSet
from ..setup import KEY_WAIT, pick_gangster, score_and_rank
from .police import Arrest, caught

__all__ = ["door_cells", "pickpocket", "sub_platform", "sub_train"]

_CONFIG_DIR = Path(__file__).resolve().parents[1]


@cache
def door_cells() -> dict[tuple[int, int], int]:
    """The city's door table (``content/map/city.yaml``), keyed by ``(la, ln)``: the
    cell a step onto a location tile reached, for the stale ``p`` capture reads (``:2030``
    ``p=br+po(sp)+x``). Shared with the bank's safe-crack (``:20142``)."""
    raw = yaml.safe_load((_CONFIG_DIR / "content" / "map" / "city.yaml").read_text("utf-8"))
    return {(door["la"], door["ln"]): door["cell"] for door in raw["doors"]}


@register("sub.platform")
def sub_platform(ctx):
    """Pick pockets on the platform — ports ``mf-prg.bas:18010`` ``onwgoto18035``."""
    return (yield from pickpocket(ctx, w=1))


@register("sub.train")
def sub_train(ctx):
    """Pick pockets on the train — ports ``mf-prg.bas:18015-18030``, then ``:18035``."""
    active = ctx.state.players[ctx.state.clock.active_player]
    price = ctx.state.config.formula_params["sub_ticket_price"]

    # :18015-18020 ``print"ein u-bahn-ticket kostet dich"`` / ``print"{down}50 $. ";``
    # ``:gosub1110:ifx$="n"thenreturn``
    yield ShowMessage("locations.sub.ticket", {"price": price})
    if not (yield Confirm("locations.sub.confirm")):
        return []  # no key wait
    if active.ka < price:  # :18025 ``ifka(sp)<50goto1125``
        yield ShowMessage("system.not_enough_money")
        yield KEY_WAIT  # :1125 ...:goto1100
        return []
    ctx.apply(MoneyChange(-price))  # :18030 ``ka(sp)=ka(sp)-50``
    return (yield from pickpocket(ctx, w=2, paid=price))


def pickpocket(ctx, *, w: int, paid: int = 0):
    """The pickpocketing body, ``mf-prg.bas:18035-18052`` — see the module docstring.

    ``w`` is the source's ``w`` (the menu option, 2 on the train); ``paid`` is the cash
    the caller has already buffered out (the ticket), which also makes the picker not
    cancellable. ``la`` is the player's entry context (``last_la``).
    """
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params

    # :18035 ``welchen spieler setzt du als dieb ein:":gosub1130:ify=0thenreturn``
    yield ShowMessage("locations.sub.thief_prompt")
    y = yield from pick_gangster(ctx, cancellable=paid == 0)
    if y is None:
        return []

    # :18039 ``x=1:gosub1160:print"{clr}{down}du stiehlst..."``
    ctx.apply(score_and_rank(params["sub_score"], params))
    yield ShowMessage("locations.sub.stealing")

    # :18040 ``ifint(rnd(1)*15)=10goto18052``; :18052 ``s9(sp)=5``
    if ctx.rng.range(params["sub_manual_roll"]) == params["sub_manual_hit"]:
        yield ShowMessage("locations.sub.loot_manual")
        ctx.apply(SafeSkillSet(params["sub_manual_tries"]))
        yield KEY_WAIT  # :18052 ...:s9(sp)=5:goto1100
        return []

    # :18041 ``ifint(rnd(1)*(in/10))goto18045`` — a roll below 10 of ``range(in)`` is 0.
    intelligenz = active.roster[y].attrs["intelligenz"]
    if ctx.rng.range(max(intelligenz, 1)) < params["sub_catch_divisor"]:
        # :18042 ``print"{down}...nichts! denn du wirst erwischt!":gosub1100:goto26020``
        yield ShowMessage("locations.sub.caught")
        yield KEY_WAIT  # :18042 ...:gosub1100, before the capture
        door = door_cells()[(active.last_la, active.last_location)]
        yield from caught(ctx, Arrest(p=params["map_screen_base"] + door, cash=active.ka - paid))
        return []

    # :18045 ``onint(rnd(1)*4)-(w=2)-(la<>9)goto18047,...,18051`` (C64 true is -1).
    index = (
        ctx.rng.range(params["sub_loot_roll"])
        + (1 if w == params["sub_loot_option"] else 0)
        + (1 if active.last_la != params["sub_loot_station_la"] else 0)
    )
    item, cash = params["sub_loot"][index]  # :18046-18051
    yield ShowMessage(f"locations.sub.loot_{item}")
    if cash:
        ctx.apply(MoneyChange(cash))
    yield KEY_WAIT  # :18046-18051 ...:goto1100
    return []
