"""The pol (Polizei-Praesidium) handlers — ports ``mf-prg.bas:21000-21255``.

``:21005 onwgoto26045,21010,21100``: three playable options behind a guardless shell
(``content/locations/pol.yaml``); every refusal happens here, after the pick.

- ``pol.surrender`` (``:21005`` -> ``:26045``) — the trial, straight away: no arrest
  menu and no wanted check, so a player no one is looking for may give himself up.
  The capture module's :func:`~.police.sentence` runs it.
- ``pol.bribe`` (``:21010-21030``) — buy months from the police chief at 1000 $ each.
  ``pl(sp)`` grows by ``x+1`` (the ``+1`` is taken by the next upkeep's ``:4050``), the
  player scores 2 and leaves by the back exit, cell 911, with the turn going on. What
  the months buy is the arrest's auto-pay (``:26021``, handlers/police.py).
- ``pol.free`` (``:21100-21255``) — bribe the guards (3000-5000 $) to free a jailed
  player, or the phantom inmate the list sometimes adds (``:21105``). The break never
  fails once paid. A freed player answers a thank-you payment prompt addressed to them,
  and keeps cell 911; a freed phantom joins the gang as "knasti", with no score.

The month prompt (``:21011``)
-----------------------------
``input"{down}nehme ich 1000 $. wieviele monate:";x:p=1000*x:ifx=0thenreturn`` has no
range check. Two answers are house rules (both checked in VICE):

- a negative count (``chief_bribe_negative_months``): C64 ``INPUT`` takes "-3" as -3,
  the price is below 0 and ``:21015`` never refuses it, so the chief pays the player
  and the months drop. Intent asks again;
- an empty answer (``chief_bribe_empty_answer``) keeps ``x``, which last held the map
  step into the door (``:2015-2018``): the door cell minus the player's cell, since a
  door step leaves the player where he stood. Intent leaves quietly, as 0 does.

A fraction ("1.5") is a count on the C64 (1500 $ and 2.5 months); the port's prompt
reads whole numbers only and asks again, as ble's and the lawyer's do.

The inmate list (``:21115-21125``)
---------------------------------
The list is numbered 1..x as the source prints it, with its heading (``:21115``) as the
pick's prompt under it rather than above it. The pick is one key (``getx$``):
a key above x is read again; the key "0", or any key that is not a digit (``val``
gives 0), leaves with no pause. The port reads a line: "0" and an empty line (the
RETURN key) leave; any other text is asked again.

The thank-you prompt (``:21250-21253``)
--------------------------------------
The prompt is the freed player's (``player=``, so the client names them before it).
``inputx$:y=val(x$)``: above the freed player's cash the whole screen is drawn again
(``goto21250``); below 0 only the prompt is asked again (``goto21252``). An empty
answer keeps ``x$``: at the first ask that is the "j" of the price confirm, so ``y`` is
0; after a refused answer it is that answer, refused again. Text and fractions, which
``val`` reads as numbers (0 for plain text), are asked again here.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
``ctx.apply`` and this config's own helpers.
"""

from __future__ import annotations

from engine.effects import MoneyChange, RosterAppend, Teleport
from engine.interactions import Confirm, PromptInt, ShowMessage
from engine.locations import register

from ..effects import BribeMonthsChange, Jail
from ..gangster import Gangster
from ..house_rules import intent
from ..setup import score_and_rank
from ..state import wanted
from .police import Arrest, sentence

__all__ = [
    "CHIEF_BRIBE_EMPTY_ANSWER",
    "CHIEF_BRIBE_NEGATIVE_MONTHS",
    "pol_bribe",
    "pol_free",
    "pol_surrender",
]

#: House rule: a negative month count pays out (faithful) or is asked again.
CHIEF_BRIBE_NEGATIVE_MONTHS = "chief_bribe_negative_months"
#: House rule: an empty month answer repeats the map step (faithful) or buys nothing.
CHIEF_BRIBE_EMPTY_ANSWER = "chief_bribe_empty_answer"

#: A prompt bound that lets any whole number through to the handler's own checks.
_ANY = 2**31


@register("pol.surrender")
def pol_surrender(ctx):
    """Give yourself up — ports ``mf-prg.bas:21005`` ``onwgoto26045``."""
    yield from sentence(ctx, Arrest())
    return []


@register("pol.bribe")
def pol_bribe(ctx):
    """Bribe the police chief — ports ``mf-prg.bas:21010-21030``."""
    active = ctx.state.players[ctx.state.clock.active_player]
    params = ctx.state.config.formula_params
    no_negatives = intent(ctx.state, CHIEF_BRIBE_NEGATIVE_MONTHS)

    yield ShowMessage("locations.pol.chief_offer")  # :21010
    # :21011 — see the module docstring for the empty and negative answers.
    if intent(ctx.state, CHIEF_BRIBE_EMPTY_ANSWER):
        blank = 0
    else:
        blank = params["pol_door_cell"] - active.po  # :2015-2018, the step into the door
    prompt = PromptInt(
        "locations.pol.chief_prompt",
        min=0 if no_negatives else -_ANY,
        max=_ANY,
        blank=blank,
    )
    months = yield prompt
    while no_negatives and months < 0:
        months = yield prompt
    price = params["pol_chief_price"] * months  # :21011 ``p=1000*x``
    if months == 0:  # :21011 ``ifx=0thenreturn``
        return []

    if active.ka < price:  # :21015 ``ifka(sp)<p``
        yield ShowMessage("locations.pol.chief_broke")
        return []

    # :21020 ``ka(sp)=ka(sp)-p:pl(sp)=pl(sp)+x+1:x=2:gosub1160``
    ctx.apply(MoneyChange(-price))
    ctx.apply(BribeMonthsChange(months + 1))
    ctx.apply(score_and_rank(params["pol_chief_score"], params))
    # :21025-21030 the back exit: ``po(sp)=911``, and the turn goes on.
    yield ShowMessage("locations.pol.chief_done")
    ctx.apply(Teleport(params["pol_back_exit_cell"]))
    return []


@register("pol.free")
def pol_free(ctx):
    """Free a jailed inmate — ports ``mf-prg.bas:21100-21255``."""
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    params = ctx.state.config.formula_params

    # :21100 ``fori=1tosz:ifgs(i)theng(x)=i``: every jailed player, in seat order.
    inmates: list[int | None] = [
        i for i, player in enumerate(ctx.state.players) if wanted(player).jail_months != 0
    ]
    # :21105 ``ifint(rnd(1)*3)=0andra(sp)>4``: the roll is drawn every time (AND does
    # not short-circuit); a hit adds the phantom inmate, ``None`` here.
    roll = ctx.rng.range(params["pol_phantom_roll"])
    if roll == 0 and active.rank > params["pol_phantom_min_rank"]:
        inmates.append(None)
    if not inmates:  # :21110 ``ifx=1thenprint"es ist niemand inhaftiert."``
        yield ShowMessage("locations.pol.nobody_jailed")
        return []

    # :21116 the list, numbered 1..x. :21115's "wen willst du befreien:" is the pick's
    # own prompt, so a client shows it under the list, next to where the key is read.
    for number, inmate in enumerate(inmates, start=1):
        if inmate is None:
            yield ShowMessage("locations.pol.free_entry_phantom", {"index": number})
        else:
            name = ctx.state.players[inmate].name
            yield ShowMessage("locations.pol.free_entry", {"index": number, "name": name})
    # :21120-21125 ``getx$:ifx$=""orval(x$)>xgoto21120`` / ``ifg=0thenreturn``: a key
    # above x is read again; "0", or RETURN (not a digit), leaves with no pause.
    pick = yield PromptInt("locations.pol.free_prompt", min=0, max=len(inmates), blank=0)
    if pick == 0:
        return []
    inmate = inmates[pick - 1]

    # :21130 ``p=500*int(rnd(1)*5)+3000``, then :21131 ok (j/n)?
    price = params["pol_release_base"] + params["pol_release_step"] * ctx.rng.range(
        params["pol_release_choices"]
    )
    yield ShowMessage("locations.pol.free_price", {"price": price})
    if not (yield Confirm("locations.pol.confirm")):  # :21131 ``ifx$="n"thenreturn``
        return []
    if active.ka < price:  # :21135 ``ifka(sp)<pgoto1125``
        yield ShowMessage("system.not_enough_money")
        return []

    ctx.apply(MoneyChange(-price))  # :21140 ``ka(sp)=ka(sp)-p``
    yield ShowMessage("locations.pol.freed")  # :21200, never a failure

    if inmate is None:
        # :21205 ``ifgz(sp)=10thenprint"{down}er bedankt sich und verschwindet..."``
        if len(active.roster) == params["pol_gang_cap"]:
            yield ShowMessage("locations.pol.phantom_leaves")
            return []
        # :21210 ``gn$(sp,x)="knasti":gw(sp,x)=0:ge$(sp,x)="05100540"`` — no score.
        ctx.apply(RosterAppend(gangster=Gangster(**params["pol_inmate"])))
        return []

    thanks = yield from _thank_you(ctx, inmate, active.name)
    # :21255 ``ka(x)=ka(x)-y:ka(sp)=ka(sp)+y:gs(x)=0:x=2:gosub1160``
    if thanks != 0:
        ctx.apply(MoneyChange(-thanks, player=inmate))
        ctx.apply(MoneyChange(thanks))
    ctx.apply(Jail(months=0, player=inmate))
    ctx.apply(score_and_rank(params["pol_free_score"], params))
    return []


def _thank_you(ctx, freed: int, rescuer: str):
    """``:21250-21253``: the freed player names a thank-you, 0 up to their cash."""
    player = ctx.state.players[freed]
    cash = player.ka
    # The screen is shown to the table; the prompt under it is the freed player's to
    # answer, so it names them and the client announces them right before it.
    screen = ShowMessage(
        "locations.pol.thank_you", {"name": player.name, "rescuer": rescuer, "cash": cash}
    )
    blank: int | None = 0  # the first ask: ``x$`` is the confirm's "j", ``val`` is 0
    while True:
        yield screen  # :21250-21252
        thanks = yield PromptInt(
            "locations.pol.thank_you_prompt", min=-_ANY, max=_ANY, blank=blank, player=freed
        )
        # :21253 ``ify>ka(x)ory<0thenprint"{up}{up}";:goto21252``: the prompt again, no screen.
        while thanks < 0:
            thanks = yield PromptInt(
                "locations.pol.thank_you_prompt", min=-_ANY, max=_ANY, player=freed
            )
        if thanks <= cash:  # :21252 ``ify>ka(x)goto21250``
            return thanks
        blank = None  # ``x$`` now holds the refused answer
