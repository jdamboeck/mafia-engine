"""The kdh (Kredit-Hai / loan shark) handlers — U11, ports ``mf-prg.bas:15000-15321``.

Six menu options, all sharing one shell (no per-option shell guard: every refusal in
the source happens INSIDE the handler body, exactly like ``pub.drink``/``pub.tip``/
``pub.job`` — KTD-8 only applies shell-level guards where the ORIGINAL refuses before
even offering the option, and kdh never does that):

- ``kdh.borrow`` (``15010-15030``) — take a loan. Guard: no existing debt
  (``kr(sp)=0``); amount 0-5000; sets ``kr+=x``, ``ka+=x``, grace ``kz=6``.
- ``kdh.repay`` (``15050-15075``) — repay part or all. Bounds ``0<=x<=kr(sp)``;
  afford check; full repayment (``kr`` reaches 0) additionally resets the grace
  counter ``kz=0`` (:class:`~engine.effects.DebtClear`, applied ALONGSIDE the final
  :class:`~engine.effects.DebtChange` that zeros ``kr`` — not instead of it, since a
  partial repay that happens to land exactly on 0 is the SAME code path as an
  intentional full repay in the source: ``ifkr(sp)=0goto15075`` tests the RESULT, not
  the caller's intent).
- ``kdh.trade`` (``15100-15155``) — buy or sell THIS tile's loan business. Own-tile
  check (``kg(sp)=ln``) routes to sell; otherwise buy, guarded by: already own a
  DIFFERENT shop (deny), own outstanding debt (deny), a rival-scan over every other
  player for who owns this exact tile (deny, naming them) — THEN roll the price,
  confirm, afford-check, settle.
- ``kdh.capital`` (``15200-15220``) — adjust shop capital. Guard: own this tile.
  Signed delta, bounded so ``kk(sp)+x`` stays in ``[0, 5000]`` (expressed as the
  ``PromptInt`` bounds themselves — the driver's own re-prompt-on-out-of-range IS
  the source's ``goto15205`` re-prompt loop, so no extra branch is needed here);
  ``x=0`` is a quiet abort; deposits (positive ``x``) are afford-checked against cash.
- ``kdh.collect`` (``15300-15321``) — collect overdue debts. Guard: own this tile.
  2/3 chance (when capital is nonzero) of an armed ambush — see
  :func:`_ambush_probability`'s docstring for the fidelity note this port pins down
  (KTD-9: the code, not the research YAML, is 2/3). One scripted ``schuldner``
  (gewehr, 35 energy) fights the player; a win loots 500-1499$ + 2 score, a loss
  costs nothing (the source's ``ifs=2thenreturn`` — a plain, silent return).
- ``kdh.leave`` — guardless leave (the shell's ``resolve`` consequence, no handler).

Faithfulness notes
-------------------
- ALL game-balance numbers (loan bounds, price rolls, ambush odds, loot range) come
  from ``formula_params`` (KTD-10) — nothing here is a bare literal.
- The combat backdrop is pinned to ``ks`` (KTD-9): the source's ``15312`` call site
  sets no ``kf$`` of its own, so a byte-faithful port would inherit whatever ``kf$``
  last held from an unrelated call site — a stale-backdrop accident this port does
  not replicate.
- KTD-7: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
  ``ctx.apply``, and this config's OWN ``..setup`` helpers.
- ``ctx.apply`` only BUFFERS — every multi-step flow below tracks its own running
  local (``debt_amount``, ``capital``, etc.) rather than re-reading ``ctx.state``
  mid-flow, per the U9-documented landmine.

``ln`` seam
-----------
Read from the active player's ``last_location`` field, exactly as ``slw``/``pub`` do
(door entry's ``SetEntryContext`` sets it before the handler runs).
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import DebtChange, DebtClear, MoneyChange, ShopChange
from engine.interactions import Confirm, PromptInt, ShowMessage, StartCombat
from engine.locations import register

from ..setup import (
    load_combat_backdrop,
    narrate_combat_outcome,
    score_and_rank,
    weapon_stats_by_id,
)

__all__ = ["kdh_borrow", "kdh_repay", "kdh_trade", "kdh_capital", "kdh_collect"]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: The debtor-ambush opponent (mf-prg.bas:15310-15312): one schuldner, gewehr
#: (weapon id 6), energy 35.
_AMBUSHER_NAME = "schuldner"
_AMBUSHER_WEAPON = 6
_AMBUSHER_ENERGIE = 35

#: Combat backdrop pinned to ``ks`` (KTD-9 — see module docstring's fidelity note).
_AMBUSH_BACKDROP = "ks"


def _weapon_stats() -> dict:
    """This config's weapon id -> ``(ts, tg, range)`` table, for ``StartCombat.weapon_stats``.

    Matches ``jobs.py``'s/``upkeep.py``'s/``waf.py``'s own fresh-per-call loader
    (KTD-7: a handler reads its OWN config's entity data, never the engine's).
    """
    return weapon_stats_by_id(_CONFIG_DIR / "entities" / "weapons.yaml")


def _backdrop(name: str) -> tuple[int, ...]:
    return load_combat_backdrop(_CONFIG_DIR / "content" / "combat" / f"{name}.yaml")


# --------------------------------------------------------------------------- #
# kdh.borrow (option 1) — mf-prg.bas:15010-15030                               #
# --------------------------------------------------------------------------- #
@register("kdh.borrow")
def kdh_borrow(ctx):
    """Take a loan — ports ``mf-prg.bas:15010-15030``.

    1. ``:15010`` — guard: existing debt (``kr(sp)!=0``) refuses outright, no roll,
       no prompt.
    2. ``:15015-15021`` — amount 0-5000 (the driver's ``PromptInt`` bounds are the
       source's own re-prompt-on-out-of-range loop); 0 is a quiet abort (``:15020``).
    3. ``:15025-15030`` — grants the loan: ``kr(sp)+=x``, ``ka(sp)+=x``, grace
       counter ``kz(sp)=6``.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    params = ctx.state.config.formula_params

    # :15010 — one loan at a time.
    if active.debt.amount != 0:
        yield ShowMessage("locations.kdh.pay_old_debts_first")
        return []

    x = yield PromptInt(
        "locations.kdh.borrow_prompt",
        min=params["kdh_borrow_min"],
        max=params["kdh_borrow_max"],
    )
    # :15020 — x=0 is a quiet abort.
    if x == 0:
        return []

    # :15025-15030 — grant the loan.
    yield ShowMessage("locations.kdh.borrow_grace_notice")
    ctx.apply(DebtChange(amount=x, months=params["kdh_borrow_grace_months"]))
    ctx.apply(MoneyChange(x))
    return []


# --------------------------------------------------------------------------- #
# kdh.repay (option 2) — mf-prg.bas:15050-15075                                #
# --------------------------------------------------------------------------- #
@register("kdh.repay")
def kdh_repay(ctx):
    """Repay part or all of an outstanding loan — ports ``mf-prg.bas:15050-15075``.

    1. ``:15050-15055`` — amount 0..kr(sp) (``PromptInt`` bounds); 0 is a quiet abort
       (``:15051``'s ``ifx=0thenreturn``).
    2. ``:15060`` — afford check against cash; broke -> ``system.not_enough_money``,
       no state change.
    3. ``:15065`` — deduct ``x`` from both debt and cash.
    4. ``:15070``/``:15075`` — if debt is now 0, ALSO reset the grace counter
       (``DebtClear``, on top of the ``DebtChange`` already applied — the source's
       ``kz(sp)=0`` at :15075 is a SEPARATE statement from the :15065 decrement, so
       porting it as a second buffered effect mirrors the source's own two writes);
       otherwise just report the remaining balance.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    debt = active.debt.amount

    # :15050-15051 — bounds 0..kr(sp); PromptInt needs max>=min even when debt is 0
    # (a guardless entry with no debt: the source still asks and the answer is
    # forced to 0, a quiet abort. This handler is never routed to at 0 debt by any
    # in-slice caller, but the bound stays well-formed regardless).
    x = yield PromptInt("locations.kdh.repay_prompt", min=0, max=max(debt, 0))
    if x == 0:
        return []

    # :15060 — afford check, runs BEFORE any write.
    if active.ka < x:
        yield ShowMessage("system.not_enough_money")
        return []

    # :15065 — settle.
    ctx.apply(DebtChange(amount=-x))
    ctx.apply(MoneyChange(-x))

    remaining = debt - x
    if remaining == 0:
        # :15075 — full repayment: also reset the grace counter.
        ctx.apply(DebtClear())
        yield ShowMessage("locations.kdh.repay_full")
    else:
        # :15070 — partial: report the remaining balance.
        yield ShowMessage("locations.kdh.repay_partial", {"remaining": remaining})
    return []


# --------------------------------------------------------------------------- #
# kdh.trade (option 3) — mf-prg.bas:15100-15155                                #
# --------------------------------------------------------------------------- #
@register("kdh.trade")
def kdh_trade(ctx):
    """Buy or sell THIS tile's loan business — ports ``mf-prg.bas:15100-15155``.

    ``:15100`` routes on ``kg(sp)=ln``: already own THIS tile -> sell; else -> buy
    (guarded by :15105 already-own-a-different-shop, :15106 own-debt, :15107-15108
    rival scan).
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location
    params = ctx.state.config.formula_params

    if active.business.shop_tile == ln:
        yield from _sell(ctx, params=params)
        return []

    yield from _buy(ctx, ln=ln, params=params)
    return []


def _buy(ctx, *, ln: int, params: dict):
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]

    # :15105 — already own a DIFFERENT shop.
    if active.business.shop_tile != 0:
        yield ShowMessage("locations.kdh.already_own_a_shop")
        return

    # :15106 — own outstanding debt.
    if active.debt.amount != 0:
        yield ShowMessage("locations.kdh.pay_own_debts_first")
        return

    # :15107-15108 — rival scan: any OTHER player already own this tile?
    for i, other in enumerate(ctx.state.players):
        if i == sp:
            continue
        if other.business.shop_tile == ln:
            yield ShowMessage("locations.kdh.shop_belongs_to", {"name": other.name})
            return

    # :15110-15111 — price roll + confirm.
    price = (
        ctx.rng.range(params["kdh_buy_price_choices"]) * params["kdh_buy_price_step"]
        + params["kdh_buy_price_base"]
    )
    yield ShowMessage("locations.kdh.buy_offer", {"price": price})
    if not (yield Confirm("locations.kdh.buy_confirm")):
        return

    # :15115 — afford check.
    if active.ka < price:
        yield ShowMessage("system.not_enough_money")
        return

    # :15120 — settle.
    ctx.apply(MoneyChange(-price))
    ctx.apply(ShopChange(tile=ln))
    yield ShowMessage("locations.kdh.bought")


def _sell(ctx, *, params: dict):
    # :15150-15151 — price roll + confirm.
    price = (
        ctx.rng.range(params["kdh_sell_price_choices"]) * params["kdh_sell_price_step"]
        + params["kdh_sell_price_base"]
    )
    yield ShowMessage("locations.kdh.sell_offer", {"price": price})
    if not (yield Confirm("locations.kdh.sell_confirm")):
        return

    # :15155 — settle unconditionally (selling never fails on affordability).
    ctx.apply(MoneyChange(price))
    ctx.apply(ShopChange(tile=0))


# --------------------------------------------------------------------------- #
# kdh.capital (option 4) — mf-prg.bas:15200-15220                              #
# --------------------------------------------------------------------------- #
@register("kdh.capital")
def kdh_capital(ctx):
    """Adjust the shop's lending capital — ports ``mf-prg.bas:15200-15220``.

    1. ``:15200`` — guard: must own THIS tile.
    2. ``:15206-15210`` — signed delta ``x``, bounded so ``kk(sp)+x`` stays in
       ``[0, kdh_capital_max]``; the ``PromptInt`` bounds ARE the source's own
       re-prompt-on-out-of-range loop, so no separate branch reproduces it.
    3. ``:15208`` — ``x=0`` is a quiet abort.
    4. ``:15215`` — afford check (only bites on a positive delta; a withdrawal's
       ``x`` is negative, always ``<= ka``).
    5. ``:15220`` — settle: cash -= x, capital += x.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location
    params = ctx.state.config.formula_params

    # :15200 — must own this tile.
    if active.business.shop_tile != ln:
        yield ShowMessage("locations.kdh.not_your_shop")
        return []

    capital = active.business.shop_capital
    cap_max = params["kdh_capital_max"]
    yield ShowMessage("locations.kdh.capital_status", {"capital": capital, "max": cap_max})
    x = yield PromptInt(
        "locations.kdh.capital_prompt", min=-capital, max=cap_max - capital
    )
    if x == 0:
        return []

    # :15215 — afford check against cash.
    if active.ka < x:
        yield ShowMessage("system.not_enough_money")
        return []

    # :15220 — settle.
    ctx.apply(MoneyChange(-x))
    ctx.apply(ShopChange(capital_delta=x))
    return []


# --------------------------------------------------------------------------- #
# kdh.collect (option 5) — mf-prg.bas:15300-15321                              #
# --------------------------------------------------------------------------- #
@register("kdh.collect")
def kdh_collect(ctx):
    """Collect overdue debts — ports ``mf-prg.bas:15300-15321``.

    1. ``:15300`` — guard: must own THIS tile.
    2. ``:15305`` — ``int(rnd(1)*3)<>0 and kk(sp)<>0``: a 2-in-3 draw (nonzero on a
       uniform 0/1/2 pick) AND nonzero capital together gate the ambush; otherwise
       every client paid on time (``:15306``). NOTE (KTD-9): the research YAML
       documents this as "1/3", which is WRONG — ``rnd(1)*3`` uniformly yields
       0, 1, or 2, and ``<>0`` (not-equal-zero) is true for 2 of those 3 outcomes,
       i.e. 2/3, not 1/3. This port follows the CODE.
    3. ``:15310-15315`` — the ambush fight: one schuldner (gewehr, 35 energy). A
       loss (``s=2``) is a plain, silent return — no cost, no narration beyond the
       fight's own outcome screen.
    4. ``:15320-15321`` — a win loots 500-1499$ and awards +2 score
       (``x=2:gosub1160``).
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location
    params = ctx.state.config.formula_params

    # :15300 — must own this tile.
    if active.business.shop_tile != ln:
        yield ShowMessage("locations.kdh.not_your_shop")
        return []

    capital = active.business.shop_capital
    # :15305 — 2/3 chance of an ambush, ONLY when capital is nonzero.
    ambush = capital != 0 and ctx.rng.range(params["kdh_ambush_roll"]) != 0
    if not ambush:
        yield ShowMessage("locations.kdh.debts_paid_on_time")
        return []

    # :15310-15312 — the ambush fight.
    from engine.combat import setup_combat

    yield ShowMessage("locations.kdh.ambush_intro")
    combat_state = setup_combat(
        active.roster,
        enemy_count=1,
        enemy_weapon=params["kdh_ambush_weapon"],
        enemy_energie=params["kdh_ambush_energie"],
        enemy_name=_AMBUSHER_NAME,
        grid=_backdrop(_AMBUSH_BACKDROP),
    )
    winner = yield StartCombat(
        sides=combat_state.sides,
        grid=combat_state.grid,
        weapon_stats=_weapon_stats(),
        dir_memory=combat_state.dir_memory,
    )

    # Outcome narration (KTD-1: the invoking handler's job — _run_combat yields no
    # final screen). Shared with jobs.py/upkeep.py's own fights.
    yield from narrate_combat_outcome(
        winner=winner, player_name=active.name, enemy_name=_AMBUSHER_NAME
    )

    if winner == 2:
        # :15315 — lost: a plain, silent return, no cost.
        return []

    # :15320-15321 — won: loot 500-1499$, +2 score.
    loot = ctx.rng.hit(params["kdh_ambush_loot_min"], params["kdh_ambush_loot_max"])
    ctx.apply(MoneyChange(loot))
    ctx.apply(score_and_rank(params["kdh_ambush_score"], params))
    yield ShowMessage("locations.kdh.ambush_loot", {"amount": loot})
    return []
