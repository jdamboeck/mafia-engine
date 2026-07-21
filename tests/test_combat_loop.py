"""Tests for the combat activation loop, driver sub-protocol, and outcomes (U5).

Covers:
- the ported formulas per weapon id 0..8 (shot range, hit check, damage bounds)
  against hand-computed research values (``mf-prg.bas:30215-30216``, ``30247``,
  ``30255``);
- the activation loop itself (side order, downed-fighter skip, one action per
  activation, illegal move re-prompt);
- surrender flipping the winner (``mf-prg.bas:30136``);
- victory detection the moment the last opposing fighter drops (``30106``);
- the driver sub-protocol: ``StartCombat`` no longer raises, the combat screen is
  a typed JSON-serializable interaction, and effects commit atomically with the
  invoking handler's (a cancelled invoking action discards both).
"""

from __future__ import annotations

import json

import pytest

from data.game_configs.mafia_1920s.combat_rules import build_rules
from engine.combat import (
    DEFAULT_RANGE,
    RANGE_MELEE,
    STEP_RIGHT,
)
from engine.effects import MoneyChange
from engine.rng import Rng
from engine.interactions import (
    CANCEL,
    CombatScreen,
    Ctx,
    PromptInt,
    StartCombat,
    run,
)
from engine.state import CombatState
from tests.helpers import WEAPON_TABLE, StubRng, build_fight, run_fight
from tests.helpers import combat_fighter as _f

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

#: This module exercises side 1 acting — CombatState's own default cursor
#: (active_side=1, active_fighter=1) — unlike tests/test_combat_ai.py's CPU-side
#: default; see tests.helpers.build_fight's docstring for why the two files each
#: keep their own thin wrapper rather than sharing one default.
_StubRng = StubRng


def _fight(*, side1, side2, grid=(), rng=None, weapon_stats=None):
    return build_fight(
        side1=side1,
        side2=side2,
        grid=grid,
        rng=rng,
        weapon_stats=weapon_stats,
        active=(1, 1),
    )


# --------------------------------------------------------------------------- #
# Formulas — per weapon id 0..8 (mf-prg.bas:30215-30216, 30247, 30255)         #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("weapon,ts,tg,wrange", WEAPON_TABLE)
def test_weapon_range_comes_from_config_data_not_the_weapon_id(weapon, ts, tg, wrange):
    """The fight reports each weapon's reach straight from the supplied entity data.

    ``wrange`` is the value the deleted ``shot_range`` function produced for this id
    (30215: ``r=2``, widened to 15 by ``w>3``; 30216: 20 for ids 6/7 AFTER that
    widening) — this is the differential that pinned the port when the hardcoded
    ladder was replaced by ``weapons.yaml``'s ``range`` attribute.
    """
    fight = _fight(side1=[_f(weapon=weapon, position=10)], side2=[_f(weapon=weapon, position=300)])
    assert fight.equipment_range(fight.sides[0][0]) == wrange


def test_grenades_reach_fifteen_not_twenty():
    """Named guard for the widening edge: id 8 satisfies ``w>3`` but is not 6 or 7.

    30215 widens it to 15 and 30216 never promotes it, so handgranaten carry the
    RANGED reach despite outranking both heavies on damage.
    """
    fight = _fight(side1=[_f(weapon=8, position=10)], side2=[_f(weapon=8, position=300)])
    assert fight.equipment_range(fight.sides[0][0]) == 15


@pytest.mark.parametrize("weapon,ts,tg,wrange", WEAPON_TABLE)
def test_melee_is_derived_from_reach_not_from_the_weapon_id(weapon, ts, tg, wrange):
    """``range <= RANGE_MELEE`` selects exactly the source's ``w<4`` set (30415)."""
    fight = _fight(side1=[_f(weapon=weapon, position=10)], side2=[_f(weapon=weapon, position=300)])
    combatant = fight.sides[0][0]
    assert fight.is_melee(combatant) == (weapon < 4)
    assert fight.is_melee(combatant) == (wrange <= RANGE_MELEE)


def test_melee_reach_constant_matches_the_source_base_range():
    # 30215's bare ``r=2`` — a weapon that reaches only its neighbour.
    assert RANGE_MELEE == 2
    assert DEFAULT_RANGE == RANGE_MELEE


def test_a_combatant_whose_equipment_omits_range_falls_back_to_adjacent_reach():
    """A weapon object with no ``range`` key reaches one cell, the source's base.

    This is the fallback in the ENGINE (``equipment.get("range", DEFAULT_RANGE)``),
    not a table lookup — a combatant built with a range-less equipment mapping, which
    is what an old two-element ``(ts, tg)`` config produced before U1 added range.
    """
    from engine.state import Fighter

    # attrs carries the stats the rules bundle reads (A5: no auto-zeroed named fields);
    # this test is about range-less EQUIPMENT, so the stat values themselves are inert.
    bare = Fighter(
        weapon=0, position=10, equipment={"ts": 5, "tg": 10}, attrs={"kraft": 0, "brutalitaet": 0}
    )
    fight = _fight(side1=[bare], side2=[_f(weapon=0, position=300)])
    assert fight.equipment_range(fight.sides[0][0]) == DEFAULT_RANGE
    assert fight.is_melee(fight.sides[0][0]) is True


# --------------------------------------------------------------------------- #
# Reach in a real fight — the projectile actually stops where the data says     #
# --------------------------------------------------------------------------- #
def _shot_reached_a_target(*, weapon, distance, weapon_stats=None):
    """Fire ``weapon`` right at an enemy ``distance`` cells away; did the shot arrive?

    Determinism comes from the DATA, not from scripting draws: the travel loop
    (30220-30226) runs BEFORE any roll, and a shot that never finds a target returns
    at once without drawing. So an empty RNG log means "out of reach" and a non-empty
    one means "the projectile got there and the hit roll was consulted" — a signal
    that stays valid however the hit/damage draws are later reordered.
    """
    attacker = _f(weapon=weapon, position=100)
    # The defender carries the ATTACKER's weapon id: with equipment constructed at
    # setup (amendment A1) every fighter's id must exist in the table, and a custom
    # table defines only its own invented ids.
    defender = _f(weapon=weapon, energie=99, position=100 + distance)
    rng = Rng(seed=1234)
    fight = _fight(side1=[attacker], side2=[defender], rng=rng, weapon_stats=weapon_stats)
    fight.shoot(STEP_RIGHT)
    return bool(rng.log)


@pytest.mark.parametrize("weapon,ts,tg,wrange", WEAPON_TABLE)
def test_shot_reaches_exactly_as_far_as_the_weapons_range_and_no_further(weapon, ts, tg, wrange):
    # The last cell inside reach is hit; one cell beyond it is not.
    assert _shot_reached_a_target(weapon=weapon, distance=wrange) is True
    assert _shot_reached_a_target(weapon=weapon, distance=wrange + 1) is False


def test_an_invented_weapon_fires_as_far_as_its_own_range_says():
    """A weapon this game never defined still works — the engine holds no id table.

    Id 42 exists nowhere in ``weapons.yaml``; it reaches 7 cells purely because the
    config handed the fight ``(ts, tg, range)`` saying so.
    """
    invented = {42: (5, 10, 7)}
    assert _shot_reached_a_target(weapon=42, distance=7, weapon_stats=invented) is True
    assert _shot_reached_a_target(weapon=42, distance=8, weapon_stats=invented) is False


def test_an_invented_weapon_is_melee_iff_its_range_is_adjacent_only():
    """Melee is a property of reach, so an invented weapon inherits it from data."""
    stats = {40: (5, 10, 1), 41: (5, 10, 2), 42: (5, 10, 3)}

    def melee(wid):
        fight = _fight(
            side1=[_f(weapon=wid, position=10)],
            side2=[_f(weapon=wid, position=300)],
            weapon_stats=stats,
        )
        return fight.is_melee(fight.sides[0][0])

    assert melee(40) is True  # reaches less than a full melee step
    assert melee(41) is True  # reaches exactly the adjacent cell
    assert melee(42) is False  # out-reaches a neighbour -> ranged


# The per-weapon hit/damage formula tests moved to tests/test_combat_rules.py when the
# formulas moved out of the engine (U2): they now exercise the config's own hit_fn /
# damage_fn — the live path — over the same WEAPON_TABLE ids, plus the full 0..99
# attribute domain the engine copies never covered. The engine no longer defines a
# hit or damage formula to test here.


# --------------------------------------------------------------------------- #
# Activation order (mf-prg.bas:30105-30109)                                   #
# --------------------------------------------------------------------------- #
def test_activation_walks_side_one_then_side_two():
    fight = _fight(
        side1=[_f(name="a", position=10), _f(name="b", position=11)],
        side2=[_f(name="x", position=300), _f(name="y", position=301)],
    )
    seen = [(fight.active_side, fight.active_fighter)]
    for _ in range(3):
        fight.advance_activation()
        seen.append((fight.active_side, fight.active_fighter))
    assert seen == [(1, 1), (1, 2), (2, 1), (2, 2)]


def test_downed_fighters_are_skipped():
    fight = _fight(
        side1=[
            _f(name="a", position=10),
            _f(name="b", position=11, down=True),
            _f(name="c", position=12),
        ],
        side2=[_f(name="x", position=300)],
    )
    fight.advance_activation()
    # fighter 2 is down -> cursor lands on fighter 3
    assert (fight.active_side, fight.active_fighter) == (1, 3)


# --------------------------------------------------------------------------- #
# Victory detection (mf-prg.bas:30106)                                        #
# --------------------------------------------------------------------------- #
def test_victory_when_all_opposing_fighters_are_down():
    fight = _fight(
        side1=[_f(name="a", position=10)],
        side2=[_f(name="x", position=300, down=True)],
    )
    assert fight.winner() == 1


def test_no_victory_while_both_sides_stand():
    fight = _fight(side1=[_f(position=10)], side2=[_f(position=300)])
    assert fight.winner() is None


def test_victory_detected_the_moment_the_last_opponent_drops_mid_round():
    # Two side-1 fighters, one side-2 fighter with 1 energy directly to the right.
    fight = _fight(
        side1=[_f(name="a", weapon=5, position=100, brutalitaet=0), _f(name="b", position=200)],
        side2=[_f(name="x", weapon=0, energie=1, position=101)],
        rng=_StubRng(1, 1, 0),  # hit factors non-zero, damage draw 0 -> 1 damage
    )
    outcome = fight.shoot(+1)
    assert outcome["hit"] is True
    assert fight.sides[1][0].down is True
    # Victory is visible immediately, without waiting for side 1's second fighter.
    assert fight.winner() == 1
    assert fight.losses == (0, 1)


# --------------------------------------------------------------------------- #
# Movement (mf-prg.bas:30140-30150)                                           #
# --------------------------------------------------------------------------- #
def test_move_commits_a_legal_step():
    fight = _fight(side1=[_f(position=100)], side2=[_f(position=300)])
    assert fight.try_move(+1) is True
    assert fight.sides[0][0].position == 101


def test_move_is_rejected_out_of_bounds():
    fight = _fight(side1=[_f(position=0)], side2=[_f(position=300)])
    assert fight.try_move(-1) is False
    assert fight.sides[0][0].position == 0


def test_cell_520_is_legally_reachable():
    fight = _fight(side1=[_f(position=519)], side2=[_f(position=300)])
    assert fight.try_move(+1) is True
    assert fight.sides[0][0].position == 520
    # ...and 521 is not.
    assert fight.try_move(+1) is False


def test_move_is_rejected_onto_an_occupied_cell():
    fight = _fight(side1=[_f(position=100)], side2=[_f(position=101)])
    assert fight.try_move(+1) is False


def test_move_is_rejected_onto_a_scenery_cell():
    grid = [32] * 521
    grid[101] = 160  # wall
    fight = _fight(side1=[_f(position=100)], side2=[_f(position=300)], grid=grid)
    assert fight.try_move(+1) is False


# --------------------------------------------------------------------------- #
# Shooting: projectile travel (mf-prg.bas:30220-30226)                        #
# --------------------------------------------------------------------------- #
def test_shot_stops_at_a_wall_before_the_target():
    grid = [32] * 521
    grid[102] = 160
    fight = _fight(
        side1=[_f(weapon=5, position=100)],
        side2=[_f(weapon=0, position=103)],
        grid=grid,
        rng=_StubRng(),  # no rolls: the projectile never reaches a target
    )
    outcome = fight.shoot(+1)
    assert outcome["hit"] is False
    assert fight.sides[1][0].vitality == 20


def test_shot_flies_over_a_friendly_fighter():
    # 30226 only connects on the OPPOSING side's colour — a friendly is overflown.
    fight = _fight(
        side1=[_f(name="a", weapon=5, position=100, brutalitaet=0), _f(name="ally", position=101)],
        side2=[_f(name="x", weapon=0, energie=20, position=102)],
        rng=_StubRng(1, 1, 0),
    )
    outcome = fight.shoot(+1)
    assert outcome["hit"] is True
    assert fight.sides[1][0].vitality == 19


def test_shot_expires_at_weapon_range():
    # melee (weapon 0) range 2: a target 3 cells away is out of reach.
    fight = _fight(
        side1=[_f(weapon=0, position=100)],
        side2=[_f(weapon=0, position=103)],
        rng=_StubRng(),
    )
    assert fight.shoot(+1)["hit"] is False


def test_melee_range_two_reaches_a_target_two_cells_away():
    fight = _fight(
        side1=[_f(weapon=0, position=100, brutalitaet=0)],
        side2=[_f(weapon=0, energie=20, position=102)],
        rng=_StubRng(1, 1, 0),
    )
    assert fight.shoot(+1)["hit"] is True


def test_a_miss_leaves_the_target_untouched():
    fight = _fight(
        side1=[_f(weapon=5, position=100)],
        side2=[_f(weapon=0, energie=20, position=101)],
        rng=_StubRng(0, 1),  # weapon factor zero -> miss
    )
    assert fight.shoot(+1)["hit"] is False
    assert fight.sides[1][0].vitality == 20


def test_energy_clamps_at_zero_and_marks_the_fighter_down():
    fight = _fight(
        side1=[_f(weapon=8, position=100, brutalitaet=90)],
        side2=[_f(weapon=0, energie=2, position=101)],
        rng=_StubRng(1, 1, 17),  # big damage roll
    )
    fight.shoot(+1)
    target = fight.sides[1][0]
    assert target.vitality == 0
    assert target.down is True
    assert fight.losses == (0, 1)


def test_attacker_stats_drive_both_rolls_not_the_targets():
    # 30246 loads a=ks(s), b=f — the ATTACKER — for kr and bt (KTD-9: code over prose).
    rng = _StubRng(1, 1, 0)
    fight = _fight(
        side1=[_f(weapon=5, position=100, kraft=37, brutalitaet=35)],
        side2=[_f(weapon=0, energie=20, position=101, kraft=99, brutalitaet=99)],
        rng=rng,
    )
    fight.shoot(+1)
    # hit draws use the ATTACKER's kraft 37 -> int(37/10)+1 = 4, and damage
    # uses the attacker's tg (revolver 10) + attacker brutalitaet 35.
    assert rng.calls == [("range", 5), ("range", 4), ("range", 10)]


# --------------------------------------------------------------------------- #
# Surrender (mf-prg.bas:30136)                                                #
# --------------------------------------------------------------------------- #
def test_surrender_flips_the_winner_to_the_other_side():
    fight = _fight(side1=[_f(position=10)], side2=[_f(position=300)])
    assert fight.active_side == 1
    fight.surrender()
    assert fight.winner() == 2
    assert fight.finished is True


def test_side_two_surrender_gives_side_one_the_win():
    fight = _fight(side1=[_f(position=10)], side2=[_f(position=300)])
    fight.advance_activation()
    assert fight.active_side == 2
    fight.surrender()
    assert fight.winner() == 1


# --------------------------------------------------------------------------- #
# Driver sub-protocol (KTD-1/KTD-2)                                           #
# --------------------------------------------------------------------------- #
def _combat_state():
    return CombatState(
        sides=(
            (_f(name="hero", weapon=5, energie=20, position=100, brutalitaet=0),),
            (_f(name="thug", weapon=0, energie=1, position=101),),
        ),
        grid=(),
    )


def _spec(**kw):
    base = dict(
        sides=_combat_state().sides,
        grid=(),
        rules=build_rules(),
    )
    base.update(kw)
    return base


def test_startcombat_no_longer_raises_and_resolves_with_a_winner():
    # shoot right -> hit -> thug (1 energy) drops -> side 1 wins. Driven through the
    # shared run_fight helper (U6, R14).
    result = run_fight(**_spec(), answers=[("shoot", +1)], rng=_StubRng(1, 1, 0))
    assert result.winner == 1


def test_combat_screen_is_yielded_each_activation_and_is_json_serializable():
    seen = []

    def handler(ctx):
        yield StartCombat(**_spec())
        return []

    def src(interaction):
        if isinstance(interaction, CombatScreen):
            seen.append(interaction)
            return ("shoot", +1)
        raise AssertionError(f"unexpected interaction {interaction!r}")

    run(handler, src, state=None, rng=_StubRng(1, 1, 0))
    assert seen, "no CombatScreen was yielded"
    screen = seen[0]
    payload = screen.to_json()
    # Structural contract U7 renders from — must survive a JSON round-trip verbatim.
    assert json.loads(json.dumps(payload)) == payload
    assert payload["version"] == CombatScreen.SCHEMA_VERSION
    assert payload["active_side"] == 1
    assert payload["active_fighter"] == 1
    assert payload["prompt"] == "action"
    assert payload["grid"] == []
    assert payload["fighter"]["name"] == "hero"
    assert payload["sides"][0][0]["position"] == 100
    assert payload["losses"] == [0, 0]


def test_illegal_move_re_prompts_without_ending_the_activation():
    calls = []

    def handler(ctx):
        yield StartCombat(**_spec())
        return []

    def src(interaction):
        calls.append(interaction)
        # first: an out-of-bounds move (left off the row start is legal here, so use
        # a move onto the enemy's occupied cell); then a shot that ends the fight.
        if len(calls) == 1:
            return ("move", +1)  # cell 101 is occupied -> rejected
        return ("shoot", +1)

    run(handler, src, state=None, rng=_StubRng(1, 1, 0))
    assert len(calls) == 2, "an illegal move must re-prompt the same activation"


def test_quit_and_eof_are_treated_as_surrender_not_cancel():
    # CANCEL at a combat prompt must NOT unwind the handler — it surrenders (KTD-2).
    result = run_fight(**_spec(), input_source=lambda i: CANCEL, rng=_StubRng())
    assert result.winner == 2  # side 1 surrendered -> side 2 wins


def test_combat_effects_commit_with_the_invoking_handlers_effects():
    def handler(ctx):
        ctx.apply(MoneyChange(amount=10))
        result = yield StartCombat(**_spec())
        ctx.apply(MoneyChange(amount=100 if result.winner == 1 else -100))
        return result.winner

    result = run(handler, _scripted_combat([("shoot", +1)]), state=None, rng=_StubRng(1, 1, 0))
    assert [e.amount for e in result.effects] == [10, 100]


def test_a_cancelled_invoking_action_discards_the_combat_effects_too():
    def handler(ctx):
        ctx.apply(MoneyChange(amount=10))
        yield StartCombat(**_spec())
        # A cancellable prompt AFTER the fight: cancelling must discard everything.
        yield PromptInt(key="k", min=0, max=9, cancellable=True)
        return "unreachable"

    def src(interaction):
        if isinstance(interaction, CombatScreen):
            return ("shoot", +1)
        return CANCEL

    result = run(handler, src, state=None, rng=_StubRng(1, 1, 0))
    assert result.status == "cancelled"
    assert result.effects == []


def test_combat_is_not_supported_inside_a_substate():
    # KTD-1: combat is only ever yielded from top-level handlers this slice — the
    # driver ASSERTS this rather than supporting it.
    from engine.interactions import LoadSubState, _run_substate

    ctx = Ctx()
    load = LoadSubState(kind="__u5_combat_probe__", params={})

    from engine import substates

    def probe(ctx, params):
        yield StartCombat(**_spec())
        return None

    substates.SUBSTATES["__u5_combat_probe__"] = probe
    try:
        with pytest.raises(AssertionError):
            _run_substate(load, lambda i: ("shoot", +1), ctx)
    finally:
        del substates.SUBSTATES["__u5_combat_probe__"]


# --------------------------------------------------------------------------- #
# Scripted seeded fight — deterministic transcript                            #
# --------------------------------------------------------------------------- #
def _scripted_combat(script):
    """An input source answering each CombatScreen from ``script`` in order."""
    it = iter(script)

    def src(interaction):
        if isinstance(interaction, CombatScreen):
            return next(it)
        raise AssertionError(f"unexpected interaction {interaction!r}")

    return src


def test_scripted_seeded_fight_has_a_deterministic_transcript():
    """A two-a-side fight, fully scripted, with a hand-checked outcome."""
    sides = (
        (
            _f(name="a", weapon=5, energie=20, position=100, kraft=30, brutalitaet=0),
            _f(name="b", weapon=5, energie=20, position=140, kraft=30, brutalitaet=0),
        ),
        (
            _f(name="x", weapon=0, energie=1, position=101),
            _f(name="y", weapon=0, energie=1, position=141),
        ),
    )

    transcript = []

    script = iter([("shoot", +1), ("shoot", +1)])

    def src(interaction):
        transcript.append((interaction.active_side, interaction.active_fighter, interaction.prompt))
        return next(script)

    # Each shot: two hit-factor draws (both non-zero) + one damage draw (0 -> 1 damage).
    rng = _StubRng(1, 1, 0, 1, 1, 0)
    result = run_fight(**_spec(sides=sides), input_source=src, rng=rng)

    assert transcript == [(1, 1, "action"), (1, 2, "action")]
    assert result.winner == 1
    assert rng.calls == [
        ("range", 5),
        ("range", 4),
        ("range", 10),
        ("range", 5),
        ("range", 4),
        ("range", 10),
    ]


def test_losses_are_visible_on_the_screen_that_follows_a_knockout():
    """The per-side loss counters (``v(1)``/``v(2)``, mf-prg.bas:30310) reach the client.

    Two enemies so the fight does NOT end on the first knockout — otherwise no further
    screen is yielded and the updated counter would never be observable.

    ``cpu_sides=()`` keeps this a hot-seat fight (U6): the point under test is that the
    loss counter reaches the CLIENT, so side 2 must be prompted rather than played by
    the AI (which would consume RNG draws and yield no screen — see
    ``tests/test_combat_ai.py`` for the CPU-branch behaviour itself).
    """
    sides = (
        (_f(name="a", weapon=5, position=100, brutalitaet=0),),
        (
            _f(name="x", weapon=0, energie=1, position=101),
            _f(name="y", weapon=0, energie=20, position=300),
        ),
    )
    screens = []

    def src(interaction):
        screens.append(interaction.to_json())
        # First activation drops enemy x; then surrender to end the fight.
        return ("shoot", +1) if len(screens) == 1 else ("surrender", None)

    run_fight(**_spec(sides=sides, cpu_sides=()), input_source=src, rng=_StubRng(1, 1, 0))
    assert screens[0]["losses"] == [0, 0]
    assert screens[1]["losses"] == [0, 1]
    # The downed fighter is still present in the payload, flagged rather than removed.
    assert screens[1]["sides"][1][0]["down"] is True
    assert screens[1]["sides"][1][0]["vitality"] == 0
