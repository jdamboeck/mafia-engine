"""Contract tests for the Effect API + pure application + driver commit integration (U5).

Written proof-first: this file is authored BEFORE ``engine/effects.py`` exists, run to
observe a red failure (import error), then the module is implemented to green.

Effects are typed, frozen, serializable pure-data records that double as replay events
(KTD-3, KTD-6). ``apply(state, effect) -> GameState`` is PURE: it deep-copies the input
state, mutates the copy, and returns it — the input is never mutated. Effects flow through
the U4 driver's buffer: ``ctx.apply`` enqueues; the buffer commits atomically (folding
``apply`` over it) on clean completion and is discarded on cancel.
"""

from __future__ import annotations

import dataclasses

import pytest

from engine.effects import (
    SCHEMA_VERSION,
    CommitResult,
    EnergyChange,
    FlagSet,
    Jail,
    MoneyChange,
    MsChange,
    ScoreChange,
    SetEntryContext,
    SetPosition,
    SpawnFighter,
    StatChange,
    Teleport,
    WantedChange,
    apply,
    commit,
)
from engine.interactions import CANCEL, PromptInt, run
from engine.state import Clock, Flags, Gangster, GameState, Player


# --------------------------------------------------------------------------- #
# State fixtures                                                              #
# --------------------------------------------------------------------------- #
def make_state():
    """A two-player state; active player is index 0."""
    p0 = Player(
        name="p0",
        ka=5000,
        gf=50.0,
        po=18,
        ms=3,
        roster=[Gangster(name="g0", energie=5, kraft=20, intelligenz=30, brutalitaet=10)],
    )
    p1 = Player(
        name="p1",
        ka=1000,
        gf=10.0,
        po=100,
        ms=2,
        roster=[Gangster(name="g1", energie=5, kraft=15, intelligenz=25, brutalitaet=5)],
    )
    return GameState(players=[p0, p1], clock=Clock(active_player=0), flags=Flags())


# --------------------------------------------------------------------------- #
# Effect schema — frozen, versioned, pure data                               #
# --------------------------------------------------------------------------- #
def test_effects_are_frozen_and_versioned():
    e = MoneyChange(-500)
    assert e.SCHEMA_VERSION == 1
    assert SCHEMA_VERSION == 1
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.amount = 1  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# money_change + purity                                                       #
# --------------------------------------------------------------------------- #
def test_money_change_and_purity():
    state = make_state()
    out = apply(state, MoneyChange(-500))

    assert out.players[0].ka == 4500  # delta applied on the copy
    assert state.players[0].ka == 5000  # ORIGINAL untouched (purity)
    assert out is not state  # a new object is returned (no aliasing)
    assert out.players[0] is not state.players[0]  # deep copy, not shared references


# --------------------------------------------------------------------------- #
# score_change clamp [0, 100]                                                 #
# --------------------------------------------------------------------------- #
def test_score_change_caps_at_100():
    state = make_state()  # gf starts at 50
    out = apply(state, ScoreChange(200.0))
    assert out.players[0].gf == 100.0


def test_score_change_floors_at_0():
    state = make_state()  # gf starts at 50
    out = apply(state, ScoreChange(-200.0))
    assert out.players[0].gf == 0.0


def test_score_change_normal_delta_lands_exactly():
    state = make_state()  # gf starts at 50
    out = apply(state, ScoreChange(25.0))
    assert out.players[0].gf == 75.0


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
    state = make_state()
    state.clock.active_player = 1
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
    state = make_state()
    state.clock.active_player = 1
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
    assert out.players[0].roster[0].kraft == 25


def test_stat_change_unknown_stat_raises_value_error():
    state = make_state()
    with pytest.raises(ValueError):
        apply(state, StatChange("charisma", 5))


def test_stat_change_targets_the_right_gangster_index():
    state = make_state()
    state.players[0].roster.append(Gangster(name="g0b", brutalitaet=1))
    out = apply(state, StatChange("brutalitaet", 7, gangster=1))
    assert out.players[0].roster[1].brutalitaet == 8
    assert out.players[0].roster[0].brutalitaet == 10  # index 0 untouched


# --------------------------------------------------------------------------- #
# flag_set                                                                    #
# --------------------------------------------------------------------------- #
def test_flag_set_global_works():
    state = make_state()
    out = apply(state, FlagSet("graphics_mode", 2))
    assert out.flags.graphics_mode == 2


def test_flag_set_unknown_flag_raises_value_error():
    state = make_state()
    with pytest.raises(ValueError):
        apply(state, FlagSet("nonexistent", 1))


def test_flag_set_non_global_scope_raises_not_implemented():
    state = make_state()
    with pytest.raises(NotImplementedError):
        apply(state, FlagSet("loaded", True, scope="player"))


# --------------------------------------------------------------------------- #
# targeting                                                                   #
# --------------------------------------------------------------------------- #
def test_explicit_player_targets_that_player():
    state = make_state()  # active is 0
    out = apply(state, MoneyChange(-100, player=1))
    assert out.players[1].ka == 900  # players[1] targeted
    assert out.players[0].ka == 5000  # active player untouched


def test_default_targets_active_player():
    state = make_state()
    state.clock.active_player = 1
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
# deferred effects raise NotImplementedError                                  #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "effect",
    [
        WantedChange(1),
        EnergyChange(1),
        Jail(3),
        SpawnFighter(fighter=object()),
    ],
)
def test_deferred_effects_raise_not_implemented(effect):
    state = make_state()
    with pytest.raises(NotImplementedError):
        apply(state, effect)


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


def test_commit_empty_effects_returns_equal_but_distinct_state():
    state = make_state()
    result = commit(state, [])

    assert result.effects == []
    assert result.state is not state  # a distinct copy, not the original
    assert result.state.players[0].ka == state.players[0].ka  # equal content
    assert result.state.players[0].ms == state.players[0].ms


def test_commit_unknown_effect_raises_type_error():
    state = make_state()
    with pytest.raises(TypeError):
        commit(state, [MoneyChange(-1), object()])


def test_commit_deferred_effect_raises_not_implemented():
    state = make_state()
    with pytest.raises(NotImplementedError):
        commit(state, [WantedChange(1)])


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
    # Clean completion always commits (one deep copy), so even an empty buffer yields a
    # distinct-but-equal copy — the driver-cancel path is the one that returns the original.
    assert result.state is not state
    assert result.state.players[0].ka == state.players[0].ka
