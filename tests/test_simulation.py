"""Per-side drivers + headless simulation — the four fight shapes (U6).

This unit split combat's *decision* from its *execution*: a driver
(:meth:`~engine.combat.CombatFight.ai_decide`, a policy callable) CHOOSES an
``(action, argument)`` off a read-only :class:`~engine.combat.CombatView`, and the
loop's single apply-block EXECUTES it. Every fight — human, AI, policy, hot-seat —
runs through the SAME shared activation loop (:func:`engine.interactions._drive_fight`),
whether a client is in it (:func:`engine.interactions._run_combat`) or not
(:func:`engine.interactions.simulate`).

The tests here cover the plan's four fight shapes plus the split's own invariants:
no double-execution, ``ai_decide`` purity, and the read-only view surface.

Cited behaviour:

- ``30110`` — a CPU side runs the AI instead of prompting the client.
- ``30400-30492`` — the AI decision block, now split into choose (``ai_decide``) and
  apply (``apply_action``).
- KTD-2 (``interactions.py``) — combat prompts are non-cancellable; a human quit/EOF
  surrenders rather than cancelling.
"""

from __future__ import annotations

import pytest

from data.game_configs.mafia_1920s.combat_rules import build_rules
from engine.combat import (
    STEP_RIGHT,
    CombatFight,
    CombatResult,
    CombatView,
)
from engine.interactions import (
    CANCEL,
    AiDriver,
    CombatScreen,
    HumanDriver,
    PolicyDriver,
    ReplayDriver,
    simulate,
)
from engine.rng import Rng
from engine.scenario import Scenario
from engine.state import CombatState
from tests.helpers import StubRng
from tests.helpers import combat_fighter as _f
from tests.helpers import run_fight


def _cell(row: int, col: int) -> int:
    """Linear combat-grid cell from (row, col) — the grid is 40 wide (CLAUDE.md)."""
    return row * 40 + col


def _scenario(*, sides, grid=(), rules=None, dir_memory=None, seed=None) -> Scenario:
    return Scenario(
        sides=sides,
        grid=grid,
        rules=build_rules() if rules is None else rules,
        dir_memory=dir_memory if dir_memory is not None else {0: -1},
        seed=seed,
    )


# --------------------------------------------------------------------------- #
# The four fight shapes                                                        #
# --------------------------------------------------------------------------- #
def test_human_vs_ai_behaves_exactly_as_today_the_fidelity_guard():
    """Side 1 human, side 2 AI: the client is asked ONLY for side 1 (30110), and a
    passive human loses to a ranged CPU. The pre-U6 default path, unchanged."""
    prompted_sides = []

    def input_source(interaction):
        assert isinstance(interaction, CombatScreen)
        prompted_sides.append(interaction.active_side)
        return ("pass", None)

    sides = (
        (_f(name="hero", weapon=5, energie=20, position=_cell(5, 10)),),
        (_f(name="thug", weapon=5, energie=20, position=_cell(5, 20)),),
    )
    result = run_fight(
        scenario=_scenario(sides=sides),
        input_source=input_source,
        rng=Rng(seed=1234),
    )
    assert set(prompted_sides) == {1}, "the client must only ever be asked for side 1"
    assert result.winner == 2, "a passive human should lose to the ranged CPU"


def test_ai_vs_ai_resolves_with_both_sides_acting():
    """Both sides CPU: the fight resolves headlessly and BOTH sides get to act
    (a side-1 CPU hunts side 2 via hostile_to, U4)."""
    sides = (
        (_f(name="a", weapon=6, energie=30, position=_cell(5, 10)),),
        (_f(name="b", weapon=6, energie=30, position=_cell(5, 25)),),
    )
    result = simulate(
        _scenario(sides=sides),
        {1: AiDriver(), 2: AiDriver()},
        rng=Rng(seed=7),
    )
    assert isinstance(result, CombatResult)
    assert result.winner in (1, 2)
    # Someone went down — the fight actually played out rather than stalling.
    assert result.losses[0] + result.losses[1] >= 1


def test_human_vs_human_prompts_for_both_sides():
    """A hot-seat fight (both sides human) prompts a CombatScreen for EACH side."""
    prompted_sides = []

    def input_source(interaction):
        prompted_sides.append(interaction.active_side)
        # side 1 passes, side 2 surrenders -> the fight ends after both have acted.
        return ("pass", None) if interaction.active_side == 1 else ("surrender", None)

    sides = (
        (_f(name="p1", weapon=5, energie=20, position=_cell(5, 10)),),
        (_f(name="p2", weapon=5, energie=20, position=_cell(5, 20)),),
    )
    result = run_fight(
        scenario=_scenario(sides=sides),
        drivers={1: HumanDriver(), 2: HumanDriver()},
        input_source=input_source,
        rng=StubRng(),
    )
    assert prompted_sides == [1, 2], "both sides must be prompted in a hot-seat fight"
    assert result.winner == 1  # side 2 surrendered


def test_policy_vs_ai_resolves_headlessly_with_no_input_source():
    """A policy side (always shoot right) vs an AI side runs with NO client at all."""
    policy = PolicyDriver(policy=lambda view: ("shoot", STEP_RIGHT))
    sides = (
        (_f(name="p", weapon=6, energie=30, position=_cell(5, 10)),),
        (_f(name="e", weapon=0, energie=10, position=_cell(5, 12)),),
    )
    result = simulate(
        _scenario(sides=sides),
        {1: policy, 2: AiDriver()},
        rng=Rng(seed=3),
    )
    assert result.winner == 1, "the shooting policy should win against a melee enemy"


# --------------------------------------------------------------------------- #
# Reproducibility + determinism                                               #
# --------------------------------------------------------------------------- #
def test_a_seeded_simulation_is_reproducible():
    """Same seed -> same CombatResult, run twice."""
    sides = (
        (_f(name="a", weapon=6, energie=30, position=_cell(5, 10)),),
        (_f(name="b", weapon=5, energie=20, position=_cell(5, 22)),),
    )
    sc = _scenario(sides=sides)
    r1 = simulate(sc, {1: AiDriver(), 2: AiDriver()}, rng=Rng(seed=99))
    r2 = simulate(sc, {1: AiDriver(), 2: AiDriver()}, rng=Rng(seed=99))
    assert r1 == r2


def test_scenario_seed_is_consumed_when_no_rng_is_given():
    """simulate() seeds its own Rng from scenario.seed, so two runs of the same
    seeded scenario resolve identically WITHOUT the caller passing an rng."""
    sides = (
        (_f(name="a", weapon=6, energie=30, position=_cell(5, 10)),),
        (_f(name="b", weapon=5, energie=20, position=_cell(5, 22)),),
    )
    sc = _scenario(sides=sides, seed=1234)
    r1 = simulate(sc, {1: AiDriver(), 2: AiDriver()})
    r2 = simulate(sc, {1: AiDriver(), 2: AiDriver()})
    assert r1 == r2


def test_a_zero_variance_weapon_forces_a_deterministic_outcome_across_seeds():
    """KTD-6: a zero-variance weapon (tg=0) makes damage deterministic, and a
    lopsided roster then forces the SAME winner across several seeds with NO scripted
    RNG draws — only a real seeded Rng, whose seed does not change the outcome."""
    # Five strong shooters with an INVENTED zero-variance weapon (tg=0 -> no damage
    # draw at all) vs one weak melee enemy. The stats ride on each fighter's own
    # equipment (amendment A1), so no weapon table is needed.
    strong = tuple(
        _f(
            name=f"s{i}",
            weapon=250,
            position=_cell(5, 10 + i),
            kraft=99,
            brutalitaet=99,
            energie=99,
            equipment={"ts": 7, "tg": 0, "range": 20},
        )
        for i in range(5)
    )
    weak = (_f(name="weak", weapon=0, energie=5, position=_cell(5, 30)),)
    sc = _scenario(sides=(strong, weak))

    winners = {
        simulate(sc, {1: AiDriver(), 2: AiDriver()}, rng=Rng(seed=seed)).winner for seed in range(6)
    }
    assert winners == {1}, f"a zero-variance lopsided fight was not deterministic: {winners}"


# --------------------------------------------------------------------------- #
# Failure modes — loud, not silent                                            #
# --------------------------------------------------------------------------- #
def test_simulate_raises_when_handed_a_human_driver():
    """A HumanDriver cannot run headlessly — simulate() rejects it up front with a
    clear error naming the side, distinct from an exhausted-answer failure."""
    sides = (
        (_f(name="a", weapon=6, energie=30, position=_cell(5, 10)),),
        (_f(name="b", weapon=6, energie=30, position=_cell(5, 25)),),
    )
    with pytest.raises(ValueError, match="HumanDriver"):
        simulate(_scenario(sides=sides), {1: HumanDriver(), 2: AiDriver()})


def test_an_exhausted_driver_response_fails_loudly():
    """A scripted policy whose answers run out raises rather than silently stalling —
    a distinct failure from the HumanDriver rejection above."""
    answers = iter([("pass", None)])  # only ONE answer, but the fight needs more

    def exhausting_policy(view):
        return next(answers)  # StopIteration when the single answer is used up

    sides = (
        (_f(name="p", weapon=0, energie=99, position=_cell(5, 10)),),
        (_f(name="e", weapon=0, energie=99, position=_cell(5, 30)),),
    )
    with pytest.raises((StopIteration, RuntimeError)):
        simulate(
            _scenario(sides=sides),
            {1: PolicyDriver(policy=exhausting_policy), 2: PolicyDriver(policy=exhausting_policy)},
            rng=Rng(seed=1),
        )


def test_a_non_human_driver_returning_an_unknown_action_fails_loudly():
    """A policy that returns an unrecognized action has a broken decide contract. Headless,
    re-prompting it would spin forever (no client to break out), so the loop raises. This
    is the 'invalid driver response fails loudly' scenario, distinct from an EXHAUSTED
    answer list (StopIteration) above."""
    sides = (
        (_f(name="p", weapon=0, energie=99, position=_cell(5, 10)),),
        (_f(name="e", weapon=0, energie=99, position=_cell(5, 30)),),
    )
    bad = PolicyDriver(policy=lambda view: ("nonsense_action", None))
    with pytest.raises(ValueError, match="unrecognized action"):
        simulate(_scenario(sides=sides), {1: bad, 2: AiDriver()}, rng=Rng(seed=1))


def test_a_non_human_driver_returning_an_illegal_move_fails_loudly():
    """A policy that returns an off-grid / blocked step is likewise a broken contract:
    a HUMAN re-prompts, but a non-human driver must fail loudly rather than re-decide the
    same illegal step forever."""
    sides = (
        (_f(name="p", weapon=0, energie=99, position=_cell(5, 10)),),
        (_f(name="e", weapon=0, energie=99, position=_cell(5, 30)),),
    )
    # STEP_RIGHT into a cell held by nothing is legal; instead force a wildly out-of-range
    # step so can_move_onto rejects it (position 410 + 999 is off the 0..520 grid).
    bad = PolicyDriver(policy=lambda view: ("move", 999))
    with pytest.raises(ValueError, match="illegal move"):
        simulate(_scenario(sides=sides), {1: bad, 2: AiDriver()}, rng=Rng(seed=1))


def test_replay_driver_is_reserved_but_not_functional_this_unit():
    """The replay kind is DECLARED (so U7 need not touch this dispatch again) but
    decide() raises — it is not functional in U6."""
    driver = ReplayDriver()
    assert driver.kind == "replay"
    with pytest.raises(NotImplementedError):
        driver.decide(object())


# --------------------------------------------------------------------------- #
# Surrender preserved (KTD-2)                                                  #
# --------------------------------------------------------------------------- #
def test_a_human_quit_or_eof_still_surrenders():
    """KTD-2: a combat prompt is non-cancellable — CANCEL / EOF from a human driver
    surrenders (the other side wins) rather than unwinding the handler."""
    sides = (
        (_f(name="hero", weapon=5, energie=20, position=_cell(5, 10)),),
        (_f(name="thug", weapon=5, energie=20, position=_cell(5, 20)),),
    )
    result = run_fight(
        scenario=_scenario(sides=sides),
        input_source=lambda interaction: CANCEL,
    )
    assert result.winner == 2, "side 1's surrender hands side 2 the win"


# --------------------------------------------------------------------------- #
# The decide/execute split — the invariants it exists to guarantee            #
# --------------------------------------------------------------------------- #
def _ai_fight(*, rng, target_col):
    """A side-2 CPU with a ranged weapon and a same-row side-1 target at ``target_col``."""
    return CombatFight(
        CombatState(
            sides=(
                (_f(name="victim", weapon=0, energie=20, position=_cell(5, target_col)),),
                (_f(name="cpu", weapon=6, position=_cell(5, 10)),),
            ),
            grid=(),
            active_side=2,
            active_fighter=1,
        ),
        rng=rng,
        rules=build_rules(),
    )


def test_no_double_execution_an_ai_shot_applies_damage_exactly_once():
    """The concrete regression the decide/execute split prevents.

    A CPU activation that SHOOTS must apply one hit's damage, not two. The buggy
    design (wiring an EXECUTING ``ai_take_turn`` behind a ``decide()`` and then letting
    the apply-block execute again) would fire twice. The split — ``ai_decide`` (pure)
    then a single ``apply_action`` — fires once.
    """
    # near-adjacent target (dx=1) so the 30415 coin flip is skipped: a shot that draws
    # only the two hit factors (both non-zero -> hit) and no damage draw (tg 12, draw 0).
    fight = _ai_fight(rng=StubRng(1, 1, 0), target_col=11)
    before = fight.sides[0][0].vitality

    # The SPLIT path: choose (no mutation), then apply once.
    action, argument = fight.ai_decide(fight.view())
    assert action == "shoot"
    fight.apply_action(action, argument)

    one_hit_damage = before - fight.sides[0][0].vitality
    assert one_hit_damage > 0
    # weapon 6: tg=12, damage draw 0, brutalitaet 30 -> int(0 + 30/10) + 1 = 4.
    assert one_hit_damage == 4, "a single AI shot must apply exactly ONE hit's damage"

    # And prove the buggy shape WOULD have doubled it, so the assertion above is load-bearing.
    buggy = _ai_fight(rng=StubRng(1, 1, 0, 1, 1, 0), target_col=11)
    before_b = buggy.sides[0][0].vitality
    outcome = buggy.ai_take_turn()  # executes the shot ONCE inside the wrapper
    if outcome["action"] == "shoot":
        buggy.apply_action("shoot", outcome["direction"])  # the erroneous second execution
    assert before_b - buggy.sides[0][0].vitality == 8, "the buggy double-exec baseline"


def test_no_double_execution_through_the_real_drive_loop():
    """The split's regression, exercised through the PRODUCTION dispatch (`_drive_fight`),
    not just a direct `apply_action` call.

    A fight defaults to side 1's activation, so the human (side 1) PASSES its first
    turn, side 2's CPU then fires one shot, and the human reads the target's vitality on
    its SECOND prompt — after precisely one AI activation through the real loop. A loop
    that applied the AI's shot twice (the concrete bug the decide/execute split prevents)
    would show 2x the damage here, where the synthetic `apply_action` test above cannot
    see the loop.
    """
    seen_vitality = []

    def input_source(interaction):
        seen_vitality.append(interaction.sides[0][0].vitality)
        # Pass the first turn (let the CPU act), surrender on the second.
        return ("pass", None) if len(seen_vitality) == 1 else ("surrender", None)

    sides = (
        # weapon 0 (melee) so the human never out-ranges; a passive target to be shot.
        (_f(name="target", weapon=0, energie=20, position=_cell(5, 11)),),
        (_f(name="cpu", weapon=6, position=_cell(5, 10)),),
    )
    run_fight(
        # side 2 (CPU) is near-adjacent + row-aligned -> fires right; rng hits, damage 0.
        scenario=_scenario(sides=sides, dir_memory={0: -1}),
        input_source=input_source,
        cpu_sides=(2,),
        rng=StubRng(1, 1, 0),
    )

    assert len(seen_vitality) >= 2, "the CPU turn did not return control to side 1"
    assert seen_vitality[0] == 20, "the target is untouched before the CPU acts"
    # weapon 6: tg=12, damage draw 0, brutalitaet 30 -> int(0 + 30/10) + 1 = 4. One shot.
    assert seen_vitality[1] == 16, "one AI shot through the loop must drop vitality by 4, not 8"


def test_ai_decide_is_pure_same_choice_twice_and_no_mutation():
    """Calling ai_decide twice on the same view returns the same choice and leaves the
    fight untouched — no position/vitality/down write, and (near-adjacent, so no coin
    flip) no rng draw either."""
    fight = _ai_fight(rng=StubRng(), target_col=11)  # empty rng: any draw would raise
    view = fight.view()

    before_sides = fight.sides
    before_dir = dict(fight.dir_memory)

    first = fight.ai_decide(view)
    second = fight.ai_decide(view)

    assert first == second, "ai_decide is not deterministic on an unchanged view"
    assert first == ("shoot", STEP_RIGHT)
    assert fight._rng.calls == [], "a near-adjacent decision must draw no rng"
    # No mutation of any kind.
    assert fight.sides == before_sides
    assert dict(fight.dir_memory) == before_dir
    assert fight.sides[0][0].vitality == 20
    assert fight.sides[0][0].down is False


def test_a_combat_view_cannot_mutate_the_fight():
    """A driver handed a CombatView has no mutators: shoot/try_move/advance_activation
    are absent from its surface, so it cannot bypass the single apply-block (U7)."""
    fight = _ai_fight(rng=StubRng(), target_col=11)
    view = fight.view()
    assert isinstance(view, CombatView)
    for mutator in ("shoot", "try_move", "advance_activation", "apply_action", "surrender"):
        assert not hasattr(view, mutator), f"CombatView must not expose {mutator!r}"
    # It DOES expose the read-only decision surface.
    for reader in ("sides", "grid", "active_side", "active_fighter", "active", "hostile_to"):
        assert hasattr(view, reader), f"CombatView must expose {reader!r}"


# --------------------------------------------------------------------------- #
# Characterization: split == pre-split, across representative AI cases          #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "target_col, expect_action",
    [
        (11, "shoot"),  # near-adjacent, row-aligned -> fire right
        (15, "shoot"),  # ranged, row-aligned, flip won -> fire right
    ],
)
def test_ai_decide_plus_dispatch_matches_ai_take_turn(target_col, expect_action):
    """For representative cases, ai_decide + apply produces the SAME board as the
    ai_take_turn wrapper does (the wrapper IS decide+apply, so this pins them equal)."""

    # A NeverMove-style rng: the 30415 flip (range(2)) answers 1 (never forces a move);
    # the hit factors + damage follow. Same script for both paths -> identical outcome.
    def script():
        return StubRng(1, 1, 1, 0)  # flip=1, hit 1,1, damage 0 (only drawn if needed)

    via_wrapper = _ai_fight(rng=script(), target_col=target_col)
    wrapped = via_wrapper.ai_take_turn()

    via_split = _ai_fight(rng=script(), target_col=target_col)
    action, argument = via_split.ai_decide(via_split.view())
    via_split.apply_action(action, argument, record_dir_memory=True)

    assert wrapped["action"] == expect_action
    assert action == expect_action
    # Same resulting board: the target's vitality/down match between the two paths.
    assert via_wrapper.sides[0][0].vitality == via_split.sides[0][0].vitality
    assert via_wrapper.sides[0][0].down == via_split.sides[0][0].down


# --------------------------------------------------------------------------- #
# Driver derivation + override                                                #
# --------------------------------------------------------------------------- #
def test_explicit_drivers_override_cpu_sides():
    """An explicit drivers map OVERRIDES cpu_sides (two knobs on one axis never merge):
    side 2 is made a HUMAN even though cpu_sides would default it to CPU."""
    prompted_sides = []

    def input_source(interaction):
        prompted_sides.append(interaction.active_side)
        return ("surrender", None) if interaction.active_side == 2 else ("pass", None)

    sides = (
        (_f(name="p1", weapon=5, energie=20, position=_cell(5, 10)),),
        (_f(name="p2", weapon=5, energie=20, position=_cell(5, 20)),),
    )
    run_fight(
        scenario=_scenario(sides=sides),
        drivers={1: HumanDriver(), 2: HumanDriver()},
        input_source=input_source,
        rng=StubRng(),
    )
    # Side 2 was prompted -> the explicit HumanDriver won over the cpu_sides default.
    assert 2 in prompted_sides
