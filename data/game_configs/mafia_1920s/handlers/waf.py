"""The waf (Waffengeschaeft / weapon shop) handlers.

Two handlers sharing one shop, ported from ``mf-prg.bas:13005-13175`` + the weapon
spec-sheet sub-state at ``13500-13525``:

- ``waf.buy`` (``13010-13091``) — stock by tile ``ln``, an RNG-gated grenade extension,
  a range-guarded weapon pick, the weapon spec-sheet SUB-STATE, a gangster pick with
  three per-gangster stat gates, a trade-in offer + yes/no confirm, and the fused
  score/rank adjust on settle. This is the widest protocol surface of any handler.
- ``waf.train`` (``13100-13175``) — pick a gangster, then (at rank >= 5) a single-key
  venue choice between the range (``schiesstand``) and the camp (``trainingscamp``),
  each with its own price, afford-check, ``ln``-modified stat gains capped at 99, and a
  training score reward.

Faithfulness notes
------------------
- ALL game-balance numbers come from ``formula_params`` — prices, caps, gain
  ranges, ratios, roll odds are read from config and passed into the effects, never
  hardcoded here.
- Every relational term uses the C64 ``true = -1`` evaluation (see
  ``docs/solutions/.../basic-relational-boolean-is-minus-one-when-porting.md``, confirmed
  by the #47 fidelity audit — do not revert to ``true = +1``).
- Stat gates are HANDLER branching, not shell guards: they test the CHOSEN
  gangster mid-handler, which the option-entry guard DSL cannot express.
- No content-specific events: outcomes are reconstructable from the committed
  effects + logged RNG draws.
- Handler API: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``, ``ctx.apply``,
  and its OWN config helpers (``..setup``).

Cancellability rule
-------------------
Only whole-action aborts use ``cancellable=True`` (the driver's atomic discard). Every
"return to an earlier menu" (afford-fail, stat-gate fail, trade-in decline) is an
IN-HANDLER loop, because a driver-cancel unwinds the entire handler and cannot resume at
an inner menu.

``ln`` seam
-----------
Read from the active player's ``last_location`` field, exactly as ``slw`` does (door
entry in ``engine.movement`` records it before the menu runs).
"""

from __future__ import annotations

from engine.effects import AssignWeapon, MoneyChange, ScoreChange, StatChangeCapped
from engine.interactions import Confirm, LoadSubState, PromptChoice, PromptInt, ShowMessage
from engine.locations import register
from engine.substates import register_substate

from ..setup import load_weapons, score_and_rank

__all__ = ["waf_buy", "waf_train", "weapon_spec"]


def _weapons():
    """Load this config's weapon table via the config's own loader.

    Reads the table relative to this module's config directory (mirrors how setup loads
    it) — the config is frozen per game, so a fresh read per action is harmless.
    """
    from pathlib import Path

    cfg_dir = Path(__file__).resolve().parents[1]
    return load_weapons(cfg_dir / "entities" / "weapons.yaml")


# --------------------------------------------------------------------------- #
# Weapon spec-sheet sub-state — display-only LoadSubState consumer            #
# --------------------------------------------------------------------------- #
@register_substate("weapon_spec")
def weapon_spec(ctx, params):
    """Show a weapon's spec sheet, then return — ports ``mf-prg.bas:13500-13525``.

    A display-only sub-state: it yields one ``ShowMessage`` (the resolved spec
    screen) and returns ``None``. The accuracy/effect labels are BUCKETED lookups, not
    raw values: accuracy bucket = ``int(ts/2)`` (13515), effect bucket = ``int(tg/4)+1``
    (13520). ``params`` carries the resolved weapon record + its index.
    """
    w = params["weapon"]
    # BASIC int() floors; use floor division (//) so the port stays faithful even if a
    # future config gives a negative ts/tg (float int() would truncate toward zero).
    yield ShowMessage(
        "locations.waf.spec_sheet",
        {
            "name": w["name"],
            "price": w["price"],
            "accuracy_bucket": w["ts"] // 2,  # 13515 ts$(int(ts/2))
            "effect_bucket": w["tg"] // 4 + 1,  # 13520 tg$(int(tg/4)+1)
        },
    )
    return None


# --------------------------------------------------------------------------- #
# waf.buy — mf-prg.bas:13010-13091                                            #
# --------------------------------------------------------------------------- #
@register("waf.buy")
def waf_buy(ctx):
    """Buy a weapon and arm a gangster — ports ``mf-prg.bas:13010-13091`` + trade-in."""
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location  # ln seam (see module docstring)
    params = ctx.state.config.formula_params
    weapons = _weapons()

    # The whole buy loops here on afford-fail / trade-in decline — the original re-enters
    # at 13010 (goto13010 at 13025/13071), which RE-EXECUTES the stock computation AND the
    # ln=1 grenade roll (13011). So the stock range + grenade roll live INSIDE the loop:
    # each re-entry re-rolls, matching the original's stock churn and RNG draw count.
    while True:
        # 13011-13013 — stock range from the tile ln.
        if ln == 1:
            lo, hi = 3, 7
            # 13011 — grenade roll: 1-in-3 AND rank > gate, extends stock to grenades (13091).
            if (
                ctx.rng.range(params["grenade_roll"]) == 0
                and active.rank > params["grenade_rank_gate"]
            ):
                yield ShowMessage("locations.waf.grenades_in")
                hi = 8
        elif ln == 2:
            lo, hi = 1, 5
        else:  # ln == 3 (13013)
            lo, hi = 1, 4

        # 13015-13020 — list weapons in [lo, hi]; pick one. 0 cancels the whole buy.
        x = yield PromptInt(
            "locations.waf.weapon_prompt",
            min=lo,
            max=hi,
            cancellable=True,
        )

        # 13025 — affordability against the model price; loops back to the list.
        if active.ka < weapons[x]["price"]:
            yield ShowMessage("system.not_enough_money")
            continue

        # 13035 — spec sheet (sub-state), then pick the gangster to arm (0 cancels back).
        yield LoadSubState("weapon_spec", {"weapon": weapons[x], "index": x})

        gangster_picked = yield from _pick_gangster_and_arm(ctx, active, weapons, x, params)
        if gangster_picked:
            return []
        # gangster pick / trade-in returned to the weapon list — loop.


def _pick_gangster_and_arm(ctx, active, weapons, x, params):
    """Pick a gangster, run the stat gates, settle the trade-in + purchase.

    Returns ``True`` when the purchase settled (the buy is done), ``False`` when the flow
    returned to the weapon list (gangster cancel or trade-in decline) so ``waf.buy`` loops.
    Yields interactions to the driver via ``yield from``.
    """
    new_price = weapons[x]["price"]
    # 1130 — the original's gangster picker returns y=0 immediately when the player owns
    # no gangster (gz(sp)=0), which at 13035 (ify=0goto13010) loops back to the weapon
    # list. Mirror that here rather than presenting an unanswerable empty PromptChoice.
    if not active.roster:
        return False
    while True:  # 13035 gangster-pick loop (stat-gate fail returns here)
        # 13035 gosub 1130 — pick which owned gangster; y=0 cancels back to the list.
        y = yield PromptChoice(
            "locations.waf.gangster_prompt",
            options=[g.name for g in active.roster],
            cancellable=True,
        )
        g = active.roster[y]

        # 13050-13060 — three per-gangster stat gates. A failed gate shows its
        # reason and returns to the gangster pick.
        if g.attrs["intelligenz"] < weapons[x]["req_int"]:
            yield ShowMessage("locations.waf.too_dumb")
            continue
        if g.attrs["kraft"] < weapons[x]["req_kraft"]:
            yield ShowMessage("locations.waf.too_weak")
            continue
        if g.attrs["brutalitaet"] < weapons[x]["req_brut"]:
            yield ShowMessage("locations.waf.not_brutal")
            continue

        old = g.weapon  # gw(sp,y) — the gangster's CURRENT weapon (0 = unarmed)
        # x8 == the score weight (formula_params["score_mult"]); the buy-score modifies gf DIRECTLY
        # by ±x8 (13065/13072/13073) and does NOT go through gosub 1160, so it neither
        # recomputes rank nor clamps: an unclamped ScoreChange, not ScoreAndRank. Only the
        # gf<100 / gf>0 guards bound it, so gf=99,x8=2 upgrading reaches 101 and stays
        # there until the next gosub 1160 (ScoreAndRank) clamps it.
        x8 = ctx.state.config.formula_params["score_mult"]

        if old == 0:
            # 13065 — no old weapon: q=0. `gf(sp)=gf(sp)-x8*1*(gf(sp)<100)` with the C64
            # true=-1 evaluation is score UP by x8 while gf<100: arming a
            # previously unarmed gangster raises the gang's notoriety.
            q = 0
            if active.gf < 100:
                ctx.apply(ScoreChange(x8, floor=None, cap=None))
        else:
            # 13070-13071 — trade-in offer on the OLD weapon's price, yes/no confirm.
            # :13070 `q=int(wp(gw(sp,y))/1.5)`
            q = int(weapons[old]["price"] / params["trade_in_divisor"])
            yield ShowMessage("locations.waf.trade_in_offer", {"amount": q})
            if not (yield Confirm("locations.waf.trade_in_confirm")):
                return False  # 13071 "n" -> back to the weapon list
            # 13072/13073 — score sign by index comparison, C64 true=-1.
            # Weapon indices ascend in power/price (DATA 50100-50115), so
            # `x > old` is an UPGRADE:
            #   :13072 upgrade    `gf(sp)=gf(sp)-x8*1*(gf(sp)<100)` -> UP by x8 (while gf<100)
            #   :13073 downgrade  `gf(sp)=gf(sp)+x8*2*(gf(sp)>0)` -> DOWN by 2*x8 (while gf>0)
            if x > old:
                if active.gf < 100:
                    ctx.apply(ScoreChange(x8, floor=None, cap=None))
            else:
                if active.gf > 0:
                    ctx.apply(ScoreChange(-2 * x8, floor=None, cap=None))

        # 13075 — settle: cash += q - new_price; assign the weapon to the gangster.
        # :13075 `ka(sp)=ka(sp)+q-wp(x)`
        # Use the picked index y directly (roster.index(g) could resolve to the wrong
        # gangster when two share identical stats — dataclass __eq__ is by value).
        ctx.apply(MoneyChange(q - new_price))
        ctx.apply(AssignWeapon(weapon=x, gangster=y))
        yield ShowMessage("locations.waf.bought")
        return True


# --------------------------------------------------------------------------- #
# waf.train — mf-prg.bas:13100-13175                                          #
# --------------------------------------------------------------------------- #
@register("waf.train")
def waf_train(ctx):
    """Train a gangster at the range or the camp — ports ``mf-prg.bas:13100-13175``."""
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location
    params = ctx.state.config.formula_params
    cap = params["stat_cap"]

    # 13100 — no gangster -> abort.
    if not active.roster:
        yield ShowMessage("locations.waf.no_gangster")
        return []

    # 13101-13102 — pick which gangster to train (0 cancels the whole action).
    y = yield PromptChoice(
        "locations.waf.train_prompt",
        options=[g.name for g in active.roster],
        cancellable=True,
    )

    # 13103-13107 — venue: rank >= 5 offers (s)chiesstand vs (t)rainingscamp; below, range only.
    is_camp = False
    if active.rank >= 5:
        venue = yield PromptChoice(
            "locations.waf.venue_prompt",
            options=["locations.waf.venue_range", "locations.waf.venue_camp"],
        )
        is_camp = venue == 1

    if is_camp:
        # 13150-13175 — trainingscamp. :13150 `p=2500+500*ra(sp)`
        p = params["camp_base"] + params["camp_per_rank"] * active.rank
        yield ShowMessage("locations.waf.camp_cost", {"price": p})
        if not (yield Confirm("locations.waf.confirm")):
            return []
        if active.ka < p:  # 13160
            yield ShowMessage("system.not_enough_money")
            return []
        yield ShowMessage("locations.waf.camp_enter", {"name": active.roster[y].name})
        ctx.apply(MoneyChange(-p))  # 13170 ka -= p
        # 13170-13172 — each of int/brut/kraft rises by its OWN fnr(0) = rng.hit(8,15) draw:
        # :13170 `in=in+fnr(0)`, :13171 `bt=bt+fnr(0)`, :13172 `kr=kr+fnr(0)`, each capped
        # at 99; :117 `deffnr(x)=int(rnd(1)*8)+8`.
        for stat in ("intelligenz", "brutalitaet", "kraft"):
            gain = ctx.rng.hit(params["camp_gain_min"], params["camp_gain_max"])
            ctx.apply(StatChangeCapped(stat, gain, cap=cap, gangster=y))
        # 13175 — score training reward x=2 (stat effects apply BEFORE the score effect),
        # then the "...da ist er wieder!" flourish.
        ctx.apply(score_and_rank(2, params))
        yield ShowMessage("locations.waf.camp_done")
    else:
        # 13110-13130 — schiesstand (range). :13110 `p=800+200*ra(sp)`
        p = params["range_base"] + params["range_per_rank"] * active.rank
        yield ShowMessage("locations.waf.range_cost", {"price": p})
        if not (yield Confirm("locations.waf.confirm")):
            return []
        if active.ka < p:  # 13116
            yield ShowMessage("system.not_enough_money")
            return []
        yield ShowMessage("locations.waf.range_enter")
        ctx.apply(MoneyChange(-p))  # 13125 ka -= p
        # 13125-13127 — ln-modified stat gains (C64 true=-1), each capped at 99:
        #   kr += 5 ; in += 3 - 2*(ln=1) ; bt += 2 - 3*(ln=2)
        #   (:13125 `kr=kr+5`, :13126 `in=in+3-2*(ln=1)`, :13127 `bt=bt+2-3*(ln=2)`)
        # A true relational is -1, so the ln-matching tile ADDS to the gain:
        # in += 5 at ln=1, bt += 5 at ln=2. (The old true=+1 reading made the
        # bt gain -1 — a training session that damaged the stat.)
        ctx.apply(StatChangeCapped("kraft", 5, cap=cap, gangster=y))
        ctx.apply(
            StatChangeCapped("intelligenz", 3 - 2 * (-1 if ln == 1 else 0), cap=cap, gangster=y)
        )
        ctx.apply(
            StatChangeCapped("brutalitaet", 2 - 3 * (-1 if ln == 2 else 0), cap=cap, gangster=y)
        )
        # 13130 — score training reward x=1.
        ctx.apply(score_and_rank(1, params))
    return []
