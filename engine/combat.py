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

from dataclasses import replace
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
    "RANGE_MELEE",
    "RANGE_RANGED",
    "RANGE_HEAVY",
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
    "shot_range",
    "is_hit",
    "damage_roll",
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


# --------------------------------------------------------------------------- #
# Shot ranges + the two combat rolls (U5)                                     #
# --------------------------------------------------------------------------- #
#: Base shot range for weapon ids 0..3 (``mf-prg.bas:30215``: ``r=2``).
RANGE_MELEE = 2
#: Range for weapon ids > 3 (``mf-prg.bas:30215``: ``ifw>3thenr=15``).
RANGE_RANGED = 15
#: Range for the two heavy weapons (``mf-prg.bas:30216``: ``ifw=6orw=7thenr=20``).
RANGE_HEAVY = 20

#: Linear step deltas for the four grid directions (``mf-prg.bas:30130-30133`` and
#: ``30206-30209``): left/right are ±1, up/down are ∓ one row width (±40). These are
#: the *only* legal move/aim deltas — the source reads exactly four keys.
STEP_LEFT = -1
STEP_RIGHT = 1
STEP_UP = -GRID_COLS
STEP_DOWN = GRID_COLS
STEPS: tuple[int, ...] = (STEP_LEFT, STEP_RIGHT, STEP_UP, STEP_DOWN)


def shot_range(weapon: int) -> int:
    """Return a shot's travel range in cells for ``weapon``.

    Ports ``mf-prg.bas:30215-30216`` exactly and in the source's order:
    ``r=2`` base, ``ifw>3thenr=15``, then ``ifw=6orw=7thenr=20``. The last test
    runs *after* the ``w>3`` widening, so ids 6 and 7 end at 20 even though they
    also satisfy ``w>3``.
    """
    r = RANGE_MELEE
    if weapon > 3:
        r = RANGE_RANGED
    if weapon in (6, 7):
        r = RANGE_HEAVY
    return r


def is_hit(rng: Any, *, ts: int, kraft: int) -> bool:
    """Roll the two miss factors; return True iff the shot connects.

    Ports ``mf-prg.bas:30247``: ``ifint(rnd(1)*ts(w))=0orint(rnd(1)*(kr/10+1))=0goto30235``
    — the shot MISSES if EITHER factor rolls 0, so it hits iff NEITHER does.
    ``rng.range(n)`` is exactly the source's ``int(rnd(1)*n)``.

    ``ts`` is the weapon's accuracy (config data, ``mf-prg.bas:50100-50115``) and
    ``kraft`` is the **attacker's** kraft: ``30246`` loads ``a=ks(s):b=f`` — the
    ACTIVE side and fighter, i.e. the attacker — before this roll, and the CPU
    branch ``30245`` substitutes the attacker's fixed ``kr=30``. (The research
    interpretation layer glosses this factor as "dodge by craft"; per KTD-9 the
    decompiled code wins, and the code unambiguously loads the attacker.)

    Both factors are drawn unconditionally, even though BASIC's ``or`` short-circuits
    past the second when the first is already 0. Drawing both keeps the RNG log
    shape stable per shot, which is what makes a seeded replay reproducible — a
    fidelity-neutral deviation (the outcome is identical either way, since a miss
    is a miss) that the behavioral bar explicitly permits.

    ``int(kr/10+1)`` is BASIC's truncation, so the second factor's bound is
    ``kraft // 10 + 1`` — never 0, so ``rng.range`` is always called legally.
    """
    weapon_factor = rng.range(ts) if ts > 0 else 0
    craft_factor = rng.range(kraft // 10 + 1)
    return weapon_factor != 0 and craft_factor != 0


def damage_roll(rng: Any, *, tg: int, brutalitaet: int) -> int:
    """Roll one hit's damage.

    Ports ``mf-prg.bas:30255``: ``y=int(rnd(1)*tg(w)+bt/10)+1``. Note where the
    ``int()`` sits — it wraps the WHOLE sum, not just the random term, so the
    fractional part of ``bt/10`` can still carry the sum past an integer boundary.
    ``tg`` is the weapon's damage rating (config data) and ``bt`` is the
    **attacker's** brutalitaet (loaded with kraft at ``30246``; fixed 30 for the
    CPU at ``30245``).

    The trailing ``+1`` makes damage at least 1 on every hit — a connecting shot
    always costs the target energy.
    """
    draw = rng.range(tg) if tg > 0 else 0
    return int(draw + brutalitaet / 10) + 1


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

    ``weapon_stats`` maps a weapon id to its ``(ts, tg)`` pair — CONFIG data
    (``data/game_configs/mafia_1920s/entities/weapons.yaml``, verbatim from
    ``mf-prg.bas:50100-50115``), passed in rather than imported, because the engine
    never reads a config's entity tables directly.
    """

    def __init__(
        self,
        combat: CombatState,
        *,
        rng: Any = None,
        weapon_stats: Any = None,
    ) -> None:
        # Fighters are frozen dataclasses; the working copy is a list-of-lists so a
        # step/damage rebuilds one Fighter in place without touching the frozen graph.
        self._sides: list[list[Fighter]] = [list(side) for side in combat.sides]
        self._grid: tuple[int, ...] = tuple(combat.grid)
        self._rng = rng
        self._weapon_stats = dict(weapon_stats or {})
        self.active_side: int = combat.active_side or 1  # s (1 or 2)
        self.active_fighter: int = combat.active_fighter or 1  # f (1-based)
        self._losses: list[int] = list(combat.losses) or [0, 0]
        self._result_flag: int = combat.result_flag
        self.finished: bool = False
        self.dir_memory: dict = dict(combat.dir_memory)

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
            f.position
            for side in self._sides
            for f in side
            if not f.down and f is not exclude
        )

    def weapon_stats(self, weapon: int) -> tuple[int, int]:
        """``(ts, tg)`` for ``weapon``; ``(0, 0)`` if the config omits it."""
        return tuple(self._weapon_stats.get(weapon, (0, 0)))  # type: ignore[return-value]

    # -- activation cursor (mf-prg.bas:30105-30109) ------------------------- #
    def _opposing(self, side: int) -> int:
        """The other side index.

        Ports the source's ``1-(s=1)`` side toggle (``mf-prg.bas:30106``, ``30108``,
        ``30250``). This is a *structural* toggle, not a numeric formula with a
        relational coefficient: the research interpretation for all three lines reads
        it as "the opposing side" / "hand the turn to the other side", and porting
        that documented prose result — rather than either raw evaluation — is exactly
        what the pinned relational convention mandates
        (docs/solutions/architecture-patterns/basic-relational-boolean-is-plus-one-when-porting.md:
        "port the prose result, not a raw C64 evaluation").
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

        - ``30215-30216`` — the weapon's travel range (:func:`shot_range`).
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
        miss = {"hit": False, "damage": 0, "target_side": None, "target_index": None, "downed": False}
        if direction not in STEPS:
            return miss

        attacker = self.active
        enemy_side = self._opposing(self.active_side)
        ts, tg = self.weapon_stats(attacker.weapon)

        # -- projectile travel (30220-30226) -------------------------------- #
        p = attacker.position
        target_index: int | None = None
        for _ in range(shot_range(attacker.weapon)):
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

        # -- hit check (30245-30247), attacker's stats ---------------------- #
        if not is_hit(self._rng, ts=ts, kraft=attacker.kraft):
            return miss

        # -- damage (30255) and application (30260/30275, clamp at 0) ------- #
        damage = damage_roll(self._rng, tg=tg, brutalitaet=attacker.brutalitaet)
        target = self._sides[enemy_side - 1][target_index]
        energie = max(0, target.energie - damage)
        downed = energie == 0
        self._replace_fighter(enemy_side, target_index, energie=energie, down=downed)
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
