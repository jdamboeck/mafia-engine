"""The pub (Kneipe) handlers — U8 (alcohol trade + tips), U9 (recruit), U10 (job).

Ports all four of the pub's menu actions from ``mf-prg.bas:12000-12335``:

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
- ``pub.recruit`` (``12100-12175``) — recruit from the 30-candidate research pool.
  Rank + housing + crew-cap guards, an offer pool capped at 3 with a nobody-available
  roll, a per-candidate draw (reroll on already-hired/already-drawn), and a settle
  that pays the price, appends the hire to the roster at energy 5, and marks the
  candidate globally hired. See ``pub_recruit``'s own docstring for the full guard
  order and the mid-batch cap quirk (``:12145``).
- ``pub.job`` (``12300-12335``) — take a job. INVERTED rank guard vs. recruit's
  (``ra(sp) <= 3``, not ``>= 5``) — refusal is for players who have ALREADY grown
  too respectable for menial work. A 1-in-5 "nobody has work" roll, then one of
  four job types (bouncer/croupier/doorman/killer) with a fixed duration and a
  per-type pay roll; accepting stores the job via ``JobSet`` and force-ends the
  turn (``ms=0``, ``:12335``). The shift flow that later takes over the employed
  player's turn is ``data/game_configs/mafia_1920s/handlers/jobs.py`` (U10, ports
  ``25000-25560``), dispatched by the CALLER (the client's turn loop), not by
  this handler or by upkeep (the U3 seam).

Faithfulness notes
-------------------
- ALL game-balance numbers (stock/price/capacity/tip-price ranges) come from
  ``formula_params`` (KTD-10) — nothing here is a bare literal.
- Relational terms use the C64 ``true = -1`` evaluation (the #47 fidelity audit
  reversed the earlier, circularly-justified ``true = +1`` pin — see
  ``docs/solutions/architecture-patterns/basic-relational-boolean-is-plus-one-when-porting.md``).
  None of this module's ported expressions contains a relational factor, so the
  reversal changed nothing here; the note stays because a future addition to this
  file will need the right convention.
- KTD-7: touches only ``ctx.state`` (read-only), ``ctx.rng``, ``yield``, ``ctx.apply``,
  and this config's OWN ``..setup`` helpers.
- No content-specific events (KTD-6): outcomes are reconstructable from the committed
  effects + logged RNG draws.

Cancellability rule (KTD-1 feasibility)
----------------------------------------
None of ``pub.drink``/``pub.tip``/``pub.recruit`` uses a driver-level
``cancellable=True`` prompt: every "decline"/"nothing to buy"/"skip this candidate"
path in the source is a plain ``return`` (or, for recruit's per-candidate loop, a
``continue`` to the next offer) back to the main loop, which this port expresses as
a quiet Python ``return []``/``continue`` after the relevant
``PromptInt``/``Confirm`` answer — there is no "cancel back to an earlier menu"
shape here (unlike waf's nested weapon/gangster pickers), so no interaction needs
the driver's atomic-discard cancel path.

``ln`` seam
-----------
Read from the active player's ``last_location`` field, exactly as ``slw``/``waf`` do.
"""

from __future__ import annotations

from pathlib import Path

from engine.effects import (
    BarrelChange,
    GangsterMarkHired,
    JobSet,
    MoneyChange,
    MsChange,
    RosterAppend,
    TipClear,
    TipSet,
)
from engine.interactions import Confirm, PromptInt, ShowMessage
from engine.locations import register
from ..gangster import Gangster

from ..setup import load_gangster_candidates, load_vehicles, score_and_rank

_CONFIG_DIR = Path(__file__).resolve().parents[1]

__all__ = ["pub_drink", "pub_recruit", "pub_tip", "pub_job"]

#: The 30 female candidate ids (0-based; source ``fnwe(x)`` predicate, mf-prg.bas:12130
#: — BLOODY MARY(4)/JOSEFINE(14)/DOROTHY(27)/MA BAKER(30), 1-based in the source).
_FEMALE_CANDIDATE_IDS = frozenset({3, 13, 26, 29})

#: Crew cap: total roster length INCLUDING the boss at roster[0] (KTD-6). gz(sp)
#: counts the boss (mf-prg.bas:300 gz(i)=1 at setup, :4651 eviction resets to 1) so
#: "10 gangsters" means at most NINE hires -- mf-prg.bas:12105/12145.
_CREW_CAP = 10

#: Candidate offer pool ceiling (mf-prg.bas:12106's `ify>3theny=3`).
_MAX_OFFERS = 3

#: Pub tile that never has recruits available (mf-prg.bas:12107's `orln=3`).
_NO_RECRUIT_TILE = 3

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

#: Job type ids (jo(sp)), matching mf-prg.bas:12305's `x=int(rnd(1)*4)+1:onxgoto
#: 12306,12310,12315,12320` dispatch order. Shared with ``handlers/jobs.py``'s shift
#: flow, which dispatches on the SAME ids (mf-prg.bas:25010's `onjo(sp)goto
#: 25015,25100,25015,25200` — note type 1 AND 3 share one flow).
JOB_BOUNCER = 1
JOB_CROUPIER = 2
JOB_DOORMAN = 3
JOB_KILLER = 4

#: Job rank guard: the offer is available only at or below this rank (mf-prg.bas:12300
#: `ifra(sp)<4goto12302` — INVERTED vs. recruit's rank>=5 guard; a high-rank boss is
#: told to find something better, not offered the menial job).
_JOB_MAX_RANK = 3

#: Per-job-type (duration, pay-min, pay-max) formula_params key triples, in the
#: SAME order as the source's ON-GOTO dispatch (mf-prg.bas:12305-12322).
_JOB_PARAMS = {
    JOB_BOUNCER: ("job_bouncer_duration", "job_bouncer_pay_min", "job_bouncer_pay_max"),
    JOB_CROUPIER: ("job_croupier_duration", "job_croupier_pay_min", "job_croupier_pay_max"),
    JOB_DOORMAN: ("job_doorman_duration", "job_doorman_pay_min", "job_doorman_pay_max"),
    JOB_KILLER: ("job_killer_duration", "job_killer_pay_min", "job_killer_pay_max"),
}

#: The job-offer flavour text key per type (mf-prg.bas:12306-12321).
_JOB_OFFER_TEXT_KEYS = {
    JOB_BOUNCER: "locations.pub.job_offer_bouncer",
    JOB_CROUPIER: "locations.pub.job_offer_croupier",
    JOB_DOORMAN: "locations.pub.job_offer_doorman",
    JOB_KILLER: "locations.pub.job_offer_killer",
}


def _vehicles():
    """Load this config's vehicle table via the config's own loader (KTD-7).

    Mirrors ``waf.py``'s ``_weapons()`` pattern: read relative to this module's config
    directory, fresh per call (the config is frozen per game, so this is harmless).
    """
    return load_vehicles(_CONFIG_DIR / "entities" / "vehicles.yaml")


def _gangster_candidates():
    """Load the 30 recruit candidates (U9). Same fresh-per-call pattern as ``_vehicles()``."""
    return load_gangster_candidates(_CONFIG_DIR / "entities" / "gangsters.yaml")


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
        price = ctx.rng.hit(
            params["pub_alcohol_buy_price_min"], params["pub_alcohol_buy_price_max"]
        )

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
# pub.recruit (R5) — mf-prg.bas:12100-12175                                    #
# --------------------------------------------------------------------------- #
@register("pub.recruit")
def pub_recruit(ctx):
    """Recruit gangsters from the 30-candidate pool — ports ``mf-prg.bas:12100-12175``.

    Guards, in order (the shell ALSO gates entry on rank>4 and gang_size<10 for the
    menu-availability UX per KTD-8, but every guard is re-checked here so the handler
    is correct standalone and each denial's message/order is independently provable):

    1. ``:12100-12102`` — rank guard ``ra(sp) > 4``.
    2. ``:12103-12104`` — housing guard: the player must hold at least one of the 5
       apartment-tenancy slots (``uk(i)=sp`` for some ``i`` in 1..5).
    3. ``:12105`` — crew cap: ``gz(sp) == 10`` denies (roster length INCLUDING the
       boss at ``roster[0]``, KTD-6 — at most nine hires).
    4. ``:12106`` — offer pool ``y`` = count of the 30 candidates not yet globally
       hired, capped at 3.
    5. ``:12107`` — roll ``x`` in ``[0, y]``; ``x == 0`` OR the current pub tile is
       ``ln == 3`` -> nobody available.
    6. ``:12108-12175`` — per-candidate loop (``x`` iterations): draw a candidate id
       (reroll on already-hired OR already-drawn-this-batch), show the gendered
       intro + offer, confirm, afford-check, then settle (deduct price, append to
       roster at energy 5, mark the candidate globally hired) — showing the new
       gang-size tally. ``:12145``'s mid-batch cap re-check (hitting the cap
       DURING this batch, e.g. after a rank/multi-hire scenario) aborts the WHOLE
       remaining batch back to the pub menu rather than continuing to offer more.
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    ln = active.last_location  # ln seam (see module docstring)

    # :12100-12102 — rank guard.
    if active.rank <= 4:
        yield ShowMessage("locations.pub.rank_too_low", {"rank": active.rank})
        return []

    # :12103-12104 — housing guard: at least one of 5 apartment slots (uk(i)=sp).
    if not any(ctx.state.map.tenancy.get(i) == sp for i in range(1, 6)):
        yield ShowMessage("locations.pub.recruit_no_housing")
        return []

    # :12105 — crew cap (roster length INCLUDES the boss, KTD-6).
    if len(active.roster) == _CREW_CAP:
        yield ShowMessage("locations.pub.recruit_gang_full")
        return []

    candidates = _gangster_candidates()
    hired = ctx.state.flags.hired_gangsters

    # :12106 — offer pool: unhired candidates, capped at 3.
    pool = sum(1 for i in range(len(candidates)) if i not in hired)
    pool = min(pool, _MAX_OFFERS)

    # :12107 — roll x in [0, pool]; 0 or tile 3 -> nobody available.
    offered = ctx.rng.range(pool + 1)
    if offered == 0 or ln == _NO_RECRUIT_TILE:
        yield ShowMessage("locations.pub.recruit_nobody_available")
        return []

    # :12108-12175 — per-candidate loop. ``ctx.state`` never reflects effects applied
    # earlier in THIS SAME handler run (buffering only commits after the generator
    # returns, engine/interactions.py's Ctx.apply docstring) -- cash/roster-size must
    # be tracked locally across iterations, not re-read from ctx.state mid-loop.
    running_cash = active.ka
    running_roster_size = len(active.roster)
    drawn_this_batch: list[int] = []
    for _ in range(offered):
        # :12145 mid-batch cap re-check: a hire earlier in THIS batch may have
        # already filled the roster -- abort the rest of the batch rather than
        # keep offering (mirrors the source's `ifgz(sp)=10goto12005`).
        if running_roster_size == _CREW_CAP:
            return []

        # :12110-12113 — draw a candidate, reroll on already-hired or already-drawn.
        while True:
            candidate_id = ctx.rng.range(len(candidates))
            if candidate_id in hired or candidate_id in drawn_this_batch:
                continue
            break
        drawn_this_batch.append(candidate_id)

        candidate = candidates[candidate_id]
        female = candidate_id in _FEMALE_CANDIDATE_IDS
        pronoun = "sie" if female else "er"

        # :12115-12121 — gendered intro.
        yield ShowMessage(
            "locations.pub.recruit_intro_female" if female else "locations.pub.recruit_intro_male"
        )
        # :12130-12136 — name/description/price offer, then yes/no confirm.
        yield ShowMessage(
            "locations.pub.recruit_offer",
            {
                "pronoun": pronoun,
                "name": candidate["name"],
                "description": candidate["description"],
                "price": candidate["price"],
            },
        )
        if not (yield Confirm("locations.pub.recruit_confirm")):
            continue  # :12136 "n" -> skip to :12175, next candidate

        # :12140 — afford check.
        if running_cash < candidate["price"]:
            yield ShowMessage("system.not_enough_money")
            continue

        # :12160-12165 — settle: pay, append to roster at energy 5, mark hired.
        ctx.apply(MoneyChange(-candidate["price"]))
        ctx.apply(
            RosterAppend(
                gangster=Gangster(
                    name=candidate["name"],
                    weapon=candidate["weapon"],
                    energie=5,
                    kraft=candidate["kraft"],
                    intelligenz=candidate["intelligenz"],
                    brutalitaet=candidate["brutalitaet"],
                )
            )
        )
        ctx.apply(GangsterMarkHired(candidate_id=candidate_id))
        running_cash -= candidate["price"]
        running_roster_size += 1
        yield ShowMessage("locations.pub.recruit_hired", {"gang_size": running_roster_size})

    return []


# --------------------------------------------------------------------------- #
# pub.job (R6) — mf-prg.bas:12300-12335                                        #
# --------------------------------------------------------------------------- #
@register("pub.job")
def pub_job(ctx):
    """Take a job — ports ``mf-prg.bas:12300-12335``.

    Steps (faithful to the BASIC line block):

    1. ``:12300`` — rank guard, INVERTED vs. recruit's: ``ra(sp) < 4`` falls through
       to the offer; ``ra(sp) >= 4`` (i.e. rank > 3) is refused as "too respectable"
       (``:12301``). Spot-checked against the oracle (see this module's docstring
       cross-reference) — this is NOT the same shape as recruit's ``rank > 4`` guard.
    2. ``:12302`` — 1-in-5 nobody has work; else continue.
    3. ``:12305`` — roll job type 1-4 uniform (bouncer/croupier/doorman/killer, in
       this exact order — the source's ``ON x GOTO`` dispatch order).
    4. ``:12306-12322`` — show the type's flavour text; the duration ``jd(sp)`` is a
       fixed per-type literal, the pay ``p`` a per-type 500-wide uniform roll (both
       config data, KTD-10 — see ``_JOB_PARAMS``).
    5. ``:12330`` — show the pay, confirm; declining returns with NO state change.
    6. ``:12335`` — accept: ``JobSet`` stores type/pay/duration, ``ms=0`` FORCE-ENDS
       the turn (KTD-3's job-shift seam: the caller dispatches the shift flow at the
       NEXT turn start, this handler only records the acceptance and stops movement
       dead per the source's literal ``ms=0``, not a relative deduction).
    """
    sp = ctx.state.clock.active_player
    active = ctx.state.players[sp]
    params = ctx.state.config.formula_params

    # :12300 — rank guard, INVERTED vs. recruit's (rank <= 3 gets the offer).
    if active.rank > _JOB_MAX_RANK:
        yield ShowMessage("locations.pub.job_rank_too_high", {"rank": active.rank})
        return []

    # :12302 — 1-in-5 nobody has work.
    if ctx.rng.range(5) == 0:
        yield ShowMessage("locations.pub.job_nobody_available")
        return []

    # :12305 — roll job type 1-4 uniform, source dispatch order.
    job_type = ctx.rng.range(4) + 1
    duration_key, pay_min_key, pay_max_key = _JOB_PARAMS[job_type]
    duration = params[duration_key]
    pay = ctx.rng.hit(params[pay_min_key], params[pay_max_key])

    # :12306-12322 — type-specific offer text.
    yield ShowMessage(_JOB_OFFER_TEXT_KEYS[job_type])

    # :12330 — show the pay, then confirm; "n" -> quiet return, no state change.
    yield ShowMessage("locations.pub.job_pay_confirm", {"pay": pay})
    if not (yield Confirm("locations.pub.job_pay_confirm_prompt")):
        return []

    # :12335 — accept: store the job, force-end the turn (ms=0, not a relative spend).
    ctx.apply(JobSet(type=job_type, pending_pay=pay, months_left=duration))
    ctx.apply(MsChange(amount=-active.ms))
    yield ShowMessage("locations.pub.job_accepted")
    return []
