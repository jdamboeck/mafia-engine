"""The pub (Kneipe) handlers — U8 (alcohol trade + tips) and U9 (recruit, later unit).

Ports three of the pub's four menu actions from ``mf-prg.bas:12000-12335``:

- ``pub.drink`` (``12010-12075``) — alcohol trade. Only pub tile ``ln=4`` serves; the
  ``ln=5`` branch (``:12010``'s ``ifln=4orln=5goto12020``) is confirmed DEAD CODE this
  slice (KTD-9: do not port). Tracing the source: ``ln=5`` is set ONLY by the ``bhf``
  (train station) handler's option 1 ("visit the station pub", ``mf-prg.bas:19010``:
  ``ln=5:la=2:goto3000``) — ``bhf`` is not one of this slice's implemented locations
  (kdh is the combat trigger; ``docs/plans/.../2026-07-18-002-...`` Key Decisions), so
  ``ln=5`` is unreachable via any in-slice map entry. Every other pub tile (1/2/3, and
  any future ``ln`` this slice doesn't reach) falls into the 50%-refusal-or-sell-offer
  branch (``:12015``).
- ``pub.tip`` (``12200-12252``) — buy a heist rumour. Rank-gated; 2/3 chance the
  informer has nothing; the roll picks one of 5 flavour texts (tip type 1-5, stored on
  the player); tip 4 alone has a follow-on 5000$ stake that a later upkeep slot (U8's
  ``upkeep.py`` fill-in) resolves.

``pub.recruit`` stays the U9 stub below (never entered this slice: the shell's
``rank>4 and gang_size<10`` guard denies it at rank 1) until U9 lands its body.

Faithfulness notes
-------------------
- ALL game-balance numbers (stock/price/capacity/tip-price ranges) come from
  ``formula_params`` (KTD-10) — nothing here is a bare literal.
- Every relational term uses the ``true = +1`` porting convention (KTD-9); none of this
  unit's ported expressions contain a relational factor that needed one (spot-checked
  against the oracle per the plan's landmine note — no conflict found).
- KTD-7: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``, ``ctx.apply``,
  and this config's OWN ``..setup`` helpers.
- No content-specific events (KTD-6): outcomes are reconstructable from the committed
  effects + logged RNG draws.

Cancellability rule (KTD-1 feasibility)
----------------------------------------
Neither ``pub.drink`` nor ``pub.tip`` uses a driver-level ``cancellable=True`` prompt:
every "decline"/"nothing to buy" path in the source is a plain ``return`` back to the
main loop (``goto1100``/``return``), which this port expresses as a quiet Python
``return []`` after the relevant ``PromptInt``/``Confirm`` answer — there is no
"cancel back to an earlier menu" shape here (unlike waf's nested weapon/gangster
pickers), so no interaction needs the driver's atomic-discard cancel path.

``ln`` seam
-----------
Read from the active player's ``last_location`` field, exactly as ``slw``/``waf`` do.
"""

from __future__ import annotations

from engine.effects import BarrelChange, MoneyChange, TipClear, TipSet
from engine.interactions import Confirm, PromptInt, ShowMessage
from engine.locations import register

from ..setup import load_vehicles, score_and_rank

__all__ = ["pub_drink", "pub_recruit", "pub_tip"]

#: Alcohol tile: only this ``ln`` serves (mf-prg.bas:12010; ln=5 is dead code, see
#: module docstring). Every other pub tile falls into the 50% refusal-or-sell branch.
_ALCOHOL_TILE = 4

#: The five heist-tip flavour text keys, 1-based to match ``tp(sp)`` (mf-prg.bas:12226
#: ``ontp(sp)goto12230,12235,12240,12245,12250``).
_TIP_TEXT_KEYS = {
    1: "locations.pub.tip_postzug",
    2: "locations.pub.tip_bank",
    3: "locations.pub.tip_geldtransport",
    4: "locations.pub.tip_waffenschmuggel",
    5: "locations.pub.tip_buergermeister",
}

#: The arms-deal tip id (mf-prg.bas:12245-12249, :4060 ``iftp(sp)=4``) — the only tip
#: with a follow-on stake. Shared with ``handlers/upkeep.py``'s arms-deal slot.
ARMS_DEAL_TIP = 4
ARMS_DEAL_STAKE = 5000


def _vehicles():
    """Load this config's vehicle table via the config's own loader (KTD-7).

    Mirrors ``waf.py``'s ``_weapons()`` pattern: read relative to this module's config
    directory, fresh per call (the config is frozen per game, so this is harmless).
    """
    from pathlib import Path

    cfg_dir = Path(__file__).resolve().parents[1]
    return load_vehicles(cfg_dir / "entities" / "vehicles.yaml")


# --------------------------------------------------------------------------- #
# pub.drink (R5) — mf-prg.bas:12010-12075                                      #
# --------------------------------------------------------------------------- #
@register("pub.drink")
def pub_drink(ctx):
    """Order a drink — alcohol trade, ports ``mf-prg.bas:12010-12075``.

    Steps (faithful to the BASIC line block):

    1. ``:12010`` — only ``ln == _ALCOHOL_TILE`` serves; every other tile falls to (2).
       BUY path (tile 4): ``:12020-12035``.
       a. Roll stock ``x`` (100-299 barrels) and price ``p`` (5-9$/barrel).
       b. Cap the offer by the vehicle's free capacity: ``x = min(x, tank - barrels)``.
       c. Prompt a quantity in ``[0, x]``; 0 is a quiet abort (``:12029``).
       d. Afford check ``ka < y*p`` -> ``not_enough_money``, no state change (``:12030``).
       e. Settle: ``+y`` barrels, ``-y*p`` cash, score+rank reward ``x=2`` (``:12035``).
    2. ``:12015`` — elsewhere: 1-in-2 refusal ("verboten!"); else the SELL path.
       SELL path (``:12050-12075``):
       a. Roll the dealer's buy price ``x`` (10-29$/barrel).
       b. Prompt a quantity in ``[0, current barrels]``; 0 is a quiet abort (``:12065``).
       c. Settle unconditionally (selling never fails on affordability): ``+y*x`` cash,
          ``-y`` barrels. NO score effect (``:12075`` has no ``gosub1160``, unlike buy).
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location  # ln seam (see module docstring)
    params = ctx.state.config.formula_params

    if ln == _ALCOHOL_TILE:
        # --- BUY path: :12020-12035 ---------------------------------------
        stock = ctx.rng.hit(params["pub_alcohol_stock_min"], params["pub_alcohol_stock_max"])
        price = ctx.rng.hit(params["pub_alcohol_buy_price_min"], params["pub_alcohol_buy_price_max"])

        # :12025 — cap the offer by the vehicle's free barrel capacity.
        vehicles = _vehicles()
        capacity = vehicles[active.vehicle]["tank"]
        free = capacity - active.contraband.alcohol_barrels
        if free < stock:
            stock = free

        yield ShowMessage("locations.pub.drink_offer", {"stock": stock, "price": price})
        y = yield PromptInt("locations.pub.drink_quantity_prompt", min=0, max=max(stock, 0))
        if y == 0:
            return []

        # :12030 — afford check; broke -> no state change at all.
        if active.ka < y * price:
            yield ShowMessage("system.not_enough_money")
            return []

        # :12035 — settle + score/rank reward (x=2, gosub1160/1165).
        ctx.apply(BarrelChange(y))
        ctx.apply(MoneyChange(-price * y))
        ctx.apply(score_and_rank(2, params))
        return []

    # --- elsewhere: :12015 1-in-2 refusal, else the SELL offer -------------
    if ctx.rng.range(2) == 0:
        yield ShowMessage("locations.pub.drink_refused")
        return []

    # --- SELL path: :12050-12075 --------------------------------------------
    price = ctx.rng.hit(params["pub_alcohol_sell_price_min"], params["pub_alcohol_sell_price_max"])
    yield ShowMessage("locations.pub.sell_offer", {"price": price})
    y = yield PromptInt(
        "locations.pub.sell_quantity_prompt", min=0, max=max(active.contraband.alcohol_barrels, 0)
    )
    if y == 0:
        return []

    # :12075 — settle unconditionally, no score effect.
    ctx.apply(MoneyChange(y * price))
    ctx.apply(BarrelChange(-y))
    return []


# --------------------------------------------------------------------------- #
# pub.tip (R5) — mf-prg.bas:12200-12252                                        #
# --------------------------------------------------------------------------- #
@register("pub.tip")
def pub_tip(ctx):
    """Ask for a heist tip — ports ``mf-prg.bas:12200-12252``.

    Steps (faithful to the BASIC line block):

    1. ``:12200`` — rank guard ``ra(sp) > 3`` (rank >= 4); below it, refusal.
    2. ``:12210`` — 2-in-3 the informer has nothing; else continue.
    3. ``:12215-12216`` — roll the price (1000/1500/2000$ uniform), confirm; "no"
       returns with NO charge and NO tip set.
    4. ``:12220`` — afford check; broke -> ``not_enough_money``, no state change
       (mirrors ``pub.drink``'s buy path: the check runs BEFORE any write).
    5. ``:12225-12226`` — charge the price, roll the tip id 1-5 uniform, ``TipSet``,
       then show the matching flavour text.
    6. Tip 4 ONLY (``:12245-12249``) — an extra 5000$ stake offer:
       - decline ("n") -> ``TipClear`` (the tip is void), return.
       - broke (``ka < 5000``) -> ``TipClear``, ``not_enough_money``, return.
       - accept -> deduct 5000$, show the confirmation flourish. The tip STAYS set
         (``:4060``'s upkeep slot resolves it next turn start via ``tp(sp)=4``).
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    params = ctx.state.config.formula_params

    # :12200 — rank guard.
    if active.rank <= 3:
        yield ShowMessage("locations.pub.tip_too_inexperienced")
        return []

    # :12210 — 2/3 chance of nothing.
    if ctx.rng.range(3) != 0:
        yield ShowMessage("locations.pub.tip_nothing")
        return []

    # :12215-12216 — price roll + confirm.
    price = params["pub_tip_price_base"] + ctx.rng.range(3) * params["pub_tip_price_step"]
    yield ShowMessage("locations.pub.tip_teaser", {"price": price})
    if not (yield Confirm("locations.pub.tip_confirm")):
        return []

    # :12220 — afford check, runs BEFORE any write.
    if active.ka < price:
        yield ShowMessage("system.not_enough_money")
        return []

    # :12225-12226 — charge, roll the tip id, set it, show the flavour text.
    ctx.apply(MoneyChange(-price))
    tip_id = ctx.rng.range(5) + 1
    ctx.apply(TipSet(tip_type=tip_id))
    yield ShowMessage(_TIP_TEXT_KEYS[tip_id])

    if tip_id != ARMS_DEAL_TIP:
        return []

    # :12245-12249 — tip 4's extra 5000$ stake sub-flow.
    yield ShowMessage("locations.pub.arms_deal_offer")
    if not (yield Confirm("locations.pub.arms_deal_confirm")):
        ctx.apply(TipClear())
        return []
    if active.ka - price < ARMS_DEAL_STAKE:
        ctx.apply(TipClear())
        yield ShowMessage("system.not_enough_money")
        return []
    ctx.apply(MoneyChange(-ARMS_DEAL_STAKE))
    yield ShowMessage("locations.pub.arms_deal_accepted")
    return []


# --------------------------------------------------------------------------- #
# pub.recruit — STUB only (U9). Unreachable this slice: the shell's           #
# rank>4 and gang_size<10 guard denies it at rank 1 (KTD-8).                  #
# --------------------------------------------------------------------------- #
@register("pub.recruit")
def pub_recruit(ctx):
    """Pub recruit — STUB (body is a later unit; ports mf-prg.bas:12100-12175).

    Never reached this slice: at rank 1 the shell guard ``ra(sp) > 4`` denies the
    option, so the handler is not entered. If a future caller reaches it before the
    real body lands, fail loudly rather than silently no-op.
    """
    raise NotImplementedError(
        "pub.recruit body is a later unit (mf-prg.bas:12100-12175); at rank 1 the "
        "shell guard ra>4 denies this option, so it is never entered this slice."
    )
    yield  # pragma: no cover — marks this a generator (handler protocol) though unreached
