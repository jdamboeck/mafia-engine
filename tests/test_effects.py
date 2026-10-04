"""Contract tests for the Effect API + pure application + driver commit integration (U5).

Written proof-first: this file is authored BEFORE ``engine/effects.py`` exists, run to
observe a red failure (import error), then the module is implemented to green.

Effects are typed, frozen, serializable pure-data records that double as replay events
(KTD-3, KTD-6). ``apply(state, effect) -> GameState`` is PURE: the state graph is frozen,
so it functionally rebuilds a new state — the input is never mutated. Effects flow through
the U4 driver's buffer: ``ctx.apply`` enqueues; the buffer commits atomically (folding
``apply`` over it) on clean completion and is discarded on cancel.
"""

from __future__ import annotations

import dataclasses

import pytest

from engine.effects import (
    SCHEMA_VERSION,
    AssignWeapon,
    CommitResult,
    EnergyChange,
    FlagSet,
    MoneyChange,
    MsChange,
    RosterAppend,
    RosterTruncate,
    ScoreChange,
    SetEntryContext,
    SetPosition,
    SpawnFighter,
    StatChange,
    StatChangeCapped,
    Teleport,
    apply,
    commit,
)
from data.game_configs.mafia_1920s.effects import (
    BarrelChange,
    DebtChange,
    DebtClear,
    GangsterMarkHired,
    Jail,
    JobClear,
    JobSet,
    RankCommit,
    RentAccrue,
    ScoreAndRank,
    SetTenancy,
    ShopChange,
    TipClear,
    TipSet,
)
from engine.interactions import CANCEL, PromptInt, run
from engine.state import Clock, Config, Fighter, GameState, Player, tuple_replace
from data.game_configs.mafia_1920s.state import Business, Debt, Job
from data.game_configs.mafia_1920s.gangster import Gangster
from tests.helpers import with_tenancy
import data.game_configs.mafia_1920s.state as game


# --------------------------------------------------------------------------- #
# State fixtures                                                              #
# --------------------------------------------------------------------------- #
def make_state(active_player: int = 0):
    """A two-player state; active player is index 0 unless overridden."""
    p0 = Player(
        name="p0",
        ka=5000,
        gf=50.0,
        po=18,
        ms=3,
        roster=(Gangster(name="g0", energie=5, kraft=20, intelligenz=30, brutalitaet=10),),
    )
    p1 = Player(
        name="p1",
        ka=1000,
        gf=10.0,
        po=100,
        ms=2,
        roster=(Gangster(name="g1", energie=5, kraft=15, intelligenz=25, brutalitaet=5),),
    )
    return GameState(
        players=(p0, p1),
        clock=Clock(active_player=active_player, player_count=2),
        config=Config(formula_params={"score_mult": 1.0}),  # x8, set by new_game
        values=game.SCHEMA.global_defaults(),
    )


def _with_second_gangster(state, gangster):
    """Return ``state`` with ``gangster`` appended to player 0's roster.

    The frozen graph has no ``roster.append`` — rebuild instead.
    """
    p0 = state.players[0]
    return dataclasses.replace(
        state,
        players=(dataclasses.replace(p0, roster=p0.roster + (gangster,)),)
        + tuple(state.players[1:]),
    )


# --------------------------------------------------------------------------- #
# Effect schema — frozen, versioned, pure data                               #
# --------------------------------------------------------------------------- #
def test_effects_are_frozen_and_versioned():
    e = MoneyChange(-500)
    assert e.SCHEMA_VERSION == 2
    assert SCHEMA_VERSION == 2
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.amount = 1  # pyright: ignore[reportAttributeAccessIssue]  # the write is the test: it must raise


# --------------------------------------------------------------------------- #
# money_change + purity                                                       #
# --------------------------------------------------------------------------- #
def test_money_change_and_purity():
    state = make_state()
    out = apply(state, MoneyChange(-500))

    assert out.players[0].ka == 4500  # delta applied on the rebuilt state
    assert state.players[0].ka == 5000  # ORIGINAL untouched (purity)
    assert out is not state  # a new object is returned (no aliasing)
    assert out.players[0] is not state.players[0]  # rebuilt, not shared references


# --------------------------------------------------------------------------- #
# score_change — a caller-supplied [floor, cap] bound                          #
# --------------------------------------------------------------------------- #
def test_score_change_caps_at_100():
    state = make_state()  # gf starts at 50
    out = apply(state, ScoreChange(200.0, floor=0.0, cap=100.0))
    assert out.players[0].gf == 100.0


def test_score_change_floors_at_0():
    state = make_state()  # gf starts at 50
    out = apply(state, ScoreChange(-200.0, floor=0.0, cap=100.0))
    assert out.players[0].gf == 0.0


def test_score_change_normal_delta_lands_exactly():
    state = make_state()  # gf starts at 50
    out = apply(state, ScoreChange(25.0, floor=0.0, cap=100.0))
    assert out.players[0].gf == 75.0


def test_score_change_unclamped_leaves_0_to_100():
    """No bound is the weapon-buy score (``:13065``/``:13072``/``:13073``),
    which changes gf with no bound: only ``gosub 1160-1161`` clamps."""
    state = make_state()  # gf starts at 50
    assert apply(state, ScoreChange(60.0, floor=None, cap=None)).players[0].gf == 110.0
    assert apply(state, ScoreChange(-53.5, floor=None, cap=None)).players[0].gf == -3.5


def test_score_change_cannot_be_built_without_choosing_the_bound():
    """A port of a direct ``gf(sp)=...`` line that writes ``ScoreChange(x)`` must not
    silently get a bound no such line has, nor lose one a ``gosub 1160`` port needs:
    the constructor refuses a missing bound."""
    with pytest.raises(TypeError, match="floor"):
        ScoreChange(1.0)  # pyright: ignore[reportCallIssue]  # the missing bound is the test: it must raise
    with pytest.raises(TypeError, match="cap"):
        ScoreChange(1.0, floor=0.0)  # pyright: ignore[reportCallIssue]  # the missing cap is the test: it must raise
    with pytest.raises(TypeError):
        ScoreChange(1.0, 0, 0.0, 100.0)  # pyright: ignore[reportCallIssue]  # a positional bound is the test: it must raise


def test_score_change_bounds_one_side_only():
    state = make_state()  # gf starts at 50
    assert apply(state, ScoreChange(60.0, floor=0.0, cap=None)).players[0].gf == 110.0
    assert apply(state, ScoreChange(-60.0, floor=None, cap=100.0)).players[0].gf == -10.0


def test_score_and_rank_clamps_an_out_of_range_gf():
    """A gf left above 100 by an unclamped change is clamped at the next ``:1160``."""
    state = apply(make_state(), ScoreChange(51.0, floor=None, cap=None))  # gf 101
    out = apply(state, ScoreAndRank(amount=0.0, rank_divisor=11.1))
    assert out.players[0].gf == 100.0
    assert game.next_rank(out.players[0]) == 10


# --------------------------------------------------------------------------- #
# ms_change / teleport                                                        #
# --------------------------------------------------------------------------- #
def test_ms_change_applies_and_reaches_zero_unclamped():
    state = make_state()  # ms starts at 3
    out = apply(state, MsChange(-3))
    assert out.players[0].ms == 0  # ms may legitimately hit 0 to force turn end


def test_ms_change_can_go_negative_not_clamped():
    state = make_state()  # ms starts at 3
    out = apply(state, MsChange(-5))
    assert out.players[0].ms == -2  # ms is not clamped


def test_teleport_sets_absolute_cell():
    state = make_state()
    out = apply(state, Teleport(569))
    assert out.players[0].po == 569


# --------------------------------------------------------------------------- #
# set_position / set_entry_context — primitive movement effects (T2)          #
# --------------------------------------------------------------------------- #
def test_set_position_sets_absolute_cell():
    state = make_state()
    out = apply(state, SetPosition(569))
    assert out.players[0].po == 569


def test_set_position_does_not_mutate_input_state():
    state = make_state()  # po starts at 18
    apply(state, SetPosition(569))
    assert state.players[0].po == 18  # ORIGINAL untouched (purity)


def test_set_position_explicit_player_targeting():
    state = make_state()  # active is 0; p1 po=100
    out = apply(state, SetPosition(861, player=1))
    assert out.players[1].po == 861  # players[1] targeted
    assert out.players[0].po == 18  # active player untouched


def test_set_position_default_targets_active_player():
    state = make_state(active_player=1)
    out = apply(state, SetPosition(861))
    assert out.players[1].po == 861
    assert out.players[0].po == 18


def test_set_entry_context_sets_both_fields():
    state = make_state()
    out = apply(state, SetEntryContext(la=7, ln=3))
    assert out.players[0].last_la == 7
    assert out.players[0].last_location == 3


def test_set_entry_context_does_not_mutate_input_state():
    state = make_state()
    apply(state, SetEntryContext(la=7, ln=3))
    assert state.players[0].last_la == 0  # ORIGINAL untouched (purity)
    assert state.players[0].last_location == 0


def test_set_entry_context_explicit_player_targeting():
    state = make_state()  # active is 0
    out = apply(state, SetEntryContext(la=12, ln=9, player=1))
    assert out.players[1].last_la == 12
    assert out.players[1].last_location == 9
    assert out.players[0].last_la == 0  # active player untouched
    assert out.players[0].last_location == 0


def test_set_entry_context_default_targets_active_player():
    state = make_state(active_player=1)
    out = apply(state, SetEntryContext(la=12, ln=9))
    assert out.players[1].last_la == 12
    assert out.players[1].last_location == 9
    assert out.players[0].last_la == 0


def test_movement_effects_work_through_commit():
    state = make_state()
    result = commit(state, [SetPosition(569), SetEntryContext(la=5, ln=2)])

    assert result.state.players[0].po == 569
    assert result.state.players[0].last_la == 5
    assert result.state.players[0].last_location == 2
    # input state untouched
    assert state.players[0].po == 18
    assert state.players[0].last_la == 0
    assert state.players[0].last_location == 0


# --------------------------------------------------------------------------- #
# stat_change                                                                 #
# --------------------------------------------------------------------------- #
def test_stat_change_adjusts_named_stat():
    state = make_state()  # g0 kraft starts at 20
    out = apply(state, StatChange("kraft", 5))
    assert out.players[0].roster[0].attrs["kraft"] == 25


def test_stat_change_unknown_stat_raises_value_error():
    state = make_state()
    with pytest.raises(ValueError):
        apply(state, StatChange("charisma", 5))


def test_stat_change_rejects_energie_which_is_the_vitality_slot():
    """``energie`` is the ``vitality`` SLOT (A4), not an ``attrs`` key — so it is not a
    valid ``StatChange`` target. Before the fix it passed validation (it was still in
    ``_STAT_NAMES``) and then ``KeyError``ed on ``attrs["energie"]``; the vitality
    resource is changed via ``EnergyChange`` instead.
    """
    state = make_state()
    with pytest.raises(ValueError, match="energie"):
        apply(state, StatChange("energie", 5))


def test_stat_change_validates_against_the_config_declared_names():
    """The valid targets are whatever the config declared, not an engine list.

    Re-declaring replaces the set (a config reload does exactly this), so a name the
    new declaration drops is refused and a name it adds is accepted.
    """
    from engine.effects import STAT_NAMES, declare_stat_names

    before = frozenset(STAT_NAMES)
    try:
        declare_stat_names(["kraft"])
        state = make_state()
        assert apply(state, StatChange("kraft", 1)).players[0].roster[0].attrs["kraft"] == 21
        with pytest.raises(ValueError, match="brutalitaet"):
            apply(state, StatChange("brutalitaet", 1))
        with pytest.raises(ValueError, match="brutalitaet"):
            apply(state, StatChangeCapped("brutalitaet", 1, cap=99))
    finally:
        declare_stat_names(before)


def test_stat_change_targets_the_right_gangster_index():
    state = _with_second_gangster(make_state(), Gangster(name="g0b", brutalitaet=1))
    out = apply(state, StatChange("brutalitaet", 7, gangster=1))
    assert out.players[0].roster[1].attrs["brutalitaet"] == 8
    assert out.players[0].roster[0].attrs["brutalitaet"] == 10  # index 0 untouched


# --------------------------------------------------------------------------- #
# stat_change_capped (U3) — a StatChange variant clamping to [floor, cap]      #
# --------------------------------------------------------------------------- #
def test_stat_change_capped_clamps_at_cap():
    state = make_state()  # g0 kraft starts at 20
    out = apply(state, StatChangeCapped("kraft", 90, cap=99))
    assert out.players[0].roster[0].attrs["kraft"] == 99  # 20+90=110 -> clamped to 99


def test_stat_change_capped_normal_raise_unclamped():
    state = make_state()  # g0 kraft starts at 20
    out = apply(state, StatChangeCapped("kraft", 5, cap=99))
    assert out.players[0].roster[0].attrs["kraft"] == 25  # under cap -> unclamped


def test_stat_change_capped_floors_at_zero_by_default():
    state = make_state()  # g0 kraft starts at 20
    out = apply(state, StatChangeCapped("kraft", -50, cap=99))
    assert out.players[0].roster[0].attrs["kraft"] == 0  # 20-50=-30 -> floored at 0


def test_stat_change_capped_cap_is_a_parameter_not_hardcoded():
    # Config-boundary (KTD-10): pass cap=50 -> clamps at 50, proving no hardcoded 99.
    state = make_state()  # g0 kraft starts at 20
    out = apply(state, StatChangeCapped("kraft", 90, cap=50))
    assert out.players[0].roster[0].attrs["kraft"] == 50


def test_stat_change_capped_unknown_stat_raises_value_error():
    state = make_state()
    with pytest.raises(ValueError):
        apply(state, StatChangeCapped("charisma", 5, cap=99))


def test_stat_change_capped_bad_gangster_index_raises_indexerror():
    state = make_state()
    with pytest.raises(IndexError):
        apply(state, StatChangeCapped("kraft", 5, cap=99, gangster=9))


# --------------------------------------------------------------------------- #
# energy_change (U3) — turn-start regen, real application                     #
# --------------------------------------------------------------------------- #
def test_energy_change_applies_unclamped_under_cap():
    state = make_state()  # g0 energie starts at 5
    out = apply(state, EnergyChange(amount=3, cap=99))
    assert out.players[0].roster[0].vitality == 8


def test_energy_change_clamps_at_cap():
    state = make_state()  # g0 energie starts at 5
    out = apply(state, EnergyChange(amount=50, cap=10))
    assert out.players[0].roster[0].vitality == 10


def test_energy_change_floors_at_zero():
    state = make_state()  # g0 energie starts at 5
    out = apply(state, EnergyChange(amount=-50, cap=99))
    assert out.players[0].roster[0].vitality == 0


def test_energy_change_bad_gangster_index_raises_indexerror():
    state = make_state()
    with pytest.raises(IndexError):
        apply(state, EnergyChange(amount=1, cap=99, gangster=9))


def test_energy_change_purity():
    state = make_state()
    out = apply(state, EnergyChange(amount=3, cap=99))
    assert state.players[0].roster[0].vitality == 5  # original untouched
    assert out is not state


# --------------------------------------------------------------------------- #
# rank_commit (U3) — ra(sp) = nr(sp), unconditional set (caller gates it)      #
# --------------------------------------------------------------------------- #
def test_rank_commit_sets_rank():
    state = make_state()  # rank defaults to 1
    out = apply(state, RankCommit(new_rank=4))
    assert out.players[0].rank == 4


def test_rank_commit_targets_explicit_player():
    state = make_state()
    out = apply(state, RankCommit(new_rank=7, player=1))
    assert out.players[1].rank == 7
    assert out.players[0].rank == 1  # untouched


def test_rank_commit_purity():
    state = make_state()
    out = apply(state, RankCommit(new_rank=4))
    assert state.players[0].rank == 1  # original untouched
    assert out is not state


# --------------------------------------------------------------------------- #
# spawn_fighter (U4) — combat setup, real application                         #
# --------------------------------------------------------------------------- #
def test_spawn_fighter_appends_to_side_1():
    state = make_state()
    f = Fighter(
        name="capone", weapon=1, vitality=5, attrs={"kraft": 15, "brutalitaet": 20}, position=129
    )
    out = apply(state, SpawnFighter(fighter=f, side=1))
    assert out.combat.sides == ((f,), ())


def test_spawn_fighter_appends_to_side_2():
    state = make_state()
    f = Fighter(
        name="thug", weapon=0, vitality=5, attrs={"kraft": 30, "brutalitaet": 30}, position=111
    )
    out = apply(state, SpawnFighter(fighter=f, side=2))
    assert out.combat.sides == ((), (f,))


def test_spawn_fighter_appends_in_order():
    state = make_state()
    f1 = Fighter(name="a", position=129)
    f2 = Fighter(name="b", position=210)
    out = apply(state, SpawnFighter(fighter=f1, side=1))
    out = apply(out, SpawnFighter(fighter=f2, side=1))
    assert out.combat.sides[0] == (f1, f2)


def test_spawn_fighter_bad_side_raises_value_error():
    state = make_state()
    with pytest.raises(ValueError):
        apply(state, SpawnFighter(fighter=Fighter(), side=3))


def test_spawn_fighter_purity():
    state = make_state()
    f = Fighter(name="capone", position=129)
    out = apply(state, SpawnFighter(fighter=f, side=1))
    assert state.combat.sides == ((), ())  # original untouched
    assert out is not state


# --------------------------------------------------------------------------- #
# barrel_change (U8) — pub alcohol trade, real application                    #
# --------------------------------------------------------------------------- #
def test_barrel_change_adds_to_alcohol_stock():
    state = make_state()  # contraband.alcohol_barrels defaults to 0
    out = apply(state, BarrelChange(10))
    assert game.contraband(out.players[0]).alcohol_barrels == 10


def test_barrel_change_negative_amount_subtracts():
    state = apply(make_state(), BarrelChange(10))
    out = apply(state, BarrelChange(-4))
    assert game.contraband(out.players[0]).alcohol_barrels == 6


def test_barrel_change_targets_explicit_player():
    state = make_state()
    out = apply(state, BarrelChange(5, player=1))
    assert game.contraband(out.players[1]).alcohol_barrels == 5
    assert game.contraband(out.players[0]).alcohol_barrels == 0  # untouched


def test_barrel_change_purity():
    state = make_state()
    out = apply(state, BarrelChange(10))
    assert game.contraband(state.players[0]).alcohol_barrels == 0  # original untouched
    assert out is not state


# --------------------------------------------------------------------------- #
# tip_set / tip_clear (U8) — pub tip flow + arms-deal resolution               #
# --------------------------------------------------------------------------- #
def test_tip_set_stores_the_tip_type():
    state = make_state()  # tip_target defaults to 0
    out = apply(state, TipSet(tip_type=4))
    assert game.tip_target(out.players[0]) == 4


def test_tip_set_targets_explicit_player():
    state = make_state()
    out = apply(state, TipSet(tip_type=2, player=1))
    assert game.tip_target(out.players[1]) == 2
    assert game.tip_target(out.players[0]) == 0  # untouched


def test_tip_clear_resets_to_zero():
    state = apply(make_state(), TipSet(tip_type=4))
    out = apply(state, TipClear())
    assert game.tip_target(out.players[0]) == 0


def test_tip_clear_targets_explicit_player():
    state = apply(make_state(), TipSet(tip_type=3, player=1))
    out = apply(state, TipClear(player=1))
    assert game.tip_target(out.players[1]) == 0


def test_tip_set_and_clear_purity():
    state = make_state()
    out = apply(state, TipSet(tip_type=4))
    assert game.tip_target(state.players[0]) == 0  # original untouched
    assert out is not state


# --------------------------------------------------------------------------- #
# roster_append (U9) — pub recruit flow, real application                     #
# --------------------------------------------------------------------------- #
def test_roster_append_adds_after_the_boss():
    state = make_state()  # roster == (g0,) — the boss at index 0 (KTD-6)
    hire = Gangster(name="new-hire", weapon=1, energie=5, kraft=40, intelligenz=10, brutalitaet=70)
    out = apply(state, RosterAppend(gangster=hire))
    assert out.players[0].roster == (state.players[0].roster[0], hire)
    assert len(out.players[0].roster) == 2


def test_roster_append_targets_explicit_player():
    state = make_state()
    hire = Gangster(name="hired")
    out = apply(state, RosterAppend(gangster=hire, player=1))
    assert out.players[1].roster[-1] == hire
    assert len(out.players[0].roster) == 1  # untouched


def test_roster_append_purity():
    state = make_state()
    hire = Gangster(name="hired")
    out = apply(state, RosterAppend(gangster=hire))
    assert len(state.players[0].roster) == 1  # original untouched
    assert out is not state


# --------------------------------------------------------------------------- #
# roster_truncate (#99) — the late-rent eviction, real application             #
# --------------------------------------------------------------------------- #
def _three_gangsters(state, player=0):
    for name in ("h1", "h2"):
        state = apply(state, RosterAppend(gangster=Gangster(name=name), player=player))
    return state


def test_roster_truncate_keeps_only_the_boss():
    # mf-prg.bas:4651 ``gz(sp)=1`` — only gangster 1 (roster[0], the boss) remains.
    state = _three_gangsters(make_state())
    out = apply(state, RosterTruncate(size=1))
    assert out.players[0].roster == (state.players[0].roster[0],)
    assert len(state.players[0].roster) == 3  # original untouched (purity)


def test_roster_truncate_targets_explicit_player():
    state = _three_gangsters(make_state(), player=1)
    out = apply(state, RosterTruncate(size=1, player=1))
    assert len(out.players[1].roster) == 1
    assert out.players[0].roster == state.players[0].roster


def test_roster_truncate_never_grows_a_roster():
    state = make_state()  # one gangster already
    out = apply(state, RosterTruncate(size=1))
    assert out.players[0].roster == state.players[0].roster


def test_rent_accrue_takes_a_negative_month_for_the_countdown():
    # mf-prg.bas:4046 ``um(sp)=um(sp)-1`` reuses the signed um(sp) accumulator.
    state = apply(make_state(), RentAccrue(3))
    out = apply(state, RentAccrue(-1))
    assert game.rented_months(out.players[0]) == 2


# --------------------------------------------------------------------------- #
# job_set / job_clear (U10) — pub job accept + shift flow, real application    #
# --------------------------------------------------------------------------- #
def test_job_set_stores_type_pay_and_duration():
    state = make_state()  # jobs defaults to Job() -- type=0
    out = apply(state, JobSet(type=2, pending_pay=1200, months_left=2))
    assert game.job(out.players[0]) == Job(type=2, pending_pay=1200, months_left=2)


def test_job_set_targets_explicit_player():
    state = make_state()
    out = apply(state, JobSet(type=4, pending_pay=2200, months_left=1, player=1))
    assert game.job(out.players[1]) == Job(type=4, pending_pay=2200, months_left=1)
    assert game.job(out.players[0]) == Job()  # untouched


def test_job_set_purity():
    state = make_state()
    out = apply(state, JobSet(type=1, pending_pay=2000, months_left=3))
    assert game.job(state.players[0]) == Job()  # original untouched
    assert out is not state


def test_job_clear_resets_to_default():
    state = apply(make_state(), JobSet(type=3, pending_pay=2000, months_left=2))
    out = apply(state, JobClear())
    assert game.job(out.players[0]) == Job()


def test_job_clear_targets_explicit_player():
    state = apply(make_state(), JobSet(type=2, pending_pay=1000, months_left=2, player=1))
    out = apply(state, JobClear(player=1))
    assert game.job(out.players[1]) == Job()


def test_job_clear_purity():
    state = apply(make_state(), JobSet(type=1, pending_pay=2000, months_left=3))
    out = apply(state, JobClear())
    assert game.job(state.players[0]) == Job(type=1, pending_pay=2000, months_left=3)
    assert out is not state


# --------------------------------------------------------------------------- #
# gangster_mark_hired (U9) — global sg(i) set, pub recruit flow                #
# --------------------------------------------------------------------------- #
def test_gangster_mark_hired_adds_to_global_flags():
    state = make_state()
    out = apply(state, GangsterMarkHired(candidate_id=3))
    assert game.hired_ids(out) == (3,)


def test_gangster_mark_hired_accumulates():
    state = apply(make_state(), GangsterMarkHired(candidate_id=3))
    out = apply(state, GangsterMarkHired(candidate_id=29))
    assert game.hired_ids(out) == (3, 29)


def test_gangster_mark_hired_is_not_per_player():
    # Global, not player-scoped -- no `player` field exists on the effect at all.
    state = apply(make_state(), GangsterMarkHired(candidate_id=0))
    assert game.hired_ids(state) == (0,)
    assert not hasattr(GangsterMarkHired(candidate_id=0), "player")


def test_gangster_mark_hired_dedupes_defensively():
    state = apply(make_state(), GangsterMarkHired(candidate_id=5))
    out = apply(state, GangsterMarkHired(candidate_id=5))
    assert game.hired_ids(out) == (5,)  # not (5, 5)


def test_gangster_mark_hired_purity():
    state = make_state()
    out = apply(state, GangsterMarkHired(candidate_id=1))
    assert game.hired_ids(state) == ()  # original untouched
    assert out is not state


# --------------------------------------------------------------------------- #
# assign_weapon (U3) — the R9 purchase-persist primitive                      #
# --------------------------------------------------------------------------- #
def test_assign_weapon_sets_gangster_weapon():
    state = make_state()  # g0 weapon starts at 0
    out = apply(state, AssignWeapon(weapon=5))
    assert out.players[0].roster[0].weapon == 5


def test_assign_weapon_targets_the_right_gangster_index():
    state = _with_second_gangster(make_state(), Gangster(name="g0b", weapon=0))
    out = apply(state, AssignWeapon(weapon=3, gangster=1))
    assert out.players[0].roster[1].weapon == 3
    assert out.players[0].roster[0].weapon == 0  # index 0 untouched


def test_assign_weapon_explicit_player_targeting():
    state = make_state()  # active is 0
    out = apply(state, AssignWeapon(weapon=7, player=1))
    assert out.players[1].roster[0].weapon == 7
    assert out.players[0].roster[0].weapon == 0  # active untouched


def test_assign_weapon_bad_gangster_index_raises_indexerror():
    state = make_state()
    with pytest.raises(IndexError):
        apply(state, AssignWeapon(weapon=1, gangster=9))


def test_assign_weapon_purity():
    state = make_state()
    apply(state, AssignWeapon(weapon=5))
    assert state.players[0].roster[0].weapon == 0  # ORIGINAL untouched


# --------------------------------------------------------------------------- #
# flag_set                                                                    #
# --------------------------------------------------------------------------- #
def test_flag_set_global_works():
    """FlagSet writes a key of the declared global value map."""
    state = make_state()
    out = apply(state, FlagSet("hired.2", True))
    assert out.values["hired.2"] is True
    assert state.values["hired.2"] is False  # purity


def test_flag_set_unknown_flag_raises_value_error():
    state = make_state()
    with pytest.raises(ValueError):
        apply(state, FlagSet("nonexistent", 1))


def test_flag_set_non_global_scope_raises_not_implemented():
    state = make_state()
    with pytest.raises(NotImplementedError):
        apply(state, FlagSet("hired.2", True, scope="player"))


# --------------------------------------------------------------------------- #
# targeting                                                                   #
# --------------------------------------------------------------------------- #
def test_explicit_player_targets_that_player():
    state = make_state()  # active is 0
    out = apply(state, MoneyChange(-100, player=1))
    assert out.players[1].ka == 900  # players[1] targeted
    assert out.players[0].ka == 5000  # active player untouched


def test_default_targets_active_player():
    state = make_state(active_player=1)
    out = apply(state, MoneyChange(-100))
    assert out.players[1].ka == 900  # active_player=1 targeted
    assert out.players[0].ka == 5000


def test_out_of_range_player_index_raises_indexerror():
    state = make_state()  # two players
    with pytest.raises(IndexError):
        apply(state, MoneyChange(-100, player=5))


def test_negative_player_index_raises_not_wraps():
    # Python indexing would silently target players[-1]; the guard must reject it
    # so the "out-of-range -> IndexError" contract holds literally.
    state = make_state()
    with pytest.raises(IndexError):
        apply(state, MoneyChange(-100, player=-1))
    assert state.players[-1].ka == 1000  # last player untouched


def test_negative_gangster_index_raises_not_wraps():
    state = make_state()
    with pytest.raises(IndexError):
        apply(state, StatChange("kraft", 1, gangster=-1))


# --------------------------------------------------------------------------- #
# jail — a config effect that sets the sentence (gs(sp))                       #
# --------------------------------------------------------------------------- #
def test_jail_sets_the_target_players_jail_months():
    state = make_state()
    out = apply(state, Jail(3))
    assert game.wanted(out.players[0]).jail_months == 3
    # an absolute set, not a delta: a second sentence replaces the first
    assert game.wanted(apply(out, Jail(2)).players[0]).jail_months == 2
    assert game.wanted(state.players[0]).jail_months == 0  # input untouched


# --------------------------------------------------------------------------- #
# debt_change / debt_clear (U11) — kdh borrow/repay flows, real application    #
# --------------------------------------------------------------------------- #
def test_debt_change_adds_to_debt_amount():
    state = make_state()  # debt defaults to Debt() -- amount=0, months=0
    out = apply(state, DebtChange(amount=500, months=6))
    assert game.debt(out.players[0]) == Debt(amount=500, months=6)


def test_debt_change_months_none_leaves_months_untouched():
    state = apply(make_state(), DebtChange(amount=1000, months=6))
    out = apply(state, DebtChange(amount=-400))
    assert game.debt(out.players[0]) == Debt(amount=600, months=6)


def test_debt_change_targets_explicit_player():
    state = make_state()
    out = apply(state, DebtChange(amount=200, months=6, player=1))
    assert game.debt(out.players[1]) == Debt(amount=200, months=6)
    assert game.debt(out.players[0]) == Debt()  # untouched


def test_debt_change_purity():
    state = make_state()
    out = apply(state, DebtChange(amount=500, months=6))
    assert game.debt(state.players[0]) == Debt()  # original untouched
    assert out is not state


def test_debt_clear_zeroes_amount_and_months():
    state = apply(make_state(), DebtChange(amount=500, months=6))
    out = apply(state, DebtClear())
    assert game.debt(out.players[0]) == Debt(amount=0, months=0)


def test_debt_clear_targets_explicit_player():
    state = apply(make_state(), DebtChange(amount=300, months=6, player=1))
    out = apply(state, DebtClear(player=1))
    assert game.debt(out.players[1]) == Debt()


def test_debt_clear_purity():
    state = apply(make_state(), DebtChange(amount=500, months=6))
    out = apply(state, DebtClear())
    assert game.debt(state.players[0]) == Debt(amount=500, months=6)  # original untouched
    assert out is not state


# --------------------------------------------------------------------------- #
# shop_change (U11) — kdh buy/sell/capital-adjust/income, real application     #
# --------------------------------------------------------------------------- #
def test_shop_change_sets_tile():
    state = make_state()  # business defaults to Business() -- shop_tile=0
    out = apply(state, ShopChange(tile=3))
    assert game.business(out.players[0]).shop_tile == 3


def test_shop_change_tile_zero_clears_ownership():
    state = apply(make_state(), ShopChange(tile=3))
    out = apply(state, ShopChange(tile=0))
    assert game.business(out.players[0]).shop_tile == 0


def test_shop_change_capital_delta_adds():
    state = apply(make_state(), ShopChange(tile=3))
    out = apply(state, ShopChange(capital_delta=500))
    assert game.business(out.players[0]) == Business(shop_tile=3, shop_capital=500)


def test_shop_change_capital_delta_negative_subtracts():
    state = apply(make_state(), ShopChange(tile=3, capital_delta=1000))
    out = apply(state, ShopChange(capital_delta=-400))
    assert game.business(out.players[0]).shop_capital == 600


def test_shop_change_tile_and_capital_delta_together():
    state = make_state()
    out = apply(state, ShopChange(tile=2, capital_delta=750))
    assert game.business(out.players[0]) == Business(shop_tile=2, shop_capital=750)


def test_shop_change_targets_explicit_player():
    state = make_state()
    out = apply(state, ShopChange(tile=4, player=1))
    assert game.business(out.players[1]).shop_tile == 4
    assert game.business(out.players[0]) == Business()  # untouched


def test_shop_change_purity():
    state = make_state()
    out = apply(state, ShopChange(tile=3))
    assert game.business(state.players[0]) == Business()  # original untouched
    assert out is not state


# --------------------------------------------------------------------------- #
# unknown effect type                                                         #
# --------------------------------------------------------------------------- #
def test_unknown_effect_type_raises_type_error():
    state = make_state()
    with pytest.raises(TypeError):
        apply(state, object())


# --------------------------------------------------------------------------- #
# commit — one deep copy, many effects, ordered committed list                #
# --------------------------------------------------------------------------- #
def test_commit_applies_multiple_effects_cumulatively():
    state = make_state()  # active player 0: ms=3, ka=5000
    result = commit(state, [MsChange(-1), MoneyChange(-500), MsChange(-2)])

    assert isinstance(result, CommitResult)
    assert result.state.players[0].ms == 0  # 3 - 1 - 2
    assert result.state.players[0].ka == 4500  # 5000 - 500


def test_commit_does_not_mutate_input_state():
    state = make_state()
    commit(state, [MoneyChange(-500), MsChange(-3)])

    assert state.players[0].ka == 5000  # ORIGINAL untouched
    assert state.players[0].ms == 3


def test_commit_returns_a_distinct_state_object():
    state = make_state()
    result = commit(state, [MoneyChange(-1)])
    assert result.state is not state
    assert result.state.players[0] is not state.players[0]


def test_commit_returns_effects_in_order():
    state = make_state()
    effects = [MsChange(-1), MoneyChange(-500), MsChange(-2)]
    result = commit(state, effects)
    assert result.effects == effects  # same objects, same order


def test_commit_empty_effects_returns_equal_state():
    """An empty commit changes nothing.

    Pre-freeze this asserted ``is not state`` — an artifact of the unconditional
    deepcopy, not a real guarantee. With a frozen graph the caller cannot mutate
    what it gets back, so returning the identical object is safe *and* the honest
    contract is about VALUES: nothing changed.
    """
    state = make_state()
    result = commit(state, [])

    assert result.effects == []
    assert result.state == state  # equal content — nothing applied
    assert result.state.players[0].ka == state.players[0].ka
    assert result.state.players[0].ms == state.players[0].ms


def test_commit_unknown_effect_raises_type_error():
    state = make_state()
    with pytest.raises(TypeError):
        commit(state, [MoneyChange(-1), object()])


def test_commit_surfaces_an_effects_apply_error():
    state = make_state()
    with pytest.raises(NotImplementedError):
        commit(state, [MoneyChange(-1), FlagSet("x", 1, scope="player")])


# --------------------------------------------------------------------------- #
# Driver integration — commit applies effects at the STATE level             #
# --------------------------------------------------------------------------- #
def test_driver_commit_applies_effects_and_is_pure():
    state = make_state()

    def handler(ctx):
        ctx.apply(MoneyChange(-100))
        return ["done"]
        yield  # pragma: no cover - make it a generator

    def src(_interaction):  # pragma: no cover - never consulted
        raise AssertionError("no prompt in this handler")

    result = run(handler, src, state=state)

    assert result.status == "completed"
    assert result.effects == [MoneyChange(-100)]
    assert result.state.players[0].ka == 4900  # effect applied at state level
    assert state.players[0].ka == 5000  # ORIGINAL unchanged (purity through driver)


def test_driver_cancel_discards_effects_at_state_level():
    state = make_state()

    def handler(ctx):
        ctx.apply(MoneyChange(-100))  # partial effect BEFORE the cancel
        yield PromptInt("confirm", min=1, max=9, cancellable=True)
        ctx.apply(MoneyChange(-999))  # unreachable
        return ["never"]

    def src(_interaction):
        return CANCEL

    result = run(handler, src, state=state)

    assert result.status == "cancelled"
    assert result.effects == []  # atomic discard
    assert result.state is state  # ORIGINAL state, unchanged
    assert result.state.players[0].ka == 5000  # ka NOT reduced


def test_driver_state_none_when_no_state_passed():
    def handler(ctx):
        ctx.apply(MoneyChange(-100))
        return []
        yield  # pragma: no cover

    def src(_interaction):  # pragma: no cover
        raise AssertionError

    result = run(handler, src)
    assert result.state is None  # no state → nothing to apply against


def test_driver_empty_buffer_returns_equal_but_distinct_state():
    state = make_state()

    def handler(ctx):
        return []
        yield  # pragma: no cover

    def src(_interaction):  # pragma: no cover
        raise AssertionError

    result = run(handler, src, state=state)
    # Clean completion always commits, but an empty buffer applies nothing — the state
    # comes back equal. (Frozen graph: no copy is needed to keep the caller safe.)
    assert result.state == state
    assert result.state.players[0].ka == state.players[0].ka


# --------------------------------------------------------------------------- #
# Nested functional-update helpers (KTD-3) — written once, used by every branch #
# --------------------------------------------------------------------------- #
def _two_player_state() -> GameState:
    return GameState(
        players=(
            Player(name="A", ka=100, roster=(Gangster(name="g0"), Gangster(name="g1"))),
            Player(name="B", ka=200),
        ),
        clock=Clock(player_count=2),
    )


def test_tuple_replace_rejects_out_of_range_index():
    """The write-once primitive must fail loudly on a bad index.

    Bare slicing would silently GROW the tuple on an out-of-range (or negative)
    index — the silent-corruption shape the frozen graph exists to eliminate. Every
    live caller guards its index today, so this pins the helper itself.
    """
    items = (1, 2, 3)
    for bad in (-1, 3, 99):
        with pytest.raises(IndexError):
            tuple_replace(items, bad, 0)


# The nested-update helpers are engine-internal; these tests pin their guarantees
# through the public apply() with an effect that routes through each one.
def test_a_player_effect_updates_only_the_target():
    """The target player changes, siblings are shared, unrelated fields are kept."""
    st = _two_player_state()
    new = apply(st, MoneyChange(899, player=0))

    assert new.players[0].ka == 999
    assert new.players[1] is st.players[1]  # structural sharing of the sibling
    assert new.players[0].name == "A"  # unrelated fields preserved


def test_a_player_effect_is_pure():
    """The input state is never mutated."""
    st = _two_player_state()
    apply(st, MoneyChange(899, player=0))

    assert st.players[0].ka == 100  # original untouched


def test_a_player_effect_returns_readonly_players():
    """R2: the rebuilt players collection is a tuple, never a mutable list."""
    st = _two_player_state()
    new = apply(st, MoneyChange(899, player=0))

    assert isinstance(new.players, tuple)


def test_a_stat_effect_updates_only_the_target_gangster():
    """One level deeper: the right gangster's stat changes; roster siblings are shared.

    Stats are read and written through ``attrs`` since ``kraft`` is no longer a named
    field on the engine's ``Combatant``.
    """
    st = _two_player_state()
    new = apply(st, StatChange(stat="kraft", amount=42, gangster=1, player=0))

    assert new.players[0].roster[1].attrs["kraft"] == 42
    assert new.players[0].roster[0] is st.players[0].roster[0]
    assert new.players[1] is st.players[1]
    assert st.players[0].roster[1].attrs["kraft"] == 0  # purity


def test_a_weapon_effect_updates_a_blueprint_field():
    """A blueprint field the engine names (``weapon``) is set on the right gangster."""
    st = _two_player_state()
    new = apply(st, AssignWeapon(weapon=3, gangster=1, player=0))

    assert new.players[0].roster[1].weapon == 3
    assert new.players[0].roster[0] is st.players[0].roster[0]
    assert st.players[0].roster[1].weapon == 0  # purity


def test_a_tenancy_effect_rebuilds_a_readonly_mapping():
    """R2: a rebuilt global value map is read-only and leaves the source map alone."""
    st = with_tenancy(_two_player_state(), ln=1, owner=0)
    new = apply(st, SetTenancy(2, player=1))

    assert (game.tenant(new, 1), game.tenant(new, 2)) == (0, 1)
    assert dict(st.values) == {"tenancy.1": 0}  # purity
    with pytest.raises(TypeError):
        new.values["tenancy.3"] = 9  # pyright: ignore[reportIndexIssue]  # the write is the test: it must raise
