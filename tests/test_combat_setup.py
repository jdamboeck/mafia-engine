"""Tests for combat core: state, grid, setup (U4).

Covers:
- the ten placement stagger offsets against the research DATA table exactly,
  for both sides and 1..10 fighters;
- enemy setup from a ``StartCombat``-shaped spec (count, weapon, energy) with the
  fixed kraft=30/brutalitaet=30 stats (``mf-prg.bas:30245``);
- the two obstruction sets (movement vs. shot) differ as specified, and cell 520
  is legally reachable;
- ``CombatState`` round-trips ``json_safe``/``state_from_dict``; purity harness
  (``run_pure``-style commit/replay) stays clean for :class:`SpawnFighter`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.combat import (
    CELL_COUNT,
    ENEMY_BRUTALITAET,
    ENEMY_KRAFT,
    GRID_COLS,
    MAX_CELL,
    SIDE1_ANCHOR,
    SIDE2_ANCHOR,
    STAGGER_OFFSETS,
    blocks_shot,
    build_enemy_side,
    build_player_side,
    can_move_onto,
    placement_position,
    placement_positions,
    setup_combat,
)
from engine.effects import SpawnFighter, apply
from engine.persistence import state_from_dict
from engine.state import CombatState, Fighter, Gangster, GameState, json_safe

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "data" / "game_configs" / "mafia_1920s"


# --------------------------------------------------------------------------- #
# Placement — DATA 50400 stagger offsets, side anchors 129 (side1) / 111 (side2) #
# --------------------------------------------------------------------------- #
def test_stagger_offsets_match_research_data_table():
    # mf-prg.bas:50400 — DATA 122,81,161,120,42,202,40,200,1,241
    assert STAGGER_OFFSETS == (122, 81, 161, 120, 42, 202, 40, 200, 1, 241)


def test_side1_anchor_is_129():
    # mf-prg.bas:30000 kp(i,j)=129-18*(i=2)+p(j); i=1 -> (i=2) false -> +0
    assert SIDE1_ANCHOR == 129


def test_side2_anchor_is_111_under_pinned_true_plus_one_convention():
    # i=2 -> (i=2) true -> contributes +1 (pinned porting convention, docs/solutions/
    # architecture-patterns/basic-relational-boolean-is-plus-one-when-porting.md):
    # 129 - 18*1 = 111.
    assert SIDE2_ANCHOR == 111


@pytest.mark.parametrize("slot,offset", list(enumerate(STAGGER_OFFSETS, start=1)))
def test_placement_position_side1_matches_anchor_plus_offset(slot, offset):
    assert placement_position(SIDE1_ANCHOR, slot) == 129 + offset


@pytest.mark.parametrize("slot,offset", list(enumerate(STAGGER_OFFSETS, start=1)))
def test_placement_position_side2_matches_anchor_plus_offset(slot, offset):
    assert placement_position(SIDE2_ANCHOR, slot) == 111 + offset


def test_placement_positions_for_one_through_ten_fighters():
    for n in range(1, 11):
        positions = placement_positions(SIDE1_ANCHOR, n)
        assert len(positions) == n
        assert positions == tuple(129 + off for off in STAGGER_OFFSETS[:n])


def test_placement_position_slot_zero_raises_value_error():
    with pytest.raises(ValueError):
        placement_position(SIDE1_ANCHOR, 0)


def test_placement_position_slot_eleven_raises_value_error():
    with pytest.raises(ValueError):
        placement_position(SIDE1_ANCHOR, 11)


# --------------------------------------------------------------------------- #
# Player-side setup — boss first (KTD-6), roster stats carried onto Fighter    #
# --------------------------------------------------------------------------- #
def test_build_player_side_boss_first_with_equipped_weapon():
    roster = [
        Gangster(name="capone", weapon=5, energie=5, kraft=15, brutalitaet=20),
        Gangster(name="thug1", weapon=1, energie=5, kraft=10, brutalitaet=10),
    ]
    side = build_player_side(roster)
    assert len(side) == 2
    assert side[0].name == "capone"  # roster[0] is always the boss, KTD-6
    assert side[0].weapon == 5
    assert side[0].kraft == 15
    assert side[0].brutalitaet == 20
    assert side[0].position == 129 + STAGGER_OFFSETS[0]
    assert side[1].name == "thug1"
    assert side[1].position == 129 + STAGGER_OFFSETS[1]
    assert all(not f.down for f in side)


def test_build_player_side_empty_roster():
    assert build_player_side([]) == ()


# --------------------------------------------------------------------------- #
# Enemy-side setup from a StartCombat-shaped spec (mf-prg.bas:5000, fixed 30/30) #
# --------------------------------------------------------------------------- #
def test_build_enemy_side_uniform_weapon_and_energy():
    # mf-prg.bas:5000 — fori=1togz(0):gw(0,i)=w:ec(i)=e:next (one w/e for all enemies)
    side = build_enemy_side(count=3, weapon=2, energie=8, name="ganove")
    assert len(side) == 3
    assert all(f.weapon == 2 for f in side)
    assert all(f.energie == 8 for f in side)
    assert all(f.name == "ganove" for f in side)


def test_build_enemy_side_fixed_kraft_brutalitaet_30():
    # mf-prg.bas:30245 — ifks(s)=0thenbt=30:kr=30
    side = build_enemy_side(count=5, weapon=0, energie=5)
    assert ENEMY_KRAFT == 30
    assert ENEMY_BRUTALITAET == 30
    assert all(f.kraft == 30 for f in side)
    assert all(f.brutalitaet == 30 for f in side)


def test_build_enemy_side_placement_uses_side2_anchor():
    side = build_enemy_side(count=2, weapon=0, energie=5)
    assert side[0].position == 111 + STAGGER_OFFSETS[0]
    assert side[1].position == 111 + STAGGER_OFFSETS[1]


def test_build_enemy_side_zero_count():
    assert build_enemy_side(count=0, weapon=0, energie=5) == ()


# --------------------------------------------------------------------------- #
# Obstruction sets — movement vs. shot differ (mf-prg.bas:30145 vs 30225-30226) #
# --------------------------------------------------------------------------- #
def test_movement_blocked_by_wall_code():
    grid = (160,) * CELL_COUNT
    assert can_move_onto(0, grid, occupied=frozenset()) is False


def test_movement_blocked_by_plain_scenery_code():
    # A non-wall, non-empty code (e.g. 224 decorative scenery) still blocks a MOVE —
    # movement's obstruction set is "anything but empty" (30145: <>32 and <>96).
    grid = (224,) * CELL_COUNT
    assert can_move_onto(0, grid, occupied=frozenset()) is False


def test_movement_allowed_onto_empty_codes():
    grid = list((160,) * CELL_COUNT)
    grid[5] = 32
    grid[6] = 96
    grid = tuple(grid)
    assert can_move_onto(5, grid, occupied=frozenset()) is True
    assert can_move_onto(6, grid, occupied=frozenset()) is True


def test_movement_blocked_by_occupied_cell():
    grid = list((160,) * CELL_COUNT)
    grid[5] = 32
    grid = tuple(grid)
    assert can_move_onto(5, grid, occupied=frozenset({5})) is False


def test_movement_blocked_out_of_bounds():
    grid = (32,) * CELL_COUNT
    assert can_move_onto(-1, grid, occupied=frozenset()) is False
    assert can_move_onto(CELL_COUNT, grid, occupied=frozenset()) is False  # 521, > MAX_CELL


def test_shot_blocked_only_by_wall_codes_not_scenery():
    # A shot's projectile is NOT stopped by plain scenery (224) or an occupied cell —
    # only by an actual wall code (160/156) or the bounds (30225-30226).
    grid = list((32,) * CELL_COUNT)
    grid[10] = 224  # decorative scenery — blocks a MOVE but not a SHOT
    grid[11] = 160  # wall — blocks a SHOT
    grid[12] = 156  # wall (alt code) — blocks a SHOT
    grid = tuple(grid)
    assert blocks_shot(10, grid) is False
    assert blocks_shot(11, grid) is True
    assert blocks_shot(12, grid) is True


def test_shot_not_blocked_by_fighter_occupancy():
    # blocks_shot takes no occupied set — fighter occupancy never stops a projectile,
    # unlike can_move_onto (the two sets genuinely differ, not just in wall vocabulary).
    grid = (32,) * CELL_COUNT
    assert blocks_shot(50, grid) is False


def test_shot_blocked_out_of_bounds():
    grid = (32,) * CELL_COUNT
    assert blocks_shot(-1, grid) is True
    assert blocks_shot(CELL_COUNT, grid) is True


def test_movement_and_shot_obstruction_sets_genuinely_differ():
    grid = list((32,) * CELL_COUNT)
    grid[20] = 224  # scenery: blocks move, not shot
    grid = tuple(grid)
    move_blocked = not can_move_onto(20, grid, occupied=frozenset())
    shot_blocked = blocks_shot(20, grid)
    assert move_blocked is True
    assert shot_blocked is False
    assert move_blocked != shot_blocked


# --------------------------------------------------------------------------- #
# Cell 520 — the kept 521-cell bound's legally-reachable edge case             #
# --------------------------------------------------------------------------- #
def test_cell_520_is_max_cell():
    assert MAX_CELL == 520
    assert CELL_COUNT == 521


def test_cell_520_legally_reachable_when_empty():
    grid = list((160,) * CELL_COUNT)
    grid[520] = 32
    grid = tuple(grid)
    assert can_move_onto(520, grid, occupied=frozenset()) is True


def test_cell_520_not_rejected_by_strict_row_math():
    # 520 = 13*40 + 0 — a partial 14th row start, NOT out of a strict 13-row (0..519)
    # bound. A correct linear implementation must not reject it as row 13 col 0.
    assert 520 // GRID_COLS == 13
    assert 520 % GRID_COLS == 0
    grid = list((32,) * CELL_COUNT)
    grid = tuple(grid)
    assert can_move_onto(520, grid, occupied=frozenset()) is True
    assert blocks_shot(520, grid) is False  # empty cell, in bounds -> not a wall


def test_cell_521_is_out_of_bounds():
    grid = (32,) * (CELL_COUNT + 5)  # even if the backdrop array were longer
    assert can_move_onto(521, grid, occupied=frozenset()) is False
    assert blocks_shot(521, grid) is True


# --------------------------------------------------------------------------- #
# setup_combat — full fight setup from a StartCombat-shaped spec               #
# --------------------------------------------------------------------------- #
def test_setup_combat_places_both_sides():
    roster = [Gangster(name="capone", weapon=5, energie=5, kraft=15, brutalitaet=20)]
    combat = setup_combat(
        roster, enemy_count=2, enemy_weapon=0, enemy_energie=5, enemy_name="ganove"
    )
    assert isinstance(combat, CombatState)
    assert len(combat.sides[0]) == 1
    assert len(combat.sides[1]) == 2
    assert combat.sides[0][0].name == "capone"
    assert combat.sides[1][0].name == "ganove"


def test_setup_combat_starts_cursor_at_side1_fighter1():
    combat = setup_combat([Gangster(name="capone")], enemy_count=1, enemy_weapon=0, enemy_energie=5)
    assert combat.active_side == 1
    assert combat.active_fighter == 1


def test_setup_combat_losses_start_zero():
    combat = setup_combat([Gangster(name="capone")], enemy_count=1, enemy_weapon=0, enemy_energie=5)
    assert combat.losses == (0, 0)


def test_setup_combat_result_flag_unset():
    combat = setup_combat([Gangster(name="capone")], enemy_count=1, enemy_weapon=0, enemy_energie=5)
    assert combat.result_flag == 0


def test_setup_combat_enemy_dir_memory_initialized_to_minus_one():
    # mf-prg.bas:30020 — ifks(2)=0thenfori=1togz(0):ri(i)=-1:next
    combat = setup_combat([Gangster(name="capone")], enemy_count=3, enemy_weapon=0, enemy_energie=5)
    assert combat.dir_memory == {0: -1, 1: -1, 2: -1}


def test_setup_combat_carries_grid():
    grid = tuple(range(CELL_COUNT))
    combat = setup_combat(
        [Gangster(name="capone")], enemy_count=1, enemy_weapon=0, enemy_energie=5, grid=grid
    )
    assert combat.grid == grid


def test_setup_combat_empty_grid_is_still_playable_open_arena():
    # A fidelity-deviation fallback (no backdrop data) must still produce a legally
    # playable arena: can_move_onto treats past-the-end cells as open ground.
    combat = setup_combat([Gangster(name="capone")], enemy_count=1, enemy_weapon=0, enemy_energie=5)
    assert combat.grid == ()
    assert can_move_onto(300, combat.grid, occupied=frozenset()) is True


# --------------------------------------------------------------------------- #
# Combat backdrop config data — ks/kp/km extraction (Assumptions/Risks)        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["ks", "kp", "km"])
def test_combat_backdrop_config_extracted_non_degenerate(name):
    """The three in-slice backdrops decode to real, non-empty wall/empty data —
    NOT the empty-arena fallback (Assumptions: surface a fidelity deviation if
    extraction is unreliable; here it succeeded, so this pins that outcome)."""
    from data.game_configs.mafia_1920s.setup import load_combat_backdrop

    path = _CONFIG_DIR / "content" / "combat" / f"{name}.yaml"
    grid = load_combat_backdrop(path)
    assert len(grid) == CELL_COUNT
    wall_count = sum(1 for c in grid if c in (160, 156))
    empty_count = sum(1 for c in grid if c in (32, 96))
    assert wall_count > 0, f"{name}: no wall cells decoded — looks like an empty-arena fallback"
    assert empty_count > 0, f"{name}: no empty cells decoded"


def test_combat_backdrop_cell_520_present_and_in_domain():
    from data.game_configs.mafia_1920s.setup import load_combat_backdrop

    for name in ("ks", "kp", "km"):
        grid = load_combat_backdrop(_CONFIG_DIR / "content" / "combat" / f"{name}.yaml")
        assert 0 <= grid[MAX_CELL] <= 255  # a real byte code, not a sentinel/absent value


# --------------------------------------------------------------------------- #
# CombatState round-trips json_safe / state_from_dict (KTD-7)                  #
# --------------------------------------------------------------------------- #
def test_combat_state_json_safe_roundtrip():
    roster = [Gangster(name="capone", weapon=5, energie=5, kraft=15, brutalitaet=20)]
    combat = setup_combat(roster, enemy_count=2, enemy_weapon=1, enemy_energie=5, enemy_name="ganove")
    state = GameState(combat=combat)
    snapshot = json_safe(state)
    restored = state_from_dict(snapshot)
    assert restored.combat.sides[0][0].name == "capone"
    assert restored.combat.sides[1][0].name == "ganove"
    assert restored.combat.dir_memory == {0: -1, 1: -1}
    assert restored.combat.losses == (0, 0)
    assert restored.combat == combat


def test_combat_state_json_safe_is_plain_containers():
    combat = setup_combat([Gangster(name="capone")], enemy_count=1, enemy_weapon=0, enemy_energie=5)
    snapshot = json_safe(combat)
    assert isinstance(snapshot["sides"], list)
    assert isinstance(snapshot["sides"][0], list)
    assert isinstance(snapshot["sides"][0][0], dict)
    assert isinstance(snapshot["dir_memory"], dict)
    assert isinstance(snapshot["losses"], list)


# --------------------------------------------------------------------------- #
# SpawnFighter purity — buffered effects independently replay to the same state #
# --------------------------------------------------------------------------- #
def test_spawn_fighter_effects_replay_to_same_state():
    from engine.effects import commit

    state = GameState()
    fighters = build_player_side([Gangster(name="capone", weapon=5, kraft=15, brutalitaet=20)])
    effects = [SpawnFighter(fighter=f, side=1) for f in fighters]
    driven = commit(state, effects).state

    snapshot = json_safe(state)
    baseline = state_from_dict(snapshot)
    replayed = commit(baseline, effects).state

    assert driven.combat.sides[0][0].name == "capone"
    assert json_safe(driven) == json_safe(replayed)


def test_spawn_fighter_does_not_mutate_original_state():
    state = GameState()
    f = Fighter(name="capone", position=129)
    apply(state, SpawnFighter(fighter=f, side=1))
    assert state.combat.sides == ((), ())
