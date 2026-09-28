"""This game's own effects: the ``mafia_1920s`` state changes the engine does not name.

The engine keeps only generic effects (``engine/effects.py``); an effect that ports one
of this game's rules or writes this game's state lives here
(docs/design/engine-architecture.md, "Engine/config seam"). Each class registers under
its class name with :func:`engine.effects.register_effect` when this module is
imported, and the config package (``__init__.py``) imports it through its handlers, so
the registry holds these effects once :func:`engine.config_loader.load_game_config`
returns. The tag is the class name, so a save written before the move still loads.

Each ``apply`` rebuilds state through the engine's public helpers
(:func:`~engine.effects.target_index`, :func:`~engine.effects.update_player`) and this
config's value-map accessors (:mod:`.state`): the state these effects write lives in
the value maps ``state_schema.yaml`` declares, never in an engine class.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from engine.effects import (
    SCHEMA_VERSION,
    register_effect,
    set_player_value,
    target_index,
    update_player,
)
from engine.state import GameState

try:
    from .state import (
        Debt,
        Job,
        business,
        contraband,
        debt,
        mark_hired,
        rented_months,
        set_tenant,
        wanted,
        write,
    )
except ImportError:  # loaded bare (config dir on sys.path), as setup.py allows
    from state import (
        Debt,
        Job,
        business,
        contraband,
        debt,
        mark_hired,
        rented_months,
        set_tenant,
        wanted,
        write,
    )

__all__ = [
    "BarrelChange",
    "BribeMonthsChange",
    "DebtChange",
    "DebtClear",
    "GangsterMarkHired",
    "Jail",
    "JobClear",
    "JobSet",
    "MarkSet",
    "PendingRankReset",
    "RankCommit",
    "RentAccrue",
    "ScoreAndRank",
    "SetTenancy",
    "ShopChange",
    "TipClear",
    "TipSet",
]


@register_effect(consequence="score_and_rank")
@dataclass(frozen=True)
class ScoreAndRank:
    """Award score and recompute rank in one effect — the port of ``gosub 1160/1165``.

    ``gf = clamp(gf + amount*score_mult, 0, 100)`` then ``nr = int(gf/rank_divisor)+1``,
    computed from the CLAMPED ``gf``. Fusing the two avoids the ordering hazard a
    separate score-then-rank pair would face (rank must see the post-clamp ``gf``). The
    ``[0, 100]`` clamp is this game's score bound (``:1160``/``:1161``). ``amount`` is
    the raw reward
    ``x``; ``score_mult`` is ``x8`` (``formula_params["score_mult"]``, set at setup).
    ``rank_divisor`` (11.1) is a config parameter, NOT hardcoded. Writes the pending
    rank ``nr`` value (per ``:1165``).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: float
    rank_divisor: float
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        p = state.players[idx]
        # gf += amount*x8, clamped to the intrinsic [0,100] gf domain (mf-prg.bas:1160-1161).
        # :1160 `gf(sp)=gf(sp)+(x*x8)`, then `gf(sp)>100` / :1161 `gf(sp)<0` clamp it.
        score_mult = state.config.formula_params["score_mult"]
        gf = max(0.0, min(100.0, p.gf + self.amount * score_mult))
        # nr recomputed from the CLAMPED gf (mf-prg.bas:1165); divisor is config data.
        # :1165 `nr(sp)=int(gf(sp)/11.1)+1`.
        state = update_player(state, player=idx, gf=gf)
        return set_player_value(state, "nr", int(gf / self.rank_divisor) + 1, player=idx)


@register_effect()
@dataclass(frozen=True)
class SetTenancy:
    """Set tenancy of within-location tile ``ln`` to the target player: ``uk(ln)=sp``.

    Ports the tenancy assignment at ``mf-prg.bas:10040`` (the slw rent block). ``ln``
    is the within-location tile index (1..9); the stored value is the resolved TARGET
    player index (explicit ``player`` else the active player, per the module's targeting
    convention). Writes the global ``tenancy.<ln>`` value.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    ln: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        # uk(ln) = sp (mf-prg.bas:10040).
        return set_tenant(state, self.ln, idx)


@register_effect()
@dataclass(frozen=True)
class RentAccrue:
    """Add ``months`` to the target player's prepaid rented-months ``um``: ``um(sp)+=x``.

    Ports the rented-months accrual at ``mf-prg.bas:10040`` — the player prepays ``x``
    months of rent. Adds to the target player's ``rented_months`` value. ``months`` is
    signed: the turn-start countdown at ``mf-prg.bas:4046`` ``um(sp)=um(sp)-1`` is a
    ``RentAccrue(-1)``.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    months: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        # um(sp) += x (mf-prg.bas:10040)
        rented = rented_months(state.players[idx]) + self.months
        return set_player_value(state, "rented_months", rented, player=idx)


@register_effect()
@dataclass(frozen=True)
class RankCommit:
    """Set the target player's committed rank ``rank`` (``ra(sp)``) to ``nr``.

    Ports the rank-promotion commit (``mf-prg.bas:4030``:
    ``ifra(sp)<>nr(sp)thenra(sp)=nr(sp):gosub4200``) — the SECOND half of the two-step
    rank system: :class:`ScoreAndRank` already recomputes the PENDING next-rank counter
    ``nr`` (a value-map key) from ``gf`` on every score award, but ``Player.rank`` (the value guards
    and prices actually read, e.g. ``waf.py``'s ``active.rank >= 5``) only moves when this
    effect commits it. The caller (the upkeep handler) is responsible for checking
    ``rank != nr`` and showing the promotion screen BEFORE applying this — the effect
    itself unconditionally sets ``rank = new_rank`` (an unconditional set is simpler and
    still faithful, since the caller never applies it when they are already equal).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    new_rank: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        # ra(sp) = nr(sp) (mf-prg.bas:4030) — the caller decides WHEN (rank != nr).
        return update_player(state, player=idx, rank=self.new_rank)


@register_effect()
@dataclass(frozen=True)
class PendingRankReset:
    """Set the target player's pending rank ``nr`` to its committed rank: ``nr(sp)=ra(sp)``.

    Ports the turn start's ``mf-prg.bas:1012``
    ``ms=tr(tm(sp)):nr(sp)=ra(sp):ll(sp)=0``. :class:`RankCommit` (``:4030``) runs
    just before it in the same turn start and leaves the two equal, so in play this
    changes nothing; the turn hook applies it only when they differ.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        return set_player_value(state, "nr", state.players[idx].rank, player=idx)


@register_effect()
@dataclass(frozen=True)
class Jail:
    """Set the target player's jail sentence to ``months``: ``gs(sp)=months``.

    Jail months are this game's state (``wanted.jail_months``, the source's ``gs(sp)``).
    The arrest sets the sentence outright (``mf-prg.bas:26045``,
    ``gs(sp)=int(ra(sp)/2+.5)``), so this is an absolute set, not a delta. The jail
    skip counts it down one month per skipped turn (``:1500``).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    months: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        new_wanted = replace(wanted(state.players[idx]), jail_months=self.months)
        return write(state, new_wanted, player=idx)


@register_effect()
@dataclass(frozen=True)
class BribeMonthsChange:
    """Add ``amount`` (signed) to the target player's chief-bribe months ``pl(sp)``.

    ``pol``'s chief bribe adds ``x+1`` (``mf-prg.bas:21020``
    ``pl(sp)=pl(sp)+x+1``), which is negative for a month count below -1, and upkeep
    takes one a month while it is positive (``:4050`` ``pl(sp)=pl(sp)+(pl(sp)>0)``).
    The months live in ``wanted.bribe_months``.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        current = wanted(state.players[idx])
        new_wanted = replace(current, bribe_months=current.bribe_months + self.amount)
        return write(state, new_wanted, player=idx)


@register_effect()
@dataclass(frozen=True)
class DebtChange:
    """Add ``amount`` (signed) to the target player's debt ``kr(sp)``, and set the
    grace-counter ``months`` (``kz(sp)``) alongside it.

    Applied by the kdh loan-shark borrow/repay handlers. Carries both fields in one
    effect because the source sets them together at every kdh call site (borrow
    :15030 sets ``kr(sp)=kr(sp)+x`` and ``kz(sp)=6`` in the same line; partial
    repayment :15065 decrements ``kr`` only, leaving ``months`` untouched — pass
    ``months=None`` for that case) — see :class:`.state.Debt` for the confirmed
    field semantics.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    months: int | None = None  # None = leave the grace counter unchanged
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        current = debt(state.players[idx])
        # kr(sp) += amount (mf-prg.bas:15030 borrow, :15065 repay). months is None on
        # a partial repay (kz(sp) untouched); borrow
        # and full-repay callers pass an explicit value alongside this effect (full
        # repay's kz=0 reset is DebtClear, applied as a SEPARATE effect by the caller).
        new_debt = replace(current, amount=current.amount + self.amount)
        if self.months is not None:
            new_debt = replace(new_debt, months=self.months)
        return write(state, new_debt, player=idx)


@register_effect()
@dataclass(frozen=True)
class DebtClear:
    """Zero the target player's debt AND its grace counter in one step.

    Ports the full-repayment reset (``mf-prg.bas:15075``, ``kz(sp)=0`` once ``kr(sp)``
    reaches 0 — kdh's repay handler applies this ALONGSIDE the final ``DebtChange`` that
    zeros ``kr``) and is also the vocabulary of the loan-default penalty (``:4370``,
    ``kr(sp)=0:kz(sp)=0``). A dedicated clear (rather than a
    ``DebtChange`` computed to exactly cancel the balance) keeps both loan-shark exit
    paths self-documenting in the replay log.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        # kr(sp)=0:kz(sp)=0 (mf-prg.bas:15075 full repayment).
        return write(state, Debt(amount=0, months=0), player=idx)


@register_effect()
@dataclass(frozen=True)
class ShopChange:
    """Set the target player's owned shop ``tile`` and/or its ``capital`` delta.

    Applied by the kdh buy/sell/capital-adjust handlers. Targets
    the ``business.*`` values (:class:`.state.Business`) —
    ``tile`` sets ``shop_tile`` (``None`` leaves it unchanged; the sentinel 0 means
    "no shop", per the field's own docstring; passing 0 explicitly clears ownership
    on a sale), ``capital_delta`` adds to ``shop_capital`` (``None`` = no change).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    tile: int | None = None
    capital_delta: int | None = None
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        new_business = business(state.players[idx])
        if self.tile is not None:
            # kg(sp)=ln (buy, mf-prg.bas:15120) or kg(sp)=0 (sell, :15155).
            new_business = replace(new_business, shop_tile=self.tile)
        if self.capital_delta is not None:
            # kk(sp) += capital_delta (fund/income, mf-prg.bas:15220, :4410).
            new_business = replace(
                new_business, shop_capital=new_business.shop_capital + self.capital_delta
            )
        return write(state, new_business, player=idx)


@register_effect()
@dataclass(frozen=True)
class BarrelChange:
    """Add ``amount`` (signed) to the target player's alcohol barrel stock ``ta(sp)``.

    Applied by the pub alcohol trade. Targets
    :attr:`.state.Contraband.alcohol_barrels`
    (mf-prg.bas:12035 buy, :12075 sell).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    amount: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        current = contraband(state.players[idx])
        # ta(sp) += amount (mf-prg.bas:12035 buy, :12075 sell).
        new_contraband = replace(current, alcohol_barrels=current.alcohol_barrels + self.amount)
        return write(state, new_contraband, player=idx)


@register_effect()
@dataclass(frozen=True)
class MarkSet:
    """Set or clear the target player's marks, the two bits of ``ag(sp)``.

    ``fake_papers`` is bit 1, the passport (set by ble ``:22020`` ``ag(sp)=ag(sp)or1``,
    cleared by the upkeep decay ``:4055`` ``ag(sp)=ag(sp)and254``); ``counterfeit`` is
    bit 2 (set ``:22120`` ``ag(sp)=ag(sp)or2``, cleared ``:4056`` ``ag(sp)=ag(sp)and253``).
    ``None`` leaves that mark as it is. Setting a held mark or clearing a clear one
    changes nothing, as the bit operations do.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    fake_papers: bool | None = None
    counterfeit: bool | None = None
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        marks = contraband(state.players[idx])
        if self.fake_papers is not None:
            marks = replace(marks, fake_papers=int(self.fake_papers))
        if self.counterfeit is not None:
            marks = replace(marks, counterfeit=int(self.counterfeit))
        return write(state, marks, player=idx)


@register_effect()
@dataclass(frozen=True)
class TipSet:
    """Set the target player's rolled heist tip type ``tp(sp)``.

    Applied by the pub tip flow. Targets the ``tip_target`` value
    (mf-prg.bas:12225-12226:
    the tip roll ``tp(sp)=int(rnd(1)*5)+1`` (1-5) dispatching to one of five heist-rumour
    texts).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    tip_type: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        # tp(sp) = tip_type (mf-prg.bas:12225-12226).
        return set_player_value(state, "tip_target", self.tip_type, player=idx)


@register_effect()
@dataclass(frozen=True)
class TipClear:
    """Clear the target player's rolled heist tip (``tp(sp)=0``).

    The counterpart to :class:`TipSet` — a used or expired tip resets ``tip_target`` to
    0 (no tip held). Applied from both ``pub.tip``'s tip-4 decline/broke paths
    and ``upkeep.py``'s arms-deal slot (the ``tp(sp)=0`` at ``mf-prg.bas:31000``,
    applied FIRST so a stake resolves exactly once — see ``handlers/upkeep.py``).
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        # tp(sp) = 0 (mf-prg.bas:31000 arms-deal resolve; also the tip4 decline/broke
        # paths in pub.tip).
        return set_player_value(state, "tip_target", 0, player=idx)


@register_effect()
@dataclass(frozen=True)
class JobSet:
    """Set the target player's accepted job: ``type``/``pending_pay``/``months_left``.

    Applied by the pub job-accept flow. Ports ``mf-prg.bas:12335``: ``jo(sp)=x:jl(sp)=p`` (plus the
    per-job-type ``jd(sp)`` duration set earlier at :12308/:12311/:12316/:12322) —
    one effect since the source sets them as a unit when a job is accepted. Targets
    :class:`.state.Job`.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    type: int
    pending_pay: int
    months_left: int
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        # jo(sp)=type : jl(sp)=pending_pay : jd(sp)=months_left (mf-prg.bas:12335, plus
        # the per-type jd(sp) set earlier at :12308/:12311/:12316/:12322).
        new_job = Job(
            type=self.type,
            pending_pay=self.pending_pay,
            months_left=self.months_left,
        )
        return write(state, new_job, player=idx)


@register_effect()
@dataclass(frozen=True)
class JobClear:
    """Clear the target player's job (``jo(sp)=0``).

    Ports the job-quit sites (mf-prg.bas:25560 completion, :25510 failed-shift-fight abort,
    :26080 jail commit forces ``jo(sp)=0``) — all zero the job the same way, so one
    effect covers every call site.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    player: int | None = None

    def apply(self, state: GameState) -> GameState:
        idx = target_index(state, self.player)
        # jo(sp)=0 (mf-prg.bas:25560 completion, :25510 failed shift fight, :26080
        # jail commit). A full reset (not just type=0) so a
        # cleared job never leaks a stale pending_pay/months_left into a future read.
        return write(state, Job(), player=idx)


@register_effect()
@dataclass(frozen=True)
class GangsterMarkHired:
    """Add ``candidate_id`` to the GLOBAL (not per-player) hired-candidates set.

    Ports ``sg(g(i))=1`` (``mf-prg.bas:12165``): once ANY player
    hires candidate ``candidate_id`` (0-based; the source's ``g(i)`` is 1-based),
    every player's future recruit roll skips them (:12110's ``ifsg(g(i))goto12110``
    reroll-on-hired guard). Idempotent by construction: the set is one bool per
    candidate (``hired.<id>``), so marking an already-hired candidate changes nothing.
    """

    SCHEMA_VERSION = SCHEMA_VERSION
    candidate_id: int

    def apply(self, state: GameState) -> GameState:
        # sg(g(i))=1 (mf-prg.bas:12165) — GLOBAL, not per-player. Setting a flag that
        # is already set changes nothing, so marking twice is harmless.
        return mark_hired(state, self.candidate_id)
