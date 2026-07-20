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

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from engine.state import CombatState, Fighter

#: Shared empty role map — a bundle that declares no roles for a capability.
_EMPTY_ROLES: Mapping[str, str] = MappingProxyType({})

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
    "RANGE_MELEE",
    "RulesBundle",
    "DEFAULT_RANGE",
    "STEP_LEFT",
    "STEP_RIGHT",
    "STEP_UP",
    "STEP_DOWN",
    "STEPS",
    "can_move_onto",
    "blocks_shot",
    "placement_position",
    "placement_positions",
    "build_player_side",
    "build_enemy_side",
    "setup_combat",
    "AiTarget",
    "ai_target",
    "CombatFight",
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
#: true, which in C64 BASIC is -1 -> anchor = 129 - 18*(-1) = 147.
#:
#: Corrected by the #47 fidelity audit (this previously shipped 111, from the
#: since-reversed true=+1 pin). The sibling expressions in this same block decide
#: the sign structurally, and all three fail under true=+1:
#:   :30010 ``pokefr+kp(i,j),2-4*(i=2)`` — a C64 colour code (0..15). true=-1
#:          gives 6 (blue) for side 2 vs 2 (red) for side 1; true=+1 gives -2.
#:   :30015 ``poke211,-20*(i=2)`` — 211/$D3 is the KERNAL cursor COLUMN and
#:          cannot be negative. true=-1 puts side 2's label at column 20 (the
#:          right half of the 40-column screen); true=+1 gives -20.
#:   :30108 ``s=1-(s=1)`` — the side toggle, which must map 1<->2. true=-1 gives
#:          1->2 and 2->1; true=+1 gives 1->0, a nonexistent side.
#: 147 also matches this unit's plan prose ("side anchors 129/147").
SIDE1_ANCHOR = 129
SIDE2_ANCHOR = 129 + 18  # 147 — (i=2) is true = -1, so 129 - 18*(-1)


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
            equipment=getattr(g, "equipment", None) or {},
            # The roster slot this fighter came from, so the outcome maps back to the
            # right gangster by identity rather than by position (amendment A1).
            roster_id=slot,
        )
        for slot, (g, pos) in enumerate(zip(roster, positions))
    )


#: Fixed enemy stats (mf-prg.bas:30245: `ifks(s)=0thenbt=30:kr=30` — the computer
#: opponent's hit-chance/damage rolls use fixed kraft=30, brutalitaet=30 instead of
#: reading a gangster's stats).
ENEMY_KRAFT = 30
ENEMY_BRUTALITAET = 30


def build_enemy_side(
    count: int, weapon: int, energie: int, *, name: str = ""
) -> tuple[Fighter, ...]:
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
    equip: Any = None,
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

    ``equip`` is the GAME's ``weapon id -> stat mapping`` constructor, called once per
    fighter here so every combatant enters the fight already carrying its equipment
    (amendment A1). It is a parameter rather than an engine table because resolving a
    weapon id is entity knowledge the engine does not have — and because building the
    equipment HERE, once, is what stops a second copy existing to disagree later.
    """
    side1 = build_player_side(roster)
    side2 = build_enemy_side(enemy_count, enemy_weapon, enemy_energie, name=enemy_name)
    if equip is not None:
        side1 = tuple(replace(f, equipment=equip(f.weapon)) for f in side1)
        side2 = tuple(replace(f, equipment=equip(f.weapon)) for f in side2)
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


# --------------------------------------------------------------------------- #
# Melee reach + the two combat rolls (U5)                                     #
# --------------------------------------------------------------------------- #
#: Longest range that still reaches only the ADJACENT cell — the engine's definition
#: of a melee weapon. A shot steps one cell per range point (``mf-prg.bas:30220``),
#: so anything at or below this can never out-reach a neighbour, and a fighter
#: carrying it must close the distance to attack at all.
#:
#: This replaces the source's own melee test, which spelled the same set out as a
#: weapon-id comparison (``30415``: ``orgw(...)<4``) because the original's range
#: table was a hardcoded ladder (``30215``: ``r=2`` widened to 15 by ``ifw>3``).
#: Per-weapon ranges are CONFIG data now, so the taxonomy is derived, not enumerated
#: — verified equivalent across all nine of the reference title's weapons.
RANGE_MELEE = 2

#: The default range for a weapon whose config omits one, matching the source's own
#: base ``r=2`` (``mf-prg.bas:30215``) — an unspecified weapon reaches one cell.
DEFAULT_RANGE = RANGE_MELEE

#: Linear step deltas for the four grid directions (``mf-prg.bas:30130-30133`` and
#: ``30206-30209``): left/right are ±1, up/down are ∓ one row width (±40). These are
#: the *only* legal move/aim deltas — the source reads exactly four keys.
STEP_LEFT = -1
STEP_RIGHT = 1
STEP_UP = -GRID_COLS
STEP_DOWN = GRID_COLS
STEPS: tuple[int, ...] = (STEP_LEFT, STEP_RIGHT, STEP_UP, STEP_DOWN)


# --------------------------------------------------------------------------- #
# The rules bundle — how a game answers the engine's combat questions (U2)     #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RulesBundle:
    """The formulas and attribute roles a fight runs on — supplied by the GAME (KTD-3).

    The engine owns *when* a hit test happens and what its result means; it does not
    own the formula. A config passes one of these to :class:`CombatFight` beside its
    ``rng``, exactly as it already passes ``weapon_stats``. There is deliberately no
    global registry: two differently-ruled fights must be able to coexist in one
    process (a genre-engine requirement, not a hypothetical — a scenario harness runs
    several at once).

    **Roles group per capability, not one flat map.** ``hit_roles`` and
    ``damage_roles`` each map a *participant* role name ("attacker") to the attribute
    key that capability reads off that participant. A single flat ``{role: key}`` map
    could not express WHOSE attribute a role reads. The reference title only ever
    reads the attacker's, but the genre contract needs defender-side ``evasion`` or
    ``initiative`` later, and that must not be a breaking change.

    Fields:

    ``vitality``
        The attribute key the engine depletes and tests for termination — the ONE
        engine-named role. Everything else the engine passes through opaquely.
    ``hit_roles`` / ``hit_fn``
        The hit test's role map and its formula ``(attacker, equipment, rng) -> bool``.
    ``damage_roles`` / ``damage_fn``
        The damage roll's role map and its formula ``(attacker, equipment, rng) -> int``.
    There is deliberately **no** ``equipment_stats`` entry (amendment A1): equipment
    stats live on the combatant, so a formula reads them off the attacker it was
    handed. A bundle-held lookup would be a second source able to disagree with the
    roster about what a weapon does.
    """

    vitality: str = "vitality"
    hit_roles: Mapping[str, str] = _EMPTY_ROLES
    hit_fn: Any = None
    damage_roles: Mapping[str, str] = _EMPTY_ROLES
    damage_fn: Any = None

    def required_keys(self) -> tuple[str, ...]:
        """Every attribute key this bundle will read off a combatant.

        Used at fight construction to reject a bundle whose roles name a key no
        combatant carries — surfacing the mismatch with both names, rather than as
        a ``KeyError`` several activations deep inside a formula.
        """
        keys = [self.vitality]
        keys.extend(self.hit_roles.values())
        keys.extend(self.damage_roles.values())
        return tuple(dict.fromkeys(keys))


# The hit/damage formulas that lived here (``is_hit``/``damage_roll``) are gone (U2):
# they name this game's ``kraft``/``brutalitaet`` and so are POLICY, not mechanism.
# They now live in ``data/game_configs/mafia_1920s/combat_rules.py`` as the rules
# bundle's ``hit_fn``/``damage_fn``, reading the attacker's attributes by role. The
# engine calls them through the bundle and never spells a stat name. The full-domain
# differential in ``tests/test_combat_rules.py`` proved the move exact before deletion.


# --------------------------------------------------------------------------- #
# CPU target selection — the `cr` machine-code routine (U6)                   #
# --------------------------------------------------------------------------- #
#: The side the CPU AI hunts.
#:
#: In the original this is not a parameter at all: the ``cr`` routine (``$C000``,
#: disassembled in ``research/research-data/verification/ml-core-disassembly.yaml``)
#: scans the screen for ``char $C1 (=193)`` cells whose **colour-RAM low nibble is 2**
#: — a hardcoded constant in the machine code. ``mf-prg.bas:30010``
#: (``pokefr+kp(i,j),2-4*(i=2)``) paints side 1 with colour 2 (the relational is false
#: for ``i=1``, so the expression is 2 under EITHER sign convention — this site is not
#: affected by the relational-sign landmine). So ``cr`` structurally always hunts
#: side 1. This port states that intent as a named constant rather than replicating
#: the colour-RAM encoding, exactly as U6's plan section directs.
AI_HUNTS_SIDE = 1

#: Sides driven by the AI rather than by a client prompt.
#:
#: Ports ``mf-prg.bas:30110`` (``ifks(s)=0thengosub30400:goto30105``) together with
#: ``5010`` (``ks(1)=sp:ks(2)=0``): the combat-launch helper puts the NPC party in
#: slot 2 and marks it CPU with ``ks(2)=0``. ``30020`` agrees — direction memory is
#: only initialized ``ifks(2)=0``. The source *can* express a player-vs-player fight
#: (``27020`` sets ``ks(1)``/``ks(2)`` to two player numbers), so this is a default,
#: not a hardcoded rule: a caller may pass an empty ``cpu_sides`` for hot-seat play.
DEFAULT_CPU_SIDES: tuple[int, ...] = (2,)


@dataclass(frozen=True)
class AiTarget:
    """The nearest hostile fighter, as ``cr`` reports it through ``ua``..``ua+3``.

    ``cr``'s four return bytes (``$A7``..``$AA``) and their BASIC decoding at
    ``mf-prg.bas:30405`` (``x=peek(ua)-1 : y=peek(ua+1)-40``):

    ==========  ===========================================  ===================
    byte        raw meaning (disassembly)                    decoded here
    ==========  ===========================================  ===================
    ``ua+0``    x-direction code 0=left / 1=none / 2=right   :attr:`x`  (-1/0/+1)
    ``ua+1``    y-direction code 0=up / 40=none / 80=down    :attr:`y`  (-40/0/+40)
    ``ua+2``    ``abs(dx)`` to the nearest enemy             :attr:`abs_dx`
    ``ua+3``    ``abs(dy)`` to the nearest enemy             :attr:`abs_dy`
    ==========  ===========================================  ===================

    Note that the decoded ``x``/``y`` are already **linear step deltas** (±1 and ±40,
    i.e. :data:`STEP_LEFT`/:data:`STEP_RIGHT` and :data:`STEP_UP`/:data:`STEP_DOWN`),
    which is exactly why the BASIC can feed them straight into ``p=x`` at ``30450``
    and into the shot resolver at ``30215`` without any conversion. ``y``'s ±40 magnitude
    is the *combat* grid's row width (40 columns) — the same 40 as the city map's width
    by coincidence of screen geometry, not because the two spaces are related (CLAUDE.md).

    ``side``/``index`` locate the chosen fighter for the caller; the original has no
    equivalent (it only ever needs the deltas), so they are port bookkeeping.

    A frozen dataclass, matching every other value type in this module/``engine.state``
    (e.g. :class:`~engine.state.Fighter`) rather than a hand-rolled ``__slots__`` class.
    """

    side: int
    index: int
    x: int
    y: int
    abs_dx: int
    abs_dy: int

    @property
    def distance(self) -> int:
        """The ``cr`` distance metric ``dy*40 + dx`` (the ``$ECF0`` table)."""
        return self.abs_dy * GRID_COLS + self.abs_dx


def ai_target(fight: "CombatFight") -> AiTarget | None:
    """Pick the active fighter's target the way ``cr`` (``$C000``) does.

    **The metric.** The disassembly's runtime-confirmed ``$ECF0`` table holds multiples
    of ``$28`` (40): ``LDA $ECF0,X`` with ``X = abs(dy)`` yields ``dy*40``, and the
    following ``ADC abs(dx)`` gives ``dy*40 + dx``. The **smallest** such value wins —
    "the nearest enemy in reading order" (ml-core-disassembly.yaml, ``cr.distance_metric``).

    This is emphatically **not** Euclidean or Chebyshev distance: a fighter five columns
    away on the same row (metric 5) is "nearer" than one a single row away in the same
    column (metric 40). The AI's whole pursuit shape follows from that bias.

    **Who is a candidate.** ``cr`` scans for cells holding char 193 with colour-RAM low
    nibble 2 — side 1 (see :data:`AI_HUNTS_SIDE`). Downed fighters are excluded because
    ``mf-prg.bas:30310`` pokes their cell back to 32, removing the 193 glyph ``cr``
    matches on; this port checks ``Fighter.down`` instead, which is the same set.

    Returns ``None`` when no hostile fighter is standing — in the original that state is
    unreachable, because the victory check at ``30106`` fires before the AI branch at
    ``30110`` ever runs. The port returns ``None`` rather than raising so a degenerate
    setup degrades to "no action" instead of crashing a fight.

    Ties are broken by scan order (lowest fighter index), matching ``cr``'s strict
    ``<`` comparison as it walks candidates: the first candidate at the minimum wins.
    """
    hostile = fight.sides[AI_HUNTS_SIDE - 1]
    origin = fight.active.position
    oy, ox = divmod(origin, GRID_COLS)

    best: AiTarget | None = None
    for index, other in enumerate(hostile):
        if other.down:
            continue
        row, col = divmod(other.position, GRID_COLS)
        dx = col - ox
        dy = row - oy
        candidate = AiTarget(
            side=AI_HUNTS_SIDE,
            index=index,
            # 30405's decoding: the direction bytes collapse the delta to its SIGN,
            # scaled to a one-cell step (±1 horizontally, ±40 = one row vertically).
            x=(STEP_RIGHT if dx > 0 else STEP_LEFT if dx < 0 else 0),
            y=(STEP_DOWN if dy > 0 else STEP_UP if dy < 0 else 0),
            abs_dx=abs(dx),
            abs_dy=abs(dy),
        )
        if best is None or candidate.distance < best.distance:
            best = candidate
    return best


# --------------------------------------------------------------------------- #
# CombatFight — the activation loop's working state (KTD-1)                   #
# --------------------------------------------------------------------------- #
class CombatFight:
    """Mutable per-fight working state: the blow-by-blow the driver loop advances.

    KTD-1 splits combat state in two. The **persistent** graph (``GameState.combat``,
    a frozen :class:`~engine.state.CombatState`) holds the setup snapshot and the
    fight's committed consequences. The **mid-fight** evolution — positions moving
    cell by cell, energies ticking down, the activation cursor walking the sides —
    is working memory and lives HERE, in a plain mutable object, never in the frozen
    graph. Only the fight's persistent consequences (roster energy/down deltas, money,
    score, result) ride effects into the shared ``Ctx``, which is what keeps
    ``run_pure``'s replay check honest: replaying the effect stream reproduces the
    post-fight graph without needing to replay every intermediate step.

    This class is deliberately NOT a generator. The driver
    (:func:`engine.interactions._run_combat`) owns the yield/send protocol; this
    object owns the *rules*. Keeping them apart is what lets U6's AI drive the same
    rules with no client in the loop, and what keeps the future async transport a
    pure swap of the driver half.

    **The fight holds no equipment table** (amendment A1). Every combatant arrives
    carrying its own constructed ``equipment`` mapping, built by the game from its
    entity data before the fight starts. There is therefore no handle to resolve, no
    lookup to miss, and — crucially — no second source that can disagree with the
    roster about what a weapon does.
    """

    def __init__(
        self,
        combat: CombatState,
        *,
        rng: Any = None,
        rules: Any = None,
    ) -> None:
        # Fighters are frozen dataclasses; the working copy is a list-of-lists so a
        # step/damage rebuilds one Fighter in place without touching the frozen graph.
        self._sides: list[list[Fighter]] = [list(side) for side in combat.sides]
        self._grid: tuple[int, ...] = tuple(combat.grid)
        self._rng = rng
        self.active_side: int = combat.active_side or 1  # s (1 or 2)
        self.active_fighter: int = combat.active_fighter or 1  # f (1-based)
        self._losses: list[int] = list(combat.losses) or [0, 0]
        self._result_flag: int = combat.result_flag
        self.finished: bool = False
        self.dir_memory: dict = dict(combat.dir_memory)
        # No engine-side default that NAMES an attribute: a bundle-less fight is a
        # fight with no formulas, which is a caller error the moment a shot is fired.
        # (The reference title's bundle lives in its own config, never here.)
        self._rules: RulesBundle = rules if rules is not None else RulesBundle()
        self._check_roles()

    def _check_roles(self) -> None:
        """Reject a bundle naming an attribute key no combatant carries (U2).

        Fails HERE, at construction, naming both the role and the missing key —
        rather than as a bare ``KeyError`` several activations deep inside a
        formula, where the message would name neither the fight nor the bundle.
        """
        # A bundle-less fight declares no formulas, so it reads no attributes and
        # has nothing to validate. It can still be moved through and surrendered —
        # which is exactly what the driver's cancel/EOF tests do — and only a SHOT
        # would fail, at the point the missing formula is actually needed.
        if self._rules.hit_fn is None and self._rules.damage_fn is None:
            return
        required = self._rules.required_keys()
        if not required:
            return
        for side_idx, side in enumerate(self._sides, start=1):
            for f_idx, fighter in enumerate(side):
                for key in required:
                    if key not in fighter.attrs:
                        role = self._role_for(key)
                        raise ValueError(
                            f"combat rules declare role {role!r} -> attribute {key!r}, "
                            f"but fighter {f_idx} on side {side_idx} ({fighter.identity!r}) "
                            f"carries no such attribute (has: "
                            f"{sorted(fighter.attrs)})"
                        )

    def _role_for(self, key: str) -> str:
        """The role name a required attribute key was declared under (for errors)."""
        if key == self._rules.vitality:
            return "vitality"
        for capability, roles in (
            ("hit", self._rules.hit_roles),
            ("damage", self._rules.damage_roles),
        ):
            for role, attr in roles.items():
                if attr == key:
                    return f"{capability}.{role}"
        return key

    def _attr(self, fighter: Fighter, roles: Mapping[str, str], role: str) -> int:
        """Resolve one declared role to the value it names on ``fighter``.

        The engine never spells an attribute name itself: it looks up which key the
        config's bundle assigned to this role and reads that key out of the opaque
        ``attrs`` map. :meth:`_check_roles` has already guaranteed the key is present.
        """
        return fighter.attrs[roles[role]]

    @property
    def vitality_key(self) -> str:
        """The attribute the fight depletes — the one engine-named role (KTD-2)."""
        return self._rules.vitality

    # -- read-only views ---------------------------------------------------- #
    @property
    def sides(self) -> tuple[tuple[Fighter, ...], tuple[Fighter, ...]]:
        """The two sides as frozen tuples (a snapshot, safe to hand to a screen)."""
        return (tuple(self._sides[0]), tuple(self._sides[1]))

    @property
    def grid(self) -> tuple[int, ...]:
        return self._grid

    @property
    def losses(self) -> tuple[int, int]:
        """Per-side downed counts — ``v(1)``/``v(2)`` (``mf-prg.bas:30310``)."""
        return (self._losses[0], self._losses[1])

    @property
    def result_flag(self) -> int:
        """The winning side once the fight ends, else 0 (the source's post-fight ``s``)."""
        return self._result_flag

    @property
    def active(self) -> Fighter:
        """The fighter whose activation is in progress."""
        return self._sides[self.active_side - 1][self.active_fighter - 1]

    def occupied(self, *, exclude: Fighter | None = None) -> frozenset[int]:
        """Cells held by a standing fighter (downed ones vacate — ``30310`` pokes 32)."""
        return frozenset(
            f.position for side in self._sides for f in side if not f.down and f is not exclude
        )

    def equipment_stats(self, combatant: Fighter) -> Mapping[str, int]:
        """``combatant``'s own equipment stats, for the game's formulas.

        A read off the roster, not a lookup (amendment A1). The old form took a
        *handle* and resolved it through a table the fight held, which meant two
        sources for one fact: a caller could hand the fight one table and the rules
        bundle another, and the fight would then compute damage from one while
        reading reach from the other. Nothing raised — it just used the wrong
        numbers. Reading the combatant's own equipment removes the second source
        rather than guarding against the disagreement.
        """
        return combatant.equipment

    def equipment_range(self, combatant: Fighter) -> int:
        """``combatant``'s shot travel range in cells (``mf-prg.bas:30215-30216``).

        Range is entity data the game put on the equipment (U1); the engine holds no
        weapon taxonomy of its own. A combatant whose equipment omits ``range`` gets
        :data:`DEFAULT_RANGE` — the source's own base ``r=2``.
        """
        return combatant.equipment.get("range", DEFAULT_RANGE)

    def is_melee(self, combatant: Fighter) -> bool:
        """True iff ``combatant``'s equipment reaches no further than the next cell.

        The derived replacement for the source's hardcoded ``orgw(...)<4``
        (``mf-prg.bas:30415``) — see :data:`RANGE_MELEE`.
        """
        return self.equipment_range(combatant) <= RANGE_MELEE

    # -- activation cursor (mf-prg.bas:30105-30109) ------------------------- #
    def _opposing(self, side: int) -> int:
        """The other side index.

        Ports the source's ``1-(s=1)`` side toggle (``mf-prg.bas:30106``, ``30108``,
        ``30250``). Under the C64 ``true = -1`` evaluation this computes directly:
        ``s=1`` -> ``1-(-1) = 2`` and ``s=2`` -> ``1-0 = 1``. (This line is in fact one
        of the structural proofs that the relational is -1 and not +1, which would give
        ``1-1 = 0`` — a side that does not exist. See
        docs/solutions/architecture-patterns/basic-relational-boolean-is-plus-one-when-porting.md.)
        """
        return 2 if side == 1 else 1

    def advance_activation(self) -> None:
        """Move the cursor to the next standing fighter, wrapping side 1 -> 2 -> 1.

        Ports ``mf-prg.bas:30105-30109``: ``f=f+1``; if ``f`` has run past this side's
        last fighter (``30107``) hand over to the other side with ``f=1`` (``30108``);
        skip any fighter already down (``30109``: ``ifkp(s,f)<0goto30105``).

        Guards against an infinite walk when neither side has a standing fighter (only
        reachable from a degenerate empty-roster setup, never from real play, since the
        victory check fires first) by bounding the scan at the total fighter count.
        """
        total = len(self._sides[0]) + len(self._sides[1]) + 2
        for _ in range(total):
            self.active_fighter += 1
            if self.active_fighter > len(self._sides[self.active_side - 1]):
                self.active_side = self._opposing(self.active_side)
                self.active_fighter = 1
            side = self._sides[self.active_side - 1]
            if side and not side[self.active_fighter - 1].down:
                return

    def winner(self) -> int | None:
        """The winning side, or ``None`` while both sides still have a standing fighter.

        Ports the per-activation victory check ``mf-prg.bas:30106``
        (``fori=1togz(ks(1-(s=1))):ifkp(1-(s=1),i)<0thennexti:goto30500``): scan the
        OPPOSING side; if every one of its fighters is marked down (``kp<0``, set at
        ``30310``) the fight is over and the *current* side is the winner (``30500``
        names ``bn$(ks(s))``, with ``s`` unchanged by the check).

        Once :meth:`surrender` has ended the fight, the recorded ``result_flag`` is
        returned instead — surrender declares a winner regardless of who is standing.
        """
        if self.finished:
            return self._result_flag or None
        for side in (1, 2):
            other = self._opposing(side)
            fighters = self._sides[other - 1]
            if all(f.down for f in fighters):
                return side
        return None

    # -- actions: one per activation (mf-prg.bas:30130-30155) --------------- #
    def _replace_fighter(self, side: int, index0: int, **changes) -> Fighter:
        """Rebuild one frozen :class:`Fighter` in the working roster and return it."""
        current = self._sides[side - 1][index0]
        updated = replace(current, **changes)
        self._sides[side - 1][index0] = updated
        return updated

    def try_move(self, step: int) -> bool:
        """Attempt a one-cell step; return whether it was legal and committed.

        Ports ``mf-prg.bas:30140-30150``: compute the target ``p = kp(s,f) + x``,
        reject it at ``30145`` when the cell is not empty (only codes 32/96 are
        walkable) or falls outside the 0..520 bound, else commit ``kp(s,f)=kp(s,f)+x``
        at ``30150``. A rejected step does NOT consume the activation — the source
        jumps back to the key-read at ``30125``, so the driver re-prompts.

        The caller supplies ``step`` as one of :data:`STEPS`; the row-width deltas
        mean a left/right step can cross a row boundary exactly as the source's
        linear screen addressing does (the original has no per-row clamp either).
        """
        return self._commit_step(step)

    def _commit_step(self, step: int) -> bool:
        """Validate one linear step and commit it if legal; shared by :meth:`try_move`
        and :meth:`_ai_step`, which layer their own pre/post rules (direction memory)
        around this common validate-then-``_replace_fighter`` core.
        """
        if step not in STEPS:
            return False
        fighter = self.active
        target = fighter.position + step
        if not can_move_onto(target, self._grid, self.occupied(exclude=fighter)):
            return False
        self._replace_fighter(self.active_side, self.active_fighter - 1, position=target)
        return True

    def shoot(self, direction: int) -> dict:
        """Fire in ``direction``; resolve travel, hit check, and damage in one call.

        The whole ``mf-prg.bas:30200-30310`` attack block:

        - ``30215-30216`` — the weapon's travel range, now CONFIG data
          (:meth:`weapon_range`).
        - ``30220-30225`` — step the projectile one cell per range point; it stops on
          leaving the grid, on a wall (:func:`blocks_shot` — codes 160/156 only), or
          when the range is exhausted. Scenery and friendly fighters are OVERFLOWN:
          the source's step test checks walls only, and its hit test ``30226``
          additionally requires the OPPOSING side's colour.
        - ``30247`` — the two miss factors (:func:`is_hit`), using the ATTACKER's
          kraft and the weapon's ``ts``.
        - ``30255`` — the damage roll (:func:`damage_roll`), using the ATTACKER's
          brutalitaet and the weapon's ``tg``.
        - ``30260``/``30275`` — subtract from the target's energy, clamped at 0.
        - ``30300-30310`` — at 0 energy the target is marked down and the side's
          loss counter increments.

        Returns a small result dict — ``hit``, ``damage``, ``target_side``,
        ``target_index`` (0-based, ``None`` on a miss), and ``downed`` — so the driver
        can narrate the shot without re-deriving what happened.
        """
        miss = {
            "hit": False,
            "damage": 0,
            "target_side": None,
            "target_index": None,
            "downed": False,
        }
        if direction not in STEPS:
            return miss

        attacker = self.active
        enemy_side = self._opposing(self.active_side)
        equipment = self.equipment_stats(attacker)

        # -- projectile travel (30220-30226) -------------------------------- #
        p = attacker.position
        target_index: int | None = None
        for _ in range(self.equipment_range(attacker)):
            p += direction
            if blocks_shot(p, self._grid):
                return miss
            for i, f in enumerate(self._sides[enemy_side - 1]):
                if not f.down and f.position == p:
                    target_index = i
                    break
            if target_index is not None:
                break
        if target_index is None:
            return miss

        # -- hit check (30245-30247): the GAME's formula, on the GAME's roles - #
        hit_input = self._attr(attacker, self._rules.hit_roles, "attacker")
        if not self._rules.hit_fn(hit_input, equipment, self._rng):
            return miss

        # -- damage (30255) and application (30260/30275, clamp at 0) ------- #
        damage_input = self._attr(attacker, self._rules.damage_roles, "attacker")
        damage = self._rules.damage_fn(damage_input, equipment, self._rng)
        target = self._sides[enemy_side - 1][target_index]
        # The ONE engine invariant on the depleting resource: subtract and clamp at
        # zero. The engine does not know what the resource means, only that reaching
        # zero terminates a combatant.
        vitality = max(0, target.attrs[self.vitality_key] - damage)
        downed = vitality == 0
        self._replace_fighter(
            enemy_side, target_index, **{self.vitality_key: vitality}, down=downed
        )
        if downed:
            # v(x)=v(x)+1 (mf-prg.bas:30310) — the STRUCK side takes the loss.
            self._losses[enemy_side - 1] += 1
        return {
            "hit": True,
            "damage": damage,
            "target_side": enemy_side,
            "target_index": target_index,
            "downed": downed,
        }

    # -- the CPU decision layer (mf-prg.bas:30400-30492) -------------------- #
    def ai_take_turn(self) -> dict:
        """Run one CPU activation: pick a target, then attack or move.

        The whole ``mf-prg.bas:30400-30492`` block, in the source's own order:

        - ``30405`` — ``syscr`` locates the nearest side-1 fighter and decodes the
          four ``ua`` bytes into step deltas ``x``/``y`` plus ``abs(dx)``/``abs(dy)``
          (:func:`ai_target`).
        - ``30410`` — ``ifpeek(ua+2)=1orpeek(ua+3)=1goto30420``: if the target is
          **near-adjacent** on either axis, jump STRAIGHT to the attack branch,
          skipping the coin flip entirely. Note this is ``= 1``, not ``<= 1`` — a
          target on the very same cell is impossible, so the distinction never bites.
        - ``30415`` — otherwise ``ifint(rnd(1)*2)=0orgw(ks(s),f)<4goto30450``: a 50%
          roll, OR a melee weapon (ids 0..3), forces the move branch. A ranged fighter
          that wins the flip falls through and tries to shoot.
        - ``30420`` — ``ifx=0thenx=y:goto30215``: column-aligned, so fire vertically.
        - ``30421`` — ``ify<>0goto30450``: reached only with ``x<>0``; if ``y`` is also
          non-zero the target is DIAGONAL, and the original has no diagonal shot, so it
          moves instead.
        - ``30425`` — ``goto30215``: reached with ``x<>0`` and ``y=0`` — row-aligned,
          fire horizontally.

        So the AI fires **only** along a shared row or column, and the 50% roll is
        consulted **only** when the target is not near-adjacent — two easy things to get
        subtly wrong when reading the block out of order.

        Returns a small result dict for the driver to narrate: ``action`` (``"shoot"``,
        ``"move"``, or ``"none"``), ``direction`` (the step/fire delta, ``None`` when
        idle), ``result`` (the :meth:`shoot` outcome, only for ``"shoot"``), and
        ``target`` (the chosen :class:`AiTarget`, ``None`` when the hostile side is
        already wiped).

        The activation is **always** consumed, including when the fighter is boxed in
        and takes no step (``30465``'s bare ``return``, then ``30110``'s
        ``gosub30400:goto30105``). Advancing the cursor is the driver's job, not this
        method's — mirroring how :meth:`try_move` and :meth:`shoot` leave it alone.
        """
        target = ai_target(self)
        idle = {"action": "none", "direction": None, "result": None, "target": target}
        if target is None:
            # Unreachable from real play: 30106's victory check fires before 30110.
            return idle

        # 30410: near-adjacent -> attack branch, WITHOUT drawing the 30415 roll.
        near_adjacent = target.abs_dx == 1 or target.abs_dy == 1
        if not near_adjacent:
            # 30415: 50% roll OR a melee weapon forces the close-distance branch.
            # The source spelled "melee" as ``orgw(...)<4``; a weapon that cannot
            # out-reach a neighbour is the same set, without the id taxonomy.
            melee = self.is_melee(self.active)
            forced_move = self._rng.range(2) == 0 if self._rng is not None else False
            if forced_move or melee:
                return self._ai_move(target)

        # 30420/30421/30425: fire only along a shared column or row.
        if target.x == 0:
            direction = target.y  # 30420: x=y, fire vertically
        elif target.y != 0:
            return self._ai_move(target)  # 30421: diagonal -> close distance
        else:
            direction = target.x  # 30425: row-aligned, fire horizontally

        if direction == 0:
            # Degenerate: attacker and target share a cell (impossible in play, since
            # occupancy blocks it). Nothing sensible to fire at, so idle.
            return idle
        return {
            "action": "shoot",
            "direction": direction,
            "result": self.shoot(direction),
            "target": target,
        }

    def _ai_move(self, target: AiTarget) -> dict:
        """The AI move routine ``mf-prg.bas:30450-30465``.

        Four step attempts, in the source's exact order, each gated on direction memory
        and each ending the activation the moment one commits:

        1. ``30450`` — the horizontal step toward the target (``p=x``).
        2. ``30451`` — the vertical step toward the target (``p=y``).
        3. ``30455``/``30460`` — the retry gates. ``30455`` reads *"if a horizontal
           approach is still available, branch to the VERTICAL sidesteps at 30460;
           else fall into the horizontal ones at 30456"*. The gate deliberately sends
           the fighter **perpendicular** to the approach it already failed — that is
           what unsticks it from a wall it is walking into.
        4. ``30456``/``30457`` or ``30461``/``30462`` — the two perpendicular sidesteps.

        **Direction memory** (``ri(f)``, seeded to -1 at ``30020``, written at ``30492``)
        forbids exactly one step per attempt: the exact reverse of the last committed
        step. The approach gates spell it as ``ri(f)<>(1+2*(x=1))`` (``30450``) and
        ``ri(f)<>(40+80*(y=1))`` (``30451``); both reduce to ``ri(f) <> -p``. The four
        sidestep lines state the SAME rule with literal constants and no relational at
        all — ``30456`` guards ``p=1`` with ``ri(f)<>-1``, ``30457`` guards ``p=-1``
        with ``ri(f)<>1``, ``30461`` guards ``p=40`` with ``ri(f)<>-40``, ``30462``
        guards ``p=-40`` with ``ri(f)<>40``. Those literals are the proof: the rule is
        "never step the exact reverse of your last step", and this port encodes it once
        (:meth:`_ai_step`) rather than re-deriving the relational per site.

        (Relational-sign note, per docs/solutions/architecture-patterns/
        basic-relational-boolean-is-plus-one-when-porting.md: the four literal sidestep
        guards pin the rule independently of any sign convention, and the C64
        ``true = -1`` evaluation agrees with them — it makes ``1+2*(x=1)`` equal
        ``-x``, the exact reverse of the last step. The since-reversed ``true=+1``
        pin would have yielded the nonsensical ``3`` for a rightward step and an
        asymmetric ``1`` for a leftward one. This site was one of the five conflicts
        that prompted the #47 audit; it is now simply consistent with the convention.)

        The seed value ``ri=-1`` (``30020``) is not neutral: it is a real leftward step,
        so a freshly-spawned enemy will not open the fight by stepping right. That is
        the original's behaviour, faithfully kept.
        """
        # 30450 then 30451: the two approach steps, horizontal first.
        for step in (target.x, target.y):
            if step != 0 and self._ai_step(step):
                return {"action": "move", "direction": step, "result": None, "target": target}

        # 30455: if the horizontal approach was AVAILABLE (x<>0 and not reverse-blocked)
        # but failed, branch to the VERTICAL sidesteps; otherwise take the horizontal
        # ones. The gate tests availability, not success — it is reached only on failure.
        horizontal_available = target.x != 0 and self._dir_memory_allows(target.x)
        if horizontal_available:
            sidesteps = (STEP_DOWN, STEP_UP)  # 30461, 30462
            # 30460: with a vertical approach also available, skip straight to the exit
            # at 30465 — the fighter has already tried both approach axes.
            if target.y != 0 and self._dir_memory_allows(target.y):
                sidesteps = ()
        else:
            sidesteps = (STEP_RIGHT, STEP_LEFT)  # 30456, 30457

        for step in sidesteps:
            if self._ai_step(step):
                return {"action": "move", "direction": step, "result": None, "target": target}

        # 30465: boxed in — the activation is spent with no step taken.
        return {"action": "none", "direction": None, "result": None, "target": target}

    def _dir_memory_allows(self, step: int) -> bool:
        """Is ``step`` permitted by this fighter's direction memory ``ri(f)``?

        The rule the four literal guards at ``30456``/``30457``/``30461``/``30462`` pin:
        ``ri(f) <> -p``. A fighter with no recorded step yet carries the ``30020`` seed
        ``-1``, which forbids a rightward step on its very first activation.
        """
        return self.dir_memory.get(self.active_fighter - 1, -1) != -step

    def _ai_step(self, step: int) -> bool:
        """One gated, validated AI step — ``30490``/``30491``/``30492`` in one call.

        Returns whether the step committed. Three things must hold, in this order:

        1. the direction-memory guard (:meth:`_dir_memory_allows`) — checked by the
           CALLER lines ``30450``-``30462`` before they ever ``gosub30490``, and
           re-checked here so no call site can forget it;
        2. ``30490``'s validity test — ``q<0 or q>520`` or the target cell is not
           walkable (``peek(br+q)`` neither 32 nor 96), which is the same movement
           obstruction set :func:`can_move_onto` owns, including fighter occupancy
           (in the source another fighter's cell holds char 193, failing the 32/96 test);
        3. on success, ``30491`` commits the position and ``30492`` records
           ``ri(f)=p``.

        On the ``p``-as-flag idiom: ``30490`` returns with ``p`` still holding the
        attempted step (non-zero) when the cell is rejected, while ``30492`` clears
        ``p=0`` after committing. Every call site then reads ``ifp=0thenreturn`` — so
        ``p=0`` means *"the step succeeded, end the activation"* and a non-zero ``p``
        means *"blocked, fall through to the next attempt"*. Reading that idiom
        backwards inverts the whole routine, which is why it is spelled out here.
        """
        if step not in STEPS:
            return False
        if not self._dir_memory_allows(step):
            return False
        if not self._commit_step(step):
            return False
        self.dir_memory[self.active_fighter - 1] = step  # 30492: ri(f)=p
        return True

    def surrender(self) -> int:
        """The active side gives up; return the winning (opposing) side.

        Ports ``mf-prg.bas:30136`` (``ifx$="q"thensysie:s=1-(s=1):goto30500``): the
        side toggle runs BEFORE the victory screen, so ``30500``'s ``bn$(ks(s))``
        names the side that did NOT surrender. Ends the fight immediately, whatever
        the board looks like.
        """
        self._result_flag = self._opposing(self.active_side)
        self.finished = True
        return self._result_flag

    def finish(self, winner: int) -> int:
        """Record ``winner`` as the fight's result and mark the fight over."""
        self._result_flag = winner
        self.finished = True
        return winner

    def snapshot(self) -> CombatState:
        """Freeze the working state back into a :class:`~engine.state.CombatState`.

        The bridge from working memory to the serializable graph shape: what the
        combat screen renders from, and what a fight-result effect would carry.
        """
        return CombatState(
            sides=self.sides,
            grid=self._grid,
            dir_memory=dict(self.dir_memory),
            active_side=self.active_side,
            active_fighter=self.active_fighter,
            losses=self.losses,
            result_flag=self._result_flag,
        )
