"""Combat core: the 40×13 grid model + fight setup (U4, KTD-1).

Combat is genre-engine machinery (docs/design/), not config code — this module owns the
grid, placement, and the two distinct obstruction sets. It does NOT own the activation
loop, shooting, damage, or AI (U5/U6): this unit is **setup only**. The driver
sub-protocol that will drive a fight through this setup lands in ``engine.interactions``
in U5 — this module exposes pure functions U5 calls, not a generator itself.

**Grid representation.** The original bounds the combat grid at cell 520 inclusive
(``mf-prg.bas:30145``: ``p<br or p>br+520``), so movement/shots range over 521 cells
(0..520) on a nominally 40-wide, 13-row grid. 521 = 13*40 + 1 — one cell of a partial
14th row. A strict ``(row, col)`` shape with 13 rows would wrongly reject the legal
cell 520, so the grid (and every position) is represented **linearly**: ``CombatState.grid``
is a flat 521-entry tuple indexed 0..520, and a fighter's ``position`` is that same linear
index (``row, col = divmod(position, GRID_COLS)`` recovers 2D coordinates for rendering).

**Two distinct obstruction sets** (do not collapse them — CLAUDE.md):

- **Movement** (``mf-prg.bas:30145``): blocked by ANY occupied-or-scenery cell — the
  target must show code 32 (space) or 96 (a second empty-look glyph) to be walkable.
  Every other code (walls, decorative scenery, another fighter's sprite code 193)
  blocks a step.
- **Shots** (``mf-prg.bas:30225-30226``): blocked ONLY by wall cells (160 or 156) and
  the grid bounds. A shot's projectile freely overflies scenery/other-fighter cells
  that would block a MOVE — it only stops at an actual wall, the edge, or a hit.

**Backdrop walls.** The three in-slice combat backdrops (``ks``/``kp``/``km``) are the
same 2003-byte C64 screen-file format as the city map (``research/src/karte``, decoded
by ``tools/decode_city_map.py``): 1000 screen-code bytes stored bottom-up/right-to-left,
so the row-major codes are ``data[:1000][::-1]`` (mirrors
``research/tools/render_c64_assets.py:106-119``'s ``parse_screen_file``). Combat only
plays out over the FIRST 521 of those 1000 decoded cells (the grid's 0..520 bound) —
rows 14..24 of the loaded screen are never addressed by the fight loop. The three
backdrops are pre-decoded once into ``data/game_configs/mafia_1920s/content/combat/*.yaml``
(mirroring the city map's committed-artifact pattern) rather than parsed at runtime, so
the engine never depends on the research checkout at runtime (KTD-1's "engine imports
nothing from server/clients" sibling rule — config data must not require research either).

``engine/`` imports nothing from ``server``/``clients``/transport, and this module holds
no display text.
"""

from __future__ import annotations

from typing import Any

from engine.state import CombatState, Fighter

__all__ = [
    "GRID_COLS",
    "GRID_ROWS",
    "CELL_COUNT",
    "MAX_CELL",
    "MOVE_EMPTY_CODES",
    "SHOT_WALL_CODES",
    "SIDE1_ANCHOR",
    "SIDE2_ANCHOR",
    "STAGGER_OFFSETS",
    "can_move_onto",
    "blocks_shot",
    "placement_position",
    "placement_positions",
    "build_player_side",
    "build_enemy_side",
    "setup_combat",
]

# --------------------------------------------------------------------------- #
# Grid constants                                                              #
# --------------------------------------------------------------------------- #
GRID_COLS = 40  # combat grid width (mf-prg.bas:systems-analysis "combat grid 40x13")
GRID_ROWS = 13  # nominal row count; the 521-cell bound admits a partial 14th row
CELL_COUNT = 521  # 0..520 inclusive (mf-prg.bas:30145, 30225 — kept for R1 fidelity)
MAX_CELL = CELL_COUNT - 1  # 520 — MUST be legally reachable (CLAUDE.md)

#: Movement target codes that are walkable (mf-prg.bas:30145: peek(p)=32 or 96).
MOVE_EMPTY_CODES = (32, 96)

#: Shot-blocking wall codes (mf-prg.bas:30225-30226: peek(br+p)=160 or 156).
SHOT_WALL_CODES = (160, 156)

# --------------------------------------------------------------------------- #
# Placement                                                                   #
# --------------------------------------------------------------------------- #
#: The ten combat spawn-position stagger offsets, DATA 50400 (mf-prg.bas:124,
#: `fori=1to10:readp(i):next` — read into p(1..10), used at 30000).
STAGGER_OFFSETS: tuple[int, ...] = (122, 81, 161, 120, 42, 202, 40, 200, 1, 241)

#: Side anchors from ``mf-prg.bas:30000``: ``kp(i,j) = 129-18*(i=2) + p(j)``.
#: Side 1 (i=1): ``(i=2)`` is false (0) -> anchor 129. Side 2 (i=2): ``(i=2)`` is
#: true, and per this project's PINNED porting convention a true relational
#: contributes +1, never the raw-C64 -1
#: (docs/solutions/architecture-patterns/basic-relational-boolean-is-plus-one-when-porting.md
#: — "settled, not a judgment call", verified by the shipped `fnm` negative-rent
#: quirk) -> anchor = 129 - 18*1 = 111. The oracle's OWN interpretation note for
#: this exact line agrees: "129 for side 1, 129-18 for side 2" (=111), and the
#: sibling colour expression on the same line block (`2-4*(i=2)`, mf-prg.bas:30010)
#: is documented the same way (side 2 -> -2, not the raw-C64 +6). NOTE: this
#: conflicts with this unit's own plan prose ("side anchors 129/147"), which reads
#: as the raw C64 true=-1 evaluation (129-18*(-1)=147) — flagged in the U4 report
#: for orchestrator review; KTD-9 resolves conflicts between decompiled code and
#: prose/research-interpretation in favour of the decompiled code + pinned
#: convention, so 111 is what ships.
SIDE1_ANCHOR = 129
SIDE2_ANCHOR = 129 - 18  # 111, per the pinned true=+1 convention (see note above)


def placement_position(anchor: int, slot: int) -> int:
    """Return the linear cell for the ``slot``-th fighter (1-based) placed at ``anchor``.

    Ports ``kp(i,j) = anchor + p(j)`` (mf-prg.bas:30000) for one fighter. ``slot`` is
    1-based (matching the source's ``forj=1togz(...)``) and indexes
    :data:`STAGGER_OFFSETS` at ``slot - 1``.

    Raises ``ValueError`` if ``slot`` is out of the supported 1..10 range (the source
    only ever reads 10 stagger offsets — a roster/spec larger than 10 fighters per
    side is a config bug the caller should catch, not silently wrap).
    """
    if slot < 1 or slot > len(STAGGER_OFFSETS):
        raise ValueError(
            f"placement slot {slot} out of range (1..{len(STAGGER_OFFSETS)} supported)"
        )
    return anchor + STAGGER_OFFSETS[slot - 1]


def placement_positions(anchor: int, count: int) -> tuple[int, ...]:
    """Return the first ``count`` placement cells for one side, in slot order."""
    return tuple(placement_position(anchor, slot) for slot in range(1, count + 1))


# --------------------------------------------------------------------------- #
# Obstruction predicates — two distinct sets (do not collapse)                #
# --------------------------------------------------------------------------- #
def can_move_onto(cell: int, grid: tuple[int, ...], occupied: frozenset[int]) -> bool:
    """Can a fighter step onto ``cell``? Ports ``mf-prg.bas:30145``.

    Blocked by: out of bounds, any non-empty backdrop code (only 32/96 are walkable —
    this also blocks walls AND plain scenery, unlike a shot), or another fighter
    already standing there (``occupied`` — the backdrop alone cannot express this,
    since a fighter's own sprite code 193 is painted onto the grid at runtime in the
    source; this engine keeps fighter occupancy out of the static ``grid`` array and
    checks it as a caller-supplied set instead, so the immutable backdrop config never
    needs to be rewritten mid-fight).
    """
    if cell < 0 or cell > MAX_CELL:
        return False
    if cell in occupied:
        return False
    code = grid[cell] if cell < len(grid) else 32  # unpopulated grid = open ground
    return code in MOVE_EMPTY_CODES


def blocks_shot(cell: int, grid: tuple[int, ...]) -> bool:
    """Would a shot's projectile stop at ``cell``? Ports ``mf-prg.bas:30225-30226``.

    True if ``cell`` is out of bounds or holds a wall code (160/156). Unlike
    :func:`can_move_onto`, plain scenery and occupied cells do NOT block a shot — only
    walls and the grid edge do (the source's projectile step only checks
    ``peek(br+p)=160 or 156``, not the fighter-occupancy check ``30145`` uses).
    """
    if cell < 0 or cell > MAX_CELL:
        return True
    code = grid[cell] if cell < len(grid) else 32
    return code in SHOT_WALL_CODES


# --------------------------------------------------------------------------- #
# Fighter-side construction                                                   #
# --------------------------------------------------------------------------- #
def build_player_side(roster: Any) -> tuple[Fighter, ...]:
    """Build side 1 from the active player's roster (boss first, KTD-6).

    Each roster :class:`~engine.state.Gangster` becomes one :class:`~engine.state.Fighter`
    carrying its own name/weapon/energie/kraft/brutalitaet, placed at
    :data:`SIDE1_ANCHOR`. ``roster[0]`` is always the boss (``Player`` docstring,
    ``mf-prg.bas:300``) — this function does not reorder it, it only maps roster order
    onto placement-slot order 1:1 (``mf-prg.bas:30000``'s ``forj=1togz(ks(i))`` walks
    the roster in its stored order).
    """
    positions = placement_positions(SIDE1_ANCHOR, len(roster))
    return tuple(
        Fighter(
            name=g.name,
            weapon=g.weapon,
            energie=g.energie,
            kraft=g.kraft,
            brutalitaet=g.brutalitaet,
            position=pos,
            down=False,
        )
        for g, pos in zip(roster, positions)
    )


#: Fixed enemy stats (mf-prg.bas:30245: `ifks(s)=0thenbt=30:kr=30` — the computer
#: opponent's hit-chance/damage rolls use fixed kraft=30, brutalitaet=30 instead of
#: reading a gangster's stats).
ENEMY_KRAFT = 30
ENEMY_BRUTALITAET = 30


def build_enemy_side(count: int, weapon: int, energie: int, *, name: str = "") -> tuple[Fighter, ...]:
    """Build side 2 (the NPC/enemy party) from a ``StartCombat`` spec.

    Ports the combat-launch helper ``mf-prg.bas:5000``: every enemy fighter is armed
    with the SAME ``weapon`` and starts with the SAME ``energie`` (``fori=1togz(0):
    gw(0,i)=w:ec(i)=e:next`` — one weapon/energy value broadcast across the whole
    enemy roster, not per-fighter). kraft/brutalitaet are the fixed 30/30
    (``mf-prg.bas:30245``) — enemies never read gangster stats. Placed at
    :data:`SIDE2_ANCHOR`.
    """
    positions = placement_positions(SIDE2_ANCHOR, count)
    return tuple(
        Fighter(
            name=name,
            weapon=weapon,
            energie=energie,
            kraft=ENEMY_KRAFT,
            brutalitaet=ENEMY_BRUTALITAET,
            position=pos,
            down=False,
        )
        for pos in positions
    )


# --------------------------------------------------------------------------- #
# Fight setup                                                                 #
# --------------------------------------------------------------------------- #
def setup_combat(
    roster: Any,
    *,
    enemy_count: int,
    enemy_weapon: int,
    enemy_energie: int,
    enemy_name: str = "",
    grid: tuple[int, ...] = (),
) -> CombatState:
    """Build the initial :class:`~engine.state.CombatState` for a new fight.

    Ports ``mf-prg.bas:30000-30020``: places both sides (:func:`build_player_side`,
    :func:`build_enemy_side`), initializes the enemy side's direction memory
    ``ri(i)=-1`` for each enemy fighter (``mf-prg.bas:30020`` — the source only
    tracks this for the CPU side), and starts the activation cursor at side 1,
    fighter 1 (``mf-prg.bas:30100``: ``s=1:f=0`` then the first ``f=f+1`` at
    activation start lands on fighter 1). Losses start at ``(0, 0)`` (``v(1)=0:v(2)=0``,
    :30100) and ``result_flag`` starts unset (0).

    ``grid`` is the backdrop's linear 521-cell wall/scenery code array (config data,
    pre-decoded — see this module's docstring); an empty ``grid`` (fidelity-deviation
    fallback) still produces a legally-playable open arena, since :func:`can_move_onto`
    treats any cell past the end of a short ``grid`` as open ground (code 32).
    """
    side1 = build_player_side(roster)
    side2 = build_enemy_side(enemy_count, enemy_weapon, enemy_energie, name=enemy_name)
    dir_memory = {i: -1 for i in range(len(side2))}
    return CombatState(
        sides=(side1, side2),
        grid=tuple(grid),
        dir_memory=dir_memory,
        active_side=1,
        active_fighter=1,
        losses=(0, 0),
        result_flag=0,
    )
