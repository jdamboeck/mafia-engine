"""Tests for the CPU combat AI — targeting, attack/move decision, pursuit (U6).

The AI is a port of the BASIC decision block ``mf-prg.bas:30400-30492`` **plus** the
machine-code target-selection routine ``cr`` (``$C000``, disassembled in
``research-data/verification/ml-core-disassembly.yaml``), which the BASIC only ever
sees through four ``peek(ua..ua+3)`` bytes.

Cited behaviour under test:

- ``cr`` ($C000) — nearest side-1 fighter by the ``$ECF0`` distance metric
  ``dy*40 + dx``, returning signed step deltas (``x`` = ±1/0, ``y`` = ±40/0) and the
  two absolute distances.
- ``30410`` — ``abs(dx)=1 or abs(dy)=1`` (near-adjacent) skips the roll and goes
  straight to the attack branch.
- ``30415`` — otherwise: a 50% roll (``int(rnd(1)*2)=0``) **or** a melee weapon
  (``gw<4``) forces the move branch.
- ``30420``/``30421``/``30425`` — fire only along a shared column (``x=0``) or row
  (``y=0``); a diagonal falls through to the move branch.
- ``30450-30462`` — move horizontally then vertically toward the target, never the
  exact reverse of the last step (``ri(f)``), with perpendicular sidestep fallbacks.
- ``30490``/``30492`` — a step is validated against the movement obstruction set and,
  on success, recorded in ``ri(f)`` and ends the activation.
- ``30110`` — the driver runs the AI instead of prompting when the active side is CPU.
"""

from __future__ import annotations

import pytest

from data.game_configs.mafia_1920s.combat_rules import build_rules
from engine.combat import (
    RANGE_MELEE,
    STEP_DOWN,
    STEP_LEFT,
    STEP_RIGHT,
    STEP_UP,
    ai_target,
)
from engine.interactions import CombatScreen
from tests.helpers import WEAPON_TABLE, StubRng, build_fight, run_fight
from tests.helpers import combat_fighter as _f

# --------------------------------------------------------------------------- #
# Helpers (mirroring tests/test_combat_loop.py's idiom)                        #
# --------------------------------------------------------------------------- #

_StubRng = StubRng


class _NeverMoveRng(_StubRng):
    """Always answers the 30415 coin flip with 1 (not 0) — i.e. never *forces* a move."""

    def range(self, n):
        if n == 2:
            self.calls.append(("range", 2))
            return 1
        return super().range(n)


#: This module exercises side 2/the CPU acting by default — unlike
#: tests/test_combat_loop.py's side-1 default — because the AI decision layer under
#: test here (ai_take_turn et al.) only ever runs for the active fighter; see
#: tests.helpers.build_fight's docstring for why each file keeps its own default
#: rather than sharing one.
def _fight(*, side1, side2, grid=(), rng=None, dir_memory=None, weapon_stats=None, active=(2, 1)):
    return build_fight(
        side1=side1,
        side2=side2,
        grid=grid,
        rng=rng,
        dir_memory=dir_memory,
        weapon_stats=weapon_stats,
        active=active,
    )


def _cell(row, col):
    """Linear combat-grid cell from (row, col) — the grid is 40 wide (CLAUDE.md)."""
    return row * 40 + col


# --------------------------------------------------------------------------- #
# Target selection — the `cr` machine-code routine ($C000)                      #
# --------------------------------------------------------------------------- #
def test_target_is_the_nearest_side_one_fighter_by_dy40_plus_dx():
    """$ECF0 metric: LDA $ECF0,X (X=abs dy) gives dy*40, ADC abs(dx) -> dy*40+dx."""
    # Candidate A: same row, 5 columns away  -> 0*40 + 5 = 5.
    # Candidate B: 1 row away, same column   -> 1*40 + 0 = 40.
    # A is nearer under the metric even though B is "closer" in Chebyshev terms.
    near = _f(name="near", position=_cell(5, 15))
    far = _f(name="far", position=_cell(4, 10))
    fight = _fight(side1=[far, near], side2=[_f(name="cpu", position=_cell(5, 10))])
    target = ai_target(fight)
    assert target is not None
    assert target.index == 1  # `near`
    assert (target.abs_dx, target.abs_dy) == (5, 0)


def test_target_direction_codes_are_step_deltas():
    """cr returns ua+0/ua+1 which the BASIC converts to ±1 and ±40 step deltas
    (mf-prg.bas:30405: x=peek(ua)-1, y=peek(ua+1)-40)."""
    cpu = _f(name="cpu", position=_cell(6, 20))
    # target up-left of the CPU fighter
    fight = _fight(side1=[_f(position=_cell(4, 18))], side2=[cpu])
    t = ai_target(fight)
    assert t.x == STEP_LEFT
    assert t.y == STEP_UP
    # target down-right
    fight = _fight(side1=[_f(position=_cell(8, 22))], side2=[cpu])
    t = ai_target(fight)
    assert t.x == STEP_RIGHT
    assert t.y == STEP_DOWN
    # aligned on both axes -> zero deltas
    fight = _fight(side1=[_f(position=_cell(6, 25))], side2=[cpu])
    t = ai_target(fight)
    assert (t.x, t.y) == (STEP_RIGHT, 0)


def test_downed_side_one_fighters_are_not_targeted():
    fight = _fight(
        side1=[
            _f(name="down", position=_cell(5, 11), down=True),
            _f(name="up", position=_cell(5, 20)),
        ],
        side2=[_f(name="cpu", position=_cell(5, 10))],
    )
    t = ai_target(fight)
    assert t.index == 1


def test_no_target_when_side_one_is_wiped():
    fight = _fight(
        side1=[_f(position=_cell(5, 11), down=True)],
        side2=[_f(position=_cell(5, 10))],
    )
    assert ai_target(fight) is None


def test_the_ai_hunts_side_one_even_when_side_two_acts_second():
    """cr hardcodes colour low-nibble 2, and mf-prg.bas:30010's `2-4*(i=2)` paints
    side 1 with colour 2 — so the CPU always hunts side 1, never its own side."""
    fight = _fight(
        side1=[_f(name="player", position=_cell(5, 30))],
        side2=[_f(name="cpu", position=_cell(5, 10)), _f(name="ally", position=_cell(5, 11))],
    )
    t = ai_target(fight)
    assert t.side == 1
    assert t.index == 0


def test_a_side_one_cpu_fighter_hunts_side_two_not_its_own_side():
    """U4: when SIDE 1 itself is CPU-driven (a shape ``cpu_sides`` permits though the
    original never produces it), the AI must hunt whoever is hostile to the ACTIVE side
    — side 2 — not the constant side 1. The side-2-hunts-side-1 path stays untouched
    (pinned above); this closes the case the constant got wrong.
    """
    fight = _fight(
        side1=[_f(name="cpu", position=_cell(5, 10))],
        side2=[_f(name="enemy", position=_cell(5, 30))],
        active=(1, 1),  # side 1 is the CPU actor
    )
    t = ai_target(fight)
    assert t is not None
    assert t.side == 2  # hostile to the active side, not the active side itself
    assert (t.x, t.y) != (0, 0)  # a real direction toward the enemy, not "stay put"


def test_a_side_one_cpu_fighter_with_a_living_teammate_targets_the_enemy_not_the_teammate():
    """Self-exclusion falls out of side-relative derivation: a side is never hostile to
    itself, so a teammate is never a candidate (U4). An identity check on the active
    fighter alone would still wrongly allow targeting a teammate."""
    fight = _fight(
        side1=[
            _f(name="cpu", position=_cell(5, 10)),
            _f(name="teammate", position=_cell(5, 11)),  # adjacent — nearest by the metric
        ],
        side2=[_f(name="enemy", position=_cell(5, 30))],
        active=(1, 1),
    )
    t = ai_target(fight)
    assert t is not None
    assert t.side == 2  # the far enemy, never the adjacent teammate on the active's own side
    assert t.index == 0


def test_hostile_to_is_the_two_party_complement():
    """U4: the reference title has two fixed sides, so hostility is each side's
    complement — N-party-shaped (a tuple) but exactly ``(2,)``/``(1,)`` here."""
    fight = _fight(
        side1=[_f(name="a", position=_cell(5, 10))],
        side2=[_f(name="b", position=_cell(5, 30))],
    )
    assert fight.hostile_to(1) == (2,)
    assert fight.hostile_to(2) == (1,)


# --------------------------------------------------------------------------- #
# Attack branch (mf-prg.bas:30410, 30420, 30421, 30425)                        #
# --------------------------------------------------------------------------- #
def test_near_adjacent_skips_the_fifty_percent_roll_entirely():
    """30410 jumps to 30420 BEFORE the 30415 roll — an adjacent enemy is never
    subject to the coin flip."""
    rng = _StubRng(1, 1, 0)  # only the hit/damage rolls; no range(2) queued
    fight = _fight(
        side1=[_f(name="player", weapon=0, energie=20, position=_cell(5, 11))],
        side2=[_f(name="cpu", weapon=5, position=_cell(5, 10))],
        rng=rng,
    )
    outcome = fight.ai_take_turn()
    assert outcome["action"] == "shoot"
    assert ("range", 2) not in rng.calls, "the 50% roll must not be drawn when adjacent"


def test_column_aligned_ai_fires_vertically():
    """30420: `ifx=0thenx=y:goto30215` — column alignment fires along y."""
    fight = _fight(
        side1=[_f(name="player", weapon=0, energie=20, position=_cell(3, 10))],
        side2=[_f(name="cpu", weapon=5, position=_cell(6, 10))],
        rng=_NeverMoveRng(1, 1, 0),
    )
    outcome = fight.ai_take_turn()
    assert outcome["action"] == "shoot"
    assert outcome["direction"] == STEP_UP
    assert outcome["result"]["hit"] is True


def test_row_aligned_ai_fires_horizontally():
    """30421 falls through to 30425 (`goto30215`) only when y=0 — a shared row."""
    fight = _fight(
        side1=[_f(name="player", weapon=0, energie=20, position=_cell(5, 16))],
        side2=[_f(name="cpu", weapon=5, position=_cell(5, 10))],
        rng=_NeverMoveRng(1, 1, 0),
    )
    outcome = fight.ai_take_turn()
    assert outcome["action"] == "shoot"
    assert outcome["direction"] == STEP_RIGHT


def test_diagonal_target_is_never_fired_at_and_closes_instead():
    """30421: `ify<>0goto30450` — with x<>0 too, the target is diagonal, so the AI
    moves instead of firing (the original has no diagonal shot)."""
    fight = _fight(
        side1=[_f(name="player", position=_cell(9, 20))],
        side2=[_f(name="cpu", weapon=5, position=_cell(5, 10))],
        rng=_NeverMoveRng(),  # coin flip says "don't force a move" — yet it still moves
    )
    outcome = fight.ai_take_turn()
    assert outcome["action"] == "move"


# --------------------------------------------------------------------------- #
# The 50% move-vs-attack roll (mf-prg.bas:30415)                               #
# --------------------------------------------------------------------------- #
def test_fifty_percent_roll_of_zero_forces_the_move_branch():
    """30415: `ifint(rnd(1)*2)=0...goto30450`."""
    rng = _StubRng(0)  # int(rnd*2)=0 -> move
    fight = _fight(
        side1=[_f(name="player", position=_cell(5, 20))],
        side2=[_f(name="cpu", weapon=5, position=_cell(5, 10))],
        rng=rng,
    )
    outcome = fight.ai_take_turn()
    assert outcome["action"] == "move"
    assert rng.calls[0] == ("range", 2), "the roll is int(rnd(1)*2)"


def test_fifty_percent_roll_of_one_lets_a_ranged_ai_fire():
    rng = _StubRng(1, 1, 1, 0)  # roll=1 (no forced move), then hit/hit/damage
    fight = _fight(
        side1=[_f(name="player", weapon=0, energie=20, position=_cell(5, 20))],
        side2=[_f(name="cpu", weapon=5, position=_cell(5, 10))],
        rng=rng,
    )
    outcome = fight.ai_take_turn()
    assert outcome["action"] == "shoot"


@pytest.mark.parametrize("weapon", [0, 1, 2, 3])
def test_melee_weapons_always_take_the_move_branch(weapon):
    """30415: `...orgw(ks(s),f)<4goto30450` — weapon ids 0..3 always close."""
    fight = _fight(
        side1=[_f(name="player", position=_cell(5, 20))],
        side2=[_f(name="cpu", weapon=weapon, position=_cell(5, 10))],
        rng=_NeverMoveRng(),  # the coin flip alone would allow firing
    )
    outcome = fight.ai_take_turn()
    assert outcome["action"] == "move"


@pytest.mark.parametrize("weapon", [4, 5, 6, 7, 8])
def test_non_melee_weapons_are_not_forced_to_close(weapon):
    fight = _fight(
        side1=[_f(name="player", weapon=0, energie=99, position=_cell(5, 20))],
        side2=[_f(name="cpu", weapon=weapon, position=_cell(5, 10))],
        rng=_NeverMoveRng(1, 1, 0),
    )
    outcome = fight.ai_take_turn()
    assert outcome["action"] == "shoot"


@pytest.mark.parametrize("weapon,ts,tg,wrange", WEAPON_TABLE)
def test_the_close_distance_branch_fires_for_exactly_the_old_weapon_id_set(weapon, ts, tg, wrange):
    """Characterization: reach-derived melee picks the same weapons ``w<4`` did.

    The two tests above assert the two halves against literal id lists; this one
    re-derives the split from the branch itself for every id at once, so a change to
    a weapon's ``range`` that silently moved it across the melee line would be caught
    here rather than only in whichever list stopped being exhaustive.
    """
    fight = _fight(
        side1=[_f(name="player", weapon=0, energie=99, position=_cell(5, 20))],
        side2=[_f(name="cpu", weapon=weapon, position=_cell(5, 10))],
        rng=_NeverMoveRng(1, 1, 0),  # the coin flip alone never forces a move
    )
    outcome = fight.ai_take_turn()
    forced_to_close = outcome["action"] == "move"
    assert forced_to_close == (weapon < 4)
    assert forced_to_close == (wrange <= RANGE_MELEE)


def test_an_invented_melee_weapon_forces_the_cpu_to_close():
    """The AI's melee branch is attribute-driven: id 42 is in no weapon table.

    It closes solely because the config said its reach is one cell — the engine no
    longer knows this game's weapon ids at all.
    """
    stats = {42: (5, 10, 1), 43: (5, 10, 12)}
    for weapon, expected in ((42, "move"), (43, "shoot")):
        fight = _fight(
            side1=[_f(name="player", weapon=0, energie=99, position=_cell(5, 20))],
            side2=[_f(name="cpu", weapon=weapon, position=_cell(5, 10))],
            rng=_NeverMoveRng(1, 1, 0),
            weapon_stats=stats,
        )
        assert fight.ai_take_turn()["action"] == expected, f"weapon {weapon}"


def test_melee_ai_closes_to_adjacency_then_attacks():
    """A melee CPU fighter walks in and, once |dx|=1, switches to the attack branch.

    The target is placed to the LEFT so the 30020 `ri=-1` seed permits the approach
    (a rightward pursuit on a shared row is the deadlock shape — see its own test).
    """
    fight = _fight(
        side1=[_f(name="player", weapon=0, energie=99, position=_cell(5, 10))],
        side2=[_f(name="cpu", weapon=0, position=_cell(5, 16))],
        rng=_NeverMoveRng(1, 1, 0),
    )
    actions = []
    for _ in range(6):
        actions.append(fight.ai_take_turn()["action"])
        if actions[-1] == "shoot":
            break
    assert actions[-1] == "shoot", f"melee AI never engaged: {actions}"
    assert actions[:-1] == ["move"] * (len(actions) - 1)
    # It stopped one cell short — 30410's adjacency test is |dx|=1, not |dx|=0.
    assert fight.sides[1][0].position == _cell(5, 11)


# --------------------------------------------------------------------------- #
# Movement + direction memory (mf-prg.bas:30450-30462, 30490-30492)            #
# --------------------------------------------------------------------------- #
def test_a_committed_step_is_recorded_in_direction_memory():
    """30492: `ri(f)=p`."""
    fight = _fight(
        side1=[_f(position=_cell(5, 20))],
        side2=[_f(name="cpu", weapon=0, position=_cell(5, 10))],
        rng=_NeverMoveRng(),
        dir_memory={0: STEP_RIGHT},  # a rightward step is already recorded
    )
    fight.ai_take_turn()
    assert fight.sides[1][0].position == _cell(5, 11)
    assert fight.dir_memory[0] == STEP_RIGHT


def test_the_minus_one_seed_blocks_a_rightward_first_step():
    """FIDELITY QUIRK, ported deliberately. 30020 seeds `ri(i)=-1` for every CPU
    fighter, and -1 is a REAL leftward step as far as every guard is concerned:
    30456 spells the rightward guard as `ri(f)<>-1` with a literal. So a
    freshly-spawned enemy cannot open the fight by stepping right, even when its
    target lies directly to the right.

    The research interpretation glosses the seed as "no last move yet", but the code
    draws no such distinction — it compares -1 exactly like a recorded -1. Per KTD-9
    the decompiled code wins over the prose gloss. This matters in practice because
    the side anchors put side 2 (111) to the LEFT of side 1 (129), so the seed
    systematically deflects an enemy's opening step onto the vertical axis.
    """
    # Target directly to the right on the same row: the horizontal approach at 30450
    # is exactly what the seed forbids, so 30451's vertical approach runs instead.
    fight = _fight(
        side1=[_f(position=_cell(9, 20))],
        side2=[_f(name="cpu", weapon=0, position=_cell(5, 10))],
        rng=_NeverMoveRng(),
    )
    assert fight.dir_memory.get(0, -1) == -1
    fight.ai_take_turn()
    assert fight.sides[1][0].position == _cell(6, 10), "seed must deflect onto the y axis"

    # With the seed cleared to a rightward step, the horizontal approach runs first.
    fight = _fight(
        side1=[_f(position=_cell(9, 20))],
        side2=[_f(name="cpu", weapon=0, position=_cell(5, 10))],
        rng=_NeverMoveRng(),
        dir_memory={0: STEP_RIGHT},
    )
    fight.ai_take_turn()
    assert fight.sides[1][0].position == _cell(5, 11)


def test_the_exact_reverse_of_the_last_step_is_forbidden():
    """30450's `ri(f)<>(1+2*(x=1))` reduces to `ri(f) <> -x`, corroborated by the
    literal sidestep guards at 30456/30457 (`ri(f)<>-1` before `p=1`, `ri(f)<>1`
    before `p=-1`) and 30461/30462 (`ri(f)<>-40` before `p=40`)."""
    # The CPU stands to the RIGHT of its target, so the step toward it is LEFT (-1),
    # but its memory says it just stepped RIGHT (+1) — the reverse is forbidden.
    fight = _fight(
        side1=[_f(position=_cell(5, 10))],
        side2=[_f(name="cpu", weapon=0, position=_cell(5, 20))],
        rng=_NeverMoveRng(),
        dir_memory={0: STEP_RIGHT},
    )
    fight.ai_take_turn()
    assert fight.sides[1][0].position != _cell(5, 19), "stepped the forbidden reverse"


def test_vertical_reverse_guard_uses_the_row_width():
    """30451's `ri(f)<>(40+80*(y=1))` reduces to `ri(f) <> -y` with y = ±40."""
    fight = _fight(
        side1=[_f(position=_cell(2, 10))],  # directly above -> step is UP (-40)
        side2=[_f(name="cpu", weapon=0, position=_cell(8, 10))],
        rng=_NeverMoveRng(),
        dir_memory={0: STEP_DOWN},  # just came from above; UP is the reverse
    )
    fight.ai_take_turn()
    assert fight.sides[1][0].position != _cell(7, 10)


def test_horizontal_is_tried_before_vertical():
    """30450 (horizontal) runs before 30451 (vertical)."""
    fight = _fight(
        side1=[_f(position=_cell(9, 20))],  # down-right of the CPU
        side2=[_f(name="cpu", weapon=0, position=_cell(5, 10))],
        rng=_NeverMoveRng(),
        dir_memory={0: STEP_RIGHT},  # clear the -1 seed, which would block right
    )
    fight.ai_take_turn()
    assert fight.sides[1][0].position == _cell(5, 11), "must step horizontally first"


def test_a_blocked_horizontal_step_falls_back_to_the_vertical_approach():
    """30450's gosub 30490 returns with p<>0 when the cell is not walkable, so the
    line's `ifp=0thenreturn` does NOT fire and 30451 is attempted next."""
    grid = [32] * 521
    grid[_cell(5, 11)] = 160  # wall directly right of the CPU fighter
    fight = _fight(
        side1=[_f(position=_cell(9, 20))],
        side2=[_f(name="cpu", weapon=0, position=_cell(5, 10))],
        grid=grid,
        rng=_NeverMoveRng(),
    )
    fight.ai_take_turn()
    assert fight.sides[1][0].position == _cell(6, 10), "should fall back to the down step"


def test_vertical_sidesteps_run_when_only_the_horizontal_approach_was_available():
    """30455 -> 30460 -> 30461/30462. The target is on the CPU's own ROW, so `y=0`
    and only the horizontal approach exists; 30455's gate sends the fighter to the
    VERTICAL (perpendicular) sidesteps, and 30460 falls through because `y=0`."""
    grid = [32] * 521
    grid[_cell(5, 11)] = 160  # the horizontal approach is blocked by a wall
    fight = _fight(
        side1=[_f(position=_cell(5, 20))],  # same row, to the right
        side2=[_f(name="cpu", weapon=0, position=_cell(5, 10))],
        grid=grid,
        rng=_NeverMoveRng(),
        dir_memory={0: STEP_RIGHT},  # clear the -1 seed so 30450 is "available"
    )
    fight.ai_take_turn()
    # 30461 tries DOWN (p=40) first of the two vertical sidesteps.
    assert fight.sides[1][0].position == _cell(6, 10)


def test_horizontal_sidesteps_run_when_the_horizontal_approach_was_unavailable():
    """30455's gate is FALSE (x=0 — the target shares the CPU's column), so the
    routine falls into the HORIZONTAL sidesteps at 30456/30457 instead."""
    grid = [32] * 521
    grid[_cell(6, 10)] = 160  # the vertical approach (down) is blocked
    fight = _fight(
        side1=[_f(position=_cell(9, 10))],  # same column, below
        side2=[_f(name="cpu", weapon=0, position=_cell(5, 10))],
        grid=grid,
        rng=_NeverMoveRng(),
        dir_memory={0: STEP_DOWN},  # -1 would block the rightward sidestep at 30456
    )
    fight.ai_take_turn()
    # 30456 tries RIGHT (p=1) first.
    assert fight.sides[1][0].position == _cell(5, 11)


def test_no_sidestep_when_both_approach_axes_were_available_but_blocked():
    """30455 -> 30460 -> 30465. When BOTH approach axes were available (and both
    happened to be blocked by scenery), 30460 jumps straight to the exit — the AI
    takes NO sidestep at all and burns the activation standing still. This is the
    original's own logic, not a port shortcut: the sidesteps exist only to unstick a
    fighter that had a single approach axis to begin with.
    """
    grid = [32] * 521
    grid[_cell(5, 11)] = 160  # right blocked (the horizontal approach)
    grid[_cell(4, 10)] = 160  # up blocked (the vertical approach)
    fight = _fight(
        side1=[_f(position=_cell(1, 20))],  # up-right of the CPU -> both axes non-zero
        side2=[_f(name="cpu", weapon=0, position=_cell(5, 10))],
        grid=grid,
        rng=_NeverMoveRng(),
        dir_memory={0: STEP_RIGHT},
    )
    outcome = fight.ai_take_turn()
    assert outcome["action"] == "none"
    assert fight.sides[1][0].position == _cell(5, 10)


def test_a_boxed_in_fighter_stays_put_and_still_consumes_its_activation():
    """30465 `return` — the AI routine can end with no step taken; 30110's
    `gosub30400:goto30105` advances the cursor regardless."""
    grid = [32] * 521
    for step in (STEP_LEFT, STEP_RIGHT, STEP_UP, STEP_DOWN):
        grid[_cell(5, 10) + step] = 160
    fight = _fight(
        side1=[_f(position=_cell(9, 20))],
        side2=[_f(name="cpu", weapon=0, position=_cell(5, 10))],
        grid=grid,
        rng=_NeverMoveRng(),
    )
    outcome = fight.ai_take_turn()
    assert outcome["action"] == "none"
    assert fight.sides[1][0].position == _cell(5, 10)


def test_a_fighter_blocks_the_ai_step_like_scenery_does():
    """30490 reuses the movement obstruction set (codes 32/96 only), and a standing
    fighter occupies its cell (char 193) — so an ally blocks the approach."""
    fight = _fight(
        side1=[_f(position=_cell(9, 20))],
        side2=[
            _f(name="cpu", weapon=0, position=_cell(5, 10)),
            _f(name="ally", position=_cell(5, 11)),
        ],
        rng=_NeverMoveRng(),
        dir_memory={0: STEP_RIGHT},  # clear the -1 seed so the ally is what blocks
    )
    fight.ai_take_turn()
    assert fight.sides[1][0].position == _cell(6, 10), "blocked right -> fall back to down"


# --------------------------------------------------------------------------- #
# Pursuit convergence (the anti-oscillation property ri() exists for)          #
# --------------------------------------------------------------------------- #
def test_pursuit_on_an_open_grid_converges_without_oscillation():
    """A melee CPU fighter chasing a stationary target must strictly reduce the
    distance and never revisit a cell (direction memory forbids the reverse step)."""
    target_cell = _cell(2, 30)
    fight = _fight(
        side1=[_f(name="player", weapon=0, energie=99, position=target_cell)],
        side2=[_f(name="cpu", weapon=0, position=_cell(10, 4))],
        rng=_NeverMoveRng(1, 1, 0),
    )
    seen = [fight.sides[1][0].position]
    for _ in range(60):
        outcome = fight.ai_take_turn()
        if outcome["action"] == "shoot":
            break
        pos = fight.sides[1][0].position
        assert pos not in seen, f"AI revisited cell {pos}; path={seen}"
        seen.append(pos)
    else:
        raise AssertionError(f"pursuit never engaged in 60 activations; path={seen}")
    # It closed from 8 rows and 26 columns away to adjacency.
    assert len(seen) < 40


def test_pursuit_closes_the_distance_on_an_open_grid():
    """On an OPEN grid every approach step succeeds, so the cr metric strictly falls
    each activation until the target is adjacent. (This is not a general invariant of
    the routine: 30460's perpendicular sidesteps can legitimately increase the metric
    when scenery is in the way — see the sidestep tests.)"""
    # Target up-LEFT of the CPU, so the 30020 `ri=-1` seed permits the very first
    # approach step (the seed only forbids a rightward opening — see the seed test).
    fight = _fight(
        side1=[_f(name="player", weapon=0, energie=99, position=_cell(2, 5))],
        side2=[_f(name="cpu", weapon=0, position=_cell(10, 30))],
        rng=_NeverMoveRng(1, 1, 0),
    )

    def metric():
        cpu = fight.sides[1][0].position
        player = fight.sides[0][0].position
        dx = abs(cpu % 40 - player % 40)
        dy = abs(cpu // 40 - player // 40)
        return dy * 40 + dx

    last = metric()
    engaged = False
    for _ in range(40):
        if fight.ai_take_turn()["action"] == "shoot":
            engaged = True
            break
        now = metric()
        assert now < last, f"open-grid pursuit did not close: {last} -> {now}"
        last = now
    assert engaged, "the melee CPU never reached its target"


def test_same_row_rightward_pursuit_walks_left_and_wraps_to_the_previous_row():
    """The `ri=-1` seed's most visible consequence, ported faithfully.

    `ri` is written in exactly two places in the whole source: the ``-1`` seed at
    ``30020`` and ``ri(f)=p`` at ``30492`` — there is no neutral/clearing write. So a
    CPU fighter on its target's ROW with the target to its RIGHT can never step right:

    - ``30450`` (approach right) is blocked by ``ri=-1``;
    - ``30451`` is skipped because ``y=0``;
    - ``30455``'s gate is false (the horizontal approach was NOT available), so the
      routine falls into the HORIZONTAL sidesteps at ``30456``/``30457``;
    - ``30456`` (step right) is blocked by the same ``ri=-1``;
    - ``30457`` (step left) succeeds and writes ``ri=-1`` again.

    It therefore walks LEFT, away from its target — but the grid is addressed
    **linearly** with no per-row clamp (``30490`` bounds only ``q<0 or q>520``), so the
    walk WRAPS into the previous row, which gives it a non-zero ``dy`` and unsticks the
    pursuit. The fight still converges; it just takes the long way around.
    """
    fight = _fight(
        side1=[_f(name="player", weapon=0, energie=99, position=_cell(6, 30))],
        side2=[_f(name="cpu", weapon=0, position=_cell(6, 5))],
        rng=_NeverMoveRng(1, 1, 0),
    )
    for _ in range(5):
        assert fight.ai_take_turn()["direction"] == STEP_LEFT
    assert fight.sides[1][0].position == _cell(6, 0)
    # The next left step wraps to the end of row 5 — the linear-addressing property.
    fight.ai_take_turn()
    assert fight.sides[1][0].position == _cell(5, 39)
    # ...and from there the pursuit converges and engages.
    for _ in range(30):
        if fight.ai_take_turn()["action"] == "shoot":
            break
    else:
        raise AssertionError("wrapped pursuit never engaged")


def test_row_zero_rightward_pursuit_stalls_at_cell_zero_forever():
    """THE ONE NON-TERMINATING SHAPE, ported faithfully. See the U6 report / board issue.

    Row 0 is the single case where the leftward walk above has nowhere to wrap TO:
    at cell 0 the left step fails ``30490``'s ``q<0`` bound, the rightward approach and
    the rightward sidestep are both blocked by ``ri=-1``, and with ``y=0`` neither
    vertical line is reachable. The fighter idles at cell 0 for the rest of the fight.

    An exhaustive sweep of every same-row start pair on all 13 rows finds this shape on
    **row 0 only** (741 of 741 non-engaging cases), always with the target to the CPU's
    right. It is unreachable from the real spawn anchors, which place both sides on rows
    4-7 — hence every seeded driver-level fight below terminates.

    Pinned rather than fixed: the original has no turn cap and no escape here, so
    "fixing" it would be an invented behaviour. Do NOT add a turn cap to paper over it.
    """
    fight = _fight(
        side1=[_f(name="player", weapon=0, energie=99, position=_cell(0, 20))],
        side2=[_f(name="cpu", weapon=0, position=_cell(0, 5))],
        rng=_NeverMoveRng(1, 1, 0),
    )
    for _ in range(5):
        assert fight.ai_take_turn()["direction"] == STEP_LEFT
    assert fight.sides[1][0].position == 0
    for _ in range(10):
        assert fight.ai_take_turn()["action"] == "none"
    assert fight.sides[1][0].position == 0


def test_the_real_spawn_anchors_never_produce_the_row_zero_stall():
    """Guard on the claim above: both spawn anchors sit well clear of row 0."""
    from engine.combat import SIDE1_ANCHOR, SIDE2_ANCHOR, placement_positions

    rows = {p // 40 for p in placement_positions(SIDE2_ANCHOR, 10)}
    rows |= {p // 40 for p in placement_positions(SIDE1_ANCHOR, 10)}
    assert 0 not in rows, f"a spawn slot landed on row 0: {sorted(rows)}"


# --------------------------------------------------------------------------- #
# Driver integration — the CPU-side branch (mf-prg.bas:30110)                  #
# --------------------------------------------------------------------------- #
def _ai_spec(**kw):
    base = dict(
        sides=(
            (_f(name="hero", weapon=5, energie=20, position=_cell(5, 10), brutalitaet=0),),
            (_f(name="thug", weapon=5, energie=20, position=_cell(5, 20)),),
        ),
        grid=(),
        rules=build_rules(),
    )
    base.update(kw)
    return base


def test_the_driver_never_prompts_the_client_for_the_cpu_side():
    """30110: `ifks(s)=0thengosub30400:goto30105` — the CPU side runs the AI
    routine INSTEAD of the key read at 30125. Driven through run_fight (U6, R14)."""
    prompted_sides = []

    def src(interaction):
        assert isinstance(interaction, CombatScreen)
        prompted_sides.append(interaction.active_side)
        return ("surrender", None)

    run_fight(**_ai_spec(), input_source=src, rng=_NeverMoveRng(1, 1, 0))
    assert prompted_sides == [1], "the client must only ever be asked for side 1"


def test_a_full_seeded_ai_vs_player_fight_reaches_a_winner():
    """End-to-end at driver level: the player passes every activation while a ranged
    CPU fighter shoots it down. The fight must terminate with a winner."""
    from engine.rng import Rng

    result = run_fight(
        **_ai_spec(
            sides=(
                (_f(name="hero", weapon=5, energie=20, position=_cell(5, 10)),),
                (_f(name="thug", weapon=5, energie=20, position=_cell(5, 20)),),
            )
        ),
        input_source=lambda i: ("pass", None),
        rng=Rng(seed=1234),
    )
    assert result.winner == 2, "the passive player should lose"


def test_a_seeded_fight_where_the_player_fights_back_also_terminates():
    from engine.rng import Rng

    result = run_fight(
        **_ai_spec(
            sides=(
                (_f(name="hero", weapon=6, energie=30, position=_cell(5, 10)),),
                (_f(name="thug", weapon=0, energie=10, position=_cell(5, 25)),),
            )
        ),
        input_source=lambda i: ("shoot", STEP_RIGHT),
        rng=Rng(seed=7),
    )
    assert result.winner in (1, 2)


def test_cpu_sides_is_explicit_and_side_one_can_be_client_driven_only():
    """The port declares CPU control explicitly rather than replicating the
    colour-RAM encoding cr uses. An empty `cpu_sides` makes the fight hot-seat."""
    prompted = []

    def src(interaction):
        prompted.append(interaction.active_side)
        return ("surrender", None) if len(prompted) > 1 else ("pass", None)

    run_fight(**_ai_spec(cpu_sides=()), input_source=src, rng=_StubRng())
    assert prompted == [1, 2], "with no CPU side, both sides are prompted"
