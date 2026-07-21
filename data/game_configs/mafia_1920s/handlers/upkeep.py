"""The turn-start upkeep generator — ports ``mf-prg.bas:4000-4090`` (U3).

Registered under :data:`engine.upkeep.UPKEEP_HANDLER_KEY` in the SAME
:data:`engine.locations.HANDLERS` registry a location option's ``handler`` string
resolves against (KTD-3) — :func:`engine.upkeep.run_upkeep` is the engine-level runner
that looks this generator up and drives it at every player's turn start, before the
free turn (or, from U10, a job shift).

This unit lands the flow's HEAD per the order fixed by KTD-3
(``banner -> regen -> rank -> debt -> shop income -> arms deal -> job-shift/free-turn``):

* **banner** (``4005-4006``) — announce the active player.
* **per-gangster energy regen** (``4015-4025``) — ``en += int(kraft/10)+1``, capped at
  ``2+int(kraft/4)+int(brutalitaet/4)``, once per gangster in ``gz(sp)`` order (the
  boss included — ``roster[0]`` IS gangster 1 of ``gz``, KTD-6).
* **rank promotion commit** (``4030``) — ``ra(sp)=nr(sp)`` iff they differ, with the
  wanted-poster promotion screen (``4200-4220``).

Three slots were declared as no-ops in U3; U8 filled the arms-deal slot, U11 the
shop-income slot, and this unit (U12) fills the debt slot — each in place, without
reordering anything already here:

* **debt check** (``4040``, ``4300-4370``, U12) — the loan-shark grace countdown and
  its collectors fight. Ports ``:4305``'s tick, ``:4306-4309``'s warning,
  ``:4350-4355``'s fight, and ``:4365-4370``'s seizure. See COUNTER DIRECTION below.
* **shop income** (``4041-4420``, U11) — the passive kdh-shop payout roll. Ports
  ``mf-prg.bas:4041``'s guard (``kg(sp)<>0andkk(sp)<>0`` — must own a shop AND have
  nonzero capital) gosub'd to ``4400-4420``: 1-in-3 quiet month (no income, no
  effect); else a payout ``p=int(rnd(1)*kk(sp)/20+kk(sp)/10)`` — 10%-15% of the
  shop's capital.
* **arms deal** (``4060``, U8) — the staked heist-tip resolution, ports
  ``mf-prg.bas:31000-31051``. Only fires when the active player's ``tip_target ==
  pub.ARMS_DEAL_TIP`` (4) — set by ``pub.tip``'s stake sub-flow. The tip is CLEARED
  FIRST (``:31000``'s ``tp(sp)=0`` is the line's first statement, before the RNG roll)
  so the resolution can only ever fire once per stake: even though this same generator
  cannot re-run mid-turn, the clear-first ordering is what a later turn's upkeep read
  of ``tip_target`` sees, and it is load-bearing precisely because nothing else ever
  sets ``tip_target`` back to 4 — a stake resolves exactly once, never again. Then
  1-in-5 total loss (no cash effect — the 5000$ stake is already spent, sunk cost);
  else a payout of 5500-14999$ (``formula_params.pub_arms_deal_payout_min/max``).

The job-shift seam (``employed -> shift flow instead of the free turn``, mirroring the
source's ``1012`` dispatch) is **out of this generator entirely**: upkeep only prepares
the player for their turn, it does not decide what KIND of turn follows. That dispatch
belongs to the caller (the client's turn loop today; U10 gives it a real shift-flow
branch) — this generator's ``return []`` handing control back is exactly the hand-off
point.

Deferred lines NOT ported here (Scope Boundaries): ``4045-4046`` (rent countdown/
eviction — an out-of-scope system this slice), ``4050`` (bribe-protection aging),
``4055-4056`` (fake-papers/counterfeit decay) — none are triggered by anything in-slice.

COUNTER DIRECTION — the U2-flagged relational-sign landmine (``:4305``)
-----------------------------------------------------------------------
``:4305`` is ``kz(sp)=kz(sp)+(kz(sp)>0)``. This project pins a porting convention of
``true=+1`` (``docs/solutions/architecture-patterns/
basic-relational-boolean-is-plus-one-when-porting.md``), under which the counter would
climb from 6 forever — a grace period that never expires and a headline flow that
never fires. Under strict C64 semantics (``true=-1``) it counts DOWN.

**The source pins DOWN**, and the convention loses here — the same resolution shape
U6 applied at ``:30450`` (sibling lines stating the rule with literal constants beat
the convention). Three independent confirmations:

1. ``:15030`` sets ``kz(sp)=6`` on borrowing, and ``:15025`` prints "du hast 6 monate
   zeit". A counter that ascends from 6 has no terminus; one that descends from 6
   expires in exactly the six months the game promises the player.
2. ``:4305``'s own branch is ``ifkz(sp)=0goto4350`` — the collectors are reachable
   only by a counter that DESCENDS to 0. Ascending from 6 never equals 0, so an
   up-count makes the entire debt-default flow dead code.
3. ``:4308`` prints ``kz(sp)+1`` as the months remaining. That ``+1`` is only correct
   if the tick has already fired this turn: after the first tick (6->5) the player is
   told "6 monat(e)", matching :15025's promise. Under an up-count it would report an
   ever-growing deadline.

Getting this backwards is SILENT — the flow simply never triggers and tests still
pass — so ``tests/test_debt_default.py::test_counter_counts_down_not_up`` exists
specifically to fail if the sign is ever inverted.

The ``(kz(sp)>0)`` guard also makes **0 a fixed point**, which is load-bearing: it is
what makes a WON fight recur every turn (KTD-9) rather than silently resetting.

KTD-7 conformance: touches only ``ctx.state`` (read-only), ``yield <Interaction>``,
``ctx.apply(<Effect>)``, and this config's own ``..setup``/entity-loader helpers.
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import (
    DebtChange,
    DebtClear,
    EnergyChange,
    MoneyChange,
    RankCommit,
    TipClear,
)
from engine.interactions import ShowMessage, StartCombat
from engine.locations import register
from engine.upkeep import UPKEEP_HANDLER_KEY

from ..combat_rules import build_rules, enemy_attrs, equipper
from ..setup import load_combat_backdrop, narrate_combat_outcome, weapon_stats_by_id
from .pub import ARMS_DEAL_TIP

__all__ = ["upkeep_turn_start"]

_CONFIG_DIR = Path(__file__).resolve().parents[1]

#: The debt-default collectors (mf-prg.bas:4355): five "eintreiber", schlagkette
#: (weapon id 3), energy 30 each, on the "ks" backdrop. Counts/stats are config
#: params (config.yaml's kdh_collectors_*); the NAME and BACKDROP are fixed strings
#: in the source line itself.
_COLLECTOR_NAME = "eintreiber"
_COLLECTORS_BACKDROP = "ks"


def _weapon_stats() -> dict:
    """This config's weapon id -> ``(ts, tg, range)`` table, for ``StartCombat.weapon_stats``.

    Matches ``kdh.py``/``jobs.py``'s fresh-per-call loader (KTD-7: a handler reads its
    OWN config's entity data, never the engine's).
    """
    return weapon_stats_by_id(_CONFIG_DIR / "entities" / "weapons.yaml")


def _backdrop(name: str) -> tuple[int, ...]:
    return load_combat_backdrop(_CONFIG_DIR / "content" / "combat" / f"{name}.yaml")


def _rank_names() -> list[str]:
    """This config's rank-name table (``ra$``), 0-based (index i == in-game rank i+1).

    Goes through the config's own ``load_ranks`` loader — which validates every entry
    against ``engine.types.validate_rank`` — rather than reading the YAML directly, so
    the handler and the client's promotion screen share one validated path. Matches
    ``waf.py``'s ``_weapons()`` pattern (KTD-7: a handler reads its OWN config's entity
    data, never the engine's); the config is frozen per game, so a fresh read per call
    is harmless.
    """
    from ..setup import load_ranks

    return load_ranks(_CONFIG_DIR / "entities" / "ranks.yaml")


@register(UPKEEP_HANDLER_KEY)
def upkeep_turn_start(ctx):
    """Run the active player's turn-start upkeep — ports ``mf-prg.bas:4000-4090``.

    Offers NO cancel path (every yielded interaction is a ``ShowMessage``, which the
    driver auto-acks without consulting the input source at all) — no player input can
    discard this flow, matching the Verification Contract.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]

    # --- 4005-4006: turn banner --------------------------------------------
    yield ShowMessage("upkeep.turn_banner", {"name": active.name})

    # --- 4010-4025: per-gangster energy regen (boss included, gz(sp) order) -
    for g_idx, gangster in enumerate(active.roster):
        cap = 2 + gangster.kraft // 4 + gangster.brutalitaet // 4  # :4020
        gain = gangster.kraft // 10 + 1  # :4015
        ctx.apply(EnergyChange(amount=gain, cap=cap, gangster=g_idx))

    # --- 4030: rank promotion commit + wanted-poster screen (4200-4220) ----
    # nr is the PENDING rank ScoreAndRank already recomputes from gf on every score
    # award; rank ("ra") is what guards/prices actually read (waf.py's `active.rank`)
    # and only moves here. Compare against the state READ AT THIS HANDLER'S START —
    # nothing above this point can have changed nr, so this is exactly :4030's read.
    if active.rank != active.nr:
        ranks = _rank_names()
        yield ShowMessage(
            "upkeep.rank_promotion",
            {
                "gang_name": active.gang_name,
                "name": active.name,
                "score": active.gf,
                "rank_name": ranks[active.nr - 1],
            },
        )
        ctx.apply(RankCommit(new_rank=active.nr))

    # --- 4040/4300-4370: debt check — the grace tick and the collectors fight ---
    # Ported from :4305's `kz(sp)=kz(sp)+(kz(sp)>0):ifkz(sp)=0goto4350`. See the
    # module docstring's COUNTER DIRECTION note: the tick counts DOWN.
    #
    # `active.debt` is safe to read here: nothing above this slot in the SAME upkeep
    # run touches debt (regen writes energy, the rank commit writes rank). Below this
    # point, `months`/`debt_amount` are tracked LOCALLY — ctx.apply only BUFFERS, so
    # re-reading ctx.state mid-flow would see pre-tick values.
    debt_amount = active.debt.amount
    months = active.debt.months
    if debt_amount != 0 or months != 0:
        # :4305's `+(kz(sp)>0)` — decrement ONLY while positive, so 0 is a fixed
        # point. That fixed point is exactly what makes a won fight recur every turn
        # (KTD-9): the counter never leaves 0, so every later turn re-enters :4350.
        if months > 0:
            months -= 1
            ctx.apply(DebtChange(amount=0, months=months))

        if months > 0:
            # :4306-4309 — still inside the grace period: warn and move on. The
            # printed figure is `kz(sp)+1` (:4308) — read AFTER the tick, so a
            # just-taken 6-month loan is reported as "6 monat(e)" on its first turn.
            yield ShowMessage(
                "upkeep.debt_warning",
                {"amount": debt_amount, "months": months + 1},
            )
        elif debt_amount != 0:
            # :4350-4370 — the grace period has expired. Guarded on a NONZERO debt so
            # a fully repaid player (:15075 leaves kr=0 AND kz=0) is never ambushed.
            #
            # The jail gate (:4040) is a read that trivially passes in-slice: jail is
            # declared-but-stubbed (KTD-7) and nothing can imprison a player, so the
            # not-jailed precondition is always true and is not re-encoded here.
            from engine.combat import setup_combat

            yield ShowMessage("upkeep.debt_collectors_intro")
            debt_params = ctx.state.config.formula_params
            combat_state = setup_combat(
                active.roster,
                # :4355 — bn$(0)="eintreiber":w=3:e=30:gz(0)=5:kf$="ks"
                enemy_count=debt_params["kdh_collectors_count"],
                enemy_weapon=debt_params["kdh_collectors_weapon"],
                enemy_vitality=debt_params["kdh_collectors_energie"],
                enemy_attrs=enemy_attrs(debt_params),
                enemy_name=_COLLECTOR_NAME,
                grid=_backdrop(_COLLECTORS_BACKDROP),
                equip=equipper(_weapon_stats()),
            )
            winner = yield StartCombat(
                sides=combat_state.sides,
                grid=combat_state.grid,
                rules=build_rules(),
                dir_memory=combat_state.dir_memory,
            )

            # Outcome narration (KTD-1: the invoking handler's job — _run_combat
            # yields no final screen). Shared with jobs.py/kdh.py's own fights.
            #
            # with_losses=False is a KNOWN DEVIATION (#50), not a faithful port.
            # The original prints the losses block for EVERY fight (:30500-30515,
            # reached unconditionally via :30106's goto30500). It is suppressed
            # here only because this is the one in-slice fight with 5 enemies
            # (gz(0)=5, :4355), and narrate_combat_outcome's per-side count is a
            # 1v1 shortcut that would print a wrong tally. Restoring it needs real
            # v(1)/v(2)-equivalent tallies from _run_combat.
            yield from narrate_combat_outcome(
                winner=winner,
                player_name=active.name,
                enemy_name=_COLLECTOR_NAME,
                with_losses=False,
            )

            if winner == 2:
                # :4365-4370 — lost: `ka(sp)=0:kr(sp)=0:kz(sp)=0`. The seizure takes
                # the cash the player holds AT THIS MOMENT. `active.ka` is still the
                # correct figure: no effect buffered earlier in this run moves money
                # except the shop-income/arms-deal slots, which run BELOW this one.
                yield ShowMessage("upkeep.debt_seized")
                ctx.apply(MoneyChange(-active.ka))
                ctx.apply(DebtClear())
            # :4355's `ifs=1thenreturn` — a WIN falls straight through: no seizure,
            # and crucially no debt relief either. The loan and its expired counter
            # both survive, so the collectors come back next turn and every turn
            # after, until the player repays at kdh or finally loses. This is
            # source-confirmed behaviour kept deliberately per KTD-9 — NOT a bug.

    # --- 4041-4420: shop income — ports mf-prg.bas:4041,4405-4410 --------------
    # ifkg(sp)<>0andkk(sp)<>0thengosub4400 (:4041). Re-read `active` is unnecessary:
    # nothing above this slot in the SAME upkeep run touches business.
    if active.business.shop_tile != 0 and active.business.shop_capital != 0:
        params = ctx.state.config.formula_params
        if ctx.rng.range(params["kdh_income_quiet_roll"]) != 0:
            # :4405 — 2-in-3 chance the loan business earns money this month.
            capital = active.business.shop_capital
            # :4410 — p = int(rnd(1)*kk(sp)/20 + kk(sp)/10) -> a continuous draw
            # (rnd(1) in [0,1)) scaled by a VARIABLE coefficient (capital), unlike a
            # fixed-bound roll (rng.hit). Ported as an exact discrete equivalent:
            # multiplying the whole expression by 20, `20p = int(2*capital +
            # t*capital)` for continuous t in [0,1) — substituting a discrete
            # rng.range(capital) draw for the continuous `t*capital` term (both are
            # uniform over the SAME achievable integer range, so the substitution
            # preserves the exact value set and per-value probability; verified by
            # simulation against the continuous source formula). Result: 10%-15% of
            # capital.
            income = (2 * capital + ctx.rng.range(capital)) // 20
            ctx.apply(MoneyChange(income))
            yield ShowMessage("upkeep.shop_income_earned", {"amount": income})
        else:
            # :4406 — 1-in-3 quiet month.
            yield ShowMessage("upkeep.shop_income_quiet")

    # --- 4060: arms deal — ports mf-prg.bas:31000-31051 ---------------------
    # iftp(sp)=4thengosub31000 (:4060). Re-read `active` is unnecessary: nothing above
    # this slot in the SAME upkeep run touches tip_target.
    if active.tip_target == ARMS_DEAL_TIP:
        # :31000 — tp(sp)=0 FIRST, before the roll: the clear must happen before the
        # loss/payout branch so a stake resolves EXACTLY ONCE (see module docstring).
        ctx.apply(TipClear())
        if ctx.rng.range(5) == 0:
            # :31050-31051 — 1-in-5 total loss; the stake is already spent (sunk cost),
            # so this branch applies no MoneyChange.
            yield ShowMessage("upkeep.arms_deal_lost")
        else:
            # :31005 — payout p = int(rnd(1)*9500)+5500 -> 5500..14999$.
            arms_params = ctx.state.config.formula_params
            payout = ctx.rng.hit(
                arms_params["pub_arms_deal_payout_min"],
                arms_params["pub_arms_deal_payout_max"],
            )
            ctx.apply(MoneyChange(payout))
            yield ShowMessage("upkeep.arms_deal_won", {"amount": payout})

    return []
