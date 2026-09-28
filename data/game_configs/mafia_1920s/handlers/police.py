"""Police capture and trial — ports ``mf-prg.bas:26000-26080``.

One module handles everything from arrest to sentence. It registers no handler: the
locations, the roadblock and the win flows call it from their own handlers, at the
source's three entries, each with ``yield from`` (every source entry is a ``goto``, so
the calling handler ends when capture returns):

* :func:`police_fight` — ``:26000``, the police fight first (sgl ``:17009``/``:17020``,
  ban ``:20142``); lost, it falls into the arrest.
* :func:`caught` — ``:26020``, the arrest menu: bribe, flee, surrender.
* :func:`sentence` — ``:26045``, the trial: lawyer and verdict (pol's surrender,
  ``:21005``).

The entry record (:class:`Arrest`)
---------------------------------
``ctx.state`` does not show a handler's own buffered effects, so the caller hands over
what capture reads that it may have changed: the cash and the gang (their size). And
the source's ``p`` at the entry: the chief-bribe auto-pay (``:26021`` to ``:26037``)
charges whatever ``p`` last held (house rule ``stale_bribe_price``), which is the map
step's ``52224+cell`` after a roadblock, or what a fight left.

Outcomes (every entry returns one of them)
------------------------------------------
* :data:`FOUGHT_OFF` — the police fight won: +2 score (``:26015``), the turn goes on.
* :data:`BRIBED` — the bribe paid and taken (``:26039``): the turn goes on.
* :data:`ESCAPED` — the flight made good (``:26041``): +2 score, the turn goes on.
* :data:`ACQUITTED` — the lawyer cut the sentence to 0 (``:26070``): ``ms=0``, the job
  kept, no -10, no move.
* :data:`SENTENCED` — ``:26080``: ``ms=0``, the job lost, -10 score, the jail months
  set, the player moved to cell 911.

Every path into the trial clears the tip and scores +2 first (``:26045``). The turn
ends through ``ms=0``: the runner's door charge (``:2060``) then takes the points
below 0, and the next screen is the next player's. The movement cost of a turn that
goes on is the caller's (a location's door charge, or the runner's after a map step).

The prompts inside capture are not cancellable. Capture changes nothing else: no
gang, no weapons, no marks, no debt, no alcohol (the roadblock takes that before it
calls in).

Two things the port does differently (the catalogue's header lists them):

* after a lost police fight the auto-pay charges 0, since the port's fight does not
  report the ``p`` it leaves behind (a combat cell, 0..520, or a step);
* the lawyer's fee is read as a whole number. The C64 ``INPUT`` at ``:26060`` takes a
  fraction and reads text as 0; here both are asked again. An empty answer keeps
  ``x$`` on the C64 (checked in VICE): at the first ask that is the "j" of the lawyer
  question, so ``val`` gives 0 and there is no lawyer; after a refused fee it is the
  refused fee, asked again. The port does the same.

Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield`` and
``ctx.apply``, and this config's own helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.effects import MoneyChange, SetMovementPoints, Teleport
from engine.interactions import Confirm, PromptChoice, PromptInt, ShowMessage

from ..effects import Jail, JobClear, TipClear
from ..house_rules import intent
from ..setup import load_encounter, load_vehicles, run_encounter, score_and_rank
from ..state import wanted

__all__ = [
    "ACQUITTED",
    "BRIBED",
    "ESCAPED",
    "FOUGHT_OFF",
    "SENTENCED",
    "Arrest",
    "caught",
    "police_fight",
    "sentence",
]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: The police squad (``:26000-26010``); its size, weapon and energy are rolled per fight.
_POLICE = load_encounter(_CONFIG_DIR / "content" / "encounters" / "police_fight.yaml")

#: House rule: the chief-bribe auto-pay charges the stale ``p`` (faithful) or the bribe.
STALE_BRIBE_PRICE = "stale_bribe_price"
#: House rule: the flight odds read the seat's vehicle range (faithful) or the vehicle's.
FLIGHT_ODDS_BY_SEAT = "flight_odds_by_seat"

FOUGHT_OFF = "fought_off"
BRIBED = "bribed"
ESCAPED = "escaped"
ACQUITTED = "acquitted"
SENTENCED = "sentenced"

#: The capture menu (``:26022-26023``), in the source's key order 1..3.
_MENU = ("police.menu_bribe", "police.menu_flee", "police.menu_surrender")
_BRIBE, _FLEE, _SURRENDER = range(3)

#: The lawyer prompt's first ask takes any whole number: the handler asks again for
#: one the source refuses (``:26061``), so the empty answer's 0 is not range-checked.
_ANY = 2**31


@dataclass(frozen=True)
class Arrest:
    """What the caller hands capture: the values it may have changed, and ``p``.

    ``p`` is the value the source's ``p`` holds at the entry (the map step's
    ``52224+cell`` after a roadblock, ``:2030``); only :func:`caught` reads it, for the
    chief-bribe auto-pay (``:26021``). ``cash`` is the player's cash as the caller left
    it, and ``gang_size`` the gang's size (the boss is gangster 1); ``None`` means the
    caller changed neither, and ``ctx.state``'s value holds.
    """

    p: int | None = None
    cash: int | None = None
    gang_size: int | None = None


class _Run:
    """One capture's running values: the cash as it stands, and the parameters."""

    def __init__(self, ctx, arrest: Arrest) -> None:
        self.player = ctx.state.players[ctx.state.clock.active_player]
        self.cash = self.player.ka if arrest.cash is None else arrest.cash
        self.params = ctx.state.config.formula_params

    def score(self, ctx, key: str) -> None:
        """``x=<n>:gosub1160``."""
        ctx.apply(score_and_rank(self.params[key], self.params))

    def pay(self, ctx, amount: int) -> None:
        ctx.apply(MoneyChange(-amount))
        self.cash -= amount


def police_fight(ctx, arrest: Arrest, *, grid: str | None = None):
    """The police fight, then the arrest if it is lost — ``:26000-26015``.

    ``grid`` is the caller's ``kf$`` (``:17009``/``:17020`` "ks", ``:20142`` "kb"); the
    encounter's backdrop when it is ``None``.
    """
    run = _Run(ctx, arrest)
    params = run.params
    rank = run.player.rank
    # :26000 ``gz(0)=5+int(rnd(1)*ra(sp)/2)``. ``int(rnd(1)*ra/2)`` is drawn as
    # ``range(ra)//2``: ``int(int(r*ra)/2) == int(r*ra/2)``, so each count and its share
    # (the top one half as likely for an odd rank) are the source's.
    count = _POLICE.variants[0].count + ctx.rng.range(rank) // params["police_count_rank_divisor"]
    # :26010 ``w=5-2*(ra(sp)>5)``: the revolver, and the gewehr above rank 5.
    weapon = (
        params["police_weapon_senior"]
        if rank > params["police_weapon_senior_rank"]
        else _POLICE.variants[0].weapon
    )
    # :26010 ``e=20+2*(ra(sp)-1)-int(rnd(1)*21)``: one energy for the whole squad.
    vitality = (
        _POLICE.variants[0].vitality
        + params["police_energy_per_rank"] * (rank - 1)
        - ctx.rng.range(params["police_energy_spread"])
    )
    roster = None if arrest.gang_size is None else run.player.roster[: arrest.gang_size]
    # :26010 gosub5000 -> :30000 the fight -> :30500 the outcome screen.
    result = yield from run_encounter(
        ctx, _POLICE, count=count, weapon=weapon, vitality=vitality, grid=grid, roster=roster
    )
    if result.winner == 1:
        # :26015 ``ifs=1thenx=2:gosub1160:return``
        run.score(ctx, "police_score_free")
        return FOUGHT_OFF
    # Lost: on into :26020. The ``p`` the fight left is not reported; see the module
    # docstring.
    return (yield from caught(ctx, Arrest(p=0, cash=run.cash, gang_size=arrest.gang_size)))


def caught(ctx, arrest: Arrest):
    """The arrest menu: bribe, flee or surrender — ``:26020-26043``."""
    if arrest.p is None:
        raise ValueError("caught() needs the entry's p for the chief-bribe auto-pay (:26021)")
    run = _Run(ctx, arrest)
    params = run.params
    rank = run.player.rank
    bribe = params["police_bribe_base"] + params["police_bribe_per_rank"] * rank  # :26035

    yield ShowMessage("police.caught")  # :26020
    # :26021 ``ifpl(sp)andint(rnd(1)*2)<>0goto26037``: the roll is drawn every time.
    skip_roll = ctx.rng.range(params["police_menu_skip_roll"])
    if wanted(run.player).bribe_months != 0 and skip_roll != 0:
        price = bribe if intent(ctx.state, STALE_BRIBE_PRICE) else arrest.p
        return (yield from _pay(ctx, run, price))

    # :26025 ``getx$:ifx$<"1"orx$>"3"goto26025``: any other key is ignored (the
    # driver asks again).
    choice = yield PromptChoice("police.menu", options=list(_MENU))
    if choice == _BRIBE:
        # :26035-26036 the price, then ok (j/n)?
        yield ShowMessage("police.bribe_demand", {"price": bribe})
        if not (yield Confirm("police.confirm")):
            # :26036 ``ifx$="n"thenx=2:gosub1160:goto26045``
            run.score(ctx, "police_score_free")
            return (yield from _trial(ctx, run))
        return (yield from _pay(ctx, run, bribe))
    if choice == _FLEE:
        return (yield from _flee(ctx, run))
    return (yield from _trial(ctx, run))  # :26030 key 3 -> :26045


def sentence(ctx, arrest: Arrest):
    """The trial: lawyer and verdict — ``:26045-26080``."""
    return (yield from _trial(ctx, _Run(ctx, arrest)))


def _pay(ctx, run: _Run, price: int):
    """``:26037-26039``: pay the police, who let the player go four times in five."""
    if run.cash < price:
        # :26037 ``ifka(sp)<pthengosub1125:goto26045``
        yield ShowMessage("system.not_enough_money")
        return (yield from _trial(ctx, run))
    # :26038 ``ka(sp)=ka(sp)-p:ifint(rnd(1)*5)=0goto26045``
    run.pay(ctx, price)
    if ctx.rng.range(run.params["police_bribe_fail_roll"]) == 0:
        return (yield from _trial(ctx, run))
    yield ShowMessage("police.let_go")  # :26039
    return BRIBED


def _flee(ctx, run: _Run):
    """``:26040-26043``: the flight."""
    vehicles = load_vehicles(_CONFIG_DIR / "entities" / "vehicles.yaml")
    if intent(ctx.state, FLIGHT_ODDS_BY_SEAT):
        reach = vehicles[run.player.vehicle]["tr"]  # :1012 ``ms=tr(tm(sp))``
    else:
        # tr(sp): the range table at the player's seat (sp is 1-based).
        reach = vehicles[ctx.state.clock.active_player + 1]["tr"]
    # :26040 ``ifint(rnd(1)*tr(sp)/11)=0``: 0 exactly when ``int(rnd(1)*tr) < 11``.
    if ctx.rng.range(reach) < run.params["police_flight_divisor"]:
        # :26040 ``x=-5:gosub1160``, :26042-26043, then on into :26045.
        run.score(ctx, "police_score_caught")
        yield ShowMessage("police.flight_failed")
        return (yield from _trial(ctx, run))
    # :26041 the escape, ``x=2:gosub1160``
    yield ShowMessage("police.escaped")
    run.score(ctx, "police_score_free")
    return ESCAPED


def _trial(ctx, run: _Run):
    """``:26045-26080``: the trial, the lawyer and the verdict."""
    params = run.params
    rank = run.player.rank
    # :26045 ``tp(sp)=0:x=2:gosub1160:gs(sp)=int(ra(sp)/2+.5)``
    ctx.apply(TipClear())
    run.score(ctx, "police_score_free")
    months = int(rank / 2 + 0.5)

    yield ShowMessage("police.trial")  # :26050
    fee = 0
    if rank >= params["police_lawyer_rank"]:  # :26050 ``ifra(sp)<5goto26075``
        # :26055 ``ifx$="n"goto26075``
        if (yield Confirm("police.lawyer_offer")):
            fee = yield from _lawyer_fee(run)

    if fee != 0:
        # :26062 ``ka(sp)=ka(sp)-x:y=int(rnd(1)*(x/1000+1))+1``. The draw is
        # ``range(x+1000)//1000``: ``int(int(r*(x+1000))/1000) == int(r*(x+1000)/1000)``.
        run.pay(ctx, fee)
        step = params["police_lawyer_step"]
        cut = ctx.rng.range(fee + step) // step + 1
        months = max(months - cut, 0)  # :26065 ``gs(sp)=gs(sp)-y:ifgs(sp)<0thengs(sp)=0``
        if months == 0:
            # :26070 ``ifgs(sp)=0thenprint"{down}du wirst freigesprochen!":ms=0:goto1100``
            ctx.apply(Jail(months=0))
            ctx.apply(SetMovementPoints(0))
            yield ShowMessage("police.acquitted")
            return ACQUITTED
        yield ShowMessage("police.lawyer_result", {"months": months})  # :26071-26072
    else:
        yield ShowMessage("police.sentenced", {"months": months})  # :26075

    # :26080 ``ms=0:jo(sp)=0:x=-10:gosub1160:po(sp)=911``
    ctx.apply(Jail(months=months))
    ctx.apply(SetMovementPoints(0))
    ctx.apply(JobClear())
    run.score(ctx, "police_score_sentenced")
    ctx.apply(Teleport(params["police_jail_cell"]))
    return SENTENCED


def _lawyer_fee(run: _Run):
    """``:26060-26061``: the fee, asked again above the cash or below 0; 0 is none.

    The first ask reads an empty answer as 0 (``x$`` still holds the "j" the lawyer
    question took, and its value is 0); an ask after a refusal reads it as the refused
    fee again, so the driver asks again.
    """
    fee = yield PromptInt("police.lawyer_prompt", min=-_ANY, max=_ANY, blank=0)
    # :26060 ``ifx=0goto26075`` / :26061 ``ifx>ka(sp)orx<0then``
    while fee != 0 and (fee > run.cash or fee < 0):
        fee = yield PromptInt("police.lawyer_prompt", min=0, max=max(run.cash, 0))
    return fee
