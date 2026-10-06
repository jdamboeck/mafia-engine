"""The kdh (Kredit-Hai / loan shark) handlers — ports ``mf-prg.bas:15000-15321``.

Six menu options, all sharing one shell (no per-option shell guard: every refusal in
the source happens INSIDE the handler body, exactly like ``pub.drink``/``pub.tip``/
``pub.job`` — the shell only carries guards where the ORIGINAL refuses before
even offering the option, and kdh never does that):

- ``kdh.borrow`` (``15010-15030``) — take a loan. Guard: no existing debt
  (``kr(sp)=0``); amount 0-5000; sets ``kr+=x``, ``ka+=x``, grace ``kz=6``.
- ``kdh.repay`` (``15050-15075``) — repay part or all. Bounds ``0<=x<=kr(sp)``;
  afford check; full repayment (``kr`` reaches 0) additionally resets the grace
  counter ``kz=0`` (:class:`DebtClear`, applied ALONGSIDE the final
  :class:`DebtChange` that zeros ``kr`` — not instead of it, since a
  partial repay that happens to land exactly on 0 is the SAME code path as an
  intentional full repay in the source: ``ifkr(sp)=0goto15075`` tests the RESULT, not
  the caller's intent).
- ``kdh.trade`` (``15100-15155``) — buy or sell THIS tile's loan business. Own-tile
  check (``kg(sp)=ln``) routes to sell; otherwise buy, guarded by: already own a
  DIFFERENT shop (deny), own outstanding debt (deny), a rival-scan over every other
  player for who owns this exact tile (deny, naming them) — THEN roll the price,
  confirm, afford-check, settle, wait for a key (``:15120 gosub1100``) and go on to
  the capital screen (``:15125 goto15200``) in the same option.
- ``kdh.capital`` (``15200-15220``) — adjust shop capital. Guard: own this tile.
  Signed delta, bounded so ``kk(sp)+x`` stays in ``[0, 5000]`` (expressed as the
  ``PromptInt`` bounds themselves — the driver's own re-prompt-on-out-of-range IS
  the source's ``goto15205`` re-prompt loop, so no extra branch is needed here);
  ``x=0`` is a quiet abort; deposits (positive ``x``) are afford-checked against cash.
- ``kdh.collect`` (``15300-15321``) — collect overdue debts. Guard: own this tile.
  2/3 chance (when capital is nonzero) of an armed ambush — see
  :func:`_ambush_probability`'s docstring for the fidelity note this port pins down
  (the code, not the research YAML, is authoritative: 2/3). One scripted ``schuldner``
  (gewehr, 35 energy) fights the player; a win loots 500-1499$ + 2 score, a loss
  costs nothing (the source's ``ifs=2thenreturn`` — a plain, silent return).
- ``kdh.leave`` — guardless leave (the shell's ``resolve`` consequence, no handler).

Faithfulness notes
-------------------
- The key wait (``:1100``); every exit the source sends through ``goto1100``/
  ``gosub1100`` or ``goto1125`` yields ``KEY_WAIT``; every ``return`` exit
  (``:15020``, ``:15051``, ``:15111``, ``:15151``, ``:15155``, ``:15208``, ``:15220``,
  ``:15315``) returns without one.
- ALL game-balance numbers (loan bounds, price rolls, ambush odds, loot range) come
  from ``formula_params`` — nothing here is a bare literal.
- The combat backdrop is pinned to ``ks``: the source's ``15312`` call site
  sets no ``kf$`` of its own, so a byte-faithful port would inherit whatever ``kf$``
  last held from an unrelated call site — a stale-backdrop accident this port does
  not replicate.
- Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``,
  ``ctx.apply``, and this config's OWN ``..setup`` helpers.
- ``ctx.apply`` only BUFFERS — every multi-step flow below tracks its own running
  local (``debt_amount``, ``capital``, etc.) rather than re-reading ``ctx.state``
  mid-flow, since effects only commit after the handler returns.

``ln`` seam
-----------
Read from the active player's ``last_location`` field, exactly as ``slw``/``pub`` do
(door entry's ``SetEntryContext`` sets it before the handler runs).
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import MoneyChange
from ..effects import DebtChange, DebtClear, ShopChange
from ..state import business, debt
from engine.interactions import Confirm, PromptInt, ShowMessage
from engine.locations import register

from ..setup import (
    KEY_WAIT,
    apply_outcome,
    load_encounter,
    run_encounter,
)

__all__ = ["kdh_borrow", "kdh_repay", "kdh_trade", "kdh_capital", "kdh_collect"]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: The debtor-ambush encounter (mf-prg.bas:15310-15321) — one schuldner (gewehr,
#: 35 energy), fully declared in data (content/encounters/kdh_ambush.yaml),
#: including its win consequence (loot roll + score + message). The NAME is read
#: back off the loaded encounter for the outcome narration; nothing about the fight
#: is assembled inline.
_AMBUSH_ENCOUNTER = load_encounter(_CONFIG_DIR / "content" / "encounters" / "kdh_ambush.yaml")


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
    if debt(active).amount != 0:
        yield ShowMessage("locations.kdh.pay_old_debts_first")
        yield KEY_WAIT  # :15010 ...:goto1100
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
    yield KEY_WAIT  # :15030 ...:kz(sp)=6:goto1100
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
    debt_amount = debt(active).amount

    # :15050-15051 — bounds 0..kr(sp); PromptInt needs max>=min even when debt is 0
    # (a guardless entry with no debt: the source still asks and the answer is
    # forced to 0, a quiet abort. This handler is never routed to at 0 debt by any
    # caller, but the bound stays well-formed regardless).
    x = yield PromptInt("locations.kdh.repay_prompt", min=0, max=max(debt_amount, 0))
    if x == 0:
        return []

    # :15060 — afford check, runs BEFORE any write.
    if active.ka < x:
        yield ShowMessage("system.not_enough_money")
        yield KEY_WAIT  # goto1125 -> :1125 ...:goto1100
        return []

    # :15065 — settle.
    ctx.apply(DebtChange(amount=-x))
    ctx.apply(MoneyChange(-x))

    remaining = debt_amount - x
    if remaining == 0:
        # :15075 — full repayment: also reset the grace counter.
        ctx.apply(DebtClear())
        yield ShowMessage("locations.kdh.repay_full")
    else:
        # :15070 — partial: report the remaining balance.
        yield ShowMessage("locations.kdh.repay_partial", {"remaining": remaining})
    yield KEY_WAIT  # :15070 / :15075 ...:goto1100
    return []


# --------------------------------------------------------------------------- #
# kdh.trade (option 3) — mf-prg.bas:15100-15155                                #
# --------------------------------------------------------------------------- #
@register("kdh.trade")
def kdh_trade(ctx):
    """Buy or sell THIS tile's loan business — ports ``mf-prg.bas:15100-15155``.

    ``:15100`` routes on ``kg(sp)=ln``: already own THIS tile -> sell; else -> buy
    (guarded by :15105 already-own-a-different-shop, :15106 own-debt, :15107-15108
    rival scan). A purchase waits for a key (``:15120 ...gosub1100``) and then opens
    the capital screen (``:15125 goto15200``, :func:`_capital_screen`).
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location
    params = ctx.state.config.formula_params

    if business(active).shop_tile == ln:
        yield from _sell(ctx, params=params)
        return []

    yield from _buy(ctx, ln=ln, params=params)
    return []


def _buy(ctx, *, ln: int, params: dict):
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]

    # :15105 — already own a DIFFERENT shop.
    if business(active).shop_tile != 0:
        yield ShowMessage("locations.kdh.already_own_a_shop")
        yield KEY_WAIT  # :15105 ...:goto1100
        return

    # :15106 — own outstanding debt.
    if debt(active).amount != 0:
        yield ShowMessage("locations.kdh.pay_own_debts_first")
        yield KEY_WAIT  # :15106 ...:goto1100
        return

    # :15107-15108 — rival scan: any OTHER player already own this tile?
    for i, other in enumerate(ctx.state.players):
        if i == sp:
            continue
        if business(other).shop_tile == ln:
            yield ShowMessage("locations.kdh.shop_belongs_to", {"name": other.name})
            yield KEY_WAIT  # :15108 ...:goto1100
            return

    # :15110-15111 — price roll + confirm. :15110 `p=int(rnd(1)*11)*100+5000`
    price = (
        ctx.rng.range(params["kdh_buy_price_choices"]) * params["kdh_buy_price_step"]
        + params["kdh_buy_price_base"]
    )
    yield ShowMessage("locations.kdh.buy_offer", {"price": price})
    if not (yield Confirm("locations.kdh.buy_confirm")):
        return  # :15111 ifx$="n"thenreturn -- no key wait

    # :15115 — afford check.
    if active.ka < price:
        yield ShowMessage("system.not_enough_money")
        yield KEY_WAIT  # :15115 goto1125 -> :1125 ...:goto1100
        return

    # :15120 — settle, then wait for a key (``gosub1100``).
    ctx.apply(MoneyChange(-price))
    ctx.apply(ShopChange(tile=ln))
    yield ShowMessage("locations.kdh.bought")
    yield KEY_WAIT

    # :15125 goto15200 — the capital screen follows in the same option. Its :15200
    # owner check passes (:15120 just set kg(sp)=ln). ``ctx.apply`` only buffers, so
    # the screen runs on the cash the purchase left; the capital kk(sp) is untouched
    # by the purchase (:15120 does not reset it).
    yield from _capital_screen(
        ctx, cash=active.ka - price, capital=business(active).shop_capital, params=params
    )


def _sell(ctx, *, params: dict):
    # :15150-15151 — price roll + confirm. :15150 `p=int(rnd(1)*11)*100+4500`
    price = (
        ctx.rng.range(params["kdh_sell_price_choices"]) * params["kdh_sell_price_step"]
        + params["kdh_sell_price_base"]
    )
    yield ShowMessage("locations.kdh.sell_offer", {"price": price})
    if not (yield Confirm("locations.kdh.sell_confirm")):
        return  # :15151 ifx$="n"thenreturn -- no key wait

    # :15155 — settle unconditionally (selling never fails on affordability), and
    # ``return`` with no message and no key wait.
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

    Steps 2-5 are :func:`_capital_screen`, which a purchase reaches too (``:15125``).
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location
    params = ctx.state.config.formula_params

    # :15200 — must own this tile.
    if business(active).shop_tile != ln:
        yield ShowMessage("locations.kdh.not_your_shop")
        yield KEY_WAIT  # :15200 ...:goto1100
        return []

    yield from _capital_screen(
        ctx, cash=active.ka, capital=business(active).shop_capital, params=params
    )
    return []


def _capital_screen(ctx, *, cash: int, capital: int, params: dict):
    """The capital screen, ``:15205-15220``: reached from the menu (:func:`kdh_capital`)
    and straight after a purchase (``:15125 goto15200``).

    ``cash``/``capital`` are the running values (``ctx.apply`` only buffers, so a
    caller that has already bought passes the cash its purchase left).
    """
    cap_max = params["kdh_capital_max"]
    yield ShowMessage("locations.kdh.capital_status", {"capital": capital, "max": cap_max})
    x = yield PromptInt("locations.kdh.capital_prompt", min=-capital, max=cap_max - capital)
    if x == 0:
        return  # :15208 ifx=0thenreturn -- no key wait

    # :15215 — afford check against cash.
    if cash < x:
        yield ShowMessage("system.not_enough_money")
        yield KEY_WAIT  # :15215 goto1125 -> :1125 ...:goto1100
        return

    # :15220 — settle, and ``return`` with no key wait.
    ctx.apply(MoneyChange(-x))
    ctx.apply(ShopChange(capital_delta=x))


# --------------------------------------------------------------------------- #
# kdh.collect (option 5) — mf-prg.bas:15300-15321                              #
# --------------------------------------------------------------------------- #
@register("kdh.collect")
def kdh_collect(ctx):
    """Collect overdue debts — ports ``mf-prg.bas:15300-15321``.

    1. ``:15300`` — guard: must own THIS tile.
    2. ``:15305`` — ``int(rnd(1)*3)<>0 and kk(sp)<>0``: a 2-in-3 draw (nonzero on a
       uniform 0/1/2 pick) AND nonzero capital together gate the ambush; otherwise
       every client paid on time (``:15306``). NOTE: the research YAML
       documents this as "1/3", which is WRONG — ``rnd(1)*3`` uniformly yields
       0, 1, or 2, and ``<>0`` (not-equal-zero) is true for 2 of those 3 outcomes,
       i.e. 2/3, not 1/3. This port follows the CODE.
    3. ``:15310-15315`` — the ambush fight: one schuldner (gewehr, 35 energy),
       declared in ``content/encounters/kdh_ambush.yaml``. A loss (``s=2``)
       is a plain, silent return — the encounter's ``on_loss: []`` applies nothing.
    4. ``:15320-15321`` — a win loots 500-1499$ and awards +2 score
       (``x=2:gosub1160``) — the encounter's ``on_win`` block (money roll + score +
       message), applied through the config's shared ``apply_outcome`` helper.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location
    params = ctx.state.config.formula_params

    # :15300 — must own this tile.
    if business(active).shop_tile != ln:
        yield ShowMessage("locations.kdh.not_your_shop")
        yield KEY_WAIT  # :15300 ...:goto1100
        return []

    capital = business(active).shop_capital
    # :15305 — 2/3 chance of an ambush, ONLY when capital is nonzero.
    ambush = capital != 0 and ctx.rng.range(params["kdh_ambush_roll"]) != 0
    if not ambush:
        yield ShowMessage("locations.kdh.debts_paid_on_time")
        yield KEY_WAIT  # :15306 ...:goto1100
        return []

    # :15310-15312 — the ambush fight, the declared encounter run by the shared fight
    # helper (which also shows the outcome screen, :30500-30515).
    yield ShowMessage("locations.kdh.ambush_intro")
    yield KEY_WAIT  # :15312 gosub1100, before the fight (gosub5000)
    enc = _AMBUSH_ENCOUNTER
    # The outcome screen (:30500-30515) and its :30520 ``print:goto1100`` key wait are
    # the combat subroutine's, shown by run_encounter for every caller.
    result = yield from run_encounter(ctx, enc)

    # :15315 (loss — on_loss: [], nothing; ``return``, no further wait) / :15320-15321
    # (win — loot + score + message, then ``goto1100``). The whole declarable
    # consequence rides the encounter's on_win/on_loss.
    yield from apply_outcome(ctx, enc, result)
    if result.winner == 1:
        yield KEY_WAIT  # :15321 ...:gosub1160:goto1100
    return []
